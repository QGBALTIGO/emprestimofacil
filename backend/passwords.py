"""Keep expensive password work outside database transactions and bound memory."""
import base64
import hashlib
import hmac
import secrets
import threading

_slots=threading.BoundedSemaphore(2)

def password_hash(password):
    salt=secrets.token_bytes(16)
    with _slots:
        key=hashlib.scrypt(password.encode(),salt=salt,n=32768,r=8,p=1,maxmem=64*1024*1024,dklen=32)
    return 'scrypt$32768$8$1$'+base64.b64encode(salt).decode()+'$'+base64.b64encode(key).decode()

def password_matches(password,encoded):
    try:
        _,n,r,p,salt,expected=encoded.split('$')
        if (n,r,p)!=('32768','8','1'):return False
        with _slots:
            actual=hashlib.scrypt(password.encode(),salt=base64.b64decode(salt),n=int(n),r=int(r),p=int(p),maxmem=64*1024*1024,dklen=32)
        return hmac.compare_digest(actual,base64.b64decode(expected))
    except (ValueError,TypeError):return False
