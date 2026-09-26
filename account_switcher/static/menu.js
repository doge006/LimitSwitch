'use strict';
// Menu bar popover (macOS). Same local API as the full view; the native host listens for
// {type: 'height' | 'full' | 'quit'} messages and resizes / opens windows accordingly.
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.app;
const $ = id => document.getElementById(id);
const PROVIDERS = [['claude', 'Claude'], ['codex', 'Codex']];
let state = null, pending = null, armed = null, armedTimer = null;

const remaining = used => Math.max(0, Math.min(100, 100 - used));
const level = left => left > 30 ? 'good' : left > 10 ? 'warn' : 'bad';
function until(ts) {
  const m = Math.max(0, Math.round((ts * 1000 - Date.now()) / 60000));
  if (m < 60) return `${m}m`;
  if (m < 1440) return `${Math.floor(m / 60)}h ${m % 60}m`;
  return `${Math.floor(m / 1440)}d ${Math.floor(m % 1440 / 60)}h`;
}
const short = w => ({ five_hour: '5h', weekly: '1w', monthly: '30d' })[w.key] || w.label.replace('Weekly · ', '');
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function post(message) { if (native) native.postMessage(message); }

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || response.statusText);
  return response.json();
}
const act = (action, body) => api(`/api/${action}`, body).catch(() => {});

function row(account) {
  const switchable = account.eligible && !account.active && !pending && !state.busy;
  const confirming = switchable && armed === account.id;
  const r = el('div', 'row' + (account.active ? ' active' : '') + (account.eligible ? '' : ' spent')
    + (switchable ? ' switchable' : '') + (confirming ? ' confirm' : ''));
  const head = el('div', 'head');
  head.append(el('span', 'email', account.name));
  if (account.plan) head.append(el('span', `plan ${account.provider}`, account.plan));
  const windows = account.windows.slice(0, 3);
  if (pending === account.id) head.append(el('span', 'state', 'Switching…'));
  else if (confirming) head.append(el('span', 'state confirm', 'Click again'));
  else if (account.active) head.append(el('span', 'state in-use', 'In use'));
  else if (!account.eligible) head.append(el('span', 'state limit', 'Limit'));
  else if (account.status && windows.length) head.append(el('span', 'state note', account.status));
  else if (switchable) head.append(el('span', 'state hint', 'Switch'));
  r.append(head);
  if (switchable) {
    // Two clicks, like the Windows panel: the first asks for confirmation, so a stray click never switches.
    r.addEventListener('click', () => {
      clearTimeout(armedTimer);
      if (!confirming) {
        armed = account.id;
        armedTimer = setTimeout(() => { armed = null; render(); }, 4000);
        render();
        return;
      }
      armed = null;
      pending = account.id;
      render();
      act('swap', { id: account.id });
    });
  }
  if (windows.length) {
    const bars = el('div', 'bars');
    bars.style.setProperty('--cols', windows.length);
    for (const w of windows) {
      const left = remaining(w.used), meter = el('div', 'meter');
      const track = el('span', 'track'), fill = el('span', `fill ${level(left)}`);
      fill.style.width = `${left}%`;
      track.append(fill);
      meter.append(el('span', 'label', short(w)), track, el('span', `pct ${level(left)}-text`, `${Math.round(left)}%`));
      if (w.resetsAt) meter.append(el('span', 'reset', `resets in ${until(w.resetsAt)}`));
      bars.append(meter);
    }
    r.append(bars);
  } else {
    r.append(el('div', 'loading', account.status || 'Usage not loaded yet'));
  }
  return r;
}

function render() {
  const list = $('list');
  list.replaceChildren();
  for (const [id, name] of PROVIDERS) {
    const accounts = state.accounts.filter(a => a.provider === id);
    if (!accounts.length) continue;
    const section = el('div', `section ${id}`);
    const icon = el('img');
    icon.src = `/assets/${id}.png`;
    icon.alt = '';
    section.append(icon, document.createTextNode(name.toUpperCase()), el('span', 'count', String(accounts.length)));
    list.append(section, ...accounts.map(row));
  }
  if (!state.accounts.length) {
    const empty = el('div', 'empty');
    empty.append(el('b', '', 'No accounts yet'), document.createTextNode('Sign in to Claude Code or Codex and it shows up here.'));
    list.append(empty);
  }
  $('auto').checked = state.autoSwap;
  $('afk').checked = state.afk;
  $('auto').disabled = $('afk').disabled = !!state.busy;
  post({ type: 'height', value: Math.ceil(document.body.getBoundingClientRect().height) });
}

async function follow() {
  let revision = -1;
  for (;;) {
    try {
      state = await api(`/api/state?after=${revision}`);
      revision = state.revision;
      if (pending && state.accounts.some(a => a.id === pending && a.active)) pending = null;
      render();
    } catch {
      await new Promise(done => setTimeout(done, 1500));
    }
  }
}

for (const id of ['auto', 'afk']) {
  $(id).addEventListener('change', () => act('preferences', { autoSwap: $('auto').checked, afk: $('afk').checked }));
}
$('full').addEventListener('click', () => native ? post({ type: 'full' }) : window.open(`/#token=${token}`));
$('quit').addEventListener('click', () => native ? post({ type: 'quit' }) : act('shutdown'));
setInterval(() => state && render(), 60000);  // keep "resets in" current
follow();
