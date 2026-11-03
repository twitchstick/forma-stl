import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import trimesh
from app import create_app
from model_details import cached_details


class CatalogToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.app=create_app(self.root/'data',self.root/'library');self.c=self.app.test_client();self.h={'X-Forma-Request':'1'}
        self.model=self.c.post('/api/models',json={'title':'Test box'},headers=self.h).json

    def tearDown(self):
        self.app.extensions['source_imports'].executor.shutdown(wait=True)
        self.app.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True);self.temp.cleanup()

    def printer(self):
        r=self.c.post('/api/printers',json={'name':'Workshop','width':220,'depth':200,'height':250,'nozzle':0.4,'notes':'PEI'},headers=self.h)
        self.assertEqual(r.status_code,201,r.json);return r.json['id']

    def upload(self,body,name):
        r=self.c.post('/api/models/'+self.model['id']+'/files',data={'files':(io.BytesIO(body),name)},headers=self.h)
        self.assertEqual(r.status_code,200,r.json)
        return next(m for m in self.c.get('/api/models').json if m['id']==self.model['id'])['files'][-1]['id']

    def test_saved_views_persist_all_rules_without_changing_catalog(self):
        rules=dict(search='desk',tag='gift',source='printables.com',status='To print',view='collection',collection='Home',sort='az',list_mode=True)
        r=self.c.post('/api/saved-views',json={'name':'Desk gifts','rules':rules},headers=self.h);self.assertEqual(r.status_code,201,r.json)
        id=r.json['id'];view=self.c.get('/api/saved-views').json[0];self.assertEqual(view['rules'],rules)
        self.assertEqual(self.c.patch('/api/saved-views/'+id,json={'name':'Ideas'},headers=self.h).status_code,200)
        self.assertEqual(self.c.get('/api/saved-views').json[0]['rules'],rules)
        self.assertEqual(self.c.get('/api/models').json[0]['title'],'Test box')
        with self.app.extensions['library_services'].db() as c:self.assertEqual(c.execute('SELECT count(*) FROM saved_views').fetchone()[0],1)
        self.assertEqual(self.c.delete('/api/saved-views/'+id,headers=self.h).status_code,200)
        rules['view']='bad';self.assertEqual(self.c.post('/api/saved-views',json={'name':'Bad','rules':rules},headers=self.h).status_code,400)

    def test_printer_validation_and_history_survive_printer_removal(self):
        id=self.printer();payload={'printer_id':id,'printed_on':'2026-11-03','filament':'PLA','settings':'0.2 mm · 15% infill','result':'Success','notes':'Good fit'}
        r=self.c.post('/api/models/'+self.model['id']+'/prints',json=payload,headers=self.h);self.assertEqual(r.status_code,201,r.json)
        for value in [0,-5,'NaN','Infinity',True,10001]:
            bad=dict(name='Bad',width=value,depth=200,height=200)
            self.assertEqual(self.c.post('/api/printers',json=bad,headers=self.h).status_code,400)
        self.assertEqual(self.c.patch('/api/printers/'+id,json=dict(name='Renamed',width=250,depth=250,height=250),headers=self.h).status_code,200)
        self.assertEqual(self.c.delete('/api/printers/'+id,headers=self.h).status_code,200)
        log=self.c.get('/api/models/'+self.model['id']+'/prints').json[0];self.assertIsNone(log['printer_id']);self.assertEqual(log['printer_name'],'Workshop')
        self.assertEqual(log['settings'],payload['settings'])
        self.assertEqual(next(m for m in self.c.get('/api/models').json if m['id']==self.model['id'])['status'],'Saved')
        self.assertEqual(self.c.delete('/api/models/'+self.model['id']+'/prints/'+r.json['id'],headers=self.h).status_code,200)

    def test_log_validation_does_not_allow_other_models_photos(self):
        other=self.c.post('/api/models',json={'title':'Other'},headers=self.h).json
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(10,10),'red').save(image,'PNG')
        self.c.post('/api/models/'+other['id']+'/files',data={'files':(io.BytesIO(image.getvalue()),'photo.png')},headers=self.h)
        photo=next(m for m in self.c.get('/api/models').json if m['id']==other['id'])['files'][0]['id']
        p=dict(printed_on='2026-11-03',result='Success',photo_id=photo)
        self.assertEqual(self.c.post('/api/models/'+self.model['id']+'/prints',json=p,headers=self.h).status_code,400)
        p.pop('photo_id');p['printed_on']='2026-02-31';self.assertEqual(self.c.post('/api/models/'+self.model['id']+'/prints',json=p,headers=self.h).status_code,400)

    def test_stl_measurements_labels_and_cache_invalidation(self):
        path=self.root/'box.stl';trimesh.creation.box(extents=[10,20,30]).export(path)
        id=self.upload(path.read_bytes(),'box.stl')
        r=self.c.get('/api/models/'+self.model['id']+'/details');self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['files'][0]['entries'][0]['dimensions_mm'],[10,20,30])
        self.assertIn('assumed',r.json['files'][0]['entries'][0]['note'])
        self.assertEqual(self.c.patch('/api/files/'+id+'/label',json={'label':'Lid · test variant'},headers=self.h).status_code,200)
        self.assertEqual(self.c.get('/api/models/'+self.model['id']+'/details').json['files'][0]['variant_label'],'Lid · test variant')
        cache=self.root/'cache';first=cached_details(path,cache);trimesh.creation.box(extents=[30,20,10]).export(path)
        second=cached_details(path,cache);self.assertNotEqual(first['entries'][0]['dimensions_mm'],second['entries'][0]['dimensions_mm'])

    def test_3mf_units_transforms_settings_and_zip_variants(self):
        mesh=trimesh.creation.box(extents=[1,2,3]);scene=trimesh.Scene(mesh);scene.units='inch';body=scene.export(file_type='3mf')
        # The exporter writes mm; use an explicit centimeter declaration to exercise normalization.
        original=zipfile.ZipFile(io.BytesIO(body));out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for name in original.namelist():
                content=original.read(name)
                if name.lower().endswith('.model'):content=content.replace(b'unit="millimeter"',b'unit="centimeter"')
                z.writestr(name,content)
            z.writestr('Metadata/project_settings.config',json.dumps({'layer_height':'0.2','sparse_infill_density':'15%','private_token':'never show'}))
        id=self.upload(out.getvalue(),'project.3mf')
        entry=self.c.get('/api/models/'+self.model['id']+'/details').json['files'][0]['entries'][0]
        self.assertEqual(entry['dimensions_mm'],[10,20,30]);self.assertEqual(entry['settings']['layer_height'],'0.2');self.assertNotIn('private_token',entry['settings'])
        archive=self.root/'variants.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('../outside.stl',mesh.export(file_type='stl'));z.writestr('variant.3mf',out.getvalue())
        result=cached_details(archive,self.root/'cache');self.assertEqual(len(result['entries']),2);self.assertFalse((self.root.parent/'outside.stl').exists())

    def test_missing_unsupported_and_xml_entities_have_clear_errors(self):
        path=self.root/'bad.3mf'
        with zipfile.ZipFile(path,'w') as z:z.writestr('3D/3dmodel.model','<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><model/>')
        self.assertIsNone(cached_details(path,self.root/'cache')['entries'][0]['dimensions_mm'])
        step=self.root/'model.step';step.write_text('dummy')
        self.assertIn('STEP',cached_details(step,self.root/'cache')['error'])
        self.assertEqual(self.c.get('/api/models/missing/details').status_code,404)
        self.assertEqual(self.c.post('/api/printers',json={},headers={}).status_code,403)

if __name__=='__main__':unittest.main()
