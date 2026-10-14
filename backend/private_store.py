"""Sessões opacas, dados cifrados e trilha de acesso sem dados pessoais."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException


def password_hash(password):
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64*1024*1024, dklen=32)
    return 'scrypt$32768$8$1$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(key).decode()


def password_matches(password, encoded):
    try:
        _, n, r, p, salt, expected = encoded.split('$')
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                maxmem=64*1024*1024, dklen=32)
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


class PrivateStore:
    def __init__(self, store, clock=time.time):
        self.store, self.clock, self.key = store, clock, store.key
        self.files_dir = store.path.parent / 'documents'
        self.files_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.files_dir, 0o700)
        self._locks, self._lock_guard = weakref.WeakValueDictionary(), threading.Lock()
        with store.connect() as db:
            db.executescript('''
                PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS secure_sessions (id TEXT PRIMARY KEY, expires REAL NOT NULL, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS security_limits (id TEXT PRIMARY KEY, start REAL NOT NULL, count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS bank_attempts (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, nonce TEXT UNIQUE NOT NULL,
                    expires REAL NOT NULL, link TEXT UNIQUE, resources TEXT NOT NULL DEFAULT '[]', consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS webhook_events (id TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pf_records (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL,
                    digest TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS private_files (id TEXT PRIMARY KEY, record_id TEXT NOT NULL, kind TEXT NOT NULL,
                    extension TEXT NOT NULL, size INTEGER NOT NULL, UNIQUE(record_id,kind));
                CREATE TABLE IF NOT EXISTS administrators (username TEXT PRIMARY KEY, password TEXT NOT NULL,
                    totp BLOB NOT NULL, last_step INTEGER NOT NULL DEFAULT -1);
                CREATE TABLE IF NOT EXISTS admin_sessions (id TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS client_sessions (id TEXT PRIMARY KEY, application_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS application_reviews (kind TEXT NOT NULL, identity TEXT NOT NULL,
                    state TEXT NOT NULL, updated REAL NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(kind,identity));
                CREATE TABLE IF NOT EXISTS admin_bootstrap (id TEXT PRIMARY KEY, username TEXT NOT NULL,
                    expires REAL NOT NULL, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS access_audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, object TEXT NOT NULL, previous TEXT NOT NULL, mac TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS provider_deletions (link TEXT PRIMARY KEY, created REAL NOT NULL);
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(pf_records)')}
            if 'owner_digest' not in columns or 'owner_email_digest' not in columns:
                if 'owner_digest' not in columns:db.execute("ALTER TABLE pf_records ADD COLUMN owner_digest TEXT NOT NULL DEFAULT ''")
                if 'owner_email_digest' not in columns:db.execute("ALTER TABLE pf_records ADD COLUMN owner_email_digest TEXT NOT NULL DEFAULT ''")
                for identity,payload in db.execute('SELECT id,payload FROM pf_records').fetchall():
                    applicant=self.unseal('pf:'+identity,payload)['applicant']
                    attestations=self.unseal('pf:'+identity,payload).get('server_attestations',{})
                    owner=self.digest('pf-phone',applicant['phone']) if 'sms' in attestations.get('contact_verified_at',{}) else ''
                    email=self.digest('pf-email',applicant['email'])
                    db.execute('UPDATE pf_records SET owner_digest=?,owner_email_digest=? WHERE id=?',(owner,email,identity))
            db.execute('CREATE INDEX IF NOT EXISTS pf_owner ON pf_records(owner_digest,created)')
            db.execute('CREATE INDEX IF NOT EXISTS pf_email_owner ON pf_records(owner_email_digest,created)')
        self.storage_max=int(os.getenv('MAX_PRIVATE_STORAGE_MB','1024'))*1024*1024
        self.disk_reserve=int(os.getenv('MIN_FREE_DISK_MB','256'))*1024*1024
        self.pf_daily_limit=int(os.getenv('PF_DAILY_LIMIT','3'))
        if not 32*1024*1024<=self.storage_max<=102400*1024*1024 or not 16*1024*1024<=self.disk_reserve<=102400*1024*1024 or not 1<=self.pf_daily_limit<=10:
            raise RuntimeError('Configure limites válidos para armazenamento e envio PF.')

    @contextmanager
    def locked(self, key):
        with self._lock_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
        with lock:
            yield

    def digest(self, purpose, value):
        return self.store.digest('security:' + purpose, str(value))

    def seal(self, purpose, value):
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, raw, purpose.encode())

    def unseal(self, purpose, value):
        return json.loads(AESGCM(self.key).decrypt(value[:12], value[12:], purpose.encode()))

    def limit(self, purpose, subject, count, seconds):
        key, now = self.digest('limit:' + purpose, subject), self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM security_limits WHERE start < ?', (now-86400,))
            row = db.execute('SELECT start,count FROM security_limits WHERE id=?', (key,)).fetchone()
            if not row or row[0] <= now-seconds:
                db.execute('INSERT OR REPLACE INTO security_limits VALUES (?,?,1)', (key,now))
            elif row[1] >= count:
                raise HTTPException(429, 'Aguarde antes de tentar novamente.', headers={'Retry-After':str(max(1,int(seconds-(now-row[0]))))})
            else:
                db.execute('UPDATE security_limits SET count=count+1 WHERE id=?', (key,))

    def new_session(self, profile):
        token = secrets.token_urlsafe(32)
        key, now = self.digest('session', token), self.clock()
        state = {'profile':profile, 'verified':{}, 'challenges':{}, 'bank':{'status':'not_connected'}, 'created_at':now}
        self.save_session(key, state, now+7200)
        return token, key, state

    def session(self, token):
        if not token or len(token) > 100:
            raise HTTPException(401, 'Sua sessão expirou. Confirme seus contatos novamente.')
        key = self.digest('session', token)
        with self.store.connect() as db:
            row = db.execute('SELECT expires,payload FROM secure_sessions WHERE id=?', (key,)).fetchone()
        if not row or row[0] <= self.clock():
            raise HTTPException(401, 'Sua sessão expirou. Confirme seus contatos novamente.')
        return key, self.unseal('session:'+key,row[1]), row[0]

    def save_session(self, key, state, expiry):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO secure_sessions VALUES (?,?,?)', (key,expiry,self.seal('session:'+key,state)))

    def audit(self, actor, action, obj=''):
        now = self.clock()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT mac FROM access_audit ORDER BY seq DESC LIMIT 1').fetchone()
            previous = row[0] if row else ''
            encoded = json.dumps([now,actor,action,obj,previous],separators=(',', ':'))
            mac = self.digest('audit',encoded)
            db.execute('INSERT INTO access_audit(at,actor,action,object,previous,mac) VALUES (?,?,?,?,?,?)',
                       (now,actor,action,obj,previous,mac))

    def create_pf(self, sid, model, request_id, attestations, retention_days):
        content = model.model_dump(mode='json')
        fingerprint = self.digest('pf-request',json.dumps(content,sort_keys=True,separators=(',', ':'),ensure_ascii=False))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,session_id,digest,status FROM pf_records WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if row[1] != sid or not hmac.compare_digest(row[2],fingerprint):
                    raise HTTPException(409, 'Revise os dados antes de tentar novamente.')
                return row[0], row[3]
            identity = str(uuid.uuid4())
            content['server_attestations'] = attestations
            content['required_files'] = model.required_files()
            now = self.clock()
            applicant=content['applicant']
            owner=self.digest('pf-phone',applicant['phone']) if 'sms' in attestations.get('contact_verified_at',{}) else ''
            email=self.digest('pf-email',applicant['email'])
            if db.execute("SELECT 1 FROM pf_records WHERE ((owner_digest<>'' AND owner_digest=?) OR owner_email_digest=?) AND status='uploading' AND expires>? AND created>?",
                          (owner,email,now,now-86400)).fetchone():
                raise HTTPException(409,'Já existe um cadastro documental em andamento. Retome o envio anterior.')
            if db.execute("SELECT count(*) FROM pf_records WHERE ((owner_digest<>'' AND owner_digest=?) OR owner_email_digest=?) AND created>?",(owner,email,now-86400)).fetchone()[0]>=self.pf_daily_limit:
                raise HTTPException(429,'Limite diário de cadastros documentais atingido. Tente novamente amanhã.',headers={'Retry-After':'86400'})
            db.execute('INSERT INTO pf_records(id,session_id,request_id,digest,status,created,expires,payload,owner_digest,owner_email_digest) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (identity,sid,request_id,fingerprint,'uploading',now,now+retention_days*86400,
                        self.seal('pf:'+identity,content),owner,email))
        self.audit(sid,'pf_started',identity)
        return identity, 'uploading'

    def pf(self, identity, sid=None):
        with self.store.connect() as db:
            row = db.execute('SELECT session_id,status,payload,expires,created FROM pf_records WHERE id=?', (identity,)).fetchone()
        if not row or row[3] <= self.clock() or row[1]=='uploading' and row[4]<=self.clock()-86400 or sid is not None and row[0] != sid:
            raise HTTPException(404, 'Cadastro não encontrado.')
        return self.unseal('pf:'+identity,row[2]), row[1]

    def save_file(self, record, kind, raw, extension):
        with self.locked('private-storage'):
            self._save_file(record,kind,raw,extension)

    def _save_file(self, record, kind, raw, extension):
        import shutil
        if shutil.disk_usage(self.files_dir).free < self.disk_reserve+len(raw)*2:
            raise HTTPException(507,'Armazenamento temporariamente indisponível. Tente novamente mais tarde.')
        with self.store.connect() as db:
            total=db.execute('SELECT COALESCE(sum(size),0) FROM private_files').fetchone()[0]
            prior=db.execute('SELECT size FROM private_files WHERE record_id=? AND kind=?',(record,kind)).fetchone()
        if total-(prior[0] if prior else 0)+len(raw)>self.storage_max:
            raise HTTPException(507,'Armazenamento temporariamente indisponível. Tente novamente mais tarde.')
        identity, nonce = str(uuid.uuid4()), secrets.token_bytes(12)
        path = self.files_dir / (identity+'.enc')
        aad = ('file:'+record+':'+kind+':'+identity).encode()
        ciphertext = nonce + AESGCM(self.key).encrypt(nonce,raw,aad)
        with open(path,'xb') as out:
            os.chmod(path,0o600)
            out.write(ciphertext)
            out.flush()
            os.fsync(out.fileno())
        old = None
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                record_state=db.execute('SELECT status,expires,created FROM pf_records WHERE id=?',(record,)).fetchone()
                if not record_state or record_state[1]<=self.clock() or record_state[0]=='uploading' and record_state[2]<=self.clock()-86400:raise HTTPException(404,'Cadastro não encontrado.')
                if record_state[0]!='uploading':raise HTTPException(409,'Cadastro já concluído.')
                old = db.execute('SELECT id FROM private_files WHERE record_id=? AND kind=?', (record,kind)).fetchone()
                db.execute('INSERT OR REPLACE INTO private_files VALUES (?,?,?,?,?)', (identity,record,kind,extension,len(raw)))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if old:
            (self.files_dir/(old[0]+'.enc')).unlink(missing_ok=True)

    def file(self, record, kind):
        with self.store.connect() as db:
            row = db.execute('SELECT id,extension FROM private_files WHERE record_id=? AND kind=?', (record,kind)).fetchone()
        if not row:
            raise HTTPException(404,'Arquivo não encontrado.')
        raw = (self.files_dir/(row[0]+'.enc')).read_bytes()
        aad = ('file:'+record+':'+kind+':'+row[0]).encode()
        return AESGCM(self.key).decrypt(raw[:12],raw[12:],aad), row[1]

    def finalize(self, identity, sid):
        data, status = self.pf(identity,sid)
        with self.store.connect() as db:
            available = {r[0] for r in db.execute('SELECT kind FROM private_files WHERE record_id=?',(identity,))}
            if not set(data['required_files']).issubset(available):
                raise HTTPException(409,'Adicione todos os arquivos obrigatórios antes de concluir.')
            db.execute("UPDATE pf_records SET status='received' WHERE id=?",(identity,))
        if status != 'received':
            self.audit(sid,'pf_received',identity)

    def delete_pf(self, identity, actor):
        with self.locked('pf:'+identity),self.locked('private-storage'):
            self._delete_pf(identity,actor)

    def _delete_pf(self, identity, actor):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            paths = [self.files_dir/(r[0]+'.enc') for r in db.execute('SELECT id FROM private_files WHERE record_id=?',(identity,))]
            db.execute('DELETE FROM private_files WHERE record_id=?',(identity,))
            db.execute('DELETE FROM pf_records WHERE id=?',(identity,))
            db.execute("DELETE FROM application_reviews WHERE kind='pf' AND identity=?",(identity,))
        for path in paths:
            path.unlink(missing_ok=True)
        self.audit(actor,'pf_deleted',identity)

    def queue_delete(self, link):
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO provider_deletions VALUES (?,?)',(link,self.clock()))

    def expire_session(self, key):
        # A provider call can be in progress; serialize revocation with its write.
        with self.locked(key):
            self._expire_session(key)

    def _expire_session(self, key):
        with self.store.connect() as db:
            links = [r[0] for r in db.execute('SELECT link FROM bank_attempts WHERE session_id=? AND link IS NOT NULL',(key,))]
            for link in links:
                db.execute('INSERT OR IGNORE INTO provider_deletions VALUES (?,?)',(link,self.clock()))
            row = db.execute('SELECT payload FROM secure_sessions WHERE id=?',(key,)).fetchone()
            if row:
                state = self.unseal('session:'+key,row[0])
                if state.get('bank',{}).get('link'):
                    db.execute('INSERT OR IGNORE INTO provider_deletions VALUES (?,?)',(state['bank']['link'],self.clock()))
            db.execute('DELETE FROM secure_sessions WHERE id=?',(key,))
            db.execute('DELETE FROM bank_attempts WHERE session_id=?',(key,))

    def purge(self, retention_days):
        now = self.clock()
        with self.store.connect() as db:
            expired_pf = [r[0] for r in db.execute('SELECT id FROM pf_records WHERE expires<=? OR (status=? AND created<=?)',
                                                  (now,'uploading',now-86400))]
            sessions = [r[0] for r in db.execute('SELECT id FROM secure_sessions WHERE expires<=?',(now,))]
        for identity in expired_pf:
            self.delete_pf(identity,'retention')
        for key in sessions:
            self.expire_session(key)
        from datetime import datetime, timezone
        cutoff = datetime.fromtimestamp(now-retention_days*86400,timezone.utc).isoformat()
        with self.store.connect() as db:
            db.execute('DELETE FROM applications WHERE created_at<=?',(cutoff,))
            db.execute('DELETE FROM client_sessions WHERE expires<=? OR application_id NOT IN (SELECT application_id FROM applications)',(now,))
            db.execute("DELETE FROM application_reviews WHERE kind='initial' AND identity NOT IN (SELECT application_id FROM applications)")
            for row in db.execute('SELECT link FROM bank_attempts WHERE expires<=? AND consumed=0 AND link IS NOT NULL',(now,)):
                db.execute('INSERT OR IGNORE INTO provider_deletions VALUES (?,?)',(row[0],now))
            db.execute('DELETE FROM bank_attempts WHERE expires<=?',(now,))
            db.execute('DELETE FROM admin_sessions WHERE expires<=?',(now,))
            db.execute('DELETE FROM admin_bootstrap WHERE expires<=?',(now,))
            db.execute('DELETE FROM security_limits WHERE start<=?',(now-86400,))
            db.execute('DELETE FROM webhook_events WHERE expires<=?',(now,))
            known = {r[0]+'.enc' for r in db.execute('SELECT id FROM private_files')}
        for path in self.files_dir.glob('*.enc'):
            if path.name not in known and path.stat().st_mtime < now-86400:
                path.unlink(missing_ok=True)
        return {'pf_removed':len(expired_pf),'sessions_removed':len(sessions)}
