"""Cached previews: preserve embedded slicer images before rendering geometry."""
import hashlib
import io
import math
import os
import threading
import zipfile
from pathlib import Path

MAX_MODEL_BYTES = 256 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
PREVIEW_VERSION = 'v1'
RENDER_LOCK = threading.Lock()


def embedded_image(archive):
    """Read image bytes only; never extract paths or expand an entire archive."""
    candidates = []
    for item in archive.infolist():
        name = item.filename.lower()
        if item.file_size > MAX_IMAGE_BYTES or item.is_dir():
            continue
        if not name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            continue
        score = (0 if name.endswith('thumbnail_3mf.png') else
                 1 if name.endswith('thumbnail.png') else
                 2 if name.endswith('thumbnail_middle.png') else
                 3 if name.endswith('plate_1.png') else
                 4 if 'thumbnail' in name else
                 5 if 'preview' in name else
                 6 if name.startswith('metadata/') else 7)
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


def render_mesh(path_or_stream, file_type):
    """Software orthographic render. No browser, GPU or display server needed."""
    import numpy as np
    import trimesh
    from PIL import Image, ImageDraw, ImageFilter
    geometry = trimesh.load(path_or_stream, file_type=file_type, force='mesh', process=False, skip_materials=True)
    if not isinstance(geometry, trimesh.Trimesh) or not len(geometry.faces):
        raise ValueError('No triangle mesh found in this file.')
    if len(geometry.faces) > 60_000:
        # Weld before decimation: STL vertices are often disconnected triangles.
        geometry.merge_vertices()
        geometry = geometry.simplify_quadric_decimation(face_count=60_000)
    triangles = np.array(geometry.triangles, dtype=np.float64, copy=True)
    if not np.isfinite(triangles).all():
        raise ValueError('Model contains invalid coordinates.')
    vertices = triangles.reshape((-1, 3))
    center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    triangles -= center
    # Right, up and camera vectors form an isometric camera basis.
    right = np.array([1, -1, 0], dtype=float) / math.sqrt(2)
    camera = np.array([1, 1, .85], dtype=float)
    camera /= np.linalg.norm(camera)
    up = np.cross(camera, right)
    projected = np.stack((triangles @ right, -(triangles @ up)), axis=-1)
    low, high = projected.min(axis=(0, 1)), projected.max(axis=(0, 1))
    span = np.maximum(high-low, 1e-9)
    scale = min(560/span[0], 390/span[1])
    projected = (projected-(low+high)/2)*scale + np.array([360, 255])
    normals = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
    normals /= np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-12)
    light = np.array([-.4, -.6, 1.0]); light /= np.linalg.norm(light)
    shades = np.clip(.58 + .42*(normals @ light), .25, 1)
    colors = (np.array([198,139,108])[None,:]*shades[:,None]).astype(int)
    depth = (triangles @ camera).mean(axis=1)
    image = Image.new('RGB',(720,540),'#efeee7')
    shadow = Image.new('RGBA',image.size)
    draw_shadow = ImageDraw.Draw(shadow)
    width = max(50,min(470,float(span[0]*scale)*.8))
    bottom = min(495,float(projected[:,:,1].max())+12)
    draw_shadow.ellipse((360-width/2,bottom-12,360+width/2,bottom+12),fill=(84,76,57,40))
    image.paste(shadow.filter(ImageFilter.GaussianBlur(12)),(0,0),shadow.filter(ImageFilter.GaussianBlur(12)))
    draw = ImageDraw.Draw(image)
    for index in np.argsort(depth):
        points = [tuple(p) for p in projected[index]]
        draw.polygon(points,fill=tuple(colors[index]))
    image.thumbnail((600,450),Image.Resampling.LANCZOS)
    result = io.BytesIO(); image.save(result,format='PNG',optimize=True)
    return result.getvalue()


def generate_preview(path):
    suffix = path.suffix.lower()
    if suffix not in ('.3mf','.zip') and path.stat().st_size > MAX_MODEL_BYTES:
        raise ValueError('Model is too large for automatic preview.')
    if suffix in ('.3mf','.zip'):
        with zipfile.ZipFile(path) as archive:
            image = embedded_image(archive)
            if image:
                return image
            # Bound uncompressed geometry size as well as the ZIP on disk.
            if sum(i.file_size for i in archive.infolist()) > MAX_MODEL_BYTES:
                raise ValueError('Archive is too large for automatic preview.')
            if suffix == '.zip':
                parts = sorted((i for i in archive.infolist() if Path(i.filename).suffix.lower() in ('.stl','.obj','.3mf') and not i.filename.startswith('__MACOSX/')),key=lambda i:i.filename)
                for part in parts:
                    body = archive.read(part)
                    kind = Path(part.filename).suffix.lower()[1:]
                    if kind == '3mf':
                        with zipfile.ZipFile(io.BytesIO(body)) as nested:
                            image = embedded_image(nested)
                            if image:
                                return image
                            if sum(i.file_size for i in nested.infolist()) > MAX_MODEL_BYTES:
                                continue
                    try:
                        return render_mesh(io.BytesIO(body),kind)
                    except Exception:
                        continue
                raise ValueError('No supported model or preview image found in this ZIP.')
    if suffix not in ('.stl','.obj','.3mf'):
        raise ValueError('Automatic previews support STL, OBJ, 3MF and model ZIP files.')
    return render_mesh(str(path),suffix[1:])


def cached_preview(path, cache):
    stat = path.stat()
    identity = f'{PREVIEW_VERSION}:{path}:{stat.st_size}:{stat.st_mtime_ns}'
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache.mkdir(exist_ok=True)
    destination = cache/(key+'.png')
    error_file = cache/(key+'.error')
    if destination.exists():
        return destination
    if error_file.exists():
        raise ValueError(error_file.read_text())
    # Keep peak mesh memory bounded and avoid duplicate work for the same file.
    with RENDER_LOCK:
        if destination.exists():
            return destination
        if error_file.exists():
            raise ValueError(error_file.read_text())
        try:
            body = generate_preview(path)
            temporary = destination.with_suffix('.tmp')
            temporary.write_bytes(body)
            os.replace(temporary,destination)
            return destination
        except Exception as exc:
            error_file.write_text('A preview could not be generated for this file. Add a cover photo using Edit model.')
            raise ValueError(error_file.read_text()) from exc
