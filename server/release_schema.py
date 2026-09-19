"""Idempotent v4 migrations. Existing businesses retain legacy entitlements."""
def initialize(c):
    cols = {r[1] for r in c.execute('PRAGMA table_info(shops)')}
    if 'plan_id' not in cols:
        c.execute("ALTER TABLE shops ADD COLUMN plan_id TEXT NOT NULL DEFAULT 'legacy'")
    if 'settings_revision' not in cols:
        c.execute('ALTER TABLE shops ADD COLUMN settings_revision INTEGER NOT NULL DEFAULT 0')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS billing_plan_intents (
      shop_id TEXT PRIMARY KEY REFERENCES shops(id) ON DELETE CASCADE,
      plan_id TEXT NOT NULL, price_id TEXT NOT NULL, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS billing_changes (
      token_hash TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      subscription_id TEXT NOT NULL, plan_id TEXT NOT NULL, price_id TEXT NOT NULL,
      previous_plan TEXT NOT NULL, mode TEXT NOT NULL, preview TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS google_flows (
      state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, nonce TEXT NOT NULL,
      verifier TEXT NOT NULL, purpose TEXT NOT NULL, user_id TEXT,
      session_hash TEXT, plan_id TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS google_identities (
      subject TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      email TEXT NOT NULL, linked_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS google_pending (
      token_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, email TEXT NOT NULL,
      name TEXT NOT NULL, plan_id TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS account_auth (
      user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      password_enabled INTEGER NOT NULL DEFAULT 1
    );
    INSERT OR IGNORE INTO schema_versions VALUES(4,strftime('%s','now'));
    ''')
