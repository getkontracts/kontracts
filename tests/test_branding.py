"""White-label, stable links, image privacy and custom-host isolation regressions."""
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from PIL import Image
from server import config,branding,brand_domains,worker,core
from server.db import db,now,initialize,packed
from tests.test_app import shop,booking,select
from scripts.domains import activate


def raw_brand(result):return {key:result[key] for key in branding.default()}

def edit(client,**changes):
    state=client.get('/api/branding').json();b=raw_brand(state['draft']);b.update(changes)
    r=client.put('/api/branding',json={'revision':state['revision'],'branding':b})
    assert r.status_code==200,r.text
    return r.json()

def publish(client):
    r=client.post('/api/branding/publish',json={'revision':client.get('/api/branding').json()['revision']})
    assert r.status_code==200,r.text
    return r.json()

def picture(size=(800,500),mode='RGB'):
    out=io.BytesIO();im=Image.new(mode,size,(25,120,150,90) if mode=='RGBA' else (25,120,150));im.save(out,'PNG');return out.getvalue()

def upload(client,kind='logo',content=None):
    rev=client.get('/api/branding').json()['revision']
    return client.post('/api/branding/assets/'+kind,data={'revision':str(rev)},files={'file':('image.png',content or picture(),'image/png')})

def new_account(client,slug='other-detailer',name='Other Detailer'):
    body={'name':'Another Owner','email':slug+'@example.com','password':'not-a-real-passphrase-123','business':name,'slug':slug,'accepted_terms':True}
    r=client.post('/api/auth/register',json=body);assert r.status_code==201,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf']
    with db(True) as c:
        s=c.execute('SELECT * FROM shops WHERE slug=?',(slug,)).fetchone()
        settings=core.settings(s);settings['zip_codes']=['78701'];settings['deposit_percent']=0;settings['policy_reviewed']=True
        c.execute('UPDATE shops SET published=1,settings=? WHERE id=?',(packed(settings),s['id']))
        c.execute('UPDATE users SET verified=1 WHERE id=?',(s['owner_id'],))
    return s

def as_demo(client):
    r=client.post('/api/auth/login',json={'email':'alex@northlinedemo.com','password':'Test-owner-password-12345'});client.headers['X-CSRF-Token']=r.json()['csrf']


def domain(client,monkeypatch,host='book.northline-example.com',active=False):
    with db(True) as c:c.execute("UPDATE shops SET plan_id='legacy',paddle_subscription='sub_existing' WHERE slug='northline'")
    r=client.post('/api/branding/domain',json={'hostname':host});assert r.status_code==200,r.text
    value=r.json()['domain']['txt_value'];monkeypatch.setattr(brand_domains,'txt_records',lambda _: [value])
    r=client.post('/api/branding/domain/verify');assert r.status_code==200,r.text
    if active:activate(host,True)
    return host


def test_branding_requires_owner(client):assert client.get('/api/branding').status_code==401

def test_branding_migration_idempotent(owner):
    initial=owner.get('/api/auth/me').json()['shop']['id'];initialize();initialize()
    assert owner.get('/api/auth/me').json()['shop']['id']==initial
    with db() as c:assert c.execute('SELECT 1 FROM schema_versions WHERE version=3').fetchone()

def test_draft_does_not_change_live_page(owner):
    edit(owner,headline='A very different heading',hide_powered_by=True)
    assert owner.get('/api/public/shops/northline').json()['branding']['headline']=='A fresh start for your car.'
    assert owner.get('/api/branding/preview').json()['branding']['headline']=='A very different heading'
    publish(owner)
    result=owner.get('/api/public/shops/northline').json()['branding']
    assert result['headline']=='A very different heading' and result['hide_powered_by'] is True

def test_brand_save_rejects_stale_revision(owner):
    state=owner.get('/api/branding').json();edit(owner,headline='Saved')
    r=owner.put('/api/branding',json={'revision':state['revision'],'branding':raw_brand(state['draft'])})
    assert r.status_code==409

def test_brand_publish_requires_current_revision(owner):
    edit(owner,headline='Saved')
    assert owner.post('/api/branding/publish',json={'revision':0}).status_code==409

def test_brand_csrf_required(owner):
    assert owner.post('/api/branding/publish',json={'revision':0},headers={'X-CSRF-Token':'wrong'}).status_code==403

@pytest.mark.parametrize('field,value',[('primary_color','#fff;display:none'),('button_color','red'),('website_url','javascript:alert(1)'),('instagram_url','https://user:password@example.com'),('headline','Injected\nHeader')])
def test_brand_untrusted_values_rejected(owner,field,value):
    state=owner.get('/api/branding').json();b=raw_brand(state['draft']);b[field]=value
    assert owner.put('/api/branding',json={'revision':state['revision'],'branding':b}).status_code==422

def test_logo_compressed_and_public_only_after_publish(owner):
    r=upload(owner,content=picture((1000,900),'RGBA'));assert r.status_code==200,r.text
    draft=r.json()['draft'];identifier=draft['logo_id']
    assert r.json()['upload']['bytes']<=120*1024
    assert owner.get('/api/public/brand-assets/northline/'+identifier).status_code==404
    private=owner.get(draft['logo_url']);assert private.status_code==200
    with Image.open(io.BytesIO(private.content)) as im:
        assert im.format=='WEBP';assert max(im.size)<=640;assert not im.getexif();assert im.mode=='RGBA'
    publish(owner)
    public=owner.get('/api/public/brand-assets/northline/'+identifier);assert public.status_code==200
    assert 'public' in public.headers['cache-control']
    icon=owner.get('/api/public/brand-assets/northline/'+identifier+'?icon=true');assert icon.status_code==200
    with Image.open(io.BytesIO(icon.content)) as im:assert im.size==(64,64)

def test_cover_is_bounded(owner):
    r=upload(owner,'cover',picture((2400,1600)));assert r.status_code==200
    assert r.json()['upload']['bytes']<=320*1024 and r.json()['upload']['width']<=1600

def test_svg_logo_not_accepted(owner):
    r=upload(owner,content=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>');assert r.status_code==422

def test_invalid_image_not_accepted(owner):assert upload(owner,content=b'not an image').status_code==422

def test_logo_live_survives_draft_removal_until_publish(owner):
    r=upload(owner);identifier=r.json()['draft']['logo_id'];publish(owner)
    rev=owner.get('/api/branding').json()['revision']
    assert owner.request('DELETE','/api/branding/assets/logo',json={'revision':rev}).status_code==200
    assert owner.get('/api/public/brand-assets/northline/'+identifier).status_code==200
    publish(owner)
    assert owner.get('/api/public/brand-assets/northline/'+identifier).status_code==404

def test_discard_draft_restores_live_branding(owner):
    edit(owner,headline='Live');publish(owner);state=edit(owner,headline='Unpublished')
    r=owner.post('/api/branding/reset',json={'revision':state['revision']});assert r.status_code==200
    assert r.json()['draft']['headline']=='Live' and not r.json()['has_unpublished_changes']

def test_other_tenant_cannot_read_or_attach_private_logo(owner):
    r=upload(owner);identifier=r.json()['draft']['logo_id'];new_account(owner)
    assert owner.get('/api/branding/assets/'+identifier).status_code==404
    state=owner.get('/api/branding').json();b=raw_brand(state['draft']);b['logo_id']=identifier
    assert owner.put('/api/branding',json={'revision':state['revision'],'branding':b}).status_code==422

def test_general_settings_do_not_overwrite_brand(owner):
    edit(owner,headline='Distinct brand');publish(owner)
    s=owner.get('/api/auth/me').json()['shop']['settings'];s['city']='Changed city'
    assert owner.put('/api/settings',json=s).status_code==200
    assert owner.get('/api/public/shops/northline').json()['branding']['headline']=='Distinct brand'

def test_each_tenant_gets_own_booking_page(owner):
    edit(owner,headline='Northline only',primary_color='#112233');publish(owner)
    new_account(owner);edit(owner,headline='Other only',primary_color='#332211');publish(owner)
    first=owner.get('/api/public/shops/northline').json();second=owner.get('/api/public/shops/other-detailer').json()
    assert first['name']!=second['name'] and first['booking_url']!=second['booking_url']
    assert first['branding']['headline']=='Northline only' and second['branding']['headline']=='Other only'
    assert '/book/other-detailer' in second['booking_url']

def test_public_initial_html_has_business_title_logo_not_kontracts_loading(owner):
    upload(owner);publish(owner)
    r=owner.get('/book/northline');assert r.status_code==200
    assert '<title>Northline Auto Care | Book a detail</title>' in r.text
    assert 'class="business-loading">Northline Auto Care' in r.text
    assert 'favicon.svg' not in r.text


def test_rename_keeps_original_link(owner):
    r=owner.put('/api/booking-page/slug',json={'slug':'northline-auto'});assert r.status_code==200,r.text
    assert r.json()['booking_url'].endswith('/book/northline-auto')
    assert owner.get('/api/public/shops/northline').json()['slug']=='northline-auto'
    assert owner.get('/api/public/shops/northline-auto').status_code==200
    assert set(r.json()['aliases'])=={'northline','northline-auto'}

def test_original_slug_cannot_be_claimed_by_new_account(owner):
    owner.put('/api/booking-page/slug',json={'slug':'northline-auto'})
    r=owner.post('/api/auth/register',json={'name':'New Owner','email':'new@example.com','password':'long-password-12345','business':'New','slug':'northline','accepted_terms':True})
    assert r.status_code==409

def test_slug_collision_rejected(owner):
    new_account(owner)
    assert owner.put('/api/booking-page/slug',json={'slug':'northline'}).status_code==409
    assert owner.get('/api/booking-page/availability?slug=northline').json()['available'] is False

def test_reserved_slug_rejected(owner):
    assert owner.put('/api/booking-page/slug',json={'slug':'admin'}).status_code==422

def test_html_email_business_brand_and_safe_sender(owner):
    edit(owner,hide_powered_by=True,primary_color='#112233');publish(owner)
    msg={'shop_id':shop()['id'],'body':'Hello <script>alert(1)</script>\n'+config.BASE_URL+'/manage/exampletoken'}
    out=branding.email_presentation(msg)
    assert 'Northline Auto Care' in out['from'] and '@example.com' in out['from']
    assert 'Powered by Kontracts' not in out['html'];assert '<script>' not in out['html']
    assert '#112233' in out['html'];assert out['html'].count('<a href="'+config.BASE_URL+'/manage/exampletoken"')==1

def test_email_verified_sender_mapping_operator_only(owner,monkeypatch):
    monkeypatch.setattr(config,'BRAND_EMAIL_SENDERS',{'northline':'bookings@northline-example.com'})
    out=branding.email_presentation({'shop_id':shop()['id'],'body':'Example'})
    assert 'bookings@northline-example.com' not in out['from']

def test_auth_email_uses_platform_sender(owner):assert branding.email_presentation({'shop_id':None,'body':'Verify'}) is None

def test_email_preview_does_not_send(owner):
    with db() as c:before=c.execute('SELECT count(*) FROM outbox').fetchone()[0]
    r=owner.get('/api/branding/email-preview');assert r.status_code==200 and '<html>' in r.json()['html']
    with db() as c:assert c.execute('SELECT count(*) FROM outbox').fetchone()[0]==before

@pytest.mark.parametrize('host',['localhost','127.0.0.1','*.example.com','https://book.example.com','book.example.com/path','book.example.com:443','x.internal','x.local'])
def test_invalid_custom_domains_rejected(owner,host):assert owner.post('/api/branding/domain',json={'hostname':host}).status_code==422

def test_domain_dns_proof_does_not_activate(owner,monkeypatch):
    host=domain(owner,monkeypatch)
    state=owner.get('/api/branding').json();assert state['domain']['status']=='verified'
    assert state['booking_url'].endswith('/book/northline')
    assert owner.get('/',headers={'Host':host}).status_code==421

def test_unverified_domain_operator_activation_rejected(owner):
    owner.post('/api/branding/domain',json={'hostname':'book.northline-example.com'})
    with pytest.raises(ValueError):activate('book.northline-example.com',True)

def test_domain_requires_explicit_operator_confirmation(owner,monkeypatch):
    host=domain(owner,monkeypatch)
    with pytest.raises(ValueError):activate(host,False)

def test_incorrect_domain_txt_rejected(owner,monkeypatch):
    with db(True) as c:c.execute("UPDATE shops SET plan_id='legacy',paddle_subscription='sub_existing' WHERE slug='northline'")
    owner.post('/api/branding/domain',json={'hostname':'book.northline-example.com'})
    monkeypatch.setattr(brand_domains,'txt_records',lambda _:['someone-else'])
    assert owner.post('/api/branding/domain/verify').status_code==422

def test_active_domain_routes_correct_business(owner,monkeypatch):
    host=domain(owner,monkeypatch,active=True)
    assert owner.get('/api/config',headers={'Host':host}).json()['tenant_slug']=='northline'
    assert '<title>Northline Auto Care' in owner.get('/',headers={'Host':host}).text
    assert owner.get('/api/branding').json()['booking_url']=='https://'+host

def test_custom_domain_same_origin_booking_api_allowed(owner,monkeypatch):
    host=domain(owner,monkeypatch,active=True)
    r=owner.post('/api/public/shops/northline/price',json=select(),headers={'Host':host,'Origin':'https://'+host})
    assert r.status_code==200,r.text
    assert owner.post('/api/public/shops/northline/price',json=select(),headers={'Host':host,'Origin':config.BASE_URL}).status_code==403

def test_custom_domain_cannot_access_owner_or_another_tenant(owner,monkeypatch):
    host=domain(owner,monkeypatch,active=True);new_account(owner)
    for path in ['/api/settings','/api/auth/me','/app','/login','/api/public/shops/other-detailer','/book/other-detailer']:
        assert owner.get(path,headers={'Host':host}).status_code==404,path

def test_manage_token_scoped_to_host(owner,monkeypatch):
    host=domain(owner,monkeypatch,active=True)
    new_account(owner);other=owner.post('/api/bookings',json=booking()).json()
    path='/api/public'+other['manage_url']
    assert owner.get(path).status_code==200
    assert owner.get(path,headers={'Host':host}).status_code==404

def test_unknown_host_and_forged_forwarded_host(owner):
    assert owner.get('/api/auth/me',headers={'Host':'evil.example.com'}).status_code==421
    assert owner.get('/api/auth/me',headers={'X-Forwarded-Host':'evil.example.com'}).status_code==200

def test_disconnect_domain_falls_back_to_original_link(owner,monkeypatch):
    host=domain(owner,monkeypatch,active=True)
    r=owner.delete('/api/branding/domain');assert r.status_code==200
    assert r.json()['booking_url'].endswith('/book/northline')
    assert owner.get('/',headers={'Host':host}).status_code==421

def test_domain_probe_available_before_activation_but_no_data(owner,monkeypatch):
    host=domain(owner,monkeypatch)
    r=owner.get('/.well-known/kontracts-domain',headers={'Host':host});assert r.status_code==200
    assert set(r.json())=={'hostname','proof','verified'}

def test_color_ink_extremes():
    assert branding.contrast_ink('#ffffff')=='#000000'
    assert branding.contrast_ink('#000000')=='#ffffff'


def test_stable_email_logo_survives_replacement_and_old_slug(owner):
    assert upload(owner).status_code == 200
    publish(owner)
    path='/api/public/brand-logo/northline'
    first=owner.get(path).content
    out=branding.email_presentation({'shop_id':shop()['id'],'body':'Your booking is confirmed.'})
    assert config.BASE_URL+path in out['html']
    assert upload(owner,content=picture((500,800),'RGBA')).status_code == 200
    publish(owner)
    assert owner.put('/api/booking-page/slug',json={'slug':'northline-auto'}).status_code == 200
    result=owner.get(path)
    assert result.status_code == 200 and result.content != first
    assert result.headers['content-type'] == 'image/webp'


def test_worker_sends_branded_resend_payload(owner,monkeypatch):
    from server.db import queue_mail
    edit(owner,hide_powered_by=True);publish(owner)
    sent=[]
    class Accepted:
        def raise_for_status(self):pass
        def json(self):return {'id':'provider-test-id'}
    def send(url,**kwargs):
        sent.append((url,kwargs));return Accepted()
    monkeypatch.setattr(config,'RESEND_API_KEY','unit-test-key')
    monkeypatch.setattr(worker.httpx,'post',send)
    with db(True) as c:
        c.execute('DELETE FROM outbox')
        queue_mail(c,'brand-send-test','recipient@example.com','Test confirmation','Your booking is confirmed.',shop_id=shop()['id'])
    worker.deliver(limit=1)
    assert len(sent)==1
    payload=sent[0][1]['json']
    assert 'Northline Auto Care' in payload['from']
    assert payload['text']=='Your booking is confirmed.'
    assert '<html>' in payload['html'] and 'Powered by Kontracts' not in payload['html']
    assert payload['reply_to']==json.loads(shop()['settings'])['contact_email']
    assert 'Idempotency-Key' in sent[0][1]['headers']
    with db() as c:assert c.execute('SELECT sent_at FROM outbox').fetchone()[0] is not None
