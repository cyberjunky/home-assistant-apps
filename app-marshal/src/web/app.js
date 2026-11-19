// Marshal dashboard. Plain script, one scope. Everything is drawn from one document: the app's api/report,
// or window.MARSHAL_STATIC when the dashboard was saved as a single file (then it is read-only).
'use strict';
const STATIC = window.MARSHAL_STATIC || null;
const SEVS = ['critical', 'high', 'medium', 'low'];
const GRADES = ['A', 'B', 'C', 'D', 'F'];
const VERDICT = {A: 'looks fine', B: 'mostly fine', C: 'worth a look', D: 'read before you trust it', F: 'needs a close look'};
const HOST_KIND = {dropbox: 'Collects data', ip: 'IP address', chat: 'Chat service', telemetry: 'Telemetry', external: 'Internet', reference: 'Documentation', local: 'Local'};
// Text from integrations (names, code lines, hosts) is written by strangers: it only ever reaches the page through esc()
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const $ = id => document.getElementById(id);
const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || word + 's')}`;
const fmtNum = n => n == null ? '–' : Number(n).toLocaleString('en');
const fmtDay = t => t ? new Date(t * 1000).toLocaleString([], {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false}) : 'never';
const fmtDate = s => { const d = new Date(s); return isNaN(d) ? '–' : d.toLocaleDateString([], {day: 'numeric', month: 'short', year: 'numeric'}); };
const gradeOf = v => v >= 90 ? 'A' : v >= 80 ? 'B' : v >= 65 ? 'C' : v >= 50 ? 'D' : 'F';
const gradeChip = (g, big) => `<span class="grade g-${esc(g)}${big ? ' big' : ''}" title="Grade ${esc(g)}: ${VERDICT[g] || ''}">${esc(g)}</span>`;
const sevBadge = s => `<span class="sev c-${esc(s)}">${esc(s)}</span>`;
const sevCounts = c => `<span class="counts">${SEVS.map(s => `<span class="sev bare c-${s}${c[s] ? '' : ' zero'}" title="${c[s] || 0} ${s}">${c[s] || 0}</span>`).join('')}</span>`;
const repoLink = r => /^[\w.-]+\/[\w.-]+$/.test(r || '') ? `<a href="https://github.com/${esc(r)}" target="_blank" rel="noopener noreferrer">${esc(r)}</a>` : esc(r || '–');

let report = null;
let group = '';
let sortKey = 'score', sortDir = 1;
let areaFilter = '';
let openKey = null;

async function api(path, options) {
  const opts = options || {};
  if (opts.body) { opts.body = JSON.stringify(opts.body); opts.headers = {'Content-Type': 'application/json'}; }
  const r = await fetch('api/' + path, opts);
  let data = null; try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
function toast(message, error) {
  const el = document.createElement('div'); el.className = 'toast' + (error ? ' err' : ''); el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), 4500);
}

// ---- theme: the same toggle as the other apps (auto follows Home Assistant, then light, then dark) ----
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = null;
try { themeChoice = localStorage.getItem('marshal_theme'); } catch (e) { /* only a convenience */ }
if (!THEME_ICONS[themeChoice]) themeChoice = 'auto';
function haDarkMode() {
  try {
    const ha = window.parent !== window && window.parent.document.querySelector('home-assistant');
    return ha && ha.hass && ha.hass.themes ? Boolean(ha.hass.themes.darkMode) : null;
  } catch (e) { return null; }
}
function applyTheme() {
  const root = document.documentElement;
  const ha = themeChoice === 'auto' ? haDarkMode() : null;
  if (themeChoice === 'auto' && ha === null) { if (!STATIC) delete root.dataset.theme; }  // the stylesheet follows the system
  else root.dataset.theme = (themeChoice === 'dark' || ha === true) ? 'dark' : 'light';
  $('theme-icon').textContent = THEME_ICONS[themeChoice];
}
$('theme-toggle').addEventListener('click', () => {
  themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
  try { localStorage.setItem('marshal_theme', themeChoice); } catch (e) { /* only a convenience */ }
  if (STATIC && themeChoice === 'auto') delete document.documentElement.dataset.theme;
  applyTheme();
});
if (STATIC) { themeChoice = 'auto'; $('theme-group').hidden = true; }  // a saved report follows whoever shows it
else { applyTheme(); setInterval(applyTheme, 2000); }

// ---- what is shown ----
const shown = () => report.integrations.filter(i => !group || i.group === group);
const byScore = list => [...list].sort((a, b) => a.score - b.score || a.name.localeCompare(b.name));
const findingsOf = (i, withInfo) => i.findings.filter(f => withInfo || f.severity !== 'info');
function label(i) {
  return `<span class="nm">${esc(i.name || i.domain)}</span><div class="sub">${esc(i.domain)}${i.repository ? ' · ' + esc(i.repository) : ''}</div>`;
}

function renderGroups() {
  const counts = {};
  report.integrations.forEach(i => { counts[i.group] = (counts[i.group] || 0) + 1; });
  const groups = report.groups.filter(g => counts[g.id]);
  if (group && !counts[group]) group = '';
  $('groups').hidden = groups.length < 2;
  $('groups').innerHTML = [{id: '', label: 'All'}, ...groups].map(g =>
    `<button type="button" data-group="${esc(g.id)}" class="${g.id === group ? 'active' : ''}">${esc(g.label)}<span class="n">${g.id ? counts[g.id] : report.integrations.length}</span></button>`).join('');
}

function renderBanners() {
  const st = report.status || {};
  const out = [];
  if (!STATIC && st.custom_components === false) out.push(['bad', 'Home Assistant has no custom_components folder: nothing is installed from the Marketplace yet. Use Check to look at an integration before you install it.']);
  (st.notes || []).forEach(n => out.push(['', n]));
  $('banners').innerHTML = out.map(([c, t]) => `<div class="banner ${c}"><span>${esc(t)}</span></div>`).join('');
}

function renderDashboard() {
  const list = shown();
  if (!list.length) {
    $('dash-tiles').innerHTML = `<div class="tile"><div class="l">Integrations</div><div class="v">0</div><div class="d">${report.status && report.status.scanning ? 'the first scan is running' : 'nothing to show yet'}</div></div>`;
    ['dash-grades', 'dash-areas', 'dash-worst', 'dash-rules'].forEach(id => { $(id).innerHTML = '<div class="empty-state">Nothing yet</div>'; });
    return;
  }
  const avg = Math.round(list.reduce((a, i) => a + i.score, 0) / list.length);
  const total = s => list.reduce((a, i) => a + (i.counts[s] || 0), 0);
  const withSev = s => list.filter(i => i.counts[s]).length;
  const sites = new Set();
  list.forEach(i => i.hosts.forEach(h => { if (!['reference', 'local'].includes(h.kind)) sites.add(h.site); }));
  const tile = (l, v, d, tab) => `<div class="tile${tab ? ' click' : ''}"${tab ? ` data-goto="${tab}"` : ''}><div class="l">${l}</div><div class="v">${v}</div><div class="d">${d}</div></div>`;
  $('dash-tiles').innerHTML =
    tile('Average score', `${avg}${gradeChip(gradeOf(avg), true)}`, VERDICT[gradeOf(avg)]) +
    tile('Integrations', list.length, `${list.filter(i => i.grade === 'A').length} with grade A`, 'integrations') +
    SEVS.slice(0, 3).map(s => tile(sevBadge(s), total(s), `in ${plural(withSev(s), 'integration')}`, 'findings')).join('') +
    tile('Internet services', sites.size, 'named in the code', 'network');

  const perGrade = GRADES.map(g => list.filter(i => i.grade === g).length);
  $('dash-grades').innerHTML =
    `<div class="dist" role="img" aria-label="Integrations per grade">${GRADES.map((g, n) => perGrade[n] ? `<div class="g-${g}" style="flex:${perGrade[n]}" title="${perGrade[n]} with grade ${g}">${perGrade[n] / list.length > 0.06 ? g : ''}</div>` : '').join('')}</div>` +
    `<div class="legend">${GRADES.map((g, n) => `<span class="g-${g}"><i></i>${g} <b>${perGrade[n]}</b> ${VERDICT[g]}</span>`).join('')}</div>`;

  $('dash-areas').innerHTML = Object.entries(report.categories).map(([id, name]) => {
    const v = Math.round(list.reduce((a, i) => a + i.scores[id], 0) / list.length);
    const hit = list.filter(i => i.scores[id] < 100).length;
    return `<div class="bar-row" title="${hit} of ${list.length} integrations have findings here"><span>${esc(name)}</span><div class="meter"><i style="width:${v}%"></i></div><b>${v}</b></div>`;
  }).join('');

  $('dash-worst').innerHTML = byScore(list).slice(0, 8).map(i =>
    `<div class="row" data-open="${esc(i.key)}"><span>${gradeChip(i.grade)} <span class="mono">${i.score}</span></span><div style="min-width:0"><div class="nm">${esc(i.name || i.domain)}</div><div class="sub">${esc((findingsOf(i)[0] || {}).title || 'no findings')}</div></div>${sevCounts(i.counts)}</div>`).join('');

  const top = rules(list, false).slice(0, 8);
  $('dash-rules').innerHTML = top.length ? top.map(r =>
    `<div class="row" data-rule="${esc(r.rule)}">${sevBadge(r.severity)}<div style="min-width:0"><div class="nm">${esc(r.title)}</div><div class="sub">${esc(report.categories[r.category])}</div></div><span class="mono">${r.items.length}</span></div>`).join('')
    : '<div class="empty-state">No findings</div>';

  const history = report.history || [];
  $('dash-history-panel').hidden = history.length < 2;
  if (history.length >= 2) {
    const w = 600, h = 70, min = Math.min(...history.map(p => p.score)) - 3, max = Math.max(...history.map(p => p.score)) + 3;
    const x = n => 4 + n * (w - 8) / (history.length - 1), y = v => h - 6 - (v - min) * (h - 12) / (max - min);
    const last = history[history.length - 1];
    $('dash-history-hint').textContent = `${history[0].day} to ${last.day}, now ${last.score}`;
    $('dash-history').innerHTML = `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Average score over time"><polyline fill="none" stroke="var(--series-1)" stroke-width="2" vector-effect="non-scaling-stroke" points="${history.map((p, n) => `${x(n)},${y(p.score)}`).join(' ')}"/>${history.map((p, n) => `<circle cx="${x(n)}" cy="${y(p.score)}" r="5" fill="transparent"><title>${esc(p.day)}: ${p.score}</title></circle>`).join('')}</svg>`;
  }
}

// ---- integrations ----
const COLUMNS = [
  ['rank', '#', 'num'], ['name', 'Integration', ''], ['score', 'Score', ''],
  ['data', 'Data', 'hide-sm'], ['code', 'Code', 'hide-sm'], ['network', 'Network', 'hide-sm'], ['performance', 'Speed', 'hide-sm'], ['upkeep', 'Upkeep', 'hide-sm'],
  ['findings', 'Findings', ''], ['hosts', 'Services', 'num hide-sm'], ['installs', 'Installs', 'num hide-sm'],
];
const outside = i => new Set(i.hosts.filter(h => !['reference', 'local'].includes(h.kind)).map(h => h.site)).size;
function sortValue(i, key) {
  if (key === 'name') return (i.name || i.domain).toLowerCase();
  if (key === 'score' || key === 'rank') return i.score;
  if (key === 'findings') return -(i.counts.critical * 1000 + i.counts.high * 100 + i.counts.medium * 10 + i.counts.low);
  if (key === 'hosts') return -outside(i);
  if (key === 'installs') return -(i.installs || 0);
  return i.scores[key];
}
function renderIntegrations() {
  const all = byScore(shown());
  const rank = new Map(all.map((i, n) => [i.key, n + 1]));
  const q = $('int-search').value.trim().toLowerCase(), g = $('int-grade').value;
  const list = all.filter(i => (!g || i.grade === g) && (!q || `${i.name} ${i.domain} ${i.repository || ''}`.toLowerCase().includes(q)));
  list.sort((a, b) => { const x = sortValue(a, sortKey), y = sortValue(b, sortKey); return (x < y ? -1 : x > y ? 1 : a.score - b.score) * sortDir; });
  $('int-count').textContent = `${list.length} of ${all.length}, ranked from most to least in need of a look`;
  const area = v => `<span class="mini g-${gradeOf(v)}"><i style="width:${v}%"></i></span><span class="mono">${v}</span>`;
  $('int-table').innerHTML = list.length ? `<table class="list"><thead><tr>${COLUMNS.map(([k, t, c]) => `<th class="sort ${c}" data-sort="${k}">${t}${sortKey === k ? (sortDir > 0 ? ' ↑' : ' ↓') : ''}</th>`).join('')}</tr></thead><tbody>${list.map(i => `<tr class="click" data-open="${esc(i.key)}">
    <td class="num">${rank.get(i.key)}</td><td>${label(i)}</td><td style="white-space:nowrap">${gradeChip(i.grade)} <span class="mono">${i.score}</span></td>
    ${Object.keys(report.categories).map(c => `<td class="hide-sm" style="white-space:nowrap">${area(i.scores[c])}</td>`).join('')}
    <td>${sevCounts(i.counts)}</td><td class="num hide-sm">${outside(i)}</td><td class="num hide-sm">${fmtNum(i.installs)}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty-state">No integration matches</div>';
}

// ---- findings, per check ----
function rules(list, withInfo) {
  const map = new Map();
  list.forEach(i => findingsOf(i, withInfo).forEach(f => {
    if (!map.has(f.rule)) map.set(f.rule, {rule: f.rule, title: f.title, why: f.why, category: f.category, severity: f.severity, items: []});
    const r = map.get(f.rule);
    if (SEVS.concat('info').indexOf(f.severity) < SEVS.concat('info').indexOf(r.severity)) r.severity = f.severity;
    r.items.push({i, f});
  }));
  const order = s => SEVS.concat('info').indexOf(s);
  return [...map.values()].sort((a, b) => order(a.severity) - order(b.severity) || b.items.length - a.items.length);
}
let openRule = null;
function renderFindings() {
  const list = shown();
  const all = rules(list, $('find-info').checked);
  $('find-areas').innerHTML = [['', 'All areas'], ...Object.entries(report.categories)].map(([id, name]) =>
    `<button type="button" data-area="${id}" class="${id === areaFilter ? 'active' : ''}">${esc(name)}<span class="n">${all.filter(r => !id || r.category === id).length}</span></button>`).join('');
  const rows = all.filter(r => !areaFilter || r.category === areaFilter);
  $('find-table').innerHTML = rows.length ? `<table class="list"><thead><tr><th>Severity</th><th>Finding</th><th class="hide-sm">Area</th><th class="num">Integrations</th></tr></thead><tbody>${rows.map(r => `
    <tr class="click" data-rule="${esc(r.rule)}"><td>${sevBadge(r.severity)}</td><td><span class="nm">${esc(r.title)}</span>${openRule === r.rule ? `<div class="muted" style="max-width:68ch;margin-top:4px">${esc(r.why)}</div>` : ''}</td><td class="hide-sm">${esc(report.categories[r.category])}</td><td class="num">${r.items.length}</td></tr>
    ${openRule === r.rule ? byScore(r.items.map(x => x.i)).map(i => { const f = i.findings.find(x => x.rule === r.rule); const w = f.where[0] || {}; return `<tr class="click" data-open="${esc(i.key)}"><td></td><td>${gradeChip(i.grade)} ${esc(i.name || i.domain)} <span class="sub">${esc(w.file || '')}${w.line ? ':' + w.line : ''} ${esc(w.note || '')}</span></td><td class="hide-sm"></td><td class="num">${f.count > 1 ? '×' + f.count : ''}</td></tr>`; }).join('') : ''}`).join('')}</tbody></table>`
    : '<div class="empty-state">No findings</div>';
}

// ---- network ----
function renderNetwork() {
  const everything = $('net-all').checked, q = $('net-search').value.trim().toLowerCase();
  const map = new Map();
  shown().forEach(i => i.hosts.forEach(h => {
    if (!everything && ['reference', 'local'].includes(h.kind)) return;
    if (!map.has(h.host)) map.set(h.host, {...h, users: []});
    const row = map.get(h.host); row.users.push(i); row.insecure = row.insecure || h.insecure;
  }));
  const order = Object.keys(HOST_KIND);
  const rows = [...map.values()].filter(h => !q || h.host.includes(q) || h.users.some(i => `${i.name} ${i.domain}`.toLowerCase().includes(q)))
    .sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || b.users.length - a.users.length || a.host.localeCompare(b.host));
  $('net-count').textContent = `${plural(rows.length, 'host')} named in the code`;
  const kind = h => `<span class="pill${h.kind === 'dropbox' ? ' bad' : ['ip', 'chat', 'telemetry'].includes(h.kind) ? ' warn' : ''}">${HOST_KIND[h.kind]}</span>`;
  $('net-table').innerHTML = rows.length ? `<table class="list"><thead><tr><th>Host</th><th>Kind</th><th class="hide-sm">Encrypted</th><th>Used by</th></tr></thead><tbody>${rows.slice(0, 600).map(h => `
    <tr><td class="mono wrap">${esc(h.host)}</td><td>${kind(h)}</td><td class="hide-sm">${h.insecure ? '<span class="pill warn">not always</span>' : 'yes'}</td>
    <td>${h.users.slice(0, 6).map(i => `<button class="link-btn" data-open="${esc(i.key)}">${esc(i.name || i.domain)}</button>`).join(', ')}${h.users.length > 6 ? ` and ${h.users.length - 6} more` : ''}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty-state">No hosts</div>';
}

// ---- check before install ----
function renderCheck() {
  const list = report.integrations.filter(i => i.group === 'checked');
  $('check-list').innerHTML = list.map(i => `<div class="row" data-open="${esc(i.key)}"><span>${gradeChip(i.grade)} <span class="mono">${i.score}</span></span><div style="min-width:0"><div class="nm">${esc(i.repository)}</div><div class="sub">${esc(i.name)} · ${esc(i.ref === 'HEAD' ? 'default branch' : i.ref)} · checked ${esc(fmtDay(i.scanned_at))}</div></div><button class="link-btn" data-forget="${esc(i.repository)}">Remove</button></div>`).join('');
}
$('check-form').addEventListener('submit', async e => {
  e.preventDefault();
  const button = $('check-btn');
  button.disabled = true; button.textContent = 'Checking…';
  try {
    const result = await api('check', {method: 'POST', body: {repository: $('check-repo').value}});
    $('check-repo').value = '';
    await load();
    openDrawer(result.key);
  } catch (err) { toast(err.message, true); }
  button.disabled = false; button.textContent = 'Check';
});

// ---- one integration ----
function openDrawer(key) {
  const i = report.integrations.find(x => x.key === key);
  if (!i) return;
  openKey = key;
  const fact = (l, v) => v == null || v === '' ? '' : `<div class="fact"><div class="l">${l}</div><div class="v">${v}</div></div>`;
  const rt = i.runtime || {};
  const places = f => f.where.filter(w => w.file || w.note).map(w => `<div><span class="loc">${esc(w.file)}${w.line && w.line < 1000 ? ':' + w.line : ''}</span>${w.note ? ` <span class="note">${esc(w.note)}</span>` : ''}${w.snippet ? `<code>${esc(w.snippet)}</code>` : ''}</div>`).join('');
  const areas = Object.entries(report.categories).map(([id, name]) => {
    const fs = i.findings.filter(f => f.category === id);
    return `<div class="panel"><div class="panel-body"><div class="title"><span>${esc(name)}</span><span class="hint"><span class="mini g-${gradeOf(i.scores[id])}"><i style="width:${i.scores[id]}%"></i></span><b class="mono">${i.scores[id]}</b></span></div>${fs.length ? fs.map(f => `<div class="finding"><h3>${sevBadge(f.severity)} ${esc(f.title)}${f.count > 1 ? ` <span class="pill">${f.count} places</span>` : ''}</h3><p>${esc(f.why)}</p><div class="where">${places(f)}${f.count > f.where.length ? `<div class="muted">and ${f.count - f.where.length} more</div>` : ''}</div></div>`).join('') : '<div class="muted">Nothing found</div>'}</div></div>`;
  }).join('');
  const hosts = i.hosts.filter(h => !['reference', 'local'].includes(h.kind));
  const reqs = i.requirements || [];
  $('modal-root').innerHTML = `<div class="modal-back" id="modal-back"><div class="drawer" role="dialog" aria-label="${esc(i.name)}">
    <div class="drawer-head"><div style="flex:1;min-width:0"><h2>${esc(i.name || i.domain)} ${gradeChip(i.grade)} <span class="mono" style="font-size:14px">${i.score}</span></h2><div class="muted">${VERDICT[i.grade]} · ${repoLink(i.repository)}</div></div><button class="close" id="drawer-close" aria-label="Close">×</button></div>
    <div class="facts">${fact('Domain', esc(i.domain))}${fact('Version', esc(i.version))}${fact('Kind', esc(i.iot_class))}${fact('Installations', i.installs ? fmtNum(i.installs) : '')}${fact('Stars', i.stars != null ? fmtNum(i.stars) : '')}${fact('Last change', i.last_updated ? esc(fmtDate(i.last_updated)) : '')}${fact('Python', `${fmtNum(i.stats.lines)} lines`)}${fact('Setup time', rt.setup_seconds != null ? rt.setup_seconds.toFixed(1) + ' s' : '')}${fact('Owners', esc((i.codeowners || []).join(' ')))}</div>
    ${areas}
    ${hosts.length ? `<div class="panel"><div class="panel-body"><div class="title"><span>Internet hosts named in the code</span></div><div class="where">${hosts.map(h => `<div><span class="loc">${esc(h.host)}</span> <span class="pill">${HOST_KIND[h.kind]}</span>${h.insecure ? ' <span class="pill warn">unencrypted</span>' : ''} <span class="muted">${esc(h.file)}:${h.line}</span></div>`).join('')}</div></div></div>` : ''}
    ${reqs.length ? `<div class="panel"><div class="panel-body"><div class="title"><span>Requirements</span></div><div class="where">${reqs.map(r => `<div><span class="loc">${esc(r.name)}</span> ${esc(r.spec)}${r.url ? ' <span class="pill bad">from a URL</span>' : r.pinned ? '' : ' <span class="pill warn">not pinned</span>'}${(r.vulnerabilities || []).map(v => ` <span class="pill bad">${esc(v.id)}</span>`).join('')}</div>`).join('')}</div></div></div>` : ''}
  </div></div>`;
  $('drawer-close').focus();
}
function closeDrawer() { openKey = null; $('modal-root').innerHTML = ''; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

// ---- wiring ----
function showTab(tab) {
  document.querySelectorAll('.tab[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tab));
}
function render() {
  renderGroups(); renderBanners(); renderDashboard(); renderIntegrations(); renderFindings(); renderNetwork(); renderCheck();
  $('about-areas').innerHTML = Object.entries(report.categories).map(([id, name]) => `<li><b>${esc(name)}</b> counts for ${Math.round(report.weights.category[id] * 100)}%</li>`).join('');
  const st = report.status || {};
  $('statusbar').innerHTML = STATIC
    ? `<span>Marshal <b>${esc(report.version)}</b></span><span>made <b>${esc(fmtDay(report.generated_at))}</b></span><span>${plural(report.integrations.length, 'integration')}</span>`
    : `<span>Marshal <b>${esc(report.version)}</b></span><span>${st.scanning ? '<b>scanning…</b>' : `last scan <b>${esc(fmtDay(st.last_scan))}</b>`}</span><span>catalog <b>${st.catalog_at ? esc(fmtDay(st.catalog_at)) : 'not fetched'}</b></span>`;
}
document.addEventListener('click', e => {
  const el = e.target.closest('[data-tab],[data-goto],[data-open],[data-rule],[data-group],[data-area],[data-sort],[data-forget]');
  if (e.target.id === 'modal-back' || e.target.id === 'drawer-close') return closeDrawer();
  if (!el) return;
  if (el.dataset.forget) {
    e.stopPropagation();
    return api('check/' + el.dataset.forget, {method: 'DELETE'}).then(load).catch(err => toast(err.message, true));
  }
  if (el.dataset.tab) showTab(el.dataset.tab);
  else if (el.dataset.goto) showTab(el.dataset.goto);
  else if (el.dataset.open) openDrawer(el.dataset.open);
  else if (el.dataset.rule) { openRule = openRule === el.dataset.rule && el.closest('#find-table') ? null : el.dataset.rule; areaFilter = ''; showTab('findings'); renderFindings(); }
  else if (el.dataset.group !== undefined) { group = el.dataset.group; render(); }
  else if (el.dataset.area !== undefined) { areaFilter = el.dataset.area; renderFindings(); }
  else if (el.dataset.sort) { sortDir = sortKey === el.dataset.sort ? -sortDir : 1; sortKey = el.dataset.sort; renderIntegrations(); }
});
['int-search', 'int-grade'].forEach(id => $(id).addEventListener('input', renderIntegrations));
['net-search', 'net-all'].forEach(id => $(id).addEventListener('input', renderNetwork));
$('find-info').addEventListener('input', renderFindings);
$('scan-btn').addEventListener('click', () => api('scan', {method: 'POST'}).then(() => { toast('Scan started'); setTimeout(load, 1500); }).catch(err => toast(err.message, true)));

async function load() {
  try {
    // A saved dashboard carries its document, or says where it is
    if (typeof STATIC === 'string') report = report || await (await fetch(STATIC)).json();
    else report = STATIC || await api('report');
  } catch (err) {
    if (!report) $('banners').innerHTML = `<div class="banner bad"><span>${esc(err.message)}</span></div>`;
    return;
  }
  render();
  if (openKey && !report.integrations.some(i => i.key === openKey)) closeDrawer();
}
if (STATIC) $('tab-btn-check').hidden = true;
else { $('scan-btn').hidden = false; $('export-btn').hidden = false; setInterval(() => { if (!openKey) load(); }, 20000); }
load();
