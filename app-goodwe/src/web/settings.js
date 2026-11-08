// GoodWe dashboard - Settings view: battery control (control.js), inverter, inverter settings,
// forecast, MQTT, data, events and debug.

let settingsCache = null;
let settingsReadAt = null;
let eventsSeqShown = -1;
let eventsFilter = localStorage.getItem(LS + 'events_filter') || '';
let debugRendered = false;

const EVENT_GROUPS = {
    '': 'All events',
    commands: 'Inverter commands',
    control: 'Battery control',
    alerts: 'Alerts',
    surplus: 'Solar surplus',
};
const EVENT_GROUP_TYPES = {
    commands: 'inverter_write,inverter_write_failed',
    control: 'hold_applied,hold_released,zero_export_set,work_mode_changed',
    alerts: 'inverter_offline,inverter_online,work_mode_not_general,hold_violation,hold_violation_recovered,export_violation,export_violation_recovered,write_budget_exhausted,fault_detected,fault_cleared,grid_outage,grid_restored,battery_low,battery_full',
    surplus: 'surplus_started,surplus_ended',
};

function renderInverterInfo() {
    const inv = app.inverter, cfg = (app.status && app.status.config) || {};
    document.getElementById('set-inverter').innerHTML = [
        infoRow('Status', inv.connected ? `<span class="${inv.online ? 'sb-ok' : 'sb-bad'}">${inv.online ? 'online' : 'not answering'}</span>` : '<span class="sb-warn">connecting…</span>'),
        infoRow('Model', escapeHtml(inv.model || '–')),
        infoRow('Serial number', escapeHtml(inv.serial || '–')),
        infoRow('Platform', escapeHtml(inv.platform ? `${inv.platform}${inv.platform === 'ES' ? ' (ES/EM/BP, AA55 protocol)' : ''}` : '–')),
        infoRow('Firmware', escapeHtml(inv.firmware ? `${inv.firmware} · DSP ${inv.dsp_version} · ARM ${inv.arm_version}${inv.arm_firmware ? ` (${inv.arm_firmware})` : ''}` : '–')),
        infoRow('Address', `${escapeHtml(inv.host || cfg.host || '–')} · read every ${num(inv.poll_seconds || cfg.poll_seconds)} s`),
        infoRow('Connected', inv.connected_at ? fmtDateTime(inv.connected_at) : '–'),
        infoRow('Last read', inv.last_read ? `${fmtAgo(inv.last_read)} · ${num(inv.polls)} reads` : '–'),
        inv.last_error ? infoRow('Last error', `<span class="sb-warn">${escapeHtml(inv.last_error)}</span>`) : '',
    ].join('') + '<p class="panel-sub" style="margin-top:10px">The address and the poll interval are set on the add-on\'s Configuration tab in Home Assistant.</p>';
}

function renderInverterControl() {
    const inv = app.inverter, c = app.control, s = settingsCache || {};
    const value = v => missing(v) ? '–' : escapeHtml(String(v));
    const general = c.work_mode_general;
    const mode = general === null || general === undefined
        ? 'not read yet'
        : general ? '<span class="sb-ok">General</span> <small>· self-use: solar → home → battery, never charging from the grid</small>'
        : `<span class="sb-warn">${escapeHtml(c.work_mode_name || '?')}</span> <button type="button" class="btn btn-small btn-primary" onclick="backToGeneral()"${inv.connected ? '' : ' disabled'}>Back to General</button>`;
    const exportText = missing(s.grid_export_limit) ? 'not read yet'
        : `${num(s.grid_export_limit)} W · ${Number(s.grid_export) ? 'enforced' : '<span class="sb-warn">not enforced</span>'}`;
    const capacity = Number(c.battery_capacity_kwh || 0);
    document.getElementById('set-inverter-control').innerHTML = [
        infoRow('Work mode', mode),
        infoRow('Export limit', `${exportText} <small>· kept at 0 W by Zero export</small>`),
        infoRow('On-grid DoD', missing(s.dod) ? '–' : `${num(s.dod)}% <small>· set by Hold / Normal and auto-hold</small>`),
        infoRow('Battery capacity', missing(s.capacity) ? (capacity ? `≈ ${num(capacity, 1)} kWh` : '–') : `${value(s.capacity)} Ah <small>≈ ${num(capacity, 1)} kWh at 51.2 V</small>`),
        infoRow('Charge voltage · current', missing(s.charge_v) ? '–' : `${num(s.charge_v, 1)} V · ${num(s.charge_i)} A`),
        infoRow('Discharge voltage · current', missing(s.discharge_v) ? '–' : `${num(s.discharge_v, 1)} V · ${num(s.discharge_i)} A`),
        missing(s.battery_soc_protection) ? '' : infoRow('SoC protection', fmtPct(s.battery_soc_protection)),
        missing(s.backup_supply) ? '' : infoRow('Backup supply', `${Number(s.backup_supply) ? 'on' : 'off'} <small>· set in SolarGo (not writable on the ES)</small>`),
    ].join('') + `<p class="panel-sub" style="margin-top:10px">Read once a day by the controller${settingsReadAt ? `, last ${fmtAgo(settingsReadAt)}` : ''}. Auto-hold needs General mode; on the ES that is also the mode that self-consumes.
            <button type="button" class="btn btn-small" onclick="readSettings(this)"${inv.connected ? '' : ' disabled'}>Read now</button></p>`;
}

async function readSettings(button) {
    if (button) button.disabled = true;
    try {
        const result = await api('inverter/settings');
        settingsCache = result.settings;
        settingsReadAt = result.read_at;
        renderInverterControl();
        if (debugRendered) renderDebug();
    } catch (e) {
        toast(e.message, 'error');
    }
    if (button) button.disabled = false;
}

async function writeSetting(key, value, title, message) {
    const ok = await confirmDialog({title, message, confirmLabel: 'Write'});
    if (!ok) return false;
    try {
        const result = await api('inverter/write', {method: 'POST', body: {key, value}});
        toast(result.result, 'success');
        if (settingsCache) {
            if (key === 'grid_export') settingsCache.grid_export = value ? 1 : 0;
            else settingsCache[key] = value;
        }
        renderInverterControl();
        return true;
    } catch (e) {
        toast(e.message, 'error');
        return false;
    }
}

async function backToGeneral() {
    if (await writeSetting('work_mode', 0, 'Switch back to General mode?', 'General mode self-consumes (solar → home → battery, never charging from the grid) and is the mode auto-hold works in. Written with the dedicated command; it takes effect right away.')) {
        await loadStatus().catch(() => {});
        renderControl(true);
        renderInverterControl();
    }
}

function renderForecastSettings() {
    const f = app.forecast, cfg = (app.status && app.status.config) || {};
    document.getElementById('set-forecast').innerHTML = [
        infoRow('Forecast', f.configured ? '<span class="sb-ok">on</span>' : f.enabled ? '<span class="sb-warn">no location</span>' : 'off'),
        infoRow('Location', f.location ? `${num(f.location.lat, 4)}, ${num(f.location.lon, 4)} <small>(${escapeHtml(f.location.source)})</small>` : '–'),
        infoRow('Installed', `${num(cfg.total_kwp, 2)} kWp in ${(cfg.planes || []).length} panel group${(cfg.planes || []).length === 1 ? '' : 's'}`),
        ...(cfg.planes || []).map(p => infoRow(escapeHtml(p.name), `${num(p.kwp, 2)} kWp · tilt ${p.tilt}° · azimuth ${p.azimuth}°`)),
        infoRow('Source', f.source ? escapeHtml(f.source === 'open-meteo' ? 'Open-Meteo (fallback, less precise)' : 'Forecast.Solar') : '–'),
        infoRow('Fetched', f.fetched_at ? `${fmtDateTime(f.fetched_at)} <small>· next ${fmtTime(f.fetched_at + 1800)}</small>` : '–'),
        f.configured ? infoRow('Today · tomorrow', `${fmtKwh(f.today_kwh)} · ${fmtKwh(f.tomorrow_kwh)}`) : '',
        f.error ? infoRow('Problem', `<span class="sb-warn">${escapeHtml(f.error)}</span>`) : '',
    ].join('') + `<p class="panel-sub" style="margin-top:10px">Forecast.Solar is asked once per panel group every 30 minutes, with Open-Meteo as a fallback. Location and panels are set on the add-on's Configuration tab; leave the location at 0 to use Home Assistant's home.</p>`
        + (f.configured ? '<div class="detail-actions" style="margin-top:10px"><button type="button" class="btn btn-small" onclick="refreshForecast(this)">Fetch again</button></div>' : '');
}

async function refreshForecast(button) {
    button.disabled = true;
    try {
        app.forecast = await api('forecast/refresh', {method: 'POST'});
        toast('Forecast refreshed', 'success');
        renderForecastSettings();
    } catch (e) {
        toast(e.message, 'error');
        button.disabled = false;
    }
}

function renderMqttSettings() {
    const m = app.mqtt, cfg = (app.status && app.status.config) || {};
    const serial = String(app.inverter.serial || '').toLowerCase();
    document.getElementById('set-mqtt').innerHTML = [
        infoRow('Publishing', !cfg.mqtt_enabled ? 'off' : m.enabled ? `<span class="${m.connected ? 'sb-ok' : 'sb-bad'}">${m.connected ? 'connected' : 'offline'}</span> · ${escapeHtml(m.broker)}` : '<span class="sb-warn">no broker</span>'),
        infoRow('State topic', escapeHtml(m.state_topic || `${cfg.mqtt_base_topic}/state`)),
        infoRow('Event topic', escapeHtml(m.event_topic || `${cfg.mqtt_base_topic}/event`)),
        infoRow('Device', serial ? `GoodWe ${escapeHtml(app.inverter.model || '')} <small>(goodwe_${escapeHtml(serial)})</small>` : '–'),
    ].join('') + `<p class="panel-sub" style="margin-top:10px">With discovery on, the inverter appears in Home Assistant as a device with sensors (solar, grid, battery and its cycles, home, forecast, surplus and curtailment), binary sensors (solar surplus, battery held, zero export, exporting, work mode not General, fault, connected) and an <b>Events</b> entity. Automate on the event entity for a Telegram message, e.g. when its <span class="mono">event_type</span> is <span class="mono">hold_applied</span> or <span class="mono">surplus_started</span>; the message is in its attributes.</p>`;
}

function renderDataSettings() {
    const db = (app.status && app.status.database) || {};
    document.getElementById('set-data').innerHTML = [
        infoRow('Recorded days', `${num(db.days)}${db.first_date ? ` <small>since ${fmtIsoDate(db.first_date, true)}</small>` : ''}`),
        infoRow('Snapshots', `${num(db.snapshots)} <small>(kept 14 days)</small>`),
        infoRow('Events', num(db.events)),
        infoRow('Database size', `${num((db.bytes || 0) / 1048576, 1)} MB`),
    ].join('');
    document.getElementById('export-daily').href = `${basePath}api/export/daily.csv`;
    document.getElementById('export-db').href = `${basePath}api/backup`;
}

function renderAbout() {
    const cfg = (app.status && app.status.config) || {};
    document.getElementById('set-support').innerHTML = `
        ${infoRow('Documentation', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-goodwe/DOCS.md" target="_blank" rel="noopener">Docs on GitHub</a>')}
        ${infoRow('Problem or idea', '<a href="https://github.com/cyberjunky/home-assistant-apps/issues" target="_blank" rel="noopener">Open an issue</a>')}
        ${infoRow('Version', `v${escapeHtml(app.version)} · <a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-goodwe/CHANGELOG.md" target="_blank" rel="noopener">changelog</a>`)}`;
    document.getElementById('set-about').innerHTML = `
        ${infoRow('Log level', escapeHtml(cfg.log_level || 'info'))}
        ${infoRow('Inverter library', 'goodwe (Python)')}
        <p class="panel-sub" style="margin-top:10px">This app is an independent project and is not affiliated with or endorsed by GoodWe Technologies Co., Ltd.</p>`;
}

async function renderEvents(force) {
    const select = document.getElementById('events-filter');
    if (!select.options.length) {
        select.innerHTML = Object.entries(EVENT_GROUPS).map(([k, v]) => `<option value="${k}"${k === eventsFilter ? ' selected' : ''}>${v}</option>`).join('');
    }
    if (force) {
        eventsFilter = select.value;
        localStorage.setItem(LS + 'events_filter', eventsFilter);
    } else if (eventsSeqShown === app.eventsSeq) {
        return;
    }
    eventsSeqShown = app.eventsSeq;
    const el = document.getElementById('events-list');
    try {
        const types = EVENT_GROUP_TYPES[eventsFilter] || '';
        const result = await api(`events?limit=200${types ? '&types=' + types : ''}`);
        const names = (app.status && app.status.event_types) || {};
        el.innerHTML = result.events.length ? result.events.map(ev => `
            <div class="event ${ev.level}"><span class="when">${fmtDateTime(ev.ts)}</span><span class="kind" title="${escapeHtml(names[ev.type] || '')}">${escapeHtml(ev.type)}</span><span class="msg">${escapeHtml(ev.message)}</span></div>`).join('')
            : '<div class="empty-state">Nothing happened yet</div>';
    } catch (e) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    }
}

function kvGrid(obj) {
    const keys = Object.keys(obj || {}).sort();
    if (!keys.length) return '<div class="panel-sub">nothing</div>';
    return `<div class="kv-grid">${keys.map(k => `<div class="kv"><span>${escapeHtml(k)}</span><span title="${escapeHtml(String(obj[k]))}">${escapeHtml(typeof obj[k] === 'number' ? Number(obj[k].toFixed(3)) : obj[k])}</span></div>`).join('')}</div>`;
}

function renderDebug() {
    debugRendered = true;
    const writable = (app.status && app.status.writable) || [];
    document.getElementById('debug').innerHTML = `
        <div class="section-title">Runtime data <small>(as normalised: grid + = import, battery + = charging)</small></div>
        ${kvGrid(app.data)}
        <div class="section-title" style="margin-top:16px">Inverter settings <small>${settingsReadAt ? `read ${fmtAgo(settingsReadAt)}` : 'not read yet'}</small> <button type="button" class="btn btn-small" onclick="readSettings(this)">Read settings</button></div>
        ${settingsCache ? kvGrid(settingsCache) : '<div class="panel-sub">Press Read settings: one full settings request to the inverter.</div>'}
        <div class="section-title" style="margin-top:16px">Send a command <small>(the dedicated commands only; anything else is silently ignored by ES firmware)</small></div>
        <div class="detail-actions">
            <select id="debug-key" class="select">${writable.map(k => `<option value="${k}">${k}</option>`).join('')}</select>
            <input id="debug-value" class="input" style="width:120px" placeholder="value">
            <button type="button" class="btn" onclick="debugWrite()">Send</button>
            <span class="panel-sub">work_mode 0–3 · dod 0–100 · grid_export_limit W · grid_export 0/1 · charge_i whole amps (0 brakes charging)</span>
        </div>
        <div class="section-title" style="margin-top:16px">Battery controller</div>
        <pre class="json">${escapeHtml(JSON.stringify(app.control, null, 2))}</pre>
        <div class="section-title" style="margin-top:16px">Inverter link</div>
        <pre class="json">${escapeHtml(JSON.stringify(app.inverter, null, 2))}</pre>
        <div class="section-title" style="margin-top:16px">Forecast</div>
        <pre class="json">${escapeHtml(JSON.stringify(app.forecast, null, 2))}</pre>`;
}

async function debugWrite() {
    const key = document.getElementById('debug-key').value;
    const raw = document.getElementById('debug-value').value.trim();
    if (raw === '') return toast('Enter a value', 'error');
    const value = key === 'grid_export' ? ['1', 'true', 'on'].includes(raw.toLowerCase()) : Number(raw);
    if (key !== 'grid_export' && Number.isNaN(value)) return toast('Enter a number', 'error');
    await writeSetting(key, value, `Send ${key} = ${raw}?`, 'This writes to the inverter right away.');
}

function renderSettings() {
    renderControl(true);
    renderInverterInfo();
    renderInverterControl();
    renderForecastSettings();
    renderMqttSettings();
    renderDataSettings();
    renderAbout();
    renderEvents(false);
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('debug-details').addEventListener('toggle', (e) => { if (e.target.open) renderDebug(); });
});

onTab('settings', async () => {
    renderSettings();
    if (!settingsCache) {
        try {
            const cached = await api('inverter/settings?cached=1');
            if (cached.cached) { settingsCache = cached.settings; settingsReadAt = cached.read_at; }
        } catch (e) { /* no cache yet */ }
    }
    // The counts and sizes aren't part of the live stream
    try { await loadStatus(); } catch (e) { /* keep what we have */ }
    renderSettings();
});
onLive(() => {
    if (activeTab !== 'settings') return;
    renderControl(false);
    renderInverterInfo();
    renderEvents(false);
    if (debugRendered && document.getElementById('debug-details').open && !document.activeElement.closest('#debug')) renderDebug();
});
