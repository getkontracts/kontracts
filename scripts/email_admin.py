"""Private email administration. Never expose this script as an HTTP endpoint.

check/status do not migrate or send. Mutating commands require the app to be stopped.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _read_db():
    from server import config
    path = Path(config.DB).resolve()
    if not path.is_file():
        raise RuntimeError('Database not found. Run migrate or start the app first.')
    c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def check():
    from server import config
    result = {
        'sending_enabled': config.MAIL_ENABLED,
        'provider': 'resend-api' if config.RESEND_API_KEY else 'smtp' if config.SMTP_HOST else 'local-eml',
        'sender_domain': config.MAIL_SENDER_DOMAIN,
        'accounts_from': config.MAIL_ACCOUNTS_FROM,
        'notifications_from': config.MAIL_NOTIFICATIONS_FROM,
        'billing_from': config.MAIL_BILLING_FROM,
        'platform_reply_to': config.MAIL_REPLY_TO,
        'support_email': config.SUPPORT_EMAIL,
        'billing_notices': config.MAIL_BILLING_NOTICES,
        'daily_app_cap': config.MAIL_DAILY_LIMIT,
        'rolling_31day_app_cap': config.MAIL_31DAY_LIMIT,
        'database_exists': Path(config.DB).is_file(),
        'note': 'Configuration only. DNS, inbox delivery, SPF/DKIM/DMARC and support replies require live checks.',
    }
    print(json.dumps(result, indent=2))


def status():
    from server import worker
    c = _read_db()
    try:
        if 'cancelled_at' not in {r[1] for r in c.execute('PRAGMA table_info(outbox)')}:
            raise RuntimeError('Email migration is not installed. Stop the app and run migrate.')
        result = {
            'pending': c.execute('SELECT count(*) FROM outbox WHERE sent_at IS NULL AND cancelled_at IS NULL AND attempts<8').fetchone()[0],
            'failed_or_manual_review': c.execute('SELECT count(*) FROM outbox WHERE sent_at IS NULL AND cancelled_at IS NULL AND attempts>=8').fetchone()[0],
            'cancelled': c.execute('SELECT count(*) FROM outbox WHERE cancelled_at IS NOT NULL').fetchone()[0],
            'quota': worker.local_quota_state(c),
            'provider_blocks': [dict(r) for r in c.execute('SELECT * FROM mail_transport_state')],
            'recent_pending': [dict(r) for r in c.execute('''SELECT id,mail_channel,attempts,last_error,due_at,provider_id
                FROM outbox WHERE sent_at IS NULL AND cancelled_at IS NULL ORDER BY rowid DESC LIMIT 30''')],
            'note': 'App-local counters do not include Thunderbird or other Resend users. Check Resend Usage too.',
        }
        print(json.dumps(result, indent=2))
    finally:
        c.close()


def smoke(args):
    from server import config, worker
    from server.db import db, now, queue_mail, uid
    from server.mail import mailbox
    recipient = mailbox(args.to, 'Test recipient', address_only=True)[1]
    if not args.send:
        print(json.dumps({'dry_run': True, 'recipient': recipient, 'channels': ['accounts', 'notifications', 'billing'],
                          'note': 'No messages queued. Use --send --app-is-stopped after DNS verification.'}, indent=2))
        return 0
    if not config.MAIL_ENABLED or not (config.RESEND_API_KEY or config.SMTP_HOST):
        raise RuntimeError('A live provider and MAIL_ENABLED=true are required for --send.')
    run = uid()
    ids = []
    with db(True) as c:
        shop = None
        if args.shop_slug:
            shop = c.execute('SELECT * FROM shops WHERE slug=?', (args.shop_slug,)).fetchone()
            if not shop or shop['is_demo']:
                raise RuntimeError('Choose an existing non-demo business slug.')
        for channel in ('accounts', 'notifications', 'billing'):
            key = 'mail-smoke:' + run + ':' + channel
            body = ('Kontracts email setup test: ' + channel + '.\n\nThis is synthetic test content. '
                    'No booking, invoice, password reset or payment was created.\n\nTest reference: ' + run)
            queue_mail(c, key, recipient, 'Kontracts email test - ' + channel, body,
                       shop['id'] if shop is not None else None, channel=channel, expires_at=now() + 3600)
            ids.append(c.execute('SELECT id FROM outbox WHERE dedupe_key=?', (key,)).fetchone()[0])
    worker.deliver(limit=3, message_ids=ids)
    with db() as c:
        rows = [dict(c.execute('SELECT id,mail_channel,sent_at,provider_id,last_error,due_at FROM outbox WHERE id=?', (identifier,)).fetchone()) for identifier in ids]
    print(json.dumps({'messages': rows, 'note': 'Acceptance is not inbox delivery. Inspect all three messages in your test inbox.'}, indent=2))
    return 0 if all(row['sent_at'] for row in rows) else 2


def cancel(identifier):
    from server.db import db, now
    with db(True) as c:
        row = c.execute('SELECT * FROM outbox WHERE id=?', (identifier,)).fetchone()
        if not row or row['sent_at'] is not None:
            raise RuntimeError('Message missing or already accepted; it cannot be cancelled here.')
        c.execute("UPDATE outbox SET cancelled_at=?,claimed_at=NULL,last_error='Cancelled by operator' WHERE id=?", (now(), identifier))
    print('Queued message cancelled. Already-sent email cannot be recalled.')


def retry(identifier, confirmed):
    from server import worker
    from server.db import db, now, packed, uid
    from server.mail import cancellation_reason
    if not confirmed:
        raise RuntimeError('Check Resend first, then acknowledge with --confirmed-not-accepted. A blind replay can duplicate email.')
    with db(True) as c:
        row = c.execute('SELECT * FROM outbox WHERE id=?', (identifier,)).fetchone()
        if not row or row['sent_at'] is not None or row['cancelled_at'] is not None:
            raise RuntimeError('Only a pending, unaccepted message can be requeued.')
        if row['guard_kind'] in ('auth', 'invoice_code'):
            raise RuntimeError('Request a fresh account link or signing code in the app instead.')
        reason = cancellation_reason(c, row, now())
        if reason:
            raise RuntimeError(reason + '. Do not replay this message.')
        new_id = uid()
        payload = json.loads(row['delivery_payload']) if row['delivery_payload'] else None
        if payload:
            from server import config
            payload.setdefault('headers', {})['Message-ID'] = '<' + new_id + '@' + config.MAIL_SENDER_DOMAIN + '>'
        c.execute('''UPDATE outbox SET id=?,attempts=0,claimed_at=NULL,first_attempt_at=NULL,
            provider_id=NULL,due_at=?,last_error=NULL,delivery_payload=? WHERE id=?''',
            (new_id, now(), packed(payload) if payload else None, identifier))
    print(json.dumps({'old_id': identifier, 'new_id': new_id, 'queued': True,
                      'note': 'Nothing sent by this command. Restart the app to deliver. Provider/local quota limits still apply.'}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('check', help='Print non-secret configuration; no network or migration.')
    subs.add_parser('status', help='Read local queue/quota state; no network or migration.')
    migrate = subs.add_parser('migrate', help='Apply additive database migrations.')
    migrate.add_argument('--app-is-stopped', action='store_true')
    probe = subs.add_parser('smoke', help='Preview or explicitly send three synthetic email probes.')
    probe.add_argument('--to', required=True)
    probe.add_argument('--shop-slug')
    probe.add_argument('--send', action='store_true')
    probe.add_argument('--app-is-stopped', action='store_true')
    for command in ('cancel', 'retry'):
        sub = subs.add_parser(command)
        sub.add_argument('id')
        sub.add_argument('--app-is-stopped', action='store_true')
        if command == 'retry':
            sub.add_argument('--confirmed-not-accepted', action='store_true')
    args = parser.parse_args(argv)
    mutating = args.command in ('migrate', 'cancel', 'retry') or (args.command == 'smoke' and args.send)
    if mutating and not args.app_is_stopped:
        parser.error('Stop the app and pass --app-is-stopped; do not run a second mail worker.')
    try:
        if args.command == 'check': check()
        elif args.command == 'status': status()
        elif args.command == 'migrate':
            from server.db import initialize
            initialize()
            print('Database schema initialized through the email migration. No email sent.')
        elif args.command == 'smoke': return smoke(args)
        elif args.command == 'cancel': cancel(args.id)
        elif args.command == 'retry': retry(args.id, args.confirmed_not_accepted)
        return 0
    except Exception as exc:
        # Expected errors use our own safe wording, never include raw provider bodies.
        if isinstance(exc, (RuntimeError, ValueError)):
            print('Email administration: ' + str(exc), file=sys.stderr)
        else:
            print('Email administration failed: ' + type(exc).__name__ + '. Check configuration and migration.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
