"""Authenticated branding editor, per-business links and domain registration."""
import asyncio
import hashlib
import json
import shutil
import sqlite3
from fastapi import Depends,File,Form,HTTPException,Request,Response,UploadFile
from . import branding,brand_domains,core,config,media,plans
from .db import db,packed,now,uid,audit
from .security import token,rate_limit


def install(app,owner,ip):
    @app.get('/api/branding')
    def state(shop=Depends(owner)):
        return branding.full_state(shop)

    @app.put('/api/branding')
    def save(body:branding.SaveBrand,shop=Depends(owner)):
        with db(True) as c:
            branding.save_draft(c,shop['id'],body.revision,body.branding.model_dump())
            audit(c,shop['id'],'branding.draft.saved')
        return branding.full_state(shop)

    @app.post('/api/branding/publish')
    def publish(body:branding.BrandRevision,shop=Depends(owner)):
        with db(True) as c:
            row=branding.ensure_state(c,shop['id']);branding.check_revision(row,body.revision)
            c.execute('UPDATE shop_branding SET live=draft,revision=revision+1,published_revision=revision+1,updated_at=? WHERE shop_id=?',(now(),shop['id']))
            branding.clean_old_assets(c,shop['id']);audit(c,shop['id'],'branding.published')
        return {**branding.full_state(shop),'message':('Branding published.' if plans.features(shop)['brand_colors'] else 'Branding published with Starter appearance. Premium draft colors, covers and attribution settings need Pro to appear to customers.')+(' Your booking page is still unpublished; complete the publishing checklist before sharing.' if not shop['published'] else '')}

    @app.post('/api/branding/reset')
    def reset(body:branding.BrandRevision,shop=Depends(owner)):
        with db(True) as c:
            row=branding.ensure_state(c,shop['id']);branding.check_revision(row,body.revision)
            c.execute('UPDATE shop_branding SET draft=live,revision=revision+1,updated_at=? WHERE shop_id=?',(now(),shop['id']))
            branding.clean_old_assets(c,shop['id']);audit(c,shop['id'],'branding.draft.discarded')
        return branding.full_state(shop)

    @app.post('/api/branding/assets/{kind}')
    async def upload(kind:str,revision:int=Form(...),file:UploadFile=File(...),shop=Depends(owner)):
        if kind not in branding.ASSET_LIMITS:raise HTTPException(404,'Choose logo or cover.')
        rate_limit('brand-upload:'+shop['id'],30,3600)
        async with media.PHOTO_PROCESSING:
            try:raw=await file.read(8*1024*1024+1)
            finally:await file.close()
            image,icon,width,height=await asyncio.to_thread(branding.encode_artwork,raw,kind)
        if shutil.disk_usage(config.DATA).free-len(image)-(len(icon) if icon else 0)<config.PHOTO_MIN_FREE:
            raise HTTPException(507,'Not enough safe storage space. Please contact support.')
        with db(True) as c:
            row=branding.ensure_state(c,shop['id']);branding.check_revision(row,revision)
            identifier=uid()
            c.execute('INSERT INTO brand_assets VALUES(?,?,?,?,?,?,?,?)',(identifier,shop['id'],kind,image,icon,width,height,now()))
            b=json.loads(row['draft']);b[kind+'_id']=identifier
            branding.save_draft(c,shop['id'],revision,b);audit(c,shop['id'],'branding.'+kind+'.uploaded')
        return {**branding.full_state(shop),'upload':{'kind':kind,'bytes':len(image),'width':width,'height':height}}

    @app.delete('/api/branding/assets/{kind}')
    def remove(kind:str,body:branding.BrandRevision,shop=Depends(owner)):
        if kind not in branding.ASSET_LIMITS:raise HTTPException(404,'Choose logo or cover.')
        with db(True) as c:
            row=branding.ensure_state(c,shop['id']);b=json.loads(row['draft']);b[kind+'_id']=''
            branding.save_draft(c,shop['id'],body.revision,b);audit(c,shop['id'],'branding.'+kind+'.removed')
        return branding.full_state(shop)

    def image_response(row,icon=False,private=True):
        content=row['icon'] if icon else row['image']
        if not content:raise HTTPException(404,'Image not available.')
        return Response(bytes(content),media_type='image/png' if icon else 'image/webp',headers={
            'Cache-Control':'private, no-store' if private else 'public, max-age=3600',
            'ETag':'"'+hashlib.sha256(content).hexdigest()+'"',
            'Cross-Origin-Resource-Policy':'cross-origin' if not private else 'same-origin'})

    @app.get('/api/branding/assets/{identifier}')
    def private_asset(identifier:str,icon:bool=False,shop=Depends(owner)):
        with db() as c:row=c.execute('SELECT * FROM brand_assets WHERE id=? AND shop_id=?',(identifier,shop['id'])).fetchone()
        if not row:raise HTTPException(404,'Image not available.')
        return image_response(row,icon,True)

    @app.get('/api/public/brand-assets/{slug}/{identifier}')
    def public_asset(slug:str,identifier:str,icon:bool=False):
        with db() as c:
            shop=core.shop_by_slug(c,slug,False);b=branding.live_brand(shop,c)
            if identifier not in (b['logo_id'],b['cover_id']):raise HTTPException(404,'Image not published.')
            row=c.execute('SELECT * FROM brand_assets WHERE id=? AND shop_id=?',(identifier,shop['id'])).fetchone()
        if not row:raise HTTPException(404,'Image not available.')
        return image_response(row,icon,False)

    @app.get('/api/public/brand-logo/{slug}')
    def email_logo(slug:str):
        with db() as c:
            shop=core.shop_by_slug(c,slug,False);b=branding.live_brand(shop,c)
            row=c.execute('SELECT * FROM brand_assets WHERE id=? AND shop_id=?',(b['logo_id'],shop['id'])).fetchone()
        if not row:raise HTTPException(404,'No published business logo.')
        response=image_response(row,False,False);response.headers['Cache-Control']='public, max-age=300'
        return response

    @app.get('/api/branding/preview')
    def preview(shop=Depends(owner)):
        result=core.public_shop(shop);result['branding']=branding.full_state(shop)['draft']
        result['branding_preview']=True
        return result

    @app.get('/api/booking-page/availability')
    def availability(slug:str,shop=Depends(owner)):
        rate_limit('slug-check:'+shop['id'],100,60)
        try:slug=branding.SlugChange(slug=slug).slug
        except ValueError:return {'available':False,'reason':'Use 3-45 lowercase letters, numbers and single hyphens.'}
        if slug in branding.RESERVED_SLUGS:return {'available':False,'reason':'That name is reserved.'}
        with db() as c:
            row=c.execute('SELECT id AS shop_id FROM shops WHERE slug=? UNION SELECT shop_id FROM booking_slugs WHERE slug=?',(slug,slug)).fetchone()
        return {'available':not row or row['shop_id']==shop['id'],'reason':'This name is already in use.' if row and row['shop_id']!=shop['id'] else 'This link is available.'}

    @app.put('/api/booking-page/slug')
    def rename(body:branding.SlugChange,shop=Depends(owner)):
        if body.slug in branding.RESERVED_SLUGS:raise HTTPException(422,'Choose another booking-page name.')
        rate_limit('slug-change:'+shop['id'],6,3600)
        try:
            with db(True) as c:
                c.execute('INSERT OR IGNORE INTO booking_slugs VALUES(?,?,?)',(shop['slug'],shop['id'],now()))
                taken=c.execute('SELECT id AS shop_id FROM shops WHERE slug=? UNION SELECT shop_id FROM booking_slugs WHERE slug=?',(body.slug,body.slug)).fetchone()
                if taken and taken['shop_id']!=shop['id']:raise HTTPException(409,'That booking link is already in use.')
                count=c.execute('SELECT count(*) FROM booking_slugs WHERE shop_id=?',(shop['id'],)).fetchone()[0]
                if count>=6 and not taken:raise HTTPException(422,'This account has reached its six-name history limit. Contact support to change it safely.')
                c.execute('INSERT OR IGNORE INTO booking_slugs VALUES(?,?,?)',(body.slug,shop['id'],now()))
                c.execute('UPDATE shops SET slug=? WHERE id=?',(body.slug,shop['id']))
                audit(c,shop['id'],'booking.slug.updated')
                shop=c.execute('SELECT * FROM shops WHERE id=?',(shop['id'],)).fetchone()
        except sqlite3.IntegrityError as exc:raise HTTPException(409,'That booking link was just claimed. Choose another.') from exc
        return branding.full_state(shop)

    @app.post('/api/branding/domain')
    def add_domain(body:brand_domains.DomainInput,shop=Depends(owner)):
        plans.require(shop,'custom_domain')
        rate_limit('domain-add:'+shop['id'],10,3600)
        with db(True) as c:
            existing=c.execute('SELECT * FROM shop_domains WHERE shop_id=?',(shop['id'],)).fetchone()
            if existing and existing['hostname']==body.hostname:return branding.full_state(shop,c)
            if existing:raise HTTPException(409,'Disconnect the existing domain before adding another. Your original booking link will continue to work.')
            # Expire unverified reservations so a typo cannot reserve a domain indefinitely.
            c.execute('DELETE FROM shop_domains WHERE verified_at IS NULL AND created_at<?',(now()-2*86400,))
            try:c.execute('INSERT INTO shop_domains VALUES(?,?,?,?,?,?)',(shop['id'],body.hostname,token(),None,0,now()))
            except sqlite3.IntegrityError as exc:raise HTTPException(409,'This hostname is already registered. Contact support if you own it.') from exc
            audit(c,shop['id'],'domain.requested')
        return branding.full_state(shop)

    @app.post('/api/branding/domain/verify')
    def verify_domain(shop=Depends(owner)):
        plans.require(shop,'custom_domain')
        rate_limit('domain-verify:'+shop['id'],8,3600)
        d=branding.domain_state(shop['id'])
        if not d:raise HTTPException(404,'Add a domain first.')
        expected='kontracts-verification='+d['token']
        if expected not in brand_domains.txt_records(d['hostname']):
            raise HTTPException(422,'The ownership TXT record was not found yet. Check the exact name and value, then retry after DNS has updated.')
        with db(True) as c:
            # Do not activate a domain here. A trusted operator must provision and test TLS.
            changed=c.execute('UPDATE shop_domains SET verified_at=? WHERE shop_id=? AND hostname=? AND token=?',(now(),shop['id'],d['hostname'],d['token'])).rowcount
            if not changed:raise HTTPException(409,'The domain changed while verification was running. Reload and retry.')
            audit(c,shop['id'],'domain.verified')
        return branding.full_state(shop)

    @app.delete('/api/branding/domain')
    def delete_domain(shop=Depends(owner)):
        with db(True) as c:
            c.execute('DELETE FROM shop_domains WHERE shop_id=?',(shop['id'],));audit(c,shop['id'],'domain.disconnected')
        return branding.full_state(shop)

    @app.get('/.well-known/kontracts-domain')
    def domain_probe(request:Request):
        d=getattr(request.state,'tenant',None)
        if not d:raise HTTPException(404,'No customer domain on this hostname.')
        return {'hostname':d['hostname'],'proof':'kontracts-verification='+d['token'],'verified':bool(d['verified_at'])}

    @app.get('/api/branding/email-preview')
    def email_preview(shop=Depends(owner)):
        result=branding.email_presentation({'shop_id':shop['id'],'body':'Your booking is confirmed.\n\nExample service: The full detail\nTuesday at 9:00 AM (business local time)\n\nThis is a sample email, not a real appointment.\n\nBooking page: '+branding.booking_url(shop)})
        return {'html':result['html'],'from':result['from'],'reply_to':result['reply_to']}
