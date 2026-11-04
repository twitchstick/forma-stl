"""Daily public collection discovery, staged for owner review; no website credentials."""
import hashlib
import json
import re
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import abort, jsonify, request
from source_metadata import web_url
from website_import import identity, PROVIDERS
from makerworld_import import normalize_item

HOSTS={'makerworld':'makerworld.com',**PROVIDERS}


def collection_url(value,provider=None):
    url=web_url(value)
    if not url:raise ValueError('Enter an HTTPS collection URL from a supported website.')
    p=urlsplit(url);host=(p.hostname or '').removeprefix('www.')
    found=next((k for k,v in HOSTS.items() if v==host),None)
    if p.scheme!='https' or not found or provider and found!=provider:raise ValueError('Use a collection URL from the matching website.')
    if not re.search(r'/(?:collections?|mythangs)(?:/|$)',p.path,re.I):raise ValueError('Use a collection page, rather than a model or creator page.')
    return found,urlunsplit(('https',host,p.path.rstrip('/'),p.query,''))


def next_daily(now,clock,zone):
    tz=ZoneInfo(zone);local=now.astimezone(tz);hour,minute=map(int,clock.split(':'))
    # Calendar arithmetic, followed by a UTC round trip for nonexistent spring times.
    candidate=local.replace(hour=hour,minute=minute,second=0,microsecond=0,fold=0)
    if candidate.astimezone(timezone.utc)<=now.astimezone(timezone.utc):candidate+=timedelta(days=1)
    return candidate.astimezone(timezone.utc).isoformat()


def model_link(url,provider):
    try:
        if provider=='makerworld':
            p=urlsplit(url)
            match=re.search(r'/models/(\d+)',p.path)
            if p.hostname not in ('makerworld.com','www.makerworld.com') or not match:return None
            return match[1],'https://makerworld.com/en/models/'+match[1]
        return identity(url,provider)
    except ValueError:return None


class CollectionPage(HTMLParser):
    def __init__(self,url,provider):
        super().__init__();self.url,self.provider=url,provider;self.items={};self.active=None;self.next='';self.ld=None;self.scripts=[]

    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('a','link') and 'next' in a.get('rel','').lower().split():self.next=urljoin(self.url,a.get('href',''))
        if tag=='a':
            link=model_link(urljoin(self.url,a.get('href','')),self.provider)
            self.active={'id':link[0],'source_url':link[1],'title':a.get('title',''),'image_url':''} if link else None
        if tag=='img' and self.active:
            self.active['image_url']=web_url(a.get('data-src') or a.get('src',''),self.url)
            self.active['title']=self.active['title'] or a.get('alt','')
        if tag=='script' and a.get('type','').lower()=='application/ld+json':self.ld=''

    def handle_data(self,data):
        if self.active and not self.active['title']:self.active['title']=data.strip()[:300]
        if self.ld is not None:self.ld+=data

    def handle_endtag(self,tag):
        if tag=='a' and self.active:
            item=self.active;self.active=None
            if item['title']:self.items.setdefault(item['id'],item)
        if tag=='script' and self.ld is not None:
            try:self.scripts.append(json.loads(self.ld))
            except (ValueError,RecursionError):pass
            self.ld=None

    def finish(self):
        for raw in self.scripts:
            nodes=raw if isinstance(raw,list) else [raw]
            for node in nodes:
                if not isinstance(node,dict):continue
                candidates=node.get('@graph',[node]);candidates=candidates if isinstance(candidates,list) else []
                for candidate in candidates:
                    if not isinstance(candidate,dict) or candidate.get('@type')!='ItemList':continue
                    for entry in candidate.get('itemListElement',[])[:500]:
                        if not isinstance(entry,dict):continue
                        value=entry.get('item',entry);value={'url':value} if isinstance(value,str) else value
                        if not isinstance(value,dict):continue
                        link=model_link(urljoin(self.url,value.get('url','')),self.provider)
                        if link and value.get('name'):self.items.setdefault(link[0],{'id':link[0],'source_url':link[1],'title':str(value['name'])[:300],'image_url':web_url(value.get('image'),self.url)})
        return list(self.items.values())


class CollectionSchedules:
    def __init__(self,app,db,bridge,fetch_public,now):
        self.app,self.db,self.bridge,self.fetch_public,self.now=app,db,bridge,fetch_public,now
        self.lock=threading.Lock();self.active=set();self.stop=threading.Event();self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='forma-collection-check');self.thread=None
        with db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS collection_schedule(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tracked_collections(id TEXT PRIMARY KEY,provider TEXT NOT NULL,membership_key TEXT NOT NULL UNIQUE,name TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1,next_check TEXT NOT NULL,last_checked TEXT NOT NULL DEFAULT '',last_success TEXT NOT NULL DEFAULT '',state TEXT NOT NULL DEFAULT 'waiting',message TEXT NOT NULL DEFAULT '',new_count INTEGER NOT NULL DEFAULT 0,removed_count INTEGER NOT NULL DEFAULT 0,last_batch_id TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS collection_seen(track_id TEXT NOT NULL REFERENCES tracked_collections(id) ON DELETE CASCADE,remote_id TEXT NOT NULL,first_seen TEXT NOT NULL,PRIMARY KEY(track_id,remote_id));
            ''')
            for k,v in [('enabled','true'),('time','04:00'),('timezone','America/New_York')]:c.execute('INSERT OR IGNORE INTO collection_schedule VALUES (?,?)',(k,v))
            c.execute("UPDATE tracked_collections SET state='interrupted',message='Previous check was interrupted. Check now or wait for the next daily check.' WHERE state IN ('checking','queued')")
        self.discover(recover=True);self.routes()

    def settings(self):
        with self.db() as c:return dict(c.execute('SELECT key,value FROM collection_schedule'))

    def due(self):
        s=self.settings();return next_daily(datetime.fromisoformat(self.now()),s['time'],s['timezone'])

    def remember(self,item):
        provider=item.get('provider') or 'makerworld'
        for collection in item.get('collections',[]):
            key=str(collection['id']);url=collection.get('source_url','')
            if not url and provider=='makerworld' and key.isdigit():url='https://makerworld.com/en/collections/'+key
            track_id=self.register(provider,key,collection['name'],url)
            if track_id and item.get('id'):
                with self.db() as c:c.execute('INSERT OR IGNORE INTO collection_seen VALUES (?,?,?)',(track_id,item['id'],self.now()))

    def register(self,provider,key,name,url=''):
        if provider not in HOSTS:return None
        if url:
            try:_,url=collection_url(url,provider)
            except ValueError:url=''
        with self.db() as c:
            old=c.execute('SELECT * FROM tracked_collections WHERE membership_key=?',(key,)).fetchone()
            if not old and url:old=c.execute('SELECT * FROM tracked_collections WHERE provider=? AND url=?',(provider,url)).fetchone()
            if old:
                if url and not old['url']:c.execute("UPDATE tracked_collections SET url=?,state='waiting',message='' WHERE id=?",(url,old['id']))
                return old['id']
            id=str(uuid.uuid4())
            c.execute('INSERT INTO tracked_collections(id,provider,membership_key,name,url,next_check,state,message) VALUES (?,?,?,?,?,?,?,?)',(id,provider,key,str(name)[:100],url,self.due(),'waiting' if url else 'needs_link','' if url else 'Add the original collection URL to enable server checks.'))
            # Imported memberships are the baseline, so installation does not stage the existing catalog again.
            for row in c.execute('SELECT s.remote_id FROM makerworld_sources s JOIN makerworld_memberships m ON m.model_id=s.model_id WHERE m.remote_collection=?',(key,)):
                c.execute('INSERT OR IGNORE INTO collection_seen VALUES (?,?,?)',(id,row[0],self.now()))
            return id

    def discover(self,recover=False):
        with self.db() as c:
            rows=c.execute('SELECT DISTINCT mm.remote_collection,mm.name,m.source_url FROM makerworld_memberships mm JOIN models m ON m.id=mm.model_id LEFT JOIN tracked_collections t ON t.membership_key=mm.remote_collection WHERE t.id IS NULL').fetchall()
        for row in rows:
            key=row['remote_collection'];provider=key.split(':',1)[0] if ':' in key else 'makerworld'
            if provider in HOSTS:self.register(provider,key,row['name'])
        # New browser imports retain exact collection URLs in approved batch metadata.
        if not recover:return
        with self.db() as c:payloads=[r[0] for r in c.execute("SELECT payload FROM makerworld_batches WHERE state='imported' ORDER BY created_at DESC LIMIT 100")]
        for payload in payloads:
            for item in json.loads(payload):self.remember(item)

    def status(self):
        self.discover();s=self.settings()
        with self.db() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM tracked_collections ORDER BY name,provider')]
            pending=c.execute("SELECT count(*) FROM makerworld_batches WHERE state='pending'").fetchone()[0]
        return {'enabled':s['enabled']=='true','time':s['time'],'timezone':s['timezone'],'collections':rows,'running':bool(self.active),'scheduler_active':bool(self.thread and self.thread.is_alive()),'pending':pending}

    def configure(self,p):
        if not isinstance(p.get('enabled'),bool) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(p.get('time',''))):raise ValueError('Choose an enabled state and a valid daily time.')
        zone=p.get('timezone','')
        try:ZoneInfo(zone)
        except (ZoneInfoNotFoundError,ValueError,TypeError):raise ValueError('Choose a valid IANA time zone, such as America/New_York.')
        with self.db() as c:
            for k,v in [('enabled','true' if p['enabled'] else 'false'),('time',p['time']),('timezone',zone)]:c.execute('UPDATE collection_schedule SET value=? WHERE key=?',(v,k))
            due=next_daily(datetime.fromisoformat(self.now()),p['time'],zone)
            c.execute('UPDATE tracked_collections SET next_check=?',(due,))
        return self.status()

    def queue(self,id,manual=True):
        with self.lock:
            if id in self.active:return False
            with self.db() as c:
                row=c.execute('SELECT * FROM tracked_collections WHERE id=?',(id,)).fetchone()
                if not row:abort(404)
                if not manual and not row['enabled']:return False
                if not row['url']:
                    c.execute("UPDATE tracked_collections SET state='needs_link',message='Add the original collection URL first.' WHERE id=?",(id,));return False
                self.active.add(id)
                c.execute("UPDATE tracked_collections SET state='queued',message='Waiting to check public collection',next_check=? WHERE id=?",(self.due(),id))
            self.executor.submit(self.check,id,manual)
        return True

    def public_items(self,row):
        provider,url=row['provider'],row['url'];items={};complete=True;note=''
        if provider=='makerworld':
            match=re.search(r'/collections/(\d+)',urlsplit(url).path)
            if not match:raise ValueError('This collection needs a Safari refresh while signed in.')
            remote=match[1]
            for offset in range(0,500,20):
                endpoint='https://makerworld.com/api/v1/design-service/favorites/'+remote+'/designs?seed=0&collectionId='+remote+'&limit=20&offset='+str(offset)
                try:
                    body,mime,_=self.fetch_public(endpoint)
                    data=json.loads(body);hits=data.get('hits',data.get('items')) if isinstance(data,dict) else None
                    if not isinstance(hits,list):raise ValueError('Collection response changed.')
                except Exception:
                    if not items:raise ValueError('This collection could not be read publicly. Use its Safari bookmark while signed in.')
                    return list(items.values()),False,'Only part of the collection was readable. Refresh it in Safari for a complete view.'
                fresh=0
                for raw in hits:
                    if not isinstance(raw,dict):continue
                    d=raw.get('design',raw);id=str(d.get('id',d.get('designId','')))
                    if not re.fullmatch(r'\d{1,20}',id):continue
                    creator=d.get('designCreator') or d.get('creator') or {};creator=creator if isinstance(creator,dict) else {}
                    image=d.get('coverUrl') or d.get('cover') or ''
                    if not isinstance(image,str) or not re.match(r'https://([^/]+\.)?(bblmw\.com|bambulab\.com|makerworld\.com)/',image):image=''
                    item={'id':id,'title':str(d.get('title') or d.get('name') or 'MakerWorld '+id),'creator':str(creator.get('name','')),'description':str(d.get('description','')),'image_url':image}
                    if id not in items:items[id]=item;fresh+=1
                if not hits:
                    if isinstance(data.get('total'),int) and data['total']>offset:
                        if not items:raise ValueError('The site returned an incomplete collection list. Refresh this collection in Safari.')
                        return list(items.values()),False,'The site returned an incomplete collection list. Refresh this collection in Safari.'
                    break
                if not fresh:return list(items.values()),False,'The site repeated a page. Review the partial check and refresh in Safari.'
                total=data.get('total');total=total if isinstance(total,int) and not isinstance(total,bool) else None
                if len(hits)<20 or total is not None and offset+len(hits)>=total:break
                if offset==480:complete=False;note='Check limited to 500 models. Split larger collections or use the Safari importer.'
                if self.stop.wait(.3):raise ValueError('Check interrupted.')
        else:
            next_url=url;visited=set()
            for page in range(25):
                if next_url in visited:break
                visited.add(next_url)
                try:
                    body,mime,final=self.fetch_public(next_url)
                    if mime not in ('text/html','application/xhtml+xml'):raise ValueError('Not a collection page.')
                    _,final=collection_url(final,provider)
                    parser=CollectionPage(final,provider);parser.feed(body.decode('utf-8',errors='replace'))
                    for item in parser.finish():items[item['id']]=item
                except Exception:
                    if not items:raise ValueError('No public collection models were readable. Use its Safari bookmark while signed in.')
                    break
                if len(items)>=500 or not parser.next:break
                try:
                    _,candidate=collection_url(parser.next,provider)
                    if urlsplit(candidate).path!=urlsplit(url).path and not urlsplit(candidate).path.startswith(urlsplit(url).path+'/'):break
                    next_url=candidate
                except ValueError:break
                if self.stop.wait(.3):raise ValueError('Check interrupted.')
            if not items:raise ValueError('The public page did not expose model cards. A signed-in Safari refresh is needed.')
            complete=False;note='Public page check: only server-rendered models are visible. Private, lazy-loaded, or recommended cards may require review in Safari.'
        result=[]
        for item in list(items.values())[:500]:
            item={**item,'collections':[{'id':row['membership_key'],'name':row['name'],'source_url':url}]}
            if provider!='makerworld':item['provider']=provider
            result.append(normalize_item(item))
        return result,complete,note

    def check(self,id,manual=True):
        try:
            with self.db() as c:
                row=c.execute('SELECT * FROM tracked_collections WHERE id=?',(id,)).fetchone()
                if not row:return
                if not manual and (not row['enabled'] or self.settings()['enabled']!='true'):
                    c.execute("UPDATE tracked_collections SET state='waiting',message='Daily checking paused' WHERE id=?",(id,));return
                row=dict(row);c.execute("UPDATE tracked_collections SET state='checking',message='Reading public collection',last_checked=? WHERE id=?",(self.now(),id))
            items,complete,note=self.public_items(row)
            with self.db() as c:
                current=c.execute('SELECT * FROM tracked_collections WHERE id=?',(id,)).fetchone()
                if not current or current['url']!=row['url']:raise ValueError('The collection link changed during this check. Check again.')
                seen={r[0] for r in c.execute('SELECT remote_id FROM collection_seen WHERE track_id=?',(id,))}
                added=[i for i in items if i['id'] not in seen]
                removed=len(seen-{i['id'] for i in items}) if complete else 0
                batch_id=''
                if added:
                    if c.execute("SELECT count(*) FROM makerworld_batches WHERE state='pending'").fetchone()[0]>=100:raise ValueError('Review pending website imports before checking for more models.')
                    device=c.execute("SELECT id FROM companion_devices WHERE name='Daily collection checker' AND revoked=1 LIMIT 1").fetchone()
                    device_id=device[0] if device else str(uuid.uuid4())
                    if not device:c.execute('INSERT INTO companion_devices VALUES (?,?,?,?,?,1)',(device_id,'Daily collection checker',self.bridge.digest(secrets.token_urlsafe(32)),self.now(),self.now()))
                    batch_id=str(uuid.uuid4());warnings=[note] if note else []
                    if removed:warnings.append(str(removed)+' previously seen models were not returned. Local models are kept.')
                    c.execute("INSERT INTO makerworld_batches(id,device_id,title,state,payload,warnings,created_at) VALUES (?,?,?,'pending',?,?,?)",(batch_id,device_id,'Collection update · '+row['name'],json.dumps(added),json.dumps(warnings),self.now()))
                for item in items:c.execute('INSERT OR IGNORE INTO collection_seen VALUES (?,?,?)',(id,item['id'],self.now()))
                message=(str(len(added))+' new model(s) waiting for review.' if added else 'No newly discovered models.' if complete else 'No new models in the readable portion.')
                if note:message+=' '+note
                if removed:message+=' '+str(removed)+' previously seen models were not returned; local entries were kept.'
                c.execute('UPDATE tracked_collections SET state=?,message=?,last_success=?,new_count=?,removed_count=?,last_batch_id=CASE WHEN ?!=\'\' THEN ? ELSE last_batch_id END WHERE id=?',('checked' if complete else 'partial',message,self.now(),len(added),removed,batch_id,batch_id,id))
        except Exception as exc:
            with self.db() as c:c.execute("UPDATE tracked_collections SET state='needs_browser',message=? WHERE id=?",(str(exc)[:1000],id))
        finally:
            with self.lock:self.active.discard(id)

    def tick(self):
        self.discover()
        if self.settings()['enabled']!='true':return
        with self.db() as c:ids=[r[0] for r in c.execute("SELECT id FROM tracked_collections WHERE enabled=1 AND url!='' AND next_check<=?",(self.now(),))]
        for id in ids:self.queue(id,manual=False)

    def start(self):
        if self.thread:return
        def run():
            while not self.stop.is_set():
                try:self.tick()
                except Exception:self.app.logger.exception('Daily collection scheduler check failed')
                self.stop.wait(30)
        self.thread=threading.Thread(target=run,name='forma-daily-collections',daemon=True);self.thread.start()

    def routes(self):
        @self.app.get('/api/collection-schedule')
        def schedule_status():return jsonify(self.status())

        @self.app.patch('/api/collection-schedule')
        def schedule_configure():return jsonify(self.configure(request.get_json()))

        @self.app.post('/api/collection-schedule/collections')
        def schedule_add():
            p=request.get_json();provider,url=collection_url(p.get('url',''));name=str(p.get('name','')).strip()[:100]
            if not name:raise ValueError('Name the collection.')
            match=re.search(r'/collections/(\d+)',urlsplit(url).path) if provider=='makerworld' else None
            key=match[1] if match else provider+':'+hashlib.sha256(url.encode()).hexdigest()[:32]
            id=self.register(provider,key,name,url);return jsonify(id=id),201

        @self.app.patch('/api/collection-schedule/collections/<id>')
        def schedule_edit(id):
            p=request.get_json()
            with self.db() as c:
                row=c.execute('SELECT * FROM tracked_collections WHERE id=?',(id,)).fetchone()
                if not row:abort(404)
                enabled=p.get('enabled',bool(row['enabled']))
                if not isinstance(enabled,bool):raise ValueError('Choose whether daily checks are enabled.')
                url=row['url']
                if 'url' in p:
                    _,url=collection_url(p['url'],row['provider'])
                    match=re.search(r'/collections/(\d+)',urlsplit(url).path)
                    if row['provider']=='makerworld' and row['membership_key'].isdigit() and (not match or match[1]!=row['membership_key']):raise ValueError('This is a different MakerWorld collection. Track it separately instead.')
                c.execute("UPDATE tracked_collections SET enabled=?,url=?,next_check=?,state=CASE WHEN url!=? THEN 'waiting' ELSE state END,message=CASE WHEN url!=? THEN '' ELSE message END WHERE id=?",(int(enabled),url,self.due(),url,url,id))
            return jsonify(ok=True)

        @self.app.post('/api/collection-schedule/check')
        def schedule_check():
            p=request.get_json() or {};self.discover()
            with self.db() as c:ids=[r[0] for r in c.execute('SELECT id FROM tracked_collections WHERE enabled=1')] if not p.get('id') else [str(p['id'])]
            queued=sum(self.queue(id) for id in ids);return jsonify(queued=queued),202
