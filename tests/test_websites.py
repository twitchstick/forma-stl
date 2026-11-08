import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
from app import create_app
from website_import import identity
from makerworld_import import normalize_item


class WebsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / 'data', self.root / 'library')
        self.client = self.app.test_client()
        self.headers = {'X-Forma-Request': '1'}

    def tearDown(self):
        self.app.extensions['source_imports'].executor.shutdown(wait=True)
        self.app.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True)
        self.temp.cleanup()

    def item(self, provider, url, name='Workshop'):
        return dict(
            provider=provider,
            source_url=url,
            title='A model',
            creator='A maker',
            tags=['useful'],
            collections=[dict(id='123', name=name)],
        )

    def stage(self, items):
        r = self.client.post(
            '/api/makerworld/browser-batch',
            json=dict(format='forma-websites-v1', items=items, title='Website test'),
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 201, r.json)
        return r.json['id']

    def apply(self, batch):
        detail = self.client.get('/api/makerworld/batches/' + batch).json
        r = self.client.post(
            '/api/makerworld/batches/' + batch + '/apply',
            json={'ids': [i['id'] for i in detail['items']]},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.json)
        return r.json

    def test_all_providers_require_review_and_do_not_collide(self):
        items = [
            self.item('printables', 'https://www.printables.com/model/123-tray'),
            self.item('cults3d', 'https://cults3d.com/en/3d-model/home/tray'),
            self.item('thangs', 'https://thangs.com/designer/maker/3d-model/tray-123'),
        ]
        batch = self.stage(items)
        self.assertEqual(self.client.get('/api/models').json, [])
        self.assertEqual(self.apply(batch)['created'], 3)
        models = self.client.get('/api/models').json
        self.assertTrue(all(m['makerworld'] is None for m in models))
        self.assertTrue(all(m['collections'] == ['Workshop'] for m in models))
        self.assertEqual(self.apply(self.stage(items))['linked'], 3)
        self.assertEqual(len(self.client.get('/api/models').json), 3)

    def test_reimport_preserves_edits_and_adds_collection(self):
        self.apply(self.stage([self.item('printables', 'https://printables.com/model/123-tray')]))
        m = self.client.get('/api/models').json[0]
        r = self.client.patch(
            '/api/models/' + m['id'],
            json={'title': 'My title', 'tags': ['mine'], 'status': 'Printed'},
            headers=self.headers,
        )
        # App uses PUT for owner edits.
        if r.status_code == 405:
            r = self.client.put(
                '/api/models/' + m['id'],
                json={**m, 'title': 'My title', 'tags': ['mine'], 'status': 'Printed'},
                headers=self.headers,
            )
        self.assertEqual(r.status_code, 200, r.json)
        new = self.item('printables', 'https://www.printables.com/model/123/files', 'Gifts')
        new['collections'][0]['id'] = '456'
        self.assertEqual(self.apply(self.stage([new]))['linked'], 1)
        m = self.client.get('/api/models').json[0]
        self.assertEqual(m['title'], 'My title')
        self.assertEqual(m['tags'], ['mine'])
        self.assertEqual(m['status'], 'Printed')
        self.assertEqual(m['collections'], ['Gifts', 'Workshop'])

    def test_identity_variants_and_existing_url_match(self):
        self.assertEqual(
            identity('https://thangs.com/m/123', 'thangs')[0],
            identity('https://thangs.com/designer/m/3d-model/Model-123', 'thangs')[0],
        )
        self.assertEqual(
            identity('https://cults3d.com/fr/3d-model/home/tray', 'cults3d')[0],
            identity('https://cults3d.com/en/3d-model/home/tray', 'cults3d')[0],
        )
        r = self.client.post(
            '/api/models',
            json={'title': 'Local', 'source_url': 'https://www.printables.com/model/123-tray'},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(
            self.apply(self.stage([self.item('printables', 'https://printables.com/model/123')]))['linked'], 1
        )

    def test_invalid_and_cross_provider_links_rejected(self):
        for provider, url in [
            ('printables', 'https://evil.example/model/123'),
            ('cults3d', 'https://cults3d.com/en/users/me'),
            ('thangs', 'https://thangs.com/designer/me'),
            ('printables', 'https://printables.com@evil.example/model/123'),
            ('printables', 'http://printables.com/model/123'),
        ]:
            with self.assertRaises(ValueError):
                normalize_item(self.item(provider, url))
        bad = self.item('unknown', 'https://evil.example/model/123')
        r = self.client.post(
            '/api/makerworld/browser-batch', json=dict(format='forma-websites-v1', items=[bad]), headers=self.headers
        )
        self.assertEqual(r.status_code, 400)

    def test_bookmark_injects_only_this_forma_origin(self):
        r = self.client.get('/api/websites/bookmarklet')
        self.assertEqual(r.status_code, 200)
        script = unquote(r.json['url'])
        self.assertIn('http://localhost', script)
        self.assertNotIn('__FORMA_ORIGIN__', script)
        self.assertIn('forma-websites-v1', script)


if __name__ == '__main__':
    unittest.main()
