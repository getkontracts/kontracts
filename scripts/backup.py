"""Consistent database + photos snapshot. Stop the app first; keep APP_SECRET separately."""
import argparse,sys,sqlite3,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import config
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('destination',type=Path);p.add_argument('--app-is-stopped',action='store_true')
a=p.parse_args()
if not a.app_is_stopped:p.error('Stop the app, then pass --app-is-stopped to acknowledge.')
if a.destination.exists():p.error('Destination must not exist; old backups are never overwritten.')
if not Path(config.DB).is_file():p.error('Database does not exist.')
a.destination.mkdir(parents=True)
with sqlite3.connect(config.DB) as source,sqlite3.connect(a.destination/'kontracts.sqlite3') as target:
    source.backup(target)
    if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Backup validation failed.')
if (config.DATA/'photos').exists():shutil.copytree(config.DATA/'photos',a.destination/'photos')
(a.destination/'RESTORE-NOTE.txt').write_text('Contains personal data. Encrypt at rest. Restore only with the matching APP_SECRET and while the app is stopped.\n')
print('Backup verified:',a.destination)
