"""A narrowly scoped browser-companion bridge; MakerWorld credentials never enter Forma."""
import hashlib
import io
import json
import re
import secrets
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, quote

from flask import abort, g, jsonify, request, send_file
from werkzeug.utils import secure_filename
from previews import normalize_image

PHOTO_HOSTS = ('bblmw.com','makerworld.com','bambulab.com')
MAX_ITEMS = 500


def safe_photo_url(value):
    if not value:
        return ''
    p = urlsplit(str(value))
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not any(p.hostname==h or (p.hostname or '').endswith('.'+h) for h in PHOTO_HOSTS):
        raise ValueError('Use a MakerWorld image URL.')
    return str(value)[:4000]


def normalize_item(item):
    if not isinstance(item,dict):
        raise ValueError('Invalid MakerWorld model.')
    remote_id = str(item.get('id',''))
    if not re.fullmatch(r'\d{1,20}',remote_id):
        raise ValueError('MakerWorld model IDs must be numeric.')
    title = str(item.get('title','')).strip()[:300]
    if not title:
        raise ValueError('Each model needs a title.')
    collections = item.get('collections',[])
    if not isinstance(collections,list) or len(collections)>100:
        raise ValueError('Too many collection memberships.')
    memberships = {}
    for c in collections:
        if not isinstance(c,dict) or not re.fullmatch(r'\d{1,20}|liked',str(c.get('id',''))):
            raise ValueError('Invalid collection ID.')
        memberships[str(c['id'])] = {'id':str(c['id']),'name':str(c.get('name','')).strip()[:100] or 'MakerWorld collection'}
    tags = item.get('tags',[])
    if not isinstance(tags,list):
        raise ValueError('Tags must be a list.')
    profile = str(item.get('profile_id',''))
    if profile and not re.fullmatch(r'\d{1,20}',profile):
        raise ValueError('Invalid print profile ID.')
    return {'id':remote_id,'title':title,'creator':str(item.get('creator',''))[:300],
            'source_url':'https://makerworld.com/en/models/'+remote_id,
            'description':str(item.get('description',''))[:30000],
            'tags':list(dict.fromkeys(str(t).strip()[:80] for t in tags if str(t).strip()))[:100],
            'image_url':safe_photo_url(item.get('image_url','')),'profile_id':profile,
            'favorite':bool(item.get('favorite',False)),'collections':list(memberships.values())}


class MakerWorldBridge:
    def __init__(self, app, db, data, insert_model, add_file, model, fetch_public, now):
        self.app,self.db,self.data = app,db,data
        self.insert_model,self.add_file,self.model,self.fetch_public,self.now = insert_model,add_file,model,fetch_public,now
        self.staging = data/'makerworld-staging';self.staging.mkdir(exist_ok=True)
        self.photos = data/'makerworld-photos';self.photos.mkdir(exist_ok=True)
        self.photo_lock=threading.Lock()
        self.photo_jobs=ThreadPoolExecutor(max_workers=2,thread_name_prefix='forma-source-photo')
        with db() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS companion_codes(hash TEXT PRIMARY KEY,expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS companion_devices(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,last_used TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS makerworld_batches(id TEXT PRIMARY KEY,device_id TEXT NOT NULL REFERENCES companion_devices(id),title TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,warnings TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS makerworld_sources(remote_id TEXT PRIMARY KEY,model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,profile_id TEXT NOT NULL DEFAULT '');
              CREATE TABLE IF NOT EXISTS makerworld_memberships(model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,remote_collection TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(model_id,remote_collection));
            ''')
        self.routes()

    @staticmethod
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()

    def authorized_bridge_request(self):
        """Called before the normal Basic-auth gate for these exact companion routes."""
        origin=request.headers.get('Origin','')
        if origin and not re.fullmatch(r'(chrome|moz)-extension://[A-Za-z0-9-]+',origin):
            abort(403,'Only the browser companion can use this endpoint.')
        if request.method=='OPTIONS':return
        if request.path=='/api/companion/connect' and request.method=='POST':return
        auth=request.authorization
        value=auth.token if auth and auth.type=='bearer' else ''
        if not value or len(value)>200:abort(401,'Pair this companion with Forma first.')
        with self.db() as c:
            device=c.execute('SELECT * FROM companion_devices WHERE token_hash=? AND revoked=0',(self.digest(value),)).fetchone()
            if not device:abort(401,'This companion pairing expired or was revoked. Pair again.')
            g.companion_id=device['id']
            c.execute('UPDATE companion_devices SET last_used=? WHERE id=?',(self.now(),device['id']))

    def batch(self,c,id,device=False):
        row=c.execute('SELECT * FROM makerworld_batches WHERE id=?',(id,)).fetchone()
        if not row or device and row['device_id']!=g.companion_id:abort(404,'Import batch not found.')
        return {**dict(row),'items':json.loads(row['payload']),'warnings':json.loads(row['warnings'])}

    def image(self,item):
        url=safe_photo_url(item.get('image_url',''))
        if not url:raise ValueError('No source photo is available.')
        path=self.photos/(hashlib.sha256(url.encode()).hexdigest()+'.png')
        if path.exists():return path
        with self.photo_lock:
            if path.exists():return path
            body,mime,_=self.fetch_public(url,16*1024*1024)
            if not mime.startswith('image/'):raise ValueError('This URL did not return a photo.')
            body=normalize_image(body)
            temp=path.with_suffix('.tmp');temp.write_bytes(body);temp.replace(path)
        return path

    def attach_photo(self,id,item):
        try:
            source=self.image(item)
            with self.db() as c:
                m=c.execute('SELECT cover_id FROM models WHERE id=?',(id,)).fetchone()
                if not m or m['cover_id']:return
                folder=self.data/'files'/id;folder.mkdir(exist_ok=True)
                destination=folder/(str(uuid.uuid4())+'.png');shutil.copyfile(source,destination)
                self.add_file(c,id,'MakerWorld cover.png',destination,'image')
        except Exception:
            # The source link and model stay usable; a user can add a cover later.
            self.app.logger.info('A MakerWorld source photo could not be loaded for model %s',id)

    def staged_files(self,batch_id,remote_id):
        return [p for p in (self.staging/batch_id).glob(remote_id+'.*') if p.suffix in ('.3mf','.stl','.obj','.zip')]

    @staticmethod
    def file_hash(path):
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        return digest.hexdigest()

    def existing(self,c,item,batch_id=None):
        found=c.execute('SELECT model_id FROM makerworld_sources WHERE remote_id=?',(item['id'],)).fetchone()
        if found:return found[0]
        for row in c.execute("SELECT id,source_url FROM models WHERE source_url LIKE '%makerworld.com/%'"):
            p=urlsplit(row['source_url'])
            if (p.hostname or '').removeprefix('www.')=='makerworld.com' and re.search(r'/models/'+re.escape(item['id'])+r'(?:\D|$)',p.path):return row['id']
        if batch_id:
            for staged in self.staged_files(batch_id,item['id']):
                sha=self.file_hash(staged)
                for f in c.execute('SELECT * FROM files WHERE sha256=? AND kind=?',(sha,'model')):
                    try:
                        path=Path(f['path']).resolve();root=self.data/'files' if not f['external'] else Path(self.app.config['FORMA_LIBRARY_ROOT'])
                        stat=path.stat()
                        if path.is_relative_to(root) and stat.st_size==f['hash_size'] and stat.st_mtime_ns==f['hash_mtime_ns']:return f['model_id']
                    except OSError:continue
        return None

    def clean_expired(self):
        cutoff=time.time()-7*86400
        with self.db() as c:
            rows=c.execute("SELECT id,created_at FROM makerworld_batches WHERE state IN ('receiving','pending','dismissed')").fetchall()
            for r in rows:
                try:
                    from datetime import datetime
                    expired=datetime.fromisoformat(r['created_at']).timestamp()<cutoff
                except ValueError:expired=False
                if expired:
                    shutil.rmtree(self.staging/r['id'],ignore_errors=True)
                    c.execute('DELETE FROM makerworld_batches WHERE id=?',(r['id'],))

    def create_batch(self, items, title, device_id, state='receiving'):
        if not isinstance(items,list) or not items or len(items)>MAX_ITEMS:raise ValueError('Import between 1 and 500 models at a time.')
        normalized={}
        for raw in items:
            item=normalize_item(raw)
            if item['id'] in normalized:
                previous=normalized[item['id']]
                item['collections']=list({c['id']:c for c in previous['collections']+item['collections']}.values())
            normalized[item['id']]=item
        id=str(uuid.uuid4());self.clean_expired()
        with self.db() as c:
            pending=c.execute("SELECT count(*) FROM makerworld_batches WHERE device_id=? AND state IN ('receiving','pending')",(device_id,)).fetchone()[0]
            if pending>=20:raise ValueError('Review or dismiss earlier import batches first.')
            c.execute('INSERT INTO makerworld_batches(id,device_id,title,state,payload,created_at) VALUES (?,?,?,?,?,?)',(id,device_id,str(title)[:200],state,json.dumps(list(normalized.values())),self.now()))
        return {'id':id,'count':len(normalized)}

    def receive_file(self,id,remote_id,device=False):
        with self.db() as c:
            batch=self.batch(c,id,device)
            if batch['state']!='receiving':raise ValueError('This batch is no longer receiving files.')
            if remote_id not in {m['id'] for m in batch['items']}:abort(404)
        upload=request.files.get('file')
        if not upload or Path(upload.filename).suffix.lower() not in ('.3mf','.stl','.zip','.obj'):raise ValueError('Upload a 3MF, STL, OBJ or ZIP model file.')
        folder=self.staging/id;folder.mkdir(exist_ok=True)
        destination=folder/(remote_id+Path(upload.filename).suffix.lower());upload.save(destination)
        if destination.stat().st_size>256*1024*1024:
            destination.unlink();raise ValueError('Browser imports are limited to 256 MB per model file.')
        return {'ok':True}

    def finalize(self,id,warnings,device=False):
        if not isinstance(warnings,list):raise ValueError('Invalid warning list.')
        with self.db() as c:
            batch=self.batch(c,id,device)
            if batch['state']!='receiving':raise ValueError('This import batch was already finalized.')
            c.execute("UPDATE makerworld_batches SET state='pending',warnings=? WHERE id=?",(json.dumps([str(w)[:500] for w in warnings[:500]]),id))
        return {'ok':True}

    def routes(self):
        app=self.app

        @app.post('/api/makerworld/pair-code')
        def mw_pair_code():
            code=secrets.token_urlsafe(24)
            with self.db() as c:
                c.execute('DELETE FROM companion_codes WHERE expires<?',(time.time(),))
                c.execute('INSERT INTO companion_codes VALUES (?,?)',(self.digest(code),time.time()+600))
            return jsonify(code=code,expires_in=600)

        @app.post('/api/companion/connect')
        def companion_connect():
            p=request.get_json();code=str(p.get('code',''));name=str(p.get('name','Forma browser companion'))[:100]
            if len(code)>100:abort(401,'Invalid or expired pairing code.')
            token=secrets.token_urlsafe(32);id=str(uuid.uuid4())
            with self.db() as c:
                used=c.execute('DELETE FROM companion_codes WHERE hash=? AND expires>?',(self.digest(code),time.time())).rowcount
                if not used:abort(401,'Invalid, used or expired pairing code. Create a new code in Forma.')
                c.execute('INSERT INTO companion_devices VALUES (?,?,?,?,?,0)',(id,name,self.digest(token),self.now(),self.now()))
            return jsonify(token=token,device_id=id,name=name),201

        @app.get('/api/makerworld/devices')
        def companion_devices():
            with self.db() as c:
                return jsonify(devices=[dict(r) for r in c.execute('SELECT id,name,created_at,last_used FROM companion_devices WHERE revoked=0')])

        @app.delete('/api/makerworld/devices/<id>')
        def revoke_companion(id):
            with self.db() as c:c.execute('UPDATE companion_devices SET revoked=1 WHERE id=?',(id,))
            return jsonify(ok=True)

        @app.post('/api/companion/batches')
        def receive_batch():
            p=request.get_json()
            return jsonify(self.create_batch(p.get('items',[]),p.get('title','MakerWorld import'),g.companion_id)),201

        @app.post('/api/companion/batches/<id>/files/<remote_id>')
        def receive_model_file(id,remote_id):
            return jsonify(self.receive_file(id,remote_id,True))

        @app.post('/api/companion/batches/<id>/finish')
        def finish_batch(id):
            return jsonify(self.finalize(id,request.get_json().get('warnings',[]),True))

        @app.get('/api/makerworld/bookmarklet')
        def safari_bookmarklet():
            source=(Path(app.static_folder)/'makerworld-bookmarklet.js').read_text()
            source=source.replace('__FORMA_ORIGIN__',json.dumps(request.host_url.rstrip('/')))
            return jsonify(url='javascript:'+quote(source,safe=''))

        @app.post('/api/makerworld/browser-batch')
        def safari_batch():
            p=request.get_json()
            if not isinstance(p,dict) or p.get('format')!='forma-makerworld-v1':raise ValueError('Choose a Forma MakerWorld export.')
            with self.db() as c:
                device=c.execute("SELECT id FROM companion_devices WHERE name='Safari browser import' AND revoked=1 LIMIT 1").fetchone()
                if device:device_id=device[0]
                else:
                    device_id=str(uuid.uuid4())
                    c.execute('INSERT INTO companion_devices VALUES (?,?,?,?,?,1)',(device_id,'Safari browser import',self.digest(secrets.token_urlsafe(32)),self.now(),self.now()))
            result=self.create_batch(p.get('items',[]),p.get('title','Safari · MakerWorld'),device_id,'receiving' if p.get('has_files') else 'pending')
            warnings=p.get('warnings',[])
            if not isinstance(warnings,list):warnings=[]
            with self.db() as c:c.execute('UPDATE makerworld_batches SET warnings=? WHERE id=?',(json.dumps([str(w)[:500] for w in warnings[:500]]),result['id']))
            return jsonify(result),201

        @app.post('/api/makerworld/batches/<id>/files/<remote_id>')
        def safari_file(id,remote_id):return jsonify(self.receive_file(id,remote_id))

        @app.post('/api/makerworld/batches/<id>/finish')
        def safari_finish(id):return jsonify(self.finalize(id,request.get_json().get('warnings',[])))

        @app.get('/api/companion/batches/<id>')
        def companion_batch_status(id):
            with self.db() as c:b=self.batch(c,id,True)
            return jsonify(id=id,state=b['state'],count=len(b['items']))

        @app.get('/api/makerworld/batches')
        def list_batches():
            with self.db() as c:
                rows=c.execute("SELECT id,title,state,created_at,payload,warnings FROM makerworld_batches ORDER BY created_at DESC LIMIT 30").fetchall()
                return jsonify(batches=[{k:r[k] for k in ('id','title','state','created_at')}|{'count':len(json.loads(r['payload'])),'warnings':json.loads(r['warnings'])} for r in rows])

        @app.get('/api/makerworld/batches/<id>')
        def review_batch(id):
            with self.db() as c:
                batch=self.batch(c,id)
                items=[]
                for item in batch['items']:
                    files=self.staged_files(id,item['id'])
                    items.append({**item,'existing_model_id':self.existing(c,item,id),'has_file':bool(files)})
            return jsonify(id=id,title=batch['title'],state=batch['state'],items=items,warnings=batch['warnings'])

        @app.get('/api/makerworld/batches/<id>/photo/<remote_id>')
        def batch_photo(id,remote_id):
            with self.db() as c:batch=self.batch(c,id)
            item=next((i for i in batch['items'] if i['id']==remote_id),None)
            if not item:abort(404)
            try:return send_file(self.image(item),mimetype='image/png',conditional=True)
            except Exception:abort(404,'The source photo could not be loaded.')

        @app.post('/api/makerworld/batches/<id>/apply')
        def apply_batch(id):
            p=request.get_json();selected=p.get('ids',[])
            if not isinstance(selected,list) or not selected or not all(isinstance(i,str) for i in selected):raise ValueError('Choose models to import.')
            photos=[];copied=[];created=0;linked=0
            try:
                with self.db() as c:
                    batch=self.batch(c,id)
                    if batch['state']!='pending':raise ValueError('This batch is not waiting for review.')
                    if not set(selected)<={i['id'] for i in batch['items']}:raise ValueError('Unknown model selection.')
                    for item in batch['items']:
                        if item['id'] not in selected:continue
                        model_id=self.existing(c,item,id)
                        if not model_id:
                            model_id=self.insert_model(c,{**item,'collection':''})
                            created+=1
                        else:linked+=1
                        c.execute('INSERT OR IGNORE INTO makerworld_sources(remote_id,model_id,profile_id) VALUES (?,?,?)',(item['id'],model_id,item['profile_id']))
                        for collection in item['collections']:
                            c.execute('INSERT INTO makerworld_memberships VALUES (?,?,?) ON CONFLICT(model_id,remote_collection) DO UPDATE SET name=excluded.name',(model_id,collection['id'],collection['name']))
                        for source in self.staged_files(id,item['id']):
                            sha=self.file_hash(source)
                            if c.execute('SELECT id FROM files WHERE model_id=? AND sha256=?',(model_id,sha)).fetchone():continue
                            folder=self.data/'files'/model_id;folder.mkdir(exist_ok=True)
                            destination=folder/(str(uuid.uuid4())+source.suffix);shutil.copyfile(source,destination);copied.append(destination)
                            name=(secure_filename(item['title']) or 'MakerWorld-'+item['id'])+source.suffix
                            self.add_file(c,model_id,name,destination,'model',sha=sha)
                        photos.append((model_id,item))
                    c.execute("UPDATE makerworld_batches SET state='imported' WHERE id=?",(id,))
            except Exception:
                for path in copied:path.unlink(missing_ok=True)
                raise
            shutil.rmtree(self.staging/id,ignore_errors=True)
            for model_id,item in photos:
                if item.get('image_url'):self.photo_jobs.submit(self.attach_photo,model_id,item)
            return jsonify(created=created,linked=linked,files=len(copied),photos_queued=sum(bool(i.get('image_url')) for _,i in photos))

        @app.delete('/api/makerworld/batches/<id>')
        def dismiss_batch(id):
            with self.db() as c:
                self.batch(c,id);c.execute("UPDATE makerworld_batches SET state='dismissed' WHERE id=?",(id,))
            shutil.rmtree(self.staging/id,ignore_errors=True)
            return jsonify(ok=True)

        @app.get('/api/makerworld/companion.zip')
        def companion_download():
            archive=io.BytesIO();root=Path(app.root_path)/'companion'
            with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for path in sorted(root.rglob('*')):
                    if path.is_file():z.write(path,'forma-makerworld-companion/'+str(path.relative_to(root)))
                z.write(Path(app.static_folder)/'app-icon.png','forma-makerworld-companion/icon.png')
            archive.seek(0)
            return send_file(archive,mimetype='application/zip',as_attachment=True,download_name='forma-makerworld-companion.zip')
