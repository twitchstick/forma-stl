import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from app import create_app
from collection_schedule import next_daily


class CollectionScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root / 'data', self.root / 'library')
        self.c = self.app.test_client()
        self.h = {'X-Forma-Request': '1'}
        self.service = self.app.extensions['collection_schedules']
        self.service.now = lambda: '2026-11-03T12:00:00+00:00'
        self.hits = [{'id': 123, 'title': 'Tray'}, {'id': 456, 'title': 'Stand'}]
        self.calls = []

        def fetch(url, limit=3000000):
            self.calls.append(url)
            return json.dumps({'hits': self.hits, 'total': len(self.hits)}).encode(), 'application/json', url

        self.service.fetch_public = fetch
        self.app.extensions['source_imports'].fetch_public = lambda *args: (_ for _ in ()).throw(
            ValueError('No network in unit tests')
        )

    def tearDown(self):
        self.service.stop.set()
        self.service.executor.shutdown(wait=True)
        self.app.extensions['source_imports'].executor.shutdown(wait=True)
        self.app.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True)
        self.temp.cleanup()

    def add(self, url='https://makerworld.com/en/collections/10', name='Workshop'):
        r = self.c.post('/api/collection-schedule/collections', json={'name': name, 'url': url}, headers=self.h)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json['id']

    def check(self, id):
        r = self.c.post('/api/collection-schedule/check', json={'id': id}, headers=self.h)
        self.assertEqual(r.status_code, 202, r.json)
        self.service.executor.shutdown(wait=True)
        self.service.executor = ThreadPoolExecutor(max_workers=1)
        return next(t for t in self.c.get('/api/collection-schedule').json['collections'] if t['id'] == id)

    def approve(self, id):
        batch = self.c.get('/api/makerworld/batches/' + id).json
        r = self.c.post(
            '/api/makerworld/batches/' + id + '/apply', json={'ids': [m['id'] for m in batch['items']]}, headers=self.h
        )
        self.assertEqual(r.status_code, 200, r.json)
        self.app.extensions['source_imports'].executor.shutdown(wait=True)
        self.app.extensions['source_imports'].executor = ThreadPoolExecutor(max_workers=2)

    def test_daily_defaults_and_dst_calendar_calculation(self):
        s = self.c.get('/api/collection-schedule').json
        self.assertTrue(s['enabled'])
        self.assertEqual(s['time'], '04:00')
        self.assertEqual(s['timezone'], 'America/New_York')
        # 4am shifts by an hour in UTC across the spring DST boundary.
        self.assertEqual(
            next_daily(datetime(2026, 3, 7, 12, tzinfo=timezone.utc), '04:00', 'America/New_York'),
            '2026-03-08T08:00:00+00:00',
        )
        self.assertEqual(
            next_daily(datetime(2026, 10, 31, 12, tzinfo=timezone.utc), '04:00', 'America/New_York'),
            '2026-11-01T09:00:00+00:00',
        )
        self.assertEqual(
            next_daily(datetime(2026, 3, 8, 6, tzinfo=timezone.utc), '02:30', 'America/New_York'),
            '2026-03-08T07:30:00+00:00',
        )
        for bad in [('25:00', 'UTC'), ('04:00', 'No/SuchZone')]:
            self.assertEqual(
                self.c.patch(
                    '/api/collection-schedule', json=dict(enabled=True, time=bad[0], timezone=bad[1]), headers=self.h
                ).status_code,
                400,
            )

    def test_new_models_wait_for_review_and_do_not_repeat(self):
        id = self.add()
        row = self.check(id)
        self.assertEqual(row['state'], 'checked')
        self.assertEqual(row['new_count'], 2)
        self.assertEqual(self.c.get('/api/models').json, [])
        batches = self.c.get('/api/makerworld/batches').json['batches']
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]['state'], 'pending')
        self.check(id)
        self.assertEqual(len(self.c.get('/api/makerworld/batches').json['batches']), 1)
        self.approve(row['last_batch_id'])
        self.assertEqual(len(self.c.get('/api/models').json), 2)
        # Removed source entries remain local and produce an advisory on complete reads.
        self.hits = self.hits[1:]
        row = self.check(id)
        self.assertEqual(row['removed_count'], 1)
        self.assertEqual(len(self.c.get('/api/models').json), 2)

    def test_existing_imports_are_baselined_automatically(self):
        payload = {
            'format': 'forma-makerworld-v1',
            'items': [{'id': '123', 'title': 'My tray', 'collections': [{'id': '10', 'name': 'Workshop'}]}],
        }
        batch = self.c.post('/api/makerworld/browser-batch', json=payload, headers=self.h).json['id']
        self.approve(batch)
        tracks = self.c.get('/api/collection-schedule').json['collections']
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]['url'], 'https://makerworld.com/en/collections/10')
        row = self.check(tracks[0]['id'])
        self.assertEqual(row['new_count'], 1)
        detail = self.c.get('/api/makerworld/batches/' + row['last_batch_id']).json
        self.assertEqual([m['id'] for m in detail['items']], ['456'])

    def test_due_check_catches_up_once_and_pause_is_persistent(self):
        id = self.add()
        with self.service.db() as c:
            c.execute('UPDATE tracked_collections SET next_check=? WHERE id=?', ('2026-11-01T09:00:00+00:00', id))
        self.service.tick()
        self.service.executor.shutdown(wait=True)
        self.service.executor = ThreadPoolExecutor(max_workers=1)
        self.service.tick()
        self.assertEqual(len(self.calls), 1)
        r = self.c.patch(
            '/api/collection-schedule',
            json=dict(enabled=False, time='05:15', timezone='America/Chicago'),
            headers=self.h,
        )
        self.assertEqual(r.status_code, 200)
        with self.service.db() as c:
            c.execute('UPDATE tracked_collections SET next_check=? WHERE id=?', ('2026-11-01T09:00:00+00:00', id))
        self.service.tick()
        self.assertEqual(len(self.calls), 1)
        s = self.c.get('/api/collection-schedule').json
        self.assertFalse(s['enabled'])
        self.assertEqual(s['time'], '05:15')

    def test_private_or_blocked_collection_is_not_reported_current(self):
        id = self.add()
        self.service.fetch_public = lambda *args: (_ for _ in ()).throw(ValueError('Blocked'))
        row = self.check(id)
        self.assertEqual(row['state'], 'needs_browser')
        self.assertEqual(row['last_success'], '')
        self.assertEqual(self.c.get('/api/makerworld/batches').json['batches'], [])
        with self.service.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM collection_seen').fetchone()[0], 0)

    def test_public_generic_capture_is_partial_and_retains_source_url(self):
        id = self.add('https://www.printables.com/collection/12-tools')
        self.service.fetch_public = lambda url, limit=3000000: (
            b'<h1>Tools</h1><a href="/model/123-tray"><img alt="Tray" src="https://media.printables.com/tray.png"></a>',
            'text/html',
            url,
        )
        row = self.check(id)
        self.assertEqual(row['state'], 'partial')
        self.assertEqual(row['removed_count'], 0)
        b = self.c.get('/api/makerworld/batches/' + row['last_batch_id']).json
        m = b['items'][0]
        self.assertEqual(m['provider'], 'printables')
        self.assertEqual(m['collections'][0]['source_url'], 'https://printables.com/collection/12-tools')
        self.approve(row['last_batch_id'])
        self.assertIn('Workshop', self.c.get('/api/models').json[0]['collections'])

    def test_legacy_generic_membership_requires_link_and_accepts_it_once(self):
        payload = {
            'format': 'forma-websites-v1',
            'items': [
                {
                    'provider': 'printables',
                    'source_url': 'https://printables.com/model/123',
                    'title': 'Tray',
                    'collections': [{'id': 'old-id', 'name': 'Old tools'}],
                }
            ],
        }
        batch = self.c.post('/api/makerworld/browser-batch', json=payload, headers=self.h).json['id']
        self.approve(batch)
        track = self.c.get('/api/collection-schedule').json['collections'][0]
        self.assertEqual(track['state'], 'needs_link')
        self.assertEqual(track['url'], '')
        r = self.c.patch(
            '/api/collection-schedule/collections/' + track['id'],
            json={'url': 'https://printables.com/collection/12-tools'},
            headers=self.h,
        )
        self.assertEqual(r.status_code, 200)
        r = self.c.patch('/api/collection-schedule/collections/' + track['id'], json={'enabled': False}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.c.get('/api/collection-schedule').json['collections'][0]['enabled'])
        for url in [
            'http://127.0.0.1/collections/1',
            'https://evil.example/collections/1',
            'https://printables.com/model/1',
            'https://printables.com@evil.example/collection/1',
        ]:
            self.assertEqual(
                self.c.post(
                    '/api/collection-schedule/collections', json={'name': 'Bad', 'url': url}, headers=self.h
                ).status_code,
                400,
            )

    def test_pagination_reads_all_pages_and_repeated_pages_stay_partial(self):
        id = self.add()
        from urllib.parse import urlsplit, parse_qs

        hits = [{'id': i, 'title': 'Model ' + str(i)} for i in range(1, 43)]

        def paged(url, limit=3000000):
            offset = int(parse_qs(urlsplit(url).query)['offset'][0])
            self.calls.append(offset)
            return json.dumps({'hits': hits[offset : offset + 20], 'total': 42}).encode(), 'application/json', url

        self.service.fetch_public = paged
        row = self.check(id)
        self.assertEqual(row['new_count'], 42)
        self.assertEqual(self.calls, [0, 20, 40])
        self.service.fetch_public = lambda url, limit=3000000: (
            json.dumps({'hits': hits[:20], 'total': 42}).encode(),
            'application/json',
            url,
        )
        row = self.check(id)
        self.assertEqual(row['state'], 'partial')
        self.assertEqual(row['removed_count'], 0)

    def test_repeated_check_requests_do_not_overlap(self):
        id = self.add()
        entered = threading.Event()
        release = threading.Event()

        def fetch(url, limit=3000000):
            entered.set()
            release.wait(3)
            return b'{"hits":[]}', 'application/json', url

        self.service.fetch_public = fetch
        self.assertTrue(self.service.queue(id))
        self.assertTrue(entered.wait(2))
        self.assertFalse(self.service.queue(id))
        release.set()
        self.service.executor.shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
