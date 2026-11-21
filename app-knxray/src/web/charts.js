// Charts (the recorded values of up to four group addresses) and Statistics (how busy the bus is, who
// talks most, when the app was listening). Both read from the recording.
'use strict';

// ---------------------------------------------------------------- charts
const CHART_MAX = 4, CHART_COLORS = ['--s1', '--s2', '--s3', '--s4'];
let chartAddresses = [];
try { chartAddresses = JSON.parse(localStorage.getItem('knxray_charts') || '[]').filter(a => typeof a === 'string').slice(0, CHART_MAX); } catch (e) { /* only a convenience */ }
let chartData = {};     // address -> the answer of api/series
let chartPlot = null, chartTimer = null, chartOptionsAt = 0;
const chartRange = seg('chart-range', () => loadCharts());

function saveCharts() { try { localStorage.setItem('knxray_charts', JSON.stringify(chartAddresses)); } catch (e) { /* only a convenience */ } }
function addChart(address) {
  if (!/^\d{1,2}\/\d{1,2}\/\d{1,3}$/.test(address)) { toast('Not a group address', true); return; }
  if (chartAddresses.includes(address)) return;
  if (chartAddresses.length >= CHART_MAX) chartAddresses.shift();
  chartAddresses.push(address); saveCharts();
  if (activeTab === 'charts') loadCharts();
}
$('chart-add').addEventListener('change', () => {
  const picked = $('chart-add').value.trim().split(/\s+/)[0];
  if (picked) { addChart(picked); $('chart-add').value = ''; }
});
$('chart-chips').addEventListener('click', e => {
  const b = e.target.closest('[data-drop]'); if (!b) return;
  chartAddresses = chartAddresses.filter(a => a !== b.dataset.drop); saveCharts(); loadCharts();
});
async function loadChartOptions() {
  if (Date.now() - chartOptionsAt < 60000) return;
  chartOptionsAt = Date.now();
  const now = Date.now() / 1000;
  let recorded = [];
  try { recorded = (await api('recorded?' + query({since: now - 30 * 86400, until: now}))).addresses; } catch (e) { /* the project's addresses are still offered */ }
  const seen = new Set(recorded.map(r => r.ga));
  const all = recorded.map(r => r.ga).concat(model.addresses.filter(a => !seen.has(a.address)).map(a => a.address));
  $('chart-options').innerHTML = all.slice(0, 3000).map(a => `<option value="${esc(a)} ${esc(nameOf(a))}">`).join('');
}
async function loadCharts() {
  clearTimeout(chartTimer);
  const range = Number(chartRange()), until = Date.now() / 1000, since = until - range;
  const points = Math.max(200, Math.min(1500, Math.round($('chart-plot').clientWidth || 800)));
  const wanted = [...chartAddresses];
  const answers = await Promise.all(wanted.map(ga => api('series?' + query({ga, since, until, points})).catch(() => null)));
  chartData = {};
  wanted.forEach((ga, i) => { if (answers[i]) chartData[ga] = answers[i]; });
  drawChart(since, until);
  // A short range keeps growing; a long one changes too slowly to watch
  if (activeTab === 'charts' && chartAddresses.length && range <= 86400) chartTimer = setTimeout(loadCharts, range <= 3600 ? 15000 : 60000);
}
let chartLast = [0, 0];
function drawChart(since, until) {
  if (since === undefined) [since, until] = chartLast; else chartLast = [since, until];
  const host = $('chart-plot');
  $('chart-chips').innerHTML = chartAddresses.map((a, i) => `<span class="chip"><i style="background:var(${CHART_COLORS[i]})"></i><span><b class="mono">${esc(a)}</b> ${esc(nameOf(a))}</span><button type="button" data-drop="${esc(a)}" aria-label="Remove ${esc(a)}">&times;</button></span>`).join('');
  if (chartPlot) { chartPlot.destroy(); chartPlot = null; }
  const drawn = chartAddresses.filter(a => chartData[a] && chartData[a].points.length);
  $('chart-empty').hidden = chartAddresses.length > 0;
  $('chart-notes').innerHTML = chartAddresses.map(a => {
    const d = chartData[a];
    if (!d) return `<div><b class="mono">${esc(a)}</b> could not be read.</div>`;
    if (!d.points.length) return `<div><b class="mono">${esc(a)}</b> has nothing to chart in this range${gaIndex[a] && !gaIndex[a].dpt ? ': it has no datapoint type' : ''}.</div>`;
    return `<div><b class="mono">${esc(a)}</b> ${d.count} values${d.bucket ? `, averaged per ${fmtSpan(d.bucket)}` : ''}${d.guessed ? ' · <span class="pill warning">guessed</span> no datapoint type: read from the size of the payload' : ''}</div>`;
  }).join('');
  if (!drawn.length || !window.uPlot) { host.innerHTML = ''; return; }
  // One scale per unit: the first on the left, the second on the right
  const units = [...new Set(drawn.map(a => chartData[a].unit || ''))];
  const text = cssVar('--secondary-text-color'), grid = cssVar('--grid-color');
  const tables = drawn.map(a => [chartData[a].points.map(p => p[0]), chartData[a].points.map(p => p[1])]);
  const data = drawn.length === 1 ? tables[0] : uPlot.join(tables);
  const long = until - since > 2 * 86400;
  // 24-hour times and the day as month-day, whatever the browser's language
  const tickText = (u, ticks) => ticks.map(t => { const clock = fmtClock(t).slice(0, 5); return !long ? clock : clock === '00:00' ? fmtDay(t).slice(5) : `${fmtDay(t).slice(5)} ${clock}`; });
  const series = [{value: (u, v) => v == null ? '–' : `${fmtDay(v)} ${fmtClock(v)}`}].concat(drawn.map(a => {
    const d = chartData[a], values = new Set(d.points.map(p => p[1]));
    // A switch or a mode jumps from one value to the next: drawn as steps, not as a slope
    const stepped = values.size <= 6 && !d.bucket;
    return {
      label: `${a} ${nameOf(a)}`.trim(), scale: 'u' + units.indexOf(d.unit || ''), stroke: cssVar(CHART_COLORS[chartAddresses.indexOf(a)]), width: 1.6, spanGaps: true,
      paths: stepped ? uPlot.paths.stepped({align: 1}) : undefined, points: {show: d.points.length < 60},
      value: (u, v) => v == null ? '–' : `${Math.round(v * 100) / 100}${d.unit ? ' ' + d.unit : ''}`,
    };
  }));
  const axis = (scale, side, unit) => ({scale, side, stroke: text, grid: {stroke: grid, width: 1, show: side === 3}, ticks: {stroke: grid}, font: '11px IBM Plex Mono, monospace', size: 56, label: unit || undefined, labelFont: '11px IBM Plex Sans, sans-serif', labelSize: unit ? 18 : 0});
  host.innerHTML = '';
  chartPlot = new uPlot({
    width: host.clientWidth || 800, height: Math.max(260, Math.min(520, window.innerHeight - 330)),
    scales: {x: {time: true, min: since, max: until}},
    series,
    axes: [{stroke: text, grid: {stroke: grid, width: 1}, ticks: {stroke: grid}, font: '11px IBM Plex Mono, monospace', values: tickText, space: long ? 90 : 60}]
      .concat(units.slice(0, 2).map((u, i) => axis('u' + i, i ? 1 : 3, u))),
    cursor: {drag: {x: true, y: false}},
  }, data, host);
}
// A telegram for a charted address came in: bring the chart up to date soon
let chartLiveTimer = null;
function chartLive(items) {
  if (activeTab !== 'charts' || chartLiveTimer || Number(chartRange()) > 86400) return;
  if (items.some(t => chartAddresses.includes(t.dst) && t.type !== 'read')) chartLiveTimer = setTimeout(() => { chartLiveTimer = null; loadCharts(); }, 4000);
}
TAB_RENDER.charts = () => { loadChartOptions(); if (!chartPlot || !Object.keys(chartData).length) loadCharts(); };
window.addEventListener('resize', () => { if (chartPlot && activeTab === 'charts') chartPlot.setSize({width: $('chart-plot').clientWidth, height: chartPlot.height}); });

// ---------------------------------------------------------------- statistics
const statsRange = seg('stats-range', () => loadStats());
let statsData = null, statsBusy = false, statsAt = 0;
$('stats-refresh').addEventListener('click', () => loadStats());
async function loadStats() {
  if (statsBusy) return;
  statsBusy = true;
  const until = Date.now() / 1000, since = until - Number(statsRange());
  try { statsData = await api('stats?' + query({since, until})); statsAt = Date.now(); renderStats(); } catch (e) { toast(e.message, true); }
  statsBusy = false;
}
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const AVAILABILITY = {recording: ['Listening', '--success-color'], link_down: ['No link to the bus', '--warning-color'], not_running: ['App not running', '--muted-text-color']};
function renderStats() {
  const s = statsData; if (!s) return;
  const span = s.until - s.since, types = s.by_type || {};
  const share = k => s.total ? Math.round((types[k] || 0) / s.total * 100) + '%' : '–';
  $('stats-span').textContent = `${fmtWhen(s.since)} – ${fmtWhen(s.until)}`;
  $('stats-tiles').innerHTML = [
    tile('Telegrams', fmtCount(s.total), `in ${fmtSpan(span)}`),
    tile('Per minute', (Math.round(s.per_minute * 10) / 10).toString(), 'on average'),
    tile('Busiest minute', s.peak_minute ? s.peak_minute.count : '–', s.peak_minute ? fmtWhen(s.peak_minute.ts) : ''),
    tile('Group addresses', s.addresses, 'carried a telegram'),
    tile('Devices', s.sources, 'sent one'),
    tile('Writes', share('write'), `${fmtCount(types.write || 0)} telegrams`),
    tile('Reads', share('read'), `${fmtCount(types.read || 0)} asked, ${fmtCount(types.response || 0)} answered`),
  ].join('');

  const h = s.histogram, stamp = ts => span > 2 * 86400 ? fmtDay(ts).slice(5) + ' ' + fmtClock(ts).slice(0, 5) : fmtClock(ts).slice(0, 5);
  $('stats-hist-hint').textContent = `per ${fmtSpan(h.width)}`;
  bars($('stats-hist'), h.counts.map((n, i) => ({value: n, title: `${stamp(s.since + i * h.width)}  ${n} telegrams`})), [stamp(s.since), stamp(s.since + span / 2), stamp(s.until)]);

  const max = Math.max(1, ...s.heatmap.flat());
  // Monday first; the recording counts Sunday as day 0
  const order = [1, 2, 3, 4, 5, 6, 0];
  $('stats-heat').innerHTML = '<div class="heat"><span></span>' + Array.from({length: 24}, (_, hour) => `<span>${hour % 3 ? '' : hour}</span>`).join('') +
    order.map(day => `<span class="d">${DAYS[day]}</span>` + s.heatmap[day].map((n, hour) =>
      `<b title="${DAYS[day]} ${pad(hour)}:00  ${n} telegrams" style="${n ? `background:color-mix(in srgb, var(--accent) ${Math.round(12 + n / max * 88)}%, var(--grid-color))` : ''}"></b>`).join('')).join('') + '</div>';

  topTable('stats-top-ga', s.top_addresses.map(r => ({address: r.ga, count: r.count})), 'Nothing recorded in this range.');
  topTable('stats-top-src', s.top_sources.map(r => ({address: r.src, count: r.count})), 'Nothing recorded in this range.');

  const a = s.availability, total = Math.max(1, a.until - a.since);
  $('stats-availability').innerHTML = '<div class="avail">' + a.segments.map(seg =>
    `<i style="width:${(seg.to - seg.from) / total * 100}%;background:var(${AVAILABILITY[seg.state][1]})" title="${esc(AVAILABILITY[seg.state][0])}: ${esc(fmtWhen(seg.from))} – ${esc(fmtWhen(seg.to))} (${esc(fmtSpan(seg.to - seg.from))})"></i>`).join('') + '</div>' +
    '<div class="legend">' + Object.entries(AVAILABILITY).map(([k, [label, color]]) => `<span><i style="background:var(${color})"></i>${esc(label)} ${Math.round((a.coverage[k] || 0) / total * 1000) / 10}%</span>`).join('') + '</div>' +
    (a.quiet.length ? '<div class="muted" style="margin-top:10px">The longest pauses on the bus while the app was listening:' + a.quiet.slice(0, 6).map(q => `<div class="mono">${esc(fmtWhen(q.from))} – ${esc(fmtWhen(q.to))} (${esc(fmtSpan(q.to - q.from))})</div>`).join('') + '</div>'
      : '<div class="muted" style="margin-top:10px">No pause on the bus longer than half an hour while the app was listening.</div>');

  const r = s.recorder;
  $('stats-recorder').innerHTML = kv([
    ['Telegrams', esc(fmtCount(r.rows))], ['Since', r.oldest ? esc(fmtWhen(r.oldest)) : '–'], ['Size', esc(fmtSize(r.bytes))],
    ['Kept', `${r.retain_days ? r.retain_days + ' days' : 'for ever'}${r.retain_rows ? `, at most ${fmtCount(r.retain_rows)} telegrams` : ''} <span class="muted">(the app's options)</span>`],
  ]) + '<div class="toolbar" style="margin:12px 0 0"><a class="btn" href="api/archive.csv?since=0">Export everything as CSV</a><button type="button" class="btn danger" id="recorder-clear">Empty the recording</button></div>';
}
$('stats-recorder').addEventListener('click', async e => {
  if (!e.target.closest('#recorder-clear')) return;
  const ok = await ask({title: 'Empty the recording?', text: 'Every recorded telegram is deleted. The charts, the archive and the statistics start again from now.', ok: 'Empty', danger: true});
  if (ok && await attempt(() => api('recorder/clear', {method: 'POST'}), 'The recording is empty')) loadStats();
});
TAB_RENDER.stats = () => { if (!statsData || Date.now() - statsAt > 60000) loadStats(); };
