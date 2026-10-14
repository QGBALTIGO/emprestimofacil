"""Independent adversarial round: temporary data and mocked providers only."""
import copy
import json
import secrets
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
import pyotp
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient

from backend.app import Application, Store, create_app
from backend.billing_models import Contract
from backend.maintenance import administrator_invite, create_administrator
from backend.providers import Providers
from backend.secure_models import PF
from backend_test import payload
from billing_test import FakeCakto, contract
from sales_test import Provider as FakeSales
from security_test import FakeProviders, jpeg, profile, pf_payload


class AdversarialRoundTwo(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.clock = lambda: self.now
        self.store = Store(Path(self.temp.name) / 'private/applications.sqlite', secrets.token_bytes(32))
        self.providers = FakeProviders()
        self.cakto = FakeCakto(self.clock)
        self.sales = FakeSales(self.clock)
        self.app = create_app(self.store, 'https://example.test', True, self.providers,
                              self.clock, cakto=self.cakto, sales_provider=self.sales)
        self.client = TestClient(self.app, base_url='https://example.test', raise_server_exceptions=False)
        self.client.__enter__()
        self.private = self.app.state.security.private
        self.headers = {'Origin': 'https://example.test'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def post(self, path, body=None, client=None, **headers):
        return (client or self.client).post(path, json={} if body is None else body,
                                            headers={**self.headers, **headers})

    def initial(self):
        return self.store.save(Application.model_validate(payload()), str(uuid.uuid4()))

    def client_login(self, identity):
        response = self.post('/api/client/login', {'application_id': identity,
                             'email': payload()['applicant']['email'], 'password': payload()['password']})
        self.assertEqual(response.status_code, 200)
        return self.client.cookies.get('__Host-ef-client')

    def admin(self):
        self.seed = pyotp.random_base32()
        self.password = 'Synthetic administrative password 123'
        create_administrator(self.private, 'admin@example.test', self.password, self.seed)
        response = self.post('/api/admin/login', {'username': 'admin@example.test',
                             'password': self.password, 'code': pyotp.TOTP(self.seed).at(self.now)})
        self.assertEqual(response.status_code, 200)
        return self.client.cookies.get('__Host-ef-admin')

    def invite(self):
        path = administrator_invite(self.private, 'admin@example.test', 'https://example.test',
                                    Path(self.temp.name) / 'operator/invite.txt')
        token = path.read_text().strip().split('#setup=')[1]
        response = self.post('/api/admin/setup/start', {'token': token})
        self.assertEqual(response.status_code, 200)
        return token, response.json()['secret']

    def test_admin_setup_rejects_non_ascii_totp_without_server_error(self):
        token, _ = self.invite()
        response = self.post('/api/admin/setup/complete', {'token': token,
                             'password': 'Synthetic administrative password 123',
                             'code': '\u0661\u0662\u0663\u0664\u0665\u0666'})
        self.assertIn(response.status_code, {401, 422})

    def test_admin_setup_rejects_visually_empty_password(self):
        token, seed = self.invite()
        response = self.post('/api/admin/setup/complete', {'token': token,
                             'password': ' ' * 12, 'code': pyotp.TOTP(seed).at(self.now)})
        self.assertEqual(response.status_code, 422)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM administrators').fetchone()[0], 0)

    def test_admin_login_rejects_non_ascii_totp_without_server_error(self):
        self.seed = pyotp.random_base32()
        self.password = 'Synthetic administrative password 123'
        create_administrator(self.private, 'admin@example.test', self.password, self.seed)
        response = self.post('/api/admin/login', {'username': 'admin@example.test',
                             'password': self.password, 'code': '\uff11\uff12\uff13\uff14\uff15\uff16'})
        self.assertIn(response.status_code, {401, 422})

    def test_bank_webhook_rejects_non_ascii_authorization(self):
        response = self.client.post('/api/security/bank/webhook', json={},
                    headers=[(b'content-type', b'application/json'), (b'authorization', b'Bearer \xff')])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.providers.resource_reads, 0)

    def test_installment_webhook_rejects_non_ascii_signature(self):
        response = self.client.post('/api/installments/cakto/webhook', json={}, headers=[
                    (b'content-type', b'application/json'),
                    (b'x-cakto-timestamp', str(int(self.now)).encode()),
                    (b'x-cakto-signature', b'v1=\xff')])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.cakto.calls, [])

    def test_sales_webhook_rejects_non_ascii_signature(self):
        response = self.client.post('/api/sales/cakto/webhook', json={}, headers=[
                    (b'content-type', b'application/json'),
                    (b'x-cakto-timestamp', str(int(self.now)).encode()),
                    (b'x-cakto-signature', b'v1=\xff')])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.sales.calls, [])

    def test_concurrent_invite_completion_consumes_once(self):
        token, seed = self.invite()
        body = {'token': token, 'password': 'Synthetic administrative password 123',
                'code': pyotp.TOTP(seed).at(self.now)}
        from backend.admin import password_hash as original_hash
        barrier = threading.Barrier(2)

        def synchronized_hash(value):
            barrier.wait(timeout=15)
            return original_hash(value)

        def complete(_):
            client = TestClient(self.app, base_url='https://example.test', raise_server_exceptions=False)
            try:
                return self.post('/api/admin/setup/complete', body, client=client).status_code
            finally:
                client.close()

        with patch('backend.admin.password_hash', side_effect=synchronized_hash), ThreadPoolExecutor(2) as pool:
            statuses = sorted(pool.map(complete, range(2)))
        self.assertEqual(statuses[0], 200)
        self.assertIn(statuses[1], {401, 409})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM administrators').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM admin_bootstrap').fetchone()[0], 0)

    def test_concurrent_same_totp_cannot_create_two_admin_sessions(self):
        seed = pyotp.random_base32()
        password = 'Synthetic administrative password 123'
        create_administrator(self.private, 'admin@example.test', password, seed)
        body = {'username': 'admin@example.test', 'password': password, 'code': pyotp.TOTP(seed).at(self.now)}
        from backend.admin import password_matches as original_matches
        barrier = threading.Barrier(2)

        def synchronized_matches(value, encoded):
            barrier.wait(timeout=15)
            return original_matches(value, encoded)

        def login(_):
            client = TestClient(self.app, base_url='https://example.test', raise_server_exceptions=False)
            try:
                return self.post('/api/admin/login', body, client=client).status_code
            finally:
                client.close()

        with patch('backend.admin.password_matches', side_effect=synchronized_matches), ThreadPoolExecutor(2) as pool:
            statuses = sorted(pool.map(login, range(2)))
        self.assertEqual(statuses, [200, 401])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM admin_sessions').fetchone()[0], 1)

    def test_role_cookies_cannot_be_relabelled_to_admin_or_another_portal(self):
        identity = self.initial()
        client_token = self.client_login(identity)
        self.assertEqual(self.post('/api/security/session', profile()).status_code, 200)
        security_token = self.client.cookies.get('__Host-ef-session')
        for token in [client_token, security_token]:
            response = self.client.get('/api/admin/overview',
                        headers={'Cookie': '__Host-ef-admin=' + token})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get('/api/installments/current',
                         headers={'Cookie': '__Host-ef-installment=' + client_token}).status_code, 401)
        self.assertEqual(self.post('/api/sales/check').status_code, 401)

    def test_client_cannot_query_admin_files_or_reviews(self):
        identity = self.initial()
        self.client_login(identity)
        for path in ['/api/admin/records', '/api/admin/records/initial/' + identity,
                     '/api/admin/files/' + str(uuid.uuid4()) + '/front']:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.post('/api/admin/records/initial/' + identity + '/review',
                         {'state': 'closed', 'message': 'Forged review'}).status_code, 401)

    def test_client_cannot_select_another_protocol_by_query_or_body(self):
        first = self.initial()
        second = self.initial()
        self.client_login(first)
        response = self.client.get('/api/client/application?application_id=' + second)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['application_id'], first)
        invalid = self.post('/api/client/login', {'application_id': second,
                            'email': 'unrelated@example.test', 'password': payload()['password']})
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(self.client.get('/api/client/application').json()['application_id'], first)

    def test_admin_logout_revokes_copied_cookie(self):
        token = self.admin()
        self.assertEqual(self.post('/api/admin/logout').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/overview',
                         headers={'Cookie': '__Host-ef-admin=' + token}).status_code, 401)

    def test_unverified_or_foreign_bank_callback_never_attaches_to_another_session(self):
        self.assertEqual(self.post('/api/security/session', profile()).status_code, 200)
        self.assertEqual(self.post('/api/security/bank/start', {'consent': True}).status_code, 403)
        token, sid, state = self.private.new_session(profile())
        state['verified'] = {'email': self.now, 'sms': self.now}
        self.private.save_session(sid, state, self.now + 7200)
        self.client.cookies.set('__Host-ef-session', token, domain='example.test', path='/')
        self.assertEqual(self.post('/api/security/bank/start', {'consent': True}).status_code, 200)
        callback = self.providers.last_callbacks['success']
        link = str(uuid.uuid4())
        self.providers.links[link] = {'id': link, 'external_id': str(uuid.uuid4())}
        self.assertEqual(self.client.get(callback + '&link=' + link).status_code, 403)
        self.assertEqual(self.client.get('/api/security/session').json()['bank_status'], 'pending')
        self.assertEqual(self.providers.resource_reads, 0)

    def test_cakto_forged_payment_event_cannot_touch_an_existing_installment(self):
        body = contract()
        self.cakto.product = body['installments'][0]['product_id']
        self.cakto.currency = 'BRL'
        self.app.state.billing.store.import_contract(Contract.model_validate(body))
        raw = json.dumps({'event': 'purchase_approved', 'data': {'id': str(uuid.uuid4()),
                          'status': 'paid', 'sck': str(uuid.uuid4())}}).encode()
        response = self.client.post('/api/installments/cakto/webhook', content=raw,
                    headers={'Content-Type': 'application/json', 'X-Cakto-Timestamp': str(int(self.now)),
                             'X-Cakto-Signature': 'v1=' + '0' * 64})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.cakto.calls, [])
        with self.store.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT status FROM billing_installments')}, {'due'})

    def test_reissuing_installment_link_revokes_old_cookie_and_old_token(self):
        body = contract()
        self.app.state.billing.store.import_contract(Contract.model_validate(body))
        billing = self.app.state.billing.store
        with self.store.connect() as db:
            identity = db.execute('SELECT id FROM billing_installments ORDER BY number').fetchone()[0]
        first = billing.link(identity)
        self.assertEqual(self.post('/api/installments/session', {'token': first}).status_code, 200)
        cookie = self.client.cookies.get('__Host-ef-installment')
        second = billing.link(identity)
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get('/api/installments/current',
                         headers={'Cookie': '__Host-ef-installment=' + cookie}).status_code, 401)
        self.assertEqual(self.post('/api/installments/session', {'token': first}).status_code, 401)
        self.assertEqual(self.post('/api/installments/session', {'token': second}).status_code, 200)

    def test_file_ciphertext_cannot_be_reassigned_to_a_different_record(self):
        _, sid, _ = self.private.new_session(profile())
        first, _ = self.private.create_pf(sid, PF.model_validate(pf_payload()), str(uuid.uuid4()),
                    {'contact_verified_at': {'email': self.now, 'sms': self.now}}, 30)
        second = str(uuid.uuid4())
        self.private.save_file(first, 'front', jpeg(), 'jpg')
        self.assertEqual(self.private.file(first, 'front')[0], jpeg())
        with self.store.connect() as db:
            db.execute('UPDATE private_files SET record_id=? WHERE record_id=?', (second, first))
        with self.assertRaises(InvalidTag):
            self.private.file(second, 'front')


class ResendAdversarialRoundTwo(unittest.TestCase):
    def test_exhausted_local_quota_never_sends_or_creates_a_challenge(self):
        private=self.app.state.security.private
        for _ in range(100):private.email_budget(True)
        response=self.client.post('/api/security/codes/send',json={'channel':'email'},headers=self.headers)
        self.assertEqual(response.status_code,429);self.assertIn('Retry-After',response.headers)
        self.assertEqual(self.messages,[])
        self.assertFalse(self.client.get('/api/security/session').json()['email_verified'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.messages = []

        def respond(request):
            self.messages.append(json.loads(request.content))
            return httpx.Response(200, json={'id': str(uuid.uuid4())})

        provider = Providers(httpx.MockTransport(respond))
        provider.email_provider = 'resend'
        provider.resend_key = 'synthetic-resend-round-two'
        provider.email_from = 'contato@example.test'
        self.store = Store(Path(self.temp.name) / 'private/db.sqlite', secrets.token_bytes(32))
        self.app = create_app(self.store, 'https://example.test', True, provider, lambda: self.now)
        self.client = TestClient(self.app, base_url='https://example.test', raise_server_exceptions=False)
        self.client.__enter__()
        self.headers = {'Origin': 'https://example.test'}
        self.assertEqual(self.client.post('/api/security/session', json=profile(), headers=self.headers).status_code, 200)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def send(self):
        response = self.client.post('/api/security/codes/send', json={'channel': 'email'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return self.messages[-1]['text'].split('é ')[1][:6]

    def check(self, code, client=None):
        return (client or self.client).post('/api/security/codes/check',
               json={'channel': 'email', 'code': code}, headers=self.headers)

    def test_five_wrong_resend_codes_prevent_correct_sixth_attempt(self):
        code = self.send()
        bad = '000000' if code != '000000' else '111111'
        for _ in range(5):
            self.assertEqual(self.check(bad).status_code, 422)
        self.assertEqual(self.check(code).status_code, 403)
        self.assertFalse(self.client.get('/api/security/session').json()['email_verified'])

    def test_new_resend_challenge_invalidates_previous_code(self):
        with patch('backend.security.secrets.randbelow', return_value=123456):
            first = self.send()
        self.now += 61
        with patch('backend.security.secrets.randbelow', return_value=654321):
            second = self.send()
        self.assertNotEqual(first, second)
        self.assertEqual(self.check(first).status_code, 422)
        self.assertEqual(self.check(second).status_code, 200)

    def test_resend_code_expires_at_boundary(self):
        code = self.send()
        self.now += 600
        self.assertEqual(self.check(code).status_code, 403)
        self.assertFalse(self.client.get('/api/security/session').json()['email_verified'])

    def test_resend_code_cannot_cross_a_new_session(self):
        code = self.send()
        other = TestClient(self.app, base_url='https://example.test', raise_server_exceptions=False)
        try:
            changed = copy.deepcopy(profile())
            changed['email'] = 'second@example.test'
            self.assertEqual(other.post('/api/security/session', json=changed, headers=self.headers).status_code, 200)
            self.assertEqual(self.check(code, other).status_code, 403)
            self.assertFalse(other.get('/api/security/session').json()['email_verified'])
            self.assertEqual(self.check(code).status_code, 200)
        finally:
            other.close()

    def test_email_only_changed_contact_requires_new_session_and_otp(self):
        self.app.state.security.phone_required = False
        first_code = self.send()
        self.assertEqual(self.check(first_code).status_code, 200)
        old_token = self.client.cookies.get('__Host-ef-session')
        changed = copy.deepcopy(profile())
        changed['email'] = 'changed-contact@example.test'
        response = self.client.post('/api/security/session', json=changed, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.client.cookies.get('__Host-ef-session'), old_token)
        self.assertFalse(response.json()['email_verified'])
        self.assertFalse(self.client.get('/api/security/session').json()['phone_verified'])
        self.assertEqual(self.check(first_code).status_code, 403)
        with self.assertRaises(Exception) as stale:
            self.app.state.security.private.session(old_token)
        self.assertEqual(stale.exception.status_code, 401)
        body = pf_payload()
        body['applicant']['email'] = changed['email']
        denied = self.client.post('/api/pf/applications', json=body,
                 headers={**self.headers, 'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.check(self.send()).status_code, 200)
        allowed = self.client.post('/api/pf/applications', json=body,
                  headers={**self.headers, 'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(allowed.status_code, 201)

    def test_email_only_session_cannot_bypass_a_new_sms_required_policy(self):
        self.app.state.security.phone_required = False
        self.app.state.security.bank_required = False
        self.assertEqual(self.check(self.send()).status_code, 200)
        self.assertTrue(self.client.get('/api/security/capabilities').json()['pf_submission'])
        self.app.state.security.phone_required = True
        capabilities = self.client.get('/api/security/capabilities').json()
        self.assertFalse(capabilities['verification'])
        self.assertFalse(capabilities['pf_submission'])
        self.assertTrue(capabilities['phone_required'])
        response = self.client.post('/api/pf/applications', json=pf_payload(),
                   headers={**self.headers, 'Idempotency-Key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pf_records').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
