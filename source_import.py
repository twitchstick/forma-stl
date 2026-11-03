"""Background metadata/photo imports with persistent progress and edit protection."""
import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import abort, jsonify, request
from previews import normalize_image
from source_metadata import parse_source, source_fields, web_url

FIELDS=('creator','description','license','license_url','attribution')

class SourceImports:
    def __init__(self,app,db,data,library,model,add_file,fetch_public,now):
        self.app,self.db,self.data,self.library=app,db,data,library
        self.model,self.add_file,self.fetch_public,self.now=model,add_file,fetch_public,now
        self.lock=threading.RLock();self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='forma-source-import')
        with db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS source_jobs(model_id TEXT PRIMARY KEY REFERENCES models(id) ON DELETE CASCADE,source_url TEXT NOT NULL,state TEXT NOT NULL,phase TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,filled TEXT NOT NULL DEFAULT '[]',notes TEXT NOT NULL DEFAULT '[]',updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_photos(model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,url TEXT NOT NULL,source_url TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',file_id TEXT REFERENCES files(id) ON DELETE SET NULL,content_hash TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',PRIMARY KEY(model_id,url));
            CREATE TABLE IF NOT EXISTS source_locks(model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,field TEXT NOT NULL,PRIMARY KEY(model_id,field));
            ''')
            c.execute("UPDATE source_jobs SET state='interrupted',phase='Retry to continue after restart.' WHERE state IN ('queued','running')")
        self.routes()

    def mark_edits(self,c,id,old,clean):
        for k in (*FIELDS,'tags'):
            previous=json.dumps(old[k]) if k=='tags' else old[k]
            if previous!=clean[k]:c.execute('INSERT OR IGNORE INTO source_locks VALUES (?,?)',(id,k))

    def status(self,id):
        with self.db() as c:
            m=self.model(c,id);job=c.execute('SELECT * FROM source_jobs WHERE model_id=?',(id,)).fetchone()
            photos=[dict(p) for p in c.execute('SELECT url,state,file_id,error FROM source_photos WHERE model_id=? AND source_url=?',(id,m['source_url']))]
            if job:
                job=dict(job);job['filled']=json.loads(job['filled']);job['notes']=json.loads(job['notes'])
                if job['source_url']!=m['source_url']:job['phase']='Source link changed. Retry using the current link.'
            return {'job':job,'photos':photos,'model':m}

    def enqueue(self,id,seed=None,fetch_page=True):
        seed=seed or {};safe={k:str(seed.get(k,''))[:30000 if k=='description' else 300] for k in ('creator','description')}
        safe.update(source_fields(seed));tags=seed.get('tags',[])
        safe['tags']=[t[:80] for t in tags if isinstance(t,str)][:100] if isinstance(tags,list) else []
        with self.lock,self.db() as c:
            m=self.model(c,id);url=web_url(m['source_url'])
            if not url:raise ValueError('Add a source URL through Edit model first.')
            current=c.execute('SELECT state FROM source_jobs WHERE model_id=?',(id,)).fetchone()
            if current and current['state'] in ('queued','running'):return {'queued':False,**self.status(id)}
            if c.execute("SELECT count(*) FROM source_jobs WHERE state IN ('queued','running')").fetchone()[0]>=1000:raise ValueError('The import queue is full. Retry after current imports finish.')
            c.execute("INSERT INTO source_jobs(model_id,source_url,state,phase,updated_at) VALUES (?,?,'queued','Waiting to read source details',?) ON CONFLICT(model_id) DO UPDATE SET source_url=excluded.source_url,state='queued',phase=excluded.phase,done=0,total=0,filled='[]',notes='[]',updated_at=excluded.updated_at",(id,url,self.now()))
        self.executor.submit(self.run,id,url,safe,fetch_page)
        return {'queued':True}

    def update(self,id,**values):
        for k in ('filled','notes'):
            if k in values:values[k]=json.dumps(values[k])
        values['updated_at']=self.now()
        with self.db() as c:c.execute('UPDATE source_jobs SET '+','.join(k+'=?' for k in values)+' WHERE model_id=?',(*values.values(),id))

    def still_current(self,c,id,url):
        m=c.execute('SELECT * FROM models WHERE id=?',(id,)).fetchone()
        if not m or m['source_url']!=url:raise ValueError('The source link changed or the model was removed. Retry with its current link.')
        return m

    def run(self,id,url,seed,fetch_page):
        notes=[];filled=[];done=0;total=0
        try:
            self.update(id,state='running',phase='Reading source metadata')
            metadata={**seed};images=list(seed['image_urls'])
            if fetch_page:
                try:
                    body,mime,final=self.fetch_public(url)
                    if mime not in ('text/html','application/xhtml+xml'):raise ValueError('Not an HTML model page.')
                    read=parse_source(body,final)
                    for k in (*FIELDS,'tags'):
                        if not metadata.get(k):metadata[k]=read.get(k,'' if k!='tags' else [])
                    images+=read['image_urls']
                except Exception:
                    notes.append('The source page did not share readable metadata. Use its Safari bookmark while signed in, or add details manually.')
            with self.db() as c:
                m=self.still_current(c,id,url);locked={r[0] for r in c.execute('SELECT field FROM source_locks WHERE model_id=?',(id,))}
                for k in FIELDS:
                    value=str(metadata.get(k,''))[:{'description':30000,'attribution':2000,'license':500,'license_url':4000,'creator':300}[k]]
                    if k=='license_url':value=web_url(value)
                    if value and not m[k] and k not in locked:
                        if c.execute("UPDATE models SET "+k+"=? WHERE id=? AND "+k+"='' AND source_url=? AND NOT EXISTS(SELECT 1 FROM source_locks WHERE model_id=? AND field=?)",(value,id,url,id,k)).rowcount:filled.append(k)
                if metadata.get('tags') and json.loads(m['tags'])==[] and 'tags' not in locked:
                    if c.execute("UPDATE models SET tags=? WHERE id=? AND tags='[]' AND source_url=? AND NOT EXISTS(SELECT 1 FROM source_locks WHERE model_id=? AND field='tags')",(json.dumps(metadata['tags']),id,url,id)).rowcount:filled.append('tags')
                if filled:c.execute('UPDATE models SET updated_at=? WHERE id=?',(self.now(),id))
                images += [r[0] for r in c.execute('SELECT url FROM source_photos WHERE model_id=? AND source_url=?',(id,url))]
                images=list(dict.fromkeys(u for u in (web_url(v) for v in images) if u))[:20]
                for image in images:c.execute("INSERT INTO source_photos(model_id,url,source_url) VALUES (?,?,?) ON CONFLICT(model_id,url) DO UPDATE SET source_url=excluded.source_url",(id,image,url))
            total=len(images);self.update(id,total=total,filled=filled,notes=notes)
            for index,image in enumerate(images):
                self.update(id,phase='Fetching photo '+str(index+1)+' of '+str(total))
                try:self.attach(id,url,image)
                except Exception:
                    with self.db() as c:c.execute("UPDATE source_photos SET state='failed',error='Photo could not be fetched or decoded. It may require website sign-in or block downloads.' WHERE model_id=? AND url=?",(id,image))
                done+=1;self.update(id,done=done)
            with self.db() as c:
                self.still_current(c,id,url)
                failed=c.execute("SELECT count(*) FROM source_photos WHERE model_id=? AND source_url=? AND state='failed'",(id,url)).fetchone()[0]
                m=c.execute('SELECT * FROM models WHERE id=?',(id,)).fetchone()
                missing=[label for k,label in [('creator','creator'),('description','description'),('license','license name or terms'),('attribution','attribution text')] if not m[k] and not (k=='license' and m['license_url'])]
            if not images:notes.append('No source photos were provided. Add photos manually or recapture the model’s own page with its Safari bookmark.')
            if failed:notes.append(str(failed)+' photo(s) could not be imported. Retry later or upload them manually.')
            if missing:notes.append('Not supplied or intentionally left blank: '+', '.join(missing)+'.')
            self.update(id,state='partial' if notes else 'complete',phase='Finished',done=done,total=total,filled=filled,notes=notes)
        except Exception:
            self.update(id,state='failed',phase='Import stopped. Retry using the current source link.',done=done,total=total,notes=notes+['No existing edits or photos were replaced.'])

    def attach(self,id,source_url,url):
        with self.db() as c:
            self.still_current(c,id,source_url)
            old=c.execute('SELECT file_id FROM source_photos WHERE model_id=? AND url=?',(id,url)).fetchone()
            existing=c.execute('SELECT * FROM files WHERE id=?',(old[0],)).fetchone() if old and old[0] else None
            if existing:
                path=Path(existing['path']).resolve();root=self.library if existing['external'] else self.data/'files'
                if path.is_relative_to(root) and path.is_file():
                    c.execute("UPDATE source_photos SET state='imported',error='' WHERE model_id=? AND url=?",(id,url));return
        body,mime,_=self.fetch_public(url,16*1024*1024)
        if not mime.startswith('image/'):raise ValueError('Not an image.')
        body=normalize_image(body);sha=hashlib.sha256(body).hexdigest();created=None
        try:
            with self.db() as c:
                self.still_current(c,id,source_url)
                # Compare normalized bytes so previous cover imports and alternate URLs do not make duplicate gallery entries.
                file_id=None
                for f in c.execute("SELECT * FROM files WHERE model_id=? AND kind='image'",(id,)):
                    path=Path(f['path']).resolve();root=self.library if f['external'] else self.data/'files'
                    try:
                        if path.is_relative_to(root) and path.stat().st_size<=16*1024*1024 and hashlib.sha256(normalize_image(path.read_bytes())).hexdigest()==sha:file_id=f['id'];break
                    except (OSError,ValueError):continue
                if not file_id:
                    folder=self.data/'files'/id;folder.mkdir(exist_ok=True);created=folder/(str(uuid.uuid4())+'.png');created.write_bytes(body)
                    file_id=self.add_file(c,id,'Source photo.png',created,'image',sha=sha)
                c.execute("UPDATE source_photos SET state='imported',file_id=?,content_hash=?,error='' WHERE model_id=? AND url=?",(file_id,sha,id,url))
        except Exception:
            if created:created.unlink(missing_ok=True)
            raise

    def routes(self):
        @self.app.get('/api/models/<id>/source-import')
        def source_status(id):return jsonify(self.status(id))

        @self.app.post('/api/models/<id>/source-import')
        def retry_source(id):
            p=request.get_json() or {}
            return jsonify(self.enqueue(id,p,fetch_page=True)),202
