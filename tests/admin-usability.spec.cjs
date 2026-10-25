const {test,expect}=require('@playwright/test'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
function totp(secret){let bits='';for(const char of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(char).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=crypto.createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash.at(-1)&15;return((hash.readUInt32BE(offset)&0x7fffffff)%1000000).toString().padStart(6,'0');}
test('admin reconecta sem apagar o acesso, filtra pelos cartões e encerra todos os aparelhos',async({page,context},info)=>{
 const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'../test-results/admin-browser-fixture.json'),'utf8')),account=fixture.admin.accounts[info.project.name].usability;
 await context.setExtraHTTPHeaders({'X-Test-Project':info.project.name});await page.goto('/admin');
 await page.locator('#login [name=username]').fill(account.username);await page.locator('#login [name=password]').fill(account.password);await page.locator('#login [name=code]').fill(totp(account.totp_secret));
 await page.locator('#login button[type=submit]').click();await expect(page.locator('#panel')).toBeVisible();
 await page.getByRole('button',{name:'Ver cadastros pf',exact:true}).click();await expect(page.locator('#filters [name=kind]')).toHaveValue('pf');await expect(page.locator('#rows tr').first()).toContainText('Pessoa de Teste');
 await page.locator('#page-size').selectOption('10');await expect(page.locator('#rows tr')).toHaveCount(10);
 let resolveRequest;const checked=new Promise(resolve=>resolveRequest=resolve);
 await page.route('**/api/admin/session',async route=>{await route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Serviço temporariamente indisponível"}'});resolveRequest();});
 await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await checked;await expect(page.locator('#message')).toContainText('Tentando reconectar');await expect(page.locator('#panel')).toBeVisible();await expect(page.locator('#login')).toBeHidden();
 await page.unroute('**/api/admin/session');await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(page.locator('#panel')).toBeVisible();
 await page.getByRole('link',{name:'Meu acesso',exact:true}).click();await page.getByRole('button',{name:'Sair de todos os aparelhos',exact:true}).click();
 await expect(page.locator('#login')).toBeVisible();await expect(page.locator('#panel')).toBeHidden();expect((await context.request.get('/api/admin/session')).status()).toBe(401);
});
