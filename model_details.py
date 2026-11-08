"""Bounded, cached model measurements and explicit slicer metadata."""

import hashlib
import io
import json
import threading
import zipfile
from pathlib import Path

LIMIT = 128 * 1024 * 1024
LOCK = threading.Lock()
SETTING_KEYS = {
    'layer_height',
    'initial_layer_print_height',
    'first_layer_height',
    'sparse_infill_density',
    'fill_density',
    'infill_density',
    'filament_type',
    'nozzle_diameter',
    'support_type',
    'enable_support',
    'support_material',
    'printer_model',
    'printer_variant',
    'printer_settings_id',
    'print_settings_id',
    'filament_settings_id',
}


def archive_safe(z):
    if len(z.infolist()) > 5000 or sum(i.file_size for i in z.infolist()) > LIMIT:
        raise ValueError('Archive exceeds the 128 MB analysis limit.')
    for entry in z.infolist():
        if entry.filename.lower().endswith(('.model', '.xml', '.config')):
            body = z.read(entry)
            if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
                raise ValueError('XML entity declarations are not supported.')


def settings_from(z):
    result = {}
    for entry in z.infolist():
        name = entry.filename.lower()
        if not name.startswith('metadata/') or not name.endswith(('.config', '.json')) or entry.file_size > 1024 * 1024:
            continue
        text = z.read(entry).decode('utf-8', errors='replace')
        try:
            values = json.loads(text)
            if not isinstance(values, dict):
                continue
        except ValueError:
            values = dict(
                line.lstrip('; ').split('=', 1)
                for line in text.splitlines()
                if '=' in line and not line.lstrip().startswith('<')
            )
        for k, value in values.items():
            k = k.strip()
            if k not in SETTING_KEYS:
                continue
            if isinstance(value, list):
                value = ', '.join(str(v) for v in value[:20])
            if isinstance(value, (str, int, float, bool)):
                result[k] = str(value).strip()[:500]
    return result


def inspect_mesh(body, suffix):
    import numpy as np
    import trimesh

    result = {'dimensions_mm': None, 'settings': {}, 'note': '', 'error': ''}
    try:
        if suffix == '.3mf':
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                archive_safe(z)
                result['settings'] = settings_from(z)
        scene = trimesh.load(
            io.BytesIO(body), file_type=suffix[1:], force='scene', process=False, skip_materials=True, resolver={}
        )
        if not scene.geometry:
            raise ValueError('No mesh geometry is available.')
        if suffix == '.3mf':
            scene = scene.convert_units('mm', guess=False)
            result['note'] = '3MF build layout, including placed parts; dimensions converted to mm.'
        else:
            result['note'] = 'STL/OBJ have no reliable unit declaration; millimeters assumed.'
        bounds = np.asarray(scene.bounds)
        if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
            raise ValueError('Geometry has invalid bounds.')
        size = bounds[1] - bounds[0]
        if (size < 0).any() or not size.any():
            raise ValueError('No measurable geometry is available.')
        result['dimensions_mm'] = [round(float(v), 3) for v in size]
    except Exception:
        result['error'] = 'Dimensions unavailable for this file. Slicer settings are shown when readable.'
    return result


def analyze(path):
    if path.stat().st_size > LIMIT:
        return {'entries': [], 'error': 'File exceeds the 128 MB analysis limit.'}
    suffix = path.suffix.lower()
    if suffix in ('.stl', '.obj', '.3mf'):
        return {'entries': [{'name': path.name, **inspect_mesh(path.read_bytes(), suffix)}], 'error': ''}
    if suffix == '.zip':
        try:
            with zipfile.ZipFile(path) as z:
                archive_safe(z)
                candidates = [
                    i
                    for i in z.infolist()
                    if not i.is_dir()
                    and not i.filename.startswith('__MACOSX/')
                    and Path(i.filename).suffix.lower() in ('.stl', '.obj', '.3mf')
                ]
                entries = [
                    {'name': i.filename, **inspect_mesh(z.read(i), Path(i.filename).suffix.lower())}
                    for i in candidates[:20]
                ]
                return {
                    'entries': entries,
                    'error': 'Only the first 20 model variants are analyzed.'
                    if len(candidates) > 20
                    else ''
                    if entries
                    else 'No supported mesh files found in this ZIP.',
                }
        except Exception:
            return {
                'entries': [],
                'error': 'ZIP analysis unavailable. The archive may be invalid or exceed the analysis limits.',
            }
    return {
        'entries': [],
        'error': 'Dimensions are available for STL, OBJ, 3MF, and model ZIP files. STEP files remain downloadable.',
    }


def cached_details(path, cache):
    stat = path.stat()
    key = hashlib.sha256(f'v1:{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
    cache.mkdir(exist_ok=True)
    destination = cache / (key + '.json')
    with LOCK:
        if destination.exists():
            return json.loads(destination.read_text())
        result = analyze(path)
        temp = destination.with_suffix('.tmp')
        temp.write_text(json.dumps(result))
        temp.replace(destination)
        return result
