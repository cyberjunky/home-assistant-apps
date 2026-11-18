// MQTT dashboard - Topics: every topic the broker carried since the dashboard started (and every
// retained one), as a tree to open level by level, or as a list from a search. #topics/<topic> opens a
// topic (its value, chart, subscribers, publish and clear) or a branch (its topics, clear retained).

const tp = {
    open: new Set(JSON.parse(stored('topics_open', '[]'))),
    children: {},
    q: '',
    sort: stored('topics_sort', 'topic'),
    only: stored('topics_only', ''),
    list: null,
    detail: null,
    loading: false,
    timer: 0,
};
const TOPIC_SORTS = [{key: 'topic', label: 'A–Z'}, {key: 'rate', label: 'Busiest'}, {key: 'last', label: 'Latest'}, {key: 'bytes', label: 'Most data'}, {key: 'size', label: 'Largest'}];
const TOPIC_ONLY = [{key: '', label: 'All'}, {key: 'retained', label: 'Retained'}, {key: 'active', label: 'Active now'}];

function saveOpen() { store('topics_open', JSON.stringify([...tp.open].slice(0, 200))); }

async function loadTree() {
    const prefixes = ['', ...[...tp.open].filter(p => p && (p.split('/').length === 1 || tp.open.has(p.split('/').slice(0, -1).join('/'))))];
    const results = await Promise.all(prefixes.map(p => api('tree?prefix=' + encodeURIComponent(p)).catch(() => null)));
    results.forEach((r, i) => { if (r) tp.children[prefixes[i]] = r.children; });
}
async function loadList() {
    const q = tp.q.trim();
    const params = new URLSearchParams({sort: tp.sort, only: tp.only, limit: '500'});
    if (isPattern(q)) params.set('filter', q); else params.set('q', q);
    tp.list = await api('topics?' + params);
}
async function loadDetail(name) {
    try {
        tp.detail = await api('topic?name=' + encodeURIComponent(name));
    } catch (e) {
        tp.detail = {missing: name};
    }
}

function treeHtml(prefix, depth) {
    const children = tp.children[prefix];
    if (!children) return depth ? '<div class="tree-loading muted">…</div>' : '';
    return children.map(c => {
        const open = tp.open.has(c.path);
        const selected = activeSub === c.path;
        const leaf = c.leaf;
        return `<div class="tree-node">
            <div class="tree-row${selected ? ' selected' : ''}" data-path="${attr(c.path)}" style="padding-left:${depth * 14 + 4}px">
                <span class="caret">${c.has_children ? (open ? '▾' : '▸') : ''}</span>
                <span class="tree-name">${escapeHtml(c.name || '(empty)')}</span>
                ${leaf ? `<span class="tree-value">${leaf.binary ? '<span class="muted">binary</span>' : escapeHtml(leaf.preview.slice(0, 60))}</span>` : ''}
                <span class="tree-meta">${c.has_children ? `<span class="muted">${num(c.topics)}</span>` : ''}${c.retained ? '<span class="ret" title="Retained">R</span>' : ''}${c.rate ? `<span class="rate">${fmtRateNum(c.rate)}/s</span>` : ''}</span>
            </div>
            ${open && c.has_children ? `<div class="tree-children">${treeHtml(c.path, depth + 1)}</div>` : ''}
        </div>`;
    }).join('');
}

function listHtml() {
    if (!tp.list) return '<div class="empty-state">Searching…</div>';
    const rows = tp.list.topics;
    return `<div class="muted list-count">${num(tp.list.total)} topic${tp.list.total === 1 ? '' : 's'}${tp.list.total > rows.length ? `, the first ${num(rows.length)}` : ''}</div>
        <div class="topic-list">${rows.map(t => `<div class="tree-row${activeSub === t.topic ? ' selected' : ''}" data-topic="${attr(t.topic)}">
            <span class="tree-name">${topicHtml(t.topic)}</span>
            <span class="tree-value">${t.binary ? '<span class="muted">binary</span>' : escapeHtml(t.preview.slice(0, 60))}</span>
            <span class="tree-meta">${t.retained ? '<span class="ret" title="Retained">R</span>' : ''}${t.rate ? `<span class="rate">${fmtRateNum(t.rate)}/s</span>` : `<span class="muted">${escapeHtml(fmtAgo(t.last))}</span>`}</span>
        </div>`).join('') || '<div class="empty-state">No topic matches</div>'}</div>`;
}

function renderTopics() {
    const el = document.getElementById('tab-topics');
    if (!el.querySelector('#tp-left')) {
        el.innerHTML = `
            <div class="topics-layout">
                <div class="panel topics-left"><div class="panel-body">
                    <div class="filters">
                        <input id="tp-q" class="input search" type="search" placeholder="Search, or a filter like zigbee2mqtt/+/availability" autocomplete="off" spellcheck="false">
                    </div>
                    <div class="filters" id="tp-list-filters"></div>
                    <div id="tp-left" class="tree"></div>
                </div></div>
                <div id="tp-right" class="topics-right"></div>
            </div>`;
        const input = document.getElementById('tp-q');
        input.value = tp.q;
        let wait = null;
        input.addEventListener('input', () => {
            tp.q = input.value;
            clearTimeout(wait);
            wait = setTimeout(() => { tp.list = null; refreshTopics(); }, 250);
        });
        document.getElementById('tp-left').addEventListener('click', onTreeClick);
    }
    const filters = document.getElementById('tp-list-filters');
    if (tp.q.trim()) {
        filters.innerHTML = chipsHtml('tp-sort', TOPIC_SORTS, tp.sort, 'Sort') + chipsHtml('tp-only', TOPIC_ONLY, tp.only, 'Show');
        bindChips('tp-sort', k => { tp.sort = k; store('topics_sort', k); refreshTopics(); });
        bindChips('tp-only', k => { tp.only = k; store('topics_only', k); refreshTopics(); });
    } else {
        filters.innerHTML = '';
    }
    const left = document.getElementById('tp-left');
    const scroll = left.scrollTop;
    left.innerHTML = tp.q.trim() ? listHtml() : (treeHtml('', 0) || '<div class="empty-state">No topics yet</div>');
    left.scrollTop = scroll;
    renderTopicDetail();
}

function onTreeClick(e) {
    const row = e.target.closest('[data-path], [data-topic]');
    if (!row) return;
    if (row.dataset.topic) { switchTab('topics', row.dataset.topic); return; }
    const path = row.dataset.path;
    const caretClick = e.target.classList.contains('caret');
    const node = (tp.children[path.split('/').slice(0, -1).join('/')] || []).find(c => c.path === path);
    if (node && node.has_children && (caretClick || activeSub === path || !node.leaf)) {
        if (tp.open.has(path)) tp.open.delete(path); else tp.open.add(path);
        saveOpen();
    }
    if (!caretClick) switchTab('topics', path); else refreshTopics();
}

function branchHtml(path) {
    const node = (tp.children[path.split('/').slice(0, -1).join('/')] || []).find(c => c.path === path);
    if (!node) return `<div class="empty-state">No topic <span class="mono">${escapeHtml(path)}</span> yet</div>`;
    return `
        <div class="page-head"><h2>${topicHtml(path)}<i class="muted">/#</i></h2></div>
        <div class="tiles">
            ${tile('Topics', num(node.topics), 'under this branch')}
            ${tile('Messages', num(node.count), 'since the dashboard started')}
            ${tile('Rate', msgRate(node.rate), 'now')}
            ${tile('Retained', num(node.retained), 'kept by the broker')}
        </div>
        ${panel('Actions', '', `<div class="detail-actions">
            <button class="btn" data-act="watch">Watch ${escapeHtml(path)}/# live</button>
            ${node.retained ? `<button class="btn btn-danger" data-act="clear-branch">Clear ${num(node.retained)} retained…</button>` : ''}
        </div>`)}`;
}

function topicDetailHtml(t) {
    const numeric = t.values.map(([ts, v]) => [ts, Number(v)]).filter(([, v]) => v !== null && !Number.isNaN(v) && isFinite(v));
    const isNumeric = t.values.length > 1 && numeric.length === t.values.length;
    return `
        <div class="page-head"><h2>${topicHtml(t.topic)}</h2>${t.retained ? badge('retained', 'ok', 'The broker keeps this value and gives it to every new subscriber') : ''}</div>
        <div class="tiles">
            ${tile('Messages', num(t.count), `since ${escapeHtml(fmtDateTime(t.first))}`)}
            ${tile('Rate', msgRate(t.rate), 'now')}
            ${tile('Last', escapeHtml(fmtAgo(t.last)), escapeHtml(fmtDateTime(t.last)))}
            ${tile('Size', bytesHtml(t.size), `QoS ${t.qos} · ${escapeHtml(fmtBytes(t.bytes))} in all`)}
        </div>
        ${panel('Value', `<button class="btn btn-small" data-act="copy">Copy</button>`, t.payload === undefined
            ? '<div class="empty-state">Payloads are not kept (option capture_payloads)</div>'
            : `<pre class="payload${t.binary ? ' binary' : ''}">${escapeHtml(t.binary ? t.payload : prettyPayload(t.payload)) || '<span class="muted">(empty)</span>'}</pre>${t.truncated ? '<div class="note">Only the start is kept.</div>' : ''}`)}
        ${isNumeric ? panel('Last values', `${num(numeric.length)} values`, '<div id="tp-chart" class="chart"></div>') : ''}
        ${!isNumeric && t.values.length > 1 ? panel('Last values', '', table([{label: 'Time'}, {label: 'Value'}],
            t.values.slice().reverse().slice(0, 20).map(([ts, v]) => `<tr>${td(escapeHtml(fmtDateTime(ts)), 'mono')}${td(payloadPreview(v), 'payload-cell')}</tr>`), {cls: 'compact'})) : ''}
        <div class="dash-grid">
            ${panel('Subscribed by', `${num(t.subscribers.length)}`, t.subscribers.length ? `<div class="chip-list">${t.subscribers.map(clientLink).join(' ')}</div>` : '<div class="empty-state">No connected client subscribes to it</div>')}
            ${panel('Actions', '', `<div class="detail-actions">
                <button class="btn" data-act="publish">Publish…</button>
                <button class="btn" data-act="watch">Watch live</button>
                ${t.retained ? '<button class="btn btn-danger" data-act="clear">Clear retained…</button>' : ''}
            </div>`)}
        </div>`;
}

function renderTopicDetail() {
    const el = document.getElementById('tp-right');
    if (!el) return;
    const sub = activeSub;
    if (!sub) {
        el.innerHTML = panel('', '', `<div class="empty-state">Pick a topic on the left${app.status ? `: ${num(app.status.monitor.topics)} topics, ${num(app.status.monitor.retained)} retained` : ''}</div>`);
        return;
    }
    const t = tp.detail && (tp.detail.topic === sub || tp.detail.missing === sub) ? tp.detail : null;
    if (!t) { el.innerHTML = '<div class="empty-state">…</div>'; return; }
    el.innerHTML = t.missing ? branchHtml(sub) : topicDetailHtml(t);
    el.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () => topicAction(b.dataset.act, t.missing ? null : t, sub)));
    const chart = document.getElementById('tp-chart');
    if (chart) {
        const pts = t.values.map(([ts, v]) => [ts, Number(v)]);
        lineChart(chart, {
            x: pts.map(p => p[0]), height: 160,
            series: [{name: 'Value', color: color('series-1'), values: pts.map(p => p[1])}],
            xTicks: timeAxisTicks(pts[0][0], pts[pts.length - 1][0], 4),
            formatX: ts => fmtDateTime(ts), formatY: v => num(v, Math.abs(v) < 10 && v % 1 ? 2 : Math.abs(v) < 100 && v % 1 ? 1 : 0), formatTick: v => num(v, Math.abs(v) < 10 && v % 1 ? 1 : 0),
        });
    }
}

async function topicAction(act, t, path) {
    if (act === 'copy') {
        try { await navigator.clipboard.writeText(t.payload || ''); toast('Copied', 'success'); } catch (e) { toast('The browser did not allow copying'); }
    } else if (act === 'watch') {
        watchTraffic(t ? t.topic : path + '/#');
    } else if (act === 'publish') {
        publishDialog(t.topic, t.binary ? '' : (t.payload || ''), t.retained, t.qos);
    } else if (act === 'clear') {
        const ok = await confirmDialog({title: 'Clear the retained message?', danger: true, confirmLabel: 'Clear',
            message: `<p>The broker stops giving ${topicHtml(t.topic)} to new subscribers.${t.topic.startsWith('homeassistant/') && t.topic.endsWith('/config') ? ' <b>This is a discovery config: Home Assistant removes the entity.</b>' : ''}</p>`});
        if (!ok) return;
        try { await apiPost('clear', {topics: [t.topic]}); toast('Cleared', 'success'); refreshTopics(); } catch (e) { toast(e.message); }
    } else if (act === 'clear-branch') {
        clearRetainedDialog(path + '/#');
    }
}

async function clearRetainedDialog(filter) {
    let preview;
    try { preview = await apiPost('clear', {filter, dry_run: true}); } catch (e) { toast(e.message); return; }
    if (!preview.count) { toast('Nothing retained there', 'success'); return; }
    const discovery = preview.topics.filter(t => t.startsWith('homeassistant/') && t.endsWith('/config')).length;
    const ok = await confirmDialog({
        title: `Clear ${num(preview.count)} retained message${preview.count > 1 ? 's' : ''}?`, danger: true, wide: true,
        confirmLabel: 'Clear', typeToConfirm: preview.count > 5 ? 'clear' : '',
        message: `<p>Every retained message matching <span class="mono">${escapeHtml(filter)}</span>. This can't be undone; a device that publishes them again brings them back.</p>
            ${discovery ? `<p class="warn-text">${num(discovery)} of them are discovery configs: Home Assistant removes those entities.</p>` : ''}
            <div class="clear-list">${preview.topics.map(t => `<div class="mono">${escapeHtml(t)}</div>`).join('')}${preview.count > preview.topics.length ? `<div class="muted">and ${num(preview.count - preview.topics.length)} more</div>` : ''}</div>`,
    });
    if (!ok) return;
    try {
        const r = await apiPost('clear', {filter});
        toast(`Cleared ${num(r.count)}`, 'success');
        app.discovery = null;
        refreshTopics();
    } catch (e) { toast(e.message); }
}

async function publishDialog(topic, payload, retain, qos) {
    const values = await formDialog({
        title: 'Publish a message', confirmLabel: 'Publish', wide: true,
        body: `<div class="form-field"><label>Topic</label><input class="input mono" name="topic" value="${attr(topic || '')}" spellcheck="false"></div>
            <div class="form-field"><label>Payload</label><textarea class="input mono payload-input" name="payload" rows="8" spellcheck="false">${escapeHtml(payload || '')}</textarea></div>
            <div class="form-grid">
                <div class="form-field"><label>QoS</label><select class="select" name="qos">${[0, 1, 2].map(q => `<option${q === (qos || 0) ? ' selected' : ''}>${q}</option>`).join('')}</select></div>
                <label class="check-row"><input type="checkbox" name="retain"${retain ? ' checked' : ''}> Retain: the broker keeps it for new subscribers</label>
            </div>`,
        validate: v => !v.topic ? 'Give a topic' : isPattern(v.topic) ? 'A topic to publish to has no + or #' : v.topic.startsWith('$') ? 'Topics starting with $ belong to the broker' : '',
    });
    if (!values) return;
    try {
        await apiPost('publish', {topic: values.topic, payload: values.payload, qos: Number(values.qos), retain: values.retain});
        toast('Published', 'success');
        setTimeout(refreshTopics, 600);
    } catch (e) { toast(e.message); }
}

async function refreshTopics() {
    if (tp.loading) return;
    tp.loading = true;
    try {
        const sub = activeSub;
        await Promise.all([tp.q.trim() ? loadList() : loadTree(), sub ? loadDetail(sub) : null]);
        tp.timer = Date.now();
    } catch (e) {
        toast(e.message);
    } finally {
        tp.loading = false;
    }
    if (activeTab === 'topics') renderTopics();
}

onTab('topics', (sub) => {
    if (sub) {
        // Open the branches down to it
        const parts = sub.split('/');
        for (let i = 1; i < parts.length; i++) tp.open.add(parts.slice(0, i).join('/'));
        if (!tp.children[sub] && !(tp.detail && tp.detail.topic === sub)) tp.open.add(sub);
        saveOpen();
    }
    renderTopics();
    refreshTopics();
}, () => {
    // Not while a dialog is open or the user selects text in the value
    if (Date.now() - tp.timer < 5000 || document.getElementById('modal-root').innerHTML || String(window.getSelection())) return;
    refreshTopics();
});
