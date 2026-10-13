"""Cakto public API: secrets and authoritative payment checks stay on the server."""
import hashlib
import hmac
import os
import re
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation

import httpx

from backend.providers import ProviderUnavailable

STATUSES = {'waiting_payment','processing','authorized','paid','partially_paid','scheduled','retrying',
            'refund_requested','in_settlement','refunded','refused','blocked','chargedback','prechargeback',
            'in_protest','acquirer_error','canceled','MED'}


def cents(value):
    try:
        if isinstance(value,bool):raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number*100 != (number*100).to_integral_value():
            raise ValueError()
        return int(number*100)
    except (InvalidOperation,ValueError,TypeError):
        raise ProviderUnavailable() from None


class Cakto:
    BASE = 'https://api.cakto.com.br/public_api/'

    def __init__(self,transport=None,clock=time.time):
        self.transport,self.clock = transport,clock
        self.client_id = os.getenv('CAKTO_CLIENT_ID','')
        self.client_secret = os.getenv('CAKTO_CLIENT_SECRET','')
        self.webhook_secret = os.getenv('CAKTO_WEBHOOK_SECRET','')
        self.enabled = os.getenv('CAKTO_PAYMENTS_ENABLED','false') == 'true'
        self.operation_confirmed = os.getenv('CAKTO_LOAN_COLLECTION_APPROVED','false') == 'true'
        self._token,self._expires,self._scopes = '',0,set()
        self._lock = threading.RLock()

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    @property
    def can_charge(self):
        return self.configured and self.enabled and self.operation_confirmed and len(self.webhook_secret)>=16

    def wire(self,method,path,**options):
        # The host is fixed. No caller-controlled URL or redirects carrying credentials.
        if not re.fullmatch(r'[a-z_]+/(?:[a-zA-Z0-9-]+/)?',path):
            raise ProviderUnavailable()
        try:
            with httpx.Client(timeout=20,follow_redirects=False,transport=self.transport) as client:
                with client.stream(method,self.BASE+path,**options) as response:
                    if response.status_code not in {200,201}:
                        if response.status_code==401:self._expires=0
                        raise ProviderUnavailable()
                    body=bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body)>1024*1024:raise ProviderUnavailable()
                    result=__import__('json').loads(body)
            if not isinstance(result,dict):raise ProviderUnavailable()
            return result
        except (httpx.HTTPError,ValueError,TypeError):
            raise ProviderUnavailable() from None

    def headers(self,required):
        if not self.configured:raise ProviderUnavailable()
        with self._lock:
            if self._expires<=self.clock()+60:
                token=self.wire('POST','token/',data={'client_id':self.client_id,'client_secret':self.client_secret})
                if (token.get('token_type')!='Bearer' or not isinstance(token.get('access_token'),str)
                        or not 10<=len(token['access_token'])<=10000 or type(token.get('expires_in')) is not int
                        or not 120<=token['expires_in']<=86400 or not isinstance(token.get('scope'),str)):
                    raise ProviderUnavailable()
                self._token,self._expires = token['access_token'],self.clock()+token['expires_in']
                self._scopes=set(token['scope'].split())
            if not set(required).issubset(self._scopes):raise ProviderUnavailable()
            return {'Authorization':'Bearer '+self._token}

    def check_connection(self):
        self.headers({'read','orders','offers'})
        return {'authenticated':True,'can_charge':bool(self.can_charge and {'write','payments'}.issubset(self._scopes))}

    def offer(self,identity):
        if not re.fullmatch(r'[A-Za-z0-9-]{1,80}',identity):raise ProviderUnavailable()
        return self.wire('GET','offers/'+identity+'/',headers=self.headers({'read','offers'}))

    def create_pix(self,body,request_id):
        if not self.can_charge:raise ProviderUnavailable()
        uuid.UUID(request_id)
        return self.wire('POST','payments/',headers={**self.headers({'write','payments'}),'X-Idempotency-Key':request_id},json=body)

    def order(self,identity):
        try:identity=str(uuid.UUID(identity))
        except (ValueError,TypeError,AttributeError):raise ProviderUnavailable() from None
        return self.wire('GET','orders/'+identity+'/',headers=self.headers({'read','orders'}))

    def valid_webhook(self,raw,timestamp,signature):
        if len(self.webhook_secret)<16 or not re.fullmatch(r'\d{10,12}',timestamp or ''):return False
        if abs(self.clock()-int(timestamp))>300:return False
        expected='v1='+hmac.new(self.webhook_secret.encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(s.strip(),expected) for s in (signature or '').split(','))
