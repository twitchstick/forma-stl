"""Single-owner account, persistent password hashes, browser sessions and profile photo."""
import hashlib
import hmac
import io
import os
import re
import secrets
import time
import uuid
from flask import abort, g, jsonify, request, send_file, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

COOKIE='forma_session'
MODE='forma_login_mode'
DURATION=14*86400


class Account:
    def __init__(self,app,db,now):
        self.app,self.db,self.now=app,db,now
        with db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS account_profile(id INTEGER PRIMARY KEY CHECK(id=1),username TEXT NOT NULL,display_name TEXT NOT NULL DEFAULT '',password_hash TEXT NOT NULL DEFAULT '',avatar BLOB,avatar_version TEXT NOT NULL DEFAULT '',auth_version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_sessions(id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,auth_version INTEGER NOT NULL,device TEXT NOT NULL,created_at TEXT NOT NULL,last_seen TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS account_attempts(bucket TEXT PRIMARY KEY,started REAL NOT NULL,failures INTEGER NOT NULL);
            ''')
            c.execute('INSERT OR IGNORE INTO account_profile(id,username,created_at,updated_at) VALUES (1,?,?,?)',(os.environ.get('FORMA_USERNAME','admin'),now(),now()))
            row=c.execute('SELECT password_hash FROM account_profile WHERE id=1').fetchone()
            password=os.environ.get('FORMA_PASSWORD','')
            if not row[0] and password:
                c.execute('UPDATE account_profile SET username=?,password_hash=?,updated_at=? WHERE id=1',(os.environ.get('FORMA_USERNAME','admin'),generate_password_hash(password,method="pbkdf2:sha256:1000000"),now()))
        self.routes()

    def row(self):
        with self.db() as c:return dict(c.execute('SELECT * FROM account_profile WHERE id=1').fetchone())

    @staticmethod
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()

    def enabled(self,row=None):return bool((row or self.row())['password_hash'] or os.environ.get('FORMA_PASSWORD',''))

    def valid(self,row,username,password):
        if not isinstance(username,str) or not isinstance(password,str) or len(username)>200 or len(password)>1024:return False
        if row['password_hash']:
            password_ok=check_password_hash(row['password_hash'],password)
            return hmac.compare_digest(username.encode(),row['username'].encode()) and password_ok
        fallback=os.environ.get('FORMA_PASSWORD','')
        if fallback:return hmac.compare_digest(username.encode(),os.environ.get('FORMA_USERNAME','admin').encode()) and hmac.compare_digest(password.encode(),fallback.encode())
        return password=='' and username==row['username']

    def bucket(self):return self.digest(request.remote_addr or 'local')

    def check_limit(self):
        with self.db() as c:
            row=c.execute('SELECT * FROM account_attempts WHERE bucket=?',(self.bucket(),)).fetchone()
            if row and time.time()-row['started']<900 and row['failures']>=10:abort(429,'Too many unsuccessful attempts. Try again in 15 minutes.')

    def failure(self):
        with self.db() as c:
            c.execute('DELETE FROM account_attempts WHERE started<?',(time.time()-900,))
            c.execute('INSERT INTO account_attempts VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET failures=account_attempts.failures+1',(self.bucket(),time.time()))

    def clear_failures(self):
        with self.db() as c:c.execute('DELETE FROM account_attempts WHERE bucket=?',(self.bucket(),))

    def session(self,c,version):
        token=secrets.token_urlsafe(32);id=str(uuid.uuid4());now=self.now()
        ua=request.headers.get('User-Agent','')
        browser='Edge' if 'Edg/' in ua else 'Chrome' if 'Chrome/' in ua else 'Safari' if 'Safari/' in ua else 'Firefox' if 'Firefox/' in ua else 'Browser / API client'
        platform='macOS' if 'Macintosh' in ua else 'iPhone / iPad' if any(s in ua for s in ('iPhone','iPad')) else 'Windows' if 'Windows' in ua else 'Linux' if 'Linux' in ua else ''
        c.execute('DELETE FROM account_sessions WHERE expires<?',(time.time(),))
        c.execute('INSERT INTO account_sessions VALUES (?,?,?,?,?,?,?)',(id,self.digest(token),version,browser+(' · '+platform if platform else ''),now,now,time.time()+DURATION))
        # Limit retained sessions without exposing their bearer tokens.
        c.execute('DELETE FROM account_sessions WHERE id IN (SELECT id FROM account_sessions ORDER BY created_at DESC LIMIT -1 OFFSET 100)')
        g.account_session_id=id;g.account_new_token=token;return token

    def authenticate(self):
        row=self.row();g.account_authenticated=False;g.account_session_id=None
        if not self.enabled(row):g.account_authenticated=True;return True
        token=request.cookies.get(COOKIE,'')
        if token and len(token)<100:
            with self.db() as c:
                s=c.execute('SELECT * FROM account_sessions WHERE token_hash=? AND auth_version=? AND expires>?',(self.digest(token),row['auth_version'],time.time())).fetchone()
                if s:
                    if self.now()[:16]!=s['last_seen'][:16]:c.execute('UPDATE account_sessions SET last_seen=? WHERE id=?',(self.now(),s['id']))
                    g.account_authenticated=True;g.account_session_id=s['id'];return True
        # After a browser has used cookie login, stale Basic credentials must not
        # silently recreate a revoked/expired session or undo an explicit logout.
        if request.cookies.get(MODE)=='1' or token:return False
        auth=request.authorization
        if auth and auth.type=='basic':
            self.check_limit()
            if self.valid(row,auth.username,auth.password):
                with self.db() as c:self.session(c,row['auth_version'])
                self.clear_failures();g.account_authenticated=True;return True
            self.failure()
        return False

    def cookies(self,response):
        if getattr(g,'account_logout',False):
            response.delete_cookie(COOKIE,path='/');response.set_cookie(MODE,'1',max_age=365*86400,httponly=True,samesite='Strict',secure=request.is_secure,path='/')
        elif getattr(g,'account_new_token',None):
            response.set_cookie(COOKIE,g.account_new_token,max_age=DURATION,httponly=True,samesite='Strict',secure=request.is_secure,path='/')
            response.set_cookie(MODE,'1',max_age=365*86400,httponly=True,samesite='Strict',secure=request.is_secure,path='/')
        return response

    def profile(self):
        row=self.row()
        return {'username':row['username'],'display_name':row['display_name'],'authentication_enabled':self.enabled(row),'avatar_url':'/api/account/avatar?v='+row['avatar_version'] if row['avatar'] else ''}

    def require_current(self,row,password):
        self.check_limit()
        if not self.valid(row,row['username'] if row['password_hash'] else os.environ.get('FORMA_USERNAME',row['username']),password):self.failure();abort(403,'Current password is incorrect.')
        self.clear_failures()

    def rotate(self,c,version):
        c.execute('DELETE FROM account_sessions')
        c.execute('DELETE FROM slicer_downloads')
        c.execute('DELETE FROM companion_codes')
        c.execute('UPDATE companion_devices SET revoked=1 WHERE revoked=0')
        self.session(c,version)

    def routes(self):
        app=self.app

        @app.get('/login')
        def account_login_page():return send_from_directory(app.static_folder,'login.html')

        @app.get('/api/auth/status')
        def account_auth_status():return jsonify(authentication_required=self.enabled(),authenticated=bool(getattr(g,'account_authenticated',False)))

        @app.post('/api/auth/login')
        def account_login():
            request.max_content_length=16384;p=request.get_json();row=self.row();self.check_limit()
            if not self.valid(row,p.get('username'),p.get('password')):self.failure();abort(401,'Username or password is incorrect.')
            if not self.enabled(row):raise ValueError('Sign-in is not enabled. Set a password in Account settings first.')
            with self.db() as c:self.session(c,row['auth_version'])
            self.clear_failures();return jsonify(ok=True)

        @app.post('/api/auth/logout')
        def account_logout():
            with self.db() as c:
                if getattr(g,'account_session_id',None):c.execute('DELETE FROM account_sessions WHERE id=?',(g.account_session_id,))
            g.account_new_token=None;g.account_logout=True;return jsonify(ok=True)

        @app.get('/api/account')
        def account_profile():return jsonify(self.profile())

        @app.patch('/api/account')
        def account_update():
            request.max_content_length=16384;p=request.get_json();row=self.row();username=p.get('username',row['username']);name=p.get('display_name',row['display_name'])
            if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,79}',username):raise ValueError('Use 1–80 letters, numbers, dots, underscores, @ or hyphens for the username.')
            if not isinstance(name,str) or len(name)>100:raise ValueError('Display name must be at most 100 characters.')
            security_change=username!=row['username']
            if security_change:self.require_current(row,p.get('current_password',''))
            with self.db() as c:
                # Reject a concurrent security update rather than restore stale credentials.
                if not c.execute('UPDATE account_profile SET username=?,display_name=?,auth_version=auth_version+?,updated_at=? WHERE id=1 AND auth_version=?',(username,name.strip(),int(security_change),self.now(),row['auth_version'])).rowcount:abort(409,'Account changed in another session. Sign in again.')
                if security_change:self.rotate(c,row['auth_version']+1)
            return jsonify(self.profile())

        @app.post('/api/account/password')
        def account_password():
            request.max_content_length=16384;p=request.get_json();row=self.row();current=p.get('current_password','');new=p.get('new_password');confirm=p.get('confirm_password')
            self.require_current(row,current)
            if not isinstance(new,str) or not 12<=len(new)<=256:raise ValueError('Use a password between 12 and 256 characters.')
            if new!=confirm:raise ValueError('The new passwords do not match.')
            if new==current:raise ValueError('Choose a different password.')
            hashed=generate_password_hash(new,method="pbkdf2:sha256:1000000")
            with self.db() as c:
                if not c.execute('UPDATE account_profile SET password_hash=?,auth_version=auth_version+1,updated_at=? WHERE id=1 AND auth_version=?',(hashed,self.now(),row['auth_version'])).rowcount:abort(409,'Account changed in another session. Sign in again.')
                self.rotate(c,row['auth_version']+1)
            return jsonify(ok=True)

        @app.get('/api/account/sessions')
        def account_sessions():
            with self.db() as c:rows=[{**dict(r),'current':r['id']==getattr(g,'account_session_id',None)} for r in c.execute('SELECT id,device,created_at,last_seen FROM account_sessions WHERE expires>? ORDER BY last_seen DESC',(time.time(),))]
            return jsonify(sessions=rows)

        @app.post('/api/account/logout-others')
        def account_logout_others():
            with self.db() as c:
                id=getattr(g,'account_session_id',None)
                if id:c.execute('DELETE FROM account_sessions WHERE id!=?',(id,))
                else:c.execute('DELETE FROM account_sessions')
            return jsonify(ok=True)

        @app.get('/api/account/avatar')
        def account_avatar():
            row=self.row()
            if not row['avatar']:abort(404)
            response=send_file(io.BytesIO(row['avatar']),mimetype='image/png',etag=self.digest(row['avatar_version']),conditional=True)
            response.headers['Cache-Control']='private, no-store';return response

        @app.post('/api/account/avatar')
        def account_avatar_upload():
            request.max_content_length=6*1024*1024
            from PIL import Image,ImageOps
            upload=request.files.get('avatar')
            if not upload:raise ValueError('Choose a PNG, JPG, WebP or GIF photo.')
            body=upload.stream.read(5*1024*1024+1)
            if len(body)>5*1024*1024:raise ValueError('Choose a photo smaller than 5 MB.')
            try:
                with Image.open(io.BytesIO(body)) as image:
                    if image.format not in ('PNG','JPEG','WEBP','GIF') or image.width*image.height>32_000_000:raise ValueError('Unsupported image.')
                    image=ImageOps.fit(ImageOps.exif_transpose(image).convert('RGBA'),(256,256));result=io.BytesIO();image.save(result,'PNG')
            except Exception:raise ValueError('Choose a valid PNG, JPG, WebP or GIF photo under 32 megapixels.')
            with self.db() as c:c.execute('UPDATE account_profile SET avatar=?,avatar_version=?,updated_at=? WHERE id=1',(result.getvalue(),uuid.uuid4().hex,self.now()))
            return jsonify(self.profile())

        @app.delete('/api/account/avatar')
        def account_avatar_remove():
            with self.db() as c:c.execute("UPDATE account_profile SET avatar=NULL,avatar_version='',updated_at=? WHERE id=1",(self.now(),))
            return jsonify(self.profile())
