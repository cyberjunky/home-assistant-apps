// Network dashboard - the internet connection on top of the Dashboard: what the router says over
// UPnP or TR-064 (its state, uptime, addresses, link) and the traffic, live and over time.

const internet = {
    data: null,
    period: stored('internet_period', '24'),
    loadedAt: 0,
    loading: null,
};
const INTERNET_PERIODS = [
    {key: 'live', label: 'Live', title: 'The last 15 minutes, every 10 seconds'},
    {key: '24', label: '24 h'},
    {key: '168', label: '7 days'},
    {key: '720', label: '30 days'},
];
const DOWN_COLOR = 'var(--series-1)';
const UP_COLOR = 'var(--series-2)';
// Bits per second, as a link speed is told
function fmtBits(bitsPerSecond) {
    return missing(bitsPerSecond) ? '–' : fmtRate(bitsPerSecond / 8).replace(/\.0 /, ' ');
}

// The axis of a chart: short, the unit is under the chart (48M for 48 Mbit/s, 37G for 37 GiB)
function compact(value, steps) {
    for (const [size, unit] of steps) {
        if (Math.abs(value) >= size) {
            const v = value / size;
            return `${num(v, v < 10 && v % 1 ? 1 : 0)}${unit}`;
        }
    }
    return num(value);
}
// An axis in one unit, picked by the largest value: plain numbers on the axis, the unit above it
function axisUnit(max, steps) {
    const [size, unit] = steps.find(([s]) => max >= s) || steps[steps.length - 1];
    return {unit, tick: v => { const x = v / size; return num(x, x && Math.abs(x) < 10 && x % 1 ? 1 : 0); }};
}
// Bytes per second on an axis in bits per second (kbit/s, Mbit/s or Gbit/s)
function rateAxis(maxBytesPerSecond) {
    const a = axisUnit(maxBytesPerSecond * 8, [[1e9, 'Gbit/s'], [1e6, 'Mbit/s'], [1e3, 'kbit/s']]);
    return {unit: a.unit, tick: v => a.tick(v * 8)};
}
const bytesAxis = maxBytes => axisUnit(maxBytes, [[2 ** 40, 'TiB'], [2 ** 30, 'GiB'], [2 ** 20, 'MiB'], [2 ** 10, 'KiB']]);

async function loadInternet() {
    if (internet.loading) return internet.loading;
    const hours = internet.period === 'live' ? 24 : Number(internet.period);
    internet.loading = api(`router?hours=${hours}`).then(data => {
        internet.data = data;
        internet.loadedAt = Date.now();
        return data;
    }).finally(() => { internet.loading = null; });
    return internet.loading;
}

// A reading from the live stream: the state now, and a point for the live chart
function internetLive(state) {
    const d = internet.data;
    if (!d) return;
    const wasFound = d.found;
    Object.assign(d, state);
    if (state.now && state.now.ts && d.recent && d.recent.x[d.recent.x.length - 1] !== state.now.ts) {
        d.recent.x.push(state.now.ts);
        d.recent.down.push(state.now.down);
        d.recent.up.push(state.now.up);
        if (d.recent.x.length > 90) ['x', 'down', 'up'].forEach(k => d.recent[k].shift());
    }
    if (activeTab !== 'overview' || !document.getElementById('net-head')) return;
    if (state.found !== wasFound) return renderInternet(true);
    renderInternetHead();
    if (internet.period === 'live') drawInternetTraffic();
    // The minutes and the hours: once a minute
    if (Date.now() - internet.loadedAt > 60000) loadInternet().then(() => { drawInternetTraffic(); drawInternetDays(); renderInternetHead(); }).catch(() => {});
}

function connectedFor(c) {
    if (missing(c.uptime) || c.status !== 'Connected') return null;
    return c.uptime + Math.max(0, Date.now() / 1000 - (c.uptime_at || Date.now() / 1000));
}

// The bytes per day of the browser's own clock, from the bytes per hour
function internetDays(days) {
    const h = (internet.data && internet.data.hours) || {x: [], down: [], up: []};
    const byDay = new Map();
    h.x.forEach((ts, i) => {
        const d = new Date(ts * 1000);
        const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
        const row = byDay.get(key) || {down: 0, up: 0};
        row.down += h.down[i] || 0;
        row.up += h.up[i] || 0;
        byDay.set(key, row);
    });
    const out = [];
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    for (let i = days - 1; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(today.getDate() - i);
        const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
        out.push({ts: d.getTime() / 1000, ...(byDay.get(key) || {down: 0, up: 0})});
    }
    return out;
}

function renderInternetHead() {
    const el = document.getElementById('net-head');
    const d = internet.data;
    if (!el || !d || !d.found) return;
    const c = d.connection || {}, now = d.now || {}, info = d.info || {};
    const up = c.status === 'Connected';
    const since = connectedFor(c);
    const today = internetDays(1)[0];
    const ratio = (rate, max) => missing(rate) || !max ? null : rate * 8 / max;
    const sub = [info.maker, info.firmware ? `firmware ${info.firmware}` : '', d.address].filter(Boolean).map(escapeHtml).join(' · ');
    const html = `
        <div class="net-title">
            ${iconHtml('router-network', 'big', 'router')}
            <div class="net-name"><div class="he-name">${escapeHtml(info.model || info.name || 'Router')}</div><div class="muted">${sub}</div></div>
            <span class="spacer"></span>
            ${up ? stateLabel('ok', 'Connected') : stateLabel('bad', c.status || 'Not connected')}
        </div>
        <div class="tiles kpi-tiles net-tiles">
            ${tile('Download', escapeHtml(fmtRate(now.down)), c.max_down ? meter(ratio(now.down, c.max_down), plainPct(ratio(now.down, c.max_down), 0), {title: `Of the ${fmtBits(c.max_down)} link`}) : 'now')}
            ${tile('Upload', escapeHtml(fmtRate(now.up)), c.max_up ? meter(ratio(now.up, c.max_up), plainPct(ratio(now.up, c.max_up), 0), {title: `Of the ${fmtBits(c.max_up)} link`}) : 'now')}
            ${tile('Today', `↓ ${bytesHtml(today.down)}`, `↑ ${escapeHtml(fmtBytes(today.up))} up since midnight`)}
            ${tile('Connected for', since === null ? '–' : escapeHtml(fmtDuration(since)), since === null ? (c.last_error ? escapeHtml(c.last_error) : '&nbsp;') : `since ${escapeHtml(fmtDateTime(Date.now() / 1000 - since))}`, up ? '' : 'bad')}
            ${tile('External address', `<span class="mono net-ip">${escapeHtml(c.external_ip || '–')}</span>`, c.external_ipv6 ? `<span class="mono" title="${attr(c.ipv6_prefix ? 'Prefix ' + c.ipv6_prefix : '')}">${escapeHtml(c.external_ipv6)}</span>` : (c.dns ? `DNS ${escapeHtml(c.dns.join(', '))}` : '&nbsp;'))}
            ${tile('Link', escapeHtml(c.access_type || '–'), c.max_down ? `${escapeHtml(fmtBits(c.max_down))} ↓ · ${escapeHtml(fmtBits(c.max_up))} ↑${c.physical && c.physical !== 'Up' ? ` · ${escapeHtml(c.physical)}` : ''}` : '&nbsp;', c.physical && c.physical !== 'Up' ? 'bad' : '')}
        </div>`;
    if (el.innerHTML !== html) el.innerHTML = html;
}

function drawInternetTraffic() {
    const el = document.getElementById('net-traffic');
    const d = internet.data;
    if (!el || !d) return;
    const src = internet.period === 'live' ? d.recent : d.minutes;
    if (!src) return;
    const live = internet.period === 'live';
    const span = src.x.length ? src.x[src.x.length - 1] - src.x[0] : 0;
    const axis = rateAxis(Math.max(0, ...[...src.down, ...src.up].filter(v => !missing(v))));
    lineChart(el, {
        x: src.x, height: 200, zero: true,
        series: [
            {name: 'Down', color: DOWN_COLOR, values: src.down, area: true},
            {name: 'Up', color: UP_COLOR, values: src.up, area: true},
        ],
        xTicks: timeAxisTicks(src.x[0], src.x[src.x.length - 1], el.clientWidth < 520 ? 3 : 6),
        formatX: t => span > 2 * 86400 ? fmtDateTime(t) : live ? new Date(t * 1000).toLocaleTimeString() : fmtTime(t),
        formatY: v => fmtRate(v), formatTick: axis.tick, unit: axis.unit,
        empty: live ? 'Collecting the first readings…' : 'No traffic recorded yet: it fills from now on',
    });
}

function drawInternetDays() {
    const el = document.getElementById('net-days');
    if (!el) return;
    const days = internetDays(30);
    // The bars stack down and up
    const axis = bytesAxis(Math.max(0, ...days.map(r => r.down + r.up)));
    barChart(el, {
        data: days.map(r => ({label: fmtDay(r.ts), tipTitle: new Date(r.ts * 1000).toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'}), values: [r.down, r.up]})),
        series: [{name: 'Down', color: DOWN_COLOR}, {name: 'Up', color: UP_COLOR}],
        height: 200, format: v => fmtBytes(v), formatTick: axis.tick, unit: axis.unit, labelWidth: 52,
        empty: 'No days recorded yet',
    });
}

// The section: built when the router is found or lost, then only filled
function renderInternet(rebuild) {
    const el = document.getElementById('ov-internet');
    if (!el) return;
    const d = internet.data;
    if (!d) {
        if (!internet.loading) loadInternet().then(() => renderInternet(true)).catch(e => { el.innerHTML = ''; });
        return;
    }
    const shape = d.found ? 'found' : 'none';
    if (rebuild || el.dataset.shape !== shape) {
        el.dataset.shape = shape;
        if (!d.found) {
            el.innerHTML = `<div class="setup-hint net-none"><span><b>Internet:</b> no router read. ${escapeHtml(d.error || 'Looking for it…')}
                <br><span class="muted">The app reads your router over UPnP or TR-064, as Home Assistant's own UPnP integration does. Give your router the kind <i>Router</i> on the map, or set its address in the app's options. On a FRITZ!Box turn on <i>Home Network → Network → Network Settings → Transmit status information over UPnP</i>.</span></span></div>`;
            return;
        }
        el.innerHTML = `
            <div class="panel net-panel"><div class="panel-body" id="net-head"></div></div>
            <div class="dash-grid net-charts">
                ${panel('Internet traffic', chipsHtml('net-period', INTERNET_PERIODS, internet.period, 'Period'), `<div id="net-traffic" class="chart"></div><div class="legend">${legendHtml([{name: 'Down', color: DOWN_COLOR}, {name: 'Up', color: UP_COLOR}])}</div>`)}
                ${panel('Per day', 'the last 30 days', `<div id="net-days" class="chart"></div><div class="legend">${legendHtml([{name: 'Down', color: DOWN_COLOR}, {name: 'Up', color: UP_COLOR}])}</div>`)}
            </div>`;
        bindChips('net-period', key => {
            internet.period = key;
            store('internet_period', key);
            if (key === 'live') drawInternetTraffic();
            else loadInternet().then(drawInternetTraffic).catch(e => toast(e.message));
        });
    }
    renderInternetHead();
    drawInternetTraffic();
    drawInternetDays();
}
