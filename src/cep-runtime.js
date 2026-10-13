(function(){
  'use strict';
  function attach({form,cep,status,retry,isActive,stepEvent,exitEvent}){
  const d=document;
  const names={street:'logradouro',district:'bairro',city:'localidade',state:'uf'},input=name=>form.elements.namedItem(name),digits=s=>String(s).replace(/\D/g,''),cache=new Map();
  const providers=[code=>'https://viacep.com.br/ws/'+code+'/json/',code=>'https://opencep.com/v1/'+code+'.json'];
  let timer=null,controller=null,sequence=0,autoValues={},lastLookup=null;
  function cancel(){clearTimeout(timer);controller?.abort();controller=null;sequence++;cep.removeAttribute('aria-busy');retry.disabled=false;}
  function clearAuto(){for(const name of Object.keys(names))if(autoValues[name]&&input(name).value===autoValues[name])input(name).value='';autoValues={};}
  function normalize(data,code){
    if(!data||typeof data!=='object'||Array.isArray(data))throw Error('Invalid address');
    if(data.erro===true||data.erro==='true')return null;
    if(digits(data.cep)!==code||typeof data.localidade!=='string'||!data.localidade.trim()||typeof data.uf!=='string')throw Error('Invalid address');
    const address={};for(const [name,key] of Object.entries(names)){if(data[key]!=null&&typeof data[key]!=='string')throw Error('Invalid field');const text=(data[key]||'').trim();if(text.length>(name==='street'?150:100))throw Error('Invalid field');address[name]=name==='state'?text.toUpperCase():text;}
    if(!Array.from(input('state').options).some(o=>o.value===address.state&&o.value))throw Error('Invalid state');
    return Object.freeze(address);
  }
  async function request(url,code,parentSignal){
    const child=new AbortController(),abort=()=>child.abort();parentSignal.addEventListener('abort',abort,{once:true});if(parentSignal.aborted)child.abort();const timeout=setTimeout(abort,5000);
    try{const response=await fetch(url,{signal:child.signal,credentials:'omit',referrerPolicy:'no-referrer',redirect:'error'});if(response.status===404)return null;if(!response.ok)throw Error('CEP unavailable');return normalize(await response.json(),code);}finally{clearTimeout(timeout);parentSignal.removeEventListener('abort',abort);}
  }
  async function lookup(){
    const code=digits(cep.value);if(code.length!==8||code===lastLookup||!isActive())return;if(code==='00000000'||code==='99999999'){lastLookup=code;status.textContent='CEP não encontrado. Confira o número ou preencha o endereço manualmente.';retry.hidden=true;return;}
    const own=++sequence,snapshot=Object.fromEntries(Object.keys(names).map(name=>[name,input(name).value])),coordinator=new AbortController();controller=coordinator;cep.setAttribute('aria-busy','true');retry.hidden=true;retry.disabled=true;status.textContent='Buscando endereço…';
    const current=()=>own===sequence&&digits(cep.value)===code&&isActive()&&!coordinator.signal.aborted;
    try{
      let address=cache.get(code),unavailable=0,notFound=0;
      if(!address)for(const provider of providers){try{address=await request(provider(code),code,coordinator.signal);if(!current())return;if(address)break;notFound++;}catch{if(!current())return;unavailable++;}}
      if(!current())return;lastLookup=code;
      if(!address){status.textContent=notFound===providers.length&&unavailable===0?'CEP não encontrado. Confira o número ou preencha o endereço manualmente.':'Não foi possível consultar o CEP agora. Tente novamente ou preencha o endereço manualmente.';retry.hidden=false;return;}
      cache.set(code,address);if(cache.size>50)cache.delete(cache.keys().next().value);
      for(const name of Object.keys(names))if(input(name).value===snapshot[name]&&(!snapshot[name]||snapshot[name]===autoValues[name])){input(name).value=address[name];autoValues[name]=address[name];}
      form.dispatchEvent(new Event('input',{bubbles:true}));status.textContent=address.street&&address.district?'Endereço encontrado. Confira as informações e preencha o número.':'CEP encontrado. Complete os campos de endereço que ficaram em branco.';
    }finally{if(own===sequence){controller=null;cep.removeAttribute('aria-busy');retry.disabled=false;}}
  }
  cep.addEventListener('input',()=>{cancel();lastLookup=null;clearAuto();retry.hidden=true;status.textContent='Digite o CEP para preencher o endereço automaticamente.';if(digits(cep.value).length===8)timer=setTimeout(lookup,300);});
  cep.addEventListener('blur',event=>{if(event.relatedTarget!==retry&&digits(cep.value).length===8&&!controller){clearTimeout(timer);lookup();}});
  retry.addEventListener('click',()=>{cancel();lastLookup=null;cache.delete(digits(cep.value));lookup();});
  d.addEventListener(stepEvent,()=>{if(!isActive())cancel();else if(digits(cep.value).length===8&&!controller)timer=setTimeout(lookup,300);});
  d.addEventListener(exitEvent,cancel);
  }
  window.EmprestimoFacilCEP=Object.freeze({attach});
  attach({form:document.getElementById('registration-form'),cep:document.getElementById('reg-cep'),status:document.getElementById('cep-lookup-status'),retry:document.getElementById('cep-retry'),isActive:()=>!document.querySelector('[data-step="1"]').hidden,stepEvent:'registration-step-change',exitEvent:'registration-exit'});
})();
