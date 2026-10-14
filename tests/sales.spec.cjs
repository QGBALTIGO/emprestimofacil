const {test,expect}=require('@playwright/test');
const fixture=require('./fixtures-sale-pix.json');
const id='12345678-1234-1234-1234-123456789012';
const base={purchase_id:id,product_name:'AFILIADOS QG BALTIGO',amount_cents:749,buyer_first_name:'Pessoa',status:'waiting_payment'};

test('GitHub Pages abre Cakto sem depender de API inexistente',async({page})=>{
 const fs=require('node:fs'),path=require('node:path');let apiCalls=0;
 page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/'))apiCalls++;});
 await page.route('https://qgbaltigo.github.io/emprestimofacil/afiliados.html',route=>route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../afiliados.html'),'utf8')}));
 await page.goto('https://qgbaltigo.github.io/emprestimofacil/afiliados.html');
 const checkout=page.locator('#sale-official-checkout a');
 await expect(checkout).toBeVisible();await expect(checkout).toHaveAttribute('href','https://pay.cakto.com.br/3bd8an5_676851');
 await expect(page.locator('#affiliate-form')).toBeHidden();await expect(page.locator('#sale-intro')).toContainText('checkout oficial da Cakto');
 await expect(page.locator('#sale-official-checkout')).toContainText('R$ 8,48');expect(apiCalls).toBe(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('compra independente identificada, indisponível sem API e cadastro PF livre',async({page},info)=>{
 await page.route('**/api/sales/config',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({available:false,product_name:base.product_name,amount_cents:749})}));
 await page.goto('/afiliados.html');await expect(page.locator('h1')).toContainText('QG BALTIGO');await expect(page.locator('#sale-price')).toHaveText('R$ 7,49');
 await expect(page.locator('#sale-service')).toContainText('ainda não está disponível');await expect(page.locator('#sale-generate')).toBeDisabled();await expect(page.locator('.sale-separation')).toContainText('não faz parte do cadastro PF');
 await expect(page.locator('#affiliate-form')).toBeHidden();await expect(page.locator('#sale-official-checkout')).toBeVisible();await expect(page.locator('#sale-official-checkout')).toContainText('R$ 8,48');await expect(page.locator('#sale-official-checkout a')).toHaveAttribute('href','https://pay.cakto.com.br/3bd8an5_676851');
 await expect(page.locator('#sale-name')).toHaveAttribute('placeholder','Seu nome completo');await expect(page.locator('#sale-qr')).toBeHidden();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 await page.screenshot({path:info.outputPath('afiliados-'+info.project.name+'.png'),fullPage:true,animations:'disabled'});
 await page.goto('/#cadastro-pf');await expect(page.locator('#pf-email')).toBeVisible();await expect(page.locator('#pf-email')).toBeEnabled();await expect(page.locator('#pf-next')).toBeEnabled();
});

test('Pix Cakto: carregamento, máscara, cópia, expiração e pagamento confirmado pelo servidor',async({page},info)=>{
 let status='new',calls=0,resolve,wrong=false,expired=false;const gate=new Promise(r=>resolve=r);
 const view=()=>status==='new'?{status:'new'}:{...base,status,amount_cents:wrong?699:749,...(status==='waiting_payment'?(expired?{pix_expired:true}:{pix:{...fixture,expires_at:new Date(Date.now()+900000).toISOString(),server_time:Date.now()/1000}}):{}),...(status==='paid'?{support_url:'https://wa.me/5511912345678?text=Protocolo%20'+id}:{})};
 await page.addInitScript(()=>{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.copiedPix=text}});});
 await page.route('**/api/sales/**',async route=>{const path=new URL(route.request().url()).pathname;let result;if(path.endsWith('/config'))result={available:true,product_name:base.product_name,amount_cents:749};else if(path.endsWith('/pix')){calls++;const body=route.request().postDataJSON();expect(body.buyer.cpf).toBe('11144477735');expect(body.buyer.phone).toBe('11912345678');expect(body).not.toHaveProperty('amount');expect(route.request().headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/);await gate;status='waiting_payment';result=view();}else result=view();await route.fulfill({contentType:'application/json',body:JSON.stringify(result)});});
 await page.goto('/afiliados.html');await expect(page.locator('#sale-generate')).toBeEnabled();await page.locator('#sale-generate').click();await expect(page.locator('#sale-name-error')).toContainText('nome e sobrenome');
 await page.locator('#sale-name').fill('Pessoa de Teste');await page.locator('#sale-email').fill('pessoa@example.test');await page.locator('#sale-cpf').fill('11144477735');await page.locator('#sale-phone').fill('11912345678');await expect(page.locator('#sale-phone')).toHaveValue('(11) 91234-5678');await expect(page.locator('#sale-cpf')).toHaveValue('111.444.777-35');await page.locator('#sale-consent').check();
 await page.locator('#sale-generate').click();await expect(page.locator('#sale-loading')).toBeVisible();expect(calls).toBe(1);resolve();await expect(page.locator('#sale-payment')).toBeVisible();await expect(page.locator('#sale-qr')).toBeVisible();await expect(page.locator('#sale-badge')).toHaveText('Aguardando confirmação');await page.locator('#sale-copy').click();expect(await page.evaluate(()=>window.copiedPix)).toBe(fixture.code);await expect(page.locator('#sale-message')).toContainText('copiado');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await page.screenshot({path:info.outputPath('pix-afiliados-'+info.project.name+'.png'),fullPage:true,animations:'disabled'});
 expired=true;await page.locator('#sale-check').click();await expect(page.locator('#sale-payment-title')).toHaveText('Pix expirado');await expect(page.locator('#sale-qr')).toBeHidden();expect(calls).toBe(1);
 status='paid';wrong=true;await page.locator('#sale-check').click();await expect(page.locator('#sale-message')).toContainText('não pôde ser confirmada');await expect(page.locator('#sale-badge')).not.toHaveText('Pagamento confirmado');wrong=false;
 await page.locator('#sale-check').click();await expect(page.locator('#sale-badge')).toHaveText('Pagamento confirmado');await expect(page.locator('#sale-qr')).toBeHidden();await expect(page.locator('#sale-support')).toBeVisible();await expect(page.locator('#sale-protocol')).toContainText(id);expect(calls).toBe(1);expect(await page.evaluate(()=>localStorage.length+sessionStorage.length)).toBe(0);
 await page.screenshot({path:info.outputPath('afiliados-confirmado-'+info.project.name+'.png'),fullPage:true,animations:'disabled'});
});
