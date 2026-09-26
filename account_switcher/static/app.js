'use strict';
// Dependency-free dashboard. DOM is built once and then patched in place.
// No idle timers except a once-a-minute countdown tick that pauses while the tab is hidden.
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const $ = id => document.getElementById(id);
const PROVIDERS = [
  { id: 'claude', name: 'Claude', caption: 'Claude Code · CLI recovery' },
  { id: 'codex', name: 'Codex', caption: 'Selection preview' },
];
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let state = null, stopped = false, submitting = null, pendingPrefs = null, lastLogId = null, minuteTimer = null;
const cards = new Map(), tiles = new Map(), lastActive = {};

// ---------- helpers ----------
function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined) n.textContent = text;
  return n;
}
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
};
const remaining = used => Math.max(0, Math.min(100, 100 - used));
const level = left => left > 30 ? 'level-good' : left > 10 ? 'level-warn' : 'level-bad';
const shortName = account => account.alias.split(' · ').pop().replace(' (synthetic)', '');
function relative(ts) {
  const m = Math.max(0, Math.round((ts * 1000 - Date.now()) / 60000));
  if (m < 1) return 'now';
  if (m < 60) return `in ${m}m`;
  if (m < 1440) return `in ${Math.floor(m / 60)}h ${m % 60}m`;
  return `in ${Math.floor(m / 1440)}d ${Math.floor(m % 1440 / 60)}h`;
}
function absolute(ts) {
  const d = new Date(ts * 1000), time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString([], { weekday: 'short' })} ${time}`;
}
const resetText = ts => `Resets ${absolute(ts)} · ${relative(ts)}`;
const accountWindows = a => a.windows.filter(w => w.key === 'five_hour' || w.key === 'weekly');
const headroom = a => Math.min(...accountWindows(a).map(w => remaining(w.used)));

// Finite count-up for changed percentages (~0.6s, then stops).
function tweenNumber(el, to) {
  const from = Number(el.dataset.value ?? to);
  el.dataset.value = to;
  if (reduceMotion || from === to) { el.textContent = `${Math.round(to)}%`; return; }
  const start = performance.now();
  const step = now => {
    const p = Math.min(1, (now - start) / 600), e = 1 - (1 - p) ** 3;
    el.textContent = `${Math.round(from + (to - from) * e)}%`;
    if (p < 1 && el.dataset.value == to) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- toasts ----------
function toast(message, kind = '') {
  const t = node('div', `toast ${kind}`, message);
  $('toasts').append(t);
  while ($('toasts').children.length > 4) $('toasts').firstElementChild.remove();
  setTimeout(() => {
    t.classList.add('leaving');
    t.addEventListener('animationend', () => t.remove(), { once: true });
    if (reduceMotion) t.remove();
  }, kind === 'error' ? 6000 : 3800);
}
const classify = text =>
  /fail|exhaust|error|stopped|attention|interrupt|quota/i.test(text) && !/→|failover|routed|selected/i.test(text) ? 'fail'
    : /→|swap|selected|routed|failover|continue/i.test(text) ? 'swap'
    : /completed|started|restored/i.test(text) ? 'ok' : '';

// ---------- build (once) ----------
function buildTile(provider) {
  const tile = $(`tile-${provider.id}`);
  const head = node('div', 'tile-head'), kicker = node('span', 'tile-kicker');
  const logo = node('img'); logo.src = `/assets/${provider.id}.png`; logo.alt = '';
  kicker.append(logo, `${provider.name} · in use`);
  head.append(kicker);
  const ring = node('div', 'ring');
  ring.innerHTML = '<svg viewBox="0 0 74 74" aria-hidden="true"><circle class="ring-track" cx="37" cy="37" r="31"/><circle class="ring-fill" cx="37" cy="37" r="31"/></svg>';
  const label = node('div', 'ring-label'), pct = node('span', 'num', '0%');
  pct.dataset.value = '0';
  const inner = node('div'); inner.append(pct, node('small', '', 'left'));
  label.append(inner); ring.append(label);
  const body = node('div', 'tile-body'), who = node('div', 'who', '—'), meta = node('div', 'meta'), next = node('div', 'next');
  body.append(who, meta, next);
  tile.append(head, ring, body);
  const fill = ring.querySelector('.ring-fill'), circumference = 2 * Math.PI * 31;
  fill.style.strokeDasharray = circumference;
  fill.style.strokeDashoffset = circumference;
  tiles.set(provider.id, { tile, ring, pct, who, meta, next, fill, circumference });
}

function buildCard(account, index) {
  const card = node('article', 'card enter');
  card.style.setProperty('--i', index);
  // Drop the entry animation once finished so it cannot pin `transform` for hover/pop.
  card.addEventListener('animationend', e => { if (e.animationName === 'rise') card.classList.remove('enter'); });
  const head = node('div', 'card-head'), avatar = node('span', 'avatar'), logo = node('img');
  logo.src = `/assets/${account.provider}.png`; logo.alt = '';
  avatar.append(logo);
  const identity = node('div', 'identity'), line = node('div', 'alias-line');
  line.append(node('span', 'alias', shortName(account)));
  if (account.plan) line.append(node('span', 'plan', account.plan));
  identity.append(line, node('div', 'email', account.email || 'Sample account'));
  const badge = node('span', 'badge', 'In use');
  head.append(avatar, identity, badge);

  const list = node('div', 'windows'), windows = new Map();
  for (const w of account.windows) {
    const row = node('div', 'window'), top = node('div', 'window-top');
    const value = node('span', 'window-value num');
    value.dataset.value = '0';
    top.append(node('span', 'window-label', w.label), value);
    const bar = node('div', 'bar'), fill = node('div', 'bar-fill');
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-label', `${w.label} remaining`);
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    bar.append(fill);
    const reset = node('div', 'window-reset num');
    row.append(top, bar, reset);
    list.append(row);
    windows.set(w.key, { row, value, bar, fill, reset });
  }

  const foot = node('div', 'card-foot'), hint = node('span', 'hint'), swap = node('button', 'button', 'Swap');
  swap.type = 'button';
  swap.addEventListener('click', () => act('swap', { id: account.id }, swap));
  foot.append(hint, swap);
  card.append(head, list, foot);
  cards.set(account.id, { card, badge, windows, swap, hint });
  return card;
}

function build() {
  PROVIDERS.forEach(buildTile);
  let index = 0;
  for (const provider of PROVIDERS) {
    const accounts = state.accounts.filter(a => a.provider === provider.id);
    if (!accounts.length) continue;
    const group = node('section', 'group');
    group.dataset.provider = provider.id;
    const head = node('div', 'group-head enter'), logo = node('img');
    head.style.setProperty('--i', index++);
    logo.src = `/assets/${provider.id}.png`; logo.alt = '';
    head.append(logo, node('h2', '', provider.name), node('span', 'count', `${accounts.length} account${accounts.length === 1 ? '' : 's'}`), node('span', 'caption', provider.caption));
    const grid = node('div', 'cards');
    for (const account of accounts) grid.append(buildCard(account, index++));
    group.append(head, grid);
    $('accounts').append(group);
  }
  applyFilter(store.get('filter') || 'all');
}

// ---------- update (every state change) ----------
function updateCards() {
  const locked = state.busy || !!submitting;
  for (const account of state.accounts) {
    const view = cards.get(account.id);
    if (!view) continue;
    const wasActive = view.card.classList.contains('active');
    view.card.classList.toggle('active', account.active);
    view.card.classList.toggle('spent', !account.eligible);
    if (account.active && !wasActive && lastActive[account.provider] !== undefined && !reduceMotion) {
      view.card.classList.remove('just-active');
      void view.card.offsetWidth;
      view.card.classList.add('just-active');
    }
    view.badge.textContent = account.eligible ? 'In use' : 'Limit reached';
    for (const w of account.windows) {
      const wv = view.windows.get(w.key);
      if (!wv) continue;
      const left = remaining(w.used);
      wv.row.className = `window ${level(left)}`;
      tweenNumber(wv.value, left);
      wv.fill.style.transform = `scaleX(${left / 100})`;
      wv.bar.setAttribute('aria-valuenow', String(Math.round(left)));
      wv.reset.textContent = resetText(w.resetsAt);
    }
    const isWorking = submitting && submitting.el === view.swap;
    view.swap.classList.toggle('working', !!isWorking);
    view.swap.disabled = locked || account.active || !account.eligible;
    view.swap.className = `button${isWorking ? ' working' : ''}${account.active ? ' current' : account.eligible ? ' accent' : ''}`;
    view.swap.textContent = !account.eligible ? 'Unavailable' : account.active ? 'Active' : 'Swap to this';
    view.hint.textContent = account.active ? 'New requests route here' : account.eligible ? `${Math.round(headroom(account))}% headroom` : 'Waiting for reset';
  }
}

function updateTiles() {
  for (const provider of PROVIDERS) {
    const view = tiles.get(provider.id);
    const account = state.accounts.find(a => a.provider === provider.id && a.active);
    if (!view || !account) continue;
    const left = headroom(account);
    view.tile.style.setProperty('--level', `var(--${left > 30 ? 'good' : left > 10 ? 'warn' : 'bad'})`);
    view.fill.style.strokeDashoffset = view.circumference * (1 - left / 100);
    tweenNumber(view.pct, left);
    view.who.textContent = shortName(account);
    view.meta.textContent = [account.plan, account.email].filter(Boolean).join(' · ');
    const soonest = accountWindows(account).reduce((a, b) => (a.resetsAt < b.resetsAt ? a : b));
    view.next.replaceChildren('Next reset ', node('b', 'num', relative(soonest.resetsAt)), ` · ${soonest.label}`);
    if (lastActive[provider.id] && lastActive[provider.id] !== account.id && !reduceMotion) {
      view.tile.classList.remove('swapped');
      void view.tile.offsetWidth;
      view.tile.classList.add('swapped');
    }
    lastActive[provider.id] = account.id;
  }
}

function updateLog() {
  const log = state.log;
  const newest = log.length ? log[log.length - 1].id : 0;
  if (lastLogId === null) lastLogId = newest; // no toasts for history on first load
  const list = $('log'), fresh = log.filter(e => e.id > lastLogId);
  if (!log.length) {
    list.replaceChildren(node('li', 'empty', 'Swaps, failovers and recoveries will appear here.'));
  } else if (fresh.length || list.children.length !== Math.min(log.length, 60)) {
    const items = log.slice(-60).reverse().map(entry => {
      const li = node('li', `${classify(entry.text)}${entry.id > lastLogId ? ' fresh' : ''}`);
      const time = node('time', 'num', new Date(entry.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
      li.append(time, entry.text);
      return li;
    });
    list.replaceChildren(...items);
  }
  for (const entry of fresh.slice(-3)) {
    const kind = classify(entry.text);
    if (kind) toast(entry.text, kind === 'fail' ? 'error' : kind === 'ok' ? 'ok' : '');
  }
  lastLogId = newest;
}

function render(next) {
  state = next;
  if (!cards.size) {
    build();
    void $('accounts').offsetHeight; // commit the empty bars so the first fill transitions
  }
  $('connection').textContent = 'Connected to local server';
  $('connection-dot').classList.add('ready');
  if (pendingPrefs && !state.busy && state.afk === pendingPrefs.afk && state.autoSwap === pendingPrefs.autoSwap) pendingPrefs = null;
  $('auto-swap').checked = pendingPrefs?.autoSwap ?? state.autoSwap;
  $('afk').checked = pendingPrefs?.afk ?? state.afk;
  const locked = state.busy || !!submitting;
  const pill = $('automation-state');
  pill.textContent = state.busy ? 'Working…' : state.afk ? 'AFK armed' : state.autoSwap ? 'Watching' : 'Manual';
  pill.className = `state-pill${state.busy ? ' busy' : state.afk || state.autoSwap ? ' on' : ''}`;

  const status = state.busy && state.status === 'Ready' ? 'Starting…' : state.status;
  $('status').textContent = status;
  $('status').classList.toggle('warn', /attention|interrupt|waiting|stopped/i.test(status));
  $('backend').textContent = state.backend;
  $('session-id').textContent = state.sessionId ? `Session ${state.sessionId.slice(0, 8)}${state.clientPid ? ` · PID ${state.clientPid}` : ''}` : 'No active session';
  const output = $('output');
  if (state.output && output.textContent !== state.output) {
    output.textContent = state.output;
    output.scrollTop = output.scrollHeight;
  } else if (!state.output && state.status === 'Stopped') {
    output.textContent = 'Session stopped. Pick a scenario to run another test.';
  }
  for (const id of ['run', 'continue', 'reset', 'auto-swap', 'afk', 'scenario']) $(id).disabled = locked;
  $('continue').disabled ||= !state.sessionId;
  $('run').classList.toggle('working', submitting?.el === $('run'));
  updateCards();
  updateTiles();
  updateLog();
}

// ---------- network ----------
async function request(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}

async function act(action, body = {}, el = null) {
  if (submitting) return;
  submitting = { action, el };
  if (action === 'preferences') pendingPrefs = body;
  if (state) render(state);
  try {
    await request(`/api/${action}`, body);
  } catch (e) {
    pendingPrefs = null;
    toast(e.message, 'error');
  } finally {
    submitting = null;
    if (state) render(state);
  }
}

async function observe() {
  if (!token) {
    toast('Open the dashboard from the app to get its private local link.', 'error');
    $('connection').textContent = 'Launch link needed';
    return;
  }
  let failures = 0;
  while (!stopped) {
    try {
      const next = await request(`/api/state?after=${state?.revision ?? -1}`);
      if (stopped) return;
      render(next);
      failures = 0;
    } catch {
      if (stopped) return;
      $('connection-dot').classList.remove('ready');
      $('connection').textContent = 'Reconnecting…';
      if (++failures >= 3) {
        $('connection').textContent = 'Disconnected';
        toast('The local server has stopped. Launch the app again to reconnect.', 'error');
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  }
}

// Countdown text only changes once a minute; tick on the minute boundary, never while hidden.
function scheduleMinute() {
  clearTimeout(minuteTimer);
  if (document.hidden) return;
  minuteTimer = setTimeout(() => { if (state) { updateCards(); updateTiles(); } scheduleMinute(); }, 60000 - Date.now() % 60000 + 50);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state) { updateCards(); updateTiles(); }
  scheduleMinute();
});

// ---------- interactions ----------
function applyFilter(filter) {
  store.set('filter', filter);
  for (const b of document.querySelectorAll('[data-filter]')) b.setAttribute('aria-pressed', String(b.dataset.filter === filter));
  for (const g of document.querySelectorAll('.group')) g.hidden = filter !== 'all' && g.dataset.provider !== filter;
}
for (const b of document.querySelectorAll('[data-filter]')) b.addEventListener('click', () => applyFilter(b.dataset.filter));

// Pointer spotlight: one delegated listener, at most one style write per frame.
let spotlightFrame = 0;
$('accounts').addEventListener('pointermove', event => {
  const card = event.target.closest('.card');
  if (!card || spotlightFrame) return;
  spotlightFrame = requestAnimationFrame(() => {
    spotlightFrame = 0;
    const r = card.getBoundingClientRect();
    card.style.setProperty('--mx', `${event.clientX - r.left}px`);
    card.style.setProperty('--my', `${event.clientY - r.top}px`);
  });
});

function setLab(open) {
  $('lab').classList.toggle('open', open);
  $('lab-toggle').setAttribute('aria-expanded', String(open));
  $('lab-body').inert = !open;
  store.set('lab', open ? '1' : '0');
}
$('lab-toggle').addEventListener('click', () => setLab(!$('lab').classList.contains('open')));
setLab(store.get('lab') === '1');

const sendPrefs = () => act('preferences', { autoSwap: $('auto-swap').checked, afk: $('afk').checked });
$('auto-swap').addEventListener('change', sendPrefs);
$('afk').addEventListener('change', sendPrefs);
$('run').addEventListener('click', () => act('run', { scenario: $('scenario').value }, $('run')));
$('continue').addEventListener('click', () => act('continue', {}, $('continue')));
$('stop').addEventListener('click', () => act('stop'));
$('reset').addEventListener('click', () => {
  const icon = $('reset');
  icon.classList.remove('spin'); void icon.offsetWidth; icon.classList.add('spin');
  act('reset');
});
$('shutdown').addEventListener('click', async () => {
  try {
    await request('/api/shutdown', {});
    stopped = true;
    $('connection').textContent = 'Shut down';
    $('connection-dot').classList.remove('ready');
    $('status').textContent = 'Stopping local processes';
    document.querySelectorAll('button,input,select').forEach(n => { n.disabled = true; });
  } catch (e) {
    toast(e.message, 'error');
  }
});

scheduleMinute();
observe();
