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
  const r = el('div', 'row' + (account.eligible ? '' : ' spent'));
  const head = el('div', 'head');
  head.append(el('span', 'email', account.name));
  if (account.plan) head.append(el('span', 'plan', account.plan));
  if (pending === account.id) head.append(el('span', 'state', 'Switching…'));
  else if (account.active) head.append(el('span', 'state in-use', '✓ In use'));
  else if (!account.eligible) head.append(el('span', 'state limit', 'Limit reached'));
  else {
    // Two clicks: the first asks for confirmation, so a stray click never switches.
    const confirming = armed === account.id;
    const b = el('button', 'switch-btn' + (confirming ? ' confirm' : ''), confirming ? 'Confirm' : 'Switch');
    b.type = 'button';
    b.disabled = !!pending || state.busy;
    b.addEventListener('click', () => {
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
    head.append(b);
  }
  r.append(head);
  const bars = el('div', 'bars');
  for (const w of account.windows) {
    const left = remaining(w.used), cell = el('div');
    const label = el('div', 'bar-label');
    label.append(el('span', '', short(w)));
    const pct = el('b', `${level(left)}-text`, `${Math.round(left)}%`);
    label.append(pct);
    const track = el('span', 'track'), fill = el('span', `fill ${level(left)}`);
    fill.style.width = `${left}%`;
    track.append(fill);
    cell.append(label, track, el('div', 'reset', w.resetsAt ? `resets in ${until(w.resetsAt)}` : ''));
    bars.append(cell);
  }
  if (account.windows.length) r.append(bars);
  if (account.status) r.append(el('div', 'note', account.status));
  return r;
}

function render() {
  const list = $('list');
  list.replaceChildren();
  for (const [id, name] of PROVIDERS) {
    const accounts = state.accounts.filter(a => a.provider === id);
    if (!accounts.length) continue;
    const section = el('div', 'section');
    const icon = el('img', id);
    icon.src = `/assets/${id}.png`;
    icon.alt = '';
    section.append(icon, document.createTextNode(name.toUpperCase()));
    list.append(section, ...accounts.map(row));
  }
  if (!state.accounts.length) list.append(el('div', 'empty', 'Sign in to Claude Code or Codex and it shows up here.'));
  $('auto').checked = state.autoSwap;
  $('afk').checked = state.afk;
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
