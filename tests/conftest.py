import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['TESTING']='true'
os.environ['DEMO_MODE']='true'
os.environ['APP_ENV']='development'
# Never let a developer's live .env send mail during regression tests.
for _key in ('RESEND_API_KEY', 'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD'):
    os.environ[_key] = ''
for _key, _value in {
    'MAIL_FROM': 'Kontracts <hello@example.com>',
    'MAIL_ACCOUNTS_FROM': 'Kontracts <accounts@example.com>',
    'MAIL_NOTIFICATIONS_FROM': 'Kontracts <notifications@example.com>',
    'MAIL_BILLING_FROM': 'Kontracts Billing <billing@example.com>',
    'MAIL_SENDER_DOMAIN': 'example.com',
    'MAIL_REPLY_TO': 'support@example.com', 'SUPPORT_EMAIL': 'support@example.com',
    'MAIL_ENABLED': 'true', 'MAIL_BILLING_NOTICES': 'true',
    'MAIL_DAILY_LIMIT': '90', 'MAIL_31DAY_LIMIT': '2700', 'MAIL_SEND_INTERVAL': '0',
}.items():
    os.environ[_key] = _value
import pytest
from fastapi.testclient import TestClient
from server import config
from server.app import app
from server.security import PASSWORDS
OWNER_PASSWORD_HASH=PASSWORDS.hash('Test-owner-password-12345')

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    monkeypatch.setattr(config,'DB',str(tmp_path/'test.sqlite3'))
    with TestClient(app,headers={'X-Requested-With':'Kontracts'}) as c: yield c

@pytest.fixture
def owner(client,monkeypatch):
    r=client.post('/api/auth/demo');assert r.status_code==200
    client.headers['X-CSRF-Token']=r.json()['csrf']
    # Real test tenant: demo writes are deliberately forbidden in v5.
    from server.db import db,packed,now
    from server.security import PASSWORDS,encrypt
    from server import core
    with db(True) as c:
        shop=c.execute("SELECT * FROM shops WHERE slug='northline'").fetchone()
        settings=core.settings(shop);settings['payment_method']='in_person'
        c.execute("UPDATE shops SET is_demo=0,settings=?,square_access=?,square_location='location-test',square_merchant='merchant-test' WHERE id=?",(packed(settings),encrypt('test-token'),shop['id']))
        c.execute('UPDATE users SET password=? WHERE id=?',(OWNER_PASSWORD_HASH,shop['owner_id']))
        c.execute("UPDATE bookings SET approved_at=created_at,payment_method='square',state='TX' WHERE shop_id=?",(shop['id'],))
    monkeypatch.setattr(config,'SQUARE_WEBHOOK','test-square-hook')
    return client
