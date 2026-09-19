"""Tenant-scoped job photo API; customer links expose only their own uploads."""
import asyncio
import json
import re
import tempfile
import zipfile
from pathlib import Path
from starlette.background import BackgroundTask
from fastapi import Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from . import config, media
from .db import db, now, audit
from .security import rate_limit


class PhotoEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    category: Literal['before', 'after', 'condition']
    caption: str = Field(default='', max_length=240)


def public_item(row, customer_token=None):
    url = '/api/job-photos/' + row['id']
    if customer_token:
        url = '/api/public/manage/' + customer_token + '/photos/' + row['id']
    return {k: row[k] for k in ('id','booking_id','category','caption','size_bytes','original_bytes',
                               'width','height','profile','uploaded_by','created_at')} | {
        'url': url, 'thumbnail_url': url + '?thumbnail=true'}


def install(app, owner, owned_booking, managed, ip):
    processing = media.PHOTO_PROCESSING

    @app.get('/api/export/photos.zip')
    def export_photos(shop=Depends(owner)):
        rate_limit('photo-export:' + shop['id'], 6, 3600)
        with db() as c:
            jobs = [dict(r) for r in c.execute('SELECT * FROM job_photos WHERE shop_id=?', (shop['id'],))]
            quotes = [dict(r) for r in c.execute('SELECT p.* FROM photos p JOIN quotes q ON q.id=p.quote_id WHERE q.shop_id=?', (shop['id'],))]
        handle = tempfile.NamedTemporaryFile(prefix='kontracts-export-', suffix='.zip', delete=False)
        path = Path(handle.name); handle.close()
        try:
            manifest = {'job_photos': [], 'quote_photos': [], 'missing_files': [],
                        'note': 'Optimized images only. Original camera files are not retained.'}
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
                for kind, rows in [('job_photos', jobs), ('quote_photos', quotes)]:
                    for row in rows:
                        source = config.DATA / 'photos' / row['path']
                        folder = row.get('booking_id') or row['quote_id']
                        name = kind + '/' + folder + '/' + row['id'] + '.webp'
                        try:
                            archive.write(source, name)
                        except FileNotFoundError:
                            manifest['missing_files'].append(row['id']); continue
                        public = {k:v for k,v in row.items() if k not in ('path','thumb_path','content_digest','idempotency_key','shop_id')}
                        manifest[kind].append(public | {'archive_path': name})
                archive.writestr('manifest.json', json.dumps(manifest, indent=2))
            return FileResponse(path, media_type='application/zip', filename='kontracts-photos.zip',
                                headers={'Cache-Control':'private, no-store'},
                                background=BackgroundTask(path.unlink, missing_ok=True))
        except BaseException:
            path.unlink(missing_ok=True); raise

    @app.delete('/api/photos/{identifier}')
    def delete_quote_photo(identifier: str, shop=Depends(owner)):
        with db(True) as c:
            row=c.execute('SELECT p.* FROM photos p JOIN quotes q ON q.id=p.quote_id WHERE p.id=? AND q.shop_id=?', (identifier,shop['id'])).fetchone()
            if not row: raise HTTPException(404,'Photo not found.')
            c.execute('DELETE FROM photos WHERE id=?',(identifier,))
            audit(c,shop['id'],'quote.photo.deleted',identifier)
        media.remove_paths([config.DATA/'photos'/row['path']] + ([config.DATA/'photos'/row['thumb_path']] if row['thumb_path'] else []))
        return {'message':'Quote photo removed from active storage.'}

    @app.get('/api/media')
    def library(shop=Depends(owner)):
        with db() as c:
            rows = c.execute('''SELECT p.*,b.customer_name,b.vehicle_notes,b.snapshot,b.start_ts FROM job_photos p
                  JOIN bookings b ON b.id=p.booking_id WHERE p.shop_id=? ORDER BY p.created_at DESC LIMIT 500''', (shop['id'],)).fetchall()
            items = [public_item(r) | {'customer_name': r['customer_name'], 'vehicle_notes': r['vehicle_notes'],
                     'start_ts': r['start_ts']} for r in rows]
            return {'photos': items, 'storage': media.storage_usage(c, shop['id']), 'returned_limit': 500}

    @app.get('/api/bookings/{identifier}/photos')
    def booking_photos(identifier: str, shop=Depends(owner)):
        with db() as c:
            b = owned_booking(c, identifier, shop['id'])
            rows = c.execute('SELECT * FROM job_photos WHERE booking_id=? AND shop_id=? ORDER BY created_at', (identifier, shop['id'])).fetchall()
            return {'photos': [public_item(r) for r in rows], 'storage': media.storage_usage(c, shop['id']),
                    'customer_name': b['customer_name'], 'vehicle_notes': b['vehicle_notes'],
                    'booking_id': identifier, 'photo_limit': config.PHOTO_PER_BOOKING}

    async def process_upload(booking_id, shop_id, request, files, category, caption,
                             profile, original_sizes, upload_key, uploaded_by):
        if not 1 <= len(files) <= 3:
            raise HTTPException(422, 'Select one to three photos at a time.')
        if category not in ('before', 'after', 'condition') or len(caption) > 240:
            raise HTTPException(422, 'Choose a valid label and a caption up to 240 characters.')
        if uploaded_by == 'customer' and category != 'condition':
            raise HTTPException(403, 'Customer uploads must be labelled condition.')
        if not re.fullmatch(r'[a-zA-Z0-9-]{20,64}', upload_key):
            raise HTTPException(422, 'A valid upload key is required. Retry from the photo picker.')
        try:
            originals = json.loads(original_sizes)
            if not isinstance(originals, list) or len(originals) != len(files):
                raise ValueError()
            if any(not isinstance(x, int) or isinstance(x, bool) or not 0 <= x <= 30*1024*1024 for x in originals):
                raise ValueError()
        except (ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(422, 'Invalid photo size metadata.')
        rate_limit('photo-upload:' + str(shop_id), 80, 3600)
        encoded = []
        async with processing:
            for upload in files:
                try:
                    raw = await upload.read(media.MAX_INPUT_BYTES + 1)
                    encoded.append(await asyncio.to_thread(media.encode_photo, raw, profile))
                finally:
                    await upload.close()
        created_paths = []
        try:
            with db(True) as c:
                owned_booking(c, booking_id, shop_id)
                existing = [c.execute('SELECT * FROM job_photos WHERE shop_id=? AND idempotency_key=?',
                                      (shop_id, upload_key + ':' + str(i))).fetchone() for i in range(len(encoded))]
                if any(existing):
                    if not all(existing) or any(r['booking_id'] != booking_id or r['content_digest'] != p.digest
                                                or r['uploaded_by'] != uploaded_by for r, p in zip(existing, encoded) if r):
                        raise HTTPException(409, 'That upload key was used for different photos.')
                    return {'photos': [public_item(r) for r in existing], 'message': 'These photos were already saved.'}
                count = c.execute('SELECT count(*) FROM job_photos WHERE booking_id=?', (booking_id,)).fetchone()[0]
                if count + len(encoded) > config.PHOTO_PER_BOOKING:
                    raise HTTPException(413, 'This job already has the maximum of 24 photos.')
                if uploaded_by == 'customer':
                    count_customer = c.execute("SELECT count(*) FROM job_photos WHERE booking_id=? AND uploaded_by='customer'", (booking_id,)).fetchone()[0]
                    if count_customer + len(encoded) > config.PHOTO_PER_CUSTOMER_BOOKING:
                        raise HTTPException(413, 'You can add up to six condition photos to this booking.')
                media.check_quota(c, shop_id, sum(p.stored_bytes for p in encoded))
                ids = []
                for index, p in enumerate(encoded):
                    identifier, filename, thumbnail = media.write_photo(p, created_paths)
                    c.execute('''INSERT INTO job_photos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                              (identifier, shop_id, booking_id, category, caption.strip(), filename, thumbnail,
                               p.stored_bytes, max(p.input_bytes, originals[index]), p.width, p.height,
                               profile, uploaded_by, p.digest, upload_key + ':' + str(index), now()))
                    ids.append(identifier)
                audit(c, shop_id, 'photos.added.' + uploaded_by, booking_id)
                rows = [c.execute('SELECT * FROM job_photos WHERE id=?', (pid,)).fetchone() for pid in ids]
                result = {'photos': [public_item(r) for r in rows], 'message': 'Photos saved privately. Originals were not retained.'}
            return result
        except BaseException:
            media.remove_paths(created_paths)
            raise

    @app.post('/api/bookings/{identifier}/photos', status_code=201)
    async def upload_owner(identifier: str, request: Request,
                           files: list[UploadFile] = File(...), category: str = Form('before'),
                           caption: str = Form(''), profile: str = Form('compact'),
                           original_sizes: str = Form('[]'), upload_key: str = Form(...), shop=Depends(owner)):
        with db() as c:
            owned_booking(c, identifier, shop['id'])
        return await process_upload(identifier, shop['id'], request, files, category, caption,
                                    profile, original_sizes, upload_key, 'owner')

    def private_file(row, thumbnail):
        if not row:
            raise HTTPException(404, 'Photo not found.')
        filename = row['thumb_path'] if thumbnail else row['path']
        path = config.DATA / 'photos' / filename
        if not path.is_file():
            raise HTTPException(404, 'The photo file is missing. Contact support to restore it from backup.')
        return FileResponse(path, media_type='image/webp', headers={
            'Cache-Control': 'private, no-store', 'Content-Disposition': 'inline; filename="vehicle.webp"'})

    @app.get('/api/job-photos/{identifier}')
    def get_photo(identifier: str, thumbnail: bool = False, shop=Depends(owner)):
        with db() as c:
            row = c.execute('SELECT * FROM job_photos WHERE id=? AND shop_id=?', (identifier, shop['id'])).fetchone()
        return private_file(row, thumbnail)

    @app.patch('/api/job-photos/{identifier}')
    def edit_photo(identifier: str, body: PhotoEdit, shop=Depends(owner)):
        with db(True) as c:
            row = c.execute('SELECT * FROM job_photos WHERE id=? AND shop_id=?', (identifier, shop['id'])).fetchone()
            if not row:
                raise HTTPException(404, 'Photo not found.')
            c.execute('UPDATE job_photos SET category=?,caption=? WHERE id=?', (body.category, body.caption, identifier))
            audit(c, shop['id'], 'photo.updated', identifier)
        return {'message': 'Photo details updated.'}

    @app.delete('/api/job-photos/{identifier}')
    def delete_photo(identifier: str, shop=Depends(owner)):
        with db(True) as c:
            row = c.execute('SELECT * FROM job_photos WHERE id=? AND shop_id=?', (identifier, shop['id'])).fetchone()
            if not row:
                raise HTTPException(404, 'Photo not found.')
            c.execute('DELETE FROM job_photos WHERE id=?', (identifier,))
            audit(c, shop['id'], 'photo.deleted', identifier)
        media.remove_paths([config.DATA / 'photos' / row['path'], config.DATA / 'photos' / row['thumb_path']])
        return {'message': 'Photo removed from active storage. Existing backups follow their retention schedule.'}

    @app.get('/api/public/manage/{raw_token}/photos')
    def customer_photos(raw_token: str):
        with db() as c:
            b, shop = managed(c, raw_token)
            rows = c.execute("SELECT * FROM job_photos WHERE booking_id=? AND uploaded_by='customer' ORDER BY created_at", (b['id'],)).fetchall()
        return {'photos': [public_item(r, raw_token) for r in rows], 'photo_limit': config.PHOTO_PER_CUSTOMER_BOOKING,
                'can_upload': b['status'] == 'confirmed'}

    @app.get('/api/public/manage/{raw_token}/photos/{identifier}')
    def get_customer_photo(raw_token: str, identifier: str, thumbnail: bool = False):
        with db() as c:
            b, shop = managed(c, raw_token)
            row = c.execute("SELECT * FROM job_photos WHERE id=? AND booking_id=? AND uploaded_by='customer'", (identifier, b['id'])).fetchone()
        return private_file(row, thumbnail)

    @app.post('/api/public/manage/{raw_token}/photos', status_code=201)
    async def upload_customer(raw_token: str, request: Request,
                              files: list[UploadFile] = File(...), category: str = Form('condition'),
                              caption: str = Form(''), profile: str = Form('compact'),
                              original_sizes: str = Form('[]'), upload_key: str = Form(...)):
        rate_limit('customer-photo-ip:' + ip(request), 30, 3600)
        with db() as c:
            b, shop = managed(c, raw_token)
            if b['status'] != 'confirmed':
                raise HTTPException(409, 'Photos can be added after the booking is confirmed.')
        result = await process_upload(b['id'], shop['id'], request, files, category, caption,
                                      profile, original_sizes, upload_key, 'customer')
        for item in result['photos']:
            item['url'] = '/api/public/manage/' + raw_token + '/photos/' + item['id']
            item['thumbnail_url'] = item['url'] + '?thumbnail=true'
        return result
