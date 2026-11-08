import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import create_app


def fingerprint(body):
    return hashlib.sha256(body).hexdigest()


class FileLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.env = patch.dict(os.environ, {'FORMA_PASSWORD': ''})
        self.env.start()
        self.app = create_app(self.root / 'data', self.library)
        self.client = self.app.test_client()
        self.headers = {'X-Forma-Request': '1'}

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def add_upload(self, title, name, body):
        model = self.client.post('/api/models', json={'title': title}, headers=self.headers).json
        self.client.post(
            f"/api/models/{model['id']}/files",
            data={'files': [(io.BytesIO(body), name)]},
            headers=self.headers,
            content_type='multipart/form-data',
        )
        return model

    def lookup(self, *bodies):
        files = [{'sha256': fingerprint(b), 'size': len(b)} for b in bodies]
        return self.client.post('/api/files/lookup', json={'files': files}, headers=self.headers)

    def scan_folder(self, folder, name, body):
        (self.library / folder).mkdir()
        path = self.library / folder / name
        path.write_bytes(body)
        self.client.post('/api/scan', json={'keys': [folder]}, headers=self.headers)
        return path

    def test_finds_uploaded_files_by_content_not_name(self):
        model = self.add_upload('Benchy', 'boat.stl', b'solid benchy')
        result = self.lookup(b'solid benchy', b'solid other').json['matches']
        self.assertEqual(
            result[fingerprint(b'solid benchy')], [{'model_id': model['id'], 'title': 'Benchy', 'name': 'boat.stl'}]
        )
        self.assertEqual(result[fingerprint(b'solid other')], [])

    def test_share_files_are_fingerprinted_on_demand_when_sizes_match(self):
        self.scan_folder('Vase', 'vase.stl', b'solid vase')
        with closing(sqlite3.connect(self.root / 'data' / 'forma.sqlite3')) as c:
            self.assertEqual(c.execute("SELECT sha256 FROM files WHERE name='vase.stl'").fetchone()[0], '')
        result = self.lookup(b'solid vase').json['matches'][fingerprint(b'solid vase')]
        self.assertEqual([m['title'] for m in result], ['Vase'])
        # The fingerprint is kept, so the duplicate finder benefits too.
        with closing(sqlite3.connect(self.root / 'data' / 'forma.sqlite3')) as c:
            self.assertEqual(
                c.execute("SELECT sha256 FROM files WHERE name='vase.stl'").fetchone()[0], fingerprint(b'solid vase')
            )

    def test_same_size_with_different_content_is_not_a_match(self):
        self.scan_folder('Vase', 'vase.stl', b'solid vase')
        self.assertEqual(self.lookup(b'solid bowl').json['matches'][fingerprint(b'solid bowl')], [])

    def test_a_file_that_changed_no_longer_matches_its_old_fingerprint(self):
        path = self.scan_folder('Vase', 'vase.stl', b'solid vase')
        self.assertTrue(self.lookup(b'solid vase').json['matches'][fingerprint(b'solid vase')])
        path.write_bytes(b'solid changed to something longer')
        os.utime(path, (1, 1))
        self.assertEqual(self.lookup(b'solid vase').json['matches'][fingerprint(b'solid vase')], [])

    def test_photos_are_ignored(self):
        self.add_upload('Benchy', 'photo.png', b'\x89PNG not a model')
        self.assertEqual(self.lookup(b'\x89PNG not a model').json['matches'][fingerprint(b'\x89PNG not a model')], [])

    def test_bad_requests_are_refused(self):
        for files in (
            'nope',
            [{'sha256': 'short', 'size': 1}],
            [{'sha256': 'a' * 64, 'size': -1}],
            [{'sha256': 'a' * 64, 'size': True}],
            [{'sha256': 'a' * 64, 'size': 1}] * 201,
        ):
            with self.subTest(files=str(files)[:40]):
                response = self.client.post('/api/files/lookup', json={'files': files}, headers=self.headers)
                self.assertEqual(response.status_code, 400)

    def test_needs_sign_in_and_the_request_header(self):
        self.assertEqual(self.client.post('/api/files/lookup', json={'files': []}).status_code, 403)
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'secret'}):
            self.assertEqual(
                self.client.post('/api/files/lookup', json={'files': []}, headers=self.headers).status_code, 401
            )


if __name__ == '__main__':
    unittest.main()
