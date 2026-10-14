(function(){
  'use strict';
  const d=document, digits=s=>String(s).replace(/\D/g,''), bindings=new WeakMap();
  const icons={
    person:'M20 21v-2a7 7 0 0 0-14 0v2M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z',
    mail:'M3 5h18v14H3zM3 5l9 7 9-7',
    phone:'M8 3H5a2 2 0 0 0-2 2c0 9 7 16 16 16a2 2 0 0 0 2-2v-3l-5-2-2 2a13 13 0 0 1-6-6l2-2z',
    lock:'M5 10h14v11H5zM8 10V7a4 4 0 0 1 8 0v3M12 14v3',
    calendar:'M4 5h16v16H4zM4 10h16M8 3v4M16 3v4M8 14h2M14 14h2',
    document:'M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h5',
    pin:'M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 1 1 16 0ZM12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z',
    home:'M3 11l9-8 9 8M5 9v12h14V9M10 21v-7h4v7',
    link:'M10 14l4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0',
    work:'M3 7h18v14H3zM8 7V3h8v4M3 12h18M10 12v3h4v-3',
    bank:'M3 9l9-6 9 6H3ZM5 12v6M10 12v6M14 12v6M19 12v6M3 21h18'
  };
  function grouped(raw,groups,separators){const n=digits(raw).slice(0,groups.reduce((a,b)=>a+b,0));let out='',offset=0;groups.forEach((size,i)=>{if(n.length>offset)out+=(i?separators[i-1]:'')+n.slice(offset,offset+size);offset+=size;});return out;}
  function nationalPhone(raw){let n=digits(raw);if((n.length===12||n.length===13)&&n.startsWith('55'))n=n.slice(2);return n.slice(0,11);}
  function format(kind,raw){
    if(kind==='cpf')return grouped(raw,[3,3,3,2],['.','.','-']);
    if(kind==='cep')return grouped(raw,[5,3],['-']);
    if(kind==='date'){if(/^\d{4}-\d{2}-\d{2}$/.test(raw))return raw.split('-').reverse().join('/');return grouped(raw,[2,2,4],['/','/']);}
    if(kind==='pixPhone'&&raw.trim().startsWith('+')&&!raw.trim().startsWith('+55'))return '+'+digits(raw).slice(0,15);
    if(kind==='phone'||kind==='pixPhone'){const n=nationalPhone(raw);if(!n)return '';const split=n.length===11||n[2]==='9'?7:6;return '('+n.slice(0,2)+(n.length>2?') '+n.slice(2,split):'')+(n.length>split?'-'+n.slice(split):'');}
    return raw;
  }
  function describe(field,id){field.setAttribute('aria-describedby',[...new Set(((field.getAttribute('aria-describedby')||'')+' '+id).trim().split(/\s+/))].join(' '));}
  function resolved(field){const kind=bindings.get(field);return typeof kind==='function'?kind():kind;}
  function refresh(field){const kind=resolved(field);if(kind)field.value=format(kind,field.value);}
  function mask(field,binding){
    bindings.set(field,binding);field.dataset.fieldMask=typeof binding==='function'?'pix':binding;
    field.addEventListener('beforeinput',e=>{
      if(!resolved(field)||e.isComposing||!['deleteContentBackward','deleteContentForward'].includes(e.inputType)||field.selectionStart!==field.selectionEnd)return;
      const start=field.selectionStart,back=e.inputType==='deleteContentBackward',raw=field.value;let at=back?start-1:start;
      if(at<0||at>=raw.length||/\d/.test(raw[at]))return;
      while(at>=0&&at<raw.length&&!/\d/.test(raw[at]))at+=back?-1:1;
      if(at<0||at>=raw.length)return;
      e.preventDefault();field.setRangeText('',back?at:start,back?start:at+1,'start');field.dispatchEvent(new Event('input',{bubbles:true}));
    });
    field.addEventListener('input',e=>{
      const kind=resolved(field);if(!kind||e.isComposing)return;
      const raw=field.value,start=field.selectionStart??raw.length,end=field.selectionEnd??start,formatted=format(kind,raw);
      const n=digits(raw),removed=['phone','pixPhone'].includes(kind)&&[12,13].includes(n.length)&&n.startsWith('55')?2:0;
      const position=cursor=>{let count=Math.max(0,digits(raw.slice(0,cursor)).length-removed);if(!count)return 0;let seen=0;for(let i=0;i<formatted.length;i++)if(/\d/.test(formatted[i])&&++seen===count)return i+1;return formatted.length;};
      field.value=formatted;
      const iso=kind==='date'&&/^\d{4}-\d{2}-\d{2}$/.test(raw);
      field.setSelectionRange(iso?formatted.length:position(start),iso?formatted.length:position(end));
    });
    field.addEventListener('change',()=>refresh(field));refresh(field);
  }
  const specs={
    full_name:['person','Seu nome completo','Informe nome e sobrenome, como aparecem no documento.'],
    email:['mail','seu@email.com','Use um e-mail que você acessa.'],
    email_confirmation:['mail','Digite novamente seu e-mail','Repita o mesmo e-mail informado anteriormente.'],
    phone:['phone','(00) 00000-0000','Informe seu celular com DDD. A formatação é automática.','phone'],
    cpf:['document','000.000.000-00','Digite os 11 números do CPF do titular.','cpf'],
    birth_date:['calendar','DD/MM/AAAA','Informe dia, mês e ano do seu nascimento.','date'],
    password:['lock','Crie sua senha',''],password_confirmation:['lock','Repita sua senha','Digite a mesma senha criada no campo anterior.'],
    postal_code:['pin','00000-000','','cep'],
    street:['home','Rua ou avenida','Confira o endereço preenchido pelo CEP.'],
    number:['home','Ex.: 123 ou S/N','Use S/N se o endereço não tiver número.'],
    complement:['home','Ex.: apartamento 12, bloco B','Opcional. Informe apartamento, bloco ou referência.'],
    district:['pin','Nome do bairro',''],city:['pin','Nome da cidade',''],
    monthly_income:['currency','0,00','Informe sua renda por mês. Ex.: 2.500,00.'],
    income_source:['work','Ex.: aluguel de imóvel','Descreva de onde vem sua renda.'],
    branch:['bank','Ex.: 1234','Informe a agência; inclua o dígito somente se houver.'],
    account:['bank','Ex.: 123456-7','Separe o número da conta e o dígito com hífen.'],
    pix_key:['document',null,''],
    trade_name:['work','Seu nome ou nome do negócio','Informe o nome que você usa na sua atividade.'],
    support_email:['mail','atendimento@exemplo.com',''],
    support_phone:['phone','(00) 00000-0000','Pode ser telefone fixo ou celular, com DDD.','phone'],
    support_site:['link','https://seusite.com.br','Informe o endereço completo, começando com https://.'],
    activity:['work','Ex.: comércio de roupas','Descreva sua principal atividade profissional.'],
    document_number:['document',null,'Informe o número do documento selecionado.'],
    issue_date:['calendar','DD/MM/AAAA','Use a data de expedição indicada no documento.','date'],
    address_proof_date:['calendar','DD/MM/AAAA','Informe a data de emissão do comprovante.','date'],
    instagram:['link','instagram.com/seuusuario','Cole o link do seu perfil no Instagram.']
  };
  for(const form of [d.getElementById('registration-form'),d.getElementById('pf-form')]){
    if(!form)continue;
    for(const field of form.querySelectorAll('.application-field input, .application-field textarea')){
      const contact=field.name.match(/^contact_(name|phone)_\d$/),spec=contact?(contact[1]==='name'?['person','Nome completo do familiar','']:['phone','(00) 00000-0000','Informe o telefone com DDD.','phone']):specs[field.name];
      if(!spec)continue;
      const [icon,placeholder,hint,kind]=spec,container=field.closest('.application-field');
      if(placeholder!==null)field.placeholder=placeholder;
      const autofill={full_name:'name',email:'email',phone:'tel-national',birth_date:'bday',postal_code:'postal-code',street:'address-line1',complement:'address-line2',city:'address-level2'};
      if(autofill[field.name])field.autocomplete=autofill[field.name];
      if(['mail','link','lock'].includes(icon)){field.setAttribute('autocapitalize','none');field.spellcheck=false;}
      if(kind)field.inputMode=kind==='phone'?'tel':'numeric';
      if(field.tagName==='INPUT'){
        let wrap=field.parentElement;
        if(!wrap.classList.contains('password-input')){wrap=d.createElement('div');field.before(wrap);wrap.append(field);}
        wrap.classList.add('field-input');
        const visual=d.createElement('span');visual.className='field-input-icon';visual.setAttribute('aria-hidden','true');
        if(icon==='currency')visual.textContent='R$';else{const svg=d.createElementNS('http://www.w3.org/2000/svg','svg'),path=d.createElementNS(svg.namespaceURI,'path');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('focusable','false');path.setAttribute('d',icons[icon]);svg.append(path);visual.append(svg);}wrap.prepend(visual);
      }
      if(hint){const existing=container.querySelector('small:not(.field-error)');if(existing){if(!existing.id)existing.id=field.id+'-hint';describe(field,existing.id);}else{const small=d.createElement('small');small.id=field.id+'-hint';small.className='field-hint';small.textContent=hint;container.insertBefore(small,container.querySelector('.field-error'));describe(field,small.id);}}
      const error=container.querySelector('.field-error');if(error)describe(field,error.id);
      if(field.name==='postal_code')describe(field,form.id==='pf-form'?'pf-cep-status':'cep-lookup-status');
      if(kind){if(kind==='phone')field.maxLength=22;mask(field,kind);}
      if(field.name==='pix_key')mask(field,()=>{const type=form.elements.namedItem('pix_key_type').value;return type==='cpf'?'cpf':type==='phone'?'pixPhone':null;});
    }
  }
  // Keep programmatic prefill and the contact picker consistent with typed values.
  function refreshForm(form){for(const field of form.querySelectorAll('[data-field-mask]'))refresh(field);}
  window.EmprestimoFacilFields=Object.freeze({format,describe,refresh,refreshForm});
})();
