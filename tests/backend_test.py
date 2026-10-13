"""Casos sintéticos: validação independente, persistência e idempotência."""
import base64
import copy
import hashlib
import json
import secrets
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import Store, create_app
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

def payload():
    return {'schema_version':1, 'applicant':{'full_name':'Pessoa de Teste','birth_date':'1990-01-01','email':'pessoa@example.test','cpf':'11144477735','phone':'11912345678'}, 'password':' Senha123 ', 'address':{'postal_code':'01001000','street':'Rua de Teste','number':'123','complement':'','district':'Centro','city':'São Paulo','state':'SP'}, 'income':{'monthly_income':1200, 'occupation':'employed'}, 'bank_account':{'bank':'341','account_type':'checking','branch':'1234','account':'123456-7'}, 'loan':{'amount_requested':2000,'installments_requested':12,'installment_frequency':'weekly'}, 'consents':{'marketing':False,'financial_history':True,'terms':True,'privacy':True,'version':'2026-10-13'}}

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.key = secrets.token_bytes(32)
        self.store = Store(Path(self.temp.name)/'private/applications.sqlite', self.key)
        self.client = TestClient(create_app(self.store, 'https://example.test', True))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def post(self, data=None, key=None, **extra):
        return self.client.post('/api/applications',json=data or payload(),headers={'Origin':'https://example.test','Idempotency-Key':key or str(uuid.uuid4()),**extra})

    def test_persistence_encryption_hash_and_retry(self):
        request_key = str(uuid.uuid4())
        result = self.post(key=request_key)
        self.assertEqual(result.status_code, 201)
        receipt = result.json()
        self.assertEqual(set(receipt), {'application_id','status'})
        self.assertEqual(receipt['status'],'received')
        self.assertEqual(self.post(key=request_key).json(),receipt)
        with self.store.connect() as db:
            rows = db.execute('SELECT application_id, nonce, ciphertext, password_hash FROM applications').fetchall()
        self.assertEqual(len(rows),1)
        app_id, nonce, ciphertext, hashed = rows[0]
        stored = json.loads(AESGCM(self.key).decrypt(nonce,ciphertext,app_id.encode()))
        self.assertEqual(stored['applicant']['cpf'],payload()['applicant']['cpf'])
        self.assertFalse(stored['consents']['marketing'])
        self.assertNotIn('password',stored)
        parts = hashed.split('$')
        actual = hashlib.scrypt(payload()['password'].encode(),salt=base64.b64decode(parts[4]),n=int(parts[1]),r=int(parts[2]),p=int(parts[3]),maxmem=64*1024*1024,dklen=32)
        self.assertEqual(actual,base64.b64decode(parts[5]))
        raw = self.store.path.read_bytes()
        for secret in ['Pessoa de Teste','pessoa@example.test','11144477735','Senha123']:
            self.assertNotIn(secret.encode(),raw)
        changed = payload();changed['loan']['amount_requested']=2250
        self.assertEqual(self.post(changed,key=request_key).status_code,409)
        self.assertEqual(self.post().status_code,409)

    def test_invalid_input_never_written_or_echoed(self):
        cases=[('applicant','cpf','11111111111'),('applicant','birth_date','2020-01-01'),('applicant','email','bad'),('income','monthly_income',1199),('loan','installment_frequency','monthly'),('consents','terms',False),('bank_account','bank','999'),('address','state','XX')]
        for group,name,value in cases:
            with self.subTest(name=name):
                data=payload();data[group][name]=value;r=self.post(data)
                self.assertEqual(r.status_code,422)
                self.assertNotIn('Senha123',r.text)
                self.assertNotIn('11144477735',r.text)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM applications').fetchone()[0],0)

    def test_origin_size_and_rate_limit(self):
        self.assertEqual(self.post(Origin='https://other.test').status_code,403)
        r=self.client.post('/api/applications',content='x'*20000,headers={'Origin':'https://example.test','Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)
        for _ in range(10):
            data=payload();data['applicant']['cpf']='11111111111';self.assertEqual(self.post(data).status_code,422)
        self.assertEqual(self.post().status_code,429)

    def test_disabled_receiver(self):
        with TestClient(create_app(self.store,'https://example.test',False)) as client:
            self.assertEqual(client.post('/api/applications',json=payload()).status_code,503)
        self.assertEqual(self.client.get('/api/health').json(),{'status':'ok','receiving':True})
        for path in ['/backend/app.py','/var/applications.sqlite','/src/business-config.json']:
            self.assertEqual(self.client.get(path).status_code,404)

if __name__ == '__main__':
    unittest.main()
