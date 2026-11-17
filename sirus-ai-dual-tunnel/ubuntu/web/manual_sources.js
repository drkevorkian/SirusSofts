'use strict';
/* Pure, CSP-safe citation parsing. URL authorization happens server-side. */
window.SirusManualSources=Object.freeze({
 parse(content){
  const sourceText=String(content||'');
  const begin='[[SIRUS_MANUAL_SOURCES_START]]';
  const end='[[SIRUS_MANUAL_SOURCES_END]]';
  const i=sourceText.indexOf(begin),j=sourceText.indexOf(end,i+begin.length);
  const hasManifest=i>=0&&j>i&&j-i<5000;
  const lines=hasManifest?sourceText.slice(i+begin.length,j):sourceText;
  const prose=hasManifest?(
    sourceText.slice(0,i)+sourceText.slice(j+end.length)).trim():sourceText;
  const rx=/^\s*\*?\s*\[M(\d{1,2})\]\s+([^\r\n]{1,180}?)\s+\|\s+PDF page\s+(\d{1,5})\s+\|\s+SHA256\s+([0-9a-f]{12}|[0-9a-f]{64})\s*$/gm;
  const sources=[],seen=new Set();let match;
  while((match=rx.exec(lines))!==null&&sources.length<6){
   const page=Number(match[3]),sha=match[4];
   if(!Number.isInteger(page)||page<1||page>20000)continue;
   const key=sha+':'+page;if(seen.has(key))continue;seen.add(key);
   sources.push({number:Number(match[1]),title:match[2],
                 page,sha});
  }
  return {prose,sources};
 }
});
