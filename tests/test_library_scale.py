import gzip
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trimesh

from app import create_app


class LibraryScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'library').mkdir()
        self.env = patch.dict(os.environ, {'FORMA_PASSWORD': ''})
        self.env.start()
        self.app = create_app(root / 'data', root / 'library')
        self.client = self.app.test_client()
        self.headers = {'X-Forma-Request': '1'}

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def add(self, title, description=''):
        return self.client.post(
            '/api/models', json={'title': title, 'description': description}, headers=self.headers
        ).json

    def attach(self, model, name, body):
        return self.client.post(
            f"/api/models/{model['id']}/files",
            data={'files': [(io.BytesIO(body), name)]},
            headers=self.headers,
            content_type='multipart/form-data',
        ).json

    def test_large_lists_are_compressed_for_browsers_that_accept_it(self):
        for i in range(5):
            self.add(f'Model {i}', 'a long description ' * 1500)
        plain = self.client.get('/api/models')
        self.assertNotIn('Content-Encoding', plain.headers)
        packed = self.client.get('/api/models', headers={'Accept-Encoding': 'gzip, deflate'})
        self.assertEqual(packed.headers['Content-Encoding'], 'gzip')
        self.assertIn('Accept-Encoding', packed.headers['Vary'])
        self.assertLess(len(packed.get_data()), len(plain.get_data()) / 4)
        self.assertEqual(json.loads(gzip.decompress(packed.get_data())), plain.json)

    def test_small_lists_are_left_alone(self):
        self.add('Benchy')
        response = self.client.get('/api/models', headers={'Accept-Encoding': 'gzip'})
        self.assertNotIn('Content-Encoding', response.headers)
        self.assertEqual(len(response.json), 1)

    def test_pictures_can_be_revalidated_but_data_is_never_stored(self):
        model = self.add('Box')
        self.attach(model, 'box.stl', trimesh.creation.box((5, 5, 5)).export(file_type='stl'))
        self.attach(model, 'photo.png', b'\x89PNG\r\n\x1a\n')
        files = {f['name']: f['id'] for f in self.client.get('/api/models').json[0]['files']}
        for url in (f"/api/models/{model['id']}/thumbnail", f"/api/files/{files['photo.png']}"):
            with self.subTest(url=url):
                first = self.client.get(url)
                self.assertEqual(first.status_code, 200)
                first.close()
                self.assertEqual(first.headers['Cache-Control'], 'private, no-cache')
                again = self.client.get(url, headers={'If-None-Match': first.headers['ETag']})
                self.assertEqual(again.status_code, 304)
                self.assertEqual(again.headers['Cache-Control'], 'private, no-cache')
        self.assertEqual(self.client.get('/api/models').headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get('/api/files/missing').headers['Cache-Control'], 'no-store')

    def test_pictures_still_need_sign_in_when_revalidated(self):
        model = self.add('Box')
        self.attach(model, 'photo.png', b'\x89PNG\r\n\x1a\n')
        file_id = self.client.get('/api/models').json[0]['files'][0]['id']
        with self.client.get(f'/api/files/{file_id}') as first:
            etag = first.headers['ETag']
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'secret'}):
            response = self.client.get(f'/api/files/{file_id}', headers={'If-None-Match': etag})
        self.assertEqual(response.status_code, 401)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'library').mkdir()
        self.env = patch.dict(os.environ, {'FORMA_PASSWORD': 'secret'})
        self.env.start()
        self.client = create_app(root / 'data', root / 'library').test_client()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_phones_can_read_the_install_details_before_sign_in(self):
        with self.client.get('/static/manifest.webmanifest') as response:
            self.assertEqual(response.status_code, 200)
            manifest = json.loads(response.get_data())
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['start_url'], '/')
        sizes = {icon['sizes'] for icon in manifest['icons']}
        self.assertTrue({'192x192', '512x512'} <= sizes)
        self.assertTrue(any(icon.get('purpose') == 'maskable' for icon in manifest['icons']))
        for src in [icon['src'] for icon in manifest['icons']] + ['/static/icons/apple-touch-icon.png']:
            with self.subTest(src=src), self.client.get(src) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'image/png')

    def test_the_rest_of_the_static_files_still_need_sign_in(self):
        self.assertEqual(self.client.get('/static/app.js').status_code, 401)
        self.assertEqual(self.client.get('/static/icons/../app.js').status_code, 401)

    def test_both_pages_link_the_install_details(self):
        root = Path(__file__).resolve().parent.parent / 'static'
        for page in ('index.html', 'login.html'):
            with self.subTest(page=page):
                html = (root / page).read_text()
                self.assertIn('rel="manifest"', html)
                self.assertIn('apple-touch-icon', html)


if __name__ == '__main__':
    unittest.main()
