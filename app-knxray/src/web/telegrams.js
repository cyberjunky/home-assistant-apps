// The Monitor page: the telegrams as they come in (Live), and the recording (Archive).
'use strict';

const LIVE_MAX = 5000, LIVE_ROWS = 300;
let live = [];              // newest last
let livePaused = false, liveDirty = false, liveLastId = 0;
let monitorMode = 'live';
let archive = {items: [], total: 0, next: null, busy: false, ga: ''};

function onTelegrams(items) {
  for (const t of items) { if (t.id > liveLastId) { live.push(t); liveLastId = t.id; } }
  if (live.length > LIVE_MAX) live = live.slice(-LIVE_MAX);
  liveDirty = true;
  if (typeof chartLive === 'function') chartLive(items);
  pulseNodes(items);
  if (typeof pulseGraph === 'function') pulseGraph(items);
}
async function loadLive() {
  try { onTelegrams((await api('telegrams?' + query({after: liveLastId}))).items); } catch (e) { /* the socket brings the next ones */ }
}
function matches(t, text, type) {
  if (type && t.type !== type) return false;
  if (!text) return true;
  return `${t.src} ${nameOf(t.src)} ${t.dst} ${nameOf(t.dst)} ${t.text ?? ''} ${t.raw} ${t.dpt || ''}`.toLowerCase().includes(text);
}
// How long after the telegram before it (the row below): what belongs together shows as a few milliseconds
function fmtDelta(s) {
  if (s == null) return '';
  if (s < 1) return `+${Math.round(s * 1000)} ms`;
  if (s < 120) return `+${s.toFixed(1)} s`;
  return `+${fmtSpan(s)}`;
}
const telegramColumns = withDay => [
  {key: 'time', label: 'Time', cls: 'addr', html: t => withDay ? `<span title="${fmtClock(t.ts, true)}">${esc(fmtDay(t.ts).slice(5))} ${fmtClock(t.ts, true)}</span>` : fmtClock(t.ts, true)},
  {key: 'delta', label: 'After', cls: 'addr sub hide-md', html: t => esc(fmtDelta(t.after))},
  {key: 'src', label: 'From', html: t => issueDot(t.src) + refNamed(t.src)},
  {key: 'dst', label: 'To', html: t => issueDot(t.dst) + refNamed(t.dst)},
  {key: 'type', label: 'Kind', html: kindHtml},
  {key: 'value', label: 'Value', html: valueHtml},
  {key: 'dpt', label: 'Type', cls: 'addr sub hide-md', html: t => esc(t.dpt || '')},
  {key: 'raw', label: 'Payload', cls: 'addr sub hide-md', html: t => esc(t.raw || '')},
];

// Rows are newest first: each gets the time since the one shown below it
function withDeltas(rows) { rows.forEach((t, i) => { t.after = i + 1 < rows.length ? Math.max(0, t.ts - rows[i + 1].ts) : null; }); }

const monitorType = seg('monitor-type', () => monitorChanged());
seg('monitor-mode', mode => {
  monitorMode = mode;
  $('archive-range').hidden = $('archive-csv').hidden = mode !== 'archive';
  $('monitor-pause').hidden = mode === 'archive';
  $('monitor-list').hidden = mode === 'timeline' || mode === 'wire'; $('monitor-timeline').hidden = mode !== 'timeline';
  $('monitor-wire').hidden = mode !== 'wire';
  $('monitor-search').hidden = $('monitor-type').hidden = mode === 'wire';
  $('monitor-count').hidden = mode === 'wire'; if (mode === 'wire') $('monitor-pause').hidden = true;
  if (mode !== 'archive') $('archive-more').hidden = true;
  if (mode === 'live') archive.ga = '';
  monitorChanged();
});
let searchTimer = null;
$('monitor-search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(monitorChanged, monitorMode === 'archive' ? 350 : 0); });
$('archive-range').addEventListener('change', () => monitorChanged());
$('monitor-pause').addEventListener('click', () => {
  livePaused = !livePaused;
  $('monitor-pause').textContent = livePaused ? 'Resume' : 'Pause';
  $('monitor-pause').classList.toggle('primary', livePaused);
  if (!livePaused) renderLive();
});
function monitorChanged() { if (monitorMode === 'wire') renderWire(); else if (monitorMode === 'archive') loadArchive(true); else if (monitorMode === 'timeline') drawTimeline(); else renderLive(); }

function renderLive() {
  liveDirty = false;
  const text = $('monitor-search').value.trim().toLowerCase(), type = monitorType();
  const rows = [];
  for (let i = live.length - 1; i >= 0 && rows.length < LIVE_ROWS; i--) if (matches(live[i], text, type)) rows.push(live[i]);
  withDeltas(rows);
  $('monitor-count').textContent = `${rows.length}${rows.length === LIVE_ROWS ? '+' : ''} shown, ${live.length} in memory`;
  $('archive-more').hidden = true;
  table('monitor-table', telegramColumns(false), rows, {tight: true, limit: LIVE_ROWS, empty: live.length ? 'Nothing matches.' : 'No telegrams yet. They appear here as they come in.'});
}

function archiveQuery() {
  const range = Number($('archive-range').value), now = Date.now() / 1000;
  const text = $('monitor-search').value.trim();
  // A group address typed in full is looked up as that address, not as text
  const asGa = /^\d{1,2}\/\d{1,2}\/\d{1,3}$/.test(text) ? text : archive.ga;
  return {since: range ? now - range : 0, until: now, type: monitorType(), ga: asGa, q: asGa ? '' : text};
}
async function loadArchive(fresh) {
  if (archive.busy) return;
  archive.busy = true;
  if (fresh) { archive.items = []; archive.next = null; }
  const q = archiveQuery();
  $('archive-csv').href = 'api/archive.csv?' + query(q);
  try {
    const out = await api('archive?' + query({...q, cursor: fresh ? '' : archive.next, limit: 300}));
    archive.items = archive.items.concat(out.items); archive.total = out.total; archive.next = out.next;
  } catch (e) { toast(e.message, true); }
  archive.busy = false;
  if (monitorMode !== 'archive') return;
  $('monitor-count').textContent = `${archive.items.length} of ${fmtCount(archive.total)} recorded`;
  $('archive-more').hidden = !archive.next;
  withDeltas(archive.items);
  table('monitor-table', telegramColumns(true), archive.items, {tight: true, limit: 100000, empty: 'Nothing recorded matches.'});
}
$('archive-more-btn').addEventListener('click', () => loadArchive(false));
// From a group address's details: everything recorded for it
function showArchive(address) {
  showTab('monitor');
  $('monitor-search').value = address;
  archive.ga = address.includes('/') ? address : '';
  $('monitor-mode').querySelector('[data-v=archive]').click();
}

TAB_RENDER.monitor = () => { if (monitorMode === 'wire') renderWire(); else if (monitorMode === 'timeline') drawTimeline(); else if (monitorMode === 'live') { if (!livePaused) renderLive(); } else if (!archive.items.length) loadArchive(true); };
setInterval(() => { if (activeTab === 'monitor' && monitorMode === 'live' && liveDirty && !livePaused) renderLive(); }, 400);

// ---------------------------------------------------------------- the wire: bus monitor mode
let wireFrames = [], wireLastId = 0, wireBusy = false;
const ACK_PILL = {ack: '<span class="pill ok">ack</span>', none: '<span class="pill warning">none</span>', busy: '<span class="pill error">busy</span>', nak: '<span class="pill error">refused</span>', 'nak+busy': '<span class="pill error">refused</span>'};
function renderWire() {
  const bm = state.busmon || {}, bus = state.bus || {}, on = bm.state === 'on', counts = bm.counts || {};
  const select = $('wire-interface'), options = [];
  if ((bus.choice || {}).host) options.push([`${bus.choice.host}:${bus.choice.port || 3671}`, `${bus.choice.name || 'The chosen interface'} (${bus.choice.host})`]);
  for (const g of bus.interfaces || []) if (g.tunneling && !options.some(o => o[0] === `${g.host}:${g.port}`)) options.push([`${g.host}:${g.port}`, `${g.name} (${g.host})`]);
  options.push(['', 'Another address…']);
  const wanted = options.map(o => o[0]).join('|');
  if (select.dataset.options !== wanted) {
    select.dataset.options = wanted;
    select.innerHTML = options.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  }
  $('wire-host').hidden = select.value !== '';
  $('wire-start').hidden = on; $('wire-stop').hidden = !on;
  $('wire-start').disabled = bm.state === 'connecting';
  $('wire-state').innerHTML = on ? `<span class="dot ok"></span>listening on ${esc(bm.host)}, until ${esc(fmtClock(bm.until).slice(0, 5))}`
    : bm.state === 'connecting' ? 'connecting…' : bm.state === 'failed' ? `<span class="dot error"></span>${esc(bm.error)}` : (bus.interfaces || []).length || (bus.choice || {}).host ? 'off' : 'off · search for interfaces on the Interface page, or enter an address';
  const share = n => counts.data ? `${Math.round(n / counts.data * 1000) / 10}% of the telegrams` : '';
  $('wire-tiles').innerHTML = counts.frames ? [
    tile('Frames', fmtCount(counts.frames), `${fmtCount(counts.data)} telegrams`),
    tile('Acknowledged', fmtCount(counts.acked), share(counts.acked), counts.data && counts.acked >= counts.data * 0.9 ? 'ok' : ''),
    tile('Sent again', counts.repeated, share(counts.repeated), counts.repeated ? 'warn' : 'ok'),
    tile('Nobody answered', counts.unanswered, share(counts.unanswered), counts.unanswered ? 'warn' : 'ok'),
    tile('Busy or refused', counts.busy + counts.nak, 'a receiver could not take it', counts.busy + counts.nak ? 'bad' : 'ok'),
    tile('Damaged', counts.errors + counts.bad_checksum, 'frame, bit, parity or checksum', counts.errors + counts.bad_checksum ? 'bad' : 'ok'),
    tile('Missed', counts.lost, 'the interface could not keep up', counts.lost ? 'warn' : 'ok'),
  ].join('') : '';
  const rows = wireFrames.slice(-400).reverse();
  withDeltas(rows);
  table('wire-table', [
    {key: 'time', label: 'Time', cls: 'addr', html: f => fmtClock(f.ts, true)},
    {key: 'delta', label: 'After', cls: 'addr sub hide-md', html: f => esc(fmtDelta(f.after))},
    {key: 'src', label: 'From', html: f => f.src ? issueDot(f.src) + refNamed(f.src) : ''},
    {key: 'dst', label: 'To', html: f => f.dst ? (f.group ? issueDot(f.dst) + refNamed(f.dst) : ref(f.dst)) : ''},
    {key: 'what', label: 'What', html: f => f.kind !== 'data' ? `<span class="pill">${esc(f.kind)}</span>` : (f.type ? typePill(f.type) : `<span class="pill">${esc(f.service || 'data')}</span>`) +
      (f.repeated ? '<span class="pill warning">sent again</span>' : '') + (f.checksum_ok === false ? '<span class="pill error">checksum</span>' : '') +
      (f.frame_error || f.bit_error || f.parity_error ? '<span class="pill error">damaged</span>' : '')},
    {key: 'value', label: 'Value', html: f => f.type && f.type !== 'read' ? valueHtml({type: f.type, text: f.text, unit: f.unit, raw: f.payload, dpt: f.dpt}) : ''},
    {key: 'ack', label: 'Answer', html: f => ACK_PILL[f.ack] || ''},
    {key: 'flags', label: 'Priority, hops', cls: 'sub hide-md', html: f => f.kind === 'data' ? `${esc(f.priority)}, ${esc(f.hops)}` : ''},
    {key: 'raw', label: 'Frame', cls: 'addr sub hide-md', html: f => esc(f.raw)},
  ], rows, {tight: true, limit: 400, empty: on ? 'Listening… frames appear as they pass.' : 'Start bus monitor mode to see the frames on the line.'});
}
async function pollWire() {
  if (wireBusy || activeTab !== 'monitor' || monitorMode !== 'wire') return;
  const bm = state.busmon || {};
  if (bm.state !== 'on' && (bm.last_id || 0) <= wireLastId) return;
  wireBusy = true;
  try {
    const out = await api('busmon?' + query({after: wireLastId}));
    // A new run starts counting again
    if (out.frames.length && out.frames[0].id <= wireLastId) wireFrames = [];
    if ((out.status.last_id || 0) < wireLastId) { wireFrames = []; wireLastId = 0; }
    for (const f of out.frames) { wireFrames.push(f); wireLastId = f.id; }
    if (wireFrames.length > 3000) wireFrames = wireFrames.slice(-3000);
    renderWire();
  } catch (e) { /* the next tick tries again */ }
  wireBusy = false;
}
setInterval(pollWire, 1000);
$('wire-interface').addEventListener('change', () => { $('wire-host').hidden = $('wire-interface').value !== ''; });
$('wire-form').addEventListener('submit', async e => {
  e.preventDefault();
  const picked = $('wire-interface').value || $('wire-host').value.trim();
  const [host, port] = picked.includes(':') ? picked.split(':') : [picked, 3671];
  wireFrames = []; wireLastId = 0;
  await attempt(() => api('busmon/start', {json: {host, port, minutes: $('wire-minutes').value}}));
});
$('wire-stop').addEventListener('click', () => attempt(() => api('busmon/stop', {method: 'POST'})));
