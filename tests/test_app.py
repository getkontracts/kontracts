import base64
import hashlib
import hmac
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from PIL import Image
from server import config,core,models,payments,worker
from server.db import db,now,uid
from server.security import digest,decrypt,billing_binding,verify_square,verify_paddle


def shop():
    with db() as c:return c.execute("SELECT * FROM shops WHERE slug='northline'").fetchone()

def select():return {'package_id':'full-detail','vehicle_id':'sedan','extra_ids':[],'zip':'78701','condition':'standard','quote_token':None}

def available(s=None,minutes=180):
    s=s or shop();today=datetime.fromtimestamp(now(),ZoneInfo(core.settings(s)['timezone'])).date()
    with db() as c:
        for i in range(3,40):
            date=(today+timedelta(days=i)).isoformat();slots=core.day_slots(c,s,date,minutes)
            if slots:return date,slots
    raise AssertionError('No slots')

def booking(**overrides):
    b={**select(),'customer_name':'Test Customer','email':'test@example.com','phone':'5125550123','address':'100 Sample Street, Austin, TX',
       'vehicle_notes':'2022 Sedan','notes':'Test appointment','start_ts':available()[1][0]['start_ts'],
       'idempotency_key':uid(),'accepted_policy':True,'has_water':False,'has_power':False,'website':''}
    b.update(overrides);return b


def test_health_and_headers(client):
    r=client.get('/api/health');assert r.json()['status']=='ok';assert r.headers['x-content-type-options']=='nosniff';assert r.headers['cache-control']=='no-store'

def test_demo_session_is_httponly(client):
    r=client.post('/api/auth/demo');assert r.status_code==200;assert 'httponly' in r.headers['set-cookie'].lower();assert 'samesite=lax' in r.headers['set-cookie'].lower()

def test_private_dashboard_needs_session(client):assert client.get('/api/dashboard').status_code==401

def test_missing_request_context_rejected(client):
    r=client.post('/api/auth/demo',headers={'X-Requested-With':'other'});assert r.status_code==403

def test_cross_origin_write_rejected(owner):
    r=owner.post('/api/publish',json={'published':False},headers={'Origin':'https://evil.example'});assert r.status_code==403

def test_csrf_required(owner):
    r=owner.post('/api/publish',json={'published':False},headers={'X-CSRF-Token':'wrong'});assert r.status_code==403

def test_logout_revokes_session(owner):
    assert owner.post('/api/auth/logout').status_code==200;assert owner.get('/api/auth/me').status_code==401

def test_register_validation_and_unverified_publish(client):
    body={'name':'Owner Person','email':'owner@example.com','password':'a strong passphrase 123','business':'Clean Detail','slug':'clean-detail','timezone':'America/Chicago','accepted_terms':True}
    r=client.post('/api/auth/register',json=body);assert r.status_code==201
    client.headers['X-CSRF-Token']=r.json()['csrf'];assert client.get('/api/auth/me').json()['user']['verified'] is False
    assert client.post('/api/publish',json={'published':True}).status_code==403
    with db() as c:
        u=c.execute('SELECT * FROM users WHERE email=?',('owner@example.com',)).fetchone();assert u['password'].startswith('$argon2id$')

def test_password_minimum(client):
    r=client.post('/api/auth/register',json={'name':'A B','email':'a@example.com','password':'short','business':'Clean','slug':'clean','accepted_terms':True});assert r.status_code==422

def test_control_characters_rejected(client):
    r=client.post('/api/auth/register',json={'name':'Bad\nName','email':'a@example.com','password':'long-enough-password','business':'Clean','slug':'clean','accepted_terms':True});assert r.status_code==422

def test_invalid_login_is_generic(client):
    r=client.post('/api/auth/login',json={'email':'unknown@example.com','password':'anything'});assert r.status_code==401;assert 'email or password' in r.json()['detail']

def test_pricing_uses_integer_cents(client):
    r=client.post('/api/public/shops/northline/price',json={**select(),'vehicle_id':'suv','extra_ids':['pet-hair']});assert r.status_code==200
    p=r.json();assert p['subtotal']==24900;assert p['deposit']==0;assert p['minutes']==240

def test_client_price_injection_rejected(client):assert client.post('/api/public/shops/northline/price',json={**select(),'total':1}).status_code==422

def test_duplicate_addons_rejected(client):assert client.post('/api/public/shops/northline/price',json={**select(),'extra_ids':['pet-hair','pet-hair']}).status_code==422

def test_unknown_service_rejected(client):assert client.post('/api/public/shops/northline/price',json={**select(),'package_id':'fake'}).status_code==422

def test_zip_outside_area_rejected(client):assert client.post('/api/public/shops/northline/price',json={**select(),'zip':'90210'}).status_code==422

def test_heavy_condition_requires_review(client):assert client.post('/api/public/shops/northline/price',json={**select(),'condition':'heavy'}).status_code==422

def test_slot_generation_and_timezone(client):
    with db(True) as c: c.execute('DELETE FROM bookings')
    date,slots=available();r=client.post('/api/public/shops/northline/slots',json={**select(),'date':date})
    assert r.status_code==200;assert r.json()['timezone']=='America/Chicago';assert len(r.json()['slots'])>0
    assert datetime.fromtimestamp(slots[0]['start_ts'],ZoneInfo('America/Chicago')).hour==8

def test_invalid_day_rejected(client):assert client.post('/api/public/shops/northline/slots',json={**select(),'date':'2026-99-01'}).status_code==422

def test_past_days_empty(client):assert client.post('/api/public/shops/northline/slots',json={**select(),'date':'2020-01-01'}).json()['slots']==[]

def test_booking_idempotency_and_manage_token(owner):
    client=owner
    b=booking();a=client.post('/api/public/shops/northline/book',json=b);assert a.status_code==201,a.text
    again=client.post('/api/public/shops/northline/book',json=b);assert again.json()['id']==a.json()['id'];assert a.json()['checkout_url'] is None
    assert a.json()['status']=='pending_approval';assert 'manage_hash' not in a.json()

def test_idempotency_payload_change_rejected(owner):
    client=owner
    b=booking();assert client.post('/api/public/shops/northline/book',json=b).status_code==201
    b['notes']='changed';assert client.post('/api/public/shops/northline/book',json=b).status_code==409

def test_double_booking_rejected(owner):
    client=owner
    b=booking();created=client.post('/api/public/shops/northline/book',json=b);assert created.status_code==201
    first=created.json()['id']
    assert client.post('/api/bookings/'+first+'/approve').status_code==200
    b['idempotency_key']=uid();assert client.post('/api/public/shops/northline/book',json=b).status_code==409

def test_concurrent_reservation_is_atomic(owner):
    client=owner
    s=shop();b=booking();one=models.BookingInput(**b);two=models.BookingInput(**{**b,'idempotency_key':uid()})
    def attempt(v):
        try:return core.reserve(s['id'],v)[0]['status']
        except Exception as exc:return getattr(exc,'status_code',str(exc))
    with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(attempt,[one,two]))
    assert sorted(map(str,r))==['pending_approval','pending_approval'] # requests do not hold slots; approval is atomic

def test_deposit_confirmation_and_cancellation_separate(owner):
    client=owner
    r=client.post('/api/public/shops/northline/book',json=booking()).json();raw=r['manage_url'].split('/')[-1]
    assert client.post('/api/bookings/'+r['id']+'/approve').status_code==200
    data=client.get('/api/public/manage/'+raw).json();assert data['booking']['status']=='confirmed';assert data['booking']['payment_status']=='unpaid'
    assert client.post('/api/public/manage/'+raw+'/cancel').status_code==200
    b=client.get('/api/public/manage/'+raw).json()['booking'];assert b['status']=='cancelled';assert b['payment_status']=='unpaid'

def test_manual_booking_does_not_fake_payment(owner):
    r=owner.post('/api/bookings',json=booking());assert r.status_code==200
    assert r.json()['status']=='pending_approval';assert r.json()['payment_status']=='unpaid'

def test_blocked_times_not_bookable(owner):
    _,slots=available();t=slots[0]['start_ts'];assert owner.post('/api/blocks',json={'start_ts':t,'end_ts':t+3600,'label':'Personal'}).status_code==200
    assert owner.post('/api/bookings',json=booking(start_ts=t)).status_code==409

def test_block_cannot_overlap_booking(owner):
    b=booking();created=owner.post('/api/bookings',json=b).json();assert owner.post('/api/bookings/'+created['id']+'/approve').status_code==200
    assert owner.post('/api/blocks',json={'start_ts':b['start_ts'],'end_ts':b['start_ts']+1800,'label':'Overlap'}).status_code==409

def test_customer_reschedule_preserves_deposit(owner):
    client=owner
    r=client.post('/api/public/shops/northline/book',json=booking()).json();raw=r['manage_url'].split('/')[-1];client.post('/api/bookings/'+r['id']+'/approve')
    before=client.get('/api/public/manage/'+raw).json()['booking'];date,slots=available()
    later=next(x['start_ts'] for x in slots if x['start_ts']>before['busy_until'])
    assert client.post('/api/public/manage/'+raw+'/reschedule',json={'start_ts':later}).status_code==200
    after=client.get('/api/public/manage/'+raw).json()['booking'];assert after['deposit']==before['deposit'];assert after['payment_status']=='unpaid';assert after['start_ts']==later

def test_completion_future_rejected(owner):
    r=owner.post('/api/bookings',json=booking()).json();assert owner.post('/api/bookings/'+r['id']+'/action',json={'action':'complete'}).status_code==409

def test_tenant_isolation(client):
    with db() as c: existing=c.execute('SELECT id FROM bookings LIMIT 1').fetchone()[0]
    body={'name':'Second Owner','email':'two@example.com','password':'a strong passphrase 456','business':'Another Detail','slug':'another-detail','accepted_terms':True}
    r=client.post('/api/auth/register',json=body);client.headers['X-CSRF-Token']=r.json()['csrf']
    assert client.get('/api/bookings').json()['bookings']==[]
    assert client.post('/api/bookings/'+existing+'/action',json={'action':'cancel'}).status_code==404
    assert client.get('/api/bookings/'+existing+'/link').status_code==404

def test_expired_hold_releases_slot_and_does_not_confirm(owner):
    client=owner
    data=booking();r=client.post('/api/public/shops/northline/book',json=data).json()
    with db(True) as c:c.execute("UPDATE bookings SET status='held',hold_until=? WHERE id=?",(now()-1,r['id']))
    worker.maintenance();assert client.get('/api/public'+r['manage_url']).json()['booking']['status']=='expired'
    assert client.post('/api/demo-checkout/'+r['manage_url'].split('/')[-1]).status_code==403

def test_square_hmac_and_paddle_replay_protection(client,monkeypatch):
    monkeypatch.setattr(config,'SQUARE_WEBHOOK','square-secret');raw=b'{"hello":"world"}'
    sig=base64.b64encode(hmac.new(b'square-secret',(config.BASE_URL+'/api/webhooks/square').encode()+raw,hashlib.sha256).digest()).decode()
    assert verify_square(raw,sig);assert not verify_square(raw+b' ',sig)
    monkeypatch.setattr(config,'PADDLE_WEBHOOK','paddle-secret');ts=str(now());hs=hmac.new(b'paddle-secret',ts.encode()+b':'+raw,hashlib.sha256).hexdigest()
    assert verify_paddle(raw,f'ts={ts};h1={hs}');assert not verify_paddle(raw,f'ts={int(ts)-100};h1={hs}');assert not verify_paddle(raw,'bad')

def test_unverified_webhooks_rejected(client):
    assert client.post('/api/webhooks/square',json={}).status_code==401;assert client.post('/api/webhooks/paddle',json={}).status_code==401

def setup_provider_booking(client):
    r=client.post('/api/public/shops/northline/book',json=booking()).json()
    with db(True) as c:
        c.execute("UPDATE shops SET square_merchant='merchant-test' WHERE slug='northline'")
        c.execute("UPDATE bookings SET square_order='order-test',payment_method='square',deposit=3780,status='held',approved_at=?,hold_until=? WHERE id=?",(now(),now()+3600,r['id']))
        b=c.execute('SELECT * FROM bookings WHERE id=?',(r['id'],)).fetchone()
    return b

def test_verified_square_payment_confirms_and_deduplicates(owner):
    client=owner
    b=setup_provider_booking(client);p={'id':'payment-test','order_id':'order-test','status':'COMPLETED','amount_money':{'amount':b['deposit'],'currency':'USD'}}
    payments.apply_square_payment('event-1','merchant-test',p);payments.apply_square_payment('event-1','merchant-test',p)
    with db() as c:
        after=c.execute('SELECT * FROM bookings WHERE id=?',(b['id'],)).fetchone();assert after['status']=='confirmed';assert after['payment_status']=='paid'
        assert c.execute("SELECT count(*) FROM webhook_events WHERE event_id='event-1'").fetchone()[0]==1

def test_wrong_payment_amount_never_confirms(owner):
    client=owner
    b=setup_provider_booking(client)
    try:payments.apply_square_payment('event-wrong','merchant-test',{'id':'p','order_id':'order-test','status':'COMPLETED','amount_money':{'amount':1,'currency':'USD'}})
    except Exception as ex:assert ex.status_code==400
    with db() as c:assert c.execute('SELECT status FROM bookings WHERE id=?',(b['id'],)).fetchone()[0]=='held'

def test_late_payment_goes_to_review(owner):
    client=owner
    b=setup_provider_booking(client)
    with db(True) as c:c.execute('UPDATE bookings SET hold_until=? WHERE id=?',(now()-1,b['id']))
    payments.apply_square_payment('event-late','merchant-test',{'id':'payment-late','order_id':'order-test','status':'COMPLETED','amount_money':{'amount':b['deposit'],'currency':'USD'}})
    with db() as c:assert c.execute('SELECT status FROM bookings WHERE id=?',(b['id'],)).fetchone()[0]=='payment_review'

def test_refund_is_explicit_and_idempotent(owner,monkeypatch):
    b=setup_provider_booking(owner);r={'id':b['id']}
    payments.apply_square_payment('paid-refund','merchant-test',{'id':'p-refund','order_id':'order-test','status':'COMPLETED','amount_money':{'amount':b['deposit'],'currency':'USD'}})
    monkeypatch.setattr(payments,'square',lambda *a,**kw:{'refund':{'id':'r-test','status':'COMPLETED'}})
    assert owner.post('/api/bookings/'+r['id']+'/refund',json={'confirmation':'REFUND DEPOSIT'}).json()['status']=='refunded'
    assert owner.post('/api/bookings/'+r['id']+'/refund',json={'confirmation':'REFUND DEPOSIT'}).json()['status']=='refunded'
    with db() as c:assert c.execute('SELECT status FROM bookings WHERE id=?',(r['id'],)).fetchone()[0]=='confirmed'

def test_email_outbox_writes_development_message(owner,tmp_path):
    owner.post('/api/bookings',json=booking());worker.deliver()
    assert len(list((tmp_path/'mail').glob('*.eml')))>=2

def test_csv_formula_injection_neutralized(owner):
    r=owner.post('/api/bookings',json=booking(customer_name='=SUM(1+1)'));assert r.status_code==200
    csv=owner.get('/api/export/bookings.csv').text;assert "'=SUM(1+1)" in csv

def test_quote_with_photo_and_offer(owner):
    body=booking();remove=['start_ts','idempotency_key','accepted_policy','has_water','has_power']
    for k in remove:body.pop(k)
    body['condition']='heavy'
    buf=io.BytesIO();Image.new('RGB',(30,30),'white').save(buf,format='JPEG')
    r=owner.post('/api/public/shops/northline/quote-requests',data={'data':json.dumps(body)},files=[('photos',('test.jpg',buf.getvalue(),'image/jpeg'))]);assert r.status_code==201,r.text
    rows=owner.get('/api/quotes').json()['quotes'];q=next(x for x in rows if x['email']=='test@example.com');assert len(q['photos'])==1
    img=owner.get('/api/photos/'+q['photos'][0]);assert img.status_code==200;assert img.headers['content-type']=='image/webp'
    result=owner.post('/api/quotes/'+q['id']+'/offer',json={'amount':25000,'minutes':240,'message':'Custom quote for your vehicle.'});assert result.status_code==200
    raw=result.json()['url'].split('/')[-1];assert owner.get('/api/public/quotes/'+raw).json()['quote']['amount']==25000

def test_upload_rejects_non_image(owner):
    b=booking()
    for k in ['start_ts','idempotency_key','accepted_policy','has_water','has_power']:b.pop(k)
    r=owner.post('/api/public/shops/northline/quote-requests',data={'data':json.dumps(b)},files=[('photos',('x.svg',b'<svg/>','image/svg+xml'))]);assert r.status_code==422

def test_paddle_binding_and_out_of_order_events(owner,monkeypatch):
    monkeypatch.setattr(config,'PADDLE_PRICE','pri_test')
    s=shop()
    with db(True) as c:c.execute("UPDATE shops SET paddle_customer='ctm_test' WHERE id=?",(s['id'],))
    d={'id':'sub_test','customer_id':'ctm_test','status':'active','items':[{'price':{'id':'pri_test'}}],
       'custom_data':{'shop_id':s['id'],'binding':billing_binding(s['id'],'ctm_test')}}
    payments.apply_paddle({'event_id':'e-new','event_type':'subscription.activated','occurred_at':'2026-09-14T10:00:00Z','data':d})
    payments.apply_paddle({'event_id':'e-old','event_type':'subscription.updated','occurred_at':'2026-09-13T10:00:00Z','data':{**d,'status':'past_due'}})
    with db() as c:assert c.execute('SELECT billing_status FROM shops WHERE id=?',(s['id'],)).fetchone()[0]=='active'

def test_security_settings_secret_not_exposed(owner):
    data=owner.get('/api/auth/me').text
    assert 'square_access' not in data;assert 'square_refresh' not in data;assert 'password' not in data
