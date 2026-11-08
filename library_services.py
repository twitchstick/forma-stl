"""Read-only folder monitoring, reviewable synchronization and content hashes."""
import hashlib
import json
import threading
import time
from pathlib import Path


class LibraryServices:
    def __init__(self, db, library, uploads, model_extensions, image_extensions, insert_model, add_file, now):
        self.db, self.library, self.uploads = db, library, uploads
        self.models, self.images = model_extensions, image_extensions
        self.insert_model, self.add_file, self.now = insert_model, add_file, now
        self.scan_lock = threading.Lock()
        self.hash_lock = threading.Lock()
        self.hash_state = {'running':False,'processed':0,'total':0,'errors':0,'completed_at':None}
        with db() as c:
            c.executescript('''
              CREATE TABLE IF NOT EXISTS library_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS library_review (
                id INTEGER PRIMARY KEY AUTOINCREMENT, folder_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, signature TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', detected_at TEXT NOT NULL
              );
            ''')
            for key,value in [('watch_enabled','true'),('watch_interval','900'),('last_scan',''),('scan_error','')]:
                c.execute('INSERT OR IGNORE INTO library_settings VALUES (?,?)',(key,value))

    def settings(self):
        with self.db() as c:
            settings = dict(c.execute('SELECT key,value FROM library_settings'))
            count = c.execute("SELECT count(*) FROM library_review WHERE state='pending'").fetchone()[0]
        return {'enabled':settings['watch_enabled']=='true','interval':int(settings['watch_interval']),
                'last_scan':settings['last_scan'],'error':settings['scan_error'],'pending':count,
                'scanning':self.scan_lock.locked()}

    def configure(self, payload):
        enabled = payload.get('enabled')
        interval = payload.get('interval',self.settings()['interval'])
        if not isinstance(enabled,bool) or not isinstance(interval,int) or isinstance(interval,bool) or not 30<=interval<=3600:
            raise ValueError('Choose an enabled state and an interval from 30 to 3600 seconds.')
        with self.db() as c:
            c.execute('UPDATE library_settings SET value=? WHERE key=?',('true' if enabled else 'false','watch_enabled'))
            c.execute('UPDATE library_settings SET value=? WHERE key=?',(str(interval),'watch_interval'))
        return self.settings()

    def inventory(self):
        if not self.library.is_dir():
            raise ValueError('The model share is unavailable. Check the /library mount.')
        groups = {}
        for p in sorted(self.library.rglob('*')):
            if p.is_symlink() or not p.is_file() or p.suffix.lower() not in self.models|self.images:
                continue
            p = p.resolve()
            if not p.is_relative_to(self.library):
                continue
            key = str(p.parent.relative_to(self.library)) if p.parent!=self.library else p.stem
            stat = p.stat()
            groups.setdefault(key,[]).append({'name':p.name,'path':str(p),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'kind':'image' if p.suffix.lower() in self.images else 'model'})
        return {k:v for k,v in groups.items() if any(f['kind']=='model' for f in v)}

    @staticmethod
    def signature(files):
        return hashlib.sha256(json.dumps(sorted((f['path'],f['size'],f['mtime_ns']) for f in files)).encode()).hexdigest()

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return self.settings()
        try:
            inventory = self.inventory()
            with self.db() as c:
                existing = {r['import_key'][7:]:dict(r) for r in c.execute("SELECT * FROM models WHERE import_key LIKE 'folder:%'")}
                active = set()
                for key in sorted(set(inventory)|set(existing)):
                    files = inventory.get(key,[])
                    record = existing.get(key)
                    old = [dict(r) for r in c.execute('SELECT * FROM files WHERE model_id=? AND external=1',(record['id'],))] if record else []
                    if record and self.signature(old)==self.signature(files):
                        c.execute('DELETE FROM library_review WHERE folder_key=?',(key,))
                        continue
                    if not record and not files:
                        continue
                    kind = 'new' if not record else 'missing' if not files else 'changed'
                    signature = self.signature(files)
                    prior = c.execute('SELECT * FROM library_review WHERE folder_key=?',(key,)).fetchone()
                    active.add(key)
                    if prior and prior['signature']==signature and prior['kind']==kind:
                        continue
                    payload = {'title':record['title'] if record else Path(key).name.replace('_',' ').replace('-',' '),
                               'model_id':record['id'] if record else None,'files':files,'previous_count':len(old)}
                    c.execute("INSERT INTO library_review(folder_key,kind,signature,payload,state,detected_at) VALUES (?,?,?,?,?,?) ON CONFLICT(folder_key) DO UPDATE SET kind=excluded.kind,signature=excluded.signature,payload=excluded.payload,state='pending',detected_at=excluded.detected_at",
                              (key,kind,signature,json.dumps(payload),'pending',self.now()))
                for row in c.execute('SELECT folder_key FROM library_review').fetchall():
                    if row[0] not in active:
                        c.execute('DELETE FROM library_review WHERE folder_key=?',(row[0],))
                c.execute('UPDATE library_settings SET value=? WHERE key=?',(self.now(),'last_scan'))
                c.execute("UPDATE library_settings SET value='' WHERE key='scan_error'")
        except Exception as error:
            with self.db() as c:
                c.execute('UPDATE library_settings SET value=? WHERE key=?',(str(error)[:500],'scan_error'))
            raise
        finally:
            self.scan_lock.release()
        return self.settings()

    def reviews(self):
        with self.db() as c:
            return [{**dict(r),'payload':json.loads(r['payload'])} for r in c.execute("SELECT * FROM library_review WHERE state='pending' ORDER BY detected_at DESC")]

    def review(self, ids, action):
        if not isinstance(ids,list) or not ids or len(ids)>500 or action not in ('approve','ignore'):
            raise ValueError('Select up to 500 items and an approve or ignore action.')
        if not self.scan_lock.acquire(blocking=False):
            raise ValueError('A folder scan is running. Try again shortly.')
        try:
            inventory = self.inventory() if action=='approve' else {}
            applied = 0
            with self.db() as c:
                for id in set(ids):
                    r = c.execute("SELECT * FROM library_review WHERE id=? AND state='pending'",(id,)).fetchone()
                    if not r:
                        raise ValueError('An item was already reviewed. Refresh the list.')
                    if action=='ignore' or r['kind']=='missing':
                        # Missing share files are never removed from the catalog automatically.
                        c.execute("UPDATE library_review SET state='ignored' WHERE id=?",(id,)); applied+=1
                        continue
                    files = inventory.get(r['folder_key'],[])
                    if self.signature(files)!=r['signature']:
                        raise ValueError('A folder changed again. Scan again before approving it.')
                    old = c.execute('SELECT id,cover_id FROM models WHERE import_key=?',('folder:'+r['folder_key'],)).fetchone()
                    if not old:
                        id_model = self.insert_model(c,{'title':json.loads(r['payload'])['title']},'folder:'+r['folder_key'])
                    else:
                        id_model = old['id']
                    old_files = {f['path']:dict(f) for f in c.execute('SELECT * FROM files WHERE model_id=? AND external=1',(id_model,))}
                    current_paths = {f['path'] for f in files}
                    for path,f in old_files.items():
                        if path not in current_paths:
                            c.execute("UPDATE models SET cover_id='' WHERE id=? AND cover_id=?",(id_model,f['id']))
                            c.execute('DELETE FROM files WHERE id=?',(f['id'],))
                    for f in files:
                        if f['path'] in old_files:
                            previous = old_files[f['path']]
                            changed = previous['size']!=f['size'] or previous['mtime_ns']!=f['mtime_ns']
                            c.execute('UPDATE files SET size=?,mtime_ns=?,sha256=? WHERE id=?',(f['size'],f['mtime_ns'],'' if changed else previous['sha256'],previous['id']))
                        else:
                            self.add_file(c,id_model,f['name'],Path(f['path']),f['kind'],external=True)
                    cover = c.execute("SELECT id FROM files WHERE model_id=? AND kind='image' LIMIT 1",(id_model,)).fetchone()
                    if cover:
                        c.execute("UPDATE models SET cover_id=? WHERE id=? AND cover_id=''",(cover[0],id_model))
                    c.execute('UPDATE models SET updated_at=? WHERE id=?',(self.now(),id_model))
                    c.execute('DELETE FROM library_review WHERE id=?',(r['id'],))
                    applied+=1
            return {'reviewed':applied}
        finally:
            self.scan_lock.release()

    def start(self):
        def watch():
            next_scan = 0
            while True:
                try:
                    settings = self.settings()
                    if settings['enabled'] and time.monotonic()>=next_scan:
                        self.scan()
                        next_scan = time.monotonic()+settings['interval']
                except Exception:
                    next_scan = time.monotonic()+60
                time.sleep(5)
        threading.Thread(target=watch,name='forma-folder-watch',daemon=True).start()

    def index_duplicates(self):
        if not self.hash_lock.acquire(blocking=False):
            return
        try:
            with self.db() as c:
                files = [dict(r) for r in c.execute("SELECT * FROM files WHERE kind='model'")]
            self.hash_state.update(running=True,processed=0,total=len(files),errors=0)
            for f in files:
                try:
                    path = Path(f['path']).resolve()
                    if not path.is_relative_to(self.library if f['external'] else self.uploads):
                        raise ValueError('Outside the model folder.')
                    before = path.stat()
                    if not f['sha256'] or before.st_size!=f['hash_size'] or before.st_mtime_ns!=f['hash_mtime_ns']:
                        digest = hashlib.sha256()
                        with path.open('rb') as stream:
                            for chunk in iter(lambda:stream.read(1024*1024),b''):
                                digest.update(chunk)
                        after = path.stat()
                        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                            raise ValueError('File is still changing.')
                        with self.db() as c:
                            # A watcher review may have updated the row while hashing.
                            c.execute('UPDATE files SET sha256=?,hash_size=?,hash_mtime_ns=? WHERE id=? AND path=?',(digest.hexdigest(),after.st_size,after.st_mtime_ns,f['id'],f['path']))
                except (OSError,ValueError):
                    self.hash_state['errors']+=1
                self.hash_state['processed']+=1
            self.hash_state['completed_at']=self.now()
        finally:
            self.hash_state['running']=False
            self.hash_lock.release()

    def duplicate_report(self):
        with self.db() as c:
            rows = [dict(r) for r in c.execute("SELECT f.*,m.title FROM files f JOIN models m ON f.model_id=m.id WHERE f.kind='model' AND f.sha256<>'' AND f.sha256 IN (SELECT sha256 FROM files WHERE kind='model' AND sha256<>'' GROUP BY sha256 HAVING count(*)>1)")]
        groups = {}
        for f in rows:
            try:
                path = Path(f['path']).resolve()
                if not path.is_relative_to(self.library if f['external'] else self.uploads):
                    continue
                stat = path.stat()
                if stat.st_size!=f['hash_size'] or stat.st_mtime_ns!=f['hash_mtime_ns']:
                    continue
            except OSError:
                continue
            groups.setdefault(f['sha256'],[]).append({'id':f['id'],'model_id':f['model_id'],'title':f['title'],'name':f['name'],'size':f['size']})
        return {'state':dict(self.hash_state),'groups':[{'sha256':key,'files':files,'extra_bytes':sum(f['size'] for f in files[1:])} for key,files in groups.items() if len(files)>1]}

    def start_duplicates(self):
        if not self.hash_state['running']:
            self.hash_state['running']=True
            threading.Thread(target=self.index_duplicates,name='forma-duplicate-index',daemon=True).start()
        return self.duplicate_report()
