// MQTT dashboard - Stats: the broker over time from the app's own history (a sample every minute):
// messages and data in and out, clients, subscriptions and retained messages, (re)connects and refused
// logins, the traffic per topic group, and the clients that keep reconnecting.

const st = {days: Number(stored('stats_days', '1')), loading: false};
const STATS_PERIODS = [{key: '1', label: '24 hours'}, {key: '7', label: '7 days'}, {key: '30', label: '30 days'}, {key: '90', label: '90 days'}, {key: '365', label: 'Year'}];

async function loadStats(days) {
    const data = await api('stats?days=' + days);
    data.at = Date.now();
    app.stats[days] = data;
    return data;
}

function bucketLabel(seconds) {
    return seconds >= 86400 ? 'per day' : seconds >= 3600 ? `per ${seconds / 3600} hour${seconds > 3600 ? 's' : ''}` : `per ${seconds / 60} minutes`;
}

async function renderStats() {
    const el = document.getElementById('tab-stats');
    let data = app.stats[st.days];
    if (!data || Date.now() - data.at > 60000) {
        if (!data) el.innerHTML = '<div class="empty-state">Reading the history…</div>';
        if (st.loading) return;
        st.loading = true;
        try { data = await loadStats(st.days); } catch (e) { el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`; return; } finally { st.loading = false; }
        if (activeTab !== 'stats') return;
    }
    const t = data.totals;
    const groupsTotal = data.groups.reduce((a, g) => a + g.messages, 0) || 1;
    el.innerHTML = `
        <div class="page-head">
            ${chipsHtml('st-days', STATS_PERIODS, String(st.days), 'Period')}
            <span class="muted">${data.first ? `history since ${escapeHtml(fmtDateTime(data.first))}` : 'the history starts now: a sample every minute'}</span>
        </div>
        <div class="tiles kpi-tiles">
            ${tile('Messages in', num(t.received), 'published to the broker')}
            ${tile('Messages out', num(t.sent), 'delivered to subscribers')}
            ${tile('Data', bytesHtml(t.bytes_received), `in · ${escapeHtml(fmtBytes(t.bytes_sent))} out`)}
            ${tile('Most clients', num(t.clients_max), 'at once')}
            ${tile('Connects', num(t.connects), `${num(t.disconnects)} disconnects`)}
            ${tile('Refused logins', num(t.refused), 'wrong user or password', t.refused ? 'warn' : '')}
        </div>
        <div class="stats-grid">
            ${panel('Messages', 'per second, in and out', '<div id="st-msgs" class="chart"></div>')}
            ${panel('Data', 'per second, in and out', '<div id="st-bytes" class="chart"></div>')}
            ${panel('Clients', 'connected, average and most', '<div id="st-clients" class="chart"></div>')}
            ${panel('Connects', bucketLabel(data.bucket), '<div id="st-connects" class="chart"></div>')}
            ${panel('Topic groups', 'messages per second, the busiest', '<div id="st-groups" class="chart"></div>')}
            ${panel('Subscriptions and retained', '', '<div id="st-subs" class="chart"></div>')}
        </div>
        <div class="dash-grid">
            ${panel('Busiest groups', 'first level of the topic', data.groups.length ? table([{label: 'Group'}, {label: 'Messages', n: true}, {label: 'Share', n: true}, {label: 'Data', n: true}],
                data.groups.map(g => `<tr>${td(`<a class="plain-link mono" href="#topics/${encodeURIComponent(g.group)}">${escapeHtml(g.group)}</a>`)}${td(num(g.messages), 'n')}${td(plainPct(g.messages / groupsTotal), 'n')}${td(escapeHtml(fmtBytes(g.bytes)), 'n')}</tr>`), {cls: 'compact'})
                : '<div class="empty-state">No messages in this period</div>')}
            ${panel('Reconnecting most', 'connects in this period', data.flapping.length ? table([{label: 'Client'}, {label: 'Connects', n: true}],
                data.flapping.map(f => `<tr>${td(clientLink(f.client))}${td(num(f.connects), 'n')}</tr>`), {cls: 'compact'})
                : '<div class="all-good"><span class="sdot ok"></span> No client connected more than once.</div>')}
        </div>`;
    bindChips('st-days', key => { st.days = Number(key); store('stats_days', key); renderStats(); });
    drawStats(data);
}

function drawStats(d) {
    const xTicks = timeAxisTicks(d.x[0], d.x[d.x.length - 1], 6);
    const fmtX = ts => d.bucket >= 86400 ? fmtDay(ts) : fmtDateTime(ts);
    const line = (id, series, formatY, extra) => lineChart(document.getElementById(id), Object.assign({
        x: d.x, height: 180, zero: true, series, xTicks, formatX: fmtX, formatY, formatTick: v => formatY(v).replace(/ .*$/, ''),
        empty: 'No history in this period yet',
    }, extra || {}));
    line('st-msgs', [
        {name: 'In', color: color('series-1'), values: d.received},
        {name: 'Out', color: color('series-2'), values: d.sent},
    ], v => `${fmtRateNum(v)} msg/s`);
    // In KiB/s throughout, so the axis has one unit
    const kib = vs => vs.map(v => missing(v) ? v : v / 1024);
    line('st-bytes', [
        {name: 'In', color: color('series-1'), values: kib(d.bytes_received)},
        {name: 'Out', color: color('series-2'), values: kib(d.bytes_sent)},
    ], v => `${num(v, v < 10 ? 2 : v < 100 ? 1 : 0)} KiB/s`);
    line('st-clients', [
        {name: 'Average', color: color('series-1'), values: d.clients},
        {name: 'Most', color: color('series-3'), values: d.clients_max},
    ], v => num(v, v % 1 ? 1 : 0));
    line('st-subs', [
        {name: 'Subscriptions', color: color('series-1'), values: d.subscriptions},
        {name: 'Retained', color: color('series-4'), values: d.retained},
    ], v => num(v));
    const gs = d.group_series;
    const names = Object.keys(gs.groups);
    lineChart(document.getElementById('st-groups'), {
        x: gs.x, height: 180, zero: true,
        series: names.map((g, i) => ({name: g, color: color(SERIES[i % SERIES.length]), values: gs.groups[g]})),
        xTicks: timeAxisTicks(gs.x[0], gs.x[gs.x.length - 1], 6), formatX: fmtX,
        formatY: v => `${fmtRateNum(v)} msg/s`, formatTick: v => fmtRateNum(v),
        empty: 'No messages in this period yet',
    });
    const legend = document.createElement('div');
    legend.className = 'legend';
    legend.innerHTML = legendHtml(names.map((g, i) => ({name: g, color: color(SERIES[i % SERIES.length])})));
    document.getElementById('st-groups').after(legend);
    barChart(document.getElementById('st-connects'), {
        data: d.x.map((x, i) => ({label: d.bucket >= 86400 ? fmtDay(x) : d.days <= 1 ? fmtTime(x) : fmtDay(x), tipTitle: fmtX(x), values: [d.connects[i] || 0, d.refused[i] || 0]})),
        series: [{name: 'Connects', color: color('series-1')}, {name: 'Refused', color: color('series-5')}],
        height: 180, format: v => num(v), formatTick: v => num(v), labelWidth: 80,
        empty: 'No connects in this period',
    });
}

onTab('stats', () => renderStats(), () => {
    const data = app.stats[st.days];
    if (data && Date.now() - data.at > 60000 && !document.getElementById('modal-root').innerHTML) renderStats();
});
