// Network dashboard - Stats: who drops off the network, how often and when; availability, a timeline
// per device, the week as a heatmap, and every coming and going.

const statsState = {
    days: Number(stored('stats_days', '7')),
    heatKey: stored('stats_heat', ''),
    loading: false,
    group: stored('stats_group', 'all'),
};
const STATS_PERIODS = [{key: '1', label: '24 hours'}, {key: '7', label: '7 days'}, {key: '30', label: '30 days'}, {key: '90', label: '90 days'}, {key: '365', label: 'Year'}];
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function strip(intervals, since, until) {
    const span = until - since;
    return `<div class="strip" title="Green: on the network">${(intervals || []).map(([a, b]) => `<i style="left:${((a - since) / span * 100).toFixed(2)}%;width:${Math.max(0.3, (b - a) / span * 100).toFixed(2)}%"></i>`).join('')}</div>`;
}

function heatmap(week) {
    const max = Math.max(0.0001, ...week.flat());
    return `<div class="heat"><span></span>${Array.from({length: 24}, (_, h) => `<span class="h">${h % 3 ? '' : h}</span>`).join('')}
        ${week.map((row, d) => `<span class="d">${WEEKDAYS[d]}</span>${row.map((v, h) => `<i class="c" style="opacity:${(0.08 + 0.92 * v / max).toFixed(2)}" title="${WEEKDAYS[d]} ${h}:00 · ${plainPct(v, 0)} of the time online"></i>`).join('')}`).join('')}</div>`;
}

async function renderStats() {
    const el = document.getElementById('tab-stats');
    if (!app.doc) return;
    const days = statsState.days;
    let data = app.stats[days];
    if (!data || Date.now() / 1000 - data.until > 60) {
        if (!data) el.innerHTML = '<div class="empty-state">Reading the history…</div>';
        if (statsState.loading) return;
        statsState.loading = true;
        try { data = await loadStats(days); } catch (e) { el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`; return; } finally { statsState.loading = false; }
        if (activeTab !== 'stats') return;
    }
    const groupTest = {all: () => true, infra: isInfra, clients: h => !isInfra(h)}[statsState.group] || (() => true);
    const hosts = networkHosts().filter(groupTest).map(h => ({h, s: data.hosts[h.key]})).filter(x => x.s);
    const known = hosts.filter(x => x.s.availability !== null && !x.h.gps_only);
    const unstable = known.filter(x => x.s.drops > 0).sort((a, b) => b.s.drops - a.s.drops).slice(0, 15);
    const gear = known.filter(x => isInfra(x.h)).sort((a, b) => a.s.availability - b.s.availability);
    const always = known.filter(x => x.s.availability >= 0.999).length;
    const totalDrops = known.reduce((a, x) => a + x.s.drops, 0);
    const median = known.length ? known.map(x => x.s.availability).sort((a, b) => a - b)[Math.floor(known.length / 2)] : null;
    if (!statsState.heatKey || !data.hosts[statsState.heatKey]) statsState.heatKey = (unstable[0] || known[0] || {h: {}}).h.key || '';
    const heatHost = hostByKey(statsState.heatKey);
    const heat = data.hosts[statsState.heatKey];
    const longAway = known.filter(x => x.s.longest_down > 0).sort((a, b) => b.s.longest_down - a.s.longest_down).slice(0, 8);
    const hasStrips = days <= 7 && data.intervals;
    el.innerHTML = `
        <div class="filters">
            ${chipsHtml('stats-days', STATS_PERIODS, String(days), 'Period')}
            ${chipsHtml('stats-group', [{key: 'all', label: 'All devices'}, {key: 'infra', label: 'Network gear'}, {key: 'clients', label: 'The rest'}], statsState.group, 'Devices')}
            <span class="range-text">${escapeHtml(fmtDateTime(data.since))} – ${escapeHtml(fmtDateTime(data.until))}</span>
        </div>
        <div class="tiles kpi-tiles">
            ${tile('Drops', num(totalDrops), `${num(totalDrops / Math.max(1, days), 1)} a day`)}
            ${tile('Always on', `${num(always)}<small> of ${num(known.length)}</small>`, 'never off the network')}
            ${tile('Median online', median === null ? '–' : plainPct(median, 0), 'half the devices are on more')}
            ${tile('Unstable', num(known.filter(x => x.s.drops_per_day >= 3).length), '3 or more drops a day', known.some(x => x.s.drops_per_day >= 3) ? 'warn' : '')}
            ${tile('Most drops', unstable[0] ? escapeHtml(clip(hostName(unstable[0].h), 16)) : '–', unstable[0] ? `${num(unstable[0].s.drops)} times` : 'none')}
        </div>
        ${panel('Devices online', '', '<div id="st-online" class="chart"></div>', {cls: ''})}
        <div class="dash-grid">
            ${panel('Drops off the network most', 'times it left; a phone leaving home counts too', unstable.length ? `<div class="table-wrap">${table(
                [{label: 'Device'}, {label: 'Drops', n: true}, {label: 'A day', n: true}, {label: 'Online'}, {label: 'Typical stay', n: true}, ...(hasStrips ? [{label: 'Timeline'}] : [])],
                unstable.map(({h, s}) => `<tr class="${s.drops_per_day >= 3 ? 'failed' : ''}" onclick="statsHeat('${attr(h.key)}')">${td(hostLink(h))}${td(num(s.drops), 'n')}${td(num(s.drops_per_day, 1), 'n')}${td(availBar(s))}${td(escapeHtml(fmtDuration(s.median_session)), 'n')}${hasStrips ? td(strip(data.intervals[h.key], data.since, data.until)) : ''}</tr>`),
                {cls: 'hover compact-rows'})}</div>` : '<div class="all-good"><span class="sdot ok"></span> Nothing dropped off.</div>')}
            <div class="dash-stack">
                ${panel(`When ${heatHost ? escapeHtml(hostName(heatHost)) : ''} is online`, 'by weekday and hour; pick a device in a table', heat ? heatmap(heat.week) : '<div class="empty-state">Pick a device</div>')}
                ${panel('Network gear', 'least available first', gear.length ? `<div class="table-wrap">${table([{label: 'Device'}, {label: 'Online'}, {label: 'Drops', n: true}],
                    gear.slice(0, 10).map(({h, s}) => `<tr onclick="statsHeat('${attr(h.key)}')">${td(hostLink(h))}${td(availBar(s))}${td(num(s.drops), 'n')}</tr>`), {cls: 'hover compact-rows'})}</div>` : '<div class="empty-state">No network gear among the devices; set a kind on your router, switches and access points</div>')}
            </div>
        </div>
        <div class="dash-grid">
            ${panel('Longest away', 'the longest gap between two times on the network', longAway.length ? `<div class="table-wrap">${table([{label: 'Device'}, {label: 'Away for', n: true}],
                longAway.map(({h, s}) => `<tr>${td(hostLink(h))}${td(escapeHtml(fmtDuration(s.longest_down)), 'n')}</tr>`), {cls: 'compact-rows'})}</div>` : '<div class="empty-state">–</div>')}
            ${panel('Comings and goings', `the latest ${Math.min(80, data.events.length)}`, data.events.length ? `<div class="events compact">${data.events.slice(0, 80).map(e => {
                const h = hostByKey(e.key);
                if (!h || !groupTest(h)) return '';
                return `<div class="event"><span class="t" title="${attr(fmtDateTime(e.ts))}">${escapeHtml(days <= 1 ? fmtTime(e.ts) : fmtDay(e.ts) + ' ' + fmtTime(e.ts))}</span><span class="k ${e.up ? 'ok' : ''}">${e.up ? 'joined' : 'left'}</span><span class="x">${hostLink(h)}</span></div>`;
            }).join('')}</div>` : '<div class="empty-state">Nothing yet</div>')}
        </div>
        <p class="panel-sub" style="margin-top:12px">From the app's own record of every change, filled from Home Assistant's recorder at start (${num(app.status.config.history_days)} days back) and kept for ${num(app.status.config.keep_days)} days. Trackers of one device count as one: it is online when any of them says so.</p>`;
    bindChips('stats-days', key => { statsState.days = Number(key); store('stats_days', key); renderStats(); });
    bindChips('stats-group', key => { statsState.group = key; store('stats_group', key); renderStats(); });
    lineChart(document.getElementById('st-online'), {
        x: data.online.x, height: 190, zero: true,
        series: [{name: 'Online', color: 'var(--series-1)', values: data.online.y, area: true}],
        xTicks: timeAxisTicks(data.online.x[0], data.online.x[data.online.x.length - 1], 7),
        formatX: t => fmtDateTime(t), formatY: v => `${num(v)} devices`, formatTick: v => num(v), empty: 'No history yet',
    });
}
function statsHeat(key) { statsState.heatKey = key; store('stats_heat', key); renderStats(); }

onTab('stats', () => renderStats(), () => {
    if (!document.getElementById('modal-root').children.length) renderStats();
});
