"""Single-process server entry point for Docker and Railway.

PORT is read at runtime. Optional volume repair is narrowly limited to the
container's documented data directories and drops root before serving traffic.
"""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare_volume() -> None:
    if os.getenv('REPAIR_VOLUME_PERMISSIONS', 'false').lower() != 'true':
        return
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise RuntimeError('Volume repair requires container UID 0 for initialization.')
    raw = Path(os.getenv('DATA_DIR', '/app/data'))
    if raw.is_symlink() or str(raw) not in ('/data', '/app/data') or raw.resolve() != raw:
        raise RuntimeError('Volume repair is restricted to /data or /app/data.')
    raw.mkdir(mode=0o750, parents=True, exist_ok=True)
    paths = [raw]
    for base, directories, files in os.walk(raw, followlinks=False):
        paths.extend(Path(base) / name for name in directories + files)
    for path in paths:
        if path.is_symlink():
            continue
        info = path.stat()
        if (info.st_uid, info.st_gid) != (10001, 10001):
            os.chown(path, 10001, 10001)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    if os.geteuid() == 0:
        raise RuntimeError('Refusing to serve after unsuccessful privilege drop.')


def main() -> None:
    port = int(os.getenv('PORT', '8000'))
    if not 1 <= port <= 65535:
        raise RuntimeError('PORT must be between 1 and 65535.')
    prepare_volume()
    import uvicorn
    uvicorn.run(
        'server.app:app', host=os.getenv('HOST', '0.0.0.0'), port=port,
        workers=1, proxy_headers=True,
        forwarded_allow_ips=os.getenv('TRUSTED_PROXIES', '127.0.0.1'),
        access_log=False,
    )


if __name__ == '__main__':
    main()
