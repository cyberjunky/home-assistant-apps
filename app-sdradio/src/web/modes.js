// SDRadio dashboard - the modes: the radio set up for pagers, sensors and more, with a view of
// what they decode in place of the favourites and the stored channels.

const modeView = {key: null, items: [], keys: new Map(), search: '', loading: false, renderQueued: false};
const MODE_LIMIT = 500;

// What the readings of rtl_433 mean, for the sensors table
const SENSOR_FIELDS = {
    temperature_C: ['', ' °C', 1], temperature_F: ['', ' °F', 1], humidity: ['', ' %', 0], moisture: ['moisture ', ' %', 0],
    pressure_hPa: ['', ' hPa', 1], pressure_kPa: ['', ' kPa', 1], wind_avg_km_h: ['wind ', ' km/h', 1],
    wind_max_km_h: ['gust ', ' km/h', 1], wind_avg_m_s: ['wind ', ' m/s', 1], wind_max_m_s: ['gust ', ' m/s', 1],
    wind_dir_deg: ['', '°', 0], rain_mm: ['rain ', ' mm', 1], rain_rate_mm_h: ['rain ', ' mm/h', 1], light_lux: ['', ' lx', 0],
    uv: ['UV ', '', 1], power_W: ['', ' W', 0], energy_kWh: ['', ' kWh', 2], current_A: ['', ' A', 2], voltage_V: ['', ' V', 1],
};

function currentMode() { return (app.state && app.state.decoder && app.state.decoder.key) || 'radio'; }

// ---------------------------------------------------------------- the mode picker in the top bar

function renderModePicker() {
    const s = app.state;
    const select = document.getElementById('mode-select');
    if (!s || !select) return;
    const modes = [{key: 'radio', name: 'Radio'}, ...(s.modes || [])];
    const signature = modes.map(m => m.key).join();
    if (select.dataset.signature !== signature) {
        select.innerHTML = modes.map(m => `<option value="${m.key}">${escapeHtml(m.name)}</option>`).join('');
        select.dataset.signature = signature;
    }
    if (select.value !== currentMode() && document.activeElement !== select) select.value = currentMode();
}

function chooseMode(key) {
    radioCommand('decoder', {key});
}

// ---------------------------------------------------------------- the mode's view on the Radio page

function renderModeLayout() {
    const key = currentMode();
    const page = document.getElementById('tab-radio');
    if (page.dataset.mode !== key) {
        page.dataset.mode = key;
        requestAnimationFrame(fitWaterfall);
    }
    if (modeView.key !== key) {
        modeView.key = key;
        modeView.items = [];
        modeView.keys = new Map();
        modeView.search = '';
        document.getElementById('mode-search').value = '';
        if (key !== 'radio') loadModeData();
        renderModeBody();
    }
    if (key !== 'radio') renderModeHead();
}

function renderModeHead() {
    const s = app.state, d = s.decoder || {};
    document.getElementById('mode-title').textContent = d.name || '';
    const status = document.getElementById('mode-status');
    const text = d.error ? d.error : `${fmtMHz(s.frequency)} MHz · ${d.count || 0} decoded since the mode started`;
    if (status.textContent !== text) status.textContent = text;
    status.classList.toggle('warn-text', !!d.error);
    document.getElementById('mode-search').placeholder = key2placeholder(d.key);
}

function key2placeholder(key) {
    return key === 'sensors' ? 'Search devices…' : 'Search messages, capcodes…';
}

async function loadModeData() {
    const key = modeView.key;
    if (!key || key === 'radio') return;
    modeView.loading = true;
    try {
        if (key === 'sensors') {
            const data = await api(`decoded/keys?mode=${key}`);
            if (modeView.key !== key) return;
            modeView.keys = new Map(data.keys.map(k => [k.key, k]));
        } else {
            const q = new URLSearchParams({mode: key, limit: 300, search: modeView.search});
            const data = await api(`decoded?${q}`);
            if (modeView.key !== key) return;
            modeView.items = data.items;
        }
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        modeView.loading = false;
    }
    renderModeBody();
}

onSocket('decoded', (m) => {
    if (m.mode !== modeView.key) return;
    const item = m.item;
    if (m.mode === 'sensors') {
        const known = modeView.keys.get(item.key);
        modeView.keys.set(item.key, {...item, count: (known ? known.count : 0) + 1, first: known ? known.first : item.ts});
    } else {
        if (modeView.search && !JSON.stringify(item).toLowerCase().includes(modeView.search.toLowerCase())) return;
        modeView.items.unshift(item);
        if (modeView.items.length > MODE_LIMIT) modeView.items.length = MODE_LIMIT;
    }
    if (!modeView.renderQueued) {
        modeView.renderQueued = true;
        requestAnimationFrame(() => { modeView.renderQueued = false; renderModeBody(); });
    }
});

function renderModeBody() {
    const body = document.getElementById('mode-body');
    if (modeView.key === 'pager') body.innerHTML = pagerHtml();
    else if (modeView.key === 'sensors') body.innerHTML = sensorsHtml();
    else body.innerHTML = '';
}

function timeOf(ts) {
    const d = new Date(ts * 1000);
    const today = new Date().toDateString() === d.toDateString();
    return today ? d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})
        : d.toLocaleString([], {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
}

function emptyHtml(text) {
    return `<div class="empty-state">${modeView.loading ? 'Loading…' : text}</div>`;
}

// ---------------------------------------------------------------- pager

function pagerHtml() {
    if (!modeView.items.length) {
        return emptyHtml(modeView.search ? 'No messages match.' : 'No messages yet. Pagers send in bursts: the waterfall shows them coming in.');
    }
    return modeView.items.map(m => `<div class="msg">
        <span class="msg-time mono">${timeOf(m.ts)}</span>
        <span class="badge">${escapeHtml(m.protocol || '')}</span>
        <span class="msg-addr mono" title="Capcode / address">${escapeHtml(m.key)}</span>
        <span class="msg-text${m.text ? '' : ' muted'}">${m.text ? escapeHtml(m.text) : escapeHtml(m.type || 'tone')}</span>
    </div>`).join('');
}

// ---------------------------------------------------------------- sensors

function sensorValues(values) {
    const parts = [];
    for (const [field, value] of Object.entries(values || {})) {
        if (field === 'battery_ok') { if (!value) parts.push('<span class="warn-text">battery low</span>'); continue; }
        const known = SENSOR_FIELDS[field];
        if (known && typeof value === 'number') parts.push(`${known[0]}${num(value, known[2])}${known[1]}`);
        else if (typeof value !== 'object') parts.push(`${escapeHtml(field.replace(/_/g, ' '))} ${escapeHtml(String(value))}`);
    }
    return parts.join(' · ');
}

function sensorsHtml() {
    let devices = Array.from(modeView.keys.values());
    const search = modeView.search.toLowerCase();
    if (search) devices = devices.filter(d => JSON.stringify(d).toLowerCase().includes(search));
    if (!devices.length) {
        return emptyHtml(search ? 'No devices match.' : 'No devices heard yet. Most send every 30 to 60 seconds.');
    }
    devices.sort((a, b) => b.ts - a.ts);
    const settings = (app.settings && app.settings.mode_sensors) || {};
    const chosen = new Set(settings.ha_devices || []);
    const ha = app.settingsData && app.settingsData.ha;
    return `<table class="data sensors"><thead><tr><th>Device</th><th>Last reading</th><th>Signal</th><th>Heard</th><th title="Readings as Home Assistant sensors">HA</th></tr></thead><tbody>
        ${devices.map(d => `<tr>
            <td><b>${escapeHtml(d.model || '')}</b><small class="muted mono">${escapeHtml([d.id, d.channel].filter(x => x !== null && x !== undefined).map(x => `${x}`).join(' · ch '))}</small></td>
            <td>${sensorValues(d.values)}</td>
            <td class="mono">${d.snr !== null && d.snr !== undefined ? `${num(d.snr, 0)} dB` : ''}</td>
            <td class="mono" title="${escapeHtml(new Date(d.ts * 1000).toLocaleString())}">${fmtAgo(d.ts)} · ${d.count}×</td>
            <td><input type="checkbox" ${chosen.has(d.key) ? 'checked' : ''} ${ha ? '' : 'disabled title="Only when SDRadio runs as a Home Assistant app"'}
                onchange="toggleSensorHa(${escapeHtml(JSON.stringify(d.key))}, this.checked)" aria-label="Home Assistant sensors for ${escapeHtml(d.key)}"></td>
        </tr>`).join('')}
    </tbody></table>`;
}

async function toggleSensorHa(key, on) {
    const settings = {...((app.settings && app.settings.mode_sensors) || {})};
    const devices = new Set(settings.ha_devices || []);
    if (on) devices.add(key); else devices.delete(key);
    settings.ha_devices = Array.from(devices);
    await saveSetting('mode_sensors', settings, true);
    toast(on ? `${key}: sensors in Home Assistant from its next reading` : `${key}: no longer sent to Home Assistant`, 'success');
}

// ---------------------------------------------------------------- search and clear

function initModes() {
    const search = document.getElementById('mode-search');
    let timer = null;
    search.addEventListener('input', () => {
        modeView.search = search.value.trim();
        if (modeView.key === 'sensors') return renderModeBody();
        clearTimeout(timer);
        timer = setTimeout(loadModeData, 300);
    });
    document.getElementById('mode-select').addEventListener('change', (e) => chooseMode(e.target.value));
    // Keep "heard … ago" current
    setInterval(() => { if (activeTab === 'radio' && modeView.key === 'sensors') renderModeBody(); }, 15000);
}

function clearModeData() {
    const key = modeView.key;
    const what = key === 'sensors' ? 'every reading of every device' : 'every message';
    confirmModal('Clear', `Delete ${what} of this mode from the database?`, 'Delete', async () => {
        await api(`decoded?mode=${key}`, {method: 'DELETE'});
        modeView.items = [];
        modeView.keys = new Map();
        renderModeBody();
    }, true);
}

onSocket('state', () => {
    renderModePicker();
    if (activeTab === 'radio') renderModeLayout();
});
