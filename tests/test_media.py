"""Image optimization, privacy, isolation, quotas and retry guarantees."""
import io
import json
import uuid
from pathlib import Path
import pytest
from PIL import Image
from server import config, media
from server.db import db


def image_bytes(size=(2400,1800), metadata=False, fmt='JPEG'):
    image=Image.new('RGB',size,(96,128,162))
    out=io.BytesIO()
    kwargs={}
    if metadata:
        exif=Image.Exif();exif[274]=6;exif[270]='Private image comment';exif[315]='Test camera owner'
        kwargs['exif']=exif
    image.save(out,fmt,quality=95,**kwargs)
    return out.getvalue()


def booking(owner):
    return owner.get('/api/bookings').json()['bookings'][0]['id']


def upload(owner,bid=None,raw=None,key=None,category='before',extra=None):
    data={'category':category,'caption':'Driver side','profile':'compact','original_sizes':json.dumps([2_000_000]),'upload_key':key or str(uuid.uuid4())}
    if extra:data.update(extra)
    return owner.post('/api/bookings/'+(bid or booking(owner))+'/photos',data=data,files=[('files',('car.jpg',raw or image_bytes(),'image/jpeg'))])


def test_photo_upload_and_metadata_removed(owner):
    result=upload(owner,raw=image_bytes(metadata=True))
    assert result.status_code==201,result.text
    p=result.json()['photos'][0]
    assert p['height']>p['width'] # EXIF orientation applied before metadata stripping
    assert max(p['height'],p['width'])<=1280
    assert p['size_bytes']<210*1024
    response=owner.get(p['url']);assert response.status_code==200
    assert response.headers['cache-control']=='private, no-store'
    with Image.open(io.BytesIO(response.content)) as im:
        assert im.format=='WEBP'
        assert not im.getexif()
        assert 'exif' not in im.info
    with Image.open(io.BytesIO(owner.get(p['thumbnail_url']).content)) as im:
        assert max(im.size)<=320
    assert owner.get('/api/media').json()['storage']['count']==1


def test_wrong_context_rejected(client):
    assert client.get('/api/media').status_code==401


def test_photo_atomic_invalid_batch(owner):
    bid=booking(owner)
    response=owner.post('/api/bookings/'+bid+'/photos',data={'category':'before','profile':'compact','original_sizes':'[100,100]','upload_key':str(uuid.uuid4())},files=[('files',('one.jpg',image_bytes(),'image/jpeg')),('files',('two.jpg',b'not a photograph','image/jpeg'))])
    assert response.status_code==422
    assert owner.get('/api/media').json()['photos']==[]
    assert not list((config.DATA/'photos').glob('*'))


def test_photo_retry_is_idempotent(owner):
    bid=booking(owner);key=str(uuid.uuid4());raw=image_bytes()
    a=upload(owner,bid,raw,key);b=upload(owner,bid,raw,key)
    assert a.status_code==b.status_code==201
    assert a.json()['photos'][0]['id']==b.json()['photos'][0]['id']
    assert owner.get('/api/media').json()['storage']['count']==1


def test_reusing_key_for_other_image_fails(owner):
    bid=booking(owner);key=str(uuid.uuid4())
    assert upload(owner,bid,image_bytes(),key).status_code==201
    assert upload(owner,bid,image_bytes((600,600)),key).status_code==409


def test_owner_category_edit_and_delete(owner):
    p=upload(owner).json()['photos'][0]
    assert owner.patch(p['url'],json={'category':'after','caption':'Finished interior'}).status_code==200
    assert owner.get('/api/media').json()['photos'][0]['category']=='after'
    assert owner.delete(p['url']).status_code==200
    assert owner.get(p['url']).status_code==404
    assert owner.get('/api/media').json()['storage']['used_bytes']==0
    assert not list((config.DATA/'photos').glob('*'))


def test_other_workspace_cannot_read_change_or_upload(owner):
    bid=booking(owner);p=upload(owner,bid).json()['photos'][0]
    r=owner.post('/api/auth/register',json={'name':'Other Owner','email':'second@example.com','password':'Correct-horse-battery-92','business':'Second Detail','slug':'second-detail','timezone':'America/New_York','accepted_terms':True})
    assert r.status_code==201,r.text
    owner.headers['X-CSRF-Token']=r.json()['csrf']
    assert owner.get(p['url']).status_code==404
    assert owner.delete(p['url']).status_code==404
    assert owner.patch(p['url'],json={'category':'after','caption':'x'}).status_code==404
    assert upload(owner,bid).status_code==404
    assert owner.get('/api/media').json()['photos']==[]


def test_workspace_storage_limit(owner,monkeypatch):
    monkeypatch.setattr(config,'PHOTO_WORKSPACE_LIMIT',1)
    assert upload(owner).status_code==413
    assert owner.get('/api/media').json()['storage']['count']==0


def test_global_storage_limit(owner,monkeypatch):
    monkeypatch.setattr(config,'PHOTO_GLOBAL_LIMIT',1)
    assert upload(owner).status_code==507


def test_low_disk_space_rejected(owner,monkeypatch):
    monkeypatch.setattr(config,'PHOTO_MIN_FREE',10**20)
    assert upload(owner).status_code==507


def test_customer_can_only_see_their_own_uploads(owner):
    rows=owner.get('/api/bookings').json()['bookings'];bid=next(b['id'] for b in rows if b['status']=='confirmed')
    token=owner.get('/api/bookings/'+bid+'/link').json()['url'].split('/')[-1]
    p=upload(owner,bid).json()['photos'][0]
    root='/api/public/manage/'+token+'/photos'
    assert owner.get(root).json()['photos']==[]
    assert owner.get(root+'/'+p['id']).status_code==404
    response=owner.post(root,data={'category':'condition','profile':'compact','original_sizes':'[100000]','upload_key':str(uuid.uuid4())},files=[('files',('condition.jpg',image_bytes(),'image/jpeg'))])
    assert response.status_code==201,response.text
    new=response.json()['photos'][0]
    assert owner.get(new['url']).status_code==200
    assert len(owner.get(root).json()['photos'])==1
    assert len(owner.get('/api/bookings/'+bid+'/photos').json()['photos'])==2


def test_customer_cannot_claim_owner_label(owner):
    bid=next(b['id'] for b in owner.get('/api/bookings').json()['bookings'] if b['status']=='confirmed')
    token=owner.get('/api/bookings/'+bid+'/link').json()['url'].split('/')[-1]
    r=owner.post('/api/public/manage/'+token+'/photos',data={'category':'after','profile':'compact','original_sizes':'[100]','upload_key':str(uuid.uuid4())},files=[('files',('c.jpg',image_bytes(),'image/jpeg'))])
    assert r.status_code==403


def test_closed_booking_disallows_customer_upload(owner):
    bid=next(b['id'] for b in owner.get('/api/bookings').json()['bookings'] if b['status']=='completed')
    token=owner.get('/api/bookings/'+bid+'/link').json()['url'].split('/')[-1]
    r=owner.post('/api/public/manage/'+token+'/photos',data={'original_sizes':'[100]','upload_key':str(uuid.uuid4())},files=[('files',('c.jpg',image_bytes(),'image/jpeg'))])
    assert r.status_code==409


def test_maximum_photos_per_booking(owner,monkeypatch):
    monkeypatch.setattr(config,'PHOTO_PER_BOOKING',1);bid=booking(owner)
    assert upload(owner,bid).status_code==201
    assert upload(owner,bid).status_code==413


@pytest.mark.parametrize('data',[{'original_sizes':'oops'},{'original_sizes':'[-1]'}, {'profile':'uncompressed'}, {'upload_key':'short'}, {'category':'public'}, {'caption':'x'*241}])
def test_invalid_photo_fields(owner,data):
    assert upload(owner,extra=data).status_code==422


def test_forged_svg_is_not_a_photo(owner):
    r=upload(owner,raw=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    assert r.status_code==422


def test_image_profile_and_animation():
    raw=image_bytes((2800,1800));p=media.encode_photo(raw,'detail')
    assert max(p.width,p.height)==2048
    assert len(p.image)<=512*1024
    out=io.BytesIO();frames=[Image.new('RGB',(100,100),color) for color in ['red','blue']]
    frames[0].save(out,'WEBP',save_all=True,append_images=frames[1:],duration=100,loop=0)
    with pytest.raises(Exception) as exc:media.encode_photo(out.getvalue())
    assert exc.value.status_code==422


def test_no_upscaling():
    p=media.encode_photo(image_bytes((100,60)))
    assert (p.width,p.height)==(100,60)


def test_quote_and_job_storage_shared(owner):
    # Existing quote upload tests also run; both categories share one byte budget.
    upload(owner)
    with db() as c:
        sid=c.execute("SELECT id FROM shops WHERE slug='northline'").fetchone()[0]
        assert media.storage_usage(c,sid)['job_bytes']>0
        assert media.storage_usage(c,sid)['used_bytes']==media.storage_usage(c,sid)['job_bytes']


def test_photo_export_is_tenant_scoped(owner):
    import zipfile
    p=upload(owner).json()['photos'][0]
    response=owner.get('/api/export/photos.zip')
    assert response.status_code==200
    assert response.headers['content-type']=='application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        assert len(manifest['job_photos'])==1
        assert manifest['missing_files']==[]
        assert manifest['job_photos'][0]['id']==p['id']
        assert 'path' not in manifest['job_photos'][0]
        assert archive.read(manifest['job_photos'][0]['archive_path']).startswith(b'RIFF')


def test_large_chunked_request_is_rejected(owner):
    # The request has no Content-Length, so this exercises the ASGI byte counter.
    response=owner.post('/api/public/shops/northline/price',content=iter([b'x'*(27*1024*1024)]),headers={'Content-Type':'application/json'})
    assert response.status_code==413
