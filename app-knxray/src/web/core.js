// KNX-Ray. Plain scripts, one scope: core.js (state, helpers, the drawer), views.js (overview, findings,
// tables), telegrams.js (monitor and archive), charts.js (charts and statistics), main.js (start).
// Telegrams and the state of the connections arrive over one WebSocket; everything else is asked of api/.
'use strict';
// Whether there is a project to be in at all: without one, nothing is 'not in the project'
const projectLoaded = () => Boolean((state.project || {}).loaded);
// Names from the project, from Home Assistant and from the bus are not ours: they only ever reach the page
// through esc() or textContent
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const $ = id => document.getElementById(id);
const query = o => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString();
const PARAMS = new URLSearchParams(window.location.search);

let state = {bus: {interfaces: []}, ha: {}, project: {}, telegrams: {}, findings: {}, job: {}, recorder: {}};
let model = {addresses: [], devices: [], entities: [], stray_entities: [], stray_devices: [], findings: [], catalog: {}, load: [], project: {}};
let modelRevision = -1;
let activeTab = 'overview';
// Lookups rebuilt with every model: address -> row, and which findings point at what
let gaIndex = {}, deviceIndex = {}, issueIndex = {};

async function api(path, options) {
  const opts = options || {};
  if (opts.json !== undefined) { opts.method = opts.method || 'POST'; opts.body = JSON.stringify(opts.json); opts.headers = {'Content-Type': 'application/json'}; }
  const r = await fetch('api/' + path, opts);
  let data = null; try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
function toast(message, error) {
  const el = document.createElement('div'); el.className = 'toast' + (error ? ' err' : ''); el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), error ? 6500 : 3500);
}
// Run something the person asked for; what goes wrong is told, never swallowed
async function attempt(job, done) {
  try { const out = await job(); if (done) toast(done); return out === undefined ? true : out; } catch (e) { toast(e.message, true); return undefined; }
}
// A small dialog: a question, some fields, OK and Cancel. Resolves to the fields, or null.
function ask({title, text, fields = [], ok = 'OK', danger = false}) {
  return new Promise(resolve => {
    const back = document.createElement('div'); back.className = 'modal-back';
    back.innerHTML = `<form class="modal" role="dialog" aria-modal="true"><h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ''}` +
      fields.map(f => `<label for="ask-${esc(f.name)}">${esc(f.label)}</label>` + (f.options
        ? `<select id="ask-${esc(f.name)}">${Object.entries(f.options).map(([v, l]) => `<option value="${esc(v)}"${v === f.value ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`
        : `<input type="text" id="ask-${esc(f.name)}" value="${esc(f.value || '')}" placeholder="${esc(f.placeholder || '')}" autocomplete="off" spellcheck="false" autocapitalize="off">`)).join('') +
      `<div class="foot"><button type="button" class="btn" data-cancel>Cancel</button><button type="submit" class="btn ${danger ? 'danger' : 'primary'}">${esc(ok)}</button></div></form>`;
    const close = value => { back.remove(); document.removeEventListener('keydown', onKey, true); resolve(value); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close(null); } };
    document.addEventListener('keydown', onKey, true);
    back.addEventListener('mousedown', e => { if (e.target === back) close(null); });
    back.querySelector('[data-cancel]').addEventListener('click', () => close(null));
    back.querySelector('form').addEventListener('submit', e => {
      e.preventDefault();
      close(Object.fromEntries(fields.map(f => [f.name, back.querySelector('#ask-' + CSS.escape(f.name)).value.trim()])));
    });
    $('modal-root').appendChild(back);
    (back.querySelector('input, select') || back.querySelector('[type=submit]')).focus();
  });
}

// ---- how things are written ----
const pad = n => String(n).padStart(2, '0');
function fmtClock(ts, ms) {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` + (ms ? '.' + String(d.getMilliseconds()).padStart(3, '0') : '');
}
function fmtDay(ts) { const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
function fmtWhen(ts) {
  if (!ts) return '–';
  return fmtDay(ts) === fmtDay(Date.now() / 1000) ? fmtClock(ts) : `${fmtDay(ts)} ${fmtClock(ts).slice(0, 5)}`;
}
function fmtAgo(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
function fmtSpan(s) {
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 129600) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} d`;
}
const fmtCount = n => n == null ? '–' : n >= 1e6 ? (n / 1e6).toFixed(1) + ' M' : n >= 1e4 ? Math.round(n / 1e3) + ' k' : String(n);
const fmtSize = n => n == null ? '–' : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(2)} GB`;
// Sorting addresses the way they read: 1/2/10 after 1/2/9, 1.1.10 after 1.1.9
const addressKey = a => String(a || '').split(/[/.]/).map(p => String(parseInt(p, 10) || 0).padStart(5, '0')).join('');
function nameOf(a) {
  if (String(a).includes('/')) return gaIndex[a] ? gaIndex[a].name || gaIndex[a].name_ha || '' : '';
  if (a && a === (state.ha || {}).address) return 'Home Assistant';
  if (a && a === (state.bus || {}).address) return 'This app';
  return deviceName(deviceIndex[a]);
}
function deviceName(d) {
  if (!d) return '';
  return [d.maker, d.name].filter(Boolean).join(' ');
}
// A group address or a device, as something to click: opens its details
// A group address takes the colour of its main group, so what belongs together looks alike everywhere
const GROUP_HUES = [212, 32, 280, 152, 352, 190, 48, 254, 112, 320, 8, 232];
const hueOf = a => GROUP_HUES[(parseInt(a, 10) || 0) % GROUP_HUES.length];
const ref = a => !a ? '' : String(a).includes('/')
  ? `<button type="button" class="ref ga" style="--h:${hueOf(a)}" data-ref="${esc(a)}">${esc(a)}</button>`
  : `<button type="button" class="ref" data-ref="${esc(a)}">${esc(a)}</button>`;
const refNamed = a => { const n = nameOf(a); return ref(a) + (n ? `<small>${esc(n)}</small>` : ''); };
// The value of a telegram: decoded, or the payload as it is when it could not be read
function valueHtml(t) {
  if (!t || t.type === 'read') return '';
  if (t.text != null) return `<span class="value">${esc(t.text)}</span>${t.unit ? `<span class="unit">${esc(t.unit)}</span>` : ''}`;
  if (!t.raw) return '';
  if (!t.dpt && t.guess != null) return `<span class="value guess" title="No datapoint type is known for this address: read by the size of the payload (0x${esc(t.raw)}). Set the type in ETS, or use the address in a Home Assistant entity.">≈ ${esc(t.guess)}</span>`;
  return `<span class="value ${t.dpt ? 'bad' : 'raw'}" title="${t.dpt ? 'does not fit DPT ' + esc(t.dpt) : 'no datapoint type: the payload as it is'}">0x${esc(t.raw)}</span>`;
}
const typePill = t => `<span class="pill ${esc(t)}">${esc(t)}</span>`;
// The kind of a telegram, and what its frame says when that is out of the ordinary
const kindHtml = t => typePill(t.type) + (t.rep ? '<span class="pill warning" title="Sent again: nobody acknowledged it the first time">repeated</span>' : '') +
  (t.hops != null && t.hops < 5 ? `<span class="pill" title="How many couplers it may still pass">${esc(t.hops)} hops left</span>` : '');
const worst = list => list.some(f => f.severity === 'error') ? 'error' : list.some(f => f.severity === 'warning') ? 'warning' : list.length ? 'info' : '';
const issuesOf = key => (issueIndex[key] || []).filter(f => !f.hidden);
function issueDot(key) {
  const found = issuesOf(key);
  return found.length ? `<span class="dot ${worst(found)}" title="${esc(found.map(f => f.title).join('\n'))}"></span>` : '';
}

// ---- theme: the same toggle as the other apps (auto follows Home Assistant, then light, then dark) ----
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = PARAMS.get('theme');
try { themeChoice = themeChoice || localStorage.getItem('knxray_theme'); } catch (e) { /* only a convenience */ }
if (!THEME_ICONS[themeChoice]) themeChoice = 'auto';
function haDarkMode() {
  try {
    const ha = window.parent !== window && window.parent.document.querySelector('home-assistant');
    return ha && ha.hass && ha.hass.themes ? Boolean(ha.hass.themes.darkMode) : null;
  } catch (e) { return null; }
}
let themeApplied = null;
function applyTheme() {
  const root = document.documentElement;
  const ha = themeChoice === 'auto' ? haDarkMode() : null;
  const wanted = themeChoice === 'auto' && ha === null ? '' : (themeChoice === 'dark' || ha === true) ? 'dark' : 'light';
  if (wanted) root.dataset.theme = wanted; else delete root.dataset.theme;  // without it the stylesheet follows the system
  $('theme-icon').textContent = THEME_ICONS[themeChoice];
  if (themeApplied !== null && themeApplied !== wanted && typeof drawChart === 'function') drawChart();
  themeApplied = wanted;
}
$('theme-toggle').addEventListener('click', () => {
  themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
  try { localStorage.setItem('knxray_theme', themeChoice); } catch (e) { /* only a convenience */ }
  applyTheme();
});
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ---- the look of each page: an icon, a title, what it is for, and its own numbers ----
// Line icons on a 24 grid, drawn with the text colour
const ICONS = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  findings: '<path d="M12 3 2 20.5h20L12 3Z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  monitor: '<path d="M2 12h4l3-8 5 16 3-8h5"/>',
  charts: '<path d="M3 3v18h18"/><path d="m7 15 4-5 4 3 5-7"/>',
  stats: '<path d="M6 20V11M12 20V4M18 20v-6M3 20h18"/>',
  addresses: '<path d="M5 9h15M4 15h15M10 3 8 21M16 3l-2 18"/>',
  devices: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  ha: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/><circle cx="12" cy="14.5" r="2"/>',
  interface: '<path d="M8 3v5M16 3v5M5 8h14v3a7 7 0 0 1-14 0V8Z"/><path d="M12 18v3"/>',
  about: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
  ok: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 7v6M12 16.5v.01"/>',
  // Home Assistant's platforms
  light: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3Z"/>',
  switch: '<rect x="3" y="7" width="18" height="10" rx="5"/><circle cx="16" cy="12" r="3"/>',
  sensor: '<path d="M4 18a8 8 0 1 1 16 0"/><path d="m12 18 4-5"/>',
  binary_sensor: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  cover: '<path d="M4 4h16M5 4v16M19 4v16M5 8h14M5 12h14M5 16h14"/>',
  climate: '<path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0Z"/>',
  expose: '<path d="M5 12h12M13 7l5 5-5 5"/>',
  other: '<circle cx="12" cy="12" r="8"/>',
};
const icon = (name, cls = '') => `<svg class="ico-svg ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.other}</svg>`;
const PAGES = {
  findings: ['Findings', 'What does not add up between the bus, the ETS project and Home Assistant, and what to do about it.'],
  monitor: ['Monitor', 'Every telegram as it happens: live, as a timeline, frame by frame on the wire, and everything recorded.'],
  charts: ['Charts', 'The recorded values of up to four group addresses, from an hour to a month.'],
  stats: ['Statistics', 'How busy the bus is, who talks most, when, and whether the app was listening.'],
  addresses: ['Group addresses', 'Every address of the project and of the bus: its type, last value, senders, objects and entities.'],
  devices: ['Devices', 'Every device of the project and of the bus: what it sends, what it answered, and its documents.'],
  ha: ['Home Assistant', 'The KNX entities of Home Assistant, held against the project, and what was left behind.'],
  interface: ['Interface', 'How the app reaches the bus: an interface of its own, or listening along with Home Assistant.'],
  about: ['Info', 'What KNX-Ray is, where its data comes from, and what to know.'],
};
// A page's own numbers: [value, label, tone]
function pageNumbers(name) {
  const f = state.findings || {}, t = state.telegrams || {}, r = state.recorder || {}, p = state.project || {}, ha = state.ha || {}, bus = state.bus || {};
  const known = model.devices.filter(d => d.known);
  switch (name) {
    case 'findings': return [[f.error || 0, 'errors', f.error ? 'bad' : 'ok'], [f.warning || 0, 'warnings', f.warning ? 'warn' : 'ok'], [f.info || 0, 'notes'], [f.hidden || 0, 'hidden']];
    case 'monitor': return [[t.rate ?? 0, 'a minute'], [fmtCount(t.total), 'since start'], [fmtCount(r.rows), 'recorded']];
    case 'charts': return [[typeof chartAddresses === 'undefined' ? 0 : chartAddresses.length, 'charted'], [fmtCount(r.rows), 'recorded'], [r.oldest ? fmtDay(r.oldest).slice(5) : '–', 'since']];
    case 'stats': return [[fmtCount(r.rows), 'recorded'], [fmtSize(r.bytes), 'on disk'], [r.oldest ? fmtDay(r.oldest).slice(5) : '–', 'since']];
    case 'addresses': return [[p.group_addresses || 0, 'in the project'], [t.addresses || 0, 'seen'], [model.addresses.filter(a => a.known && !a.dpt).length, 'without a type'], [model.addresses.filter(a => (a.users || []).length).length, 'in Home Assistant']];
    case 'devices': return [[p.devices || 0, 'in the project'], [known.filter(d => (d.stats || {}).count).length, 'heard'], [model.devices.filter(d => d.reach && d.reach.reachable).length, 'answered'], [model.devices.filter(d => d.reach && !d.reach.reachable).length, 'silent', model.devices.some(d => d.reach && !d.reach.reachable) ? 'bad' : '']];
    case 'ha': return [[ha.entities ?? '–', 'entities'], [ha.stray || 0, 'stray', ha.stray ? 'warn' : 'ok'], [ha.connected ? 'yes' : 'no', 'on the bus', ha.connected ? 'ok' : 'bad']];
    case 'interface': return [[bus.state === 'connected' ? 'on' : bus.mode === 'ha' ? 'HA' : bus.state || '–', 'own link', bus.state === 'connected' ? 'ok' : bus.state === 'failed' ? 'bad' : ''], [bus.address || '–', 'as'], [(bus.interfaces || []).length, 'found']];
    case 'about': return [[state.version || '–', 'version']];
    default: return [];
  }
}
function renderHead(name) {
  const el = $('head-' + name), page = PAGES[name];
  if (!el || !page) return;
  el.innerHTML = `<span class="ico">${icon(name)}</span><div class="words"><h1>${esc(page[0])}</h1><p>${esc(page[1])}</p></div>` +
    `<div class="kpis">${pageNumbers(name).map(([v, l, tone]) => `<div class="kpi ${tone || ''}"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join('')}</div>`;
}

// ---- tabs ----
const TAB_RENDER = {};
function showTab(name) {
  if (!$('tab-' + name)) return;
  activeTab = name;
  document.querySelectorAll('.topbar .tab[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.toggle('active', el.id === 'tab-' + name));
  renderTab();
}
function renderTab() { renderHead(activeTab); if (TAB_RENDER[activeTab]) TAB_RENDER[activeTab](); }
document.querySelectorAll('.topbar .tab[data-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
document.addEventListener('click', e => {
  const go = e.target.closest('[data-go]');
  if (go) { showTab(go.dataset.go); return; }
  const link = e.target.closest('[data-ref]');
  if (link) { e.stopPropagation(); openDrawer(link.dataset.ref); }
});
// A group of buttons of which one is chosen; calls back with its value
function seg(id, onChange) {
  const el = $(id);
  el.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    el.querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
    onChange(b.dataset.v);
  });
  return () => el.querySelector('button.active').dataset.v;
}

// A table with headers that sort. columns: [{key, label, cls, html(row), sort(row)}]
function table(target, columns, rows, opts = {}) {
  const el = typeof target === 'string' ? $(target) : target;
  const view = el._view || (el._view = {sort: opts.sort || null, asc: opts.asc !== false, limit: opts.limit || 300});
  let list = rows;
  const column = columns.find(c => c.key === view.sort);
  if (column && column.sort) {
    list = [...rows].sort((a, b) => { const x = column.sort(a), y = column.sort(b); return (x < y ? -1 : x > y ? 1 : 0) * (view.asc ? 1 : -1); });
  }
  const shown = list.slice(0, view.limit);
  if (!rows.length) { el.innerHTML = `<div class="empty-state">${esc(opts.empty || 'Nothing here.')}</div>`; return; }
  el.innerHTML = `<table class="list${opts.tight ? ' tight' : ''}"><thead><tr>` +
    columns.map(c => `<th class="${c.cls || ''}${c.sort ? ' sort' : ''}${view.sort === c.key ? ' sorted' + (view.asc ? ' asc' : '') : ''}" data-key="${esc(c.key)}">${esc(c.label)}</th>`).join('') +
    '</tr></thead><tbody>' +
    shown.map((row, i) => `<tr class="${opts.onRow ? 'click' : ''} ${opts.rowClass ? opts.rowClass(row) : ''}" data-i="${i}">` + columns.map(c => `<td class="${c.cls || ''}">${c.html(row)}</td>`).join('') + '</tr>').join('') +
    '</tbody></table>' + (list.length > shown.length ? `<div class="more"><button type="button" class="btn" data-more>Show ${Math.min(500, list.length - shown.length)} more of ${list.length}</button></div>` : '');
  el.onclick = e => {
    const th = e.target.closest('th.sort');
    if (th) {
      if (view.sort === th.dataset.key) view.asc = !view.asc; else { view.sort = th.dataset.key; view.asc = true; }
      table(el, columns, rows, opts); return;
    }
    if (e.target.closest('[data-more]')) { view.limit += 500; table(el, columns, rows, opts); return; }
    if (e.target.closest('button, a')) return;
    const tr = e.target.closest('tbody tr');
    if (tr && opts.onRow) opts.onRow(shown[Number(tr.dataset.i)]);
  };
}

// ---- the model: everything the tables show ----
let modelBusy = false, modelAt = 0;
async function loadModel(force) {
  // The model changes with every telegram's statistics; asking for it a few times a minute is enough
  if (modelBusy || (!force && Date.now() - modelAt < 4000)) return;
  modelBusy = true;
  try {
    model = await api('model');
    modelAt = Date.now(); modelRevision = model.revision;
    gaIndex = Object.fromEntries(model.addresses.map(a => [a.address, a]));
    deviceIndex = Object.fromEntries(model.devices.map(d => [d.address, d]));
    issueIndex = {};
    for (const f of model.findings) {
      // A finding about an entity on an address points at both
      const keys = f.kind === 'entity' ? f.subject.split('|') : [f.subject];
      for (const key of keys) (issueIndex[key] = issueIndex[key] || []).push(f);
    }
    renderTab();
    // Not while somebody is reading a property there: a redraw would wipe what they typed and got
    if (drawerAddress && !$('drawer').querySelector('details[open]')) renderDrawer();
  } catch (e) { /* the next tick tries again */ } finally { modelBusy = false; }
}

// ---- the live connection to the app ----
let socket = null, socketUp = false;
function connect() {
  const url = new URL('api/ws', window.location.href); url.protocol = url.protocol.replace('http', 'ws'); url.search = '';
  socket = new WebSocket(url);
  socket.onopen = () => { socketUp = true; };
  socket.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.t === 'state') {
      const changed = m.revision !== state.revision;
      // Another interface was chosen: what this page had collected belongs to the old one
      const afresh = state.epoch !== undefined && m.epoch !== state.epoch;
      state = m; renderState();
      if (afresh) resetPages();
      if (changed) loadModel(true);
    } else if (m.t === 'telegrams') onTelegrams(m.items);
  };
  socket.onclose = () => { socket = null; socketUp = false; renderState(); setTimeout(connect, 1500); };
}

function resetPages() {
  live = []; liveDirty = true; wireFrames = []; wireLastId = 0;
  archive.items = []; graphKey = ''; statsData = null; chartData = {};
  if (drawerAddress) closeDrawer();
  loadModel(true);
}
// The chosen interface by its name, or by its address when it was entered by hand
function interfaceLabel(bus) {
  const choice = bus.choice || {};
  return choice.name || choice.host || (choice.mode === 'routing' ? `Routing ${choice.multicast || ''}`.trim() : 'Own interface');
}
function renderState() {
  const link = $('link-state'), bus = state.bus || {}, ha = state.ha || {};
  let cls = '', text;
  link.title = '';
  if (!socketUp) { cls = 'bad'; text = 'App not reachable'; }
  else if (state.source === 'bus') { cls = 'on'; text = interfaceLabel(bus); link.title = `On the bus as ${bus.address || '?'}`; }
  else if (state.source === 'ha') { cls = ha.connected ? 'on' : 'bad'; text = ha.connected ? 'Through Home Assistant' : 'Home Assistant has no bus'; }
  else if (bus.state === 'connecting') { cls = 'wait'; text = 'Connecting…'; }
  else { cls = 'bad'; text = 'No telegrams'; }
  link.className = 'link-state ' + cls; $('link-text').textContent = text;
  const f = state.findings || {}, badge = $('tab-count-findings');
  const n = (f.error || 0) + (f.warning || 0);
  badge.hidden = !n; badge.textContent = n; badge.className = 'count' + (f.error ? '' : ' warn');
  const t = state.telegrams || {}, r = state.recorder || {};
  $('statusbar').innerHTML = [
    `<span>source <b>${esc(state.source === 'bus' ? 'own interface' : state.source === 'ha' ? 'Home Assistant' : 'none')}</b></span>`,
    `<span><b>${esc(t.rate ?? 0)}</b> telegrams/min</span>`,
    `<span class="hide-sm"><b>${fmtCount(t.total)}</b> since ${t.since ? fmtWhen(t.since) : '–'}</span>`,
    `<span class="hide-sm">recorded <b>${fmtCount(r.rows)}</b> (${fmtSize(r.bytes)})</span>`,
    state.project && state.project.loaded ? `<span class="hide-md">project <b>${esc(state.project.name || '')}</b></span>` : '<span>no project</span>',
    `<span class="spacer"></span><span>${esc(state.version || '')}</span>`,
  ].join('');
  renderBanners();
  renderHead(activeTab);
  if (activeTab === 'overview' || activeTab === 'interface' || activeTab === 'devices' || (activeTab === 'monitor' && typeof monitorMode !== 'undefined' && monitorMode === 'wire')) renderTab();
}
function renderBanners() {
  const out = [], ha = state.ha || {}, bus = state.bus || {};
  if (socketUp && !ha.loaded && ha.error) out.push(`<div class="banner bad">${esc(ha.error)}</div>`);
  else if (socketUp && ha.loaded && !(state.project || {}).loaded) out.push('<div class="banner">Home Assistant has no ETS project. Upload the .knxproj in the KNX integration\'s settings: without it there are no names, no datapoint types and few checks.</div>');
  if (bus.state === 'failed') out.push(`<div class="banner">The chosen KNX interface cannot be reached (${esc(bus.error)}). Telegrams come through Home Assistant meanwhile. <button class="link-btn" data-go="interface">Interface</button></div>`);
  $('banners').innerHTML = out.join('');
}

// ---- the drawer: one group address or one device ----
let drawerAddress = null, drawerDetail = null;
async function openDrawer(address) {
  drawerAddress = address; drawerDetail = null;
  $('drawer').hidden = false;
  renderDrawer();
  try { drawerDetail = await api('detail?' + query({address})); } catch (e) { drawerDetail = {objects: [], telegrams: []}; }
  if (drawerAddress === address) renderDrawer();
}
function closeDrawer() { drawerAddress = null; $('drawer').hidden = true; }
document.addEventListener('keydown', e => { if (e.key === 'Escape' && drawerAddress && !document.querySelector('.modal-back')) closeDrawer(); });
const kv = rows => '<table class="kv">' + rows.filter(r => r && r[1] !== '' && r[1] != null).map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td>${v}</td></tr>`).join('') + '</table>';
const flagsHtml = f => '<span class="flags">' + [['C', 'communication'], ['R', 'read'], ['W', 'write'], ['T', 'transmit'], ['U', 'update']].map(([l, k]) => f[k] ? l : '<s>-</s>').join('') + '</span>';
function findingsHtml(key) {
  const found = issueIndex[key] || [];
  if (!found.length) return '';
  return '<section><div class="title"><span>Findings</span></div>' + found.map(f =>
    `<div class="finding-row short"><span class="pill ${esc(f.severity)}">${esc(f.severity === 'info' ? 'note' : f.severity)}</span><div class="what"><b>${esc(f.title)}</b><div class="muted">${esc(f.detail)}</div><div class="muted" style="margin-top:4px">${esc((model.catalog[f.code] || {}).fix || '')}</div></div></div>`).join('') + '</section>';
}
function telegramRows(list, forDevice) {
  if (!list.length) return '<div class="empty-state">No telegrams since the app started.</div>';
  return '<table class="list tight"><thead><tr><th>Time</th><th>' + (forDevice ? 'To' : 'From') + '</th><th>Kind</th><th>Value</th></tr></thead><tbody>' +
    list.slice(0, 60).map(t => `<tr><td class="addr" title="${esc(fmtDay(t.ts))}">${fmtClock(t.ts, true)}</td><td>${refNamed(forDevice ? t.dst : t.src)}</td><td>${typePill(t.type)}</td><td>${valueHtml(t)}</td></tr>`).join('') + '</tbody></table>';
}
// What is kept with a device: manuals and data sheets as PDF, and links
function documentsHtml(device) {
  const list = (drawerDetail || {}).documents;
  if (!list) return '';
  const product = [device.maker, device.order_number || device.product].filter(Boolean).join(' ');
  const source = doc => doc.url ? esc(doc.url) : 'api/documents/file/' + esc(doc.id);
  const scope = doc => doc.product ? ' · for every ' + esc(product || 'device of this product') : '';
  const acts = doc => `<span class="acts"><button class="link-btn" data-doc-rename="${esc(doc.id)}" data-title="${esc(doc.title)}">Rename</button><button class="link-btn danger" data-doc-delete="${esc(doc.id)}" data-title="${esc(doc.title)}">Delete</button></span>`;
  const pictures = list.filter(doc => doc.kind === 'image'), papers = list.filter(doc => doc.kind !== 'image');
  return '<section><div class="title"><span>Documents and pictures</span><span><button class="link-btn" data-doc-pdf>Add file</button> · <button class="link-btn" data-doc-link>Add link</button></span></div>' +
    (pictures.length ? '<div class="pictures">' + pictures.map(doc => `<figure><a href="${source(doc)}" target="_blank" rel="noopener noreferrer"><img src="${source(doc)}" alt="${esc(doc.title)}" loading="lazy" referrerpolicy="no-referrer"></a><figcaption><span title="${esc(doc.title)}">${esc(doc.title)}</span><span class="muted">${doc.url ? esc(new URL(doc.url).hostname) : fmtSize(doc.size)}${scope(doc)}</span>${acts(doc)}</figcaption></figure>`).join('') + '</div>' : '') +
    (papers.length ? papers.map(doc => `<div class="doc"><a class="doc-name" href="${source(doc)}" target="_blank" rel="noopener noreferrer">${esc(doc.title)}</a>` +
      `<span class="muted">${doc.kind === 'pdf' ? 'PDF, ' + fmtSize(doc.size) : esc(new URL(doc.url).hostname)}${scope(doc)}</span>${acts(doc)}</div>`).join('')
      : list.length ? '' : '<div class="muted">Nothing yet. Keep the manual, a photo of the wiring or the maker\'s page here for when you need it.</div>') +
    '<input type="file" id="doc-file" accept="application/pdf,.pdf,image/jpeg,image/png,image/gif,image/webp" hidden></section>';
}
async function reloadDrawer() {
  const address = drawerAddress;
  try { const detail = await api('detail?' + query({address})); if (drawerAddress === address) { drawerDetail = detail; renderDrawer(); } } catch (e) { /* the next model brings it */ }
}
// Whether a new document is for this device only, or for every device of the same product
function documentScope(device) {
  const product = [device.maker, device.order_number || device.product].filter(Boolean).join(' ');
  return (drawerDetail || {}).product_key ? [{name: 'product', label: 'For', options: {'': `This device only (${device.address})`, product: `Every ${product}`}, value: ''}] : [];
}
// The group addresses read from a device, held against what the project links to it
function heldHtml(device, held) {
  const linked = new Set(((drawerDetail || {}).objects || []).flatMap(o => o.flags.communication ? o.group_addresses : []));
  const known = device.known && drawerDetail;
  const row = (ga, note) => `<div>${ref(ga)} <span class="muted">${esc(nameOf(ga))}</span>${note}</div>`;
  return `<div class="title" style="margin-top:12px"><span>Group addresses in the device</span><span class="hint">${held.length} read from it</span></div>` +
    held.map(ga => row(ga, known && !linked.has(ga) ? ' <span class="pill warning">not linked in the project</span>' : '')).join('') +
    (known ? [...linked].filter(ga => !held.includes(ga)).map(ga => row(ga, ' <span class="pill warning">in the project, not in the device</span>')).join('') : '') +
    (held.length ? '' : '<div class="muted">None.</div>');
}
function renderDrawer() {
  const a = drawerAddress, el = $('drawer');
  if (!a) return;
  const isGa = a.includes('/'), detail = drawerDetail;
  let html;
  if (isGa) {
    const ga = gaIndex[a] || {address: a, name: '', known: false, stats: {}, users: []};
    const s = ga.stats || {}, last = s.last_value;
    const senders = Object.entries(s.sources || {}).sort((x, y) => y[1] - x[1]);
    html = `<div class="drawer-head"><div><div class="addr">${esc(a)}</div><h2>${esc(ga.name || ga.name_ha || (ga.known || !(state.project || {}).loaded ? '' : 'Not in the project'))}</h2></div><button class="x" data-close aria-label="Close">&times;</button></div>` +
      '<section>' + kv([
        ['Datapoint type', ga.dpt ? esc(ga.dpt) : ga.dpt_ha ? `${esc(ga.dpt_ha)} <span class="muted">the type Home Assistant uses; not in a project</span>` : ga.dpt_objects ? `<span class="pill warning">not set</span> <span class="muted">read as ${esc(ga.dpt_objects)}, the type of its objects</span>` : ga.known ? '<span class="pill warning">not set</span>' : ''],
        ['Range', esc(ga.range || '')],
        ['Description', esc(ga.description || '')],
        ['Last value', last ? `${valueHtml(last)} <span class="muted">from ${ref(last.src)}, ${esc(fmtAgo(last.ts))}</span>` : ''],
        ['Telegrams', s.count ? `${s.count} <span class="muted">(${s.writes} write, ${s.reads} read, ${s.responses} response)</span>` : 'none since the app started'],
        ['Busiest minute', s.peak > 1 ? `${s.peak} telegrams` : ''],
        ['Reads', s.reads ? `${s.answered} answered, ${s.unanswered} not` + (s.multi_answer ? `, ${s.multi_answer} by several` : '') : ''],
        ['Sent by', senders.map(([src, n]) => `${ref(src)} <span class="muted">${esc(nameOf(src))} × ${n}</span>`).join('<br>')],
        ['By the project', ga.known ? `${ga.senders} sending, ${ga.listeners} listening, ${ga.answerers} answering reads` : ''],
      ]) + `<div class="toolbar" style="margin-top:10px"><button class="btn" data-read>Read now</button><button class="btn" data-chart>Chart</button><button class="btn" data-archive>In the archive</button></div><div id="read-result"></div></section>` +
      findingsHtml(a) +
      ((ga.users || []).length ? '<section><div class="title"><span>Home Assistant</span></div>' + ga.users.map(u =>
        `<div>${issueDot(u.entity_id + '|' + a)}<span class="mono">${esc(u.entity_id || u.name)}</span> <span class="muted">${esc(u.role || '')}${u.use ? ' · ' + esc(u.use === 'state' ? 'listens' : u.use === 'write' ? 'writes' : u.use) : ''}${u.expects ? ' · needs DPT ' + esc(u.expects) : ''}</span></div>`).join('') + '</section>' : '') +
      '<section class="flush"><div class="title"><span>Linked objects</span></div>' + (detail ? (detail.objects.length
        ? '<table class="list tight"><thead><tr><th>Device</th><th>Object</th><th>Type</th><th>Flags</th></tr></thead><tbody>' + detail.objects.map(o =>
          `<tr><td>${ref(o.device)}<small>${esc(o.device_name)}</small></td><td>${esc(o.number)} ${esc(o.name)}</td><td class="addr">${esc(o.dpts.join(', '))}</td><td>${flagsHtml(o.flags)}</td></tr>`).join('') + '</tbody></table>'
        : '<div class="empty-state">No object is linked to this address in the project.</div>') : '<div class="empty-state">…</div>') + '</section>' +
      '<section class="flush"><div class="title"><span>Last telegrams</span></div>' + (detail ? telegramRows(detail.telegrams, false) : '') + '</section>';
  } else {
    const d = deviceIndex[a] || {address: a, known: false, stats: {}};
    const s = d.stats || {}, r = d.reach;
    const targets = Object.entries(s.addresses || {}).sort((x, y) => y[1] - x[1]);
    html = `<div class="drawer-head"><div><div class="addr">${esc(a)}</div><h2>${esc(deviceName(d) || (d.known || !(state.project || {}).loaded ? '' : 'Not in the project'))}</h2></div><button class="x" data-close aria-label="Close">&times;</button></div>` +
      '<section>' + kv([
        ['Product', esc(d.product || '')],
        ['Order number', esc(d.order_number || '')],
        ['Application', esc(d.application || '')],
        ['Line', d.line ? `${esc(d.line)} <span class="muted">${esc(((model.project.lines || {})[d.line] || {}).name || '')} ${esc(d.medium || '')}</span>` : ''],
        ['Room', esc(d.room || '')],
        ['Not downloaded by ETS', (d.pending || []).length ? `<span class="pill warning">${esc(d.pending.join(' '))}</span>` : ''],
        ['Telegrams', s.count ? `${s.count} <span class="muted">to ${Object.keys(s.addresses || {}).length} addresses, last ${esc(fmtAgo(s.last))}</span>` : 'none since the app started'],
        ['Busiest minute', s.peak > 1 ? `${s.peak} telegrams` : ''],
      ]) + (d.own ? '' : `<div class="toolbar" style="margin-top:10px"><button class="btn" data-job="ping">Check</button><button class="btn" data-job="info">Read details</button><button class="btn" data-job="deep" title="Every object, the state of each table, and the group addresses the device really holds. Takes a minute.">Read everything</button>${r ? '<button class="btn" data-forget>Forget answer</button>' : ''}</div>`) + '</section>' +
      documentsHtml(d) +
      (r ? '<section><div class="title"><span>What it answered</span><span class="hint">' + esc(fmtAgo(r.checked)) + '</span></div>' + kv([
        ['Reachable', r.reachable ? '<span class="pill ok">yes</span>' : '<span class="pill error">no answer</span>'],
        ['Mask', esc(r.mask || '')], ['Serial number', esc(r.serial || '')], ['Maker id', esc(r.maker_id ?? '')],
        ['Order info', esc(r.order_info || '')], ['Firmware', esc(r.firmware || '')], ['Application id', esc(r.program || '')],
        ['Application', r.load_state ? `<span class="pill ${r.load_state === 'loaded' ? 'ok' : 'error'}">${esc(r.load_state)}</span>` : ''],
        ['Programming mode', r.progmode == null ? '' : r.progmode ? '<span class="pill warning">on</span>' : 'off'],
        ['Application running', r.stopped == null ? '' : r.stopped ? '<span class="pill error">stopped</span>' : 'yes'],
        ['Its address used twice', r.address_clash == null ? '' : r.address_clash ? '<span class="pill error">yes, it saw another sender</span>' : 'no'],
        ['Programmed', r.downloads == null ? '' : `${esc(r.downloads)} times`],
        ['Longest telegram', r.max_apdu ? `${esc(r.max_apdu)} bytes` : ''],
        ['Note', r.reachable ? esc(r.note || '') : ''],
      ]) + (r.objects ? '<div class="title" style="margin-top:12px"><span>What is in it</span></div>' + r.objects.map(o =>
        `<div><span class="muted mono">${esc(o.index)}</span> ${esc(o.type)} <span class="muted">${esc(o.properties)} properties</span>${o.load_state ? ` <span class="pill ${o.load_state === 'loaded' ? 'ok' : 'error'}">${esc(o.load_state)}</span>` : ''}${o.error ? ` <span class="pill error">error ${esc(o.error)}</span>` : ''}</div>`).join('') : '') +
      (r.group_addresses ? heldHtml(d, r.group_addresses) : '') +
      (r.held_objects ? '<div class="title" style="margin-top:12px"><span>Objects in the device</span><span class="hint">' + r.held_objects.length + ' in use, as the device holds them</span></div><table class="list tight"><tbody>' + r.held_objects.map(o =>
        `<tr><td class="addr">${esc(o.number)}</td><td>${flagsHtml(o.flags)}</td><td>${o.group_addresses.map(ref).join(' ') || '<span class="muted">not linked</span>'}</td></tr>`).join('') + '</tbody></table>' : '') +
      (r.router ? '<div class="title" style="margin-top:12px"><span>What the coupler passes</span></div>' + kv([
        ['Towards the main line', r.router.main_groups_low ? `main groups 0–13: ${esc(r.router.main_groups_low)}; 14–31: ${esc(r.router.main_groups_high)}` : ''],
        ['Towards the line', r.router.sub_groups_low ? `main groups 0–13: ${esc(r.router.sub_groups_low)}; 14–31: ${esc(r.router.sub_groups_high)}` : ''],
        ['Hop count it sets', esc(r.router.hop_count ?? '')],
        ['Filter table', r.router.filter_table == null ? '' : r.router.filter_table ? 'in use' : 'not in use'],
      ]) : '') + '</section>' : '') +
      (d.own || (state.bus || {}).state !== 'connected' ? '' : `<section><details><summary class="muted" style="cursor:pointer">Read a property or memory</summary>
        <form class="form-row" id="raw-form" style="margin-top:10px">
          <select id="raw-what" aria-label="What"><option value="property">Property</option><option value="memory">Memory</option></select>
          <input type="text" id="raw-a" placeholder="object 0" aria-label="Object index, or memory address" style="width:110px">
          <input type="text" id="raw-b" placeholder="property 11" aria-label="Property id, or number of bytes" style="width:110px">
          <input type="text" id="raw-c" placeholder="elements 1" aria-label="Number of elements" style="width:100px">
          <button type="submit" class="btn">Read</button>
        </form>
        <p class="muted" style="margin-top:6px">A property: object index, property id, number of elements. Memory: address (0x4000) and number of bytes. Numbers as 23 or 0x17.</p>
        <pre class="mono" id="raw-result" style="margin-top:8px;white-space:pre-wrap;overflow-wrap:anywhere"></pre></details></section>`) +
      findingsHtml(a) +
      (targets.length ? '<section><div class="title"><span>Sends to</span></div>' + targets.slice(0, 30).map(([ga, n]) => `<div>${issueDot(ga)}${ref(ga)} <span class="muted">${esc(nameOf(ga))} × ${n}</span></div>`).join('') + '</section>' : '') +
      '<section class="flush"><div class="title"><span>Objects</span></div>' + (detail ? (detail.objects.length
        ? '<table class="list tight"><thead><tr><th>Object</th><th>Type</th><th>Flags</th><th>Group addresses</th></tr></thead><tbody>' + detail.objects.map(o =>
          `<tr><td>${esc(o.number)} ${esc(o.name)}</td><td class="addr">${esc(o.dpts.join(', '))}</td><td>${flagsHtml(o.flags)}</td><td>${o.group_addresses.map(ref).join(' ') || '<span class="muted">not linked</span>'}</td></tr>`).join('') + '</tbody></table>'
        : '<div class="empty-state">The project has no objects for this device.</div>') : '<div class="empty-state">…</div>') + '</section>' +
      '<section class="flush"><div class="title"><span>Last telegrams</span></div>' + (detail ? telegramRows(detail.telegrams, true) : '') + '</section>';
  }
  const scroll = el.scrollTop;
  el.innerHTML = html; el.scrollTop = scroll;
}
$('drawer').addEventListener('change', e => {
  if (e.target.id !== 'raw-what') return;
  const memory = e.target.value === 'memory';
  $('raw-a').placeholder = memory ? 'address 0x4000' : 'object 0'; $('raw-b').placeholder = memory ? 'bytes 16' : 'property 11'; $('raw-c').hidden = memory;
});
$('drawer').addEventListener('submit', async e => {
  if (e.target.id !== 'raw-form') return;
  e.preventDefault();
  const memory = $('raw-what').value === 'memory', box = $('raw-result');
  box.textContent = 'Asking…';
  try {
    const out = await api('raw', {json: {address: drawerAddress, what: $('raw-what').value, a: $('raw-a').value.trim() || 0, b: $('raw-b').value.trim() || (memory ? 16 : 11), c: $('raw-c').value.trim() || 1}});
    const bytes = out.hex.match(/../g) || [];
    box.textContent = `${out.length} bytes\n` + bytes.map((b, i) => b + ((i + 1) % 16 ? ' ' : '\n')).join('').toUpperCase() +
      (bytes.length <= 4 && bytes.length ? `\n= ${parseInt(out.hex, 16)}` : '') + (/^(?:[2-7][0-9a-f])+(?:00)*$/i.test(out.hex) ? `\n"${bytes.map(b => parseInt(b, 16)).filter(Boolean).map(n => String.fromCharCode(n)).join('')}"` : '');
  } catch (err) { box.textContent = err.message; }
});
$('drawer').addEventListener('change', async e => {
  if (e.target.id !== 'doc-file' || !e.target.files.length) return;
  const file = e.target.files[0], device = deviceIndex[drawerAddress] || {address: drawerAddress};
  const answer = await ask({title: 'Add a PDF or a picture', fields: [{name: 'title', label: 'Name', value: file.name.replace(/\.(pdf|jpe?g|png|gif|webp)$/i, '')}, ...documentScope(device)], ok: 'Add'});
  if (!answer) return;
  const done = await attempt(() => api('documents/upload?' + query({address: drawerAddress, title: answer.title, product: answer.product ? 1 : ''}), {method: 'POST', body: file, headers: {'Content-Type': 'application/octet-stream'}}), 'Added');
  if (done !== undefined) reloadDrawer();
});
$('drawer').addEventListener('click', async e => {
  const a = drawerAddress;
  const doc = e.target.closest('[data-doc-pdf], [data-doc-link], [data-doc-rename], [data-doc-delete]');
  if (doc) {
    const device = deviceIndex[a] || {address: a};
    if (doc.hasAttribute('data-doc-pdf')) { $('doc-file').value = ''; $('doc-file').click(); return; }
    let done;
    if (doc.hasAttribute('data-doc-link')) {
      const answer = await ask({title: 'Add a link', text: 'A web page, or a picture on the web: a link that ends in .jpg, .png, .gif or .webp is shown as a picture.', fields: [{name: 'url', label: 'Address', placeholder: 'https://'}, {name: 'title', label: 'Name', placeholder: 'Product page'}, ...documentScope(device)], ok: 'Add'});
      if (!answer) return;
      done = await attempt(() => api('documents/link', {json: {address: a, url: answer.url, title: answer.title, product: answer.product ? 1 : ''}}), 'Added');
    } else if (doc.dataset.docRename) {
      const answer = await ask({title: 'Rename', fields: [{name: 'title', label: 'Name', value: doc.dataset.title}], ok: 'Rename'});
      if (!answer || !answer.title) return;
      done = await attempt(() => api('documents/rename', {json: {id: doc.dataset.docRename, title: answer.title}}));
    } else {
      const ok = await ask({title: `Delete ${doc.dataset.title}?`, text: 'The document is removed from the app. It cannot be brought back.', ok: 'Delete', danger: true});
      if (!ok) return;
      done = await attempt(() => api('documents/delete', {json: {id: doc.dataset.docDelete}}), 'Deleted');
    }
    if (done !== undefined) reloadDrawer();
    return;
  }
  if (e.target.closest('[data-close]')) closeDrawer();
  else if (e.target.closest('[data-chart]')) { addChart(a); showTab('charts'); }
  else if (e.target.closest('[data-archive]')) { showArchive(a); }
  else if (e.target.closest('[data-read]')) {
    const box = $('read-result'); box.innerHTML = '<p class="muted" style="margin-top:8px">Asking…</p>';
    try {
      const out = await api('read', {json: {address: a}});
      box.innerHTML = out.responses.length
        ? '<div style="margin-top:8px">' + out.responses.map(t => `<div>${valueHtml(t)} <span class="muted">answered by</span> ${ref(t.src)} <span class="muted">${esc(nameOf(t.src))}</span></div>`).join('') + '</div>'
        : '<p style="margin-top:8px"><span class="pill warning">no answer</span> <span class="muted">Nobody responded within three seconds.</span></p>';
    } catch (err) { box.innerHTML = ''; toast(err.message, true); }
  } else if (e.target.closest('[data-job]')) {
    await attempt(() => api('job', {json: {kind: e.target.closest('[data-job]').dataset.job, target: a}}));
  } else if (e.target.closest('[data-forget]')) {
    await attempt(() => api('forget', {json: {address: a}}));
  }
});
