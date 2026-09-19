"""Environment configuration. Production deliberately fails closed."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Private database, sidecars, uploads and local mail are owner-only by default.
os.umask(0o077)
for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines() if (ROOT / '.env').exists() else []:
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_data_path = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).expanduser()
DATA = (_data_path if _data_path.is_absolute() else ROOT / _data_path).resolve()
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
if os.name != 'nt': DATA.chmod(0o700)
# Reuse a legacy database in place; never silently start an empty account database.
_default_database = DATA / 'kontracts.sqlite3'
_legacy_database = DATA / 'detaillane.sqlite3'
if not _default_database.exists() and _legacy_database.exists():
    _default_database = _legacy_database
_db_path = Path(os.getenv('DATABASE_PATH', str(_default_database))).expanduser()
DB = str((_db_path if _db_path.is_absolute() else ROOT / _db_path).resolve())
PRODUCTION = os.getenv('APP_ENV', 'development') == 'production'
BASE_URL = os.getenv('BASE_URL', 'https://getkontracts.app' if PRODUCTION else 'http://localhost:8000').strip().rstrip('/')
from urllib.parse import urlsplit
_base=urlsplit(BASE_URL)
if _base.scheme not in ('http','https') or not _base.hostname or _base.username or _base.password or _base.path or _base.query or _base.fragment:
    raise RuntimeError('BASE_URL must be a canonical HTTP(S) origin with no path, credentials, query or fragment.')
if PRODUCTION:
    from importlib.metadata import version
    for package,minimum in [('starlette',(1,6,0)),('cryptography',(50,0,1)),('python-multipart',(0,0,32)),('reportlab',(5,0,1))]:
        if tuple(int(x) for x in version(package).split('.')[:3]) < minimum:
            raise RuntimeError('Install the patched production requirements: '+package+' is below the release minimum.')
DEMO = os.getenv('DEMO_MODE', 'false').lower() == 'true'
SECRET = os.getenv('APP_SECRET', '')
if not SECRET and not PRODUCTION:
    p = DATA / '.development-secret'
    if not p.exists():
        p.write_text(secrets.token_urlsafe(48)); p.chmod(0o600)
    SECRET = p.read_text().strip()
if len(SECRET) < 40:
    raise RuntimeError('APP_SECRET must contain at least 40 random characters.')
if PRODUCTION and (not BASE_URL.startswith('https://') or DEMO):
    raise RuntimeError('Production requires HTTPS and DEMO_MODE=false.')
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
SMTP_HOST = os.getenv('SMTP_HOST', '')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
# Three outbound channels plus one support inbox. Legacy installations may
# still provide MAIL_FROM/SMTP_FROM; new installations use explicit identities.
from .mail import mailbox
_legacy_from = os.getenv('MAIL_FROM') or os.getenv('SMTP_FROM')
try:
    _domain_hint = os.getenv('MAIL_ACCOUNTS_FROM') or _legacy_from
    _default_domain = (mailbox(_domain_hint, 'MAIL_ACCOUNTS_FROM')[1].rsplit('@', 1)[1]
                       if _domain_hint else 'getkontracts.app')
    MAIL_SENDER_DOMAIN = (os.getenv('MAIL_SENDER_DOMAIN') or _default_domain).strip().lower()
    MAIL_ACCOUNTS_FROM, MAIL_ACCOUNTS_ADDRESS = mailbox(
        os.getenv('MAIL_ACCOUNTS_FROM') or _legacy_from or 'Kontracts <accounts@' + MAIL_SENDER_DOMAIN + '>', 'MAIL_ACCOUNTS_FROM')
    MAIL_NOTIFICATIONS_FROM, MAIL_NOTIFICATIONS_ADDRESS = mailbox(
        os.getenv('MAIL_NOTIFICATIONS_FROM') or _legacy_from or 'Kontracts <notifications@' + MAIL_SENDER_DOMAIN + '>', 'MAIL_NOTIFICATIONS_FROM')
    MAIL_BILLING_FROM, MAIL_BILLING_ADDRESS = mailbox(
        os.getenv('MAIL_BILLING_FROM') or _legacy_from or 'Kontracts Billing <billing@' + MAIL_SENDER_DOMAIN + '>', 'MAIL_BILLING_FROM')
    SUPPORT_EMAIL = mailbox(os.getenv('SUPPORT_EMAIL') or 'support@' + MAIL_SENDER_DOMAIN, 'SUPPORT_EMAIL', address_only=True)[1]
    MAIL_REPLY_TO = mailbox(os.getenv('MAIL_REPLY_TO') or SUPPORT_EMAIL, 'MAIL_REPLY_TO', address_only=True)[1]
except ValueError as exc:
    raise RuntimeError(str(exc)) from exc
if any(address.rsplit('@', 1)[1].lower() != MAIL_SENDER_DOMAIN for address in
       (MAIL_ACCOUNTS_ADDRESS, MAIL_NOTIFICATIONS_ADDRESS, MAIL_BILLING_ADDRESS)):
    raise RuntimeError('All three app senders must use MAIL_SENDER_DOMAIN. Verify that domain with the provider.')
# Compatibility for older extensions; new code uses the explicit channel fields.
SMTP_FROM = MAIL_ACCOUNTS_FROM
MAIL_ADDRESS = SUPPORT_EMAIL

def _mail_bool(name, default):
    value = os.getenv(name, default).lower()
    if value not in ('true', 'false'):
        raise RuntimeError(name + ' must be true or false.')
    return value == 'true'

def _mail_int(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        if value < 0: raise ValueError()
        return value
    except ValueError as exc:
        raise RuntimeError(name + ' must be a non-negative integer (0 disables this local cap).') from exc

MAIL_ENABLED = _mail_bool('MAIL_ENABLED', 'true')
if PRODUCTION and not MAIL_ENABLED:
    raise RuntimeError('Production requires MAIL_ENABLED=true for account verification and invoice signing. Stop the app for mail maintenance.')
MAIL_BILLING_NOTICES = _mail_bool('MAIL_BILLING_NOTICES', 'true')
MAIL_DAILY_LIMIT = _mail_int('MAIL_DAILY_LIMIT', 90)
# Rolling 31 days: deliberately conservative, not the provider's billing cycle.
MAIL_31DAY_LIMIT = _mail_int('MAIL_31DAY_LIMIT', 2700)
try:
    MAIL_SEND_INTERVAL = float(os.getenv('MAIL_SEND_INTERVAL', '0.6'))
    import math as _math
    if not _math.isfinite(MAIL_SEND_INTERVAL) or not 0 <= MAIL_SEND_INTERVAL <= 60:
        raise ValueError()
except ValueError as exc:
    raise RuntimeError('MAIL_SEND_INTERVAL must be 0 to 60 seconds.') from exc
LEGAL_NAME = os.getenv('LEGAL_NAME', '')
LEGAL_ADDRESS = os.getenv('LEGAL_ADDRESS', '')
_placeholder_domains = ('example.com', 'example.net', 'example.org')
if PRODUCTION and (not (SMTP_HOST or RESEND_API_KEY) or not LEGAL_NAME or not LEGAL_ADDRESS
        or MAIL_SENDER_DOMAIN in _placeholder_domains or SUPPORT_EMAIL.rsplit('@', 1)[1] in _placeholder_domains):
    raise RuntimeError('Production requires a mail provider, real senders/support, LEGAL_NAME and LEGAL_ADDRESS.')
SQUARE_ENV = os.getenv('SQUARE_ENV', 'sandbox')
SQUARE_URL = 'https://connect.squareup.com' if SQUARE_ENV == 'production' else 'https://connect.squareupsandbox.com'
SQUARE_ID = os.getenv('SQUARE_APPLICATION_ID', '')
SQUARE_SECRET = os.getenv('SQUARE_APPLICATION_SECRET', '')
SQUARE_WEBHOOK = os.getenv('SQUARE_WEBHOOK_SIGNATURE_KEY', '')
SQUARE_VERSION = os.getenv('SQUARE_API_VERSION', '2026-08-19')
if SQUARE_ENV not in ('sandbox','production'): raise RuntimeError('Invalid SQUARE_ENV.')
PADDLE_ENV = os.getenv('PADDLE_ENV', 'sandbox')
PADDLE_URL = 'https://api.paddle.com' if PADDLE_ENV == 'production' else 'https://sandbox-api.paddle.com'
PADDLE_KEY = os.getenv('PADDLE_API_KEY', '')
PADDLE_CLIENT = os.getenv('PADDLE_CLIENT_TOKEN', '')
PADDLE_WEBHOOK = os.getenv('PADDLE_WEBHOOK_SECRET', '')
PADDLE_PRICE = os.getenv('PADDLE_PRICE_ID', '')
if PADDLE_ENV not in ('sandbox','production'): raise RuntimeError('Invalid PADDLE_ENV.')
if PRODUCTION and ((PADDLE_KEY and PADDLE_ENV!='production') or (SQUARE_ID and SQUARE_ENV!='production')):
    raise RuntimeError('Production must not use sandbox payment credentials. Use a separate staging deployment.')
COOKIE = '__Host-kontracts' if PRODUCTION else 'kontracts_session'
TESTING = os.getenv('TESTING', 'false') == 'true'
US_ZONES = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu']

# Production must publish operator-reviewed policies, not the development placeholders.
def policy_text(key):
    value=os.getenv(key,'')
    if not value: return ''
    path=Path(value).expanduser().resolve()
    if not path.is_file(): raise RuntimeError(key+' must point to an existing UTF-8 text file.')
    text=path.read_text(encoding='utf-8').strip()
    if not 300 <= len(text) <= 100000: raise RuntimeError(key+' must contain 300 to 100000 characters.')
    return text
TERMS_TEXT=policy_text('LEGAL_TERMS_PATH')
PRIVACY_TEXT=policy_text('LEGAL_PRIVACY_PATH')
if PRODUCTION and (not TERMS_TEXT or not PRIVACY_TEXT or os.getenv('LEGAL_REVIEW_CONFIRMED')!='true'):
    raise RuntimeError('Production requires reviewed policy files and LEGAL_REVIEW_CONFIRMED=true. See docs/LEGAL_CHECKLIST.md.')

# Conservative defaults for a small persistent volume. Set larger limits deliberately.
PHOTO_WORKSPACE_LIMIT = int(os.getenv('PHOTO_WORKSPACE_LIMIT_MB', '30')) * 1024 * 1024
PHOTO_GLOBAL_LIMIT = int(os.getenv('PHOTO_GLOBAL_LIMIT_MB', '300')) * 1024 * 1024
PHOTO_MIN_FREE = int(os.getenv('PHOTO_MIN_FREE_MB', '40')) * 1024 * 1024
PHOTO_PER_BOOKING = 24
PHOTO_PER_CUSTOMER_BOOKING = 6

# Optional operator-approved From addresses. Verify every domain with the email
# provider first. Business users cannot set these values or impersonate senders.
import json as _json
import re as _re
BRAND_EMAIL_SENDERS = _json.loads(os.getenv('BRAND_EMAIL_SENDERS_JSON', '{}'))
if not isinstance(BRAND_EMAIL_SENDERS, dict) or any(
    not isinstance(k,str) or not isinstance(v,str) or not _re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}',v)
    for k,v in BRAND_EMAIL_SENDERS.items()
):
    raise RuntimeError('BRAND_EMAIL_SENDERS_JSON must map shop IDs or slugs to verified email addresses.')

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
PADDLE_STARTER_PRICE_ID = os.getenv('PADDLE_STARTER_PRICE_ID', '')
PADDLE_PRO_PRICE_ID = os.getenv('PADDLE_PRO_PRICE_ID', '')
PADDLE_BUSINESS_PRICE_ID = os.getenv('PADDLE_BUSINESS_PRICE_ID', '')
