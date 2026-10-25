"""Encrypted browser fixtures only. No production files, identities or provider calls."""
import copy
import io
import json
import secrets
import time
import uuid
import base64
from pathlib import Path

import pyotp
from PIL import Image
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.app import Application
from backend.maintenance import create_administrator
from backend.private_store import PrivateStore
from backend.secure_models import PF
from backend_test import payload
from security_test import pf_payload
from backend.private_store import password_hash
from unittest.mock import patch


def seed(store,app):
    private=PrivateStore(store)
    template=payload();template['password']='Synthetic client password 123'
    first=store.save(Application.model_validate(template),str(uuid.uuid4()))
    with store.connect() as db:password_encoded=db.execute('SELECT password_hash FROM applications WHERE application_id=?',(first,)).fetchone()[0]

    def initial(email):
        # These fixtures reuse one synthetic password derivation; every payload
        # and nonce is independently encrypted and bound to its protocol.
        body=copy.deepcopy(template);body['applicant']['email']=email
        body['address']['complement']='<img src=x onerror="window.__unexpected=true">'
        normalized=Application.model_validate(body).model_dump()
        fingerprint=store.digest('request',json.dumps(normalized,sort_keys=True,ensure_ascii=False,separators=(',',':')))
        normalized.pop('password');normalized['received_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        identity=str(uuid.uuid4());nonce=secrets.token_bytes(12)
        encrypted=AESGCM(store.key).encrypt(nonce,json.dumps(normalized,ensure_ascii=False).encode(),identity.encode())
        with store.connect() as db:
            db.execute('INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?)',
                (identity,str(uuid.uuid4()),fingerprint,store.digest('cpf',body['applicant']['cpf']),
                 store.digest('email',email),nonce,encrypted,password_encoded,normalized['received_at']))
        return identity

    image=io.BytesIO();Image.new('RGB',(64,64),(30,100,60)).save(image,format='JPEG')
    video_bytes=base64.b64decode(json.loads((Path(__file__).parent/'fixture-admin-video.json').read_text(encoding='utf-8-sig'))['data'])
    accounts={}
    synthetic_admin_hash=password_hash('Synthetic admin password 123')
    for browser in ['chromium','firefox','webkit']:
        for width in [320,360,390,412,640,768,1024,1280,1440,1920,2560]:
            project=f'{browser}-{width}';email=f'admin-{project}@example.test'
            secret=pyotp.random_base32();password='Synthetic admin password 123'
            # Derive the shared synthetic password once; production functions
            # and browser login still use their real password verification.
            with patch('backend.maintenance.password_hash',return_value=synthetic_admin_hash):
                create_administrator(private,email,password,secret)
            identity=initial(email)
            pf=pf_payload();pf['applicant']['email']=email;pf['account']['support_email']=email
            pf_identity,_=private.create_pf('synthetic-'+project,PF.model_validate(pf),str(uuid.uuid4()),
                {'contact_verified_at':{'email':time.time()},'phone_verification_required':False,'bank':{'status':'not_connected'}},30)
            private.save_file(pf_identity,'front',image.getvalue(),'jpg')
            private.save_file(pf_identity,'back',image.getvalue(),'jpg')
            private.save_file(pf_identity,'video',video_bytes,'mp4')
            accounts[project]={'username':email,'password':password,'totp_secret':secret,'record_id':identity,
                               'email':email,'client_password':template['password'],'pf_id':pf_identity}
            usage_email='usability-'+project+'@example.test';usage_secret=pyotp.random_base32()
            with patch('backend.maintenance.password_hash',return_value=synthetic_admin_hash):
                create_administrator(private,usage_email,password,usage_secret)
            accounts[project]['usability']={'username':usage_email,'password':password,'totp_secret':usage_secret}
    for index in range(45):initial(f'pagination-{index}@example.test')
    return {'accounts':accounts,'minimum_total':100,'synthetic_only':True}
