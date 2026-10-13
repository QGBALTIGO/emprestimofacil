"""Dados sintéticos e provedores substituídos; nenhum SMS, e-mail ou banco real."""
import copy
import io
import json
import os
import secrets
import sqlite3
import subprocess
import tempfile
import time
import unittest
import uuid
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.parse import parse_qs,urlparse

from cryptography.exceptions import InvalidTag
import httpx
import pyotp
from PIL import Image
from pypdf import PdfWriter
from fastapi.testclient import TestClient

from backend.app import Store,create_app
from backend.file_validation import validate_file
from backend.maintenance import audit_valid,backup,restore,create_administrator
from backend.private_store import PrivateStore
from backend.providers import Providers,ProviderUnavailable
from backend_test import payload


class FakeProviders:
    verify_ready=True
    bank_ready=True
    belvo_host='production'
    webhook_secret='synthetic-webhook-token-000000000000000'
    def __init__(self):
        self.challenges={};self.links={};self.deleted=[];self.owner_cpf='11144477735';self.resource_reads=0;self.calls=[]
    def verification_start(self,destination,channel):
        sid='VE'+uuid.uuid4().hex;self.challenges[sid]=(destination,channel);self.calls.append(('send',channel));return sid
    def verification_check(self,sid,code,destination,channel):
        self.calls.append(('check',channel));return code=='123456' and self.challenges.get(sid)==(destination,channel)
    def bank_start(self,profile,attempt,callbacks):
        self.last_attempt=attempt;self.last_callbacks=callbacks;self.last_profile=profile.copy()
        return 'https://widget.belvo.io/?access_token=synthetic-token-for-testing&integration_type=openfinance'
    def bank_link(self,link):
        return self.links.get(link,{'id':link,'external_id':'unrelated'})
    def bank_resources(self,link):
        self.resource_reads+=1
        return {'owners':[{'link':link,'document_id':{'document_type':'CPF','document_number':self.owner_cpf},'display_name':'Pessoa de Teste'}],
                'accounts':[{'link':link,'id':str(uuid.uuid4()),'name':'Conta de teste'}],
                'transactions':[{'link':link,'id':str(uuid.uuid4()),'amount':1000,'type':'INFLOW'}],'truncated':False}
    def bank_delete(self,link):
        self.deleted.append(link)


def profile():
    return {k:payload()['applicant'][k] for k in ['full_name','email','phone','cpf']}


def pf_payload():
    return {'schema_version':1,'applicant':payload()['applicant'],'address':payload()['address'],
            'account':{'trade_name':'Pessoa de Teste','monthly_income':2000,'occupation':'self_employed',
                       'activity':'Serviços','support_email':'pessoa@example.test','support_phone':'','support_site':'','income_source':None},
            'holder':{'gender':'','document_type':'rg','document_number':'12345678','issue_date':'2010-01-01'},
            'contacts':[{'name':'Familiar de Teste '+str(i),'phone':'1191234567'+str(i),'relation':'sibling'} for i in range(5)],
            'instagram':'https://instagram.com/perfildeteste','location':{'latitude':-23.55,'longitude':-46.63,'accuracy':14,
                       'captured_at':datetime.now(timezone.utc).isoformat()},'address_proof_date':date.today().isoformat(),
            'consents':{'data':True,'contacts':True,'location':True,'address_captures':True,'version':'2026-10-13'}}


def jpeg():
    stream=io.BytesIO();Image.new('RGB',(64,64),(80,120,90)).save(stream,'JPEG');return stream.getvalue()


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'video.webm'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=64x64:r=10',
                            '-t','2.1','-c:v','libvpx','-an',str(path)],check=True)
            cls.video=path.read_bytes()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.key=secrets.token_bytes(32);self.clock=time.time()
        self.store=Store(Path(self.temp.name)/'private/applications.sqlite',self.key);self.provider=FakeProviders()
        self.app=create_app(self.store,'https://example.test',True,self.provider,lambda:self.clock)
        self.client=TestClient(self.app,base_url='https://example.test');self.client.__enter__()
        self.headers={'Origin':'https://example.test','Content-Type':'application/json'}

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def post(self,path,body=None,**headers):
        return self.client.post(path,json={} if body is None else body,headers={**self.headers,**headers})

    def begin(self,data=None):
        result=self.post('/api/security/session',data or profile());self.assertEqual(result.status_code,200);return result

    def verify(self):
        self.begin()
        for channel in ['email','sms']:
            self.assertEqual(self.post('/api/security/codes/send',{'channel':channel}).status_code,200)
            r=self.post('/api/security/codes/check',{'channel':channel,'code':'123456'})
            self.assertEqual(r.status_code,200);self.assertEqual(r.json()['status'],'verified')

    def bank(self,ready=True):
        r=self.post('/api/security/bank/start',{'consent':True});self.assertEqual(r.status_code,200)
        link=str(uuid.uuid4());self.provider.links[link]={'id':link,'external_id':self.provider.last_attempt}
        callback=self.provider.last_callbacks['success']+'&link='+link
        r=self.client.get(callback);self.assertEqual(r.status_code,200)
        if ready:
            for resource in ['OWNERS','ACCOUNTS','TRANSACTIONS']:
                event={'webhook_type':resource,'webhook_code':'historical_update','link_id':link,
                       'external_id':self.provider.last_attempt,'data':{'total':1}}
                result=self.post('/api/security/bank/webhook',event,Authorization='Bearer '+self.provider.webhook_secret)
                self.assertEqual(result.status_code,200)
        return link,callback

    def start_pf(self):
        r=self.post('/api/pf/applications',pf_payload(),**{'Idempotency-Key':str(uuid.uuid4())})
        self.assertEqual(r.status_code,201);return r.json()['application_id']

    def upload(self,identity,kind,content=None):
        raw=content if content is not None else self.video if kind in {'video','house_entry'} else jpeg()
        return self.client.put('/api/pf/applications/'+identity+'/files/'+kind,content=raw,
                               headers={'Origin':'https://example.test','Content-Type':'application/octet-stream'})

    def test_session_cookie_and_unverified_submission(self):
        r=self.begin();cookie=r.headers['set-cookie']
        for part in ['__Host-ef-session','HttpOnly','Secure','SameSite=strict','Path=/']:
            self.assertIn(part,cookie)
        self.assertNotIn('token',r.json());self.assertNotIn('cpf',r.json());self.assertFalse(r.json()['email_verified'])
        r=self.post('/api/pf/applications',pf_payload(),**{'Idempotency-Key':str(uuid.uuid4())});self.assertEqual(r.status_code,403)
        self.assertEqual(self.post('/api/security/session',{**profile(),'email_verified':True}).status_code,422)
        self.assertEqual(self.post('/api/security/session',profile(),Origin='https://attacker.test').status_code,403)
        self.assertEqual(self.client.get('/api/security/session').headers['cache-control'],'no-store')

    def test_delivery_cooldown_limits_expiry_and_replay(self):
        self.begin();channel={'channel':'email'}
        self.assertEqual(self.post('/api/security/codes/send',channel).json()['status'],'sent')
        self.assertEqual(self.post('/api/security/codes/send',channel).status_code,429)
        self.clock+=61
        for _ in range(5):
            self.assertEqual(self.post('/api/security/codes/check',{**channel,'code':'000000'}).status_code,422)
        self.assertEqual(self.post('/api/security/codes/check',{**channel,'code':'123456'}).status_code,403)
        self.assertEqual(self.post('/api/security/codes/send',channel).status_code,200)
        self.clock+=601
        self.assertEqual(self.post('/api/security/codes/check',{**channel,'code':'123456'}).status_code,403)
        self.assertEqual(self.post('/api/security/codes/send',channel).status_code,200)
        self.assertEqual(self.post('/api/security/codes/check',{**channel,'code':'123456'}).status_code,200)
        self.assertEqual(self.post('/api/security/codes/check',{**channel,'code':'123456'}).status_code,403)

    def test_changed_contact_rotates_session_and_invalidates_codes(self):
        self.verify();old=self.client.cookies.get('__Host-ef-session');changed=profile();changed['email']='outro@example.test'
        r=self.begin(changed);self.assertFalse(r.json()['email_verified']);self.assertFalse(r.json()['phone_verified'])
        self.assertNotEqual(self.client.cookies.get('__Host-ef-session'),old)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM secure_sessions').fetchone()[0],1)

    def test_bank_requires_contacts_consent_and_matching_return(self):
        self.begin();self.assertEqual(self.post('/api/security/bank/start',{'consent':True}).status_code,403)
        self.verify();self.assertEqual(self.post('/api/security/bank/start',{'consent':False}).status_code,422)
        self.assertEqual(self.post('/api/security/bank/start',{'consent':True}).status_code,200)
        wrong=str(uuid.uuid4());callback=self.provider.last_callbacks['success']+'&link='+wrong
        self.assertEqual(self.client.get(callback).status_code,403)
        good=str(uuid.uuid4());self.provider.links[good]={'id':good,'external_id':self.provider.last_attempt}
        callback=self.provider.last_callbacks['success']+'&link='+good
        self.assertEqual(self.client.get(callback).status_code,200)
        self.assertEqual(self.client.get(callback).status_code,400)
        state=self.client.get('/api/security/bank/status').json();self.assertEqual(state['bank_status'],'connected')
        self.assertFalse(state['cpf_match']);self.assertEqual(self.provider.resource_reads,0)

    def test_webhooks_verified_resources_and_identity(self):
        self.verify();link,_=self.bank(ready=False)
        bad={'webhook_type':'OWNERS','webhook_code':'historical_update','link_id':link,'data':{}}
        self.assertEqual(self.post('/api/security/bank/webhook',bad).status_code,401)
        self.assertEqual(self.post('/api/security/bank/webhook',{**bad,'external_id':'other'},Authorization='Bearer '+self.provider.webhook_secret).status_code,403)
        for resource in ['OWNERS','ACCOUNTS','TRANSACTIONS']:
            self.assertEqual(self.post('/api/security/bank/webhook',{**bad,'webhook_type':resource},Authorization='Bearer '+self.provider.webhook_secret).json()['status'],'accepted')
        state=self.client.get('/api/security/bank/status').json();self.assertEqual(state['bank_status'],'data_ready');self.assertTrue(state['cpf_match'])
        self.assertEqual(self.provider.resource_reads,1)
        self.client.get('/api/security/bank/status');self.assertEqual(self.provider.resource_reads,1)
        r=self.client.request('DELETE','/api/security/bank',json={},headers=self.headers)
        self.assertEqual(r.json()['bank_status'],'not_connected');self.assertEqual(self.provider.deleted,[link])

    def test_bank_cpf_mismatch_and_required_connection(self):
        self.app.state.security.bank_required=True;self.verify();self.provider.owner_cpf='52998224725';self.bank()
        state=self.client.get('/api/security/bank/status').json();self.assertEqual(state['bank_status'],'mismatch');self.assertFalse(state['cpf_match'])
        self.assertEqual(self.post('/api/applications',payload(),**{'Idempotency-Key':str(uuid.uuid4())}).status_code,201)
        self.assertEqual(self.post('/api/pf/applications',pf_payload(),**{'Idempotency-Key':str(uuid.uuid4())}).status_code,403)

    def test_pf_receipt_only_after_all_private_files(self):
        self.verify();identity=self.start_pf()
        self.assertEqual(self.post('/api/pf/applications/'+identity+'/complete').status_code,409)
        for kind in ['house_front','house_entry','front','back','selfie','video','address_proof','instagram_proof']:
            self.assertEqual(self.upload(identity,kind).status_code,200,kind)
        r=self.post('/api/pf/applications/'+identity+'/complete');self.assertEqual(r.json(),{'application_id':identity,'status':'received'})
        self.assertEqual(self.post('/api/pf/applications/'+identity+'/complete').json(),r.json())
        self.assertEqual(self.upload(identity,'front').status_code,409)
        raw=self.store.path.read_bytes()
        for content in [b'11144477735',b'pessoa@example.test',b'Familiar de Teste',b'instagram.com/perfildeteste']:
            self.assertNotIn(content,raw)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.app.state.security.private.files_dir.iterdir()))
        self.assertEqual(self.client.get('/documents/'+identity+'.enc').status_code,404)
        self.assertEqual(self.client.get('/api/admin/files/'+identity+'/front').status_code,401)

    def test_files_validation_size_and_cross_session_access(self):
        self.verify();identity=self.start_pf()
        self.assertEqual(self.upload(identity,'front',b'%PDF-1.7').status_code,422)
        self.assertEqual(self.upload(identity,'front',b'x'*(3*1024*1024+1)).status_code,413)
        self.assertEqual(self.upload(identity,'not-required',jpeg()).status_code,409)
        self.assertEqual(self.upload(identity,'front',jpeg()).status_code,200)
        alternate=profile();alternate['email']='outro@example.test';self.begin(alternate)
        self.assertEqual(self.upload(identity,'back').status_code,404)
        self.assertEqual(self.post('/api/pf/applications/'+identity+'/complete').status_code,404)

    def test_file_formats_and_active_pdf(self):
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);out=io.BytesIO();writer.write(out)
        self.assertEqual(validate_file(out.getvalue(),'address_proof')[1],'pdf')
        writer.add_js('app.alert("test")');out=io.BytesIO();writer.write(out)
        with self.assertRaises(ValueError):validate_file(out.getvalue(),'address_proof')
        with self.assertRaises(ValueError):validate_file(b'%PDF-1.7','instagram_proof')
        self.assertEqual(validate_file(self.video,'house_entry')[1],'webm')

    def test_admin_mfa_cookie_replay_and_audit(self):
        private=self.app.state.security.private;secret=pyotp.random_base32();password=' Senha da equipe 123 '
        create_administrator(private,'revisor',password,secret)
        self.assertEqual(self.client.get('/api/admin/records').status_code,401)
        body={'username':'revisor','password':password,'code':'000000'}
        self.assertEqual(self.post('/api/admin/login',body).status_code,401)
        body['code']=pyotp.TOTP(secret).at(self.clock)
        response=self.post('/api/admin/login',body);self.assertEqual(response.status_code,200)
        self.assertIn('HttpOnly',response.headers['set-cookie']);self.assertIn('Secure',response.headers['set-cookie'])
        self.assertEqual(self.post('/api/admin/login',body).status_code,401)
        self.assertEqual(self.client.get('/api/admin/records').status_code,200)
        self.assertTrue(audit_valid(private))
        raw=self.store.path.read_bytes();self.assertNotIn(secret.encode(),raw);self.assertNotIn(password.encode(),raw)
        self.assertEqual(self.post('/api/admin/logout').status_code,200)
        self.assertEqual(self.client.get('/api/admin/records').status_code,401)
        with self.store.connect() as db:db.execute("UPDATE access_audit SET action='tampered' WHERE seq=1")
        self.assertFalse(audit_valid(private))

    def test_backup_restore_expiration_and_private_files(self):
        self.verify();identity=self.start_pf();self.assertEqual(self.upload(identity,'front').status_code,200)
        private=self.app.state.security.private;copied=backup(private,Path(self.temp.name)/'backups')
        self.assertNotIn(b'pessoa@example.test',(copied/'applications.sqlite').read_bytes())
        self.assertEqual((copied.stat().st_mode & 0o777),0o700)
        target=PrivateStore(Store(Path(self.temp.name)/'restored/applications.sqlite',self.key))
        restore(target,copied);self.assertEqual(target.file(identity,'front')[1],'jpg')
        with target.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM secure_sessions').fetchone()[0],0)
        self.clock+=31*86400;stats=private.purge(30);self.assertEqual(stats['pf_removed'],1)
        self.assertEqual(list(private.files_dir.iterdir()),[])
        self.assertEqual(self.client.get('/api/security/session').status_code,401)

    def test_missing_providers_fail_closed(self):
        missing=FakeProviders();missing.verify_ready=False;missing.bank_ready=False
        with TestClient(create_app(self.store,'https://example.test',True,missing),base_url='https://example.test') as client:
            self.assertFalse(client.get('/api/security/capabilities').json()['verification'])
            self.assertTrue(client.get('/api/health').json()['receiving'])
            self.assertEqual(client.post('/api/security/session',json=profile(),headers=self.headers).status_code,503)

    def test_restore_without_files_rejects_wrong_key(self):
        self.verify()
        copied=backup(self.app.state.security.private,Path(self.temp.name)/'backup-without-files')
        target=PrivateStore(Store(Path(self.temp.name)/'wrong-key/applications.sqlite',secrets.token_bytes(32)))
        with self.assertRaises(InvalidTag):restore(target,copied)
        with target.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM secure_sessions').fetchone()[0],0)


class ProviderWireTests(unittest.TestCase):
    def test_twilio_and_belvo_use_server_credentials_and_bound_identifiers(self):
        seen=[]
        def respond(request):
            seen.append(request)
            if request.url.host=='verify.twilio.com':
                data={'sid':'VE'+'c'*32,'status':'approved' if request.url.path.endswith('VerificationCheck') else 'pending',
                      'to':'+5511912345678','channel':'sms','service_sid':'VA'+'b'*32}
                return httpx.Response(200,json=data)
            return httpx.Response(200,json={'access':'opaque-token-for-widget-integration'})
        provider=Providers(httpx.MockTransport(respond));provider.twilio_account='AC'+'a'*32;provider.twilio_service='VA'+'b'*32
        provider.twilio_token='synthetic-auth';provider.belvo_id='synthetic-id';provider.belvo_password='synthetic-password'
        provider.webhook_secret='s'*40;provider.company_name='Empresa de Teste';provider.terms_url='https://example.test/termos'
        sid=provider.verification_start('+5511912345678','sms');self.assertTrue(provider.verification_check(sid,'123456','+5511912345678','sms'))
        self.assertFalse(provider.verification_check(sid,'123456','outro@example.test','email'))
        url=provider.bank_start(profile(),'attempt-identifier',{event:'https://example.test/api/security/bank/callback/'+event for event in ['success','exit','event']})
        query=parse_qs(urlparse(url).query);self.assertEqual(query['integration_type'],['openfinance']);self.assertEqual(query['external_id'],['attempt-identifier'])
        self.assertNotIn('11144477735',url);self.assertNotIn('synthetic-password',url)
        self.assertTrue(all(request.headers.get('authorization','').startswith('Basic ') for request in seen))
        token_payload=json.loads(seen[-1].content);self.assertEqual(token_payload['widget']['consent']['identification_info'][0]['number'],'11144477735')


if __name__=='__main__':unittest.main()
