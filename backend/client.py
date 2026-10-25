"""Private customer portal. Registration passwords authenticate only their own record."""
import hashlib
import base64
import json
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints
from typing import Annotated
from uuid import UUID
from datetime import datetime,timezone

from backend.private_store import password_hash, password_matches

COOKIE = '__Host-ef-client'


def private_page(filename):
    page = (Path(__file__).parent / filename).read_text(encoding='utf-8')
    script = page.split('<script>')[1].split('</script>')[0]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    media_policy="img-src 'self' blob:; media-src 'self' blob:; frame-src blob:; " if filename=='admin-panel.html' else "img-src 'self'; "
    return HTMLResponse(page,headers={'Cache-Control':'private, no-store',
        'Content-Security-Policy':"default-src 'none'; script-src 'sha256-"+digest+"'; style-src 'unsafe-inline'; "
            "connect-src 'self'; "+media_policy+"font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})


class ClientLogin(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    email: StrictStr = Field(min_length=3, max_length=254)
    application_id: UUID
    password: Annotated[StrictStr,StringConstraints(strip_whitespace=False)] = Field(min_length=7,max_length=128,repr=False)


def review(private, kind, identity):
    with private.store.connect() as db:
        row = db.execute('SELECT state,updated,payload FROM application_reviews WHERE kind=? AND identity=?',
                         (kind,identity)).fetchone()
    if not row:
        return {'state':'received','updated_at':None,'message':''}
    data = private.unseal('review:'+kind+':'+identity,row[2])
    return {'state':row[0],'updated_at':row[1],'message':data.get('message','')}


def install_client(app, security):
    dummy = password_hash(secrets.token_urlsafe(32))

    def session(request):
        if not security.origin_valid:
            raise HTTPException(503,'O acesso à área do cliente ainda não está disponível.')
        token = request.cookies.get(COOKIE,'')
        if not 20 <= len(token) <= 100:
            raise HTTPException(401,'Entre para acompanhar sua solicitação.')
        key = security.private.digest('client-session',token)
        cutoff=datetime.fromtimestamp(security.clock()-security.retention*86400,timezone.utc).isoformat()
        with security.private.store.connect() as db:
            row = db.execute('SELECT s.application_id,s.expires FROM client_sessions s '
                             'JOIN applications a ON a.application_id=s.application_id WHERE s.id=? AND a.created_at>?',(key,cutoff)).fetchone()
        if not row or row[1] <= security.clock():
            raise HTTPException(401,'Sua sessão expirou. Entre novamente.')
        return key,row[0]

    @app.get('/cliente')
    @app.get('/cliente.html')
    def page():
        return private_page('client-portal.html')

    @app.post('/api/client/login')
    def login(body:ClientLogin,request:Request,response:Response):
        if not security.origin_valid:
            raise HTTPException(503,'O acesso à área do cliente ainda não está disponível.')
        private = security.private
        email = body.email.lower()
        ip = request.client.host if request.client else 'unknown'
        private.limit('client-login-ip',ip,10,900)
        private.limit('client-login-email',email,5,900)
        with private.store.connect() as db:
            row = db.execute('SELECT application_id,password_hash FROM applications WHERE application_id=? AND email_digest=?',
                             (str(body.application_id),private.store.digest('email',email))).fetchone()
        valid = password_matches(body.password,row[1] if row else dummy)
        if not row or not valid:
            private.audit(private.digest('client-login',email),'client_login_failed')
            raise HTTPException(401,'E-mail ou senha inválidos.')
        token = secrets.token_urlsafe(32)
        with private.store.connect() as db:
            prior = request.cookies.get(COOKIE)
            if prior:
                db.execute('DELETE FROM client_sessions WHERE id=?',(private.digest('client-session',prior),))
            db.execute('INSERT INTO client_sessions VALUES (?,?,?)',
                       (private.digest('client-session',token),row[0],security.clock()+1800))
        response.set_cookie(COOKIE,token,max_age=1800,secure=True,httponly=True,samesite='strict',path='/')
        private.audit(private.digest('client-actor',row[0]),'client_login',row[0])
        return {'status':'authenticated','expires_in':1800}

    @app.get('/api/client/application')
    def application(request:Request):
        _, identity = session(request)
        private = security.private
        with private.store.connect() as db:
            row = db.execute('SELECT nonce,ciphertext FROM applications WHERE application_id=?',(identity,)).fetchone()
        data = json.loads(AESGCM(private.key).decrypt(row[0],row[1],identity.encode()))
        applicant = data['applicant']
        private.audit(private.digest('client-actor',identity),'client_application_viewed',identity)
        return {'application_id':identity,'first_name':applicant['full_name'].split()[0],
                'cpf_masked':'***.***.***-'+applicant['cpf'][-2:],'received_at':data['received_at'],
                'loan':data['loan'],'review':review(private,'initial',identity)}

    @app.post('/api/client/logout')
    def logout(request:Request,response:Response):
        # Idempotent logout also clears an expired browser session.
        token = request.cookies.get(COOKIE,'')
        with security.private.store.connect() as db:
            db.execute('DELETE FROM client_sessions WHERE id=?',(security.private.digest('client-session',token),))
        response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='strict',path='/')
        return {'status':'closed'}
