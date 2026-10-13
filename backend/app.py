"""API para a futura VPS. Não é executada no GitHub Pages ou no Sites."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, StringConstraints, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
BANKS = {'237', '104', '341', '033', '001', '323', '260'}
STATES = set('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split())

def valid_cpf(s):
    if not re.fullmatch(r'\d{11}', s) or len(set(s)) == 1:
        return False
    return all((sum(int(s[i]) * (n + 1 - i) for i in range(n)) * 10 % 11) % 10 == int(s[n]) for n in (9, 10))

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Applicant(Model):
    full_name: StrictStr = Field(min_length=3, max_length=150)
    birth_date: StrictStr
    email: StrictStr = Field(max_length=254)
    cpf: StrictStr
    phone: StrictStr

    @model_validator(mode='after')
    def validate_applicant(self):
        if len(self.full_name.split()) < 2 or not all(c.isalpha() or c in " '-’." for c in self.full_name):
            raise ValueError('Invalid name')
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', self.birth_date):
                raise ValueError('Invalid birth date')
            birth = date.fromisoformat(self.birth_date)
        except ValueError:
            raise ValueError('Invalid birth date') from None
        today = date.today()
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        if birth.year < 1900 or age < 18:
            raise ValueError('Applicant must be adult')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', self.email) or not valid_cpf(self.cpf) or not re.fullmatch(r'[1-9]\d9\d{8}', self.phone):
            raise ValueError('Invalid applicant details')
        self.email = self.email.lower()
        return self

class Address(Model):
    postal_code: StrictStr = Field(pattern=r'^\d{8}$')
    street: StrictStr = Field(min_length=1, max_length=150)
    number: StrictStr = Field(min_length=1, max_length=20)
    complement: StrictStr = Field(max_length=100)
    district: StrictStr = Field(min_length=1, max_length=100)
    city: StrictStr = Field(min_length=1, max_length=100)
    state: StrictStr

    @field_validator('state')
    @classmethod
    def validate_state(cls, value):
        if value not in STATES:
            raise ValueError('Invalid state')
        return value

class Income(Model):
    monthly_income: float = Field(strict=True, ge=1200, le=100000000, allow_inf_nan=False)
    occupation: StrictStr

    @field_validator('occupation')
    @classmethod
    def validate_occupation(cls, value):
        if value not in {'employed', 'self_employed', 'business_owner', 'retired', 'other'}:
            raise ValueError('Invalid occupation')
        return value

class BankAccount(Model):
    bank: StrictStr
    account_type: StrictStr
    branch: StrictStr = Field(pattern=r'^\d{1,4}(?:-[\dX])?$')
    account: StrictStr = Field(pattern=r'^\d{1,16}-[\dX]$')

    @model_validator(mode='after')
    def validate_bank(self):
        if self.bank not in BANKS or self.account_type not in {'checking', 'savings', 'payment'}:
            raise ValueError('Invalid bank account')
        return self

class Loan(Model):
    amount_requested: StrictInt
    installments_requested: StrictInt
    installment_frequency: StrictStr

    @model_validator(mode='after')
    def validate_loan(self):
        if not 500 <= self.amount_requested <= 3500 or (self.amount_requested - 500) % 250 or self.installments_requested not in {3, 6, 9, 12} or self.installment_frequency != 'weekly':
            raise ValueError('Invalid loan selection')
        return self

class Consents(Model):
    marketing: StrictBool
    financial_history: StrictBool
    terms: StrictBool
    privacy: StrictBool
    version: StrictStr

    @model_validator(mode='after')
    def validate_consents(self):
        if not self.financial_history or not self.terms or not self.privacy or self.version != '2026-10-13':
            raise ValueError('Required consents missing')
        return self

class Application(Model):
    schema_version: StrictInt
    applicant: Applicant
    password: Annotated[StrictStr, StringConstraints(strip_whitespace=False)] = Field(min_length=7, max_length=128, repr=False)
    address: Address
    income: Income
    bank_account: BankAccount
    loan: Loan
    consents: Consents

    @model_validator(mode='after')
    def validate_version(self):
        if self.schema_version != 1 or not self.password.strip():
            raise ValueError('Invalid application')
        return self

class Store:
    def __init__(self, path, key):
        self.path = Path(path)
        self.key = key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS applications (
                application_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                request_digest TEXT NOT NULL, cpf_digest TEXT UNIQUE NOT NULL,
                email_digest TEXT UNIQUE NOT NULL, nonce BLOB NOT NULL, ciphertext BLOB NOT NULL,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL)''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def digest(self, purpose, value):
        return hmac.new(self.key, (purpose + '\0' + value).encode(), hashlib.sha256).hexdigest()

    def save(self, application, request_key):
        payload = application.model_dump()
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        fingerprint = self.digest('request', canonical)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT application_id, request_digest FROM applications WHERE request_key = ?', (request_key,)).fetchone()
            if prior:
                if not hmac.compare_digest(prior[1], fingerprint):
                    raise HTTPException(409, 'Não foi possível receber esta solicitação. Revise os dados.')
                return prior[0]
            application_id = str(uuid.uuid4())
            password = payload.pop('password')
            salt = secrets.token_bytes(16)
            derived = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64*1024*1024, dklen=32)
            password_hash = 'scrypt$32768$8$1$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(derived).decode()
            created_at = datetime.now(timezone.utc).isoformat()
            payload['received_at'] = created_at
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(self.key).encrypt(nonce, json.dumps(payload, ensure_ascii=False).encode(), application_id.encode())
            try:
                db.execute('INSERT INTO applications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (application_id, request_key, fingerprint, self.digest('cpf', application.applicant.cpf), self.digest('email', application.applicant.email), nonce, ciphertext, password_hash, created_at))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'Não foi possível receber esta solicitação. Revise os dados.') from None
            return application_id

def create_app(store=None, origin=None, legal_ready=None):
    origin = origin or os.getenv('PUBLIC_ORIGIN', '')
    ready = legal_ready if legal_ready is not None else os.getenv('APPLICATIONS_ENABLED', 'false') == 'true'
    limits, lock = {}, threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if not store:
            try:
                key = base64.b64decode(os.environ['DATA_ENCRYPTION_KEY'], validate=True)
                if len(key) != 32:
                    raise ValueError('Invalid key size')
            except (KeyError, ValueError):
                raise RuntimeError('Configure DATA_ENCRYPTION_KEY with a base64-encoded 32-byte key.') from None
            app.state.store = Store(os.getenv('APPLICATIONS_DB', str(ROOT/'var/applications.sqlite')), key)
        else:
            app.state.store = store
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail':'Confira os campos obrigatórios e os dados da solicitação.'}, status_code=422)

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.url.path == '/api/applications':
            if not ready or not origin:
                return JSONResponse({'detail':'Recebimento temporariamente indisponível.'}, status_code=503)
            if request.headers.get('origin') != origin:
                return JSONResponse({'detail':'Origem não permitida.'}, status_code=403)
            if request.method != 'POST' or request.headers.get('content-type','').split(';')[0].strip().lower() != 'application/json':
                return JSONResponse({'detail':'Use POST com JSON.'}, status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 16384:
                    return JSONResponse({'detail':'Solicitação muito grande.'}, status_code=413)
            request._body = bytes(body)
            now = time.monotonic()
            ip = request.client.host if request.client else 'unknown'
            with lock:
                for k in list(limits):
                    if not limits[k] or limits[k][-1] < now-600:
                        del limits[k]
                bucket = limits.setdefault(ip, deque())
                while bucket and bucket[0] < now-600:
                    bucket.popleft()
                if len(bucket) >= 10 or len(limits) > 10000:
                    return JSONResponse({'detail':'Aguarde antes de tentar novamente.'}, status_code=429, headers={'Retry-After':'600'})
                bucket.append(now)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/api/applications', status_code=201)
    def receive(application: Application, request: Request):
        try:
            request_key = str(uuid.UUID(request.headers.get('idempotency-key', '')))
        except ValueError:
            raise HTTPException(400, 'Informe uma chave de envio válida.') from None
        application_id = request.app.state.store.save(application, request_key)
        return {'application_id':application_id, 'status':'received'}

    @app.get('/api/health')
    def health():
        return {'status':'ok', 'receiving':bool(ready and origin)}

    @app.get('/')
    def home():
        return FileResponse(ROOT/'index.html')

    app.mount('/assets', StaticFiles(directory=ROOT/'assets'), name='assets')
    return app

app = create_app()
