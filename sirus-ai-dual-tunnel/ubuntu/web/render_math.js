(function (root) {
  'use strict';
  // Math-only parser. All non-math output remains text, never model-supplied HTML.
  // Skip fenced and inline code; preserve original TeX if a parser is unavailable.
  const TOKEN = /```[\s\S]*?```|`[^`\n]+`|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|(?<!\\)\$(?!\s)(?:\\.|[^$\r\n])+(?<!\s)(?<!\\)\$/g;
  function splitSegments(input) {
    const source = String(input == null ? '' : input);
    const pieces = [];
    let offset = 0;
    TOKEN.lastIndex = 0;
    for (const found of source.matchAll(TOKEN)) {
      const start = found.index;
      const raw = found[0];
      if (start > offset) pieces.push({type: 'text', raw: source.slice(offset, start)});
      if (raw.startsWith('`')) {
        pieces.push({type: 'text', raw});
      } else {
        const size = raw.startsWith('$$') || raw.startsWith('\\[') || raw.startsWith('\\(') ? 2 : 1;
        const tex = raw.slice(size, -size);
        if (tex.trim() && tex.length <= 5000) {
          pieces.push({type: 'math', raw, tex, display: raw.startsWith('$$') || raw.startsWith('\\[')});
        } else {
          pieces.push({type: 'text', raw});
        }
      }
      offset = start + raw.length;
    }
    if (offset < source.length) pieces.push({type: 'text', raw: source.slice(offset)});
    return pieces;
  }
  function renderMathText(target, input) {
    target.replaceChildren();
    const doc = target.ownerDocument || document;
    for (const part of splitSegments(input)) {
      if (part.type === 'text') {
        target.appendChild(doc.createTextNode(part.raw));
        continue;
      }
      const holder = doc.createElement(part.display ? 'div' : 'span');
      holder.className = part.display ? 'math-display' : 'math-inline';
      try {
        if (!root.temml || typeof root.temml.render !== 'function') throw Error('Math renderer unavailable');
        root.temml.render(part.tex, holder, {displayMode: part.display, trust: false, throwOnError: true});
        target.appendChild(holder);
      } catch (_) {
        target.appendChild(doc.createTextNode(part.raw));
      }
    }
  }
  const api = {splitSegments, renderMathText};
  root.SirusMath = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
