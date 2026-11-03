import io
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import create_app


class MakerWorldTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.library=self.root/'library';self.library.mkdir()
        self.app=create_app(self.root/'data',self.library);self.client=self.app.test_client()
        self.h={'X-Forma-Request':'1'}
        code=self.client.post('/api/makerworld/pair-code',json={},headers=self.h).json['code']
        result=self.client.post('/api/companion/connect',json={'code':code,'name':'Test browser'})
        self.assertEqual(result.status_code,201,result.json)
        self.device=result.json['device_id'];self.token=result.json['token']
        self.auth={'Authorization':'Bearer '+self.token,'Origin':'chrome-extension://abcdefghijklmnopqrstuvwxabcdefgh'}

    def tearDown(self):
        self.app.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True)
        self.temp.cleanup()

    def item(self,id='123',**kw):
        return {'id':id,'title':'Desk tray','creator':'Designer','tags':['desk'],
                'collections':[{'id':'10','name':'Home'},{'id':'20','name':'Gifts'}],**kw}

    def stage(self,items=None,finish=True):
        r=self.client.post('/api/companion/batches',json={'items':items or [self.item()]},headers=self.auth)
        self.assertEqual(r.status_code,201,r.json)
        if finish:self.finish(r.json['id'])
        return r.json['id']

    def finish(self,id):
        r=self.client.post('/api/companion/batches/'+id+'/finish',json={'warnings':[]},headers=self.auth)
        self.assertEqual(r.status_code,200,r.json)

    def apply(self,id,ids=['123']):
        return self.client.post('/api/makerworld/batches/'+id+'/apply',json={'ids':ids},headers=self.h)

    def test_pairing_codes_are_one_use_and_hashes_only_are_stored(self):
        code=self.client.post('/api/makerworld/pair-code',json={},headers=self.h).json['code']
        self.assertEqual(self.client.post('/api/companion/connect',json={'code':code}).status_code,201)
        self.assertEqual(self.client.post('/api/companion/connect',json={'code':code}).status_code,401)
        with sqlite3.connect(self.root/'data'/'forma.sqlite3') as c:
            self.assertNotIn(self.token,str(c.execute('SELECT token_hash FROM companion_devices').fetchall()))

    def test_expired_code_is_rejected(self):
        code=self.client.post('/api/makerworld/pair-code',json={},headers=self.h).json['code']
        with sqlite3.connect(self.root/'data'/'forma.sqlite3') as c:c.execute('UPDATE companion_codes SET expires=0')
        self.assertEqual(self.client.post('/api/companion/connect',json={'code':code}).status_code,401)

    def test_companion_token_cannot_read_or_edit_catalog_or_approve(self):
        batch=self.stage()
        with patch.dict(os.environ,{'FORMA_PASSWORD':'secret'}):
            self.assertEqual(self.client.get('/api/models',headers=self.auth).status_code,401)
            self.assertEqual(self.client.post('/api/makerworld/batches/'+batch+'/apply',json={'ids':['123']},headers=self.auth).status_code,401)
            self.assertEqual(self.client.post('/api/models',json={'title':'Bad'},headers=self.auth).status_code,401)
            self.assertEqual(self.client.get('/api/companion/batches/'+batch,headers=self.auth).status_code,200)

    def test_untrusted_origins_and_unpaired_uploads_are_rejected(self):
        r=self.client.post('/api/companion/batches',json={'items':[self.item()]})
        self.assertEqual(r.status_code,401)
        r=self.client.post('/api/companion/batches',json={'items':[self.item()]},headers={**self.auth,'Origin':'https://evil.test'})
        self.assertEqual(r.status_code,403)
        r=self.client.options('/api/companion/batches',headers={'Origin':self.auth['Origin']})
        self.assertEqual(r.status_code,204);self.assertEqual(r.headers['Access-Control-Allow-Origin'],self.auth['Origin'])

    def test_staging_does_not_import_until_user_approves(self):
        batch=self.stage()
        self.assertEqual(self.client.get('/api/models').json,[])
        r=self.apply(batch);self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['created'],1)
        model=self.client.get('/api/models').json[0]
        self.assertEqual(model['collections'],['Gifts','Home'])
        self.assertEqual(model['source_url'],'https://makerworld.com/en/models/123')
        self.assertEqual(self.apply(batch).status_code,400)

    def test_reimport_keeps_edits_and_adds_memberships(self):
        self.apply(self.stage())
        m=self.client.get('/api/models').json[0]
        self.client.patch('/api/models/'+m['id'],json={'title':'My edited title','description':'My notes','status':'Printed','tags':['personal']},headers=self.h)
        batch=self.stage([self.item(collections=[{'id':'30','name':'Desk'}])])
        r=self.apply(batch);self.assertEqual(r.json['created'],0);self.assertEqual(r.json['linked'],1)
        models=self.client.get('/api/models').json;self.assertEqual(len(models),1)
        self.assertEqual(models[0]['title'],'My edited title');self.assertEqual(models[0]['tags'],['personal'])
        self.assertEqual(models[0]['description'],'My notes');self.assertEqual(models[0]['status'],'Printed')
        self.assertEqual(models[0]['collections'],['Desk','Gifts','Home'])

    def test_existing_source_url_is_matched_by_numeric_model_id(self):
        old=self.client.post('/api/models',json={'title':'Existing','source_url':'https://makerworld.com/en/models/123-desk-tray?from=share'},headers=self.h).json
        r=self.apply(self.stage());self.assertEqual(r.json['created'],0)
        self.assertEqual(self.client.get('/api/models').json[0]['id'],old['id'])

    def test_file_uploads_wait_in_staging_and_attach_on_approval(self):
        batch=self.stage(finish=False)
        r=self.client.post('/api/companion/batches/'+batch+'/files/123',data={'file':(io.BytesIO(b'model bytes'),'desk.3mf')},headers=self.auth)
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.apply(batch).status_code,400)
        self.finish(batch);r=self.apply(batch);self.assertEqual(r.json['files'],1)
        model=self.client.get('/api/models').json[0];self.assertEqual(len(model['files']),1)
        with self.client.get('/api/files/'+model['files'][0]['id']) as response:self.assertEqual(response.data,b'model bytes')
        self.assertFalse((self.root/'data'/'makerworld-staging'/batch).exists())

    def test_file_hash_matches_an_existing_local_model(self):
        old=self.client.post('/api/models',json={'title':'My local tray'},headers=self.h).json
        self.client.post('/api/models/'+old['id']+'/files',data={'files':(io.BytesIO(b'same model'),'different-name.3mf')},headers=self.h)
        batch=self.stage(finish=False)
        self.client.post('/api/companion/batches/'+batch+'/files/123',data={'file':(io.BytesIO(b'same model'),'source.3mf')},headers=self.auth)
        self.finish(batch)
        review=self.client.get('/api/makerworld/batches/'+batch).json
        self.assertEqual(review['items'][0]['existing_model_id'],old['id'])
        r=self.apply(batch);self.assertEqual(r.json['created'],0);self.assertEqual(r.json['files'],0)
        self.assertEqual(len(self.client.get('/api/models').json),1)
        model=self.client.get('/api/models').json[0]
        self.assertEqual(model['source_url'],'https://makerworld.com/en/models/123')
        self.assertEqual(model['creator'],'Designer')

    def test_revocation_and_device_isolation(self):
        batch=self.stage()
        code=self.client.post('/api/makerworld/pair-code',json={},headers=self.h).json['code']
        second=self.client.post('/api/companion/connect',json={'code':code}).json
        other={'Authorization':'Bearer '+second['token']}
        self.assertEqual(self.client.get('/api/companion/batches/'+batch,headers=other).status_code,404)
        self.client.delete('/api/makerworld/devices/'+self.device,headers=self.h)
        self.assertEqual(self.client.get('/api/companion/batches/'+batch,headers=self.auth).status_code,401)

    def test_unsupported_urls_and_unknown_model_files_rejected(self):
        r=self.client.post('/api/companion/batches',json={'items':[self.item(image_url='http://127.0.0.1/private')]},headers=self.auth)
        self.assertEqual(r.status_code,400)
        batch=self.stage(finish=False)
        r=self.client.post('/api/companion/batches/'+batch+'/files/999',data={'file':(io.BytesIO(b'bad'),'bad.3mf')},headers=self.auth)
        self.assertEqual(r.status_code,404)

    def test_duplicate_items_merge_collections_without_copying_credentials(self):
        batch=self.stage([self.item(collections=[{'id':'10','name':'Home'}],token='must-not-be-stored'),self.item(collections=[{'id':'20','name':'Gifts'}])])
        with sqlite3.connect(self.root/'data'/'forma.sqlite3') as c:
            payload=c.execute('SELECT payload FROM makerworld_batches WHERE id=?',(batch,)).fetchone()[0]
            self.assertNotIn('must-not-be-stored',payload)
        review=self.client.get('/api/makerworld/batches/'+batch).json
        self.assertEqual(len(review['items']),1);self.assertEqual(len(review['items'][0]['collections']),2)

    def test_companion_zip_contains_manifest_scripts_and_icon(self):
        with self.client.get('/api/makerworld/companion.zip') as response:
            self.assertEqual(response.status_code,200)
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                names=archive.namelist()
                self.assertIn('forma-makerworld-companion/manifest.json',names)
                self.assertIn('forma-makerworld-companion/icon.png',names)
                self.assertIn('forma-makerworld-companion/makerworld.js',names)

    def test_safari_json_waits_for_review_without_a_companion_token(self):
        r=self.client.post('/api/makerworld/browser-batch',json={'format':'forma-makerworld-v1','title':'Safari saves','items':[self.item()]},headers=self.h)
        self.assertEqual(r.status_code,201,r.json)
        self.assertEqual(self.client.get('/api/models').json,[])
        self.assertEqual(self.client.get('/api/makerworld/batches/'+r.json['id']).json['state'],'pending')
        self.assertEqual(self.apply(r.json['id']).json['created'],1)
        devices=self.client.get('/api/makerworld/devices').json['devices']
        self.assertFalse(any(d['name']=='Safari browser import' for d in devices))

    def test_safari_file_handoff_is_authenticated_and_staged(self):
        r=self.client.post('/api/makerworld/browser-batch',json={'format':'forma-makerworld-v1','has_files':True,'items':[self.item()]},headers=self.h)
        id=r.json['id'];self.assertEqual(self.apply(id).status_code,400)
        r=self.client.post('/api/makerworld/batches/'+id+'/files/123',data={'file':(io.BytesIO(b'3mf data'),'source.3mf')},headers=self.h)
        self.assertEqual(r.status_code,200,r.json)
        r=self.client.post('/api/makerworld/batches/'+id+'/finish',json={'warnings':['Test warning']},headers=self.h)
        self.assertEqual(r.status_code,200);self.assertEqual(self.apply(id).json['files'],1)
        with patch.dict(os.environ,{'FORMA_PASSWORD':'secret'}):
            self.assertEqual(self.client.post('/api/makerworld/browser-batch',json={'format':'forma-makerworld-v1','items':[self.item()]},headers=self.h).status_code,401)

    def test_bookmarklet_contains_server_address_but_no_auth_credentials(self):
        from urllib.parse import unquote
        r=self.client.get('/api/makerworld/bookmarklet')
        self.assertEqual(r.status_code,200)
        source=unquote(r.json['url'])
        self.assertTrue(source.startswith('javascript:'))
        self.assertIn('http://localhost',source)
        self.assertNotIn('__FORMA_ORIGIN__',source)
        self.assertNotIn(self.token,source)
        self.assertNotIn('document.cookie',source)


if __name__=='__main__':unittest.main()
