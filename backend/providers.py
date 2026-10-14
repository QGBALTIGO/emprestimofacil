"""Transportes documentados; nenhuma credencial ou confirmação vem do navegador."""
import os
import re
import threading
import time
from urllib.parse import urlencode

import httpx

_email_rate_lock=threading.Lock()
_email_next=0.0

def email_delivery_slot():
    """Pace this single-worker site's Resend calls below the 10/s team limit."""
    global _email_next
    with _email_rate_lock:
        now=time.monotonic();reserved=max(now,_email_next);_email_next=reserved+.125
    if reserved>now:time.sleep(reserved-now)


class ProviderUnavailable(Exception):
    pass


class EmailQuotaExceeded(ProviderUnavailable):
    def __init__(self,retry_after):
        self.retry_after=max(1,int(retry_after))


class Providers:
    def __init__(self, transport=None):
        self.transport = transport
        self.twilio_account = os.getenv('TWILIO_ACCOUNT_SID', '')
        self.twilio_token = os.getenv('TWILIO_AUTH_TOKEN', '')
        self.twilio_service = os.getenv('TWILIO_VERIFY_SERVICE_SID', '')
        self.belvo_id = os.getenv('BELVO_SECRET_ID', '')
        self.belvo_password = os.getenv('BELVO_SECRET_PASSWORD', '')
        self.belvo_host = os.getenv('BELVO_ENVIRONMENT', 'sandbox')
        self.webhook_secret = os.getenv('BELVO_WEBHOOK_TOKEN', '')
        self.terms_url = os.getenv('COMPANY_TERMS_URL', '')
        self.company_name = os.getenv('COMPANY_NAME', '')
        self.email_provider = os.getenv('EMAIL_PROVIDER', 'twilio')
        self.resend_key = os.getenv('RESEND_API_KEY', '')
        self.email_from = os.getenv('EMAIL_FROM', '')

    @property
    def email_ready(self):
        if self.email_provider == 'resend':
            return bool(self.resend_key and re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', self.email_from)
                        and not self.email_from.lower().endswith('@resend.dev'))
        return self.email_provider == 'twilio' and self.sms_ready

    @property
    def sms_ready(self):
        return bool(re.fullmatch(r'AC[0-9a-fA-F]{32}', self.twilio_account)
                    and re.fullmatch(r'VA[0-9a-fA-F]{32}', self.twilio_service) and self.twilio_token)

    @property
    def verify_ready(self):
        return self.email_ready and self.sms_ready

    def email_code(self, destination, code, delivery_id):
        """Only the private server creates the challenge; the provider receives the e-mail."""
        if self.email_provider != 'resend' or not self.email_ready:
            raise ProviderUnavailable()
        email_delivery_slot()
        result = self.request('POST', 'https://api.resend.com/emails',
            headers={'Authorization': 'Bearer '+self.resend_key, 'Idempotency-Key': 'ef-code/'+delivery_id},
            json={'from': self.email_from, 'to': [destination], 'subject': 'Confirme seu e-mail — Empréstimo Fácil',
                  'text': 'Seu código de confirmação é '+code+'. Ele expira em 10 minutos. '
                          'Não compartilhe este código. Se não solicitou, ignore esta mensagem.'})
        identity = result.get('id') if isinstance(result, dict) else None
        if not isinstance(identity, str) or not 10 <= len(identity) <= 100:
            raise ProviderUnavailable()
        return identity

    @property
    def bank_ready(self):
        return bool(self.belvo_id and self.belvo_password and len(self.webhook_secret) >= 32
                    and self.belvo_host in {'sandbox', 'production'} and self.company_name
                    and self.terms_url.startswith('https://'))

    def request(self, method, url, **kwargs):
        try:
            with httpx.Client(timeout=15, follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, url, **kwargs)
            if response.status_code not in {200, 201, 204}:
                if url=='https://api.resend.com/emails' and response.status_code==429 and len(response.content)<16384:
                    data=response.json()
                    if isinstance(data,dict) and data.get('name') in {'daily_quota_exceeded','monthly_quota_exceeded'}:
                        from datetime import datetime,timezone
                        import time
                        now=time.time();date=datetime.fromtimestamp(now,timezone.utc)
                        until=(int(now//86400)+1)*86400
                        if data['name']=='monthly_quota_exceeded':
                            until=date.replace(year=date.year+(date.month==12),month=date.month%12+1,day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()
                        raise EmailQuotaExceeded(until-now)
                raise ProviderUnavailable()
            if response.status_code == 204:
                return None
            if len(response.content) > 10 * 1024 * 1024:
                raise ProviderUnavailable()
            result=response.json()
            if not isinstance(result,dict):raise ProviderUnavailable()
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            raise ProviderUnavailable() from None

    def verification_start(self, destination, channel):
        if not self.sms_ready or channel not in {'email','sms'} or (channel=='email' and self.email_provider!='twilio'):
            raise ProviderUnavailable()
        result = self.request('POST', f'https://verify.twilio.com/v2/Services/{self.twilio_service}/Verifications',
                              auth=(self.twilio_account, self.twilio_token),
                              data={'To': destination, 'Channel': channel, 'Locale': 'pt-BR'})
        if not isinstance(result,dict):raise ProviderUnavailable()
        sid = result.get('sid', '')
        if (not isinstance(sid,str) or not re.fullmatch(r'VE[0-9a-fA-F]{32}', sid) or result.get('status') != 'pending'
                or not isinstance(result.get('to'),str) or result['to'].lower() != destination.lower() or result.get('channel') != channel
                or result.get('service_sid') != self.twilio_service):
            raise ProviderUnavailable()
        return sid

    def verification_check(self, sid, code, destination, channel):
        if not self.sms_ready or channel not in {'email','sms'} or (channel=='email' and self.email_provider!='twilio') or not re.fullmatch(r'VE[0-9a-fA-F]{32}', sid):
            raise ProviderUnavailable()
        result = self.request('POST', f'https://verify.twilio.com/v2/Services/{self.twilio_service}/VerificationCheck',
                              auth=(self.twilio_account, self.twilio_token),
                              data={'VerificationSid': sid, 'Code': code})
        if not isinstance(result,dict):raise ProviderUnavailable()
        return (result.get('status') == 'approved' and result.get('sid') == sid
                and isinstance(result.get('to'),str) and result['to'].lower() == destination.lower() and result.get('channel') == channel
                and result.get('service_sid') == self.twilio_service)

    def belvo(self, method, path, **kwargs):
        if not self.bank_ready or not path.startswith('/api/'):
            raise ProviderUnavailable()
        base = 'https://api.belvo.com' if self.belvo_host == 'production' else 'https://sandbox.belvo.com'
        return self.request(method, base + path, auth=(self.belvo_id, self.belvo_password), **kwargs)

    def bank_start(self, profile, attempt, callbacks):
        result = self.belvo('POST', '/api/token/', json={
            'id': self.belvo_id, 'password': self.belvo_password,
            'scopes': 'read_institutions,write_links,read_consents,write_consents,write_consent_callback,delete_consents',
            'fetch_resources': ['OWNERS', 'ACCOUNTS', 'TRANSACTIONS'], 'stale_in': '30d',
            'widget': {'openfinance_feature': 'consent_link_creation', 'callback_urls': callbacks,
                       'branding': {'company_name': self.company_name, 'company_terms_url': self.terms_url,
                                    'company_icon': callbacks['success'].split('/api/')[0] + '/assets/logo-transparent.png',
                                    'company_logo': callbacks['success'].split('/api/')[0] + '/assets/logo-transparent.png'},
                       'consent': {'purpose': 'Conferir cadastro, titularidade e movimentação para análise da solicitação de crédito.',
                                   'terms_and_conditions_url': self.terms_url,
                                   'permissions': ['REGISTER', 'ACCOUNTS', 'CREDIT_CARDS', 'CREDIT_OPERATIONS'],
                                   'identification_info': [{'type': 'CPF', 'number': profile['cpf'], 'name': profile['full_name']}],
                                   'default_consent_duration_days': 92}}})
        if not isinstance(result,dict):raise ProviderUnavailable()
        access = result.get('access')
        if not isinstance(access, str) or not 20 <= len(access) <= 10000:
            raise ProviderUnavailable()
        return 'https://widget.belvo.io/?' + urlencode({'access_token': access, 'locale': 'pt',
                'integration_type': 'openfinance', 'institution_types': 'retail', 'country_codes': 'BR',
                'access_mode': 'single', 'external_id': attempt, 'resources': 'OWNERS,ACCOUNTS,TRANSACTIONS'})

    def bank_link(self, link):
        return self.belvo('GET', f'/api/links/{link}/')

    def bank_resources(self, link):
        output, truncated = {}, False
        for resource in ['owners', 'accounts', 'transactions']:
            rows = []
            for page in range(1, 21):
                data = self.belvo('GET', f'/api/{resource}/', params={'link': link, 'page_size': 100, 'page': page})
                if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                    raise ProviderUnavailable()
                # A ignored query or unrelated result must never attach another person's data.
                if any(not isinstance(row,dict) or row.get('link') != link for row in data['results']):
                    raise ProviderUnavailable()
                rows.extend(data['results'])
                if not data.get('next'):
                    break
            else:
                truncated = True
            output[resource] = rows
        output['truncated'] = truncated
        return output

    def bank_delete(self, link):
        self.belvo('DELETE', f'/api/links/{link}/')
