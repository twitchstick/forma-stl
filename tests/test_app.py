import io
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import create_app, public_url, require_password


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.app = create_app(self.root / 'data', self.library)
        self.client = self.app.test_client()
        self.headers = {'X-Forma-Request': '1'}

    def tearDown(self):
        self.temp.cleanup()

    def add(self, **payload):
        r = self.client.post('/api/models', json={'title': 'Spiral vase', **payload}, headers=self.headers)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json

    def test_catalog_persists_and_edits_keep_files(self):
        m = self.add(tags=['vase', 'gift'], collection='Home', source_url='https://example.com/model')
        r = self.client.post(
            '/api/models/' + m['id'] + '/files',
            data={'files': [(io.BytesIO(b'solid test'), 'vase.stl'), (io.BytesIO(b'photo'), 'vase.png')]},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(len(r.json['files']), 2)
        self.assertTrue(r.json['cover_id'])
        file_id = r.json['files'][0]['id']
        edited = self.client.patch(
            '/api/models/' + m['id'],
            json={'title': 'My vase', 'favorite': True, 'status': 'Printed'},
            headers=self.headers,
        )
        self.assertEqual(edited.status_code, 200, edited.json)
        self.assertEqual(len(edited.json['files']), 2)
        fresh = create_app(self.root / 'data', self.library).test_client().get('/api/models').json
        self.assertEqual(fresh[0]['title'], 'My vase')
        self.assertTrue(fresh[0]['favorite'])
        with self.client.get('/api/files/' + file_id) as downloaded:
            self.assertEqual(downloaded.data, b'solid test')
        duplicate = self.client.post(
            '/api/models', json={'title': 'Again', 'source_url': 'https://example.com/model'}, headers=self.headers
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_invalid_edits_are_atomic(self):
        m = self.add()
        r = self.client.patch(
            '/api/models/' + m['id'], json={'title': 'Changed', 'cover_id': 'not-an-image'}, headers=self.headers
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get('/api/models').json[0]['title'], 'Spiral vase')

    def test_scan_is_readonly_and_idempotent(self):
        folder = self.library / 'Desk organizer'
        folder.mkdir()
        (folder / 'tray.stl').write_bytes(b'solid tray')
        (folder / 'cover.jpg').write_bytes(b'photo')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'secret.stl').write_bytes(b'not public')
        (self.library / 'escape').symlink_to(outside, target_is_directory=True)
        groups = self.client.get('/api/scan').json['groups']
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['files'], 2)
        r = self.client.post('/api/scan', json={'keys': [g['key'] for g in groups]}, headers=self.headers)
        self.assertEqual(r.json['imported'], 1)
        self.assertEqual(self.client.get('/api/scan').json['groups'], [])
        m = self.client.get('/api/models').json[0]
        self.assertEqual(len(m['files']), 2)
        self.assertTrue(m['cover_id'])
        self.client.delete('/api/models/' + m['id'], headers=self.headers)
        self.assertEqual((folder / 'tray.stl').read_bytes(), b'solid tray')

    def test_local_path_traversal_rejected(self):
        m = self.add()
        secret = self.root / 'secret.txt'
        secret.write_text('secret')
        with sqlite3.connect(self.root / 'data' / 'forma.sqlite3') as db:
            db.execute(
                'INSERT INTO files(id,model_id,name,path,size,kind) VALUES (?,?,?,?,?,?)',
                ('bad', m['id'], 'secret', str(secret), 6, 'model'),
            )
        self.assertEqual(self.client.get('/api/files/bad').status_code, 404)

    def test_backup_has_consistent_database_and_uploads(self):
        m = self.add()
        self.client.post(
            '/api/models/' + m['id'] + '/files', data={'files': (io.BytesIO(b'mesh'), 'test.stl')}, headers=self.headers
        )
        response = self.client.get('/api/backup')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as z:
            self.assertIn('forma.sqlite3', z.namelist())
            self.assertEqual(sum(n.startswith('files/') for n in z.namelist()), 1)
            z.extract('forma.sqlite3', self.root / 'restore')
        with sqlite3.connect(self.root / 'restore' / 'forma.sqlite3') as db:
            self.assertEqual(db.execute('SELECT title FROM models').fetchone()[0], 'Spiral vase')
        response.close()

    def test_upload_rejects_html_and_deduplicates(self):
        m = self.add()
        url = '/api/models/' + m['id'] + '/files'
        bad = self.client.post(url, data={'files': (io.BytesIO(b'<script>'), 'evil.html')}, headers=self.headers)
        self.assertEqual(bad.status_code, 400)
        for _ in range(2):
            r = self.client.post(url, data={'files': (io.BytesIO(b'mesh'), 'part.stl')}, headers=self.headers)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json['files']), 1)

    def test_metadata_success_and_blocked_site_fallback(self):
        html = b'<title>Fallback</title><meta property="og:title" content="A nice vase"><meta property="og:image" content="/photo.jpg"><meta name="keywords" content="vase, spiral"><meta name="author" content="Designer">'
        with patch('app.fetch_public', return_value=(html, 'text/html', 'https://example.com/model')):
            r = self.client.post('/api/metadata', json={'url': 'https://example.com/model'}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['title'], 'A nice vase')
        self.assertEqual(r.json['image_url'], 'https://example.com/photo.jpg')
        self.assertEqual(r.json['tags'], ['vase', 'spiral'])
        with patch('app.fetch_public', side_effect=ValueError('Blocked')):
            r = self.client.post('/api/metadata', json={'url': 'https://example.com'}, headers=self.headers)
        self.assertEqual(r.status_code, 422)
        self.assertIn('save its link', r.json['error'])

    def test_import_blocks_local_network(self):
        for host in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '192.168.1.1']:
            addr = ('AF_INET', 'SOCK_STREAM', 6, '', (host, 80))
            with patch('app.socket.getaddrinfo', return_value=[addr]):
                with self.assertRaises(ValueError):
                    public_url('http://example.test')
        with self.assertRaises(ValueError):
            public_url('file:///etc/passwd')

    def test_auth_and_cross_origin_changes(self):
        self.assertEqual(self.client.post('/api/models', json={'title': 'Bad'}).status_code, 403)
        self.assertEqual(
            self.client.post(
                '/api/models', json={'title': 'Bad'}, headers={**self.headers, 'Origin': 'https://evil.test'}
            ).status_code,
            403,
        )
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'test-password', 'FORMA_USERNAME': 'me'}):
            self.assertEqual(self.client.get('/api/models').status_code, 401)
            self.assertEqual(
                self.client.get('/api/models', headers={'Authorization': 'Basic bWU6dGVzdC1wYXNzd29yZA=='}).status_code,
                200,
            )

    def test_unraid_icon_is_public_but_catalog_stays_protected(self):
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'test-password'}):
            with self.client.get('/static/app-icon.png') as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'image/png')
                self.assertTrue(response.data.startswith(b'\x89PNG'))
            with self.client.head('/static/app-icon.png') as response:
                self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.get('/api/models').status_code, 401)
            with self.client.get('/') as response:
                self.assertEqual(response.status_code, 401)
            self.assertEqual(self.client.get('/static/app.js').status_code, 401)

    def test_startup_requires_a_password(self):
        with patch.dict(os.environ, {'FORMA_PASSWORD': '', 'FORMA_ALLOW_NO_PASSWORD': ''}):
            with self.assertRaises(SystemExit):
                require_password(self.app)
        with patch.dict(os.environ, {'FORMA_PASSWORD': '', 'FORMA_ALLOW_NO_PASSWORD': '1'}):
            require_password(self.app)
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'test-password'}):
            require_password(self.app)

    def test_folder_watch_defaults_to_fifteen_minutes(self):
        self.assertEqual(self.client.get('/api/watch').json['interval'], 900)


if __name__ == '__main__':
    unittest.main()
