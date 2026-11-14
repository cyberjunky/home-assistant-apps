// Chronicle dashboard - SQL: scripts against any database, as in pgAdmin. A session keeps its
// connection, so a transaction stays open between runs (BEGIN in the script, or Auto-commit
// off) until Commit or Rollback. Statements that change something are shown first and run
// after a confirmation. Examples (query-examples.json, also run by the tests) fill in their
// <entity_id> from a picker; the history keeps what was run, with favourites.

const TABLE_ROWS = 2000;  // rows put in the page; the CSV has all of them
const TIME_TYPES = ['timestamptz', 'timestamp', 'date'];
const NUMBER_TYPES = ['int2', 'int4', 'int8', 'float4', 'float8', 'numeric'];
const CHART_SERIES = 8;
const TIMEOUT_CHOICES = [[30, '30 s'], [60, '1 min'], [300, '5 min'], [1800, '30 min'], [3600, '1 h']];

const HISTORY_SIZE = 50;  // queries kept besides the favourites
const HISTORY_KEY = LS + 'query_history';

const sql = {
    session: null,        // the server's session id (its connection)
    inTransaction: false,
    running: false,
    confirmedAll: false,  // "don't ask again" until the page is closed
    results: [],          // the results of the last run, one per statement
    shown: 0,             // which result the grid shows
};
let queryExamples = [];
let querySide = 'examples';
let queryChart = true;  // a result with a time and a number column is drawn; the button hides it

async function showQuery() {
    const editor = document.getElementById('query-sql');
    if (!editor.value) {
        let saved = null;
        try { saved = localStorage.getItem(LS + 'query'); } catch (e) { /* only a convenience */ }
        editor.value = saved || '';
    }
    await fillDatabaseSelect();
    if (!queryExamples.length) {
        try {
            const response = await fetch(basePath + 'web/query-examples.json?v=' + encodeURIComponent(app.version));
            queryExamples = await response.json();
        } catch (e) {
            queryExamples = [];
        }
    }
    if (!editor.value && queryExamples.length) {
        const busiest = queryExamples.find(e => e.title.startsWith('Busiest entities')) || queryExamples[0];
        editor.value = busiest.sql;
    }
    renderQuerySide();
    renderTransaction();
    // #query/4 fills in and runs the fourth example (a link to a ready answer)
    const example = Number(window.location.hash.slice(1).split('/')[1]);
    if (example >= 1 && example <= queryExamples.length) {
        history.replaceState(null, '', '#query');
        useExample(example - 1, true);
    }
}

async function fillDatabaseSelect() {
    const select = document.getElementById('sql-database');
    let data;
    try { data = await api('databases'); } catch (e) { return; }
    const current = select.value || localStorage.getItem(LS + 'sql_database') || data.chronicle;
    select.innerHTML = data.databases.filter(d => d.allow).map(d =>
        `<option value="${escapeHtml(d.name)}"${d.name === current ? ' selected' : ''}>${escapeHtml(d.name)}${d.chronicle ? ' (Chronicle)' : ''}</option>`).join('');
    if (!select.value) select.value = data.chronicle;
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
            </button>`).join('') + '<p class="panel-sub side-note">The examples read the Chronicle database. A click puts one in the editor; one about an entity asks which first.</p>'
            : '<div class="empty-state">The examples could not be loaded</div>';
        return;
    }
    const items = loadHistory();
    el.innerHTML = items.length ? items.map((h, i) => `
        <div class="side-item history${h.fav ? ' fav' : ''}" data-history="${i}">
            <button type="button" class="star" data-star="${i}" title="${h.fav ? 'Remove from favourites' : 'Keep as favourite'}" aria-label="Favourite" aria-pressed="${h.fav ? 'true' : 'false'}">${h.fav ? '★' : '☆'}</button>
            <button type="button" class="history-body" data-use="${i}" title="Put this query in the editor">
                <span class="preview">${escapeHtml(preview(h.sql))}</span>
                <span class="meta">${escapeHtml(fmtAgo(h.at))}${h.database ? ' · ' + escapeHtml(h.database) : ''} · ${h.error ? '<span class="sb-bad">error</span>' : escapeHtml(h.status || '')}</span>
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
function preview(text) {
    const plain = text.replace(/--[^\n]*/g, ' ').replace(/\s+/g, ' ').trim() || text.trim();
    return plain.length > 90 ? plain.slice(0, 88) + '…' : plain;
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

function rememberQuery(text, database, status, error) {
    const trimmed = text.trim();
    if (!trimmed) return;
    const items = loadHistory();
    // The same statement again moves to the top, and stays a favourite if it was one
    const same = items.find(h => h.sql.trim() === trimmed);
    const rest = items.filter(h => h !== same);
    rest.push({sql: trimmed, database, at: Date.now() / 1000, status, error: Boolean(error), fav: Boolean(same && same.fav)});
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

function fillEditor(text) {
    const editor = document.getElementById('query-sql');
    editor.value = text;
    editor.focus();
    editor.setSelectionRange(0, 0);
    editor.scrollTop = 0;
}

// ---------------------------------------------------------------------
// Examples: their placeholders are filled in before they reach the editor
// ---------------------------------------------------------------------
async function useExample(index, run) {
    const example = queryExamples[index];
    if (!example) return;
    let text = example.sql;
    const needs = [...new Set(text.match(/<[a-z_]+>/g) || [])];
    if (needs.length) {
        const values = await askPlaceholders(example, needs);
        if (!values) return;
        needs.forEach(p => { text = text.split(p).join(values[p]); });
    }
    // The examples are about the Chronicle database
    const select = document.getElementById('sql-database');
    const chronicle = [...select.options].find(o => o.textContent.endsWith('(Chronicle)'));
    if (chronicle) select.value = chronicle.value;
    fillEditor(text);
    if (run) runQuery();
}

async function askPlaceholders(example, needs) {
    let entities = [];
    if (needs.includes('<entity_id>')) {
        try { entities = (await api('entities')).entities || []; } catch (e) { entities = []; }
    }
    // A switch-like entity for "Hours on", else the busiest numeric one, else the busiest
    const onOff = example.sql.includes('value = 1');
    const pick = entities.find(e => onOff ? ['switch', 'light', 'binary_sensor', 'input_boolean'].includes(e.entity_id.split('.')[0]) : e.unit)
        || entities[0];
    return new Promise(resolve => {
        const fields = needs.map(p => {
            const key = p.slice(1, -1);
            if (key === 'entity_id') {
                return `<label class="form-row"><span>Entity</span>
                    <input class="input" id="ph-entity_id" list="ph-entities" value="${escapeHtml(pick ? pick.entity_id : '')}" autocomplete="off" spellcheck="false">
                    <datalist id="ph-entities">${entities.map(e => `<option value="${escapeHtml(e.entity_id)}">${escapeHtml(e.name || '')}</option>`).join('')}</datalist></label>`;
            }
            return `<label class="form-row"><span>${escapeHtml(key.replace(/_/g, ' '))}</span>
                <input class="input" id="ph-${key}" value="${key === 'attribute' ? 'friendly_name' : ''}" autocomplete="off" spellcheck="false"></label>`;
        }).join('');
        let answered = false;
        actionModal(example.title, `<p class="panel-sub">${placeholders(escapeHtml(example.description))}</p>${fields}`, 'Use', false, async () => {
            const values = {};
            for (const p of needs) {
                const value = document.getElementById('ph-' + p.slice(1, -1)).value.trim();
                if (!value) throw new Error('Fill in every field');
                // Inside a quoted string in the examples: a quote would end it
                values[p] = value.replace(/'/g, "''");
            }
            answered = true;
            resolve(values);
        });
        // Closing the dialog another way is a cancel
        const root = document.getElementById('modal-root');
        const watch = new MutationObserver(() => { if (!root.innerHTML) { watch.disconnect(); if (!answered) resolve(null); } });
        watch.observe(root, {childList: true});
    });
}

// ---------------------------------------------------------------------
// Running
// ---------------------------------------------------------------------
function editorScript() {
    // With text selected, only that runs (as in pgAdmin)
    const editor = document.getElementById('query-sql');
    const selected = editor.value.slice(editor.selectionStart, editor.selectionEnd);
    return selected.trim() ? {text: selected, offset: editor.selectionStart} : {text: editor.value, offset: 0};
}

async function runQuery(confirmed) {
    if (sql.running) return;
    const {text, offset} = editorScript();
    if (!text.trim()) return;
    try { localStorage.setItem(LS + 'query', document.getElementById('query-sql').value); } catch (e) { /* only a convenience */ }
    const database = document.getElementById('sql-database').value;
    try { localStorage.setItem(LS + 'sql_database', database); } catch (e) { /* only a convenience */ }
    const status = document.getElementById('query-status');
    document.getElementById('query-error').hidden = true;
    status.textContent = 'Running…';
    setQueryRunning(true);
    let data;
    try {
        data = await api('sql/run', {
            sql: text,
            database,
            session: sql.session,
            autocommit: document.getElementById('sql-autocommit').checked,
            timeout: Number(document.getElementById('sql-timeout').value),
            confirmed: Boolean(confirmed) || sql.confirmedAll,
        });
    } catch (e) {
        status.textContent = '';
        renderQueryError({message: e.message}, text, offset);
        setQueryRunning(false);
        return;
    }
    setQueryRunning(false);
    if (data.confirm) {
        status.textContent = '';
        confirmChanges(data.confirm);
        return;
    }
    sql.session = data.session;
    sql.inTransaction = data.in_transaction;
    sql.results = data.results;
    sql.shown = Math.max(0, lastRowsIndex(data.results));
    renderTransaction();
    renderMessages(data);
    if (data.error) {
        renderQueryError(data.error, text, offset);
        status.textContent = `${num(data.results.length)} of ${num(data.statements)} statements ran`;
        rememberQuery(text, database, '', true);
    }
    renderResults();
    if (!data.error) {
        const last = data.results[data.results.length - 1];
        status.innerHTML = `${data.statements > 1 ? `${num(data.statements)} statements · ` : ''}${escapeHtml(last ? last.status : '')} · ${num(data.duration_ms)} ms`;
        rememberQuery(text, database, last ? last.status : '', false);
    }
}

function lastRowsIndex(results) {
    for (let i = results.length - 1; i >= 0; i--) if (results[i].columns) return i;
    return results.length - 1;
}

function confirmChanges(kinds) {
    const list = kinds.map(k => `<li><b>${escapeHtml(k.kind)}</b>${k.count > 1 ? ` × ${num(k.count)}` : ''}</li>`).join('');
    const autocommit = document.getElementById('sql-autocommit').checked;
    actionModal('Change the database?', `
        <p>This script changes the database <b>${escapeHtml(document.getElementById('sql-database').value)}</b>:</p>
        <ul class="change-list">${list}</ul>
        <p class="panel-sub">${autocommit && !sql.inTransaction
            ? 'Auto-commit is on: every statement is saved as soon as it ran. Switch it off to check the result first and then Commit or Roll back.'
            : 'A transaction is open: nothing is saved until you Commit.'}</p>
        <label class="action-check"><input type="checkbox" id="confirm-all"> Don't ask again until I close this page</label>`,
        'Run', true, async () => {
            if (document.getElementById('confirm-all').checked) sql.confirmedAll = true;
            setTimeout(() => runQuery(true), 0);
        });
}

async function cancelQuery() {
    if (!sql.session) return;
    try { await api('sql/cancel', {session: sql.session}); } catch (e) { toast(e.message, 'error'); }
}

async function endTransaction(commit) {
    if (!sql.session) return;
    try {
        const data = await api('sql/end', {session: sql.session, commit});
        sql.inTransaction = data.in_transaction;
        toast(commit ? 'Committed' : 'Rolled back');
    } catch (e) {
        sql.inTransaction = false;
        toast(e.message, 'error');
    }
    renderTransaction();
}

function setQueryRunning(running) {
    sql.running = running;
    document.getElementById('query-run').disabled = running;
    document.getElementById('query-cancel').hidden = !running;
}

function renderTransaction() {
    const open = sql.inTransaction;
    document.getElementById('sql-txn').hidden = !open;
    document.getElementById('sql-commit').hidden = !open;
    document.getElementById('sql-rollback').hidden = !open;
    // The database can't change under an open transaction
    document.getElementById('sql-database').disabled = open;
}

function renderMessages(data) {
    const el = document.getElementById('sql-messages');
    const lines = [];
    data.results.forEach((r, i) => { if (!r.columns) lines.push(`${data.results.length > 1 ? `${i + 1}. ` : ''}${r.status}`); });
    (data.notices || []).forEach(n => lines.push(n));
    el.innerHTML = lines.map(l => `<div>${escapeHtml(l)}</div>`).join('');
    el.hidden = !lines.length;
}

function renderQueryError(error, text, offset) {
    const el = document.getElementById('query-error');
    let where = '';
    // PostgreSQL gives the position (1-based character) in the statement; the server made
    // it a position in the script, and the selection's start makes it one in the editor
    if (error.position) {
        const pos = Number(error.position) - 1;
        const before = text.slice(0, pos);
        const line = before.split('\n').length;
        where = ` (line ${line}, column ${pos - before.lastIndexOf('\n')})`;
        const editor = document.getElementById('query-sql');
        const at = (offset || 0) + pos;
        editor.focus();
        editor.setSelectionRange(at, Math.min(at + 1, editor.value.length));
    }
    el.innerHTML = `<b>${escapeHtml(error.message || error.error)}</b>${escapeHtml(where)}`
        + (error.statement !== undefined ? `<div class="muted">in statement ${num(error.statement + 1)}</div>` : '')
        + (error.detail ? `<div>${escapeHtml(error.detail)}</div>` : '')
        + (error.hint ? `<div class="muted">Hint: ${escapeHtml(error.hint)}</div>` : '')
        + (sql.inTransaction ? '<div class="muted">The transaction failed: Roll back to continue.</div>' : '');
    el.hidden = false;
}

function cellText(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

function renderResults() {
    const sets = sql.results.map((r, i) => ({r, i})).filter(x => x.r.columns);
    const tabs = document.getElementById('sql-result-tabs');
    tabs.hidden = sets.length < 2;
    tabs.innerHTML = sets.map(({r, i}) =>
        `<button type="button" class="tab chip${i === sql.shown ? ' active' : ''}" data-result="${i}" title="${escapeHtml(r.statement)}">${i + 1}. ${escapeHtml(r.status)}</button>`).join('');
    const result = sql.results[sql.shown];
    const el = document.getElementById('query-result');
    const hasRows = result && result.columns;
    document.getElementById('query-csv').hidden = !hasRows;
    if (!hasRows) {
        el.innerHTML = `<div class="empty-state">${sql.results.length ? 'The statements returned no rows' : 'Run a query, or pick one of the examples'}</div>`;
        document.getElementById('query-chart-toggle').hidden = true;
        document.getElementById('query-chart-wrap').hidden = true;
        return;
    }
    const numeric = result.columns.map(c => NUMBER_TYPES.includes(c.type));
    const head = result.columns.map((c, i) => `<th class="${numeric[i] ? 'n' : ''}" title="${escapeHtml(c.type)}">${escapeHtml(c.name)}</th>`).join('');
    const rows = result.rows.length;
    const body = result.rows.slice(0, TABLE_ROWS).map(row => `<tr>${row.map((v, i) =>
        `<td class="${numeric[i] ? 'n' : v !== null && typeof v === 'object' ? 'mono wrap' : ''}">${v === null ? '<span class="muted">NULL</span>' : escapeHtml(cellText(v))}</td>`).join('')}</tr>`).join('');
    el.innerHTML = `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
        + (rows > TABLE_ROWS ? `<p class="panel-sub" style="padding:8px 12px">The table shows the first ${num(TABLE_ROWS)} rows; the CSV has all ${num(rows)}.</p>` : '')
        + (result.truncated ? `<p class="panel-sub" style="padding:8px 12px"><span class="sb-warn">Only the first ${num(result.limit)} rows were fetched.</span> Add a LIMIT or a WHERE to see the rest.</p>` : '');

    const chart = chartPlan(result);
    document.getElementById('query-chart-toggle').hidden = !chart;
    document.getElementById('query-chart-toggle').classList.toggle('active', queryChart);
    document.getElementById('query-chart-wrap').hidden = !(chart && queryChart);
    if (chart && queryChart) drawQueryChart(result, chart);
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
    renderResults();
}

function downloadQueryCsv() {
    const result = sql.results[sql.shown];
    if (!result || !result.columns) return;
    const quote = v => {
        const text = cellText(v);
        return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const lines = [result.columns.map(c => quote(c.name)).join(',')].concat(result.rows.map(row => row.map(quote).join(',')));
    downloadBlob(new Blob([lines.join('\n') + '\n'], {type: 'text/csv'}), 'chronicle-query.csv');
}

// ---------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------
(function initSqlPage() {
    const timeout = document.getElementById('sql-timeout');
    let saved = null;
    try { saved = Number(localStorage.getItem(LS + 'sql_timeout')); } catch (e) { /* only a convenience */ }
    timeout.innerHTML = TIMEOUT_CHOICES.map(([s, label]) =>
        `<option value="${s}"${s === (saved || 60) ? ' selected' : ''}>stop after ${label}</option>`).join('');
    timeout.addEventListener('change', () => { try { localStorage.setItem(LS + 'sql_timeout', timeout.value); } catch (e) { /* convenience */ } });
    const autocommit = document.getElementById('sql-autocommit');
    try { autocommit.checked = localStorage.getItem(LS + 'sql_autocommit') !== 'off'; } catch (e) { autocommit.checked = true; }
    autocommit.addEventListener('change', () => { try { localStorage.setItem(LS + 'sql_autocommit', autocommit.checked ? 'on' : 'off'); } catch (e) { /* convenience */ } });
    document.getElementById('sql-database').addEventListener('change', () => {
        try { localStorage.setItem(LS + 'sql_database', document.getElementById('sql-database').value); } catch (e) { /* convenience */ }
    });
})();

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
    if (d.example !== undefined) useExample(Number(d.example), false);
    else if (d.star !== undefined) toggleFavourite(Number(d.star));
    else if (d.remove !== undefined) removeHistory(Number(d.remove));
    else if (d.use !== undefined) {
        const item = loadHistory()[Number(d.use)];
        if (item.database && !sql.inTransaction) document.getElementById('sql-database').value = item.database;
        fillEditor(item.sql);
    }
});
document.getElementById('sql-result-tabs').addEventListener('click', (e) => {
    const button = e.target.closest('[data-result]');
    if (!button) return;
    sql.shown = Number(button.dataset.result);
    renderResults();
});
// The session's connection (and an open transaction) ends with the page
window.addEventListener('pagehide', () => {
    if (!sql.session) return;
    fetch(basePath + 'api/sql/close', {
        method: 'POST', keepalive: true,
        headers: {'Content-Type': 'application/json', 'X-Chronicle': '1'},
        body: JSON.stringify({session: sql.session}),
    }).catch(() => {});
});
onTab('query', showQuery);
