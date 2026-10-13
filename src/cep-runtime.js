(function(){
  'use strict';
  const d=document,cep=d.getElementById('reg-cep'),status=d.getElementById('cep-lookup-status'),form=d.getElementById('registration-form');
  const names={street:'logradouro',district:'bairro',city:'localidade',state:'uf'},input=name=>form.elements.namedItem(name),digits=s=>String(s).replace(/\D/g,'');
  let timer=null,controller=null,sequence=0,autoValues={},lastLookup=null;
  function cancel(){clearTimeout(timer);controller?.abort();controller=null;sequence++;cep.removeAttribute('aria-busy');}
  function clearAuto(){for(const name of Object.keys(names))if(autoValues[name]&&input(name).value===autoValues[name])input(name).value='';autoValues={};}
  async function lookup(){const code=digits(cep.value);if(code.length!==8||code===lastLookup||d.querySelector('[data-step="1"]').hidden)return;const own=++sequence,snapshot=Object.fromEntries(Object.keys(names).map(name=>[name,input(name).value]));const requestController=new AbortController();controller=requestController;const signal=requestController.signal,timeout=setTimeout(()=>requestController.abort(),8000);cep.setAttribute('aria-busy','true');status.textContent='Buscando endereço…';
    try{const response=await fetch('https://viacep.com.br/ws/'+code+'/json/',{signal,credentials:'omit',referrerPolicy:'no-referrer'});if(!response.ok)throw Error('CEP unavailable');const data=await response.json();if(own!==sequence||digits(cep.value)!==code||d.querySelector('[data-step="1"]').hidden)return;
      if(data.erro){lastLookup=code;status.textContent='CEP não encontrado. Confira o número ou preencha o endereço manualmente.';return;}
      if(digits(data.cep)!==code||typeof data.localidade!=='string'||!data.localidade.trim()||!Array.from(input('state').options).some(o=>o.value===data.uf&&o.value))throw Error('Invalid address');
      for(const [name,key] of Object.entries(names)){const text=typeof data[key]==='string'?data[key].trim():'';if(text.length>150)throw Error('Invalid field');if(input(name).value===snapshot[name]&&(!snapshot[name]||snapshot[name]===autoValues[name])){input(name).value=text;autoValues[name]=text;}}
      lastLookup=code;form.dispatchEvent(new Event('input',{bubbles:true}));status.textContent=data.logradouro&&data.bairro?'Endereço encontrado. Confira as informações e preencha o número.':'CEP encontrado. Complete os campos de endereço que ficaram em branco.';
    }catch{if(own===sequence)status.textContent='Não foi possível consultar o CEP. Você pode preencher o endereço manualmente.';}finally{clearTimeout(timeout);if(own===sequence){controller=null;cep.removeAttribute('aria-busy');}}
  }
  cep.addEventListener('input',()=>{cancel();lastLookup=null;clearAuto();status.textContent='Digite o CEP para preencher o endereço automaticamente.';if(digits(cep.value).length===8)timer=setTimeout(lookup,300);});
  cep.addEventListener('blur',()=>{if(digits(cep.value).length===8&&!controller){clearTimeout(timer);lookup();}});
  d.addEventListener('registration-step-change',()=>{if(d.querySelector('[data-step="1"]').hidden)cancel();else if(digits(cep.value).length===8&&!controller)timer=setTimeout(lookup,300);});
  d.addEventListener('registration-exit',cancel);
})();
