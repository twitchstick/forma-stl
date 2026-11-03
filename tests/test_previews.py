import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image
import trimesh
from app import create_app
from previews import cached_preview, generate_preview


def png(color):
    body = io.BytesIO()
    Image.new('RGB',(200,150),color).save(body,'PNG')
    return body.getvalue()


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_3mf_prefers_creator_picture_over_build_plate(self):
        model = self.root/'project.3mf'
        with zipfile.ZipFile(model,'w') as z:
            z.writestr('Metadata/plate_1.png',png('blue'))
            z.writestr('Auxiliaries/.thumbnails/thumbnail_3mf.png',png('red'))
        body = generate_preview(model)
        image = Image.open(io.BytesIO(body))
        self.assertEqual(image.getpixel((50,50)),(255,0,0))

    def test_stl_software_render_produces_model_picture(self):
        model = self.root/'cube.stl'
        trimesh.creation.box(extents=(10,10,20)).export(model)
        image = Image.open(io.BytesIO(generate_preview(model)))
        self.assertEqual(image.size,(600,450))
        self.assertGreater(len(image.getcolors(1000000)),20)
        self.assertNotEqual(image.getpixel((300,225)),image.getpixel((0,0)))

    def test_zip_nested_3mf_preserves_embedded_photo(self):
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w') as z:
            z.writestr('Metadata/thumbnail.png',png('green'))
        model = self.root/'download.zip'
        with zipfile.ZipFile(model,'w') as z:
            z.writestr('../project.3mf',inner.getvalue())
        image = Image.open(io.BytesIO(generate_preview(model)))
        self.assertEqual(image.getpixel((0,0)),(0,128,0))
        self.assertFalse((self.root.parent/'project.3mf').exists())

    def test_cache_reuses_and_invalidates_on_source_change(self):
        model = self.root/'project.3mf'
        with zipfile.ZipFile(model,'w') as z:
            z.writestr('Metadata/thumbnail.png',png('red'))
        cache = self.root/'cache'
        first = cached_preview(model,cache)
        self.assertEqual(first,cached_preview(model,cache))
        stat=model.stat()
        with zipfile.ZipFile(model,'w') as z:
            z.writestr('Metadata/thumbnail.png',png('blue'))
        os.utime(model,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
        self.assertNotEqual(first,cached_preview(model,cache))

    def test_existing_catalog_entry_automatically_gets_preview(self):
        library=self.root/'library'; library.mkdir()
        folder=library/'My model'; folder.mkdir()
        with zipfile.ZipFile(folder/'project.3mf','w') as z:
            z.writestr('Auxiliaries/.thumbnails/thumbnail_3mf.png',png('red'))
        client=create_app(self.root/'data',library).test_client()
        client.post('/api/scan',json={'keys':['My model']},headers={'X-Forma-Request':'1'})
        model=client.get('/api/models').json[0]
        self.assertEqual(model['cover_id'],'')
        with client.get('/api/models/'+model['id']+'/thumbnail') as response:
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.mimetype,'image/png')
            self.assertTrue(response.data.startswith(b'\x89PNG'))
        self.assertEqual(client.get('/api/models').json[0]['cover_id'],'')


if __name__=='__main__':
    unittest.main()
