"""Tenant branding, public-safe assets, and stable booking-page addresses.

Brand artwork is public only after publication. Customer vehicle photos continue
using the independent, private photo endpoints. Small artwork blobs live in the
SQLite backup, with strict per-image limits and bounded retained versions.
"""
from __future__ import annotations
import hashlib
import html
import io
import json
import re
from email.utils import formataddr, parseaddr
from urllib.parse import urlsplit
from fastapi import HTTPException
from pydantic import Field, field_validator
from PIL import Image, ImageOps, UnidentifiedImageError
from . import config
from .db import db, now, packed, uid
from .models import Model

RESERVED_SLUGS = frozenset(('api','app','admin','demo','support','assets','book','checkout','terms','privacy','login','register','manage','quote','brand-preview','www','mail','static','billing','help'))
SLUG_PATTERN = r'^[a-z0-9]+(?:-[a-z0-9]+)*$'
ASSET_LIMITS = {'logo': (640, 120*1024), 'cover': (1600, 320*1024)}

class BrandSettings(Model):
    primary_color: str = Field(default='#144c3b', pattern=r'^#[0-9a-fA-F]{6}$')
    button_color: str = Field(default='#144c3b', pattern=r'^#[0-9a-fA-F]{6}$')
    headline: str = Field(default='A fresh start for your car.', max_length=100)
    introduction: str = Field(default='', max_length=240)
    logo_id: str = Field(default='', max_length=36, pattern=r'^(?:[0-9a-f-]{36})?$')
    cover_id: str = Field(default='', max_length=36, pattern=r'^(?:[0-9a-f-]{36})?$')
    show_phone: bool = True
    show_email: bool = True
    hide_powered_by: bool = False
    website_url: str = Field(default='', max_length=300)
    instagram_url: str = Field(default='', max_length=300)
    facebook_url: str = Field(default='', max_length=300)
    footer_note: str = Field(default='', max_length=180)

    @field_validator('headline','introduction','footer_note')
    @classmethod
    def single_line(cls, value):
        if any(ord(ch) < 32 for ch in value):
            raise ValueError('Use a single line of text.')
        return value

    @field_validator('website_url','instagram_url','facebook_url')
    @classmethod
    def safe_link(cls, value):
        if not value: return ''
        p=urlsplit(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None,443) or any(ord(ch)<33 for ch in value):
            raise ValueError('Use a complete HTTPS address, without a username or password.')
        return value

class SaveBrand(Model):
    revision: int = Field(ge=0, strict=True)
    branding: BrandSettings

class BrandRevision(Model):
    revision: int = Field(ge=0, strict=True)

class SlugChange(Model):
    slug: str = Field(min_length=3,max_length=45,pattern=SLUG_PATTERN)


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS shop_branding (
      shop_id TEXT PRIMARY KEY REFERENCES shops(id) ON DELETE CASCADE,
      draft TEXT NOT NULL, live TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
      published_revision INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS brand_assets (
      id TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('logo','cover')), image BLOB NOT NULL,
      icon BLOB, width INTEGER NOT NULL, height INTEGER NOT NULL,
      created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS brand_assets_shop ON brand_assets(shop_id);
    CREATE TABLE IF NOT EXISTS booking_slugs (
      slug TEXT PRIMARY KEY, shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      created_at INTEGER NOT NULL
    );
    INSERT OR IGNORE INTO booking_slugs SELECT slug,id,created_at FROM shops;
    CREATE TABLE IF NOT EXISTS shop_domains (
      shop_id TEXT PRIMARY KEY REFERENCES shops(id) ON DELETE CASCADE,
      hostname TEXT NOT NULL UNIQUE, token TEXT NOT NULL, verified_at INTEGER,
      active INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
    );
    INSERT OR IGNORE INTO schema_versions VALUES(3,strftime('%s','now'));
    ''')


def default(): return BrandSettings().model_dump()


def row_state(c, shop_id):
    row=c.execute('SELECT * FROM shop_branding WHERE shop_id=?',(shop_id,)).fetchone()
    if row: return dict(row)
    base=packed(default())
    return {'shop_id':shop_id,'draft':base,'live':base,'revision':0,'published_revision':0,'updated_at':0}


def ensure_state(c, shop_id):
    c.execute('INSERT OR IGNORE INTO shop_branding VALUES(?,?,?,?,?,?)', (shop_id,packed(default()),packed(default()),0,0,now()))
    return row_state(c,shop_id)


def clean_old_assets(c, shop_id):
    row=row_state(c,shop_id)
    used=set()
    for side in ('draft','live'):
        b=json.loads(row[side]);used.update(b[k] for k in ('logo_id','cover_id') if b[k])
    if used:
        c.execute('DELETE FROM brand_assets WHERE shop_id=? AND id NOT IN ('+','.join('?' for _ in used)+')',(shop_id,*used))
    else:
        c.execute('DELETE FROM brand_assets WHERE shop_id=?',(shop_id,))


def validate_assets(c,shop_id,b):
    for kind in ('logo','cover'):
        identifier=b[kind+'_id']
        if identifier and not c.execute('SELECT 1 FROM brand_assets WHERE id=? AND shop_id=? AND kind=?',(identifier,shop_id,kind)).fetchone():
            raise HTTPException(422,'Upload this business\'s own '+kind+' before saving.')


def check_revision(row, revision):
    if row['revision']!=revision:
        raise HTTPException(409,'Branding changed in another tab. Reload this page before saving again.')


def save_draft(c,shop_id,revision,b):
    row=ensure_state(c,shop_id);check_revision(row,revision);validate_assets(c,shop_id,b)
    c.execute('UPDATE shop_branding SET draft=?,revision=revision+1,updated_at=? WHERE shop_id=?',(packed(b),now(),shop_id))
    clean_old_assets(c,shop_id)


def asset_url(shop,kind,b,private=False,icon=False):
    identifier=b.get(kind+'_id')
    if not identifier: return ''
    base='/api/branding/assets/'+identifier if private else '/api/public/brand-assets/'+shop['slug']+'/'+identifier
    return base+('?icon=true' if icon else '')


def decorate(shop,b,private=False):
    return {**b,'logo_url':asset_url(shop,'logo',b,private),'cover_url':asset_url(shop,'cover',b,private),
            'favicon_url':asset_url(shop,'logo',b,private,True)}


def live_brand(shop,c=None):
    if c is not None: row=row_state(c,shop['id'])
    else:
        with db() as connection: row=row_state(connection,shop['id'])
    b=json.loads(row['live'])
    from .plans import features
    allowed=features(shop)
    if not allowed['hide_attribution']: b['hide_powered_by']=False
    if not allowed['brand_colors']:
        defaults=default()
        for key in ('primary_color','button_color','cover_id'): b[key]=defaults[key]
    return decorate(shop,b)


def domain_state(shop_id,c=None):
    if c is not None: row=c.execute('SELECT * FROM shop_domains WHERE shop_id=?',(shop_id,)).fetchone()
    else:
        with db() as connection: row=connection.execute('SELECT * FROM shop_domains WHERE shop_id=?',(shop_id,)).fetchone()
    if not row:return None
    return dict(row)


def public_origin(shop):
    d=domain_state(shop['id'])
    from .plans import features
    if d and d['active'] and d['verified_at'] and features(shop)['custom_domain']: return 'https://'+d['hostname']
    return config.BASE_URL


def booking_url(shop):
    origin=public_origin(shop)
    return origin if origin!=config.BASE_URL else config.BASE_URL+'/book/'+shop['slug']


def links(shop):
    return {'booking_url':booking_url(shop),'canonical_booking_url':config.BASE_URL+'/book/'+shop['slug']}


def full_state(shop,c=None):
    if c is None:
        with db() as connection:return full_state(shop,connection)
    from .plans import features
    row=row_state(c,shop['id']);d=domain_state(shop['id'],c)
    if d:
        d={'hostname':d['hostname'],'status':'active' if d['active'] else 'verified' if d['verified_at'] else 'pending',
           'txt_name':'_kontracts.'+d['hostname'],'txt_value':'kontracts-verification='+d['token'],
           'verified_at':d['verified_at'],'created_at':d['created_at']}
    aliases=[r[0] for r in c.execute('SELECT slug FROM booking_slugs WHERE shop_id=? ORDER BY created_at',(shop['id'],))]
    if shop['slug'] not in aliases:aliases.insert(0,shop['slug'])
    return {'draft':decorate(shop,json.loads(row['draft']),True),'live':live_brand(shop,c),
            'revision':row['revision'],'published_revision':row['published_revision'],'updated_at':row['updated_at'],
            'has_unpublished_changes':row['draft']!=row['live'],'domain':d,'aliases':aliases,
            'features':features(shop),'slug':shop['slug'],'page_published':bool(shop['published']),**links(shop)}


def encode_artwork(raw,kind):
    if kind not in ASSET_LIMITS: raise HTTPException(404,'Choose logo or cover.')
    if not raw or len(raw)>8*1024*1024:raise HTTPException(413,'Choose an image under 8 MB.')
    edge,cap=ASSET_LIMITS[kind]
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('PNG','JPEG','WEBP') or getattr(source,'n_frames',1)!=1 or source.width*source.height>8_000_000:
                raise ValueError('Unsupported image')
            source.load(); oriented=ImageOps.exif_transpose(source).convert('RGBA' if kind=='logo' else 'RGB')
            oriented.thumbnail((edge,edge),Image.Resampling.LANCZOS)
            clean=Image.frombytes(oriented.mode,oriented.size,oriented.tobytes())
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
        raise HTTPException(422,'Use a still JPEG, PNG or WebP under 8 megapixels. SVG and animated images are not accepted.') from exc
    data=b''
    for quality in (86,75,64,54):
        stream=io.BytesIO();clean.save(stream,'WEBP',quality=quality,method=5);data=stream.getvalue()
        if len(data)<=cap:break
    if len(data)>cap:raise HTTPException(422,'Crop or simplify this image and try again.')
    icon=None
    if kind=='logo':
        small=clean.copy();small.thumbnail((64,64),Image.Resampling.LANCZOS)
        square=Image.new('RGBA',(64,64),(255,255,255,0));square.alpha_composite(small,((64-small.width)//2,(64-small.height)//2))
        stream=io.BytesIO();square.save(stream,'PNG',optimize=True);icon=stream.getvalue()
    return data,icon,clean.width,clean.height


def contrast_ink(color):
    parts=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in parts]
    luminance=sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
    return '#000000' if luminance>.179 else '#ffffff'


def email_presentation(message):
    """Branded notification HTML; platform account/billing mail stays unbranded."""
    from .mail import channel_for, identity
    if not message['shop_id'] or channel_for(message) != 'notifications':return None
    with db() as c:
        shop=c.execute('SELECT * FROM shops WHERE id=?',(message['shop_id'],)).fetchone()
        if not shop:return None
        b=live_brand(shop,c)
    s=json.loads(shop['settings']);esc=lambda x:html.escape(str(x),quote=True)
    routing=identity(message,shop)
    from_value=routing['from']
    reply_to=routing['reply_to']
    logo=(config.BASE_URL+'/api/public/brand-logo/'+shop['slug']) if b['logo_url'] else ''
    # Only our own canonical/customer origins become hyperlinks, not customer-supplied URLs.
    body=esc(message['body'])
    for origin in dict.fromkeys((config.BASE_URL,public_origin(shop))):
        pattern=re.escape(esc(origin))+r'/[a-zA-Z0-9_/?=&.%~:+#-]+'
        body=re.sub(pattern,lambda m:'<a href="'+m.group(0)+'" style="color:'+b['primary_color']+';text-decoration:underline">'+m.group(0)+'</a>',body,count=20)
    footer='' if b['hide_powered_by'] else '<p style="font-size:12px;color:#647067">Powered by Kontracts</p>'
    artwork='<img src="'+esc(logo)+'" alt="'+esc(shop['name'])+'" width="112" style="max-height:72px;object-fit:contain;margin-bottom:18px">' if logo else ''
    markup='''<!doctype html><html><body style="margin:0;background:#f5f6f4;font:15px/1.65 Arial,sans-serif;color:#202b25"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:28px 12px"><table role="presentation" width="100%" style="max-width:560px;background:#ffffff;border:1px solid #e6eae4;border-radius:12px" cellpadding="0" cellspacing="0"><tr><td style="padding:32px;border-top:5px solid '''+b['primary_color']+'">'+artwork+'<h1 style="font-size:22px;margin:0 0 20px">'+esc(shop['name'])+'</h1><div style="white-space:pre-wrap;overflow-wrap:anywhere">'+body+'</div><p style="margin-top:28px;font-size:13px">Questions? <a href="mailto:'+esc(reply_to)+'">'+esc(reply_to)+'</a></p>'+footer+'</td></tr></table></td></tr></table></body></html>'
    return {'from':from_value,'reply_to':reply_to,'html':markup}
