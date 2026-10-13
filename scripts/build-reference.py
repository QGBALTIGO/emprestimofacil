from pathlib import Path
from urllib.parse import urlparse,unquote
import re,base64,json,mimetypes,math

root=Path(__file__).resolve().parents[1]
assets=root/'assets/reference'
def data_url(p):
    p=Path(p)
    mime=mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
    if p.suffix=='.woff2':mime='font/woff2'
    if p.suffix=='.woff':mime='font/woff'
    if p.suffix=='.ttf':mime='font/ttf'
    if p.suffix=='.eot':mime='application/vnd.ms-fontobject'
    return 'data:'+mime+';base64,'+base64.b64encode(p.read_bytes()).decode('ascii')
def resource(value):
    value=value.strip().strip('\"\'')
    if value.startswith('data:') or value.startswith('#'):return value
    parsed=urlparse(value)
    name=unquote(Path(parsed.path).name)
    p=assets/name
    if not p.is_file():raise FileNotFoundError('Missing referenced resource: '+value)
    return data_url(p)+('#'+parsed.fragment if parsed.fragment else '')
def css_resources(s):
    s=re.sub(r'@import\s+url\([^)]*fonts\.googleapis[^)]*\);','',s)
    return re.sub(r'url\(([^)]*)\)',lambda m:'url("'+resource(m[1])+'")',s)

fonts=(assets/'fonts.css').read_text()
# Embed only the font families and weights used by this page.
fonts='\n'.join(block for block in re.findall(r'@font-face\s*\{[^}]*\}',fonts)
    if "'Bree Serif'" in block or ("'Open Sans'" in block and re.search(r'font-weight:\s*(300|400|700)\s*;',block)))
fonts=fonts.replace('@font-face {','@font-face {\n  font-display: swap;')
css=css_resources(fonts+'\n'+(root/'src/reference.css').read_text())
body=(root/'src/reference-body.html').read_text()
body=re.sub(r'<style([^>]*)>(.*?)</style>',lambda m:'<style'+m[1]+'>'+css_resources(m[2])+'</style>',body,flags=re.S)
body=re.sub(r'\bsrc="([^"]+)"',lambda m:'src="'+resource(m[1])+'"',body)
logo='<svg class="brand-logo brand-adapted" xmlns="http://www.w3.org/2000/svg" viewBox="40 205 1595 500" role="img" aria-label="Empréstimo Fácil — Seus planos mais perto"><image href="'+data_url(root/'assets/logo.webp')+'" x="0" y="0" width="1672" height="941"/></svg>'
body=body.replace('__BRAND_LOGO__',logo)
config=json.loads((root/'src/reference-capture.json').read_text())
assert math.isfinite(config['simulationMonthlyRate']) and config['simulationMonthlyRate']>=0
assert config['minAmount']<=config['initialAmount']<=config['maxAmount']
assert config['initialMonths'] in [3,6,9,12]
assert (config['maxAmount']-config['minAmount'])%config['amountStep']==0
capture=json.dumps({key:config[key] for key in ['minAmount','maxAmount','amountStep','initialAmount','initialMonths','simulationMonthlyRate','simulationMode']},ensure_ascii=False)
enhancements=(root/'src/reference-enhancements.css').read_text()
runtime=(root/'src/reference-runtime.js').read_text()
result='''<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#048a05">
<title>Empréstimo Fácil | Empréstimo pessoal online</title>
<meta name="description" content="Simule de R$ 500 a R$ 3.500 e compare 3, 6, 9 ou 12 parcelas. Confira o valor mensal e o total estimado com a Empréstimo Fácil.">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%23048a05'/%3E%3Cpath d='m16 33 10 10 23-25' fill='none' stroke='white' stroke-width='7' stroke-linecap='round'/%3E%3C/svg%3E">
<style id="reference-styles">'''+css+'</style><style>'+enhancements+'</style></head><body>'+body+'''
<script id="reference-capture" type="application/json">'''+capture+'''</script>
<script>'''+runtime+'''</script>
</body></html>'''
(root/'index.html').write_text(result)
print(json.dumps({'output':str(root/'index.html'),'bytes':len(result.encode()),'combinations':int((config['maxAmount']-config['minAmount'])/config['amountStep']+1)*4,'simulation':'illustrative'}))
