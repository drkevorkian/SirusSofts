'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const path=require('node:path');
const window={};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../ubuntu/web/manual_sources.js'),'utf8'),
                   {window});
const parse=window.SirusManualSources.parse;
const sha='f29ffa50ce28'+'e'.repeat(52);
const block='[[SIRUS_MANUAL_SOURCES_START]]\n'+
  '[M1] SSV 2027 DEFENDER_TRAXTER HD11 - Shop Manual_SM91Y027_en.pdf | PDF page 298 | SHA256 '+sha+'\n'+
  '[M2] SSV 2027 DEFENDER_TRAXTER HD11 - Shop Manual_SM91Y027_en.pdf | PDF page 289 | SHA256 '+sha+'\n'+
  '[[SIRUS_MANUAL_SOURCES_END]]\n\n'+
  'These pages exist, but the model did not produce a procedure.';
let out=parse(block);
assert.equal(out.sources.length,2);
assert.equal(out.sources[0].sha,sha);
assert.equal(out.sources[0].page,298);
assert.equal(out.sources[1].page,289);
assert(!out.prose.includes('[[SIRUS'));
assert(out.prose.includes('model did not produce'));
out=parse('* [M1] HD11 shop.pdf | PDF page 298 | SHA256 f29ffa50ce28\nOther text');
assert.equal(out.sources.length,1);
assert.equal(out.sources[0].sha,'f29ffa50ce28');
out=parse('Hello, no manual references here.');
assert.equal(out.sources.length,0);
out=parse('[[SIRUS_MANUAL_SOURCES_START]]\n'+
  '[M1] fake.pdf | PDF page 0 | SHA256 '+sha+'\n'+
  '[M2] bad.pdf | PDF page 20001 | SHA256 '+sha+'\n'+
  '[[SIRUS_MANUAL_SOURCES_END]]\nResult');
assert.equal(out.sources.length,0);
console.log('PASS: verified manual citation manifests, legacy refs and bounded pages');
