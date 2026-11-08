"""Printer presets and scoped, expiring downloads for desktop slicer handoffs."""

import hashlib
import secrets
import time
import uuid
import io
import threading
from pathlib import Path
from urllib.parse import quote
from flask import abort, jsonify, request, send_file
from werkzeug.utils import secure_filename
from migrations import add_columns

SLICERS = {'bambu': 'Bambu Studio', 'snapmaker': 'Snapmaker Orca'}
FORMATS = {'.stl', '.3mf', '.obj', '.step', '.stp'}
CONVERT_LOCK = threading.Lock()
PRESETS = [
    {
        'id': 'h2d',
        'name': 'Bambu Lab H2D',
        'width': 300,
        'depth': 320,
        'height': 320,
        'nozzle': 0.4,
        'slicer': 'bambu',
        'notes': 'Fit checks use the shared dual-nozzle envelope (300 × 320 × 320 mm). Single-nozzle reach is larger (up to 325 × 320 × 325 mm); the combined bed spans 350 mm. Confirm nozzle, placement and height in Bambu Studio. Default 0.4 mm nozzle; edit to match your hardware.',
    },
    {
        'id': 'h2s',
        'name': 'Bambu Lab H2S',
        'width': 340,
        'depth': 320,
        'height': 340,
        'nozzle': 0.4,
        'slicer': 'bambu',
        'notes': '340 × 320 × 340 mm build envelope. Confirm bed exclusions, brims and clearance in Bambu Studio. Default 0.4 mm nozzle; edit to match your hardware.',
    },
    {
        'id': 'p1s',
        'name': 'Bambu Lab P1S',
        'width': 256,
        'depth': 256,
        'height': 250,
        'nozzle': 0.4,
        'slicer': 'bambu',
        'notes': 'Fit checks use the installed Bambu Studio profile height of 250 mm; nominal hardware height is 256 mm. The profile excludes an 18 × 28 mm corner. Confirm usable height and placement in Bambu Studio. Default 0.4 mm nozzle; edit to match your hardware.',
    },
    {
        'id': 'u1',
        'name': 'Snapmaker U1',
        'width': 270,
        'depth': 270,
        'height': 270,
        'nozzle': 0.4,
        'slicer': 'snapmaker',
        'notes': '270 × 270 × 270 mm nominal build volume, four toolheads. Use Snapmaker Orca to assign tools and confirm clearances. Default 0.4 mm nozzle; edit to match your hardware.',
    },
]


def converted_3mf(path, cache):
    import numpy as np
    import trimesh

    stat = path.stat()
    if stat.st_size > 128 * 1024 * 1024:
        raise ValueError('This mesh is too large to prepare automatically. Download it and open it in Bambu Studio.')
    key = hashlib.sha256(f'v1:{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
    cache.mkdir(exist_ok=True)
    target = cache / (key + '.3mf')
    with CONVERT_LOCK:
        if target.exists():
            target.touch()
            return target
        try:
            scene = trimesh.load(
                io.BytesIO(path.read_bytes()),
                file_type=path.suffix.lower()[1:],
                force='scene',
                process=False,
                skip_materials=True,
                resolver={},
            )
            if not scene.geometry or not np.isfinite(scene.bounds).all():
                raise ValueError('No usable mesh geometry.')
            # STL/OBJ unit assumptions match Forma's measurements. Translation only;
            # no scaling or changes to the original attachment.
            scene.units = 'mm'
            scene.apply_translation(-scene.bounds[0])
            body = scene.export(file_type='3mf')
        except Exception as exc:
            raise ValueError(
                'A 3MF copy could not be prepared. Download the original and open it in your slicer.'
            ) from exc
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(body)
        temporary.replace(target)
    return target


def register_slicers(app, db, data, library):
    cache = data / 'slicer-cache'
    with db() as c:
        c.executescript(
            '''CREATE TABLE IF NOT EXISTS slicer_downloads(token_hash TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,name TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,expires REAL NOT NULL);'''
        )
        add_columns(c, 'slicer_downloads', {'payload_path': "TEXT NOT NULL DEFAULT ''"})

    def checked_file(c, id):
        f = c.execute("SELECT * FROM files WHERE id=? AND kind='model'", (id,)).fetchone()
        if not f:
            abort(404, 'Model file not found.')
        path = Path(f['path']).resolve()
        root = library if f['external'] else data / 'files'
        if not path.is_relative_to(root) or not path.is_file():
            abort(404, 'The original file is unavailable.')
        if path.suffix.lower() not in FORMATS:
            raise ValueError(
                'Download and extract ZIP archives first. Direct handoff supports STL, 3MF, OBJ, STEP and STP.'
            )
        return f, path, path.stat()

    @app.get('/api/printer-presets')
    def printer_presets():
        return jsonify(presets=PRESETS, slicers=SLICERS)

    @app.post('/api/printer-presets/<id>/add')
    def add_preset(id):
        p = next((p for p in PRESETS if p['id'] == id), None)
        if not p:
            abort(404)
        with db() as c:
            aliases = {
                'h2d': ['Bambu Lab H2D', 'Bambu H2D', 'H2D'],
                'h2s': ['Bambu Lab H2S', 'Bambu H2S', 'H2S'],
                'p1s': ['Bambu Lab P1S', 'Bambu P1S', 'P1S'],
                'u1': ['Snapmaker U1', 'U1'],
            }[id]
            old = c.execute(
                'SELECT id FROM printers WHERE preset=? OR lower(name) IN (' + ','.join('?' for _ in aliases) + ')',
                (id, *(n.lower() for n in aliases)),
            ).fetchone()
            if old:
                c.execute(
                    "UPDATE printers SET slicer=CASE WHEN slicer='' THEN ? ELSE slicer END,preset=CASE WHEN preset='' THEN ? ELSE preset END WHERE id=?",
                    (p['slicer'], id, old['id']),
                )
                return jsonify(id=old['id'], created=False)
            printer_id = str(uuid.uuid4())
            c.execute(
                'INSERT INTO printers(id,name,width,depth,height,nozzle,notes,slicer,preset) VALUES (?,?,?,?,?,?,?,?,?)',
                (
                    printer_id,
                    p['name'],
                    p['width'],
                    p['depth'],
                    p['height'],
                    p['nozzle'],
                    p['notes'],
                    p['slicer'],
                    p['id'],
                ),
            )
        return jsonify(id=printer_id, created=True), 201

    @app.post('/api/files/<id>/slicer-link')
    def slicer_link(id):
        p = request.get_json()
        slicer = p.get('slicer')
        platform = p.get('platform', 'mac')
        if slicer not in SLICERS or platform not in ('mac', 'windows', 'linux'):
            raise ValueError('Choose a supported slicer and operating system.')
        with db() as c:
            f, path, stat = checked_file(c, id)
        if slicer == 'bambu' and path.suffix.lower() in ('.step', '.stp'):
            raise ValueError(
                'Bambu Studio’s web handoff requires 3MF. Download this STEP file and open it in Bambu Studio manually.'
            )
        if slicer == 'bambu' and stat.st_size > 500 * 1024 * 1024:
            raise ValueError('This file exceeds Bambu Studio’s web download limit. Download it and open it manually.')
        converted = slicer == 'bambu' and path.suffix.lower() in ('.stl', '.obj')
        if cache.exists():
            for old in cache.glob('*.3mf'):
                try:
                    if old.stat().st_mtime < time.time() - 3600:
                        old.unlink()
                except OSError:
                    pass
        payload = converted_3mf(path, cache) if converted else path
        token = secrets.token_urlsafe(32)
        expires = time.time() + 600
        with db() as c:
            name = secure_filename(Path(f['name']).stem)[:100] or 'Model'
            name += payload.suffix.lower()
            c.execute('DELETE FROM slicer_downloads WHERE expires<?', (time.time(),))
            # Avoid unbounded active links while allowing native HEAD, GET and Range retries.
            if c.execute('SELECT count(*) FROM slicer_downloads').fetchone()[0] >= 500:
                raise ValueError('Too many active handoffs. Wait ten minutes before preparing another.')
            c.execute(
                'INSERT INTO slicer_downloads(token_hash,file_id,name,path,size,mtime_ns,expires,payload_path) VALUES (?,?,?,?,?,?,?,?)',
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    id,
                    name,
                    str(path),
                    stat.st_size,
                    stat.st_mtime_ns,
                    expires,
                    str(payload),
                ),
            )
        url = request.host_url.rstrip('/') + '/slicer-download/' + token + '/' + quote(name, safe='')
        if slicer == 'bambu':
            launch = (
                ('bambustudioopen://' + quote(url, safe=''))
                if platform == 'mac'
                else 'bambustudio://open?file=' + quote(url, safe='')
            )
        else:
            launch = 'snapmaker-orca://open?file=' + quote(url, safe='') + '&name=' + quote(name, safe='')
        return jsonify(
            slicer=slicer,
            slicer_name=SLICERS[slicer],
            download_url=url,
            launch_url=launch,
            expires_in=600,
            name=name,
            converted=converted,
        )

    @app.route('/slicer-download/<token>/<name>', methods=['GET', 'HEAD'])
    def slicer_download(token, name):
        if len(token) > 100:
            abort(404)
        with db() as c:
            row = c.execute(
                'SELECT * FROM slicer_downloads WHERE token_hash=? AND expires>?',
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
            if not row or row['name'] != name:
                abort(404, 'This handoff expired. Prepare a new link in Forma.')
            f, path, stat = checked_file(c, row['file_id'])
            if str(path) != row['path'] or stat.st_size != row['size'] or stat.st_mtime_ns != row['mtime_ns']:
                abort(410, 'This file changed. Prepare a new handoff link.')
            payload = Path(row['payload_path'] or str(path)).resolve()
            if (payload != path and not payload.is_relative_to(cache)) or not payload.is_file():
                abort(404, 'Prepare a new handoff link.')
        response = send_file(payload, as_attachment=True, download_name=name, conditional=True)
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        return response
