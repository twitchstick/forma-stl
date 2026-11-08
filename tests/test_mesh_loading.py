import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import trimesh

import model_details
import previews

CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PRODUCTION = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'


def cube_mesh_xml(object_id, size=10):
    box = trimesh.creation.box((size, size, size))
    box.apply_translation(box.extents / 2)  # corner at the origin
    vertices = ''.join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in box.vertices)
    triangles = ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in box.faces)
    return f'<object id="{object_id}" type="model"><mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object>'


def write_3mf(parts):
    body = io.BytesIO()
    with zipfile.ZipFile(body, 'w') as z:
        for name, xml in parts.items():
            z.writestr(name, xml)
    return body.getvalue()


class MeshLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, name, body):
        path = self.root / name
        path.write_bytes(body)
        return path

    def test_binary_stl_reader_matches_trimesh(self):
        mesh = trimesh.creation.icosphere(subdivisions=3)
        path = self.save('ball.stl', mesh.export(file_type='stl'))
        vertices, faces = previews.read_binary_stl(path)
        self.assertEqual((len(vertices), len(faces)), (len(mesh.vertices), len(mesh.faces)))
        self.assertTrue(np.allclose(vertices.max(axis=0) - vertices.min(axis=0), mesh.extents, atol=1e-5))
        same = previews.read_binary_stl(path.read_bytes())
        self.assertEqual(len(same[1]), len(faces))

    def test_ascii_stl_uses_the_general_loader(self):
        path = self.save('box.stl', trimesh.creation.box((4, 5, 6)).export(file_type='stl_ascii').encode())
        self.assertIsNone(previews.read_binary_stl(path))
        vertices, faces = previews.mesh_arrays(path, 'stl')
        self.assertEqual((vertices.max(axis=0) - vertices.min(axis=0)).round(3).tolist(), [4, 5, 6])

    def test_3mf_reader_matches_trimesh(self):
        mesh = trimesh.creation.icosphere(subdivisions=3)
        mesh.apply_scale(25)
        path = self.save('ball.3mf', mesh.export(file_type='3mf'))
        vertices, faces = previews.read_3mf(path)
        self.assertEqual(len(faces), len(mesh.faces))
        self.assertTrue(np.allclose(vertices.max(axis=0) - vertices.min(axis=0), mesh.extents, atol=1e-3))

    def test_3mf_parts_in_separate_files_with_transforms_and_units(self):
        # Bambu Studio style: the build places an object whose parts live in another file.
        part = f'<?xml version="1.0"?><model xmlns="{CORE}" unit="centimeter"><resources>{cube_mesh_xml(1)}</resources><build/></model>'
        root = (
            f'<?xml version="1.0"?><model xmlns="{CORE}" xmlns:p="{PRODUCTION}" unit="centimeter"><resources>'
            '<object id="5" type="model"><components>'
            '<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
            '<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 20 0 0"/>'
            '</components></object></resources>'
            '<build><item objectid="5" transform="1 0 0 0 1 0 0 0 1 0 0 5"/></build></model>'
        )
        body = write_3mf({'3D/3dmodel.model': root, '3D/Objects/object_1.model': part})
        vertices, faces = previews.read_3mf(body)
        self.assertEqual(len(faces), 24)  # two cubes
        # Units: centimetres become millimetres. Two 10 cm cubes, 20 cm apart, lifted 5 cm.
        self.assertEqual(vertices.min(axis=0).round(3).tolist(), [0, 0, 50])
        self.assertEqual(vertices.max(axis=0).round(3).tolist(), [300, 100, 150])
        details = model_details.inspect_mesh(body, '.3mf')
        self.assertEqual(details['dimensions_mm'], [300, 100, 100])

    def test_3mf_reader_refuses_document_type_declarations(self):
        xml = f'<?xml version="1.0"?><!DOCTYPE model [<!ENTITY a "aaaa">]><model xmlns="{CORE}"><resources>{cube_mesh_xml(1)}</resources><build><item objectid="1"/></build></model>'
        with self.assertRaisesRegex(ValueError, 'document type'):
            previews.read_3mf(write_3mf({'3D/3dmodel.model': xml}))

    def test_large_meshes_are_simplified_and_keep_their_shape(self):
        mesh = trimesh.creation.icosphere(subdivisions=7)  # 327,680 faces
        mesh.apply_scale(50)
        vertices, faces = previews.simplified(mesh.vertices, mesh.faces, 20_000)
        self.assertLessEqual(len(faces), 20_000)
        self.assertGreater(len(faces), 10_000)
        extents = np.asarray(vertices).max(axis=0) - np.asarray(vertices).min(axis=0)
        self.assertTrue(np.allclose(extents, mesh.extents, rtol=0.02))

    def test_binary_stl_measurements_skip_loading_the_mesh(self):
        path = self.save('box.stl', trimesh.creation.box((12, 3, 7)).export(file_type='stl'))
        result = model_details.analyze(path)
        self.assertEqual(result['entries'][0]['dimensions_mm'], [12, 3, 7])


if __name__ == '__main__':
    unittest.main()
