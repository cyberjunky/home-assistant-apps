// Chronicle dashboard - Overview: the integration's writer, totals, rows written per hour, the most
// active entities and domains.

let overviewHours = Number(localStorage.getItem(LS + 'overview_hours')) || 24;
let overviewTimer = null;

async function showOverview() {
    buildChips('overview-range', [{key: '24', label: '24 hours'}, {key: '168', label: '7 days'}], String(overviewHours), key => {
        overviewHours = Number(key);
        try { localStorage.setItem(LS + 'overview_hours', key); } catch (e) { /* only a convenience */ }
        loadOverview();
    });
    await Promise.all([loadOverview(), loadRecorder()]);
    clearInterval(overviewTimer);
    // Keeps counting while the page is open: the writer every 15 s, the totals every minute
    let ticks = 0;
    overviewTimer = setInterval(() => {
        if (activeTab !== 'overview' || document.visibilityState !== 'visible') return;
        loadRecorder();
        if (++ticks % 4 === 0) loadOverview();
    }, 15000);
}

// The Chronicle integration's writer, from its sensors in Home Assistant. Hidden when the app
// can't read them (not running as an app, or an integration without those sensors).
async function loadRecorder() {
    const el = document.getElementById('recorder-strip');
    let r;
    try {
        r = await api('recorder');
    } catch (e) {
        r = {available: false};
    }
    el.hidden = !r.available;
    if (!r.available) return;
    const stale = r.last_write && Date.now() / 1000 - r.last_write > 300;
    const state = r.connected === false ? ['down', 'Not connected to the database']
        : stale ? ['warn', 'Not writing'] : r.connected ? ['ok', 'Recording'] : ['warn', 'Unknown'];
    const part = (label, value, cls) => `<span class="rec-item${cls ? ' ' + cls : ''}"><span>${label}</span><b>${value}</b></span>`;
    el.innerHTML = `<span class="rec-state ${state[0]}"><i></i>${state[1]}</span>`
        + part('last write', r.last_write ? `<span title="${escapeHtml(fmtDateTime(r.last_write))}">${escapeHtml(fmtAgo(r.last_write))}</span>` : '–')
        + part('took', missing(r.write_duration) ? '–' : `${num(r.write_duration)} ms`)
        + part('queue', missing(r.queue) ? '–' : num(r.queue), r.queue > 1000 ? 'warn' : '')
        + part('dropped', missing(r.dropped) ? '–' : num(r.dropped), r.dropped > 0 ? 'down' : '');
}

async function loadOverview() {
    if (!ready()) return renderOverviewEmpty();
    let data;
    try {
        data = await api('overview?hours=' + overviewHours);
    } catch (e) {
        return handleError(e);
    }
    renderOverview(data);
}

function renderOverviewEmpty() {
    document.getElementById('overview-tiles').innerHTML = '';
    emptyChart(document.getElementById('chart-rows'), 'Nothing recorded yet');
    document.getElementById('rows-summary').innerHTML = '';
    document.getElementById('top-entities').innerHTML = '<div class="empty-state">Nothing recorded yet</div>';
    document.getElementById('domains').innerHTML = '';
}

// Short enough for a tile: 11 Nov this year, Nov 2025 before
function oldestDate(ts) {
    const d = new Date(ts * 1000);
    return d.getFullYear() === new Date().getFullYear() ? fmtShortDate(ts)
        : d.toLocaleDateString([], {month: 'short', year: 'numeric'});
}

function renderOverview(data) {
    const t = data.totals;
    const ratio = t.compression_ratio;
    document.getElementById('overview-tiles').innerHTML = [
        tile('Entities', num(t.entities), `${fmtCount(t.rows_24h)} rows in 24 h`),
        tile('Rows', '≈ ' + fmtCount(t.rows), 'estimate by TimescaleDB'),
        tile('History size', bytesUnit(t.history_size), `${num(t.chunks)} chunks · ${num(t.compressed_chunks)} compressed`),
        tile('Compression', ratio ? withUnit(num(ratio, 1), '×') : 'none yet',
            ratio ? `${fmtBytes(t.compressed_before)} → ${fmtBytes(t.compressed_after)}` : 'chunks compress with age'),
        tile('Oldest history', t.oldest ? `<span title="${escapeHtml(fmtDate(t.oldest))}">${escapeHtml(oldestDate(t.oldest))}</span>` : '–', t.oldest ? fmtAgo(t.oldest) : ''),
        tile('Database size', bytesUnit(t.database_size), 'everything in this database'),
    ].join('');

    // Rows per hour, the hours without rows included. A history younger than the period starts
    // at its first hour, so an hour of history isn't a sliver at the edge of 24 hours. The hour in
    // progress isn't drawn (it would always dip): the summary has it.
    const el = document.getElementById('chart-rows');
    const current = Math.floor(Date.now() / 3600000) * 3600;
    let first = current - overviewHours * 3600;
    if (t.oldest) first = Math.max(first, Math.floor(t.oldest / 3600) * 3600);
    const byHour = new Map(data.per_hour.map(p => [p.t, p.rows]));
    const xs = [], rows = [];
    for (let t0 = first; t0 < current; t0 += 3600) {
        xs.push(t0 + 1800);  // drawn in the middle of its hour
        rows.push(byHour.get(t0) || 0);
    }
    const thisHour = byHour.get(current) || 0;
    const total = rows.reduce((a, b) => a + b, 0) + thisHour;
    const peak = rows.length ? rows.indexOf(Math.max(...rows)) : -1;
    const summary = [
        `<span><b>${fmtCount(total)}</b> rows</span>`,
        rows.length ? `<span><b>${fmtCount(Math.round((total - thisHour) / rows.length))}</b> per hour</span>` : '',
        peak >= 0 && rows[peak] ? `<span>peak <b>${fmtCount(rows[peak])}</b> at ${escapeHtml((overviewHours > 24 ? fmtDay(xs[peak] - 1800) + ' ' : '') + fmtTime(xs[peak] - 1800))}</span>` : '',
        `<span>this hour <b>${fmtCount(thisHour)}</b></span>`,
    ].filter(Boolean).join('');
    document.getElementById('rows-summary').innerHTML = summary;
    timeChart(el, {
        x: xs,
        series: [{name: 'rows', color: cssVar('--series-1'), values: rows, kind: 'area'}],
        xRange: [first, current],
        height: 150,
        compact: true,
        title: 'Rows written per hour',
        filename: 'chronicle-rows-per-hour',
        format: v => `${num(v)} rows`,
        tipTitle: t => `${fmtDay(t - 1800)} ${fmtTime(t - 1800)} – ${fmtTime(t + 1800)}`,
        csv: (from, to) => csvFrom(['hour_start', 'rows'], xs.map((x, i) => [x - 1800, rows[i]])
            .filter(([x]) => x + 1800 >= from && x + 1800 <= to).map(([x, r]) => [isoTime(x), r])),
        empty: rows.length ? 'No rows written in this period' : 'The first hour is still being written',
    });

    const top = data.top;
    const max = Math.max(1, ...top.map(e => e.rows_24h));
    document.getElementById('top-entities').innerHTML = top.length ? top.map(e => `
        <div class="rank-row link" onclick="openEntity('${escapeHtml(e.entity_id)}')" title="Open ${escapeHtml(e.entity_id)}">
            <div class="name"><b>${escapeHtml(e.name || e.entity_id)}</b><small>${escapeHtml(e.entity_id)}</small></div>
            <div class="n">${num(e.rows_24h)}<small>${num(e.rows_24h / 24, 1)} / h</small></div>
            <div class="bar-track"><i style="width:${(e.rows_24h / max * 100).toFixed(1)}%"></i></div>
        </div>`).join('') : '<div class="empty-state">Nothing written in the last 24 hours</div>';

    const domains = data.domains;
    const dmax = Math.max(1, ...domains.map(d => d.rows_24h));
    document.getElementById('domains').innerHTML = domains.map(d => `
        <div class="rank-row link" onclick="openDomain('${escapeHtml(d.domain)}')" title="Show the ${escapeHtml(d.domain)} entities">
            <div class="name"><b>${escapeHtml(d.domain)}</b><small>${num(d.entities)} ${d.entities === 1 ? 'entity' : 'entities'}</small></div>
            <div class="n">${num(d.rows_24h)}<small>rows</small></div>
            <div class="bar-track"><i style="width:${(d.rows_24h / dmax * 100).toFixed(1)}%;background:var(--series-3)"></i></div>
        </div>`).join('');
}

onTab('overview', showOverview);
