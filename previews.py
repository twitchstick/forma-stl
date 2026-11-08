"""Cached previews: preserve embedded slicer images before rendering geometry.

Geometry work (thumbnails and 3D viewer meshes) runs through workers.run, so
it happens in a short-lived process with a memory budget, one job at a time.
"""

import hashlib
import io
import math
import os
import threading
import zipfile
from pathlib import Path

import workers

MAX_MODEL_BYTES = 256 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
PREVIEW_VERSION = 'v2'
RENDER_LOCK = threading.Lock()
MODEL_PARTS = ('.stl', '.obj', '.3mf')


def embedded_image(archive):
    """Read image bytes only; never extract paths or expand an entire archive."""
    candidates = []
    for item in archive.infolist():
        name = item.filename.lower()
        if item.file_size > MAX_IMAGE_BYTES or item.is_dir():
            continue
        if not name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            continue
        score = (
            0
            if name.endswith('thumbnail_3mf.png')
            else 1
            if name.endswith('thumbnail.png')
            else 2
            if name.endswith('thumbnail_middle.png')
            else 3
            if name.endswith('plate_1.png')
            else 4
            if 'thumbnail' in name
            else 5
            if 'preview' in name
            else 6
            if name.startswith('metadata/')
            else 7
        )
        # Exclude UV textures, which are not pictures of the model.
        if 'texture' in name or score == 7:
            continue
        candidates.append((score, item.filename, item))
    for _, _, item in sorted(candidates):
        body = archive.read(item)
        try:
            return normalize_image(body)
        except (ValueError, OSError):
            continue
    return None


def normalize_image(body):
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(body)) as original:
        if original.width * original.height > 32_000_000:
            raise ValueError('Embedded image is too large.')
        image = ImageOps.exif_transpose(original).convert('RGBA')
        image.thumbnail((720, 540))
        background = Image.new('RGBA', image.size, '#efeee7')
        background.alpha_composite(image)
        result = io.BytesIO()
        background.convert('RGB').save(result, format='PNG', optimize=True)
        return result.getvalue()


# ---------- Loading geometry ----------


def read_binary_stl(source):
    """Vertices and faces from a binary STL, or None if it is not one.

    Reads the triangles straight into numpy and welds shared corners, using a
    fraction of the memory a general-purpose loader needs for large files.
    source is a Path or the file's bytes.
    """
    import numpy as np

    from_bytes = isinstance(source, (bytes, bytearray))
    if from_bytes:
        header, size = bytes(source[:84]), len(source)
    else:
        with open(source, 'rb') as f:
            header = f.read(84)
        size = source.stat().st_size
    if len(header) < 84:
        return None
    count = int.from_bytes(header[80:84], 'little')
    if not count or size != 84 + count * 50:
        return None  # ASCII STL or a damaged file: use the general loader.
    record = np.dtype([('normal', '<f4', (3,)), ('corners', '<f4', (3, 3)), ('attribute', '<u2')])
    if from_bytes:
        records = np.frombuffer(source, dtype=record, count=count, offset=84)
    else:
        records = np.fromfile(source, dtype=record, count=count, offset=84)
    corners = records['corners'].reshape(-1, 3)
    del records
    vertices, inverse = np.unique(corners, axis=0, return_inverse=True)
    return vertices, inverse.reshape(-1, 3).astype(np.int32)


UNIT_MM = {'micron': 0.001, 'millimeter': 1.0, 'centimeter': 10.0, 'inch': 25.4, 'foot': 304.8, 'meter': 1000.0}


def _transform(text):
    """3MF 3x4 transform as (3x3 matrix, translation); points map as p @ m + t."""
    import numpy as np

    if not text:
        return None
    values = [float(v) for v in text.split()]
    if len(values) != 12:
        raise ValueError('Invalid 3MF transform.')
    return np.array(values[:9]).reshape(3, 3), np.array(values[9:])


def _compose(inner, outer):
    if inner is None:
        return outer
    if outer is None:
        return inner
    return inner[0] @ outer[0], inner[1] @ outer[0] + outer[1]


def read_3mf(source):
    """(vertices in mm, faces) for a whole 3MF build, read piece by piece.

    A general XML loader keeps the full document tree in memory, which for a
    large 3MF is many times the file size. This streams each model part into
    compact arrays instead, and follows components, separate part files
    (the 3MF production extension used by Bambu Studio and Orca) and placement
    transforms. Returns None when the file uses something it does not handle,
    so the caller can fall back to the general loader.
    """
    from array import array

    import numpy as np
    from lxml import etree

    archive = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
    with archive:
        names = {i.filename.lstrip('/'): i for i in archive.infolist()}
        root_name = next((n for n in names if n.lower() == '3d/3dmodel.model'), None)
        if root_name is None:
            return None
        objects, items, unit = {}, [], [1.0]

        def local(tag):
            return tag.rsplit('}', 1)[-1]

        def path_attr(element):
            return next((v.lstrip('/') for k, v in element.attrib.items() if local(k) == 'path'), None)

        def parse(name):
            if name not in names:
                raise ValueError('A 3MF part is missing.')
            with archive.open(names[name]) as f:
                if b'<!DOCTYPE' in f.read(4096).upper():
                    raise ValueError('XML document type declarations are not supported.')
            current, vertices, triangles, components = None, None, None, None
            with archive.open(names[name]) as f:
                context = etree.iterparse(
                    f, events=('start', 'end'), resolve_entities=False, no_network=True, huge_tree=True
                )
                for event, element in context:
                    tag = local(element.tag)
                    if event == 'start':
                        if tag == 'model' and name == root_name:
                            unit[0] = UNIT_MM.get(element.get('unit', 'millimeter'), 1.0)
                        elif tag == 'object':
                            current, vertices, triangles, components = element.get('id'), None, None, None
                        elif tag == 'mesh':
                            vertices, triangles = array('f'), array('i')
                        elif tag == 'components':
                            components = []
                        elif tag == 'component' and components is not None:
                            components.append(
                                (
                                    path_attr(element) or name,
                                    element.get('objectid'),
                                    _transform(element.get('transform')),
                                )
                            )
                        elif tag == 'item' and name == root_name:
                            items.append(
                                (
                                    path_attr(element) or name,
                                    element.get('objectid'),
                                    _transform(element.get('transform')),
                                )
                            )
                        continue
                    if tag == 'vertex':
                        vertices.extend((float(element.get('x')), float(element.get('y')), float(element.get('z'))))
                    elif tag == 'triangle':
                        triangles.extend((int(element.get('v1')), int(element.get('v2')), int(element.get('v3'))))
                    elif tag == 'object':
                        if vertices is not None:
                            objects[(name, current)] = ('mesh', vertices, triangles)
                        elif components is not None:
                            objects[(name, current)] = ('components', components)
                        current = None
                    # Drop finished elements so the tree never grows.
                    if tag in ('vertex', 'triangle', 'component', 'item', 'object'):
                        element.clear()
                        parent = element.getparent()
                        while parent is not None and element.getprevious() is not None:
                            del parent[0]

        parse(root_name)
        parsed = {root_name}
        if not items:
            items = [(n, i, None) for (n, i), o in objects.items() if n == root_name and o[0] == 'mesh']
        all_vertices, all_faces, offset = [], [], 0

        def emit(name, object_id, transform, depth=0):
            nonlocal offset
            if depth > 16:
                raise ValueError('3MF components nest too deeply.')
            if name not in parsed:
                parse(name)
                parsed.add(name)
            entry = objects.get((name, object_id))
            if entry is None:
                raise ValueError('A 3MF object is missing.')
            if entry[0] == 'components':
                for child_name, child_id, child_transform in entry[1]:
                    emit(child_name, child_id, _compose(child_transform, transform), depth + 1)
                return
            points = np.frombuffer(entry[1], dtype=np.float32).reshape(-1, 3).astype(np.float64)
            faces = np.frombuffer(entry[2], dtype=np.int32).reshape(-1, 3)
            if len(faces) and (faces.min() < 0 or faces.max() >= len(points)):
                raise ValueError('3MF triangle refers to a missing vertex.')
            if transform is not None:
                points = points @ transform[0] + transform[1]
            all_vertices.append(points.astype(np.float32))
            all_faces.append(faces + offset)
            offset += len(points)

        for name, object_id, transform in items:
            emit(name, object_id, transform)
    if not all_faces:
        raise ValueError('No triangle mesh found in this file.')
    vertices = np.concatenate(all_vertices)
    if unit[0] != 1.0:
        vertices *= unit[0]
    return vertices, np.concatenate(all_faces).astype(np.int32)


def mesh_arrays(source, file_type):
    """Welded (vertices, faces) from an STL, OBJ or 3MF path or bytes."""
    import numpy as np

    arrays = read_binary_stl(source) if file_type == 'stl' else None
    if file_type == '3mf':
        try:
            arrays = read_3mf(source)
        except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, SyntaxError, ValueError):
            arrays = None  # Let the general loader try, or report the problem.
    if arrays is None:
        import trimesh

        stream = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else str(source)
        geometry = trimesh.load(stream, file_type=file_type, force='mesh', process=False, skip_materials=True)
        if not isinstance(geometry, trimesh.Trimesh):
            raise ValueError('No triangle mesh found in this file.')
        # Weld before decimation: STL vertices are often disconnected triangles.
        geometry.merge_vertices()
        arrays = geometry.vertices, geometry.faces
        del geometry
    vertices, faces = arrays
    if not len(faces):
        raise ValueError('No triangle mesh found in this file.')
    if not np.isfinite(vertices).all():
        raise ValueError('Model contains invalid coordinates.')
    return vertices, faces


def clustered(vertices, faces, target_faces):
    """Cheap first pass for very large meshes: merge vertices that share a grid cell.

    Picks a cell size so that roughly target_faces triangles survive, using a
    fraction of the memory quadric simplification needs for millions of faces.
    """
    import numpy as np

    vertices = np.asarray(vertices, dtype=np.float32)
    low = vertices.min(axis=0)
    extent = float((vertices.max(axis=0) - low).max()) or 1.0
    target_vertices = max(target_faces // 2, 1000)
    # Surface meshes have roughly as many occupied cells as vertices; refine the cell size twice.
    cell = extent / np.sqrt(target_vertices)
    for _ in range(4):
        keys = np.floor((vertices - low) / cell).astype(np.int64)
        keys = (keys[:, 0] * 2_097_152 + keys[:, 1]) * 2_097_152 + keys[:, 2]
        cells, inverse = np.unique(keys, return_inverse=True)
        if len(cells) <= target_vertices * 1.3:
            break
        cell *= float(np.sqrt(len(cells) / target_vertices))
    inverse = inverse.astype(np.int32)
    counts = np.bincount(inverse, minlength=len(cells)).astype(np.float32)[:, None]
    merged = np.zeros((len(cells), 3), dtype=np.float32)
    np.add.at(merged, inverse, vertices)
    merged /= counts
    remapped = inverse[faces]
    keep = (remapped[:, 0] != remapped[:, 1]) & (remapped[:, 1] != remapped[:, 2]) & (remapped[:, 0] != remapped[:, 2])
    return merged, remapped[keep]


def simplified(vertices, faces, limit):
    """Reduce to at most `limit` triangles, keeping the shape."""
    import numpy as np

    if len(faces) <= limit:
        return vertices, faces
    import fast_simplification

    faces = np.asarray(faces, dtype=np.int32)
    if len(faces) > limit * 4:
        vertices, faces = clustered(vertices, faces, limit * 4)
    if len(faces) <= limit:
        return vertices, faces
    return fast_simplification.simplify(vertices, faces, target_count=limit)


def zip_model_parts(archive):
    return sorted(
        (
            i
            for i in archive.infolist()
            if Path(i.filename).suffix.lower() in MODEL_PARTS and not i.filename.startswith('__MACOSX/')
        ),
        key=lambda i: i.filename,
    )


def load_geometry(path):
    """Welded (vertices, faces) for an STL/OBJ/3MF file or the first usable model in a ZIP."""
    suffix = path.suffix.lower()
    if suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_MODEL_BYTES:
                raise ValueError('This file is too large to process.')
            for part in zip_model_parts(archive):
                try:
                    return mesh_arrays(archive.read(part), Path(part.filename).suffix.lower()[1:])
                except Exception:
                    continue
        raise ValueError('No supported model found in this ZIP.')
    if suffix == '.3mf':
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > MAX_MODEL_BYTES:
                raise ValueError('This file is too large to process.')
    elif path.stat().st_size > MAX_MODEL_BYTES:
        raise ValueError('This file is too large to process.')
    if suffix not in MODEL_PARTS:
        raise ValueError('Supported formats are STL, OBJ, 3MF and model ZIP files.')
    return mesh_arrays(path, suffix[1:])


# ---------- Thumbnails ----------


def render_mesh(vertices, faces):
    """Software orthographic render. No browser, GPU or display server needed."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    vertices, faces = simplified(vertices, faces, 60_000)
    triangles = np.asarray(vertices, dtype=np.float64)[np.asarray(faces)]
    vertices = triangles.reshape((-1, 3))
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    triangles -= center
    # Right, up and camera vectors form an isometric camera basis.
    right = np.array([1, -1, 0], dtype=float) / math.sqrt(2)
    camera = np.array([1, 1, 0.85], dtype=float)
    camera /= np.linalg.norm(camera)
    up = np.cross(camera, right)
    projected = np.stack((triangles @ right, -(triangles @ up)), axis=-1)
    low, high = projected.min(axis=(0, 1)), projected.max(axis=(0, 1))
    span = np.maximum(high - low, 1e-9)
    scale = min(560 / span[0], 390 / span[1])
    projected = (projected - (low + high) / 2) * scale + np.array([360, 255])
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    light = np.array([-0.4, -0.6, 1.0])
    light /= np.linalg.norm(light)
    shades = np.clip(0.58 + 0.42 * (normals @ light), 0.25, 1)
    colors = (np.array([198, 139, 108])[None, :] * shades[:, None]).astype(int)
    depth = (triangles @ camera).mean(axis=1)
    image = Image.new('RGB', (720, 540), '#efeee7')
    shadow = Image.new('RGBA', image.size)
    draw_shadow = ImageDraw.Draw(shadow)
    width = max(50, min(470, float(span[0] * scale) * 0.8))
    bottom = min(495, float(projected[:, :, 1].max()) + 12)
    draw_shadow.ellipse((360 - width / 2, bottom - 12, 360 + width / 2, bottom + 12), fill=(84, 76, 57, 40))
    image.paste(shadow.filter(ImageFilter.GaussianBlur(12)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(12)))
    draw = ImageDraw.Draw(image)
    for index in np.argsort(depth):
        points = [tuple(p) for p in projected[index]]
        draw.polygon(points, fill=tuple(colors[index]))
    image.thumbnail((600, 450), Image.Resampling.LANCZOS)
    result = io.BytesIO()
    image.save(result, format='PNG', optimize=True)
    return result.getvalue()


def generate_preview(path):
    """PNG thumbnail: an image embedded by the slicer if there is one, otherwise a render."""
    suffix = path.suffix.lower()
    if suffix in ('.3mf', '.zip'):
        with zipfile.ZipFile(path) as archive:
            image = embedded_image(archive)
            if image:
                return image
            if suffix == '.zip':
                for part in zip_model_parts(archive):
                    if Path(part.filename).suffix.lower() == '.3mf' and part.file_size <= MAX_MODEL_BYTES:
                        with zipfile.ZipFile(io.BytesIO(archive.read(part))) as nested:
                            image = embedded_image(nested)
                            if image:
                                return image
    elif suffix not in MODEL_PARTS:
        raise ValueError('Automatic previews support STL, OBJ, 3MF and model ZIP files.')
    return render_mesh(*load_geometry(path))


def cached_preview(path, cache):
    stat = path.stat()
    identity = f'{PREVIEW_VERSION}:{path}:{stat.st_size}:{stat.st_mtime_ns}'
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache.mkdir(exist_ok=True)
    destination = cache / (key + '.png')
    error_file = cache / (key + '.error')
    if destination.exists():
        return destination
    if error_file.exists():
        raise ValueError(error_file.read_text())
    # Avoid duplicate work for the same file; workers.run bounds memory.
    with RENDER_LOCK:
        if destination.exists():
            return destination
        if error_file.exists():
            raise ValueError(error_file.read_text())
        try:
            body = workers.run(generate_preview, path)
            temporary = destination.with_suffix('.tmp')
            temporary.write_bytes(body)
            os.replace(temporary, destination)
            return destination
        except Exception as exc:
            error_file.write_text('A preview could not be generated for this file. Add a cover photo using Edit model.')
            raise ValueError(error_file.read_text()) from exc


# ---------- Meshes for the in-browser 3D viewer ----------

MESH_VERSION = 'v2'
MESH_FACE_LIMIT = 150_000  # About 7.5 MB as binary STL: quick on a LAN and on phones.
MESH_FORMATS = ('.stl', '.obj', '.3mf', '.zip')


def mesh_stl(path):
    """Binary STL for the viewer, simplified to at most MESH_FACE_LIMIT triangles."""
    import numpy as np

    if path.suffix.lower() not in MESH_FORMATS:
        raise ValueError('3D preview supports STL, OBJ, 3MF and model ZIP files.')
    vertices, faces = simplified(*load_geometry(path), MESH_FACE_LIMIT)
    triangles = np.asarray(vertices, dtype=np.float32)[np.asarray(faces)]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    record = np.dtype([('normal', '<f4', (3,)), ('corners', '<f4', (3, 3)), ('attribute', '<u2')])
    body = np.zeros(len(triangles), dtype=record)
    body['normal'], body['corners'] = normals, triangles
    return b'Forma 3D preview'.ljust(80, b' ') + len(body).to_bytes(4, 'little') + body.tobytes()


def cached_mesh(path, cache):
    stat = path.stat()
    identity = f'{MESH_VERSION}:{path}:{stat.st_size}:{stat.st_mtime_ns}'
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache.mkdir(exist_ok=True)
    destination = cache / (key + '.stl')
    error_file = cache / (key + '.error')
    if destination.exists():
        return destination
    if error_file.exists():
        raise ValueError(error_file.read_text())
    with RENDER_LOCK:
        if destination.exists():
            return destination
        if error_file.exists():
            raise ValueError(error_file.read_text())
        try:
            body = workers.run(mesh_stl, path)
            temporary = destination.with_suffix('.tmp')
            temporary.write_bytes(body)
            os.replace(temporary, destination)
            return destination
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else 'This file could not be shown in 3D.'
            error_file.write_text(message)
            raise ValueError(message) from exc
