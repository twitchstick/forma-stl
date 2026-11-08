import io
import tempfile
import unittest
from pathlib import Path
from app import create_app


class LibraryToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.app = create_app(self.root / 'data', self.library)
        self.client = self.app.test_client()
        self.headers = {'X-Forma-Request': '1'}
        self.service = self.app.extensions['library_services']

    def tearDown(self):
        self.temp.cleanup()

    def folder(self, name='Desk', content=b'mesh'):
        folder = self.library / name
        folder.mkdir(exist_ok=True)
        (folder / 'part.stl').write_bytes(content)
        return folder

    def scan(self):
        r = self.client.post('/api/watch/scan', json={}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.json)
        return self.client.get('/api/watch/review').json['items']

    def approve(self, items):
        r = self.client.post(
            '/api/watch/review', json={'ids': [i['id'] for i in items], 'action': 'approve'}, headers=self.headers
        )
        self.assertEqual(r.status_code, 200, r.json)

    def test_new_files_wait_for_approval(self):
        self.folder()
        items = self.scan()
        self.assertEqual(items[0]['kind'], 'new')
        self.assertEqual(self.client.get('/api/models').json, [])
        self.approve(items)
        self.assertEqual(len(self.client.get('/api/models').json), 1)
        self.assertEqual(self.scan(), [])

    def test_folder_changes_preserve_organization_and_uploaded_photos(self):
        folder = self.folder()
        self.approve(self.scan())
        m = self.client.get('/api/models').json[0]
        self.client.patch(
            '/api/models/' + m['id'],
            json={
                'title': 'My desk',
                'tags': ['gift'],
                'description': 'Keep my notes',
                'status': 'Printed',
                'favorite': True,
            },
            headers=self.headers,
        )
        self.client.post(
            '/api/models/' + m['id'] + '/files',
            data={'files': (io.BytesIO(b'photo'), 'cover.png')},
            headers=self.headers,
        )
        cover = self.client.get('/api/models').json[0]['cover_id']
        (folder / 'part.stl').unlink()
        (folder / 'revision.stl').write_bytes(b'updated mesh')
        items = self.scan()
        self.assertEqual(items[0]['kind'], 'changed')
        self.approve(items)
        updated = self.client.get('/api/models').json[0]
        self.assertEqual(updated['id'], m['id'])
        self.assertEqual(updated['title'], 'My desk')
        self.assertEqual(updated['tags'], ['gift'])
        self.assertEqual(updated['description'], 'Keep my notes')
        self.assertEqual(updated['status'], 'Printed')
        self.assertTrue(updated['favorite'])
        self.assertEqual(updated['cover_id'], cover)
        self.assertEqual({f['name'] for f in updated['files']}, {'revision.stl', 'cover.png'})
        self.assertTrue((folder / 'revision.stl').exists())
        self.assertEqual(self.scan(), [])

    def test_ignored_changes_return_only_after_files_change(self):
        folder = self.folder()
        items = self.scan()
        self.client.post('/api/watch/review', json={'ids': [items[0]['id']], 'action': 'ignore'}, headers=self.headers)
        self.assertEqual(self.scan(), [])
        (folder / 'another.stl').write_bytes(b'new')
        self.assertEqual(len(self.scan()), 1)

    def test_missing_files_keep_catalog_entries(self):
        folder = self.folder()
        self.approve(self.scan())
        (folder / 'part.stl').unlink()
        folder.rmdir()
        items = self.scan()
        self.assertEqual(items[0]['kind'], 'missing')
        self.approve(items)
        models = self.client.get('/api/models').json
        self.assertEqual(len(models), 1)
        self.assertEqual(len(models[0]['files']), 1)
        self.assertEqual(self.scan(), [])

    def test_missing_mount_does_not_queue_everything_for_removal(self):
        self.folder()
        self.approve(self.scan())
        self.library.rename(self.root / 'unmounted')
        r = self.client.post('/api/watch/scan', json={}, headers=self.headers)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get('/api/watch/review').json['items'], [])
        self.assertEqual(len(self.client.get('/api/models').json), 1)

    def test_changed_again_requires_a_fresh_review(self):
        folder = self.folder()
        items = self.scan()
        (folder / 'part.stl').write_bytes(b'changed again')
        r = self.client.post(
            '/api/watch/review', json={'ids': [items[0]['id']], 'action': 'approve'}, headers=self.headers
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get('/api/models').json, [])

    def test_bulk_edit_merges_tags_and_updates_selected_models(self):
        ids = []
        for title in ['A', 'B', 'Unselected']:
            r = self.client.post('/api/models', json={'title': title, 'tags': ['old', 'keep']}, headers=self.headers)
            ids.append(r.json['id'])
        r = self.client.post(
            '/api/models/bulk',
            json={
                'ids': ids[:2],
                'changes': {
                    'tags_add': ['new', 'keep'],
                    'tags_remove': ['old'],
                    'collection': 'Desk',
                    'status': 'To print',
                    'favorite': True,
                },
            },
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['updated'], 2)
        for m in self.client.get('/api/models').json:
            if m['id'] in ids[:2]:
                self.assertEqual(m['tags'], ['keep', 'new'])
                self.assertEqual(m['collection'], 'Desk')
                self.assertTrue(m['favorite'])
            else:
                self.assertEqual(m['tags'], ['old', 'keep'])
                self.assertFalse(m['favorite'])

    def test_bulk_edit_rolls_back_if_any_id_is_missing(self):
        id = self.client.post('/api/models', json={'title': 'A'}, headers=self.headers).json['id']
        r = self.client.post(
            '/api/models/bulk', json={'ids': [id, 'missing'], 'changes': {'status': 'Printed'}}, headers=self.headers
        )
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get('/api/models').json[0]['status'], 'Saved')

    def test_duplicates_compare_contents_and_do_not_hide_watch_changes(self):
        a = self.folder('A', b'identical')
        b = self.folder('B', b'identical')
        (b / 'part.stl').rename(b / 'different-name.stl')
        self.approve(self.scan())
        self.service.index_duplicates()
        groups = self.client.get('/api/duplicates').json['groups']
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]['files']), 2)
        self.assertTrue((a / 'part.stl').exists())
        self.assertTrue((b / 'different-name.stl').exists())
        (a / 'part.stl').write_bytes(b'updated')
        self.assertEqual(self.client.get('/api/duplicates').json['groups'], [])
        self.service.index_duplicates()
        changes = self.scan()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['kind'], 'changed')
        self.assertEqual(self.client.get('/api/duplicates').json['groups'], [])

    def test_watcher_preferences_persist_and_validate(self):
        r = self.client.patch('/api/watch', json={'enabled': False, 'interval': 300}, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        app = create_app(self.root / 'data', self.library)
        self.assertFalse(app.test_client().get('/api/watch').json['enabled'])
        self.assertEqual(app.test_client().get('/api/watch').json['interval'], 300)
        r = self.client.patch('/api/watch', json={'enabled': True, 'interval': 1}, headers=self.headers)
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main()
