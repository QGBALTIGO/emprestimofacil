import hmac
import secrets
from datetime import datetime,timezone
from typing import Annotated,Literal
from uuid import UUID

import pyotp
from fastapi import HTTPException, Request, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel,ConfigDict,Field,StrictStr,StringConstraints,field_validator

from backend.private_store import password_hash, password_matches
from backend.secure_models import AdminLogin

COOKIE = '__Host-ef-admin'


class ReviewUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    state:Literal['received','under_review','information_requested','closed']
    message:StrictStr=Field(default='',max_length=500)
    internal_note:StrictStr=Field(default='',max_length=2000)


class SetupStart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:StrictStr=Field(min_length=40,max_length=100,repr=False)


class SetupComplete(SetupStart):
    password:Annotated[StrictStr,StringConstraints(strip_whitespace=False)]=Field(min_length=12,max_length=128,repr=False)
    code:StrictStr=Field(pattern=r'^[0-9]{6}$',repr=False)

    @field_validator('password')
    @classmethod
    def meaningful_password(cls,value):
        if not value.strip():raise ValueError('A senha não pode conter somente espaços.')
        return value



def install_admin(app, security):
    dummy_password = password_hash(secrets.token_urlsafe(24))

    def session(request):
        if not security.origin_valid:
            raise HTTPException(503,'Revisão ainda não disponível.')
        token = request.cookies.get(COOKIE,'')
        if not 20 <= len(token) <= 100:
            raise HTTPException(401,'Entre com senha e autenticador para continuar.')
        key = security.private.digest('admin-session',token)
        with security.private.store.connect() as db:
            row = db.execute('SELECT username,expires FROM admin_sessions WHERE id=?',(key,)).fetchone()
        if not row or row[1] <= security.clock():
            raise HTTPException(401,'Sua sessão expirou. Entre novamente.')
        return key,security.private.digest('admin-actor',row[0])

    app.state.require_admin = session

    @app.get('/admin',response_class=HTMLResponse)
    def panel():
        from backend.client import private_page
        return private_page('admin-panel.html')

    @app.post('/api/admin/login')
    def login(body: AdminLogin,request: Request,response: Response):
        if not security.origin_valid:
            raise HTTPException(503,'Revisão ainda não disponível.')
        private,now = security.private,security.clock()
        ip = request.client.host if request.client else 'unknown'
        private.limit('admin-login-ip',ip,10,900)
        private.limit('admin-login-user',body.username,5,900)
        with private.store.connect() as db:
            row = db.execute('SELECT password,totp,last_step FROM administrators WHERE username=?',(body.username,)).fetchone()
        password_ok = password_matches(body.password,row[0] if row else dummy_password)
        step = None
        if row and password_ok:
            secret = private.unseal('totp:'+body.username,row[1])['secret']
            totp = pyotp.TOTP(secret)
            step = next((n for n in [int(now//30),int(now//30)-1,int(now//30)+1]
                         if hmac.compare_digest(totp.at(n*30),body.code)),None)
        if step is None or step <= (row[2] if row else -1):
            private.audit(private.digest('admin-actor',body.username),'admin_login_failed')
            raise HTTPException(401,'Usuário, senha ou código inválido.')
        token = secrets.token_urlsafe(32)
        lifetime=30*86400 if body.remember else 900
        with private.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE administrators SET last_step=? WHERE username=? AND last_step<?',(step,body.username,step))
            if changed.rowcount != 1:
                raise HTTPException(401,'Código já utilizado. Aguarde o próximo código do autenticador.')
            prior = request.cookies.get(COOKIE)
            if prior:
                db.execute('DELETE FROM admin_sessions WHERE id=?',(private.digest('admin-session',prior),))
            db.execute('INSERT INTO admin_sessions VALUES (?,?,?)',(private.digest('admin-session',token),body.username,now+lifetime))
        response.set_cookie(COOKIE,token,max_age=lifetime,httponly=True,secure=True,samesite='strict',path='/')
        private.audit(private.digest('admin-actor',body.username),'admin_login')
        return {'status':'authenticated','expires_in':lifetime}

    @app.post('/api/admin/logout-all')
    def logout_all(request:Request,response:Response):
        key,actor=session(request)
        with security.private.store.connect() as db:
            row=db.execute('SELECT username FROM admin_sessions WHERE id=?',(key,)).fetchone()
            if row:db.execute('DELETE FROM admin_sessions WHERE username=?',(row[0],))
        response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='strict',path='/')
        security.private.audit(actor,'admin_logout_all')
        return {'status':'closed'}

    @app.post('/api/admin/logout')
    def logout(request: Request,response: Response):
        key,actor = session(request)
        with security.private.store.connect() as db:
            db.execute('DELETE FROM admin_sessions WHERE id=?',(key,))
        response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='strict',path='/')
        security.private.audit(actor,'admin_logout')
        return {'status':'closed'}

    @app.get('/api/admin/session')
    def session_status(request:Request):
        key,_=session(request)
        with security.private.store.connect() as db:
            row=db.execute('SELECT expires FROM admin_sessions WHERE id=?',(key,)).fetchone()
        remaining=max(0,int(row[0]-security.clock())) if row else 0
        if not remaining:raise HTTPException(401,'Sua sessão expirou. Entre novamente.')
        return {'status':'authenticated','expires_in':remaining}

    @app.get('/api/admin/records')
    def records(request: Request,page:int=Query(1,ge=1,le=100000),page_size:int=Query(25,ge=1,le=100),
                kind:Literal['all','initial','pf']='all',
                state:Literal['all','received','uploading','under_review','information_requested','closed']='all',
                q:str=Query('',max_length=36)):
        _,actor = session(request)
        now=security.clock()
        cutoff=datetime.fromtimestamp(now-security.retention*86400,timezone.utc).isoformat()
        combined='''SELECT p.id AS id,'pf' AS kind,COALESCE(r.state,p.status) AS status,p.created AS created
            FROM pf_records p LEFT JOIN application_reviews r ON r.kind='pf' AND r.identity=p.id WHERE p.expires>? AND (p.status!='uploading' OR p.created>?)
            UNION ALL SELECT a.application_id,'initial',COALESCE(r.state,'received'),CAST(strftime('%s',a.created_at) AS REAL)
            FROM applications a LEFT JOIN application_reviews r ON r.kind='initial' AND r.identity=a.application_id WHERE a.created_at>?'''
        where=" WHERE (?='all' OR kind=?) AND (?='all' OR status=?) AND instr(id,?)>0"
        params=(now,now-86400,cutoff,kind,kind,state,state,q.strip().lower())
        with security.private.store.connect() as db:
            total=db.execute('SELECT count(*) FROM ('+combined+')'+where,params).fetchone()[0]
            rows=db.execute('SELECT * FROM ('+combined+')'+where+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',
                            (*params,page_size,(page-1)*page_size)).fetchall()
        security.private.audit(actor,'records_listed')
        def display_name(identity,kind):
            private=security.private
            if kind=='pf':data,_=private.pf(identity)
            else:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                with private.store.connect() as db:encrypted=db.execute('SELECT nonce,ciphertext FROM applications WHERE application_id=?',(identity,)).fetchone()
                if not encrypted:return 'Cadastro'
                data=__import__('json').loads(AESGCM(private.key).decrypt(encrypted[0],encrypted[1],identity.encode()))
            return data.get('applicant',{}).get('full_name','Cadastro')
        return {'records':[{'id':r[0],'kind':r[1],'status':r[2],'created_at':datetime.fromtimestamp(r[3],timezone.utc).isoformat(),
                            'display_name':display_name(r[0],r[1])} for r in rows],
                'page':page,'page_size':page_size,'total':total,'total_pages':max(1,(total+page_size-1)//page_size)}

    @app.get('/api/admin/records/{kind}/{identity}')
    def record(kind: str,identity: str,request: Request):
        _,actor = session(request)
        private = security.private
        if kind == 'pf':
            data,_ = private.pf(identity)
            with private.store.connect() as db:
                files = [r[0] for r in db.execute('SELECT kind FROM private_files WHERE record_id=?',(identity,))]
        elif kind == 'initial':
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            with private.store.connect() as db:
                cutoff=datetime.fromtimestamp(security.clock()-security.retention*86400,timezone.utc).isoformat()
                row = db.execute('SELECT nonce,ciphertext FROM applications WHERE application_id=? AND created_at>?',(identity,cutoff)).fetchone()
            if not row:
                raise HTTPException(404,'Cadastro não encontrado.')
            data = __import__('json').loads(AESGCM(private.key).decrypt(row[0],row[1],identity.encode()))
            files = []
        else:
            raise HTTPException(404,'Cadastro não encontrado.')
        private.audit(actor,'record_viewed',identity)
        from backend.client import review
        current=review(private,kind,identity)
        with private.store.connect() as db:
            row=db.execute('SELECT payload FROM application_reviews WHERE kind=? AND identity=?',(kind,identity)).fetchone()
        if row:current['internal_note']=private.unseal('review:'+kind+':'+identity,row[0]).get('internal_note','')
        return {'data':data,'files':files,'review':current}

    @app.post('/api/admin/records/{kind}/{identity}/review')
    def update_review(kind:Literal['initial','pf'],identity:UUID,body:ReviewUpdate,request:Request):
        _,actor=session(request)
        identity=str(identity);private=security.private
        if kind=='pf':private.pf(identity)
        else:
            with private.store.connect() as db:
                if not db.execute('SELECT 1 FROM applications WHERE application_id=?',(identity,)).fetchone():
                    raise HTTPException(404,'Cadastro não encontrado.')
        payload=private.seal('review:'+kind+':'+identity,{'message':body.message,'internal_note':body.internal_note})
        with private.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO application_reviews VALUES (?,?,?,?,?)',
                       (kind,identity,body.state,security.clock(),payload))
        private.audit(actor,'record_review_'+body.state,identity)
        return {'status':'updated'}

    @app.get('/api/admin/overview')
    def overview(request:Request):
        _,actor=session(request);private=security.private
        now=security.clock();cutoff=datetime.fromtimestamp(now-security.retention*86400,timezone.utc).isoformat()
        with private.store.connect() as db:
            counts={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['administrators','provider_deletions']}
            counts['applications']=db.execute('SELECT count(*) FROM applications WHERE created_at>?',(cutoff,)).fetchone()[0]
            counts['pf_records']=db.execute("SELECT count(*) FROM pf_records WHERE expires>? AND (status!='uploading' OR created>?)",(now,now-86400)).fetchone()[0]
            uploads=db.execute("SELECT count(*) FROM pf_records WHERE status='uploading' AND expires>? AND created>?",(now,now-86400)).fetchone()[0]
            stored_bytes=db.execute('SELECT COALESCE(sum(size),0) FROM private_files').fetchone()[0]
            pending_pix=db.execute('SELECT count(*) FROM sales_reconcile_queue').fetchone()[0]
        billing,sales=app.state.billing,app.state.sales
        provider=security.providers
        def absent(items):return [key for key,attribute in items if not getattr(provider,attribute,'')]
        twilio=[('TWILIO_ACCOUNT_SID','twilio_account'),('TWILIO_AUTH_TOKEN','twilio_token'),('TWILIO_VERIFY_SERVICE_SID','twilio_service')]
        email_provider=getattr(provider,'email_provider','twilio')
        email_missing=absent([('RESEND_API_KEY','resend_key'),('EMAIL_FROM','email_from')]) if email_provider=='resend' else absent(twilio)
        cakto_missing=[key for key,attribute in [('CAKTO_CLIENT_ID','client_id'),('CAKTO_CLIENT_SECRET','client_secret'),
                                               ('CAKTO_WEBHOOK_SECRET','webhook_secret')] if not getattr(billing.provider,attribute,'')]
        private.audit(actor,'overview_viewed')
        return {'receiving':security.enabled,'storage':{'used_bytes':stored_bytes,'limit_bytes':private.storage_max,
                'free_bytes':__import__('shutil').disk_usage(private.files_dir).free,'reserve_bytes':private.disk_reserve},
                'email_budget':private.email_budget() if email_provider=='resend' else None,
                'counts':{'initial':counts['applications'],'pf':counts['pf_records'],'pending_pix':pending_pix,
                'incomplete':uploads,'administrators':counts['administrators'],'pending_revocations':counts['provider_deletions']},
                'retention_days':security.retention,'integrations':{
                    'email':{'configured':security.channel_ready('email'),'provider':email_provider,'required':True,
                             'missing':email_missing,'documentation_url':'https://resend.com/docs' if email_provider=='resend' else 'https://www.twilio.com/docs/verify',
                             'requirements':['Conta própria e chave de envio.','Domínio/remetente verificado e entrega do código homologada.' if email_provider=='resend' else 'E-mail habilitado no Verify com integração SendGrid e remetente verificado.']},
                    'sms':{'configured':security.channel_ready('sms'),'provider':'twilio','required':security.phone_required,
                           'missing':absent(twilio),'documentation_url':'https://www.twilio.com/docs/verify',
                           'requirements':['Conta própria, serviço Verify e saldo para mensagens.','Entrega de SMS homologada em telefone real.']},
                    'open_finance':{'configured':bool(security.providers.bank_ready),'environment':security.providers.belvo_host,
                                    'required':security.bank_required,'missing':absent([('BELVO_SECRET_ID','belvo_id'),('BELVO_SECRET_PASSWORD','belvo_password'),
                                        ('BELVO_WEBHOOK_TOKEN','webhook_secret'),('COMPANY_NAME','company_name'),('COMPANY_TERMS_URL','terms_url')]),
                                    'documentation_url':'https://developers.belvo.com/',
                                    'requirements':['Conta/contratação e credenciais próprias da Belvo.','Teste de consentimento, titularidade, dados e revogação antes de produção.']},
                    'cakto':{'configured':bool(billing.provider.configured),'enabled':bool(security.enabled and billing.provider.can_charge),
                             'missing':cakto_missing,'documentation_url':'https://docs.cakto.com.br/',
                             'requirements':['Conta ativa, OAuth com escopos adequados e webhook assinado.','Oferta em BRL com valor correto e autorização da Cakto para a operação.','Parcelas apenas de contratos reais assinados e já desembolsados.']},
                    'affiliate_sales':{'enabled':bool(sales.ready),'documentation_url':'https://docs.cakto.com.br/',
                                       'requirements':['Compra independente de afiliados com oferta e webhook próprios.']}},
                'configuration':{'private_file':'/etc/emprestimofacil/backend.env',
                                 'instruction':'O operador insere as credenciais somente no arquivo privado da VPS, com permissão 0600, e reinicia apenas o serviço emprestimofacil. Não cole chaves neste painel.',
                                 'phone_required':bool(security.phone_required)},
                'note':'Configuração não confirma entrega de códigos, conexão bancária ou pagamento. Valide os provedores antes de ativar.'}

    def bootstrap(body,request):
        if not security.origin_valid:raise HTTPException(503,'Configure a origem HTTPS antes de iniciar.')
        private=security.private
        private.limit('admin-setup-ip',request.client.host if request.client else 'unknown',5,900)
        key=private.digest('admin-bootstrap',body.token)
        with private.store.connect() as db:
            row=db.execute('SELECT username,expires,payload FROM admin_bootstrap WHERE id=?',(key,)).fetchone()
        if not row or row[1]<=security.clock():raise HTTPException(401,'Convite inválido ou expirado.')
        return key,row[0],private.unseal('bootstrap:'+key,row[2])

    @app.post('/api/admin/setup/start')
    def setup_start(body:SetupStart,request:Request):
        key,username,data=bootstrap(body,request)
        uri=pyotp.TOTP(data['secret']).provisioning_uri(username,issuer_name='Empréstimo Fácil')
        return {'username':username,'secret':data['secret'],'provisioning_uri':uri,
                'note':'Guarde a chave apenas no seu autenticador. O convite expira e é consumido ao concluir.'}

    @app.post('/api/admin/setup/complete')
    def setup_complete(body:SetupComplete,request:Request):
        key,username,data=bootstrap(body,request)
        now=security.clock();totp=pyotp.TOTP(data['secret'])
        step=next((n for n in [int(now//30),int(now//30)-1,int(now//30)+1] if hmac.compare_digest(totp.at(n*30),body.code)),None)
        if step is None:raise HTTPException(422,'Código do autenticador inválido.')
        encoded=password_hash(body.password)
        with security.private.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT username,expires FROM admin_bootstrap WHERE id=?',(key,)).fetchone()
            if not row or row[1]<=now:raise HTTPException(401,'Convite inválido ou expirado.')
            if db.execute('SELECT 1 FROM administrators WHERE username=?',(username,)).fetchone():
                raise HTTPException(409,'Este acesso já foi criado.')
            db.execute('INSERT INTO administrators(username,password,totp,last_step) VALUES (?,?,?,?)',
                       (username,encoded,security.private.seal('totp:'+username,{'secret':data['secret']}),step))
            db.execute('DELETE FROM admin_bootstrap WHERE id=?',(key,))
        security.private.audit(security.private.digest('admin-actor',username),'admin_created')
        return {'status':'created'}

    @app.get('/api/admin/files/{identity}/{kind}')
    def file(identity: str,kind: str,request: Request,preview:bool=False):
        _,actor = session(request)
        security.private.pf(identity)
        raw,extension = security.private.file(identity,kind)
        if preview:
            types={'jpg':'image/jpeg','png':'image/png','webm':'video/webm','mp4':'video/mp4','pdf':'application/pdf'}
            if extension not in types:raise HTTPException(415,'Este arquivo está disponível somente para download.')
            security.private.audit(actor,'file_previewed',identity+':'+kind)
            headers={'Content-Disposition':'inline; filename="'+kind+'.'+extension+'"',
                     'Cache-Control':'private, no-store','Content-Security-Policy':"default-src 'none'; sandbox",
                     'Accept-Ranges':'bytes','X-Content-Type-Options':'nosniff'}
            requested=request.headers.get('range');status=200
            if requested:
                import re
                match=re.fullmatch(r'bytes=([0-9]*)-([0-9]*)',requested)
                if not match or not any(match.groups()) or len(requested)>80:
                    raise HTTPException(416,'Trecho inválido.',headers={'Content-Range':'bytes */'+str(len(raw))})
                a,b=match.groups();start=int(a) if a else max(0,len(raw)-int(b));end=min(int(b),len(raw)-1) if a and b else len(raw)-1
                if start>end or start>=len(raw):raise HTTPException(416,'Trecho inválido.',headers={'Content-Range':'bytes */'+str(len(raw))})
                headers['Content-Range']=f'bytes {start}-{end}/{len(raw)}';raw=raw[start:end+1];status=206
            return Response(raw,status_code=status,media_type=types[extension],headers=headers)
        security.private.audit(actor,'file_downloaded',identity+':'+kind)
        return Response(raw,media_type='application/octet-stream',headers={
            'Content-Disposition':'attachment; filename="'+kind+'.'+extension+'"',
            'Content-Security-Policy':"default-src 'none'; sandbox",'Cache-Control':'no-store'})
