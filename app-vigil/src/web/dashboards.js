// Vigil dashboard - Dashboards: panels (time series, stat, logs, bar, table) on a 4-column grid,
// the built-in ones and your own; edit mode adds, edits, moves, resizes and removes panels.
//
// A panel: {id, title, type, query, w: 1..4, h: 1..3}. Its query says where the data comes from:
//   metrics  {name, filter, agg, group, anomaly}  or  {queries: [those]} for an overlay
//   logs     {q}                       the histogram (timeseries), the total (stat), the lines (logs)
//   fields   {field: 'hosts'|'apps'|'sources'|'severities', q}   top values (bar, table)
//   patterns {sort: 'count'|'new'|'spike', q}                    top patterns (table, bar)
// query.source names it; without it, it is inferred from the keys.

const PANEL_TYPES = [
    {key: 'timeseries', label: 'Time series'},
    {key: 'stat', label: 'Stat'},
    {key: 'logs', label: 'Log lines'},
    {key: 'bar', label: 'Bar (top values)'},
    {key: 'table', label: 'Table'},
];
const PANEL_SOURCES = [
    {key: 'metrics', label: 'Metric'},
    {key: 'logs', label: 'Log search'},
    {key: 'fields', label: 'Log field (top values)'},
    {key: 'patterns', label: 'Log patterns'},
];
const ROW_HEIGHT = 150;   // one grid row, px (CSS: .dash-grid grid-auto-rows)
const dash = {list: [], currentId: null, editing: false, draft: null, timer: null, seq: 0};

function panelSource(q) {
    q = q || {};
    if (q.source && PANEL_SOURCES.some(s => s.key === q.source)) return q.source;
    if (q.name || Array.isArray(q.queries)) return 'metrics';
    if (q.field) return 'fields';
    if (q.sort || q.patterns) return 'patterns';
    return 'logs';
}
const panelMetricQueries = q => (Array.isArray(q.queries) ? q.queries : [q]).map(normaliseQuery).filter(x => x.name);
const clamp = (v, lo, hi, dflt) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : dflt; };
const newPanelId = () => 'p' + Math.random().toString(36).slice(2, 10);
const dashKey = d => String(d.id !== undefined && d.id !== null ? d.id : d.name);

async function showDashboards(arg) {
    const grid = document.getElementById('dash-grid');
    if (!ready()) {
        grid.innerHTML = emptyHtml('The database is not connected');
        document.getElementById('dash-list').innerHTML = '';
        return;
    }
    if (!dash.editing) await loadDashboardList();
    if (arg && dash.list.some(d => dashKey(d) === arg)) dash.currentId = arg;
    if (!dash.list.some(d => dashKey(d) === dash.currentId)) dash.currentId = dash.list.length ? dashKey(dash.list[0]) : null;
    renderDashboard();
    clearInterval(dash.timer);
    dash.timer = setInterval(() => {
        if (activeTab === 'dashboards' && !dash.editing && timeRange.preset && document.visibilityState === 'visible') renderPanels();
    }, 60000);
}

async function loadDashboardList() {
    try {
        const data = await api('dashboards');
        dash.list = (data && (data.dashboards || data)) || [];
        if (!Array.isArray(dash.list)) dash.list = [];
    } catch (e) {
        dash.list = [];
        document.getElementById('dash-grid').innerHTML = errorHtml(e.message);
        handleError(e);
    }
}

function currentDashboard() {
    if (dash.editing && dash.draft) return dash.draft;
    return dash.list.find(d => dashKey(d) === dash.currentId) || null;
}

function renderDashboard() {
    const listEl = document.getElementById('dash-list');
    listEl.innerHTML = dash.list.map(d => `<button type="button" class="tab chip${dashKey(d) === dash.currentId ? ' active' : ''}" data-key="${escapeHtml(dashKey(d))}">${escapeHtml(d.name)}${d.builtin ? ' <small class="muted">built-in</small>' : ''}</button>`).join('');
    listEl.hidden = !dash.list.length;
    listEl.onclick = (e) => {
        const b = e.target.closest('[data-key]');
        if (!b || dash.editing) return;
        dash.currentId = b.dataset.key;
        updateHash(dash.currentId);
        renderDashboard();
    };
    document.getElementById('dash-edit-tools').hidden = !dash.editing;
    document.getElementById('dash-edit').hidden = dash.editing || !currentDashboard();
    document.getElementById('dash-grid').classList.toggle('editing', dash.editing);
    if (dash.currentId) updateHash(dash.currentId);
    renderPanels();
}

function renderPanels() {
    const grid = document.getElementById('dash-grid');
    const d = currentDashboard();
    if (!d) {
        grid.innerHTML = '<div class="empty-state dash-empty">No dashboards yet. <button type="button" class="btn btn-small" onclick="newDashboard()">+ New dashboard</button></div>';
        return;
    }
    const panels = Array.isArray(d.panels) ? d.panels : [];
    if (!panels.length) {
        grid.innerHTML = `<div class="empty-state dash-empty">This dashboard has no panels yet.${dash.editing
            ? ' <button type="button" class="btn btn-small" onclick="editPanel(null)">+ Add panel</button>'
            : ' <button type="button" class="btn btn-small" onclick="editDashboard()">Edit</button>'}</div>`;
        return;
    }
    const seq = ++dash.seq;
    grid.innerHTML = panels.map((p, i) => {
        const w = clamp(p.w, 1, 4, 2), h = clamp(p.h, 1, 3, 1);
        return `<div class="panel dash-panel" style="grid-column:span ${w};grid-row:span ${h}" data-i="${i}">
            ${dash.editing ? `<div class="dash-edit-bar">
                <button type="button" class="chart-tool" data-op="left" title="Move earlier">◀</button>
                <button type="button" class="chart-tool" data-op="right" title="Move later">▶</button>
                <span class="sep"></span>
                <button type="button" class="chart-tool" data-op="w-" title="Narrower">W−</button><span class="mono small">${w}</span>
                <button type="button" class="chart-tool" data-op="w+" title="Wider">W+</button>
                <button type="button" class="chart-tool" data-op="h-" title="Lower">H−</button><span class="mono small">${h}</span>
                <button type="button" class="chart-tool" data-op="h+" title="Higher">H+</button>
                <span class="spacer"></span>
                <button type="button" class="chart-tool" data-op="edit" title="Edit this panel">Edit</button>
                <button type="button" class="chart-tool danger" data-op="remove" title="Remove this panel">Remove</button>
            </div>` : ''}
            <div class="panel-body">
                <div class="chart-title"><span class="panel-title-text">${escapeHtml(p.title || 'Panel')}</span><span class="hint" data-hint></span></div>
                <div class="dash-content${p.type === 'timeseries' ? ' chart' : ''}" data-content>${loadingHtml()}</div>
            </div>
        </div>`;
    }).join('');
    grid.querySelectorAll('.dash-panel').forEach(el => {
        const i = Number(el.dataset.i);
        renderPanel(el, panels[i], seq).catch(e => {
            if (seq !== dash.seq) return;
            const content = el.querySelector('[data-content]');
            if (content.classList.contains('chart')) emptyChart(content, e.message);
            else content.innerHTML = errorHtml(e.message);
        });
    });
}

document.getElementById('dash-grid').addEventListener('click', (e) => {
    const b = e.target.closest('[data-op]');
    if (!b || !dash.editing) return;
    const i = Number(b.closest('[data-i]').dataset.i);
    const panels = dash.draft.panels;
    const p = panels[i];
    const op = b.dataset.op;
    if (op === 'left' && i > 0) [panels[i - 1], panels[i]] = [panels[i], panels[i - 1]];
    else if (op === 'right' && i < panels.length - 1) [panels[i + 1], panels[i]] = [panels[i], panels[i + 1]];
    else if (op === 'w-') p.w = clamp((p.w || 2) - 1, 1, 4, 2);
    else if (op === 'w+') p.w = clamp((p.w || 2) + 1, 1, 4, 2);
    else if (op === 'h-') p.h = clamp((p.h || 1) - 1, 1, 3, 1);
    else if (op === 'h+') p.h = clamp((p.h || 1) + 1, 1, 3, 1);
    else if (op === 'edit') return editPanel(i);
    else if (op === 'remove') panels.splice(i, 1);
    renderPanels();
});

// The height a panel's chart gets: its rows minus the title row
const panelChartHeight = (p, editing) => Math.max(70, clamp(p.h, 1, 3, 1) * ROW_HEIGHT + (clamp(p.h, 1, 3, 1) - 1) * 14 - 62 - (editing ? 30 : 0));

async function renderPanel(el, p, seq) {
    const content = el.querySelector('[data-content]');
    const hint = el.querySelector('[data-hint]');
    const q = p.query || {};
    const source = panelSource(q);
    const w = rangeWindow();
    const height = panelChartHeight(p, dash.editing);
    const stale = () => seq !== dash.seq;
    content.style.maxHeight = (height + 10) + 'px';

    if (source === 'metrics') {
        const queries = panelMetricQueries(q);
        if (!queries.length) { content.innerHTML = emptyHtml('No metric chosen'); return; }
        const result = await fetchMetricSeries(queries, w);
        if (stale()) return;
        if (p.type === 'timeseries') {
            content.style.maxHeight = '';
            hint.innerHTML = `<button type="button" class="link-btn" title="Open in Metrics">explore</button>`;
            hint.querySelector('button').onclick = () => openMetrics(queries);
            return timeChart(content, {
                x: result.x, series: result.series, xRange: [w.from, w.to], aligned: result.aligned,
                height, compact: true, title: p.title, filename: 'vigil-panel',
                format: (v, s) => fmtMetric(v, s && s.unit),
                onZoom: zoomRange, zoomed: rangeZoomed(), onReset: rangeBack,
                empty: 'No data in the time range',
            });
        }
        const lasts = result.series.map(s => {
            let last = null;
            for (let i = s.values.length - 1; i >= 0; i--) if (!missing(s.values[i])) { last = s.values[i]; break; }
            const real = s.values.filter(v => !missing(v));
            return {name: s.name, last, avg: real.length ? real.reduce((a, b) => a + b, 0) / real.length : null, max: real.length ? Math.max(...real) : null, values: s.values, unit: s.unit, anomalies: s.anomalies.length};
        });
        if (!lasts.length) { content.innerHTML = emptyHtml('No data in the time range'); return; }
        if (p.type === 'stat') {
            const s = lasts[0];
            hint.textContent = lasts.length > 1 ? `${lasts.length} series: the first` : 'last value';
            content.innerHTML = `<div class="stat-panel"><div class="stat-value${s.anomalies ? ' txt-bad' : ''}">${escapeHtml(fmtMetric(s.last, s.unit))}</div>
                <div class="stat-sub">avg ${escapeHtml(fmtMetric(s.avg, s.unit))} · max ${escapeHtml(fmtMetric(s.max, s.unit))}${s.anomalies ? ` · <span class="txt-bad">${num(s.anomalies)} anomalies</span>` : ''}</div>
                ${sparkline(s.values, {width: 260, height: Math.max(24, height - 80), color: 'var(--series-1)', highlightLast: true})}</div>`;
            return;
        }
        if (p.type === 'bar') {
            const max = Math.max(1e-9, ...lasts.map(s => Math.abs(s.last || 0)));
            content.innerHTML = `<div class="rank">${lasts.sort((a, b) => (b.last || 0) - (a.last || 0)).map(s => `<div class="rank-row">
                <div class="name"><b>${escapeHtml(s.name)}</b></div><div class="n">${escapeHtml(fmtMetric(s.last, s.unit))}</div>
                <div class="bar-track"><i style="width:${(Math.abs(s.last || 0) / max * 100).toFixed(1)}%"></i></div></div>`).join('')}</div>`;
            return;
        }
        content.innerHTML = `<table class="data"><thead><tr><th>Series</th><th class="n">Last</th><th class="n">Avg</th><th class="n">Max</th></tr></thead><tbody>${lasts.map(s => `<tr>
            <td>${escapeHtml(s.name)}</td><td class="n">${escapeHtml(fmtMetric(s.last, s.unit))}</td><td class="n">${escapeHtml(fmtMetric(s.avg, s.unit))}</td><td class="n">${escapeHtml(fmtMetric(s.max, s.unit))}</td></tr>`).join('')}</tbody></table>`;
        return;
    }

    if (source === 'logs') {
        const lq = q.q || '';
        if (p.type === 'logs' || p.type === 'table') {
            const limit = clamp(p.h, 1, 3, 1) * 8;
            const data = await api('logs?' + qs({q: lq, ...w, limit}));
            if (stale()) return;
            hint.innerHTML = `<button type="button" class="link-btn">open in Logs</button>`;
            hint.querySelector('button').onclick = () => openLogs(lq);
            const rows = data.rows || [];
            content.innerHTML = rows.length ? `<table class="data logs-table compact"><tbody>${rows.map(r => `<tr class="log-row s${severityNumber(r.severity)}">
                <td class="t mono">${escapeHtml(fmtLogTime(r.ts))}</td><td>${sevPill(r.severity)}</td><td class="host">${escapeHtml(r.host || '')}</td><td class="msg">${escapeHtml(r.message)}</td></tr>`).join('')}</tbody></table>`
                : emptyHtml('No lines match in the time range');
            return;
        }
        const data = await api('logs/histogram?' + qs({q: lq, ...w, buckets: p.type === 'stat' ? 30 : 60}));
        if (stale()) return;
        if (p.type === 'stat') {
            const totals = (data.t || []).map((_, i) => Object.values(data.by_severity || {}).reduce((a, arr) => a + (arr[i] || 0), 0));
            hint.textContent = lq || 'all lines';
            content.innerHTML = `<div class="stat-panel link" title="Open in Logs"><div class="stat-value">${fmtCount(data.total)}</div><div class="stat-sub">lines in ${escapeHtml(rangeText())}</div>
                ${sparkline(totals, {width: 260, height: Math.max(24, height - 80), color: 'var(--series-1)'})}</div>`;
            content.querySelector('.stat-panel').onclick = () => openLogs(lq);
            return;
        }
        content.style.maxHeight = '';
        hint.textContent = `${num(data.total)} lines`;
        return severityChart(content, data, {height, compact: true, title: p.title, onZoom: zoomRange, zoomed: rangeZoomed(), onReset: rangeBack});
    }

    if (source === 'fields') {
        const field = ['hosts', 'apps', 'sources', 'severities'].includes(q.field) ? q.field : 'hosts';
        const data = await api('logs/fields?' + qs({...w, q: q.q || undefined}));
        if (stale()) return;
        const values = (data[field] || []).slice(0, clamp(p.h, 1, 3, 1) * 6);
        const filterKey = {hosts: 'host', apps: 'app', sources: 'source', severities: 'severity'}[field];
        hint.textContent = `top ${field}${q.q ? ' · ' + q.q : ''}`;
        if (!values.length) { content.innerHTML = emptyHtml('Nothing in the time range'); return; }
        const max = Math.max(1, ...values.map(v => v.count));
        const label = v => field === 'severities' ? severityName(v.value) : v.value;
        if (p.type === 'table') {
            content.innerHTML = `<table class="data"><thead><tr><th>${escapeHtml(filterKey)}</th><th class="n">Lines</th></tr></thead><tbody>${values.map((v, i) => `<tr class="link" data-v="${i}"><td>${escapeHtml(label(v))}</td><td class="n">${num(v.count)}</td></tr>`).join('')}</tbody></table>`;
        } else {
            content.innerHTML = `<div class="rank">${values.map((v, i) => `<div class="rank-row link" data-v="${i}">
                <div class="name"><b>${field === 'severities' ? sevPill(v.value) : escapeHtml(label(v))}</b></div><div class="n">${fmtCount(v.count)}</div>
                <div class="bar-track"><i style="width:${(v.count / max * 100).toFixed(1)}%${field === 'severities' ? ';background:' + severityColor(v.value) : ''}"></i></div></div>`).join('')}</div>`;
        }
        content.onclick = (e) => {
            const r = e.target.closest('[data-v]');
            if (r) openLogs([q.q || '', `${filterKey}:${queryValue(label(values[Number(r.dataset.v)]))}`].filter(Boolean).join(' '));
        };
        return;
    }

    // patterns
    const data = await api('patterns?' + qs({...w, sort: q.sort || 'count', q: q.q || undefined}));
    if (stale()) return;
    const rows = (data.patterns || []).slice(0, clamp(p.h, 1, 3, 1) * 6);
    hint.textContent = `by ${q.sort || 'count'}`;
    content.innerHTML = rows.length ? `<table class="data patterns-table compact"><tbody>${rows.map((r, i) => `<tr class="link" data-v="${i}">
        <td class="n"><b>${fmtCount(r.count)}</b></td><td>${sparkline(r.spark, {width: 70, height: 18})}</td>
        <td class="wrap template-cell">${patternBadges(r, w.from)}<span class="template">${templateHtml(r.template)}</span></td></tr>`).join('')}</tbody></table>`
        : emptyHtml('No patterns in the time range');
    content.onclick = (e) => {
        const r = e.target.closest('[data-v]');
        if (r) openLogs(`pattern:${Number(rows[Number(r.dataset.v)].id)}`);
    };
}

// =====================================================================
// Editing
// =====================================================================
function editDashboard() {
    const d = currentDashboard();
    if (!d) return;
    dash.draft = JSON.parse(JSON.stringify(d));
    if (!Array.isArray(dash.draft.panels)) dash.draft.panels = [];
    dash.editing = true;
    renderDashboard();
}

function cancelDashboardEdit() {
    dash.editing = false;
    dash.draft = null;
    renderDashboard();
}

async function saveDashboard() {
    const d = dash.draft;
    if (!d) return;
    try {
        const saved = await api('dashboards/save', {id: d.id, name: d.name, panels: d.panels});
        dash.editing = false;
        dash.draft = null;
        await loadDashboardList();
        if (saved && saved.id !== undefined) dash.currentId = String(saved.id);
        else if (saved && saved.dashboard) dash.currentId = dashKey(saved.dashboard);
        toast(`Saved "${d.name}"`, 'success');
        renderDashboard();
    } catch (e) {
        toast(e.message, 'error');
    }
}

function newDashboard() {
    openModal({
        title: 'New dashboard',
        body: '<div class="form-row"><span>Name</span><input type="text" class="input" id="dash-name" maxlength="80" placeholder="e.g. Servers"></div>',
        label: 'Create',
        onConfirm: async () => {
            const name = document.getElementById('dash-name').value.trim();
            if (!name) throw new Error('Give the dashboard a name');
            if (dash.list.some(d => d.name === name)) throw new Error('There is a dashboard with that name already');
            const saved = await api('dashboards/save', {name, panels: []});
            await loadDashboardList();
            const made = dash.list.find(d => d.name === name);
            dash.currentId = made ? dashKey(made) : (saved && saved.id !== undefined ? String(saved.id) : name);
            if (activeTab !== 'dashboards') switchTab('dashboards', dash.currentId);
            editDashboard();
        },
    });
}

function renameDashboard() {
    const d = dash.draft;
    if (!d) return;
    openModal({
        title: 'Rename dashboard',
        body: `<div class="form-row"><span>Name</span><input type="text" class="input" id="dash-name" maxlength="80" value="${escapeHtml(d.name)}"></div>`,
        label: 'Rename',
        onConfirm: async () => {
            const name = document.getElementById('dash-name').value.trim();
            if (!name) throw new Error('Give the dashboard a name');
            d.name = name;
            toast('Renamed: Save to keep it');
        },
    });
}

function deleteDashboard() {
    const d = dash.draft;
    if (!d) return;
    confirmModal('Delete dashboard', `<p>Delete <b>${escapeHtml(d.name)}</b> and its ${num((d.panels || []).length)} panels?${d.builtin ? ' It is a built-in dashboard: it does not come back by itself.' : ''}</p>`, 'Delete', async () => {
        await api('dashboards/delete', {id: d.id, name: d.name});
        dash.editing = false;
        dash.draft = null;
        dash.currentId = null;
        await loadDashboardList();
        toast(`Deleted "${d.name}"`, 'success');
        renderDashboard();
    });
}

// The panel editor: index in the draft, or null for a new one
function editPanel(index) {
    const p = index === null ? {id: newPanelId(), title: '', type: 'timeseries', query: {source: 'metrics'}, w: 2, h: 1} : JSON.parse(JSON.stringify(dash.draft.panels[index]));
    const q = p.query || {};
    const source = panelSource(q);
    const mq = source === 'metrics' ? (panelMetricQueries(q)[0] || blankQuery()) : blankQuery();
    const filterText = Object.entries(mq.filter || {}).map(([k, v]) => `${k}=${v}`).join(', ');
    const opt = (list, cur) => list.map(o => `<option value="${o.key}"${o.key === cur ? ' selected' : ''}>${escapeHtml(o.label)}</option>`).join('');
    const overlay = openModal({
        title: index === null ? 'Add panel' : 'Edit panel',
        wide: true,
        body: `<div class="form-row"><span>Title</span><input type="text" class="input" id="pe-title" maxlength="80" value="${escapeHtml(p.title)}" placeholder="Panel title"></div>
            <div class="form-row"><span>Type</span><select class="select" id="pe-type">${opt(PANEL_TYPES, p.type)}</select></div>
            <div class="form-row"><span>Data</span><select class="select" id="pe-source">${opt(PANEL_SOURCES, source)}</select></div>
            <div data-src="metrics">
                <div class="form-row"><span>Metric</span><input type="text" class="input" id="pe-name" list="metric-name-options" value="${escapeHtml(mq.name)}" spellcheck="false" autocomplete="off"></div>
                <div class="form-row"><span>Label filter</span><input type="text" class="input mono" id="pe-filter" value="${escapeHtml(filterText)}" placeholder="interface=ether1, host=freqle" spellcheck="false"></div>
                <div class="form-row"><span>Aggregation</span><select class="select" id="pe-agg">${METRIC_AGGS.map(a => `<option${a === mq.agg ? ' selected' : ''}>${a}</option>`).join('')}</select></div>
                <div class="form-row"><span>Group by</span><input type="text" class="input mono" id="pe-group" value="${escapeHtml(mq.group)}" placeholder="a label key, e.g. interface" spellcheck="false"></div>
                <div class="form-row"><span></span><label class="action-check"><input type="checkbox" id="pe-anomaly"${mq.anomaly ? ' checked' : ''}> Anomaly band</label></div>
                ${Array.isArray(q.queries) && q.queries.length > 1 ? `<p class="panel-sub">This panel overlays ${q.queries.length} queries; saving here keeps only the first. Edit the overlay in Metrics and add it again.</p>` : ''}
            </div>
            <div data-src="logs fields patterns">
                <div class="form-row"><span>Log search</span><input type="text" class="input mono" id="pe-q" value="${escapeHtml(q.q || '')}" placeholder="empty: all lines; e.g. severity:<=err" spellcheck="false"></div>
            </div>
            <div data-src="fields"><div class="form-row"><span>Field</span><select class="select" id="pe-field">${['hosts', 'apps', 'sources', 'severities'].map(f => `<option${f === q.field ? ' selected' : ''}>${f}</option>`).join('')}</select></div></div>
            <div data-src="patterns"><div class="form-row"><span>Order</span><select class="select" id="pe-sort">${opt(PATTERN_SORTS, q.sort || 'count')}</select></div></div>
            <div class="form-row"><span>Width</span><select class="select" id="pe-w">${[1, 2, 3, 4].map(n => `<option${n === clamp(p.w, 1, 4, 2) ? ' selected' : ''}>${n}</option>`).join('')}</select>
                <span class="muted">Height</span><select class="select" id="pe-h">${[1, 2, 3].map(n => `<option${n === clamp(p.h, 1, 3, 1) ? ' selected' : ''}>${n}</option>`).join('')}</select></div>
            <p class="panel-sub" id="pe-help"></p>`,
        label: index === null ? 'Add' : 'Apply',
        onConfirm: async () => {
            const src = document.getElementById('pe-source').value;
            const out = {
                id: p.id || newPanelId(),
                title: document.getElementById('pe-title').value.trim(),
                type: document.getElementById('pe-type').value,
                w: Number(document.getElementById('pe-w').value),
                h: Number(document.getElementById('pe-h').value),
            };
            if (src === 'metrics') {
                const name = document.getElementById('pe-name').value.trim();
                if (!name) throw new Error('Choose a metric');
                const filter = {};
                document.getElementById('pe-filter').value.split(',').map(s => s.trim()).filter(Boolean).forEach(pair => {
                    const k = pair.indexOf('=');
                    if (k < 1) throw new Error(`"${pair}" is not label=value`);
                    filter[pair.slice(0, k).trim()] = pair.slice(k + 1).trim();
                });
                out.query = {source: 'metrics', name, filter, agg: document.getElementById('pe-agg').value};
                const group = document.getElementById('pe-group').value.trim();
                if (group) out.query.group = group;
                if (document.getElementById('pe-anomaly').checked) out.query.anomaly = true;
            } else {
                out.query = {source: src, q: document.getElementById('pe-q').value.trim()};
                if (src === 'fields') out.query.field = document.getElementById('pe-field').value;
                if (src === 'patterns') out.query.sort = document.getElementById('pe-sort').value;
            }
            if (!out.title) out.title = out.query.name || out.query.q || `${out.query.field || out.query.source}`;
            if (index === null) dash.draft.panels.push(out); else dash.draft.panels[index] = out;
            renderPanels();
        },
    });
    const help = {
        'metrics': {timeseries: 'The metric over time.', stat: 'The last value, with a sparkline.', bar: 'The last value per series (group by a label).', table: 'Last, average and maximum per series.', logs: 'Log lines need a log search as data.'},
        'logs': {timeseries: 'Lines over time, stacked by severity.', stat: 'How many lines match.', logs: 'The newest matching lines.', table: 'The newest matching lines.', bar: 'Use "Log field" for top values.'},
        'fields': {bar: 'The top values of the field.', table: 'The top values of the field.'},
        'patterns': {table: 'The top patterns.', bar: 'The top patterns.'},
    };
    const sync = () => {
        const src = overlay.querySelector('#pe-source').value;
        overlay.querySelectorAll('[data-src]').forEach(el => { el.hidden = !el.dataset.src.split(' ').includes(src); });
        const type = overlay.querySelector('#pe-type').value;
        overlay.querySelector('#pe-help').textContent = (help[src] && help[src][type]) || 'This type shows a table of this data.';
    };
    overlay.querySelector('#pe-source').onchange = sync;
    overlay.querySelector('#pe-type').onchange = sync;
    sync();
    if (!metrics.names.length) loadMetricNames('').then(() => renderNameOptions());
}

// From Metrics: add a panel to a dashboard (or a new one) and save it
async function addPanelToDashboard(panel) {
    await loadDashboardList();
    const options = dash.list.map(d => `<option value="${escapeHtml(dashKey(d))}">${escapeHtml(d.name)}</option>`).join('');
    openModal({
        title: 'Add to dashboard',
        body: `<div class="form-row"><span>Dashboard</span><select class="select" id="atd-dash">${options}<option value="__new">New dashboard…</option></select></div>
            <div class="form-row" id="atd-new-row" ${dash.list.length ? 'hidden' : ''}><span>New name</span><input type="text" class="input" id="atd-new" maxlength="80"></div>
            <div class="form-row"><span>Panel title</span><input type="text" class="input" id="atd-title" maxlength="80" value="${escapeHtml(panel.title)}"></div>
            <div class="form-row"><span>Width</span><select class="select" id="atd-w">${[1, 2, 3, 4].map(n => `<option${n === panel.w ? ' selected' : ''}>${n}</option>`).join('')}</select></div>`,
        label: 'Add',
        onConfirm: async () => {
            const sel = document.getElementById('atd-dash').value;
            const p = {...panel, id: newPanelId(), title: document.getElementById('atd-title').value.trim() || panel.title, w: Number(document.getElementById('atd-w').value)};
            let target;
            if (sel === '__new' || !dash.list.length) {
                const name = document.getElementById('atd-new').value.trim();
                if (!name) throw new Error('Name the new dashboard');
                target = {name, panels: [p]};
            } else {
                const d = dash.list.find(x => dashKey(x) === sel);
                target = {id: d.id, name: d.name, panels: [...(d.panels || []), p]};
            }
            const saved = await api('dashboards/save', target);
            await loadDashboardList();
            const made = dash.list.find(d => d.name === target.name);
            dash.currentId = made ? dashKey(made) : (saved && saved.id !== undefined ? String(saved.id) : target.name);
            toast(`Added to "${target.name}"`, 'success');
            switchTab('dashboards', dash.currentId);
        },
    });
    const sel = document.getElementById('atd-dash');
    sel.onchange = () => { document.getElementById('atd-new-row').hidden = sel.value !== '__new'; };
}

onTab('dashboards', showDashboards);
onRange('dashboards', () => renderPanels());
onLeave('dashboards', () => clearInterval(dash.timer));
