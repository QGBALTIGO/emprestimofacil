'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM,VirtualConsole}=require('jsdom');
const errors=[],console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync('index.html','utf8'),{runScripts:'dangerously',url:'https://example.test',pretendToBeVisual:true,virtualConsole:console,beforeParse(w){w.matchMedia=()=>({matches:false,addEventListener(){}});w.Element.prototype.scrollIntoView=function(){};w.HTMLMediaElement.prototype.pause=function(){};}});
const w=dom.window,d=w.document,api=w.EmprestimoFacilFields;
for(const [kind,raw,want] of [
 ['cpf','11144477735','111.444.777-35'],['cpf','111.444.777-35','111.444.777-35'],['cpf','',''],
 ['phone','11912345678','(11) 91234-5678'],['phone','1132345678','(11) 3234-5678'],
 ['phone','+55 (21) 98765-4321','(21) 98765-4321'],['phone','+55 11 3234 5678','(11) 3234-5678'],
 ['phone','619','(61) 9'],['cep','01001000','01001-000'],['date','05072004','05/07/2004'],['date','2004-07-05','05/07/2004'],
 ['pixPhone','+14155552671','+14155552671']
])assert.equal(api.format(kind,raw),want);
function put(id,raw,start=raw.length,end=start){const f=d.getElementById(id);f.value=raw;f.setSelectionRange(start,end);f.dispatchEvent(new w.InputEvent('input',{bubbles:true,inputType:'insertText'}));return f;}
let cpf=put('reg-cpf','11144477735');assert.equal(cpf.value,'111.444.777-35');assert.equal(cpf.selectionStart,14);
cpf=put('reg-cpf','111.544.777-35',5);assert.equal(cpf.selectionStart,5);
cpf.setSelectionRange(4,4);const back=new w.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'deleteContentBackward'});cpf.dispatchEvent(back);assert(back.defaultPrevented);assert.equal(cpf.value,'115.447.773-5');assert.equal(cpf.selectionStart,2);
cpf=put('reg-cpf','111.444.777-35');cpf.setSelectionRange(3,3);cpf.dispatchEvent(new w.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'deleteContentForward'}));assert.equal(cpf.value,'111.447.773-5');assert.equal(cpf.selectionStart,3);
assert.equal(put('pf-phone','+5511912345678').value,'(11) 91234-5678');
assert.equal(put('pf-support_phone','1132345678').value,'(11) 3234-5678');
for(let i=1;i<=5;i++)assert.equal(put('pf-contact_phone_'+i,'21987654321').value,'(21) 98765-4321');
assert.equal(put('reg-birth','1990-01-01').value,'01/01/1990');assert.equal(put('pf-issue_date','01012010').value,'01/01/2010');
d.getElementById('reg-pix-type').value='cpf';assert.equal(put('reg-pix-key','11144477735').value,'111.444.777-35');
d.getElementById('reg-pix-type').value='phone';assert.equal(put('reg-pix-key','+').selectionStart,1);assert.equal(put('reg-pix-key','+14155552671').value,'+14155552671');
for(const id of ['reg-name','reg-email','reg-password-confirm','pf-instagram','pf-contact_phone_1']){
 const f=d.getElementById(id);assert(f.placeholder);assert(f.parentElement.querySelector('.field-input-icon[aria-hidden="true"]'));
 for(const ref of (f.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean))assert(d.getElementById(ref),ref);
}
w.EmprestimoFacilRegistration.open();d.getElementById('registration-next').click();assert.equal(d.getElementById('reg-name').getAttribute('aria-invalid'),'true');assert(d.getElementById('reg-name').getAttribute('aria-describedby').includes('reg-name-hint'));assert(d.getElementById('reg-name').getAttribute('aria-describedby').includes('reg-name-error'));
assert.deepEqual(errors,[]);dom.window.close();process.stdout.write('Field masks, editing, helpers and accessible errors: passed\n');
