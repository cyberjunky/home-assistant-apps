// Toon dashboard - Settings: the Toon's health, connection history, configuration and about.

const OUTAGE_KINDS = {reboot: 'Restarted', network: 'Connection lost', app_stopped: 'App was stopped', unknown: 'Unknown'};
const HEALTH_RANGES = [{key: '24', label: '24 hours'}, {key: '168', label: '7 days'}, {key: '720', label: '30 days'}];
// One unit per chart. Data-driven so another source only needs an entry here.
const HEALTH_CHARTS = [
    {title: 'Toon memory', unit: 'MB', series: ['memory_free', 'memory_cached', 'memory_gui', 'memory_web'], format: v => `${num(v, 1)} MB`},
    {title: 'Bus threads and open files', unit: 'count', series: ['bus_threads', 'bus_fds'], format: v => num(v)},
    {title: 'WiFi signal', unit: '%', series: ['wifi_signal'], format: v => `${num(v)}%`},
    {title: 'WiFi packets dropped', unit: 'count since boot', series: ['wifi_rx_dropped'], format: v => num(v)},
    {title: 'Toon uptime', unit: 'hours', series: ['uptime'], format: v => `${num(v, 1)} h`},
    {title: 'Toon clock offset', unit: 'seconds', series: ['clock_offset'], format: v => `${num(v)} s`},
];
const SERIES_COLORS = ['series-1', 'series-2', 'series-3', 'series-4', 'series-5'];
let healthHours = '24';

function renderHealth() {
    const offset = value('clock_offset');
    document.getElementById('health-tiles').innerHTML = [
        tile('Uptime', unit(value('uptime'), 1, 'h')),
        tile('Clock offset', missing(offset) ? '–' : withUnit(num(offset), 's'), Math.abs(offset) > 120 ? `${fmtDuration(Math.abs(offset))} off` : 'on time', Math.abs(offset) > 120 ? 'warn' : ''),
        tile('WiFi signal', unit(value('wifi_signal'), 0, '%')),
        tile('WiFi dropped', num(value('wifi_rx_dropped')), 'packets since boot'),
        tile('Memory free', unit(value('memory_free'), 1, 'MB'), `cached ${num(value('memory_cached'), 1)} MB`),
        tile('Screen software', unit(value('memory_gui'), 1, 'MB')),
        tile('Web server', unit(value('memory_web'), 1, 'MB')),
        tile('Bus threads', num(value('bus_threads')), `${num(value('bus_fds'))} open files`),
        tile('Meter adapter', value('meter_adapter_connected') === 0 ? 'Offline' : 'Connected', `health ${num(value('meter_adapter_health'))}`),
        tile('Load', num(value('load'), 2)),
    ].join('');
}

function renderConfig() {
    const s = app.status;
    const c = s.config;
    const yes = v => v ? 'on' : 'off';
    const when = s.database.oldest ? fmtIsoDate(isoDate(new Date(s.database.oldest * 1000)), true) : '–';
    document.getElementById('set-connection').innerHTML = [
        infoRow('Status', s.connected ? stateLabel('ok', 'connected') : stateLabel('bad', 'not connected')),
        infoRow('Connected for', s.connected ? fmtDuration(Date.now() / 1000 - s.connected_since) : '–'),
        infoRow('Address', `${escapeHtml(c.ssh_host)}:${c.ssh_port}`),
        infoRow('User', escapeHtml(c.ssh_username)),
        infoRow('Trust a new SSH key', yes(c.trust_new_host_key)),
        s.error ? infoRow('Last error', escapeHtml(s.error)) : '',
    ].join('');
    document.getElementById('set-mqtt').innerHTML = [
        infoRow('Publishing', yes(c.mqtt_enabled)),
        infoRow('Status', s.mqtt.broker ? (s.mqtt.connected ? stateLabel('ok', 'connected') : stateLabel('bad', 'not connected')) : 'no broker'),
        infoRow('Broker', escapeHtml(s.mqtt.broker || '–')),
        infoRow('Base topic', escapeHtml(c.mqtt_base_topic)),
        infoRow('Discovery prefix', escapeHtml(c.mqtt_discovery_prefix)),
    ].join('');
    document.getElementById('set-data').innerHTML = [
        infoRow('History import', c.import_history ? (s.import.running ? 'running' : `${s.import.done} of ${s.import.total} done`) : 'off'),
        infoRow('Keep samples', `${c.raw_retention_days} days`),
        infoRow('Samples', num(s.database.samples)),
        infoRow('Hourly values', num(s.database.hourly)),
        infoRow('Oldest value', when),
        infoRow('Database size', `${num(s.database.size / 1048576, 1)} MB`),
        infoRow('Energy in gas', `${num(c.gas_kwh_per_m3, 2)} kWh per m³`),
    ].join('');
    const adapter = s.adapters.boiler_adapter || {};
    document.getElementById('set-toon').innerHTML = [
        infoRow('Firmware', escapeHtml(s.info.vers || '–')),
        infoRow('Serial number', escapeHtml(s.info.serialNumber || '–')),
        infoRow('Part number', escapeHtml(s.info.partNumber || '–')),
        adapter.ZWUuid || adapter.swVersion ? infoRow('Boiler module', escapeHtml(adapter.swVersion || '')) : '',
        infoRow('Boiler module connected', value('boiler_module_connected') === 0 ? stateLabel('bad', 'no') : stateLabel('ok', 'yes')),
    ].join('');
    document.getElementById('set-about').innerHTML = [
        infoRow('App version', escapeHtml(s.version)),
        infoRow('Mode', 'read-only'),
        infoRow('Log level', escapeHtml(c.log_level)),
        infoRow('Documentation', '<a href="https://github.com/cyberjunky/home-assistant-apps/tree/main/app-toon" target="_blank" rel="noopener">on GitHub</a>'),
    ].join('');
}

async function loadOutages() {
    const rows = await api('outages');
    const now = Date.now() / 1000;
    const month = rows.filter(r => now - r.start_ts < 30 * 86400);
    const restarts = month.filter(r => r.kind === 'reboot').length;
    const lost = month.filter(r => r.kind === 'network').length;
    document.getElementById('outage-summary').textContent = `last 30 days: ${restarts} restarted, ${lost} connection lost`;
    document.getElementById('outages').innerHTML = rows.length ? `<table class="data"><thead><tr>
            <th>Started</th><th>Duration</th><th>What happened</th><th class="n">Memory free before</th><th class="n">Screen software before</th><th>Reason</th>
        </tr></thead><tbody>${rows.map(r => `<tr>
            <td>${escapeHtml(fmtDateTime(r.start_ts))}</td>
            <td>${escapeHtml(r.end_ts ? fmtDuration(r.end_ts - r.start_ts) : fmtDuration(now - r.start_ts) + ', ongoing')}</td>
            <td>${escapeHtml(r.end_ts ? (OUTAGE_KINDS[r.kind] || r.kind) : 'Not connected')}</td>
            <td class="n">${missing(r.memory_free) ? '–' : num(r.memory_free, 1) + ' MB'}</td>
            <td class="n">${missing(r.memory_gui) ? '–' : num(r.memory_gui, 1) + ' MB'}</td>
            <td class="wrap">${escapeHtml(r.reason || '')}</td>
        </tr>`).join('')}</tbody></table>` : '<div class="empty-state">No outages recorded yet.</div>';
}

function renderExternalSettings() {
    const sensors = externals();
    document.getElementById('set-external-panel').style.display = sensors.length ? '' : 'none';
    document.getElementById('set-external').innerHTML = sensors.map(s => infoRow(
        escapeHtml(s.name),
        `${s.error ? stateLabel('bad', s.error) : (missing(s.value) ? '–' : `${num(s.value, 2)} ${escapeHtml(s.unit || '')}`)} <small>${escapeHtml(s.entity)}</small>`,
    )).join('');
}

async function loadDatabaseReport() {
    const d = await api('database');
    const total = d.metrics.reduce((sum, m) => sum + m.samples + m.hourly, 0);
    document.getElementById('set-database').innerHTML = [
        infoRow('On disk', `${num(d.size / 1048576, 1)} MB <small>+ ${num(d.wal_size / 1048576, 1)} MB not yet merged</small>`),
        infoRow('Values measured', num(d.metrics.length)),
        infoRow('Rows', num(total)),
        infoRow('Outages recorded', num(d.outages)),
        ...d.metrics.slice(0, 8).map(m => infoRow(
            escapeHtml(m.name),
            `${num(m.samples)} samples · ${num(m.hourly)} hourly <small>since ${fmtIsoDate(isoDate(new Date(m.first * 1000)), true)}</small>`,
        )),
    ].join('');
}

async function loadHealthCharts() {
    const end = Math.floor(Date.now() / 1000);
    const hours = Number(healthHours);
    const start = end - hours * 3600;
    const keys = HEALTH_CHARTS.flatMap(c => c.series);
    const data = await api(`history?start=${start}&end=${end}&metrics=${keys.join(',')}`);
    const step = hours <= 24 ? 300 : hours <= 168 ? 1800 : 3600;
    const xs = gridTimes(start, end, step);
    const ticks = hours <= 24 ? hourTicks(start, end, 4) : timeTicks(xs, 4, fmtDay);
    const host = document.getElementById('health-charts');
    host.innerHTML = HEALTH_CHARTS.map((c, i) => `<div class="panel"><div class="panel-body"><div class="chart-title"><span>${escapeHtml(c.title)}</span><span class="hint">${escapeHtml(c.unit)}</span></div><div id="health-${i}" class="chart"></div><div class="legend" id="health-legend-${i}"></div></div></div>`).join('');
    HEALTH_CHARTS.forEach((c, i) => {
        const metrics = app.status.metrics;
        const series = c.series.map((key, s) => ({
            name: metrics[key].name.replace(/^Toon /, ''), color: color(SERIES_COLORS[s]),
            values: grid(data[key], start, end, step, Math.max(1800, step * 2)),
        }));
        lineChart(document.getElementById(`health-${i}`), {x: xs, series, xTicks: ticks, formatX: fmtDateTime, formatY: c.format, height: 150, empty: 'No values in this period yet'});
        if (series.length > 1) document.getElementById(`health-legend-${i}`).innerHTML = legendHtml(series.map(s => ({name: s.name, color: s.color, kind: 'line'})));
    });
}

async function showSettings() {
    renderHealth();
    renderConfig();
    renderExternalSettings();
    buildChips('health-range', HEALTH_RANGES, healthHours, key => { healthHours = key; loadHealthCharts(); });
    try {
        await Promise.all([loadOutages(), loadHealthCharts(), loadDatabaseReport()]);
    } catch (e) {
        toast('Cannot load this page: ' + e.message);
    }
}

onTab('settings', showSettings, () => { renderHealth(); renderConfig(); renderExternalSettings(); });
