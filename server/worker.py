"""Durable email outbox, reservation cleanup and payment reconciliation."""
import logging
import json
import math
import threading
from time import sleep
from email.utils import parsedate_to_datetime
import httpx
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from . import config,branding
from .db import db,now,packed
from . import payments

log=logging.getLogger('kontracts.worker')

def maintenance():
    with db(True) as c:
        expired=c.execute("SELECT * FROM bookings WHERE status='held' AND hold_until<=?",(now(),)).fetchall()
        for b in expired:
            c.execute("UPDATE bookings SET status='expired',updated_at=? WHERE id=?",(now(),b['id']))
            if b['quote_id']:
                c.execute("UPDATE quotes SET status='offered' WHERE id=? AND expires_at>?",(b['quote_id'],now()))
        c.execute("UPDATE bookings SET status='expired',updated_at=? WHERE status='pending_approval' AND (created_at<? OR start_ts<?)",(now(),now()-48*3600,now()))
        c.execute('DELETE FROM sessions WHERE expires_at<?',(now(),))
        c.execute('DELETE FROM auth_tokens WHERE expires_at<?',(now(),))
        c.execute('DELETE FROM oauth_states WHERE expires_at<?',(now(),))
        c.execute('DELETE FROM rate_limits WHERE expires_at<?',(now(),))
        c.execute('DELETE FROM webhook_events WHERE created_at<?',(now()-90*86400,))
        c.execute('DELETE FROM mail_usage WHERE accepted_at<?',(now()-62*86400,))
        from .mail import queue_trial_notices
        queue_trial_notices(c)
    for b in expired:
        try: payments.delete_checkout(b)
        except Exception: log.warning('Could not retire an expired checkout; late payments will require review.')

class MailDeferred(Exception):
    """Provider explicitly rejected the send for a temporary quota/rate limit."""
    def __init__(self, until, reason):
        super().__init__(reason)
        self.until = until
        self.reason = reason


_DELIVERY_LOCK = threading.Lock()


def local_quota_state(c, timestamp=None):
    """Counts this application's accepted sends, not Thunderbird/other apps."""
    timestamp = now() if timestamp is None else timestamp
    day_start = timestamp - timestamp % 86400
    daily = c.execute('SELECT count(*) FROM mail_usage WHERE accepted_at>=?', (day_start,)).fetchone()[0]
    rolling = c.execute('SELECT count(*) FROM mail_usage WHERE accepted_at>?', (timestamp - 31 * 86400,)).fetchone()[0]
    until, reason = 0, ''
    if config.MAIL_DAILY_LIMIT and daily >= config.MAIL_DAILY_LIMIT:
        until, reason = day_start + 86400 + 1, 'Local daily allowance reached'
    if config.MAIL_31DAY_LIMIT and rolling >= config.MAIL_31DAY_LIMIT:
        # If the cap was reduced, wait until enough accepted sends age out.
        offset = rolling - config.MAIL_31DAY_LIMIT
        oldest = c.execute('''SELECT accepted_at FROM mail_usage WHERE accepted_at>?
            ORDER BY accepted_at LIMIT 1 OFFSET ?''', (timestamp - 31 * 86400, offset)).fetchone()[0]
        until = max(until, oldest + 31 * 86400 + 1)
        reason = 'Local rolling-31-day allowance reached'
    return {'daily_used': daily, 'rolling_31day_used': rolling,
            'daily_limit': config.MAIL_DAILY_LIMIT, 'rolling_31day_limit': config.MAIL_31DAY_LIMIT,
            'blocked_until': until, 'reason': reason}


def _retry_after(response, timestamp):
    value = getattr(response, 'headers', {}).get('Retry-After', '')
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError()
        return timestamp + max(1, min(31 * 86400, int(math.ceil(seconds))))
    except (ValueError, TypeError):
        try:
            return max(timestamp + 1, min(timestamp + 31 * 86400,
                                         int(parsedate_to_datetime(value).timestamp())))
        except (ValueError, TypeError, OverflowError, AttributeError):
            return timestamp + 60


def _resend(payload, identifier):
    response = httpx.post(
        'https://api.resend.com/emails',
        headers={'Authorization': 'Bearer ' + config.RESEND_API_KEY,
                 'Idempotency-Key': 'kontracts-mail/' + identifier},
        json=payload, timeout=20,
    )
    if getattr(response, 'status_code', 200) == 429:
        timestamp = now()
        try:
            result = response.json()
            name = result.get('name', '') if isinstance(result, dict) else ''
        except ValueError:
            name = ''
        until = _retry_after(response, timestamp)
        if name == 'daily_quota_exceeded':
            until = max(until, timestamp - timestamp % 86400 + 86400 + 60)
        elif name == 'monthly_quota_exceeded':
            until = max(until, timestamp + 6 * 3600)
        else:
            name = 'rate_limit_exceeded'
        raise MailDeferred(until, 'Resend: ' + name)
    response.raise_for_status()
    result = response.json()
    identifier = result.get('id') if isinstance(result, dict) else None
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 255:
        raise RuntimeError('Email provider did not acknowledge acceptance.')
    return identifier


def build_payload(message):
    """Called once per queued message; its result is frozen before a send."""
    from .mail import identity, channel_for, mailbox
    presentation = branding.email_presentation(message)
    routing = presentation or identity(message)
    sender = mailbox(routing['from'], 'From')[0]
    reply_to = mailbox(routing['reply_to'], 'Reply-To', address_only=True)[1]
    recipient = mailbox(message['recipient'], 'Recipient', address_only=True)[1]
    subject = message['subject']
    if not isinstance(subject, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in subject):
        raise ValueError('Invalid email subject.')
    payload = {'from': sender, 'to': [recipient], 'subject': subject, 'text': message['body'],
               'reply_to': reply_to,
               'headers': {'Message-ID': '<' + message['id'] + '@' + config.MAIL_SENDER_DOMAIN + '>',
                           'X-Kontracts-Category': channel_for(message)}}
    if presentation:
        payload['html'] = presentation['html']
    return payload


def _mime(payload, identifier):
    msg = EmailMessage()
    msg['From'] = payload['from']
    msg['To'] = ', '.join(payload['to'])
    msg['Subject'] = payload['subject']
    msg['Reply-To'] = payload['reply_to']
    for name, value in payload.get('headers', {}).items():
        msg[name] = value
    if config.SMTP_HOST.lower() == 'smtp.resend.com':
        msg['Resend-Idempotency-Key'] = 'kontracts-mail/' + identifier
    msg.set_content(payload['text'])
    if payload.get('html'):
        msg.add_alternative(payload['html'], subtype='html')
    return msg


def _smtp(payload, identifier):
    msg = _mime(payload, identifier)
    context = ssl.create_default_context()
    if config.SMTP_PORT == 465:
        connection = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20, context=context)
    else:
        connection = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
    with connection as smtp:
        if config.SMTP_PORT != 465:
            smtp.starttls(context=context)
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        if smtp.send_message(msg):
            raise RuntimeError('SMTP did not accept the recipient.')


def deliver(limit=20, message_ids=None):
    """Single-process sender. message_ids lets the CLI send only its own probes."""
    if not config.MAIL_ENABLED or not _DELIVERY_LOCK.acquire(blocking=False):
        return
    try:
        _deliver(max(0, min(int(limit), 100)), message_ids)
    finally:
        _DELIVERY_LOCK.release()


def _deliver(limit, message_ids):
    from .mail import cancellation_reason
    for _ in range(limit):
        timestamp = now()
        with db(True) as c:
            c.execute('''UPDATE outbox SET cancelled_at=?,claimed_at=NULL,last_error='Expired before delivery'
                WHERE sent_at IS NULL AND cancelled_at IS NULL AND expires_at<=?
                AND (claimed_at IS NULL OR claimed_at<?)''', (timestamp, timestamp, timestamp - 300))
            params = [timestamp, timestamp - 300]
            restriction = ''
            if message_ids is not None:
                if not message_ids:
                    return
                restriction = ' AND id IN (' + ','.join('?' for _ in message_ids) + ')'
                params.extend(message_ids)
            message = c.execute('''SELECT * FROM outbox WHERE sent_at IS NULL AND cancelled_at IS NULL
                AND attempts<8 AND due_at<=? AND (claimed_at IS NULL OR claimed_at<?)'''
                + restriction + ''' ORDER BY CASE WHEN mail_channel='accounts' OR guard_kind='invoice_code'
                    THEN 0 WHEN mail_channel='notifications' THEN 1 ELSE 2 END,due_at,rowid LIMIT 1''', params).fetchone()
            if not message:
                return
            reason = cancellation_reason(c, message, timestamp)
            if reason:
                c.execute('UPDATE outbox SET cancelled_at=?,claimed_at=NULL,last_error=? WHERE id=?',
                          (timestamp, reason, message['id']))
                continue
            if message['first_attempt_at'] is not None and timestamp - message['first_attempt_at'] >= 23 * 3600:
                c.execute('''UPDATE outbox SET attempts=8,claimed_at=NULL,
                    last_error='ManualReview: provider retry window exceeded' WHERE id=?''', (message['id'],))
                continue
            if config.RESEND_API_KEY or config.SMTP_HOST:
                state = local_quota_state(c, timestamp)
                block = c.execute("SELECT * FROM mail_transport_state WHERE name='resend' AND blocked_until>?", (timestamp,)).fetchone() if config.RESEND_API_KEY else None
                until = max(state['blocked_until'], block['blocked_until'] if block else 0)
                if until:
                    reason = block['reason'] if block and block['blocked_until'] >= state['blocked_until'] else state['reason']
                    c.execute('UPDATE outbox SET due_at=?,last_error=? WHERE id=?', (until, reason, message['id']))
                    return
            c.execute('''UPDATE outbox SET claimed_at=?,attempts=attempts+1,
                first_attempt_at=COALESCE(first_attempt_at,?) WHERE id=?''', (timestamp, timestamp, message['id']))
        attempted_transport = False
        try:
            if message['delivery_payload']:
                payload = json.loads(message['delivery_payload'])
            else:
                payload = build_payload(message)
                with db(True) as c:
                    c.execute('UPDATE outbox SET delivery_payload=? WHERE id=?', (packed(payload), message['id']))
            provider_id = None
            if config.RESEND_API_KEY:
                attempted_transport = True
                provider_id = _resend(payload, message['id'])
            elif config.SMTP_HOST:
                attempted_transport = True
                _smtp(payload, message['id'])
            elif not config.PRODUCTION:
                path = config.DATA / 'mail'
                path.mkdir(exist_ok=True)
                (path / (message['id'] + '.eml')).write_text(_mime(payload, message['id']).as_string(), encoding='utf-8')
            else:
                raise RuntimeError('Email is not configured')
            with db(True) as c:
                timestamp = now()
                c.execute('UPDATE outbox SET sent_at=?,provider_id=?,claimed_at=NULL,last_error=NULL WHERE id=?',
                          (timestamp, provider_id, message['id']))
                if attempted_transport:
                    c.execute('INSERT OR IGNORE INTO mail_usage VALUES(?,?)', (message['id'], timestamp))
        except MailDeferred as exc:
            with db(True) as c:
                c.execute('''UPDATE outbox SET claimed_at=NULL,attempts=MAX(0,attempts-1),last_error=?,due_at=?,
                    first_attempt_at=CASE WHEN attempts=1 THEN NULL ELSE first_attempt_at END WHERE id=?''',
                    (exc.reason, exc.until, message['id']))
                c.execute('''INSERT INTO mail_transport_state VALUES('resend',?,?) ON CONFLICT(name)
                    DO UPDATE SET blocked_until=MAX(blocked_until,excluded.blocked_until),reason=excluded.reason''',
                    (exc.until, exc.reason))
            log.warning('Email deferred by provider quota/rate limit; retry budget preserved.')
            return
        except Exception as exc:
            # Do not log message content, recipients, keys, or raw provider responses.
            with db(True) as c:
                c.execute('UPDATE outbox SET claimed_at=NULL,last_error=?,due_at=? WHERE id=?',
                          (type(exc).__name__, now() + min(3600, 30 * (2 ** message['attempts'])), message['id']))
            log.warning('Email delivery failed; retained for retry.')
        finally:
            if attempted_transport and config.MAIL_SEND_INTERVAL:
                sleep(config.MAIL_SEND_INTERVAL)


def reconcile(limit=15):
    with db() as c:
        rows=c.execute('''SELECT b.*,s.square_merchant FROM bookings b JOIN shops s ON s.id=b.shop_id
            WHERE s.square_access IS NOT NULL AND b.square_order IS NOT NULL
            AND b.created_at>? AND b.status IN ('held','expired','payment_review','cancelled','confirmed','awaiting_signature','completed')
            ORDER BY b.updated_at LIMIT ?''',(now()-90*86400,limit)).fetchall()
    for b in rows:
        try:
            if b['square_payment']:
                p=payments.square(b['shop_id'],'/v2/payments/'+b['square_payment'],method='GET')['payment']
                eid='reconcile:'+p['id']+':'+p.get('status','')+':'+str(p.get('refunded_money',{}).get('amount',0))
                payments.apply_square_payment(eid,b['square_merchant'],p)
            else:
                order=payments.square(b['shop_id'],'/v2/orders/'+b['square_order'],method='GET')['order']
                for tender in order.get('tenders',[]):
                    if tender.get('payment_id'):
                        p=payments.square(b['shop_id'],'/v2/payments/'+tender['payment_id'],method='GET')['payment']
                        payments.apply_square_payment('reconcile:'+p['id'],b['square_merchant'],p)
            with db(True) as c: c.execute('UPDATE bookings SET updated_at=? WHERE id=?',(now(),b['id']))
        except Exception: log.warning('Payment reconciliation needs retry.')

def run_once():
    from .invoices import reconcile as reconcile_invoices
    maintenance();deliver();reconcile();reconcile_invoices()

if __name__=='__main__':
    from .db import initialize
    initialize();run_once()
