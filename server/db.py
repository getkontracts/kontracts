"""SQLite WAL database. Run one application instance on a persistent local disk."""
import contextlib
import json
import sqlite3
import time
import uuid
from . import config

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password TEXT NOT NULL, name TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shops (
 id TEXT PRIMARY KEY, owner_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL, settings TEXT NOT NULL,
 published INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
 trial_end INTEGER NOT NULL, billing_status TEXT NOT NULL DEFAULT 'trial',
 paddle_customer TEXT UNIQUE, paddle_subscription TEXT UNIQUE, billing_updated_at TEXT,
 square_merchant TEXT UNIQUE, square_location TEXT, square_access TEXT,
 square_refresh TEXT, square_expires INTEGER, is_demo INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_states (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 session_hash TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
 id TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
 manage_hash TEXT UNIQUE NOT NULL, manage_encrypted TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 customer_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
 address TEXT NOT NULL, zip TEXT NOT NULL, vehicle TEXT NOT NULL, vehicle_notes TEXT NOT NULL,
 notes TEXT NOT NULL, snapshot TEXT NOT NULL, start_ts INTEGER NOT NULL,
 end_ts INTEGER NOT NULL, busy_until INTEGER NOT NULL,
 subtotal INTEGER NOT NULL CHECK(subtotal >= 0), tax INTEGER NOT NULL CHECK(tax >= 0),
 total INTEGER NOT NULL CHECK(total >= 0), deposit INTEGER NOT NULL CHECK(deposit >= 0),
 status TEXT NOT NULL, payment_status TEXT NOT NULL DEFAULT 'unpaid',
 hold_until INTEGER, checkout_url TEXT, square_order TEXT UNIQUE, square_link TEXT,
 square_payment TEXT UNIQUE, refunded INTEGER NOT NULL DEFAULT 0, refund_id TEXT,
 quote_id TEXT, accepted_policy TEXT NOT NULL, created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 UNIQUE(shop_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS bookings_schedule ON bookings(shop_id,start_ts,busy_until);
CREATE TABLE IF NOT EXISTS blocks (
 id TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
 start_ts INTEGER NOT NULL, end_ts INTEGER NOT NULL, label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
 id TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
 token_hash TEXT UNIQUE NOT NULL, token_encrypted TEXT NOT NULL,
 customer_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
 address TEXT NOT NULL, zip TEXT NOT NULL, vehicle TEXT NOT NULL, vehicle_notes TEXT NOT NULL,
 notes TEXT NOT NULL, selections TEXT NOT NULL, status TEXT NOT NULL,
 amount INTEGER, minutes INTEGER, message TEXT, expires_at INTEGER,
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
 id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
 path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
 provider TEXT NOT NULL, event_id TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(provider,event_id)
);
CREATE TABLE IF NOT EXISTS billing_intents (
 shop_id TEXT PRIMARY KEY REFERENCES shops(id) ON DELETE CASCADE,
 transaction_id TEXT NOT NULL, checkout_url TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
 shop_id TEXT REFERENCES shops(id) ON DELETE CASCADE,
 recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 due_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 sent_at INTEGER, claimed_at INTEGER, last_error TEXT,
 booking_id TEXT, expected_start INTEGER
);
CREATE TABLE IF NOT EXISTS rate_limits (
 key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id TEXT PRIMARY KEY, shop_id TEXT REFERENCES shops(id) ON DELETE CASCADE,
 action TEXT NOT NULL, entity_id TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
INSERT OR IGNORE INTO schema_versions VALUES(1,strftime('%s','now'));
'''

def connect():
    c = sqlite3.connect(config.DB, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=15000')
    c.execute('PRAGMA secure_delete=ON')
    c.execute('PRAGMA trusted_schema=OFF')
    return c

@contextlib.contextmanager
def db(write=False):
    c = connect()
    try:
        if write: c.execute('BEGIN IMMEDIATE')
        yield c
        if write: c.commit()
    except BaseException:
        if write: c.rollback()
        raise
    finally: c.close()

def initialize():
    with db() as c:
        c.executescript(SCHEMA)
        from .media import initialize_media
        initialize_media(c)
        from .branding import initialize as initialize_branding
        initialize_branding(c)
        from .release_schema import initialize as initialize_release
        initialize_release(c)
        from .workflow_schema import initialize as initialize_workflow
        initialize_workflow(c)
        from .email_schema import initialize as initialize_email
        initialize_email(c)
    from pathlib import Path
    import os
    if os.name != 'nt':
        for suffix in ('','-wal','-shm'):
            path=Path(str(config.DB)+suffix)
            if path.exists(): path.chmod(0o600)

def uid(): return str(uuid.uuid4())
def now(): return int(time.time())
def packed(value): return json.dumps(value, separators=(',', ':'))
def audit(c, shop_id, action, entity=None):
    c.execute('INSERT INTO audit VALUES(?,?,?,?,?)', (uid(), shop_id, action, entity, now()))

def queue_mail(c, key, recipient, subject, body, shop_id=None, due=None, booking_id=None, expected_start=None,
               *, channel=None, reply_to=None, expires_at=None, guard_kind=None, guard_id=None, guard_value=None):
    """Durably queue a single-recipient message in the caller's transaction."""
    from .mail import CHANNELS, GUARDS, mailbox, identity
    channel = channel or ('notifications' if shop_id else 'accounts')
    if channel not in CHANNELS:
        raise ValueError('Unknown mail channel.')
    if guard_kind and (guard_kind not in GUARDS or not guard_id):
        raise ValueError('Invalid mail validity guard.')
    shop = c.execute('SELECT * FROM shops WHERE id=?', (shop_id,)).fetchone() if shop_id else None
    if shop_id and not shop:
        raise ValueError('Email business does not exist.')
    if shop and shop['is_demo']:
        return
    if shop and channel == 'notifications' and shop['name'] not in subject:
        subject = shop['name'] + ' | ' + subject
    recipient = mailbox(recipient, 'Recipient', address_only=True)[1]
    if not isinstance(subject, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in subject):
        raise ValueError('Invalid mail subject.')
    if not isinstance(body, str):
        raise ValueError('Email body must be text.')
    if reply_to is not None:
        reply_to = mailbox(reply_to, 'Reply-To', address_only=True)[1]
    else:
        reply_to = identity({'mail_channel': channel, 'shop_id': shop_id}, shop)['reply_to']
    timestamp = now()
    expiry = expires_at if expires_at is not None else timestamp + 7 * 86400
    c.execute("""INSERT OR IGNORE INTO outbox
        (id,dedupe_key,shop_id,recipient,subject,body,due_at,booking_id,expected_start,
         mail_channel,reply_to,expires_at,guard_kind,guard_id,guard_value)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid(), key, shop_id, recipient, subject, body, timestamp if due is None else due,
         booking_id, expected_start, channel, reply_to, expiry, guard_kind, guard_id, guard_value))
