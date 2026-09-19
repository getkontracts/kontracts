"""Read-only founder overview. Run only on your server, never expose as a public endpoint."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.db import db,now
with db() as c:
    result={'accounts':c.execute('SELECT count(*) FROM shops WHERE is_demo=0').fetchone()[0],
        'stored_job_photos':c.execute('SELECT count(*) FROM job_photos').fetchone()[0],
        'stored_photo_bytes':c.execute('SELECT coalesce(sum(size_bytes),0) FROM job_photos').fetchone()[0]+c.execute('SELECT coalesce(sum(size_bytes),0) FROM photos').fetchone()[0],
        'active_subscriptions':c.execute("SELECT count(*) FROM shops WHERE is_demo=0 AND billing_status='active'").fetchone()[0],
        'payment_reviews':c.execute("SELECT count(*) FROM bookings WHERE status='payment_review'").fetchone()[0],
        'exhausted_email_retries':c.execute('SELECT count(*) FROM outbox WHERE sent_at IS NULL AND cancelled_at IS NULL AND attempts>=8').fetchone()[0],
        'expired_holds_not_cleaned':c.execute("SELECT count(*) FROM bookings WHERE status='held' AND hold_until<?",(now(),)).fetchone()[0]}
print(json.dumps(result,indent=2))
