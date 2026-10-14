"""Independent product sales; synthetic customers/provider, never live charges."""
import copy
import contextlib
import io
import hashlib
import hmac
import json
import secrets
import tempfile
import time
import unittest
import uuid
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import Store,create_app
from backend.cakto import Cakto
from backend.providers import ProviderUnavailable
from backend.sales import SalesCakto
from backend.maintenance import backup,restore,purge

BUYER={'full_name':'Pessoa de Teste','email':'pessoa@example.test','phone':'11912345678','cpf':'11144477735'}

class Provider(SalesCakto):
    def __init__(self,clock):
        super().__init__(clock=clock);self.webhook_secret='synthetic-secret-not-for-production';self.calls=[]
        self.product=str(uuid.uuid4());self.orders={};self.status='waiting_payment';self.price='7.49';self.fail=False
    @property
    def can_charge(self):return True
    def offer(self,identity):return {'id':identity,'name':'AFILIADOS QG BALTIGO','price':self.price,'currency':'BRL','type':'unique','status':'active','product':self.product}
    def create_pix(self,body,request_id):
        self.calls.append((copy.deepcopy(body),request_id))
        if self.fail:raise ProviderUnavailable()
        identity=str(uuid.uuid4());result={'id':identity,'paymentMethod':'pix','status':'waiting_payment','amount':'7.49','product':{'id':self.product},'offer':{'id':'3bd8an5'},'pix':{'qrCode':'000201'+'synthetic-only-'*8,'expirationDate':datetime.fromtimestamp(self.clock()+900,timezone.utc).isoformat()}}
        self.orders[identity]={**result,'customer':body['customer'],'sck':request_id}
        return result
    def order(self,identity):
        result=copy.deepcopy(self.orders[identity]);result['status']=self.status
        if self.status=='paid':result['paidAt']=datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()
        if hasattr(self,'wrong'):result.update(self.wrong)
        return result

class SalesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=time.time();self.clock=lambda:self.now
        self.provider=Provider(self.clock);self.store=Store(Path(self.temp.name)/'private/db.sqlite',secrets.token_bytes(32))
        self.app=create_app(self.store,'https://example.test',False,clock=self.clock,sales_provider=self.provider)
        self.client=TestClient(self.app,base_url='https://example.test');self.client.__enter__()
        self.headers={'Origin':'https://example.test','Idempotency-Key':str(uuid.uuid4())}
        self.payload={'buyer':copy.deepcopy(BUYER),'terms_accepted':True}
    def tearDown(self):self.client.__exit__(None,None,None);self.temp.cleanup()
    def start(self):self.assertEqual(self.client.post('/api/sales/session',json={},headers=self.headers).status_code,200)
    def buy(self):return self.client.post('/api/sales/pix',json=self.payload,headers=self.headers)
    def check(self):return self.client.post('/api/sales/check',json={},headers=self.headers)
    def test_independent_purchase_and_no_frontend_amount(self):
        self.assertTrue(self.client.get('/api/sales/config').json()['available']);self.start()
        self.assertFalse(self.app.state.security.enabled)
        result=self.buy();self.assertEqual(result.status_code,200);self.assertEqual(result.json()['amount_cents'],749)
        self.assertTrue(result.json()['pix']['qr_image'].startswith('data:image/png;base64,'))
        body=self.provider.calls[0][0];self.assertEqual(body['items'][0]['offerId'],'3bd8an5');self.assertNotIn('amount',body)
        self.assertEqual(body['metadata']['sck'],self.headers['Idempotency-Key']);self.assertNotIn('birthDate',body['customer'])
        self.assertNotIn('cpf',result.text);self.assertNotIn(BUYER['email'],result.text)
        self.assertEqual(self.buy().json()['purchase_id'],result.json()['purchase_id']);self.assertEqual(len(self.provider.calls),1)
    def test_validation_origin_and_fixed_offer(self):
        self.assertEqual(self.client.post('/api/sales/session',json={},headers={'Origin':'https://other.test'}).status_code,403)
        self.start();self.payload['amount_cents']=699;self.assertEqual(self.buy().status_code,422);del self.payload['amount_cents']
        self.payload['terms_accepted']=False;self.assertEqual(self.buy().status_code,422);self.payload['terms_accepted']=True
        self.provider.price='6.99';self.assertEqual(self.buy().status_code,409);self.assertEqual(len(self.provider.calls),0)
    def test_timeout_and_changed_key_cannot_duplicate_charge(self):
        self.start();self.provider.fail=True;self.assertEqual(self.buy().status_code,503)
        self.assertEqual(self.check().json()['status'],'uncertain');self.assertEqual(self.buy().json()['status'],'uncertain')
        self.headers['Idempotency-Key']=str(uuid.uuid4());self.assertEqual(self.buy().status_code,409);self.assertEqual(len(self.provider.calls),1)
    def test_only_authoritative_full_matching_payment(self):
        self.start();self.buy();self.provider.status='paid';self.now+=20
        for wrong in [{'amount':'6.99'},{'product':{'id':str(uuid.uuid4())}},{'offer':{'id':'other'}},{'customer':{'docType':'cpf','docNumber':'00000000000','email':BUYER['email']}},{'sck':'another-request'},{'paidAt':None}]:
            self.provider.wrong=wrong;self.assertEqual(self.check().status_code,503)
        del self.provider.wrong;self.assertEqual(self.check().json()['status'],'paid')
        self.provider.status='refunded';self.now+=20;self.assertEqual(self.check().json()['status'],'refunded')
    def test_expiration_does_not_mean_payment_or_second_pix(self):
        self.start();self.buy();self.now+=901;r=self.check().json();self.assertTrue(r['pix_expired']);self.assertNotIn('pix',r)
        self.assertEqual(self.buy().json()['status'],'waiting_payment');self.assertEqual(len(self.provider.calls),1)
        self.provider.status='paid';self.now+=20;self.assertEqual(self.check().json()['status'],'paid')
    def test_session_isolation_and_encrypted_personal_data(self):
        self.start();self.buy()
        with TestClient(self.app,base_url='https://example.test') as other:
            self.assertEqual(other.post('/api/sales/check',json={},headers=self.headers).status_code,401)
        raw=self.store.path.read_bytes();self.assertNotIn(BUYER['cpf'].encode(),raw);self.assertNotIn(BUYER['email'].encode(),raw)
        cookie=self.client.cookies.get('__Host-baltigo-sale');self.assertTrue(cookie);self.assertNotIn(cookie.encode(),raw)
    def test_signed_webhook_requeries_provider_and_deduplicates(self):
        self.start();self.buy();identity=next(iter(self.provider.orders));raw=json.dumps({'event':'purchase_approved','data':{'id':identity,'status':'paid'}}).encode();stamp=str(int(self.now))
        signature='v1='+hmac.new(self.provider.webhook_secret.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        headers={'Content-Type':'application/json','X-Cakto-Timestamp':stamp,'X-Cakto-Signature':signature}
        self.assertEqual(self.client.post('/api/sales/cakto/webhook',content=raw,headers={**headers,'X-Cakto-Signature':'v1=invalid'}).status_code,401)
        r=self.client.post('/api/sales/cakto/webhook',content=raw,headers=headers);self.assertEqual(r.status_code,200)
        self.assertEqual(self.check().json()['status'],'waiting_payment')
        self.assertEqual(self.client.post('/api/sales/cakto/webhook',content=raw,headers=headers).json()['status'],'duplicate')
        self.provider.status='paid';self.now+=20;self.assertEqual(self.check().json()['status'],'paid')
    def test_backup_restore_and_retention_cover_independent_purchases(self):
        self.start();self.buy();private=self.app.state.security.private
        directory=backup(private,Path(self.temp.name)/'backups')
        with self.assertRaises(ValueError):restore(private,directory)
        self.now+=31*86400;purge(private,30)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM product_sales').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM sales_sessions').fetchone()[0],0)
    def test_preflight_rejects_credentials_without_payment_permissions(self):
        from backend.sales_cli import main
        self.provider.check_connection=lambda:{'authenticated':True,'can_charge':False}
        with patch('backend.sales_cli.SalesCakto',return_value=self.provider),patch('sys.argv',['sales_cli','check']),contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error:main()
            self.assertEqual(error.exception.code,1)
            self.assertFalse(json.loads(output.getvalue())['payment_scopes_ready'])
    def test_sales_flag_does_not_require_loan_collection_approval(self):
        with patch.dict('os.environ',{'CAKTO_CLIENT_ID':'test','CAKTO_CLIENT_SECRET':'test','CAKTO_WEBHOOK_SECRET':'test-secret-minimum-16','CAKTO_SALES_ENABLED':'true','CAKTO_LOAN_COLLECTION_APPROVED':'false','CAKTO_PAYMENTS_ENABLED':'false'}):
            self.assertTrue(SalesCakto().can_charge);self.assertFalse(Cakto().can_charge)

if __name__=='__main__':unittest.main()
