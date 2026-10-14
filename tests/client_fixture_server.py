"""Loopback-only HTTPS application and encrypted synthetic records for browser QA."""
import argparse
import ipaddress
import json
import os
import secrets
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from backend.app import Application, Store, create_app
from backend_test import payload


def certificate(folder):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost browser QA')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.ip_address('127.0.0.1')), x509.DNSName('localhost')]), False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = folder/'certificate.pem', folder/'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


def fixture_app(folder, port, client_only=False):
    # These changes affect only this child test process. It never uses accounts
    # or credentials from an operator's integration environment.
    for name in list(os.environ):
        if name.startswith(('TWILIO_', 'BELVO_', 'RESEND_', 'CAKTO_')):
            os.environ.pop(name)
    origin = f'https://127.0.0.1:{port}'
    store = Store(folder/'private/applications.sqlite', secrets.token_bytes(32))
    app = create_app(store, origin, True)
    clients = {}
    sizes = [320,390,768,1440] if client_only else [320,360,390,412,640,768,1024,1280,1440,1920,2560]
    for browser in ['chromium','firefox','webkit']:
        for width in sizes:
            project = f'{browser}-{width}'
            body = payload()
            body['applicant']['email'] = f'{project}@example.test'
            body['password'] = ' Synthetic client password 123 '
            other = json.loads(json.dumps(body))
            other['applicant']['full_name'] = 'Outra Pessoa de Teste'
            other['password'] = 'Different synthetic password 123'
            other['loan']['amount_requested'] = 1000
            other['loan']['installments_requested'] = 6
            identity = store.save(Application.model_validate(other), str(uuid.uuid4()))
            clients[project] = {'payload':body, 'other_application_id':identity,
                                'other_password':other['password']}
    metadata = {'origin':origin,'clients':clients,'admin':{}}
    admin_seed = ROOT/'tests/admin_browser_seed.py'
    if admin_seed.exists() and not client_only:
        from admin_browser_seed import seed
        metadata['admin'] = seed(store, app)

    @app.middleware('http')
    async def separate_test_networks(request:Request, call_next):
        project = request.headers.get('x-test-project')
        if project in clients:
            index = list(clients).index(project)+1
            request.scope['client'] = (f'192.0.2.{index}', 1)
        return await call_next(request)

    def test_profile(request, project):
        if project not in clients or request.headers.get('x-test-project') != project:
            raise HTTPException(403, 'Unknown synthetic browser project')
        return clients[project]

    @app.post('/__test/client/{project}/review')
    async def write_review(project:str, request:Request):
        profile = test_profile(request, project)
        data = await request.json()
        identity = str(uuid.UUID(data['application_id']))
        state = data.get('state','under_review')
        if state not in {'received','under_review','information_requested','closed'}:
            raise HTTPException(422, 'Invalid synthetic review')
        with store.connect() as db:
            row = db.execute('SELECT email_digest FROM applications WHERE application_id=?',(identity,)).fetchone()
            if not row or row[0] != store.digest('email',profile['payload']['applicant']['email']):
                raise HTTPException(404,'Unknown synthetic application')
            private = app.state.security.private
            sealed = private.seal('review:initial:'+identity, {
                'message':data.get('message','Documento adicional solicitado.'),
                'internal_note':'Nota privada de teste: nunca exibir ao cliente.'})
            db.execute('INSERT OR REPLACE INTO application_reviews VALUES (?,?,?,?,?)',
                       ('initial',identity,state,time.time(),sealed))
        return {'persisted':True}

    @app.get('/__test/client/{project}/snapshot')
    def inspect_database(project:str, request:Request, application_id:uuid.UUID):
        profile = test_profile(request, project)
        identity = str(application_id)
        with store.connect() as db:
            row = db.execute('SELECT email_digest,nonce,ciphertext FROM applications WHERE application_id=?',(identity,)).fetchone()
            if not row or row[0] != store.digest('email',profile['payload']['applicant']['email']):
                raise HTTPException(404,'Unknown synthetic application')
            count = db.execute('SELECT count(*) FROM applications WHERE email_digest=?',(row[0],)).fetchone()[0]
            sessions = db.execute('SELECT count(*) FROM client_sessions WHERE application_id=?',(identity,)).fetchone()[0]
        return {'application_exists':True,'encrypted':len(row[1])==12 and len(row[2])>16,
                'applications_for_email':count,'active_sessions':sessions,
                'password_absent_from_database':profile['payload']['password'].encode() not in store.path.read_bytes()}

    return app, metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=4186)
    parser.add_argument('--ready-file',default='test-results/browser-fixture.json')
    parser.add_argument('--client-only',action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='emprestimofacil-browser-qa-') as temporary:
        folder = Path(temporary)
        cert, key = certificate(folder)
        app, metadata = fixture_app(folder, args.port, args.client_only)
        target = ROOT/args.ready_file
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
        uvicorn.run(app,host='127.0.0.1',port=args.port,ssl_certfile=str(cert),
                    ssl_keyfile=str(key),access_log=False,log_level='error',backlog=128)
