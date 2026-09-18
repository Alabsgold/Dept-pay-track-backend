"""
Download / back up the local SQLite database.

    python db_backup.py                 -> backend/backups/db-YYYYMMDD-HHMM.sqlite3
    python db_backup.py C:\\path\\to.db  -> that exact file

Uses Django's own backup-safe copy (WAL-friendly). Your demo/dev data in
db.sqlite3 is untouched — this only writes a copy.
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

import django
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.conf import settings  # noqa: E402


def main():
    engine = settings.DATABASES['default']['ENGINE']
    if not engine.endswith('sqlite3'):
        print(
            'This backup script copies the local SQLite file. '
            f"Current DB engine is {engine} (DATABASE_URL points at a server). "
            'For PostgreSQL use: pg_dump $DATABASE_URL > backup.sql'
        )
        sys.exit(1)

    source = Path(settings.DATABASES['default']['NAME'])
    if len(sys.argv) > 1:
        destination = Path(sys.argv[1])
    else:
        destination = (
            Path(__file__).parent / 'backups'
            / f"db-{datetime.now():%Y%m%d-%H%M}.sqlite3"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    print(f'Backed up {source} -> {destination}')


if __name__ == '__main__':
    main()