"""Shared provider timestamp and local QR rendering; no application initialization."""
import base64
import io
from datetime import datetime
import qrcode
from backend.providers import ProviderUnavailable


def timestamp(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:raise ValueError()
        return parsed.timestamp()
    except (ValueError,TypeError,AttributeError):raise ProviderUnavailable() from None


def qr_image(code):
    if not isinstance(code,str) or not 20<=len(code.encode('utf8'))<=2000 or not code.startswith('000201'):
        raise ProviderUnavailable()
    qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=6,border=4)
    qr.add_data(code);qr.make(fit=True);buffer=io.BytesIO()
    qr.make_image(fill_color='black',back_color='white').save(buffer,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()

