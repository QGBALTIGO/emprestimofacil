const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require(process.env.TEST_JSDOM_PATH||'jsdom');
const {quotes}=require('./fixtures.cjs');
const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{runScripts:'dangerously',url:'https://example.test',virtualConsole:vc,beforeParse(w){w.matchMedia=()=>({matches:false,addEventListener(){}});w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};}});
const w=dom.window,d=w.document,api=w.EmprestimoFacil,range=d.getElementById('quote_amount');
assert(api);assert.deepEqual(errors,[]);assert.equal(api.getState().applicationReady,true);assert.equal(api.getState().submissionReady,false);assert.equal(api.getState().quote,null);
let cases=0;
for(let amount=500;amount<=3500;amount+=250)for(const weeks of [3,6,9,12]){range.value=String(amount);range.dispatchEvent(new w.Event('input'));d.getElementById('quote_installment_'+weeks).click();assert.equal(api.getState().amount,amount);assert.equal(api.getState().weeks,weeks);assert.equal(api.getState().quote,null);const initial=api.getPreliminaryPricing(amount,weeks);assert(initial.is_preliminary);assert(!('annual_cet' in initial));let balance=amount;for(let i=0;i<weeks;i++)balance=balance*(1+initial.weekly_rate)-initial.raw_installment_amount;assert(Math.abs(balance)<.000001);assert.equal(d.getElementById('quote-payment-display').textContent,new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(initial.installment_amount));cases++;}
assert.equal(api.loadQuotes(quotes()),52);
for(const q of quotes()){api.setAmount(q.amount_requested);api.selectWeeks(q.installments_requested);assert.equal(api.getState().quote.installment_amount,q.installment_amount);assert.equal(Math.round(api.getState().quote.total_amount*100),Math.round(q.installment_amount*100)*q.installments_requested);}
assert.equal(api.getPreliminaryPricing(2000,12).installment_amount,206.75);
const before=api.getState().quote;
assert.equal(d.getElementById('quote-iof-display').textContent,'R$\u00a01,23');assert.equal(d.getElementById('quote-fees-display').textContent,'R$\u00a04,56');assert.equal(d.getElementById('quote-cet-display').textContent,'50,00%');assert.equal(d.getElementById('quote-annual-rate-display').textContent,'26,82%');
assert.throws(()=>api.loadQuotes([{...quotes()[0],iof_amount:-1}]));assert.throws(()=>api.loadQuotes([{...quotes()[0],fees_amount:99999}]));assert.throws(()=>api.loadQuotes([{...quotes()[0],iof_amount:true}]));
assert.throws(()=>api.loadQuotes([{...quotes()[0],annual_cet:null}]));assert.throws(()=>api.loadQuotes([{...quotes()[0],total_amount:1}]));assert.throws(()=>api.loadQuotes([quotes()[0],quotes()[0]]));assert.throws(()=>api.loadQuotes([{...quotes()[0],expires_at:'invalid'}]));assert.throws(()=>api.loadQuotes([{...quotes()[0],monthly_rate:true}]));assert.equal(api.getState().quote,before);
api.loadQuotes([{...quotes()[0],expires_at:'2000-01-01T00:00:00Z'}]);api.setAmount(500);api.selectWeeks(3);assert.equal(api.getState().quote,null);
api.setAmount(-1);assert.equal(api.getState().amount,500);assert.equal(range.value,'500');api.setAmount(Infinity);assert.equal(api.getState().amount,500);api.setAmount(999999);assert.equal(api.getState().amount,3500);assert.equal(range.value,'3500');
assert.equal(api.buildApplicationUrl({applicationUrl:'javascript:alert(1)'},2000,12),null);assert.equal(api.buildApplicationUrl({applicationUrl:'https://user:secret@example.test'},2000,12),null);assert.equal(api.buildApplicationUrl({whatsapp:'abc'},2000,12),null);assert.equal(api.buildApplicationUrl({whatsapp:'5511999999999'},2000,5),null);assert(api.buildApplicationUrl({whatsapp:'5511999999999'},2000,12).startsWith('https://wa.me/5511999999999?text='));assert(api.buildApplicationUrl({applicationUrl:'https://example.test/request'},2000,12).includes('installments=12'));
d.getElementById('home-page--get-quote-submit-btn').click();assert(d.getElementById('request-dialog').open);assert(!d.getElementById('application-action').disabled);assert(!/enviad/i.test(d.getElementById('application-status').textContent));d.getElementById('request-dialog').querySelector('[data-close]').click();assert(!d.body.classList.contains('dialog-open'));
for(const a of d.querySelectorAll('a')){const href=a.getAttribute('href');if(!href){assert(a.hidden);continue;}if(href.startsWith('#'))assert(d.getElementById(href.slice(1)));else assert.equal(new URL(href).protocol,'https:');}
assert.equal(api.getState().installmentFrequency,'weekly');
assert.throws(()=>api.loadQuotes([{...quotes()[0],installment_frequency:'monthly'}]));
assert.throws(()=>api.loadQuotes([{...quotes()[0],installment_frequency:undefined}]));
assert.equal(d.getElementById('summary-weeks').textContent,'3 parcelas semanais');
assert.equal(d.querySelectorAll('.faq details').length,16);
assert(!d.querySelector('#ajuda').textContent.includes('Simplic'));
assert(!d.querySelector('main').textContent.includes('mensais'));
assert(!d.querySelector('a[href^="mailto:"], a[href*="simplic.com.br"]'));
for(const img of d.querySelectorAll('img'))assert(fs.existsSync(img.getAttribute('src')));
assert(!/simula[çc]|ilustrativ|prot[oó]tipo|367,15/i.test(d.querySelector('main').textContent));assert.deepEqual(errors,[]);
console.log(JSON.stringify({choices_checked:cases,quote_fixture_cases:52,invalid_and_expired_quotes:'passed',limits:'passed',contact_urls:'passed',no_false_submission:'passed',assets:'passed',runtime_errors:0}));w.close();
