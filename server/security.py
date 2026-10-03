"""Authentication, encryption, CSRF support and signed webhook primitives."""
import base64
import hashlib
import hmac
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from fastapi import HTTPException
from . import config
from .db import db, now

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
FERNET = Fernet(base64.urlsafe_b64encode(hashlib.sha256(config.SECRET.encode()).digest()))
DUMMY = PASSWORDS.hash('dummy-not-a-real-password-123')

def token(): return secrets.token_urlsafe(32)
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def encrypt(value): return FERNET.encrypt(value.encode()).decode()
def decrypt(value): return FERNET.decrypt(value.encode()).decode()
def password_ok(stored, password):
    try: return PASSWORDS.verify(stored or DUMMY,password)
    except (VerifyMismatchError, InvalidHashError, VerificationError): return False

def rate_limit(key, limit=30, window=60):
    key = digest(key)
    with db(True) as c:
        r = c.execute('SELECT * FROM rate_limits WHERE key=?',(key,)).fetchone()
        if r and r['expires_at']>now():
            if r['count']>=limit: raise HTTPException(429,'Too many attempts. Please try again shortly.',headers={'Retry-After':str(r['expires_at']-now())})
            c.execute('UPDATE rate_limits SET count=count+1 WHERE key=?',(key,))
        else:
            c.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?)',(key,1,now()+window))

def constant_equal(a,b):
    try: return hmac.compare_digest(a.encode(),b.encode())
    except (AttributeError,UnicodeError): return False

def verify_square(raw,signature):
    if not config.SQUARE_WEBHOOK: return False
    message=(config.BASE_URL+'/api/webhooks/square').encode()+raw
    expected=base64.b64encode(hmac.new(config.SQUARE_WEBHOOK.encode(),message,hashlib.sha256).digest()).decode()
    return constant_equal(expected, signature or '')

def verify_paddle(raw,signature):
    if not config.PADDLE_WEBHOOK or not signature: return False
    parts=[x.strip().split('=',1) for x in signature.split(';')]
    ts=next((p[1] for p in parts if len(p)==2 and p[0]=='ts'),None)
    if not ts or not ts.isdigit() or abs(now()-int(ts))>5: return False
    expected=hmac.new(config.PADDLE_WEBHOOK.encode(),ts.encode()+b':'+raw,hashlib.sha256).hexdigest()
    return any(len(p)==2 and p[0]=='h1' and constant_equal(expected,p[1]) for p in parts)

def billing_binding(shop_id,customer_id):
    return hmac.new(config.SECRET.encode(),f'{shop_id}:{customer_id}:{config.PADDLE_PRICE}'.encode(),hashlib.sha256).hexdigest()
