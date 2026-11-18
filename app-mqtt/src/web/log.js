// MQTT dashboard - Log: the broker's own log since it started (without the subscribe lines, which
// are on each client's page), with search and the problems only.

const lg = {lines: [], seq: 0, q: '', level: stored('log_level', ''), follow: true, loading: false};
const LOG_LEVELS = [{key: '', label: 'All'}, {key: 'warning', label: 'Problems'}, {key: 'error', label: 'Errors'}];

async function loadLog(reset) {
    if (lg.loading) return;
    lg.loading = true;
    try {
        const params = new URLSearchParams({since: reset ? 0 : lg.seq, q: lg.q, level: lg.level});
        const r = await api('log?' + params);
        lg.lines = reset ? r.lines : [...lg.lines, ...r.lines].slice(-3000);
        lg.seq = r.seq;
    } finally {
        lg.loading = false;
    }
}

function renderLog() {
    const el = document.getElementById('tab-log');
    if (!el.querySelector('#lg-body')) {
        el.innerHTML = `
            <div class="page-head">
                <div class="filters">
                    ${chipsHtml('lg-level', LOG_LEVELS, lg.level, 'Show')}
                    <input id="lg-q" class="input search" type="search" placeholder="Search the log" autocomplete="off" spellcheck="false">
                </div>
                <label class="check-row"><input type="checkbox" id="lg-follow" checked> Follow</label>
            </div>
            ${panel('', '', '<div id="lg-body" class="logs log-view"></div>')}`;
        bindChips('lg-level', key => { lg.level = key; store('log_level', key); loadLog(true).then(drawLog).catch(e => toast(e.message)); });
        let wait = null;
        document.getElementById('lg-q').addEventListener('input', (e) => {
            clearTimeout(wait);
            wait = setTimeout(() => { lg.q = e.target.value.trim(); loadLog(true).then(drawLog).catch(err => toast(err.message)); }, 250);
        });
        document.getElementById('lg-follow').addEventListener('change', (e) => { lg.follow = e.target.checked; if (lg.follow) drawLog(); });
    }
    drawLog();
}

function drawLog() {
    const body = document.getElementById('lg-body');
    if (!body) return;
    body.innerHTML = lg.lines.length ? lg.lines.map(l => `<div class="log-line ${l.level}"><span class="t">${escapeHtml(fmtDay(l.ts))} ${escapeHtml(new Date(l.ts * 1000).toLocaleTimeString())}</span><span class="x">${escapeHtml(l.text)}</span></div>`).join('')
        : `<div class="empty-state">${lg.q || lg.level ? 'Nothing matches' : 'Nothing logged yet'}</div>`;
    if (lg.follow) body.scrollTop = body.scrollHeight;
}

onTab('log', () => {
    renderLog();
    loadLog(true).then(() => { if (activeTab === 'log') drawLog(); }).catch(e => toast(e.message));
}, () => {
    if (!app.status || app.status.log_seq === lg.seq) return;
    loadLog(false).then(() => { if (activeTab === 'log' && lg.follow) drawLog(); }).catch(() => {});
});
