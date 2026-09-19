"""Private, bounded image processing and storage for Kontracts.

Only decoded JPEG/PNG/WebP pixels are retained. Metadata and original files are
not saved. File-system writes are paired with a database transaction and cleaned
up on failure. A process crash can leave an orphan; scripts/photo_audit.py reports it.
"""
from dataclasses import dataclass
from pathlib import Path
import asyncio
import hashlib
import io
import shutil
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from . import config
from .db import uid

Image.MAX_IMAGE_PIXELS = 8_000_000
PHOTO_PROCESSING = asyncio.Semaphore(1)
MAX_INPUT_BYTES = 16 * 1024 * 1024
PROFILES = {
    'compact': {'edge': 1280, 'target': 150 * 1024, 'cap': 192 * 1024},
    'detail': {'edge': 2048, 'target': 350 * 1024, 'cap': 512 * 1024},
}

@dataclass(frozen=True)
class EncodedPhoto:
    image: bytes
    thumbnail: bytes
    width: int
    height: int
    input_bytes: int
    digest: str
    profile: str

    @property
    def stored_bytes(self):
        return len(self.image) + len(self.thumbnail)


def _webp(image, quality):
    stream = io.BytesIO()
    image.save(stream, 'WEBP', quality=quality, method=5)
    return stream.getvalue()


def encode_photo(raw: bytes, profile='compact') -> EncodedPhoto:
    """Enforce content, dimension and byte bounds, not just MIME or extension."""
    if profile not in PROFILES:
        raise HTTPException(422, 'Choose compact or detail photo quality.')
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise HTTPException(413, 'Each uploaded image must be between 1 byte and 16 MB.')
    spec = PROFILES[profile]
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('format')
            if source.width * source.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError('dimensions')
            if getattr(source, 'n_frames', 1) != 1:
                raise ValueError('animation')
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in ('RGBA', 'LA') or 'transparency' in oriented.info:
                rgba = oriented.convert('RGBA')
                flat = Image.new('RGB', rgba.size, 'white')
                flat.paste(rgba, mask=rgba.getchannel('A'))
            else:
                flat = oriented.convert('RGB')
            flat.thumbnail((spec['edge'], spec['edge']), Image.Resampling.LANCZOS)
            # Copy only pixel values. EXIF/GPS, XMP, comments and ICC are discarded.
            clean = Image.frombytes('RGB', flat.size, flat.tobytes())
    except (ValueError, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, 'Use a still JPEG, PNG or WebP under 8 megapixels. '
                                  'Export HEIC/HEIF as JPEG when your browser cannot convert it.') from exc
    best = b''
    for _ in range(4):
        for quality in (78, 68, 58):
            best = _webp(clean, quality)
            if len(best) <= spec['target']:
                break
        if len(best) <= spec['target']:
            break
        if max(clean.size) <= 640:
            break
        edge = max(640, round(max(clean.size) * 0.82))
        clean.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    if len(best) > spec['cap']:
        raise HTTPException(422, 'This image is unusually complex. Crop it or choose a different photo.')
    thumb = clean.copy()
    thumb.thumbnail((320, 320), Image.Resampling.LANCZOS)
    return EncodedPhoto(best, _webp(thumb, 62), clean.width, clean.height,
                        len(raw), hashlib.sha256(raw).hexdigest(), profile)


def quote_usage(c, shop_id=None):
    if shop_id:
        rows = c.execute('SELECT p.size_bytes FROM photos p JOIN quotes q ON q.id=p.quote_id WHERE q.shop_id=?', (shop_id,))
    else:
        rows = c.execute('SELECT size_bytes FROM photos')
    return sum(r[0] for r in rows)


def storage_usage(c, shop_id):
    row = c.execute('SELECT count(*),coalesce(sum(size_bytes),0),coalesce(sum(original_bytes),0) '
                    'FROM job_photos WHERE shop_id=?', (shop_id,)).fetchone()
    legacy = quote_usage(c, shop_id)
    return {'count': row[0], 'job_bytes': row[1], 'quote_bytes': legacy,
            'used_bytes': row[1] + legacy, 'limit_bytes': config.PHOTO_WORKSPACE_LIMIT,
            'reported_original_bytes': row[2]}


def check_quota(c, shop_id, adding):
    used = storage_usage(c, shop_id)['used_bytes']
    total = c.execute('SELECT coalesce(sum(size_bytes),0) FROM job_photos').fetchone()[0] + quote_usage(c)
    if used + adding > config.PHOTO_WORKSPACE_LIMIT:
        raise HTTPException(413, 'Your workspace photo allowance is full. Remove unneeded photos or contact support.')
    if total + adding > config.PHOTO_GLOBAL_LIMIT:
        raise HTTPException(507, 'Photo storage is temporarily full. Contact support before adding more photos.')
    if shutil.disk_usage(config.DATA).free - adding < config.PHOTO_MIN_FREE:
        raise HTTPException(507, 'Not enough safe disk space to save these photos. No images were added.')


def write_photo(encoded, paths):
    directory = config.DATA / 'photos'
    directory.mkdir(parents=True, exist_ok=True)
    identifier = uid()
    full = identifier + '.webp'
    thumb = identifier + '.thumb.webp'
    for filename, content in ((full, encoded.image), (thumb, encoded.thumbnail)):
        path = directory / filename
        paths.append(path)
        with path.open('xb') as handle:
            handle.write(content)
        path.chmod(0o600)
    return identifier, full, thumb


def remove_paths(paths):
    for path in paths:
        Path(path).unlink(missing_ok=True)


def initialize_media(c):
    columns = {r[1] for r in c.execute('PRAGMA table_info(photos)')}
    for name, definition in [('size_bytes', 'INTEGER NOT NULL DEFAULT 0'),
                             ('thumb_path', 'TEXT')]:
        if name not in columns:
            c.execute(f'ALTER TABLE photos ADD COLUMN {name} {definition}')
    # Backfill existing quote-file sizes once so upgrades cannot evade quotas.
    for row in c.execute('SELECT id,path FROM photos WHERE size_bytes=0').fetchall():
        path = config.DATA / 'photos' / row['path']
        if path.is_file():
            c.execute('UPDATE photos SET size_bytes=? WHERE id=?', (path.stat().st_size, row['id']))
    c.executescript('''
    CREATE TABLE IF NOT EXISTS job_photos (
      id TEXT PRIMARY KEY,
      shop_id TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
      booking_id TEXT NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
      category TEXT NOT NULL CHECK(category IN ('before','after','condition')),
      caption TEXT NOT NULL DEFAULT '', path TEXT NOT NULL, thumb_path TEXT NOT NULL,
      size_bytes INTEGER NOT NULL, original_bytes INTEGER NOT NULL,
      width INTEGER NOT NULL, height INTEGER NOT NULL, profile TEXT NOT NULL,
      uploaded_by TEXT NOT NULL CHECK(uploaded_by IN ('owner','customer')),
      content_digest TEXT NOT NULL, idempotency_key TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      UNIQUE(shop_id,idempotency_key)
    );
    CREATE INDEX IF NOT EXISTS job_photos_shop ON job_photos(shop_id,created_at);
    CREATE INDEX IF NOT EXISTS job_photos_booking ON job_photos(booking_id);
    INSERT OR IGNORE INTO schema_versions VALUES(2,strftime('%s','now'));
    ''')
