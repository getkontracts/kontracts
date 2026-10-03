"""v4 regression checks. Google JWTs are locally signed; providers are mocked."""
import json
from types import SimpleNamespace
from urllib.parse import urlparse,parse_qs
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from server import config,core,plans,billing,google_auth,payments,branding
from server.db import db,now,packed,uid
from server.security import digest
from tests.test_app import select,booking

def shop():
    with db() as c:return c.execute("SELECT * FROM shops WHERE slug='northline'").fetchone()


def put_rates(owner,rates=None,**changes):
    s=owner.get('/api/auth/me').json()['shop']['settings']
    s['packages'][1]['vehicle_rates']=rates or {'suv':{'price':27500,'minutes':210}}
    s.update(changes)
    s['payment_method']='square'
    r=owner.put('/api/settings',json=s)
    assert r.status_code==200,r.text
    return s


def test_exact_vehicle_price_replaces_surcharge(owner):
    put_rates(owner)
    r=owner.post('/api/public/shops/northline/price',json={**select(),'vehicle_id':'suv'}).json()
    assert r['subtotal']==27500 and r['minutes']==210 and r['deposit']==5500


def test_exact_price_with_extras_tax_and_deposit(owner):
    put_rates(owner,tax_basis_points=825,deposit_percent=25)
    r=owner.post('/api/public/shops/northline/price',json={**select(),'vehicle_id':'suv','extra_ids':['pet-hair']}).json()
    assert r['subtotal']==31000 and r['tax']==2558 and r['total']==33558 and r['deposit']==8390
    assert r['minutes']==240


def test_fallback_vehicle_price_unchanged(owner):
    put_rates(owner)
    r=owner.post('/api/public/shops/northline/price',json=select()).json()
    assert r['subtotal']==18900 and r['minutes']==180


@pytest.mark.parametrize('value',[{'price':1,'minutes':120},{'price':-100,'minutes':120},{'price':15001.5,'minutes':120},{'price':10000,'minutes':29},{'price':10000,'minutes':721}])
def test_invalid_exact_prices_are_rejected(owner,value):
    s=owner.get('/api/auth/me').json()['shop']['settings'];s['packages'][0]['vehicle_rates']={'suv':value}
    assert owner.put('/api/settings',json=s).status_code==422


def test_unknown_vehicle_rate_rejected(owner):
    s=owner.get('/api/auth/me').json()['shop']['settings'];s['packages'][0]['vehicle_rates']={'spaceship':{'price':10000,'minutes':120}}
    assert owner.put('/api/settings',json=s).status_code==422


def test_old_booking_keeps_its_agreed_price(owner):
    put_rates(owner);r=owner.post('/api/bookings',json=booking(vehicle_id='suv'))
    assert r.status_code==200,r.text
    identifier=r.json()['id']
    with db() as c:before=dict(c.execute('SELECT * FROM bookings WHERE id=?',(identifier,)).fetchone())
    put_rates(owner,{'suv':{'price':39900,'minutes':270}})
    with db() as c:b=dict(c.execute('SELECT * FROM bookings WHERE id=?',(identifier,)).fetchone())
    assert b['total']==27500 and before['total']==27500


def test_settings_revision_conflict_does_not_overwrite(owner):
    s=put_rates(owner);s['packages'][1]['price']=88800
    assert owner.put('/api/settings',json=s).status_code==409
    assert owner.get('/api/auth/me').json()['shop']['settings']['packages'][1]['price']==18900


def test_pricing_independent_between_businesses(owner):
    put_rates(owner)
    owner.post('/api/auth/logout');r=owner.post('/api/auth/register',json={'name':'Other Owner','email':'other@example.com','password':'a long password phrase','business':'Other Detailing','slug':'other-detailing','accepted_terms':True})
    owner.headers['X-CSRF-Token']=r.json()['csrf']
    assert not owner.get('/api/auth/me').json()['shop']['settings']['packages'][0]['vehicle_rates']
    s=owner.get('/api/auth/me').json()['shop']['settings'];s['packages'][0]['vehicle_rates']={'suv':{'price':65000,'minutes':180}}
    assert owner.put('/api/settings',json=s).status_code==200
    assert owner.post('/api/public/shops/northline/price',json={**select(),'vehicle_id':'suv'}).json()['total']==27500


def test_real_catalog_and_no_secrets(owner):
    r=owner.get('/api/billing/plans');p=r.json()['plans']
    assert [(x['id'],x['amount']) for x in p]==[('solo',4900)]
    assert 'legacy' not in [x['id'] for x in p]
    assert config.GOOGLE_CLIENT_SECRET not in r.text if config.GOOGLE_CLIENT_SECRET else True


def set_plan(plan,billing_status='active'):
    with db(True) as c:c.execute('UPDATE shops SET is_demo=0,plan_id=?,billing_status=?,trial_end=?',(plan,billing_status,now()-1))
    return shop()


@pytest.mark.parametrize('plan,color,domain',[('solo',True,False),('starter',True,False),('pro',True,False),('business',True,False),('legacy',True,False)])
def test_plan_entitlements(owner,plan,color,domain):
    s=set_plan(plan);f=plans.features(s)
    assert f['brand_colors']==color and f['hide_attribution']==color
    assert f['custom_domain']==domain and f['custom_sender']==domain


def test_starter_branding_enforced_on_server_and_email(owner,monkeypatch):
    st=owner.get('/api/branding').json();b=st['draft'];b={k:b[k] for k in branding.BrandSettings.model_fields}
    b.update(primary_color='#ff0000',button_color='#cc0000',hide_powered_by=True)
    r=owner.put('/api/branding',json={'revision':st['revision'],'branding':b})
    assert r.status_code==200,r.text
    assert owner.post('/api/branding/publish',json={'revision':r.json()['revision']}).status_code==200
    s=set_plan('starter');monkeypatch.setattr(config,'BRAND_EMAIL_SENDERS',{s['id']:'hello@custom.example'})
    live=branding.live_brand(s);assert live['primary_color']=='#ff0000' and live['hide_powered_by']
    v=owner.get('/api/branding').json();assert not v['features']['custom_domain'] and v['live']['primary_color']==live['primary_color']
    r=owner.post('/api/branding/domain',json={'hostname':'book.custom-example.com'});assert r.status_code==402
    email=branding.email_presentation({'shop_id':s['id'],'body':'Test message'})
    assert 'hello@custom.example' not in str(email)


def test_new_password_signup_selects_plan(client):
    r=client.post('/api/auth/register',json={'name':'Owner Plan','email':'plan@example.com','password':'a long password phrase','business':'Plan Business','slug':'plan-business','plan_id':'solo','accepted_terms':True})
    assert r.status_code==201
    assert client.get('/api/auth/me').json()['shop']['plan_id']=='solo'


def price_setup(monkeypatch):
    for k,v in [('PADDLE_STARTER_PRICE_ID','pri_starter'),('PADDLE_PRO_PRICE_ID','pri_pro'),('PADDLE_BUSINESS_PRICE_ID','pri_business'),('PADDLE_PRICE','pri_legacy'),('PADDLE_KEY','test_key'),('PADDLE_CLIENT','test_client'),('PADDLE_WEBHOOK','test_webhook')]:monkeypatch.setattr(config,k,v)


def fake_price(plan='solo'):
    return {'status':'active','unit_price':{'amount':str(plans.get(plan)['amount']),'currency_code':'USD'},'billing_cycle':{'interval':'month','frequency':1},'trial_period':None,'unit_price_overrides':[]}


def test_checkout_matches_selected_price_and_reuses(owner,monkeypatch):
    price_setup(monkeypatch);s=set_plan('starter','trial');seen=[]
    def provider(path,payload=None,method='POST'):
        seen.append((path,payload))
        if path.startswith('/prices/'):return fake_price('pro')
        if path=='/customers':return {'id':'ctm_test'}
        if path=='/transactions':return {'id':'txn_test','checkout':{'url':'https://checkout.example/one'}}
        raise AssertionError(path)
    monkeypatch.setattr(payments,'paddle',provider)
    first=billing.checkout(s['id'],'solo');assert billing.checkout(s['id'],'solo')==first
    assert len([x for x in seen if x[0]=='/transactions'])==1
    assert [x for x in seen if x[0]=='/transactions'][0][1]['items']==[{'price_id':'pri_legacy','quantity':1}]
    assert shop()['plan_id']=='starter'  # no access until a verified event
    with pytest.raises(HTTPException) as exc:billing.checkout(s['id'],'business')
    assert exc.value.status_code==422


def test_expired_local_checkout_does_not_create_duplicate(owner,monkeypatch):
    price_setup(monkeypatch);s=set_plan('starter','trial')
    with db(True) as c:
        c.execute('INSERT INTO billing_intents VALUES(?,?,?,?)',(s['id'],'txn_old','https://checkout.example/old',now()-4000))
        c.execute('INSERT INTO billing_plan_intents VALUES(?,?,?,?)',(s['id'],'solo','pri_legacy',now()-4000))
    monkeypatch.setattr(payments,'paddle',lambda path,**kwargs: {'status':'ready'})
    assert billing.checkout(s['id'],'solo')['url'].endswith('/old')
    with pytest.raises(HTTPException):billing.checkout(s['id'],'business')


def test_provider_price_mismatch_rejected(owner,monkeypatch):
    price_setup(monkeypatch);monkeypatch.setattr(payments,'paddle',lambda *a,**k:fake_price('starter'))
    with pytest.raises(HTTPException) as exc:billing.checked_price('pro')
    assert exc.value.status_code==503


def test_price_id_cannot_be_shared_between_plans(owner,monkeypatch):
    price_setup(monkeypatch);monkeypatch.setattr(config,'PADDLE_PRO_PRICE_ID','pri_starter')
    assert plans.for_price('pri_starter')=='solo' # legacy provider prices map to one entitlement
    with pytest.raises(HTTPException):billing.checked_price('starter')


def test_subscription_event_controls_actual_tier(owner,monkeypatch):
    price_setup(monkeypatch);s=set_plan('starter')
    with db(True) as c:c.execute("UPDATE shops SET paddle_customer='ctm_test'")
    event={'event_id':'evt_upgrade','event_type':'subscription.updated','occurred_at':'2026-09-14T10:00:00Z','data':{'id':'sub_test','customer_id':'ctm_test','custom_data':{'shop_id':s['id'],'binding':billing.binding(s['id'],'ctm_test'),'plan_id':'solo'},'items':[{'price':{'id':'pri_pro'},'quantity':1}],'status':'active'}}
    payments.apply_paddle(event);assert shop()['plan_id']=='solo' # ignores untrusted plan field
    payments.apply_paddle(event);assert shop()['plan_id']=='solo'
    event.update(event_id='evt_older',occurred_at='2026-09-13T10:00:00Z');event['data']['items'][0]['price']['id']='pri_starter'
    payments.apply_paddle(event);assert shop()['plan_id']=='solo'


def test_plan_change_endpoints_are_disabled(owner):
    s=shop()
    with pytest.raises(HTTPException) as ex:billing.preview_change(s,'business')
    assert ex.value.status_code==409
    with pytest.raises(HTTPException) as ex:billing.confirm_change(s,'fake-token')
    assert ex.value.status_code==409


@pytest.fixture
def signed_google(monkeypatch):
    monkeypatch.setattr(config,'GOOGLE_CLIENT_ID','test-client.apps.googleusercontent.com');monkeypatch.setattr(config,'GOOGLE_CLIENT_SECRET','test-secret')
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    monkeypatch.setattr(google_auth,'JWKS',SimpleNamespace(get_signing_key_from_jwt=lambda raw:SimpleNamespace(key=private.public_key())))
    def encode(nonce='nonce',**changes):
        claims={'sub':'123456789','email':'google@example.com','email_verified':True,'name':'Google Owner','nonce':nonce,'iss':'https://accounts.google.com','aud':config.GOOGLE_CLIENT_ID,'iat':now(),'exp':now()+300};claims.update(changes)
        return jwt.encode(claims,private,algorithm='RS256',headers={'kid':'local-test'})
    return encode


def test_google_valid_signature_and_claims(signed_google):
    result=google_auth.validate_token(signed_google(),'nonce')
    assert result['sub']=='123456789' and result['email']=='google@example.com'


@pytest.mark.parametrize('changes',[{'aud':'wrong'},{'iss':'https://evil.example'},{'nonce':'wrong'},{'exp':1},{'iat':4102444800},{'email_verified':False},{'email_verified':'true'},{'azp':'wrong'},{'email':'invalid'},{'sub':''}])
def test_google_invalid_claims_rejected(signed_google,changes):
    with pytest.raises(HTTPException) as exc:google_auth.validate_token(signed_google(**changes),'nonce')
    assert exc.value.status_code==401


def test_google_bad_signature_rejected(signed_google):
    raw=signed_google();parts=raw.split('.');parts[2]=('A' if parts[2][0]!='A' else 'B')+parts[2][1:]
    with pytest.raises(HTTPException):google_auth.validate_token('.'.join(parts),'nonce')


def start_google(client):
    r=client.post('/api/auth/google/start',json={'plan_id':'solo'});assert r.status_code==200,r.text
    values=parse_qs(urlparse(r.json()['url']).query)
    assert values['code_challenge_method']==['S256'] and values['scope']==['openid email profile']
    assert 'test-secret' not in r.text
    assert 'httponly' in r.headers['set-cookie'].lower()
    return {k:v[0] for k,v in values.items()}


def callback(client,values):return client.get('/api/auth/google/callback',params={'state':values['state'],'code':'one-time-code'},follow_redirects=False)


def test_google_not_configured_keeps_password_login(client,monkeypatch):
    monkeypatch.setattr(config,'GOOGLE_CLIENT_ID','')
    assert client.post('/api/auth/google/start',json={}).status_code==503
    assert client.post('/api/auth/demo').status_code==200


def test_google_state_cookie_required(client,signed_google):
    values=start_google(client);client.cookies.clear()
    assert callback(client,values).status_code==403


def test_google_cancellation_consumes_state(client,signed_google):
    values=start_google(client)
    r=client.get('/api/auth/google/callback',params={'state':values['state'],'error':'access_denied'},follow_redirects=False)
    assert r.headers['location'].endswith('google-cancelled') and callback(client,values).status_code==403


def test_google_signup_and_subsequent_login(client,signed_google,monkeypatch):
    values=start_google(client);monkeypatch.setattr(google_auth,'exchange',lambda *a:signed_google(values['nonce']))
    assert callback(client,values).headers['location']=='/google/complete'
    assert callback(client,values).status_code==403
    assert client.get('/api/auth/google/pending').json()['email']=='google@example.com'
    data={'business':'Google Detailing','slug':'google-detailing','timezone':'America/Chicago','accepted_terms':True,'plan_id':'solo'}
    bad=client.post('/api/auth/google/complete',json={**data,'accepted_terms':False});assert bad.status_code==422
    r=client.post('/api/auth/google/complete',json=data);assert r.status_code==201,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf'];me=client.get('/api/auth/me').json()
    assert me['user']['verified'] and me['user']['google_linked'] and not me['user']['local_login_enabled']
    assert me['shop']['slug']=='google-detailing' and me['shop']['plan_id']=='solo'
    assert client.post('/api/auth/google/unlink',json={'password':'anything'}).status_code==409
    assert client.post('/api/auth/google/complete',json=data).status_code==410
    client.post('/api/auth/logout');values=start_google(client)
    assert callback(client,values).headers['location']=='/app'
    assert client.get('/api/auth/me').json()['user']['id']==me['user']['id']


def test_google_does_not_take_over_matching_email(client,signed_google,monkeypatch):
    r=client.post('/api/auth/register',json={'name':'Local Owner','email':'google@example.com','password':'a long password phrase','business':'Local Business','slug':'local-business','accepted_terms':True})
    client.headers['X-CSRF-Token']=r.json()['csrf'];client.post('/api/auth/logout')
    values=start_google(client);monkeypatch.setattr(google_auth,'exchange',lambda *a:signed_google(values['nonce']))
    assert callback(client,values).headers['location'].endswith('google-link-required')
    assert client.get('/api/auth/me').status_code==401
    with db() as c:assert c.execute('SELECT COUNT(*) FROM google_identities').fetchone()[0]==0


def test_google_explicit_link_and_unlink(client,signed_google,monkeypatch):
    password='a long password phrase'
    r=client.post('/api/auth/register',json={'name':'Local Owner','email':'google@example.com','password':password,'business':'Local Business','slug':'local-business','accepted_terms':True})
    client.headers['X-CSRF-Token']=r.json()['csrf']
    assert client.post('/api/auth/google/link',json={'password':'wrong'}).status_code==403
    r=client.post('/api/auth/google/link',json={'password':password});assert r.status_code==200
    values={k:v[0] for k,v in parse_qs(urlparse(r.json()['url']).query).items()}
    monkeypatch.setattr(google_auth,'exchange',lambda *a:signed_google(values['nonce']))
    assert callback(client,values).headers['location'].endswith('google-linked')
    assert client.get('/api/auth/me').json()['user']['google_linked']
    assert client.post('/api/auth/google/unlink',json={'password':password}).status_code==200
    assert not client.get('/api/auth/me').json()['user']['google_linked']


def test_setup_checklist_reports_configuration_not_live_verification(owner):
    r=owner.get('/api/setup');assert r.status_code==200
    assert r.json()['platform']['google_callback'].endswith('/api/auth/google/callback')
    assert 'live' in r.json()['note'].lower()


def test_cancelled_account_can_start_a_new_subscription(owner):
    set_plan('pro','canceled')
    with db(True) as c:c.execute("UPDATE shops SET paddle_subscription='sub_old'")
    assert owner.get('/api/auth/me').json()['shop']['has_subscription'] is False


def test_price_review_is_separate_from_policy_review(owner):
    s=owner.get('/api/auth/me').json()['shop']['settings'];s['prices_reviewed']=True;s['policy_reviewed']=False
    assert owner.put('/api/settings',json=s).status_code==200
    checks=owner.get('/api/setup').json()['checks']
    assert next(x for x in checks if x['id']=='prices')['ready']
