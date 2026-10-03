"""Restore a trusted backup directory. This replaces the current database and photos."""
import argparse,sys,sqlite3,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import config
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('source',type=Path);p.add_argument('--app-is-stopped',action='store_true');p.add_argument('--replace-data',action='store_true')
a=p.parse_args()
if not(a.app_is_stopped and a.replace_data):p.error('Stop the app and acknowledge --app-is-stopped --replace-data.')
source=a.source/'kontracts.sqlite3'
if not source.is_file():source=a.source/'detaillane.sqlite3'
if not source.is_file():p.error('No database in the backup directory.')
with sqlite3.connect('file:'+str(source.resolve())+'?mode=ro',uri=True) as c:
    if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Invalid backup.')
for suffix in ('-wal','-shm'):Path(config.DB+suffix).unlink(missing_ok=True)
shutil.copy2(source,config.DB)
photos=config.DATA/'photos'
if photos.exists():shutil.rmtree(photos)
if (a.source/'photos').exists():shutil.copytree(a.source/'photos',photos)
print('Restored. Verify the matching APP_SECRET before restarting. Test a private booking link and a Square token decryption.')
