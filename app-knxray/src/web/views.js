// The pages that are tables and lists: overview, findings, group addresses, devices, Home Assistant and
// the interface. They draw from `model` and `state` (core.js).
'use strict';

const tile = (label, value, detail, cls, go) => `<div class="tile${go ? ' click' : ''}"${go ? ` data-go="${go}"` : ''}><div class="l">${esc(label)}</div><div class="v ${cls || ''}">${value}</div><div class="d">${detail || '&nbsp;'}</div></div>`;
// Bars for counts over time: [{value, title}]
function bars(el, items, axis) {
  const max = Math.max(1, ...items.map(i => i.value));
  el.innerHTML = items.map(i => `<i style="height:${Math.max(i.value ? 3 : 0, i.value / max * 100)}%" title="${esc(i.title)}"></i>`).join('');
  let ax = el.nextElementSibling;
  if (!ax || !ax.classList.contains('bars-axis')) { ax = document.createElement('div'); ax.className = 'bars-axis'; el.after(ax); }
  ax.innerHTML = axis.map(a => `<span>${esc(a)}</span>`).join('');
}
// A list with a bar per row: who is busiest
function topTable(el, rows, empty) {
  if (!rows.length) { $(el).innerHTML = `<div class="empty-state">${esc(empty)}</div>`; return; }
  const max = Math.max(1, ...rows.map(r => r.count));
  $(el).innerHTML = '<table class="list tight"><tbody>' + rows.map(r =>
    `<tr><td class="addr" style="width:1%">${issueDot(r.address)}${ref(r.address)}</td><td>${esc(nameOf(r.address) || (r.note || ''))}</td><td class="num" style="width:1%">${fmtCount(r.count)}</td><td style="width:28%"><span class="share" style="width:${Math.max(2, r.count / max * 100)}%"></span></td></tr>`).join('') + '</tbody></table>';
}

// ---------------------------------------------------------------- overview
TAB_RENDER.overview = () => {
  const f = state.findings || {}, t = state.telegrams || {}, p = state.project || {}, ha = state.ha || {}, bus = state.bus || {}, r = state.recorder || {};
  const heard = model.devices.filter(d => d.known && (d.stats || {}).count).length;
  $('overview-tiles').innerHTML = [
    tile('Errors', f.error || 0, 'things that do not work', f.error ? 'bad' : 'ok', 'findings'),
    tile('Warnings', f.warning || 0, `${f.info || 0} notes`, f.warning ? 'warn' : 'ok', 'findings'),
    tile('Telegrams / min', esc(t.rate ?? 0), `${fmtCount(t.total)} since ${t.since ? fmtWhen(t.since) : '–'}`, '', 'monitor'),
    tile('Group addresses', p.loaded ? `${t.addresses || 0}<small style="font-size:13px;color:var(--secondary-text-color)"> / ${p.group_addresses}</small>` : (t.addresses || 0), p.loaded ? 'seen on the bus / in the project' : 'seen on the bus', '', 'addresses'),
    tile('Devices', p.loaded ? `${heard}<small style="font-size:13px;color:var(--secondary-text-color)"> / ${p.devices}</small>` : (t.sources || 0), p.loaded ? 'heard / in the project' : 'heard', '', 'devices'),
    tile('Entities', ha.entities ?? '–', ha.stray ? `${ha.stray} stray` : 'in Home Assistant', ha.stray ? 'warn' : '', 'ha'),
    tile('Recorded', fmtCount(r.rows), r.oldest ? `since ${fmtDay(r.oldest)} · ${fmtSize(r.bytes)}` : 'nothing yet', '', 'stats'),
  ].join('');

  const open = model.findings.filter(x => !x.hidden);
  $('overview-findings').innerHTML = open.length
    ? open.slice(0, 9).map(x => `<div class="finding-row short"><span class="pill ${esc(x.severity)}">${esc(x.severity === 'info' ? 'note' : x.severity)}</span><div class="what"><b>${esc(x.title)}</b><div class="muted">${subjectHtml(x)} ${esc(x.detail)}</div></div></div>`).join('') +
      (open.length > 9 ? `<div class="muted" style="margin-top:8px">and ${open.length - 9} more</div>` : '')
    : `<div class="empty-state">${p.loaded ? 'Nothing found. The checks of the bus need telegrams: give it a day.' : 'No findings yet.'}</div>`;

  const load = model.load || [];
  bars($('overview-load'), load.map(([ts, n, peak]) => ({value: n, title: `${fmtClock(ts).slice(0, 5)}  ${n} telegrams, at most ${peak} in a second`})),
    load.length ? [fmtClock(load[0][0]).slice(0, 5), fmtClock(load[load.length - 1][0]).slice(0, 5)] : []);

  const mode = {ha: 'Through Home Assistant', routing: 'Routing', tunneling: 'Tunnelling (UDP)', tunneling_tcp: 'Tunnelling (TCP)'}[bus.mode] || '';
  const busLine = bus.mode === 'ha' ? '<span class="muted">none chosen</span>'
    : `<span class="dot ${bus.state === 'connected' ? 'ok' : bus.state === 'failed' ? 'error' : 'warning'}"></span>${esc(mode)}${(bus.choice || {}).host ? ' ' + esc(bus.choice.host) : ''} <span class="muted">${esc(bus.state === 'connected' ? 'as ' + bus.address : bus.error || bus.state)}</span>`;
  $('overview-links').innerHTML = '<table class="list"><tbody>' + [
    ['Own interface', busLine + ' <button class="link-btn" data-go="interface" style="margin-left:8px">Choose</button>'],
    ['Home Assistant', ha.loaded ? `<span class="dot ${ha.connected ? 'ok' : 'error'}"></span>${ha.connected ? 'connected to the bus' : 'not connected to the bus'} <span class="muted">as ${esc(ha.address || '?')} · xknx ${esc(ha.xknx || '')}</span>` : `<span class="dot error"></span>${esc(ha.error || 'not reachable')}`],
    ['ETS project', p.loaded ? `${esc(p.name || '')} <span class="muted">${p.modified ? 'changed ' + esc(String(p.modified).slice(0, 10)) : ''}${p.tool ? ' · ETS ' + esc(p.tool) : ''}</span>` : '<span class="pill warning">none</span>'],
  ].map(([k, v]) => `<tr><td class="sub" style="width:1%;white-space:nowrap">${k}</td><td>${v}</td></tr>`).join('') + '</tbody></table>';

  const byCount = list => list.filter(x => (x.stats || {}).count).sort((a, b) => b.stats.count - a.stats.count).slice(0, 8).map(x => ({address: x.address, count: x.stats.count}));
  topTable('overview-top-ga', byCount(model.addresses), 'No telegrams yet.');
  topTable('overview-top-src', byCount(model.devices), 'No telegrams yet.');
};
// What a finding is about, as something to click
function subjectHtml(f) {
  if (f.kind === 'ga' || f.kind === 'device') return ref(f.subject);
  // The text of a finding about an entity names the entity itself: only its address is added
  if (f.kind === 'entity') { const ga = f.subject.split('|')[1]; return ga ? ref(ga) : ''; }
  return '';
}

// ---------------------------------------------------------------- findings
const findingsSeverity = seg('findings-severity', () => TAB_RENDER.findings());
const findingsCategory = seg('findings-category', () => TAB_RENDER.findings());
$('findings-search').addEventListener('input', () => TAB_RENDER.findings());
const findingsOpen = new Set();
TAB_RENDER.findings = () => {
  const severity = findingsSeverity(), category = findingsCategory(), text = $('findings-search').value.trim().toLowerCase();
  const list = model.findings.filter(f =>
    (severity === 'hidden' ? f.hidden : !f.hidden && (!severity || f.severity === severity)) &&
    (!category || f.category === category) &&
    (!text || `${f.title} ${f.subject} ${f.detail} ${nameOf(f.subject)}`.toLowerCase().includes(text)));
  $('findings-count').textContent = `${list.length} of ${model.findings.length}`;
  const groups = new Map();
  for (const f of list) { if (!groups.has(f.code)) groups.set(f.code, []); groups.get(f.code).push(f); }
  const el = $('findings-list');
  if (!list.length) { el.innerHTML = `<div class="panel"><div class="empty-state">${model.findings.length ? 'Nothing matches.' : 'No findings.'}</div></div>`; return; }
  el.innerHTML = [...groups.entries()].map(([code, items]) => {
    const info = model.catalog[code] || {};
    const isOpen = findingsOpen.has(code) || groups.size <= 3 || items[0].severity === 'error';
    return `<details class="finding-group" data-code="${esc(code)}"${isOpen ? ' open' : ''}><summary><span class="pill ${esc(items[0].severity)}">${esc(items[0].severity === 'info' ? 'note' : items[0].severity)}</span><span>${esc(info.title || code)}</span><span class="muted hide-sm">${esc(items[0].category)}</span><span class="n">${items.length}</span></summary>` +
      `<div class="fix">${esc(info.fix || '')}</div>` +
      items.slice(0, 200).map(f => `<div class="finding-row"><div class="subject">${subjectHtml(f)}</div><div class="what">${esc(f.detail)}</div><button type="button" class="link-btn" data-hide="${esc(f.id)}" data-hidden="${f.hidden ? '' : '1'}">${f.hidden ? 'Show again' : 'Hide'}</button></div>`).join('') +
      (items.length > 200 ? `<div class="finding-row muted">and ${items.length - 200} more</div>` : '') + '</details>';
  }).join('');
};
$('findings-list').addEventListener('toggle', e => {
  const group = e.target.closest('.finding-group'); if (!group) return;
  if (group.open) findingsOpen.add(group.dataset.code); else findingsOpen.delete(group.dataset.code);
}, true);
$('findings-list').addEventListener('click', async e => {
  const b = e.target.closest('[data-hide]'); if (!b) return;
  await attempt(() => api('findings/hide', {json: {id: b.dataset.hide, hidden: Boolean(b.dataset.hidden)}}));
});

// ---------------------------------------------------------------- group addresses
const addressesFilter = seg('addresses-filter', () => TAB_RENDER.addresses());
$('addresses-search').addEventListener('input', () => TAB_RENDER.addresses());
TAB_RENDER.addresses = () => {
  const filter = addressesFilter(), text = $('addresses-search').value.trim().toLowerCase();
  const rows = model.addresses.filter(a => {
    const seen = (a.stats || {}).count;
    if (filter === 'issues' && !issuesOf(a.address).length) return false;
    if (filter === 'seen' && !seen) return false;
    if (filter === 'silent' && (seen || !a.known)) return false;
    if (filter === 'ha' && !(a.users || []).length) return false;
    if (filter === 'unknown' && a.known) return false;
    return !text || `${a.address} ${a.name} ${a.dpt || ''} ${a.range || ''} ${(a.users || []).map(u => u.entity_id).join(' ')}`.toLowerCase().includes(text);
  });
  $('addresses-count').textContent = `${rows.length} of ${model.addresses.length}`;
  table('addresses-table', [
    {key: 'address', label: 'Address', cls: 'addr', sort: a => addressKey(a.address), html: a => issueDot(a.address) + esc(a.address)},
    {key: 'name', label: 'Name', sort: a => (a.name || '').toLowerCase(), html: a => `<span class="nm">${esc(a.name || '')}</span>${a.known ? '' : '<span class="pill warning">not in the project</span>'}<small>${esc(a.range || '')}</small>`},
    {key: 'dpt', label: 'Type', cls: 'addr', sort: a => addressKey(a.dpt || '999'), html: a => a.dpt ? esc(a.dpt) : a.known ? '<span class="muted">–</span>' : ''},
    {key: 'value', label: 'Last value', html: a => valueHtml((a.stats || {}).last_value)},
    {key: 'last', label: 'Last seen', cls: 'sub', sort: a => (a.stats || {}).last || 0, html: a => (a.stats || {}).last ? esc(fmtAgo(a.stats.last)) : ''},
    {key: 'count', label: 'Telegrams', cls: 'num', sort: a => (a.stats || {}).count || 0, html: a => (a.stats || {}).count || ''},
    {key: 'links', label: 'Objects', cls: 'num hide-md', sort: a => (a.objects || []).length, html: a => a.known ? (a.objects || []).length : ''},
    {key: 'ha', label: 'Home Assistant', cls: 'sub hide-md', sort: a => (a.users || []).length, html: a => (a.users || []).slice(0, 2).map(u => esc(u.entity_id || u.name)).join('<br>') + ((a.users || []).length > 2 ? ` +${a.users.length - 2}` : '')},
  ], rows, {sort: 'address', onRow: a => openDrawer(a.address), empty: model.addresses.length ? 'Nothing matches.' : 'No group addresses yet: there is no project and no telegram was seen.'});
};

// ---------------------------------------------------------------- devices
const devicesFilter = seg('devices-filter', () => TAB_RENDER.devices());
const devicesView = seg('devices-view', () => TAB_RENDER.devices());
$('devices-search').addEventListener('input', () => TAB_RENDER.devices());
function reachHtml(d) {
  const r = d.reach;
  if (!r) return '';
  if (!r.reachable) return '<span class="pill error">no answer</span>';
  return '<span class="pill ok">answers</span>' + (r.progmode ? '<span class="pill warning">prog. mode</span>' : '') + (r.load_state && r.load_state !== 'loaded' ? `<span class="pill error">${esc(r.load_state)}</span>` : '');
}
TAB_RENDER.devices = () => {
  renderJob();
  const filter = devicesFilter(), text = $('devices-search').value.trim().toLowerCase();
  const rows = model.devices.filter(d => {
    if (filter === 'issues' && !issuesOf(d.address).length) return false;
    if (filter === 'silent' && ((d.stats || {}).count || !d.known)) return false;
    if (filter === 'unknown' && d.known) return false;
    return !text || `${d.address} ${d.name || ''} ${d.maker || ''} ${d.product || ''} ${d.room || ''}`.toLowerCase().includes(text);
  });
  const view = devicesView();
  $('devices-list').hidden = view !== 'list'; $('devices-lines').hidden = view !== 'lines'; $('devices-graph').hidden = view !== 'graph';
  if (view === 'lines') { renderLines(rows); return; }
  if (view === 'graph') { renderGraph(rows); return; }
  table('devices-table', [
    {key: 'address', label: 'Address', cls: 'addr', sort: d => addressKey(d.address), html: d => issueDot(d.address) + esc(d.address)},
    {key: 'name', label: 'Device', sort: d => (d.name || '').toLowerCase(), html: d => `<span class="nm">${esc(d.name || '')}</span>${d.known ? '' : d.own ? '' : '<span class="pill warning">not in the project</span>'}<small>${esc([d.maker, d.product].filter(Boolean).join(' · '))}</small>`},
    {key: 'room', label: 'Room', cls: 'sub hide-md', sort: d => (d.room || '').toLowerCase(), html: d => esc((d.room || '').split(' › ').pop())},
    {key: 'last', label: 'Last heard', cls: 'sub', sort: d => (d.stats || {}).last || 0, html: d => (d.stats || {}).last ? esc(fmtAgo(d.stats.last)) : ''},
    {key: 'count', label: 'Telegrams', cls: 'num', sort: d => (d.stats || {}).count || 0, html: d => (d.stats || {}).count || ''},
    {key: 'reach', label: 'Asked directly', sort: d => d.reach ? (d.reach.reachable ? 1 : 0) : 2, html: reachHtml},
    {key: 'mask', label: 'Mask', cls: 'sub hide-md', html: d => esc((d.reach || {}).mask || '')},
    {key: 'documents', label: 'Docs', cls: 'num hide-md', sort: d => d.documents || 0, html: d => d.documents || ''},
  ], rows, {sort: 'address', onRow: d => openDrawer(d.address), empty: model.devices.length ? 'Nothing matches.' : 'No devices yet: there is no project and no telegram was seen.'});
};
// The installation as it is wired: every line with its devices on it. A device lights up when it sends.
function renderLines(rows) {
  const byLine = new Map();
  for (const d of rows) { const line = d.line || '?'; if (!byLine.has(line)) byLine.set(line, []); byLine.get(line).push(d); }
  const keys = [...byLine.keys()].sort((a, b) => addressKey(a).localeCompare(addressKey(b)));
  $('devices-lines').innerHTML = keys.length ? keys.map(line => {
    const info = (model.project.lines || {})[line] || {}, devices = byLine.get(line).sort((a, b) => addressKey(a.address).localeCompare(addressKey(b.address)));
    const sent = devices.reduce((n, d) => n + ((d.stats || {}).count || 0), 0);
    const heard = devices.filter(d => (d.stats || {}).count).length, dead = devices.filter(d => d.reach && !d.reach.reachable).length;
    return `<div class="panel"><div class="panel-body"><div class="title"><span><span class="mono">${esc(line)}</span> ${esc(info.name || '')}</span><span class="hint">${esc(info.medium || '')}${info.medium ? ' · ' : ''}${devices.length} devices, ${heard} heard${dead ? `, ${dead} not answering` : ''} · ${fmtCount(sent)} telegrams</span></div><div class="line">` +
      devices.map(d => {
        const found = issuesOf(d.address);
        const tone = d.reach && !d.reach.reachable ? 'error' : found.length ? worst(found) : (d.stats || {}).count || (d.reach && d.reach.reachable) ? 'ok' : '';
        return `<button type="button" class="node ${tone}${d.known || d.own ? '' : ' unknown'}" data-ref="${esc(d.address)}" data-node="${esc(d.address)}" title="${esc(found.map(f => f.title).join('\n'))}"><b class="mono">${esc(d.address)}</b><span>${esc(d.name || (d.known ? '' : 'not in the project'))}</span><small>${(d.stats || {}).count ? fmtCount(d.stats.count) + ' sent' : 'silent'}</small></button>`;
      }).join('') + '</div></div></div>';
  }).join('') : '<div class="panel"><div class="empty-state">No devices.</div></div>';
}
// A telegram came in: its sender blinks on the lines
function pulseNodes(items) {
  if (activeTab !== 'devices' || $('devices-lines').hidden) return;
  for (const src of new Set(items.map(t => t.src))) {
    const node = $('devices-lines').querySelector(`[data-node="${CSS.escape(src)}"]`);
    if (node) { node.classList.remove('pulse'); void node.offsetWidth; node.classList.add('pulse'); }
  }
}
function renderJob() {
  const job = state.job || {}, box = $('job-box');
  const connected = (state.bus || {}).state === 'connected';
  ['devices-check', 'devices-scan', 'devices-progmode', 'devices-serial'].forEach(id => { $(id).disabled = !connected || job.running; });
  if (!connected && !job.kind) {
    box.innerHTML = '<div class="banner">Asking devices directly needs an interface of the app\'s own. <button class="link-btn" data-go="interface">Choose one</button></div>';
    return;
  }
  if (!job.kind) { box.innerHTML = ''; return; }
  const share = Math.round(job.done / Math.max(1, job.total) * 100);
  // Worth naming: what is in programming mode, what did not answer, what the project does not have
  const notable = (job.results || []).filter(r => job.kind === 'progmode' || !r.reachable || !deviceIndex[r.address] || !deviceIndex[r.address].known);
  box.innerHTML = `<div class="panel"><div class="panel-body"><div class="title"><span>${esc(job.title)}</span><span class="hint">${job.running ? `${job.done} of ${job.total} · <button class="link-btn" id="job-cancel">Stop</button>` : esc(job.error || 'done')}</span></div>` +
    (job.running ? `<div class="progress"><i style="width:${share}%"></i></div>` : '') +
    `<div class="muted">${job.kind === 'progmode' ? (job.running ? 'Listening for three seconds…' : `${job.found} in programming mode`) : `${job.found} answered${job.running ? ' so far' : `, ${Math.max(0, job.results.length - job.found)} did not`}`}` +
    (!job.running && notable.length ? ': ' + notable.slice(0, 12).map(r => `${ref(r.address)}${r.reachable ? '' : ' no answer'}`).join(', ') : '') + '</div></div></div>';
}
$('job-box').addEventListener('click', e => { if (e.target.closest('#job-cancel')) attempt(() => api('job/cancel', {method: 'POST'})); });
$('devices-check').addEventListener('click', () => attempt(() => api('job', {json: {kind: 'project'}})));
$('devices-progmode').addEventListener('click', () => attempt(() => api('job', {json: {kind: 'progmode'}})));
$('devices-serial').addEventListener('click', async () => {
  const answer = await ask({title: 'Find a device by its serial number', text: 'The serial number is printed on the device and shown by Read details. The whole bus is asked which device has it.', fields: [{name: 'serial', label: 'Serial number', placeholder: '0083:65FE3897'}], ok: 'Find'});
  if (!answer || !answer.serial) return;
  const out = await attempt(() => api('serial', {json: {serial: answer.serial}}));
  if (out === undefined) return;
  if (out.address) { toast(`It is ${out.address}`); openDrawer(out.address); } else toast('No device answered with that serial number', true);
});
$('devices-scan').addEventListener('click', async () => {
  const lines = [...new Set(model.devices.map(d => d.line).filter(Boolean))].sort((a, b) => addressKey(a).localeCompare(addressKey(b)));
  const answer = await ask({title: 'Scan a line', text: 'Every address of the line is asked whether a device is there: 256 addresses, a few minutes. It finds devices the project does not have.', fields: [{name: 'line', label: 'Line (area.line)', value: lines[0] || '1.1', placeholder: '1.1'}], ok: 'Scan'});
  if (answer) attempt(() => api('job', {json: {kind: 'line', target: answer.line}}));
});

// ---------------------------------------------------------------- Home Assistant
const haFilter = seg('ha-filter', () => TAB_RENDER.ha());
$('ha-search').addEventListener('input', () => TAB_RENDER.ha());
const entityIssues = e => (e.addresses || []).flatMap(a => issuesOf(`${e.entity_id || e.name}|${a.ga}`)).concat(issuesOf(e.entity_id || e.name));
TAB_RENDER.ha = () => {
  const ha = state.ha || {}, canRemove = state.allow_remove;
  const withIssues = model.entities.filter(e => entityIssues(e).length).length;
  $('ha-tiles').innerHTML = [
    tile('KNX integration', ha.loaded ? (ha.connected ? 'Connected' : 'No bus') : 'Missing', ha.loaded ? `as ${esc(ha.address || '?')} · xknx ${esc(ha.xknx || '')}` : esc(ha.error || ''), ha.loaded && ha.connected ? 'ok' : 'bad'),
    tile('Entities', model.entities.length, `${model.entities.filter(e => e.source === 'ui').length} from the UI, ${model.entities.filter(e => e.source === 'yaml').length} from YAML`),
    tile('With findings', withIssues, 'held against the project', withIssues ? 'warn' : 'ok'),
    tile('Stray', model.stray_entities.length + model.stray_devices.length, 'left behind in Home Assistant', model.stray_entities.length ? 'warn' : ''),
    tile('Sent to the bus', Object.keys(model.exposes || {}).length, 'entities exposed to KNX'),
  ].join('');

  const stray = model.stray_entities.map(e => ({kind: 'stray_entity', id: e.entity_id, name: e.name, what: `${e.platform} entity`, note: e.restored ? 'kept by Home Assistant, no longer provided by KNX' : 'not in the KNX configuration any more'}))
    .concat(model.stray_devices.map(d => ({kind: 'stray_device', id: d.id, name: d.name, what: 'device', note: 'has no entities'})));
  $('ha-stray').innerHTML = stray.length ? '<div class="panel flush stray"><div class="panel-head"><span>Stray data</span><span class="hint">' +
    (canRemove ? 'removing takes it out of Home Assistant for good' : 'removing is switched off in the app\'s options') + '</span></div><table class="list"><tbody>' +
    stray.map(s => `<tr><td class="addr">${esc(s.kind === 'stray_device' ? '' : s.id)}</td><td><span class="nm">${esc(s.name)}</span><small>${esc(s.what)} · ${esc(s.note)}</small></td><td class="acts">${canRemove ? `<button type="button" class="link-btn danger" data-remove="${esc(s.kind)}" data-id="${esc(s.id)}" data-name="${esc(s.name)}">Remove</button>` : ''}</td></tr>`).join('') + '</tbody></table></div>' : '';

  const filter = haFilter(), text = $('ha-search').value.trim().toLowerCase();
  const rows = model.entities.filter(e => {
    if (filter === 'issues' && !entityIssues(e).length) return false;
    if ((filter === 'ui' || filter === 'yaml') && e.source !== filter) return false;
    return !text || `${e.entity_id} ${e.name} ${e.platform} ${e.addresses.map(a => a.ga + ' ' + nameOf(a.ga)).join(' ')}`.toLowerCase().includes(text);
  });
  $('ha-count').textContent = `${rows.length} of ${model.entities.length}`;
  table('ha-table', [
    {key: 'entity', label: 'Entity', sort: e => e.entity_id || e.name, html: e => { const found = entityIssues(e); return (found.length ? `<span class="dot ${worst(found)}" title="${esc(found.map(f => f.title).join('\n'))}"></span>` : '') + `<span class="mono">${esc(e.entity_id || '(no entity id)')}</span><small>${esc(e.name || '')}</small>`; }},
    {key: 'source', label: 'From', cls: 'sub', sort: e => e.source, html: e => `<span class="pill">${esc(e.source === 'ui' ? 'UI' : 'YAML')}</span>${e.disabled ? '<span class="pill">disabled</span>' : ''}`},
    {key: 'state', label: 'State', cls: 'sub hide-md', sort: e => String(e.state), html: e => e.state === 'unavailable' ? '<span class="pill warning">unavailable</span>' : esc(e.state ?? '')},
    {key: 'addresses', label: 'Group addresses', html: e => e.addresses.map(a => `<div>${issueDot(`${e.entity_id || e.name}|${a.ga}`)}${ref(a.ga)} <span class="muted">${esc(a.role || '')}${a.use === 'state' ? ' · listens' : a.use === 'passive' ? ' · passive' : ''} ${esc(nameOf(a.ga) ? '· ' + nameOf(a.ga) : gaIndex[a.ga] && gaIndex[a.ga].known ? '' : '· not in the project')}</span></div>`).join('')},
    {key: 'acts', label: '', cls: 'acts', html: e => canRemove && e.source === 'ui' && e.entity_id ? `<button type="button" class="link-btn danger" data-remove="entity" data-id="${esc(e.entity_id)}" data-name="${esc(e.name)}">Remove</button>` : ''},
  ], rows, {sort: 'entity', empty: ha.loaded ? (model.entities.length ? 'Nothing matches.' : 'Home Assistant has no KNX entities.') : 'The KNX integration is not there.'});
};
$('tab-ha').addEventListener('click', async e => {
  const b = e.target.closest('[data-remove]'); if (!b) return;
  const what = {stray_entity: 'the stray entity', stray_device: 'the device', entity: 'the entity and its KNX configuration'}[b.dataset.remove];
  const ok = await ask({title: `Remove ${b.dataset.name}?`, text: `This removes ${what} from Home Assistant. It cannot be undone from here.`, ok: 'Remove', danger: true});
  if (ok) await attempt(() => api('remove', {json: {kind: b.dataset.remove, id: b.dataset.id}}), 'Removed');
});

// ---------------------------------------------------------------- interface
TAB_RENDER.interface = () => {
  const bus = state.bus || {}, choice = bus.choice || {mode: 'ha'}, counters = bus.counters || {};
  const modes = {ha: 'Home Assistant only', routing: 'Routing', tunneling: 'Tunnelling (UDP)', tunneling_tcp: 'Tunnelling (TCP)'};
  const stateText = {off: 'not used', connecting: 'connecting…', connected: 'connected', failed: 'cannot connect'}[bus.state] || bus.state;
  $('interface-current').innerHTML = kv([
    ['Telegrams come from', esc(state.source === 'bus' ? 'the app\'s own interface' : state.source === 'ha' ? 'Home Assistant' : 'nowhere right now')],
    ['Chosen', esc(modes[choice.mode] || choice.mode) + (choice.name ? ` <span class="muted">${esc(choice.name)}</span>` : '')],
    ['Address', choice.host ? `<span class="mono">${esc(choice.host)}:${esc(choice.port)}</span>` : choice.mode === 'routing' ? `<span class="mono">${esc(choice.multicast || '224.0.23.12')}</span>` : ''],
    ['State', choice.mode === 'ha' ? '' : `<span class="pill ${bus.state === 'connected' ? 'ok' : bus.state === 'failed' ? 'error' : 'warning'}">${esc(stateText)}</span> ${esc(bus.error || '')}`],
    ['On the bus as', bus.address ? `<span class="mono">${esc(bus.address)}</span>` : ''],
    ['Connected', bus.connected_since ? esc(fmtAgo(bus.connected_since)) : ''],
    ['Link', counters.type ? esc(counters.type) + (counters.drops ? ` <span class="pill warning">lost ${counters.drops} times</span>` : ' <span class="muted">never lost since the app started</span>') : ''],
    ['Frames', counters.type ? `${fmtCount(counters.frames_in)} in, ${fmtCount(counters.frames_out)} out` + (counters.frames_in_error || counters.frames_out_error ? ` <span class="pill error">${counters.frames_in_error} in and ${counters.frames_out_error} out failed</span>` : ' <span class="muted">none failed</span>') : ''],
    ['Data Secure', counters.secure_undecoded ? `<span class="pill warning">${counters.secure_undecoded} telegrams could not be read</span>` : ''],
  ]) + (choice.mode !== 'ha' ? '<div class="toolbar" style="margin:12px 0 0"><button type="button" class="btn" data-use=\'{"mode":"ha"}\'>Use Home Assistant only</button></div>' : '');

  const list = bus.interfaces || [];
  $('interface-scan').textContent = bus.scanning ? 'Searching…' : 'Search';
  $('interface-scan').disabled = bus.scanning;
  $('interface-list').innerHTML = list.length ? list.map(g => {
    const base = {name: g.name, host: g.host, port: g.port, local_ip: g.local_ip, multicast_port: g.multicast_port};
    const chosen = mode => choice.mode === mode && (mode === 'routing' ? (choice.multicast_port || 3671) === g.multicast_port && choice.name === g.name : choice.host === g.host);
    const use = (mode, label, extra) => `<button type="button" class="btn${chosen(mode) ? ' primary' : ''}" data-use="${esc(JSON.stringify({...base, mode, ...extra}))}">${label}</button>`;
    const acts = [];
    if (g.routing && !g.routing_secure) acts.push(use('routing', 'Use routing', {multicast: g.multicast || '224.0.23.12'}));
    if (g.tunneling_tcp && !g.tunneling_secure) acts.push(use('tunneling_tcp', 'Use tunnelling (TCP)'));
    if (g.tunneling && !g.tunneling_secure) acts.push(use('tunneling', 'Use tunnelling (UDP)'));
    return `<div class="iface"><h4>${esc(g.name)}</h4><div class="muted mono">${esc(g.host)}:${esc(g.port)}${g.address ? ' · ' + esc(g.address) : ''}${g.mac ? ' · ' + esc(g.mac) : ''}</div><div style="margin-top:6px">` +
      (g.routing ? `<span class="pill">routing${g.routing_secure ? ' (secure)' : ''}</span>` : '') +
      (g.tunneling ? `<span class="pill">tunnelling${g.tunneling_tcp ? ', also TCP' : ''}${g.tunneling_secure ? ' (secure)' : ''}</span>` : '') +
      (g.slots ? `<span class="pill ${g.slots_free ? 'ok' : 'warning'}">${g.slots_free} of ${g.slots} tunnels free</span>` : '') +
      `</div><div class="acts">${acts.join('') || '<span class="muted">Only KNX Secure: not supported. Use Home Assistant\'s connection.</span>'}</div></div>`;
  }).join('') : `<div class="empty-state">${bus.scanning ? 'Searching the network…' : bus.scanned_at ? 'No KNX IP interface or router answered. It may be on another network: enter its address by hand.' : 'Press Search to look for KNX IP interfaces and routers on the network.'}</div>`;
};
$('interface-scan').addEventListener('click', () => attempt(() => api('interface/scan', {method: 'POST'})));
$('tab-interface').addEventListener('click', e => {
  const b = e.target.closest('[data-use]'); if (!b) return;
  attempt(() => api('interface/select', {json: JSON.parse(b.dataset.use)}), 'Saved. The pages start afresh for this interface.');
});
$('interface-form').addEventListener('submit', e => {
  e.preventDefault();
  attempt(() => api('interface/select', {json: {mode: $('interface-type').value, host: $('interface-host').value.trim(), port: $('interface-port').value.trim() || 3671}}), 'Saved');
});
