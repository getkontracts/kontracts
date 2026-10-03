"""Additive customer experience migrations. Existing money and signatures are unchanged."""
def initialize(c):
    sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='job_photos'").fetchone()[0]
    if "payment_proof" not in sql:
        # No table references job_photos in earlier releases. Rebuild its CHECK atomically.
        c.execute('BEGIN IMMEDIATE')
        try:
            c.execute(sql.replace('job_photos', 'job_photos_v6', 1).replace("'before','after','condition'", "'before','after','condition','payment_proof'"))
            c.execute('INSERT INTO job_photos_v6 SELECT * FROM job_photos')
            c.execute('DROP TABLE job_photos')
            c.execute('ALTER TABLE job_photos_v6 RENAME TO job_photos')
            c.execute('CREATE INDEX job_photos_shop ON job_photos(shop_id,created_at)')
            c.execute('CREATE INDEX job_photos_booking ON job_photos(booking_id)')
            c.commit()
        except BaseException:
            c.rollback();raise
    c.executescript("""
    CREATE TABLE IF NOT EXISTS customer_messages (
      id TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      booking_id TEXT REFERENCES bookings(id) ON DELETE CASCADE,
      quote_id TEXT REFERENCES quotes(id) ON DELETE CASCADE,
      sender TEXT NOT NULL CHECK(sender IN ('owner','customer')), body TEXT NOT NULL,
      idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL, read_at INTEGER,
      CHECK((booking_id IS NULL) != (quote_id IS NULL)), UNIQUE(shop_id,sender,idempotency_key)
    );
    CREATE INDEX IF NOT EXISTS messages_booking ON customer_messages(booking_id,created_at);
    CREATE INDEX IF NOT EXISTS messages_quote ON customer_messages(quote_id,created_at);
    CREATE INDEX IF NOT EXISTS messages_unread ON customer_messages(shop_id,sender,read_at);
    CREATE TABLE IF NOT EXISTS job_reviews (
      booking_id TEXT PRIMARY KEY REFERENCES bookings(id) ON DELETE CASCADE,
      shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      requested_at INTEGER NOT NULL, rating INTEGER CHECK(rating BETWEEN 1 AND 5),
      comment TEXT, submitted_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS customer_notes (
      shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      email TEXT NOT NULL COLLATE NOCASE, notes TEXT NOT NULL DEFAULT '',
      revision INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
      PRIMARY KEY(shop_id,email)
    );
    CREATE TABLE IF NOT EXISTS quote_submissions (
      shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
      quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
      PRIMARY KEY(shop_id,idempotency_key)
    );
    CREATE TABLE IF NOT EXISTS invoice_receipt_notes (
      receipt_id TEXT PRIMARY KEY REFERENCES invoice_receipts(id),
      note TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS immutable_receipt_note_update BEFORE UPDATE ON invoice_receipt_notes
      BEGIN SELECT RAISE(ABORT,'Payment receipt notes are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS immutable_receipt_note_delete BEFORE DELETE ON invoice_receipt_notes
      BEGIN SELECT RAISE(ABORT,'Payment receipt notes are retained'); END;
    CREATE TABLE IF NOT EXISTS square_pending_connections (
      shop_id TEXT PRIMARY KEY REFERENCES shops(id) ON DELETE CASCADE,
      merchant_id TEXT NOT NULL, access_encrypted TEXT NOT NULL, refresh_encrypted TEXT NOT NULL,
      token_expires INTEGER NOT NULL, locations TEXT NOT NULL, expires_at INTEGER NOT NULL
    );
    INSERT OR IGNORE INTO schema_versions VALUES(7,strftime('%s','now'));
    """)

    # A legacy ZIP list must never be silently interpreted as citywide consent.
    # Unpublish only affected pages once; bookings, capabilities and receipts survive.
    if not c.execute('SELECT 1 FROM schema_versions WHERE version=8').fetchone():
        import json
        from .db import audit
        c.execute('BEGIN IMMEDIATE')
        try:
            for shop in c.execute('SELECT id,settings,published FROM shops').fetchall():
                settings = json.loads(shop['settings'])
                if shop['published'] and (not settings.get('service_areas') or not settings.get('service_area_reviewed')):
                    c.execute('UPDATE shops SET published=0 WHERE id=?', (shop['id'],))
                    audit(c, shop['id'], 'service_area.review_required')
            c.execute("INSERT OR IGNORE INTO schema_versions VALUES(8,strftime('%s','now'))")
            c.commit()
        except BaseException:
            c.rollback()
            raise
