"""Confirmação de contatos, Open Finance e recebimento documental na mesma origem."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
import asyncio
import threading
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from backend.file_validation import MAX_FILE, validate_file
from backend.private_store import PrivateStore
from backend.providers import ProviderUnavailable, Providers, EmailQuotaExceeded
from backend.secure_models import BankConsent, CodeCheck, CodeRequest, PF, Profile

COOKIE = '__Host-ef-session'


class Security:
    def __init__(self, origin, enabled, providers=None, clock=time.time, retention_days=None, bank_required=None,phone_required=None):
        self.origin, self.enabled = origin, enabled
        parsed = urlparse(origin)
        self.origin_valid = bool(parsed.scheme == 'https' and parsed.netloc and not parsed.username
                            and not parsed.password and parsed.path in {'','/'} and not parsed.query and not parsed.fragment)
        self.enabled = bool(enabled and self.origin_valid)
        self.providers = providers or Providers()
        self.clock = clock
        self.retention = int(retention_days if retention_days is not None else os.getenv('DATA_RETENTION_DAYS','30'))
        if not 1 <= self.retention <= 365:
            raise RuntimeError('DATA_RETENTION_DAYS must be between 1 and 365.')
        self.bank_required = bank_required if bank_required is not None else os.getenv('OPEN_FINANCE_REQUIRED','false') == 'true'
        self.phone_required = phone_required if phone_required is not None else os.getenv('PHONE_VERIFICATION_REQUIRED','true') == 'true'
        self.private = None
        self._upload_slots=threading.BoundedSemaphore(2)

    def open(self, store):
        self.private = PrivateStore(store,self.clock)

    @property
    def can_receive(self):
        return self.enabled and self.verification_ready and (not self.bank_required or self.providers.bank_ready)

    @property
    def verification_ready(self):
        return self.channel_ready('email') and (not self.phone_required or self.channel_ready('sms'))

    def channel_ready(self, channel):
        return bool(getattr(self.providers, 'email_ready' if channel == 'email' else 'sms_ready', self.providers.verify_ready))

    def available(self, bank=False, channel=None):
        ready = self.providers.bank_ready if bank else self.channel_ready(channel) if channel else (
            self.channel_ready('email') or self.channel_ready('sms'))
        if not self.enabled or not ready:
            raise HTTPException(503,'Este serviço ainda não está disponível. Tente novamente mais tarde.')

    def session(self, request):
        self.available()
        return self.private.session(request.cookies.get(COOKIE))

    def snapshot(self, request, applicant, require_bank=None):
        sid, state, _ = self.session(request)
        expected = state['profile']
        for field in ['email','phone','cpf','full_name']:
            supplied = str(applicant.get(field,'')).strip()
            if field in {'email','full_name'}:
                supplied = supplied.lower()
            stored = str(expected.get(field,'')).strip()
            if field in {'email','full_name'}:
                stored = stored.lower()
            if supplied != stored:
                raise HTTPException(409,'Os dados mudaram. Confirme seus contatos e a conexão novamente.')
        required=['email','sms'] if self.phone_required else ['email']
        if not all(channel in state['verified'] for channel in required):
            raise HTTPException(403,'Confirme o e-mail e o celular antes de enviar.' if self.phone_required else 'Confirme o e-mail antes de enviar.')
        bank = state.get('bank',{})
        if (self.bank_required if require_bank is None else require_bank) and bank.get('status') != 'data_ready':
            raise HTTPException(403,'Conclua a conexão bancária antes de enviar.')
        return sid, {'contact_verified_at':state['verified'].copy(), 'phone_verification_required':bool(self.phone_required),
                     'bank':bank.copy(), 'received_at':self.clock()}

    def state_response(self, state):
        bank = state.get('bank',{})
        return {'email_verified':'email' in state['verified'],'phone_verified':'sms' in state['verified'],
                'bank_status':bank.get('status','not_connected'),'bank_required':bool(self.bank_required),
                'cpf_match':bank.get('cpf_match') is True,'phone_required':bool(self.phone_required)}

    def apply_bank_data(self, sid):
        with self.private.locked(sid):
            with self.private.store.connect() as db:
                session_row = db.execute('SELECT expires,payload FROM secure_sessions WHERE id=?',(sid,)).fetchone()
                attempt = db.execute('SELECT id,link,resources FROM bank_attempts WHERE session_id=? AND consumed=1 ORDER BY expires DESC LIMIT 1',(sid,)).fetchone()
            if not session_row or session_row[0] <= self.clock() or not attempt or not attempt[1]:
                return
            state = self.private.unseal('session:'+sid,session_row[1])
            if state.get('bank',{}).get('status') in {'data_ready','mismatch'}:
                return
            if not {'OWNERS','ACCOUNTS','TRANSACTIONS'}.issubset(set(json.loads(attempt[2]))):
                return
            data = self.providers.bank_resources(attempt[1])
            if (not isinstance(data,dict) or any(not isinstance(data.get(key),list) for key in ['owners','accounts','transactions'])
                    or any(not isinstance(owner,dict) or not isinstance(owner.get('document_id'),dict)
                           or owner['document_id'].get('document_type')=='CPF' and not isinstance(owner['document_id'].get('document_number'),str)
                           for owner in data['owners'])):
                raise ProviderUnavailable()
            expected = state['profile']['cpf']
            owners = [owner for owner in data['owners'] if owner.get('document_id',{}).get('document_type') == 'CPF'
                      and re.sub(r'\D','',owner.get('document_id',{}).get('document_number','')) == expected]
            if not owners:
                state['bank'] = {'status':'mismatch','link':attempt[1],'cpf_match':False}
                self.private.audit(sid,'bank_identity_mismatch',attempt[0])
            else:
                data['owners'] = owners
                state['bank'] = {'status':'data_ready','link':attempt[1],'cpf_match':True,
                                 'collected_at':self.clock(),'data':data,'sandbox':self.providers.belvo_host == 'sandbox'}
                self.private.audit(sid,'bank_data_received',attempt[0])
            self.private.save_session(sid,state,session_row[0])

    def install(self, app):
        @app.exception_handler(ProviderUnavailable)
        async def provider_error(request, exc):
            if isinstance(exc,EmailQuotaExceeded):
                return JSONResponse({'detail':'O limite de envio de e-mails foi atingido. Tente novamente após a renovação do limite.'},status_code=429,headers={'Retry-After':str(exc.retry_after)})
            return JSONResponse({'detail':'Não foi possível confirmar a operação com o serviço. Tente novamente.'},status_code=503)

        @app.middleware('http')
        async def protect(request: Request, call_next):
            path = request.url.path
            if path.startswith(('/api/security/','/api/pf/','/api/admin/','/api/installments/','/api/client/')):
                callback = path.startswith('/api/security/bank/callback/') or path in {'/api/security/bank/webhook','/api/installments/cakto/webhook'}
                if request.method not in {'GET','HEAD'} and not callback:
                    if not (self.origin_valid if path.startswith(('/api/admin/','/api/client/')) else self.enabled):
                        return JSONResponse({'detail':'Serviço ainda não disponível.'},status_code=503)
                    if request.headers.get('origin') != self.origin:
                        return JSONResponse({'detail':'Origem não permitida.'},status_code=403)
                if request.method not in {'GET','HEAD'}:
                    if path.startswith('/api/pf/') and '/files/' in path and self.private:
                        import shutil
                        if shutil.disk_usage(self.private.files_dir).free < self.private.disk_reserve+MAX_FILE*2:
                            return JSONResponse({'detail':'Armazenamento temporariamente indisponível.'},status_code=507)
                    limit = MAX_FILE if '/files/' in path else 131072 if path=='/api/installments/cakto/webhook' else 24576
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > limit:
                            return JSONResponse({'detail':'Arquivo muito grande.' if '/files/' in path else 'Solicitação muito grande.'},status_code=413)
                    request._body = bytes(body)
                    if '/files/' not in path and request.headers.get('content-type','').split(';')[0].strip().lower() != 'application/json':
                        return JSONResponse({'detail':'Use JSON para esta operação.'},status_code=415)
                if self.private:
                    try:
                        ip = request.client.host if request.client else 'unknown'
                        if not callback and path!='/api/security/capabilities':await run_in_threadpool(self.private.limit,'requests',ip,120,60)
                    except HTTPException as error:
                        return JSONResponse({'detail':error.detail},status_code=error.status_code,headers=error.headers)
            response = await call_next(request)
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['Permissions-Policy'] = 'camera=(self), microphone=(self), geolocation=(self)'
            if path.startswith('/api/') or path.startswith('/admin'):
                response.headers['Cache-Control'] = 'no-store'
            return response

        @app.middleware('http')
        async def bounded_upload(request:Request,call_next):
            path=request.url.path
            if not (request.method=='PUT' and path.startswith('/api/pf/applications/') and '/files/' in path):
                return await call_next(request)
            if request.headers.get('origin')!=self.origin:
                return JSONResponse({'detail':'Origem não permitida.'},status_code=403)
            if __import__('shutil').disk_usage(self.private.files_dir).free<self.private.disk_reserve+MAX_FILE*2:
                return JSONResponse({'detail':'Armazenamento temporariamente indisponível.'},status_code=507)
            try:
                sid,_,_=await run_in_threadpool(self.session,request)
                identity=path.split('/')[4]
                await run_in_threadpool(self.private.pf,identity,sid)
            except HTTPException as error:
                return JSONResponse({'detail':error.detail},status_code=error.status_code)
            deadline=asyncio.get_running_loop().time()+8
            while not self._upload_slots.acquire(blocking=False):
                if asyncio.get_running_loop().time()>=deadline:
                    return JSONResponse({'detail':'O envio de documentos está ocupado. Aguarde e tente novamente.'},status_code=503,headers={'Retry-After':'3'})
                await asyncio.sleep(.05)
            try:return await call_next(request)
            finally:self._upload_slots.release()

        @app.get('/api/security/capabilities')
        async def capabilities():
            return {'verification':bool(self.enabled and self.verification_ready),
                    'client_portal':bool(self.origin_valid),
                    'email_verification':bool(self.enabled and self.channel_ready('email')),
                    'sms_verification':bool(self.enabled and self.channel_ready('sms')),
                    'open_finance':bool(self.enabled and self.providers.bank_ready),
                    'pf_submission':bool(self.can_receive),'bank_required':bool(self.bank_required),'phone_required':bool(self.phone_required),
                    'sandbox':self.providers.belvo_host == 'sandbox','retention_days':self.retention}

        @app.post('/api/security/session')
        def begin(profile: Profile, request: Request, response: Response):
            self.available()
            ip = request.client.host if request.client else 'unknown'
            self.private.limit('sessions',ip,20,3600)
            wanted = profile.model_dump()
            token = request.cookies.get(COOKIE)
            prior = None
            try:
                prior = self.private.session(token)
            except HTTPException:
                pass
            if prior:
                sid, state, expiry = prior
                with self.private.locked(sid):
                    _, state, expiry = self.private.session(token)
                    if state['profile']['email'] == wanted['email'] and state['profile']['phone'] == wanted['phone']:
                        if state['profile'] != wanted:
                            if state.get('bank',{}).get('status') != 'not_connected':
                                raise HTTPException(409,'Desconecte o banco antes de alterar os dados do titular.')
                            state['profile'] = wanted
                            self.private.save_session(sid,state,expiry)
                        return self.state_response(state)
                    self.private.expire_session(sid)
            token, sid, state = self.private.new_session(wanted)
            response.set_cookie(COOKIE,token,max_age=7200,secure=True,httponly=True,samesite='strict',path='/')
            self.private.audit(sid,'session_started')
            return self.state_response(state)

        @app.get('/api/security/session')
        def session_status(request: Request):
            _, state, _ = self.session(request)
            return self.state_response(state)

        @app.delete('/api/security/session')
        def logout(request: Request, response: Response):
            sid, _, _ = self.session(request)
            self.private.expire_session(sid)
            response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='strict',path='/')
            return {'status':'closed'}

        @app.post('/api/security/codes/send')
        def send_code(body: CodeRequest, request: Request):
            self.available(channel=body.channel)
            sid, _, _ = self.session(request)
            with self.private.locked(sid):
                _, state, expiry = self.session(request)
                if body.channel in state['verified']:
                    raise HTTPException(409,'Este contato já foi confirmado nesta sessão.')
                destination = state['profile']['email'] if body.channel == 'email' else '+55'+state['profile']['phone']
                ip = request.client.host if request.client else 'unknown'
                self.private.limit('send-ip',ip,10,3600)
                self.private.limit('send-destination-hour',destination,5,3600)
                self.private.limit('send-destination-cooldown',destination,1,60)
                # Remove an old challenge before the provider call, including if delivery fails.
                state['challenges'].pop(body.channel,None)
                self.private.save_session(sid,state,expiry)
                if body.channel == 'email' and getattr(self.providers, 'email_provider', '') == 'resend':
                    self.private.email_budget(reserve=True)
                    code = str(secrets.randbelow(1000000)).zfill(6)
                    delivery_id = str(uuid.uuid4())
                    code_sid = self.providers.email_code(destination, code, delivery_id)
                    challenge = {'sid':code_sid, 'provider':'resend', 'salt':delivery_id,
                                 'digest':self.private.digest('email-code',sid+':'+delivery_id+':'+code)}
                else:
                    challenge = {'sid':self.providers.verification_start(destination,body.channel), 'provider':'twilio'}
                state['challenges'][body.channel] = {**challenge,'expires':self.clock()+600,'attempts':0}
                self.private.save_session(sid,state,expiry)
                self.private.audit(sid,'code_sent_'+body.channel)
                return {'status':'sent','expires_in':600,'retry_after':60}

        @app.post('/api/security/codes/check')
        def check_code(body: CodeCheck, request: Request):
            self.available(channel=body.channel)
            sid, _, _ = self.session(request)
            with self.private.locked(sid):
                _, state, expiry = self.session(request)
                challenge = state['challenges'].get(body.channel)
                if not challenge or challenge['expires'] <= self.clock() or challenge['attempts'] >= 5:
                    raise HTTPException(403,'Código expirado ou limite de tentativas atingido. Solicite outro código.')
                challenge['attempts'] += 1
                self.private.save_session(sid,state,expiry)
                destination = state['profile']['email'] if body.channel == 'email' else '+55'+state['profile']['phone']
                if challenge.get('provider') == 'resend':
                    valid = hmac.compare_digest(challenge['digest'], self.private.digest(
                        'email-code',sid+':'+challenge['salt']+':'+body.code))
                else:
                    valid = self.providers.verification_check(challenge['sid'],body.code,destination,body.channel)
                if not valid:
                    raise HTTPException(422,'Código incorreto ou expirado. Confira o código recebido.')
                state['verified'][body.channel] = self.clock()
                del state['challenges'][body.channel]
                self.private.save_session(sid,state,expiry)
                self.private.audit(sid,'contact_verified_'+body.channel)
                return {'status':'verified', **self.state_response(state)}

        @app.post('/api/security/bank/start')
        def bank_start(body: BankConsent, request: Request):
            self.available(bank=True)
            sid, state, expiry = self.session(request)
            self.snapshot(request,state['profile'],require_bank=False)
            if not state['profile'].get('cpf'):
                raise HTTPException(422,'Informe o CPF do titular antes de conectar o banco.')
            with self.private.locked(sid):
                _, state, expiry = self.session(request)
                if state.get('bank',{}).get('status') != 'not_connected':
                    raise HTTPException(409,'Confira a conexão existente ou desconecte para iniciar outra.')
                self.private.limit('bank-start',sid,3,3600)
                attempt, nonce = str(uuid.uuid4()), secrets.token_urlsafe(32)
                callbacks = {event:self.origin+'/api/security/bank/callback/'+event+'?state='+nonce for event in ['success','exit','event']}
                url = self.providers.bank_start(state['profile'],attempt,callbacks)
                with self.private.store.connect() as db:
                    db.execute('INSERT INTO bank_attempts(id,session_id,nonce,expires) VALUES (?,?,?,?)',
                               (attempt,sid,self.private.digest('bank-nonce',nonce),self.clock()+1800))
                state['bank'] = {'status':'pending','attempt':attempt,'consented_at':self.clock()}
                self.private.save_session(sid,state,expiry)
                self.private.audit(sid,'bank_consent_started',attempt)
                return {'widget_url':url,'status':'pending'}

        @app.get('/api/security/bank/callback/{event}',response_class=HTMLResponse)
        def bank_callback(event: str, request: Request):
            self.available(bank=True)
            nonce, link = request.query_params.get('state',''), request.query_params.get('link','')
            if event not in {'success','exit','event'} or not 20 <= len(nonce) <= 100:
                raise HTTPException(400,'Retorno bancário inválido.')
            with self.private.store.connect() as db:
                row = db.execute('SELECT id,session_id,expires,consumed FROM bank_attempts WHERE nonce=?',
                                 (self.private.digest('bank-nonce',nonce),)).fetchone()
            if not row or row[2] <= self.clock() or row[3]:
                raise HTTPException(400,'Este retorno expirou ou já foi utilizado.')
            attempt,sid = row[0],row[1]
            with self.private.locked(sid):
                with self.private.store.connect() as db:
                    current_attempt = db.execute('SELECT consumed,expires,link FROM bank_attempts WHERE id=?',(attempt,)).fetchone()
                    saved = db.execute('SELECT expires,payload FROM secure_sessions WHERE id=?',(sid,)).fetchone()
                if not current_attempt or current_attempt[0] or current_attempt[1] <= self.clock():
                    raise HTTPException(400,'Este retorno expirou ou já foi utilizado.')
                if not saved or saved[0] <= self.clock():
                    raise HTTPException(401,'Sua sessão expirou. Retorne ao cadastro.')
                state = self.private.unseal('session:'+sid,saved[1])
                if event == 'success':
                    try:
                        link = str(uuid.UUID(link))
                    except ValueError:
                        raise HTTPException(400,'Conexão inválida.') from None
                    remote = self.providers.bank_link(link)
                    if not isinstance(remote,dict):raise ProviderUnavailable()
                    if remote.get('id') != link or remote.get('external_id') != attempt or current_attempt[2] and current_attempt[2]!=link:
                        raise HTTPException(403,'Não foi possível vincular esta conexão ao cadastro.')
                    state['bank'] = {'status':'connected','link':link,'attempt':attempt,'consented_at':state['bank'].get('consented_at')}
                    with self.private.store.connect() as db:
                        db.execute('UPDATE bank_attempts SET link=?,consumed=1,expires=? WHERE id=? AND consumed=0',(link,saved[0],attempt))
                    message = 'Conexão registrada. Volte ao cadastro e clique em Verificar conexão para conferir a disponibilidade dos dados.'
                    self.private.audit(sid,'bank_connected',attempt)
                else:
                    state['bank'] = {'status':'not_connected'}
                    with self.private.store.connect() as db:
                        db.execute('UPDATE bank_attempts SET consumed=1 WHERE id=?',(attempt,))
                    message = 'A conexão não foi concluída. Você pode voltar ao cadastro e tentar novamente.'
                self.private.save_session(sid,state,saved[0])
            return HTMLResponse('<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Conexão bancária</title><body><h1>Empréstimo Fácil</h1><p>'+message+'</p></body></html>',
                                headers={'Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"})

        @app.post('/api/security/bank/webhook')
        def bank_webhook(payload: dict, request: Request):
            self.available(bank=True)
            expected = 'Bearer '+self.providers.webhook_secret
            if not hmac.compare_digest(request.headers.get('authorization','').encode('utf-8'),expected.encode('utf-8')):
                raise HTTPException(401,'Autenticação necessária.')
            link, external = payload.get('link_id',''), payload.get('external_id','')
            try:
                link = str(uuid.UUID(link))
            except (ValueError,TypeError):
                raise HTTPException(422,'Evento inválido.') from None
            remote = self.providers.bank_link(link)
            if not isinstance(remote,dict):raise ProviderUnavailable()
            attempt_id = remote.get('external_id')
            if not isinstance(attempt_id,str):raise ProviderUnavailable()
            if external and external != attempt_id:
                raise HTTPException(403,'Evento não corresponde à conexão.')
            with self.private.store.connect() as db:
                row = db.execute('SELECT id,session_id,resources FROM bank_attempts WHERE id=? AND expires>?',
                                 (attempt_id,self.clock())).fetchone()
            if not row or remote.get('id') != link:
                return {'status':'ignored'}
            resource = payload.get('webhook_type')
            if not isinstance(resource,str):raise HTTPException(422,'Evento inválido.')
            if not isinstance(payload.get('data',{}),dict):raise HTTPException(422,'Evento inválido.')
            if payload.get('webhook_code') != 'historical_update' or resource not in {'OWNERS','ACCOUNTS','TRANSACTIONS'} or payload.get('data',{}).get('errors'):
                return {'status':'ignored'}
            with self.private.locked(row[1]):
                with self.private.store.connect() as db:
                    current = db.execute('SELECT resources,link,expires FROM bank_attempts WHERE id=?',(row[0],)).fetchone()
                    if not current or current[2]<=self.clock():return {'status':'ignored'}
                    if current[1] and current[1]!=link:raise HTTPException(403,'Evento não corresponde à conexão.')
                    resources = set(json.loads(current[0]));resources.add(resource)
                    db.execute('UPDATE bank_attempts SET resources=?,link=? WHERE id=?',(json.dumps(sorted(resources)),link,row[0]))
            return {'status':'accepted'}

        @app.get('/api/security/bank/status')
        def bank_status(request: Request):
            sid,state,_ = self.session(request)
            self.snapshot(request,state['profile'],require_bank=False)
            self.private.limit('bank-check',sid,30,60)
            self.apply_bank_data(sid)
            _,state,_ = self.session(request)
            return self.state_response(state)

        @app.delete('/api/security/bank')
        def bank_disconnect(request: Request):
            sid,_,_ = self.session(request)
            with self.private.locked(sid):
                _,state,expiry = self.session(request)
                link = state.get('bank',{}).get('link')
                if link:
                    self.providers.bank_delete(link)
                state['bank'] = {'status':'not_connected'}
                with self.private.store.connect() as db:
                    db.execute('DELETE FROM bank_attempts WHERE session_id=?',(sid,))
                self.private.save_session(sid,state,expiry)
                self.private.audit(sid,'bank_disconnected')
                return self.state_response(state)

        @app.post('/api/pf/applications',status_code=201)
        def start_pf(body: PF, request: Request):
            sid,attestations = self.snapshot(request,body.applicant)
            try:
                key = str(uuid.UUID(request.headers.get('idempotency-key','')))
            except ValueError:
                raise HTTPException(400,'Informe uma chave de envio válida.') from None
            identity,status = self.private.create_pf(sid,body,key,attestations,self.retention)
            return {'application_id':identity,'status':status,'required_files':body.required_files()}

        @app.put('/api/pf/applications/{identity}/files/{kind}')
        async def upload_pf(identity: str, kind: str, request: Request):
            sid,_,_ = self.session(request)
            body,status = self.private.pf(identity,sid)
            if status != 'uploading' or kind not in body['required_files']:
                raise HTTPException(409,'Este arquivo não pode ser enviado para este cadastro.')
            raw = await request.body()
            try:
                sanitized,extension = await run_in_threadpool(validate_file,raw,kind)
            except ValueError as error:
                raise HTTPException(422,str(error)) from None
            with self.private.locked('pf:'+identity):
                _,current = self.private.pf(identity,sid)
                if current != 'uploading':
                    raise HTTPException(409,'Cadastro já concluído.')
                self.private.save_file(identity,kind,sanitized,extension)
                self.private.audit(sid,'file_received',identity+':'+kind)
            return {'status':'stored'}

        @app.post('/api/pf/applications/{identity}/complete')
        def complete_pf(identity: str, request: Request):
            sid,_,_ = self.session(request)
            body,_ = self.private.pf(identity,sid)
            self.snapshot(request,body['applicant'])
            with self.private.locked('pf:'+identity):
                self.private.finalize(identity,sid)
            return {'application_id':identity,'status':'received'}

        from backend.admin import install_admin
        install_admin(app,self)
