// Nuclei Scanner dashboard. Plain scripts, one scope. Served through Home Assistant ingress.
'use strict';
const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'];
const STATUSES = ['open', 'in_progress', 'false_positive', 'wont_fix', 'resolved'];
const STATUS_LABEL = {open: 'Open', in_progress: 'In progress', false_positive: 'False positive', wont_fix: "Won't fix", resolved: 'Resolved'};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
// day/month/year spelled unambiguously (never all-numeric: "09/11/2026" reads as Sep 11 or Nov 9 depending on locale)
const fmtTime = t => t ? new Date(t * 1000).toLocaleString([], {day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false}) : '–';
let activeTab = 'dashboard';

async function api(path, options) {
  const opts = options || {};
  if (opts.body && typeof opts.body !== 'string') { opts.body = JSON.stringify(opts.body); opts.headers = {'Content-Type': 'application/json'}; }
  const r = await fetch('api/' + path, opts);
  let data = null; try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
function toast(message) {
  const el = document.createElement('div'); el.className = 'toast'; el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), 4000);
}
function sevBadge(s) { return `<span class="sev ${s}">${s}</span>`; }

// ---- theme: the same toggle as the other apps (auto follows Home Assistant, then light, then dark) ----
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = new URLSearchParams(window.location.search).get('theme');
try { themeChoice = themeChoice || localStorage.getItem('nuclei_theme'); } catch (e) { /* only a convenience */ }
// Like the other apps, it follows Home Assistant until another choice is made
if (!THEME_ICONS[themeChoice]) themeChoice = 'auto';
function haDarkMode() {
  try {
    const ha = window.parent !== window && window.parent.document.querySelector('home-assistant');
    return ha && ha.hass && ha.hass.themes ? Boolean(ha.hass.themes.darkMode) : null;
  } catch (e) {
    return null;  // Not inside Home Assistant's frontend
  }
}
function applyTheme() {
  let dark = themeChoice === 'dark';
  if (themeChoice === 'auto') {
    const ha = haDarkMode();
    dark = ha === null ? window.matchMedia('(prefers-color-scheme: dark)').matches : ha;
  }
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}
function initThemeToggle() {
  document.getElementById('theme-icon').textContent = THEME_ICONS[themeChoice];
  document.getElementById('theme-toggle').title = themeChoice === 'auto'
    ? 'Theme follows Home Assistant - click for light'
    : `Theme: ${themeChoice} - click to change`;
}
function cycleTheme() {
  themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
  try { localStorage.setItem('nuclei_theme', themeChoice); } catch (e) { /* only a convenience */ }
  initThemeToggle();
  applyTheme();
}
applyTheme();
initThemeToggle();
// Home Assistant doesn't tell the iframe when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// ---- clock ----
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
updateClock();
setInterval(updateClock, 10000);

// ---- tabs ----
document.querySelectorAll('.tab[data-tab]').forEach(tab => tab.onclick = () => {
  activeTab = tab.dataset.tab;
  document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t === tab));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + activeTab));
  loadTab();
});
function loadTab() {
  if (activeTab === 'dashboard') loadDashboard();
  else if (activeTab === 'devices') loadDevices();
  else if (activeTab === 'tickets') loadTickets();
  else if (activeTab === 'objects') loadObjects();
  else if (activeTab === 'templates') loadTemplates();
  else if (activeTab === 'settings') loadSettings();
}

// ---- dashboard ----
async function loadDashboard() {
  const s = await api('status');
  const sum = s.summary;
  const sev = sum.open_by_severity || {};
  const kpi = (l, v, cls, title) => `<div class="kpi"${title ? ` title="${esc(title)}"` : ''}><div class="l">${l}</div><div class="v" style="color:${cls || 'var(--text)'}">${v}</div></div>`;
  document.getElementById('kpis').innerHTML =
    kpi('Open tickets', sum.open_total || 0) +
    kpi('Critical', sev.critical || 0, 'var(--critical)') +
    kpi('High', sev.high || 0, 'var(--high)') +
    kpi('Medium', sev.medium || 0, 'var(--medium)') +
    kpi('Devices online', `${sum.devices_online || 0}<span class="muted" style="font-size:15px"> / ${sum.devices_total || 0}</span>`) +
    kpi('Scan objects', sum.objects || 0) +
    kpi('Last scan', sum.last_scan ? agoAgo(sum.last_scan) : '–', null, sum.last_scan ? fmtTime(sum.last_scan) : '');
  const scans = (await api('scans')).scans.slice(0, 8);
  document.getElementById('recent-scans').innerHTML = scans.length ? table(
    ['Target', 'Trigger', 'Status', 'Ports', 'Findings', 'Started'],
    scans.map(sc => [esc(sc.target), sc.trigger, scanStatus(sc), sc.open_ports, sc.findings, fmtTime(sc.started_at)])
  ) : '<div class="empty">No scans yet. Add a scan object and run one.</div>';
  const tickets = (await api('tickets?status=open')).tickets.slice(0, 8);
  document.getElementById('recent-tickets').innerHTML = tickets.length ? ticketTable(tickets) : '<div class="empty">No open tickets.</div>';
  document.querySelectorAll('#recent-tickets tbody tr').forEach(tr => tr.onclick = () => openTicket(tr.dataset.id));
}
function scanStatus(sc) {
  // The detail line (which passes ran, on how many targets, whether the credential check was on) is
  // on every pill as a title, so hovering a scan says what actually ran without reading the app log.
  const detail = esc(sc.detail || (sc.status === 'running' ? 'starting…' : ''));
  if (sc.status === 'running') return `<span class="pill in_progress" title="${detail}">running</span>`;
  if (sc.status === 'failed') return `<span class="pill" style="color:var(--critical)" title="${esc(sc.error || '')}${sc.error && detail ? ' · ' : ''}${detail}">failed</span>`;
  return `<span class="pill resolved" title="${detail}">done</span>`;
}
function table(headers, rows) {
  return `<table><thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${
    rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

// ---- devices ----
let deviceTag = '';
let deviceState = '';
const DEVICE_TAG_SUGGESTIONS = ['linux', 'windows', 'macos', 'router', 'switch', 'access-point', 'firewall', 'server', 'nas', 'camera', 'doorbell', 'nvr', 'tv', 'speaker', 'printer', 'phone', 'tablet', 'laptop', 'iot', 'smart-home-hub', 'thermostat', 'light', 'alarm', 'voip-phone', 'media-server', 'hypervisor', 'database', 'ups', 'game-console', 'guest', 'critical'];
function statusDot(d) {
  if (d.online === null || d.online === undefined) return '<span class="dot" style="background:var(--text3)" title="Not checked yet"></span>';
  return `<span class="dot ${d.online ? 'ok' : 'bad'}" title="${d.online ? 'Online' : 'Offline'}"></span>`;
}
function ago(ts) {
  if (!ts) return '';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min';
  if (s < 129600) return Math.round(s / 3600) + ' h';
  return Math.round(s / 86400) + ' d';
}
// Like ago(), but reads as a full phrase: "just now", "3 min ago", "2 d ago"
function agoAgo(ts) {
  const a = ago(ts);
  return a === 'just now' ? a : a + ' ago';
}
function statusText(d) {
  if (d.online === null || d.online === undefined) return 'not checked yet';
  return d.online ? `online for ${ago(d.status_changed_at)}` : `offline for ${ago(d.status_changed_at)}`;
}
function deviceLabel(d) { return d.name || d.guessed_type || d.host; }
function serviceChips(d) {
  return d.services.length ? d.services.map(s =>
    `<span class="pill" ${s.risky ? 'style="color:var(--high);border:1px solid var(--high)" title="Shouldn\'t normally be exposed"' : ''}>${esc(s.service)}:${s.port}</span>`).join(' ') : '<span class="muted">none open</span>';
}
async function loadDevices() {
  const d = await api('devices');
  const tags = [...new Set(d.devices.flatMap(x => x.tags))].sort();
  document.getElementById('device-filters').innerHTML = ['', ...tags].map(t =>
    `<button class="chip ${t === deviceTag ? 'active' : ''}" data-t="${esc(t)}">${t ? esc(t) : 'All'}</button>`).join('');
  const states = [['', 'Any'], ['online', 'Online'], ['offline', 'Offline']];
  document.getElementById('device-filters').innerHTML += '<span class="spacer"></span>' + states.map(([k, l]) =>
    `<button class="chip ${k === deviceState ? 'active' : ''}" data-s="${k}">${l}</button>`).join('');
  document.querySelectorAll('#device-filters .chip[data-t]').forEach(c => c.onclick = () => { deviceTag = c.dataset.t; loadDevices(); });
  document.querySelectorAll('#device-filters .chip[data-s]').forEach(c => c.onclick = () => { deviceState = c.dataset.s; loadDevices(); });
  const shown = d.devices.filter(x => (!deviceTag || x.tags.includes(deviceTag)) &&
    (!deviceState || (deviceState === 'online' ? x.online === 1 : x.online === 0)));
  document.getElementById('devices-table').innerHTML = shown.length ?
    `<table><thead><tr><th>Status</th><th>Device</th><th>Address</th><th>Services</th><th>Tags</th><th>Open tickets</th><th>Last seen</th></tr></thead><tbody>${shown.map(x =>
      `<tr data-id="${x.id}"><td>${statusDot(x)} <span class="muted">${x.online === 1 ? 'online' : x.online === 0 ? 'offline' : '–'}</span></td><td>${esc(deviceLabel(x))}${!x.name && x.guessed_type ? ' <span class="muted">(guess)</span>' : ''}</td><td class="mono">${esc(x.host)}</td><td>${serviceChips(x)}</td><td>${x.tags.map(t => `<span class="pill">${esc(t)}</span>`).join(' ')}</td><td>${x.open_tickets || 0}</td><td>${fmtTime(x.last_seen)}</td></tr>`).join('')}</tbody></table>` :
    '<div class="empty">No devices yet. They appear once a scan finds open ports.</div>';
  document.querySelectorAll('#devices-table tbody tr').forEach(tr => tr.onclick = () => editDevice(Number(tr.dataset.id), d.devices.find(x => x.id === Number(tr.dataset.id))));
}
function editDevice(id, d) {
  modal(`
    <h3>${esc(deviceLabel(d))}</h3>
    <div class="mono muted">${esc(d.host)}${d.guessed_type ? ' · guess: ' + esc(d.guessed_type) : ''}</div>
    <div style="margin-top:6px">${statusDot(d)} ${esc(statusText(d))}${d.last_online && !d.online ? ' · last seen online ' + fmtTime(d.last_online) : ''}</div>
    <label>Services</label><div>${serviceChips(d)}</div>
    ${d.signals.length ? `<label>Detected</label><div class="muted">${d.signals.map(esc).join(', ')}</div>` : ''}
    <label>Name</label><input id="d-name" value="${esc(d.name || '')}" placeholder="${esc(d.guessed_type || d.host)}" style="width:100%">
    <label>Description</label><textarea id="d-desc" class="note-field" placeholder="What is it, where is it, who owns it">${esc(d.description || '')}</textarea>
    <label>Tags (comma separated)</label><input id="d-tags" value="${esc(d.tags.join(', '))}" style="width:100%">
    <div class="filters" style="margin-top:6px;max-height:110px;overflow:auto">${DEVICE_TAG_SUGGESTIONS.map(t => `<button class="chip" data-add="${t}">+ ${t}</button>`).join('')}</div>
    <div class="row" style="margin-top:14px"><button class="btn btn-primary" id="d-save">Save</button><button class="btn" id="d-rescan" ${d.object_id ? 'title="Runs a new scan of the scan object this device belongs to"' : 'disabled title="The scan object this device came from was removed"'}>Rescan</button><button class="btn btn-danger" id="d-del">Remove</button><button class="btn" data-close>Close</button></div>
  `);
  document.querySelectorAll('[data-add]').forEach(b => b.onclick = () => {
    const input = document.getElementById('d-tags');
    const list = input.value.split(',').map(s => s.trim()).filter(Boolean);
    if (!list.includes(b.dataset.add)) list.push(b.dataset.add);
    input.value = list.join(', ');
  });
  document.getElementById('d-save').onclick = async () => {
    try {
      await api('devices/' + id, {method: 'PATCH', body: {name: document.getElementById('d-name').value, description: document.getElementById('d-desc').value, tags: document.getElementById('d-tags').value}});
      closeModal(); loadDevices();
    } catch (e) { toast(e.message); }
  };
  document.getElementById('d-rescan').onclick = async () => {
    try {
      await api(`objects/${d.object_id}/scan`, {method: 'POST'});
      toast('Scan started');
      closeModal();
    } catch (e) { toast(e.message); }
  };
  document.getElementById('d-del').onclick = async () => {
    if (!confirm('Remove this device? A later scan will add it again.')) return;
    await api('devices/' + id, {method: 'DELETE'}); closeModal(); loadDevices();
  };
}

// ---- tickets ----
let ticketFilter = 'open';
function loadTickets() {
  const filters = ['open', ...STATUSES.filter(s => s !== 'open' && s !== 'in_progress'), 'in_progress'];
  document.getElementById('ticket-filters').innerHTML = filters.map(f =>
    `<button class="chip ${f === ticketFilter ? 'active' : ''}" data-f="${f}">${STATUS_LABEL[f] || f}</button>`).join('');
  document.querySelectorAll('#ticket-filters .chip').forEach(c => c.onclick = () => { ticketFilter = c.dataset.f; loadTickets(); });
  api('tickets?status=' + ticketFilter).then(d => {
    document.getElementById('tickets-table').innerHTML = d.tickets.length ? ticketTable(d.tickets) : '<div class="empty">No tickets here.</div>';
    document.querySelectorAll('#tickets-table tbody tr').forEach(tr => tr.onclick = () => openTicket(tr.dataset.id));
  });
}
// A named device shows its name, with the address underneath in small print; otherwise just the address
function hostLabel(t) {
  const addr = esc(t.host) + (t.port ? ':' + t.port : '');
  return t.device_name ? `${esc(t.device_name)}<div class="muted mono" style="font-size:11px">${addr}</div>` : `<span class="mono">${addr}</span>`;
}
function ticketTable(tickets) {
  const rows = tickets.map(t => `<tr data-id="${t.id}"><td>${sevBadge(t.severity)}</td><td>${esc(t.name)}${t.cve ? ` <span class="pill">${esc(t.cve)}</span>` : ''}</td><td>${hostLabel(t)}</td><td><span class="pill ${t.status}">${STATUS_LABEL[t.status] || t.status}</span></td><td>${t.cvss ?? '–'}</td><td>${fmtTime(t.last_seen)}</td></tr>`).join('');
  return `<table><thead><tr><th>Sev</th><th>Issue</th><th>Host</th><th>Status</th><th>CVSS</th><th>Last seen</th></tr></thead><tbody>${rows}</tbody></table>`;
}
async function openTicket(id) {
  const t = await api('tickets/' + id);
  const field = (l, v) => v ? `<label>${l}</label><div class="mono" style="white-space:pre-wrap;word-break:break-word">${esc(v)}</div>` : '';
  modal(`
    <h3>${sevBadge(t.severity)} ${esc(t.name)}</h3>
    <div class="row" style="margin-bottom:8px">${t.cve ? `<span class="pill">${esc(t.cve)}</span>` : ''}${t.cvss ? `<span class="pill">CVSS ${t.cvss}</span>` : ''}<span>${t.device_name ? `${esc(t.device_name)} · ` : ''}<span class="mono">${esc(t.host)}${t.port ? ':' + t.port : ''}</span></span></div>
    <label>Status</label>
    <select id="t-status">${STATUSES.map(s => `<option value="${s}" ${s === t.status ? 'selected' : ''}>${STATUS_LABEL[s]}</option>`).join('')}</select>
    ${field('Where', t.matched_at)}
    ${field('Proof', t.proof)}
    ${field('Description', t.description)}
    ${field('Remediation', t.remediation)}
    ${field('Reference', t.reference)}
    <label>Note</label><textarea id="t-note" class="note-field">${esc(t.note || '')}</textarea>
    <div class="muted" style="margin-top:6px">Seen ${t.times_seen}× · first ${fmtTime(t.first_seen)} · last ${fmtTime(t.last_seen)}</div>
    <div class="row" style="margin-top:14px"><button class="btn btn-primary" id="t-save">Save</button><button class="btn" data-close>Close</button></div>
  `);
  document.getElementById('t-save').onclick = async () => {
    try {
      await api('tickets/' + id, {method: 'PATCH', body: {status: document.getElementById('t-status').value, note: document.getElementById('t-note').value}});
      closeModal(); loadTab();
    } catch (e) { toast(e.message); }
  };
}

// ---- objects ----
async function loadObjects() {
  const d = await api('objects');
  const rows = d.objects.map(o => `<tr><td class="mono">${esc(o.target)}</td><td>${esc(o.name || '')}</td><td>${o.kind}</td><td>${o.authorised ? '<span class="dot ok"></span> yes' : '<span class="dot bad"></span> no'}</td><td>${o.schedule || '–'}</td><td>${o.last_scan_at ? fmtTime(o.last_scan_at) : 'never'}</td><td><div class="row"><button class="btn btn-small" data-scan="${o.id}">Scan</button><button class="btn btn-small btn-danger" data-del="${o.id}">×</button></div></td></tr>`).join('');
  document.getElementById('objects-table').innerHTML = d.objects.length ?
    `<table><thead><tr><th>Target</th><th>Name</th><th>Kind</th><th>Authorised</th><th>Schedule</th><th>Last scan</th><th></th></tr></thead><tbody>${rows}</tbody></table>` :
    '<div class="empty">No scan objects yet.</div>';
  document.querySelectorAll('[data-scan]').forEach(b => b.onclick = async () => {
    try { await api(`objects/${b.dataset.scan}/scan`, {method: 'POST'}); toast('Scan started'); } catch (e) { toast(e.message); }
  });
  document.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Remove this scan object?')) return;
    await api('objects/' + b.dataset.del, {method: 'DELETE'}); loadObjects();
  });
}
document.getElementById('obj-preview').onclick = async () => {
  const target = document.getElementById('obj-target').value.trim();
  if (!target) return;
  let p;
  try { p = await api('objects/preview', {method: 'POST', body: {target}}); } catch (e) { return toast(e.message); }
  const name = document.getElementById('obj-name').value.trim();
  const schedule = document.getElementById('obj-schedule').value;
  const hostList = p.hosts ? `<label>${p.host_count} host${p.host_count === 1 ? '' : 's'}</label><div class="mono" style="max-height:160px;overflow:auto">${p.hosts.map(esc).join('<br>')}${p.host_count > p.hosts.length ? '<br>…' : ''}</div>` : '';
  modal(`
    <h3>Review target</h3>
    <div class="mono" style="margin-bottom:8px">${esc(p.target)} <span class="pill">${p.kind}</span>${p.private ? '<span class="pill">private</span>' : ''}</div>
    ${hostList}
    ${p.private ? '<p class="muted">A private range, treated as yours.</p>' :
      '<p class="muted">This is not a private range. Only continue if you are authorised to scan it.</p>'}
    <label style="display:flex;gap:8px;align-items:center;margin-top:10px"><input type="checkbox" id="obj-auth" style="width:auto"> I am authorised to scan this target</label>
    <div class="row" style="margin-top:14px"><button class="btn btn-primary" id="obj-add">Add object</button><button class="btn" data-close>Cancel</button></div>
  `);
  document.getElementById('obj-add').onclick = async () => {
    if (!document.getElementById('obj-auth').checked) return toast('Confirm you are authorised first');
    try {
      await api('objects', {method: 'POST', body: {target: p.target, name, schedule, authorised: true}});
      closeModal(); document.getElementById('obj-target').value = ''; document.getElementById('obj-name').value = ''; loadObjects();
    } catch (e) { toast(e.message); }
  };
};

// ---- templates ----
async function loadTemplates() {
  const d = await api('templates');
  const kpi = (l, v) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`;
  document.getElementById('template-kpis').innerHTML =
    kpi('Community templates', d.community_count || 0) +
    kpi('Last updated', d.community_updated_at ? ago(d.community_updated_at) + ' ago' : 'never') +
    kpi('Your templates', d.templates.length);
  document.getElementById('templates-table').innerHTML = d.templates.length ?
    `<table><tbody>${d.templates.map(t => `<tr><td class="mono">${esc(t.name)}</td><td>${t.size} bytes</td><td><div class="row"><button class="btn btn-small" data-edit="${esc(t.name)}">Edit</button><button class="btn btn-small btn-danger" data-delt="${esc(t.name)}">×</button></div></td></tr>`).join('')}</tbody></table>` :
    '<div class="empty">No custom templates yet.</div>';
  document.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => editTemplate(b.dataset.edit));
  document.querySelectorAll('[data-delt]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this template?')) return;
    await api('templates/' + encodeURIComponent(b.dataset.delt), {method: 'DELETE'}); loadTemplates();
  });
}
document.getElementById('tpl-new').onclick = () => editTemplate(null);
async function editTemplate(name) {
  let body = 'id: my-check\n\ninfo:\n  name: My check\n  author: me\n  severity: info\n\nhttp:\n  - method: GET\n    path:\n      - "{{BaseURL}}"\n    matchers:\n      - type: word\n        words:\n          - "example"\n';
  if (name) { const t = await api('templates/' + encodeURIComponent(name)); body = t.body; }
  modal(`
    <h3>${name ? 'Edit' : 'New'} template</h3>
    <label>Name (file name, lower-case)</label>
    <input id="tpl-name" value="${esc(name || '')}" ${name ? 'readonly' : ''} placeholder="my-check" style="width:100%">
    <label>Template (YAML)</label>
    <textarea id="tpl-body">${esc(body)}</textarea>
    <div class="row" style="margin-top:12px"><button class="btn btn-primary" id="tpl-save">Validate &amp; save</button><button class="btn" data-close>Cancel</button></div>
  `);
  document.getElementById('tpl-save').onclick = async () => {
    const n = document.getElementById('tpl-name').value.trim();
    if (!n) return toast('Give it a name');
    try {
      await api('templates/' + encodeURIComponent(n), {method: 'PUT', body: {body: document.getElementById('tpl-body').value}});
      closeModal(); loadTemplates(); toast('Saved');
    } catch (e) { toast(e.message); }
  };
}

// ---- settings ----
async function loadSettings() {
  const s = await api('status');
  const c = s.config;
  const rows = [
    ['Minimum severity', c.min_severity], ['Requests per second', c.requests_per_second],
    ['Concurrent hosts', c.max_concurrent_hosts], ['Default-credential check', c.default_credentials ? 'on' : 'off'],
    ['Update templates', c.update_templates ? 'on' : 'off'], ['Online check', c.presence_interval_minutes ? 'every ' + c.presence_interval_minutes + ' min' : 'off'], ['Keep data', c.retention_days + ' days'],
    ['Exclude tags', c.nuclei_exclude_tags || '–'],
  ];
  document.getElementById('settings-options').innerHTML =
    `<table><tbody>${rows.map(([l, v]) => `<tr><td class="muted">${l}</td><td class="mono">${esc(v)}</td></tr>`).join('')}</tbody></table>
     <p class="muted" style="margin-top:8px">Change these on the app's Configuration tab in Home Assistant.</p>`;
  const db = await api('database');
  document.getElementById('settings-db').innerHTML = `<table><tbody>
    <tr><td class="muted">On disk</td><td class="mono">${(db.size / 1048576).toFixed(2)} MB</td></tr>
    <tr><td class="muted">Scan objects</td><td class="mono">${db.counts.scan_objects}</td></tr>
    <tr><td class="muted">Scans</td><td class="mono">${db.counts.scans}</td></tr>
    <tr><td class="muted">Devices</td><td class="mono">${db.counts.devices}</td></tr>
    <tr><td class="muted">Tickets</td><td class="mono">${db.counts.findings}</td></tr></tbody></table>`;
}
document.getElementById('wipe-btn').onclick = () => {
  modal(`<h3>Delete all data</h3><p class="muted">This removes every scan object, scan and ticket. Type <b>delete</b> to confirm.</p>
    <input id="wipe-confirm" placeholder="delete" style="width:100%">
    <div class="row" style="margin-top:14px"><button class="btn btn-danger" id="wipe-go">Delete everything</button><button class="btn" data-close>Cancel</button></div>`);
  document.getElementById('wipe-go').onclick = async () => {
    try { await api('wipe', {method: 'POST', body: {confirm: document.getElementById('wipe-confirm').value}}); closeModal(); toast('Database emptied'); loadTab(); }
    catch (e) { toast(e.message); }
  };
};

// ---- modal ----
function modal(html) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="overlay"><div class="modal">${html}</div></div>`;
  root.querySelector('.overlay').onclick = e => { if (e.target.classList.contains('overlay')) closeModal(); };
  root.querySelectorAll('[data-close]').forEach(b => b.onclick = closeModal);
}
function closeModal() { document.getElementById('modal-root').innerHTML = ''; }

// ---- status bar + startup ----
async function refreshStatus() {
  try {
    const s = await api('status');
    const sc = s.scanners;
    document.getElementById('statusbar').innerHTML =
      `<span><span class="dot ${sc.nuclei && sc.naabu ? 'ok' : 'bad'}"></span> scanners ${sc.nuclei && sc.naabu ? 'ready' : 'missing'}</span>` +
      `<span>${s.running.length ? s.running.length + ' scan(s) running' : 'idle'}</span>` +
      `<span class="spacer"></span><span class="muted">Nuclei Scanner ${esc(s.version)} · internal use</span>`;
  } catch (e) {
    document.getElementById('statusbar').innerHTML = '<span><span class="dot bad"></span> cannot reach the app</span>';
  }
}
loadDashboard().catch(e => toast(e.message));
refreshStatus();
setInterval(() => { refreshStatus(); if (activeTab === 'dashboard' || activeTab === 'objects') loadTab(); }, 5000);
