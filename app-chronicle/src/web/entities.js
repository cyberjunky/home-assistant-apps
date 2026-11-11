// Chronicle dashboard - Entities: the list (search, domain, sort) and one entity's history.

let entityList = null;
let entityListAt = 0;
let entitySort = {key: 'entity_id', desc: false};
let entityDomain = '';
let openEntityId = null;
let entityRange = localStorage.getItem(LS + 'entity_range') || '24h';
let entityData = null;

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
    openEntityId = entityId;
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
        try { localStorage.setItem(LS + 'entity_range', key); } catch (e) { /* only a convenience */ }
        loadEntity();
    });
    loadEntity();
}

function closeEntity() {
    openEntityId = null;
    entityData = null;
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

async function loadEntity() {
    const id = openEntityId;
    // The link opens this entity and range again
    history.replaceState(null, '', `#entities/${encodeURIComponent(id)}/${entityRange}`);
    const query = `entity_id=${encodeURIComponent(id)}&range=${entityRange}`;
    document.getElementById('entity-csv').href = basePath + 'api/entity.csv?' + query;
    try {
        const data = await api('entity?' + query);
        if (id !== openEntityId) return;
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

function renderEntity(d) {
    const e = d.entity;
    const latest = d.latest;
    document.getElementById('entity-head').innerHTML = `
        <h2>${escapeHtml(e.name || e.entity_id)}</h2>
        <span class="id">${escapeHtml(e.entity_id)}</span>
        ${e.device_class ? `<span class="badge">${escapeHtml(e.device_class)}</span>` : ''}
        ${e.state_class ? `<span class="badge">${escapeHtml(e.state_class)}</span>` : ''}`;
    document.getElementById('entity-source').textContent = `${fmtDateTime(d.from)} – ${fmtDateTime(d.to)} · ${SOURCE_TEXT[d.source] || d.source}`;
    const latestHtml = !latest ? '–' : latest.state === null || latest.state === undefined
        ? withUnit(escapeHtml(fmtValue(latest.value)), e.unit) : escapeHtml(latest.state);
    const tiles = [tile('Now', latestHtml, latest ? fmtAgo(latest.t) : '')];

    if (d.kind === 'numeric') renderNumeric(d, tiles);
    else renderStates(d, tiles);

    tiles.push(tile('First record', d.entity.first ? escapeHtml(fmtDate(d.entity.first)) : '–', d.entity.first ? fmtAgo(d.entity.first) : ''));
    document.getElementById('entity-tiles').innerHTML = tiles.join('');

    const attrs = latest && latest.attributes;
    document.getElementById('entity-attr-hint').textContent = latest ? fmtDateTime(latest.t) : '';
    document.getElementById('entity-attributes').innerHTML = attrs
        ? `<pre class="json">${escapeHtml(JSON.stringify(attrs, null, 2))}</pre>`
        : '<div class="empty-state">No attributes recorded</div>';
}

function renderNumeric(d, tiles) {
    const e = d.entity;
    const unit = e.unit || '';
    const pts = d.points;
    const el = document.getElementById('chart-entity');
    el.classList.remove('timeline');
    const fmt = v => `${fmtValue(v)}${unit ? ' ' + unit : ''}`;
    const raw = d.source === 'raw';
    const xs = pts.map(p => p.t);
    const values = pts.map(p => raw ? p.v : p.mean);
    const counter = e.state_class === 'total_increasing' || e.state_class === 'total';
    const series = [{name: raw ? e.name || 'value' : 'mean', color: cssVar('--series-1'), values, width: 1.8, step: raw && counter}];
    const opts = {
        x: xs, series, height: 240,
        formatX: t => raw ? `${fmtDay(t)} ${fmtTimeSec(t)}` : bucketTitle(t, d.bucket),
        formatY: v => fmt(v),
        formatTick: v => tickLabel(v),
        xTicks: smartTicks(d.from, d.to, el.clientWidth),
        empty: 'No values in this period',
    };
    if (!raw) {
        opts.range = {lo: pts.map(p => p.min), hi: pts.map(p => p.max), color: cssVar('--series-1')};
        opts.extraRows = i => [
            {value: fmt(pts[i].min), name: 'min'},
            {value: fmt(pts[i].max), name: 'max'},
            {value: num(pts[i].n), name: 'samples'},
        ];
    }
    // The x axis spans the whole range, also when the history starts later (a first daily
    // bucket can start before the range: then the axis starts there)
    if (xs.length > 1) {
        const from = Math.min(d.from, xs[0]);
        opts.xTicks = smartTicks(from, d.to, el.clientWidth);
        opts.x = [from, ...xs, d.to];
        opts.series = series.map(s => ({...s, values: [null, ...s.values, null]}));
        if (opts.range) opts.range = {...opts.range, lo: [null, ...opts.range.lo, null], hi: [null, ...opts.range.hi, null]};
        const extra = opts.extraRows;
        if (extra) opts.extraRows = i => (i === 0 || i === opts.x.length - 1 ? [] : extra(i - 1));
    }
    lineChart(el, opts);
    document.getElementById('legend-entity').innerHTML = raw ? '' : legendHtml([
        {name: 'mean', color: cssVar('--series-1'), kind: 'line'},
        {name: 'min – max', color: cssVar('--series-1'), kind: 'band'},
    ]);
    document.getElementById('entity-chart-title').textContent = unit ? `History · ${unit}` : 'History';
    document.getElementById('entity-chart-hint').textContent = raw ? `${num(pts.length)} values`
        : `${num(pts.length)} × ${fmtDuration(d.bucket)}`;

    const all = raw ? values.filter(v => !missing(v)) : null;
    const lo = raw ? Math.min(...all) : Math.min(...pts.map(p => p.min).filter(v => !missing(v)));
    const hi = raw ? Math.max(...all) : Math.max(...pts.map(p => p.max).filter(v => !missing(v)));
    const samples = raw ? all.length : pts.reduce((a, p) => a + (p.n || 0), 0);
    const mean = raw ? all.reduce((a, b) => a + b, 0) / Math.max(1, all.length)
        : pts.reduce((a, p) => a + (p.mean || 0) * (p.n || 0), 0) / Math.max(1, samples);
    const has = pts.length > 0;
    tiles.push(
        tile('Minimum', has ? withUnit(escapeHtml(fmtValue(lo)), unit) : '–', 'in this period'),
        tile('Average', has ? withUnit(escapeHtml(fmtValue(mean)), unit) : '–', 'in this period'),
        tile('Maximum', has ? withUnit(escapeHtml(fmtValue(hi)), unit) : '–', 'in this period'),
        tile('Values', fmtCount(samples), 'recorded in this period'),
    );

    // The shown values, newest first
    document.getElementById('entity-changes-title').textContent = raw ? 'Values' : 'Averages';
    document.getElementById('entity-changes-hint').textContent = pts.length > 500 ? 'newest 500' : '';
    const rows = pts.slice(-500).reverse();
    document.getElementById('entity-changes').innerHTML = rows.length ? `<table class="data"><thead><tr>${raw
        ? '<th>Time</th><th class="n">Value</th>'
        : '<th>Period</th><th class="n">Mean</th><th class="n">Min</th><th class="n">Max</th><th class="n">Samples</th>'}</tr></thead><tbody>${rows.map(p => raw
        ? `<tr><td class="muted">${escapeHtml(fmtDateTime(p.t))}</td><td class="n">${escapeHtml(fmtValue(p.v))}</td></tr>`
        : `<tr><td class="muted">${escapeHtml(bucketTitle(p.t, d.bucket))}</td><td class="n">${escapeHtml(fmtValue(p.mean))}</td><td class="n">${escapeHtml(fmtValue(p.min))}</td><td class="n">${escapeHtml(fmtValue(p.max))}</td><td class="n">${num(p.n)}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty-state">No values in this period</div>';
}

function bucketTitle(t, bucket) {
    if (bucket >= 86400) return fmtDay(t) + (new Date(t * 1000).getFullYear() !== new Date().getFullYear() ? ' ' + new Date(t * 1000).getFullYear() : '');
    return `${fmtDay(t)} ${fmtTime(t)} – ${fmtTime(t + bucket)}`;
}

function renderStates(d, tiles) {
    const pts = d.points;
    const el = document.getElementById('chart-entity');
    // Segments: each state lasts until the next change (the last one until now)
    const changes = d.before && (!pts.length || pts[0].t > d.from) ? [{t: d.from, state: d.before.state}, ...pts] : pts;
    const segments = changes.map((p, i) => ({from: Math.max(p.t, d.from), to: i + 1 < changes.length ? changes[i + 1].t : d.to, state: p.state}));

    // Time per state in this period; the colours follow that order
    const totals = {};
    segments.forEach(s => { totals[s.state] = (totals[s.state] || 0) + (s.to - s.from); });
    const span = Math.max(1, d.to - d.from);
    const order = Object.keys(totals).sort((a, b) => totals[b] - totals[a]);
    const stateColor = stateColors(order);
    timelineChart(el, {from: d.from, to: d.to, segments, colorOf: stateColor, height: 70});
    document.getElementById('legend-entity').innerHTML = order.map(s =>
        `<span><i style="background:${stateColor(s)}"></i>${escapeHtml(s || '(empty)')} · ${num(totals[s] / span * 100, 1)}% · ${escapeHtml(fmtDuration(totals[s]))}</span>`).join('');
    document.getElementById('entity-chart-title').textContent = 'States';
    document.getElementById('entity-chart-hint').textContent = `${num(pts.length)} changes${d.truncated ? ' (the newest)' : ''}`;

    const on = totals.on;
    tiles.push(
        tile('Changes', num(pts.length), 'in this period'),
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
