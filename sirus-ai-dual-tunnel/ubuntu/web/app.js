'use strict';
const API='/ai/api', $=id=>document.getElementById(id);
const s={role:'guest',csrf:'',models:{},categories:[],id:null,convs:[],msgs:[],guest:[],busy:false,job:null,attachments:[],uploading:false,renameTarget:null,deletedView:false,deleted:[],researchSelected:null,researchLast:null,researchOffset:0,researchDraft:null};
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
function panel(id){for(const n of ['messages','knowledgePanel','memoryPanel','documentPanel','researchPanel'])$(n).hidden=n!==id;}
function drawManualSources(article,raw){
 if(s.role!=='admin'||!window.SirusManualSources)return raw;
 const {prose,sources}=window.SirusManualSources.parse(raw);
 if(!sources.length)return prose;
 const section=el('section','');section.className='manual-sources';
 section.append(el('h3','Retrieved PDF pages'));
 for(const item of sources){
  const card=el('div','');card.className='manual-source-card';
  const title=el('strong','M'+item.number+' · '+item.title);
  const desc=el('span','PDF page '+item.page);
  const image=document.createElement('img');
  image.alt=item.title+', PDF page '+item.page;
  image.loading='lazy';image.decoding='async';
  image.src='/ai/api/manuals/page/'+item.sha+'/'+item.page+'?size=preview';
  const links=el('div','');links.className='manual-source-links';
  const pageLink=el('a','View cited page');
  pageLink.href='/Gentoo/AI/manual.html?sha='+item.sha+'&page='+item.page;
  pageLink.target='_blank';pageLink.rel='noopener noreferrer';
  const whole=el('a','View entire manual');
  whole.href='/Gentoo/AI/manual.html?sha='+item.sha+'&page=1';
  whole.target='_blank';whole.rel='noopener noreferrer';
  links.append(pageLink,whole);
  card.append(title,desc,image,links);section.append(card);
 }
 article.append(section);return prose;
}
function draw(){
 const root=$('messages');root.replaceChildren();
 if(!s.msgs.length){let w=el('div','');w.className='welcome';w.append(el('h2','SirusSofts AI'),
  el('p','Local Gentoo inference. Sign in for saved conversations, recall and your 2 GiB memory library.'));
  root.append(w);return;}
 for(const m of s.msgs){const b=el('article','');b.className='message '+m.role;
 const h=el('div',m.role==='user'?'You':'SirusSofts AI');h.className='meta';
 const body=el('div','');
 const displayed=m.role==='assistant'?drawManualSources(b,m.content):m.content;
 if(m.role==='assistant' && typeof window.SirusMath?.renderMathText==='function')
   window.SirusMath.renderMathText(body,displayed);
 else body.textContent=displayed;
 b.insertBefore(body,b.firstChild);b.insertBefore(h,b.firstChild);root.append(b);}
 root.scrollTop=root.scrollHeight;
}
function closeMobileConversations(){
 document.querySelector('.sidebar').classList.remove('mobile-list-open');
 $('mobileConversations').setAttribute('aria-expanded','false');
}
function nav(){
 const root=$('conversations');root.replaceChildren();
 const bar=$('conversationBar'),trash=$('deletedChatsToggle');
 if(s.role==='guest'){
  $('mobileConversations').hidden=true;trash.hidden=true;
  closeMobileConversations();
  root.append(el('p','Guest chats last for this browser session.'));
  bar.hidden=true;return;
 }
 $('mobileConversations').hidden=false;trash.hidden=false;
 trash.textContent=s.deletedView?'← Back to conversations':'Deleted chats';
 $('conversationsHeading').textContent=s.deletedView?'Deleted chats':'Conversations';
 root.setAttribute('aria-label',s.deletedView?'Deleted conversations':'Saved conversations');
 const visible=s.deletedView?s.deleted:s.convs;
 if(!visible.length)root.append(el('p',s.deletedView?'No deleted conversations.':'No saved conversations yet.'));
 for(const item of visible){
  const row=el('div','');row.className='conversation-entry'+(!s.deletedView&&item.id===s.id?' active':'');
  if(s.deletedView){
   const name=el('span',item.title);name.className='deleted-conversation-name';name.title=item.title;
   const restore=el('button','Restore');restore.type='button';restore.className='conversation-restore';
   restore.title='Restore conversation: '+item.title;
   restore.setAttribute('aria-label','Restore conversation: '+item.title);
   restore.onclick=()=>{void restoreConversation(item.id);};
   row.append(name,restore);
  }else{
   const open=el('button',item.title);open.type='button';open.className='conversation-open';
   open.title=item.title;open.setAttribute('aria-label','Open conversation: '+item.title);
   open.onclick=()=>{closeMobileConversations();void openConversation(item.id);};
   const rename=el('button','✎');rename.type='button';rename.className='conversation-rename';
   rename.title='Rename conversation';rename.setAttribute('aria-label','Rename conversation: '+item.title);
   rename.onclick=()=>showRename(item.id);
   const del=el('button','×');del.type='button';del.className='conversation-delete';
   del.title='Move to Deleted chats';del.setAttribute('aria-label','Delete conversation: '+item.title);
   del.onclick=()=>{void deleteConversation(item.id);};
   row.append(open,rename,del);
  }
  root.append(row);
 }
 const current=s.convs.find(c=>c.id===s.id);
 bar.hidden=!current;
 if(current)$('conversationName').textContent=current.title;
}
async function deleteConversation(id){
 if(s.role==='guest'||s.busy||s.uploading)return;
 const item=s.convs.find(c=>c.id===id);if(!item)return;
 if(!confirm('Move "'+item.title+'" to Deleted chats? It can be restored later.'))return;
 try{
  await api('/conversations/'+id,'DELETE');
  if(s.id===id){
   s.id=null;s.msgs=[];s.attachments=[];renderAttachments();
   panel('messages');draw();
  }
  await refreshConversations();
  closeMobileConversations();
  status('Conversation moved to Deleted chats. It can be restored.');
 }catch(e){status(e.message);}
}
async function restoreConversation(id){
 if(s.role==='guest'||s.busy||s.uploading)return;
 try{
  await api('/conversations/'+id+'/restore','POST',{});
  s.deletedView=false;
  closeMobileConversations();
  await refreshConversations();
  await openConversation(id);
  status('Conversation restored.');
 }catch(e){status(e.message);}
}
function showRename(id){
 if(s.role==='guest')return;
 const c=s.convs.find(item=>item.id===id);if(!c)return;
 s.renameTarget=id;
 $('renameInput').value=c.title;
 closeMobileConversations();
 $('renameDialog').showModal();
 $('renameInput').focus();$('renameInput').select();
}
async function refreshConversations(){
 if(s.role==='guest')return;
 s.convs=(await api('/conversations')).conversations||[];
 if(s.deletedView)s.deleted=(await api('/conversations/deleted')).conversations||[];
 nav();
}
async function openConversation(id){
 if(s.busy)return;
 try{s.msgs=(await api('/conversations/'+id)).messages||[];
  s.id=id;nav();panel('messages');draw();status('');await refreshAttachments();}
 catch(e){status(e.message);}
}
async function newChat(title='New conversation'){
 if(s.busy)return false;
 if(s.role==='guest'){s.id=null;s.msgs=[];s.guest=[];sessionStorage.removeItem('sirus-ai-guest-history');}
 else {try{s.id=(await api('/conversations','POST',{title})).id;
   s.deletedView=false;
   s.msgs=[];s.attachments=[];renderAttachments();await refreshConversations();}catch(e){status(e.message);return false;}}
 s.deletedView=false;
 closeMobileConversations();
 panel('messages');draw();renderAttachments();$('prompt').focus();status('');
 return true;
}
function showNewChatDialog(){
 if(s.busy)return;
 if(s.role==='guest'){void newChat();return;}
 $('newChatTitle').value='';
 $('newChatError').hidden=true;
 $('newChatError').textContent='';
 $('newChatDialog').showModal();
 $('newChatTitle').focus();
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
 $('send').disabled=!all.size||s.busy||s.uploading;
 const researchModel=$('researchModel');
 const prior=researchModel.value;
 researchModel.replaceChildren();
 for(const id of [...all].sort())researchModel.add(new Option(id+' · local',id));
 if(all.has(prior))researchModel.value=prior;
 else if(all.has('sirussofts-coder'))researchModel.value='sirussofts-coder';
 researchModel.disabled=!all.size;
 $('researchStart').disabled=!all.size;
 $('aiStatus').textContent=all.size?'● Local models available':'○ Inference unavailable';
 $('aiStatus').className='status '+(all.size?'on':'off');
}
async function loadState(){
 const data=await api('/state');s.role=data.role;s.csrf=data.csrf;
 s.categories=data.categories||[];s.models=data.models||{};
 const roleText=s.role==='admin'?'Administrator':s.role==='user'?'Registered user':'Guest · limited chat';
 $('roleLabel').textContent=roleText+
  (s.role!=='guest'&&typeof data.username==='string'&&data.username?' · '+data.username:'');
 $('accountLink').textContent=s.role==='guest'?'Sign in':s.role==='admin'?'Admin panel':'My account';
 $('accountLink').href=s.role==='guest'?'/admin/login?next=%2FGentoo%2FAI%2F':
  s.role==='admin'?'/admin':'/admin/account';
 $('adminLink').hidden=s.role!=='admin';$('gentooLink').hidden=s.role!=='admin';
 $('memoryToggle').hidden=s.role==='guest';$('documentsToggle').hidden=s.role==='guest';
 $('researchToggle').hidden=s.role!=='admin';
 $('chatAttachLabel').hidden=s.role==='guest';
 $('manualSearchLabel').hidden=s.role!=='admin';
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
async function knowledge(){
 const root=$('knowledgeEntries');root.replaceChildren();
 const slug=$('profile').value==='auto'?(s.categories[0]||{}).slug:$('profile').value;
 if(!slug){root.append(el('p','No public or authorized knowledge categories are available.'));return;}
 const data=await api('/knowledge/'+encodeURIComponent(slug));
 if(!(data.documents||[]).length)root.append(el('p','No approved entries in this category yet.'));
 for(const d of data.documents||[]){
  const card=el('article','');card.className='workspace-entry';
  card.append(el('b',d.title),el('p',d.excerpt||'No preview available'));
  if(d.source_url)card.append(el('small','Source: '+d.source_url));
  root.append(card);
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
async function researchJobs(){
 if(s.role!=='admin')return;
 const root=$('researchJobList');root.replaceChildren();
 const jobs=(await api('/research')).jobs||[];
 if(!jobs.length)root.append(el('p','No research runs yet.'));
 for(const job of jobs){
  const b=el('button',job.title+' · '+job.passes+' passes');
  b.type='button';b.className='research-job';
  b.onclick=()=>{void showResearch(job.id);};
  root.append(b);
 }
 if(!s.researchSelected&&jobs.length)await showResearch(jobs[0].id);
}
async function showResearch(id){
 if(s.role!=='admin')return;
 const info=await api('/research/'+id);
 if(s.researchSelected!==id){
  $('researchHistoryPanel').hidden=true;s.researchOffset=0;s.researchDraft=null;
 }
 s.researchSelected=id;s.researchLast=info;
 $('researchDetail').hidden=false;
 $('researchDetailTitle').textContent=info.title;
 $('researchDetailMeta').textContent=
  (info.source_url||'Pasted text')+' · '+(info.worker||'Not assigned')+
  ' · source SHA-256: '+(info.source_sha256||'Awaiting extraction');
 $('researchProgress').max=info.passes;
 $('researchProgress').value=info.completed;
 $('researchProgressText').textContent=info.state+' · '+info.completed+'/'+
  info.passes+' completed passes'+
  (info.sections_per_pass?
   ' · current pass section '+info.section_completed+'/'+info.sections_per_pass:'')+
  (info.source_pages?' · PDF pages '+info.source_pages:'')+
  (info.cancel_requested?' · cancellation requested':'')+
  (info.error?' · '+info.error:'');
 $('researchNotes').textContent=info.latest_notes||'No completed passes yet.';
 $('researchCancel').disabled=!['queued','running'].includes(info.state)||
  Boolean(info.cancel_requested);
 $('researchRerun').disabled=!['completed','error','canceled'].includes(info.state);
 $('researchCopy').disabled=!info.latest_notes;
}
$('researchSourceType').onchange=()=>{
 const src=$('researchSourceType').value;
 $('researchUrlFields').hidden=src!=='url';
 $('researchTextFields').hidden=src!=='text';
 $('researchPdfFields').hidden=src!=='pdf';
 $('researchUrl').required=src==='url';
 $('researchText').required=src==='text';
 $('researchPdfSelect').required=src==='pdf';
};
async function researchManuals(){
 if(s.role!=='admin')return;
 const query=$('researchPdfSearch').value.trim();
 if(!/^[A-Za-z0-9_-]{0,60}$/.test(query)){
  $('researchPdfHint').textContent='Search using one keyword: letters, numbers, hyphen or underscore.';
  return;
 }
 const data=await api('/research/manuals'+(query?'?q='+encodeURIComponent(query):''));
 const select=$('researchPdfSelect'),current=select.value;
 select.replaceChildren();
 for(const manual of data.manuals||[]){
  const label=manual.filename+' · '+manual.pages+' pages · '+
   (manual.reviewed?'Approved':'Ready for review');
  select.add(new Option(label,manual.sha256));
 }
 if([...select.options].some(o=>o.value===current))select.value=current;
 select.disabled=!select.options.length;
 const found=select.options.length;
 $('researchPdfHint').textContent=found+
  ' extracted PDFs found (up to 120). Results contain PDF metadata only. '+
  'Use another keyword if the manual is not listed.';
}
$('researchPdfFind').onclick=()=>{
 void researchManuals().catch(e=>{status(e.message);});
};
$('researchPdfSearch').onkeydown=event=>{
 if(event.key==='Enter'){
  event.preventDefault();
  void researchManuals().catch(e=>{status(e.message);});
 }
};
$('researchPdfSelect').onchange=()=>{
 const option=$('researchPdfSelect').selectedOptions[0];
 if(!option)return;
 $('researchTitleInput').value=option.textContent.split(' · ')[0].slice(0,160);
};
$('researchToggle').onclick=async()=>{
 if(s.role!=='admin')return;
 panel('researchPanel');
 try{
  await researchJobs();
  if(!$('researchPdfSelect').options.length)await researchManuals();
 }catch(e){status(e.message);}
};
$('researchForm').onsubmit=async event=>{
 event.preventDefault();
 if(s.role!=='admin')return;
 const src=$('researchSourceType').value;
 const payload={
  title:$('researchTitleInput').value.trim(),
  source_type:src,
  url:src==='url'?$('researchUrl').value.trim():'',
  text:src==='text'?$('researchText').value.trim():'',
  pdf_sha256:src==='pdf'?$('researchPdfSelect').value:'',
  page_start:src==='pdf'?Number($('researchPageStart').value):1,
  page_end:src==='pdf'?Number($('researchPageEnd').value):0,
  focus:$('researchFocus').value.trim(),
  passes:Number($('researchPasses').value),
  model_id:$('researchModel').value,target:$('researchTarget').value
 };
 const start=$('researchStart');start.disabled=true;
 try{
  const response=await api('/research','POST',payload);
  s.researchSelected=response.id;
  await researchJobs();
  await showResearch(response.id);
  status('Local research queued; passes continue if you close the browser.');
 }catch(e){status(e.message);}
 finally{start.disabled=false;}
};
$('researchCancel').onclick=async()=>{
 if(!s.researchSelected)return;
 if(!confirm('Cancel this research after its current inference pass? Completed draft passes will be retained.'))return;
 try{
  await api('/research/'+s.researchSelected+'/cancel','POST',{});
  await showResearch(s.researchSelected);
  status('Cancellation requested.');
 }catch(e){status(e.message);}
};
$('researchRerun').onclick=async()=>{
 if(!s.researchSelected)return;
 if(!confirm('Start a new run from the same source and settings? It will fetch the URL again if applicable.'))return;
 try{
  const response=await api('/research/'+s.researchSelected+'/rerun','POST',{});
  s.researchSelected=response.id;
  await researchJobs();await showResearch(response.id);
  status('Research rerun queued.');
 }catch(e){status(e.message);}
};
async function researchHistory(){
 if(!s.researchSelected||s.role!=='admin')return;
 const data=await api('/research/'+s.researchSelected+'/sections/'+s.researchOffset);
 const root=$('researchSectionList');root.replaceChildren();
 for(const section of data.sections||[]){
  const pages=section.pages?.length?' · PDF pages '+section.pages.join(', '):'';
  const b=el('button','Pass '+section.pass+' · section '+section.section+pages);
  b.type='button';b.className='research-job';
  b.onclick=()=>{
   s.researchDraft=section;
   $('researchHistoryNotes').textContent=section.notes;
  };
  root.append(b);
 }
 if(!(data.sections||[]).length)root.append(el('p','No drafts on this page.'));
 $('researchHistoryNewer').disabled=s.researchOffset===0;
 $('researchHistoryOlder').disabled=!data.has_more;
 $('researchHistoryPage').textContent='Saved section results '+(s.researchOffset+1)+
  '–'+(s.researchOffset+(data.sections||[]).length)+
  ' · review before reuse';
 s.researchDraft=data.sections?.[0]||null;
 $('researchHistoryNotes').textContent=s.researchDraft?.notes||'No draft selected.';
}
$('researchHistory').onclick=async()=>{
 $('researchHistoryPanel').hidden=!$('researchHistoryPanel').hidden;
 if(!$('researchHistoryPanel').hidden){
  s.researchOffset=0;
  try{await researchHistory();}catch(e){status(e.message);}
 }
};
$('researchHistoryNewer').onclick=()=>{
 s.researchOffset=Math.max(0,s.researchOffset-12);
 void researchHistory().catch(e=>status(e.message));
};
$('researchHistoryOlder').onclick=()=>{
 s.researchOffset+=12;
 void researchHistory().catch(e=>status(e.message));
};
$('researchHistoryCopy').onclick=async()=>{
 if(!s.researchDraft)return;
 try{
  await navigator.clipboard.writeText(s.researchDraft.notes);
  status('Copied unverified section draft. Check the original PDF pages before approval.');
 }catch(e){status('Clipboard unavailable: '+e.message);}
};
$('researchCopy').onclick=async()=>{
 if(!s.researchLast?.latest_notes)return;
 try{
  await navigator.clipboard.writeText(s.researchLast.latest_notes);
  status('Copied unreviewed notes. Verify facts before importing to knowledge.');
 }catch(e){status('Clipboard unavailable: '+e.message);}
};
let researchPolling=false;
window.setInterval(async()=>{
 if(s.role!=='admin'||!s.researchSelected||
    $('researchPanel').hidden||document.hidden||researchPolling)return;
 researchPolling=true;
 try{await showResearch(s.researchSelected);}
 catch(e){status('Research status unavailable: '+e.message);}
 finally{researchPolling=false;}
},5000);
function renderAttachments(){
 const tray=$('attachmentTray');tray.replaceChildren();
 tray.hidden=s.role==='guest'||!s.attachments.length;
 for(const item of s.attachments){
  const badge=el('span',item.name+' · '+item.status);badge.className='attached-file';
  if(item.status==='failed' && item.error)badge.title=item.error;
  if(!s.busy){
   const remove=el('button','×');remove.type='button';remove.className='remove-attachment';
   remove.title='Remove attachment from this conversation';
   remove.onclick=async()=>{
    if(!s.id)return;
    try{await api('/conversations/'+s.id+'/attachments/'+item.id,'DELETE');
     await refreshAttachments();status('Attachment removed.');}
    catch(e){status(e.message);}
   };
   badge.append(remove);
  }
  tray.append(badge);
 }
}
async function refreshAttachments(){
 if(s.role==='guest'||!s.id){s.attachments=[];renderAttachments();return;}
 const data=await api('/conversations/'+s.id+'/attachments');
 s.attachments=data.attachments||[];renderAttachments();
}
$('chatAttach').addEventListener('change',async()=>{
 const files=[...$('chatAttach').files];$('chatAttach').value='';
 if(!files.length||s.role==='guest'||s.uploading||s.busy)return;
 if(!s.id){await newChat();if(!s.id)return;}
 s.uploading=true;inventory();
 try{
  for(const file of files){
   const form=new FormData();
   form.append('file',file);
   const selected=$('profile').value;
   form.append('profile',selected==='auto'?(s.categories[0]||{}).slug:selected);
   status('Attaching '+file.name+'…');
   await api('/conversations/'+s.id+'/attachments','POST',form);
  }
  await refreshAttachments();
  status('Attachments added. Text files are ready; PDF processing may take longer.');
 }catch(e){status(e.message);try{await refreshAttachments();}catch(_){}}
 finally{s.uploading=false;inventory();}
});
window.setInterval(async()=>{
 if(s.role==='guest'||!s.id||s.uploading||
    !s.attachments.some(a=>a.status==='queued'||a.status==='processing')||document.hidden)return;
 try{await refreshAttachments();}catch(_){}
},3500);
async function send(event){
 event.preventDefault();if(s.busy)return;
 const text=$('prompt').value.trim();if(!text)return;
 if(s.uploading){status('Wait for the current attachment upload.');return;}
 if(s.attachments.some(a=>a.status==='queued'||a.status==='processing')){
  status('Your attached PDF is still being extracted; wait for its status to say parsed.');return;
 }
 if(s.attachments.some(a=>a.status==='failed')){
  status('An attached PDF failed parsing. Remove it or upload a readable file.');return;
 }
 if(s.role!=='guest'&&!s.id){await newChat();if(!s.id)return;}
 s.msgs.push({role:'user',content:text});$('prompt').value='';$('prompt').style.height='';panel('messages');draw();
 s.busy=true;inventory();status('Sending to local Gentoo model…');
 try{
  const body={message:text,profile:$('profile').value,
   model_id:$('model').value,target:$('worker').value,
   use_manuals:s.role==='admin'&&$('useManuals').checked};
  if(s.role==='guest')body.history=s.guest;else body.conversation_id=s.id;
  const result=await api('/jobs','POST',body);s.job=result.job_id;
  // Update the sidebar title after the message is stored, not after inference.
  if(s.role!=='guest')try{await refreshConversations();}catch(_){}
  while(s.busy&&s.job===result.job_id){
   try{
    const info=await api('/jobs/'+s.job);
    if(info.state==='completed'){
     if(s.role==='guest'){
      s.msgs.push({role:'assistant',content:info.output||'(empty answer)'});
      s.guest=s.msgs.slice(-5);
      sessionStorage.setItem('sirus-ai-guest-history',JSON.stringify(s.guest));
     }else{s.msgs=(await api('/conversations/'+s.id)).messages||[];await refreshConversations();}
     s.busy=false;s.job=null;draw();inventory();renderAttachments();await loadState();
     status('Completed on '+(info.worker||'local worker'));return;
    }
    if(info.state==='error'){
     s.busy=false;s.job=null;inventory();renderAttachments();
     status('Inference failed. Check the saved conversation before retrying.');return;
    }
    status(info.state==='running'?'Generating on Gentoo…':'Queued for inference…');
   }catch(e){status('Waiting for output: '+e.message);}
   await new Promise(resolve=>setTimeout(resolve,1700));
  }
 }catch(e){s.busy=false;s.job=null;inventory();renderAttachments();status(e.message);}
}
$('composer').onsubmit=send;
$('prompt').addEventListener('input',()=>{
 const field=$('prompt');
 field.style.height='auto';
 field.style.height=Math.min(field.scrollHeight,180)+'px';
});
$('prompt').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){
 e.preventDefault();$('composer').requestSubmit();}};
$('newChat').onclick=showNewChatDialog;
$('deleteCurrent').onclick=()=>{if(s.id)void deleteConversation(s.id);};
$('deletedChatsToggle').onclick=async()=>{
 if(s.role==='guest'||s.busy||s.uploading)return;
 const previous=s.deletedView;
 s.deletedView=!previous;
 try{await refreshConversations();}
 catch(e){s.deletedView=previous;nav();status(e.message);}
};
$('mobileConversations').onclick=()=>{
 const open=document.querySelector('.sidebar').classList.toggle('mobile-list-open');
 $('mobileConversations').setAttribute('aria-expanded',String(open));
};
$('newChatCancel').onclick=()=>$('newChatDialog').close();
$('newChatForm').onsubmit=async e=>{
 e.preventDefault();
 if(s.busy)return;
 const title=$('newChatTitle').value.trim();
 const error=$('newChatError');
 error.hidden=true;
 if(title&&new TextEncoder().encode(title).length>160){
  error.textContent='The title must be 160 UTF-8 bytes or fewer.';
  error.hidden=false;return;
 }
 const save=$('newChatSave');save.disabled=true;
 try{
  const created=await newChat(title||'New conversation');
  if(created){$('newChatDialog').close();$('prompt').focus();}
  else{error.textContent=$('progress').textContent||'Could not create conversation.';
   error.hidden=false;}
 }finally{save.disabled=false;}
};
$('renameCurrent').onclick=()=>{if(s.id)showRename(s.id);};
$('renameCancel').onclick=()=>$('renameDialog').close();
$('renameDialog').addEventListener('close',()=>{s.renameTarget=null;});
$('renameForm').onsubmit=async e=>{
 e.preventDefault();
 const id=s.renameTarget,title=$('renameInput').value.trim();
 if(!id||!title)return;
 const save=$('renameSave');save.disabled=true;
 try{
  const changed=await api('/conversations/'+id+'/title','POST',{title});
  const current=s.convs.find(item=>item.id===id);
  if(current)current.title=changed.title;
  $('renameDialog').close();nav();
  try{await refreshConversations();}catch(_){}
  status('Conversation renamed.');
 }catch(err){status(err.message);}
 finally{save.disabled=false;}
};
$('knowledgeToggle').onclick=async()=>{panel('knowledgePanel');try{await knowledge();}catch(e){status(e.message);}};
$('profile').addEventListener('change',async()=>{
 if(!$('knowledgePanel').hidden)try{await knowledge();}catch(e){status(e.message);}
});
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