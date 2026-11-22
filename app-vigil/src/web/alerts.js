// Vigil dashboard - Alerts: the rules with their live state, the rule editor (all five kinds, with
// Test), built-in templates, and the history of what fired.

const RULE_KINDS = [
    {key: 'log_count', label: 'Log lines', help: 'The number of log lines matching a search in the last window.'},
    {key: 'metric', label: 'Metric threshold', help: 'An aggregate of a metric over the last window.'},
    {key: 'absence', label: 'No data', help: 'Seconds since a log search or a metric last had data: fires when a host or exporter goes silent.'},
    {key: 'pattern_new', label: 'New patterns', help: 'How many new log patterns appeared in the window (optionally only for a search).'},
    {key: 'anomaly', label: 'Anomaly', help: 'How far the last point of a metric is outside its usual range, in sigma (|z|); 3 is the band\'s edge.'},
];
const RULE_OPS = ['>', '>=', '<', '<=', '=='];
const RULE_SEVERITIES = ['critical', 'warning', 'info'];
const RULE_WINDOWS = [60, 300, 900, 1800, 3600, 6 * 3600, 86400];
let rules = [];
let rulesTimer = null;

function ruleSeverityPill(sev) {
    const s = String(sev || 'warning').toLowerCase();
    const cls = s === 'critical' || s === 'crit' ? 'bad' : s === 'warning' || s === 'warn' ? 'warn' : '';
    return `<span class="badge ${cls}">${escapeHtml(s)}</span>`;
}

const kindLabel = k => (RULE_KINDS.find(x => x.key === k) || {label: k}).label;
const filterText = f => Object.entries(f || {}).map(([k, v]) => `${k}=${v}`).join(', ');
function parseFilterText(text) {
    const filter = {};
    String(text || '').split(',').map(s => s.trim()).filter(Boolean).forEach(pair => {
        const k = pair.indexOf('=');
        if (k < 1) throw new Error(`"${pair}" is not label=value`);
        filter[pair.slice(0, k).trim()] = pair.slice(k + 1).trim();
    });
    return filter;
}

// One line saying what a rule measures
function describeRule(r) {
    const q = r.query || {};
    const c = r.condition || {};
    const win = q.window ? ` in ${fmtDuration(q.window)}` : '';
    const metric = () => `${q.agg || 'avg'}(${q.name || '?'}${Object.keys(q.filter || {}).length ? '{' + filterText(q.filter) + '}' : ''})`;
    let what;
    if (r.kind === 'log_count') what = `lines matching ${q.q ? `“${q.q}”` : 'anything'}${win}`;
    else if (r.kind === 'metric') what = `${metric()}${win}`;
    else if (r.kind === 'absence') what = `seconds since ${q.name ? q.name : `lines matching “${q.q || '*'}”`}`;
    else if (r.kind === 'pattern_new') what = `new patterns${q.q ? ` in “${q.q}”` : ''}${win}`;
    else if (r.kind === 'anomaly') what = `|z| of ${q.name || '?'}${Object.keys(q.filter || {}).length ? '{' + filterText(q.filter) + '}' : ''}`;
    else what = r.kind;
    return `${what} ${c.op || '>'} ${missing(c.threshold) ? '?' : c.threshold}${r.for_seconds ? ` for ${fmtDuration(r.for_seconds)}` : ''}`;
}

async function showAlerts() {
    renderTemplates();
    await Promise.all([loadRules(), loadAlertHistory()]);
    clearInterval(rulesTimer);
    // The states change as the engine runs (every 30 s)
    rulesTimer = setInterval(() => {
        if (activeTab !== 'alerts' || document.visibilityState !== 'visible') return;
        loadRules();
    }, 10000);
}

async function loadRules() {
    const el = document.getElementById('rules-table');
    if (!ready()) { el.innerHTML = emptyHtml('The database is not connected'); return; }
    try {
        const data = await api('rules');
        rules = (data && (data.rules || data)) || [];
        if (!Array.isArray(rules)) rules = [];
    } catch (e) {
        el.innerHTML = errorHtml(e.message);
        return handleError(e);
    }
    renderRules();
}

function renderRules() {
    const el = document.getElementById('rules-table');
    const firing = rules.filter(r => r.enabled !== false && r.state === 'firing').length;
    const pending = rules.filter(r => r.enabled !== false && r.state === 'pending').length;
    document.getElementById('rules-count').textContent = `${num(rules.length)} rules${firing ? ` · ${firing} firing` : ''}${pending ? ` · ${pending} pending` : ''}`;
    if (!rules.length) {
        el.innerHTML = '<div class="empty-state">No rules yet. Start from a template above, or <button type="button" class="btn btn-small" onclick="openRuleEditor()">+ New rule</button></div>';
        return;
    }
    const order = {firing: 0, pending: 1, ok: 2};
    const sorted = [...rules].sort((a, b) => (a.enabled === false) - (b.enabled === false)
        || (order[a.state] ?? 3) - (order[b.state] ?? 3) || String(a.name).localeCompare(String(b.name)));
    el.innerHTML = `<table class="data rules-table"><thead><tr><th>State</th><th>Rule</th><th>Severity</th><th class="n">Value</th><th>Since</th><th>Checked</th><th></th></tr></thead><tbody>
        ${sorted.map(r => `<tr class="${r.enabled === false ? 'off' : ''}" data-id="${escapeHtml(r.id)}">
            <td>${statePill(r.state, r.enabled)}</td>
            <td class="wrap"><b>${escapeHtml(r.name)}</b><small class="rule-desc">${escapeHtml(kindLabel(r.kind))} · ${escapeHtml(describeRule(r))}</small></td>
            <td>${ruleSeverityPill(r.severity)}${r.notify ? ' <span class="muted" title="Sends a notification">🔔</span>' : ''}</td>
            <td class="n">${escapeHtml(fmtValue(r.last_value))}</td>
            <td class="muted" title="${escapeHtml(r.state_since ? fmtDateTime(r.state_since) : '')}">${escapeHtml(r.state_since ? fmtAgo(r.state_since) : '–')}</td>
            <td class="muted">${escapeHtml(r.last_eval ? fmtAgo(r.last_eval) : 'not yet')}</td>
            <td class="n actions">
                <button type="button" class="btn btn-small" data-act="edit">Edit</button>
                <button type="button" class="btn btn-small" data-act="toggle">${r.enabled === false ? 'Enable' : 'Disable'}</button>
                <button type="button" class="btn btn-small btn-danger" data-act="delete" title="Delete">×</button>
            </td>
        </tr>`).join('')}</tbody></table>`;
    el.onclick = async (e) => {
        const b = e.target.closest('[data-act]');
        if (!b) return;
        const id = b.closest('[data-id]').dataset.id;
        const r = rules.find(x => String(x.id) === id);
        if (!r) return;
        if (b.dataset.act === 'edit') return openRuleEditor(r);
        if (b.dataset.act === 'toggle') {
            try {
                await api('rules/save', {...cleanRule(r), enabled: r.enabled === false});
                toast(`${r.name} ${r.enabled === false ? 'enabled' : 'disabled'}`, 'success');
                loadRules();
            } catch (err) { toast(err.message, 'error'); }
            return;
        }
        confirmModal('Delete rule', `<p>Delete the rule <b>${escapeHtml(r.name)}</b> and its alert history?</p>`, 'Delete', async () => {
            await api('rules/delete', {id: r.id});
            toast(`Deleted ${r.name}`, 'success');
            loadRules();
            loadAlertHistory();
        });
    };
}

// What the API takes: the rule without its live state
function cleanRule(r) {
    const out = {name: r.name, kind: r.kind, query: r.query || {}, condition: r.condition || {}, for_seconds: r.for_seconds || 0,
        severity: r.severity || 'warning', enabled: r.enabled !== false, notify: Boolean(r.notify)};
    if (r.id !== undefined && r.id !== null) out.id = r.id;
    return out;
}

// =====================================================================
// Templates
// =====================================================================
const RULE_TEMPLATES = [
    {key: 'errors', label: 'Errors > 20 in 5 min', make: async () => ({name: 'Errors from any host', kind: 'log_count', query: {q: 'severity:<=err', window: 300}, condition: {op: '>', threshold: 20}, for_seconds: 0, severity: 'warning'})},
    {key: 'silent', label: 'Host silent 10 min', make: async () => {
        let host = '';
        try { const f = await api('logs/fields?' + qs(rangeWindow())); host = ((f.hosts || [])[0] || {}).value || ''; } catch (e) { /* the user fills it in */ }
        return {name: `${host || 'Host'} silent`, kind: 'absence', query: {q: host ? `host:${queryValue(host)}` : 'host:'}, condition: {op: '>', threshold: 600}, for_seconds: 0, severity: 'warning'};
    }},
    {key: 'ssh', label: 'SSH login', make: async () => ({name: 'SSH login', kind: 'log_count', query: {q: 'app:sshd Accepted', window: 300}, condition: {op: '>', threshold: 0}, for_seconds: 0, severity: 'info'})},
    {key: 'patterns', label: 'New log patterns', make: async () => ({name: 'New log patterns', kind: 'pattern_new', query: {q: '', window: 3600}, condition: {op: '>', threshold: 0}, for_seconds: 0, severity: 'info'})},
    {key: 'cpu', label: 'Router CPU > 85 %', make: async () => {
        let name = '';
        try { const n = await api('metrics/names?' + qs({q: 'cpu'})); name = ((n.names || [])[0] || {}).name || ''; } catch (e) { /* the user fills it in */ }
        return {name: 'Router CPU > 85 %', kind: 'metric', query: {name, filter: {}, agg: 'avg', window: 300}, condition: {op: '>', threshold: 85}, for_seconds: 300, severity: 'warning'};
    }},
];

function renderTemplates() {
    const el = document.getElementById('rule-templates');
    el.innerHTML = RULE_TEMPLATES.map(t => `<button type="button" class="btn btn-small" data-template="${t.key}">${escapeHtml(t.label)}</button>`).join('');
    el.onclick = async (e) => {
        const b = e.target.closest('[data-template]');
        if (!b) return;
        const t = RULE_TEMPLATES.find(x => x.key === b.dataset.template);
        openRuleEditor({...(await t.make()), enabled: true, notify: true});
    };
}

// =====================================================================
// The rule editor
// =====================================================================
function openRuleEditor(rule) {
    if (activeTab !== 'alerts') switchTab('alerts');
    const r = rule ? JSON.parse(JSON.stringify(rule)) : {name: '', kind: 'log_count', query: {q: '', window: 300}, condition: {op: '>', threshold: 0}, for_seconds: 0, severity: 'warning', enabled: true, notify: true};
    r.query = r.query || {};
    r.condition = r.condition || {op: '>', threshold: 0};
    const opt = (list, cur, label) => list.map(o => `<option value="${escapeHtml(o)}"${String(o) === String(cur) ? ' selected' : ''}>${escapeHtml(label ? label(o) : o)}</option>`).join('');
    const overlay = openModal({
        title: r.id !== undefined && r.id !== null ? 'Edit rule' : 'New rule',
        wide: true,
        body: `<div class="form-row"><span>Name</span><input type="text" class="input" id="re-name" maxlength="120" value="${escapeHtml(r.name)}" placeholder="What this rule watches"></div>
            <div class="form-row"><span>Kind</span><select class="select" id="re-kind">${RULE_KINDS.map(k => `<option value="${k.key}"${k.key === r.kind ? ' selected' : ''}>${escapeHtml(k.label)}</option>`).join('')}</select></div>
            <p class="panel-sub" id="re-kind-help"></p>
            <div class="rule-fields">
                <div class="form-row" data-kinds="absence"><span>Watch</span>
                    <label class="action-check"><input type="radio" name="re-target" value="logs"${r.query.name ? '' : ' checked'}> log lines</label>
                    <label class="action-check"><input type="radio" name="re-target" value="metric"${r.query.name ? ' checked' : ''}> a metric</label></div>
                <div class="form-row" data-kinds="log_count pattern_new absence:logs"><span>Log search</span><input type="text" class="input mono" id="re-q" value="${escapeHtml(r.query.q || '')}" placeholder='e.g. severity:<=err host:mikrotik' spellcheck="false"></div>
                <div class="form-row" data-kinds="metric anomaly absence:metric"><span>Metric</span><input type="text" class="input mono" id="re-metric" list="metric-name-options" value="${escapeHtml(r.query.name || '')}" spellcheck="false" autocomplete="off"></div>
                <div class="form-row" data-kinds="metric anomaly absence:metric"><span>Label filter</span><input type="text" class="input mono" id="re-filter" value="${escapeHtml(filterText(r.query.filter))}" placeholder="interface=ether1, host=freqle" spellcheck="false"></div>
                <div class="form-row" data-kinds="metric"><span>Aggregation</span><select class="select" id="re-agg">${opt(METRIC_AGGS, r.query.agg || 'avg')}</select></div>
                <div class="form-row" data-kinds="log_count metric pattern_new"><span>Window</span><select class="select" id="re-window">${opt(RULE_WINDOWS, r.query.window || 300, fmtDuration)}</select></div>
            </div>
            <div class="form-row"><span>Fires when the value</span><select class="select" id="re-op" style="flex:0 0 70px;min-width:0">${opt(RULE_OPS, r.condition.op || '>')}</select>
                <input type="number" class="input" id="re-threshold" step="any" value="${escapeHtml(missing(r.condition.threshold) ? 0 : r.condition.threshold)}"></div>
            <div class="form-row"><span>For at least</span><select class="select" id="re-for">${opt([0, 60, 120, 300, 600, 900, 1800, 3600], r.for_seconds || 0, s => s ? fmtDuration(s) : 'at once')}</select>
                <span class="muted">pending until then</span></div>
            <div class="form-row"><span>Severity</span><select class="select" id="re-severity">${opt(RULE_SEVERITIES, r.severity || 'warning')}</select></div>
            <div class="form-row"><span></span><label class="action-check"><input type="checkbox" id="re-enabled"${r.enabled !== false ? ' checked' : ''}> Enabled</label>
                <label class="action-check" title="A Home Assistant notification (and the notify service, when set) on firing and resolved"><input type="checkbox" id="re-notify"${r.notify !== false ? ' checked' : ''}> Notify</label></div>
            <div class="rule-test" id="re-test-result" hidden></div>`,
        foot: '<button type="button" class="btn" id="re-test">Test</button>',
        label: 'Save rule',
        onConfirm: async () => {
            const out = readRuleForm(r);
            await api('rules/save', out);
            toast(`Saved ${out.name}`, 'success');
            loadRules();
        },
    });
    const sync = () => {
        const kind = overlay.querySelector('#re-kind').value;
        const target = (overlay.querySelector('input[name="re-target"]:checked') || {}).value || 'logs';
        overlay.querySelector('#re-kind-help').textContent = (RULE_KINDS.find(k => k.key === kind) || {}).help || '';
        overlay.querySelectorAll('[data-kinds]').forEach(el => {
            el.hidden = !el.dataset.kinds.split(' ').some(k => k === kind || k === `${kind}:${target}`);
        });
    };
    overlay.querySelector('#re-kind').onchange = () => {
        // Sensible thresholds per kind
        const kind = overlay.querySelector('#re-kind').value;
        const th = overlay.querySelector('#re-threshold');
        if (kind === 'anomaly' && Number(th.value) < 1) th.value = 3;
        if (kind === 'absence' && Number(th.value) < 60) th.value = 600;
        sync();
    };
    overlay.querySelectorAll('input[name="re-target"]').forEach(i => { i.onchange = sync; });
    overlay.querySelector('#re-test').onclick = () => testRule(r);
    sync();
    if (!metrics.names.length) loadMetricNames('').then(() => renderNameOptions());
}

function readRuleForm(base) {
    const val = id => document.getElementById(id).value;
    const kind = val('re-kind');
    const target = (document.querySelector('input[name="re-target"]:checked') || {}).value || 'logs';
    const name = val('re-name').trim();
    if (!name) throw new Error('Give the rule a name');
    const threshold = Number(val('re-threshold'));
    if (!Number.isFinite(threshold)) throw new Error('The threshold must be a number');
    let query;
    const metricQuery = () => {
        const metric = val('re-metric').trim();
        if (!metric) throw new Error('Choose a metric');
        return {name: metric, filter: parseFilterText(val('re-filter'))};
    };
    if (kind === 'log_count') query = {q: val('re-q').trim(), window: Number(val('re-window'))};
    else if (kind === 'metric') query = {...metricQuery(), agg: val('re-agg'), window: Number(val('re-window'))};
    else if (kind === 'absence') query = target === 'metric' ? metricQuery() : {q: val('re-q').trim()};
    else if (kind === 'pattern_new') query = {q: val('re-q').trim(), window: Number(val('re-window'))};
    else query = metricQuery();
    const out = {
        name, kind, query,
        condition: {op: val('re-op'), threshold},
        for_seconds: Number(val('re-for')),
        severity: val('re-severity'),
        enabled: document.getElementById('re-enabled').checked,
        notify: document.getElementById('re-notify').checked,
    };
    if (base && base.id !== undefined && base.id !== null) out.id = base.id;
    return out;
}

async function testRule(base) {
    const box = document.getElementById('re-test-result');
    const button = document.getElementById('re-test');
    let rule;
    try { rule = readRuleForm(base); } catch (e) { modalError(e.message); return; }
    modalError('');
    button.disabled = true;
    box.hidden = false;
    box.className = 'rule-test';
    box.textContent = 'Testing…';
    try {
        const r = await api('rules/test', rule);
        box.className = 'rule-test ' + (r.would_fire ? 'fire' : 'ok');
        box.innerHTML = `<div><b>${r.would_fire ? 'Would fire' : 'Would not fire'}</b> · value <span class="mono">${escapeHtml(fmtValue(r.value))}</span> ${escapeHtml(rule.condition.op)} ${escapeHtml(rule.condition.threshold)}</div>
            ${r.detail ? `<div class="muted">${escapeHtml(typeof r.detail === 'string' ? r.detail : JSON.stringify(r.detail))}</div>` : ''}
            ${rule.for_seconds && r.would_fire ? `<div class="muted">It becomes pending first, and fires after ${escapeHtml(fmtDuration(rule.for_seconds))}.</div>` : ''}`;
    } catch (e) {
        box.className = 'rule-test error';
        box.textContent = e.message;
    }
    button.disabled = false;
}

// =====================================================================
// History
// =====================================================================
async function loadAlertHistory() {
    const chart = document.getElementById('chart-alerts');
    const table = document.getElementById('alert-history');
    if (!ready()) { emptyChart(chart, 'The database is not connected'); table.innerHTML = ''; return; }
    const w = rangeWindow();
    let data;
    try {
        data = await api('alerts?' + qs(w));
    } catch (e) {
        emptyChart(chart, e.message);
        table.innerHTML = '';
        return;
    }
    const alerts = (data.alerts || []).slice().sort((a, b) => a.ts - b.ts);
    // Lanes: per rule, firing until the next resolved (or now)
    const byRule = new Map();
    alerts.forEach(a => {
        const key = String(a.rule);
        if (!byRule.has(key)) byRule.set(key, {name: a.rule_name || `rule ${a.rule}`, events: []});
        byRule.get(key).events.push(a);
    });
    const end = Math.min(w.to, Date.now() / 1000);
    const lanes = [...byRule.values()].map(lane => {
        const segments = [];
        let open = null;
        lane.events.forEach((a, i) => {
            if (a.state === 'firing') {
                if (!open) open = a;
            } else if (a.state === 'resolved') {
                const start = open ? open.ts : (i === 0 ? w.from : null);
                if (start !== null) segments.push({from: start, to: a.ts, state: 'firing', title: (open && open.message) || a.message});
                open = null;
            }
        });
        if (open) segments.push({from: open.ts, to: end, state: 'firing', title: open.message});
        return {name: lane.name, segments};
    }).filter(l => l.segments.length);
    laneTimeline(chart, {
        from: w.from, to: w.to, lanes,
        colorOf: s => (s === 'firing' ? cssVar('--danger-color') : cssVar('--warning-color')),
        onZoom: zoomRange, zoomed: rangeZoomed(), onReset: rangeBack,
        title: 'Alert history', filename: 'vigil-alerts',
        csv: () => csvFrom(['time', 'rule', 'state', 'value', 'message'], alerts.map(a => [isoTime(a.ts), a.rule_name, a.state, a.value, a.message])),
        empty: 'Nothing fired in this time range',
    });
    const newest = alerts.slice().reverse();
    table.innerHTML = newest.length ? `<table class="data"><thead><tr><th>Time</th><th>Rule</th><th>State</th><th>Severity</th><th class="n">Value</th><th>Message</th></tr></thead><tbody>${newest.map(a => `<tr>
        <td class="mono" title="${escapeHtml(fmtAgo(a.ts))}">${escapeHtml(fmtDateTime(a.ts))}</td>
        <td><b>${escapeHtml(a.rule_name || '#' + a.rule)}</b></td>
        <td>${statePill(a.state === 'resolved' ? 'resolved' : a.state)}</td>
        <td>${ruleSeverityPill(a.severity)}</td>
        <td class="n">${escapeHtml(fmtValue(a.value))}</td>
        <td class="wrap">${escapeHtml(a.message || '')}</td></tr>`).join('')}</tbody></table>` : '';
}

onTab('alerts', showAlerts);
onRange('alerts', loadAlertHistory);
onLeave('alerts', () => clearInterval(rulesTimer));
