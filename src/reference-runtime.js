(function () {
  'use strict';
  const config = JSON.parse(document.getElementById('business-config').textContent);
  const d = document, form = d.getElementById('home-page--get-quote'), range = d.getElementById('quote_amount');
  const money = new Intl.NumberFormat('pt-BR', {style:'currency',currency:'BRL'});
  const percent = value => new Intl.NumberFormat('pt-BR',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
  const dialogs = [...d.querySelectorAll('dialog')];
  let amount=config.initialAmount,months=config.initialMonths,quotes=[],quoteStatus='unavailable',requestTrigger;
  function normalizeQuotes(data) {
    const rows=Array.isArray(data)?data:data?.quotes;
    if(!Array.isArray(rows))throw new TypeError('Invalid quote response');
    const keys=new Set();
    return rows.map(row=>{
      const a=Number(row.amount_requested),n=Number(row.installments_requested);
      const p=Number(row.installment_amount),total=Number(row.total_amount),rate=Number(row.monthly_rate),cet=Number(row.annual_cet);
      const required=['amount_requested','installments_requested','installment_amount','total_amount','monthly_rate','annual_cet'];
      if(required.some(k=>!['number','string'].includes(typeof row[k])||String(row[k]).trim()==='')||![a,n,p,total,rate,cet].every(Number.isFinite)||a<config.minAmount||a>config.maxAmount||(a-config.minAmount)%config.amountStep!==0||!config.terms.includes(n)||p<=0||total<a||Math.abs(Math.round(total*100)-Math.round(p*100)*n)>1||rate<0||rate>1||cet<0)throw new TypeError('Incomplete or inconsistent quote');
      const key=a+':'+n;if(keys.has(key))throw new TypeError('Duplicate quote');keys.add(key);
      if(row.expires_at&&!Number.isFinite(Date.parse(row.expires_at)))throw new TypeError('Invalid quote expiry');
      return Object.freeze({amount_requested:a,installments_requested:n,installment_amount:p,total_amount:total,monthly_rate:rate,annual_cet:cet,expires_at:row.expires_at||null});
    });
  }
  function getQuote(value,term){return quotes.find(q=>q.amount_requested===Number(value)&&q.installments_requested===Number(term)&&(!q.expires_at||Date.parse(q.expires_at)>Date.now()))||null;}
  function buildApplicationUrl(contact,value,term){
    if(!Number.isFinite(Number(value))||Number(value)<config.minAmount||Number(value)>config.maxAmount||(Number(value)-config.minAmount)%config.amountStep!==0||!config.terms.includes(Number(term)))return null;
    if(contact?.applicationUrl){try{const url=new URL(contact.applicationUrl);if(url.protocol!=='https:'||url.username||url.password)return null;url.searchParams.set('amount',String(value));url.searchParams.set('installments',String(term));return url.href;}catch{return null;}}
    const phone=String(contact?.whatsapp||'').replace(/[\s()+.-]/g,'');
    if(!/^55\d{10,11}$/.test(phone))return null;
    return 'https://wa.me/'+phone+'?text='+encodeURIComponent('Olá! Quero solicitar um empréstimo de '+money.format(value)+' em '+term+' parcelas. Gostaria de conferir as condições.');
  }
  const setText=(id,text)=>{d.getElementById(id).textContent=text;};
  function updateSummary(){
    const q=getQuote(amount,months);setText('summary-amount',money.format(amount));setText('summary-months',months+' parcelas mensais');setText('summary-payment',q?money.format(q.installment_amount):'A confirmar');
    for(const [id,key,format] of [['total','total_amount',money.format],['rate','monthly_rate',percent],['cet','annual_cet',percent]]){d.getElementById('summary-'+id+'-row').hidden=!q;setText('summary-'+id,q?format(q[key]):'');}
    const url=buildApplicationUrl(config.contact,amount,months);d.getElementById('application-action').disabled=!url;
    setText('application-status',url?'A solicitação continua no atendimento. A aprovação depende de análise de crédito.':'O atendimento para novas solicitações está temporariamente indisponível. Tente novamente mais tarde.');
  }
  function render(){
    range.value=String(amount);range.style.setProperty('--range-progress',(amount-config.minAmount)/(config.maxAmount-config.minAmount)*100+'%');range.setAttribute('aria-valuetext',money.format(amount));setText('quote-amount-display',money.format(amount));
    for(const radio of form.querySelectorAll('[name="months"]'))radio.checked=Number(radio.value)===months;
    for(const button of form.querySelectorAll('[data-amount-adjust]'))button.disabled=Number(button.dataset.amountAdjust)<0?amount===config.minAmount:amount===config.maxAmount;
    const q=getQuote(amount,months);d.getElementById('quote-result').classList.toggle('has-quote',Boolean(q));d.getElementById('quote-total-row').hidden=!q;
    setText('quote-payment-display',q?money.format(q.installment_amount):(quoteStatus==='loading'?'Consultando condições…':'Consulte as condições'));
    setText('quote-payment-duration',q?months+' parcelas mensais • Taxa de '+percent(q.monthly_rate)+' a.m.':'Taxa, parcela e CET informados antes da contratação.');
    setText('quote-total-display',q?money.format(q.total_amount):'');setText('quote-status',q?'CET: '+percent(q.annual_cet)+' a.a. Crédito sujeito à análise.':quoteStatus==='error'?'Não foi possível consultar as condições agora. Tente novamente mais tarde.':'A aprovação e as condições dependem da análise de crédito.');
    if(d.getElementById('request-dialog').open)updateSummary();
  }
  function setAmount(value){if(value===null||value===''||!Number.isFinite(Number(value)))return;amount=Math.min(config.maxAmount,Math.max(config.minAmount,config.minAmount+Math.round((Number(value)-config.minAmount)/config.amountStep)*config.amountStep));render();}
  function selectMonths(value){if(config.terms.includes(Number(value))){months=Number(value);render();}}
  function loadQuotes(data){const next=normalizeQuotes(data);quotes=next;quoteStatus=next.length?'ready':'unavailable';render();return quotes.length;}
  range.addEventListener('input',()=>setAmount(range.value));range.addEventListener('change',()=>setAmount(range.value));
  for(const b of form.querySelectorAll('[data-amount-adjust]'))b.addEventListener('click',()=>setAmount(amount+Number(b.dataset.amountAdjust)*config.amountStep));
  for(const r of form.querySelectorAll('[name="months"]'))r.addEventListener('change',()=>{if(r.checked)selectMonths(r.value);});
  function openDialog(dialog,trigger){requestTrigger=trigger||d.activeElement;dialog.showModal();d.body.classList.add('dialog-open');}
  form.addEventListener('submit',event=>{event.preventDefault();updateSummary();openDialog(d.getElementById('request-dialog'),d.getElementById('home-page--get-quote-submit-btn'));});
  d.getElementById('application-action').addEventListener('click',()=>{const url=buildApplicationUrl(config.contact,amount,months);if(url)window.open(url,'_blank','noopener,noreferrer');});
  for(const dialog of dialogs){
    for(const b of dialog.querySelectorAll('[data-close]'))b.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('cancel',()=>{if(!dialogs.some(x=>x!==dialog&&x.open))d.body.classList.remove('dialog-open');});
    dialog.addEventListener('close',()=>{if(!dialogs.some(x=>x.open))d.body.classList.remove('dialog-open');requestTrigger?.focus({preventScroll:true});});
    let outside=false;const onBackdrop=e=>{const r=dialog.getBoundingClientRect();return e.target===dialog&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom);};
    dialog.addEventListener('pointerdown',e=>{outside=onBackdrop(e);});dialog.addEventListener('pointercancel',()=>{outside=false;});dialog.addEventListener('click',e=>{if(outside&&onBackdrop(e))dialog.close();outside=false;});
  }
  for(const button of d.querySelectorAll('[data-info]'))button.addEventListener('click',()=>{
    const privacy=button.dataset.info==='privacy';setText('info-title',privacy?'Sua privacidade':'Condições de contratação');setText('info-message',privacy?'Esta página não pede CPF, senha ou dados bancários para escolher o valor e o prazo. A continuidade da solicitação acontece no canal de atendimento indicado. Confira as informações de privacidade desse canal antes de fornecer dados pessoais.':'O crédito está sujeito à análise. Antes de contratar, confira a instituição responsável, a taxa de juros, os impostos, as tarifas, o total a pagar e o custo efetivo total (CET). Nunca pague uma taxa antecipada para liberar empréstimo.');openDialog(d.getElementById('info-dialog'),button);
  });
  const nav=d.getElementById('site-header--mobile-nav'),toggle=nav.querySelector('.mobile-nav--toggle'),navContent=d.getElementById('main-navigation'),desktop=window.matchMedia('(min-width: 900px)');
  function closeMenu(focus){nav.classList.remove('active');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menu');navContent.inert=!desktop.matches;if(focus)toggle.focus();}
  toggle.addEventListener('click',()=>{const open=!nav.classList.contains('active');nav.classList.toggle('active',open);toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'Fechar menu':'Abrir menu');navContent.inert=!open;});
  for(const link of navContent.querySelectorAll('a'))link.addEventListener('click',()=>closeMenu(false));d.addEventListener('keydown',e=>{if(e.key==='Escape'&&nav.classList.contains('active'))closeMenu(true);});d.addEventListener('click',e=>{if(nav.classList.contains('active')&&!nav.contains(e.target))closeMenu(false);});desktop.addEventListener('change',()=>closeMenu(false));closeMenu(false);
  setText('current-year',String(new Date().getFullYear()));
  window.EmprestimoFacil=Object.freeze({setAmount,selectMonths,loadQuotes,getQuote,buildApplicationUrl,getState:()=>({amount,months,quote:getQuote(amount,months),quoteStatus,applicationReady:Boolean(buildApplicationUrl(config.contact,amount,months))})});
  try{loadQuotes(config.quotes);}catch{quoteStatus='error';render();}
  async function fetchQuotes(){
    if(!config.quotesUrl)return;
    let url;try{url=new URL(config.quotesUrl,location.href);if(url.protocol!=='https:'&&url.origin!==location.origin)throw new Error('Invalid quote URL');}catch{quoteStatus='error';render();return;}
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);quoteStatus='loading';render();
    try{const response=await fetch(url.href,{signal:controller.signal,credentials:'omit',cache:'no-store'});if(!response.ok)throw new Error('Quote service unavailable');loadQuotes(await response.json());}catch{quotes=[];quoteStatus='error';render();}finally{clearTimeout(timeout);}
  }
  fetchQuotes();
})();
