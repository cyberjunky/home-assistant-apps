// Vigil dashboard - "What changed?": the current window against the one of the same length just
// before it - hosts and severities that got louder, patterns that grew or appeared, metrics whose
// mean moved most. A side panel over the page.

const CHANGE_KINDS = {host: 'Host', severity: 'Severity', pattern: 'Pattern', metric: 'Metric'};
let changesOpen = false;

async function openChanges() {
    const panel = document.getElementById('changes-panel');
    const body = document.getElementById('changes-body');
    const w = rangeWindow();
    changesOpen = true;
    panel.hidden = false;
    document.getElementById('changes-window').textContent = `${rangeText()} against the ${fmtDuration(w.to - w.from)} before`;
    if (!ready()) { body.innerHTML = emptyHtml('The database is not connected'); return; }
    body.innerHTML = loadingHtml('Comparing…');
    let data;
    try {
        data = await api('changes?' + qs(w));
    } catch (e) {
        body.innerHTML = errorHtml(e.message);
        return;
    }
    if (!changesOpen) return;
    const items = (data.items || []).slice().sort((a, b) => (b.score || 0) - (a.score || 0));
    if (!items.length) { body.innerHTML = emptyHtml('Nothing stands out: this window looks like the one before it'); return; }
    const max = Math.max(1e-9, ...items.map(i => Math.abs(i.score || 0)));
    body.innerHTML = items.map((it, i) => {
        const kind = CHANGE_KINDS[it.kind] ? it.kind : 'other';
        const link = changeLink(it.link);
        return `<div class="change-item${link ? ' link' : ''}" data-i="${i}">
            <div class="change-head"><span class="badge change-${kind}">${escapeHtml(CHANGE_KINDS[it.kind] || it.kind || '')}</span>
                <b>${escapeHtml(it.title)}</b><span class="spacer"></span><span class="mono small">${escapeHtml(fmtValue(it.score))}</span></div>
            <div class="change-detail">${escapeHtml(it.detail || '')}</div>
            <div class="bar-track"><i style="width:${(Math.abs(it.score || 0) / max * 100).toFixed(1)}%"></i></div>
        </div>`;
    }).join('');
    body.onclick = (e) => {
        const row = e.target.closest('[data-i]');
        if (!row) return;
        const link = changeLink(items[Number(row.dataset.i)].link);
        if (link) followChangeLink(link);
    };
}

// A link is a place in this page: "#logs/<q>" style, or {tab, q} / {tab, query}. Never a URL.
function changeLink(link) {
    if (!link) return null;
    if (typeof link === 'string') {
        const m = link.match(/#?(\w+)(?:\/(.*))?$/);
        if (!m || !TABS.includes(m[1])) return null;
        return {tab: m[1], arg: m[2] ? decodeURIComponent(m[2].split('/')[0]) : ''};
    }
    if (typeof link === 'object' && TABS.includes(link.tab)) {
        if (link.tab === 'metrics' && link.query) return {tab: 'metrics', query: link.query};
        return {tab: link.tab, arg: String(link.q || link.arg || '')};
    }
    return null;
}

function followChangeLink(link) {
    closeChanges();
    if (link.tab === 'logs') return openLogs(link.arg);
    if (link.tab === 'metrics' && link.query) return openMetrics(link.query);
    switchTab(link.tab, link.arg);
}

function closeChanges() {
    changesOpen = false;
    document.getElementById('changes-panel').hidden = true;
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && changesOpen && !document.getElementById('app-modal')) closeChanges();
});
