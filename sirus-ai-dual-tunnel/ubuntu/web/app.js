'use strict';
const API='/ai/api', $=id=>document.getElementById(id);
const s={role:'guest',csrf:'',models:{},categories:[],id:null,convs:[],msgs:[],guest:[],busy:false,job:null};
const el=(tag,t)=>{const n=document.createElement(tag);n.textContent=String(t);return n;};
async function api(path,method='GET',body=null){
 const opt={method,credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}};
 if(method!=='GET')opt.headers['X-Sirus-CSRF']=s.csrf;
 if(body instanceof FormData)opt.body=body;
 else if(body!==null){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body);}
 const res=await fetch(API+path,opt),data=await res.json().catch(()=>({error:'Bad server response'}));
 if(!res.ok)throw Error(data.error||'Request rejected');return data;
}
function status(t){$('progress').textContent=t;}
function panel(id){for(const n of ['messages','memoryPanel','documentPanel'])$(n).hidden=n!==id;}
function draw(){
 const root=$('messages');root.replaceChildren();
 if(!s.msgs.length){let w=el('div','');w.className='welcome';w.append(el('h2','SirusSofts AI'),
  el('p','Local Gentoo inference. Sign in for saved conversations, recall and your 2 GiB memory library.'));
  root.append(w);return;}
 for(const m of s.msgs){const b=el('article','');b.className='message '+m.role;
 const h=el('div',m.role==='user'?'You':'SirusSofts AI');h.className='meta';
 b.append(h,el('div',m.content));root.append(b);}
 root.scrollTop=root.scrollHeight;
}
function nav(){
 const root=$('conversations');root.replaceChildren();
 if(s.role==='guest'){root.append(el('p','Guest chats last for this browser session.'));return;}
 for(const c of s.convs){const b=el('button',c.title);b.type='button';
 b.className=c.id===s.id?'active':'';
 b.onclick=()=>openConversation(c.id);root.append(b);}
}
async function refreshConversations(){
 if(s.role==='guest')return;
 s.convs=(await api('/conversations')).conversations||[];nav();
}
async function openConversation(id){
 if(s.busy)return;
 try{s.msgs=(await api('/conversations/'+id)).messages||[];
  s.id=id;nav();panel('messages');draw();status('');}
 catch(e){status(e.message);}
}
async function newChat(){
 if(s.busy)return;
 if(s.role==='guest'){s.id=null;s.msgs=[];s.guest=[];sessionStorage.removeItem('sirus-ai-guest-history');}
 else {try{s.id=(await api('/conversations','POST',{title:'New conversation'})).id;
   s.msgs=[];await refreshConversations();}catch(e){status(e.message);return;}}
 panel('messages');draw();$('prompt').focus();status('');
}
function inventory(){
 const all=new Set();
 for(const ids of Object.values(s.models))if(Array.isArray(ids))
  for(const id of ids)if(typeof id==='string')all.add(id);
 const select=$('model');select.replaceChildren();
 for(const id of [...all].sort())select.add(new Option(id+' · local',id));
 if(!all.size)select.add(new Option('No local models',''));
 if(all.has('sirussofts-coder'))select.value='sirussofts-coder';
 select.disabled=!all.size||s.role==='guest';
 $('send').disabled=!all.size||s.busy;
 $('aiStatus').textContent=all.size?'● Local models available':'○ Inference unavailable';
 $('aiStatus').className='status '+(all.size?'on':'off');
}
async function loadState(){
 const data=await api('/state');s.role=data.role;s.csrf=data.csrf;
 s.categories=data.categories||[];s.models=data.models||{};
 $('roleLabel').textContent=s.role==='admin'?'Administrator':s.role==='user'?'Registered user':'Guest · limited chat';
 $('accountLink').textContent=s.role==='guest'?'Sign in':'My account';
 $('accountLink').href=s.role==='guest'?'/admin/login':'/admin/account';
 $('adminLink').hidden=s.role!=='admin';$('gentooLink').hidden=s.role!=='admin';
 $('memoryToggle').hidden=s.role==='guest';$('documentsToggle').hidden=s.role==='guest';
 $('worker').hidden=s.role!=='admin';$('workerLabel').hidden=s.role!=='admin';
 $('quotaLabel').textContent=s.role==='guest'?'Sign in for persistent memory':
  (Number(data.used_bytes||0)/1048576).toFixed(1)+' MiB / 2048 MiB memories and chats';
 const profile=$('profile'),upload=$('documentProfile');
 profile.replaceChildren(new Option('General','auto'));upload.replaceChildren();
 for(const entry of s.categories){profile.add(new Option(entry.title,entry.slug));
   upload.add(new Option(entry.title,entry.slug));}
 $('uploadForm').querySelector('button').disabled=!s.categories.length;
 inventory();
}
async function memories(){
 if(s.role==='guest')return;
 const root=$('memoryEntries');root.replaceChildren();
 for(const m of (await api('/memories')).memories||[]){
  const card=el('article','');card.className='workspace-entry';
  card.append(el('p',m.content));
  if(m.source_url)card.append(el('small','Source: '+m.source_url));
  const del=el('button','Delete memory');del.type='button';del.className='new subtle';
  del.onclick=async()=>{if(!confirm('Delete memory?'))return;
    try{await api('/memories/'+m.id,'DELETE');await memories();await loadState();}
    catch(e){status(e.message);}};
  card.append(del);root.append(card);
 }
}
async function documents(){
 if(s.role==='guest')return;
 const root=$('documentEntries');root.replaceChildren();
 for(const d of (await api('/documents')).documents||[]){
  const card=el('article','');card.className='workspace-entry';
  card.append(el('b',d.title),el('p',d.profile+' · '+d.status+' · '+
    (d.status==='parsed'?d.pages+' parsed pages':d.processed_pages+'/'+d.total_pages+' pages processed')));
  if(d.error)card.append(el('small',d.error));root.append(card);
 }
}
async function send(event){
 event.preventDefault();if(s.busy)return;
 const text=$('prompt').value.trim();if(!text)return;
 if(s.role!=='guest'&&!s.id){await newChat();if(!s.id)return;}
 s.msgs.push({role:'user',content:text});$('prompt').value='';panel('messages');draw();
 s.busy=true;inventory();status('Sending to local Gentoo model…');
 try{
  const body={message:text,profile:$('profile').value,
   model_id:$('model').value,target:$('worker').value};
  if(s.role==='guest')body.history=s.guest;else body.conversation_id=s.id;
  const result=await api('/jobs','POST',body);s.job=result.job_id;
  while(s.busy&&s.job===result.job_id){
   try{
    const info=await api('/jobs/'+s.job);
    if(info.state==='completed'){
     if(s.role==='guest'){
      s.msgs.push({role:'assistant',content:info.output||'(empty answer)'});
      s.guest=s.msgs.slice(-5);
      sessionStorage.setItem('sirus-ai-guest-history',JSON.stringify(s.guest));
     }else{s.msgs=(await api('/conversations/'+s.id)).messages||[];await refreshConversations();}
     s.busy=false;s.job=null;draw();inventory();await loadState();
     status('Completed on '+(info.worker||'local worker'));return;
    }
    if(info.state==='error'){
     s.busy=false;s.job=null;inventory();
     status('Inference failed. Check the saved conversation before retrying.');return;
    }
    status(info.state==='running'?'Generating on Gentoo…':'Queued for inference…');
   }catch(e){status('Waiting for output: '+e.message);}
   await new Promise(resolve=>setTimeout(resolve,1700));
  }
 }catch(e){s.busy=false;s.job=null;inventory();status(e.message);}
}
$('composer').onsubmit=send;
$('prompt').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){
 e.preventDefault();$('composer').requestSubmit();}};
$('newChat').onclick=newChat;
$('memoryToggle').onclick=async()=>{panel('memoryPanel');try{await memories();}catch(e){status(e.message);}};
$('documentsToggle').onclick=async()=>{panel('documentPanel');try{await documents();}catch(e){status(e.message);}};
$('memoryForm').onsubmit=async e=>{
 e.preventDefault();
 try{await api('/memories','POST',{content:$('memoryText').value.trim(),
 source_url:$('memorySource').value.trim()});
 $('memoryText').value='';$('memorySource').value='';
 await memories();await loadState();status('Memory saved.');
 }catch(err){status(err.message);}
};
$('uploadForm').onsubmit=async e=>{
 e.preventDefault();const file=$('documentFile').files[0];if(!file)return;
 const form=new FormData();form.append('document',file);
 form.append('profile',$('documentProfile').value);
 try{status('Uploading to private staging…');const result=await api('/documents','POST',form);
 $('documentFile').value='';await documents();
 status(result.duplicate?'Known PDF: reused existing document.':'New PDF queued for parsing.');
 }catch(err){status(err.message);}
};
(async()=>{
 try{await loadState();
  if(s.role==='guest'){
   try{const old=JSON.parse(sessionStorage.getItem('sirus-ai-guest-history')||'[]');
    if(Array.isArray(old))s.msgs=s.guest=old.slice(-5);}catch(_){}}
  else{await refreshConversations();if(s.convs.length)await openConversation(s.convs[0].id);}
  nav();draw();
 }catch(e){status('Workspace unavailable: '+e.message);}
})();