"""HTTP API and same-origin, dependency-free web application."""
import asyncio
import csv
import io
import json
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, Response, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError
from . import config, models, core, payments, worker, media, branding, brand_domains, brand_frontend
from .db import db,initialize,uid,now,packed,queue_mail,audit
from . import plans
from .security import token,digest,encrypt,decrypt,password_ok,PASSWORDS,constant_equal,rate_limit,verify_square,verify_paddle

logging.basicConfig(level=logging.INFO)
log=logging.getLogger('kontracts')
quote_photo_processing=media.PHOTO_PROCESSING

async def background():
    while True:
        try: await asyncio.to_thread(worker.run_once)
        except Exception: log.exception('Maintenance cycle failed')
        await asyncio.sleep(45)

@asynccontextmanager
async def lifespan(app):
    initialize()
    from .postal import registry
    registry.cache_clear(); registry()
    if config.DEMO:
        from .seed import seed
        seed()
    task=None if config.TESTING else asyncio.create_task(background())
    yield
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

app=FastAPI(title='Kontracts API',version='5.0.0',lifespan=lifespan,
            docs_url=None if config.PRODUCTION else '/api/docs',redoc_url=None,openapi_url=None if config.PRODUCTION else '/openapi.json')

@app.middleware('http')
async def security_headers(request,call_next):
    try:
        tenant=brand_domains.tenant_for_host(request)
        request.state.tenant=tenant
        brand_domains.enforce_tenant_path(request,tenant)
        if tenant and tenant.get('plan_enabled') is False:
            if request.method=='GET' and not request.url.path.startswith('/api/'):
                target='/book/'+tenant['slug'] if request.url.path=='/' else request.url.path
                return RedirectResponse(config.BASE_URL+target,307)
            raise HTTPException(402,'This custom domain is inactive on the current plan. Use the original booking link.')
    except HTTPException as exc:
        return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    if request.url.path.startswith('/api/public/manage/') and request.method not in ('GET','HEAD','OPTIONS'):
        raw=request.url.path.split('/')[4]
        with db() as c:
            sample=c.execute('SELECT s.is_demo FROM bookings b JOIN shops s ON s.id=b.shop_id WHERE b.manage_hash=?',(digest(raw),)).fetchone()
        if sample and sample['is_demo']: return JSONResponse({'detail':'Demo customer links are read-only.'},403)
    if request.url.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
        if request.url.path not in ('/api/auth/logout','/api/auth/demo','/api/preview/price','/api/preview/slots') and not request.url.path.startswith('/api/webhooks/'):
            raw=request.cookies.get(config.COOKIE,'')
            if raw:
                with db() as c:
                    sample=c.execute('SELECT 1 FROM sessions se JOIN shops s ON s.owner_id=se.user_id WHERE se.token_hash=? AND se.expires_at>? AND s.is_demo=1',(digest(raw),now())).fetchone()
                if sample: return JSONResponse({'detail':'The server demo is read-only. Use the isolated /demo simulation, or sign out to create a real account.'},403)
        length=request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>26*1024*1024): return JSONResponse({'detail':'Request too large.'},status_code=413)
        if not request.url.path.startswith('/api/webhooks/'):
            origin=request.headers.get('origin')
            expected_origin=('https://'+tenant['hostname']) if tenant else config.BASE_URL
            if origin and origin!=expected_origin: return JSONResponse({'detail':'Cross-origin requests are not allowed.'},status_code=403)
            if request.headers.get('x-requested-with')!='Kontracts': return JSONResponse({'detail':'Invalid request context.'},status_code=403)
    r=await call_next(request)
    r.headers['X-Content-Type-Options']='nosniff'
    r.headers['X-Frame-Options']='DENY'
    r.headers['Referrer-Policy']='no-referrer'
    r.headers['Permissions-Policy']='camera=(self), microphone=(), geolocation=()'
    default_csp="default-src 'self'; script-src 'self' https://cdn.paddle.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' https://*.paddle.com https://*.paddle.io; frame-src 'self' https://*.paddle.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    r.headers.setdefault('Content-Security-Policy',default_csp)
    if config.PRODUCTION: r.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    if request.url.path.startswith('/api/') or request.url.path.startswith(('/manage/','/invoice/')):
        r.headers.setdefault('Cache-Control','no-store')
    return r

from fastapi.exceptions import RequestValidationError
@app.exception_handler(RequestValidationError)
async def invalid_input(request,exc):
    return JSONResponse({'detail':'Check the submitted fields. Use a recognized US ZIP and matching state; required fields and amounts must be valid.'},422)

@app.exception_handler(Exception)
async def unexpected(request,exc):
    log.error('Unhandled server error: %s',type(exc).__name__)
    return JSONResponse({'detail':'Something unexpected happened. Please retry; your existing booking is preserved.'},500)

def ip(request): return request.client.host if request.client else 'unknown'

def session(request:Request):
    raw=request.cookies.get(config.COOKIE,'')
    with db() as c:
        row=c.execute('''SELECT s.*,u.email,u.name,u.verified FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>?''',(digest(raw),now())).fetchone()
    if not row: raise HTTPException(401,'Please sign in to continue.')
    if request.method not in ('GET','HEAD') and not constant_equal(request.headers.get('x-csrf-token',''),row['csrf']):
        raise HTTPException(403,'Your session changed. Refresh the page and try again.')
    return row

def owner(request:Request,s=Depends(session)):
    with db() as c: shop=c.execute('SELECT * FROM shops WHERE owner_id=?',(s['user_id'],)).fetchone()
    if not shop: raise HTTPException(404,'Business account not found.')
    if shop['is_demo'] and request.method not in ('GET','HEAD') and request.url.path not in ('/api/preview/price','/api/preview/slots'):
        raise HTTPException(403,'The server demo is read-only. Use /demo for an isolated interactive simulation.')
    return shop

def login_session(response,user_id):
    raw=token();csrf=token()
    with db(True) as c: c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(raw),user_id,csrf,now()+14*86400))
    response.set_cookie(config.COOKIE,raw,max_age=14*86400,httponly=True,secure=config.PRODUCTION,samesite='lax',path='/')
    return csrf

def auth_mail(c,user_id,email,kind):
    raw=token()
    c.execute('DELETE FROM auth_tokens WHERE user_id=? AND kind=?',(user_id,kind))
    c.execute('INSERT INTO auth_tokens VALUES(?,?,?,?)',(digest(raw),user_id,kind,now()+(3600 if kind=='reset' else 86400)))
    path='/reset-password?token=' if kind=='reset' else '/verify-email?token='
    label='Reset your password' if kind=='reset' else 'Verify your email'
    queue_mail(c,'auth:'+digest(raw),email,label+' - Kontracts',label+':\n\n'+config.BASE_URL+path+raw+'\n\nIf you did not request this, you can ignore this email.'+'\n\nQuestions: '+config.SUPPORT_EMAIL,
               channel='accounts',reply_to=config.MAIL_REPLY_TO,expires_at=now()+(3600 if kind=='reset' else 86400),
               guard_kind='auth',guard_id=digest(raw))

@app.get('/api/health')
def health():
    with db() as c: c.execute('SELECT 1').fetchone()
    return {'status':'ok','version':'5.0.0'}

@app.get('/api/config')
def public_config(request:Request):
    tenant=getattr(request.state,'tenant',None)
    return {'google_enabled':bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET),'plans':plans.catalog(),'tenant_slug':tenant['slug'] if tenant else None,'platform_url':config.BASE_URL,'demo':config.DEMO,'production':config.PRODUCTION,'support_email':config.SUPPORT_EMAIL,
        'legal_name':config.LEGAL_NAME,'legal_address':config.LEGAL_ADDRESS,
        'terms_text':config.TERMS_TEXT,'privacy_text':config.PRIVACY_TEXT,
        'photo_profiles':media.PROFILES,'photo_workspace_limit':config.PHOTO_WORKSPACE_LIMIT,'paddle_client_token':config.PADDLE_CLIENT,'paddle_environment':config.PADDLE_ENV,'timezones':config.US_ZONES}

@app.post('/api/auth/register',status_code=201)
def register(body:models.Register,request:Request,response:Response):
    rate_limit('register:'+ip(request),5,3600)
    if body.website: raise HTTPException(422,'Unable to create this account.')
    if body.slug in branding.RESERVED_SLUGS:
        raise HTTPException(422,'Choose another booking-page name.')
    password=PASSWORDS.hash(body.password); user=uid();shop=uid()
    try:
        with db(True) as c:
            if c.execute('SELECT 1 FROM booking_slugs WHERE slug=?',(body.slug,)).fetchone():
                raise HTTPException(409,'That booking-page name is already in use.')
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(user,str(body.email).lower(),password,body.name,0,now()))
            s=models.default_settings(body.business,str(body.email).lower(),body.timezone)
            c.execute('''INSERT INTO shops(id,owner_id,slug,name,settings,created_at,trial_end,plan_id)
              VALUES(?,?,?,?,?,?,?,?)''',(shop,user,body.slug,body.business,packed(s),now(),now()+14*86400,body.plan_id))
            c.execute('INSERT INTO booking_slugs VALUES(?,?,?)',(body.slug,shop,now()))
            auth_mail(c,user,str(body.email),'verify')
    except sqlite3.IntegrityError:
        raise HTTPException(409,'That email or booking-page name is already in use. Try signing in or choose another name.')
    csrf=login_session(response,user)
    return {'csrf':csrf,'redirect':'/app/settings','message':'Account created. Verify your email before publishing.'}

@app.post('/api/auth/login')
def login(body:models.Login,request:Request,response:Response):
    rate_limit('login-ip:'+ip(request),30,900);rate_limit('login-email:'+str(body.email).lower(),10,900)
    with db() as c: u=c.execute('SELECT * FROM users WHERE email=?',(str(body.email).lower(),)).fetchone()
    if not password_ok(u['password'] if u else None,body.password): raise HTTPException(401,'The email or password is incorrect.')
    csrf=login_session(response,u['id'])
    return {'csrf':csrf}

@app.post('/api/auth/demo')
def demo_login(response:Response):
    if not config.DEMO: raise HTTPException(404,'Not found.')
    with db() as c: u=c.execute('SELECT owner_id FROM shops WHERE is_demo=1').fetchone()
    return {'csrf':login_session(response,u['owner_id'])}

@app.get('/api/auth/me')
def me(s=Depends(session),shop=Depends(owner)):
    from .google_auth import account_methods
    return {'user':{'id':s['user_id'],'name':s['name'],'email':s['email'],'verified':bool(s['verified']),**account_methods(s['user_id'])},'csrf':s['csrf'],
            'shop':owner_shop(shop)}

@app.post('/api/auth/logout')
def logout(response:Response,s=Depends(session)):
    with db(True) as c: c.execute('DELETE FROM sessions WHERE token_hash=?',(s['token_hash'],))
    response.delete_cookie(config.COOKIE,path='/')
    return {'ok':True}

@app.post('/api/auth/forgot-password')
def forgot(body:models.EmailInput,request:Request):
    rate_limit('forgot:'+ip(request),5,3600)
    with db(True) as c:
        u=c.execute('SELECT * FROM users WHERE email=?',(str(body.email).lower(),)).fetchone()
        if u: auth_mail(c,u['id'],u['email'],'reset')
    return {'message':'If that account exists, a password-reset link will be sent.'}

@app.post('/api/auth/reset-password')
def reset_password(body:models.PasswordReset,request:Request):
    rate_limit('reset:'+ip(request),10,3600)
    hashed=PASSWORDS.hash(body.password)
    with db(True) as c:
        t=c.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND kind='reset' AND expires_at>?",(digest(body.token),now())).fetchone()
        if not t: raise HTTPException(410,'This password-reset link has expired or was already used.')
        c.execute('UPDATE users SET password=? WHERE id=?',(hashed,t['user_id']))
        c.execute('UPDATE account_auth SET password_enabled=1 WHERE user_id=?',(t['user_id'],))
        c.execute('DELETE FROM auth_tokens WHERE user_id=?',(t['user_id'],))
        c.execute('DELETE FROM sessions WHERE user_id=?',(t['user_id'],))
    return {'message':'Password updated. Please sign in again.'}

@app.post('/api/auth/verify-email')
def verify_email(body:dict,request:Request):
    rate_limit('verify:'+ip(request),20,3600)
    value=body.get('token','')
    if not isinstance(value,str) or len(value)>128: raise HTTPException(422,'Invalid verification link.')
    with db(True) as c:
        t=c.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND kind='verify' AND expires_at>?",(digest(value),now())).fetchone()
        if not t: raise HTTPException(410,'This verification link has expired or was already used.')
        c.execute('UPDATE users SET verified=1 WHERE id=?',(t['user_id'],))
        c.execute('DELETE FROM auth_tokens WHERE token_hash=?',(t['token_hash'],))
    return {'message':'Email verified. You can now publish your booking page.'}

@app.post('/api/auth/resend-verification')
def resend(request:Request,s=Depends(session)):
    rate_limit('resend:'+s['user_id'],3,3600)
    with db(True) as c:
        if not s['verified']: auth_mail(c,s['user_id'],s['email'],'verify')
    return {'message':'Verification email queued.'}

@app.post('/api/auth/change-password')
def change_password(body:models.PasswordChange,response:Response,s=Depends(session)):
    rate_limit('password:'+s['user_id'],5,3600)
    with db() as c: u=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
    if not password_ok(u['password'],body.current_password): raise HTTPException(403,'The current password is incorrect.')
    hashed=PASSWORDS.hash(body.password)
    with db(True) as c:
        c.execute('UPDATE users SET password=? WHERE id=?',(hashed,s['user_id']))
        c.execute('DELETE FROM sessions WHERE user_id=?',(s['user_id'],))
    return {'csrf':login_session(response,s['user_id']),'message':'Password changed. Other sessions were signed out.'}

def owner_shop(shop):
    return {'id':shop['id'],'slug':shop['slug'],'name':shop['name'],'settings':core.settings(shop),**branding.links(shop),'branding':branding.live_brand(shop),
        'plan_id':plans.selected(shop),'plan':plans.get(plans.selected(shop)),'features':plans.features(shop),'settings_revision':shop['settings_revision'],'has_subscription':bool(shop['paddle_subscription']) and shop['billing_status'] in ('active','trialing','past_due','paused'),'published':bool(shop['published']),'trial_end':shop['trial_end'],'billing_status':shop['billing_status'],
        'entitled':core.entitled(shop),'square_connected':bool(shop['square_access']),
        'square_location':shop['square_location'],'demo':bool(shop['is_demo']),
        'square_configured':bool(config.SQUARE_ID and config.SQUARE_SECRET and config.SQUARE_WEBHOOK),
        'billing_configured':plans.configured(plans.selected(shop))}

@app.put('/api/settings')
def save_settings(body:models.Settings,shop=Depends(owner)):
    if shop['published'] and body.payment_method=='square' and body.deposit_percent and not shop['square_access'] and not shop['is_demo']:
        raise HTTPException(422,'Connect Square before requiring deposits on a published page.')
    with db(True) as c:
        current=c.execute('SELECT settings_revision FROM shops WHERE id=?',(shop['id'],)).fetchone()
        if body.revision is not None and body.revision != current['settings_revision']:
            raise HTTPException(409,'Settings changed in another tab. Reload before saving to avoid overwriting a newer price.')
        c.execute('UPDATE shops SET name=?,settings=?,settings_revision=settings_revision+1 WHERE id=?',
            (body.name,packed(body.model_dump(exclude={'revision'})),shop['id']))
        audit(c,shop['id'],'settings.updated')
    return {'message':'Settings saved.'}

@app.post('/api/publish')
def publish(body:dict,s=Depends(session),shop=Depends(owner)):
    enabled=body.get('published')
    if not isinstance(enabled,bool): raise HTTPException(422,'Choose publish or unpublish.')
    if enabled:
        st=core.settings(shop)
        if not s['verified']: raise HTTPException(403,'Verify your email before publishing.')
        if not st.get('policy_reviewed'): raise HTTPException(422,'Review your prices, tax settings and cancellation policy, then confirm the setup checklist.')
        if not core.entitled(shop): raise HTTPException(402,'Your trial has ended. Activate a subscription to accept new bookings.')
        if st['payment_method']=='square' and not (shop['square_access'] and config.SQUARE_WEBHOOK) and not shop['is_demo']:
            raise HTTPException(422,'Connect Square or select in-person payment collection.')
    with db(True) as c:
        c.execute('UPDATE shops SET published=? WHERE id=?',(int(enabled),shop['id']))
        audit(c,shop['id'],'page.published' if enabled else 'page.unpublished')
    return {'published':enabled}

@app.get('/api/dashboard')
def dashboard(shop=Depends(owner)):
    with db() as c:
        rows=c.execute('SELECT * FROM bookings WHERE shop_id=? ORDER BY start_ts',(shop['id'],)).fetchall()
        requests=c.execute("SELECT count(*) FROM quotes WHERE shop_id=? AND status='new'",(shop['id'],)).fetchone()[0]
        failures=c.execute('SELECT count(*) FROM outbox WHERE shop_id=? AND sent_at IS NULL AND cancelled_at IS NULL AND attempts>=8',(shop['id'],)).fetchone()[0]
    zone=ZoneInfo(core.settings(shop)['timezone']); local=datetime.fromtimestamp(now(),zone)
    month_start=int(local.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp())
    month=[b for b in rows if b['start_ts']>=month_start and b['start_ts']<=now() and b['status']=='completed']
    current=[b for b in rows if b['start_ts']>=month_start and datetime.fromtimestamp(b['start_ts'],zone).month==local.month and b['status'] in ('confirmed','completed')]
    return {'stats':{'scheduled_value':sum(b['total'] for b in current),'completed_value':sum(b['total'] for b in month),
        'deposits_received':sum(max(0,b['deposit']-b['refunded']) for b in current if b['payment_status'] in ('paid','refunded','partially_refunded','refund_pending')),
        'upcoming':sum(b['status']=='confirmed' and b['start_ts']>=now() for b in rows),'quote_requests':requests,
        'pending_approvals':sum(b['status']=='pending_approval' for b in rows),'payment_reviews':sum(b['status']=='payment_review' for b in rows),'email_failures':failures},
        'bookings':[serialize_booking(b) for b in rows if b['status'] in ('pending_approval','confirmed','payment_review','awaiting_signature') and b['start_ts']>=now()-86400][:30],
        'month':local.strftime('%B %Y')}

def serialize_booking(b):
    omit={'manage_hash','manage_encrypted','request_hash','idempotency_key','square_payment','square_order','square_link','checkout_url','accepted_policy'}
    out={k:b[k] for k in b.keys() if k not in omit};out['snapshot']=json.loads(b['snapshot'])
    return out

def owned_booking(c,identifier,shop_id):
    b=c.execute('SELECT * FROM bookings WHERE id=? AND shop_id=?',(identifier,shop_id)).fetchone()
    if not b: raise HTTPException(404,'Booking not found.')
    return b

@app.get('/api/bookings')
def bookings(shop=Depends(owner)):
    with db() as c: rows=c.execute('SELECT * FROM bookings WHERE shop_id=? ORDER BY start_ts DESC LIMIT 1000',(shop['id'],)).fetchall()
    return {'bookings':[serialize_booking(b) for b in rows]}

@app.post('/api/bookings')
def manual_booking(body:models.BookingInput,shop=Depends(owner)):
    b,_=core.reserve(shop['id'],body,owner=True)
    return core.booking_result(b)

@app.post('/api/bookings/{identifier}/action')
def booking_action(identifier:str,body:models.Action,shop=Depends(owner)):
    with db(True) as c:
        b=owned_booking(c,identifier,shop['id'])
        if body.action=='complete': raise HTTPException(409,'Use Finish and sign invoice. A job is completed after its saved invoice is customer-signed.')
        if body.action=='cancel': core.cancel(c,shop,b)
        else:
            if b['status']!='confirmed': raise HTTPException(409,'Only confirmed bookings can be marked complete or no-show.')
            if b['start_ts']>now(): raise HTTPException(409,'Wait until the appointment starts before marking completion or no-show.')
            c.execute('UPDATE bookings SET status=?,updated_at=? WHERE id=?',('completed' if body.action=='complete' else 'no_show',now(),identifier))
            audit(c,shop['id'],'booking.'+body.action,identifier)
    if body.action=='cancel':
        try: payments.delete_checkout(b)
        except HTTPException: pass
    return {'message':'Booking updated. Refunds are managed separately.'}

@app.get('/api/bookings/{identifier}/slots')
def reschedule_slots(identifier:str,date:str,shop=Depends(owner)):
    with db() as c:
        b=owned_booking(c,identifier,shop['id'])
        return {'slots':core.day_slots(c,shop,date,json.loads(b['snapshot'])['minutes'],identifier)}

@app.post('/api/bookings/{identifier}/reschedule')
def change_time(identifier:str,body:models.Reschedule,shop=Depends(owner)):
    with db(True) as c:
        b=owned_booking(c,identifier,shop['id']);core.reschedule(c,shop,b,body.start_ts)
    return {'message':'Booking rescheduled. Notifications queued.'}

@app.post('/api/bookings/{identifier}/refund')
def refund(identifier:str,body:models.Refund,shop=Depends(owner)):
    with db() as c: b=owned_booking(c,identifier,shop['id'])
    return payments.refund_deposit(b)

@app.get('/api/bookings/{identifier}/link')
def booking_link(identifier:str,shop=Depends(owner)):
    with db() as c: b=owned_booking(c,identifier,shop['id'])
    return {'url':branding.public_origin(shop)+'/manage/'+decrypt(b['manage_encrypted'])}

@app.get('/api/customers')
def customers(shop=Depends(owner)):
    with db() as c:
        rows=c.execute('''SELECT email,MAX(customer_name) AS name,MAX(phone) AS phone,COUNT(*) AS bookings,
            SUM(CASE WHEN status='completed' THEN total ELSE 0 END) AS completed_value,
            MAX(start_ts) AS last_booking FROM bookings WHERE shop_id=? AND status NOT IN ('held','expired')
            GROUP BY email ORDER BY last_booking DESC''',(shop['id'],)).fetchall()
    return {'customers':[dict(x) for x in rows]}

@app.get('/api/blocks')
def blocks(shop=Depends(owner)):
    with db() as c: rows=c.execute('SELECT * FROM blocks WHERE shop_id=? AND end_ts>? ORDER BY start_ts',(shop['id'],now())).fetchall()
    return {'blocks':[dict(x) for x in rows]}

@app.post('/api/blocks')
def add_block(body:models.Block,shop=Depends(owner)):
    with db(True) as c:
        if core.conflict(c,shop['id'],body.start_ts,body.end_ts): raise HTTPException(409,'This block overlaps an appointment or an existing block. Reschedule that first.')
        identifier=uid();c.execute('INSERT INTO blocks VALUES(?,?,?,?,?)',(identifier,shop['id'],body.start_ts,body.end_ts,body.label))
    return {'id':identifier}

@app.delete('/api/blocks/{identifier}')
def delete_block(identifier:str,shop=Depends(owner)):
    with db(True) as c: c.execute('DELETE FROM blocks WHERE id=? AND shop_id=?',(identifier,shop['id']))
    return {'ok':True}

@app.get('/api/quotes')
def quotes(shop=Depends(owner)):
    with db() as c:
        rows=c.execute('SELECT * FROM quotes WHERE shop_id=? ORDER BY created_at DESC LIMIT 500',(shop['id'],)).fetchall()
        out=[]
        for q in rows:
            x={k:q[k] for k in q.keys() if k not in ('token_hash','token_encrypted')}
            x['selections']=json.loads(q['selections']);x['photos']=[p[0] for p in c.execute('SELECT id FROM photos WHERE quote_id=?',(q['id'],))]
            out.append(x)
    return {'quotes':out}

@app.post('/api/quotes/{identifier}/offer')
def offer_quote(identifier:str,body:models.QuoteOffer,shop=Depends(owner)):
    with db(True) as c:
        q=c.execute('SELECT * FROM quotes WHERE id=? AND shop_id=?',(identifier,shop['id'])).fetchone()
        if not q or q['status'] not in ('new','offered'): raise HTTPException(409,'This quote request cannot be edited now.')
        c.execute("UPDATE quotes SET status='offered',amount=?,minutes=?,message=?,expires_at=? WHERE id=?",(body.amount,body.minutes,body.message,now()+7*86400,identifier))
        url=branding.public_origin(shop)+'/quote/'+decrypt(q['token_encrypted'])
        queue_mail(c,'quote:'+identifier+':'+uid(),q['email'],'Your custom quote - '+shop['name'],
            f"{body.message}\n\nQuoted service subtotal: ${body.amount/100:.2f}. Applicable configured tax and your deposit appear before booking.\n\nChoose an appointment within 7 days: {url}",shop['id'])
        audit(c,shop['id'],'quote.offered',identifier)
    return {'message':'Quote sent. The customer can choose a time and book.','url':url}

@app.post('/api/quotes/{identifier}/decline')
def decline_quote(identifier:str,shop=Depends(owner)):
    with db(True) as c:
        q=c.execute('SELECT * FROM quotes WHERE id=? AND shop_id=?',(identifier,shop['id'])).fetchone()
        if not q or q['status'] not in ('new','offered'): raise HTTPException(409,'Quote cannot be declined now.')
        c.execute("UPDATE quotes SET status='declined' WHERE id=?",(identifier,))
        queue_mail(c,'quote-declined:'+identifier,q['email'],'Update on your quote request - '+shop['name'],
                   'The business is unable to offer this service through online booking. Please contact '+core.settings(shop)['contact_email']+' to discuss alternatives.',shop['id'])
    return {'message':'Quote declined and customer notified.'}

@app.get('/api/photos/{identifier}')
def photo(identifier:str,thumbnail:bool=False,shop=Depends(owner)):
    with db() as c:
        p=c.execute('SELECT p.* FROM photos p JOIN quotes q ON q.id=p.quote_id WHERE p.id=? AND q.shop_id=?',(identifier,shop['id'])).fetchone()
    if not p: raise HTTPException(404,'Photo not found.')
    path=config.DATA/'photos'/(p['thumb_path'] if thumbnail and p['thumb_path'] else p['path'])
    if not path.is_file(): raise HTTPException(404,'Photo file not found.')
    return FileResponse(path,media_type='image/webp',headers={'Cache-Control':'private, no-store'})

@app.get('/api/public/shops/{slug}')
def public_shop(slug:str):
    with db() as c: return core.public_shop(core.shop_by_slug(c,slug))

@app.post('/api/public/shops/{slug}/price')
def public_price(slug:str,body:models.Selection,request:Request):
    rate_limit('price:'+ip(request),120,60)
    with db() as c: return core.quote_price(c,core.shop_by_slug(c,slug),body)

@app.post('/api/public/shops/{slug}/slots')
def public_slots(slug:str,body:models.SlotRequest,request:Request):
    rate_limit('slots:'+ip(request),120,60)
    with db() as c:
        shop=core.shop_by_slug(c,slug);price=core.quote_price(c,shop,body)
        return {'slots':core.day_slots(c,shop,body.date,price['minutes']),'timezone':core.settings(shop)['timezone']}

@app.post('/api/public/shops/{slug}/book',status_code=201)
def public_book(slug:str,body:models.BookingInput,request:Request):
    rate_limit('book-ip:'+ip(request),20 if config.TESTING else 8,600)
    rate_limit('book-email:'+str(body.email).lower(),15,86400)
    with db() as c: shop=core.shop_by_slug(c,slug)
    b,_=core.reserve(shop['id'],body)
    if b['status']=='held':
        # Network uncertainty must never release the slot and then assume a payment failed.
        b=payments.create_checkout(b['id'])
    return core.booking_result(b)

@app.post('/api/public/shops/{slug}/quote-requests',status_code=201)
async def request_quote(slug:str,request:Request,data:str=Form(...),photos:list[UploadFile]=File(default=[])):
    rate_limit('quote:'+ip(request),5,3600)
    try: body=models.QuoteRequest.model_validate_json(data)
    except ValidationError as exc: raise HTTPException(422,'Check the quote request fields.') from exc
    if body.website or len(photos)>3: raise HTTPException(422,'Upload up to three photos.')
    with db() as c:
        shop=core.shop_by_slug(c,slug);s=core.settings(shop)
        if shop['is_demo']: raise HTTPException(403,'The demo does not accept quote requests.')
        if body.zip not in s['zip_codes']: raise HTTPException(422,'This ZIP code is outside the service area.')
        if not any(v['id']==body.vehicle_id for v in s['vehicles']): raise HTTPException(422,'Select a valid vehicle.')
    safe_photos=[]
    async with quote_photo_processing:
        for upload in photos:
            try:
                raw=await upload.read(media.MAX_INPUT_BYTES+1)
                safe_photos.append(await asyncio.to_thread(media.encode_photo,raw,'compact'))
            finally:
                await upload.close()
    identifier=uid();raw_token=token();paths=[]
    try:
        with db(True) as c:
            media.check_quota(c,shop['id'],sum(p.stored_bytes for p in safe_photos))
            c.execute('''INSERT INTO quotes(id,shop_id,token_hash,token_encrypted,customer_name,email,phone,address,zip,vehicle,vehicle_notes,notes,selections,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?)''',
                (identifier,shop['id'],digest(raw_token),encrypt(raw_token),body.customer_name,str(body.email).lower(),body.phone,body.address,body.zip,
                 body.vehicle_id,body.vehicle_notes,body.notes,packed(body.model_dump()),now()))
            for image in safe_photos:
                pid,filename,thumb=media.write_photo(image,paths)
                c.execute('INSERT INTO photos(id,quote_id,path,size_bytes,thumb_path) VALUES(?,?,?,?,?)',(pid,identifier,filename,image.stored_bytes,thumb))
            queue_mail(c,'new-quote:'+identifier,s['contact_email'],'New custom quote request - '+body.customer_name,
                'A customer has requested a reviewed quote. Sign in to view details and photos: '+config.BASE_URL+'/app/quotes',shop['id'],reply_to=str(body.email))
            queue_mail(c,'quote-ack:'+identifier,str(body.email),'Quote request received - '+shop['name'],
                'Your request has been received. No appointment is reserved and no payment has been taken. The business will review it and email you a quote.',shop['id'])
    except BaseException:
        for p in paths: p.unlink(missing_ok=True)
        raise
    return {'message':'Quote request sent. No appointment is reserved or payment taken.'}

@app.get('/api/public/quotes/{raw_token}')
def view_quote(raw_token:str):
    if len(raw_token)>128: raise HTTPException(404,'Quote not found.')
    with db() as c:
        q=c.execute('SELECT * FROM quotes WHERE token_hash=?',(digest(raw_token),)).fetchone()
        if not q or q['status']!='offered' or q['expires_at']<now(): raise HTTPException(410,'This quote has expired, was declined, or is already reserved.')
        shop=c.execute('SELECT * FROM shops WHERE id=?',(q['shop_id'],)).fetchone()
        if not shop['published'] or not core.entitled(shop): raise HTTPException(410,'This business is not currently accepting bookings.')
    return {'shop':core.public_shop(shop),'quote':{'amount':q['amount'],'minutes':q['minutes'],'message':q['message'],
        'expires_at':q['expires_at'],'customer_name':q['customer_name'],'email':q['email'],'phone':q['phone'],
        'address':q['address'],'zip':q['zip'],'vehicle_id':q['vehicle'],'vehicle_notes':q['vehicle_notes']}}


def managed(c,raw_token):
    if len(raw_token)>128: raise HTTPException(404,'Booking not found.')
    b=c.execute('SELECT * FROM bookings WHERE manage_hash=?',(digest(raw_token),)).fetchone()
    if not b: raise HTTPException(404,'This booking link is invalid.')
    shop=c.execute('SELECT * FROM shops WHERE id=?',(b['shop_id'],)).fetchone()
    return b,shop

@app.get('/api/public/manage/{raw_token}')
def manage(raw_token:str):
    with db() as c: b,shop=managed(c,raw_token)
    out=serialize_booking(b)
    if out['status']=='held' and out['hold_until']<=now(): out['status']='expired'
    out['checkout_url']=b['checkout_url'] if out['status']=='held' else None
    return {'booking':out,'shop':{'name':shop['name'],'slug':shop['slug'],'correspondence_email':core.shop_contact(shop),'branding':branding.live_brand(shop),**branding.links(shop),'settings':{k:core.settings(shop)[k] for k in ('timezone','contact_email','phone','cancellation_policy','cancellation_hours')}},
        'can_change':b['status']=='confirmed' and b['start_ts']>=now()+core.settings(shop)['cancellation_hours']*3600}

@app.post('/api/public/manage/{raw_token}/cancel')
def customer_cancel(raw_token:str,request:Request):
    rate_limit('manage:'+ip(request),15,600)
    with db(True) as c:
        b,shop=managed(c,raw_token)
        if b['status']!='held' and b['start_ts']<now()+core.settings(shop)['cancellation_hours']*3600:
            raise HTTPException(409,'The self-service cancellation window has closed. Please contact the business.')
        core.cancel(c,shop,b)
    try: payments.delete_checkout(b)
    except HTTPException: pass
    return {'message':'Booking cancelled. A deposit refund is a separate action reviewed by the business.'}

@app.get('/api/public/manage/{raw_token}/slots')
def customer_slots(raw_token:str,date:str):
    with db() as c:
        b,shop=managed(c,raw_token)
        return {'slots':core.day_slots(c,shop,date,json.loads(b['snapshot'])['minutes'],b['id'])}

@app.post('/api/public/manage/{raw_token}/reschedule')
def customer_reschedule(raw_token:str,body:models.Reschedule,request:Request):
    rate_limit('manage:'+ip(request),15,600)
    with db(True) as c:
        b,shop=managed(c,raw_token)
        if b['status']!='confirmed' or b['start_ts']<now()+core.settings(shop)['cancellation_hours']*3600:
            raise HTTPException(409,'Please contact the business to reschedule this booking.')
        core.reschedule(c,shop,b,body.start_ts)
    return {'message':'Booking rescheduled. Your deposit remains attached.'}

@app.get('/api/public/manage/{raw_token}/calendar.ics')
def calendar(raw_token:str):
    with db() as c: b,shop=managed(c,raw_token)
    def esc(v): return str(v).replace('\\','\\\\').replace('\n','\\n').replace(',','\\,').replace(';','\\;').replace('\r','')
    def utc(t): return datetime.fromtimestamp(t,ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ')
    text='\r\n'.join(['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Kontracts//Booking//EN','BEGIN:VEVENT',
        'UID:'+b['id']+'@kontracts','DTSTAMP:'+utc(now()),'DTSTART:'+utc(b['start_ts']),'DTEND:'+utc(b['end_ts']),
        'SUMMARY:'+esc(shop['name']+' - '+json.loads(b['snapshot'])['service']),'LOCATION:'+esc(b['address']),
        'STATUS:'+('CANCELLED' if b['status'] in ('cancelled','expired') else 'CONFIRMED' if b['status'] in ('confirmed','completed') else 'TENTATIVE'),
        'END:VEVENT','END:VCALENDAR',''])
    return Response(text,media_type='text/calendar',headers={'Content-Disposition':'attachment; filename="detail-appointment.ics"'})

@app.post('/api/demo-checkout/{raw_token}')
def demo_checkout(raw_token:str):
    raise HTTPException(403,'Demo payments are disabled on the server. Use /demo for a local simulation.')

    if not config.DEMO: raise HTTPException(404,'Not found.')
@app.post('/api/integrations/square/connect')
def connect_square(s=Depends(session),shop=Depends(owner)):
    if not config.SQUARE_ID or not config.SQUARE_SECRET: raise HTTPException(503,'The platform Square application must be configured first.')
    raw=token()
    with db(True) as c: c.execute('INSERT INTO oauth_states VALUES(?,?,?,?)',(digest(raw),s['user_id'],s['token_hash'],now()+600))
    from urllib.parse import urlencode
    scopes='MERCHANT_PROFILE_READ ORDERS_READ ORDERS_WRITE PAYMENTS_READ PAYMENTS_WRITE'
    return {'url':config.SQUARE_URL+'/oauth2/authorize?'+urlencode({'client_id':config.SQUARE_ID,'scope':scopes,'session':'false','state':raw})}

@app.get('/api/integrations/square/callback')
def square_callback(request:Request,state:str='',code:str='',error:str=''):
    if len(state)>128 or len(code)>2048: raise HTTPException(400,'Invalid authorization response.')
    with db(True) as c:
        row=c.execute('SELECT * FROM oauth_states WHERE token_hash=? AND expires_at>?',(digest(state),now())).fetchone()
        if not row or not constant_equal(row['session_hash'],digest(request.cookies.get(config.COOKIE,''))):
            raise HTTPException(403,'This authorization session is invalid or expired. Reconnect from your dashboard.')
        c.execute('DELETE FROM oauth_states WHERE token_hash=?',(row['token_hash'],))
        shop=c.execute('SELECT * FROM shops WHERE owner_id=?',(row['user_id'],)).fetchone()
    if error or not code: return RedirectResponse('/app/settings?notice=square-declined',303)
    result=payments.call(config.SQUARE_URL,'/oauth2/token','',{'client_id':config.SQUARE_ID,'client_secret':config.SQUARE_SECRET,
        'code':code,'grant_type':'authorization_code','redirect_uri':config.BASE_URL+'/api/integrations/square/callback'},square=True)
    locations=payments.call(config.SQUARE_URL,'/v2/locations',result['access_token'],method='GET',square=True).get('locations',[])
    eligible=[x for x in locations if x.get('country')=='US' and x.get('currency')=='USD' and x.get('status')=='ACTIVE' and 'CREDIT_CARD_PROCESSING' in x.get('capabilities',[])]
    if len(eligible)!=1:
        raise HTTPException(422,'This first release supports exactly one active US Square processing location. Use a single-location seller account or contact support before connecting.')
    with db(True) as c:
        c.execute('''UPDATE shops SET square_merchant=?,square_location=?,square_access=?,square_refresh=?,square_expires=? WHERE id=?''',
            (result['merchant_id'],eligible[0]['id'],encrypt(result['access_token']),encrypt(result['refresh_token']),
             int(datetime.fromisoformat(result['expires_at'].replace('Z','+00:00')).timestamp()),shop['id']))
        audit(c,shop['id'],'square.connected')
    return RedirectResponse('/app/settings?notice=square-connected',303)

@app.post('/api/integrations/square/disconnect')
def disconnect_square(shop=Depends(owner)):
    with db() as c:
        outstanding=c.execute("SELECT 1 FROM bookings WHERE shop_id=? AND (status IN ('held','pending_approval','awaiting_signature') OR payment_status IN ('paid','refund_pending','partially_refunded')) LIMIT 1",(shop['id'],)).fetchone()
        outstanding=outstanding or c.execute("SELECT 1 FROM invoices WHERE shop_id=? AND payment_method='square' LIMIT 1",(shop['id'],)).fetchone()
    if outstanding: raise HTTPException(409,'Keep Square connected while Square jobs or retained invoices need payment/refund management. Contact support for an orderly disconnect.')
    if shop['square_access']:
        payments.call(config.SQUARE_URL,'/oauth2/revoke',config.SQUARE_SECRET,{'client_id':config.SQUARE_ID,'access_token':decrypt(shop['square_access'])},square=True,auth_scheme='Client')
    with db(True) as c: c.execute('UPDATE shops SET square_merchant=NULL,square_location=NULL,square_access=NULL,square_refresh=NULL,square_expires=NULL,published=0 WHERE id=?',(shop['id'],))
    return {'message':'Square disconnected and booking page unpublished.'}

@app.post('/api/billing/checkout')
def billing_checkout(body:plans.PlanSelection=plans.PlanSelection(),shop=Depends(owner)):
    if shop['is_demo']: raise HTTPException(409,'Demo accounts cannot purchase a real subscription.')
    return payments.billing_checkout(shop['id'],body.plan_id)

@app.post('/api/billing/portal')
def billing_portal(shop=Depends(owner)):
    return {'url':payments.billing_portal(shop)}

@app.post('/api/webhooks/square')
async def square_webhook(request:Request):
    raw=await request.body()
    if not verify_square(raw,request.headers.get('x-square-hmacsha256-signature')): raise HTTPException(401,'Invalid webhook signature.')
    try: event=json.loads(raw)
    except ValueError: raise HTTPException(400,'Invalid event JSON.')
    eid=event.get('event_id');kind=event.get('type');obj=event.get('data',{}).get('object',{})
    if not isinstance(eid,str): raise HTTPException(400,'Missing event ID.')
    if kind in ('payment.created','payment.updated'):
        await asyncio.to_thread(payments.apply_square_payment,eid,event.get('merchant_id'),obj.get('payment',{}))
    elif kind in ('refund.created','refund.updated'):
        await asyncio.to_thread(payments.apply_refund,eid,event.get('merchant_id'),obj.get('refund',{}))
    elif kind=='oauth.authorization.revoked':
        with db(True) as c:
            c.execute('UPDATE shops SET square_access=NULL,square_refresh=NULL,published=0 WHERE square_merchant=?',(event.get('merchant_id'),))
    return {'received':True}

@app.post('/api/webhooks/paddle')
async def paddle_webhook(request:Request):
    raw=await request.body()
    if not verify_paddle(raw,request.headers.get('paddle-signature')): raise HTTPException(401,'Invalid webhook signature.')
    try: event=json.loads(raw)
    except ValueError: raise HTTPException(400,'Invalid event JSON.')
    await asyncio.to_thread(payments.apply_paddle,event)
    return {'received':True}

@app.get('/api/export/bookings.csv')
def export_csv(shop=Depends(owner)):
    with db() as c: rows=c.execute('SELECT * FROM bookings WHERE shop_id=? ORDER BY start_ts DESC',(shop['id'],)).fetchall()
    out=io.StringIO();w=csv.writer(out)
    w.writerow(['Booking ID','Customer','Email','Phone','Local appointment','Timezone','Status','Service total USD','Deposit USD','Payment status','Refunded USD'])
    def safe(x):
        s=str(x)
        return "'"+s if s.lstrip().startswith(('=','+','-','@','\t','\r')) else s
    for b in rows:
        w.writerow([safe(x) for x in [b['id'],b['customer_name'],b['email'],b['phone'],
            datetime.fromtimestamp(b['start_ts'],ZoneInfo(core.settings(shop)['timezone'])).isoformat(),core.settings(shop)['timezone'],
            b['status'],f"{b['total']/100:.2f}",f"{b['deposit']/100:.2f}",b['payment_status'],f"{b['refunded']/100:.2f}"]])
    return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="kontracts-bookings.csv"'})

@app.get('/api/export/account.json')
def export_account(shop=Depends(owner)):
    with db() as c:
        data={'business':owner_shop(shop),'bookings':[serialize_booking(b) for b in c.execute('SELECT * FROM bookings WHERE shop_id=?',(shop['id'],))],
          'quotes':[{k:r[k] for k in r.keys() if k not in ('token_hash','token_encrypted')} for r in c.execute('SELECT * FROM quotes WHERE shop_id=?',(shop['id'],))]}
        from . import invoices
        import base64
        data['invoices']=[]
        for inv in c.execute('SELECT * FROM invoices WHERE shop_id=?',(shop['id'],)).fetchall():
            versions=[]
            for v in c.execute('SELECT version FROM invoice_versions WHERE invoice_id=? ORDER BY version',(inv['id'],)).fetchall():
                row,value,pdf=invoices.read_version(c,inv['id'],v['version'])
                versions.append({'version':v['version'],'document':value,'pdf_base64':base64.b64encode(pdf).decode(),'pdf_sha256':row['pdf_sha256'],'seal':row['seal']})
            data['invoices'].append({**invoices.summary(inv),'versions':versions,
                'receipts':[dict(r) for r in c.execute('SELECT amount,method,recorded_at FROM invoice_receipts WHERE invoice_id=?',(inv['id'],))],
                'adjustments':[dict(r) for r in c.execute('SELECT amount,reason,created_at FROM invoice_adjustments WHERE invoice_id=?',(inv['id'],))]})

    return JSONResponse(data,headers={'Content-Disposition':'attachment; filename="kontracts-account.json"'})

@app.get('/api/operations')
def operations(shop=Depends(owner)):
    with db() as c:
        failed=[dict(r) for r in c.execute('SELECT id,subject,attempts,last_error,due_at FROM outbox WHERE shop_id=? AND sent_at IS NULL AND cancelled_at IS NULL ORDER BY due_at LIMIT 50',(shop['id'],))]
        history=[dict(r) for r in c.execute('SELECT action,entity_id,created_at FROM audit WHERE shop_id=? ORDER BY created_at DESC LIMIT 30',(shop['id'],))]
    return {'email_queue':failed,'activity':history}

@app.post('/api/operations/retry-emails')
def retry_mail(shop=Depends(owner)):
    with db(True) as c:
        updated=c.execute("""UPDATE outbox SET attempts=0,due_at=?,claimed_at=NULL
            WHERE shop_id=? AND sent_at IS NULL AND cancelled_at IS NULL AND attempts>=8
            AND COALESCE(last_error,'') NOT LIKE 'ManualReview:%'
            AND (first_attempt_at IS NULL OR first_attempt_at>?)""",(now(),shop['id'],now()-23*3600)).rowcount
    return {'message':str(updated)+' eligible email(s) returned to the queue. Manual-review messages require the platform operator.'}

@app.delete('/api/account')
def delete_account(body:models.DeleteAccount,response:Response,s=Depends(session),shop=Depends(owner)):
    if shop['is_demo']: raise HTTPException(403,'The sample account cannot be deleted.')
    with db() as c: u=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
    if not password_ok(u['password'],body.password): raise HTTPException(403,'The password is incorrect.')
    if shop['paddle_subscription'] and shop['billing_status'] not in ('canceled',):
        raise HTTPException(409,'Cancel the subscription in the billing portal and wait for cancellation to take effect before deleting the account.')
    with db(True) as c:
        if c.execute('SELECT 1 FROM invoices WHERE shop_id=? LIMIT 1',(shop['id'],)).fetchone():
            raise HTTPException(409,'This account has retained signed financial records. Export them and contact support for the documented retention/deletion process.')
        if c.execute("SELECT 1 FROM bookings WHERE shop_id=? AND (status IN ('pending_approval','awaiting_signature','held','payment_review') OR (status='confirmed' AND start_ts>? ) OR payment_status='refund_pending') LIMIT 1",(shop['id'],now())).fetchone():
            raise HTTPException(409,'Resolve upcoming bookings and pending payments before deleting this account.')
        paths=[]
        for r in c.execute('SELECT p.path,p.thumb_path FROM photos p JOIN quotes q ON q.id=p.quote_id WHERE q.shop_id=?',(shop['id'],)):
            paths.extend(x for x in r if x)
        for r in c.execute('SELECT path,thumb_path FROM job_photos WHERE shop_id=?',(shop['id'],)):
            paths.extend(r)
        c.execute('DELETE FROM users WHERE id=?',(s['user_id'],))
    for filename in paths: (config.DATA/'photos'/filename).unlink(missing_ok=True)
    response.delete_cookie(config.COOKIE,path='/')
    return {'message':'Account and active application records deleted. Independent processor records and rotating backups follow their retention policies.'}

@app.get('/api/preview-shop')
def preview_shop(shop=Depends(owner)):
    return core.public_shop(shop)

@app.post('/api/preview/price')
def preview_price(body:models.Selection,shop=Depends(owner)):
    with db() as c: return core.quote_price(c,shop,body)

@app.post('/api/preview/slots')
def preview_slots(body:models.SlotRequest,shop=Depends(owner)):
    with db() as c:
        price=core.quote_price(c,shop,body)
        return {'slots':core.day_slots(c,shop,body.date,price['minutes']),'timezone':core.settings(shop)['timezone']}

from .limits import BodySizeLimit
app.add_middleware(BodySizeLimit)
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware,minimum_size=1000,compresslevel=6)

from .branding_routes import install as install_branding_routes
install_branding_routes(app,owner,ip)

from .media_routes import install as install_media_routes
install_media_routes(app,owner,owned_booking,managed,ip)

from .release_routes import install as install_release_routes
install_release_routes(app,owner,session,login_session,ip)

from .invoices import install as install_invoice_routes
install_invoice_routes(app,owner,session,owned_booking,ip)

@app.post('/api/bookings/{identifier}/approve')
def approve_job(identifier:str,shop=Depends(owner),user=Depends(session)):
    with db(True) as c:
        current=c.execute('SELECT * FROM shops WHERE id=?',(shop['id'],)).fetchone()
        b=owned_booking(c,identifier,shop['id'])
        if not user['verified']: raise HTTPException(403,'Verify your email before approving jobs.')
        if not core.entitled(current): raise HTTPException(402,'Activate your subscription to approve new work.')
        if b['approved_at'] and b['status'] in ('held','confirmed'): return core.booking_result(b)
        if b['status']!='pending_approval': raise HTTPException(409,'Only pending requests can be approved.')
        p=json.loads(b['snapshot']);core.require_slot(c,current,b['start_ts'],p['minutes'],b['id'])
        needs_payment=b['payment_method']=='square' and b['deposit']>0
        if b['payment_method']=='square' and not (current['square_access'] and current['square_location'] and config.SQUARE_WEBHOOK):
            raise HTTPException(409,'Connect Square before approving an online-payment job.')
        c.execute('UPDATE bookings SET approved_at=?,approved_by=?,status=?,payment_status=?,hold_until=?,updated_at=? WHERE id=?',
                  (now(),user['user_id'],'held' if needs_payment else 'confirmed','pending' if needs_payment else 'unpaid',min(now()+86400,b['start_ts']) if needs_payment else None,now(),identifier))
        b=owned_booking(c,identifier,shop['id'])
        core.notify_booking(c,current,b,'Job approved - deposit requested' if needs_payment else 'Job approved - booking confirmed',identifier+':approved')
        if not needs_payment: core.notify_booking(c,current,b,'Your detail is tomorrow',identifier+':reminder',True)
        audit(c,shop['id'],'booking.approved',identifier)
    # Email goes to the durable outbox even if Square is temporarily unavailable.
    # The customer can safely retry checkout from their private management page.
    if needs_payment:
        try: b=payments.create_checkout(identifier)
        except HTTPException: return {**core.booking_result(b),'message':'Job approved. Square checkout needs retry from the customer page; no payment has been assumed.'}
    return core.booking_result(b)

@app.post('/api/public/manage/{raw_token}/pay')
def pay_deposit(raw_token:str,request:Request):
    rate_limit('deposit-pay:'+ip(request),10,600)
    with db() as c: b,shop=managed(c,raw_token)
    if not b['approved_at'] or b['payment_method']!='square' or b['status']!='held' or b['hold_until']<=now():
        raise HTTPException(409,'No payable approved reservation is available.')
    result=payments.create_checkout(b['id'])
    return {'url':result['checkout_url']}

from .hardening import RequestBoundary
app.add_middleware(RequestBoundary)

@app.get('/demo',include_in_schema=False)
def isolated_demo():
    return FileResponse(config.ROOT/'Kontracts-Preview.html',headers={'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",'Cache-Control':'no-store'})

app.mount('/assets',StaticFiles(directory=config.ROOT/'web'/'assets'),name='assets')

@app.get('/{path:path}',include_in_schema=False)
def frontend(path:str,request:Request):
    if path.startswith('api/'): raise HTTPException(404,'API endpoint not found.')
    return brand_frontend.response(path,getattr(request.state,'tenant',None))
