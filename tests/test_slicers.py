import base64
import io
import os
import tempfile
import trimesh
import zipfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit
from unittest.mock import patch
from app import create_app


class SlicerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / 'data', self.root / 'library')
        self.c = self.app.test_client()
        self.h = {'X-Forma-Request': '1'}
        m = self.c.post('/api/models', json={'title': 'Handoff model'}, headers=self.h).json
        self.body = trimesh.creation.box(extents=[10, 20, 30]).export(file_type='stl')
        self.c.post(
            '/api/models/' + m['id'] + '/files', data={'files': (io.BytesIO(self.body), 'Sample.stl')}, headers=self.h
        )
        self.file = self.c.get('/api/models').json[0]['files'][0]['id']
        self.db = self.app.extensions['library_services'].db

    def tearDown(self):
        self.app.extensions['collection_schedules'].stop.set()
        self.app.extensions['collection_schedules'].executor.shutdown(wait=True)
        self.app.extensions['source_imports'].executor.shutdown(wait=True)
        self.app.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True)
        self.temp.cleanup()

    def link(self, slicer='bambu', platform='mac', headers=None):
        r = self.c.post(
            '/api/files/' + self.file + '/slicer-link',
            json={'slicer': slicer, 'platform': platform},
            headers=headers or self.h,
        )
        self.assertEqual(r.status_code, 200, r.json)
        return r.json

    def test_four_presets_add_once_and_keep_user_edits(self):
        p = self.c.get('/api/printer-presets').json
        self.assertEqual({i['id'] for i in p['presets']}, {'h2d', 'h2s', 'p1s', 'u1'})
        for id in ['h2d', 'h2s', 'p1s', 'u1']:
            self.assertEqual(
                self.c.post('/api/printer-presets/' + id + '/add', json={}, headers=self.h).status_code, 201
            )
        printer = next(p for p in self.c.get('/api/printers').json if p['preset'] == 'u1')
        r = self.c.patch('/api/printers/' + printer['id'], json={'name': 'My U1', 'nozzle': 0.6}, headers=self.h)
        self.assertEqual(r.status_code, 200, r.json)
        self.c.post('/api/printer-presets/u1/add', json={}, headers=self.h)
        printers = self.c.get('/api/printers').json
        self.assertEqual(len(printers), 4)
        p = next(p for p in printers if p['preset'] == 'u1')
        self.assertEqual(p['name'], 'My U1')
        self.assertEqual(p['slicer'], 'snapmaker')
        self.assertEqual(p['nozzle'], 0.6)
        h2d = next(p for p in printers if p['preset'] == 'h2d')
        self.assertEqual(h2d['width'], 300)
        self.assertIn('shared dual-nozzle', h2d['notes'])

    def test_scheme_encoding_uses_the_correct_slicer_and_platform(self):
        mac = self.link()
        self.assertTrue(mac['launch_url'].startswith('bambustudioopen://'))
        self.assertEqual(unquote(mac['launch_url'].split('://', 1)[1]), mac['download_url'])
        win = self.link(platform='windows')
        self.assertTrue(win['launch_url'].startswith('bambustudio://open?file='))
        snap = self.link(slicer='snapmaker')
        self.assertTrue(snap['launch_url'].startswith('snapmaker-orca://open?file='))
        self.assertEqual(snap['name'], 'Sample.stl')
        self.assertIn('%3A%2F%2F', snap['launch_url'])

    def test_bambu_stl_copy_is_valid_3mf_and_original_is_kept(self):
        result = self.link()
        self.assertTrue(result['converted'])
        self.assertTrue(result['name'].endswith('.3mf'))
        response = self.c.get(urlsplit(result['download_url']).path)
        with zipfile.ZipFile(io.BytesIO(response.data)) as z:
            self.assertIn('3D/3dmodel.model', z.namelist())
        scene = trimesh.load(io.BytesIO(response.data), file_type='3mf', force='scene')
        self.assertEqual(list(scene.extents), [10, 20, 30])
        response.close()
        with self.db() as c:
            original = c.execute('SELECT path FROM files WHERE id=?', (self.file,)).fetchone()[0]
        self.assertEqual(Path(original).read_bytes(), self.body)

    def test_only_the_scoped_file_is_available_without_catalog_auth(self):
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'test-secret', 'FORMA_USERNAME': 'owner'}):
            owner = {**self.h, 'Authorization': 'Basic ' + base64.b64encode(b'owner:test-secret').decode()}
            self.assertEqual(
                self.c.post(
                    '/api/files/' + self.file + '/slicer-link', json={'slicer': 'bambu'}, headers=self.h
                ).status_code,
                401,
            )
            result = self.link(slicer='snapmaker', headers=owner)
            path = urlsplit(result['download_url']).path
            guest = self.app.test_client()
            self.assertNotIn('test-secret', result['download_url'])
            r = guest.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data, self.body)
            self.assertIn('no-store', r.headers['Cache-Control'])
            r.close()
            head = guest.head(path)
            self.assertEqual(head.status_code, 200)
            head.close()
            self.assertEqual(guest.get('/api/models').status_code, 401)
            self.assertEqual(guest.get('/api/files/' + self.file).status_code, 401)
            self.assertEqual(guest.get(path.replace('Sample.stl', 'Other.stl')).status_code, 404)
            self.assertEqual(guest.get('/slicer-download/not-a-token/Sample.stl').status_code, 404)

    def test_native_range_retries_are_allowed_until_expiry(self):
        path = urlsplit(self.link(slicer='snapmaker')['download_url']).path
        r = self.c.get(path, headers={'Range': 'bytes=0-4'})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.data, self.body[:5])
        r.close()
        whole = self.c.get(path)
        self.assertEqual(whole.status_code, 200)
        whole.close()
        with self.db() as c:
            c.execute('UPDATE slicer_downloads SET expires=0')
        self.assertEqual(self.c.get(path).status_code, 404)

    def test_changed_file_or_outside_library_is_never_served(self):
        path = urlsplit(self.link(slicer='snapmaker')['download_url']).path
        with self.db() as c:
            f = c.execute('SELECT path FROM files WHERE id=?', (self.file,)).fetchone()[0]
        Path(f).write_bytes(b'Changed file')
        self.assertEqual(self.c.get(path).status_code, 410)
        outside = self.root / 'outside.stl'
        outside.write_text('private')
        with self.db() as c:
            c.execute('UPDATE files SET path=? WHERE id=?', (str(outside), self.file))
        self.assertEqual(self.c.get(path).status_code, 404)
        self.assertEqual(
            self.c.post(
                '/api/files/' + self.file + '/slicer-link', json={'slicer': 'bambu'}, headers=self.h
            ).status_code,
            404,
        )

    def test_unsupported_types_and_cross_origin_link_creation_are_rejected(self):
        self.assertEqual(
            self.c.post(
                '/api/files/' + self.file + '/slicer-link', json={'slicer': 'shell'}, headers=self.h
            ).status_code,
            400,
        )
        self.assertEqual(
            self.c.post(
                '/api/files/' + self.file + '/slicer-link',
                json={'slicer': 'bambu'},
                headers={'X-Forma-Request': '1', 'Origin': 'https://evil.example'},
            ).status_code,
            403,
        )
        with self.db() as c:
            original = c.execute('SELECT path FROM files WHERE id=?', (self.file,)).fetchone()[0]
            zipped = Path(original).with_suffix('.zip')
            zipped.write_bytes(b'ZIP')
            c.execute('UPDATE files SET path=?,name=? WHERE id=?', (str(zipped), 'archive.zip', self.file))
        self.assertEqual(
            self.c.post(
                '/api/files/' + self.file + '/slicer-link', json={'slicer': 'snapmaker'}, headers=self.h
            ).status_code,
            400,
        )


if __name__ == '__main__':
    unittest.main()
