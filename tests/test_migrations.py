import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import migrations
from app import create_app


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.env = patch.dict(os.environ, {'FORMA_PASSWORD': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def start(self):
        return create_app(self.data, self.root)

    def user_version(self):
        with closing(sqlite3.connect(self.data / 'forma.sqlite3')) as c:
            return c.execute('PRAGMA user_version').fetchone()[0]

    def backups(self):
        return sorted((self.data / 'backups').glob('*.sqlite3')) if (self.data / 'backups').exists() else []

    def test_fresh_install_is_current_without_backup(self):
        self.start()
        self.assertEqual(self.user_version(), migrations.LATEST)
        self.assertEqual(self.backups(), [])

    def test_upgrading_an_existing_database_saves_a_copy_once(self):
        client = self.start().test_client()
        client.post('/api/models', json={'title': 'Keep me'}, headers={'X-Forma-Request': '1'})
        with closing(sqlite3.connect(self.data / 'forma.sqlite3')) as c:
            c.execute('PRAGMA user_version=0')  # a database from before version tracking
            c.commit()
        client = self.start().test_client()
        self.assertEqual(self.user_version(), migrations.LATEST)
        self.assertEqual([m['title'] for m in client.get('/api/models').json], ['Keep me'])
        [copy] = self.backups()
        with closing(sqlite3.connect(copy)) as c:
            self.assertEqual(c.execute('SELECT title FROM models').fetchall(), [('Keep me',)])
        self.start()
        self.assertEqual(len(self.backups()), 1)

    def test_new_steps_run_once_in_order(self):
        self.start()
        ran = []
        steps = migrations.MIGRATIONS + [
            (migrations.LATEST + 1, 'first', lambda c: ran.append('first')),
            (migrations.LATEST + 2, 'second', lambda c: ran.append('second')),
        ]
        with patch.object(migrations, 'MIGRATIONS', steps), patch.object(migrations, 'LATEST', steps[-1][0]):
            self.start()
            self.start()
        self.assertEqual(ran, ['first', 'second'])
        self.assertEqual(self.user_version(), steps[-1][0])

    def test_failed_step_leaves_version_unchanged(self):
        self.start()

        def broken(c):
            c.execute('ALTER TABLE models ADD COLUMN half_done TEXT')
            raise RuntimeError('boom')

        steps = migrations.MIGRATIONS + [(migrations.LATEST + 1, 'broken', broken)]
        with patch.object(migrations, 'MIGRATIONS', steps), patch.object(migrations, 'LATEST', steps[-1][0]):
            with self.assertRaises(RuntimeError):
                self.start()
        self.assertEqual(self.user_version(), migrations.LATEST)
        with closing(sqlite3.connect(self.data / 'forma.sqlite3')) as c:
            self.assertNotIn('half_done', {r[1] for r in c.execute('PRAGMA table_info(models)')})

    def test_old_backups_are_pruned(self):
        db = self.data / 'forma.sqlite3'
        self.data.mkdir()
        sqlite3.connect(db).close()
        for _ in range(migrations.KEEP_BACKUPS + 2):
            migrations.backup_before_upgrade(db, log=lambda m: None)
        self.assertEqual(len(self.backups()), migrations.KEEP_BACKUPS)


if __name__ == '__main__':
    unittest.main()
