'use strict';
const $=id=>document.getElementById(id);
const sha=new URLSearchParams(location.search).get('sha')||'';
const requested=Number(new URLSearchParams(location.search).get('page')||1);
let total=0,page=1,fullSha='',loading=false;
function note(text){$('manualStatus').textContent=text;}
function validSha(s){return /^[0-9a-f]{12}(?:[0-9a-f]{52})?$/.test(s);}
function src(pageNumber){
 return '/ai/api/manuals/page/'+fullSha+'/'+pageNumber+'?size=full';
}
function showPage(next){
 if(!total||loading||!Number.isInteger(next)||next<1||next>total)return;
 page=next;
 $('manualImage').hidden=true;
 $('pageNumber').value=String(page);
 $('prevPage').disabled=page===1;
 $('nextPage').disabled=page===total;
 $('pageNumber').max=String(total);
 note('Rendering PDF page '+page+' of '+total+'…');
 loading=true;
 $('manualImage').onload=()=>{
  loading=false;$('manualImage').hidden=false;note('Page '+page+' of '+total);
 };
 $('manualImage').onerror=()=>{
  loading=false;$('manualImage').hidden=true;
  note('Page preview unavailable. Verify approval, document access and Gentoo.');
 };
 $('manualImage').src=src(page);
 const url=new URL(location.href);
 url.searchParams.set('sha',fullSha);
 url.searchParams.set('page',String(page));
 history.replaceState(null,'',url);
}
$('pageForm').onsubmit=e=>{
 e.preventDefault();
 showPage(Number($('pageNumber').value));
};
$('prevPage').onclick=()=>showPage(page-1);
$('nextPage').onclick=()=>showPage(page+1);
(async()=>{
 if(!validSha(sha)){note('Invalid manual reference.');return;}
 try{
  const response=await fetch('/ai/api/manuals/meta/'+sha,{
   credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}
  });
  const data=await response.json();
  if(!response.ok)throw Error(data.error||'Manual access denied.');
  if(!validSha(data.sha256)||data.sha256.length!==64
      ||!Number.isInteger(data.pages)||data.pages<1)throw Error('Invalid source metadata.');
  fullSha=data.sha256;total=data.pages;
  $('manualTitle').textContent=data.title;
  $('pageTotal').textContent='of '+total;
  document.title=data.title+' · SirusSofts';
  showPage(Number.isInteger(requested)&&requested>=1&&requested<=total?requested:1);
 }catch(e){note(e.message);}
})();
