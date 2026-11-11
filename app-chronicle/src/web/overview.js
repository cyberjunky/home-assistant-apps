// Chronicle dashboard - Overview: totals, rows written per hour, the most active entities and domains.

let overviewHours = Number(localStorage.getItem(LS + 'overview_hours')) || 24;
let overviewTimer = null;

async function showOverview() {
    buildChips('overview-range', [{key: '24', label: '24 hours'}, {key: '168', label: '7 days'}], String(overviewHours), key => {
        overviewHours = Number(key);
        try { localStorage.setItem(LS + 'overview_hours', key); } catch (e) { /* only a convenience */ }
        loadOverview();
    });
    await loadOverview();
    clearInterval(overviewTimer);
    // Keeps counting while the page is open
    overviewTimer = setInterval(() => { if (activeTab === 'overview' && document.visibilityState === 'visible') loadOverview(); }, 60000);
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
        tile('Compression', ratio ? withUnit(num(ratio, 1), '×') : '<small>none yet</small>',
            ratio ? `${fmtBytes(t.compressed_before)} → ${fmtBytes(t.compressed_after)}` : 'no chunk compressed yet'),
        tile('Oldest history', t.oldest ? `<span title="${escapeHtml(fmtDate(t.oldest))}">${escapeHtml(oldestDate(t.oldest))}</span>` : '–', t.oldest ? fmtAgo(t.oldest) : ''),
        tile('Database size', bytesUnit(t.database_size), 'everything in this database'),
    ].join('');

    // Rows per hour, the hours without rows included. A history younger than the period starts
    // at its first hour, so an hour of history isn't a sliver at the edge of 24 hours.
    const el = document.getElementById('chart-rows');
    const now = Math.floor(Date.now() / 3600000) * 3600;
    let first = now - (overviewHours - 1) * 3600;
    if (t.oldest) first = Math.max(first, Math.floor(t.oldest / 3600) * 3600);
    const byHour = new Map(data.per_hour.map(p => [p.t, p.rows]));
    const xs = [], rows = [];
    for (let t0 = first; t0 <= now; t0 += 3600) {
        xs.push(t0 + 1800);  // drawn in the middle of its hour
        rows.push(byHour.get(t0) || 0);
    }
    timeChart(el, {
        x: xs,
        series: [{name: 'rows', unit: 'rows', color: cssVar('--series-1'), values: rows, kind: 'bars'}],
        xRange: [first, now + 3600],
        height: 200,
        barSize: 34,
        title: 'Rows written per hour',
        filename: 'chronicle-rows-per-hour',
        tipTitle: t => `${fmtDay(t - 1800)} ${fmtTime(t - 1800)} – ${fmtTime(t + 1800)}`,
        csv: (from, to) => csvFrom(['hour_start', 'rows'], xs.map((x, i) => [x - 1800, rows[i]])
            .filter(([x]) => x + 1800 >= from && x + 1800 <= to).map(([x, r]) => [isoTime(x), r])),
        empty: 'No rows written in this period',
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
