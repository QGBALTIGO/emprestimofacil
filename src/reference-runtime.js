(function () {
  'use strict';
  const config = JSON.parse(document.getElementById('business-config').textContent);
  const d = document, form = d.getElementById('home-page--get-quote'), range = d.getElementById('quote_amount');
  const money = new Intl.NumberFormat('pt-BR', {style:'currency',currency:'BRL'});
  const percent = value => new Intl.NumberFormat('pt-BR',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
  const annualInterest = rate => Math.expm1(12*Math.log1p(rate));
  const rateRange = (annual=false) => [config.monthlyRateMin,config.monthlyRateMax].map(rate=>percent(annual?annualInterest(rate):rate)).join(' a ');
  const dialogs = [...d.querySelectorAll('dialog')];
  let amount=config.initialAmount,weeks=config.initialWeeks,quotes=[],quoteStatus='unavailable',requestTrigger;
  function normalizeQuotes(data) {
    const rows=Array.isArray(data)?data:data?.quotes;
    if(!Array.isArray(rows))throw new TypeError('Invalid quote response');
    const keys=new Set();
    return rows.map(row=>{
      const a=Number(row.amount_requested),n=Number(row.installments_requested);
      const p=Number(row.installment_amount),total=Number(row.total_amount),rate=Number(row.monthly_rate),cet=Number(row.annual_cet);
      const required=['amount_requested','installments_requested','installment_amount','total_amount','monthly_rate','annual_cet','installment_frequency'];
      if(row.installment_frequency!==config.installmentFrequency)throw new TypeError('Quote installment frequency does not match');
      if(required.some(k=>!['number','string'].includes(typeof row[k])||String(row[k]).trim()==='')||![a,n,p,total,rate,cet].every(Number.isFinite)||a<config.minAmount||a>config.maxAmount||(a-config.minAmount)%config.amountStep!==0||!config.terms.includes(n)||p<=0||total<a||Math.abs(Math.round(total*100)-Math.round(p*100)*n)>1||rate<0||rate>1||cet<0)throw new TypeError('Incomplete or inconsistent quote');
      const charges={};
      for(const key of ['iof_amount','fees_amount']){if(row[key]!=null){if(!['number','string'].includes(typeof row[key])||String(row[key]).trim()===''||!Number.isFinite(Number(row[key]))||Number(row[key])<0)throw new TypeError('Invalid quote charges');charges[key]=Number(row[key]);}}
      if((charges.iof_amount||0)+(charges.fees_amount||0)>total-a+.01)throw new TypeError('Quote charges exceed total cost');
      const key=a+':'+n;if(keys.has(key))throw new TypeError('Duplicate quote');keys.add(key);
      if(row.expires_at&&!Number.isFinite(Date.parse(row.expires_at)))throw new TypeError('Invalid quote expiry');
      return Object.freeze({amount_requested:a,installments_requested:n,installment_frequency:row.installment_frequency,installment_amount:p,total_amount:total,monthly_rate:rate,annual_cet:cet,expires_at:row.expires_at||null,...charges});
    });
  }
  function getQuote(value,term){return quotes.find(q=>q.amount_requested===Number(value)&&q.installments_requested===Number(term)&&(!q.expires_at||Date.parse(q.expires_at)>Date.now()))||null;}
  function buildApplicationUrl(contact,value,term){
    if(!Number.isFinite(Number(value))||Number(value)<config.minAmount||Number(value)>config.maxAmount||(Number(value)-config.minAmount)%config.amountStep!==0||!config.terms.includes(Number(term)))return null;
    if(contact?.applicationUrl){try{const url=new URL(contact.applicationUrl);if(url.protocol!=='https:'||url.username||url.password)return null;url.searchParams.set('amount',String(value));url.searchParams.set('installments',String(term));url.searchParams.set('installment_frequency',config.installmentFrequency);return url.href;}catch{return null;}}
    const phone=String(contact?.whatsapp||'').replace(/[\s()+.-]/g,'');
    if(!/^55\d{10,11}$/.test(phone))return null;
    return 'https://wa.me/'+phone+'?text='+encodeURIComponent('Olá! Quero solicitar um empréstimo de '+money.format(value)+' em '+term+' parcelas semanais. Gostaria de conferir as condições.');
  }
  const setText=(id,text)=>{d.getElementById(id).textContent=text;};
  function getPreliminaryPricing(value,term){
    const policy=config.preliminaryPricing,a=Number(value),n=Number(term);
    if(!policy?.enabled||!Number.isFinite(a)||a<config.minAmount||a>config.maxAmount||(a-config.minAmount)%config.amountStep!==0||!config.terms.includes(n))return null;
    const rate=Math.expm1(Math.log1p(config.monthlyRateMin)*policy.daysPerWeek/policy.daysPerMonth);
    const raw=rate===0?a/n:a*rate/(-Math.expm1(-n*Math.log1p(rate))),payment=Math.round(raw*100)/100;
    return Object.freeze({amount_requested:a,installments_requested:n,installment_frequency:'weekly',installment_amount:payment,total_amount:Math.round(payment*100)*n/100,raw_installment_amount:raw,monthly_rate:config.monthlyRateMin,weekly_rate:rate,is_preliminary:true,excludes:['iof','fees']});
  }
  function positionAmountBubble(){
    const bubble=d.getElementById('quote-amount-display'),width=range.clientWidth,bubbleWidth=bubble.offsetWidth;
    if(!width||!bubbleWidth)return;
    const progress=(amount-config.minAmount)/(config.maxAmount-config.minAmount),target=15+(width-30)*progress;
    const center=Math.min(width-bubbleWidth/2,Math.max(bubbleWidth/2,target));
    bubble.style.left=center+'px';bubble.style.setProperty('--tip-left',(target-center+bubbleWidth/2)+'px');
  }
  function updateSummary(){
    const q=getQuote(amount,weeks),initial=getPreliminaryPricing(amount,weeks);setText('summary-amount',money.format(amount));setText('summary-weeks',weeks+' parcelas semanais');setText('summary-payment',q?money.format(q.installment_amount):initial?money.format(initial.installment_amount)+' (valor inicial, antes de encargos)':'A confirmar');
    for(const [id,key,format] of [['total','total_amount',money.format],['rate','monthly_rate',percent],['cet','annual_cet',percent]]){d.getElementById('summary-'+id+'-row').hidden=!q;setText('summary-'+id,q?format(q[key]):'');}
    const url=buildApplicationUrl(config.contact,amount,weeks);d.getElementById('application-action').disabled=false;
    setText('application-status','Continue para preencher seu cadastro. A aprovação depende de análise de crédito.');
  }
  function render(){
    range.value=String(amount);range.style.setProperty('--range-progress',(amount-config.minAmount)/(config.maxAmount-config.minAmount)*100+'%');range.setAttribute('aria-valuetext',money.format(amount));setText('quote-amount-display','R$ '+amount);
    positionAmountBubble();
    for(const radio of form.querySelectorAll('[name="weeks"]'))radio.checked=Number(radio.value)===weeks;
    for(const button of form.querySelectorAll('[data-amount-adjust]'))button.disabled=Number(button.dataset.amountAdjust)<0?amount===config.minAmount:amount===config.maxAmount;
    const q=getQuote(amount,weeks),initial=getPreliminaryPricing(amount,weeks);d.getElementById('quote-result').classList.toggle('has-quote',Boolean(q||initial));d.getElementById('quote-total-row').hidden=!q;
    setText('quote-payment-label',q?'Sua parcela semanal será de:':'Sua parcela semanal a partir de:');
    setText('quote-payment-display',q?money.format(q.installment_amount):initial?money.format(initial.installment_amount):(quoteStatus==='loading'?'Consultando…':'A confirmar'));
    setText('quote-payment-duration',q?weeks+' parcelas semanais • Taxa de '+percent(q.monthly_rate)+' a.m.':'Parcela e custos apresentados na proposta, antes da contratação.');
    setText('quote-monthly-rate-display',q?percent(q.monthly_rate):rateRange());
    setText('quote-annual-rate-display',q?percent(annualInterest(q.monthly_rate)):rateRange(true));
    setText('quote-iof-display',q?.iof_amount!=null?money.format(q.iof_amount):'Informado na proposta');
    setText('quote-fees-display',q?.fees_amount!=null?money.format(q.fees_amount):'Informadas na proposta');
    setText('quote-cet-display',q?percent(q.annual_cet):'Informado na proposta');
    d.querySelector('.pricing-explanation').hidden=Boolean(q)||!initial;
    setText('loan-rate-info',q?'Taxa de juros: '+percent(q.monthly_rate)+' ao mês (equivalente a '+percent(annualInterest(q.monthly_rate))+' ao ano).':'Taxa de juros: de '+rateRange()+' ao mês (equivalente a '+rateRange(true)+' ao ano).');
    setText('quote-total-display',q?money.format(q.total_amount):'');setText('quote-status',q?'CET: '+percent(q.annual_cet)+' a.a. Crédito sujeito à análise.':quoteStatus==='error'?'Não foi possível consultar as condições agora. Tente novamente mais tarde.':'A aprovação e as condições dependem da análise de crédito.');
    if(d.getElementById('request-dialog').open)updateSummary();
  }
  function setAmount(value){if(value===null||value===''||!Number.isFinite(Number(value)))return;amount=Math.min(config.maxAmount,Math.max(config.minAmount,config.minAmount+Math.round((Number(value)-config.minAmount)/config.amountStep)*config.amountStep));render();}
  function selectWeeks(value){if(config.terms.includes(Number(value))){weeks=Number(value);render();}}
  function loadQuotes(data){const next=normalizeQuotes(data);quotes=next;quoteStatus=next.length?'ready':'unavailable';render();return quotes.length;}
  range.addEventListener('input',()=>setAmount(range.value));range.addEventListener('change',()=>setAmount(range.value));
  for(const b of form.querySelectorAll('[data-amount-adjust]'))b.addEventListener('click',()=>setAmount(amount+Number(b.dataset.amountAdjust)*config.amountStep));
  for(const r of form.querySelectorAll('[name="weeks"]'))r.addEventListener('change',()=>{if(r.checked)selectWeeks(r.value);});
  function openDialog(dialog,trigger){requestTrigger=trigger||d.activeElement;dialog._returnFocus=requestTrigger;dialog.showModal();d.body.classList.add('dialog-open');}
  form.addEventListener('submit',event=>{event.preventDefault();updateSummary();openDialog(d.getElementById('request-dialog'),d.getElementById('home-page--get-quote-submit-btn'));});
  d.getElementById('application-action').addEventListener('click',()=>{d.getElementById('request-dialog').close();window.EmprestimoFacilRegistration.open();});
  for(const dialog of dialogs){
    for(const b of dialog.querySelectorAll('[data-close]'))b.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('cancel',()=>{if(!dialogs.some(x=>x!==dialog&&x.open))d.body.classList.remove('dialog-open');});
    dialog.addEventListener('close',()=>{if(!dialogs.some(x=>x.open))d.body.classList.remove('dialog-open');(dialog._returnFocus||requestTrigger)?.focus({preventScroll:true});});
    let outside=false;const onBackdrop=e=>{const r=dialog.getBoundingClientRect();return e.target===dialog&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);};
    dialog.addEventListener('pointerdown',e=>{outside=onBackdrop(e);});dialog.addEventListener('pointercancel',()=>{outside=false;});dialog.addEventListener('click',e=>{if(outside&&onBackdrop(e))dialog.close();outside=false;});
  }
  for(const button of d.querySelectorAll('[data-info]'))button.addEventListener('click',()=>{
    const privacy=button.dataset.info==='privacy';setText('info-title',privacy?'Sua privacidade':'Condições de contratação');setText('info-message',privacy?'Ao continuar a solicitação, o cadastro pede seus dados pessoais. Essas informações não são guardadas no armazenamento do navegador. Sem o serviço de envio habilitado, os dados não são transmitidos. Consulte a Política de Privacidade apresentada no cadastro antes de continuar.':'O crédito está sujeito à análise. Antes de contratar, confira a instituição responsável, a taxa de juros, os impostos, as tarifas, o total a pagar e o custo efetivo total (CET). Nunca pague uma taxa antecipada para liberar empréstimo.');openDialog(d.getElementById('info-dialog'),button);
  });
  const nav=d.getElementById('site-header--mobile-nav'),toggle=nav.querySelector('.mobile-nav--toggle'),navContent=d.getElementById('main-navigation'),desktop=window.matchMedia('(min-width: 900px)');
  function closeMenu(focus){nav.classList.remove('active');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menu');navContent.inert=!desktop.matches;if(focus)toggle.focus();}
  toggle.addEventListener('click',()=>{const open=!nav.classList.contains('active');nav.classList.toggle('active',open);toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'Fechar menu':'Abrir menu');navContent.inert=!open;});
  for(const link of navContent.querySelectorAll('a'))link.addEventListener('click',()=>closeMenu(false));d.addEventListener('keydown',e=>{if(e.key==='Escape'&&nav.classList.contains('active'))closeMenu(true);});d.addEventListener('click',e=>{if(nav.classList.contains('active')&&!nav.contains(e.target))closeMenu(false);});desktop.addEventListener('change',()=>closeMenu(false));closeMenu(false);
  setText('current-year',String(new Date().getFullYear()));
  window.addEventListener('resize',positionAmountBubble);
  if('ResizeObserver' in window)new window.ResizeObserver(positionAmountBubble).observe(range);
  d.fonts?.ready?.then(positionAmountBubble);
  window.EmprestimoFacil=Object.freeze({setAmount,selectWeeks,loadQuotes,getQuote,getPreliminaryPricing,buildApplicationUrl,getState:()=>({amount,weeks,installmentFrequency:config.installmentFrequency,quote:getQuote(amount,weeks),preliminaryPricing:getPreliminaryPricing(amount,weeks),quoteStatus,applicationReady:Boolean(window.EmprestimoFacilRegistration),submissionReady:Boolean(window.EmprestimoFacilRegistration?.getState().serviceReady)})});
  try{loadQuotes(config.quotes);}catch{quoteStatus='error';render();}
  async function fetchQuotes(){
    if(!config.quotesUrl)return;
    let url;try{url=new URL(config.quotesUrl,location.href);if(url.protocol!=='https:'&&url.origin!==location.origin)throw new Error('Invalid quote URL');url.searchParams.set('installment_frequency',config.installmentFrequency);}catch{quoteStatus='error';render();return;}
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);quoteStatus='loading';render();
    try{const response=await fetch(url.href,{signal:controller.signal,credentials:'omit',cache:'no-store'});if(!response.ok)throw new Error('Quote service unavailable');loadQuotes(await response.json());}catch{quotes=[];quoteStatus='error';render();}finally{clearTimeout(timeout);}
  }
  fetchQuotes();
})();
