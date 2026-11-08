import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import trimesh

import previews
from app import create_app


class ViewerMeshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'FORMA_PASSWORD': ''})
        self.env.start()
        self.app = create_app(self.root / 'data', self.root / 'library')
        self.client = self.app.test_client()
        self.headers = {'X-Forma-Request': '1'}
        self.model = self.client.post('/api/models', json={'title': 'Box'}, headers=self.headers).json

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def upload(self, name, body):
        files = self.client.post(
            f"/api/models/{self.model['id']}/files",
            data={'files': [(io.BytesIO(body), name)]},
            headers=self.headers,
            content_type='multipart/form-data',
        ).json['files']
        return next(f for f in files if f['name'] == name)

    def mesh(self, file_id):
        response = self.client.get(f'/api/files/{file_id}/mesh')
        geometry = trimesh.load(io.BytesIO(response.data), file_type='stl') if response.status_code == 200 else None
        return response, geometry

    def test_stl_3mf_and_zip_become_the_same_mesh(self):
        box = trimesh.creation.box((20, 30, 10))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('readme.txt', 'hello')
            z.writestr('parts/box.stl', box.export(file_type='stl'))
        for name, body in [
            ('box.stl', box.export(file_type='stl')),
            ('box.3mf', box.export(file_type='3mf')),
            ('box.zip', archive.getvalue()),
        ]:
            with self.subTest(name):
                response, geometry = self.mesh(self.upload(name, body)['id'])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'model/stl')
                self.assertEqual(geometry.extents.round(3).tolist(), [20, 30, 10])
        self.assertTrue(list((self.root / 'data' / 'meshes').glob('*.stl')))

    def test_large_models_are_simplified(self):
        ball = trimesh.creation.icosphere(subdivisions=7)
        self.assertGreater(len(ball.faces), previews.MESH_FACE_LIMIT)
        response, geometry = self.mesh(self.upload('ball.stl', ball.export(file_type='stl'))['id'])
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(geometry.faces), previews.MESH_FACE_LIMIT)

    def test_unsupported_and_unknown_files(self):
        step = self.upload('part.step', b'ISO-10303-21;')
        self.assertEqual(self.client.get(f"/api/files/{step['id']}/mesh").status_code, 422)
        broken = self.upload('broken.3mf', b'not a zip')
        response = self.client.get(f"/api/files/{broken['id']}/mesh")
        self.assertEqual(response.status_code, 422)
        self.assertIn('error', response.json)
        photo = self.upload('photo.png', b'\x89PNG fake')
        self.assertEqual(self.client.get(f"/api/files/{photo['id']}/mesh").status_code, 404)
        self.assertEqual(self.client.get('/api/files/missing/mesh').status_code, 404)

    def test_model_pages_load_the_app_and_need_sign_in(self):
        page = self.client.get('/models/' + self.model['id'])
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="model-grid"', page.data)
        box = self.upload('box.stl', b'solid x')
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'secret'}):
            signed_out = self.app.test_client()
            login = signed_out.get('/models/' + self.model['id'])
            self.assertEqual(login.status_code, 401)
            self.assertIn(b'login-form', login.data)
            self.assertEqual(signed_out.get(f"/api/files/{box['id']}/mesh").status_code, 401)
            self.assertEqual(signed_out.get('/static/fonts/Geist-Variable.woff2').status_code, 200)


if __name__ == '__main__':
    unittest.main()
