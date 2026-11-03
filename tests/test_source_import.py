import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import create_app
from source_metadata import parse_source

PAGE=b'''<title>Tray</title><meta property="og:image" content="/one.png"><meta property="og:image" content="/two.png"><script type="application/ld+json">{"@type":"Product","name":"Tray","author":{"name":"Maker"},"description":"Useful tray","license":{"name":"CC BY 4.0","url":"https://creativecommons.org/licenses/by/4.0/"},"creditText":"Tray by Maker","image":["/one.png","/three.png"],"keywords":"desk, useful"}</script>'''

def png(color):
    stream=io.BytesIO();Image.new('RGB',(20,20),color).save(stream,'PNG');return stream.getvalue()

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.app=create_app(self.root/'data',self.root/'library');self.c=self.app.test_client();self.h={'X-Forma-Request':'1'}
        self.m=self.c.post('/api/models',json={'title':'My tray','source_url':'https://example.com/model'},headers=self.h).json;self.service=self.app.extensions['source_imports']
        def fetch(url,limit=3000000):
            if url.endswith('/model'):return PAGE,'text/html',url
            return png('red' if url.endswith(('one.png','three.png')) else 'blue'),'image/png',url
        self.service.fetch_public=fetch

    def tearDown(self):
        self.service.executor.shutdown(wait=True);self.app.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True);self.temp.cleanup()

    def retry(self,seed=None):
        r=self.c.post('/api/models/'+self.m['id']+'/source-import',json=seed or {},headers=self.h);self.assertEqual(r.status_code,202,r.json)
        # Wait for all background work before checking the persisted status.
        self.service.executor.shutdown(wait=True)
        from concurrent.futures import ThreadPoolExecutor
        self.service.executor=ThreadPoolExecutor(max_workers=2)
        return self.c.get('/api/models/'+self.m['id']+'/source-import').json

    def current(self):return next(m for m in self.c.get('/api/models').json if m['id']==self.m['id'])

    def test_repeated_metadata_images_and_licenses_are_explicit(self):
        meta=parse_source(PAGE,'https://example.com/model');self.assertEqual(len(meta['image_urls']),3);self.assertEqual(meta['creator'],'Maker');self.assertEqual(meta['license'],'CC BY 4.0')
        meta=parse_source(b'<title>Unknown</title><img src="/ad.png">','https://example.com/model');self.assertEqual(meta['license'],'');self.assertEqual(meta['image_urls'],[])
        other=b'<title>Tray</title><script type="application/ld+json">[{"@type":"Product","name":"Advertisement","image":"/ad.png"},{"@type":"Product","name":"Tray","image":"/tray.png"}]</script>'
        self.assertEqual(parse_source(other,'https://example.com/model')['image_urls'],['https://example.com/tray.png'])

    def test_gallery_deduplication_cover_and_retries(self):
        result=self.retry();m=self.current();self.assertEqual(m['title'],'My tray');self.assertEqual(m['license'],'CC BY 4.0');self.assertEqual(len(m['files']),2);cover=m['cover_id']
        self.assertEqual(result['job']['state'],'complete');self.assertEqual(result['job']['done'],3);self.assertEqual(result['job']['total'],3)
        result=self.retry();self.assertEqual(len(self.current()['files']),2);self.assertEqual(self.current()['cover_id'],cover)
        self.assertTrue(all(p['state']=='imported' for p in result['photos']))

    def test_manual_edits_and_intentionally_cleared_fields_survive(self):
        self.retry()
        self.c.patch('/api/models/'+self.m['id'],json={'creator':'Me','license':'My terms','attribution':'','description':'','tags':[]},headers=self.h)
        self.retry();m=self.current();self.assertEqual(m['creator'],'Me');self.assertEqual(m['license'],'My terms');self.assertEqual(m['description'],'');self.assertEqual(m['attribution'],'');self.assertEqual(m['tags'],[])

    def test_partial_failure_remains_retryable_and_existing_photos_stay(self):
        original=self.service.fetch_public
        def flaky(url,limit=3000000):
            if url.endswith('two.png'):raise ValueError('Blocked')
            return original(url,limit)
        self.service.fetch_public=flaky;status=self.retry();self.assertEqual(status['job']['state'],'partial');self.assertEqual(len(self.current()['files']),1)
        self.assertEqual(sum(p['state']=='failed' for p in status['photos']),1)
        self.service.fetch_public=original;status=self.retry();self.assertEqual(status['job']['state'],'complete');self.assertEqual(len(self.current()['files']),2)

    def test_blocked_page_uses_browser_capture_and_shows_notes(self):
        def fetch(url,limit=3000000):
            if url.endswith('/model'):raise ValueError('Blocked')
            return png('red'),'image/png',url
        self.service.fetch_public=fetch;status=self.retry({'image_urls':['https://example.com/captured.png'],'license':'Creator terms','attribution':'Credit from browser'})
        self.assertEqual(len(self.current()['files']),1);self.assertEqual(self.current()['license'],'Creator terms');self.assertEqual(status['job']['state'],'partial')
        self.assertTrue(status['job']['notes'])

    def test_unsafe_images_and_license_links_are_not_followed(self):
        self.assertEqual(self.c.patch('/api/models/'+self.m['id'],json={'license_url':'javascript:alert(1)'},headers=self.h).status_code,400)
        self.service.fetch_public=lambda *args: (_ for _ in ()).throw(ValueError('blocked'))
        status=self.retry({'image_urls':['javascript:alert(1)','file:///etc/passwd']});self.assertEqual(status['photos'],[])
        # Actual network helper rejects private addresses before opening a socket.
        from app import fetch_public
        with self.assertRaises(ValueError):fetch_public('http://127.0.0.1/private')

    def test_metadata_api_exposes_all_photos_and_credit(self):
        with patch('app.fetch_public',return_value=(PAGE,'text/html','https://example.com/model')):
            r=self.c.post('/api/metadata',json={'url':'https://example.com/model'},headers=self.h)
        self.assertEqual(r.status_code,200);self.assertEqual(len(r.json['image_urls']),3);self.assertEqual(r.json['attribution'],'Tray by Maker')

if __name__=='__main__':unittest.main()
