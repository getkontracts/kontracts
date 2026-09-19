"""v5 approval, single-plan, invoice, US geography and abuse regression tests.
All provider calls here are mocks. No live email, payment or order is sent.
"""
import base64
import hashlib
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi import HTTPException
from server import config, core, invoices, payments, postal, worker
from server.db import db, now, uid, packed, queue_mail
from server.security import decrypt, digest
from tests.test_app import booking, select, shop


def request_job(owner, **overrides):
    response=owner.post('/api/public/shops/northline/book',json=booking(**overrides))
    assert response.status_code==201,response.text
    return response.json()


def approve(owner,b):
    r=owner.post('/api/bookings/'+b['id']+'/approve')
    assert r.status_code==200,r.text
    return r.json()


def issue(owner,received=False):
    b=request_job(owner)
    approve(owner,b)
    with db(True) as c:
        c.execute('UPDATE bookings SET start_ts=?,end_ts=?,busy_until=? WHERE id=?',(now()-7200,now()-3600,now()-1800,b['id']))
    r=owner.post('/api/bookings/'+b['id']+'/complete',json={'signer_name':'Alex Detailer','consent':True,'received_in_person':received})
    assert r.status_code==200,r.text
    with db() as c: inv=dict(c.execute('SELECT * FROM invoices WHERE id=?',(r.json()['id'],)).fetchone())
    return inv,decrypt(inv['token_encrypted'])


def signing_code(owner,raw):
    assert owner.get('/api/public/invoices/'+raw+'/pdf').status_code==200
    r=owner.post('/api/public/invoices/'+raw+'/code');assert r.status_code==200,r.text
    with db() as c:
        body=c.execute("SELECT body FROM outbox WHERE subject LIKE '%Invoice signing code%' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    return re.search(r'code is ([0-9]{6})',body).group(1)


def sign(owner,raw,code=None):
    code=code or signing_code(owner,raw)
    d=owner.get('/api/public/invoices/'+raw).json()
    return owner.post('/api/public/invoices/'+raw+'/sign',json={'signer_name':'Test Customer','consent':True,'code':code,'document_hash':d['document_hash']})


@pytest.mark.parametrize('zip_code',['00000','99999','SW1A1AA','110001','7870','787010','78701-0001','\uff17\uff18\uff17\uff10\uff11'])
def test_reject_unrecognized_or_non_us_zip(owner,zip_code):
    assert owner.post('/api/public/shops/northline/book',json=booking(zip=zip_code)).status_code==422


@pytest.mark.parametrize('changes',[{'country':'CA'},{'country':'IN'},{'state':'CA'},{'state':'XX'}])
def test_us_country_and_matching_state_required(owner,changes):
    assert owner.post('/api/public/shops/northline/book',json=booking(**changes)).status_code==422


def test_us_zip_state_and_area_verified(owner):
    b=request_job(owner,country='US',state='TX')
    with db() as c: row=c.execute('SELECT state,country FROM bookings WHERE id=?',(b['id'],)).fetchone()
    assert tuple(row)==('TX','US')
    assert owner.post('/api/public/shops/northline/book',json=booking(zip='90210',state='CA')).status_code==422


def test_fixture_postal_registry_cannot_boot_production(client,monkeypatch):
    # Fixture-only guard; on a fully installed production registry this test checks completeness instead.
    postal.registry.cache_clear()
    value=postal.registry()
    monkeypatch.setattr(config,'PRODUCTION',True);postal.registry.cache_clear()
    try:
        if value.get('fixture'):
            with pytest.raises(RuntimeError,match='full US ZIP'):postal.registry()
        else: assert len(postal.registry()['codes'])>=30000
    finally:postal.registry.cache_clear()


def test_no_payment_or_confirmation_before_approval(owner,monkeypatch):
    monkeypatch.setattr(payments,'square',lambda *a,**kw:pytest.fail('No provider call before approval'))
    b=request_job(owner)
    assert b['status']=='pending_approval' and not b.get('checkout_url')
    with db() as c:
        mails=[dict(r) for r in c.execute('SELECT * FROM outbox WHERE shop_id=?',(shop()['id'],))]
    assert mails and all(m['subject'].startswith(shop()['name']) for m in mails)
    assert not any('confirmed' in m['subject'].lower() for m in mails)
    before=len(mails);approve(owner,b);approve(owner,b)
    with db() as c:
        assert c.execute("SELECT count(*) FROM outbox WHERE dedupe_key=?",(b['id']+':approved:customer',)).fetchone()[0]==1
        subjects=[r[0] for r in c.execute('SELECT subject FROM outbox')]
    assert any('Job approved' in s for s in subjects)
    assert next(x for x in owner.get('/api/bookings').json()['bookings'] if x['id']==b['id'])['payment_method']=='in_person'


def test_only_one_competing_request_can_be_approved(owner):
    a=request_job(owner);b=request_job(owner,email='other@example.com')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda i:owner.post('/api/bookings/'+i+'/approve').status_code,[a['id'],b['id']]))
    assert sorted(results)==[200,409]


def test_unapproved_and_future_jobs_cannot_issue_invoice(owner):
    b=request_job(owner);body={'signer_name':'Owner Name','consent':True}
    assert owner.post('/api/bookings/'+b['id']+'/complete',json=body).status_code==409
    approve(owner,b)
    assert owner.post('/api/bookings/'+b['id']+'/complete',json=body).status_code==409
    assert owner.post('/api/bookings/'+b['id']+'/action',json={'action':'complete'}).status_code==409


def test_dual_signature_saved_pdf_and_no_false_payment(owner):
    inv,raw=issue(owner)
    before=owner.get('/api/invoices/'+inv['id']+'/pdf');assert before.content.startswith(b'%PDF-')
    assert b'Test Customer' not in inv['token_encrypted'].encode()
    assert owner.get('/api/public/invoices/'+raw).json()['status']=='awaiting_signature'
    assert owner.post('/api/public/invoices/'+raw+'/code').status_code==409
    r=sign(owner,raw);assert r.status_code==200,r.text
    assert r.json()['status']=='signed' and r.json()['balance']==inv['total']
    after=owner.get('/api/invoices/'+inv['id']+'/pdf');assert after.content.startswith(b'%PDF-') and after.content!=before.content
    with db() as c:
        assert c.execute('SELECT status FROM bookings WHERE id=?',(inv['booking_id'],)).fetchone()[0]=='completed'
        versions=c.execute('SELECT * FROM invoice_versions WHERE invoice_id=? ORDER BY version',(inv['id'],)).fetchall()
        assert len(versions)==2 and [v['version'] for v in versions]==[0,1]
        for v in versions:
            assert 'Test Customer' not in v['payload'] and 'Alex Detailer' not in v['payload']
            row,value,pdf=invoices.read_version(c,inv['id'],v['version'])
            assert hashlib.sha256(pdf).hexdigest()==row['pdf_sha256']
        assert value['customer_signature']['identity']=='Verified invoice email: test@example.com'
        assert value['detailer_signature']['name']=='Alex Detailer'
    export=owner.get('/api/export/account.json').json()['invoices'][0]
    assert len(export['versions'])==2 and base64.b64decode(export['versions'][1]['pdf_base64'])==after.content
    assert 'token' not in str(export.keys())


def test_wrong_codes_commit_attempt_count_and_lock(owner):
    inv,raw=issue(owner);code=signing_code(owner,raw)
    wrong='000000' if code!='000000' else '000001'
    for _ in range(5):assert sign(owner,raw,wrong).status_code==422
    assert sign(owner,raw,code).status_code==422
    with db() as c: assert c.execute('SELECT otp_attempts FROM invoices WHERE id=?',(inv['id'],)).fetchone()[0]==5
    assert sign(owner,raw,signing_code(owner,raw)).status_code==200


def test_consent_document_and_code_expiry_enforced(owner):
    inv,raw=issue(owner);code=signing_code(owner,raw)
    d=owner.get('/api/public/invoices/'+raw).json()
    body={'signer_name':'Test Customer','consent':False,'code':code,'document_hash':d['document_hash']}
    assert owner.post('/api/public/invoices/'+raw+'/sign',json=body).status_code==422
    body.update(consent=True,document_hash='0'*64)
    assert owner.post('/api/public/invoices/'+raw+'/sign',json=body).status_code==422
    with db(True) as c:c.execute('UPDATE invoices SET otp_expires=? WHERE id=?',(now()-1,inv['id']))
    assert sign(owner,raw,code).status_code==422


def test_rotating_customer_link_invalidates_previous_and_keeps_pdf(owner):
    inv,raw=issue(owner);before=owner.get('/api/invoices/'+inv['id']+'/pdf').content
    assert owner.post('/api/invoices/'+inv['id']+'/send-link').status_code==200
    assert owner.get('/api/public/invoices/'+raw).status_code==404
    with db() as c:new=decrypt(c.execute('SELECT token_encrypted FROM invoices WHERE id=?',(inv['id'],)).fetchone()[0])
    assert owner.get('/api/public/invoices/'+new).status_code==200
    assert owner.get('/api/invoices/'+inv['id']+'/pdf').content==before


def test_invoice_access_is_tenant_scoped(owner):
    inv,raw=issue(owner);owner.post('/api/auth/logout')
    assert owner.get('/api/invoices/'+inv['id']+'/pdf').status_code==401
    r=owner.post('/api/auth/register',json={'name':'Other Owner','business':'Other Detailer','email':'other-owner@example.com','password':'Long-second-owner-password','slug':'other-owner','accepted_terms':True})
    owner.headers['X-CSRF-Token']=r.json()['csrf']
    assert owner.get('/api/invoices/'+inv['id']).status_code==404
    assert owner.get('/api/invoices/'+inv['id']+'/pdf').status_code==404
    assert owner.post('/api/invoices/'+inv['id']+'/send-link').status_code==404
    assert owner.post('/api/invoices/'+inv['id']+'/received-in-person',json={'confirmation':'PAYMENT RECEIVED'}).status_code==404
    assert owner.get('/api/public/invoices/'+'a'*43).status_code==404


def test_immutable_versions_and_receipts(owner):
    inv,raw=issue(owner,received=True)
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with db(True) as c:c.execute("UPDATE invoice_versions SET pdf='x' WHERE invoice_id=?",(inv['id'],))
    with pytest.raises(sqlite3.IntegrityError,match='retained'):
        with db(True) as c:c.execute('DELETE FROM invoice_versions WHERE invoice_id=?',(inv['id'],))
    with pytest.raises(sqlite3.IntegrityError,match='retained'):
        with db(True) as c:c.execute('DELETE FROM invoice_receipts WHERE invoice_id=?',(inv['id'],))
    assert owner.get('/api/public/invoices/'+raw).json()['balance']==0


def test_integrity_failure_is_not_silently_replaced(owner):
    inv,raw=issue(owner)
    # Model a privileged storage attacker, beyond ordinary SQL/API access.
    with db(True) as c:
        c.execute('DROP TRIGGER immutable_invoice_update')
        c.execute("UPDATE invoice_versions SET seal=? WHERE invoice_id=?",('0'*64,inv['id']))
    r=owner.get('/api/invoices/'+inv['id']+'/pdf');assert r.status_code==409
    assert 'integrity' in r.json()['detail'].lower()


def test_cash_receipt_idempotent_and_has_no_checkout(owner,monkeypatch):
    inv,raw=issue(owner)
    monkeypatch.setattr(payments,'square',lambda *a,**k:pytest.fail('Cash jobs must not invoke Square'))
    assert owner.post('/api/public/invoices/'+raw+'/pay').status_code==409
    for _ in range(2):assert owner.post('/api/invoices/'+inv['id']+'/received-in-person',json={'confirmation':'PAYMENT RECEIVED'}).status_code==200
    with db() as c:assert c.execute('SELECT count(*) FROM invoice_receipts WHERE invoice_id=?',(inv['id'],)).fetchone()[0]==1
    assert owner.get('/api/public/invoices/'+raw).json()['balance']==0


def square_invoice(owner):
    inv,raw=issue(owner)
    # Provider bookkeeping fixture; protocol association and payment amount are tested below.
    with db(True) as c:c.execute("UPDATE invoices SET payment_method='square',deposit_received=3780 WHERE id=?",(inv['id'],))
    assert sign(owner,raw).status_code==200
    with db() as c:inv=dict(c.execute('SELECT * FROM invoices WHERE id=?',(inv['id'],)).fetchone())
    return inv,raw


def test_square_balance_direct_account_and_verified_only(owner,monkeypatch):
    inv,raw=square_invoice(owner);calls=[]
    def square_call(sid,path,body=None,method='POST'):
        calls.append((sid,path,body))
        return {'payment_link':{'id':'invoice-link','order_id':'invoice-order','url':'https://square.link/u/example'}}
    monkeypatch.setattr(payments,'square',square_call)
    r=owner.post('/api/public/invoices/'+raw+'/pay');assert r.status_code==200,r.text
    assert len(calls)==1 and calls[0][0]==inv['shop_id']
    payload=calls[0][2]
    assert payload['order']['line_items'][0]['base_price_money']=={'amount':15120,'currency':'USD'}
    assert 'app_fee_money' not in json.dumps(payload)
    assert owner.post('/api/public/invoices/'+raw+'/pay').status_code==200 and len(calls)==1
    assert owner.get('/api/public/invoices/'+raw).json()['balance']==15120
    assert owner.post('/api/invoices/'+inv['id']+'/received-in-person',json={'confirmation':'PAYMENT RECEIVED'}).status_code==409
    payment={'id':'balance-payment','order_id':'invoice-order','location_id':'location-test','status':'COMPLETED','amount_money':{'amount':15120,'currency':'USD'}}
    with pytest.raises(HTTPException):invoices.apply_payment('bad','merchant-test',{**payment,'amount_money':{'amount':1,'currency':'USD'}})
    payments.apply_square_payment('balance-event','merchant-test',payment)
    payments.apply_square_payment('balance-event','merchant-test',payment)
    assert owner.get('/api/public/invoices/'+raw).json()['balance']==0
    with db() as c:assert c.execute('SELECT count(*) FROM invoice_receipts WHERE invoice_id=?',(inv['id'],)).fetchone()[0]==1
    # Aggregate refunds are monotonic, deduplicated and stop further collection.
    invoices.apply_payment('refund-event','merchant-test',{**payment,'refunded_money':{'amount':1000,'currency':'USD'}})
    invoices.apply_payment('refund-event','merchant-test',{**payment,'refunded_money':{'amount':1000,'currency':'USD'}})
    after=owner.get('/api/public/invoices/'+raw).json()
    assert after['payment_review'] and after['balance_refunded']==1000 and len(after['adjustments'])==1
    assert owner.post('/api/public/invoices/'+raw+'/pay').status_code==409


def test_expired_and_completed_invoice_links_no_unsafe_actions(owner):
    inv,raw=issue(owner)
    with db(True) as c:c.execute('UPDATE invoices SET token_expires=? WHERE id=?',(now()-1,inv['id']))
    assert owner.get('/api/public/invoices/'+raw).status_code==404
    assert owner.get('/api/public/invoices/'+raw+'/pdf').status_code==404
    assert owner.post('/api/public/invoices/'+raw+'/code').status_code==404
    assert owner.get('/api/invoices/'+inv['id']+'/pdf').status_code==200


def test_demo_server_writes_and_email_disabled(client):
    with db() as c:before=c.execute('SELECT count(*) FROM bookings').fetchone()[0]
    assert client.post('/api/public/shops/northline/book',json=booking()).status_code==403
    r=client.post('/api/auth/demo');client.headers['X-CSRF-Token']=r.json()['csrf']
    for path in ['/api/bookings','/api/branding/publish','/api/publish','/api/billing/checkout']:
        assert client.post(path,json={}).status_code==403
    with db(True) as c:
        assert c.execute('SELECT count(*) FROM bookings').fetchone()[0]==before
        sid=shop()['id'];queue_mail(c,'demo-block','real@example.com','No send','No send',sid)
        assert c.execute("SELECT count(*) FROM outbox WHERE dedupe_key='demo-block'").fetchone()[0]==0
    worker.deliver()
    assert not list(config.DATA.glob('mail/*.eml'))
    demo=client.get('/demo');assert demo.status_code==200
    assert "connect-src 'none'" in demo.headers['content-security-policy']
    assert 'DEMO ONLY' in demo.text and '__OFFLINE_DEMO__' in demo.text


@pytest.mark.parametrize('bad_host',['attacker.example/api/public','example.com?x=y','user@example.com','evil\\host','example.com#fragment'])
def test_host_path_poisoning_rejected(client,bad_host):
    assert client.get('/api/health',headers={'Host':bad_host}).status_code==400


def test_request_boundary_and_limits(client):
    assert client.request('TRACE','/api/health').status_code==405
    assert client.get('/assets/app.css',headers={'Range':'bytes=0-100,200-300'}).status_code==416
    assert client.post('/api/auth/login',content='email=x&password=y',headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code==415
    assert client.post('/api/auth/login',content=b'"'+b'x'*262144+b'"',headers={'Content-Type':'application/json'}).status_code==413
    for path in ['/.env','/data/kontracts.sqlite3','/server/config.py','/scripts/backup.py']:
        assert client.get(path).status_code==404


def test_platform_brand_and_favicon_every_page(client):
    for path in ['/','/login','/register','/how-payments-work','/terms','/privacy','/book/northline','/invoice/'+'a'*43]:
        r=client.get(path);assert r.status_code==200
        assert 'rel="icon"' in r.text and ('Kontracts' in r.text or 'Northline' in r.text)
    assert client.get('/assets/favicon.svg').status_code==200


def test_single_plan_cannot_choose_hidden_tiers(owner):
    assert [(p['id'],p['amount']) for p in owner.get('/api/billing/plans').json()['plans']]==[('solo',4900)]
    for plan in ['starter','pro','business','legacy']:
        assert owner.post('/api/billing/checkout',json={'plan_id':plan}).status_code==422


def test_backup_retains_verifiable_pdf(owner,tmp_path):
    inv,raw=issue(owner);assert sign(owner,raw).status_code==200
    target=tmp_path/'backup.sqlite3'
    with sqlite3.connect(config.DB) as source,sqlite3.connect(target) as backup:
        source.backup(backup);backup.row_factory=sqlite3.Row
        assert backup.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert backup.execute('PRAGMA foreign_key_check').fetchall()==[]
        _,value,pdf=invoices.read_version(backup,inv['id'])
        assert value['customer_signature']['name']=='Test Customer' and pdf.startswith(b'%PDF-')
    if __import__('os').name!='nt':assert Path(config.DB).stat().st_mode & 0o077==0


def test_existing_square_checkout_amount_survives_deposit_refund(owner,monkeypatch):
    inv,raw=square_invoice(owner)
    monkeypatch.setattr(payments,'square',lambda *a,**k:{'payment_link':{'id':'fixed-link','order_id':'fixed-order','url':'https://square.link/u/fixed'}})
    assert owner.post('/api/public/invoices/'+raw+'/pay').status_code==200
    with db(True) as c:
        c.execute("UPDATE bookings SET square_payment='deposit-payment',deposit=?,refunded=1000 WHERE id=?",(inv['deposit_received'],inv['booking_id']))
        invoices.sync_deposit(c,c.execute('SELECT * FROM bookings WHERE id=?',(inv['booking_id'],)).fetchone())
    payment={'id':'fixed-balance','order_id':'fixed-order','location_id':'location-test','status':'COMPLETED','amount_money':{'amount':15120,'currency':'USD'}}
    assert invoices.apply_payment('after-deposit-refund','merchant-test',payment)
    result=owner.get('/api/public/invoices/'+raw).json()
    assert result['collected']==15120 and result['deposit_received']==inv['deposit_received']-1000 and result['payment_review']
    assert owner.post('/api/public/invoices/'+raw+'/pay').status_code==409
    with pytest.raises(HTTPException):
        invoices.apply_payment('wrong-refund-currency','merchant-test',{**payment,'refunded_money':{'amount':100,'currency':'CAD'}})
