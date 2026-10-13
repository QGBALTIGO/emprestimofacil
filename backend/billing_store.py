import json
import os
import secrets
import uuid

from fastapi import HTTPException


class BillingStore:
    def __init__(self,private):
        self.private,self.store,self.clock = private,private.store,private.clock
        self.retention=int(os.getenv('PAYMENT_RETENTION_DAYS','365'))
        if not 180<=self.retention<=3650:raise ValueError('PAYMENT_RETENTION_DAYS must be 180–3650.')
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS billing_contracts(id TEXT PRIMARY KEY,digest TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS billing_installments(id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,number INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'due',attempt TEXT,UNIQUE(contract_id,number));
                CREATE TABLE IF NOT EXISTS billing_attempts(id TEXT PRIMARY KEY,installment_id TEXT NOT NULL,created REAL NOT NULL,state TEXT NOT NULL,order_id TEXT UNIQUE,payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS billing_links(id TEXT PRIMARY KEY,installment_id TEXT NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS billing_sessions(id TEXT PRIMARY KEY,installment_id TEXT NOT NULL,expires REAL NOT NULL,fingerprint TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS billing_events(id TEXT PRIMARY KEY,created REAL NOT NULL);
            ''')

    def import_contract(self,model,retention_days=None):
        retention_days=self.retention if retention_days is None else retention_days
        if not 180<=retention_days<=3650:raise ValueError('PAYMENT_RETENTION_DAYS must be 180–3650.')
        content=model.model_dump(mode='json');identity=str(model.id)
        digest=self.private.digest('contract',json.dumps(content,sort_keys=True,separators=(',', ':')))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT digest FROM billing_contracts WHERE id=?',(identity,)).fetchone()
            if row:
                if row[0]!=digest:raise ValueError('Existing contracts are immutable; do not change a debt silently.')
                return identity
            db.execute('INSERT INTO billing_contracts VALUES(?,?,?,?,?)',(identity,digest,self.clock(),self.clock()+retention_days*86400,self.private.seal('contract:'+identity,content)))
            for item in content['installments']:
                db.execute('INSERT INTO billing_installments(id,contract_id,number) VALUES(?,?,?)',(str(uuid.uuid4()),identity,item['number']))
        self.private.audit('operator','contract_imported',identity)
        return identity

    def installment(self,identity):
        with self.store.connect() as db:
            row=db.execute('SELECT i.contract_id,i.number,i.status,i.attempt,c.payload,c.expires FROM billing_installments i JOIN billing_contracts c ON c.id=i.contract_id WHERE i.id=?',(identity,)).fetchone()
        if not row:raise HTTPException(404,'Parcela não encontrada.')
        contract=self.private.unseal('contract:'+row[0],row[4])
        item=next(i for i in contract['installments'] if i['number']==row[1])
        return contract,item,row[2],row[3]

    def link(self,identity):
        self.installment(identity);token=secrets.token_urlsafe(32)
        with self.store.connect() as db:
            db.execute('DELETE FROM billing_links WHERE installment_id=?',(identity,))
            db.execute('DELETE FROM billing_sessions WHERE installment_id=?',(identity,))
            db.execute('INSERT INTO billing_links VALUES(?,?,?)',(self.private.digest('bill-link',token),identity,self.clock()+7*86400))
        return token

    def exchange(self,token):
        with self.store.connect() as db:
            row=db.execute('SELECT installment_id,expires FROM billing_links WHERE id=?',(self.private.digest('bill-link',token),)).fetchone()
        if not row or row[1]<=self.clock():raise HTTPException(401,'Link inválido ou expirado. Solicite um novo link ao atendimento.')
        self.installment(row[0]);cookie=secrets.token_urlsafe(32);key=self.private.digest('bill-session',cookie)
        with self.store.connect() as db:
            db.execute('INSERT INTO billing_sessions VALUES(?,?,?,?)',(key,row[0],min(row[1],self.clock()+1800),secrets.token_urlsafe(24)))
        return cookie,row[0]

    def session(self,token):
        with self.store.connect() as db:
            row=db.execute('SELECT installment_id,expires,fingerprint FROM billing_sessions WHERE id=?',(self.private.digest('bill-session',token or ''),)).fetchone()
        if not row or row[1]<=self.clock():raise HTTPException(401,'Sessão expirada. Abra novamente o link da parcela.')
        self.installment(row[0]);return row[0],row[2]

    def attempt(self,identity):
        with self.store.connect() as db:
            row=db.execute('SELECT state,order_id,payload FROM billing_attempts WHERE id=?',(identity,)).fetchone()
        return (row[0],row[1],self.private.unseal('payment:'+identity,row[2])) if row else None

    def save_attempt(self,identity,state,data,order_id=None):
        with self.store.connect() as db:
            db.execute('UPDATE billing_attempts SET state=?,order_id=COALESCE(?,order_id),payload=? WHERE id=?',(state,order_id,self.private.seal('payment:'+identity,data),identity))

    def start_attempt(self,installment_id,payload):
        identity=str(uuid.uuid4())
        payload['metadata']={'sck':identity}
        with self.store.connect() as db:
            db.execute('INSERT INTO billing_attempts VALUES(?,?,?,?,?,?)',(identity,installment_id,self.clock(),'creating',None,self.private.seal('payment:'+identity,{'request':payload})))
            db.execute('UPDATE billing_installments SET attempt=? WHERE id=?',(identity,installment_id))
        return identity

    def purge(self):
        with self.store.connect() as db:
            db.execute('DELETE FROM billing_sessions WHERE expires<=?',(self.clock(),))
            db.execute('DELETE FROM billing_links WHERE expires<=?',(self.clock(),))
            db.execute('DELETE FROM billing_events WHERE created<=?',(self.clock()-30*86400,))
            ids=[r[0] for r in db.execute("SELECT id FROM billing_contracts c WHERE expires<=? AND NOT EXISTS(SELECT 1 FROM billing_installments i WHERE i.contract_id=c.id AND i.status!='paid')",(self.clock(),))]
            for identity in ids:
                children=[r[0] for r in db.execute('SELECT id FROM billing_installments WHERE contract_id=?',(identity,))]
                for child in children:
                    for table in ['billing_attempts','billing_links','billing_sessions']:
                        db.execute('DELETE FROM '+table+' WHERE installment_id=?',(child,))
                db.execute('DELETE FROM billing_installments WHERE contract_id=?',(identity,))
                db.execute('DELETE FROM billing_contracts WHERE id=?',(identity,))
        return len(ids)
