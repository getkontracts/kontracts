"""Additive v5 migrations; never fabricate historical customer signatures."""
def initialize(c):
    shop_cols={r[1] for r in c.execute('PRAGMA table_info(shops)')}
    if 'legacy_custom_domain' not in shop_cols:
        c.execute('ALTER TABLE shops ADD COLUMN legacy_custom_domain INTEGER NOT NULL DEFAULT 0')
        c.execute("UPDATE shops SET legacy_custom_domain=1 WHERE paddle_subscription IS NOT NULL AND plan_id IN ('legacy','business')")
    cols={r[1] for r in c.execute('PRAGMA table_info(bookings)')}
    additions={'approved_at':'INTEGER','approved_by':'TEXT','payment_method':"TEXT NOT NULL DEFAULT 'in_person'",'state':"TEXT NOT NULL DEFAULT ''",'country':"TEXT NOT NULL DEFAULT 'US'"}
    for name,spec in additions.items():
        if name not in cols: c.execute('ALTER TABLE bookings ADD COLUMN '+name+' '+spec)
    if not c.execute('SELECT 1 FROM schema_versions WHERE version=5').fetchone():
        c.execute("UPDATE bookings SET approved_at=created_at WHERE status IN ('confirmed','completed','held','no_show','payment_review')")
        c.execute("UPDATE bookings SET payment_method='square' WHERE square_order IS NOT NULL OR deposit>0")
        c.execute("UPDATE shops SET plan_id='solo' WHERE paddle_subscription IS NULL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS invoices (
      id TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id),
      booking_id TEXT NOT NULL UNIQUE REFERENCES bookings(id), number TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE, token_encrypted TEXT NOT NULL, token_expires INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('awaiting_signature','signed')),
      created_at INTEGER NOT NULL, signed_at INTEGER, payment_method TEXT NOT NULL,
      total INTEGER NOT NULL CHECK(total>=0), deposit_received INTEGER NOT NULL CHECK(deposit_received>=0),
      collected INTEGER NOT NULL DEFAULT 0 CHECK(collected>=0),
      square_order TEXT UNIQUE, square_link TEXT, checkout_url TEXT, square_payment TEXT UNIQUE,
      otp_hash TEXT, otp_expires INTEGER, otp_attempts INTEGER NOT NULL DEFAULT 0,
      pdf_viewed_at INTEGER, UNIQUE(shop_id,number)
    );
    CREATE TABLE IF NOT EXISTS invoice_versions (
      id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id), version INTEGER NOT NULL,
      payload TEXT NOT NULL, pdf TEXT NOT NULL, pdf_sha256 TEXT NOT NULL,
      seal TEXT NOT NULL, created_at INTEGER NOT NULL, UNIQUE(invoice_id,version)
    );
    CREATE TRIGGER IF NOT EXISTS immutable_invoice_update BEFORE UPDATE ON invoice_versions
      BEGIN SELECT RAISE(ABORT,'Signed invoice versions are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_invoice_delete BEFORE DELETE ON invoice_versions
      BEGIN SELECT RAISE(ABORT,'Signed invoice versions are retained'); END;
    CREATE TABLE IF NOT EXISTS invoice_receipts (
      id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id),
      amount INTEGER NOT NULL CHECK(amount>0), method TEXT NOT NULL, reference TEXT NOT NULL UNIQUE,
      recorded_at INTEGER NOT NULL, recorded_by TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS immutable_receipt_update BEFORE UPDATE ON invoice_receipts
      BEGIN SELECT RAISE(ABORT,'Payment receipts are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_receipt_delete BEFORE DELETE ON invoice_receipts
      BEGIN SELECT RAISE(ABORT,'Payment receipts are retained'); END;
    INSERT OR IGNORE INTO schema_versions VALUES(5,strftime('%s','now'));
    """)

    invoice_cols={r[1] for r in c.execute('PRAGMA table_info(invoices)')}
    for name,spec in {'checkout_amount':'INTEGER NOT NULL DEFAULT 0','balance_refunded':'INTEGER NOT NULL DEFAULT 0','payment_review':'INTEGER NOT NULL DEFAULT 0','last_checked_at':'INTEGER NOT NULL DEFAULT 0'}.items():
        if name not in invoice_cols: c.execute('ALTER TABLE invoices ADD COLUMN '+name+' '+spec)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS invoice_adjustments(
        id TEXT PRIMARY KEY,invoice_id TEXT NOT NULL REFERENCES invoices(id),amount INTEGER NOT NULL CHECK(amount>0),
        reason TEXT NOT NULL,reference TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS immutable_adjustment_update BEFORE UPDATE ON invoice_adjustments
      BEGIN SELECT RAISE(ABORT,'Payment adjustments are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_adjustment_delete BEFORE DELETE ON invoice_adjustments
      BEGIN SELECT RAISE(ABORT,'Payment adjustments are retained'); END;
    """)

    if 'checkout_amount' not in invoice_cols:
        c.execute('UPDATE invoices SET checkout_amount=total-deposit_received WHERE square_order IS NOT NULL')
