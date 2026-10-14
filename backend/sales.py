"""Independent, honestly identified product purchase. Never gates loan/PF registration."""
import hashlib
import json
import os
import re
import secrets
import uuid
from urllib.parse import urlparse
from datetime import datetime,timezone

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator
from starlette.concurrency import run_in_threadpool


from backend.payment_utils import qr_image, timestamp
from backend.cakto import Cakto, STATUSES, cents
from backend.providers import ProviderUnavailable

COOKIE = '__Host-baltigo-sale'
PRODUCT_NAME = 'AFILIADOS QG BALTIGO'
OFFER_ID = '3bd8an5'
PRICE = 749


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def valid_cpf(raw):
    return bool(re.fullmatch(r'\d{11}',raw) and len(set(raw))>1 and all(
        (sum(int(raw[i])*(n+1-i) for i in range(n))*10%11)%10==int(raw[n]) for n in [9,10]))


class Buyer(Model):
    full_name: StrictStr = Field(min_length=3, max_length=150)
    email: StrictStr = Field(max_length=254)
    cpf: StrictStr
    phone: StrictStr

    @model_validator(mode='after')
    def valid(self):
        if len(self.full_name.split()) < 2 or not all(c.isalpha() or c in " '-’." for c in self.full_name):
            raise ValueError('Full name')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', self.email) or not valid_cpf(self.cpf):
            raise ValueError('Buyer details')
        if not re.fullmatch(r'[1-9]\d9\d{8}', self.phone):raise ValueError('Phone with DDD')
        self.email = self.email.lower()
        return self


class Purchase(Model):
    buyer: Buyer
    terms_accepted: StrictBool

    @model_validator(mode='after')
    def accepted(self):
        if not self.terms_accepted:raise ValueError('Confirm purchase')
        return self


class SalesCakto(Cakto):
    @property
    def can_charge(self):
        return (self.configured and os.getenv('CAKTO_SALES_ENABLED','false') == 'true'
                and len(self.webhook_secret) >= 16)


class Sales:
    def __init__(self, security, provider=None):
        self.security, self.provider = security, provider or SalesCakto(clock=security.clock)
        self.offer_id = os.getenv('CAKTO_SALES_OFFER_ID', OFFER_ID)
        # This module sells one declared product. Price/name cannot be sent by the buyer.
        self.retention = int(os.getenv('SALES_RETENTION_DAYS','30'))
        if not 1 <= self.retention <= 365:raise RuntimeError('SALES_RETENTION_DAYS: 1–365')
        if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', self.offer_id):raise RuntimeError('Invalid sales offer ID')
        u = urlparse(security.origin)
        self.origin_ready = (u.scheme == 'https' and bool(u.netloc) and not u.username and not u.password
                             and u.path in {'','/'} and not u.query and not u.fragment)

    @property
    def ready(self):return bool(self.origin_ready and self.provider.can_charge)

    def open(self):
        with self.private.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sales_sessions (id TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS product_sales (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    request_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
                    order_id TEXT UNIQUE, created REAL NOT NULL, expires REAL NOT NULL, payload BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS sales_by_session ON product_sales(session_id);
                CREATE TABLE IF NOT EXISTS sales_events (id TEXT PRIMARY KEY, expires REAL NOT NULL);
            ''')

    @property
    def private(self):return self.security.private

    def available(self):
        if not self.ready:raise HTTPException(503,'O pagamento direto ainda não está disponível.')

    def outbound(self):self.private.limit('cakto-outbound','account',50,60)

    def session(self, request):
        token = request.cookies.get(COOKIE,'')
        if not token or len(token)>100:raise HTTPException(401,'Reabra a página para iniciar uma sessão segura.')
        sid = self.private.digest('sales-session',token)
        with self.private.store.connect() as db:
            row = db.execute('SELECT expires FROM sales_sessions WHERE id=?',(sid,)).fetchone()
        if not row or row[0]<=self.security.clock():raise HTTPException(401,'Sua sessão de compra expirou.')
        return sid

    def row(self, sid):
        with self.private.store.connect() as db:
            row = db.execute('SELECT id,request_id,digest,status,order_id,payload FROM product_sales '
                             'WHERE session_id=? ORDER BY created DESC LIMIT 1',(sid,)).fetchone()
        if not row:return None
        return {'id':row[0],'request_id':row[1],'digest':row[2],'status':row[3],'order_id':row[4],
                'data':self.private.unseal('sale:'+row[0],row[5])}

    def save(self, row):
        with self.private.store.connect() as db:
            db.execute('UPDATE product_sales SET status=?,order_id=?,payload=? WHERE id=?',
                       (row['status'],row['order_id'],self.private.seal('sale:'+row['id'],row['data']),row['id']))

    def checked_offer(self):
        self.outbound();offer = self.provider.offer(self.offer_id)
        if (offer.get('id') != self.offer_id or offer.get('status') != 'active'
                or offer.get('type') != 'unique' or offer.get('currency') != 'BRL'
                or offer.get('name') != PRODUCT_NAME or cents(offer.get('price')) != PRICE):
            raise HTTPException(409,'A oferta mudou. Confira o produto e o preço na Cakto antes de continuar.')
        try:product_id = str(uuid.UUID(offer['product']))
        except (KeyError,ValueError,TypeError):raise ProviderUnavailable() from None
        return product_id

    def validate_order(self, row, order, customer=True):
        data = row['data'];buyer = data['buyer']
        if (order.get('id') != row['order_id'] or order.get('paymentMethod') != 'pix'
                or order.get('status') not in STATUSES or cents(order.get('amount')) != PRICE
                or order.get('product',{}).get('id') != data['product_id']
                or order.get('offer',{}).get('id') != data['offer_id']):raise ProviderUnavailable()
        if customer:
            person = order.get('customer',{})
            reference = order.get('sck') or order.get('metadata',{}).get('sck')
            if (person.get('docType') != 'cpf' or person.get('docNumber') != buyer['cpf']
                    or str(person.get('email','')).lower() != buyer['email']
                    or reference != row['request_id']):raise ProviderUnavailable()
        if order['status'] == 'paid' and (not order.get('paidAt') or timestamp(order['paidAt']) > self.security.clock()+300):
            raise ProviderUnavailable()

    def reconcile(self, row, force=False):
        if not row['order_id']:return
        if not force and row['data'].get('checked_at',0)>self.security.clock()-15:return
        self.outbound();order = self.provider.order(row['order_id'])
        self.validate_order(row,order)
        if not row['data'].get('pix') and order.get('pix'):
            pix=order['pix'];qr_image(pix.get('qrCode'))
            if timestamp(pix.get('expirationDate')) <= self.security.clock()+86400:row['data']['pix']=pix
        row['status'] = order['status'];row['data']['checked_at'] = self.security.clock()
        row['data']['paid_at'] = order.get('paidAt') if row['status']=='paid' else None
        self.save(row)

    def view(self, row):
        if not row:return {'status':'new'}
        result = {'purchase_id':row['id'],'status':row['status'],'product_name':PRODUCT_NAME,
                  'amount_cents':PRICE,'buyer_first_name':row['data']['buyer']['full_name'].split()[0]}
        pix = row['data'].get('pix')
        if pix and row['status'] in {'waiting_payment','processing','authorized'}:
            expiration = timestamp(pix['expirationDate'])
            if expiration>self.security.clock():
                result['pix']={'code':pix['qrCode'],'qr_image':qr_image(pix['qrCode']),
                               'expires_at':datetime.fromtimestamp(expiration,timezone.utc).isoformat(),'server_time':self.security.clock()}
            else:result['pix_expired']=True
        phone=os.getenv('BALTIGO_SUPPORT_WHATSAPP','')
        if row['status']=='paid' and re.fullmatch(r'[1-9]\d{9,14}',phone):
            from urllib.parse import urlencode
            result['support_url']='https://wa.me/'+phone+'?'+urlencode({'text':'Olá! Meu pagamento de AFILIADOS QG BALTIGO foi confirmado. Protocolo: '+row['id']})
        return result

    def create(self, sid, purchase, request_id, ip):
        self.available()
        with self.private.locked('sale:'+sid):
            digest=self.private.digest('sales-request',json.dumps(purchase.model_dump(),sort_keys=True))
            previous=self.row(sid)
            if previous:
                if previous['digest'] != digest or previous['request_id'] != request_id:
                    raise HTTPException(409,'Já existe uma compra nesta sessão. Confira o pagamento existente.')
                self.reconcile(previous);return self.view(previous)
            self.private.limit('sales-create',sid,2,3600)
            product_id=self.checked_offer();buyer=purchase.buyer.model_dump()
            body={'paymentMethod':'pix','customer':{'name':buyer['full_name'],'email':buyer['email'],
                  'phone':'55'+buyer['phone'],'docType':'cpf','docNumber':buyer['cpf'],
                  'fingerprint':'sale_'+secrets.token_hex(16),'ip':ip},
                  'items':[{'offerId':self.offer_id,'quantity':1,'offerType':'main'}],
                  'metadata':{'sck':request_id},'pixExpiresIn':900}
            identity=str(uuid.uuid4());now=self.security.clock()
            row={'id':identity,'request_id':request_id,'digest':digest,'status':'creating','order_id':None,
                 'data':{'buyer':buyer,'product_id':product_id,'offer_id':self.offer_id,
                         'request':body,'terms_accepted_at':now}}
            with self.private.store.connect() as db:
                try:db.execute('INSERT INTO product_sales VALUES (?,?,?,?,?,?,?,?,?)',
                    (identity,sid,request_id,digest,'creating',None,now,now+self.retention*86400,
                     self.private.seal('sale:'+identity,row['data'])))
                except __import__('sqlite3').IntegrityError:
                    raise HTTPException(409,'Esta tentativa já está vinculada a outra sessão.') from None
            try:
                self.outbound();order=self.provider.create_pix(body,request_id)
                row['order_id']=str(uuid.UUID(order['id']));row['status']='review';self.save(row)
                self.validate_order(row,order,False)
                pix=order.get('pix',{});expiration=timestamp(pix.get('expirationDate'))
                qr_image(pix.get('qrCode'))
                if not now<expiration<=now+86400:raise ProviderUnavailable()
                row['data']['pix']=pix;self.save(row)
                self.reconcile(row,True)
                self.private.audit(sid,'affiliate_pix_created',identity)
                return self.view(row)
            except (ProviderUnavailable,ValueError,TypeError,KeyError):
                row['status']='review' if row['order_id'] else 'uncertain';self.save(row)
                raise HTTPException(503,'A geração do Pix aguarda conferência. Nenhuma nova cobrança será criada automaticamente.') from None

    def install(self, app):
        @app.middleware('http')
        async def guard(request, call_next):
            if request.url.path.startswith('/api/sales/'):
                webhook=request.url.path=='/api/sales/cakto/webhook'
                if request.method not in {'GET','HEAD'}:
                    if not webhook and request.headers.get('origin')!=self.security.origin:
                        return JSONResponse({'detail':'Origem não permitida.'},status_code=403)
                    if request.headers.get('content-type','').split(';')[0]!='application/json':
                        return JSONResponse({'detail':'Use JSON.'},status_code=415)
                    raw=bytearray()
                    async for chunk in request.stream():
                        raw.extend(chunk)
                        if len(raw)>(131072 if webhook else 4096):
                            return JSONResponse({'detail':'Solicitação muito grande.'},status_code=413)
                    request._body=bytes(raw)
                try:self.private.limit('sales-requests',request.client.host if request.client else '',60,60)
                except HTTPException as error:return JSONResponse({'detail':error.detail},status_code=error.status_code,headers=error.headers)
            return await call_next(request)

        @app.get('/afiliados')
        @app.get('/afiliados.html')
        def page():
            from backend.app import ROOT
            return FileResponse(ROOT/'afiliados.html',headers={'Cache-Control':'no-store'})

        @app.get('/api/sales/config')
        def config():return {'available':self.ready,'product_name':PRODUCT_NAME,'amount_cents':PRICE}

        @app.post('/api/sales/session')
        def session(request:Request,response:Response):
            self.available()
            try:sid=self.session(request)
            except HTTPException:
                token=secrets.token_urlsafe(32);sid=self.private.digest('sales-session',token)
                with self.private.store.connect() as db:db.execute('INSERT INTO sales_sessions VALUES (?,?)',(sid,self.security.clock()+86400))
                response.set_cookie(COOKIE,token,max_age=86400,secure=True,httponly=True,samesite='strict',path='/')
            with self.private.locked('sale:'+sid):
                row=self.row(sid)
                try:self.reconcile(row) if row else None
                except ProviderUnavailable:raise HTTPException(503,'A compra aguarda conferência na Cakto.') from None
                return self.view(row)

        @app.post('/api/sales/pix')
        def pix(body:Purchase,request:Request):
            try:request_id=str(uuid.UUID(request.headers.get('idempotency-key','')))
            except ValueError:raise HTTPException(400,'Chave de envio inválida.') from None
            try:return self.create(self.session(request),body,request_id,request.client.host if request.client else '')
            except ProviderUnavailable:raise HTTPException(503,'Não foi possível conferir a oferta na Cakto. Tente mais tarde.') from None

        @app.post('/api/sales/check')
        def check(request:Request):
            self.available();sid=self.session(request)
            self.private.limit('sales-check',sid,10,60)
            with self.private.locked('sale:'+sid):
                row=self.row(sid)
                try:self.reconcile(row) if row else None
                except ProviderUnavailable:raise HTTPException(503,'Não foi possível confirmar o pagamento. Aguarde e tente novamente.') from None
                return self.view(row)

        @app.post('/api/sales/cakto/webhook')
        async def webhook(request:Request):
            raw=await request.body()
            if not self.provider.valid_webhook(raw,request.headers.get('x-cakto-timestamp'),request.headers.get('x-cakto-signature')):
                raise HTTPException(401,'Assinatura inválida.')
            try:data=json.loads(raw)['data'];order_id=str(uuid.UUID(data['id']))
            except (ValueError,TypeError,KeyError):raise HTTPException(400,'Evento inválido.') from None
            def receive_event():
                event=self.private.digest('sales-webhook',hashlib.sha256(raw).hexdigest())
                reference=data.get('sck') or data.get('metadata',{}).get('sck') or ''
                with self.private.store.connect() as db:
                    if db.execute('SELECT 1 FROM sales_events WHERE id=?',(event,)).fetchone():return {'status':'duplicate'}
                    found=db.execute('SELECT session_id FROM product_sales WHERE order_id=? OR request_id=?',(order_id,reference)).fetchone()
                if not found:return {'status':'ignored'}
                with self.private.locked('sale:'+found[0]):
                    current=self.row(found[0])
                    if current['order_id'] and current['order_id']!=order_id:raise HTTPException(409,'Pedido divergente.')
                    current['order_id']=order_id
                    try:self.reconcile(current,True)
                    except ProviderUnavailable:raise HTTPException(503,'Conciliação pendente.') from None
                    with self.private.store.connect() as db:db.execute('INSERT OR IGNORE INTO sales_events VALUES (?,?)',(event,self.security.clock()+86400))
                return {'status':'received'}
            return await run_in_threadpool(receive_event)
