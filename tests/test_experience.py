"""Customer experience contracts; isolated SQLite and mocked provider HTTP only."""
import hashlib
import io
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import httpx
import pytest
from fastapi import HTTPException
from server import config, core, payments, postal
from server.db import db, now, packed
from server.security import decrypt, encrypt
from server.service_areas import city_id
from tests.test_app import booking, shop, select
from tests.test_workflow import request_job, issue, sign
from tests.test_media import image_bytes
from tests.test_providers import capture_http, enable_billing


def area(name='Austin',state='TX',coverage='city',codes=None):
    return {'city_id':city_id(name,state),'coverage':coverage,'zip_codes':codes or []}


def settings(owner):return owner.get('/api/auth/me').json()['shop']['settings']


def save_area(owner,areas):
    s=settings(owner);s.update(service_areas=areas,service_area_reviewed=True)
    return owner.put('/api/settings',json=s)


def quote(owner,**overrides):
    b=booking(condition='heavy');b.pop('start_ts');b.pop('accepted_policy');b.pop('has_water');b.pop('has_power')
    b.update(overrides)
    return owner.post('/api/public/shops/northline/quote-requests',files={'data':(None,json.dumps(b))})


def send(owner,base,body='Please share a photo of the rear seats.',key=None):
    return owner.post(base+'/messages',json={'body':body,'idempotency_key':key or str(uuid.uuid4())})


def upload_customer(owner,token,category='condition',key=None):
    return owner.post('/api/public/manage/'+token+'/photos',
        data={'category':category,'caption':'Customer attachment','profile':'compact','upload_key':key or str(uuid.uuid4()),'original_sizes':'[100000]'},
        files=[('files',('car.jpg',image_bytes((800,600)),'image/jpeg'))])


def other_owner(owner):
    r=owner.post('/api/auth/register',json={'name':'Other Person','email':'isolated@example.com','password':'Independent-test-password-125',
           'business':'Isolated Detail','slug':'isolated-detail','timezone':'America/Chicago','accepted_terms':True})
    assert r.status_code==201,r.text
    owner.headers['X-CSRF-Token']=r.json()['csrf']


def test_city_search_filters_and_details(owner):
    r=owner.get('/api/locations/cities?q=aus&state=TX').json()
    assert any(x['id']==city_id('Austin','TX') for x in r['cities'])
    assert all(x['state']=='TX' for x in r['cities'])
    d=owner.get('/api/locations/cities/'+city_id('Austin','TX')).json()
    assert '78701' in d['zip_codes'] and '90210' not in d['zip_codes']
    assert owner.get('/api/locations/cities?state=ZZ').status_code==422


def test_citywide_and_selected_zip_authoritative(owner):
    assert save_area(owner,[area()]).status_code==200
    assert '78702' in settings(owner)['zip_codes']
    assert save_area(owner,[area(coverage='zip_codes',codes=['78701'])]).status_code==200
    assert settings(owner)['zip_codes']==['78701']
    assert owner.get('/api/public/shops/northline/coverage?zip=78701').json()['available']
    for z in ['78702','90210','99999','7870','78701-1234']:
        r=owner.get('/api/public/shops/northline/coverage?zip='+z)
        assert r.status_code==200 and r.json()['available'] is False
    assert owner.post('/api/public/shops/northline/price',json={**select(),'zip':'78702'}).status_code==422
    assert quote(owner,zip='78702').status_code==422


@pytest.mark.parametrize('areas',[[area(coverage='zip_codes')],[area(coverage='zip_codes',codes=['90210'])],[area(),area()],[],[{'city_id':'forged','coverage':'city','zip_codes':[]}]])
def test_service_area_rejects_invalid_and_duplicate_cities(owner,areas):
    old=settings(owner)['zip_codes']
    assert save_area(owner,areas).status_code==422
    assert settings(owner)['zip_codes']==old


def test_leading_zero_zip_is_preserved(owner):
    assert save_area(owner,[area('Holtsville','NY')]).status_code==200
    assert '00501' in settings(owner)['zip_codes']
    assert owner.get('/api/public/shops/northline/coverage?zip=00501').json()['states']==['NY']


def test_unreviewed_city_prevents_publish(owner):
    with db(True) as c:
        s=core.settings(shop());s.update(service_areas=[],service_area_reviewed=False)
        c.execute('UPDATE shops SET settings=?,published=0 WHERE id=?',(packed(s),shop()['id']))
    r=owner.post('/api/publish',json={'published':True})
    assert r.status_code==422 and 'city' in r.json()['detail'].lower()


def test_city_dataset_builder_checks_nationwide_coverage_without_replacing_file():
    import zipfile
    from scripts.update_zipcodes import build
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:z.writestr('US.txt','US\t78701\tAustin\tTexas\tTX\n')
    with pytest.raises(ValueError,match='nationwide'):build(raw.getvalue())


def test_booking_chat_private_two_way_and_idempotent(owner):
    b=request_job(owner);owner_base='/api/bookings/'+b['id'];public='/api/public'+b['manage_url']
    key=str(uuid.uuid4())
    first=send(owner,owner_base,key=key);again=send(owner,owner_base,key=key)
    assert first.status_code==again.status_code==201 and first.json()['id']==again.json()['id']
    assert send(owner,owner_base,body='Changed message',key=key).status_code==409
    assert send(owner,public,body='There is pet hair in the rear seats.').status_code==201
    rows=owner.get(public+'/messages').json()['messages'];assert [x['sender'] for x in rows]==['owner','customer']
    assert owner.get('/api/conversations').json()['conversations'][0]['unread']==1
    assert owner.post(owner_base+'/messages/read').status_code==200
    assert owner.get('/api/conversations').json()['conversations'][0]['unread']==0
    with db() as c:
        job=c.execute('SELECT status,payment_status FROM bookings WHERE id=?',(b['id'],)).fetchone()
        assert tuple(job)==('pending_approval','unpaid')
        assert c.execute("SELECT count(*) FROM outbox WHERE dedupe_key LIKE 'chat:%'").fetchone()[0]==2
    assert owner.get('/api/public/manage/invalid/messages').status_code==404


def test_chat_does_not_leak_to_other_tenants_or_customers(owner):
    a=request_job(owner);b=request_job(owner,email='another@example.com')
    send(owner,'/api/bookings/'+a['id'],body='Private details')
    assert owner.get('/api/public'+b['manage_url']+'/messages').json()['messages']==[]
    other_owner(owner)
    assert owner.get('/api/bookings/'+a['id']+'/messages').status_code==404
    assert send(owner,'/api/bookings/'+a['id']).status_code==404
    assert owner.get('/api/conversations').json()['conversations']==[]
    assert owner.get('/api/customers/detail?email=test@example.com').status_code==404


@pytest.mark.parametrize('payload',[{'body':'','idempotency_key':str(uuid.uuid4())},{'body':'x'*4001,'idempotency_key':str(uuid.uuid4())},
                                     {'body':'hello','idempotency_key':'short'},{'body':'hello','idempotency_key':str(uuid.uuid4()),'sender':'owner'}])
def test_chat_input_cannot_override_sender_or_exceed_limits(owner,payload):
    b=request_job(owner)
    assert owner.post('/api/public'+b['manage_url']+'/messages',json=payload).status_code==422


def test_chat_pagination_retains_old_messages(owner):
    b=request_job(owner)
    with db(True) as c:
        for n in range(103):c.execute('INSERT INTO customer_messages(id,shop_id,booking_id,sender,body,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?)',
             (str(uuid.uuid4()),shop()['id'],b['id'],'customer','Message '+str(n),str(uuid.uuid4()),now()+n))
    base='/api/bookings/'+b['id']+'/messages'
    r=owner.get(base).json();assert len(r['messages'])==100 and r['next_before']
    old=owner.get(base+'?before='+str(r['next_before'])).json()
    assert len(old['messages'])==3 and old['next_before'] is None
    assert old['messages'][0]['body']=='Message 0'


def test_quote_chat_available_before_offer_and_retry_safe(owner):
    key=str(uuid.uuid4());r=quote(owner,idempotency_key=key)
    assert r.status_code==201,r.text
    token=r.json()['conversation_url'].split('/')[-1]
    retry=quote(owner,idempotency_key=key)
    assert retry.status_code==201 and retry.json()['conversation_url']==r.json()['conversation_url']
    changed=quote(owner,idempotency_key=key,notes='different request')
    assert changed.status_code==409
    view=owner.get('/api/public/quote-requests/'+token).json();assert not view['offer_available']
    with db() as c:q=c.execute('SELECT * FROM quotes WHERE token_hash=?',(__import__('server.security',fromlist=['digest']).digest(token),)).fetchone()
    assert send(owner,'/api/quotes/'+q['id']).status_code==201
    assert send(owner,'/api/public/quote-requests/'+token,body='The seats need stain removal.').status_code==201
    assert len(owner.get('/api/public/quote-requests/'+token+'/messages').json()['messages'])==2
    with db() as c:assert c.execute('SELECT count(*) FROM quote_submissions WHERE idempotency_key=?',(key,)).fetchone()[0]==1


def test_customer_notes_history_and_export(owner):
    b=request_job(owner)
    assert quote(owner).status_code==201
    summary=owner.get('/api/customers').json()['customers']
    entry=next(c for c in summary if c['email']=='test@example.com');assert entry['bookings']==1 and entry['quotes']==1 and entry['needs_approval']==1
    endpoint='/api/customers/notes'
    body={'email':'test@example.com','notes':'Customer prefers a morning appointment.','revision':0}
    assert owner.put(endpoint,json=body).json()['revision']==1
    assert owner.put(endpoint,json=body).status_code==409
    profile=owner.get('/api/customers/detail?email=test@example.com').json()
    assert profile['notes']==body['notes'] and len(profile['bookings'])==1
    assert 'notes' not in owner.get('/api/public'+b['manage_url']+'/messages').json()
    export=owner.get('/api/export/account.json').json()
    assert export['customer_notes'][0]['notes']==body['notes']
    assert 'messages' in export and 'reviews' in export


def test_pending_booking_optional_photo_and_proof_do_not_confirm_payment(owner):
    b=request_job(owner);raw=b['manage_url'].split('/')[-1]
    view=owner.get('/api/public/manage/'+raw+'/photos').json();assert view['can_upload'] and view['can_condition']
    assert upload_customer(owner,raw).status_code==201
    result=upload_customer(owner,raw,'payment_proof');assert result.status_code==201,result.text
    assert result.json()['photos'][0]['category']=='payment_proof'
    with db() as c:
        row=c.execute('SELECT status,payment_status FROM bookings WHERE id=?',(b['id'],)).fetchone()
        assert tuple(row)==('pending_approval','unpaid')
    assert upload_customer(owner,raw,'after').status_code==403


def test_photo_retry_rejects_relabelled_attempt(owner):
    b=request_job(owner);raw=b['manage_url'].split('/')[-1];key=str(uuid.uuid4())
    assert upload_customer(owner,raw,'condition',key).status_code==201
    assert upload_customer(owner,raw,'payment_proof',key).status_code==409


def test_review_requires_completed_signed_job_and_accepts_all_ratings(owner):
    b=request_job(owner)
    assert owner.post('/api/bookings/'+b['id']+'/request-review').status_code==409
    inv,token=issue(owner);assert sign(owner,token).status_code==200
    bid=inv['booking_id'];raw=owner.get('/api/bookings/'+bid+'/link').json()['url'].split('/')[-1]
    base='/api/public/manage/'+raw+'/review'
    assert owner.post(base,json={'rating':1,'comment':'Please improve punctuality.'}).status_code==409
    assert owner.post('/api/bookings/'+bid+'/request-review').status_code==200
    assert owner.post('/api/bookings/'+bid+'/request-review').status_code==200
    assert owner.get(base).json()['can_review'] is True
    assert owner.post(base,json={'rating':0,'comment':''}).status_code==422
    body={'rating':1,'comment':'Please improve punctuality.'}
    assert owner.post(base,json=body).status_code==200
    assert owner.post(base,json=body).status_code==200
    assert owner.post(base,json={'rating':5,'comment':'Changed'}).status_code==409
    assert owner.get(base).json()['review']['rating']==1
    assert upload_customer(owner,raw,'payment_proof').status_code==201
    assert upload_customer(owner,raw,'condition').status_code==409
    with db() as c:assert c.execute("SELECT count(*) FROM outbox WHERE dedupe_key=?",('review:'+bid,)).fetchone()[0]==1


@pytest.mark.parametrize('method',['cash','venmo','cash_app','other'])
def test_direct_payment_receipts_are_explicit_idempotent_and_immutable(owner,method):
    inv,raw=issue(owner);before=owner.get('/api/public/invoices/'+raw).json()['document_hash']
    endpoint='/api/invoices/'+inv['id']+'/received-in-person'
    body={'confirmation':'PAYMENT RECEIVED','method':method,'reference':'Sample transaction reference'}
    assert owner.post(endpoint,json=body).status_code==200
    assert owner.post(endpoint,json=body).status_code==200
    with db() as c:
        rows=c.execute('SELECT * FROM invoice_receipts WHERE invoice_id=?',(inv['id'],)).fetchall()
        assert len(rows)==1 and rows[0]['method']==method
        assert c.execute('SELECT note FROM invoice_receipt_notes WHERE receipt_id=?',(rows[0]['id'],)).fetchone()[0]==body['reference']
    after=owner.get('/api/public/invoices/'+raw).json();assert after['balance']==0 and after['document_hash']==before


def test_subscription_trial_sync_uses_only_bound_paddle_records(owner,monkeypatch):
    enable_billing(monkeypatch)
    from server.billing import binding
    sid=shop()['id']
    with db(True) as c:
        c.execute("UPDATE shops SET paddle_customer='ctm_bound' WHERE id=?",(sid,))
        c.execute('INSERT INTO billing_intents VALUES(?,?,?,?)',(sid,'txn_bound','https://example.test/checkout',now()))
    subscription={'id':'sub_bound','customer_id':'ctm_bound','status':'trialing','updated_at':'2026-10-01T00:00:00Z',
                  'custom_data':{'shop_id':sid,'binding':binding(sid,'ctm_bound')},'items':[{'price':{'id':'pri_sample'},'quantity':1}]}
    def paddle(path,body=None,method='POST'):
        assert method=='GET'
        if path=='/transactions/txn_bound':return {'id':'txn_bound','customer_id':'ctm_bound','subscription_id':'sub_bound'}
        assert path=='/subscriptions/sub_bound';return subscription
    monkeypatch.setattr(payments,'paddle',paddle)
    monkeypatch.setattr(payments,'square',lambda *a,**k:pytest.fail('Paddle sync must never call Square'))
    assert owner.post('/api/billing/sync').status_code==200
    me=owner.get('/api/auth/me').json()['shop'];assert me['has_subscription'] and me['membership']['subscribed'] and me['entitled']
    assert me['membership']['label']=='Subscribed (trial period)'
    assert 'trial' in me['membership']['note']
    assert owner.post('/api/billing/sync').status_code==200


def test_subscription_sync_rejects_foreign_customer(owner,monkeypatch):
    with db(True) as c:
        c.execute("UPDATE shops SET paddle_customer='ctm_mine' WHERE id=?",(shop()['id'],))
        c.execute('INSERT INTO billing_intents VALUES(?,?,?,?)',(shop()['id'],'txn_mine','https://example.test',now()))
    monkeypatch.setattr(payments,'paddle',lambda *a,**k:{'id':'txn_mine','customer_id':'ctm_foreign','subscription_id':'sub_foreign'})
    assert owner.post('/api/billing/sync').status_code==409
    assert shop()['paddle_subscription'] is None


def test_internal_free_trial_is_not_falsely_subscribed(owner):
    membership=owner.get('/api/auth/me').json()['shop']['membership']
    assert membership['subscribed'] is False and membership['label']=='Free trial'


def test_referral_config_is_independent_of_oauth_and_subscription(owner,monkeypatch):
    monkeypatch.setattr(config,'SQUARE_REFERRAL_URL','https://square.example/referral?code=sample')
    monkeypatch.setattr(config,'SQUARE_ID','app-square-only');monkeypatch.setattr(config,'SQUARE_SECRET','square-secret')
    monkeypatch.setattr(config,'PADDLE_KEY','')
    r=owner.post('/api/integrations/square/connect');assert r.status_code==200
    assert 'square.example/referral' not in r.json()['url'] and '/oauth2/authorize?' in r.json()['url']
    assert owner.get('/api/config').json()['square_referral_url']=='https://square.example/referral?code=sample'


def test_square_multi_location_selection_no_silent_switch(owner,monkeypatch):
    from server.square_connections import complete_oauth
    with db(True) as c:c.execute('UPDATE shops SET square_access=NULL,square_merchant=NULL,square_location=NULL WHERE id=?',(shop()['id'],))
    result={'merchant_id':'merchant-choice','access_token':'private-access','refresh_token':'private-refresh','expires_at':'2027-10-01T00:00:00Z'}
    locations=[{'id':x,'name':'Location '+x,'country':'US','currency':'USD','status':'ACTIVE','capabilities':['CREDIT_CARD_PROCESSING']} for x in ['A','B']]
    assert complete_oauth(shop(),result,locations)=='square-select-location'
    status=owner.get('/api/integrations/square/status').json()
    assert len(status['pending_locations'])==2 and not status['connected']
    assert 'private-access' not in json.dumps(status)
    monkeypatch.setattr(payments,'call',lambda *a,**k:{'locations':locations})
    assert owner.post('/api/integrations/square/location',json={'location_id':'forged'}).status_code==422
    assert owner.post('/api/integrations/square/location',json={'location_id':'B'}).status_code==200
    assert shop()['square_location']=='B' and decrypt(shop()['square_access'])=='private-access'
    assert owner.get('/api/integrations/square/status').json()['pending_locations']==[]
    assert complete_oauth(shop(),result,locations)=='square-select-location'
    assert owner.post('/api/integrations/square/location',json={'location_id':'A'}).status_code==409
    assert shop()['square_location']=='B'


def test_square_expired_location_selection_preserves_connection(owner,monkeypatch):
    from server.square_connections import complete_oauth
    result={'merchant_id':'merchant-test','access_token':'new-access','refresh_token':'new-refresh','expires_at':'2027-10-01T00:00:00Z'}
    locations=[{'id':x,'country':'US','currency':'USD','status':'ACTIVE','capabilities':['CREDIT_CARD_PROCESSING']} for x in ['A','B']]
    complete_oauth(shop(),result,locations)
    before=shop()['square_access']
    with db(True) as c:c.execute('UPDATE square_pending_connections SET expires_at=?',(now()-1,))
    assert owner.post('/api/integrations/square/location',json={'location_id':'A'}).status_code==409
    assert shop()['square_access']==before


def test_provider_invalid_json_has_actionable_safe_error(monkeypatch):
    capture_http(monkeypatch,lambda r:httpx.Response(200,content=b'not json'))
    with pytest.raises(HTTPException) as err:payments.call('https://square.invalid','/v2/x','secret',square=True)
    assert err.value.status_code==502 and 'invalid response' in err.value.detail and 'secret' not in err.value.detail


def test_provider_modules_and_ui_do_not_use_native_confirm():
    root=Path(__file__).resolve().parents[1]
    square=(root/'server/square_gateway.py').read_text();paddle=(root/'server/paddle_gateway.py').read_text()
    assert 'def billing_checkout' not in square and 'def create_checkout' not in paddle
    import re
    for filename in ['app.js','workflow.js','photos.js','branding.js']:
        assert not re.search(r'\b(?:window\.)?(?:confirm|alert)\s*\(',(root/'web/assets'/filename).read_text())
    assert 'Starter USD' not in (root/'web/assets/app.js').read_text()


def test_upgrade_unpublishes_only_unreviewed_legacy_area_and_retains_jobs(owner):
    from server.db import initialize
    b=request_job(owner)
    with db(True) as c:
        s=dict(shop());st=core.settings(s);st.pop('service_areas',None);st.pop('service_area_reviewed',None)
        c.execute('UPDATE shops SET settings=?,published=1 WHERE id=?',(packed(st),s['id']))
        c.execute('DELETE FROM schema_versions WHERE version=8')
    initialize();initialize()
    with db() as c:
        assert c.execute('SELECT published FROM shops WHERE id=?',(s['id'],)).fetchone()[0]==0
        assert c.execute('SELECT status FROM bookings WHERE id=?',(b['id'],)).fetchone()[0]=='pending_approval'
        assert c.execute("SELECT count(*) FROM audit WHERE action='service_area.review_required' AND shop_id=?",(s['id'],)).fetchone()[0]==1
    assert owner.get('/api/public'+b['manage_url']).status_code==200
    assert settings(owner)['zip_codes']==st['zip_codes']


def test_upgrade_keeps_reviewed_city_area_published(owner):
    from server.db import initialize
    with db(True) as c:c.execute('DELETE FROM schema_versions WHERE version=8')
    initialize()
    assert shop()['published']==1


def test_photo_status_rechecked_after_async_encoding(owner,monkeypatch):
    from server import media
    b=request_job(owner);token=b['manage_url'].split('/')[-1]
    encoder=media.encode_photo
    def raced(raw,profile):
        photo=encoder(raw,profile)
        with db(True) as c:c.execute("UPDATE bookings SET status='cancelled' WHERE id=?",(b['id'],))
        return photo
    monkeypatch.setattr(media,'encode_photo',raced)
    r=upload_customer(owner,token)
    assert r.status_code==409
    assert owner.get('/api/bookings/'+b['id']+'/photos').json()['photos']==[]


def test_legacy_photo_check_upgrade_retains_existing_image(owner):
    from server import media
    from server.db import initialize
    from tests.test_media import upload
    b=request_job(owner);photo=upload(owner,b['id']).json()['photos'][0]
    with db(True) as c:
        schema=c.execute("SELECT sql FROM sqlite_master WHERE name='job_photos'").fetchone()[0]
        assert "'payment_proof'" in schema
        c.execute(schema.replace('job_photos','legacy_job_photos',1).replace(",'payment_proof'",''))
        c.execute('INSERT INTO legacy_job_photos SELECT * FROM job_photos')
        c.execute('DROP TABLE job_photos');c.execute('ALTER TABLE legacy_job_photos RENAME TO job_photos')
    initialize();initialize()
    assert owner.get(photo['url']).status_code==200
    assert upload(owner,b['id'],category='payment_proof').status_code==201


def test_private_quote_chat_on_own_custom_domain(owner,monkeypatch):
    from tests.test_branding import domain
    q=quote(owner,email='custom-host@example.com').json();token=q['conversation_url'].split('/')[-1]
    host=domain(owner,monkeypatch,active=True);headers={'Host':host}
    assert owner.get('/request/'+token,headers=headers).status_code==200
    assert 'no-store' in owner.get('/request/'+token,headers=headers).headers['cache-control']
    base='/api/public/quote-requests/'+token
    assert owner.get(base,headers=headers).status_code==200
    assert owner.post(base+'/messages',headers=headers,json={'body':'Custom domain question','idempotency_key':str(uuid.uuid4())}).status_code==201
    assert owner.get(base+'/messages',headers=headers).json()['messages'][0]['body']=='Custom domain question'
    assert owner.get('/api/bookings',headers=headers).status_code==404


def test_other_custom_domain_cannot_open_private_quote(owner,monkeypatch):
    from tests.test_branding import domain, new_account
    host=domain(owner,monkeypatch,active=True)
    new_account(owner)
    response=owner.post('/api/public/shops/other-detailer/quote-requests',files={'data':(None,json.dumps({**{k:v for k,v in booking(condition='heavy').items() if k not in ('start_ts','accepted_policy','has_water','has_power')},'idempotency_key':str(uuid.uuid4())}))})
    assert response.status_code==201,response.text
    raw=response.json()['conversation_url'].split('/')[-1]
    assert owner.get('/request/'+raw,headers={'Host':host}).status_code==404
    assert owner.get('/api/public/quote-requests/'+raw+'/messages',headers={'Host':host}).status_code==404


def test_duration_used_in_real_price_and_appointment(owner):
    s=settings(owner);p=next(p for p in s['packages'] if p['id']=='full-detail')
    p['minutes']=135;p['vehicle_rates']={}
    sedan=next(v for v in s['vehicles'] if v['id']=='sedan');sedan['minutes']=0
    assert owner.put('/api/settings',json=s).status_code==200
    price=owner.post('/api/public/shops/northline/price',json=select()).json();assert price['minutes']==135
    b=request_job(owner)
    with db() as c:
        row=c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone()
        assert row['end_ts']-row['start_ts']==135*60
        assert row['busy_until']-row['end_ts']==s['buffer_minutes']*60
