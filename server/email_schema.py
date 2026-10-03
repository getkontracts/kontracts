"""Transactional, additive email migration for the reviewed v5 database.

Existing attempted messages have no recoverable provider payload; park them for
operator review rather than changing a payload under an existing idempotency key.
"""
import json
from . import config
from .mail import shop_contact, valid_address


def _legacy_reply(c, message, shop):
    key = message['dedupe_key']
    customer = None
    if key.startswith('new-quote:'):
        row = c.execute('SELECT email FROM quotes WHERE id=? AND shop_id=?',
                        (key[len('new-quote:'):], shop['id'])).fetchone()
        customer = row['email'] if row else None
    elif key.endswith(':owner'):
        row = c.execute('SELECT email FROM bookings WHERE id=? AND shop_id=?',
                        (key[:36], shop['id'])).fetchone()
        customer = row['email'] if row else None
    elif key.endswith(':owner-signed'):
        row = c.execute("""SELECT v.payload FROM invoice_versions v JOIN invoices i ON i.id=v.invoice_id
            WHERE i.id=? AND i.shop_id=? ORDER BY v.version DESC LIMIT 1""", (key[:36], shop['id'])).fetchone()
        if row:
            try:
                from .security import decrypt
                customer = json.loads(decrypt(row['payload']))['customer']['email']
            except (ValueError, KeyError, TypeError): pass
    if key.startswith('new-quote:') or key.endswith((':owner', ':owner-signed')):
        return valid_address(customer) or config.SUPPORT_EMAIL
    return shop_contact(shop)


def initialize(c):
    c.execute('BEGIN IMMEDIATE')
    try:
        columns = {row[1] for row in c.execute('PRAGMA table_info(outbox)')}
        additions = {
            'mail_channel': "TEXT NOT NULL DEFAULT 'accounts' CHECK(mail_channel IN ('accounts','notifications','billing'))",
            'reply_to': 'TEXT', 'expires_at': 'INTEGER', 'guard_kind': 'TEXT',
            'guard_id': 'TEXT', 'guard_value': 'TEXT', 'delivery_payload': 'TEXT',
            'first_attempt_at': 'INTEGER', 'provider_id': 'TEXT', 'cancelled_at': 'INTEGER',
        }
        for name, spec in additions.items():
            if name not in columns:
                c.execute('ALTER TABLE outbox ADD COLUMN ' + name + ' ' + spec)
        c.execute('CREATE TABLE IF NOT EXISTS mail_usage(message_id TEXT PRIMARY KEY, accepted_at INTEGER NOT NULL)')
        c.execute('CREATE INDEX IF NOT EXISTS mail_usage_time ON mail_usage(accepted_at)')
        c.execute("""CREATE TABLE IF NOT EXISTS mail_transport_state(
            name TEXT PRIMARY KEY, blocked_until INTEGER NOT NULL, reason TEXT NOT NULL)""")
        c.execute("""CREATE INDEX IF NOT EXISTS outbox_pending_email ON outbox(due_at)
            WHERE sent_at IS NULL AND cancelled_at IS NULL""")
        if not c.execute('SELECT 1 FROM schema_versions WHERE version=6').fetchone():
            c.execute("""INSERT OR IGNORE INTO mail_usage
                SELECT id,sent_at FROM outbox WHERE sent_at IS NOT NULL AND last_error IS NULL""")
            for message in c.execute('SELECT * FROM outbox WHERE sent_at IS NULL').fetchall():
                shop = (c.execute('SELECT * FROM shops WHERE id=?', (message['shop_id'],)).fetchone()
                        if message['shop_id'] else None)
                channel = 'notifications' if message['shop_id'] else 'accounts'
                reply = _legacy_reply(c, message, shop) if shop else config.MAIL_REPLY_TO
                c.execute('UPDATE outbox SET mail_channel=?,reply_to=? WHERE id=?', (channel, reply, message['id']))
                if message['attempts'] or message['claimed_at']:
                    c.execute("""UPDATE outbox SET attempts=8,claimed_at=NULL,
                        last_error='ManualReview: legacy delivery payload unavailable' WHERE id=?""", (message['id'],))
                if message['dedupe_key'].startswith('auth:'):
                    ref = message['dedupe_key'][5:]
                    row = c.execute('SELECT expires_at FROM auth_tokens WHERE token_hash=?', (ref,)).fetchone()
                    c.execute("UPDATE outbox SET guard_kind='auth',guard_id=?,expires_at=? WHERE id=?",
                              (ref, row['expires_at'] if row else 0, message['id']))
                elif ':code:' in message['dedupe_key']:
                    # Never deliver a legacy OTP after deployment. Request a new one.
                    c.execute("UPDATE outbox SET cancelled_at=strftime('%s','now'),last_error='Legacy signing code: request a fresh code' WHERE id=?", (message['id'],))
            c.execute("INSERT INTO schema_versions VALUES(6,strftime('%s','now'))")
        c.commit()
    except BaseException:
        c.rollback()
        raise
