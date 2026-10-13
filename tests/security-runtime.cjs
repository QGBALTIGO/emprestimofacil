'use strict';
const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM,VirtualConsole}=require('jsdom');
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
async function settled(predicate){for(let i=0;i<100;i++){if(predicate())return;await tick();}assert.fail('Async operation did not settle');}
function setup(configured=true){
 const errors=[],requests=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const config={securityApiUrl:configured?'/api/security':null,pfApplicationsApiUrl:configured?'/api/pf/applications':null};
 const html='<script id="business-config" type="application/json">'+JSON.stringify(config)+'</script>'+fs.readFileSync('src/pf-body.html','utf8');
 const dom=new JSDOM(html,{url:'https://example.test',runScripts:'outside-only',virtualConsole:vc});
 const w=dom.window,d=w.document;for(const fieldset of d.querySelectorAll('fieldset'))fieldset.disabled=false;w.EmprestimoFacilRegistration={validCPF:cpf=>cpf==='11144477735'};w.AbortController=AbortController;
 let state={email_verified:false,phone_verified:false,bank_status:'not_connected',cpf_match:false},lastProfile=null,bankRequired=false,invalidReceipt=false,popupAllowed=true;
 const identity='a44d01e0-93c7-4d17-9c69-27e20f57b914',uploaded=[];
 w.open=()=>popupAllowed?{opener:w,closed:false,location:{replace(url){requests.push({path:'popup',url});}},close(){this.closed=true;}}:null;
 w.fetch=async(url,options={})=>{
  const path=new URL(url).pathname;requests.push({path,options});const body=options.body&&typeof options.body==='string'?JSON.parse(options.body):{};
  let result,status=200;
  if(path.endsWith('/capabilities'))result={verification:true,open_finance:true,pf_submission:true,bank_required:bankRequired,sandbox:true};
  else if(path==='/api/security/session'){
   if(lastProfile&&(lastProfile.email!==body.email||lastProfile.phone!==body.phone))state={email_verified:false,phone_verified:false,bank_status:'not_connected',cpf_match:false};
   lastProfile=body;result={...state};
  }else if(path.endsWith('/codes/send'))result={status:'sent',expires_in:600,retry_after:60};
  else if(path.endsWith('/codes/check')){
   if(body.code!=='123456'){status=400;result={detail:'Código inválido ou expirado.'};}
   else{state[body.channel==='email'?'email_verified':'phone_verified']=true;result={...state,status:'verified'};}
  }else if(path.endsWith('/bank/start')){state.bank_status='pending';result={status:'pending',widget_url:'https://widget.belvo.io/?access_token=synthetic-token'};}
  else if(path.endsWith('/bank/status'))result={...state};
  else if(path.endsWith('/bank')){state.bank_status='not_connected';state.cpf_match=false;result={...state};}
  else if(path==='/api/pf/applications')result={application_id:invalidReceipt?'-'.repeat(36):identity,status:'uploading',required_files:['front','back']};
  else if(path.includes('/files/')){uploaded.push(path.split('/').at(-1));result={status:'stored'};}
  else if(path.endsWith('/complete'))result={application_id:identity,status:'received'};
  else assert.fail('Unexpected endpoint '+path);
  return {ok:status<400,status,json:async()=>result};
 };
 w.eval(fs.readFileSync('src/security-runtime.js','utf8'));
 const field=(name,value)=>{const e=d.querySelector('[name="'+name+'"]');e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));};
 const click=id=>d.getElementById(id).click();
 field('full_name','Pessoa de Teste');field('email','pessoa@example.test');field('phone','11912345678');
 const verify=async()=>{for(const channel of ['email','sms']){click('pf-sec-'+channel+'-send');await settled(()=>d.getElementById('pf-sec-message').textContent.includes('Código enviado'));field('security_'+channel+'_code','123456');click('pf-sec-'+channel+'-check');await settled(()=>channel==='email'?w.EmprestimoFacilSecurity.getState().email_verified:w.EmprestimoFacilSecurity.getState().phone_verified);}};
 return {w,d,requests,errors,field,click,verify,uploaded,setState(value){state={...state,...value};},setRequired(v){bankRequired=v;},setReceipt(v){invalidReceipt=v;},setPopup(v){popupAllowed=v;},close(){dom.window.close();}};
}
(async()=>{
 const unavailable=setup(false);await tick();assert.equal(unavailable.requests.length,0);assert.equal(unavailable.d.getElementById('pf-sec-email-send').disabled,true);assert.equal(unavailable.w.EmprestimoFacilSecurity.getState().email_verified,false);unavailable.close();
 const env=setup();const {w,d,field,click}=env;await settled(()=>w.EmprestimoFacilSecurity.getState().verificationAvailable);
 assert.deepEqual(Array.from(w.EmprestimoFacilSecurity.validation('reg',0)),[]);
 await assert.rejects(()=>w.EmprestimoFacilSecurity.beforeSend('reg'),/apenas do cadastro PF/);
 assert.equal(w.EmprestimoFacilSecurity.validation('pf',0).length,2);
 click('pf-sec-email-send');await settled(()=>d.getElementById('pf-sec-message').textContent.includes('Código enviado'));
 assert.equal(d.getElementById('pf-sec-email-send').disabled,true);field('security_email_code','999999');click('pf-sec-email-check');await settled(()=>d.getElementById('pf-sec-message').textContent.includes('inválido'));assert.equal(w.EmprestimoFacilSecurity.getState().email_verified,false);
 field('security_email_code','123456');click('pf-sec-email-check');await settled(()=>w.EmprestimoFacilSecurity.getState().email_verified);
 click('pf-sec-sms-send');await settled(()=>d.getElementById('pf-sec-message').textContent.includes('Código enviado'));field('security_sms_code','123456');click('pf-sec-sms-check');await settled(()=>w.EmprestimoFacilSecurity.getState().phone_verified);
 assert.equal(w.EmprestimoFacilSecurity.validation('pf',0).length,0);field('email','outro@example.test');assert.equal(w.EmprestimoFacilSecurity.validation('pf',0).length,2);assert.equal(d.getElementById('pf-email-state').textContent,'Validação pendente');field('email','pessoa@example.test');
 field('cpf','11144477735');click('pf-bank-start');assert.equal(env.requests.filter(r=>r.path.endsWith('/bank/start')).length,0);await w.EmprestimoFacilSecurity.refresh();assert.match(d.getElementById('pf-bank-message').textContent,/autorização/);d.getElementById('pf-bank-consent').checked=true;
 env.setPopup(false);click('pf-bank-start');await tick();assert.equal(env.requests.filter(r=>r.path.endsWith('/bank/start')).length,0);env.setPopup(true);click('pf-bank-start');await settled(()=>env.requests.some(r=>r.path==='popup'));
 assert.equal(w.EmprestimoFacilSecurity.getState().bank_status,'pending');env.setRequired(true);await w.EmprestimoFacilSecurity.refresh();assert.equal(w.EmprestimoFacilSecurity.validation('pf',2).length,1);
 env.setState({bank_status:'connected'});click('pf-bank-check');await settled(()=>w.EmprestimoFacilSecurity.getState().bank_status==='connected');assert.equal(w.EmprestimoFacilSecurity.validation('pf',2).length,1);
 env.setState({bank_status:'data_ready',cpf_match:true});click('pf-bank-check');await settled(()=>w.EmprestimoFacilSecurity.getState().bank_status==='data_ready');assert.equal(w.EmprestimoFacilSecurity.validation('pf',2).length,0);assert.match(d.getElementById('pf-bank-status').textContent,/não representa aprovação/);
 const files=new Map(['front','back'].map(name=>[name,{file:new w.Blob(['synthetic'])}]));
 env.setReceipt(true);await assert.rejects(()=>w.EmprestimoFacilSecurity.submitPF({},files,'synthetic',()=>{}),/arquivos exigidos/);assert.equal(env.uploaded.length,0);
 env.setReceipt(false);const id=await w.EmprestimoFacilSecurity.submitPF({},files,'synthetic',()=>{});assert.match(id,/^[a-f0-9-]{36}$/);assert.deepEqual(env.uploaded,['front','back']);
 click('pf-bank-disconnect');await settled(()=>w.EmprestimoFacilSecurity.getState().bank_status==='not_connected');assert.match(d.getElementById('pf-bank-message').textContent,/revogação foi confirmada/);
 assert.equal(w.localStorage.length+w.sessionStorage.length,0);assert.equal(env.errors.length,0);assert.equal(env.requests.filter(r=>r.path.startsWith('/api/')).every(r=>r.options.credentials==='same-origin'),true);
 d.dispatchEvent(new w.Event('pf-exit'));assert.equal(w.EmprestimoFacilSecurity.getState().email_verified,false);assert.equal(d.getElementById('pf-sec-email-code').value,'');env.close();
 const registration=fs.readFileSync('src/registration-body.html','utf8');assert.equal(/reg-sec-|reg-bank-start|Open Finance/.test(registration),false);
 console.log(JSON.stringify({pf_only_codes:'passed',unavailable_service:'passed',invalid_codes:'passed',changed_contacts:'passed',bank_consent:'passed',popup_blocked:'passed',server_confirmed_bank:'passed',receipt_validation:'passed',private_upload_order:'passed',cleanup:'passed'}));
})().catch(error=>{console.error(error);process.exit(1);});
