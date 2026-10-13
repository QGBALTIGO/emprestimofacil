"""Executar na VPS: cadastro do administrador, retenção e backup privado."""
import argparse
import base64
import getpass
import hmac
import json
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyotp

from backend.app import Store
from backend.private_store import PrivateStore, password_hash
from backend.providers import ProviderUnavailable, Providers


def configured_store():
    try:
        key = base64.b64decode(os.environ['DATA_ENCRYPTION_KEY'],validate=True)
        if len(key) != 32:
            raise ValueError()
        return PrivateStore(Store(os.environ['APPLICATIONS_DB'],key))
    except (KeyError,ValueError):
        raise SystemExit('Configure APPLICATIONS_DB e DATA_ENCRYPTION_KEY no ambiente privado do serviço.') from None


def backup(private, destination):
    destination = Path(destination)
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(destination,0o700)
    for attempt in range(3):
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(uuid.uuid4())
        work = destination/('.partial-'+name)
        work.mkdir(mode=0o700)
        try:
            db_path = work/'applications.sqlite'
            with private.store.connect() as source, sqlite3.connect(db_path) as target:
                source.backup(target)
            os.chmod(db_path,0o600)
            files = work/'documents';files.mkdir(mode=0o700)
            with sqlite3.connect(db_path) as db:
                identities = [r[0] for r in db.execute('SELECT id FROM private_files')]
            for identity in identities:
                target = files/(identity+'.enc')
                shutil.copyfile(private.files_dir/(identity+'.enc'),target)
                os.chmod(target,0o600)
            final = destination/name
            work.rename(final)
            return final
        except FileNotFoundError:
            shutil.rmtree(work)
        except Exception:
            shutil.rmtree(work)
            raise
    raise RuntimeError('Arquivos mudaram durante o backup. Tente novamente.')


def restore(private, source):
    """Restore to a new destination only; the service must be stopped by the operator."""
    source = Path(source)
    with sqlite3.connect(source/'applications.sqlite') as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup inválido.')
        rows = db.execute('SELECT id,record_id,kind FROM private_files').fetchall()
        # Validate the key even when a backup contains no document files.
        encrypted = []
        for table,prefix in [('pf_records','pf:'),('secure_sessions','session:'),('administrators','totp:')]:
            identity_column = 'username' if table == 'administrators' else 'id'
            data_column = 'totp' if table == 'administrators' else 'payload'
            encrypted.extend((prefix+identity,content) for identity,content in db.execute(
                'SELECT '+identity_column+','+data_column+' FROM '+table))
        applications = db.execute('SELECT application_id,nonce,ciphertext FROM applications').fetchall()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    for purpose,content in encrypted:
        private.unseal(purpose,content)
    for identity,nonce,content in applications:
        AESGCM(private.key).decrypt(nonce,content,identity.encode())
    for identity,record,kind in rows:
        content = (source/'documents'/(identity+'.enc')).read_bytes()
        AESGCM(private.key).decrypt(content[:12],content[12:],('file:'+record+':'+kind+':'+identity).encode())
    with private.store.connect() as db:
        counts = sum(db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                     for table in ['applications','pf_records','administrators','secure_sessions'])
    if counts:
        raise ValueError('Restaure em um diretório novo e vazio, com o serviço parado.')
    shutil.copyfile(source/'applications.sqlite',private.store.path)
    os.chmod(private.store.path,0o600)
    for identity,_,_ in rows:
        target = private.files_dir/(identity+'.enc')
        shutil.copyfile(source/'documents'/(identity+'.enc'),target)
        os.chmod(target,0o600)
    # Restored bearer sessions must not regain access after a restore.
    # Queue their known bank links for revocation instead of losing the consent references.
    with private.store.connect() as db:
        links = {r[0] for r in db.execute('SELECT link FROM bank_attempts WHERE link IS NOT NULL')}
        for identity,content in db.execute('SELECT id,payload FROM secure_sessions'):
            link = private.unseal('session:'+identity,content).get('bank',{}).get('link')
            if link:
                links.add(link)
        for link in links:
            db.execute('INSERT OR IGNORE INTO provider_deletions VALUES (?,?)',(link,private.clock()))
        db.execute('DELETE FROM secure_sessions');db.execute('DELETE FROM admin_sessions')
        db.execute('DELETE FROM bank_attempts')


def purge(private, days):
    stats = private.purge(days)
    provider = Providers()
    with private.store.connect() as db:
        links = [r[0] for r in db.execute('SELECT link FROM provider_deletions ORDER BY created LIMIT 100')]
    confirmed = 0
    for link in links:
        try:
            provider.bank_delete(link)
        except ProviderUnavailable:
            continue
        with private.store.connect() as db:
            db.execute('DELETE FROM provider_deletions WHERE link=?',(link,))
        confirmed += 1
    stats.update(provider_deleted=confirmed,provider_pending=len(links)-confirmed)
    return stats


def create_administrator(private,username,password,secret):
    if not 3 <= len(username) <= 80 or not 12 <= len(password) <= 128 or len(secret) < 32:
        raise ValueError('Use usuário de 3–80 caracteres, senha de 12–128 e segredo TOTP válido.')
    with private.store.connect() as db:
        db.execute('INSERT INTO administrators(username,password,totp) VALUES (?,?,?)',
                   (username,password_hash(password),private.seal('totp:'+username,{'secret':secret})))
    private.audit(private.digest('admin-actor',username),'admin_created')


def audit_valid(private):
    previous = ''
    with private.store.connect() as db:
        rows = db.execute('SELECT at,actor,action,object,previous,mac FROM access_audit ORDER BY seq').fetchall()
    for at,actor,action,obj,prior,mac in rows:
        encoded = json.dumps([at,actor,action,obj,prior],separators=(',', ':'))
        if prior != previous or not hmac.compare_digest(mac,private.digest('audit',encoded)):
            return False
        previous = mac
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['admin-create','purge','backup','restore','audit-check'])
    parser.add_argument('--directory')
    args = parser.parse_args()
    private = configured_store()
    if args.command == 'admin-create':
        username = input('Usuário da equipe: ').strip()
        password = getpass.getpass('Senha (mínimo 12 caracteres): ')
        if password != getpass.getpass('Confirme a senha: '):
            raise SystemExit('As senhas precisam ser iguais.')
        secret = pyotp.random_base32()
        print('Cadastre esta chave no autenticador, apenas neste terminal privado:')
        print(pyotp.TOTP(secret).provisioning_uri(username,issuer_name='Empréstimo Fácil'))
        if not pyotp.TOTP(secret).verify(getpass.getpass('Código do autenticador: '),valid_window=1):
            raise SystemExit('Código inválido. Administrador não criado.')
        create_administrator(private,username,password,secret)
        print('Administrador criado com senha e TOTP.')
    elif args.command == 'purge':
        print(json.dumps(purge(private,int(os.getenv('DATA_RETENTION_DAYS','30')))))
    elif args.command == 'backup':
        if not args.directory:
            raise SystemExit('Informe --directory fora do diretório público.')
        final = backup(private,args.directory)
        retention = int(os.getenv('BACKUP_RETENTION_DAYS','7'))
        for path in Path(args.directory).iterdir():
            if path.is_dir() and not path.name.startswith('.') and path.stat().st_mtime < time.time()-retention*86400:
                shutil.rmtree(path)
        print('Backup privado concluído: '+str(final))
    elif args.command == 'restore':
        if not args.directory:
            raise SystemExit('Informe --directory do backup. Use um banco novo, com o serviço parado.')
        restore(private,args.directory)
        print('Backup restaurado; sessões invalidadas.')
    elif args.command == 'audit-check':
        if not audit_valid(private):
            raise SystemExit('A trilha de acesso não passou na verificação de integridade.')
        print('Trilha de acesso íntegra.')


if __name__ == '__main__':
    main()
