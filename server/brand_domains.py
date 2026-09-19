"""Optional tenant hosts: DNS proof plus explicit operator-approved HTTPS.

No arbitrary URL fetches, wildcard Host trust, CORS wildcards or automatic TLS
claims. The normal /book/{slug} pages do not require DNS configuration per shop.
"""
import ipaddress
import re
import shlex
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from pydantic import Field, field_validator
from . import config, branding, plans
from .models import Model
from .db import db,now
from .security import digest

class DomainInput(Model):
    hostname: str = Field(min_length=4,max_length=253)
    @field_validator('hostname')
    @classmethod
    def clean(cls,value):return normalize_domain(value)


def normalize_domain(value):
    value=value.strip().lower().rstrip('.')
    if '://' in value or '/' in value or ':' in value or '*' in value:
        raise ValueError('Enter only a hostname, such as book.yourbusiness.com.')
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',value):
        raise ValueError('Use a valid public DNS hostname; no IP addresses or wildcards.')
    if value.endswith(('.local','.localhost','.internal','.test','.invalid','.example','.onion','.home.arpa')):
        raise ValueError('Use a public domain that you control.')
    try:ipaddress.ip_address(value)
    except ValueError:pass
    else:raise ValueError('IP addresses are not supported.')
    primary=urlsplit(config.BASE_URL).hostname
    if value==primary:raise ValueError('This hostname belongs to the main application.')
    return value


def txt_records(hostname):
    """Fixed DoH endpoint prevents SSRF through a user-controlled hostname."""
    try:
        r=httpx.get('https://cloudflare-dns.com/dns-query',params={'name':'_kontracts.'+hostname,'type':'TXT'},headers={'Accept':'application/dns-json'},timeout=8,follow_redirects=False)
        r.raise_for_status();payload=r.json()
        if payload.get('Status') not in (0,3):raise ValueError('DNS resolver could not confirm the record')
        return [''.join(shlex.split(item.get('data',''))) for item in payload.get('Answer',[]) if item.get('type')==16]
    except (httpx.HTTPError,ValueError,TypeError,KeyError) as exc:
        raise HTTPException(502,'DNS verification is temporarily unavailable. Your booking link still works. Please retry.') from exc


def tenant_for_host(request):
    host=(request.url.hostname or '').lower().rstrip('.')
    primary=(urlsplit(config.BASE_URL).hostname or '').lower()
    # Platform health checks contain no customer data and need no custom Host.
    if request.url.path=='/api/health' and request.method in ('GET','HEAD'):
        return None
    if host==primary or (not config.PRODUCTION and host in ('localhost','127.0.0.1','testserver','kontracts.local')):
        return None
    with db() as c:
        d=c.execute('SELECT d.*,s.slug FROM shop_domains d JOIN shops s ON s.id=d.shop_id WHERE d.hostname=?',(host,)).fetchone()
    if not d:raise HTTPException(421,'This hostname is not connected to a business.')
    if request.url.path=='/.well-known/kontracts-domain' and request.method=='GET':
        return dict(d)
    if not d['active'] or not d['verified_at']:
        raise HTTPException(421,'This domain is awaiting ownership verification and HTTPS activation. Use the business\'s original booking link.')
    with db() as c: shop=c.execute('SELECT * FROM shops WHERE id=?',(d['shop_id'],)).fetchone()
    result=dict(d);result['plan_enabled']=plans.features(shop)['custom_domain']
    return result


def enforce_tenant_path(request,tenant):
    if not tenant:return
    path=request.url.path
    if path=='/.well-known/kontracts-domain':return
    if path in ('/','/api/config','/terms','/privacy') and request.method=='GET':return
    if path.startswith('/assets/') and request.method in ('GET','HEAD'):return
    # Same-host requests are constrained to this business, even with a valid
    # token for another business. Admin and provider callbacks remain canonical.
    patterns=[
        (r'^/api/public/shops/([^/]+)(?:/|$)','slug'),
        (r'^/book/([^/]+)(?:/|$)','slug'),
        (r'^/api/public/brand-assets/([^/]+)(?:/|$)','slug'),
        (r'^/api/public/brand-logo/([^/]+)(?:/|$)','slug'),
        (r'^/api/public/manage/([^/]+)(?:/|$)','booking'),
        (r'^/manage/([^/]+)(?:/|$)','booking'),
        (r'^/api/public/quotes/([^/]+)(?:/|$)','quote'),
        (r'^/quote/([^/]+)(?:/|$)','quote'),
    ]
    if config.DEMO:
        patterns += [(r'^/(?:api/)?demo-checkout/([^/]+)(?:/|$)','booking')]
    for pattern,kind in patterns:
        match=re.match(pattern,path)
        if not match:continue
        key=match.group(1)
        with db() as c:
            if kind=='slug':
                row=c.execute('SELECT id AS shop_id FROM shops WHERE slug=? UNION SELECT shop_id FROM booking_slugs WHERE slug=?',(key,key)).fetchone()
            else:
                table,column=('bookings','manage_hash') if kind=='booking' else ('quotes','token_hash')
                row=c.execute('SELECT shop_id FROM '+table+' WHERE '+column+'=?',(digest(key),)).fetchone()
        if row and row['shop_id']==tenant['shop_id']:return
        raise HTTPException(404,'This link does not belong to this business.')
    raise HTTPException(404,'This page is not available on a business booking domain. Use the main application to sign in.')
