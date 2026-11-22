// Vigil dashboard - Logs: the query bar with suggestions, the histogram by severity (drag to zoom),
// the lines (older ones by page), a row's fields, live tail, saved searches.

const LOG_PAGE = 200;
const LOG_MAX_ROWS = 5000;        // the table keeps at most this many lines
const logs = {
    q: '', rows: [], more: false, took: null, error: null,
    expanded: new Set(), nextKey: 1, loadedKey: null, loadedAt: 0,
    live: false, liveTimer: null, liveTicks: 0, pending: [], paused: false,
    fields: null, fieldsKey: null, saved: [], loading: false,
};

// From other pages: the logs for a query (host:x, pattern:12)
function openLogs(q) {
    hashArgs.logs = q;
    if (activeTab === 'logs') { setLogQuery(q); runLogSearch(); }
    else switchTab('logs', q);
}

function setLogQuery(q) {
    document.getElementById('log-q').value = q;
}

async function showLogs(arg) {
    if (arg !== logs.q) setLogQuery(arg);
    loadSavedSearches();
    const key = arg + '|' + rangeToHash();
    if (key !== logs.loadedKey || Date.now() - logs.loadedAt > 60000) runLogSearch();
    else renderLogChips();
    if (logs.live) startLive();
}

function runLogSearch() {
    hideSuggest();
    logs.q = document.getElementById('log-q').value.trim();
    updateHash(logs.q);
    logs.loadedKey = logs.q + '|' + rangeToHash();
    logs.loadedAt = Date.now();
    renderLogChips();
    if (!ready()) {
        emptyChart(document.getElementById('chart-histogram'), 'The database is not connected');
        document.getElementById('log-table').innerHTML = emptyHtml('The database is not connected');
        document.getElementById('log-more').hidden = true;
        return;
    }
    loadHistogram();
    loadLogRows();
    loadFields();
}

async function loadHistogram() {
    const el = document.getElementById('chart-histogram');
    const q = logs.q;
    let data;
    try {
        data = await api('logs/histogram?' + qs({q, ...rangeWindow(), buckets: 60}));
    } catch (e) {
        if (q === logs.q) emptyChart(el, e.message);
        return;
    }
    if (q !== logs.q) return;
    document.getElementById('log-hist-hint').textContent = `${num(data.total)} lines · ${rangeText()} · drag to zoom`;
    severityChart(el, data, {
        height: 150,
        title: q ? `Lines matching ${q}` : 'All lines',
        onZoom: zoomRange,
        zoomed: rangeZoomed(),
        onReset: rangeBack,
        empty: noDataYet() ? 'Nothing has arrived yet: see Sources to point devices at Vigil' : 'No lines match in this period',
    });
}

async function loadLogRows() {
    const q = logs.q;
    const table = document.getElementById('log-table');
    if (!logs.rows.length) table.innerHTML = loadingHtml('Searching…');
    else table.classList.add('stale');
    logs.loading = true;
    let data;
    try {
        data = await api('logs?' + qs({q, ...rangeWindow(), limit: LOG_PAGE}));
    } catch (e) {
        logs.loading = false;
        table.classList.remove('stale');
        if (q !== logs.q) return;
        logs.rows = [];
        logs.error = e.message;
        table.innerHTML = errorHtml(e.status === 400 ? `The search can't be read: ${e.message}` : e.message);
        document.getElementById('log-more').hidden = true;
        document.getElementById('log-count').textContent = '';
        if (e.status !== 400) handleError(e);
        return;
    }
    logs.loading = false;
    table.classList.remove('stale');
    if (q !== logs.q) return;
    logs.error = null;
    logs.expanded.clear();
    logs.pending = [];
    logs.rows = (data.rows || []).map(keyed);
    logs.more = Boolean(data.more);
    logs.took = data.took_ms;
    table.scrollTop = 0;
    renderLogTable();
}

async function loadOlderLogs() {
    const last = logs.rows[logs.rows.length - 1];
    if (!last) return;
    const button = document.getElementById('log-more');
    button.disabled = true;
    button.textContent = 'Loading…';
    try {
        const data = await api('logs?' + qs({q: logs.q, ...rangeWindow(), limit: LOG_PAGE, before: last.ts}));
        const rows = (data.rows || []).map(keyed);
        logs.more = Boolean(data.more) && logs.rows.length + rows.length < LOG_MAX_ROWS;
        logs.rows = logs.rows.concat(rows);
        document.querySelector('#log-table tbody').insertAdjacentHTML('beforeend', rows.map(logRowHtml).join(''));
        renderLogCount();
    } catch (e) {
        handleError(e);
    }
    button.disabled = false;
    button.textContent = 'Load older lines';
}

function keyed(row) {
    row._k = logs.nextKey++;
    return row;
}

const LOG_COLUMNS = '<thead><tr><th class="t">Time</th><th>Severity</th><th>Host</th><th>App</th><th>Message</th></tr></thead>';

function renderLogTable() {
    const table = document.getElementById('log-table');
    if (!logs.rows.length) {
        table.innerHTML = noDataYet()
            ? '<div class="empty-state">Nothing has arrived yet. <a href="#sources" onclick="switchTab(\'sources\');return false">Sources</a> shows how to send logs to Vigil.</div>'
            : emptyHtml(logs.q ? 'No lines match this search in the time range' : 'No log lines in this time range');
        document.getElementById('log-more').hidden = true;
        renderLogCount();
        return;
    }
    table.innerHTML = `<table class="data logs-table">${LOG_COLUMNS}<tbody>${logs.rows.map(logRowHtml).join('')}</tbody></table>`;
    renderLogCount();
}

function renderLogCount() {
    document.getElementById('log-count').textContent = `${num(logs.rows.length)}${logs.more ? '+' : ''} lines${logs.took !== null && logs.took !== undefined ? ` · ${num(logs.took)} ms` : ''}`;
    document.getElementById('log-more').hidden = !logs.more;
    document.getElementById('log-foot-text').textContent = !logs.more && logs.rows.length ? 'the oldest line in the range' : '';
}

function logRowHtml(r) {
    const sev = severityNumber(r.severity);
    const cell = (field, value) => value ? `<button type="button" class="cell-link" data-filter="${field}" data-value="${escapeHtml(value)}" title="Only ${escapeHtml(field)} ${escapeHtml(value)}">${escapeHtml(value)}</button>` : '<span class="muted">–</span>';
    const pattern = r.pattern !== null && r.pattern !== undefined
        ? ` <button type="button" class="pat-link" data-filter="pattern" data-value="${Number(r.pattern)}" title="Lines like this one">#${Number(r.pattern)}</button>` : '';
    const open = logs.expanded.has(r._k);
    return `<tr class="log-row s${sev}${open ? ' open' : ''}" data-k="${r._k}">
        <td class="t mono" title="${escapeHtml(new Date(r.ts * 1000).toISOString())}">${escapeHtml(fmtLogTime(r.ts))}</td>
        <td>${sevPill(r.severity)}</td>
        <td class="host">${cell('host', r.host)}</td>
        <td class="app">${cell('app', r.app)}</td>
        <td class="msg"><span class="msg-text">${escapeHtml(r.message)}</span>${pattern}</td>
    </tr>${open ? logDetailHtml(r) : ''}`;
}

function logDetailHtml(r) {
    const attrs = r.attrs && typeof r.attrs === 'object' ? r.attrs : {};
    const field = (label, value, filter) => `<div class="kv"><span>${label}</span><b>${filter && value !== null && value !== undefined && value !== ''
        ? `<button type="button" class="cell-link" data-filter="${filter}" data-value="${escapeHtml(value)}">${escapeHtml(value)}</button>` : escapeHtml(value === null || value === undefined || value === '' ? '–' : value)}</b></div>`;
    const attrRows = Object.keys(attrs).sort().map(k => {
        const v = attrs[k];
        const text = typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v);
        const simple = typeof v !== 'object' || v === null;
        return `<tr><td class="mono">${escapeHtml(k)}</td><td class="wrap mono">${escapeHtml(text)}</td><td class="n">${simple
            ? `<button type="button" class="btn btn-small" data-filter="attrs.${escapeHtml(k)}" data-value="${escapeHtml(text)}" title="Only lines with this value">+ filter</button>` : ''}</td></tr>`;
    }).join('');
    return `<tr class="log-detail" data-detail="${r._k}"><td colspan="5"><div class="log-detail-body">
        <div class="kv-grid">
            ${field('Time', `${fmtDateTime(r.ts)} · ${new Date(r.ts * 1000).toISOString()}`)}
            ${field('Host', r.host, 'host')}
            ${field('App', r.app, 'app')}
            ${field('Source', r.source, 'source')}
            ${field('Severity', `${severityName(r.severity)} (${severityNumber(r.severity)})`)}
            ${field('Facility', r.facility)}
            ${field('Pattern', r.pattern === null || r.pattern === undefined ? null : r.pattern, 'pattern')}
        </div>
        <pre class="code-block log-message">${escapeHtml(r.message)}</pre>
        ${attrRows ? `<table class="data attrs-table"><thead><tr><th>Attribute</th><th>Value</th><th></th></tr></thead><tbody>${attrRows}</tbody></table>` : '<div class="panel-sub">No attributes</div>'}
        <div class="detail-actions" style="margin-top:10px">
            <button type="button" class="btn btn-small" data-action="context" data-k="${r._k}">Lines around this one</button>
            <button type="button" class="btn btn-small" data-action="copy" data-k="${r._k}">Copy as JSON</button>
        </div>
    </div></td></tr>`;
}

function toggleLogRow(k) {
    const row = logs.rows.find(r => r._k === k);
    const tr = document.querySelector(`#log-table tr[data-k="${k}"]`);
    if (!row || !tr) return;
    if (logs.expanded.has(k)) {
        logs.expanded.delete(k);
        const detail = document.querySelector(`#log-table tr[data-detail="${k}"]`);
        if (detail) detail.remove();
        tr.classList.remove('open');
    } else {
        logs.expanded.add(k);
        tr.classList.add('open');
        tr.insertAdjacentHTML('afterend', logDetailHtml(row));
    }
}

function expandAllLogs() {
    logs.expanded.clear();
    document.querySelectorAll('#log-table tr.log-detail').forEach(d => d.remove());
    document.querySelectorAll('#log-table tr.log-row.open').forEach(r => r.classList.remove('open'));
}

document.getElementById('log-table').addEventListener('click', (e) => {
    const filter = e.target.closest('[data-filter]');
    if (filter) {
        e.stopPropagation();
        return addLogFilter(filter.dataset.filter, filter.dataset.value, e.altKey);
    }
    const action = e.target.closest('[data-action]');
    if (action) {
        const row = logs.rows.find(r => r._k === Number(action.dataset.k));
        if (!row) return;
        if (action.dataset.action === 'copy') {
            const {_k, ...plain} = row;
            return copyPlain(JSON.stringify(plain, null, 2), 'Line');
        }
        if (action.dataset.action === 'context') {
            // Five minutes either side, from the same host
            setLogQuery(row.host ? `host:${queryValue(row.host)}` : '');
            stopLive();
            setRange({from: row.ts - 300, to: row.ts + 300}, true);
            return;
        }
    }
    if (e.target.closest('.log-detail')) return;
    if (window.getSelection && String(window.getSelection()).length) return;  // selecting text
    const tr = e.target.closest('tr.log-row');
    if (tr) toggleLogRow(Number(tr.dataset.k));
});

// =====================================================================
// The query: filters added by a click, shown as chips
// =====================================================================
const QUERY_TOKEN = /-?[\w.]+:(?:[<>]=?|=)?(?:"(?:[^"\\]|\\.)*"|\S+)|-?"(?:[^"\\]|\\.)*"|\S+/g;
const queryTokens = q => q.match(QUERY_TOKEN) || [];

function addLogFilter(field, value, negate) {
    const token = `${negate ? '-' : ''}${field}:${queryValue(value)}`;
    const tokens = queryTokens(document.getElementById('log-q').value);
    // One value per field: host:a replaces host:b (the alt key adds a NOT instead)
    const kept = tokens.filter(t => {
        if (negate) return t !== token;
        const m = t.match(/^([\w.]+):/);
        return !(m && m[1] === field);
    });
    kept.push(token);
    setLogQuery(kept.join(' '));
    runLogSearch();
}

function renderLogChips() {
    const el = document.getElementById('log-chips');
    const tokens = queryTokens(logs.q);
    el.innerHTML = tokens.map((t, i) => {
        const neg = t.startsWith('-');
        const m = t.replace(/^-/, '').match(/^([\w.]+):(.*)$/);
        const label = m ? `<span class="muted">${escapeHtml(m[1])}</span> ${escapeHtml(m[2])}` : escapeHtml(t.replace(/^-/, ''));
        return `<span class="filter-chip${neg ? ' neg' : ''}">${neg ? '<b>NOT</b> ' : ''}${label}<button type="button" data-remove="${i}" aria-label="Remove">×</button></span>`;
    }).join('') + (tokens.length ? '<button type="button" class="btn btn-small chip-clear" data-remove="all">Clear</button>' : '');
    el.onclick = (e) => {
        const b = e.target.closest('[data-remove]');
        if (!b) return;
        const rest = b.dataset.remove === 'all' ? [] : tokens.filter((_, i) => i !== Number(b.dataset.remove));
        setLogQuery(rest.join(' '));
        runLogSearch();
    };
}

// =====================================================================
// Suggestions: field names, then the top values of a field (from /api/logs/fields)
// =====================================================================
const LOG_FIELDS = [
    {key: 'host', hint: 'sending host', list: 'hosts'},
    {key: 'app', hint: 'program / service', list: 'apps'},
    {key: 'severity', hint: 'err, warning; <=warning is warning and worse', list: 'severities'},
    {key: 'source', hint: 'syslog, otlp, json, fritzbox', list: 'sources'},
    {key: 'pattern', hint: 'pattern id'},
    {key: 'attrs.', hint: 'an attribute: attrs.key:value'},
];
let suggestItems = [];
let suggestIndex = -1;

async function loadFields() {
    const w = rangeWindow();
    const key = rangeToHash();
    if (logs.fields && logs.fieldsKey === key && timeRange.preset) return;
    try {
        logs.fields = await api('logs/fields?' + qs(w));
        logs.fieldsKey = key;
    } catch (e) { /* suggestions are a convenience */ }
}

// The token the caret is in: [start, end, text]
function currentToken(input) {
    const v = input.value;
    const caret = input.selectionStart === null ? v.length : input.selectionStart;
    let start = caret;
    let quotes = 0;
    for (let i = 0; i < caret; i++) if (v[i] === '"') quotes++;
    while (start > 0 && (quotes % 2 === 1 || !/\s/.test(v[start - 1]))) {
        if (v[start - 1] === '"') quotes--;
        start--;
    }
    let end = caret;
    while (end < v.length && !/\s/.test(v[end])) end++;
    return [start, end, v.slice(start, caret)];
}

function buildSuggestions() {
    const input = document.getElementById('log-q');
    const [, , token] = currentToken(input);
    const neg = token.startsWith('-') ? '-' : '';
    const t = token.replace(/^-/, '');
    const f = logs.fields || {};
    const items = [];
    const m = t.match(/^([\w.]+):([<>]=?|=)?(.*)$/);
    if (m) {
        const field = LOG_FIELDS.find(x => x.key === m[1]);
        const op = m[2] || '';
        const prefix = m[3].replace(/^"/, '').toLowerCase();
        let values = field && field.list ? (f[field.list] || []) : [];
        if (m[1] === 'severity') {
            const counts = {};
            values.forEach(v => { counts[severityName(v.value)] = v.count; });
            values = SEVERITIES.map(s => ({value: s, count: counts[s]}));
        }
        values.filter(v => String(m[1] === 'severity' ? v.value : v.value).toLowerCase().startsWith(prefix)).slice(0, 12).forEach(v => {
            items.push({insert: `${neg}${m[1]}:${op}${queryValue(v.value)} `, label: String(v.value), field: m[1], count: v.count});
        });
    } else {
        const low = t.toLowerCase();
        LOG_FIELDS.filter(x => x.key.startsWith(low) && x.key !== low).forEach(x => items.push({insert: `${neg}${x.key}${x.key.endsWith('.') ? '' : ':'}`, label: x.key + (x.key.endsWith('.') ? '…' : ':'), hint: x.hint, more: true}));
        if (low.length >= 1) {
            ['hosts', 'apps'].forEach(list => (f[list] || []).filter(v => String(v.value).toLowerCase().includes(low)).slice(0, 5).forEach(v => {
                const field = list === 'hosts' ? 'host' : 'app';
                items.push({insert: `${neg}${field}:${queryValue(v.value)} `, label: String(v.value), field, count: v.count});
            }));
        }
    }
    return items.slice(0, 14);
}

function showSuggest() {
    const box = document.getElementById('log-suggest');
    suggestItems = buildSuggestions();
    suggestIndex = -1;
    if (!suggestItems.length) return hideSuggest();
    box.innerHTML = suggestItems.map((it, i) => `<div class="suggest-item" role="option" data-i="${i}">
        ${it.field ? `<span class="muted">${escapeHtml(it.field)}:</span>` : ''}<b>${escapeHtml(it.label)}</b>
        <span class="spacer"></span>${it.hint ? `<small>${escapeHtml(it.hint)}</small>` : ''}${it.count !== undefined && it.count !== null ? `<small class="mono">${fmtCount(it.count)}</small>` : ''}</div>`).join('');
    box.hidden = false;
}
function hideSuggest() {
    document.getElementById('log-suggest').hidden = true;
    suggestItems = [];
    suggestIndex = -1;
}
function pickSuggestion(i) {
    const it = suggestItems[i];
    if (!it) return;
    const input = document.getElementById('log-q');
    const [start, end] = currentToken(input);
    const v = input.value;
    input.value = v.slice(0, start) + it.insert + v.slice(end).replace(/^\s+/, '');
    const caret = start + it.insert.length;
    input.setSelectionRange(caret, caret);
    input.focus();
    if (it.more) showSuggest(); else hideSuggest();
}
function moveSuggest(delta) {
    if (!suggestItems.length) return;
    suggestIndex = (suggestIndex + delta + suggestItems.length) % suggestItems.length;
    document.querySelectorAll('#log-suggest .suggest-item').forEach((el, i) => el.classList.toggle('active', i === suggestIndex));
}

(function wireQueryBar() {
    const input = document.getElementById('log-q');
    input.addEventListener('input', showSuggest);
    input.addEventListener('focus', () => { loadFields(); });
    input.addEventListener('blur', () => setTimeout(hideSuggest, 150));
    input.addEventListener('keydown', (e) => {
        const open = !document.getElementById('log-suggest').hidden;
        if (e.key === 'ArrowDown' && open) { e.preventDefault(); moveSuggest(1); }
        else if (e.key === 'ArrowUp' && open) { e.preventDefault(); moveSuggest(-1); }
        else if ((e.key === 'Tab' || e.key === 'Enter') && open && suggestIndex >= 0) { e.preventDefault(); pickSuggestion(suggestIndex); }
        else if (e.key === 'Escape' && open) { e.stopPropagation(); hideSuggest(); }
        else if (e.key === 'Enter') { e.preventDefault(); runLogSearch(); }
    });
    document.getElementById('log-suggest').addEventListener('mousedown', (e) => {
        const item = e.target.closest('[data-i]');
        if (item) { e.preventDefault(); pickSuggestion(Number(item.dataset.i)); }
    });
})();

// =====================================================================
// Live tail: new lines every 2 s, on top; paused while the table is scrolled down
// =====================================================================
function toggleLive() {
    if (logs.live) return stopLive();
    logs.live = true;
    document.getElementById('log-live').classList.add('active');
    // Live is about now: a fixed window in the past becomes the last 15 minutes
    if (!timeRange.preset) setRange({preset: '15m'});
    startLive();
}

function startLive() {
    clearInterval(logs.liveTimer);
    logs.liveTimer = setInterval(liveTick, 2000);
    renderLiveState();
}

function stopLive() {
    logs.live = false;
    clearInterval(logs.liveTimer);
    logs.liveTimer = null;
    document.getElementById('log-live').classList.remove('active');
    flushPending();
    renderLiveState();
}

async function liveTick() {
    if (activeTab !== 'logs' || document.visibilityState !== 'visible' || logs.loading || !ready()) return;
    const newest = logs.pending.length ? logs.pending[0] : logs.rows[0];
    const after = newest ? newest.ts : rangeWindow().to - 2;
    const q = logs.q;
    let data;
    try {
        data = await api('logs/tail?' + qs({q, after}));
    } catch (e) {
        return;
    }
    if (q !== logs.q || !logs.live) return;
    const rows = (Array.isArray(data) ? data : (data && data.rows) || []).filter(r => r.ts > after || !newest);
    if (rows.length) {
        rows.sort((a, b) => b.ts - a.ts);
        rows.forEach(keyed);
        logs.pending = rows.concat(logs.pending);
        if (!logs.paused) flushPending();
    }
    renderLiveState();
    // The histogram follows every 10 s
    if (++logs.liveTicks % 5 === 0) loadHistogram();
}

function flushPending() {
    if (!logs.pending.length) return;
    const rows = logs.pending;
    logs.pending = [];
    if (!logs.rows.length) {
        logs.rows = rows;
        renderLogTable();
    } else {
        logs.rows = rows.concat(logs.rows);
        const tbody = document.querySelector('#log-table tbody');
        if (tbody) {
            tbody.insertAdjacentHTML('afterbegin', rows.map(logRowHtml).join(''));
            tbody.querySelectorAll('tr.log-row.fresh').forEach(r => r.classList.remove('fresh'));
            rows.forEach(r => { const tr = tbody.querySelector(`tr[data-k="${r._k}"]`); if (tr) tr.classList.add('fresh'); });
        }
    }
    // Keep the table bounded: the oldest lines go
    if (logs.rows.length > LOG_MAX_ROWS) {
        logs.rows.splice(LOG_MAX_ROWS).forEach(r => {
            logs.expanded.delete(r._k);
            document.querySelectorAll(`#log-table tr[data-k="${r._k}"], #log-table tr[data-detail="${r._k}"]`).forEach(el => el.remove());
        });
        logs.more = true;
    }
    renderLogCount();
}

function renderLiveState() {
    const el = document.getElementById('log-live-state');
    el.hidden = !logs.live;
    if (!logs.live) return;
    el.className = 'live-state' + (logs.paused ? ' paused' : '');
    el.innerHTML = logs.paused
        ? `<i></i>Paused while scrolled${logs.pending.length ? ` · <b>${num(logs.pending.length)}</b> new` : ''} · <button type="button" class="link-btn" onclick="scrollLogsTop()">back to top</button>`
        : '<i></i>Live · every 2 s';
}

function scrollLogsTop() {
    document.getElementById('log-table').scrollTop = 0;
}

document.getElementById('log-table').addEventListener('scroll', (e) => {
    const paused = e.target.scrollTop > 8;
    if (paused === logs.paused) return;
    logs.paused = paused;
    if (!paused) flushPending();
    renderLiveState();
});

// =====================================================================
// Saved searches and alerts from a search
// =====================================================================
async function loadSavedSearches() {
    try {
        const data = await api('searches');
        logs.saved = (data && (data.searches || data)) || [];
        if (!Array.isArray(logs.saved)) logs.saved = [];
    } catch (e) {
        logs.saved = [];
    }
    renderSavedSelect();
}

function renderSavedSelect() {
    const el = document.getElementById('log-saved');
    el.innerHTML = `<option value="">Saved searches${logs.saved.length ? ` (${logs.saved.length})` : ''}</option>`
        + logs.saved.map((s, i) => `<option value="${i}">${escapeHtml(s.name)}</option>`).join('')
        + (logs.saved.length ? '<option disabled>──────────</option><option value="manage">Manage saved searches…</option>' : '');
    el.onchange = () => {
        const v = el.value;
        el.value = '';
        if (v === 'manage') return manageSavedSearches();
        const s = logs.saved[Number(v)];
        if (!s) return;
        setLogQuery(s.query || '');
        runLogSearch();
    };
}

function saveLogSearch() {
    const q = document.getElementById('log-q').value.trim();
    if (!q) return toast('Type a search first', 'error');
    openModal({
        title: 'Save search',
        body: `<div class="form-row"><span>Name</span><input type="text" class="input" id="save-name" maxlength="120" placeholder="e.g. Router errors"></div>
            <div class="form-row"><span>Search</span><code class="mono query-preview">${escapeHtml(q)}</code></div>`,
        label: 'Save',
        onConfirm: async () => {
            const name = document.getElementById('save-name').value.trim();
            if (!name) throw new Error('Give the search a name');
            await api('searches/save', {name, query: q});
            toast(`Saved "${name}"`, 'success');
            loadSavedSearches();
        },
    });
}

function manageSavedSearches() {
    const body = logs.saved.map((s, i) => `<div class="saved-row"><div><b>${escapeHtml(s.name)}</b><code class="mono">${escapeHtml(s.query)}</code></div>
        <button type="button" class="btn btn-small" data-load="${i}">Open</button>
        <button type="button" class="btn btn-small btn-danger" data-delete="${i}">Delete</button></div>`).join('');
    const overlay = openModal({title: 'Saved searches', body: `<div class="saved-list">${body}</div>`});
    overlay.querySelector('.saved-list').onclick = async (e) => {
        const load = e.target.closest('[data-load]');
        const del = e.target.closest('[data-delete]');
        if (load) {
            const s = logs.saved[Number(load.dataset.load)];
            closeModal();
            setLogQuery(s.query || '');
            runLogSearch();
        } else if (del) {
            const s = logs.saved[Number(del.dataset.delete)];
            try {
                await api('searches/delete', {id: s.id, name: s.name});
                toast(`Deleted "${s.name}"`, 'success');
                await loadSavedSearches();
                if (logs.saved.length) manageSavedSearches(); else closeModal();
            } catch (err) {
                modalError(err.message);
            }
        }
    };
}

function alertFromSearch() {
    const q = document.getElementById('log-q').value.trim();
    openRuleEditor({
        name: q ? `Logs: ${q}`.slice(0, 80) : 'Log lines',
        kind: 'log_count',
        query: {q, window: 300},
        condition: {op: '>', threshold: 0},
        for_seconds: 0,
        severity: 'warning',
        enabled: true,
        notify: true,
    });
}

onTab('logs', showLogs);
onRange('logs', runLogSearch);
onLeave('logs', () => { clearInterval(logs.liveTimer); logs.liveTimer = null; hideSuggest(); });
