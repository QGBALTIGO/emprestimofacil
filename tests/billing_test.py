"""Synthetic invoices and fake provider: no money, external messages or live customer data."""
import copy
import hashlib
import hmac
import json
import secrets
import tempfile
import time
import unittest
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pyotp
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import Store,create_app
from backend.billing_models import Contract
from backend.billing_store import BillingStore
from backend.cakto import Cakto,cents
from backend.maintenance import backup,restore,create_administrator
from backend.private_store import PrivateStore
from backend.providers import ProviderUnavailable
from backend_test import payload


def contract():
    now=datetime.now(timezone.utc);product=str(uuid.uuid4())
    return {'id':str(uuid.uuid4()),'reference':'Contrato de teste','customer':payload()['applicant'],
            'principal_cents':50000,'signed':True,'signed_at':(now-timedelta(days=2)).isoformat(),
            'signed_document_sha256':'a'*64,'funds_disbursed':True,'disbursed_at':(now-timedelta(days=1)).isoformat(),
            'disbursement_reference':'comprovante-sintetico','payment_data_authorized':True,'frequency':'weekly',
            'installments':[{'number':i+1,'amount_cents':20000,'due_date':(now+timedelta(days=7*(i+1))).date().isoformat(),
                             'offer_id':'offer-test','product_id':product} for i in range(3)]}


class FakeCakto(Cakto):
    def __init__(self,clock):
        super().__init__(clock=clock);self.client_id='synthetic';self.client_secret='synthetic-secret'
        self.enabled=self.operation_confirmed=True;self.webhook_secret='synthetic-webhook-secret-12345';self.calls=[];self.orders={};self.status='waiting_payment';self.fail=False
    def check_connection(self):return {'authenticated':True,'can_charge':self.can_charge}
    def offer(self,identity):
        self.calls.append('offer');return {'id':identity,'product':self.product,'status':'active','type':'unique','price':'200.00','currency':self.currency}
    def create_pix(self,body,request_id):
        self.calls.append(('create',copy.deepcopy(body),request_id))
        if self.fail:raise ProviderUnavailable()
        identity=str(uuid.uuid4())
        result={'id':identity,'status':'waiting_payment','paymentMethod':'pix','amount':'200.00',
                'product':{'id':self.product},'offer':{'id':'offer-test'},'pix':{'qrCode':'000201'+('synthetic-code-'*8),'expirationDate':datetime.fromtimestamp(self.clock()+900,timezone.utc).isoformat()}}
        self.orders[identity]={**copy.deepcopy(result),'customer':body['customer'],'sck':request_id,'paidAt':None}
        return result
    def order(self,identity):
        self.calls.append(('order',identity));order=copy.deepcopy(self.orders[identity]);order['status']=self.status
        order['paidAt']=datetime.fromtimestamp(self.clock(),timezone.utc).isoformat() if self.status=='paid' else None
        if hasattr(self,'wrong_cpf'):order['customer']['docNumber']=self.wrong_cpf
        if hasattr(self,'wrong_amount'):order['amount']=self.wrong_amount
        return order


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=time.time();self.clock=lambda:self.now
        self.provider=FakeCakto(self.clock);self.body=contract();self.provider.product=self.body['installments'][0]['product_id'];self.provider.currency='BRL'
        self.key=secrets.token_bytes(32);self.base=Store(Path(self.temp.name)/'private/app.sqlite',self.key)
        self.app=create_app(self.base,'https://example.test',True,cakto=self.provider,clock=self.clock)
        self.client=TestClient(self.app,base_url='https://example.test');self.client.__enter__();self.store=self.app.state.billing.store
        self.store.import_contract(Contract.model_validate(self.body))
        with self.base.connect() as db:self.identity=db.execute('SELECT id FROM billing_installments ORDER BY number').fetchone()[0]
        self.headers={'Origin':'https://example.test'}
    def tearDown(self):self.client.__exit__(None,None,None);self.temp.cleanup()
    def login(self,client=None):
        token=self.store.link(self.identity);r=(client or self.client).post('/api/installments/session',json={'token':token},headers=self.headers)
        self.assertEqual(r.status_code,200);return token,r
    def create(self):return self.client.post('/api/installments/pix',json={},headers=self.headers)
    def webhook(self,data):
        body=json.dumps({'event':'purchase_approved','data':data}).encode();timestamp=str(int(self.now))
        signature='v1='+hmac.new(self.provider.webhook_secret.encode(),timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        return self.client.post('/api/installments/cakto/webhook',content=body,headers={'Content-Type':'application/json','X-Cakto-Timestamp':timestamp,'X-Cakto-Signature':signature})

    def test_only_existing_signed_disbursed_contracts(self):
        for update in [{'signed':False},{'funds_disbursed':False},{'payment_data_authorized':False},{'frequency':'monthly'}]:
            invalid={**self.body,**update}
            with self.assertRaises(ValidationError):Contract.model_validate(invalid)
        invalid=copy.deepcopy(self.body);invalid['installments'][0]['due_date']=self.body['disbursed_at'][:10]
        with self.assertRaises(ValidationError):Contract.model_validate(invalid)
        self.assertEqual(self.client.post('/api/admin/contracts',json=self.body,headers=self.headers).status_code,404)
        changed=copy.deepcopy(self.body);changed['reference']='Changed'
        with self.assertRaises(ValueError):self.store.import_contract(Contract.model_validate(changed))

    def test_private_session_origin_expiration_and_scope(self):
        self.assertEqual(self.client.get('/api/installments/current').status_code,401)
        token,r=self.login();cookie=r.headers['set-cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie);self.assertIn('SameSite=strict',cookie)
        self.assertEqual(self.client.post('/api/installments/pix',json={},headers={'Origin':'https://evil.test'}).status_code,403)
        view=self.client.get('/api/installments/current').json();self.assertNotIn('cpf',view);self.assertNotIn('email',view);self.assertEqual(view['customer_cpf_suffix'],'35')
        with self.base.connect() as db:self.assertNotIn(token,repr(db.execute('SELECT * FROM billing_links').fetchall()))
        self.store.link(self.identity);self.assertEqual(self.client.get('/api/installments/current').status_code,401)
        self.login();self.now+=1801;self.assertEqual(self.client.get('/api/installments/current').status_code,401)

    def test_pix_value_offer_metadata_and_no_duplicate_charge(self):
        self.login();r=self.create();self.assertEqual(r.status_code,200,r.text);view=r.json();self.assertEqual(view['status'],'waiting_payment');self.assertTrue(view['pix']['qr_data_url'].startswith('data:image/png;base64,'))
        self.assertEqual(self.create().status_code,200)
        creates=[c for c in self.provider.calls if isinstance(c,tuple) and c[0]=='create'];self.assertEqual(len(creates),1)
        body=creates[0][1];self.assertEqual(body['customer']['docNumber'],'11144477735');self.assertEqual(body['customer']['phone'],'5511912345678');self.assertEqual(body['metadata'],{'sck':creates[0][2]});self.assertNotIn('amount',body)
        database=self.base.path.read_bytes();self.assertNotIn(b'pessoa@example.test',database);self.assertNotIn(b'11144477735',database)

    def test_only_authoritative_full_payment_changes_status(self):
        self.login();self.create()
        for status in ['authorized','partially_paid','processing']:
            self.provider.status=status;self.now+=31
            self.assertNotEqual(self.client.post('/api/installments/check',json={},headers=self.headers).json()['status'],'paid')
        self.provider.status='paid';self.now+=31
        self.assertEqual(self.client.post('/api/installments/check',json={},headers=self.headers).json()['status'],'paid')
        self.assertIsNone(self.create().json()['pix'])
        self.provider.status='refunded';self.now+=31
        self.assertEqual(self.client.post('/api/installments/check',json={},headers=self.headers).json()['status'],'refunded')

    def test_cpf_and_amount_mismatch_cannot_confirm_payment(self):
        self.login();self.create();self.provider.status='paid';self.provider.wrong_cpf='12345678909';self.now+=31
        self.assertEqual(self.client.post('/api/installments/check',json={},headers=self.headers).status_code,503)
        self.assertEqual(self.client.get('/api/installments/current').json()['status'],'review')
        del self.provider.wrong_cpf;self.provider.wrong_amount='201.00';self.now+=31
        self.assertEqual(self.client.post('/api/installments/check',json={},headers=self.headers).status_code,503)

    def test_uncertain_creation_does_not_create_another_payment(self):
        self.login();self.provider.fail=True
        self.assertEqual(self.create().status_code,503);self.provider.fail=False
        self.assertEqual(self.create().status_code,409)
        creates=[c for c in self.provider.calls if isinstance(c,tuple) and c[0]=='create'];self.assertEqual(len(creates),1)

    def test_currency_mismatch_and_unconfigured_account(self):
        self.login();self.provider.currency='USD';self.assertEqual(self.create().status_code,409)
        self.assertFalse(any(isinstance(c,tuple) and c[0]=='create' for c in self.provider.calls))
        self.provider.operation_confirmed=False;self.assertEqual(self.create().status_code,503)

    def test_expired_qr_does_not_imply_payment_or_duplicate(self):
        self.login();self.create();self.now+=901
        result=self.client.get('/api/installments/current').json();self.assertEqual(result['status'],'expired');self.assertIsNone(result['pix']);self.assertFalse(result['can_generate'])
        self.create();self.assertEqual(len([c for c in self.provider.calls if isinstance(c,tuple) and c[0]=='create']),1)

    def test_signed_webhook_reconciles_once_and_rejects_tampering(self):
        self.login();self.create();identity=next(iter(self.provider.orders));self.provider.status='paid';data={**self.provider.orders[identity],'status':'paid'}
        self.assertEqual(self.client.post('/api/installments/cakto/webhook',json={'event':'purchase_approved','data':data}).status_code,401)
        r=self.webhook(data);self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'received')
        self.assertEqual(self.webhook(data).json()['status'],'duplicate');self.assertEqual(self.client.get('/api/installments/current').json()['status'],'paid')
        self.assertFalse(self.provider.valid_webhook(b'changed',str(int(self.now)),'v1='+'0'*64));self.assertFalse(self.provider.valid_webhook(b'{}',str(int(self.now)-400),'v1='+'0'*64))

    def test_backup_restore_private_contracts_and_retention(self):
        self.login();self.create();private=self.app.state.security.private;copied=backup(private,Path(self.temp.name)/'backups')
        target=PrivateStore(Store(Path(self.temp.name)/'restored/app.sqlite',self.key));restore(target,copied);restored=BillingStore(target)
        with target.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM billing_sessions').fetchone()[0],0);self.assertEqual(db.execute('SELECT count(*) FROM billing_links').fetchone()[0],0)
        self.assertEqual(restored.installment(self.identity)[1]['amount_cents'],20000)
        self.now+=366*86400;self.assertEqual(self.store.purge(),0) # Do not discard active debts by age.
        with self.base.connect() as db:db.execute("UPDATE billing_installments SET status='paid'")
        self.assertEqual(self.store.purge(),1)

    def test_admin_mfa_is_required_to_list_or_issue_payment_links(self):
        self.assertEqual(self.client.get('/api/admin/installments').status_code,401)
        self.assertEqual(self.client.get('/api/admin/cakto/status').status_code,401)
        secret=pyotp.random_base32();password='Synthetic admin password!'
        create_administrator(self.app.state.security.private,'team',password,secret)
        login=self.client.post('/api/admin/login',json={'username':'team','password':password,'code':pyotp.TOTP(secret).at(self.now)},headers=self.headers)
        self.assertEqual(login.status_code,200)
        self.assertTrue(self.client.get('/api/admin/cakto/status').json()['authenticated'])
        listing=self.client.get('/api/admin/installments').json()['installments'];self.assertEqual(len(listing),3)
        link=self.client.post('/api/admin/installments/'+self.identity+'/link',json={},headers=self.headers)
        self.assertEqual(link.status_code,200);self.assertTrue(link.json()['url'].startswith('https://example.test/pagar#'))
        self.assertNotIn('11144477735',link.text)


class CaktoWireTests(unittest.TestCase):
    def test_oauth_cache_headers_no_redirects_and_fixed_payment_contract(self):
        calls=[];now=[1000]
        def respond(request):
            calls.append(request)
            if request.url.path.endswith('/token/'):
                self.assertEqual(parse_qs(request.content.decode()),{'client_id':['synthetic-client'],'client_secret':['synthetic-secret']})
                return httpx.Response(200,json={'access_token':'synthetic-token-at-least-ten-characters','token_type':'Bearer','expires_in':36000,'scope':'read write orders offers payments'})
            return httpx.Response(201 if request.method=='POST' else 200,json={'id':'synthetic-result'})
        client=Cakto(httpx.MockTransport(respond),lambda:now[0]);client.client_id='synthetic-client';client.client_secret='synthetic-secret';client.webhook_secret='s'*32;client.enabled=client.operation_confirmed=True
        client.offer('offer1');client.order(str(uuid.uuid4()));request_id=str(uuid.uuid4());client.create_pix({'paymentMethod':'pix'},request_id)
        self.assertEqual(len([r for r in calls if r.url.path.endswith('/token/')]),1);self.assertEqual(calls[-1].headers['X-Idempotency-Key'],request_id)
        self.assertEqual(calls[-1].url.host,'api.cakto.com.br');self.assertEqual(calls[-1].headers['Authorization'],'Bearer synthetic-token-at-least-ten-characters')
        with self.assertRaises(ProviderUnavailable):client.offer('../withdrawals')
        self.assertEqual(cents('200.00'),20000)
        for value in [float('nan'),'-1','1.001',True]:
            with self.assertRaises(ProviderUnavailable):cents(value)


if __name__=='__main__':unittest.main()
