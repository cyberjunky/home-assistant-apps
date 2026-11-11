// Chronicle dashboard - Query: read-only SQL against the database, with examples, CSV and a chart.

const QUERY_EXAMPLES = [
    {label: 'Last day of one entity', sql: `-- Every recorded value of one entity in the last 24 hours
SELECT time, state, value
FROM chronicle.history
WHERE entity_id = 'sensor.outdoor_temperature'
  AND time > now() - INTERVAL '1 day'
ORDER BY time`},
    {label: 'Daily averages for a year', sql: `-- Mean, minimum and maximum per day (local days), from the daily averages
SELECT d.bucket AS day, round(d.mean::numeric, 2) AS mean, d.min, d.max
FROM chronicle.states_daily d
JOIN chronicle.entities e ON e.id = d.entity
WHERE e.entity_id = 'sensor.outdoor_temperature'
  AND d.bucket > now() - INTERVAL '1 year'
ORDER BY d.bucket`},
    {label: 'Busiest entities (24 h)', sql: `-- Which entities wrote the most rows in the last 24 hours
SELECT e.entity_id, e.name, count(*) AS rows
FROM chronicle.states s
JOIN chronicle.entities e ON e.id = s.entity
WHERE s.time > now() - INTERVAL '24 hours'
GROUP BY e.entity_id, e.name
ORDER BY rows DESC
LIMIT 20`},
    {label: 'Hours on per day', sql: `-- How long a switch or binary sensor was on per day, from its on/off changes.
-- Each state lasts until the next change; a stretch past midnight counts for the day it started.
WITH changes AS (
    SELECT s.time, s.value,
           lead(s.time, 1, now()) OVER (ORDER BY s.time) AS until
    FROM chronicle.states s
    JOIN chronicle.entities e ON e.id = s.entity
    WHERE e.entity_id = 'switch.washing_machine'
      AND s.time > now() - INTERVAL '30 days'
)
SELECT date_trunc('day', time) AS day,
       round((sum(extract(epoch FROM until - time)) FILTER (WHERE value = 1) / 3600)::numeric, 2) AS hours_on
FROM changes
GROUP BY 1
ORDER BY 1`},
    {label: 'Attribute over time', sql: `-- One attribute's value over time, here the target temperature of a thermostat
SELECT time, state, (attributes->>'temperature')::numeric AS target
FROM chronicle.history
WHERE entity_id = 'climate.thermostat'
  AND attributes ? 'temperature'
ORDER BY time DESC
LIMIT 500`},
    {label: 'Table sizes', sql: `-- The size of every hypertable in the database: the history and the averages
SELECT format('%I.%I', hypertable_schema, hypertable_name) AS hypertable,
       pg_size_pretty(hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass)) AS size,
       num_chunks,
       compression_enabled
FROM timescaledb_information.hypertables
ORDER BY hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass) DESC`},
];
const TABLE_ROWS = 2000;  // rows put in the page; the CSV has all of them
const TIME_TYPES = ['timestamptz', 'timestamp', 'date'];
const NUMBER_TYPES = ['int2', 'int4', 'int8', 'float4', 'float8', 'numeric'];
const CHART_SERIES = 8;

let queryResult = null;
let queryAbort = null;
let queryChart = true;  // a result with a time and a number column is drawn; the button hides it

function showQuery() {
    const editor = document.getElementById('query-sql');
    if (!editor.value) {
        let saved = null;
        try { saved = localStorage.getItem(LS + 'query'); } catch (e) { /* only a convenience */ }
        editor.value = saved || QUERY_EXAMPLES[0].sql;
    }
    document.getElementById('query-examples').innerHTML = QUERY_EXAMPLES.map((ex, i) =>
        `<button type="button" class="btn btn-small" data-example="${i}">${escapeHtml(ex.label)}</button>`).join('');
    // #query/3 opens and runs the third example
    const example = Number(window.location.hash.slice(1).split('/')[1]);
    if (example >= 1 && example <= QUERY_EXAMPLES.length) {
        history.replaceState(null, '', '#query');
        useExample(example - 1);
    }
}

function useExample(i) {
    const editor = document.getElementById('query-sql');
    editor.value = QUERY_EXAMPLES[i].sql;
    editor.focus();
    runQuery();
}

async function runQuery() {
    if (queryAbort) return;
    const sql = document.getElementById('query-sql').value;
    try { localStorage.setItem(LS + 'query', sql); } catch (e) { /* only a convenience */ }
    const status = document.getElementById('query-status');
    const error = document.getElementById('query-error');
    error.hidden = true;
    status.textContent = 'Running…';
    setQueryRunning(true);
    queryAbort = new AbortController();
    try {
        const response = await fetch(basePath + 'api/query', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({sql}),
            signal: queryAbort.signal,
        });
        let data = null;
        try { data = await response.json(); } catch (e) { /* not JSON */ }
        if (!response.ok) {
            queryResult = null;
            renderQueryError(data || {error: `Request failed (${response.status})`}, sql);
            status.textContent = '';
            return;
        }
        queryResult = data;
        renderQueryResult();
    } catch (e) {
        status.textContent = e.name === 'AbortError' ? 'Cancelled' : '';
        if (e.name !== 'AbortError') renderQueryError({error: e.message}, sql);
    } finally {
        queryAbort = null;
        setQueryRunning(false);
    }
}

function cancelQuery() {
    if (queryAbort) queryAbort.abort();
}

function setQueryRunning(running) {
    document.getElementById('query-run').disabled = running;
    document.getElementById('query-cancel').hidden = !running;
}

function renderQueryError(data, sql) {
    const el = document.getElementById('query-error');
    let where = '';
    // PostgreSQL gives the position (1-based character) of the error in the query
    if (data.position) {
        const pos = Number(data.position) - 1;
        const before = sql.slice(0, pos);
        const line = before.split('\n').length;
        where = ` (line ${line}, column ${pos - before.lastIndexOf('\n')})`;
    }
    el.innerHTML = `<b>${escapeHtml(data.error)}</b>${escapeHtml(where)}`
        + (data.detail ? `<div>${escapeHtml(data.detail)}</div>` : '')
        + (data.hint ? `<div class="muted">Hint: ${escapeHtml(data.hint)}</div>` : '');
    el.hidden = false;
    document.getElementById('query-result').innerHTML = '';
    document.getElementById('query-chart-wrap').hidden = true;
    document.getElementById('query-csv').hidden = true;
    document.getElementById('query-chart-toggle').hidden = true;
}

function cellText(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

function renderQueryResult() {
    const r = queryResult;
    const status = document.getElementById('query-status');
    const rows = r.rows.length;
    status.innerHTML = `${num(rows)} ${rows === 1 ? 'row' : 'rows'} · ${num(r.duration_ms)} ms`
        + (r.truncated ? ` · <span class="sb-warn">first ${num(r.limit)} rows</span>` : '');
    document.getElementById('query-csv').hidden = !r.columns.length;
    const el = document.getElementById('query-result');
    if (!r.columns.length) {
        el.innerHTML = '<div class="empty-state">The statement returned no columns</div>';
        document.getElementById('query-chart-toggle').hidden = true;
        document.getElementById('query-chart-wrap').hidden = true;
        return;
    }
    const numeric = r.columns.map(c => NUMBER_TYPES.includes(c.type));
    const head = r.columns.map((c, i) => `<th class="${numeric[i] ? 'n' : ''}" title="${escapeHtml(c.type)}">${escapeHtml(c.name)}</th>`).join('');
    const body = r.rows.slice(0, TABLE_ROWS).map(row => `<tr>${row.map((v, i) =>
        `<td class="${numeric[i] ? 'n' : v !== null && typeof v === 'object' ? 'mono wrap' : ''}">${v === null ? '<span class="muted">NULL</span>' : escapeHtml(cellText(v))}</td>`).join('')}</tr>`).join('');
    el.innerHTML = `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
        + (rows > TABLE_ROWS ? `<p class="panel-sub" style="padding:8px 12px">The table shows the first ${num(TABLE_ROWS)} rows; the CSV has all ${num(rows)}.</p>` : '');

    const chart = chartPlan(r);
    document.getElementById('query-chart-toggle').hidden = !chart;
    document.getElementById('query-chart-toggle').classList.toggle('active', queryChart);
    document.getElementById('query-chart-wrap').hidden = !(chart && queryChart);
    if (chart && queryChart) drawQueryChart(r, chart);
}

// A chart is possible with a time column and at least one number column. With an entity_id
// column and one number column there is a line per entity, else a line per number column.
function chartPlan(r) {
    const time = r.columns.findIndex(c => TIME_TYPES.includes(c.type));
    if (time < 0 || !r.rows.length) return null;
    const numbers = r.columns.map((c, i) => i).filter(i => i !== time && NUMBER_TYPES.includes(r.columns[i].type));
    if (!numbers.length) return null;
    const entity = r.columns.findIndex(c => c.name === 'entity_id');
    return {time, numbers, entity: entity >= 0 && numbers.length === 1 ? entity : -1};
}

function drawQueryChart(r, plan) {
    const colors = [1, 2, 3, 4, 5].map(i => cssVar(`--series-${i}`)).concat(['--seq-2', '--seq-4', '--seq-5'].map(cssVar));
    const rows = r.rows.map(row => ({t: Date.parse(row[plan.time]) / 1000, row})).filter(p => !Number.isNaN(p.t)).sort((a, b) => a.t - b.t);
    const xs = [...new Set(rows.map(p => p.t))];
    const index = new Map(xs.map((t, i) => [t, i]));
    let series;
    if (plan.entity >= 0) {
        const names = [...new Set(rows.map(p => p.row[plan.entity]))].slice(0, CHART_SERIES);
        series = names.map((name, k) => ({name: String(name), color: colors[k % colors.length], values: new Array(xs.length).fill(null)}));
        const byName = new Map(series.map(s => [s.name, s]));
        rows.forEach(p => {
            const s = byName.get(String(p.row[plan.entity]));
            if (s) s.values[index.get(p.t)] = p.row[plan.numbers[0]];
        });
    } else {
        series = plan.numbers.slice(0, CHART_SERIES).map((col, k) => ({name: r.columns[col].name, color: colors[k % colors.length], values: new Array(xs.length).fill(null)}));
        rows.forEach(p => series.forEach((s, k) => { s.values[index.get(p.t)] = p.row[plan.numbers[k]]; }));
    }
    const el = document.getElementById('query-chart');
    const span = xs.length > 1 ? xs[xs.length - 1] - xs[0] : 0;
    lineChart(el, {
        x: xs, series, height: 260, connect: true,
        formatX: t => span > 3 * 86400 ? fmtDateTime(t) : `${fmtDay(t)} ${fmtTimeSec(t)}`,
        formatY: v => fmtValue(v),
        xTicks: xs.length > 1 ? smartTicks(xs[0], xs[xs.length - 1], el.clientWidth) : [],
        empty: 'Nothing to draw',
    });
    document.getElementById('query-legend').innerHTML = legendHtml(series.map(s => ({name: s.name, color: s.color, kind: 'line'})));
}

function toggleQueryChart() {
    queryChart = !queryChart;
    if (queryResult) renderQueryResult();
}

function downloadQueryCsv() {
    if (!queryResult) return;
    const quote = v => {
        const text = cellText(v);
        return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const lines = [queryResult.columns.map(c => quote(c.name)).join(',')]
        .concat(queryResult.rows.map(row => row.map(quote).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], {type: 'text/csv'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'chronicle-query.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

document.getElementById('query-sql').addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        runQuery();
    }
});
document.getElementById('query-examples').addEventListener('click', (e) => {
    const button = e.target.closest('[data-example]');
    if (button) useExample(Number(button.dataset.example));
});
onTab('query', showQuery);
