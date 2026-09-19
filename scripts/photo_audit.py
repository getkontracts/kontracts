"""Read-only comparison of active photo records with disk. No files are deleted.

Run during a maintenance pause for a stable view. Output includes internal file
identifiers, so keep the report private. Restore missing files from a known-good
backup before manually investigating orphans.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import config
from server.db import db


def main():
    referenced = set()
    with db() as c:
        for table in ('photos', 'job_photos'):
            for row in c.execute('SELECT path,thumb_path FROM ' + table):
                referenced.update(name for name in row if name)
    folder = config.DATA / 'photos'
    files = {p.name for p in folder.iterdir() if p.is_file()} if folder.exists() else set()
    print(json.dumps({'referenced_files': len(referenced), 'stored_files': len(files),
                      'missing': sorted(referenced - files), 'unreferenced': sorted(files - referenced),
                      'read_only': True}, indent=2))


if __name__ == '__main__':
    main()
