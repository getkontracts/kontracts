"""Provider protocol contracts with mocked HTTP. Not a live provider sandbox test."""
import json
import httpx
import pytest
from fastapi import HTTPException
from server import payments,config
from server.db import db
from tests.test_app import shop


def capture_http(monkeypatch, handler):
    original=httpx.Client
    monkeypatch.setattr(payments.httpx,'Client',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))


def test_square_token_exchange_omits_empty_authorization(monkeypatch):
    def handler(request):
        assert 'authorization' not in request.headers
        assert request.headers['square-version']==config.SQUARE_VERSION
        assert json.loads(request.content)['grant_type']=='authorization_code'
        return httpx.Response(200,json={'access_token':'sample'})
    capture_http(monkeypatch,handler)
    result=payments.call('https://square.invalid','/oauth2/token','',{'grant_type':'authorization_code'},square=True)
    assert result['access_token']=='sample'


def test_square_revoke_uses_client_authorization(monkeypatch):
    def handler(request):
        assert request.headers['authorization']=='Client test-secret'
        return httpx.Response(200,json={'success':True})
    capture_http(monkeypatch,handler)
    assert payments.call('https://square.invalid','/oauth2/revoke','test-secret',{},square=True,auth_scheme='Client')['success']


def test_provider_error_does_not_leak_response(monkeypatch):
    capture_http(monkeypatch,lambda request:httpx.Response(400,json={'sensitive':'private-provider-data'}))
    with pytest.raises(HTTPException) as exc: payments.call('https://provider.invalid','/action','secret',{})
    assert exc.value.status_code==502
    assert 'private-provider-data' not in str(exc.value.detail)


def enable_billing(monkeypatch):
    for key,value in [('PADDLE_PRICE','pri_sample'),('PADDLE_CLIENT','test_client'),('PADDLE_WEBHOOK','test_hook')]:
        monkeypatch.setattr(config,key,value)


@pytest.mark.parametrize('bad_price',[
 {'amount':'4901','currency_code':'USD'},
 {'amount':'4900','currency_code':'INR'},
 {'amount':'0','currency_code':'USD'},
])
def test_wrong_advertised_paddle_price_fails_closed(owner,monkeypatch,bad_price):
    enable_billing(monkeypatch)
    monkeypatch.setattr(payments,'paddle',lambda *a,**kw:{'status':'active','unit_price':bad_price,'billing_cycle':{'interval':'month','frequency':1}})
    with pytest.raises(HTTPException) as exc: payments.billing_checkout(shop()['id'])
    assert exc.value.status_code==503


def test_subscription_checkout_binds_account_and_is_reused(owner,monkeypatch):
    enable_billing(monkeypatch);seen=[]
    def paddle(path,body=None,method='POST'):
        seen.append(path)
        if path.startswith('/prices/'):
            return {'status':'active','unit_price':{'amount':'4900','currency_code':'USD'},'billing_cycle':{'interval':'month','frequency':1}}
        if path.startswith('/customers?email='):
            return []
        if path == '/customers':
            return {'id': 'ctm_sample'}
        assert path=='/transactions'
        assert body['custom_data']['shop_id']==shop()['id']
        assert len(body['custom_data']['binding'])>30
        assert body['items']==[{'price_id':'pri_sample','quantity':1}]
        return {'id':'txn_sample','checkout':{'url':'https://example.com/checkout?_ptxn=txn_sample'}}
    monkeypatch.setattr(payments,'paddle',paddle)
    first=payments.billing_checkout(shop()['id']);second=payments.billing_checkout(shop()['id'])
    assert first==second
    assert seen.count('/transactions')==1


def test_oauth_state_rejects_unknown_state(owner):
    assert owner.get('/api/integrations/square/callback?state=not-valid&code=test').status_code==403


def test_demo_seed_never_overlaps(owner):
    with db() as c:
        rows=c.execute('SELECT start_ts,busy_until FROM bookings ORDER BY start_ts').fetchall()
    assert all(a['busy_until']<=b['start_ts'] for a,b in zip(rows,rows[1:]))
