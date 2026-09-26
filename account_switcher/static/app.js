'use strict';
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const $ = id => document.getElementById(id);
let state = null, stopped = false, submitting = false, pendingPrefs = null;
const rowViews = new Map();
function error(message) { $('error').textContent = message; $('error').hidden = false; }
function resetIn(timestamp) { const m = Math.max(0, Math.floor((timestamp * 1000 - Date.now()) / 60000)); return m >= 1440 ? `${Math.floor(m / 1440)}d ${Math.floor(m % 1440 / 60)}h` : `${Math.floor(m / 60)}h ${m % 60}m`; }
function node(tag, className, text) { const n = document.createElement(tag); if (className) n.className = className; if (text !== undefined) n.textContent = text; return n; }
function providerIcon(provider) {
  const logo = node('img', `provider-icon ${provider}`);
  logo.src = `/assets/${provider}.png`; logo.alt = ''; logo.setAttribute('aria-hidden', 'true');
  return logo;
}
function makeUsage(label) {
  const box = node('div','usage'), top = node('div','usage-top'), value = node('strong'), meter = node('div','meter'), fill = node('div','fill'), reset = node('div','reset');
  top.append(node('span','',label),value); meter.setAttribute('role','progressbar'); meter.setAttribute('aria-label',`${label} quota remaining`); meter.setAttribute('aria-valuemin','0'); meter.setAttribute('aria-valuemax','100'); meter.append(fill); box.append(top,meter,reset);
  return {box,value,meter,fill,reset};
}
function updateUsage(view, used, timestamp) {
  const left = Math.max(0,100-used); view.value.textContent = `${Math.round(left)}%`;
  view.box.className = `usage${left <= 10 ? ' danger' : left <= 30 ? ' warning' : ''}`;
  view.fill.style.transform = `scaleX(${left/100})`; view.meter.setAttribute('aria-valuenow',String(left)); view.reset.textContent = `Resets in ${resetIn(timestamp)}`;
}
function buildRows() {
  for (const provider of ['codex','claude']) {
    const section = node('section','provider-group'), heading = node('div',`group-heading ${provider}`), title = node('h2','',provider === 'codex' ? 'Codex' : 'Claude');
    title.append(node('span','provider-count',String(state.accounts.filter(a=>a.provider===provider).length))); heading.append(providerIcon(provider),title,node('span','group-caption',provider === 'codex' ? 'Selection preview' : 'CLI recovery enabled'));
    section.append(heading); $('accounts').append(section);
    for (const account of state.accounts.filter(a=>a.provider===provider)) {
      const row = node('article','account-row'), identity = node('div','account-identity'), text = node('div','identity-text'), alias = node('div','alias',account.alias.split(' · ')[1].replace(' (synthetic)','')), subtitle = node('div','identity-subtitle','Sample account · '+(provider === 'claude' ? 'Claude Code' : 'Codex'));
      const avatar = node('span','avatar'); avatar.append(providerIcon(provider)); identity.append(avatar); text.append(alias,subtitle); identity.append(text);
      const short = makeUsage('5 hours'), weekly = makeUsage('Weekly'), swap = node('button','swap','Swap');
      swap.addEventListener('click',()=>act('swap',{id:account.id})); row.setAttribute('aria-label',account.alias.replace(' (synthetic)','')); row.append(identity,short.box,weekly.box,swap); section.append(row); rowViews.set(account.id,{row,short,weekly,swap,subtitle});
    }
  }
}
function cards() {
  if (!rowViews.size) buildRows();
  for (const account of state.accounts) {
    const view=rowViews.get(account.id);
    view.row.classList.toggle('active',account.active); updateUsage(view.short,account.five_hour,account.reset_at); updateUsage(view.weekly,account.weekly,account.weekly_reset_at);
    view.swap.disabled=state.busy||submitting||account.active||!account.eligible; view.swap.textContent=!account.eligible?'Exhausted':account.active?'✓ Active':'Swap';
    view.swap.className=`swap${!account.eligible?' exhausted':account.active?' current':''}`;
  }
}
function render(next) {
  state=next; $('connection').textContent='Local server connected'; $('connection-dot').classList.add('ready');
  if(pendingPrefs&&!state.busy&&state.afk===pendingPrefs.afk&&state.autoSwap===pendingPrefs.autoSwap)pendingPrefs=null;
  $('auto-swap').checked=pendingPrefs?.autoSwap??state.autoSwap; $('afk').checked=pendingPrefs?.afk??state.afk; $('status').textContent=state.busy&&state.status==='Ready'?'Starting…':state.status; $('backend').textContent=state.backend;
  $('session-id').textContent=state.sessionId?`Session ${state.sessionId.slice(0,8)}`:'No active session';
  if(state.output&&$('output').textContent!==state.output){$('output').textContent=state.output;$('output').scrollTop=$('output').scrollHeight;}
  if(!state.output&&state.status==='Stopped')$('output').textContent='Session stopped. Choose a scenario to run another test.';
  $('log').textContent=state.log.join('\n');
  for(const id of ['run','continue','reset','auto-swap','afk','scenario'])$(id).disabled=state.busy||submitting;
  $('continue').disabled||=!state.sessionId; cards();
}
async function request(path,body) {
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{Authorization:`Bearer ${token}`,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  const result=await response.json();if(!response.ok)throw new Error(result.error||`Request failed (${response.status})`);return result;
}
async function act(action,body={}) {
  if(submitting)return;submitting=true;if(action==='preferences')pendingPrefs=body;$('error').hidden=true;if(state)render(state);
  try{await request(`/api/${action}`,body);}catch(e){pendingPrefs=null;error(e.message);}finally{submitting=false;if(state)render(state);}
}
async function observe() {
  if(!token){error('Launch the dashboard to obtain its private local URL.');$('connection').textContent='Launch URL needed';return;}
  let failures=0;while(!stopped){try{const next=await request(`/api/state?after=${state?.revision??-1}`);if(stopped)return;render(next);failures=0;}catch(e){if(stopped)return;$('connection-dot').classList.remove('ready');$('connection').textContent='Disconnected';if(++failures>=3){error('The local server has stopped. Launch the app to reconnect.');break;}await new Promise(resolve=>setTimeout(resolve,1500));}}
}
$('auto-swap').addEventListener('change',()=>act('preferences',{autoSwap:$('auto-swap').checked,afk:$('afk').checked}));
$('afk').addEventListener('change',()=>act('preferences',{autoSwap:$('auto-swap').checked,afk:$('afk').checked}));
$('run').addEventListener('click',()=>act('run',{scenario:$('scenario').value}));$('continue').addEventListener('click',()=>act('continue'));$('stop').addEventListener('click',()=>act('stop'));$('reset').addEventListener('click',()=>act('reset'));
$('shutdown').addEventListener('click',async()=>{try{await request('/api/shutdown',{});stopped=true;$('connection').textContent='Shutting down';$('connection-dot').classList.remove('ready');$('status').textContent='Stopping local processes';document.querySelectorAll('button,input,select').forEach(n=>n.disabled=true);}catch(e){error(e.message);}});
observe();
