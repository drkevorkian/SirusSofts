'use strict';
const BASE = '/Gentoo/AI/';
const STORE = 'sirus-ai-conversations-v1';
const MODEL_PREF = 'sirus-ai-selected-local-model-v1';
const $ = id => document.getElementById(id);
let conversations = [];
let selectedId = null;
let pending = false;
let eventSource = null;
let localModels = new Set();
let modelInventory = {};
try {
  conversations = JSON.parse(localStorage.getItem(STORE) || '[]');
  if (!Array.isArray(conversations)) conversations = [];
} catch (_) { conversations = []; }
function persist() {
  try { localStorage.setItem(STORE, JSON.stringify(conversations.slice(-30))); }
  catch (_) { $('progress').textContent = 'Browser storage is full; messages will not persist after refresh.'; }
}
function selected() { return conversations.find(x => x.id === selectedId); }
function startChat() {
  if (pending) return;
  const id = crypto.randomUUID();
  conversations.push({id, title: 'New conversation', messages: [], pendingJob: null});
  selectedId = id;
  persist();
  render();
  $('prompt').focus();
}
function renderNav() {
  const nav = $('conversations'); nav.replaceChildren();
  for (const c of [...conversations].reverse()) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = c.title;
    if (c.id === selectedId) b.className = 'active';
    b.addEventListener('click', () => {
      if (pending && c.id !== selectedId) return;
      selectedId = c.id; render();
    });
    nav.appendChild(b);
  }
}
function render() {
  renderNav();
  const area = $('messages'); area.replaceChildren();
  const c = selected();
  if (!c || !c.messages.length) {
    const block = document.createElement('div'); block.className = 'welcome';
    const logo = document.createElement('div'); logo.className = 'hero-logo'; logo.textContent = 'S';
    const title = document.createElement('h2'); title.textContent = 'What are we building?';
    const p = document.createElement('p'); p.textContent = 'Input travels to Gentoo on one persistent tunnel. Answers return over a separate persistent output tunnel.';
    block.append(logo, title, p); area.append(block);
  } else {
    for (const msg of c.messages) {
      const block = document.createElement('article');
      block.className = 'message ' + (msg.role === 'user' ? 'user' : 'assistant') + (msg.error ? ' error' : '');
      const heading = document.createElement('div'); heading.className = 'meta';
      heading.textContent = msg.role === 'user' ? 'You' : 'SirusSofts AI' + (msg.worker ? ' · ' + msg.worker.toUpperCase() : '');
      const text = document.createElement('div'); text.textContent = msg.content;
      block.append(heading, text); area.append(block);
    }
  }
  area.scrollTop = area.scrollHeight;
}
function showLocalModels() {
  const select = $('model');
  const target = $('worker').value;
  const ids = new Set();
  for (const key of (target === 'auto' ? ['cpu','nvidia','amd'] : [target])) {
    for (const id of (Array.isArray(modelInventory[key]) ? modelInventory[key] : [])) {
      if (typeof id === 'string' && id.length <= 128) ids.add(id);
    }
  }
  localModels = ids;
  const previous = select.value || localStorage.getItem(MODEL_PREF) || '';
  select.replaceChildren();
  if (!ids.size) {
    select.appendChild(new Option('No local models available', ''));
    select.disabled = true;
    $('send').disabled = true;
    $('modelStatus').textContent = 'No verified local model is available on the selected worker.';
    return;
  }
  for (const id of [...ids].sort()) select.appendChild(new Option(id + ' · local', id));
  select.value = ids.has(previous) ? previous :
    (ids.has('sirussofts-coder') ? 'sirussofts-coder' : [...ids][0]);
  select.disabled = false;
  $('send').disabled = pending;
  $('modelStatus').textContent = 'Local Gentoo llama.cpp only · external inference disabled';
}
function setStatus(id, ok, label) { const el = $(id); el.className = 'status ' + (ok ? 'on' : 'off'); el.textContent = (ok ? '● ' : '○ ') + label; }
async function health() {
  try {
    const r = await fetch(BASE + 'api/in/health', {cache:'no-store'});
    if (!r.ok) throw Error('input unavailable');
    const data = await r.json();
    setStatus('inStatus', true, 'Input tunnel');
    for (const [id, key, name] of [['cpuStatus','cpu','CPU'],['nvStatus','nvidia','NVIDIA'],['amdStatus','amd','AMD']]) {
      setStatus(id, !!data.workers[key], name);
    }
    if (data.local_only !== true || !data.models) throw Error('Strict local inventory missing');
    modelInventory = data.models;
    showLocalModels();
  } catch (_) {
    setStatus('inStatus', false, 'Input offline');
    modelInventory = {};
    showLocalModels();
  }
  try {
    const r = await fetch(BASE + 'api/out/health', {cache:'no-store'});
    if (!r.ok) throw Error('output unavailable');
    const data = await r.json();
    setStatus('outStatus', !!data.tunnel_connected, data.tunnel_connected ? 'Output tunnel' : 'Output tunnel offline');
  } catch (_) { setStatus('outStatus', false, 'Output offline'); }
}
function watchJob(jobId) {
  if (eventSource) eventSource.close();
  const c = selected(); if (!c) return;
  pending = true; $('send').disabled = true; $('progress').textContent = 'Queued · waiting for the output channel…';
  let highest = 0;
  const chunks = [];
  eventSource = new EventSource(BASE + 'api/out/events/' + encodeURIComponent(jobId) + '?after=0');
  eventSource.addEventListener('update', e => {
    let ev; try { ev = JSON.parse(e.data); } catch (_) { return; }
    if (ev.job_id !== jobId || ev.seq <= highest) return;
    highest = ev.seq;
    if (ev.type === 'started') $('progress').textContent = 'Running on ' + (ev.worker || 'worker').toUpperCase() + ' · separate output tunnel…';
    if (ev.type === 'chunk') {
      chunks.push(ev.content || '');
      $('progress').textContent = 'Receiving AI output · ' + chunks.length + ' chunk(s)…';
    }
    if (ev.type === 'completed' || ev.type === 'error') {
      const completedText = ev.type === 'completed'
        ? chunks.join('') + (ev.content || '') : ev.content || '';
      c.messages.push({role: 'assistant', content: completedText || '(empty response)', worker: ev.worker, error: ev.type === 'error'});
      c.pendingJob = null;
      persist(); render(); finish();
    }
  });
  eventSource.onerror = () => {
    if (!pending) return;
    $('progress').textContent = 'Waiting for output; reconnecting automatically. Results are queued safely on Gentoo.';
  };
}
function finish() { pending = false; $('send').disabled = !localModels.has($('model').value); $('progress').textContent = ''; if (eventSource) eventSource.close(); eventSource = null; }
async function send(e) {
  e.preventDefault(); if (pending) return;
  const text = $('prompt').value.trim(); if (!text) return;
  const modelId = $('model').value;
  if (!localModels.has(modelId)) { $('progress').textContent = 'Select a verified local model.'; return; }
  if (!selected()) startChat();
  const c = selected();
  c.messages.push({role:'user', content:text});
  if (c.title === 'New conversation') c.title = text.slice(0, 45);
  $('prompt').value = ''; pending = true; $('send').disabled = true;
  render(); persist(); $('progress').textContent = 'Sending through the input tunnel…';
  const history = c.messages.slice(-20).map(({role, content}) => ({role, content}));
  try {
    const r = await fetch(BASE + 'api/in/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({messages: history, target: $('worker').value,
        profile: $('profile').value, model_id:modelId, local_only:true})});
    const data = await r.json();
    if (!r.ok || !data.job_id) throw Error(data.error || 'Input queue unavailable');
    c.pendingJob = data.job_id;
    persist(); watchJob(data.job_id);
  } catch (err) {
    c.messages.push({role:'assistant', error:true, content:'Could not send request: ' + String(err.message || err)});
    persist(); render(); finish();
  }
}
$('worker').addEventListener('change', showLocalModels);
$('model').addEventListener('change', () => {
  localStorage.setItem(MODEL_PREF, $('model').value);
  $('send').disabled = pending || !localModels.has($('model').value);
});
$('newChat').addEventListener('click', startChat);
$('composer').addEventListener('submit', send);
$('prompt').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('composer').requestSubmit(); }
});
if (conversations.length) selectedId = conversations.at(-1).id;
else startChat();
render();
if (selected()?.pendingJob) watchJob(selected().pendingJob);
health(); setInterval(health, 12000);
