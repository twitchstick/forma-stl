"""Saved views, personal printer profiles, print logs and file details."""
import math
import uuid
from pathlib import Path
from flask import abort, jsonify, request
from model_details import cached_details


def text(value, limit=300):
    if not isinstance(value, str): raise ValueError('Use text for this field.')
    return value.strip()[:limit]


def number(value, optional=False, maximum=10000):
    if optional and value in ('', None): return None
    if isinstance(value, bool): raise ValueError('Use a positive number.')
    try: value = float(value)
    except (TypeError, ValueError): raise ValueError('Use a positive number.')
    if not math.isfinite(value) or not 0 < value <= maximum: raise ValueError('Use a positive number up to ' + str(maximum) + '.')
    return value


def view_rules(p):
    import json
    if not isinstance(p, dict): raise ValueError('Invalid saved filters.')
    result = {k: text(p.get(k, ''), 1000 if k == 'search' else 300) for k in ('search', 'tag', 'source', 'status', 'view', 'collection', 'sort')}
    if result['view'] not in ('all', 'favorites', 'queue', 'printed', 'collection'): raise ValueError('Invalid library view.')
    if result['status'] not in ('', 'Saved', 'To print', 'Printing', 'Printed'): raise ValueError('Invalid print status.')
    if result['sort'] not in ('newest', 'oldest', 'az'): raise ValueError('Invalid sort order.')
    result['list_mode'] = bool(p.get('list_mode', False))
    return json.dumps(result)


def register_catalog_tools(app, db, data, library, model, now):
    import json
    from datetime import date
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS saved_views(id TEXT PRIMARY KEY,name TEXT NOT NULL,rules TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS printers(id TEXT PRIMARY KEY,name TEXT NOT NULL,width REAL NOT NULL,depth REAL NOT NULL,height REAL NOT NULL,nozzle REAL,notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS file_labels(file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,label TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS print_logs(id TEXT PRIMARY KEY,model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,printer_id TEXT REFERENCES printers(id) ON DELETE SET NULL,printer_name TEXT NOT NULL DEFAULT '',printed_on TEXT NOT NULL,filament TEXT NOT NULL DEFAULT '',settings TEXT NOT NULL DEFAULT '',result TEXT NOT NULL,notes TEXT NOT NULL DEFAULT '',photo_id TEXT REFERENCES files(id) ON DELETE SET NULL,created_at TEXT NOT NULL);
        ''')

    @app.get('/api/saved-views')
    def views():
        with db() as c: return jsonify([dict(r) | {'rules': json.loads(r['rules'])} for r in c.execute('SELECT * FROM saved_views ORDER BY created_at,name')])

    @app.post('/api/saved-views')
    def add_view():
        p = request.get_json();name = text(p.get('name', ''), 100);rules = view_rules(p.get('rules'))
        if not name: raise ValueError('Name your saved view.')
        id = str(uuid.uuid4())
        with db() as c:
            if c.execute('SELECT count(*) FROM saved_views').fetchone()[0] >= 100: raise ValueError('Keep up to 100 saved views.')
            c.execute('INSERT INTO saved_views VALUES (?,?,?,?)', (id, name, rules, now()))
        return jsonify(id=id), 201

    @app.patch('/api/saved-views/<id>')
    def edit_view(id):
        p = request.get_json();name = text(p.get('name', ''), 100)
        if not name: raise ValueError('Name your saved view.')
        with db() as c:
            if not c.execute('UPDATE saved_views SET name=? WHERE id=?', (name, id)).rowcount: abort(404)
        return jsonify(ok=True)

    @app.delete('/api/saved-views/<id>')
    def remove_view(id):
        with db() as c:
            if not c.execute('DELETE FROM saved_views WHERE id=?', (id,)).rowcount: abort(404)
        return jsonify(ok=True)

    @app.get('/api/printers')
    def printers():
        with db() as c: return jsonify([dict(r) for r in c.execute('SELECT * FROM printers ORDER BY name')])

    def printer_values(p):
        name = text(p.get('name', ''), 100)
        if not name: raise ValueError('Name your printer.')
        return (name, number(p.get('width')), number(p.get('depth')), number(p.get('height')), number(p.get('nozzle'), True, 5), text(p.get('notes', ''), 5000))

    @app.post('/api/printers')
    def add_printer():
        values = printer_values(request.get_json());id = str(uuid.uuid4())
        with db() as c: c.execute('INSERT INTO printers VALUES (?,?,?,?,?,?,?)', (id, *values))
        return jsonify(id=id), 201

    @app.patch('/api/printers/<id>')
    def edit_printer(id):
        values = printer_values(request.get_json())
        with db() as c:
            if not c.execute('UPDATE printers SET name=?,width=?,depth=?,height=?,nozzle=?,notes=? WHERE id=?', (*values, id)).rowcount: abort(404)
        return jsonify(ok=True)

    @app.delete('/api/printers/<id>')
    def remove_printer(id):
        with db() as c:
            if not c.execute('DELETE FROM printers WHERE id=?', (id,)).rowcount: abort(404)
        return jsonify(ok=True)

    @app.get('/api/models/<id>/details')
    def details(id):
        with db() as c:
            model(c, id)
            files = [dict(r) for r in c.execute("SELECT f.*,COALESCE(l.label,'') AS variant_label FROM files f LEFT JOIN file_labels l ON f.id=l.file_id WHERE f.model_id=? AND f.kind='model'", (id,))]
        result = []
        for f in files:
            path = Path(f['path']).resolve();root = library if f['external'] else data / 'files'
            info = {'entries': [], 'error': 'File is missing or outside the mounted library.'}
            if path.is_relative_to(root) and path.is_file():
                try: info = cached_details(path, data / 'model-details')
                except OSError: info = {'entries': [], 'error': 'File could not be read. Try again after the file finishes copying.'}
            # Use the original filename, never expose internal storage paths.
            if path.suffix.lower() != '.zip':
                for entry in info['entries']: entry['name'] = f['name']
            result.append({'id': f['id'], 'name': f['name'], 'size': f['size'], 'variant_label': f['variant_label'], **info})
        return jsonify(files=result)

    @app.patch('/api/files/<id>/label')
    def file_label(id):
        label = text(request.get_json().get('label', ''), 150)
        with db() as c:
            if not c.execute("SELECT id FROM files WHERE id=? AND kind='model'", (id,)).fetchone(): abort(404)
            c.execute('INSERT INTO file_labels VALUES (?,?) ON CONFLICT(file_id) DO UPDATE SET label=excluded.label', (id, label))
        return jsonify(ok=True)

    @app.get('/api/models/<id>/prints')
    def logs(id):
        with db() as c:
            model(c, id)
            return jsonify([dict(r) for r in c.execute('SELECT * FROM print_logs WHERE model_id=? ORDER BY printed_on DESC,created_at DESC', (id,))])

    @app.post('/api/models/<id>/prints')
    def add_log(id):
        p = request.get_json();printed_on = text(p.get('printed_on', ''), 10)
        try: date.fromisoformat(printed_on)
        except ValueError: raise ValueError('Choose a valid print date.')
        outcome = text(p.get('result', ''), 30)
        if outcome not in ('Success', 'Failed', 'In progress'): raise ValueError('Choose a print result.')
        printer = text(p.get('printer_id', '')) or None;photo = text(p.get('photo_id', '')) or None
        filament = text(p.get('filament', ''), 300);settings = text(p.get('settings', ''), 5000);notes = text(p.get('notes', ''), 10000);log_id = str(uuid.uuid4())
        with db() as c:
            model(c, id);printer_name = ''
            if printer:
                r = c.execute('SELECT name FROM printers WHERE id=?', (printer,)).fetchone()
                if not r: raise ValueError('Choose an existing printer.')
                printer_name = r[0]
            if photo and not c.execute("SELECT id FROM files WHERE id=? AND model_id=? AND kind='image'", (photo, id)).fetchone(): raise ValueError('Choose a photo from this model’s gallery.')
            c.execute('INSERT INTO print_logs VALUES (?,?,?,?,?,?,?,?,?,?,?)', (log_id, id, printer, printer_name, printed_on, filament, settings, outcome, notes, photo, now()))
        return jsonify(id=log_id), 201

    @app.delete('/api/models/<id>/prints/<log_id>')
    def remove_log(id, log_id):
        with db() as c:
            if not c.execute('DELETE FROM print_logs WHERE id=? AND model_id=?', (log_id, id)).rowcount: abort(404)
        return jsonify(ok=True)
