// SDRadio dashboard - the modes: the radio set up for pagers, sensors and more, with a view of
// what they decode in place of the favourites and the stored channels.

const modeView = {key: null, items: [], keys: new Map(), search: '', loading: false, renderQueued: false,
    planes: new Map(), selected: null, list: lsGet('aircraft_list', 'live'), messages: 0};
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
        clearPlanes();
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
    const text = d.error ? d.error
        : d.key === 'aircraft' ? `${fmtMHz(s.frequency)} MHz · ${modeView.planes.size} in view · ${d.count || 0} seen since the mode started`
        : `${fmtMHz(s.frequency)} MHz · ${d.count || 0} decoded since the mode started`;
    if (status.textContent !== text) status.textContent = text;
    status.classList.toggle('warn-text', !!d.error);
    document.getElementById('mode-search').placeholder = key2placeholder(d.key);
}

function key2placeholder(key) {
    return {sensors: 'Search devices…', aircraft: 'Search callsign, registration, type…'}[key] || 'Search messages, capcodes…';
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
        } else if (key === 'aircraft') {
            const [live, history] = await Promise.all([api(`decoded/live?mode=${key}`),
                api(`decoded?${new URLSearchParams({mode: key, limit: 300, search: modeView.search})}`)]);
            if (modeView.key !== key) return;
            modeView.location = live.location;
            if (live.live) {
                modeView.messages = live.live.messages;
                for (const plane of live.live.aircraft) applyPlane(plane);
            }
            modeView.items = history.items;
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

onSocket('live', (m) => {
    if (m.mode !== modeView.key || m.mode !== 'aircraft') return;
    modeView.messages = m.messages;
    for (const plane of m.aircraft || []) applyPlane(plane);
    for (const icao of m.gone || []) removePlane(icao);
    queueModeRender();
});

function queueModeRender() {
    if (!modeView.renderQueued) {
        modeView.renderQueued = true;
        requestAnimationFrame(() => { modeView.renderQueued = false; renderModeBody(); });
    }
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
    queueModeRender();
});

function renderModeBody() {
    const body = document.getElementById('mode-body');
    if (modeView.key === 'aircraft') return renderAircraft(body);
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

// ---------------------------------------------------------------- aircraft

const planeLayers = new Map();  // ICAO address → {marker, trail}

function applyPlane(p) {
    const known = modeView.planes.get(p.icao);
    const plane = {...(known || {}), ...p};
    if (p.trail) plane.trail = p.trail;
    else {
        plane.trail = known ? known.trail || [] : [];
        if (p.point) {
            const last = plane.trail[plane.trail.length - 1];
            if (!last || last[0] !== p.point[0] || last[1] !== p.point[1]) plane.trail = [...plane.trail, p.point].slice(-400);
        }
    }
    delete plane.point;
    modeView.planes.set(p.icao, plane);
    drawPlane(plane);
}

function planeLabel(p) { return p.flight || p.registration || p.icao.toUpperCase(); }

function drawPlane(p) {
    const map = modeMap.map;
    if (!map || p.lat === undefined) return;
    const colour = altitudeColour(p.ground ? 0 : p.altitude);
    let layers = planeLayers.get(p.icao);
    if (!layers) {
        layers = {
            marker: L.marker([p.lat, p.lon], {icon: planeIcon(p.track, colour, false), keyboard: false})
                .bindTooltip('', {permanent: true, direction: 'right', offset: [10, 0], className: 'plane-label'})
                .on('click', () => selectPlane(p.icao, false)).addTo(map),
            trail: L.polyline([], {weight: 2, opacity: 0.75, interactive: false}).addTo(map),
            look: '',
        };
        planeLayers.set(p.icao, layers);
    }
    layers.marker.setLatLng([p.lat, p.lon]);
    const look = `${Math.round(p.track || 0)}|${colour}|${modeView.selected === p.icao}`;
    if (layers.look !== look) {
        layers.marker.setIcon(planeIcon(p.track, colour, modeView.selected === p.icao));
        layers.look = look;
    }
    layers.marker.setTooltipContent(escapeHtml(planeLabel(p)));
    layers.trail.setLatLngs(p.trail.map(t => [t[0], t[1]]));
    layers.trail.setStyle({color: colour});
}

function removePlane(icao) {
    const layers = planeLayers.get(icao);
    if (layers) { layers.marker.remove(); layers.trail.remove(); planeLayers.delete(icao); }
    modeView.planes.delete(icao);
    if (modeView.selected === icao) modeView.selected = null;
}

function clearPlanes() {
    for (const icao of Array.from(planeLayers.keys())) removePlane(icao);
    modeView.planes = new Map();
    modeView.selected = null;
}

function selectPlane(icao, pan) {
    const before = modeView.selected;
    modeView.selected = modeView.selected === icao ? null : icao;
    for (const id of [before, modeView.selected]) {
        const p = id && modeView.planes.get(id);
        if (p) drawPlane(p);
    }
    const p = modeView.planes.get(icao);
    if (pan && p && p.lat !== undefined && modeView.selected) modeMap.map.panTo([p.lat, p.lon]);
    renderModeBody();
}

function renderAircraft(body) {
    if (!body.querySelector('.mode-split')) {
        body.innerHTML = `<div class="mode-split">
            <div class="map-slot" id="map-slot"></div>
            <div class="mode-list">
                <div class="tabs inline-tabs list-tabs" id="aircraft-tabs" role="group" aria-label="Which aircraft"></div>
                <div class="mode-list-body" id="mode-list"></div>
            </div></div>`;
        buildChips(document.getElementById('aircraft-tabs'), [{key: 'live', label: 'In view'}, {key: 'history', label: 'History'}],
            modeView.list, (key) => { modeView.list = key; lsSet('aircraft_list', key); renderModeBody(); });
        ensureMap(document.getElementById('map-slot'));
        for (const p of modeView.planes.values()) drawPlane(p);
    }
    setMapHome(modeView.location);
    const tabs = document.getElementById('aircraft-tabs');
    const live = tabs.querySelector('[data-key="live"]');
    if (live) live.textContent = `In view · ${modeView.planes.size}`;
    document.getElementById('mode-list').innerHTML = modeView.list === 'history' ? aircraftHistoryHtml() : aircraftLiveHtml();
    renderModeHead();
}

function matchesSearch(p) {
    const search = modeView.search.toLowerCase();
    return !search || [p.flight, p.registration, p.type, p.icao, p.key, p.squawk].some(v => v && String(v).toLowerCase().includes(search));
}

function squawkHtml(squawk) {
    if (!squawk) return '';
    const emergency = {7500: 'hijack', 7600: 'radio failure', 7700: 'emergency'}[squawk];
    return emergency ? `<span class="badge warn" title="${emergency}">${escapeHtml(squawk)}</span>` : escapeHtml(squawk);
}

function aircraftLiveHtml() {
    let planes = Array.from(modeView.planes.values()).filter(matchesSearch);
    if (!planes.length) {
        return emptyHtml(modeView.search ? 'No aircraft match.' : 'No aircraft in view yet. With a 1090 MHz antenna (or the stock one high and outside) they come in within a minute.');
    }
    const hasDistance = planes.some(p => p.distance !== undefined);
    planes.sort((a, b) => hasDistance ? (a.distance ?? 1e9) - (b.distance ?? 1e9) : planeLabel(a).localeCompare(planeLabel(b)));
    return `<table class="data aircraft"><thead><tr><th>Flight</th><th>Altitude</th><th>Speed · track</th>${hasDistance ? '<th>Distance</th>' : ''}<th>Squawk</th><th>Seen</th></tr></thead><tbody>
        ${planes.map(p => `<tr class="link${modeView.selected === p.icao ? ' on' : ''}" onclick="selectPlane('${p.icao}', true)">
            <td><span class="plane-dot" style="background:${altitudeColour(p.ground ? 0 : p.altitude)}"></span><b>${escapeHtml(planeLabel(p))}</b>
                <small class="muted mono">${escapeHtml([p.registration && p.registration !== planeLabel(p) ? p.registration : null, p.type, p.icao.toUpperCase()].filter(Boolean).join(' · '))}</small></td>
            <td class="mono">${p.ground ? 'ground' : p.altitude !== undefined && p.altitude !== null ? `${num(p.altitude)} ft${p.climb > 300 ? ' ↑' : p.climb < -300 ? ' ↓' : ''}` : ''}</td>
            <td class="mono">${[p.speed !== undefined && p.speed !== null ? `${num(p.speed)} kt` : '', p.track !== undefined && p.track !== null ? `${num(p.track)}°` : ''].filter(Boolean).join(' · ')}</td>
            ${hasDistance ? `<td class="mono">${p.distance !== undefined ? `${num(p.distance, 0)} km` : ''}</td>` : ''}
            <td class="mono">${squawkHtml(p.squawk)}</td>
            <td class="mono" title="${p.lat === undefined ? 'No position heard yet' : `signal ${num(p.rssi, 0)} dBFS · ${num(p.messages)} messages`}">${fmtAgo(p.seen)}${p.lat === undefined ? ' · no position' : ''}</td>
        </tr>`).join('')}
    </tbody></table>`;
}

function aircraftHistoryHtml() {
    const items = modeView.items.filter(matchesSearch);
    if (!items.length) return emptyHtml(modeView.search ? 'No aircraft match.' : 'Aircraft are listed here when they have gone out of reach.');
    const altitudes = i => i.alt_min === null || i.alt_min === undefined ? '' : i.alt_min === i.alt_max ? `${num(i.alt_min)} ft` : `${num(i.alt_min)}–${num(i.alt_max)} ft`;
    return `<table class="data aircraft"><thead><tr><th>Heard</th><th>Flight</th><th>Altitude</th><th>Positions</th><th>Squawk</th></tr></thead><tbody>
        ${items.map(i => `<tr>
            <td class="mono">${i.first ? `${timeOf(i.first)} – ${fmtTime(i.seen)}` : timeOf(i.ts)}</td>
            <td><b>${escapeHtml(i.flight || i.registration || i.key.toUpperCase())}</b>
                <small class="muted mono">${escapeHtml([i.registration && i.registration !== i.flight ? i.registration : null, i.type, i.key.toUpperCase()].filter(Boolean).join(' · '))}</small></td>
            <td class="mono">${altitudes(i)}</td>
            <td class="mono">${num(i.positions || 0)}</td>
            <td class="mono">${squawkHtml(i.squawk)}</td>
        </tr>`).join('')}
    </tbody></table>`;
}

// ---------------------------------------------------------------- search and clear

function initModes() {
    const search = document.getElementById('mode-search');
    let timer = null;
    search.addEventListener('input', () => {
        modeView.search = search.value.trim();
        if (modeView.key === 'sensors' || (modeView.key === 'aircraft' && modeView.list === 'live')) return renderModeBody();
        clearTimeout(timer);
        timer = setTimeout(loadModeData, 300);
    });
    document.getElementById('mode-select').addEventListener('change', (e) => chooseMode(e.target.value));
    // Keep "heard … ago" current
    setInterval(() => { if (activeTab === 'radio' && modeView.key === 'sensors') renderModeBody(); }, 15000);
    setInterval(() => { if (activeTab === 'radio' && modeView.key === 'aircraft') renderModeBody(); }, 5000);
}

function clearModeData() {
    const key = modeView.key;
    const what = key === 'sensors' ? 'every reading of every device' : key === 'aircraft' ? 'the history of the aircraft' : 'every message';
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
