"""Installment collection after an existing loan has been signed and disbursed."""
import hashlib
import json
import uuid
from datetime import datetime,timezone

from fastapi import HTTPException,Request,Response,Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from backend.billing_models import Invite
from backend.billing_store import BillingStore
from backend.cakto import Cakto,STATUSES,cents
from backend.providers import ProviderUnavailable
from backend.payment_utils import qr_image, timestamp

COOKIE='__Host-ef-installment'




class Billing:
    def __init__(self,security,provider=None):
        self.security,self.provider=security,provider or Cakto(clock=security.clock)
        self.store=None

    def open(self):
        self.store=BillingStore(self.security.private)

    def available(self):
        if not self.security.enabled or not self.provider.can_charge:
            raise HTTPException(503,'O pagamento de parcelas ainda não está disponível.')

    def outbound(self):
        self.security.private.limit('cakto-outbound','account',50,60)

    def validate_order(self,order,identity,item,customer,attempt,verify_customer=True):
        if (not isinstance(order,dict) or not isinstance(order.get('product'),dict) or not isinstance(order.get('offer'),dict)
                or not isinstance(order.get('status'),str) or order.get('currency','BRL')!='BRL'):
            raise ProviderUnavailable()
        if (order.get('id')!=identity or order.get('paymentMethod')!='pix' or order.get('status') not in STATUSES
                or cents(order.get('amount'))!=item['amount_cents']
                or order['product'].get('id')!=item['product_id'] or order['offer'].get('id')!=item['offer_id']):
            raise ProviderUnavailable()
        if verify_customer:
            supplied=order.get('customer',{})
            if not isinstance(supplied,dict):raise ProviderUnavailable()
            if (supplied.get('docType')!='cpf' or supplied.get('docNumber')!=customer['cpf']
                    or str(supplied.get('email','')).lower()!=customer['email'] or order.get('sck')!=attempt):
                raise ProviderUnavailable()
        if order.get('status')=='paid' and (not order.get('paidAt') or timestamp(order['paidAt'])>self.security.clock()+300):
            raise ProviderUnavailable()

    def reconcile(self,installment_id,force=False):
        with self.security.private.locked('bill:'+installment_id):
            contract,item,previous_status,attempt=self.store.installment(installment_id)
            if not attempt:return
            state,order_id,data=self.store.attempt(attempt)
            if not order_id:return
            if not force and data.get('checked_at',0)>self.security.clock()-30:return
            self.outbound();order=self.provider.order(order_id)
            try:self.validate_order(order,order_id,item,contract['customer'],attempt)
            except ProviderUnavailable:
                self.store.save_attempt(attempt,'review',data)
                raise
            summary={name:order.get(name) for name in ['id','status','paidAt','refundedAt','canceledAt','chargedbackAt']}
            data.update(order=summary,checked_at=self.security.clock())
            self.store.save_attempt(attempt,'confirmed',data)
            status=order['status']
            with self.store.store.connect() as db:
                db.execute('UPDATE billing_installments SET status=? WHERE id=?',(status,installment_id))
                if status=='paid' and previous_status!='paid' and not db.execute("SELECT 1 FROM billing_installments WHERE contract_id=? AND status!='paid'",(contract['id'],)).fetchone():
                    db.execute('UPDATE billing_contracts SET expires=MAX(expires,?) WHERE id=?',(self.security.clock()+self.store.retention*86400,contract['id']))
            self.security.private.audit('cakto','installment_reconciled',installment_id+':'+status)

    def create_pix(self,identity,fingerprint,ip):
        self.available()
        with self.security.private.locked('bill:'+identity):
            contract,item,status,attempt=self.store.installment(identity)
            if attempt:
                self.reconcile(identity,True)
                contract,item,status,attempt=self.store.installment(identity)
                state,order_id,data=self.store.attempt(attempt)
                if status=='paid':return self.view(identity)
                if state in {'creating','uncertain','review'} or not order_id:
                    raise HTTPException(409,'A geração do Pix aguarda conferência. Não foi criada outra cobrança.')
                if status not in {'canceled','refused','blocked'}:
                    return self.view(identity)
            self.outbound();offer=self.provider.offer(item['offer_id'])
            if not isinstance(offer,dict):raise ProviderUnavailable()
            if (offer.get('id')!=item['offer_id'] or offer.get('product')!=item['product_id'] or offer.get('status')!='active'
                    or offer.get('type')!='unique' or offer.get('currency')!='BRL' or cents(offer.get('price'))!=item['amount_cents']):
                raise HTTPException(409,'A oferta de pagamento precisa corresponder ao valor e à moeda desta parcela.')
            customer=contract['customer']
            body={'paymentMethod':'pix','customer':{'name':customer['full_name'],'email':customer['email'],
                  'phone':'55'+customer['phone'],'docType':'cpf','docNumber':customer['cpf'],
                  'birthDate':customer['birth_date'],'fingerprint':fingerprint,'ip':ip},
                  'items':[{'offerId':item['offer_id'],'quantity':1,'offerType':'main'}],'pixExpiresIn':900}
            attempt=self.store.start_attempt(identity,body)
            data=self.store.attempt(attempt)[2]
            try:
                self.outbound();response=self.provider.create_pix(data['request'],attempt)
                order_id=str(uuid.UUID(response['id']))
                # Persist the provider ID before any further validation or lookup.
                data['response']=response
                self.store.save_attempt(attempt,'review',data,order_id)
                self.validate_order(response,order_id,item,customer,attempt,False)
                expiration=timestamp(response.get('pix',{}).get('expirationDate'))
                code=response.get('pix',{}).get('qrCode')
                qr_image(code)
                if not self.security.clock()<expiration<=self.security.clock()+86400:raise ProviderUnavailable()
                self.reconcile(identity,True)
                return self.view(identity)
            except (ProviderUnavailable,ValueError,KeyError,TypeError):
                current=self.store.attempt(attempt)
                self.store.save_attempt(attempt,'uncertain' if not current[1] else 'review',current[2])
                raise HTTPException(503,'Não foi possível confirmar a geração do Pix. Aguarde a conferência antes de tentar uma nova cobrança.') from None

    def view(self,identity):
        contract,item,status,attempt=self.store.installment(identity)
        result={'installment_id':identity,'number':item['number'],'total_installments':len(contract['installments']),
                'amount_cents':item['amount_cents'],'due_date':item['due_date'],'status':status,
                'customer_first_name':contract['customer']['full_name'].split()[0],
                'customer_cpf_suffix':contract['customer']['cpf'][-2:],'pix':None,'can_generate':not bool(attempt)}
        if attempt:
            state,_,data=self.store.attempt(attempt)
            if state in {'uncertain','creating','review'}:result['status']='review'
            pix=data.get('response',{}).get('pix')
            if pix and state=='confirmed' and status=='waiting_payment':
                expires=timestamp(pix.get('expirationDate'))
                if expires>self.security.clock():
                    result['pix']={'code':pix['qrCode'],'qr_data_url':qr_image(pix['qrCode']),
                                   'expires_at':datetime.fromtimestamp(expires,timezone.utc).isoformat()}
                else:result['status']='expired'
            result['can_generate']=status in {'canceled','refused','blocked'} and state=='confirmed'
        return result

    def webhook(self,raw,headers):
        self.available()
        if not self.provider.valid_webhook(raw,headers.get('x-cakto-timestamp',''),headers.get('x-cakto-signature','')):
            raise HTTPException(401,'Assinatura de pagamento inválida.')
        try:
            event=json.loads(raw)
            if not isinstance(event,dict):raise ValueError()
            if not isinstance(event.get('event'),str):raise ValueError()
            if event['event'] not in {'pix_gerado','purchase_approved','refund','chargeback','refund_requested','purchase_refused','purchase_canceled'}:return {'status':'ignored'}
            details=event['data'];order_id=str(uuid.UUID(details['id']))
            if not isinstance(details,dict) or not isinstance(details.get('sck',''),str):raise ValueError()
        except (ValueError,KeyError,TypeError):raise HTTPException(400,'Evento de pagamento inválido.') from None
        digest=self.security.private.digest('cakto-event',hashlib.sha256(raw).hexdigest())
        with self.store.store.connect() as db:
            if db.execute('SELECT 1 FROM billing_events WHERE id=?',(digest,)).fetchone():return {'status':'duplicate'}
            row=db.execute('SELECT installment_id,id,order_id FROM billing_attempts WHERE order_id=? OR id=?',(order_id,details.get('sck',''))).fetchone()
        if not row:return {'status':'ignored'}
        identity,attempt,bound=row
        with self.security.private.locked('bill:'+identity):
            with self.store.store.connect() as db:
                if db.execute('SELECT 1 FROM billing_events WHERE id=?',(digest,)).fetchone():return {'status':'duplicate'}
            contract,item,_,_=self.store.installment(identity)
            self.outbound();order=self.provider.order(order_id)
            self.validate_order(order,order_id,item,contract['customer'],attempt)
            if bound and bound!=order_id:raise HTTPException(409,'Pedido não corresponde à parcela.')
            state,_,data=self.store.attempt(attempt)
            if not bound:
                data['response']={'id':order_id,'pix':details.get('pix')}
                self.store.save_attempt(attempt,'review',data,order_id)
            self.reconcile(identity,True)
            with self.store.store.connect() as db:
                db.execute('INSERT OR IGNORE INTO billing_events VALUES(?,?)',(digest,self.security.clock()))
        return {'status':'received'}

    def install(self,app):
        @app.get('/pagar')
        def page():
            from backend.app import ROOT
            return FileResponse(ROOT/'pagamentos.html',headers={'Cache-Control':'no-store'})

        @app.post('/api/installments/session')
        def exchange(body:Invite,response:Response):
            self.available();cookie,_=self.store.exchange(body.token)
            response.set_cookie(COOKIE,cookie,max_age=1800,secure=True,httponly=True,samesite='strict',path='/')
            return {'status':'authenticated'}

        @app.get('/api/installments/current')
        def current(request:Request):
            self.available();identity,_=self.store.session(request.cookies.get(COOKIE))
            return self.view(identity)

        @app.post('/api/installments/pix')
        def pix(request:Request):
            identity,fingerprint=self.store.session(request.cookies.get(COOKIE))
            self.security.private.limit('bill-pix',identity,5,3600)
            return self.create_pix(identity,fingerprint,request.client.host if request.client else '')

        @app.post('/api/installments/check')
        def check(request:Request):
            self.available();identity,_=self.store.session(request.cookies.get(COOKIE))
            self.security.private.limit('bill-check',identity,10,60)
            self.reconcile(identity);return self.view(identity)

        @app.delete('/api/installments/session')
        def logout(request:Request,response:Response):
            with self.store.store.connect() as db:
                db.execute('DELETE FROM billing_sessions WHERE id=?',(self.security.private.digest('bill-session',request.cookies.get(COOKIE,'')),))
            response.delete_cookie(COOKIE,secure=True,httponly=True,samesite='strict',path='/')
            return {'status':'closed'}

        @app.post('/api/installments/cakto/webhook')
        async def webhook(request:Request):
            return await run_in_threadpool(self.webhook,await request.body(),request.headers)

        @app.get('/api/admin/cakto/status')
        def connection(request:Request):
            _,actor=app.state.require_admin(request)
            if not self.provider.configured:return {'configured':False,'authenticated':False,'can_charge':False}
            self.outbound();result=self.provider.check_connection();self.security.private.audit(actor,'cakto_connection_checked')
            return {'configured':True,**result}

        @app.get('/api/admin/installments')
        def listing(request:Request,page:int=Query(1,ge=1,le=100000),page_size:int=Query(25,ge=1,le=100)):
            _,actor=app.state.require_admin(request)
            with self.store.store.connect() as db:
                base="FROM billing_installments i JOIN billing_contracts c ON c.id=i.contract_id WHERE (c.expires>? OR EXISTS(SELECT 1 FROM billing_installments pending WHERE pending.contract_id=c.id AND pending.status!='paid'))"
                total=db.execute('SELECT count(*) '+base,(self.security.clock(),)).fetchone()[0]
                ids=[r[0] for r in db.execute('SELECT i.id '+base+' ORDER BY c.created DESC,c.id DESC,i.number LIMIT ? OFFSET ?',
                                              (self.security.clock(),page_size,(page-1)*page_size))]
            self.security.private.audit(actor,'installments_listed')
            return {'installments':[{k:v for k,v in self.view(identity).items() if k not in {'pix','can_generate'}} for identity in ids],
                    'page':page,'page_size':page_size,'total':total,'total_pages':max(1,(total+page_size-1)//page_size)}

        @app.post('/api/admin/installments/{identity}/link')
        def link(identity:str,request:Request):
            _,actor=app.state.require_admin(request);self.available()
            token=self.store.link(identity);self.security.private.audit(actor,'installment_link_issued',identity)
            return {'url':self.security.origin+'/pagar#'+token,'expires_in':7*86400}

        @app.post('/api/admin/installments/{identity}/check')
        def review(identity:str,request:Request):
            _,actor=app.state.require_admin(request);self.available();self.reconcile(identity,True)
            self.security.private.audit(actor,'installment_checked',identity)
            return self.view(identity)
