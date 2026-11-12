// SDRadio dashboard - the modes: the radio set up for pagers, sensors and more, with a view of
// what they decode in place of the favourites and the stored channels.

const modeView = {key: null, items: [], keys: new Map(), search: '', loading: false, renderQueued: false,
    planes: new Map(), selected: null, list: lsGet('tracked_list', 'live'), messages: 0};
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
        : TRACKED[d.key] ? `${fmtMHz(s.frequency)} MHz · ${modeView.planes.size} in view · ${d.count || 0} heard since the mode started`
        : `${fmtMHz(s.frequency)} MHz · ${d.count || 0} decoded since the mode started`;
    if (status.textContent !== text) status.textContent = text;
    status.classList.toggle('warn-text', !!d.error);
    document.getElementById('mode-search').placeholder = key2placeholder(d.key);
}

function key2placeholder(key) {
    return {sensors: 'Search devices…', aircraft: 'Search callsign, registration, type…', ships: 'Search name, MMSI, destination…'}[key] || 'Search messages, capcodes…';
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
        } else if (TRACKED[key]) {
            const [live, history] = await Promise.all([api(`decoded/live?mode=${key}`),
                api(`decoded?${new URLSearchParams({mode: key, limit: 300, search: modeView.search})}`)]);
            if (modeView.key !== key) return;
            modeView.location = live.location;
            if (live.live) {
                modeView.messages = live.live.messages;
                for (const o of live.live[TRACKED[key].listKey]) applyTracked(o);
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
    if (m.mode !== modeView.key || !TRACKED[m.mode]) return;
    modeView.messages = m.messages;
    for (const o of m[TRACKED[m.mode].listKey] || []) applyTracked(o);
    for (const id of m.gone || []) removeTracked(String(id));
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
    if (TRACKED[modeView.key]) return renderTracked(body);
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

// ---------------------------------------------------------------- aircraft and ships on the map

// What differs between the modes that put things on the map
const TRACKED = {
    aircraft: {
        listKey: 'aircraft', noun: 'aircraft',
        id: p => p.icao,
        label: p => p.flight || p.registration || p.icao.toUpperCase(),
        colour: p => altitudeColour(p.ground ? 0 : p.altitude),
        icon: (p, colour, selected) => planeIcon(p.track, colour, selected),
        look: p => Math.round(p.track || 0),
        search: p => [p.flight, p.registration, p.type, p.icao, p.squawk],
        liveHtml: aircraftRows, historyHtml: aircraftHistoryRows,
        empty: 'No aircraft in view yet. With a 1090 MHz antenna (or the stock one high and outside) they come in within a minute.',
        gone: 'Aircraft are listed here when they have gone out of reach.',
    },
    ships: {
        listKey: 'ships', noun: 'ships',
        id: s => String(s.mmsi),
        label: s => s.name || s.callsign || String(s.mmsi),
        colour: s => shipColour(s),
        icon: (s, colour, selected) => shipIcon(s, colour, selected),
        look: s => `${Math.round(s.heading ?? s.course ?? 0)}|${(s.speed || 0) < 0.5}`,
        search: s => [s.name, s.callsign, s.mmsi, s.destination, s.kind],
        liveHtml: shipRows, historyHtml: shipHistoryRows,
        empty: 'No ships heard yet. Near a waterway or the coast they come in within a few minutes; inland ships send every few seconds while sailing.',
        gone: 'Ships are listed here when they have not been heard for a quarter of an hour.',
    },
};
const tracked = () => TRACKED[modeView.key];
const trackLayers = new Map();  // id → {marker, trail, look}

function applyTracked(o) {
    const cfg = tracked();
    const id = cfg.id(o);
    const known = modeView.planes.get(id);
    const thing = {...(known || {}), ...o};
    if (o.trail) thing.trail = o.trail;
    else {
        thing.trail = known ? known.trail || [] : [];
        if (o.point) {
            const last = thing.trail[thing.trail.length - 1];
            if (!last || last[0] !== o.point[0] || last[1] !== o.point[1]) thing.trail = [...thing.trail, o.point].slice(-400);
        }
    }
    delete thing.point;
    modeView.planes.set(id, thing);
    drawTracked(thing);
}

function drawTracked(o) {
    const map = modeMap.map, cfg = tracked();
    if (!map || !cfg || o.lat === undefined) return;
    const id = cfg.id(o);
    const colour = cfg.colour(o);
    const selected = modeView.selected === id;
    let layers = trackLayers.get(id);
    if (!layers) {
        layers = {
            marker: L.marker([o.lat, o.lon], {icon: cfg.icon(o, colour, false), keyboard: false})
                .bindTooltip('', {permanent: true, direction: 'right', offset: [10, 0], className: 'plane-label'})
                .on('click', () => selectTracked(id, false)).addTo(map),
            trail: L.polyline([], {weight: 2, opacity: 0.75, interactive: false}).addTo(map),
            look: '',
        };
        trackLayers.set(id, layers);
    }
    layers.marker.setLatLng([o.lat, o.lon]);
    const look = `${cfg.look(o)}|${colour}|${selected}`;
    if (layers.look !== look) {
        layers.marker.setIcon(cfg.icon(o, colour, selected));
        layers.look = look;
    }
    layers.marker.setTooltipContent(escapeHtml(cfg.label(o)));
    layers.trail.setLatLngs(o.trail.map(t => [t[0], t[1]]));
    layers.trail.setStyle({color: colour});
}

function removeTracked(id) {
    const layers = trackLayers.get(id);
    if (layers) { layers.marker.remove(); layers.trail.remove(); trackLayers.delete(id); }
    modeView.planes.delete(id);
    if (modeView.selected === id) modeView.selected = null;
}

function clearPlanes() {
    for (const id of Array.from(trackLayers.keys())) {
        const layers = trackLayers.get(id);
        layers.marker.remove(); layers.trail.remove();
    }
    trackLayers.clear();
    modeView.planes = new Map();
    modeView.selected = null;
}

function selectTracked(id, pan) {
    const before = modeView.selected;
    modeView.selected = modeView.selected === id ? null : id;
    for (const other of [before, modeView.selected]) {
        const o = other && modeView.planes.get(other);
        if (o) drawTracked(o);
    }
    const o = modeView.planes.get(id);
    if (pan && o && o.lat !== undefined && modeView.selected) modeMap.map.panTo([o.lat, o.lon]);
    renderModeBody();
}

function renderTracked(body) {
    const cfg = tracked();
    if (!body.querySelector('.mode-split')) {
        body.innerHTML = `<div class="mode-split">
            <div class="map-slot" id="map-slot"></div>
            <div class="mode-list">
                <div class="tabs inline-tabs list-tabs" id="tracked-tabs" role="group" aria-label="Which"></div>
                <div class="mode-list-body" id="mode-list"></div>
            </div></div>`;
        buildChips(document.getElementById('tracked-tabs'), [{key: 'live', label: 'In view'}, {key: 'history', label: 'History'}],
            modeView.list, (key) => { modeView.list = key; lsSet('tracked_list', key); renderModeBody(); });
        ensureMap(document.getElementById('map-slot'));
        for (const o of modeView.planes.values()) drawTracked(o);
    }
    setMapHome(modeView.location);
    const live = document.querySelector('#tracked-tabs [data-key="live"]');
    if (live) live.textContent = `In view · ${modeView.planes.size}`;
    let html;
    if (modeView.list === 'history') {
        const items = modeView.items.filter(matchesSearch);
        html = items.length ? cfg.historyHtml(items) : emptyHtml(modeView.search ? `No ${cfg.noun} match.` : cfg.gone);
    } else {
        const things = Array.from(modeView.planes.values()).filter(matchesSearch);
        html = things.length ? cfg.liveHtml(things) : emptyHtml(modeView.search ? `No ${cfg.noun} match.` : cfg.empty);
    }
    document.getElementById('mode-list').innerHTML = html;
    renderModeHead();
}

function matchesSearch(o) {
    const search = modeView.search.toLowerCase();
    const cfg = tracked();
    return !search || [...cfg.search(o), o.key].some(v => v !== null && v !== undefined && String(v).toLowerCase().includes(search));
}

const withDistance = things => things.some(o => o.distance !== undefined);
function sortByDistance(things, label) {
    const has = withDistance(things);
    return things.sort((a, b) => has ? (a.distance ?? 1e9) - (b.distance ?? 1e9) : label(a).localeCompare(label(b)));
}
const distanceCell = (o, has) => has ? `<td class="mono">${o.distance !== undefined ? `${num(o.distance, 0)} km` : ''}</td>` : '';
const speedCourse = (speed, course, unit) => [speed !== undefined && speed !== null ? `${num(speed, unit === 'kn' && speed < 20 ? 1 : 0)} ${unit}` : '',
    course !== undefined && course !== null ? `${num(course)}°` : ''].filter(Boolean).join(' · ');

// ---------------------------------------------------------------- aircraft

function squawkHtml(squawk) {
    if (!squawk) return '';
    const emergency = {7500: 'hijack', 7600: 'radio failure', 7700: 'emergency'}[squawk];
    return emergency ? `<span class="badge warn" title="${emergency}">${escapeHtml(squawk)}</span>` : escapeHtml(squawk);
}

function aircraftRows(planes) {
    const label = TRACKED.aircraft.label, has = withDistance(planes);
    sortByDistance(planes, label);
    return `<table class="data aircraft"><thead><tr><th>Flight</th><th>Altitude</th><th>Speed · track</th>${has ? '<th>Distance</th>' : ''}<th>Squawk</th><th>Seen</th></tr></thead><tbody>
        ${planes.map(p => `<tr class="link${modeView.selected === p.icao ? ' on' : ''}" onclick="selectTracked('${p.icao}', true)">
            <td><span class="plane-dot" style="background:${TRACKED.aircraft.colour(p)}"></span><b>${escapeHtml(label(p))}</b>
                <small class="muted mono">${escapeHtml([p.registration && p.registration !== label(p) ? p.registration : null, p.type, p.icao.toUpperCase()].filter(Boolean).join(' · '))}</small></td>
            <td class="mono">${p.ground ? 'ground' : p.altitude !== undefined && p.altitude !== null ? `${num(p.altitude)} ft${p.climb > 300 ? ' ↑' : p.climb < -300 ? ' ↓' : ''}` : ''}</td>
            <td class="mono">${speedCourse(p.speed, p.track, 'kt')}</td>
            ${distanceCell(p, has)}
            <td class="mono">${squawkHtml(p.squawk)}</td>
            <td class="mono" title="${p.lat === undefined ? 'No position heard yet' : `signal ${num(p.rssi, 0)} dBFS · ${num(p.messages)} messages`}">${fmtAgo(p.seen)}${p.lat === undefined ? ' · no position' : ''}</td>
        </tr>`).join('')}
    </tbody></table>`;
}

function aircraftHistoryRows(items) {
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

// ---------------------------------------------------------------- ships

function shipRows(ships) {
    const label = TRACKED.ships.label, has = withDistance(ships);
    sortByDistance(ships, label);
    return `<table class="data aircraft"><thead><tr><th>Ship</th><th>Speed · course</th><th>Destination</th>${has ? '<th>Distance</th>' : ''}<th>Seen</th></tr></thead><tbody>
        ${ships.map(s => `<tr class="link${modeView.selected === String(s.mmsi) ? ' on' : ''}" onclick="selectTracked('${s.mmsi}', true)">
            <td><span class="plane-dot" style="background:${shipColour(s)}"></span><b>${escapeHtml(label(s))}</b>
                <small class="muted mono">${escapeHtml([s.kind || (s.base_station ? 'base station' : s.aid !== undefined ? 'aid to navigation' : null), s.callsign, s.length ? `${s.length} m` : null, s.mmsi].filter(Boolean).join(' · '))}</small></td>
            <td class="mono">${(s.speed || 0) < 0.5 && s.status ? escapeHtml(s.status) : speedCourse(s.speed, s.course, 'kn')}</td>
            <td>${escapeHtml(s.destination || '')}</td>
            ${distanceCell(s, has)}
            <td class="mono" title="${s.lat === undefined ? 'No position heard yet' : `${num(s.messages)} messages${s.class === 'B' ? ' · class B' : ''}`}">${fmtAgo(s.seen)}${s.lat === undefined ? ' · no position' : ''}</td>
        </tr>`).join('')}
    </tbody></table>`;
}

function shipHistoryRows(items) {
    return `<table class="data aircraft"><thead><tr><th>Heard</th><th>Ship</th><th>Destination</th><th>Positions</th></tr></thead><tbody>
        ${items.map(i => `<tr>
            <td class="mono">${i.first ? `${timeOf(i.first)} – ${fmtTime(i.seen)}` : timeOf(i.ts)}</td>
            <td><b>${escapeHtml(i.name || i.callsign || i.key)}</b>
                <small class="muted mono">${escapeHtml([i.kind, i.callsign, i.key].filter(Boolean).join(' · '))}</small></td>
            <td>${escapeHtml(i.destination || '')}</td>
            <td class="mono">${num(i.positions || 0)}</td>
        </tr>`).join('')}
    </tbody></table>`;
}

// ---------------------------------------------------------------- search and clear

function initModes() {
    const search = document.getElementById('mode-search');
    let timer = null;
    search.addEventListener('input', () => {
        modeView.search = search.value.trim();
        if (modeView.key === 'sensors' || (TRACKED[modeView.key] && modeView.list === 'live')) return renderModeBody();
        clearTimeout(timer);
        timer = setTimeout(loadModeData, 300);
    });
    document.getElementById('mode-select').addEventListener('change', (e) => chooseMode(e.target.value));
    // Keep "heard … ago" current
    setInterval(() => { if (activeTab === 'radio' && modeView.key === 'sensors') renderModeBody(); }, 15000);
    setInterval(() => { if (activeTab === 'radio' && TRACKED[modeView.key]) renderModeBody(); }, 5000);
}

function clearModeData() {
    const key = modeView.key;
    const what = key === 'sensors' ? 'every reading of every device' : TRACKED[key] ? `the history of the ${TRACKED[key].noun}` : 'every message';
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
