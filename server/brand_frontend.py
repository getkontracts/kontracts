"""Initial HTML metadata for branded pages, with no executable tenant content."""
import html
import json
from fastapi.responses import HTMLResponse
from . import config,core,branding
from .db import db
from .security import digest


def response(path,tenant=None):
    shop=None
    with db() as c:
        if tenant and path=='':shop=c.execute('SELECT * FROM shops WHERE id=?',(tenant['shop_id'],)).fetchone()
        elif path.startswith('book/'):
            slug=path.split('/')[1]
            try:shop=core.shop_by_slug(c,slug,False)
            except Exception:shop=None
        elif path.startswith(('manage/','quote/','invoice/')):
            kind,raw=path.split('/',1)
            if len(raw)<=128:
                table,column=('bookings','manage_hash') if kind=='manage' else ('invoices','token_hash') if kind=='invoice' else ('quotes','token_hash')
                shop=c.execute('SELECT s.* FROM shops s JOIN '+table+' b ON s.id=b.shop_id WHERE b.'+column+'=?',(digest(raw),)).fetchone()
    text=(config.ROOT/'web/index.html').read_text()
    if shop:
        b=branding.live_brand(shop);settings=core.settings(shop);esc=lambda x:html.escape(str(x),quote=True)
        title=shop['name']+(' | Service invoice' if path.startswith('invoice/') else ' | Book a detail')
        description=b['introduction'] or settings['tagline']
        text=text.replace('<title>Kontracts - Your work, beautifully organized.</title>','<title>'+esc(title)+'</title>')
        text=text.replace('content="Kontracts: booking approval, signed invoices and simple payments for solo mobile detailers."','content="'+esc(description)+'"')
        text=text.replace('content="#103c30"','content="'+b['primary_color']+'"')
        favicon=b['favicon_url'] or '/assets/booking-icon.svg'
        text=text.replace('<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">','<link rel="icon" href="'+esc(favicon)+'" type="'+('image/png' if b['favicon_url'] else 'image/svg+xml')+'">')
        # Shared-link metadata does not contain a customer name or private token.
        extra='<meta name="robots" content="noindex,nofollow"><meta property="og:title" content="'+esc(title)+'"><meta property="og:description" content="'+esc(description)+'">'
        if b['cover_url']:extra+='<meta property="og:image" content="'+esc(config.BASE_URL+b['cover_url'])+'">'
        if path.startswith('book/') or (tenant and path==''):
            extra+='<link rel="canonical" href="'+esc(branding.booking_url(shop))+'">'
        text=text.replace('</head>',extra+'</head>')
        text=text.replace('<span class="brand-mark">k<span>.</span></span>','<span class="business-loading">'+esc(shop['name'])+'</span>')
    elif path=='brand-preview':
        text=text.replace('</head>','<meta name="robots" content="noindex,nofollow"></head>')
    return HTMLResponse(text,headers={'Cache-Control':'no-store' if path.startswith(('manage/','quote/','invoice/','brand-preview')) else 'no-cache'})
