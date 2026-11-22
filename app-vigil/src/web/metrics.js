// Vigil dashboard - Metrics: pick a metric, filter on labels, aggregate, group, show the anomaly
// band, overlay several queries; add the chart to a dashboard or make an alert of it.

const METRIC_AGGS = ['avg', 'min', 'max', 'sum', 'rate', 'last'];
const METRIC_STEPS = [
    {key: '', label: 'auto'}, {key: '60', label: '1 min'}, {key: '300', label: '5 min'},
    {key: '900', label: '15 min'}, {key: '3600', label: '1 h'}, {key: '86400', label: '1 day'},
];
const QUERY_KEYS = 'ABCDEFGH';
const metrics = {
    names: [], namesQuery: null, labels: {}, queries: [], active: 0, step: '', lastResult: null, loadSeq: 0,
};

const blankQuery = () => ({name: '', filter: {}, agg: 'avg', group: '', anomaly: false});

function normaliseQuery(q) {
    const out = blankQuery();
    if (!q || typeof q !== 'object') return out;
    out.name = String(q.name || '');
    out.filter = q.filter && typeof q.filter === 'object' ? {...q.filter} : {};
    out.agg = METRIC_AGGS.includes(q.agg) ? q.agg : 'avg';
    out.group = q.group ? String(q.group) : '';
    out.anomaly = Boolean(q.anomaly);
    return out;
}

// The hash holds the queries (as JSON), so a link opens the same chart
function metricsHashArg() {
    const qs_ = metrics.queries.filter(q => q.name).map(q => {
        const o = {name: q.name};
        if (Object.keys(q.filter).length) o.filter = q.filter;
        if (q.agg !== 'avg') o.agg = q.agg;
        if (q.group) o.group = q.group;
        if (q.anomaly) o.anomaly = 1;
        return o;
    });
    return qs_.length ? JSON.stringify(qs_) : '';
}

// From other pages (Sources): the metric list for this search, such as job=freqle
function openMetricSearch(text) {
    document.getElementById('metric-search').value = text;
    metrics.namesQuery = null;
    switchTab('metrics', metricsHashArg());
}

// The label=value words of the metric search (job=freqle), as the server reads them
function searchLabels(text) {
    const labels = {};
    for (const word of String(text || '').split(/\s+/)) {
        const at = word.indexOf('=');
        if (at > 0 && at < word.length - 1 && /^[A-Za-z0-9_.\-]{1,64}$/.test(word.slice(0, at))) labels[word.slice(0, at)] = word.slice(at + 1);
    }
    return labels;
}

// From other pages (a dashboard panel, What changed?): this query in the explorer
function openMetrics(queries) {
    metrics.queries = (Array.isArray(queries) ? queries : [queries]).map(normaliseQuery);
    switchTab('metrics', metricsHashArg());
}

async function showMetrics(arg) {
    if (arg && arg !== metricsHashArg()) {
        try {
            const parsed = JSON.parse(arg);
            metrics.queries = (Array.isArray(parsed) ? parsed : [parsed]).map(normaliseQuery);
        } catch (e) { /* a broken link: keep what was there */ }
    }
    if (!metrics.queries.length) metrics.queries = [blankQuery()];
    buildStepSelect();
    loadMetricNames(document.getElementById('metric-search').value.trim());
    await Promise.all(metrics.queries.filter(q => q.name).map(q => loadLabels(q.name)));
    renderMetricQueries();
    loadMetricChart();
}

function buildStepSelect() {
    const el = document.getElementById('metric-step');
    el.innerHTML = METRIC_STEPS.map(s => `<option value="${s.key}"${s.key === metrics.step ? ' selected' : ''}>${s.label}</option>`).join('');
    el.onchange = () => { metrics.step = el.value; loadMetricChart(); };
}

// ---------------------------------------------------------------------
// The name picker
// ---------------------------------------------------------------------
async function loadMetricNames(q) {
    const el = document.getElementById('metric-names');
    if (!ready()) { el.innerHTML = emptyHtml('The database is not connected'); return; }
    if (metrics.namesQuery === q && metrics.names.length) return renderMetricNames();
    try {
        const data = await api('metrics/names?' + qs({q}));
        metrics.names = data.names || [];
        metrics.namesQuery = q;
    } catch (e) {
        el.innerHTML = errorHtml(e.message);
        return;
    }
    renderMetricNames();
    renderNameOptions();
}

function renderMetricNames() {
    const el = document.getElementById('metric-names');
    const used = new Set(metrics.queries.map(q => q.name));
    el.innerHTML = metrics.names.length ? metrics.names.map((n, i) => `
        <button type="button" class="metric-name-row${used.has(n.name) ? ' active' : ''}" data-i="${i}" title="${escapeHtml(n.name)}">
            <span class="nm">${escapeHtml(n.name)}</span>
            <small>${num(n.series)} series${n.unit ? ' · ' + escapeHtml(n.unit) : ''}${n.kind ? ' · ' + escapeHtml(n.kind) : ''}</small>
        </button>`).join('')
        : emptyHtml(metrics.namesQuery ? 'No metric matches' : noDataYet() ? 'No metrics yet: add a scrape target or the FRITZ!Box in the app\'s options (see Sources)' : 'No metrics yet');
    el.onclick = (e) => {
        const b = e.target.closest('[data-i]');
        if (b) pickMetricName(metrics.names[Number(b.dataset.i)]);
    };
}

function renderNameOptions() {
    let list = document.getElementById('metric-name-options');
    if (!list) {
        list = document.createElement('datalist');
        list.id = 'metric-name-options';
        document.body.appendChild(list);
    }
    list.innerHTML = metrics.names.map(n => `<option value="${escapeHtml(n.name)}"></option>`).join('');
}

async function pickMetricName(n) {
    const i = Math.min(metrics.active, metrics.queries.length - 1);
    const q = metrics.queries[i];
    q.name = n.name;
    // Found with job=freqle: the chart shows freqle's series of it
    q.filter = searchLabels(document.getElementById('metric-search').value);
    q.group = '';
    q.agg = n.kind === 'counter' ? 'rate' : 'avg';
    await loadLabels(n.name);
    renderMetricQueries();
    renderMetricNames();
    loadMetricChart();
}

let metricSearchTimer = null;
document.getElementById('metric-search').addEventListener('input', (e) => {
    clearTimeout(metricSearchTimer);
    metricSearchTimer = setTimeout(() => loadMetricNames(e.target.value.trim()), 250);
});

async function loadLabels(name) {
    if (!name || metrics.labels[name]) return;
    try {
        const data = await api('metrics/labels?' + qs({name}));
        metrics.labels[name] = data.labels || {};
    } catch (e) {
        metrics.labels[name] = {};
    }
}

// ---------------------------------------------------------------------
// The queries
// ---------------------------------------------------------------------
function renderMetricQueries() {
    const el = document.getElementById('metric-queries');
    el.innerHTML = metrics.queries.map((q, i) => {
        const labels = metrics.labels[q.name] || {};
        const keys = Object.keys(labels).sort();
        const chips = Object.entries(q.filter).map(([k, v]) =>
            `<span class="filter-chip"><span class="muted">${escapeHtml(k)}</span> = ${escapeHtml(v)}<button type="button" data-unfilter="${escapeHtml(k)}" aria-label="Remove">×</button></span>`).join('');
        const addOptions = keys.filter(k => !(k in q.filter)).map(k =>
            `<optgroup label="${escapeHtml(k)}">${(labels[k] || []).slice(0, 200).map(v => `<option value="${escapeHtml(JSON.stringify([k, v]))}">${escapeHtml(k)} = ${escapeHtml(v)}</option>`).join('')}</optgroup>`).join('');
        return `<div class="metric-query${i === metrics.active ? ' active' : ''}" data-q="${i}">
            <span class="q-key" style="background:${seriesColor(i)}">${QUERY_KEYS[i]}</span>
            <input type="text" class="input metric-name" list="metric-name-options" value="${escapeHtml(q.name)}" placeholder="Pick a metric on the left, or type its name" aria-label="Metric name" spellcheck="false" autocomplete="off">
            <select class="select" data-field="agg" aria-label="Aggregation">${METRIC_AGGS.map(a => `<option${a === q.agg ? ' selected' : ''}>${a}</option>`).join('')}</select>
            <select class="select" data-field="group" aria-label="Group by"><option value="">no grouping</option>${keys.map(k => `<option value="${escapeHtml(k)}"${k === q.group ? ' selected' : ''}>by ${escapeHtml(k)}</option>`).join('')}</select>
            <label class="action-check small-check" title="Mean ± 3 sigma of the same time over the previous days; points outside are marked"><input type="checkbox" data-field="anomaly"${q.anomaly ? ' checked' : ''}> Anomaly band</label>
            ${metrics.queries.length > 1 ? `<button type="button" class="btn btn-small" data-remove-query="${i}" title="Remove this query">×</button>` : ''}
            <div class="metric-filters">${chips}${addOptions ? `<select class="select filter-add" aria-label="Add a label filter"><option value="">+ label filter</option>${addOptions}</select>` : q.name ? '<span class="muted small-label">no labels</span>' : ''}</div>
        </div>`;
    }).join('');
}

(function wireMetricQueries() {
    const el = document.getElementById('metric-queries');
    const queryOf = target => {
        const row = target.closest('[data-q]');
        return row ? Number(row.dataset.q) : -1;
    };
    el.addEventListener('focusin', (e) => {
        const i = queryOf(e.target);
        if (i < 0 || i === metrics.active) return;
        metrics.active = i;
        el.querySelectorAll('.metric-query').forEach((r, k) => r.classList.toggle('active', k === i));
    });
    el.addEventListener('change', async (e) => {
        const i = queryOf(e.target);
        if (i < 0) return;
        const q = metrics.queries[i];
        if (e.target.classList.contains('metric-name')) {
            const name = e.target.value.trim();
            if (name === q.name) return;
            q.name = name;
            q.filter = {};
            q.group = '';
            await loadLabels(name);
            renderMetricQueries();
            renderMetricNames();
        } else if (e.target.classList.contains('filter-add')) {
            if (!e.target.value) return;
            const [k, v] = JSON.parse(e.target.value);
            q.filter[k] = v;
            renderMetricQueries();
        } else if (e.target.dataset.field === 'anomaly') {
            q.anomaly = e.target.checked;
        } else if (e.target.dataset.field) {
            q[e.target.dataset.field] = e.target.value;
        }
        loadMetricChart();
    });
    el.addEventListener('click', (e) => {
        const un = e.target.closest('[data-unfilter]');
        const rm = e.target.closest('[data-remove-query]');
        if (un) {
            delete metrics.queries[queryOf(un)].filter[un.dataset.unfilter];
            renderMetricQueries();
            loadMetricChart();
        } else if (rm) {
            metrics.queries.splice(Number(rm.dataset.removeQuery), 1);
            metrics.active = Math.min(metrics.active, metrics.queries.length - 1);
            renderMetricQueries();
            renderMetricNames();
            loadMetricChart();
        }
    });
})();

function addMetricQuery() {
    if (metrics.queries.length >= QUERY_KEYS.length) return toast(`At most ${QUERY_KEYS.length} queries`, 'error');
    metrics.queries.push(blankQuery());
    metrics.active = metrics.queries.length - 1;
    renderMetricQueries();
    const inputs = document.querySelectorAll('#metric-queries .metric-name');
    inputs[inputs.length - 1].focus();
}

// ---------------------------------------------------------------------
// Fetching and drawing: shared with the dashboards
// ---------------------------------------------------------------------
function metricQueryString(q, w, step) {
    return qs({
        name: q.name,
        filter: Object.keys(q.filter || {}).length ? q.filter : undefined,
        agg: q.agg || 'avg',
        group: q.group || undefined,
        from: w.from, to: w.to,
        step: step || undefined,
        anomaly: q.anomaly ? 1 : 0,
    });
}

// Several queries on one time axis: {x, series: [{name, color, values, unit, band, anomalies, kind}], step, units}
async function fetchMetricSeries(queries, w, step) {
    const results = await Promise.all(queries.map(q => api('metrics/query?' + metricQueryString(q, w, step))));
    const times = new Set();
    results.forEach(r => (r.t || []).forEach(t => times.add(t)));
    const x = [...times].sort((a, b) => a - b);
    const pos = new Map(x.map((t, i) => [t, i]));
    const multi = queries.length > 1;
    const aligned = results.some(r => (r.t || []).length !== x.length);
    const series = [];
    results.forEach((r, qi) => {
        const t = r.t || [];
        const map = arr => {
            const out = new Array(x.length).fill(aligned ? undefined : null);
            (arr || []).forEach((v, k) => { out[pos.get(t[k])] = v; });
            return out;
        };
        (r.series || []).forEach(s => {
            const base = s.label || queries[qi].name;
            series.push({
                name: multi ? `${QUERY_KEYS[qi]}: ${base}` : base,
                color: seriesColor(series.length),
                values: map(s.values),
                unit: r.unit || '',
                band: s.band ? {lo: map(s.band.lo), hi: map(s.band.hi)} : undefined,
                bandLabel: 'expected',
                anomalies: (s.anomalies || []).map(k => pos.get(t[k])).filter(k => k !== undefined),
            });
        });
    });
    return {x, series, aligned, step: results.length ? results[0].step : null, unit: results.length ? results[0].unit : ''};
}

async function loadMetricChart() {
    updateHash(metricsHashArg());
    const el = document.getElementById('chart-metrics');
    const queries = metrics.queries.filter(q => q.name);
    const title = document.getElementById('metric-chart-title');
    const hint = document.getElementById('metric-chart-hint');
    if (!ready()) return emptyChart(el, 'The database is not connected');
    if (!queries.length) {
        title.textContent = 'Metric';
        hint.textContent = '';
        metrics.lastResult = null;
        return emptyChart(el, 'Pick a metric on the left');
    }
    const seq = ++metrics.loadSeq;
    const w = rangeWindow();
    let result;
    el.classList.add('stale');
    try {
        result = await fetchMetricSeries(queries, w, metrics.step);
    } catch (e) {
        el.classList.remove('stale');
        if (seq === metrics.loadSeq) emptyChart(el, e.message);
        return;
    }
    el.classList.remove('stale');
    if (seq !== metrics.loadSeq) return;
    metrics.lastResult = result;
    title.textContent = queries.map(q => `${q.agg}(${q.name})${q.group ? ' by ' + q.group : ''}`).join(' · ');
    const anomalies = result.series.reduce((a, s) => a + s.anomalies.length, 0);
    hint.textContent = [
        `${result.series.length} series`,
        result.step ? `step ${fmtDuration(result.step)}` : '',
        anomalies ? `${anomalies} anomalous points` : '',
        'drag to zoom',
    ].filter(Boolean).join(' · ');
    timeChart(el, {
        x: result.x,
        series: result.series,
        xRange: [w.from, w.to],
        aligned: result.aligned,
        height: 320,
        title: title.textContent,
        filename: 'vigil-metric',
        format: (v, s) => fmtMetric(v, s && s.unit),
        formatAxis: v => axisNumber(v),
        onZoom: zoomRange,
        zoomed: rangeZoomed(),
        onReset: rangeBack,
        empty: 'No data for this metric in the time range',
    });
}

// The last value of the first series, for "Alert on this"
function lastMetricValue() {
    const s = metrics.lastResult && metrics.lastResult.series[0];
    if (!s) return null;
    for (let i = s.values.length - 1; i >= 0; i--) if (!missing(s.values[i])) return s.values[i];
    return null;
}

function metricAlert() {
    const q = metrics.queries.find(x => x.name);
    if (!q) return toast('Pick a metric first', 'error');
    const last = lastMetricValue();
    openRuleEditor({
        name: `${q.name}${Object.keys(q.filter).length ? ' ' + Object.entries(q.filter).map(([k, v]) => `${k}=${v}`).join(',') : ''}`.slice(0, 80),
        kind: q.anomaly ? 'anomaly' : 'metric',
        query: {name: q.name, filter: q.filter, agg: q.agg === 'rate' ? 'rate' : q.agg === 'last' ? 'last' : 'avg', window: 300},
        condition: q.anomaly ? {op: '>', threshold: 3} : {op: '>', threshold: last === null ? 0 : Number(Number(last).toPrecision(3))},
        for_seconds: 300,
        severity: 'warning',
        enabled: true,
        notify: true,
    });
}

function metricToDashboard() {
    const queries = metrics.queries.filter(q => q.name).map(q => {
        const o = {name: q.name, filter: q.filter, agg: q.agg};
        if (q.group) o.group = q.group;
        if (q.anomaly) o.anomaly = true;
        return o;
    });
    if (!queries.length) return toast('Pick a metric first', 'error');
    addPanelToDashboard({
        title: document.getElementById('metric-chart-title').textContent.slice(0, 80),
        type: 'timeseries',
        query: queries.length === 1 ? queries[0] : {queries},
        w: 2, h: 1,
    });
}

onTab('metrics', showMetrics);
onRange('metrics', loadMetricChart);
