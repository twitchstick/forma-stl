import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


class DocumentTests(unittest.TestCase):
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

    def add(self, title='Robot arm'):
        return self.client.post('/api/models', json={'title': title}, headers=self.headers).json

    def attach(self, model, name, body):
        return self.client.post(
            f"/api/models/{model['id']}/files",
            data={'files': [(io.BytesIO(body), name)]},
            headers=self.headers,
            content_type='multipart/form-data',
        )

    def scan(self):
        self.client.post('/api/watch/scan', json={}, headers=self.headers)
        return self.client.get('/api/watch/review').json['items']

    def approve(self, items):
        response = self.client.post(
            '/api/watch/review', json={'ids': [i['id'] for i in items], 'action': 'approve'}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.json)

    def kinds(self, model):
        return {f['name']: f['kind'] for f in model['files']}

    def test_an_uploaded_pdf_is_kept_as_a_document(self):
        model = self.add()
        response = self.attach(model, 'Assembly Guide.PDF', PDF)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.kinds(response.json), {'Assembly_Guide.PDF': 'document'})
        self.assertEqual(response.json['cover_id'], '')  # Only pictures become covers.

    def test_a_pdf_opens_in_the_browser_and_can_still_be_downloaded(self):
        model = self.add()
        file_id = self.attach(model, 'guide.pdf', PDF).json['files'][0]['id']
        with self.client.get(f'/api/files/{file_id}') as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'application/pdf')
            self.assertTrue(response.headers['Content-Disposition'].startswith('inline'))
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.headers['Content-Security-Policy'], "frame-ancestors 'none'")
            self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
            self.assertEqual(response.get_data(), PDF)
        with self.client.get(f'/api/files/{file_id}?download=1') as response:
            self.assertTrue(response.headers['Content-Disposition'].startswith('attachment'))
            self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])

    def test_a_file_that_only_claims_to_be_a_pdf_is_refused(self):
        model = self.add()
        response = self.attach(model, 'guide.pdf', b'<html><script>alert(1)</script></html>')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not a PDF', response.json['error'])
        self.assertEqual(self.client.get('/api/models').json[0]['files'], [])
        self.assertEqual(list((self.root / 'data' / 'files' / model['id']).iterdir()), [])

    def test_other_document_types_are_still_refused(self):
        response = self.attach(self.add(), 'notes.docx', b'PK')
        self.assertEqual(response.status_code, 400)
        self.assertIn('PDF', response.json['error'])

    def test_a_share_file_named_pdf_is_never_served_as_a_web_page(self):
        folder = self.library / 'Trick'
        folder.mkdir()
        (folder / 'part.stl').write_bytes(b'mesh')
        (folder / 'guide.pdf').write_bytes(b'<html><script>alert(1)</script></html>')
        self.approve(self.scan())
        files = {f['name']: f['id'] for f in self.client.get('/api/models').json[0]['files']}
        with self.client.get(f"/api/files/{files['guide.pdf']}") as response:
            self.assertEqual(response.mimetype, 'application/pdf')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_documents_are_not_previewed_measured_or_matched_as_models(self):
        model = self.add()
        file_id = self.attach(model, 'guide.pdf', PDF).json['files'][0]['id']
        self.assertEqual(self.client.get(f"/api/models/{model['id']}/thumbnail").status_code, 404)
        self.assertEqual(self.client.get(f'/api/files/{file_id}/mesh').status_code, 404)
        sha = hashlib.sha256(PDF).hexdigest()
        lookup = self.client.post(
            '/api/files/lookup', json={'files': [{'sha256': sha, 'size': len(PDF)}]}, headers=self.headers
        )
        self.assertEqual(lookup.json['matches'][sha], [])

    def test_folder_scans_bring_in_pdfs_next_to_model_files(self):
        folder = self.library / 'Crane'
        folder.mkdir()
        (folder / 'boom.stl').write_bytes(b'mesh')
        (folder / 'Instructions.pdf').write_bytes(PDF)
        self.approve(self.scan())
        model = self.client.get('/api/models').json[0]
        self.assertEqual(self.kinds(model), {'boom.stl': 'model', 'Instructions.pdf': 'document'})

    def test_a_pdf_beside_a_loose_model_file_joins_it(self):
        (self.library / 'whistle.stl').write_bytes(b'mesh')
        (self.library / 'whistle.pdf').write_bytes(PDF)
        self.approve(self.scan())
        models = self.client.get('/api/models').json
        self.assertEqual(len(models), 1)
        self.assertEqual(set(self.kinds(models[0]).values()), {'model', 'document'})

    def test_a_folder_with_only_a_pdf_is_not_a_model(self):
        (self.library / 'Manuals').mkdir()
        (self.library / 'Manuals' / 'printer.pdf').write_bytes(PDF)
        self.assertEqual(self.scan(), [])

    def test_a_pdf_added_to_an_imported_folder_shows_up_for_review(self):
        folder = self.library / 'Crane'
        folder.mkdir()
        (folder / 'boom.stl').write_bytes(b'mesh')
        self.approve(self.scan())
        self.client.patch(
            '/api/models/' + self.client.get('/api/models').json[0]['id'],
            json={'title': 'My crane', 'tags': ['gift']},
            headers=self.headers,
        )
        (folder / 'Instructions.pdf').write_bytes(PDF)
        items = self.scan()
        self.assertEqual([i['kind'] for i in items], ['changed'])
        self.approve(items)
        model = self.client.get('/api/models').json[0]
        self.assertEqual(model['title'], 'My crane')
        self.assertEqual(self.kinds(model), {'boom.stl': 'model', 'Instructions.pdf': 'document'})
        self.assertEqual(self.scan(), [])

    def test_the_manual_folder_import_brings_in_pdfs(self):
        folder = self.library / 'Crane'
        folder.mkdir()
        (folder / 'boom.stl').write_bytes(b'mesh')
        (folder / 'Instructions.pdf').write_bytes(PDF)
        self.client.post('/api/scan', json={'keys': ['Crane']}, headers=self.headers)
        self.assertEqual(self.kinds(self.client.get('/api/models').json[0])['Instructions.pdf'], 'document')

    def test_pdfs_need_sign_in(self):
        file_id = self.attach(self.add(), 'guide.pdf', PDF).json['files'][0]['id']
        with patch.dict(os.environ, {'FORMA_PASSWORD': 'secret'}):
            self.assertEqual(self.client.get(f'/api/files/{file_id}').status_code, 401)


if __name__ == '__main__':
    unittest.main()
