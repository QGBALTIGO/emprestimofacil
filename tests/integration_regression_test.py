"""Adversarial provider/session contracts. Every contact and payment is synthetic."""
import concurrent.futures
import copy
import hashlib
import hmac
import json
import secrets
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

import httpx
import pyotp
from fastapi.testclient import TestClient

from backend.app import Application,Store,create_app
from backend.billing_models import Contract
from backend.cakto import Cakto
from backend.maintenance import create_administrator
from backend.providers import Providers,ProviderUnavailable
from backend.secure_models import PF
from backend_test import payload
from billing_test import FakeCakto,contract
from security_test import FakeProviders,profile,pf_payload


class IntegrationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=time.time();self.provider=FakeProviders()
        self.cakto=FakeCakto(lambda:self.now);self.contract=contract();self.cakto.product=self.contract['installments'][0]['product_id'];self.cakto.currency='BRL'
        self.store=Store(Path(self.temp.name)/'private/db.sqlite',secrets.token_bytes(32))
        self.app=create_app(self.store,'https://example.test',True,self.provider,lambda:self.now,cakto=self.cakto)
        self.client=TestClient(self.app,base_url='https://example.test',raise_server_exceptions=False);self.client.__enter__()
        self.private=self.app.state.security.private;self.headers={'Origin':'https://example.test'}

    def tearDown(self):self.client.__exit__(None,None,None);self.temp.cleanup()

    def post(self,path,body=None,**headers):return self.client.post(path,json={} if body is None else body,headers={**self.headers,**headers})
    def begin(self):self.assertEqual(self.post('/api/security/session',profile()).status_code,200)
    def verified(self):
        self.begin()
        for channel in ['email','sms']:
            self.assertEqual(self.post('/api/security/codes/send',{'channel':channel}).status_code,200)
            self.assertEqual(self.post('/api/security/codes/check',{'channel':channel,'code':'123456'}).status_code,200)
    def bank(self):
        self.verified();self.assertEqual(self.post('/api/security/bank/start',{'consent':True}).status_code,200)
        link=str(uuid.uuid4());self.provider.links[link]={'id':link,'external_id':self.provider.last_attempt}
        self.assertEqual(self.client.get(self.provider.last_callbacks['success']+'&link='+link).status_code,200)
        return link
    def admin(self):
        secret=pyotp.random_base32();password='Synthetic admin password 123'
        create_administrator(self.private,'team',password,secret)
        return {'username':'team','password':password,'code':pyotp.TOTP(secret).at(self.now)}
    def installment(self):
        billing=self.app.state.billing;billing.store.import_contract(Contract.model_validate(self.contract))
        with self.store.connect() as db:identity=db.execute('SELECT id FROM billing_installments ORDER BY number').fetchone()[0]
        self.assertEqual(self.post('/api/installments/session',{'token':billing.store.link(identity)}).status_code,200)
        return identity

    def test_logout_during_delivery_does_not_restore_revoked_session(self):
        self.begin();cookie=self.client.cookies.get('__Host-ef-session');entered=threading.Event();release=threading.Event()
        original=self.provider.verification_start
        def delayed(*args):entered.set();self.assertTrue(release.wait(5));return original(*args)
        self.provider.verification_start=delayed
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            send=pool.submit(self.post,'/api/security/codes/send',{'channel':'email'})
            self.assertTrue(entered.wait(5))
            logout=pool.submit(self.client.request,'DELETE','/api/security/session',json={},headers=self.headers)
            time.sleep(.05);release.set()
            self.assertEqual(send.result(timeout=10).status_code,200);self.assertEqual(logout.result(timeout=10).status_code,200)
        self.client.cookies.set('__Host-ef-session',cookie,domain='example.test',path='/')
        self.assertEqual(self.client.get('/api/security/session').status_code,401)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM secure_sessions').fetchone()[0],0)

    def test_two_valid_code_checks_consume_one_challenge(self):
        self.begin();self.post('/api/security/codes/send',{'channel':'email'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.post('/api/security/codes/check',{'channel':'email','code':'123456'}),range(2)))
        self.assertEqual(sorted(r.status_code for r in results),[200,403])
        self.assertEqual(self.provider.calls.count(('check','email')),1)

    def test_session_revocation_waits_for_in_progress_state_update(self):
        token,sid,state=self.private.new_session(profile());started=threading.Event();done=threading.Event()
        def revoke():started.set();self.private.expire_session(sid);done.set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with self.private.locked(sid):
                worker=pool.submit(revoke);self.assertTrue(started.wait(5))
                self.assertFalse(done.wait(.05))
                self.private.save_session(sid,state,self.now+7200)
            worker.result(timeout=5)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):self.private.session(token)

    def test_email_only_policy_receives_pf_without_claiming_sms(self):
        self.app.state.security.phone_required=False
        self.provider.email_ready=True;self.provider.sms_ready=False;self.provider.verify_ready=False
        self.begin();caps=self.client.get('/api/security/capabilities').json()
        self.assertTrue(caps['verification']);self.assertTrue(caps['pf_submission']);self.assertFalse(caps['phone_required'])
        self.assertFalse(caps['sms_verification'])
        self.assertEqual(self.post('/api/security/codes/send',{'channel':'email'}).status_code,200)
        checked=self.post('/api/security/codes/check',{'channel':'email','code':'123456'}).json()
        self.assertTrue(checked['email_verified']);self.assertFalse(checked['phone_verified'])
        self.assertEqual(self.post('/api/security/codes/send',{'channel':'sms'}).status_code,503)
        key=str(uuid.uuid4())
        received=self.post('/api/pf/applications',pf_payload(),**{'Idempotency-Key':key})
        self.assertEqual(received.status_code,201)
        record=self.private.pf(received.json()['application_id'])[0]
        self.assertEqual(set(record['server_attestations']['contact_verified_at']),{'email'})
        self.assertFalse(record['server_attestations']['phone_verification_required'])
        self.assertEqual(self.post('/api/pf/applications',pf_payload(),**{'Idempotency-Key':str(uuid.uuid4())}).status_code,409)

    def test_unverified_phone_does_not_reserve_another_email_only_applicant(self):
        first=PF.model_validate(pf_payload());second_body=pf_payload();second_body['applicant']['email']='another-owner@example.test'
        second=PF.model_validate(second_body)
        attestations={'contact_verified_at':{'email':self.now},'phone_verification_required':False}
        self.private.create_pf('verified-email-1',first,str(uuid.uuid4()),attestations,30)
        self.private.create_pf('verified-email-2',second,str(uuid.uuid4()),attestations,30)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pf_records').fetchone()[0],2)
            self.assertTrue(all(not r[0] for r in db.execute('SELECT owner_digest FROM pf_records')))

    def test_incomplete_pf_deadline_is_enforced_before_maintenance(self):
        from fastapi import HTTPException
        identity,_=self.private.create_pf('synthetic',PF.model_validate(pf_payload()),str(uuid.uuid4()),{},30)
        self.now+=86401
        with self.assertRaises(HTTPException) as error:self.private.pf(identity)
        self.assertEqual(error.exception.status_code,404)
        with self.assertRaises(HTTPException):self.private.save_file(identity,'front',b'synthetic','jpg')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM private_files').fetchone()[0],0)

    def test_failed_resend_removes_previous_challenge(self):
        self.begin();self.post('/api/security/codes/send',{'channel':'email'});self.now+=61
        self.provider.verification_start=lambda *args:(_ for _ in ()).throw(ProviderUnavailable())
        self.assertEqual(self.post('/api/security/codes/send',{'channel':'email'}).status_code,503)
        self.assertEqual(self.post('/api/security/codes/check',{'channel':'email','code':'123456'}).status_code,403)

    def test_concurrent_mfa_login_consumes_one_totp(self):
        body=self.admin()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.post('/api/admin/login',body),range(2)))
        self.assertEqual(sorted(r.status_code for r in results),[200,401])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0],1)

    def test_concurrent_initial_and_pf_idempotency(self):
        key=str(uuid.uuid4());model=Application.model_validate(payload())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:ids=list(pool.map(lambda _:self.store.save(model,key),range(4)))
        self.assertEqual(len(set(ids)),1)
        self.verified();pf=PF.model_validate(pf_payload());sid=self.private.session(self.client.cookies.get('__Host-ef-session'))[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(lambda _:self.private.create_pf(sid,pf,key,{},30)[0],range(4)))
        self.assertEqual(len(set(ids)),1)

    def test_bank_webhook_cannot_replace_link_after_verified_callback(self):
        link=self.bank();other=str(uuid.uuid4());self.provider.links[other]={'id':other,'external_id':self.provider.last_attempt}
        event={'webhook_type':'OWNERS','webhook_code':'historical_update','link_id':other,'data':{}}
        result=self.post('/api/security/bank/webhook',event,Authorization='Bearer '+self.provider.webhook_secret)
        self.assertEqual(result.status_code,403)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT link FROM bank_attempts').fetchone()[0],link)

    def test_bank_malformed_event_and_resource_response_are_controlled(self):
        link=self.bank();event={'webhook_type':'OWNERS','webhook_code':'historical_update','link_id':link,'data':None}
        self.assertEqual(self.post('/api/security/bank/webhook',event,Authorization='Bearer '+self.provider.webhook_secret).status_code,422)
        for resource in ['OWNERS','ACCOUNTS','TRANSACTIONS']:
            self.assertEqual(self.post('/api/security/bank/webhook',{**event,'webhook_type':resource,'data':{}},Authorization='Bearer '+self.provider.webhook_secret).status_code,200)
        self.provider.bank_resources=lambda _: {'owners':[{'document_id':None}],'accounts':[],'transactions':[],'truncated':False}
        result=self.client.get('/api/security/bank/status');self.assertEqual(result.status_code,503)
        self.assertNotIn('data_ready',result.text)

    def test_guard_errors_are_private_and_never_echo_credentials(self):
        cases=[('/api/admin/login',{'password':'private-do-not-echo'},{'Origin':'https://other.test'},403),
               ('/api/admin/login',{}, {'Origin':'https://example.test','Content-Type':'text/plain'},415)]
        for path,body,headers,status in cases:
            result=self.post(path,body,**headers);self.assertEqual(result.status_code,status)
            self.assertIn('no-store',result.headers.get('cache-control',''));self.assertEqual(result.headers.get('x-content-type-options'),'nosniff')
            self.assertNotIn('private-do-not-echo',result.text)
        self.app.state.security.enabled=False
        result=self.post('/api/applications',payload());self.assertEqual(result.status_code,503)
        self.assertIn('no-store',result.headers.get('cache-control',''))

    def test_concurrent_pix_creation_does_not_duplicate_charge(self):
        identity=self.installment();entered=threading.Event();release=threading.Event();original=self.cakto.create_pix
        def delayed(*args):entered.set();self.assertTrue(release.wait(5));return original(*args)
        self.cakto.create_pix=delayed
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.post,'/api/installments/pix');self.assertTrue(entered.wait(5))
            second=pool.submit(self.post,'/api/installments/pix');time.sleep(.05);release.set()
            self.assertEqual(first.result(timeout=10).status_code,200);self.assertEqual(second.result(timeout=10).status_code,200)
        self.assertEqual(len([c for c in self.cakto.calls if isinstance(c,tuple) and c[0]=='create']),1)

    def test_signed_webhook_duplicate_is_checked_inside_payment_lock(self):
        identity=self.installment();self.assertEqual(self.post('/api/installments/pix').status_code,200)
        with self.store.connect() as db:order_id=db.execute('SELECT order_id FROM billing_attempts').fetchone()[0]
        raw=json.dumps({'event':'purchase_approved','data':{'id':order_id}}).encode();timestamp=str(int(self.now))
        signature='v1='+hmac.new(self.cakto.webhook_secret.encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        headers={'x-cakto-timestamp':timestamp,'x-cakto-signature':signature}
        entered=threading.Event();release=threading.Event();original=self.cakto.order
        def delayed(*args):entered.set();self.assertTrue(release.wait(5));return original(*args)
        self.cakto.order=delayed
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.app.state.billing.webhook,raw,headers);self.assertTrue(entered.wait(5))
            second=pool.submit(self.app.state.billing.webhook,raw,headers);time.sleep(.05);release.set()
            statuses=[first.result(timeout=10)['status'],second.result(timeout=10)['status']]
        self.assertEqual(sorted(statuses),['duplicate','received'])
        self.assertNotEqual(self.app.state.billing.view(identity)['status'],'paid')

    def test_order_offer_currency_and_nested_shapes_must_match(self):
        identity=self.installment();self.assertEqual(self.post('/api/installments/pix').status_code,200)
        original=self.cakto.order
        for change in [{'product':None},{'customer':None},{'offer':{'id':'different'}},{'currency':'USD'}]:
            self.cakto.order=lambda order,change=change:{**original(order),**change}
            self.now+=31;result=self.post('/api/installments/check')
            self.assertEqual(result.status_code,503,change)
            self.assertNotEqual(self.app.state.billing.view(identity)['status'],'paid')


class ProviderMalformedTests(unittest.TestCase):
    def provider(self,body,status=200):
        provider=Providers(httpx.MockTransport(lambda _:httpx.Response(status,json=body)))
        provider.twilio_account='AC'+'a'*32;provider.twilio_service='VA'+'b'*32;provider.twilio_token='synthetic'
        provider.belvo_id='synthetic';provider.belvo_password='synthetic';provider.webhook_secret='s'*40
        provider.company_name='Synthetic';provider.terms_url='https://example.test/terms'
        return provider

    def test_twilio_and_belvo_malformed_bodies_fail_as_provider_errors(self):
        for body in [None,[],{'sid':None},{'sid':'VE'+'c'*32,'status':'pending','to':None}]:
            with self.subTest(body=body):
                with self.assertRaises(ProviderUnavailable):self.provider(body).verification_start('+5511912345678','sms')
        for body in [None,[],{'access':False}]:
            with self.subTest(body=body):
                with self.assertRaises(ProviderUnavailable):self.provider(body).bank_start(profile(),'synthetic',{name:'https://example.test/api/'+name for name in ['success','exit','event']})
        for body in [{'results':[None]},{'results':[{}]},{'results':{},'next':None}]:
            with self.assertRaises(ProviderUnavailable):self.provider(body).bank_resources(str(uuid.uuid4()))

    def test_oauth_concurrency_fetches_one_token_and_bad_tokens_never_cache(self):
        calls=[]
        def response(request):
            calls.append(request)
            return httpx.Response(200,json={'access_token':'synthetic-token-123456','token_type':'Bearer','expires_in':3600,'scope':'read orders'})
        cakto=Cakto(httpx.MockTransport(response));cakto.client_id='synthetic';cakto.client_secret='synthetic'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:cakto.headers({'read'}),range(8)))
        self.assertEqual(len(calls),1)
        for body in [{'access_token':'short','token_type':'Bearer','expires_in':3600,'scope':'read'},
                     {'access_token':'synthetic-token-123456','token_type':'Bearer','expires_in':True,'scope':'read'},
                     {'access_token':'synthetic-token-123456','token_type':'Bearer','expires_in':3600,'scope':None}]:
            broken=Cakto(httpx.MockTransport(lambda _,body=body:httpx.Response(200,json=body)));broken.client_id='x';broken.client_secret='x'
            with self.assertRaises(ProviderUnavailable):broken.headers({'read'})
            self.assertEqual(broken._token,'')


if __name__=='__main__':unittest.main()
