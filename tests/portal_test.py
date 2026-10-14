"""Private portal, enrollment, review pagination and Resend tests. Synthetic only."""
import copy
import json
import secrets
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from backend.app import Application,Store,create_app
from backend.maintenance import administrator_invite,audit_valid,backup,restore,create_administrator
from backend.private_store import PrivateStore
from backend.providers import Providers,ProviderUnavailable
from backend_test import payload
from backend.secure_models import PF
from security_test import pf_payload


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.key=secrets.token_bytes(32);self.now=time.time()
        self.store=Store(Path(self.temp.name)/'private/applications.sqlite',self.key)
        self.app=create_app(self.store,'https://example.test',True,clock=lambda:self.now)
        self.client=TestClient(self.app,base_url='https://example.test');self.client.__enter__()
        self.private=self.app.state.security.private
        self.headers={'Origin':'https://example.test','Content-Type':'application/json'}

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def post(self,path,body=None,**headers):
        return self.client.post(path,json=body or {},headers={**self.headers,**headers})

    def initial(self,body=None):
        return self.store.save(Application.model_validate(body or payload()),str(uuid.uuid4()))

    def admin(self):
        secret=pyotp.random_base32();password='Private test password 123'
        create_administrator(self.private,'kaykys468@gmail.com',password,secret)
        r=self.post('/api/admin/login',{'username':'kaykys468@gmail.com','password':password,'code':pyotp.TOTP(secret).at(self.now)})
        self.assertEqual(r.status_code,200)

    def test_customer_login_isolates_protocols_and_expiry(self):
        first=self.initial();other=copy.deepcopy(payload());other['password']='Different password 123';second=self.initial(other)
        self.assertNotEqual(first,second)
        self.assertEqual(self.client.get('/api/client/application').status_code,401)
        credentials={'application_id':first,'email':payload()['applicant']['email'],'password':payload()['password']}
        result=self.post('/api/client/login',credentials)
        self.assertEqual(result.status_code,200)
        for flag in ['HttpOnly','Secure','SameSite=strict','Path=/']:self.assertIn(flag,result.headers['set-cookie'])
        data=self.client.get('/api/client/application').json()
        self.assertEqual(data['application_id'],first)
        self.assertEqual(data['cpf_masked'],'***.***.***-35')
        for field in ['password','email','phone','address','bank_account','pf_records']:self.assertNotIn(field,data)
        self.assertEqual(self.post('/api/client/login',{**credentials,'application_id':second}).status_code,401)
        self.assertEqual(self.client.get('/api/client/application').json()['application_id'],first)
        self.now+=1801
        self.assertEqual(self.client.get('/api/client/application').status_code,401)
        self.assertEqual(self.post('/api/client/logout').status_code,200)

    def test_portal_origin_limits_and_pages_protect_content(self):
        identity=self.initial();body={'application_id':identity,'email':'wrong@example.test','password':'wrong-pass'}
        self.assertEqual(self.post('/api/client/login',body,Origin='https://other.test').status_code,403)
        for _ in range(5):self.assertEqual(self.post('/api/client/login',body).status_code,401)
        self.assertEqual(self.post('/api/client/login',body).status_code,429)
        for path in ['/cliente','/admin']:
            r=self.client.get(path);self.assertEqual(r.status_code,200)
            self.assertIn('no-store',r.headers['cache-control']);self.assertIn("script-src 'sha256-",r.headers['content-security-policy'])
            self.assertNotIn('localStorage',r.text);self.assertNotIn('innerHTML',r.text)

    def test_admin_filters_pagination_and_private_review(self):
        self.admin()
        ids=[self.initial()]
        # Populate a large encrypted fixture without deriving the same synthetic
        # password 104 times. This test checks listing/review, not password hashing.
        with self.store.connect() as db:
            saved=db.execute('SELECT * FROM applications WHERE application_id=?',(ids[0],)).fetchone()
            clear=AESGCM(self.key).decrypt(saved[5],saved[6],ids[0].encode())
            for _ in range(103):
                identity=str(uuid.uuid4());nonce=secrets.token_bytes(12);ids.append(identity)
                row=(identity,str(uuid.uuid4()),saved[2],saved[3],saved[4],nonce,
                     AESGCM(self.key).encrypt(nonce,clear,identity.encode()),saved[7],saved[8])
                db.execute('INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?)',row)
        first=self.client.get('/api/admin/records?page=1&page_size=25').json()
        last=self.client.get('/api/admin/records?page=5&page_size=25').json()
        self.assertEqual(first['total'],104);self.assertEqual(first['total_pages'],5);self.assertEqual(len(last['records']),4)
        self.assertFalse({r['id'] for r in first['records']} & {r['id'] for r in last['records']})
        data={'state':'under_review','message':'Documento adicional solicitado.','internal_note':'Nota privada da equipe.'}
        self.assertEqual(self.post('/api/admin/records/initial/'+ids[0]+'/review',data).status_code,200)
        self.assertEqual(self.post('/api/admin/records/initial/'+ids[0]+'/review',{**data,'state':'approved'}).status_code,422)
        filtered=self.client.get('/api/admin/records?state=under_review&kind=initial').json()
        self.assertEqual([r['id'] for r in filtered['records']],[ids[0]])
        self.assertEqual(self.client.get('/api/admin/records?q='+ids[0]).json()['total'],1)
        credentials={'application_id':ids[0],'email':payload()['applicant']['email'],'password':payload()['password']}
        self.assertEqual(self.post('/api/client/login',credentials).status_code,200)
        current=self.client.get('/api/client/application').json()
        self.assertEqual(current['review']['message'],data['message']);self.assertNotIn('internal_note',current['review'])
        self.assertNotIn(data['internal_note'].encode(),self.store.path.read_bytes())
        overview=self.client.get('/api/admin/overview').json();self.assertEqual(overview['counts']['initial'],104)
        self.assertFalse(overview['integrations']['email']['configured']);self.assertTrue(audit_valid(self.private))

    def test_admin_invite_is_private_single_use_and_works_with_intake_paused(self):
        self.app.state.security.enabled=False
        path=administrator_invite(self.private,'kaykys468@gmail.com','https://example.test',Path(self.temp.name)/'operator/invite.txt')
        token=path.read_text().strip().split('#setup=')[1]
        start=self.post('/api/admin/setup/start',{'token':token});self.assertEqual(start.status_code,200)
        secret=start.json()['secret'];self.assertNotIn(token.encode(),self.store.path.read_bytes());self.assertNotIn(secret.encode(),self.store.path.read_bytes())
        body={'token':token,'password':'Private chosen password 123','code':pyotp.TOTP(secret).at(self.now)}
        self.assertEqual(self.post('/api/admin/setup/complete',body,Origin='https://other.test').status_code,403)
        self.assertEqual(self.post('/api/admin/setup/complete',body).status_code,200)
        self.assertEqual(self.post('/api/admin/setup/complete',body).status_code,401)
        self.now+=31
        self.assertEqual(self.post('/api/admin/login',{'username':'kaykys468@gmail.com','password':body['password'],'code':pyotp.TOTP(secret).at(self.now)}).status_code,200)
        self.assertFalse(self.client.get('/api/admin/overview').json()['receiving'])
        self.assertEqual(self.post('/api/applications',payload(),**{'Idempotency-Key':str(uuid.uuid4())}).status_code,503)

    def test_expired_or_replaced_invite_and_restore_invalidate_sessions(self):
        target=Path(self.temp.name)/'operator/invite.txt'
        first=administrator_invite(self.private,'owner@example.test','https://example.test',target).read_text().strip().split('#setup=')[1]
        second=administrator_invite(self.private,'owner@example.test','https://example.test',target).read_text().strip().split('#setup=')[1]
        self.assertEqual(self.post('/api/admin/setup/start',{'token':first}).status_code,401)
        self.now+=1801
        self.assertEqual(self.post('/api/admin/setup/start',{'token':second}).status_code,401)
        self.now-=1801
        identity=self.initial()
        self.assertEqual(self.post('/api/client/login',{'application_id':identity,'email':payload()['applicant']['email'],'password':payload()['password']}).status_code,200)
        copied=backup(self.private,Path(self.temp.name)/'backups')
        restored=PrivateStore(Store(Path(self.temp.name)/'restored/applications.sqlite',self.key),lambda:self.now)
        restore(restored,copied)
        with restored.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM client_sessions').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM admin_bootstrap').fetchone()[0],0)

    def test_legacy_schema_migration_preserves_records_and_releases_identity(self):
        identity=self.initial()
        with self.store.connect() as db:
            db.execute('ALTER TABLE applications RENAME TO applications_current')
            db.execute('''CREATE TABLE applications(application_id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,
                request_digest TEXT NOT NULL,cpf_digest TEXT UNIQUE NOT NULL,email_digest TEXT UNIQUE NOT NULL,
                nonce BLOB NOT NULL,ciphertext BLOB NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL)''')
            db.execute('INSERT INTO applications SELECT * FROM applications_current');db.execute('DROP TABLE applications_current')
        migrated=Store(self.store.path,self.key)
        repeated=migrated.save(Application.model_validate(payload()),str(uuid.uuid4()))
        self.assertNotEqual(identity,repeated)
        with migrated.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM applications').fetchone()[0],2)

    def test_pf_limits_idempotence_and_storage_reject_controlled(self):
        model=PF.model_validate(pf_payload());sid='verified-synthetic-session';key=str(uuid.uuid4())
        identity,status=self.private.create_pf(sid,model,key,{},30)
        self.assertEqual(self.private.create_pf(sid,model,key,{},30),(identity,status))
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:self.private.create_pf(sid,model,str(uuid.uuid4()),{},30)
        self.assertEqual(error.exception.status_code,409)
        changed=pf_payload();changed['applicant']['cpf']='52998224725'
        with self.assertRaises(HTTPException) as error:self.private.create_pf(sid,PF.model_validate(changed),str(uuid.uuid4()),{},30)
        self.assertEqual(error.exception.status_code,409)
        for _ in range(2):
            with self.store.connect() as db:db.execute("UPDATE pf_records SET status='received' WHERE id=?",(identity,))
            identity,_=self.private.create_pf(sid,model,str(uuid.uuid4()),{},30)
        with self.store.connect() as db:db.execute("UPDATE pf_records SET status='received' WHERE id=?",(identity,))
        with self.assertRaises(HTTPException) as error:self.private.create_pf('another-verified-session',model,str(uuid.uuid4()),{},30)
        self.assertEqual(error.exception.status_code,429)
        self.private.storage_max=8
        self.private.save_file(identity,'front',b'12345678','jpg')
        self.private.save_file(identity,'front',b'87654321','jpg')
        with self.assertRaises(HTTPException) as error:self.private.save_file(identity,'back',b'123','jpg')
        self.assertEqual(error.exception.status_code,507)
        with patch('shutil.disk_usage',return_value=type('Usage',(),{'free':0})()):
            self.assertEqual(self.client.put('/api/pf/applications/'+identity+'/files/front',content=b'x',headers=self.headers).status_code,507)


class ResendTests(unittest.TestCase):
    def test_twilio_sms_does_not_depend_on_resend_email_configuration(self):
        seen=[]
        def receive(request):
            seen.append(request)
            return httpx.Response(200,json={'sid':'VE'+'c'*32,'status':'pending','to':'+5511912345678',
                                           'channel':'sms','service_sid':'VA'+'b'*32})
        provider=Providers(httpx.MockTransport(receive));provider.email_provider='resend'
        provider.twilio_account='AC'+'a'*32;provider.twilio_service='VA'+'b'*32;provider.twilio_token='synthetic'
        self.assertFalse(provider.email_ready);self.assertTrue(provider.sms_ready);self.assertFalse(provider.verify_ready)
        self.assertEqual(provider.verification_start('+5511912345678','sms'),'VE'+'c'*32)
        self.assertEqual(seen[0].url.host,'verify.twilio.com')

    def test_server_challenge_binding_no_leak_replay_and_missing_sms(self):
        emails=[]
        def receive(request):
            emails.append(request)
            return httpx.Response(200,json={'id':str(uuid.uuid4())})
        with tempfile.TemporaryDirectory() as temp:
            provider=Providers(httpx.MockTransport(receive));provider.email_provider='resend';provider.resend_key='synthetic-resend-key';provider.email_from='contato@example.test'
            store=Store(Path(temp)/'applications.sqlite',secrets.token_bytes(32))
            now=time.time()
            app=create_app(store,'https://example.test',True,provider,lambda:now)
            with TestClient(app,base_url='https://example.test') as client:
                headers={'Origin':'https://example.test'}
                caps=client.get('/api/security/capabilities').json()
                self.assertTrue(caps['email_verification']);self.assertFalse(caps['sms_verification']);self.assertFalse(caps['pf_submission'])
                profile={k:payload()['applicant'][k] for k in ['full_name','cpf','email','phone']}
                self.assertEqual(client.post('/api/security/session',json=profile,headers=headers).status_code,200)
                sent=client.post('/api/security/codes/send',json={'channel':'email'},headers=headers)
                self.assertEqual(sent.status_code,200)
                outgoing=json.loads(emails[0].content);code=outgoing['text'].split('é ')[1][:6]
                self.assertNotIn(code,sent.text);self.assertEqual(emails[0].url.host,'api.resend.com')
                self.assertEqual(emails[0].headers['authorization'],'Bearer synthetic-resend-key');self.assertIn('idempotency-key',emails[0].headers)
                self.assertNotIn(code.encode(),store.path.read_bytes())
                bad='000000' if code!='000000' else '111111'
                self.assertEqual(client.post('/api/security/codes/check',json={'channel':'email','code':bad},headers=headers).status_code,422)
                self.assertEqual(client.post('/api/security/codes/check',json={'channel':'email','code':code},headers=headers).status_code,200)
                self.assertEqual(client.post('/api/security/codes/check',json={'channel':'email','code':code},headers=headers).status_code,403)
                self.assertEqual(client.post('/api/security/codes/send',json={'channel':'sms'},headers=headers).status_code,503)

    def test_resend_rejects_test_sender_and_failed_delivery(self):
        provider=Providers(httpx.MockTransport(lambda req:httpx.Response(403,json={'message':'not authorized'})))
        provider.email_provider='resend';provider.resend_key='synthetic';provider.email_from='onboarding@resend.dev'
        self.assertFalse(provider.email_ready)
        provider.email_from='contato@example.test'
        with self.assertRaises(ProviderUnavailable):provider.email_code('pessoa@example.test','123456',str(uuid.uuid4()))


if __name__=='__main__':unittest.main()
