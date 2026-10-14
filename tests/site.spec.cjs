const {test,expect}=require('@playwright/test');
const {quotes}=require('./fixtures.cjs');
const money=x=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(x);
const clean=x=>x.replace(/\u00a0/g,' ');
test.beforeEach(async({page})=>{await page.goto('/');await page.evaluate(()=>document.fonts.ready);});
test('layout, fotos, logos transparentes e tema claro',async({page},info)=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await expect(page.locator('#hero-title')).toBeVisible();await expect(page.locator('.hero-lead')).toBeVisible();
 if(page.viewportSize().width<700){const intro=await page.locator('.hero-copy').boundingBox(),card=await page.locator('.reference-quote').boundingBox();expect(intro.y+intro.height).toBeLessThanOrEqual(card.y);}
 for(const item of await page.locator('.about-difference').all()){const title=await item.locator('h4').boundingBox(),icon=await item.locator('.about-icon').boundingBox(),copy=await item.locator('p').boundingBox();expect(Math.abs(title.y+title.height/2-icon.y-icon.height/2)).toBeLessThanOrEqual(1);expect(copy.y).toBeGreaterThanOrEqual(icon.y+icon.height);}
 await expect(page.locator('#hero-title')).toHaveText('Seus planos, mais perto.');await expect(page.locator('#about-title')).toHaveText('Quem somos e nossa história');await expect(page.locator('#ajuda h2')).toHaveText('Uma ajuda para seguir com confiança.');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
 for(const img of await page.locator('img').all()){if(await img.isVisible())await img.scrollIntoViewIfNeeded();await expect.poll(()=>img.evaluate(e=>e.complete&&e.naturalWidth>0)).toBe(true);}
 const alpha=await page.locator('.brand-logo').first().evaluate(img=>{const canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;const c=canvas.getContext('2d');c.drawImage(img,0,0);return [c.getImageData(0,0,1,1).data[3],c.getImageData(canvas.width-1,0,1,1).data[3],c.getImageData(0,canvas.height-1,1,1).data[3]];});
 expect(alpha).toEqual([0,0,0]);
 expect(await page.locator('body').evaluate(e=>getComputedStyle(e).backgroundColor)).toBe('rgb(255, 255, 255)');
 await page.emulateMedia({colorScheme:'dark'});
 expect(await page.locator('body').evaluate(e=>getComputedStyle(e).backgroundColor)).toBe('rgb(255, 255, 255)');
 await expect(page.locator('main')).not.toContainText(/simula[çc]|ilustrativ|prot[oó]tipo|367,15/i);
 await page.locator('.about-difference').first().scrollIntoViewIfNeeded();await page.screenshot({path:info.outputPath('diferenciais-'+info.project.name+'.png'),fullPage:false,animations:'disabled'});
 await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:info.outputPath('inicio-'+info.project.name+'.png'),fullPage:false,animations:'disabled'});await page.screenshot({path:info.outputPath('pagina-'+info.project.name+'.png'),fullPage:true,animations:'disabled'});expect(errors).toEqual([]);
});
test('barra atualiza durante o movimento, limites e teclado',async({page})=>{
 const range=page.locator('#quote_amount');await range.focus();await range.press('ArrowRight');await expect(page.locator('#quote-amount-display')).toContainText('2.250,00');await range.press('Home');await expect(page.locator('#quote-amount-display')).toContainText('500');await expect(range).toHaveValue('500');await range.press('End');await expect(range).toHaveValue('3500');
 await range.press('ArrowLeft');await expect(page.locator('#quote-amount-display')).toContainText('3.250,00');
 for(const key of ['Home','End']){await range.press(key);const row=await page.locator('.amount-bubble-row').boundingBox(),bubble=await page.locator('#quote-amount-display').boundingBox();expect(bubble.x).toBeGreaterThanOrEqual(row.x-1);expect(bubble.x+bubble.width).toBeLessThanOrEqual(row.x+row.width+1);}
 await page.evaluate(()=>EmprestimoFacil.setAmount(2000));await range.scrollIntoViewIfNeeded();const box=await range.boundingBox();const y=box.y+box.height/2;await page.mouse.move(box.x+box.width/2,y);await page.mouse.down();await page.mouse.move(box.x+box.width*.75,y,{steps:12});await expect.poll(()=>page.evaluate(()=>EmprestimoFacil.getState().amount)).toBeGreaterThan(2000);await page.mouse.up();
});
test('52 cálculos iniciais e 52 cotações oficiais com prioridade',async({page})=>{
 for(let a=500;a<=3500;a+=250)for(const n of [3,6,9,12]){await page.locator('#quote_amount').evaluate((e,value)=>{e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));},String(a));await page.locator('label').filter({has:page.locator('#quote_installment_'+n)}).click();await expect(page.locator('#quote_installment_'+n)).toBeChecked();expect(await page.evaluate(()=>({a:EmprestimoFacil.getState().amount,n:EmprestimoFacil.getState().weeks}))).toEqual({a,n});}
 expect(await page.evaluate(()=>EmprestimoFacil.getState().preliminaryPricing.is_preliminary)).toBe(true);await expect(page.locator('#quote-payment-display')).toContainText('R$');await page.evaluate(data=>EmprestimoFacil.loadQuotes(data),quotes());
 for(const q of quotes()){await page.evaluate(q=>{EmprestimoFacil.setAmount(q.amount_requested);EmprestimoFacil.selectWeeks(q.installments_requested);},q);expect(clean(await page.locator('#quote-payment-display').innerText())).toBe(clean(money(q.installment_amount)));expect(clean(await page.locator('#quote-total-display').innerText())).toBe(clean(money(q.total_amount)));}
 await page.locator('.quote-breakdown summary').click();await expect(page.locator('#quote-iof-display')).toHaveText(money(1.23));await expect(page.locator('#quote-fees-display')).toHaveText(money(4.56));await expect(page.locator('#quote-cet-display')).toHaveText('50,00%');await expect(page.locator('#quote-annual-rate-display')).toHaveText('26,82%');expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});
test('solicitação honesta, resumo e fechamento por teclado',async({page})=>{
 await page.locator('#home-page--get-quote-submit-btn').click();await expect(page.locator('#request-dialog')).toBeVisible();await expect(page.locator('#summary-amount')).toContainText('2.000');await expect(page.locator('#summary-weeks')).toHaveText('12 parcelas semanais');await expect(page.locator('#application-action')).toBeEnabled();await expect(page.locator('#application-status')).toContainText('cadastro');await page.keyboard.press('Escape');await expect(page.locator('#request-dialog')).not.toBeVisible();await expect(page.locator('#home-page--get-quote-submit-btn')).toBeFocused();await expect(page.locator('body')).not.toHaveClass(/dialog-open/);
});
test('menu, navegação, FAQ e privacidade',async({page})=>{
 test.setTimeout(60000);
 const width=page.viewportSize().width;
 if(width<900){await page.locator('.mobile-nav--toggle').click();await expect(page.locator('.mobile-nav--toggle')).toHaveAttribute('aria-expanded','true');await page.locator('#main-navigation a[href="#ajuda"]').click();await expect(page.locator('.mobile-nav--toggle')).toHaveAttribute('aria-expanded','false');await page.locator('.mobile-nav--toggle').click();await page.keyboard.press('Escape');await expect(page.locator('.mobile-nav--toggle')).toHaveAttribute('aria-expanded','false');}
 else await expect(page.locator('#main-navigation')).toBeVisible();
 await expect(page.locator('.faq details')).toHaveCount(16);
 for(const question of await page.locator('.faq details').all()){const summary=question.locator('summary');await summary.click();await expect(question).toHaveAttribute('open','');await expect(question.locator('.faq-answer')).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await summary.press('Enter');await expect(question).not.toHaveAttribute('open','');}
 await page.locator('[data-info="privacy"]').click();await expect(page.locator('#info-dialog')).toBeVisible();await expect(page.locator('#info-title')).toHaveText('Sua privacidade');await page.locator('#info-dialog [data-close]').first().click();await expect(page.locator('#info-dialog')).not.toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});
