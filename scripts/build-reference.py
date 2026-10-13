from pathlib import Path
from urllib.parse import urlparse
import re,json,shutil
root=Path(__file__).resolve().parents[1]
config=json.loads((root/'src/business-config.json').read_text())
assert config['minAmount']<=config['initialAmount']<=config['maxAmount']
assert config['initialWeeks'] in config['terms']
assert config['installmentFrequency']=='weekly'
assert config['preliminaryPricing']['daysPerWeek']==7 and config['preliminaryPricing']['daysPerMonth']==30
assert 0<=config['monthlyRateMin']<=config['monthlyRateMax']<=1
assert (config['maxAmount']-config['minAmount'])%config['amountStep']==0
fonts=(root/'assets/reference/fonts.css').read_text()
fonts=re.sub(r'url\(([^)]+)\)',lambda m:'url("assets/reference/'+Path(urlparse(m[1].strip('\"\'')).path).name+'")',fonts)
fonts=fonts.replace('@font-face {','@font-face {\n  font-display: swap;')
css=fonts+'\n'+(root/'src/reference.css').read_text()+'\n'+(root/'src/registration.css').read_text()
body=(root/'src/reference-body.html').read_text().replace('<!-- REGISTRATION_VIEW -->',(root/'src/registration-body.html').read_text())
runtime=(root/'src/reference-runtime.js').read_text()+'\n'+(root/'src/registration-runtime.js').read_text()+'\n'+(root/'src/cep-runtime.js').read_text()
result='''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="only light"><meta name="theme-color" content="#087d32"><title>Empréstimo Fácil | Seus planos, mais perto</title><meta name="description" content="Escolha o valor e o prazo do seu empréstimo pessoal. Confira as condições antes de contratar com a Empréstimo Fácil."><link rel="icon" type="image/png" href="assets/logo-transparent.png"><style>'''+css+'''</style></head><body>'''+body+'''<script id="business-config" type="application/json">'''+json.dumps(config,ensure_ascii=False).replace('<','\\u003c')+'''</script><script>'''+runtime+'''</script></body></html>'''
out=root/'dist' if (root/'.openai/hosting.json').exists() else root
out.mkdir(exist_ok=True)
(out/'index.html').write_text(result)
if out!=root:shutil.copytree(root/'assets',out/'assets',dirs_exist_ok=True)
print(json.dumps({'output':str(out/'index.html'),'bytes':len(result.encode()),'quotes':len(config['quotes']),'contact_configured':bool(config['contact']['whatsapp'] or config['contact']['applicationUrl'])}))
