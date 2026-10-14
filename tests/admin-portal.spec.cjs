const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
function totp(secret){const chars='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';let bits='';for(const char of secret)bits+=chars.indexOf(char).toString(2).padStart(5,'0');const bytes=[];for(let offset=0;offset+8<=bits.length;offset+=8)bytes.push(parseInt(bits.slice(offset,offset+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const digest=crypto.createHmac('sha1',Buffer.from(bytes)).update(counter).digest();const offset=digest[digest.length-1]&15;return ((digest.readUInt32BE(offset)&0x7fffffff)%1000000).toString().padStart(6,'0');}

test('admin MFA, readable records, private notes, protected files and pagination through the real API',async({page,context,browser},testInfo)=>{
  const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'../test-results/admin-browser-fixture.json'),'utf8'));
  const account=fixture.admin.accounts[testInfo.project.name];expect(account).toBeTruthy();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let releaseProbe,probeCaptured,probeReturned;
  const probeGate=new Promise(resolve=>releaseProbe=resolve),captured=new Promise(resolve=>probeCaptured=resolve),returned=new Promise(resolve=>probeReturned=resolve);
  let firstProbe=true;
  await page.route('**/api/admin/session',async route=>{
    if(!firstProbe)return route.continue();firstProbe=false;
    const response=await route.fetch();expect(response.status()).toBe(401);probeCaptured();
    await probeGate;await route.fulfill({response});probeReturned();
  });
  await context.setExtraHTTPHeaders({'X-Test-Project':testInfo.project.name});
  await page.goto('/admin');await expect(page.getByRole('heading',{name:'Painel administrativo',exact:true})).toBeVisible();
  await captured;
  await page.locator('#login [name=username]').fill(account.username);await page.locator('#login [name=password]').fill(account.password);
  await page.locator('#login [name=code]').fill(totp(account.totp_secret));
  releaseProbe();await returned;await page.waitForTimeout(150);
  await expect(page.locator('#login [name=username]')).toHaveValue(account.username);
  await expect(page.locator('#login [name=password]')).toHaveValue(account.password);
  const login=page.waitForResponse(response=>response.url().endsWith('/api/admin/login')&&response.request().method()==='POST');
  await page.locator('#login').getByRole('button',{name:'Entrar',exact:true}).click();expect((await login).status()).toBe(200);
  await expect(page.locator('#panel')).toBeVisible();await expect(page.locator('#rows tr')).toHaveCount(25);
  const firstPage=await page.locator('#rows tr td:first-child').allTextContents();
  await expect(page.locator('#page-info')).toContainText(/Página 1 de [5-9]/);
  await page.getByRole('button',{name:'Próxima',exact:true}).click();await expect(page.locator('#page-info')).toContainText('Página 2');
  const secondPage=await page.locator('#rows tr td:first-child').allTextContents();expect(secondPage.some(id=>firstPage.includes(id))).toBeFalsy();
  await page.locator('#filters [name=q]').fill(account.record_id);await page.locator('#filters').getByRole('button',{name:'Filtrar',exact:true}).click();
  await expect(page.locator('#rows tr')).toHaveCount(1);await expect(page.locator('#rows')).toContainText(account.record_id);await page.locator('#rows').getByRole('button',{name:'Abrir',exact:true}).click();
  await expect(page.locator('#detail')).toBeVisible();await expect(page.locator('#record-protocol')).toContainText(account.record_id);
  await expect(page.locator('#data')).toContainText('Identificação e contatos');await expect(page.locator('#data')).toContainText('Pessoa de Teste');
  await expect(page.locator('#data')).toContainText('111.444.777-35');await expect(page.locator('#data')).toContainText('Renda e atividade');
  await expect(page.locator('#data')).toContainText('Destino informado para recebimento');await expect(page.locator('#data')).toContainText('Itaú');
  await expect(page.locator('#data')).toContainText('<img src=x onerror=');expect(await page.locator('#data img').count()).toBe(0);
  await expect(page.locator('#data')).not.toContainText('schema_version');await expect(page.locator('#data')).not.toContainText('server_attestations');
  await expect(page.locator('#data')).not.toContainText(account.client_password);
  await page.locator('#review [name=state]').selectOption('information_requested');
  const message='Documento adicional solicitado para '+testInfo.project.name;const privateNote='Nota restrita da equipe '+testInfo.project.name;
  await page.locator('#review [name=message]').fill(message);await page.locator('#review [name=internal_note]').fill(privateNote);
  const saved=page.waitForResponse(response=>response.url().endsWith('/review')&&response.request().method()==='POST');
  await page.locator('#review').getByRole('button',{name:'Salvar andamento',exact:true}).click();expect((await saved).status()).toBe(200);
  await expect(page.locator('#message')).toContainText('Andamento atualizado.');
  const persisted=await context.request.get('/api/admin/records/initial/'+account.record_id);expect(persisted.status()).toBe(200);
  const reviewed=await persisted.json();expect(reviewed.review.internal_note).toBe(privateNote);expect(reviewed.review.message).toBe(message);
  await page.locator('#detail-close').click();
  await page.locator('#filters [name=q]').fill(account.pf_id);await page.locator('#filters').getByRole('button',{name:'Filtrar',exact:true}).click();
  await expect(page.locator('#rows tr')).toHaveCount(1);await expect(page.locator('#rows')).toContainText(account.pf_id);await page.locator('#rows').getByRole('button',{name:'Abrir',exact:true}).click();
  await expect(page.locator('#data')).toContainText('Confirmações do cadastro');await expect(page.locator('#data')).toContainText('SMS não exigido');
  await expect(page.locator('#data')).toContainText('Contatos de familiares');
  const document=page.getByRole('link',{name:'Documento — frente Baixar arquivo'});await expect(document).toBeVisible();
  const windowsWebKit=process.platform==='win32'&&testInfo.project.use.browserName==='webkit';
  if(windowsWebKit){
    // The Windows WebKit harness does not emit a download event for this local
    // self-signed HTTPS fixture. Keep the authenticated server/file assertions;
    // OS file saving in real Safari remains a separate physical-device check.
    const fileResponse=await context.request.get(await document.getAttribute('href'));
    expect(fileResponse.status()).toBe(200);expect(fileResponse.headers()['content-disposition']).toMatch(/attachment; filename="front\.jpg"/);
    expect(fileResponse.headers()['cache-control']).toContain('no-store');expect((await fileResponse.body()).subarray(0,3)).toEqual(Buffer.from([255,216,255]));
    testInfo.annotations.push({type:'platform limitation',description:'Windows WebKit local HTTPS download event unverified; real authenticated bytes, attachment headers and access checks passed.'});
  }else{
    const download=page.waitForEvent('download');await document.click();const received=await download;
    expect(await received.failure()).toBeNull();expect(received.suggestedFilename()).toMatch(/\.jpg$/);
  }
  const privateClient=await browser.newContext({baseURL:fixture.origin,ignoreHTTPSErrors:true,extraHTTPHeaders:{'X-Test-Project':testInfo.project.name}});
  try{
    const forbidden=await privateClient.request.get('/api/admin/files/'+account.pf_id+'/front');expect(forbidden.status()).toBe(401);
    const logged=await privateClient.request.post('/api/client/login',{headers:{Origin:fixture.origin},data:{application_id:account.record_id,email:account.email,password:account.client_password}});expect(logged.status()).toBe(200);
    const publicSummary=await privateClient.request.get('/api/client/application');expect(publicSummary.status()).toBe(200);const summary=await publicSummary.json();
    expect(summary.review.message).toBe(message);expect(summary.review.state).toBe('information_requested');expect(JSON.stringify(summary)).not.toContain(privateNote);
    expect((await privateClient.request.get('/api/admin/records')).status()).toBe(401);
  }finally{await privateClient.close();}
  await page.screenshot({path:testInfo.outputPath('admin-record.png'),fullPage:true});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);expect(overflow).toBeFalsy();
  let releaseLateResponse;const held=new Promise(resolve=>releaseLateResponse=resolve);let receivedLateResponse;
  const responseCaptured=new Promise(resolve=>receivedLateResponse=resolve);
  await page.route('**/api/admin/records?*',async route=>{
    const response=await route.fetch();receivedLateResponse();await held;
    try{await route.fulfill({response});}catch{/* Logout aborts the original request. */}
  });
  await page.locator('#overview-refresh').click();await responseCaptured;
  const logout=page.waitForResponse(response=>response.url().endsWith('/api/admin/logout')&&response.request().method()==='POST');
  await page.locator('#logout').click();expect((await logout).status()).toBe(200);await expect(page.locator('#login')).toBeVisible();
  releaseLateResponse();await page.waitForTimeout(150);
  await expect(page.locator('#data')).toBeEmpty();await expect(page.locator('#panel')).toBeHidden();await expect(page.locator('#rows')).toBeEmpty();
  await expect(page.locator('#review [name=internal_note]')).toHaveValue('');await expect(page.locator('#login [name=password]')).toHaveValue('');
  expect(errors).toEqual([]);
});
