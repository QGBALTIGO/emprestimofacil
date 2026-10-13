import hashlib
import hmac
import secrets

import pyotp
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from backend.private_store import password_hash, password_matches
from backend.secure_models import AdminLogin

COOKIE = '__Host-ef-admin'

# The panel is isolated from the public registration and never stores tokens in browser storage.
PAGE = '''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Revisão de cadastros | Empréstimo Fácil</title><style>
body{margin:0;background:#f4f7f5;color:#18352b;font:15px/1.6 system-ui,sans-serif}main{max-width:1000px;margin:40px auto;padding:24px}h1{font-size:26px}h2{font-size:20px}form,section{background:white;border:1px solid #dce6df;border-radius:12px;padding:24px;margin:20px 0}label{display:block;margin:16px 0}input{display:block;box-sizing:border-box;width:100%;max-width:420px;padding:12px;border:1px solid #bacfc1;border-radius:6px;font:inherit}button,a{font:inherit}button{padding:10px 16px;border:1px solid #087d32;border-radius:6px;background:#087d32;color:white;cursor:pointer}button:disabled{opacity:.5}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f5;padding:20px}td,th{text-align:left;padding:12px;border-bottom:1px solid #dce6df}table{width:100%;font-size:13px;border-collapse:collapse}.scroll{overflow:auto}.files{display:flex;gap:12px;flex-wrap:wrap}a{color:#087d32}[hidden]{display:none!important}#message{color:#8b2525}@media(max-width:600px){main{margin:12px auto;padding:16px}section,form{padding:16px}td,th{padding:8px}}
</style></head><body><main><h1>Empréstimo Fácil</h1><p>Revisão de cadastros</p><p id="message" role="status"></p><form id="login"><h2>Acesso da equipe</h2><label>Usuário<input name="username" required autocomplete="username" maxlength="80"></label><label>Senha<input name="password" type="password" required autocomplete="current-password" minlength="12" maxlength="128"></label><label>Código do autenticador<input name="code" required inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6"></label><button>Entrar</button></form><section id="panel" hidden><button id="logout">Sair</button><h2>Cadastros recebidos</h2><p>Recebimento e conexão bancária não representam aprovação de crédito.</p><button id="billing-load" type="button">Parcelas e pagamentos</button><p id="cakto-state"></p><div id="billing-rows" class="scroll"></div><div class="scroll"><table><thead><tr><th>Protocolo</th><th>Tipo</th><th>Estado</th><th>Recebido</th><th></th></tr></thead><tbody id="rows"></tbody></table></div></section><section id="detail" hidden><h2>Conferência do cadastro</h2><p id="record-note"></p><div id="files" class="files"></div><pre id="data"></pre></section></main><script>
(()=>{'use strict';const d=document,msg=d.getElementById('message');async function api(path,options={}){const r=await fetch(path,{...options,credentials:'same-origin',cache:'no-store',redirect:'error',headers:{'Content-Type':'application/json',...options.headers}});if(!r.ok){if(r.status===401){d.getElementById('login').hidden=false;d.getElementById('panel').hidden=true;d.getElementById('detail').hidden=true;}let error;try{error=(await r.json()).detail;}catch{}throw Error(typeof error==='string'?error:'Não foi possível concluir a operação.');}return r.json();}async function load(){const result=await api('/api/admin/records'),rows=d.getElementById('rows');rows.replaceChildren();d.getElementById('login').hidden=true;d.getElementById('panel').hidden=false;for(const item of result.records){const tr=d.createElement('tr');for(const text of [item.id,item.kind,item.status,new Date(item.created_at).toLocaleString('pt-BR')]){const td=d.createElement('td');td.textContent=text;tr.append(td);}const td=d.createElement('td'),button=d.createElement('button');button.textContent='Abrir';button.addEventListener('click',async()=>{try{const data=await api('/api/admin/records/'+item.kind+'/'+item.id);d.getElementById('data').textContent=JSON.stringify(data.data,null,2);d.getElementById('record-note').textContent='Acesso registrado. Dados bancários de sandbox devem ser usados somente em testes.';const links=d.getElementById('files');links.replaceChildren();for(const key of data.files){const a=d.createElement('a');a.textContent='Baixar '+key;a.href='/api/admin/files/'+item.id+'/'+key;a.rel='noreferrer';links.append(a);}d.getElementById('detail').hidden=false;d.getElementById('detail').scrollIntoView({block:'start'});msg.textContent='';}catch(error){msg.textContent=error.message;}});td.append(button);tr.append(td);rows.append(tr);}}d.getElementById('login').addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget,button=form.querySelector('button');button.disabled=true;try{await api('/api/admin/login',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(form)))});form.reset();await load();msg.textContent='';}catch(error){msg.textContent=error.message;}finally{button.disabled=false;form.elements.password.value='';form.elements.code.value='';}});d.getElementById('billing-load').addEventListener('click',async()=>{try{const connection=await api('/api/admin/cakto/status');d.getElementById('cakto-state').textContent=connection.authenticated?'Autenticação da API Cakto confirmada. '+(connection.can_charge?'Cobranças habilitadas no serviço.':'Cobranças aguardam ativação.'): 'Cakto ainda não configurada no serviço.';const result=await api('/api/admin/installments'),box=d.getElementById('billing-rows');box.replaceChildren();if(!result.installments.length){box.textContent='Nenhum contrato desembolsado com parcelas registradas.';return;}const table=d.createElement('table');for(const item of result.installments){const row=d.createElement('tr');for(const text of [item.customer_first_name,item.number+' / '+item.total_installments,item.due_date,new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(item.amount_cents/100),item.status]){const cell=d.createElement('td');cell.textContent=text;row.append(cell);}const cell=d.createElement('td'),button=d.createElement('button');button.textContent='Gerar link da parcela';button.disabled=!connection.can_charge;button.addEventListener('click',async()=>{button.disabled=true;try{const link=await api('/api/admin/installments/'+item.installment_id+'/link',{method:'POST',body:'{}'});const field=d.createElement('input');field.readOnly=true;field.value=link.url;field.setAttribute('aria-label','Link privado da parcela');cell.append(field);field.select();msg.textContent='Link privado válido por sete dias. Compartilhe apenas com o titular.';}catch(error){msg.textContent=error.message;}finally{button.disabled=false;}});cell.append(button);row.append(cell);table.append(row);}box.append(table);}catch(error){msg.textContent=error.message;}});d.getElementById('logout').addEventListener('click',async()=>{try{await api('/api/admin/logout',{method:'POST',body:'{}'});}finally{d.getElementById('login').hidden=false;d.getElementById('panel').hidden=true;d.getElementById('detail').hidden=true;d.getElementById('data').textContent='';d.getElementById('rows').replaceChildren();d.getElementById('files').replaceChildren();d.getElementById('billing-rows').replaceChildren();d.getElementById('cakto-state').textContent='';}});load().catch(()=>{});})();
</script></body></html>'''


def install_admin(app, security):
    dummy_password = password_hash(secrets.token_urlsafe(24))

    def session(request):
        if not security.enabled:
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
        script = PAGE.split('<script>')[1].split('</script>')[0]
        digest = __import__('base64').b64encode(hashlib.sha256(script.encode()).digest()).decode()
        return HTMLResponse(PAGE,headers={'Content-Security-Policy':"default-src 'none'; script-src 'sha256-"+digest+"'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",'Cache-Control':'no-store'})

    @app.post('/api/admin/login')
    def login(body: AdminLogin,request: Request,response: Response):
        if not security.enabled:
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
        with private.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE administrators SET last_step=? WHERE username=? AND last_step<?',(step,body.username,step))
            if changed.rowcount != 1:
                raise HTTPException(401,'Código já utilizado. Aguarde o próximo código do autenticador.')
            prior = request.cookies.get(COOKIE)
            if prior:
                db.execute('DELETE FROM admin_sessions WHERE id=?',(private.digest('admin-session',prior),))
            db.execute('INSERT INTO admin_sessions VALUES (?,?,?)',(private.digest('admin-session',token),body.username,now+900))
        response.set_cookie(COOKIE,token,max_age=900,httponly=True,secure=True,samesite='strict',path='/')
        private.audit(private.digest('admin-actor',body.username),'admin_login')
        return {'status':'authenticated','expires_in':900}

    @app.post('/api/admin/logout')
    def logout(request: Request,response: Response):
        key,actor = session(request)
        with security.private.store.connect() as db:
            db.execute('DELETE FROM admin_sessions WHERE id=?',(key,))
        response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='strict',path='/')
        security.private.audit(actor,'admin_logout')
        return {'status':'closed'}

    @app.get('/api/admin/records')
    def records(request: Request):
        _,actor = session(request)
        from datetime import datetime,timezone
        with security.private.store.connect() as db:
            pf = [{'id':r[0],'kind':'pf','status':r[1],'created_at':datetime.fromtimestamp(r[2],timezone.utc).isoformat()}
                  for r in db.execute('SELECT id,status,created FROM pf_records WHERE expires>? ORDER BY created DESC LIMIT 100',(security.clock(),))]
            original = [{'id':r[0],'kind':'initial','status':'received','created_at':r[1]}
                        for r in db.execute('SELECT application_id,created_at FROM applications ORDER BY created_at DESC LIMIT 100')]
        security.private.audit(actor,'records_listed')
        return {'records':sorted(pf+original,key=lambda r:r['created_at'],reverse=True)[:100]}

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
                row = db.execute('SELECT nonce,ciphertext FROM applications WHERE application_id=?',(identity,)).fetchone()
            if not row:
                raise HTTPException(404,'Cadastro não encontrado.')
            data = __import__('json').loads(AESGCM(private.key).decrypt(row[0],row[1],identity.encode()))
            files = []
        else:
            raise HTTPException(404,'Cadastro não encontrado.')
        private.audit(actor,'record_viewed',identity)
        return {'data':data,'files':files}

    @app.get('/api/admin/files/{identity}/{kind}')
    def file(identity: str,kind: str,request: Request):
        _,actor = session(request)
        security.private.pf(identity)
        raw,extension = security.private.file(identity,kind)
        security.private.audit(actor,'file_downloaded',identity+':'+kind)
        return Response(raw,media_type='application/octet-stream',headers={
            'Content-Disposition':'attachment; filename="'+kind+'.'+extension+'"',
            'Content-Security-Policy':"default-src 'none'; sandbox",'Cache-Control':'no-store'})
