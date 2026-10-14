from pathlib import Path
from urllib.parse import urlparse
import re,json,shutil,base64,hashlib,html
root=Path(__file__).resolve().parents[1]
config=json.loads((root/'src/business-config.json').read_text(encoding='utf-8'))
assert config['minAmount']<=config['initialAmount']<=config['maxAmount']
assert config['initialWeeks'] in config['terms']
assert config['installmentFrequency']=='weekly'
assert config['preliminaryPricing']['daysPerWeek']==7 and config['preliminaryPricing']['daysPerMonth']==30
assert 0<=config['monthlyRateMin']<=config['monthlyRateMax']<=1
assert (config['maxAmount']-config['minAmount'])%config['amountStep']==0
fonts=(root/'assets/reference/fonts.css').read_text(encoding='utf-8')
fonts=re.sub(r'url\(([^)]+)\)',lambda m:'url("assets/reference/'+Path(urlparse(m[1].strip('\"\'')).path).name+'")',fonts)
fonts=fonts.replace('@font-face {','@font-face {\n  font-display: swap;')
css=fonts+'\n'+(root/'src/reference.css').read_text(encoding='utf-8')+'\n'+(root/'src/registration.css').read_text(encoding='utf-8')+'\n'+(root/'src/pf.css').read_text(encoding='utf-8')+'\n'+(root/'src/security.css').read_text(encoding='utf-8')+'\n'+(root/'src/form-fields.css').read_text(encoding='utf-8')
body=(root/'src/reference-body.html').read_text(encoding='utf-8').replace('<!-- REGISTRATION_VIEW -->',(root/'src/registration-body.html').read_text(encoding='utf-8')).replace('<!-- PF_VIEW -->',(root/'src/pf-body.html').read_text(encoding='utf-8'))
runtime=(root/'src/reference-runtime.js').read_text(encoding='utf-8')+'\n'+(root/'src/form-fields.js').read_text(encoding='utf-8')+'\n'+(root/'src/registration-runtime.js').read_text(encoding='utf-8')+'\n'+(root/'src/cep-runtime.js').read_text(encoding='utf-8')+'\n'+(root/'src/pf-runtime.js').read_text(encoding='utf-8')+'\n'+(root/'src/security-runtime.js').read_text(encoding='utf-8')
digest=base64.b64encode(hashlib.sha256(runtime.encode()).digest()).decode()
quote_origin=''
if config.get('quotesUrl'):
    quote_url=urlparse(config['quotesUrl'])
    if quote_url.scheme=='https' and quote_url.netloc and not quote_url.username and not quote_url.password:
        quote_origin=' https://'+quote_url.netloc
policy="default-src 'self'; script-src 'sha256-"+digest+"'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; font-src 'self'; connect-src 'self' https://viacep.com.br https://opencep.com"+quote_origin+"; object-src 'none'; base-uri 'none'; form-action 'self'"
result='''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="only light"><meta name="theme-color" content="#087d32"><title>Empréstimo Fácil | Seus planos, mais perto</title><meta name="description" content="Escolha o valor e o prazo do seu empréstimo pessoal. Confira as condições antes de contratar com a Empréstimo Fácil."><link rel="icon" type="image/png" href="assets/logo-transparent.png"><style>'''+css+'''</style></head><body>'''+body+'''<script id="business-config" type="application/json">'''+json.dumps(config,ensure_ascii=False).replace('<','\\u003c')+'''</script><script>'''+runtime+'''</script></body></html>'''
result=result.replace('<meta name="color-scheme"', '<meta http-equiv="Content-Security-Policy" content="'+html.escape(policy,quote=True)+'"><meta name="referrer" content="no-referrer"><meta name="color-scheme"',1)
out=root/'dist' if (root/'.openai/hosting.json').exists() else root
out.mkdir(exist_ok=True)
(out/'index.html').write_text(result,encoding='utf-8')
payment_runtime=(root/'src/payments-runtime.js').read_text(encoding='utf-8')
payment_digest=base64.b64encode(hashlib.sha256(payment_runtime.encode()).digest()).decode()
payment_policy="default-src 'self'; script-src 'sha256-"+payment_digest+"'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
payment='<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="'+html.escape(payment_policy,quote=True)+'"><title>Pagamento de parcela | Empréstimo Fácil</title><link rel="icon" href="assets/logo-transparent.png"><style>'+(root/'src/payments.css').read_text(encoding='utf-8')+'</style></head><body>'+(root/'src/payments-body.html').read_text(encoding='utf-8')+'<script id="payment-config" type="application/json">'+json.dumps({'apiUrl':config.get('paymentApiUrl')},ensure_ascii=False).replace('<','\\u003c')+'</script><script>'+payment_runtime+'</script></body></html>'
(out/'pagamentos.html').write_text(payment,encoding='utf-8')
sales_runtime=(root/'src/form-fields.js').read_text(encoding='utf-8')+'\n'+(root/'src/sales-runtime.js').read_text(encoding='utf-8')
sales_digest=base64.b64encode(hashlib.sha256(sales_runtime.encode()).digest()).decode()
sales_policy="default-src 'self'; script-src 'sha256-"+sales_digest+"'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
sales='<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="'+html.escape(sales_policy,quote=True)+'"><title>Pagamento via Pix</title><style>'+(root/'src/form-fields.css').read_text(encoding='utf-8')+'\n'+(root/'src/sales.css').read_text(encoding='utf-8')+'</style></head><body>'+(root/'src/sales-body.html').read_text(encoding='utf-8')+'<script>'+sales_runtime+'</script></body></html>'
(out/'afiliados.html').write_text(sales,encoding='utf-8')
terms_source=root/'src/terms.html'
if not terms_source.is_file():terms_source=root/'termos.html'
(out/'termos.html').write_text(terms_source.read_text(encoding='utf-8'),encoding='utf-8')
if out!=root:shutil.copytree(root/'assets',out/'assets',dirs_exist_ok=True)
print(json.dumps({'output':str(out/'index.html'),'bytes':len(result.encode()),'quotes':len(config['quotes']),'contact_configured':bool(config['contact']['whatsapp'] or config['contact']['applicationUrl'])}))
