// Nuclei Scanner dashboard. Plain scripts, one scope. Served through Home Assistant ingress.
'use strict';
const SEVS = ['critical', 'high', 'medium', 'low'];
const SEV_COLOR = s => `var(--${s})`;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
// day/month/year spelled unambiguously (never all-numeric: "09/11/2026" reads as Sep 11 or Nov 9 depending on locale)
const fmtTime = t => t ? new Date(t * 1000).toLocaleString([], {day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false}) : '–';
const fmtDay = d => new Date(d + 'T12:00:00').toLocaleDateString([], {day: 'numeric', month: 'short'});
let activeTab = 'dashboard';
let overview = null;

async function api(path, options) {
  const opts = options || {};
  if (opts.body && typeof opts.body !== 'string') { opts.body = JSON.stringify(opts.body); opts.headers = {'Content-Type': 'application/json'}; }
  const r = await fetch('api/' + path, opts);
  let data = null; try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
function toast(message, error) {
  const el = document.createElement('div'); el.className = 'toast' + (error ? ' err' : ''); el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), 4000);
}
const sevBadge = s => `<span class="sev c-${s}">${s}</span>`;
function ago(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + ' min ago';
  if (s < 129600) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}
function inTime(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return 'due';
  if (s < 5400) return 'in ' + Math.round(s / 60) + ' min';
  if (s < 129600) return 'in ' + Math.round(s / 3600) + ' h';
  return 'in ' + Math.round(s / 86400) + ' d';
}
const gradeColor = g => ({A: 'var(--ok)', B: '#5aa833', C: 'var(--medium)', D: 'var(--high)', F: 'var(--critical)'}[g] || 'var(--text3)');
const scoreGrade = v => v >= 90 ? 'A' : v >= 80 ? 'B' : v >= 65 ? 'C' : v >= 50 ? 'D' : 'F';
const VERDICT = {A: 'Looking good', B: 'Mostly fine', C: 'Some work to do', D: 'Needs work', F: 'Exposed'};

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
function showTab(name) {
  activeTab = name;
  history.replaceState(null, '', name === 'dashboard' ? location.pathname + location.search : '#' + name);
  document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
  loadTab();
}
document.querySelectorAll('.tab[data-tab]').forEach(tab => tab.onclick = () => showTab(tab.dataset.tab));
document.querySelectorAll('[data-goto]').forEach(b => b.onclick = () => showTab(b.dataset.goto));
function loadTab() {
  const run = {dashboard: loadDashboard, issues: loadIssues, devices: loadDevices, scanner: loadScanner, settings: loadSettings}[activeTab];
  return run().catch(e => toast(e.message, true));
}

// The database file and its write-ahead log together: new rows sit in the log first
function fmtBytes(n) { return n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} kB` : `${(n / 1048576).toFixed(1)} MB`; }

// ---- tooltip ----
const tip = document.getElementById('tooltip');
function showTip(html, x, y) {
  tip.innerHTML = html; tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(window.innerWidth - w - 8, x + 14) + 'px';
  tip.style.top = Math.max(8, y - h - 10) + 'px';
}
function hideTip() { tip.hidden = true; }

// ---- charts (inline SVG, one axis each) ----
function chartFrame(el) {
  const w = Math.max(280, el.clientWidth), h = el.clientHeight || 190;
  return {w, h, l: 34, r: 8, t: 8, b: 22};
}
function xLabels(days, f, x) {
  const step = Math.max(1, Math.ceil(days.length / 6));
  return days.map((d, i) => (i % step === 0 || i === days.length - 1) && (i === days.length - 1 || days.length - 1 - i >= step / 2)
    ? `<text x="${x(i)}" y="${f.h - 6}" text-anchor="middle">${fmtDay(d.day)}</text>` : '').join('');
}
function scoreChart(el, days) {
  if (days.length < 2) { el.innerHTML = '<div class="empty small">The trend shows up after the first days of scanning.</div>'; return; }
  const f = chartFrame(el);
  const x = i => f.l + (f.w - f.l - f.r) * i / (days.length - 1);
  const y = v => f.t + (f.h - f.t - f.b) * (1 - v / 100);
  const pts = days.map((d, i) => `${x(i).toFixed(1)},${y(d.score).toFixed(1)}`);
  const last = days[days.length - 1];
  const color = gradeColor(scoreGrade(last.score));
  el.innerHTML = `<svg viewBox="0 0 ${f.w} ${f.h}" preserveAspectRatio="none">
    <defs><linearGradient id="sg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".18"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <g class="grid">${[0, 50, 100].map(v => `<line x1="${f.l}" x2="${f.w - f.r}" y1="${y(v)}" y2="${y(v)}"/>`).join('')}</g>
    <g class="axis">${[0, 50, 100].map(v => `<text x="${f.l - 6}" y="${y(v) + 3}" text-anchor="end">${v}</text>`).join('')}${xLabels(days, f, x)}</g>
    <path d="M${pts.join(' L')} L${x(days.length - 1)},${y(0)} L${x(0)},${y(0)} Z" fill="url(#sg)"/>
    <polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="${x(days.length - 1)}" cy="${y(last.score)}" r="4" fill="${color}" stroke="var(--card)" stroke-width="2"/>
    <line class="cross" id="sx" y1="${f.t}" y2="${f.h - f.b}" visibility="hidden"/>
    <rect class="hit" x="${f.l}" y="0" width="${f.w - f.l - f.r}" height="${f.h}"/></svg>`;
  const svg = el.querySelector('svg'), cross = el.querySelector('#sx');
  svg.querySelector('.hit').onmousemove = e => {
    const r = svg.getBoundingClientRect();
    const px = (e.clientX - r.left) * f.w / r.width;
    const i = Math.max(0, Math.min(days.length - 1, Math.round((px - f.l) / (f.w - f.l - f.r) * (days.length - 1))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    const d = days[i];
    showTip(`<div class="t">${fmtDay(d.day)}</div><div class="r"><span>Score</span><b>${d.score} (${scoreGrade(d.score)})</b></div><div class="r"><span>Devices scanned</span><b>${d.devices}</b></div><div class="r"><span>Scans that day</span><b>${d.scans}</b></div>`, e.clientX, e.clientY);
  };
  svg.querySelector('.hit').onmouseleave = () => { cross.setAttribute('visibility', 'hidden'); hideTip(); };
}
function issuesChart(el, days) {
  // The legend sits below the chart, not in it: the chart has a fixed height that the drawing fills
  const legend = document.getElementById('chart-issues-legend');
  legend.innerHTML = days.length ? SEVS.map(s => `<span><i style="background:${SEV_COLOR(s)}"></i>${s}</span>`).join('') : '';
  if (!days.length) { el.innerHTML = '<div class="empty small">No history yet.</div>'; return; }
  const f = chartFrame(el);
  const totals = days.map(d => SEVS.reduce((a, s) => a + d[s], 0));
  const max = Math.max(4, ...totals);
  const nice = max <= 8 ? Math.ceil(max / 4) * 4 : Math.ceil(max / 10) * 10;
  const band = (f.w - f.l - f.r) / days.length;
  const bw = Math.max(3, Math.min(22, band - 3));
  const x = i => f.l + band * i + band / 2;
  const y = v => f.t + (f.h - f.t - f.b) * (1 - v / nice);
  let bars = '';
  days.forEach((d, i) => {
    let base = 0;
    // stacked from the bottom: low first, critical on top; 2px surface gap between segments
    [...SEVS].reverse().forEach((s, si, arr) => {
      if (!d[s]) return;
      const y0 = y(base), y1 = y(base + d[s]);
      base += d[s];
      const isTop = !arr.slice(si + 1).some(t => d[t]);
      const hgt = Math.max(1, y0 - y1 - (base === d[s] ? 0 : 2));
      bars += `<rect x="${x(i) - bw / 2}" y="${y1}" width="${bw}" height="${hgt}" fill="${SEV_COLOR(s)}" rx="${isTop ? 3 : 0}"/>`;
    });
  });
  const ticks = [0, nice / 2, nice];
  el.innerHTML = `<svg viewBox="0 0 ${f.w} ${f.h}" preserveAspectRatio="none">
    <g class="grid">${ticks.map(v => `<line x1="${f.l}" x2="${f.w - f.r}" y1="${y(v)}" y2="${y(v)}"/>`).join('')}</g>
    <g class="axis">${ticks.map(v => `<text x="${f.l - 6}" y="${y(v) + 3}" text-anchor="end">${v}</text>`).join('')}${xLabels(days, f, x)}</g>
    ${bars}
    ${days.map((d, i) => `<rect class="hit" data-i="${i}" x="${f.l + band * i}" y="0" width="${band}" height="${f.h - f.b}"/>`).join('')}</svg>`;
  el.querySelectorAll('.hit').forEach(r => {
    r.onmousemove = e => {
      const d = days[Number(r.dataset.i)];
      showTip(`<div class="t">${fmtDay(d.day)}</div>${SEVS.map(s => `<div class="r"><span><span class="sev c-${s}" style="background:none;padding:0">${s}</span></span><b>${d[s]}</b></div>`).join('')}`, e.clientX, e.clientY);
    };
    r.onmouseleave = hideTip;
  });
}

// ---- dashboard ----
function gauge(value, grade) {
  const r = 54, c = 2 * Math.PI * r, part = value == null ? 0 : value / 100;
  return `<div class="gauge"><svg viewBox="0 0 132 132"><circle cx="66" cy="66" r="${r}" fill="none" stroke="var(--grid)" stroke-width="11"/>
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="${gradeColor(grade)}" stroke-width="11" stroke-linecap="round" stroke-dasharray="${(c * part).toFixed(1)} ${c.toFixed(1)}"/></svg>
    <div class="num"><b>${value ?? '–'}</b><span>of 100</span></div></div>`;
}
async function loadDashboard() {
  const o = overview = await api('overview');
  const s = o.summary;
  const ha = o.home_assistant;
  document.getElementById('ha-banner').innerHTML = ha.error ? `<div class="banner"><b>Can't read Home Assistant's device trackers:</b> ${esc(ha.error)}. Devices already known are still shown; nothing new is scanned until this works.</div>` : '';

  const hist = o.history;
  const weekAgo = hist.length > 7 ? hist[hist.length - 8].score : (hist[0] ? hist[0].score : null);
  const delta = weekAgo == null ? 0 : s.score - weekAgo;
  const noScans = !s.last_scan;
  document.getElementById('score-card').innerHTML = gauge(noScans ? null : s.score, s.grade) + `
    <div class="score-meta"><div class="label">Security score</div>
      <div class="verdict">${noScans ? 'No scans yet' : `<span class="grade" style="background:${gradeColor(s.grade)}">${s.grade}</span> ${VERDICT[s.grade]}`}</div>
      <div class="small muted">${delta ? `<span class="delta ${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '▲' : '▼'} ${Math.abs(delta)}</span> in 7 days` : 'unchanged in 7 days'}</div>
      <div class="small muted" style="margin-top:6px">${s.new_this_week} new · ${s.resolved_this_week} resolved this week</div></div>`;

  document.getElementById('sev-tiles').innerHTML = SEVS.map(sev => {
    const total = s.unresolved[sev], open = s.needs_attention[sev];
    return `<div class="sev-tile c-${sev} ${total ? '' : 'zero'}" data-sev="${sev}" title="Show these issues">
      <div class="l">${sev}</div>
      <div class="v">${total}</div>
      <div><div class="s">${total ? (open ? `<b>${open}</b> need${open === 1 ? 's' : ''} attention` : 'all acknowledged') : 'none'}</div>
      <div class="m" style="margin-top:8px" title="${open} of ${total} not acknowledged yet"><i style="width:${total ? Math.round(100 * open / total) : 0}%"></i></div></div></div>`;
  }).join('');
  const allTotal = SEVS.reduce((n, sev) => n + s.unresolved[sev], 0), allOpen = SEVS.reduce((n, sev) => n + s.needs_attention[sev], 0);
  document.getElementById('sev-total').innerHTML = allTotal ? `<b style="color:var(--text)">${allOpen}</b> of ${allTotal} need attention` : 'nothing found';
  document.querySelectorAll('.sev-tile').forEach(t => t.onclick = () => { issueState = 'unresolved'; issueSev = t.dataset.sev; showTab('issues'); });

  const d = s.devices;
  const pct = d.scannable ? Math.round(100 * d.covered / d.scannable) : 0;
  document.getElementById('coverage-card').innerHTML = `<div class="card-head"><h2>Coverage</h2><span class="cov-pct">${pct}%</span></div>
    <div><span class="big">${d.covered}</span><span class="cov-of">of ${d.scannable} devices scanned in ${o.config.rescan_days} days</span></div>
    <div class="bar" title="${pct}% covered"><i style="width:${pct}%;background:var(--primary)"></i></div>
    <dl class="facts two"><dt>Home now</dt><dd>${d.online}/${d.tracked}</dd><dt>Scans this week</dt><dd>${s.scans_this_week}</dd>
    <dt>Never scanned</dt><dd>${d.never_scanned}</dd><dt>Excluded</dt><dd>${d.excluded}</dd></dl>`;

  scoreChart(document.getElementById('chart-score'), hist);
  issuesChart(document.getElementById('chart-issues'), hist);

  const attention = (await api('issues?state=open')).issues;
  const el = document.getElementById('attention');
  el.innerHTML = attention.length ? attention.slice(0, 7).map(i => `<div class="issue-row" data-id="${i.id}">
      ${sevBadge(i.severity)}<div style="min-width:0"><div class="name">${esc(i.name)}</div><div class="sub">${esc(i.device_name)}${i.port ? ' · port ' + i.port : ''}${i.cve ? ' · ' + esc(i.cve.split(',')[0]) : ''} · found ${ago(i.first_seen)}</div></div>
      <button class="btn btn-small" data-ack="${i.id}">Acknowledge</button></div>`).join('') +
      (attention.length > 7 ? `<div class="small muted" style="padding:8px 4px">and ${attention.length - 7} more</div>` : '') :
    `<div class="empty"><span class="ok-icon">✓</span>Nothing needs your attention.${s.acknowledged ? `<div class="small">${s.acknowledged} acknowledged issue${s.acknowledged === 1 ? ' is' : 's are'} still there.</div>` : ''}</div>`;
  const attIds = attention.slice(0, 7).map(x => x.id);
  el.querySelectorAll('.issue-row').forEach(r => r.onclick = e => { if (!e.target.dataset.ack) openIssue(r.dataset.id, {ids: attIds}); });
  el.querySelectorAll('[data-ack]').forEach(b => b.onclick = () => setIssue(b.dataset.ack, 'acknowledged'));

  renderActivity(o);
}
const PHASES = [['templates', 'Templates'], ['ports', 'Ports'], ['vulnerabilities', 'Vulnerabilities']];
function renderActivity(o) {
  const run = o.running;
  const queue = o.queue.filter(q => !run || q.id !== run.device_id);
  let html = '<h2>Scanning</h2>';
  if (run) {
    const at = PHASES.findIndex(p => p[0] === run.phase);
    html += `<div class="now"><span class="pulse"></span><div style="min-width:0"><b>${esc(run.device_name)}</b> <span class="mono muted small">${esc(run.ip)}</span>
      <div class="small muted">${esc(run.reason)} · started ${ago(run.started_at)} · ${run.open_ports} open port${run.open_ports === 1 ? '' : 's'} · ${run.findings} issue${run.findings === 1 ? '' : 's'} so far</div></div></div>
      <div class="steps">${PHASES.map(([k, l], i) => `<span class="${i < at ? 'done' : i === at ? 'active' : ''}">${i < at ? '✓ ' : ''}${l}</span>`).join('')}</div>`;
  } else {
    const last = o.summary.last_scan;
    html += `<div class="now"><span class="pulse idle"></span><div><b>Idle</b><div class="small muted">${last ? 'last scan ' + ago(last) : 'waiting for devices to come home'}</div></div></div>`;
  }
  if (queue.length) html += `<div class="queue">Next: ${queue.slice(0, 4).map(q => `${esc(q.name)} <span class="muted">(${esc(q.reason)})</span>`).join(', ')}${queue.length > 4 ? ` and ${queue.length - 4} more` : ''}</div>`;
  document.getElementById('activity-card').innerHTML = html;
}
const COMPONENT = {nuclei: 'nuclei engine', naabu: 'naabu port scanner', templates: 'Community templates'};
function freshState(c) {
  if (!c.installed) return '<span class="state bad">not found</span>';
  if (c.outdated) return `<span class="state warn">${esc(c.latest)} available</span>`;
  return c.latest ? '<span class="state good">latest</span>' : '<span class="state">latest unknown</span>';
}

// ---- issues ----
let issueState = 'open';
let issueSev = '';
let issueList = [];
const STATE_LABEL = {open: 'Needs attention', acknowledged: 'Acknowledged', resolved: 'Resolved', unresolved: 'All still there'};
async function loadIssues() {
  document.getElementById('issue-states').innerHTML = Object.entries(STATE_LABEL).map(([k, l]) =>
    `<button class="${k === issueState ? 'active' : ''}" data-s="${k}">${l}</button>`).join('');
  document.getElementById('issue-sevs').innerHTML = ['', ...SEVS].map(s =>
    `<button class="${s === issueSev ? 'active' : ''}" data-v="${s}">${s || 'Any severity'}</button>`).join('');
  document.querySelectorAll('#issue-states button').forEach(b => b.onclick = () => { issueState = b.dataset.s; loadIssues(); });
  document.querySelectorAll('#issue-sevs button').forEach(b => b.onclick = () => { issueSev = b.dataset.v; loadIssues(); });
  issueList = (await api('issues?state=' + issueState)).issues;
  renderIssues();
}
function renderIssues() {
  const q = document.getElementById('issue-search').value.trim().toLowerCase();
  const shown = issueList.filter(i => (!issueSev || i.severity === issueSev) &&
    (!q || [i.name, i.device_name, i.device_ip, i.cve, i.template_id].some(v => (v || '').toLowerCase().includes(q))));
  document.getElementById('ack-shown').hidden = issueState !== 'open' || !shown.length;
  document.getElementById('issues-table').innerHTML = shown.length ?
    `<table><thead><tr><th>Severity</th><th>Issue</th><th>Device</th><th class="hide-sm">Port</th><th class="hide-sm">CVE</th><th class="hide-sm">${issueState === 'resolved' ? 'Resolved' : 'Found'}</th>${issueState === 'unresolved' ? '<th>State</th>' : ''}</tr></thead><tbody>${shown.map(i =>
      `<tr class="click" data-id="${i.id}"><td>${sevBadge(i.severity)}</td><td>${esc(i.name)}</td><td>${esc(i.device_name)} <span class="mono muted small">${esc(i.device_ip || '')}</span></td>
       <td class="mono hide-sm">${i.port || '–'}</td><td class="mono small hide-sm">${esc((i.cve || '').split(',')[0]) || '–'}</td><td class="small muted hide-sm">${ago(issueState === 'resolved' ? i.resolved_at : i.first_seen)}</td>
       ${issueState === 'unresolved' ? `<td><span class="pill ${i.state === 'open' ? 'open' : 'ack'}">${i.state === 'open' ? 'needs attention' : 'acknowledged'}</span></td>` : ''}</tr>`).join('')}</tbody></table>` :
    `<div class="empty">${issueState === 'open' ? '<span class="ok-icon">✓</span>Nothing needs your attention.' : 'No issues here.'}</div>`;
  const shownIds = shown.map(i => i.id);
  document.querySelectorAll('#issues-table tbody tr').forEach(tr => tr.onclick = () => openIssue(tr.dataset.id, {ids: shownIds}));
  document.getElementById('ack-shown').onclick = async () => {
    if (!confirm(`Acknowledge ${shown.length} issue${shown.length === 1 ? '' : 's'}?`)) return;
    for (const i of shown) await api('issues/' + i.id, {method: 'PATCH', body: {state: 'acknowledged'}});
    toast(`${shown.length} acknowledged`); loadIssues();
  };
}
document.getElementById('issue-search').oninput = renderIssues;
// The list an issue was opened from, so the detail can step through it (prev/next) and go back.
let issueNav = null;
async function setIssue(id, state, reopen) {
  try {
    await api('issues/' + id, {method: 'PATCH', body: {state}});
    toast(state === 'acknowledged' ? 'Acknowledged' : 'Needs attention again');
    if (reopen) { await openIssue(id, issueNav); loadTab().catch(() => {}); }
    else { closeModal(); loadTab(); }
  } catch (e) { toast(e.message, true); }
}
async function openIssue(id, nav) {
  id = Number(id);
  issueNav = nav && nav.ids && nav.ids.length ? nav : null;
  const i = await api('issues/' + id);
  const refs = (i.reference || '').split(',').map(s => s.trim()).filter(Boolean);
  const stateLine = i.state === 'resolved' ? `<span class="pill resolved">resolved ${ago(i.resolved_at)}</span> - the latest scan didn't find it any more`
    : i.state === 'acknowledged' ? `<span class="pill ack">acknowledged ${ago(i.acknowledged_at)}</span> - still counts in the score until a scan no longer finds it`
    : '<span class="pill open">needs attention</span>';
  const pos = issueNav ? issueNav.ids.indexOf(id) : -1;
  const pager = pos >= 0 && issueNav.ids.length > 1 ? `<div class="pager">
      <button class="btn btn-small" id="i-prev" ${pos === 0 ? 'disabled' : ''} title="Previous (←)">←</button>
      <span class="small muted">${pos + 1} of ${issueNav.ids.length}</span>
      <button class="btn btn-small" id="i-next" ${pos === issueNav.ids.length - 1 ? 'disabled' : ''} title="Next (→)">→</button></div>` : '';
  modal(`
    <div class="row">${sevBadge(i.severity)}${i.kind === 'open_port' ? '<span class="pill">exposed service</span>' : ''}</div>
    <h3 style="margin-top:8px">${esc(i.name)}</h3>
    <div class="muted">${esc(i.device_name)} <span class="mono small">${esc(i.device_ip || '')}${i.port ? ':' + i.port : ''}</span></div>
    <div style="margin-top:8px" class="small">${stateLine}</div>
    <div class="kv"><div><span>CVE</span><b>${esc(i.cve || '–')}</b></div><div><span>CVSS</span><b>${i.cvss ?? '–'}</b></div>
      <div><span>First found</span><b>${fmtTime(i.first_seen)}</b></div><div><span>Last seen</span><b>${fmtTime(i.last_seen)}</b></div></div>
    ${i.description ? `<div class="section"><h4>What it is</h4><div>${esc(i.description)}</div></div>` : ''}
    ${i.remediation ? `<div class="section"><h4>What to do</h4><div>${esc(i.remediation)}</div></div>` : ''}
    ${i.proof ? `<div class="section"><h4>Proof</h4><pre>${esc(i.proof)}</pre></div>` : ''}
    <div class="section"><h4>Check</h4><div class="mono small">${esc(i.template_id || '')}${i.matched_at ? ' · ' + esc(i.matched_at) : ''}</div></div>
    ${refs.length ? `<div class="section"><h4>References</h4>${refs.slice(0, 6).map(r => `<div class="small"><a href="${esc(r)}" target="_blank" rel="noopener noreferrer">${esc(r)}</a></div>`).join('')}</div>` : ''}
    <div class="row" style="margin-top:18px">
      ${nav && nav.back ? `<button class="btn" id="i-back">← ${esc(nav.backLabel || 'Back')}</button>` : ''}
      ${i.state === 'open' ? `<button class="btn btn-primary" id="i-ack">Acknowledge</button>` : ''}
      ${i.state === 'acknowledged' ? `<button class="btn" id="i-open">Needs attention again</button>` : ''}
      <span class="spacer"></span>
      ${pager}
      <button class="btn" data-close>Close</button></div>`);
  const ack = document.getElementById('i-ack'); if (ack) ack.onclick = () => setIssue(i.id, 'acknowledged', true);
  const reopen = document.getElementById('i-open'); if (reopen) reopen.onclick = () => setIssue(i.id, 'open', true);
  const back = document.getElementById('i-back'); if (back) back.onclick = () => issueNav.back();
  const go = step => { const n = issueNav.ids[pos + step]; if (n != null) openIssue(n, issueNav); };
  const prev = document.getElementById('i-prev'); if (prev) prev.onclick = () => go(-1);
  const next = document.getElementById('i-next'); if (next) next.onclick = () => go(1);
}

// ---- devices ----
let deviceFilter = 'home';
let deviceData = null;
const DEVICE_FILTERS = {home: 'Home now', issues: 'With issues', all: 'All', excluded: 'Excluded'};
async function loadDevices() {
  deviceData = await api('devices');
  renderDevices();
}
function deviceState(d) {
  if (d.scanning) return '<span class="state warn">scanning now</span>';
  if (d.excluded) return '<span class="state">excluded</span>';
  if (!d.scannable) return `<span class="state">${d.ip ? 'not a local address' : 'no address'}</span>`;
  if (d.queued) return '<span class="state warn">queued</span>';
  if (d.last_scan_status === 'failed') return '<span class="state bad">last scan failed</span>';
  if (!d.last_scan_at) return '<span class="state">not scanned yet</span>';
  return `<span class="state good">scanned ${ago(d.last_scan_at)}</span>`;
}
function sevbar(c) {
  const total = SEVS.reduce((a, s) => a + c[s], 0);
  if (!total) return '<div class="sevbar"></div>';
  return `<div class="sevbar">${SEVS.filter(s => c[s]).map(s => `<i style="flex:${c[s]};background:${SEV_COLOR(s)}" title="${c[s]} ${s}"></i>`).join('')}</div>`;
}
function renderDevices() {
  const list = deviceData.devices;
  const counts = {home: list.filter(d => d.in_ha && d.online).length, issues: list.filter(d => SEVS.some(s => d.issues[s])).length,
    all: list.length, excluded: list.filter(d => d.excluded).length};
  document.getElementById('device-filter').innerHTML = Object.entries(DEVICE_FILTERS).map(([k, l]) =>
    `<button class="${k === deviceFilter ? 'active' : ''}" data-f="${k}">${l}<span class="n">${counts[k]}</span></button>`).join('');
  document.querySelectorAll('#device-filter button').forEach(b => b.onclick = () => { deviceFilter = b.dataset.f; renderDevices(); });
  document.getElementById('device-note').textContent = `A device is scanned when it is new, and again when it is home and its last scan is ${deviceData.rescan_days} days old.`;
  const q = document.getElementById('device-search').value.trim().toLowerCase();
  const shown = list.filter(d => ({home: d.in_ha && d.online, issues: SEVS.some(s => d.issues[s]), all: true, excluded: d.excluded})[deviceFilter] &&
    (!q || [d.name, d.ip, d.entity_id].some(v => (v || '').toLowerCase().includes(q))));
  document.getElementById('devices').innerHTML = shown.length ? shown.map(d => {
    const total = SEVS.reduce((a, s) => a + d.issues[s], 0);
    return `<div class="device ${d.online ? '' : 'away'}" data-id="${d.id}">
      <div class="top"><span class="dot ${d.online ? 'ok' : 'off'}" title="${d.online ? 'Home' : 'Away'}"></span>
        <div style="min-width:0"><div class="nm">${esc(d.name)}</div><div class="ip">${esc(d.ip || 'no address')}</div></div>
        ${d.score == null ? '<span class="score none">–</span>' : `<span class="score" style="background:${gradeColor(scoreGrade(d.score))}" title="Security score ${d.score}">${d.score}</span>`}</div>
      ${sevbar(d.issues)}
      <div class="meta"><span>${total ? `${total} issue${total === 1 ? '' : 's'}${d.needs_attention ? ` · <b style="color:var(--text)">${d.needs_attention} to check</b>` : ''}` : (d.last_scan_at ? 'no issues' : '')}</span>${deviceState(d)}</div>
    </div>`;
  }).join('') : '<div class="empty" style="grid-column:1/-1">No devices here.</div>';
  document.querySelectorAll('#devices .device').forEach(c => c.onclick = () => openDevice(Number(c.dataset.id)));
}
document.getElementById('device-search').oninput = () => deviceData && renderDevices();
async function openDevice(id) {
  const d = deviceData.devices.find(x => x.id === id);
  const issues = (await api('issues?state=unresolved&device=' + id)).issues;
  modal(`
    <div class="row"><span class="dot ${d.online ? 'ok' : 'off'}"></span><span class="small muted">${d.online ? 'home' : 'away'}${d.last_online && !d.online ? ' · last home ' + ago(d.last_online) : ''}</span></div>
    <h3 style="margin-top:6px">${esc(d.name)}</h3>
    <div class="mono small muted">${esc(d.ip || 'no address')}${d.mac ? ' · ' + esc(d.mac) : ''} · ${esc(d.entity_id)}</div>
    <div class="kv"><div><span>Score</span><b>${d.score ?? '–'}</b></div><div><span>Last scan</span><b>${d.last_scan_at ? ago(d.last_scan_at) : 'never'}</b></div>
      <div><span>Next scan</span><b>${d.excluded ? 'excluded' : d.next_scan_at ? (d.online ? inTime(d.next_scan_at) : 'when home, ' + inTime(d.next_scan_at)) : 'when home'}</b></div><div><span>First seen</span><b>${fmtTime(d.first_seen)}</b></div></div>
    <div class="section"><h4>Open ports</h4><div class="row">${d.services.length ? d.services.map(s => `<span class="pill ${s.risky ? 'risky' : ''}" ${s.risky ? 'title="Shouldn\'t normally be exposed"' : ''}>${esc(s.service)} ${s.port}</span>`).join('') : '<span class="muted small">none found</span>'}</div></div>
    <div class="section"><h4>Issues</h4>${issues.length ? issues.map(i => `<div class="issue-row" data-id="${i.id}">${sevBadge(i.severity)}<div style="min-width:0"><div class="name">${esc(i.name)}</div><div class="sub">${i.port ? 'port ' + i.port + ' · ' : ''}${i.state === 'open' ? 'needs attention' : 'acknowledged'}</div></div><span></span></div>`).join('') : '<div class="muted small">None.</div>'}</div>
    <div class="row" style="margin-top:18px">
      <button class="btn btn-primary" id="d-scan" ${d.online && d.scannable && !d.excluded ? '' : 'disabled'}>Scan now</button>
      <button class="btn" id="d-excl">${d.excluded ? 'Scan this device again' : 'Never scan this device'}</button>
      <span class="spacer"></span><button class="btn" data-close>Close</button></div>`);
  const devIssueIds = issues.map(x => x.id);
  const nav = {ids: devIssueIds, back: () => openDevice(id), backLabel: d.name};
  document.querySelectorAll('.modal .issue-row').forEach(r => r.onclick = () => openIssue(r.dataset.id, nav));
  document.getElementById('d-scan').onclick = async () => {
    try { await api(`devices/${id}/scan`, {method: 'POST'}); toast('Queued for a scan'); closeModal(); loadDevices(); } catch (e) { toast(e.message, true); }
  };
  document.getElementById('d-excl').onclick = async () => {
    await api('devices/' + id, {method: 'PATCH', body: {excluded: !d.excluded}}); closeModal(); loadDevices();
  };
}

// ---- scanner ----
async function loadScanner() {
  const s = await api('scanner');
  const f = s.freshness, t = f.templates || {};
  document.getElementById('components').innerHTML = f.components.map(c => `<div class="fresh-row"><span class="k">${COMPONENT[c.key]}</span>${c.key === 'templates' && s.updating ? '<span class="state warn">updating…</span>' : freshState(c)}
    <span class="d">installed <span class="mono">${c.installed ? 'v' + esc(c.installed) : '–'}</span> · latest <span class="mono">${c.latest ? 'v' + esc(c.latest) : 'unknown'}</span>${c.outdated && c.key !== 'templates' ? ' · arrives with an app update' : ''}</span></div>`).join('') +
    `<p class="small muted" style="margin:10px 0 0">Latest releases checked ${ago(f.latest_checked_at)}. The templates update themselves every day${overview && !overview.config.update_templates ? ' (turned off in the options)' : ''}; nuclei and naabu come with the app.</p>`;
  const btn = document.getElementById('update-now');
  btn.disabled = s.updating;
  btn.onclick = async () => { try { await api('scanner/update', {method: 'POST'}); toast('Updating templates'); setTimeout(loadScanner, 800); } catch (e) { toast(e.message, true); } };
  document.getElementById('template-stats').innerHTML = `<dl class="facts" style="font-size:13px">
    <dt>Last updated</dt><dd>${t.updated_at ? ago(t.updated_at) : 'never'}</dd>
    <dt>Checks installed</dt><dd>${(t.total || 0).toLocaleString()}</dd>
    <dt>CVE checks</dt><dd>${(t.cves || 0).toLocaleString()}</dd>
    <dt>Newest CVE covered</dt><dd>${esc(t.newest_cve || '–')}</dd></dl>`;
  const scans = (await api('scans')).scans;
  document.getElementById('scans-table').innerHTML = scans.length ? `<table><thead><tr><th>Device</th><th>Why</th><th>Result</th><th class="hide-sm">Ports</th><th class="hide-sm">Issues</th><th class="hide-sm">Took</th><th>Started</th></tr></thead><tbody>${scans.slice(0, 25).map(sc =>
    `<tr><td>${esc(sc.device_name || '–')} <span class="mono muted small">${esc(sc.ip)}</span></td><td class="small">${esc(sc.reason)}</td>
     <td>${sc.status === 'running' ? '<span class="state warn">running</span>' : sc.status === 'failed' ? `<span class="state bad" title="${esc(sc.error || '')}">failed</span>` : `<span class="state good">done</span>${sc.new_issues ? ` <span class="small muted">+${sc.new_issues}</span>` : ''}${sc.resolved_issues ? ` <span class="small muted">−${sc.resolved_issues}</span>` : ''}`}</td>
     <td class="mono hide-sm">${sc.open_ports}</td><td class="mono hide-sm">${sc.findings}</td><td class="mono small hide-sm">${sc.finished_at ? Math.max(1, Math.round((sc.finished_at - sc.started_at) / 60)) + ' min' : '–'}</td><td class="small muted">${fmtTime(sc.started_at)}</td></tr>`).join('')}</tbody></table>` :
    '<div class="empty">No scans yet.</div>';
  await loadTemplates();
}

// ---- templates ----
async function loadTemplates() {
  const d = await api('templates');
  document.getElementById('templates-table').innerHTML = d.templates.length ?
    `<table><tbody>${d.templates.map(t => `<tr><td class="mono">${esc(t.name)}</td><td>${t.size} bytes</td><td><div class="row"><button class="btn btn-small" data-edit="${esc(t.name)}">Edit</button><button class="btn btn-small btn-danger" data-delt="${esc(t.name)}">×</button></div></td></tr>`).join('')}</tbody></table>` :
    '<div class="empty small">No templates of your own.</div>';
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
  const c = (overview || await api('overview')).config;
  const rows = [
    ['Rescan after', c.rescan_days + ' days'], ['Minimum severity', c.min_severity], ['Requests per second', c.requests_per_second],
    ['Concurrent hosts', c.max_concurrent_hosts], ['Default-credential check', c.default_credentials ? 'on' : 'off'],
    ['Update templates', c.update_templates ? 'daily' : 'off'], ['Keep resolved issues', c.retention_days + ' days'],
    ['Only these tags', c.nuclei_tags || '–'], ['Exclude tags', c.nuclei_exclude_tags || '–'],
  ];
  document.getElementById('settings-options').innerHTML =
    `<table><tbody>${rows.map(([l, v]) => `<tr><td class="muted">${l}</td><td class="mono">${esc(v)}</td></tr>`).join('')}</tbody></table>
     <p class="muted small" style="margin-top:8px">Change these on the app's Configuration tab in Home Assistant.</p>`;
  const db = await api('database');
  document.getElementById('settings-db').innerHTML = `<table><tbody>
    <tr><td class="muted">On disk</td><td class="mono">${fmtBytes(db.size + db.wal_size)}</td></tr>
    <tr><td class="muted">Devices</td><td class="mono">${db.counts.devices}</td></tr>
    <tr><td class="muted">Scans</td><td class="mono">${db.counts.scans}</td></tr>
    <tr><td class="muted">Issues</td><td class="mono">${db.counts.issues}</td></tr></tbody></table>`;
}
document.getElementById('wipe-btn').onclick = () => {
  modal(`<h3>Delete all data</h3><p class="muted">This removes every device, scan, issue and the history. Type <b>delete</b> to confirm.</p>
    <input id="wipe-confirm" placeholder="delete" style="width:100%">
    <div class="row" style="margin-top:14px"><button class="btn btn-danger" id="wipe-go">Delete everything</button><button class="btn" data-close>Cancel</button></div>`);
  document.getElementById('wipe-go').onclick = async () => {
    try { await api('wipe', {method: 'POST', body: {confirm: document.getElementById('wipe-confirm').value}}); closeModal(); toast('Database emptied'); loadTab(); }
    catch (e) { toast(e.message, true); }
  };
};

// ---- modal ----
function modal(html) {
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="overlay"><div class="modal">${html}</div></div>`;
  root.querySelector('.overlay').onclick = e => { if (e.target.classList.contains('overlay')) closeModal(); };
  root.querySelectorAll('[data-close]').forEach(b => b.onclick = closeModal);
}
function closeModal() { document.getElementById('modal-root').innerHTML = ''; issueNav = null; }
// Esc closes a modal; ← / → step through issues when the detail is open from a list
document.addEventListener('keydown', e => {
  if (!document.getElementById('modal-root').innerHTML) return;
  if (e.key === 'Escape') return closeModal();
  if (issueNav && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    const btn = document.getElementById(e.key === 'ArrowLeft' ? 'i-prev' : 'i-next');
    if (btn && !btn.disabled) btn.click();
  }
});

// ---- status bar + startup ----
function renderStatus() {
  const o = overview;
  if (!o) return;
  const ha = o.home_assistant;
  document.getElementById('statusbar').innerHTML =
    `<span><span class="dot ${ha.error ? 'bad' : 'ok'}"></span> Home Assistant ${ha.error ? 'unreachable' : 'trackers read ' + ago(ha.last_sync)}</span>` +
    `<span>${o.running ? 'scanning ' + esc(o.running.device_name) : 'idle'}${o.queue.length ? ` · ${o.queue.length} queued` : ''}</span>` +
    `<span class="spacer"></span><span class="muted">Nuclei Scanner ${esc(o.version)} · internal use</span>`;
}
async function tick() {
  try {
    if (activeTab === 'dashboard') await loadDashboard();
    else { overview = await api('overview'); if (activeTab === 'devices' || activeTab === 'scanner') await loadTab(); }
    renderStatus();
  } catch (e) {
    document.getElementById('statusbar').innerHTML = '<span><span class="dot bad"></span> cannot reach the app</span>';
  }
}
const startTab = location.hash.slice(1);
if (startTab && document.getElementById('tab-' + startTab)) showTab(startTab);
// Deep link to one issue: #issue-<id> opens it, stepping through its device's issues
const deepIssue = (location.hash.match(/^#issue-(\d+)$/) || [])[1];
if (deepIssue) api('issues/' + deepIssue).then(i =>
  api('issues?state=unresolved&device=' + i.device_id).then(d =>
    openIssue(deepIssue, {ids: d.issues.map(x => x.id), back: () => showTab('devices'), backLabel: i.device_name}))).catch(() => {});
tick();
setInterval(() => { if (!document.getElementById('modal-root').innerHTML) tick(); }, 5000);
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => activeTab === 'dashboard' && overview && (scoreChart(document.getElementById('chart-score'), overview.history), issuesChart(document.getElementById('chart-issues'), overview.history)), 150); });
