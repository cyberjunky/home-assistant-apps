// Chronicle dashboard - Entities: the list (search, domain, sort) and one entity's history.

let entityList = null;
let entityListAt = 0;
let entitySort = {key: 'entity_id', desc: false};
let entityDomain = '';
let openEntityId = null;
let entityRange = localStorage.getItem(LS + 'entity_range') || '24h';
let entityData = null;
let entityZoom = null;       // {from, to} while zoomed in
let compareIds = [];         // entities drawn with the open one, at most COMPARE_MAX
let compareData = {};
const COMPARE_MAX = 4;

const ENTITY_RANGES = [
    {key: '24h', label: '24 hours'},
    {key: '7d', label: '7 days'},
    {key: '30d', label: '30 days'},
    {key: '1y', label: '1 year'},
    {key: 'all', label: 'All'},
];
const SOURCE_TEXT = {
    raw: 'every recorded row',
    buckets: 'averaged from the rows',
    hourly: 'hourly averages',
    daily: 'daily averages',
};

async function showEntities() {
    const [, hashEntity, hashRange] = window.location.hash.slice(1).split('/');
    if (hashEntity) return openEntity(decodeURIComponent(hashEntity), hashRange);
    if (openEntityId) return renderEntityDetail();
    await loadEntityList();
}

async function loadEntityList(force) {
    if (!ready()) {
        document.getElementById('entity-table').innerHTML = '<div class="empty-state">Nothing recorded yet</div>';
        return;
    }
    // The list is cheap but not free: reuse it for a minute
    if (force || !entityList || Date.now() - entityListAt > 60000) {
        try {
            entityList = (await api('entities')).entities;
            entityListAt = Date.now();
        } catch (e) {
            return handleError(e);
        }
    }
    renderDomainSelect();
    renderEntityTable();
}

function renderDomainSelect() {
    const el = document.getElementById('entity-domain');
    const counts = {};
    entityList.forEach(e => { counts[e.domain] = (counts[e.domain] || 0) + 1; });
    el.innerHTML = `<option value="">All domains</option>` + Object.keys(counts).sort()
        .map(d => `<option value="${escapeHtml(d)}"${d === entityDomain ? ' selected' : ''}>${escapeHtml(d)} (${counts[d]})</option>`).join('');
    el.onchange = () => { entityDomain = el.value; renderEntityTable(); };
}

function lastStateHtml(e) {
    if (e.last === null || e.last === undefined) return '<span class="muted">–</span>';
    if (e.state === null || e.state === undefined) return withUnit(escapeHtml(fmtValue(e.value)), e.unit);
    return escapeHtml(e.state);
}

const ENTITY_COLUMNS = [
    {key: 'entity_id', label: 'Entity'},
    {key: 'state', label: 'Last state'},
    {key: 'last', label: 'Last change'},
    {key: 'rows_24h', label: 'Rows 24 h', n: true},
    {key: 'since', label: 'Since', n: true},
];

function renderEntityTable() {
    const search = document.getElementById('entity-search').value.trim().toLowerCase();
    const rows = entityList.filter(e => (!entityDomain || e.domain === entityDomain)
        && (!search || e.entity_id.includes(search) || (e.name || '').toLowerCase().includes(search)));
    const {key, desc} = entitySort;
    const value = e => key === 'state' ? (e.state === null ? (e.value ?? -Infinity) : e.state) : (e[key] ?? '');
    rows.sort((a, b) => {
        const va = value(a), vb = value(b);
        const c = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb));
        return desc ? -c : c;
    });
    document.getElementById('entity-count').textContent = `${num(rows.length)} of ${num(entityList.length)} entities`;
    const head = ENTITY_COLUMNS.map(c => `<th class="sortable${c.n ? ' n' : ''}" data-sort="${c.key}">${c.label}${c.key === key ? (desc ? ' ↓' : ' ↑') : ''}</th>`).join('');
    const body = rows.map(e => `
        <tr class="link" data-entity="${escapeHtml(e.entity_id)}">
            <td class="ent"><b>${escapeHtml(e.name || e.entity_id)}</b><small>${escapeHtml(e.entity_id)}</small></td>
            <td>${lastStateHtml(e)}</td>
            <td class="muted">${e.last ? escapeHtml(fmtAgo(e.last)) : '–'}</td>
            <td class="n">${num(e.rows_24h)}</td>
            <td class="n muted">${e.since ? escapeHtml(fmtDate(e.since)) : '–'}</td>
        </tr>`).join('');
    const el = document.getElementById('entity-table');
    el.innerHTML = rows.length
        ? `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
        : '<div class="empty-state">No entity matches</div>';
    el.onclick = (ev) => {
        const th = ev.target.closest('[data-sort]');
        if (th) {
            const k = th.dataset.sort;
            entitySort = {key: k, desc: entitySort.key === k ? !entitySort.desc : ['rows_24h', 'last'].includes(k)};
            return renderEntityTable();
        }
        const tr = ev.target.closest('[data-entity]');
        if (tr) openEntity(tr.dataset.entity);
    };
}

// From the Overview: the list filtered on one domain
function openDomain(domain) {
    entityDomain = domain;
    openEntityId = null;
    document.getElementById('entity-search').value = '';
    switchTab('entities');
}

function openEntity(entityId, range) {
    if (entityId !== openEntityId) {
        compareIds = [];
        compareData = {};
    }
    openEntityId = entityId;
    entityZoom = null;
    if (ENTITY_RANGES.some(r => r.key === range)) entityRange = range;
    if (activeTab !== 'entities') {
        switchTab('entities');
        return;
    }
    document.getElementById('entity-list-view').hidden = true;
    document.getElementById('entity-detail-view').hidden = false;
    document.getElementById('pages').scrollTop = 0;
    buildChips('entity-range', ENTITY_RANGES, entityRange, key => {
        entityRange = key;
        entityZoom = null;
        try { localStorage.setItem(LS + 'entity_range', key); } catch (e) { /* only a convenience */ }
        loadEntity();
    });
    loadEntity();
}

function closeEntity() {
    openEntityId = null;
    entityData = null;
    entityZoom = null;
    compareIds = [];
    compareData = {};
    hideTip();
    history.replaceState(null, '', '#entities');
    document.getElementById('entity-detail-view').hidden = true;
    document.getElementById('entity-list-view').hidden = false;
    loadEntityList();
}

function renderEntityDetail() {
    if (entityData && entityData.entity.entity_id === openEntityId) renderEntity(entityData);
    else openEntity(openEntityId);
}

function entityQuery(id, window) {
    const base = `entity_id=${encodeURIComponent(id)}`;
    if (window) return `${base}&from=${window.from}&to=${window.to}`;
    return `${base}&range=${entityRange}`;
}

// The open entity and the compared ones, for the range or the zoomed window. The compared ones
// use the open entity's window, so all share one time axis.
async function loadEntity() {
    const id = openEntityId;
    // The link opens this entity and range again
    history.replaceState(null, '', `#entities/${encodeURIComponent(id)}/${entityRange}`);
    document.querySelectorAll('#entity-range .tab').forEach(t => t.classList.toggle('active', !entityZoom && t.dataset.key === entityRange));
    try {
        const data = await api('entity?' + entityQuery(id, entityZoom));
        if (id !== openEntityId) return;
        const window = {from: data.from, to: data.to};
        const compared = await Promise.all(compareIds.map(c => api('entity?' + entityQuery(c, window)).catch(() => null)));
        if (id !== openEntityId) return;
        compareData = {};
        compareIds.forEach((c, i) => { if (compared[i]) compareData[c] = compared[i]; });
        entityData = data;
        renderEntity(data);
    } catch (e) {
        if (e.status === 404) {
            toast(`${id} is not in the history`, 'error');
            return closeEntity();
        }
        handleError(e);
    }
}

function zoomEntity(from, to) {
    entityZoom = {from: Math.floor(from), to: Math.ceil(to)};
    loadEntity();
}

function resetEntityZoom() {
    entityZoom = null;
    loadEntity();
}

function renderEntity(d) {
    const e = d.entity;
    const latest = d.latest;
    document.getElementById('entity-head').innerHTML = `
        <h2>${escapeHtml(e.name || e.entity_id)}</h2>
        <span class="id">${escapeHtml(e.entity_id)}</span>
        ${e.device_class ? `<span class="badge">${escapeHtml(e.device_class)}</span>` : ''}
        ${e.state_class ? `<span class="badge">${escapeHtml(e.state_class)}</span>` : ''}`;
    document.getElementById('entity-source').textContent = `${fmtDateTime(d.from)} – ${fmtDateTime(d.to)} · ${SOURCE_TEXT[d.source] || d.source}${d.zoomed ? ' · zoomed' : ''}`;
    const latestHtml = !latest ? '–' : latest.state === null || latest.state === undefined
        ? withUnit(escapeHtml(fmtValue(latest.value)), e.unit) : escapeHtml(latest.state);
    const tiles = [tile('Now', latestHtml, latest ? fmtAgo(latest.t) : '')];

    document.getElementById('entity-compare').hidden = d.kind !== 'numeric';
    if (d.kind === 'numeric') renderNumeric(d, tiles);
    else renderStates(d, tiles);

    tiles.push(tile('First record', e.first ? escapeHtml(fmtDate(e.first)) : '–', e.first ? fmtAgo(e.first) : ''));
    document.getElementById('entity-tiles').innerHTML = tiles.join('');

    const attrs = latest && latest.attributes;
    document.getElementById('entity-attr-hint').textContent = latest ? fmtDateTime(latest.t) : '';
    document.getElementById('entity-attributes').innerHTML = attrs
        ? `<pre class="json">${escapeHtml(JSON.stringify(attrs, null, 2))}</pre>`
        : '<div class="empty-state">No attributes recorded</div>';
}

const isCounter = e => e.state_class === 'total_increasing' || e.state_class === 'total';

// The series of one entity's data: raw values (gaps where unavailable) or the averages with
// their min-max band. Counters are steps; averaged counters show their last value per period.
function entitySeries(d, color, withBand) {
    const e = d.entity;
    const raw = d.source === 'raw';
    const counter = isCounter(e);
    const pts = d.points;
    const values = pts.map(p => (raw ? p.v : counter ? p.last : p.mean));
    const series = {
        name: e.name || e.entity_id,
        unit: e.unit || '',
        color,
        values,
        kind: raw && counter ? 'step' : 'line',
    };
    if (!raw && !counter && withBand) series.band = {lo: pts.map(p => p.min), hi: pts.map(p => p.max)};
    return {x: pts.map(p => p.t), series, carried: new Set(pts.filter(p => p.carried).map(p => p.t))};
}

function renderNumeric(d, tiles) {
    const e = d.entity;
    const unit = e.unit || '';
    const pts = d.points;
    const raw = d.source === 'raw';
    const el = document.getElementById('chart-entity');
    const colors = [1, 2, 3, 4, 5].map(i => cssVar(`--series-${i}`));
    const main = entitySeries(d, colors[0], true);
    const others = compareIds.filter(c => compareData[c] && compareData[c].kind === 'numeric')
        .map((c, i) => entitySeries(compareData[c], colors[i + 1], false));

    // One time axis for all: the compared entities' values fall between the open one's
    let x = main.x, series = [main.series];
    if (others.length) {
        const tables = [[main.x, main.series.values], ...others.map(o => [o.x, o.series.values])];
        const joined = uPlot.join(tables, tables.map(() => [1]));  // keep the gaps (null)
        x = Array.from(joined[0]);
        const position = new Map(x.map((t, i) => [t, i]));
        series = [main.series, ...others.map(o => o.series)].map((s, k) => ({...s, values: Array.from(joined[k + 1])}));
        if (main.series.band) {
            const lo = new Array(x.length).fill(undefined), hi = new Array(x.length).fill(undefined);
            main.x.forEach((t, i) => { lo[position.get(t)] = main.series.band.lo[i]; hi[position.get(t)] = main.series.band.hi[i]; });
            series[0].band = {lo, hi};
        }
    }
    const carried = [main, ...others].map(o => o.carried);
    timeChart(el, {
        x,
        series,
        xRange: [d.from, d.to],
        height: 280,
        title: `${e.name || e.entity_id}${others.length ? ' and ' + others.map(o => o.series.name).join(', ') : ''}`,
        filename: `${e.entity_id}-${d.zoomed ? 'zoom' : d.range}`,
        onZoom: zoomEntity,
        onReset: resetEntityZoom,
        zoomed: d.zoomed,
        aligned: others.length > 0,
        gapText: raw ? 'unavailable' : undefined,
        tipTitle: raw ? undefined : t => bucketTitle(t, d.bucket),
        extraRows: raw ? undefined : idx => {
            const p = pts.find(q => q.t === x[idx]);
            return p ? [{value: num(p.n), name: 'values averaged'}] : [];
        },
        csv: (from, to) => csvFrom(
            ['time', ...series.map(s => (s.unit ? `${s.name} (${s.unit})` : s.name)), ...(series[0].band ? ['min', 'max'] : [])],
            x.map((t, i) => [t, i]).filter(([t]) => t >= from && t <= to)
                .map(([t, i]) => [isoTime(t), ...series.map((s, k) => (carried[k].has(t) || missing(s.values[i]) ? '' : s.values[i])),
                    ...(series[0].band ? [series[0].band.lo[i] ?? '', series[0].band.hi[i] ?? ''] : [])])
                .filter(row => row.slice(1).some(v => v !== ''))),
        empty: 'Not enough data yet: no values in this period',
    });
    document.getElementById('entity-chart-title').textContent = unit ? `History · ${unit}` : 'History';
    const counted = pts.filter(p => !p.carried);
    document.getElementById('entity-chart-hint').textContent = raw ? `${num(counted.filter(p => p.v !== null).length)} values`
        : `${num(pts.length)} × ${fmtDuration(d.bucket)}`;
    renderCompareChips(others);

    const all = raw ? counted.map(p => p.v).filter(v => !missing(v)) : null;
    const lows = raw ? all : pts.map(p => p.min).filter(v => !missing(v));
    const highs = raw ? all : pts.map(p => p.max).filter(v => !missing(v));
    const samples = raw ? all.length : pts.reduce((a, p) => a + (p.n || 0), 0);
    const mean = raw ? all.reduce((a, b) => a + b, 0) / Math.max(1, all.length)
        : pts.reduce((a, p) => a + (p.mean || 0) * (p.n || 0), 0) / Math.max(1, samples);
    const has = lows.length > 0;
    tiles.push(
        tile('Minimum', has ? withUnit(escapeHtml(fmtValue(Math.min(...lows))), unit) : '–', 'in this period'),
        tile('Average', has ? withUnit(escapeHtml(fmtValue(mean)), unit) : '–', 'per value, in this period'),
        tile('Maximum', has ? withUnit(escapeHtml(fmtValue(Math.max(...highs))), unit) : '–', 'in this period'),
        tile('Values', fmtCount(samples), 'recorded in this period'),
    );

    // The shown values, newest first
    document.getElementById('entity-changes-title').textContent = raw ? 'Values' : 'Averages';
    const rows = (raw ? counted : pts).slice(-500).reverse();
    document.getElementById('entity-changes-hint').textContent = rows.length >= 500 ? 'newest 500' : '';
    document.getElementById('entity-changes').innerHTML = rows.length ? `<table class="data"><thead><tr>${raw
        ? '<th>Time</th><th class="n">Value</th>'
        : '<th>Period</th><th class="n">Mean</th><th class="n">Min</th><th class="n">Max</th><th class="n">Samples</th>'}</tr></thead><tbody>${rows.map(p => raw
        ? `<tr><td class="muted">${escapeHtml(fmtDateTime(p.t))}</td><td class="n">${p.v === null ? '<span class="muted">unavailable</span>' : escapeHtml(fmtValue(p.v))}</td></tr>`
        : `<tr><td class="muted">${escapeHtml(bucketTitle(p.t, d.bucket))}</td><td class="n">${escapeHtml(fmtValue(p.mean))}</td><td class="n">${escapeHtml(fmtValue(p.min))}</td><td class="n">${escapeHtml(fmtValue(p.max))}</td><td class="n">${num(p.n)}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty-state">No values in this period</div>';
}

function bucketTitle(t, bucket) {
    if (bucket >= 86400) return fmtDay(t) + (new Date(t * 1000).getFullYear() !== new Date().getFullYear() ? ' ' + new Date(t * 1000).getFullYear() : '');
    return `${fmtDay(t)} ${fmtTime(t)} – ${fmtTime(t + bucket)}`;
}

// ---------------------------------------------------------------------
// Compare: up to COMPARE_MAX numeric entities in the same chart, at most two units
// ---------------------------------------------------------------------
async function renderCompareChips(others) {
    const chips = document.getElementById('compare-chips');
    chips.innerHTML = others.map((o, i) => `<span class="compare-chip" style="--c:${o.series.color}"><i></i>${escapeHtml(o.series.name)}
        <button type="button" data-remove="${escapeHtml(compareIds[i])}" title="Remove from the chart" aria-label="Remove">✕</button></span>`).join('');
    const input = document.getElementById('compare-input');
    input.disabled = compareIds.length >= COMPARE_MAX;
    input.placeholder = compareIds.length >= COMPARE_MAX ? `at most ${COMPARE_MAX}` : 'Compare with…';
    if (!entityList) {
        try { entityList = (await api('entities')).entities; entityListAt = Date.now(); } catch (e) { return; }
    }
    document.getElementById('compare-options').innerHTML = entityList
        .filter(e => e.value !== null && e.value !== undefined && e.entity_id !== openEntityId && !compareIds.includes(e.entity_id))
        .map(e => `<option value="${escapeHtml(e.entity_id)}">${escapeHtml(e.name || '')}${e.unit ? ' · ' + escapeHtml(e.unit) : ''}</option>`).join('');
}

function addCompare(entityId) {
    const id = entityId.trim();
    if (!id || id === openEntityId || compareIds.includes(id)) return;
    const known = entityList && entityList.find(e => e.entity_id === id);
    if (!known) return toast(`${id} is not in the history`, 'error');
    if (known.value === null || known.value === undefined) return toast(`${id} has no numbers to draw`, 'error');
    if (compareIds.length >= COMPARE_MAX) return toast(`At most ${COMPARE_MAX} entities to compare`, 'error');
    // Two y axes at most: one unit on the left, one on the right
    const units = new Set([entityData.entity.unit || '', ...compareIds.map(c => (compareData[c] ? compareData[c].entity.unit || '' : ''))]);
    units.add(known.unit || '');
    if (units.size > 2) return toast('A chart has two y axes: this entity has a third unit', 'error');
    compareIds.push(id);
    document.getElementById('compare-input').value = '';
    loadEntity();
}

function removeCompare(entityId) {
    compareIds = compareIds.filter(c => c !== entityId);
    delete compareData[entityId];
    renderEntity(entityData);
}

document.getElementById('compare-input').addEventListener('change', (e) => addCompare(e.target.value));
document.getElementById('compare-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') addCompare(e.target.value); });
document.getElementById('compare-chips').addEventListener('click', (e) => {
    const button = e.target.closest('[data-remove]');
    if (button) removeCompare(button.dataset.remove);
});

// ---------------------------------------------------------------------
// States: a timeline, zoomable like the charts
// ---------------------------------------------------------------------
function renderStates(d, tiles) {
    const pts = d.points;
    const el = document.getElementById('chart-entity');
    // Segments: each state lasts until the next change (the last one until the end)
    const changes = d.before && (!pts.length || pts[0].t > d.from) ? [{t: d.from, state: d.before.state}, ...pts] : pts;
    const segments = changes.map((p, i) => ({from: Math.max(p.t, d.from), to: i + 1 < changes.length ? changes[i + 1].t : d.to, state: p.state}));

    // Time per state in this period; the colours follow that order
    const totals = {};
    segments.forEach(s => { totals[s.state] = (totals[s.state] || 0) + (s.to - s.from); });
    const span = Math.max(1, d.to - d.from);
    const order = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
    const stateColor = stateColors(order);
    timelineChart(el, {
        from: d.from, to: d.to, segments, colorOf: stateColor, height: 72,
        title: d.entity.name || d.entity.entity_id,
        filename: `${d.entity.entity_id}-${d.zoomed ? 'zoom' : d.range}`,
        onZoom: zoomEntity,
        onReset: resetEntityZoom,
        zoomed: d.zoomed,
        csv: () => csvFrom(['from', 'to', 'state', 'seconds'], segments.map(s => [isoTime(s.from), isoTime(s.to), s.state, Math.round(s.to - s.from)])),
        empty: 'Not enough data yet: no state recorded in this period',
    });
    el.querySelector('.chart-legend') && (el.querySelector('.chart-legend').innerHTML = order.map(s =>
        `<span><i style="background:${stateColor(s)}"></i>${escapeHtml(s || '(empty)')} · ${num(totals[s] / span * 100, 1)}% · ${escapeHtml(fmtDuration(totals[s]))}</span>`).join(''));
    document.getElementById('entity-chart-title').textContent = 'States';
    const attributeOnly = d.updates > pts.length ? ` · ${num(d.updates - pts.length)} attribute updates` : '';
    document.getElementById('entity-chart-hint').textContent = `${num(pts.length)} changes${d.truncated ? ' (the newest)' : ''}${attributeOnly}`;
    renderCompareChips([]);

    const on = totals.on;
    tiles.push(
        tile('Changes', num(pts.length), d.updates > pts.length ? `${num(d.updates)} updates in this period` : 'in this period'),
        tile('States', num(order.length), order.slice(0, 3).map(s => escapeHtml(s)).join(' · ')),
    );
    if (on !== undefined) tiles.push(tile('On', withUnit(num(on / span * 100, 1), '%'), escapeHtml(fmtDuration(on))));

    // Changes, newest first, with how long each lasted
    const rows = segments.slice().reverse().slice(0, 500);
    document.getElementById('entity-changes-title').textContent = 'Changes';
    document.getElementById('entity-changes-hint').textContent = segments.length > 500 ? 'newest 500' : '';
    document.getElementById('entity-changes').innerHTML = rows.length
        ? `<table class="data"><thead><tr><th>From</th><th>State</th><th class="n">Lasted</th></tr></thead><tbody>${rows.map(s => `
            <tr><td class="muted">${escapeHtml(fmtDateTime(s.from))}</td>
                <td><span class="state" style="--c:${stateColor(s.state)}">${escapeHtml(s.state || '(empty)')}</span></td>
                <td class="n">${escapeHtml(fmtDuration(s.to - s.from))}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty-state">No changes in this period</div>';
}

document.getElementById('entity-search').addEventListener('input', () => { if (entityList) renderEntityTable(); });
onTab('entities', showEntities);
