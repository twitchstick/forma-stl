"""Forma: a single-user, self-hosted print library."""
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import shutil
import socket
import ssl
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
from previews import cached_preview
from library_services import LibraryServices
from makerworld_import import MakerWorldBridge
from catalog_tools import register_catalog_tools
from slicers import register_slicers
from source_import import SourceImports
from collection_schedule import CollectionSchedules
from source_metadata import parse_source, web_url

ROOT = Path(__file__).resolve().parent
MODELS = {'.stl', '.3mf', '.obj', '.step', '.stp', '.zip'}
IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def now():
    return datetime.now(timezone.utc).isoformat()


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('Use a public http or https page URL.')
    addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Only public websites can be imported.')
    return url


def fetch_public(url, limit=3_000_000):
    # Resolve and pin the validated IP for each hop, including redirects. TLS
    # still verifies the original hostname. Never follow redirects into the LAN.
    for _ in range(6):
        public_url(url)
        p = urlsplit(url)
        port = p.port or (443 if p.scheme == 'https' else 80)
        addresses = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
        ip = addresses[0][4][0]
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Only public websites can be imported.')
        conn = http.client.HTTPConnection(p.hostname, port, timeout=15)
        sock = socket.create_connection((ip, port), timeout=15)
        try:
            if p.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=p.hostname)
            conn.sock = sock
            target = p.path or '/'
            if p.query:
                target += '?' + p.query
            conn.request('GET', target, headers={'User-Agent':'Forma/1.0 personal-model-catalog','Accept-Encoding':'identity'})
            r = conn.getresponse()
            if r.status in (301,302,303,307,308):
                location = r.getheader('Location')
                if not location:
                    raise ValueError('The page returned an invalid redirect.')
                url = urljoin(url, location)
                continue
            if r.status != 200:
                raise ValueError('The site did not allow this request.')
            body = r.read(limit + 1)
            if len(body) > limit:
                raise ValueError('The remote resource is too large.')
            return body, r.headers.get_content_type(), url
        finally:
            conn.close()
            sock.close()
    raise ValueError('Too many redirects.')


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.title, self.in_title = {}, '', False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            self.meta[(a.get('property') or a.get('name') or '').lower()] = a.get('content', '')
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def create_app(data_dir=None, library_dir=None, start_watcher=False):
    app = Flask(__name__, static_folder=str(ROOT / 'static'))
    data = Path(data_dir or os.environ.get('FORMA_DATA', '/data')).resolve()
    library = Path(library_dir or os.environ.get('FORMA_LIBRARY', '/library')).resolve()
    uploads = data / 'files'
    uploads.mkdir(parents=True, exist_ok=True)
    db_path = data / 'forma.sqlite3'
    app.config.update(MAX_CONTENT_LENGTH=512 * 1024 * 1024)
    app.config['FORMA_LIBRARY_ROOT']=str(library)

    @contextmanager
    def db():
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS models (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, creator TEXT NOT NULL DEFAULT '',
          source_url TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '[]', collection TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'Saved', favorite INTEGER NOT NULL DEFAULT 0,
          cover_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          import_key TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS files (
          id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
          name TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL,
          sha256 TEXT NOT NULL DEFAULT '', external INTEGER NOT NULL DEFAULT 0
        );
        ''')
        model_columns = {r[1] for r in c.execute('PRAGMA table_info(models)')}
        for name in ('license','license_url','attribution'):
            if name not in model_columns:c.execute('ALTER TABLE models ADD COLUMN '+name+" TEXT NOT NULL DEFAULT ''")
        columns = {r[1] for r in c.execute('PRAGMA table_info(files)')}
        for name in ('mtime_ns','hash_mtime_ns','hash_size'):
            if name not in columns:
                c.execute('ALTER TABLE files ADD COLUMN '+name+' INTEGER NOT NULL DEFAULT 0')
        for row in c.execute('SELECT id,path,size FROM files WHERE mtime_ns=0').fetchall():
            try:
                path = Path(row['path']).resolve()
                if not (path.is_relative_to(library) or path.is_relative_to(uploads)):
                    continue
                stat = path.stat()
                if stat.st_size==row['size']:
                    c.execute('UPDATE files SET mtime_ns=? WHERE id=?',(stat.st_mtime_ns,row['id']))
            except OSError:
                pass

    @app.before_request
    def protect():
        if request.path.startswith('/api/companion/'):
            app.extensions['makerworld_bridge'].authorized_bridge_request()
            if request.method=='OPTIONS':
                return ('',204)
            return
        # A short-lived capability grants read-only access to one selected model file.
        # The download handler validates its token, expiry, path, and file fingerprint.
        if request.endpoint=='slicer_download' and request.method in ('GET','HEAD'):return
        # Docker Manager downloads this non-sensitive logo without browser auth.
        if request.path == '/static/app-icon.png' and request.method in ('GET','HEAD'):
            return
        password = os.environ.get('FORMA_PASSWORD', '')
        if password:
            auth = request.authorization
            if not auth or auth.type != 'basic' or not hmac.compare_digest(auth.username or '', os.environ.get('FORMA_USERNAME', 'admin')) or not hmac.compare_digest(auth.password or '', password):
                return ('Sign in to Forma', 401, {'WWW-Authenticate': 'Basic realm="Forma"'})
        if request.method in ('POST', 'PATCH', 'DELETE'):
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                abort(403, 'Cross-origin changes are not allowed.')
            if request.headers.get('X-Forma-Request') != '1':
                abort(403, 'Missing request header.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        origin=request.headers.get('Origin','')
        if request.path.startswith('/api/companion/') and origin.startswith(('chrome-extension://','moz-extension://')):
            response.headers['Access-Control-Allow-Origin']=origin
            response.headers['Vary']='Origin'
            response.headers['Access-Control-Allow-Methods']='GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers']='Authorization, Content-Type'
            response.headers['Access-Control-Max-Age']='300'
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(exc, (ValueError, KeyError)):
            return jsonify(error=str(exc)), 400
        app.logger.exception('Request failed')
        return jsonify(error='The request failed. Check server logs for details.'), 500

    def model(c, id):
        row = c.execute('SELECT * FROM models WHERE id=?', (id,)).fetchone()
        if not row:
            abort(404, 'Model not found.')
        result = dict(row)
        result['tags'] = json.loads(result['tags'])
        result['favorite'] = bool(result['favorite'])
        result['files'] = [dict(x) for x in c.execute('SELECT id,name,size,kind FROM files WHERE model_id=?', (id,))]
        result['collections'] = list(dict.fromkeys(([result['collection']] if result['collection'] else [])+[r[0] for r in c.execute('SELECT name FROM makerworld_memberships WHERE model_id=? ORDER BY name',(id,))]))
        source=c.execute("SELECT remote_id,profile_id FROM makerworld_sources WHERE model_id=? AND remote_id NOT LIKE '%:%'",(id,)).fetchone()
        result['makerworld'] = dict(source) if source else None
        return result

    def insert_model(c, payload, import_key=None):
        title = str(payload.get('title', '')).strip()[:300]
        if not title:
            raise ValueError('Give this model a title.')
        tags = payload.get('tags', [])
        if not isinstance(tags, list) or len(tags) > 100:
            raise ValueError('Tags must be a list of up to 100 names.')
        tags = list(dict.fromkeys(str(t).strip()[:80] for t in tags if str(t).strip()))
        status = payload.get('status', 'Saved')
        if status not in ('Saved', 'To print', 'Printing', 'Printed'):
            raise ValueError('Invalid print status.')
        source = str(payload.get('source_url', '')).strip()[:3000]
        if source and (urlsplit(source).scheme not in ('http', 'https') or not urlsplit(source).hostname):
            raise ValueError('Source must be an http or https URL.')
        license_url = str(payload.get('license_url','')).strip()
        if license_url and not web_url(license_url):raise ValueError('Use a public http or https license URL.')
        id = str(uuid.uuid4())
        c.execute('INSERT INTO models (id,title,creator,source_url,description,tags,collection,status,favorite,created_at,updated_at,import_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (id,title,str(payload.get('creator',''))[:300],source,str(payload.get('description',''))[:30000],json.dumps(tags),str(payload.get('collection',''))[:100],status,int(bool(payload.get('favorite',False))),now(),now(),import_key))
        c.execute('UPDATE models SET license=?,license_url=?,attribution=? WHERE id=?',(str(payload.get('license',''))[:500],license_url,str(payload.get('attribution',''))[:2000],id))
        return id

    @app.get('/')
    def home():
        return send_from_directory(app.static_folder, 'index.html')

    @app.get('/health')
    def health():
        return jsonify(status='ok')

    @app.get('/api/models')
    def list_models():
        with db() as c:
            ids = [r[0] for r in c.execute('SELECT id FROM models ORDER BY created_at DESC')]
            return jsonify([model(c, id) for id in ids])

    @app.post('/api/models')
    def add_model():
        with db() as c:
            payload = request.get_json()
            source = payload.get('source_url', '').strip()
            existing = c.execute('SELECT id FROM models WHERE source_url=?', (source,)).fetchone() if source else None
            if existing:
                return jsonify(error='This source is already in your library.', existing_id=existing[0]), 409
            id = insert_model(c, payload)
            return jsonify(model(c,id)), 201

    @app.post('/api/models/bulk')
    def bulk_update():
        payload = request.get_json()
        ids = payload.get('ids')
        changes = payload.get('changes',{})
        allowed = {'tags_add','tags_remove','collection','status','favorite'}
        if not isinstance(ids,list) or not ids or len(ids)>1000 or not all(isinstance(id,str) for id in ids):
            raise ValueError('Select between 1 and 1000 models.')
        if not isinstance(changes,dict) or not changes or not set(changes)<=allowed:
            raise ValueError('Choose tags, a collection, status or a favorite state to update.')
        for name in ('tags_add','tags_remove'):
            tags = changes.get(name,[])
            if not isinstance(tags,list) or len(tags)>100 or not all(isinstance(t,str) and len(t)<=80 for t in tags):
                raise ValueError('Use up to 100 tags with at most 80 characters each.')
        if 'collection' in changes and (not isinstance(changes['collection'],str) or len(changes['collection'])>100):
            raise ValueError('Collection names must be at most 100 characters.')
        if 'status' in changes and changes['status'] not in ('Saved','To print','Printing','Printed'):
            raise ValueError('Invalid print status.')
        if 'favorite' in changes and not isinstance(changes['favorite'],bool):
            raise ValueError('Favorite must be true or false.')
        with db() as c:
            for id in set(ids):
                old = model(c,id)
                tags = list(dict.fromkeys(old['tags']+[t.strip() for t in changes.get('tags_add',[]) if t.strip()]))
                remove = {t.strip() for t in changes.get('tags_remove',[])}
                tags = [t for t in tags if t not in remove]
                if len(tags)>100:
                    raise ValueError('A model cannot have more than 100 tags.')
                if tags!=old['tags']:c.execute("INSERT OR IGNORE INTO source_locks VALUES (?,'tags')",(id,))
                c.execute('UPDATE models SET tags=?,collection=?,status=?,favorite=?,updated_at=? WHERE id=?',
                          (json.dumps(tags),changes.get('collection',old['collection']).strip(),changes.get('status',old['status']),int(changes.get('favorite',old['favorite'])),now(),id))
        return jsonify(updated=len(set(ids)))

    @app.patch('/api/models/<id>')
    def update_model(id):
        payload = request.get_json()
        with db() as c:
            old = model(c,id)
            allowed = {'title','creator','source_url','description','tags','collection','status','favorite','license','license_url','attribution'}
            merged = {**old, **{k:v for k,v in payload.items() if k in allowed}}
            # Validate with the same rules without replacing the record or its files.
            temp_id = insert_model(c, merged)
            clean = c.execute('SELECT * FROM models WHERE id=?', (temp_id,)).fetchone()
            columns = ['title','creator','source_url','description','tags','collection','status','favorite','license','license_url','attribution']
            app.extensions['source_imports'].mark_edits(c,id,old,clean)
            c.execute('UPDATE models SET '+','.join(k+'=?' for k in columns)+',updated_at=? WHERE id=?', tuple(clean[k] for k in columns)+(now(),id))
            c.execute('DELETE FROM models WHERE id=?',(temp_id,))
            if 'cover_id' in payload:
                cover = c.execute('SELECT id FROM files WHERE id=? AND model_id=? AND kind=?',(payload['cover_id'],id,'image')).fetchone()
                if not cover:
                    raise ValueError('Cover must be an attached image.')
                c.execute('UPDATE models SET cover_id=? WHERE id=?',(cover[0],id))
            return jsonify(model(c,id))

    @app.delete('/api/models/<id>')
    def delete_model(id):
        with db() as c:
            model(c,id)
            c.execute('DELETE FROM models WHERE id=?',(id,))
        # Files in the read-only library are never touched.
        shutil.rmtree(uploads/id, ignore_errors=True)
        return jsonify(ok=True)

    def add_file(c, id, name, path, kind, external=False, sha=''):
        fid = str(uuid.uuid4())
        stat = path.stat()
        c.execute('INSERT INTO files (id,model_id,name,path,size,kind,external,sha256,mtime_ns,hash_mtime_ns,hash_size) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(fid,id,name,str(path),stat.st_size,kind,int(external),sha,stat.st_mtime_ns,stat.st_mtime_ns if sha else 0,stat.st_size if sha else 0))
        if kind == 'image':
            c.execute("UPDATE models SET cover_id=? WHERE id=? AND cover_id=''",(fid,id))
        return fid

    services = LibraryServices(db,library,uploads,MODELS,IMAGES,insert_model,add_file,now)
    app.extensions['library_services'] = services
    app.extensions['source_imports'] = SourceImports(app,db,data,library,model,add_file,lambda *args:fetch_public(*args),now)
    app.extensions['makerworld_bridge'] = MakerWorldBridge(app,db,data,insert_model,add_file,model,fetch_public,now)

    app.extensions['collection_schedules'] = CollectionSchedules(app,db,app.extensions['makerworld_bridge'],lambda *args:fetch_public(*args),now)
    register_catalog_tools(app,db,data,library,model,now)
    register_slicers(app,db,data,library)

    @app.get('/api/watch')
    def watch_status():
        return jsonify(services.settings())

    @app.patch('/api/watch')
    def watch_configure():
        return jsonify(services.configure(request.get_json()))

    @app.post('/api/watch/scan')
    def watch_scan():
        return jsonify(services.scan())

    @app.get('/api/watch/review')
    def watch_review_list():
        return jsonify(items=services.reviews())

    @app.post('/api/watch/review')
    def watch_review_apply():
        payload = request.get_json()
        return jsonify(services.review(payload.get('ids'),payload.get('action')))

    @app.get('/api/duplicates')
    def duplicates():
        return jsonify(services.duplicate_report())

    @app.post('/api/duplicates/scan')
    def duplicate_scan():
        return jsonify(services.start_duplicates()), 202

    @app.post('/api/models/<id>/files')
    def upload_files(id):
        items = request.files.getlist('files')
        if not items:
            raise ValueError('Choose files to upload.')
        if any(Path(f.filename).suffix.lower() not in MODELS | IMAGES for f in items):
            raise ValueError('Use STL, 3MF, OBJ, STEP, ZIP, JPG, PNG, WebP or GIF files.')
        folder = uploads/id
        with db() as c:
            model(c,id)
            folder.mkdir(exist_ok=True)
            saved = []
            try:
                for f in items:
                    name = secure_filename(f.filename) or 'file'
                    path = folder/(str(uuid.uuid4())+Path(name).suffix.lower())
                    saved.append(path)
                    f.save(path)
                    kind = 'image' if path.suffix in IMAGES else 'model'
                    digest = hashlib.sha256()
                    with path.open('rb') as stream:
                        for chunk in iter(lambda:stream.read(1024*1024),b''):
                            digest.update(chunk)
                    sha = digest.hexdigest()
                    duplicate = c.execute('SELECT id FROM files WHERE model_id=? AND sha256=?',(id,sha)).fetchone() if sha else None
                    if duplicate:
                        path.unlink()
                        continue
                    add_file(c,id,name,path,kind,sha=sha)
                return jsonify(model(c,id))
            except Exception:
                for p in saved:
                    p.unlink(missing_ok=True)
                raise

    @app.get('/api/files/<fid>')
    def get_file(fid):
        with db() as c:
            f = c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        if not f:
            abort(404)
        path = Path(f['path']).resolve()
        root = library if f['external'] else uploads
        if not path.is_relative_to(root) or not path.is_file():
            abort(404, 'File no longer exists at its original location.')
        return send_file(path, as_attachment=f['kind'] != 'image' or request.args.get('download') == '1', download_name=f['name'], conditional=True)

    @app.get('/api/models/<id>/thumbnail')
    def thumbnail(id):
        with db() as c:
            model(c, id)
            files = [dict(f) for f in c.execute('SELECT * FROM files WHERE model_id=? AND kind=?',(id,'model'))]
        priority = {'.3mf':0,'.stl':1,'.obj':2,'.zip':3}
        files.sort(key=lambda f:priority.get(Path(f['name']).suffix.lower(),99))
        for f in files:
            path = Path(f['path']).resolve()
            root = library if f['external'] else uploads
            if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in priority:
                continue
            try:
                preview = cached_preview(path,data/'thumbnails')
                return send_file(preview,mimetype='image/png',conditional=True)
            except ValueError:
                continue
        abort(404, 'No preview is available for this model. Add a cover photo using Edit model.')

    @app.post('/api/metadata')
    def metadata():
        url = str(request.get_json().get('url','')).strip()
        try:
            body, content_type, final_url = fetch_public(url)
            if content_type not in ('text/html','application/xhtml+xml'):
                raise ValueError('This URL is not an HTML model page.')
            result=parse_source(body,final_url);result['source_url']=url
            return jsonify(result)
        except Exception as exc:
            return jsonify(error='This site did not share readable metadata. You can still save its link and add a title and photo yourself.', source_url=url), 422

    @app.post('/api/models/<id>/remote-image')
    def remote_image(id):
        image_url = request.get_json().get('url','')
        body, mime, _ = fetch_public(image_url,12_000_000)
        exts = {'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/gif':'.gif'}
        if mime not in exts:
            raise ValueError('This URL did not return a supported image.')
        with db() as c:
            model(c,id)
            folder = uploads/id
            folder.mkdir(exist_ok=True)
            path = folder/(str(uuid.uuid4())+exts[mime])
            path.write_bytes(body)
            add_file(c,id,'source-photo'+exts[mime],path,'image')
            return jsonify(model(c,id))

    def scan():
        if not library.is_dir():
            raise ValueError('No library folder is mounted. Map your models share to /library first.')
        groups = {}
        for p in sorted(library.rglob('*')):
            if not p.is_file() or not p.resolve().is_relative_to(library) or p.suffix.lower() not in MODELS | IMAGES:
                continue
            # Root files are individual models; nested files are grouped by their containing folder.
            key = str(p.parent.relative_to(library)) if p.parent != library else p.stem
            groups.setdefault(key,[]).append(p)
        with db() as c:
            existing = {r[0] for r in c.execute('SELECT import_key FROM models WHERE import_key IS NOT NULL')}
        return [(k,ps) for k,ps in groups.items() if any(p.suffix.lower() in MODELS for p in ps) and 'folder:'+k not in existing]

    @app.get('/api/scan')
    def preview_scan():
        groups = scan()
        return jsonify(groups=[{'key':k,'title':Path(k).name.replace('_',' ').replace('-',' '),'files':len(ps)} for k,ps in groups])

    @app.post('/api/scan')
    def import_scan():
        selected = request.get_json().get('keys',[])
        if not isinstance(selected,list):
            raise ValueError('Choose folders to import.')
        count = 0
        with db() as c:
            for key,paths in scan():
                if key not in selected:
                    continue
                id = insert_model(c,{'title':Path(key).name.replace('_',' ').replace('-',' '),'tags':[]},'folder:'+key)
                for p in paths:
                    add_file(c,id,p.name,p,'image' if p.suffix.lower() in IMAGES else 'model',external=True)
                count += 1
        return jsonify(imported=count)

    @app.get('/api/export')
    def export():
        with db() as c:
            records = [model(c,r[0]) for r in c.execute('SELECT id FROM models')]
        response = jsonify(format='forma-catalog-v1',exported_at=now(),models=records)
        response.headers['Content-Disposition'] = 'attachment; filename="forma-catalog.json"'
        return response

    @app.get('/api/backup')
    def backup():
        # Disk-backed archive avoids retaining the complete model library in RAM.
        archive = tempfile.TemporaryFile()
        try:
            with tempfile.TemporaryDirectory() as temp:
                snapshot = Path(temp)/'forma.sqlite3'
                with db() as source, sqlite3.connect(snapshot) as target:
                    source.backup(target)
                with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
                    z.write(snapshot,'forma.sqlite3')
                    for p in uploads.rglob('*'):
                        if p.is_file():
                            z.write(p,str(p.relative_to(data)))
            archive.seek(0)
            # The WSGI file wrapper closes and removes the temporary archive.
            return send_file(archive,as_attachment=True,download_name='forma-backup.zip',mimetype='application/zip')
        except Exception:
            archive.close()
            raise

    if start_watcher:
        services.start()
        app.extensions['collection_schedules'].start()
    return app


if __name__ == '__main__':
    from waitress import serve
    serve(create_app(start_watcher=True),host=os.environ.get('HOST','0.0.0.0'),port=int(os.environ.get('PORT','8080')),threads=6,max_request_body_size=512*1024*1024)
