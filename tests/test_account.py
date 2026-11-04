import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import create_app

OLD='original-password-123'
NEW='new-owner-password-456'

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'FORMA_USERNAME':'owner','FORMA_PASSWORD':OLD});self.env.start()
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.app=create_app(self.root/'data',self.root/'library');self.c=self.app.test_client();self.h={'X-Forma-Request':'1'}
        self.db=self.app.extensions['library_services'].db

    def tearDown(self):
        self.app.extensions['collection_schedules'].stop.set();self.app.extensions['collection_schedules'].executor.shutdown(wait=True);self.app.extensions['source_imports'].executor.shutdown(wait=True);self.app.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True);self.temp.cleanup();self.env.stop()

    def login(self,c=None,password=OLD,username='owner'):
        c=c or self.c;return c.post('/api/auth/login',json={'username':username,'password':password},headers=self.h)

    def change(self,c=None,current=OLD,new=NEW,confirm=None):
        return (c or self.c).post('/api/account/password',json={'current_password':current,'new_password':new,'confirm_password':new if confirm is None else confirm},headers=self.h)

    def test_bootstrap_and_public_login_do_not_expose_account_data(self):
        with self.c.get('/') as r:self.assertEqual(r.status_code,401);self.assertIn(b'login-form',r.data);self.assertNotIn('WWW-Authenticate',r.headers)
        with self.c.get('/login') as r:self.assertEqual(r.status_code,200)
        with self.c.get('/static/login.js') as r:self.assertEqual(r.status_code,200)
        self.assertEqual(self.c.get('/api/models').status_code,401);self.assertEqual(self.c.get('/api/account').status_code,401);self.assertEqual(self.c.get('/health').status_code,200)
        status=self.c.get('/api/auth/status').json;self.assertTrue(status['authentication_required']);self.assertFalse(status['authenticated']);self.assertNotIn('username',status)
        with self.db() as c:hashed=c.execute('SELECT password_hash FROM account_profile').fetchone()[0]
        self.assertNotEqual(hashed,OLD);self.assertTrue(hashed.startswith(('scrypt:','pbkdf2:')))

    def test_login_cookie_is_httponly_and_session_data_contains_no_bearer_token(self):
        result=self.login();self.assertEqual(result.status_code,200);headers=';'.join(result.headers.getlist('Set-Cookie'));self.assertIn('HttpOnly',headers);self.assertIn('SameSite=Strict',headers)
        token=self.c.get_cookie('forma_session').value
        self.assertEqual(self.c.get('/api/models').status_code,200)
        profile=self.c.get('/api/account').json;self.assertEqual(profile['username'],'owner');self.assertNotIn('password_hash',profile)
        sessions=self.c.get('/api/account/sessions').json;self.assertEqual(len(sessions['sessions']),1);self.assertTrue(sessions['sessions'][0]['current']);self.assertNotIn(token,str(sessions))
        with self.db() as c:self.assertNotEqual(c.execute('SELECT token_hash FROM account_sessions').fetchone()[0],token)

    def test_password_change_keeps_current_session_and_revokes_stale_browser_basic_auth(self):
        self.login();other=self.app.test_client();self.login(other)
        r=self.change();self.assertEqual(r.status_code,200,r.json);self.assertEqual(self.c.get('/api/models').status_code,200)
        basic={'Authorization':'Basic '+base64.b64encode(('owner:'+OLD).encode()).decode()}
        self.assertEqual(other.get('/api/models',headers=basic).status_code,401)
        fresh=self.app.test_client();self.assertEqual(self.login(fresh).status_code,401);self.assertEqual(self.login(fresh,password=NEW).status_code,200)
        with self.db() as c:self.assertNotIn(NEW,str(c.execute('SELECT * FROM account_profile').fetchall()))

    def test_new_password_persists_across_restart_despite_old_environment(self):
        self.login();self.assertEqual(self.change().status_code,200)
        second=create_app(self.root/'data',self.root/'library');client=second.test_client()
        try:
            self.assertEqual(self.login(client).status_code,401);self.assertEqual(self.login(client,password=NEW).status_code,200)
        finally:
            second.extensions['collection_schedules'].executor.shutdown(wait=True);second.extensions['source_imports'].executor.shutdown(wait=True);second.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True)

    def test_invalid_password_change_is_atomic_and_needs_current_password(self):
        self.login()
        for kwargs,status in [({'current':'wrong'},403),({'new':'short'},400),({'confirm':'different'},400),({'new':OLD},400)]:
            self.assertEqual(self.change(**kwargs).status_code,status)
        self.assertEqual(self.login(self.app.test_client()).status_code,200)

    def test_profile_and_username_changes_have_different_reauthentication_requirements(self):
        self.login();r=self.c.patch('/api/account',json={'display_name':'Chris <script>'},headers=self.h);self.assertEqual(r.status_code,200);self.assertEqual(r.json['display_name'],'Chris <script>')
        self.assertEqual(self.c.patch('/api/account',json={'username':'chris'},headers=self.h).status_code,403)
        r=self.c.patch('/api/account',json={'username':'chris','current_password':OLD},headers=self.h);self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.c.get('/api/models').status_code,200);self.assertEqual(self.login(self.app.test_client(),username='chris').status_code,200)
        self.assertEqual(self.c.patch('/api/account',json={'username':'bad name','current_password':OLD},headers=self.h).status_code,400)

    def test_explicit_logout_and_revoke_other_browsers_cannot_be_undone_by_cached_basic(self):
        self.login();other=self.app.test_client();self.login(other)
        self.assertEqual(self.c.post('/api/account/logout-others',json={},headers=self.h).status_code,200)
        basic={'Authorization':'Basic '+base64.b64encode(('owner:'+OLD).encode()).decode()}
        self.assertEqual(other.get('/api/models',headers=basic).status_code,401)
        self.assertEqual(self.c.post('/api/auth/logout',json={},headers=self.h).status_code,200);self.assertEqual(self.c.get('/api/models',headers=basic).status_code,401)
        self.assertEqual(self.login().status_code,200)

    def test_avatar_is_validated_private_and_persistent(self):
        self.login();stream=io.BytesIO();Image.new('RGB',(800,400),'purple').save(stream,'PNG')
        r=self.c.post('/api/account/avatar',data={'avatar':(io.BytesIO(stream.getvalue()),'photo.png')},headers=self.h);self.assertEqual(r.status_code,200,r.json);self.assertTrue(r.json['avatar_url'])
        with self.c.get('/api/account/avatar') as response:
            self.assertEqual(response.mimetype,'image/png');image=Image.open(io.BytesIO(response.data));self.assertEqual(image.size,(256,256))
        self.assertEqual(self.app.test_client().get('/api/account/avatar').status_code,401)
        r=self.c.post('/api/account/avatar',data={'avatar':(io.BytesIO(b'<svg onload="alert(1)"></svg>'),'photo.svg')},headers=self.h);self.assertEqual(r.status_code,400)
        self.assertEqual(self.c.delete('/api/account/avatar',headers=self.h).json['avatar_url'],'');self.assertEqual(self.c.get('/api/account/avatar').status_code,404)

    def test_login_rate_limit_and_cross_site_mutations(self):
        self.assertEqual(self.c.post('/api/auth/login',json={'username':'owner','password':OLD}).status_code,403)
        self.assertEqual(self.c.post('/api/auth/login',json={'username':'owner','password':OLD},headers={**self.h,'Origin':'https://evil.example'}).status_code,403)
        for _ in range(10):self.assertEqual(self.login(password='wrong').status_code,401)
        self.assertEqual(self.login().status_code,429)
        with self.db() as c:c.execute('UPDATE account_attempts SET started=0')
        self.assertEqual(self.login().status_code,200)

    def test_password_change_revokes_companion_pairings_and_pending_codes(self):
        self.login();code=self.c.post('/api/makerworld/pair-code',json={},headers=self.h).json['code'];paired=self.c.post('/api/companion/connect',json={'code':code}).json
        self.assertTrue(paired['token']);self.c.post('/api/makerworld/pair-code',json={},headers=self.h)
        self.assertEqual(self.change().status_code,200)
        with self.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM companion_devices WHERE revoked=0').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM companion_codes').fetchone()[0],0)

    def test_password_can_enable_auth_on_an_open_install(self):
        with patch.dict(os.environ,{'FORMA_PASSWORD':'','FORMA_USERNAME':'admin'}):
            second=create_app(self.root/'open-data',self.root/'library');client=second.test_client()
            try:
                self.assertFalse(client.get('/api/account').json['authentication_enabled'])
                self.assertEqual(self.change(client,current='').status_code,200)
                self.assertTrue(client.get('/api/account').json['authentication_enabled']);self.assertEqual(second.test_client().get('/api/models').status_code,401)
            finally:
                second.extensions['collection_schedules'].executor.shutdown(wait=True);second.extensions['source_imports'].executor.shutdown(wait=True);second.extensions['makerworld_bridge'].photo_jobs.shutdown(wait=True)

if __name__=='__main__':unittest.main()
