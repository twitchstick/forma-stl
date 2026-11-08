"""Database schema versions, tracked with SQLite's user_version.

Each module still creates its own tables with CREATE TABLE IF NOT EXISTS at
startup. Changes to existing tables (new columns, data rewrites, renames) go in
MIGRATIONS as numbered steps, so every install runs each step exactly once and
in order. Before an existing database is upgraded, a copy is saved in
/data/backups, because the container can update itself unattended.

To change the schema: append (next_number, 'what it does', function) to
MIGRATIONS. Never edit or reorder a step that has already been released.
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

KEEP_BACKUPS = 5


def add_columns(c, table, columns):
    """Add any missing columns. columns maps name -> SQL declaration."""
    existing = {r[1] for r in c.execute('PRAGMA table_info(' + table + ')')}
    for name, declaration in columns.items():
        if name not in existing:
            c.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + declaration)


def _baseline(c):
    # Version 1 is the schema that the modules create at startup as of this release.
    pass


MIGRATIONS = [
    (1, 'Baseline schema', _baseline),
]
LATEST = MIGRATIONS[-1][0]


def version(db_path):
    with closing(sqlite3.connect(db_path)) as c:
        return c.execute('PRAGMA user_version').fetchone()[0]


def backup_before_upgrade(db_path, log=print):
    """Copy an existing database before this release changes it. Returns the copy's path or None."""
    db_path = Path(db_path)
    if not db_path.exists() or version(db_path) >= LATEST:
        return None
    folder = db_path.parent / 'backups'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = folder / f'forma-v{version(db_path)}-{stamp}.sqlite3'
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    for old in sorted(folder.glob('forma-v*.sqlite3'), key=lambda p: p.stat().st_mtime_ns)[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    log(f'Saved a copy of the database before upgrading: {target}')
    return target


def migrate(db):
    """Run pending numbered migrations, each in its own transaction."""
    with db() as c:
        current = c.execute('PRAGMA user_version').fetchone()[0]
    for number, _description, step in MIGRATIONS:
        if number <= current:
            continue
        with db() as c:
            # Python's sqlite3 does not open a transaction for DDL such as ALTER TABLE,
            # so start one explicitly: a failing step then rolls back completely.
            c.execute('BEGIN')
            step(c)
            c.execute(f'PRAGMA user_version={int(number)}')
    return max(current, LATEST)
