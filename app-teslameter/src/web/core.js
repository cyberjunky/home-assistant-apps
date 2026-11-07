// TeslaMeter dashboard - shared state, formatting, theme, map, modals and the live stream.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const LS = 'teslameter_';

// What the server told us; filled by loadStatus() and kept fresh by the live stream
const app = {
    version: '', units: 'metric', currency: 'EUR', auth: {signed_in: false}, cars: [], carId: null,
    mqtt: {}, accountError: '', status: null,
};

function currentCar() {
    return app.cars.find(c => c.id === app.carId) || app.cars[0] || null;
}

async function api(path, options) {
    const opts = options || {};
    if (opts.body && typeof opts.body !== 'string') {
        opts.body = JSON.stringify(opts.body);
        opts.headers = {'Content-Type': 'application/json'};
    }
    const response = await fetch(basePath + 'api/' + path, opts);
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
    return data;
}

// =====================================================================
// Units and formatting: the server speaks metric, the options pick the display
// =====================================================================
const KM_PER_MILE = 1.609344;
const imperial = () => app.units === 'imperial';
const unit = {
    dist: () => imperial() ? 'mi' : 'km',
    speed: () => imperial() ? 'mph' : 'km/h',
    cons: () => imperial() ? 'Wh/mi' : 'Wh/km',
    temp: () => imperial() ? '°F' : '°C',
    press: () => imperial() ? 'psi' : 'bar',
    elev: () => imperial() ? 'ft' : 'm',
};
const conv = {
    dist: km => imperial() ? km / KM_PER_MILE : km,
    cons: whkm => imperial() ? whkm * KM_PER_MILE : whkm,
    temp: c => imperial() ? c * 9 / 5 + 32 : c,
    press: bar => imperial() ? bar * 14.5038 : bar,
    elev: m => imperial() ? m * 3.28084 : m,
};
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(value, digits) {
    if (missing(value)) return '–';
    return Number(value).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
// Value with its unit as HTML: the unit sits small behind the number
function withUnit(text, unitText) { return text === '–' ? '–' : `${text}<small> ${unitText}</small>`; }
const fmtDist = (km, digits) => missing(km) ? '–' : `${num(conv.dist(km), digits === undefined ? 1 : digits)} ${unit.dist()}`;
const fmtSpeed = kmh => missing(kmh) ? '–' : `${num(conv.dist(kmh))} ${unit.speed()}`;
const fmtCons = whkm => missing(whkm) ? '–' : `${num(conv.cons(whkm))} ${unit.cons()}`;
const fmtTemp = c => missing(c) ? '–' : `${num(conv.temp(c), 1)} ${unit.temp()}`;
const fmtKwh = (kwh, digits) => missing(kwh) ? '–' : `${num(kwh, digits === undefined ? 1 : digits)} kWh`;
const fmtPct = v => missing(v) ? '–' : `${num(v)}%`;

function fmtMoney(value, currency) {
    if (missing(value)) return '–';
    try {
        return new Intl.NumberFormat(undefined, {style: 'currency', currency: currency || app.currency}).format(value);
    } catch (e) {
        return `${num(value, 2)} ${currency || app.currency}`;
    }
}

function fmtDuration(seconds) {
    if (missing(seconds)) return '–';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const fmtDay = ts => new Date(ts * 1000).toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'});
// Short date that adds the year only when it isn't the current year (for lists spanning years)
function fmtDayY(ts) {
    const d = new Date(ts * 1000);
    const opts = {day: 'numeric', month: 'short'};
    if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString([], opts);
}
const fmtDate = ts => new Date(ts * 1000).toLocaleDateString([], {day: 'numeric', month: 'short', year: 'numeric'});
const fmtDateTime = ts => `${fmtDay(ts)} ${fmtTime(ts)}`;

function fmtAgo(ts) {
    if (!ts) return 'never';
    const seconds = Math.max(0, Date.now() / 1000 - ts);
    if (seconds < 10) return 'just now';
    if (seconds < 90) return `${Math.round(seconds)} s ago`;
    if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} days ago`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : String(text);
    return div.innerHTML;
}

const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// A place name, or its coordinates when it has no name (yet)
function placeName(name, lat, lon) {
    if (name) return name;
    if (missing(lat) || missing(lon)) return 'Unknown place';
    return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}

// =====================================================================
// Theme: follows Home Assistant's dark mode (this page runs in its ingress
// iframe, same origin), the system outside Home Assistant, or a manual choice
// =====================================================================
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
// "?theme=dark" (or light) in the address wins, e.g. for a wall display
let themeChoice = new URLSearchParams(window.location.search).get('theme') || localStorage.getItem(LS + 'theme');
if (!THEME_ICONS[themeChoice]) themeChoice = 'auto';

function haDarkMode() {
    try {
        const ha = window.parent !== window && window.parent.document.querySelector('home-assistant');
        return ha && ha.hass && ha.hass.themes ? Boolean(ha.hass.themes.darkMode) : null;
    } catch (e) {
        return null;  // Not inside Home Assistant's frontend
    }
}

function applyTheme() {
    let dark = themeChoice === 'dark';
    if (themeChoice === 'auto') {
        const ha = haDarkMode();
        dark = ha === null ? window.matchMedia('(prefers-color-scheme: dark)').matches : ha;
    }
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

function initThemeToggle() {
    document.getElementById('theme-icon').textContent = THEME_ICONS[themeChoice];
    document.getElementById('theme-toggle').title = themeChoice === 'auto'
        ? 'Theme follows Home Assistant - click for light'
        : `Theme: ${themeChoice} - click to change`;
}

function cycleTheme() {
    themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
    localStorage.setItem(LS + 'theme', themeChoice);
    initThemeToggle();
    applyTheme();
}

applyTheme();
// Home Assistant doesn't tell the iframe when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// =====================================================================
// Toasts & modals
// =====================================================================
function toast(message, type) {
    const root = document.getElementById('toast-root');
    const el = document.createElement('div');
    el.className = 'toast' + (type ? ' ' + type : '');
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; }, 3600);
    setTimeout(() => el.remove(), 4000);
}

function openModal(opts) {
    closeModal();
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    overlay.innerHTML = `
        <div class="modal" role="dialog" aria-modal="true">
            <div class="modal-head">
                <span class="modal-title">${escapeHtml(opts.title || '')}</span>
                <button class="modal-close" data-close aria-label="Close">✕</button>
            </div>
            <div class="modal-body">${opts.bodyHTML || ''}</div>
            <div class="modal-foot">${opts.footHTML || ''}</div>
        </div>`;
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.hasAttribute('data-close')) closeModal();
    });
    document.getElementById('modal-root').appendChild(overlay);
    return overlay;
}

function closeModal() {
    document.getElementById('modal-root').innerHTML = '';
}

// opts.typeToConfirm: a word the user must type before the confirm button works (for what can't be undone)
function confirmDialog(opts) {
    return new Promise((resolve) => {
        const word = opts.typeToConfirm || '';
        const overlay = openModal({
            title: opts.title || 'Are you sure?',
            bodyHTML: `<div>${opts.message || ''}</div>` + (word ? `
                <div class="form-field"><label>Type <span class="mono">${escapeHtml(word)}</span> to confirm</label>
                <input class="input" data-confirm-word autocomplete="off" spellcheck="false" aria-label="Type ${escapeHtml(word)} to confirm"></div>` : ''),
            footHTML: `
                <button class="btn" data-close>Cancel</button>
                <button class="btn ${opts.danger ? 'btn-danger-solid' : 'btn-primary'}" data-confirm>${escapeHtml(opts.confirmLabel || 'Confirm')}</button>`
        });
        const button = overlay.querySelector('[data-confirm]');
        if (word) {
            const input = overlay.querySelector('[data-confirm-word]');
            button.disabled = true;
            input.addEventListener('input', () => { button.disabled = input.value.trim() !== word; });
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !button.disabled) button.click(); });
            input.focus();
        }
        button.addEventListener('click', () => { closeModal(); resolve(true); });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.hasAttribute('data-close')) resolve(false);
        });
    });
}

document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// =====================================================================
// Map: one Leaflet map, moved into the active view's slot. Every view draws
// into its own layer group; only the active view's group is on the map.
// =====================================================================
const MAP_VIEWS = ['dashboard', 'trips', 'charging'];
let savedView = null;
try { savedView = JSON.parse(localStorage.getItem(LS + 'map_view')); } catch (e) { /* first visit */ }
const map = L.map('map', {zoomControl: true}).setView(savedView ? [savedView.lat, savedView.lng] : [52.2, 5.3], savedView ? savedView.zoom : 8);
map.on('moveend', () => {
    const c = map.getCenter();
    localStorage.setItem(LS + 'map_view', JSON.stringify({lat: c.lat, lng: c.lng, zoom: map.getZoom()}));
});

// Tiles come through the add-on (and Home Assistant's built-in map tile
// proxy); OSM blocks browsers requesting them directly from here.
function tileLayer() {
    return L.tileLayer(basePath + 'api/map-tiles/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
        maxZoom: 19
    });
}
tileLayer().addTo(map);

const mapLayers = {};
MAP_VIEWS.forEach(view => { mapLayers[view] = L.layerGroup(); });

// A heatmap of where the car has been, toggled by the "Heatmap" control on the map.
let heatLayer = null;
async function loadMapHeat() {
    const select = document.getElementById('map-heat');
    const value = select ? select.value : '';
    const car = currentCar();
    if (value === '' || !car) {
        if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
        return;
    }
    select.disabled = true;
    try {
        const data = await api(`cars/${car.id}/heat?days=${value}`);
        const points = (data.points || []).map(p => [p[0], p[1], p[2]]);
        if (heatLayer) {
            heatLayer.setLatLngs(points);
        } else {
            heatLayer = L.heatLayer(points, {radius: 24, blur: 20, maxZoom: 14, minOpacity: 0.25}).addTo(map);
        }
        if (!points.length) toast('No location history in this period');
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        select.disabled = false;
    }
}

function pinIcon(label, kind) {
    return L.divIcon({className: '', html: `<div class="pin ${kind || ''}">${escapeHtml(label)}</div>`, iconSize: [22, 22], iconAnchor: [11, 11]});
}

// Speed bands of a track on the map (km/h): one hue, light to dark
const SPEED_BANDS = [30, 60, 90, 120];
const speedBand = kmh => { let i = 0; while (i < SPEED_BANDS.length && kmh >= SPEED_BANDS[i]) i++; return i + 1; };

// Draw a track coloured by speed into a layer group. points: [[lat, lon, speedKmh], ...]
function drawSpeedTrack(group, points) {
    const line = points.map(p => [p[0], p[1]]);
    // A surface-coloured casing keeps the track readable on any map background
    L.polyline(line, {weight: 8, className: 'trk-casing', interactive: false}).addTo(group);
    let segment = [line[0]];
    let band = null;
    const flush = () => {
        if (segment.length > 1) L.polyline(segment, {weight: 4, className: 'trk-speed trk-s' + band, interactive: false}).addTo(group);
    };
    for (let i = 1; i < points.length; i++) {
        const next = speedBand(points[i][2] || 0);
        if (band !== null && next !== band) {
            flush();
            segment = [line[i - 1]];
        }
        band = next;
        segment.push(line[i]);
    }
    flush();
    return L.latLngBounds(line);
}

function showSpeedLegend(visible) {
    const legend = document.getElementById('map-legend');
    legend.hidden = !visible;
    if (visible) {
        legend.innerHTML = `<span>0</span><span class="ramp"></span><span>${num(conv.dist(120))}+ ${unit.speed()}</span>`;
    }
}

// =====================================================================
// Views
// =====================================================================
const TABS = ['dashboard', 'trips', 'charging', 'stats', 'settings'];
const tabHooks = {};
let activeTab = null;

function onTab(name, fn) { tabHooks[name] = fn; }

function switchTab(tabName) {
    if (!TABS.includes(tabName)) tabName = 'dashboard';
    activeTab = tabName;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    localStorage.setItem(LS + 'tab', tabName);
    if (window.location.hash.slice(1).split('/')[0] !== tabName) history.replaceState(null, '', '#' + tabName);
    hideTip();
    if (MAP_VIEWS.includes(tabName)) {
        document.getElementById('slot-' + tabName).appendChild(document.getElementById('map-root'));
        MAP_VIEWS.forEach(view => {
            if (view === tabName) mapLayers[view].addTo(map); else map.removeLayer(mapLayers[view]);
        });
        showSpeedLegend(false);
        map.invalidateSize();
        setTimeout(() => map.invalidateSize(), 60);
    }
    if (tabHooks[tabName]) tabHooks[tabName]();
}

// Draggable dividers: each sets a CSS size variable, kept per browser;
// double-click restores the default
function initSplitters() {
    const root = document.documentElement.style;
    document.querySelectorAll('.splitter').forEach(handle => {
        const name = handle.dataset.size;
        const saved = localStorage.getItem(LS + 'size' + name);
        if (saved) root.setProperty(name, saved);

        handle.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            const pane = document.getElementById(handle.dataset.pane);
            const row = handle.classList.contains('row');
            const dir = Number(handle.dataset.dir);
            const start = row ? e.clientY : e.clientX;
            const startSize = row ? pane.offsetHeight : pane.offsetWidth;
            const room = row ? pane.parentElement.offsetHeight : pane.parentElement.offsetWidth;
            handle.setPointerCapture(e.pointerId);
            handle.classList.add('dragging');
            const move = (ev) => {
                const size = startSize + dir * ((row ? ev.clientY : ev.clientX) - start);
                root.setProperty(name, Math.round(Math.max(200, Math.min(room - 200, size))) + 'px');
                map.invalidateSize();
            };
            const up = () => {
                handle.classList.remove('dragging');
                handle.removeEventListener('pointermove', move);
                handle.removeEventListener('pointerup', up);
                localStorage.setItem(LS + 'size' + name, root.getPropertyValue(name));
                window.dispatchEvent(new Event('resize'));
            };
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', up);
        });

        handle.addEventListener('dblclick', () => {
            root.removeProperty(name);
            localStorage.removeItem(LS + 'size' + name);
            map.invalidateSize();
            window.dispatchEvent(new Event('resize'));
        });
    });
}

// =====================================================================
// Period chips: the same presets on Trips, Charging and Stats
// =====================================================================
const RANGES = [
    {key: '7d', label: '7 days', days: 7},
    {key: '30d', label: '30 days', days: 30},
    {key: '90d', label: '90 days', days: 90},
    {key: '12m', label: '12 months', days: 365},
    {key: 'year', label: 'This year'},
    {key: 'all', label: 'All'},
];

// {from, to} in unix seconds for a preset or a specific year ("y2024"); whole days
function rangeBounds(key) {
    const now = new Date();
    const to = Math.floor(now.getTime() / 1000) + 60;
    const year = /^y(\d{4})$/.exec(key || '');
    if (year) {
        const y = Number(year[1]);
        return {from: Math.floor(new Date(y, 0, 1).getTime() / 1000), to: Math.floor(new Date(y + 1, 0, 1).getTime() / 1000)};
    }
    const preset = RANGES.find(r => r.key === key) || RANGES[1];
    if (preset.key === 'all') return {from: 0, to};
    if (preset.key === 'year') return {from: Math.floor(new Date(now.getFullYear(), 0, 1).getTime() / 1000), to};
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (preset.days - 1));
    return {from: Math.floor(start.getTime() / 1000), to};
}

function buildChips(containerId, options, current, onChange) {
    const el = document.getElementById(containerId);
    el.innerHTML = options.map(o =>
        `<button type="button" class="tab chip${o.key === current ? ' active' : ''}" data-key="${o.key}">${escapeHtml(o.label)}</button>`
    ).join('');
    el.onclick = (e) => {
        const button = e.target.closest('[data-key]');
        if (!button) return;
        el.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === button));
        onChange(button.dataset.key);
    };
}

// Years that have data (newest first), from the recorded span
function availableYears() {
    const now = new Date().getFullYear();
    let first = now;
    const span = app.status && app.status.data_span;
    if (span && span.first_ts) first = new Date(span.first_ts * 1000).getFullYear();
    const years = [];
    for (let y = now; y >= Math.min(first, now); y--) years.push(y);
    return years;
}

// A year dropdown beside the period chips; picking a year sets the range to that calendar year
function buildYearSelect(selectId, current, onChange) {
    const el = document.getElementById(selectId);
    if (!el) return;
    const match = /^y(\d{4})$/.exec(current || '');
    el.innerHTML = '<option value="">Year…</option>'
        + availableYears().map(y => `<option value="y${y}"${match && Number(match[1]) === y ? ' selected' : ''}>${y}</option>`).join('');
    el.onchange = () => { if (el.value) onChange(el.value); };
}

// =====================================================================
// Live state: status once, then server-sent events
// =====================================================================
const liveListeners = [];
function onLive(fn) { liveListeners.push(fn); }

function applyLive(data) {
    app.auth = data.auth || {};
    app.cars = data.cars || [];
    app.mqtt = data.mqtt || {};
    app.accountError = data.account_error || '';
    if (!app.cars.some(c => c.id === app.carId)) {
        const saved = Number(localStorage.getItem(LS + 'car'));
        app.carId = (app.cars.find(c => c.id === saved) || app.cars[0] || {id: null}).id;
    }
    renderChrome();
    liveListeners.forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
}

async function loadStatus() {
    const status = await api('status');
    app.status = status;
    app.version = status.version;
    app.units = status.units;
    app.currency = status.currency;
    applyLive(status);
}

let evtSource = null;
let streamOk = false;
function connectStream() {
    if (evtSource) evtSource.close();
    evtSource = new EventSource(basePath + 'api/stream');
    evtSource.onmessage = (e) => { streamOk = true; applyLive(JSON.parse(e.data)); };
    evtSource.addEventListener('ping', () => { streamOk = true; });
    evtSource.onerror = () => {
        streamOk = false;
        renderStatusbar();
        // The browser retries by itself unless the connection was closed for good
        if (evtSource.readyState === EventSource.CLOSED) setTimeout(connectStream, 5000);
    };
}

function selectCar(id) {
    app.carId = id;
    localStorage.setItem(LS + 'car', id);
    renderChrome();
    liveListeners.forEach(fn => fn());
    if (tabHooks[activeTab]) tabHooks[activeTab](true);
}

// Top bar and status bar
function renderChrome() {
    const car = currentCar();
    document.getElementById('brand-car').textContent = car ? (car.name || car.vin || '') : '';
    const select = document.getElementById('car-select');
    select.hidden = app.cars.length < 2;
    if (!select.hidden) {
        select.innerHTML = app.cars.map(c => `<option value="${c.id}"${c.id === app.carId ? ' selected' : ''}>${escapeHtml(c.name || c.vin)}</option>`).join('');
    }
    renderStatusbar();
}

function renderStatusbar() {
    const car = currentCar();
    const parts = [];
    if (!streamOk) parts.push('<span class="sb-bad">● dashboard disconnected</span>');
    if (!app.auth.signed_in) {
        parts.push('<span class="sb-warn">● not signed in to Tesla</span>');
    } else if (car) {
        parts.push(`<span><span class="${car.error ? 'sb-warn' : 'sb-ok'}">●</span> <b>${escapeHtml(car.name || car.vin || 'car')}</b> ${escapeHtml(car.state)}</span>`);
        parts.push(`<span>seen <b>${fmtAgo(car.last_seen)}</b></span>`);
        if (car.streaming) parts.push('<span class="sb-extra">live stream</span>');
        if (car.error) parts.push(`<span class="sb-warn sb-extra">${escapeHtml(car.error)}</span>`);
    } else {
        parts.push(app.accountError ? `<span class="sb-warn">${escapeHtml(app.accountError)}</span>` : '<span>looking for your cars…</span>');
    }
    if (app.mqtt.enabled) {
        parts.push(`<span class="sb-extra">MQTT <b class="${app.mqtt.connected ? 'sb-ok' : 'sb-bad'}">${app.mqtt.connected ? 'connected' : 'offline'}</b></span>`);
    }
    parts.push(`<span class="spacer"></span><span class="sb-extra">TeslaMeter ${escapeHtml(app.version)}</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

// =====================================================================
// Car picture: Tesla's own render when the paint is known, otherwise a
// side-view silhouette tinted to the real paint colour.
// =====================================================================
// Side-view shapes per body style, in a 0..100 x 0..40 box. [body], [glass], [pillars], wheels.
const CAR_SHAPES = {
    sedan: {
        body: [[3,33],[1,24.5],[2.4,15],[9,10],[26,4],[50,1],[70,4],[88,12],[96,16.5],[99,23],[99,31],[95,33]],
        glass: [[15,11],[24,5.5],[40,2.4],[56,2.6],[68,6],[76,12]],
        pillars: [[[49,1.2],[50,13]]], wheels: [22,78], wr: 8, hub: 2.8,
    },
    suv: {
        body: [[3,33],[1,23],[2,12],[7,8],[18,5.5],[30,3.5],[52,2.2],[66,3],[82,8],[93,12],[98,16],[99,22],[99,31],[95,33]],
        glass: [[15,10],[23,5.5],[38,3.6],[55,3.4],[70,5],[82,10.5]],
        pillars: [[[38,3.6],[39,12]],[[55,3.4],[56,12]]], wheels: [22,78], wr: 8.6, hub: 3,
    },
    truck: {
        body: [[2,33],[2,22],[44,9],[99,15],[99,31],[95,33]],
        glass: [[30,13.5],[44,10.2],[70,11.8],[70,16],[30,16]],
        pillars: [], wheels: [22,80], wr: 8.4, hub: 3,
    },
    roadster: {
        body: [[3,32],[1.5,26],[4,20],[16,16],[34,8],[52,5],[70,7],[86,14],[96,18],[99,24],[99,30.5],[96,32]],
        glass: [[30,10.5],[42,7],[56,6.5],[66,10]],
        pillars: [], wheels: [22,78], wr: 8.2, hub: 3,
    },
};

// Chaikin corner-cutting, to round a polygon's corners (same smoothing as the icon)
function chaikin(points, rounds) {
    for (let r = 0; r < (rounds || 3); r++) {
        const out = [];
        for (let i = 0; i < points.length; i++) {
            const a = points[i], b = points[(i + 1) % points.length];
            out.push([a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25]);
            out.push([a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75]);
        }
        points = out;
    }
    return points;
}
const pointsStr = (pts) => pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');

function carSilhouette(car) {
    // Unknown paint (e.g. on a fresh sign-in, before Tesla reports it): a clean
    // pearl white, the standard Model 3 colour, rather than a dull grey.
    const paint = (car && car.color_hex) || '#eef1f5';
    const style = (car && car.body_style);
    const shape = CAR_SHAPES[style] || CAR_SHAPES.sedan;
    const body = pointsStr(chaikin(shape.body, 3));
    const glass = pointsStr(chaikin(shape.glass, 2));
    const pillars = shape.pillars.map(([a, b]) => `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${paint}" stroke-width="1.2"/>`).join('');
    const wheel = (cx) => `<circle cx="${cx}" cy="30" r="${shape.wr}" fill="#15171a"/><circle cx="${cx}" cy="30" r="${shape.hub}" fill="#3a3f45"/>`;
    // The new Roadster's SpaceX package puts rocket thrusters at the rear (the car faces right).
    const roadster = style === 'roadster';
    const view = roadster ? '-20 -1 122 42' : '-1 -1 102 42';
    const nozzle = (cy) => `<rect x="-1.5" y="${cy - 2}" width="4.5" height="4" rx="1.2" fill="#2c3035"/>`;
    const flame = (cy) => `<polygon points="1,${cy - 2.4} -18,${cy} 1,${cy + 2.4}" fill="url(#thrust)"/>`
        + `<polygon points="1,${cy - 1.3} -10,${cy} 1,${cy + 1.3}" fill="#fff6d8"/>`;
    const thrusters = roadster ? `<defs><linearGradient id="thrust" gradientUnits="userSpaceOnUse" x1="2" y1="0" x2="-18" y2="0">
            <stop offset="0" stop-color="#fff4c2"/><stop offset="0.35" stop-color="#ffd34d"/>
            <stop offset="0.7" stop-color="#ff7a18"/><stop offset="1" stop-color="#ff7a18" stop-opacity="0"/></linearGradient></defs>
        ${flame(22.5)}${flame(28)}${nozzle(22.5)}${nozzle(28)}` : '';
    return `<svg viewBox="${view}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${escapeHtml((car && car.model_name) || 'car')}">
        ${thrusters}<polygon points="${body}" fill="${paint}" stroke="rgba(0,0,0,0.2)" stroke-width="0.5"/>
        <polygon points="${glass}" fill="rgba(255,255,255,0.32)" stroke="rgba(0,0,0,0.14)" stroke-width="0.4"/>
        ${pillars}${wheel(shape.wheels[0])}${wheel(shape.wheels[1])}</svg>`;
}

function carVisual(car, height) {
    const sil = carSilhouette(car);
    const h = height || 88;
    if (car.image_url) {
        // Tesla's render; if it fails to load, fall back to the tinted silhouette
        return `<div class="car-visual" style="height:${h}px">
            <img class="car-photo" src="${escapeHtml(car.image_url)}" alt="${escapeHtml(car.model_name || 'Tesla')}" loading="lazy"
                 onerror="var v=this.closest('.car-visual'); this.remove(); v.classList.add('no-photo')">
            <div class="car-sil">${sil}</div></div>`;
    }
    return `<div class="car-visual no-photo" style="height:${h}px"><div class="car-sil">${sil}</div></div>`;
}

// "Model 3 · Standard Range · Deep Blue Metallic · 19" Sport" from a car's readable fields
function carSubtitle(car) {
    return [car.model_name || car.model, car.trim_name, car.color_name, car.wheels_name].filter(Boolean).join(' · ');
}
