'use strict';
const assert = require('node:assert/strict');
const math = require('../ubuntu/web/render_math.js');
const parts = math.splitSegments('Inline \\(x^2 + y^2\\) then \\[E=mc^2\\] and $\\alpha+\\beta$.');
assert.deepEqual(parts.filter(p => p.type === 'math').map(p => p.display), [false, true, true]);
assert.equal(parts.filter(p => p.type === 'math').length, 3);
assert.equal(math.splitSegments('Cost $100 and $200.').filter(p => p.type === 'math').length, 0);
assert.equal(math.splitSegments('`do not parse \\(x\\)` and ```\\[y\\]```').filter(p => p.type === 'math').length, 0);
assert.equal(math.splitSegments('Plain $x^2$ inline').filter(p => p.type === 'math').length, 1);
const doc = {
  createTextNode: raw => ({kind: 'text', raw}),
  createElement: tag => ({kind: 'element', tag, className: '', children: [],
    appendChild(node) { this.children.push(node); }})
};
const target = {ownerDocument: doc, children: [],
  replaceChildren() { this.children = []; },
  appendChild(node) { this.children.push(node); }};
let invoked = 0;
globalThis.temml = {render(tex, holder, options) {
  invoked++;
  assert.equal(options.trust, false);
  assert.equal(options.throwOnError, true);
  holder.appendChild(doc.createTextNode('MATH:' + tex));
}};
math.renderMathText(target, 'Before \\(x^2\\) <img src=x onerror=alert(1)>');
assert.equal(invoked, 1);
assert.equal(target.children[0].kind, 'text');
assert.equal(target.children[1].kind, 'element');
assert.equal(target.children[2].kind, 'text');
assert.ok(target.children[2].raw.includes('<img'));
globalThis.temml.render = () => { throw Error('bad TeX'); };
math.renderMathText(target, '\\(unknown\\)');
assert.equal(target.children[0].kind, 'text');
assert.equal(target.children[0].raw, '\\(unknown\\)');
console.log('PASS: Math segments, code exclusions, no HTML injection, untrusted mode and fallback');
