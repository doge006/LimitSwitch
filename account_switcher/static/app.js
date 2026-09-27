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
let pendingSwap = null, pendingTimer = null, layoutKey = '';
const cards = new Map();

// ---------- helpers ----------
function node(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text !== undefined) n.textContent = text;
  return n;
}
const remaining = used => Math.max(0, Math.min(100, 100 - used));
const level = left => left > 30 ? 'level-good' : left > 10 ? 'level-warn' : 'level-bad';
const displayName = account => account.name || account.email || account.alias;
// d**********@gmail.com: the first letter and the domain stay, the rest of the name is starred.
const redact = email => { const [user, domain] = (email || '').split('@'); return user ? user[0] + '*'.repeat(Math.max(1, user.length - 1)) + (domain ? '@' + domain : '') : ''; };
const accountById = id => state.accounts.find(a => a.id === id);
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
const resetText = ts => ts ? `Resets ${absolute(ts)} · ${relative(ts)}` : 'Reset time not reported';
const accountWindows = a => a.windows.filter(w => w.scope === 'account');
const headroom = a => (a.headroom ?? -1) >= 0 ? a.headroom : 100;
function dateText(ts) {
  return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
}
function daysLeft(ts) {
  const d = Math.floor((ts * 1000 - Date.now()) / 86400000);
  return d >= 1 ? `in ${d}d` : 'today';
}
// Subscription line: "Renews Oct 14 · in 18d" / "Ends Oct 14 · in 3d"; '' when unknown.
function subscriptionText(account) {
  const sub = account.subscription;
  if (!sub || !sub.at || sub.at * 1000 < Date.now() - 86400000) return '';
  const est = sub.estimated ? '~' : '';
  return `${sub.ends ? 'Ends' : 'Renews'} ${est}${dateText(sub.at)} · ${daysLeft(sub.at)}`;
}
// Credits and banked resets as [label, value] pairs, one line each.
function creditsItems(account) {
  const c = account.credits;
  if (!c) return [];
  const items = [];
  const main = creditsMain(c);
  if (main) items.push(main);
  if (typeof c.resets === 'number') items.push(['Usage limit resets', `${c.resets} available`]);
  return items;
}
function creditsMain(c) {
  if (c.kind === 'none') return null;
  if (c.kind === 'credits') {
    if (c.unlimited) return ['Credits', 'unlimited'];
    if (typeof c.balance === 'number') return ['Credits', c.balance.toLocaleString([], { maximumFractionDigits: 2 })];
    return c.enabled ? ['Credits', 'available'] : null;
  }
  if (!c.enabled) return ['Extra usage', 'off'];
  if (typeof c.limit === 'number' && typeof c.used === 'number') {
    const left = Math.max(0, c.limit - c.used);
    return ['Extra usage', `${left.toLocaleString()} of ${c.limit.toLocaleString()} left`];
  }
  return ['Extra usage', 'on'];
}
function ago(ts) {
  const m = Math.floor((Date.now() / 1000 - ts) / 60);
  return m < 1 ? 'Updated just now' : m < 60 ? `Updated ${m}m ago` : `Updated ${Math.floor(m / 60)}h ago`;
}
// Swap a text label with a short cross-fade instead of an instant jump.
function setLabel(el, text) {
  if (el.textContent === text) return;
  if (reduceMotion || !el.isConnected || !el.textContent) { el.textContent = text; return; }
  el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 90, easing: 'ease-in' }).onfinish = () => {
    el.textContent = text;
    el.animate([{ opacity: 0, transform: 'translateY(2px)' }, { opacity: 1, transform: 'none' }], { duration: 160, easing: 'ease-out' });
  };
}

// Short count for changed percentages (~0.35s, then stops).
function tweenNumber(el, to) {
  const from = Number(el.dataset.value ?? to);
  el.dataset.value = to;
  if (reduceMotion || from === to) { el.textContent = `${Math.round(to)}%`; return; }
  const start = performance.now();
  const step = now => {
    const p = Math.min(1, (now - start) / 350), e = 1 - (1 - p) ** 3;
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
  /fail|exhaust|error|stopped|attention|interrupt|quota|not found|closed without/i.test(text) && !/→|failover|routed|selected/i.test(text) ? 'fail'
    : /→|swap|selected|routed|failover|continue|now uses/i.test(text) ? 'swap'
    : /added|completed|started|restored|opened/i.test(text) ? 'ok' : '';

// ---------- build (once) ----------
function buildCard(account, index) {
  const card = node('article', 'card enter');
  card.style.setProperty('--i', index);
  // Drop the entry animation class once it has played.
  card.addEventListener('animationend', e => { if (e.animationName === 'rise') card.classList.remove('enter'); });
  const head = node('div', 'card-head'), avatar = node('span', 'avatar'), logo = node('img');
  logo.src = `/assets/${account.provider}.png`; logo.alt = '';
  avatar.append(logo);
  const identity = node('div', 'identity'), line = node('div', 'alias-line');
  let name, email = null;
  if (state.nameMode) {
    // Name mode: a name to type, and the email under it, redacted until clicked.
    name = node('input', 'alias alias-input');
    Object.assign(name, { type: 'text', value: account.label || '', placeholder: 'Name this account', maxLength: 40,
                          spellcheck: false, ariaLabel: 'Account name' });
    const save = () => { if (name.value.trim() !== (accountById(account.id)?.label || '')) act('rename', { id: account.id, name: name.value }); };
    name.addEventListener('change', save);
    name.addEventListener('keydown', e => { if (e.key === 'Enter') name.blur(); if (e.key === 'Escape') { name.value = accountById(account.id)?.label || ''; name.blur(); } });
    email = node('button', 'email-reveal', redact(account.email));
    email.type = 'button';
    email.title = 'Show email';
    email.addEventListener('click', () => {
      const shown = email.classList.toggle('shown');
      email.textContent = shown ? (accountById(account.id)?.email || '') : redact(accountById(account.id)?.email);
      email.title = shown ? 'Hide email' : 'Show email';
    });
  } else {
    name = node('span', 'alias', displayName(account));
    name.title = displayName(account);
  }
  line.append(name);
  const plan = node('span', 'plan', account.plan || '');
  plan.hidden = !account.plan;
  identity.append(line);
  if (email) {  // name mode: name, then the email, then the plan
    identity.append(email);
    const planLine = node('div', 'alias-line');
    planLine.append(plan);
    identity.append(planLine);
  } else {
    line.append(plan);
  }
  const side = node('div', 'card-side'), renew = node('button', 'renew num'), badge = node('span', 'badge', 'In use');
  renew.type = 'button';
  renew.title = 'Set the renewal or end date';
  renew.addEventListener('click', event => { event.stopPropagation(); openSubscriptionEditor(account.id, renew); });
  side.append(renew, badge);
  head.append(avatar, identity, side);

  const list = node('div', 'windows'), windows = new Map();
  for (const w of account.windows) {
    const row = node('div', 'window'), top = node('div', 'window-top');
    const value = node('span', 'window-value num');
    value.dataset.value = '0';
    const amount = node('span', 'window-amount');
    amount.append(value, node('span', 'window-left', ' left'));
    top.append(node('span', 'window-label', w.label), amount);
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

  if (!account.windows.length) list.append(node('div', 'window-empty', 'Usage not loaded yet'));
  const credits = node('div', 'credits');
  list.append(credits);
  const foot = node('div', 'card-foot'), hint = node('span', 'hint'), actions = node('div', 'card-actions');
  const swap = node('button', 'button swap'), label = node('span', 'label', 'Swap to this');
  swap.type = 'button';
  swap.append(label);
  swap.addEventListener('click', () => startSwap(account.id));
  const remove = node('button', 'button quiet remove', 'Remove');
  remove.type = 'button';
  remove.title = 'Forget this saved login';
  remove.addEventListener('click', () => {
    if (confirm(`Remove ${displayName(account)} from Account Switcher? Its saved login is deleted from this PC.`)) act('remove', { id: account.id });
  });
  remove.hidden = state.mode !== 'live';
  actions.append(remove, swap);
  foot.append(hint, actions);
  card.append(head, list, foot);
  cards.set(account.id, { card, badge, renew, windows, swap, label, hint, remove, plan, credits });
  return card;
}

function build() {
  cards.clear();
  $('accounts').replaceChildren();
  let index = 0;
  if (!state.accounts.length) {
    const empty = node('section', 'empty-state enter');
    empty.append(node('h2', '', 'No accounts yet'),
      node('p', '', state.mode === 'live'
        ? 'Sign in to Claude Code or Codex as usual and the account appears here automatically, or add one now.'
        : 'No sample accounts.'));
    if (state.mode === 'live') {
      const row = node('div', 'empty-actions');
      for (const provider of PROVIDERS) {
        const add = node('button', 'button', `Add ${provider.name} account`);
        add.type = 'button';
        add.addEventListener('click', () => act('add', { provider: provider.id }, add));
        row.append(add);
      }
      empty.append(row);
    }
    $('accounts').append(empty);
    return;
  }
  for (const provider of PROVIDERS) {
    const accounts = state.accounts.filter(a => a.provider === provider.id);
    if (!accounts.length) continue;
    const group = node('section', 'group');
    group.dataset.provider = provider.id;
    const head = node('div', 'group-head enter'), logo = node('img');
    head.style.setProperty('--i', index++);
    logo.src = `/assets/${provider.id}.png`; logo.alt = '';
    head.append(logo, node('h2', '', provider.name), node('span', 'count', `${accounts.length} account${accounts.length === 1 ? '' : 's'}`),
      node('span', 'caption', state.mode === 'live' ? (provider.id === 'claude' ? 'Claude Code' : 'Codex CLI & app') : provider.caption));
    const grid = node('div', 'cards');
    for (const account of accounts) grid.append(buildCard(account, index++));
    group.append(head, grid);
    $('accounts').append(group);
  }
}

// ---------- update (every state change) ----------
function startSwap(id) {
  if (pendingSwap || submitting) return;
  pendingSwap = id;
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(() => { pendingSwap = null; if (state) updateCards(); }, 8000);
  updateCards();
  act('swap', { id }).then(ok => { if (!ok) { pendingSwap = null; updateCards(); } });
}

function updateCards() {
  const locked = state.busy || !!submitting || !!pendingSwap;
  if (pendingSwap && state.accounts.some(a => a.id === pendingSwap && a.active)) {
    pendingSwap = null;  // the switch landed
    clearTimeout(pendingTimer);
  }
  for (const account of state.accounts) {
    const view = cards.get(account.id);
    if (!view) continue;
    view.card.classList.toggle('active', account.active);
    view.card.classList.toggle('spent', !account.eligible);
    view.badge.textContent = account.eligible ? 'In use' : 'Limit reached';
    const sub = subscriptionText(account);
    view.renew.textContent = sub || (state.mode === 'live' ? 'Set renewal date' : '');
    view.renew.classList.toggle('unset', !sub);
    view.renew.classList.toggle('ends', !!account.subscription?.ends && !!sub);
    view.renew.disabled = state.mode !== 'live';
    const items = creditsItems(account);
    const key = JSON.stringify(items);
    if (view.credits.dataset.key !== key) {
      view.credits.dataset.key = key;
      view.credits.replaceChildren(...items.map(([label, value]) => {
        const line = node('div', 'credit');
        line.append(node('span', 'credit-label', `${label}: `), node('span', 'credit-value', value));
        return line;
      }));
    }
    view.credits.hidden = !items.length;
    view.plan.textContent = account.plan || '';
    view.plan.hidden = !account.plan;
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
    const switching = pendingSwap === account.id;
    view.swap.disabled = locked || account.active || !account.eligible;
    view.swap.classList.toggle('working', switching);
    view.swap.classList.toggle('current', account.active);
    view.swap.classList.toggle('accent', !account.active && account.eligible);
    setLabel(view.label, switching ? 'Switching…' : account.active ? 'In use' : !account.eligible ? 'Limit reached' : 'Swap to this');
    view.swap.title = switching ? 'Switching…' : account.active ? 'This account is already in use'
      : !account.eligible ? 'This account has used its limit; it can be picked again after the reset'
      : locked ? 'Another switch is in progress' : `Use ${displayName(account)} for all sessions`;
    view.remove.hidden = state.mode !== 'live' || account.active;
    view.remove.disabled = locked;
    const problem = account.status;
    view.hint.classList.toggle('warn', !!problem);
    const relogin = state.mode === 'live' && /sign in|expired|missing/i.test(problem || '');
    if (relogin) {  // one click back in, with the app's own sign-in (it leaves other logins alone)
      const again = node('button', 'link-button', 'Login expired · Sign in again');
      again.type = 'button';
      again.disabled = (state.signingIn || []).includes(account.provider);
      again.title = `Opens the sign-in: sign in as ${displayName(account)}`;
      again.addEventListener('click', event => { event.stopPropagation(); act('add', { provider: account.provider, id: account.id }); });
      view.hint.replaceChildren(again);  // the whole text is the button
    } else {
      view.hint.textContent = problem
        || (account.active ? 'All sessions use this account' : !account.eligible ? 'Waiting for reset' : '');
    }
    if (!problem && state.mode === 'live' && account.updated_at) view.hint.title = ago(account.updated_at);
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
  // Rebuild only when the set of accounts or their usage windows changes.
  const key = state.mode + '|' + !!state.nameMode + '|' + state.accounts.map(a => a.id + ':' + a.windows.map(w => w.key).join(',')).join(';');
  if (key !== layoutKey) {
    layoutKey = key;
    build();
    void $('accounts').offsetHeight; // commit the empty bars so the first fill transitions
  }
  const live = state.mode === 'live';
  document.body.classList.toggle('live', live);
  $('lab').hidden = live;
  $('reset').title = $('reset').ariaLabel = live ? 'Refresh usage' : 'Reset sample accounts';
  $('add').hidden = !live;
  if (pendingPrefs && !state.busy && state.afk === pendingPrefs.afk && state.autoSwap === pendingPrefs.autoSwap) pendingPrefs = null;
  $('auto-swap').checked = pendingPrefs?.autoSwap ?? state.autoSwap;
  $('afk').checked = pendingPrefs?.afk ?? state.afk;
  $('name-mode').checked = !!state.nameMode;
  const taskbar = !!state.taskbarAvailable;
  $('taskbar-settings').hidden = !taskbar;
  if (taskbar) {
    $('taskbar-view').checked = state.taskbar;
    const displays = state.taskbarDisplays || [];
    const select = $('taskbar-display');
    const key = displays.map(d => d.id + '=' + d.label).join('|');
    if (select.dataset.key !== key) {
      select.dataset.key = key;
      select.replaceChildren(...displays.map(d => new Option(d.label, d.id)));
    }
    select.value = displays.some(d => d.id === state.taskbarDisplay) ? state.taskbarDisplay : 'main';
    $('taskbar-display-row').hidden = displays.length < 2 || !state.taskbar;
  }
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
  updateLog();
}

// ---------- subscription date editor ----------
function openSubscriptionEditor(id, anchor) {
  const account = state.accounts.find(a => a.id === id);
  if (!account || state.mode !== 'live') return;
  const editor = $('sub-editor');
  const sub = account.subscription;
  const at = sub?.at ? new Date(sub.at * 1000) : new Date(Date.now() + 30 * 86400000);
  $('sub-date').value = `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, '0')}-${String(at.getDate()).padStart(2, '0')}`;
  $('sub-ends').checked = !!sub?.ends;
  $('sub-renews').checked = !sub?.ends;
  $('sub-clear').hidden = sub?.source !== 'manual';
  $('sub-source').textContent = sub?.source === 'auto' ? 'Detected from your account. Change it if it is wrong.'
    : sub?.source === 'manual' ? 'Set by you.' : 'Not reported by the provider. Enter it from your billing page.';
  editor.dataset.id = id;
  const r = anchor.getBoundingClientRect();
  editor.style.top = `${r.bottom + window.scrollY + 6}px`;
  editor.style.left = `${Math.max(12, Math.min(r.right + window.scrollX - 280, window.innerWidth - 292))}px`;
  editor.hidden = false;
  $('sub-date').focus();
}
function closeSubscriptionEditor() { $('sub-editor').hidden = true; }
$('sub-editor').addEventListener('click', event => event.stopPropagation());
$('sub-save').addEventListener('click', async () => {
  const value = $('sub-date').value;
  if (!value) return;
  const [y, m, d] = value.split('-').map(Number);
  await act('subscription', { id: $('sub-editor').dataset.id, at: new Date(y, m - 1, d, 12).getTime() / 1000, ends: $('sub-ends').checked });
  closeSubscriptionEditor();
});
$('sub-clear').addEventListener('click', async () => {
  await act('subscription', { id: $('sub-editor').dataset.id, at: null });
  closeSubscriptionEditor();
});
$('sub-cancel').addEventListener('click', closeSubscriptionEditor);
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeSubscriptionEditor(); });

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
    return true;
  } catch (e) {
    pendingPrefs = null;
    toast(e.message, 'error');
    return false;
  } finally {
    submitting = null;
    if (state) render(state);
  }
}

async function observe() {
  if (!token) {
    toast('Open the dashboard from the app to get its private local link.', 'error');
    return;
  }
  let failures = 0;
  request('/api/refresh', { ifOlderThan: 60 }).catch(() => {});  // fresh numbers when the dashboard opens
  while (!stopped) {
    try {
      const next = await request(`/api/state?after=${state?.revision ?? -1}`);
      if (stopped) return;
      render(next);
      failures = 0;
    } catch {
      if (stopped) return;
      if (++failures >= 3) {
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
  minuteTimer = setTimeout(() => { if (state) updateCards(); scheduleMinute(); }, 60000 - Date.now() % 60000 + 50);
}
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state) updateCards();
  scheduleMinute();
});

// ---------- interactions ----------
function setLab(open) {
  $('lab').classList.toggle('open', open);
  $('lab-toggle').setAttribute('aria-expanded', String(open));
  $('lab-body').inert = !open;
}
$('lab-toggle').addEventListener('click', () => setLab(!$('lab').classList.contains('open')));
setLab(false);

const sendPrefs = () => act('preferences', { autoSwap: $('auto-swap').checked, afk: $('afk').checked });
$('auto-swap').addEventListener('change', sendPrefs);
$('afk').addEventListener('change', sendPrefs);
$('name-mode').addEventListener('change', () => act('names', { on: $('name-mode').checked }));
$('taskbar-view').addEventListener('change', () => act('taskbar', { on: $('taskbar-view').checked }));
$('taskbar-display').addEventListener('change', () => act('taskbar', { display: $('taskbar-display').value }));
$('run').addEventListener('click', () => act('run', { scenario: $('scenario').value }, $('run')));
$('continue').addEventListener('click', () => act('continue', {}, $('continue')));
$('stop').addEventListener('click', () => act('stop'));
$('reset').addEventListener('click', () => {
  const icon = $('reset');
  icon.classList.remove('spin'); void icon.offsetWidth; icon.classList.add('spin');
  act('reset');
});
$('add').addEventListener('click', event => {
  event.stopPropagation();
  $('auto-menu').hidden = true;
  $('add-menu').hidden = !$('add-menu').hidden;
});
for (const item of document.querySelectorAll('[data-add]')) {
  item.addEventListener('click', () => { $('add-menu').hidden = true; act('add', { provider: item.dataset.add }); });
}
$('auto-button').addEventListener('click', event => {
  event.stopPropagation();
  $('add-menu').hidden = true;
  const menu = $('auto-menu');
  menu.hidden = !menu.hidden;
  $('auto-button').setAttribute('aria-expanded', String(!menu.hidden));
});
$('auto-menu').addEventListener('click', event => event.stopPropagation());  // toggling keeps it open
document.addEventListener('keydown', event => { if (event.key === 'Escape') $('auto-menu').hidden = true; });
document.addEventListener('click', () => {
  $('add-menu').hidden = true;
  $('auto-menu').hidden = true;
  $('auto-button').setAttribute('aria-expanded', 'false');
  closeSubscriptionEditor();
});

scheduleMinute();
observe();
