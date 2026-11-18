// MQTT dashboard - the Dashboard: the broker now, the last 15 minutes of traffic, what needs a look,
// the busiest topics and what happened lately.

const ov = {series: null, top: [], events: [], loadedAt: 0, loading: false};

async function loadOverview() {
    if (ov.loading) return;
    ov.loading = true;
    try {
        const [series, top, events] = await Promise.all([
            api('series'), api('topics?sort=rate&only=active&limit=12'), api('events'), loadClients(),
            app.discovery && Date.now() - app.discovery.at < 30000 ? null : loadDiscovery(),
        ]);
        ov.series = series;
        ov.top = top.topics;
        ov.events = events.events;
        ov.loadedAt = Date.now();
    } finally {
        ov.loading = false;
    }
}

function attentionItems() {
    const s = app.status;
    const out = [];
    if (!s.ha.connected) out.push({kind: 'warn', text: 'Home Assistant is not connected to this broker', href: '#info'});
    const flapping = app.clients.filter(c => c.drops >= 3 || c.connects >= 5).sort((a, b) => b.connects - a.connects);
    if (flapping.length) out.push({kind: 'warn', text: `Reconnecting often: ${flapping.slice(0, 6).map(c => `${clientLink(c.id)} <span class="muted">${num(c.connects)}×</span>`).join(', ')}`, href: '#clients'});
    const refused = ov.events.filter(e => e.kind === 'refused' && Date.now() / 1000 - e.ts < 86400);
    if (refused.length) out.push({kind: 'bad', text: `${num(refused.length)} refused login${refused.length > 1 ? 's' : ''} in the last 24 hours: ${[...new Set(refused.map(e => e.client))].slice(0, 5).map(clientLink).join(', ')}`, href: '#log'});
    const d = app.discovery;
    if (d) {
        const offline = d.devices.filter(x => x.available === 'offline');
        if (offline.length) out.push({kind: 'warn', text: `Unavailable devices: ${offline.slice(0, 6).map(x => escapeHtml(x.name)).join(', ')}${offline.length > 6 ? ` and ${offline.length - 6} more` : ''}`, href: '#discovery'});
        const stale = d.devices.filter(x => x.stale && x.available !== 'offline');
        if (stale.length) out.push({kind: '', text: `No news for a day: ${stale.slice(0, 6).map(x => escapeHtml(x.name)).join(', ')}${stale.length > 6 ? ` and ${stale.length - 6} more` : ''}`, href: '#discovery'});
        const bad = d.problems.length + d.devices.reduce((a, x) => a + x.duplicates, 0);
        if (bad) out.push({kind: 'bad', text: `${num(bad)} discovery config${bad > 1 ? 's' : ''} Home Assistant can't use`, href: '#discovery'});
    }
    if (s.broker.dropped) out.push({kind: 'warn', text: `The broker dropped ${num(s.broker.dropped)} message${s.broker.dropped > 1 ? 's' : ''} for clients whose queue was full`});
    if (!s.monitor.capture) out.push({kind: 'info', text: 'Payloads are not kept (option capture_payloads): topics show counts, not values'});
    return out;
}

function renderOverview() {
    const el = document.getElementById('tab-overview');
    const s = app.status;
    if (!s) return;
    if (!ov.loadedAt) {
        el.innerHTML = '<div class="empty-state">Reading the broker…</div>';
        loadOverview().then(renderOverview).catch(e => { el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`; });
        return;
    }
    const b = s.broker;
    const items = attentionItems();
    const groups = s.monitor.groups.slice(0, 8);
    const maxGroup = Math.max(0.001, ...groups.map(g => g.rate));
    el.innerHTML = `
        <div class="tiles kpi-tiles">
            ${tile('Clients', `${num(s.clients.connected)}`, `${num(b.clients_max)} at most since the start`)}
            ${tile('Messages in', msgRate(b.received_rate ?? s.monitor.rate), 'published to the broker')}
            ${tile('Messages out', msgRate(b.sent_rate), 'delivered to subscribers')}
            ${tile('Data', `${escapeHtml(fmtByteRate(b.bytes_received_rate))}`, `in · ${escapeHtml(fmtByteRate(b.bytes_sent_rate))} out`)}
            ${tile('Topics', num(s.monitor.topics), `${num(s.monitor.retained)} retained`)}
            ${tile('Subscriptions', num(b.subscriptions ?? s.clients.subscriptions), `uptime ${escapeHtml(fmtUptime(b.uptime))}`)}
        </div>
        <div class="dash-grid wide-left">
            <div class="dash-stack">
                ${panel('Last 15 minutes', 'messages per second the dashboard saw', '<div id="ov-rate" class="chart"></div>')}
                ${panel('Busiest topics now', '<a href="#topics" class="plain-link">all topics →</a>', topTopicsTable())}
            </div>
            <div class="dash-stack">
                ${panel('Needs a look', '', items.length ? `<div class="checks-list attention">${items.map(i => `<div class="check"><span class="sdot ${i.kind === 'bad' ? 'bad' : i.kind === 'warn' ? 'warn' : i.kind === 'info' ? 'ok' : ''}"></span><span>${i.text}${i.href ? ` <a href="${i.href}" class="plain-link muted">→</a>` : ''}</span></div>`).join('')}</div>`
                    : '<div class="all-good"><span class="sdot ok"></span> Nothing needs a look.</div>')}
                ${panel('Traffic per group', 'first level of the topic', groups.length ? `<div class="group-rows">${groups.map(g => `
                    <div class="group-row"><a class="plain-link mono" href="#topics/${encodeURIComponent(g.group)}">${escapeHtml(g.group)}</a>
                        <div class="bar"><i style="width:${(g.rate / maxGroup * 100).toFixed(1)}%"></i></div>
                        <span class="n">${fmtRateNum(g.rate)}<small> /s</small></span><span class="n muted">${num(g.topics)} topics</span></div>`).join('')}</div>` : '<div class="empty-state">No messages yet</div>')}
                ${panel('Lately', '<a href="#clients" class="plain-link">clients →</a>', eventsList(ov.events.slice(0, 12)))}
            </div>
        </div>`;
    drawRate();
}

function topTopicsTable() {
    if (!ov.top.length) return '<div class="empty-state">No topic had a message in the last minute</div>';
    return table(
        [{label: 'Topic'}, {label: 'Rate', n: true}, {label: 'Last value'}],
        ov.top.map(t => `<tr>${td(topicLink(t.topic), 'wrap-cell')}${td(`${fmtRateNum(t.rate)}<small> /s</small>`, 'n')}${td(payloadPreview(t.preview, t.binary), 'payload-cell')}</tr>`),
        {cls: 'compact'});
}

const EVENT_LABELS = {connect: 'connected', disconnect: 'left', refused: 'refused', takeover: 'taken over', broker: 'broker'};
function eventsList(events) {
    if (!events.length) return '<div class="empty-state">Nothing yet</div>';
    const kindClass = e => e.kind === 'connect' ? 'ok' : e.kind === 'refused' ? 'bad' : e.kind === 'disconnect' && !/client|^disconnected$/.test(e.detail) ? 'warn' : '';
    return `<div class="events compact">${events.map(e => `<div class="event"><span class="t">${escapeHtml(fmtTime(e.ts))}</span>
        <span class="k ${kindClass(e)}">${escapeHtml(EVENT_LABELS[e.kind] || e.kind)}</span>
        <span class="x">${e.client ? clientLink(e.client) + ' ' : ''}<span class="muted">${escapeHtml(e.detail)}</span></span></div>`).join('')}</div>`;
}

function drawRate() {
    const el = document.getElementById('ov-rate');
    const sr = ov.series;
    if (!el || !sr) return;
    lineChart(el, {
        x: sr.x, height: 170, zero: true,
        series: [{name: 'Messages/s', color: color('series-1'), values: sr.messages}],
        xTicks: timeAxisTicks(sr.x[0], sr.x[sr.x.length - 1], 5),
        formatX: t => fmtTime(t), formatY: v => `${fmtRateNum(v)} msg/s`, formatTick: v => fmtRateNum(v),
        empty: 'No messages in the last 15 minutes',
    });
}

let ovTimer = 0;
onTab('overview', () => { renderOverview(); }, () => {
    if (Date.now() - ovTimer < 5000) return;
    ovTimer = Date.now();
    loadOverview().then(() => { if (activeTab === 'overview') renderOverview(); }).catch(() => {});
});
