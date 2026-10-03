"""Google OpenID Connect: one-time browser-bound code flow, PKCE and signed JWTs.

Google subjects, not emails, are identity keys. Existing email accounts require
explicit authenticated linking; a Google email match never takes an account over.
No Google access/refresh tokens or credentials are stored in browser storage.
"""
import base64
import hashlib
import sqlite3
from typing import Literal
from urllib.parse import urlencode
import httpx
import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field, EmailStr, TypeAdapter
from . import config, models, branding
from .db import db, now, uid, packed, audit
from .security import token, digest, encrypt, decrypt, constant_equal, rate_limit, PASSWORDS, password_ok

# Fixed provider endpoints: no client-supplied URL, JWKS URI, issuer or algorithm.
JWKS = PyJWKClient('https://www.googleapis.com/oauth2/v3/certs', cache_jwk_set=True, lifespan=3600, timeout=8)
EMAIL = TypeAdapter(EmailStr)

class Start(models.Model):
    plan_id: Literal['solo'] = 'solo'

class Link(models.Model):
    password: str = Field(min_length=1,max_length=128)

class Finish(models.Model):
    business: str = Field(min_length=2,max_length=80)
    slug: str = Field(min_length=3,max_length=45,pattern=branding.SLUG_PATTERN)
    timezone: str = Field(default='America/New_York')
    accepted_terms: Literal[True]
    plan_id: Literal['solo'] = 'solo'
    website: str = Field(default='',max_length=200)


def cookie_name(kind):
    return ('__Host-' if config.PRODUCTION else '')+'kontracts-google-'+kind


def set_cookie(response, kind, raw):
    response.set_cookie(cookie_name(kind),raw,max_age=600,secure=config.PRODUCTION,httponly=True,samesite='lax',path='/')


def account_methods(user_id):
    with db() as c:
        google=c.execute('SELECT email FROM google_identities WHERE user_id=?',(user_id,)).fetchone()
        local=c.execute('SELECT password_enabled FROM account_auth WHERE user_id=?',(user_id,)).fetchone()
    return {'google_linked':bool(google),'google_email':google['email'] if google else None,
            'local_login_enabled':bool(local['password_enabled']) if local else True}


def validate_token(raw, nonce):
    """Verify signature plus every identity-critical claim before using a subject."""
    try:
        if not isinstance(raw,str) or len(raw)>16000: raise ValueError('token')
        key=JWKS.get_signing_key_from_jwt(raw).key
        data=jwt.decode(raw,key,algorithms=['RS256'],audience=config.GOOGLE_CLIENT_ID,
                        issuer=['https://accounts.google.com','accounts.google.com'],
                        options={'require':['sub','aud','iss','exp','iat','nonce','email','email_verified']},leeway=20)
        if data.get('azp') and data['azp']!=config.GOOGLE_CLIENT_ID: raise ValueError('presenter')
        if not constant_equal(str(data.get('nonce','')),nonce): raise ValueError('nonce')
        if data.get('email_verified') is not True: raise ValueError('email')
        sub=data['sub']
        if not isinstance(sub,str) or not 1<=len(sub)<=255 or not sub.isascii(): raise ValueError('subject')
        email=str(EMAIL.validate_python(data['email'])).lower()
        name=' '.join(str(data.get('name','Business owner')).split())[:80] or 'Business owner'
        return {'sub':sub,'email':email,'name':name}
    except (jwt.PyJWTError,ValueError,TypeError,KeyError) as exc:
        raise HTTPException(401,'Google could not verify this sign-in. Please try again.') from exc


def exchange(code, verifier):
    try:
        r=httpx.post('https://oauth2.googleapis.com/token',data={
            'code':code,'client_id':config.GOOGLE_CLIENT_ID,'client_secret':config.GOOGLE_CLIENT_SECRET,
            'redirect_uri':config.BASE_URL+'/api/auth/google/callback','grant_type':'authorization_code',
            'code_verifier':verifier},timeout=12,follow_redirects=False)
        r.raise_for_status()
        return r.json()['id_token']
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        raise HTTPException(502,'Google sign-in is temporarily unavailable. Please try again or use your password.') from exc


def begin(response, purpose, plan='starter', user=None):
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise HTTPException(503,'Google sign-in is not configured yet. Email and password still work.')
    raw=token();browser=token();nonce=token();verifier=token()+token()
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    with db(True) as c:
        c.execute('DELETE FROM google_flows WHERE expires_at<=?',(now(),))
        c.execute('DELETE FROM google_pending WHERE expires_at<=?',(now(),))
        c.execute('INSERT INTO google_flows VALUES(?,?,?,?,?,?,?,?,?)',
            (digest(raw),digest(browser),nonce,encrypt(verifier),purpose,user['user_id'] if user else None,
             user['token_hash'] if user else None,plan,now()+600))
    set_cookie(response,'state',browser)
    return {'url':'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({
        'client_id':config.GOOGLE_CLIENT_ID,'response_type':'code','scope':'openid email profile',
        'redirect_uri':config.BASE_URL+'/api/auth/google/callback','state':raw,'nonce':nonce,
        'code_challenge':challenge,'code_challenge_method':'S256','prompt':'select_account'})}


def pending(request):
    raw=request.cookies.get(cookie_name('signup'),'')
    with db() as c:
        row=c.execute('SELECT * FROM google_pending WHERE token_hash=? AND expires_at>?',(digest(raw),now())).fetchone()
    if not row: raise HTTPException(410,'Your sign-up session expired. Continue with Google again.')
    return row


def install(app,session,login_session,ip):
    @app.post('/api/auth/google/start')
    def start(body:Start,request:Request,response:Response):
        rate_limit('google-start:'+ip(request),15,600)
        return begin(response,'login',body.plan_id)

    @app.post('/api/auth/google/link')
    def link(body:Link,request:Request,response:Response,s=Depends(session)):
        rate_limit('google-link:'+s['user_id'],5,600)
        with db() as c:u=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
        if not password_ok(u['password'],body.password): raise HTTPException(403,'Enter your current password to connect Google.')
        return begin(response,'link',user=s)

    @app.post('/api/auth/google/unlink')
    def unlink(body:Link,s=Depends(session)):
        rate_limit('google-unlink:'+s['user_id'],5,600)
        methods=account_methods(s['user_id'])
        if not methods['local_login_enabled']: raise HTTPException(409,'Set a password with the password-reset link before disconnecting Google.')
        with db() as c:u=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
        if not password_ok(u['password'],body.password): raise HTTPException(403,'The current password is incorrect.')
        with db(True) as c:
            c.execute('DELETE FROM google_identities WHERE user_id=?',(s['user_id'],))
            c.execute('DELETE FROM sessions WHERE user_id=? AND token_hash!=?',(s['user_id'],s['token_hash']))
        return {'message':'Google disconnected. Your password still works.'}

    @app.get('/api/auth/google/callback')
    def callback(request:Request,state:str='',code:str='',error:str=''):
        rate_limit('google-callback:'+ip(request),30,600)
        if len(state)>128 or len(code)>4096: raise HTTPException(400,'Invalid sign-in response.')
        with db(True) as c:
            flow=c.execute('SELECT * FROM google_flows WHERE state_hash=? AND expires_at>?',(digest(state),now())).fetchone()
            if not flow or not constant_equal(flow['browser_hash'],digest(request.cookies.get(cookie_name('state'),''))):
                raise HTTPException(403,'This sign-in session is invalid or expired. Start again from Log in.')
            if flow['purpose']=='link':
                if not constant_equal(flow['session_hash'],digest(request.cookies.get(config.COOKIE,''))):
                    raise HTTPException(403,'Your account session changed. Sign in again before connecting Google.')
                if not c.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?',(flow['session_hash'],now())).fetchone():
                    raise HTTPException(403,'Your account session expired.')
            c.execute('DELETE FROM google_flows WHERE state_hash=?',(digest(state),))
        if error or not code:
            result=RedirectResponse('/login?notice=google-cancelled',303)
        else:
            try:
                identity=validate_token(exchange(code,decrypt(flow['verifier'])),flow['nonce'])
                with db(True) as c:
                    known=c.execute('SELECT * FROM google_identities WHERE subject=?',(identity['sub'],)).fetchone()
                    if flow['purpose']=='link':
                        u=c.execute('SELECT * FROM users WHERE id=?',(flow['user_id'],)).fetchone()
                        if not u or u['email'].lower()!=identity['email']:
                            raise HTTPException(409,'Use the Google account with the same email as your Kontracts account.')
                        if known and known['user_id']!=u['id']: raise HTTPException(409,'That Google account is already linked.')
                        other=c.execute('SELECT * FROM google_identities WHERE user_id=?',(u['id'],)).fetchone()
                        if other and other['subject']!=identity['sub']: raise HTTPException(409,'Disconnect your current Google account first.')
                        c.execute('INSERT OR IGNORE INTO google_identities VALUES(?,?,?,?)',(identity['sub'],u['id'],identity['email'],now()))
                        c.execute('UPDATE users SET verified=1 WHERE id=?',(u['id'],))
                        result=RedirectResponse('/app/settings?notice=google-linked',303)
                        login_user=None
                    elif known:
                        login_user=known['user_id'];result=RedirectResponse('/app',303)
                    else:
                        if c.execute('SELECT 1 FROM users WHERE email=?',(identity['email'],)).fetchone():
                            result=RedirectResponse('/login?notice=google-link-required',303);login_user=None
                        else:
                            raw=token()
                            c.execute('INSERT INTO google_pending VALUES(?,?,?,?,?,?)',
                                (digest(raw),identity['sub'],identity['email'],identity['name'],flow['plan_id'],now()+600))
                            result=RedirectResponse('/google/complete',303);set_cookie(result,'signup',raw);login_user=None
                if login_user:
                    with db(True) as c:
                        c.execute('DELETE FROM sessions WHERE token_hash=?',(digest(request.cookies.get(config.COOKIE,'')),))
                    login_session(result,login_user)
            except HTTPException:
                result=RedirectResponse('/login?notice=google-failed',303)
            except sqlite3.IntegrityError:
                result=RedirectResponse('/login?notice=google-link-required',303)
        result.delete_cookie(cookie_name('state'),path='/')
        return result

    @app.get('/api/auth/google/pending')
    def get_pending(request:Request):
        row=pending(request)
        return {'email':row['email'],'name':row['name'],'plan_id':row['plan_id']}

    @app.post('/api/auth/google/complete',status_code=201)
    def complete(body:Finish,request:Request,response:Response):
        rate_limit('google-register:'+ip(request),5,3600)
        if body.website or body.slug in branding.RESERVED_SLUGS or body.timezone not in config.US_ZONES:
            raise HTTPException(422,'Choose a valid business, booking name and US timezone.')
        row=pending(request);user=uid();shop=uid()
        try:
            with db(True) as c:
                current=c.execute('SELECT * FROM google_pending WHERE token_hash=? AND expires_at>?',(row['token_hash'],now())).fetchone()
                if not current: raise HTTPException(410,'Sign-up session already used. Please sign in.')
                if c.execute('SELECT 1 FROM booking_slugs WHERE slug=?',(body.slug,)).fetchone():
                    raise HTTPException(409,'That booking page name is taken. Choose another.')
                c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(user,row['email'],PASSWORDS.hash(token()),row['name'],1,now()))
                settings=models.default_settings(body.business,row['email'],body.timezone)
                c.execute('INSERT INTO shops(id,owner_id,slug,name,settings,created_at,trial_end,plan_id) VALUES(?,?,?,?,?,?,?,?)',
                          (shop,user,body.slug,body.business,packed(settings),now(),now()+14*86400,body.plan_id))
                c.execute('INSERT INTO booking_slugs VALUES(?,?,?)',(body.slug,shop,now()))
                c.execute('INSERT INTO google_identities VALUES(?,?,?,?)',(row['subject'],user,row['email'],now()))
                c.execute('INSERT INTO account_auth VALUES(?,0)',(user,))
                c.execute('DELETE FROM google_pending WHERE token_hash=?',(row['token_hash'],))
                audit(c,shop,'account.google.created')
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409,'An account or booking name already exists. Try signing in or choose another name.') from exc
        response.delete_cookie(cookie_name('signup'),path='/')
        return {'csrf':login_session(response,user),'redirect':'/app/setup'}
