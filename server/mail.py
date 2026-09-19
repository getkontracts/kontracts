"""Email identities, queue validity and subscription notices.

No provider calls occur here. Tenant-controlled text never chooses a From domain.
Imports of config/db are local so config can reuse the address validator safely.
"""
import json
import unicodedata
from email.utils import formataddr, getaddresses
from email_validator import EmailNotValidError, validate_email

CHANNELS = frozenset(('accounts', 'notifications', 'billing'))
GUARDS = frozenset(('auth', 'invoice_code', 'invoice_link', 'trial', 'subscription'))


def mailbox(value, label='Email', address_only=False):
    """Return (canonical header, bare address), or fail without exposing input."""
    if not isinstance(value, str) or not value.strip() or any(
        unicodedata.category(ch).startswith('C') for ch in value
    ):
        raise ValueError(label + ' must contain one valid mailbox without control characters.')
    try:
        if address_only:
            display, address = '', value.strip()
        else:
            boxes = getaddresses([value.strip()])
            if len(boxes) != 1:
                raise ValueError('Multiple mailboxes')
            display, address = boxes[0]
        validated = validate_email(address, check_deliverability=False, allow_smtputf8=False)
        address = validated.ascii_email or validated.normalized
        return formataddr((display, address)), address
    except (EmailNotValidError, ValueError, IndexError) as exc:
        raise ValueError(label + ' must contain one valid mailbox.') from exc


def valid_address(value):
    try:
        return mailbox(value, address_only=True)[1]
    except ValueError:
        return None


def clean_name(value):
    """Keep a useful display name but remove header/control characters."""
    return ' '.join(''.join(ch if not unicodedata.category(ch).startswith('C')
                           else ' ' for ch in str(value)).split())[:120] or 'Kontracts'


def field(message, name, default=None):
    return message[name] if name in message.keys() else default


def channel_for(message):
    channel = field(message, 'mail_channel')
    if channel is None:
        channel = 'notifications' if field(message, 'shop_id') else 'accounts'
    if channel not in CHANNELS:
        raise ValueError('Unknown mail channel.')
    return channel


def shop_contact(shop):
    from . import config
    try:
        address = valid_address(json.loads(shop['settings']).get('contact_email'))
    except (KeyError, TypeError, ValueError):
        address = None
    return address or config.SUPPORT_EMAIL


def identity(message, shop=None):
    from . import config
    channel = channel_for(message)
    if channel == 'notifications':
        sender = (formataddr((clean_name(shop['name']), config.MAIL_NOTIFICATIONS_ADDRESS))
                  if shop is not None else config.MAIL_NOTIFICATIONS_FROM)
        fallback = shop_contact(shop) if shop is not None else config.SUPPORT_EMAIL
    else:
        sender = config.MAIL_ACCOUNTS_FROM if channel == 'accounts' else config.MAIL_BILLING_FROM
        fallback = config.MAIL_REPLY_TO
    reply = valid_address(field(message, 'reply_to')) or fallback
    return {'from': sender, 'reply_to': reply}


def cancellation_reason(c, message, timestamp):
    """Return a safe reason when a queued message must no longer be sent."""
    if field(message, 'expires_at') is not None and message['expires_at'] <= timestamp:
        return 'Expired before delivery'
    if message['shop_id']:
        shop = c.execute('SELECT is_demo FROM shops WHERE id=?', (message['shop_id'],)).fetchone()
        if not shop or shop['is_demo']:
            return 'Demo or missing business: email disabled'
    if message['booking_id']:
        b = c.execute('SELECT status,start_ts FROM bookings WHERE id=?', (message['booking_id'],)).fetchone()
        if not b or b['status'] != 'confirmed' or b['start_ts'] != message['expected_start'] or b['start_ts'] <= timestamp:
            return 'Appointment reminder is no longer current'
    kind = field(message, 'guard_kind')
    if not kind:
        return None
    ref = message['guard_id']
    expected = message['guard_value']
    if kind == 'auth':
        row = c.execute('SELECT expires_at FROM auth_tokens WHERE token_hash=?', (ref,)).fetchone()
        if not row or row['expires_at'] <= timestamp:
            return 'Account link expired, used or replaced'
    elif kind in ('invoice_code', 'invoice_link'):
        inv = c.execute('SELECT * FROM invoices WHERE id=?', (ref,)).fetchone()
        if not inv:
            return 'Invoice no longer available'
        if kind == 'invoice_code':
            if (inv['status'] != 'awaiting_signature' or inv['otp_hash'] != expected
                    or (inv['otp_expires'] or 0) <= timestamp or inv['otp_attempts'] >= 5):
                return 'Signing code expired, used, locked or replaced'
        elif inv['token_hash'] != expected or inv['token_expires'] <= timestamp:
            return 'Invoice link expired or replaced'
    elif kind == 'trial':
        shop = c.execute('SELECT * FROM shops WHERE id=?', (ref,)).fetchone()
        if (not shop or shop['billing_status'] != 'trial' or shop['paddle_subscription']
                or str(shop['trial_end']) != expected or shop['trial_end'] <= timestamp):
            return 'Trial notice is no longer current'
    elif kind == 'subscription':
        shop = c.execute('SELECT billing_status FROM shops WHERE id=?', (ref,)).fetchone()
        if not shop or shop['billing_status'] != expected:
            return 'Subscription notice is no longer current'
    else:
        return 'Unknown email validity guard'
    return None


def queue_subscription_notice(c, shop, new_status, event_id):
    """A SaaS status update, never a fabricated Paddle payment receipt."""
    from . import config
    from .db import queue_mail
    if not config.MAIL_BILLING_NOTICES or shop['billing_status'] == new_status:
        return
    owner = c.execute('SELECT email FROM users WHERE id=?', (shop['owner_id'],)).fetchone()
    if not owner:
        return
    wording = {
        'active': ('Your Kontracts subscription is active', 'Your Kontracts subscription is now active.'),
        'trialing': ('Your Kontracts subscription trial is active', 'Paddle reports that your subscription is in a trial period.'),
        'past_due': ('Action needed for your Kontracts subscription', 'Paddle reports that your subscription is past due. Review your payment details in Kontracts.'),
        'paused': ('Your Kontracts subscription is paused', 'Paddle reports that your Kontracts subscription is paused.'),
        'canceled': ('Your Kontracts subscription is cancelled', 'Paddle reports that your Kontracts subscription is cancelled.'),
    }
    subject, text = wording[new_status]
    body = (text + '\n\nBusiness: ' + shop['name'] + '\nOpen your account: '
            + config.BASE_URL + '/app/billing\n\nThis is a subscription-status notice, not a payment receipt. '
            'Use Paddle for official billing documents.\nQuestions: ' + config.SUPPORT_EMAIL)
    queue_mail(c, 'subscription-notice:' + event_id, owner['email'], subject, body,
               shop['id'], channel='billing', reply_to=config.MAIL_REPLY_TO,
               guard_kind='subscription', guard_id=shop['id'], guard_value=new_status)


def queue_trial_notices(c):
    from datetime import datetime, timezone
    from . import config
    from .db import now, queue_mail
    if not config.MAIL_BILLING_NOTICES:
        return
    timestamp = now()
    rows = c.execute("""SELECT s.*,u.email AS owner_email FROM shops s JOIN users u ON u.id=s.owner_id
        WHERE s.is_demo=0 AND u.verified=1 AND s.billing_status='trial'
        AND s.paddle_subscription IS NULL AND s.trial_end>? AND s.trial_end<=?""",
        (timestamp, timestamp + 72 * 3600)).fetchall()
    for shop in rows:
        ending = datetime.fromtimestamp(shop['trial_end'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        body = ('Your Kontracts trial for ' + shop['name'] + ' ends on ' + ending
                + '.\n\nChoose a subscription to continue using paid features: '
                + config.BASE_URL + '/app/billing\n\nYour no-card trial will not charge you automatically.'
                + '\nQuestions: ' + config.SUPPORT_EMAIL)
        queue_mail(c, 'trial-ending:' + shop['id'] + ':' + str(shop['trial_end']),
                   shop['owner_email'], 'Your Kontracts trial is ending', body, shop['id'],
                   channel='billing', reply_to=config.MAIL_REPLY_TO, expires_at=shop['trial_end'],
                   guard_kind='trial', guard_id=shop['id'], guard_value=str(shop['trial_end']))
