// Chronicle dashboard - Query: read-only SQL against the database, with described examples, a
// history of what was run (with favourites), CSV and a chart. The examples live in
// query-examples.json, which the tests also run against a database.

const TABLE_ROWS = 2000;  // rows put in the page; the CSV has all of them
const TIME_TYPES = ['timestamptz', 'timestamp', 'date'];
const NUMBER_TYPES = ['int2', 'int4', 'int8', 'float4', 'float8', 'numeric'];
const CHART_SERIES = 8;

const HISTORY_SIZE = 50;  // queries kept besides the favourites
const HISTORY_KEY = LS + 'query_history';

let queryExamples = [];
let querySide = 'examples';
let queryResult = null;
let queryAbort = null;
let queryChart = true;  // a result with a time and a number column is drawn; the button hides it

async function showQuery() {
    const editor = document.getElementById('query-sql');
    if (!editor.value) {
        let saved = null;
        try { saved = localStorage.getItem(LS + 'query'); } catch (e) { /* only a convenience */ }
        editor.value = saved || '';
    }
    if (!queryExamples.length) {
        try {
            const response = await fetch(basePath + 'web/query-examples.json?v=' + encodeURIComponent(app.version));
            queryExamples = await response.json();
        } catch (e) {
            queryExamples = [];
        }
    }
    if (!editor.value && queryExamples.length) editor.value = queryExamples[3].sql;
    renderQuerySide();
    // #query/4 fills in and runs the fourth example (a link to a ready answer)
    const example = Number(window.location.hash.slice(1).split('/')[1]);
    if (example >= 1 && example <= queryExamples.length) {
        history.replaceState(null, '', '#query');
        editor.value = queryExamples[example - 1].sql;
        runQuery();
    }
}

// ---------------------------------------------------------------------
// Side panel: examples and history
// ---------------------------------------------------------------------
function switchQuerySide(which) {
    querySide = which;
    renderQuerySide();
}

function renderQuerySide() {
    document.querySelectorAll('#query-side-tabs .tab').forEach(t => t.classList.toggle('active', t.dataset.side === querySide));
    const el = document.getElementById('query-side');
    if (querySide === 'examples') {
        el.innerHTML = queryExamples.length ? queryExamples.map((ex, i) => `
            <button type="button" class="side-item example" data-example="${i}">
                <b>${escapeHtml(ex.title)}</b>
                <span>${placeholders(escapeHtml(ex.description))}</span>
            </button>`).join('') + '<p class="panel-sub side-note">A click puts the example in the editor; run it with Run or Ctrl+Enter. Fill in what is between &lt; &gt; first.</p>'
            : '<div class="empty-state">The examples could not be loaded</div>';
        return;
    }
    const items = loadHistory();
    el.innerHTML = items.length ? items.map((h, i) => `
        <div class="side-item history${h.fav ? ' fav' : ''}" data-history="${i}">
            <button type="button" class="star" data-star="${i}" title="${h.fav ? 'Remove from favourites' : 'Keep as favourite'}" aria-label="Favourite" aria-pressed="${h.fav ? 'true' : 'false'}">${h.fav ? '★' : '☆'}</button>
            <button type="button" class="history-body" data-use="${i}" title="Put this query in the editor">
                <span class="preview">${escapeHtml(preview(h.sql))}</span>
                <span class="meta">${escapeHtml(fmtAgo(h.at))} · ${h.error ? '<span class="sb-bad">error</span>' : `${num(h.rows)} ${h.rows === 1 ? 'row' : 'rows'}`}</span>
            </button>
            <button type="button" class="remove" data-remove="${i}" title="Remove from the history" aria-label="Remove">✕</button>
        </div>`).join('') + `<div class="side-actions"><button type="button" class="btn btn-small" onclick="clearHistory()"${items.some(h => !h.fav) ? '' : ' disabled'}>Clear history</button><span class="panel-sub">favourites stay</span></div>`
        : '<div class="empty-state">The queries you run appear here. ☆ keeps one as a favourite at the top.</div>';
}

// <entity_id> and the like stand out in the descriptions
function placeholders(html) {
    return html.replace(/&lt;([a-z_]+)&gt;/g, '<code>&lt;$1&gt;</code>');
}

// The first line that isn't a comment, shortened
function preview(sql) {
    const line = sql.split('\n').map(l => l.trim()).find(l => l && !l.startsWith('--')) || sql.trim();
    const text = sql.replace(/--[^\n]*/g, ' ').replace(/\s+/g, ' ').trim() || line;
    return text.length > 90 ? text.slice(0, 88) + '…' : text;
}

// ---------------------------------------------------------------------
// History: favourites first (never pushed out), then the last HISTORY_SIZE runs
// ---------------------------------------------------------------------
function loadHistory() {
    let items = [];
    try { items = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch (e) { items = []; }
    if (!Array.isArray(items)) items = [];
    items = items.filter(h => h && typeof h.sql === 'string');
    return items.filter(h => h.fav).sort((a, b) => b.at - a.at).concat(items.filter(h => !h.fav).sort((a, b) => b.at - a.at));
}

function saveHistory(items) {
    const favourites = items.filter(h => h.fav);
    const rest = items.filter(h => !h.fav).sort((a, b) => b.at - a.at).slice(0, HISTORY_SIZE);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(favourites.concat(rest))); } catch (e) { /* only a convenience */ }
}

function rememberQuery(sql, rows, error) {
    const text = sql.trim();
    if (!text) return;
    const items = loadHistory();
    // The same statement again moves to the top, and stays a favourite if it was one
    const same = items.find(h => h.sql.trim() === text);
    const rest = items.filter(h => h !== same);
    rest.push({sql: text, at: Date.now() / 1000, rows: error ? null : rows, error: Boolean(error), fav: Boolean(same && same.fav)});
    saveHistory(rest);
    if (querySide === 'history') renderQuerySide();
}

function toggleFavourite(i) {
    const items = loadHistory();
    if (!items[i]) return;
    items[i].fav = !items[i].fav;
    saveHistory(items);
    renderQuerySide();
}

function removeHistory(i) {
    const items = loadHistory();
    items.splice(i, 1);
    saveHistory(items);
    renderQuerySide();
}

function clearHistory() {
    saveHistory(loadHistory().filter(h => h.fav));
    renderQuerySide();
}

function fillEditor(sql) {
    const editor = document.getElementById('query-sql');
    editor.value = sql;
    editor.focus();
    // Straight to the first placeholder, so it can be typed over
    const at = sql.indexOf('<');
    if (at >= 0) editor.setSelectionRange(at, sql.indexOf('>', at) + 1);
    else editor.setSelectionRange(0, 0);
    editor.scrollTop = 0;
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
            // Refused or failed in the database: still worth keeping, to fix it later
            if (response.status === 400) rememberQuery(sql, null, true);
            return;
        }
        queryResult = data;
        renderQueryResult();
        rememberQuery(sql, data.rows.length, false);
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
    const number = v => (v === null || v === undefined || v === '' ? null : Number(v));
    let x, series, aligned = false;
    if (plan.entity >= 0) {
        // A line per entity: each with its own times, aligned on one axis
        const names = [...new Set(rows.map(p => String(p.row[plan.entity])))].slice(0, CHART_SERIES);
        const tables = names.map(name => {
            const own = rows.filter(p => String(p.row[plan.entity]) === name);
            return [own.map(p => p.t), own.map(p => number(p.row[plan.numbers[0]]))];
        });
        const joined = tables.length > 1 ? uPlot.join(tables, tables.map(() => [1])) : tables[0];
        x = Array.from(joined[0]);
        series = names.map((name, k) => ({name, color: colors[k % colors.length], values: Array.from(joined[k + 1])}));
        aligned = names.length > 1;
    } else {
        x = rows.map(p => p.t);
        series = plan.numbers.slice(0, CHART_SERIES).map((col, k) => ({
            name: r.columns[col].name, color: colors[k % colors.length], values: rows.map(p => number(p.row[col])),
        }));
    }
    timeChart(document.getElementById('query-chart'), {
        x, series, aligned, height: 280,
        title: 'Query result',
        filename: 'chronicle-query',
        empty: 'Nothing to draw: the number columns are empty',
    });
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
document.getElementById('query-side').addEventListener('click', (e) => {
    const target = e.target.closest('[data-example], [data-star], [data-use], [data-remove]');
    if (!target) return;
    const d = target.dataset;
    if (d.example !== undefined) fillEditor(queryExamples[Number(d.example)].sql);
    else if (d.star !== undefined) toggleFavourite(Number(d.star));
    else if (d.remove !== undefined) removeHistory(Number(d.remove));
    else if (d.use !== undefined) fillEditor(loadHistory()[Number(d.use)].sql);
});
onTab('query', showQuery);
