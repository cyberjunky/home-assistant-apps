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
// The dashboard's own car drawings, for when there is no photo: side views to scale (a model's real
// length, height, wheelbase and wheel size), facing left like Tesla's pictures.
const CAR_DRAWINGS = {
    model3: {
        body: 'M15 75.5C10 73 6.5 66 7.5 58C9 52 16 49 28 46C44 42 58 40 72 38C84 30 100 20 116 16.5C130 13.5 146 13.5 158 15.5C178 19 198 31 214 37C221 39 227 40 232 41C235 45 235.5 52 233.5 58C231 66 225 73 216 76L205.5 76A19.5 19.5 0 0 0 166.5 76L64.5 76A19.5 19.5 0 0 0 25.5 76L18 76C17 76 16 75.8 15 75.5Z',
        glass: 'M77 38.5C89 31 103 22 118 19C132 16.8 146 16.8 156 18.5C172 21.5 186 30 198 36C160 38 116 38.5 77 38.5Z',
        pillars: ['M127 17.4L125 38.5'],
        seams: ['M77 39L73 58C72 66 67 72 64.5 76', 'M125 39L127 76', 'M179 36.5C178 46 172 54 166.5 66'],
        handles: [[106, 44], [163, 43]], crease: 'M24 52C80 45 160 43 230 45',
        light: 'M9 55.5C16 51.5 26 48.5 36 46.6C29 49.6 19 52.8 10.5 56.6Z', tail: 'M220 39.5L233 42C234 44 234.6 46 234.8 48C229 47 223 44 220 39.5Z',
        mirror: [79, 37.5], wheels: [45, 186], cy: 67.5, r: 16.5,
    },
    modely: {
        body: 'M15 74.5C10 72 6.5 64 7.5 55C9 49 17 45 30 42C44 39 58 37 70 34C84 24 98 12 116 8.5C132 5.8 150 6 164 9C186 14 204 28 220 35C226 37 230 38 233 39C235.5 44 236 51 234 57C231.5 65 225 72 216 75L206 75A20 20 0 0 0 166 75L64 75A20 20 0 0 0 24 75L18 75C17 75 16 74.8 15 74.5Z',
        glass: 'M75 34.5C87 25 101 14.5 118 11.5C134 9 150 9.2 162 12C178 16 194 27 206 33.5C164 35.5 118 35 75 34.5Z',
        pillars: ['M128 10.2L126 35'],
        seams: ['M75 35L71 56C70 65 66 71 64 75', 'M126 35L128 75', 'M183 34C181 45 173 54 166 66'],
        handles: [[106, 41], [166, 40]], crease: 'M24 49C80 42 160 40 231 42',
        light: 'M9 52.5C16 48 26 44.6 36 42.6C29 46 19 49.4 10.5 53.6Z', tail: 'M222 35.5L233.5 39C234.6 41 235 43 235.2 45C230 44 225 40.5 222 35.5Z',
        mirror: [77, 33.5], wheels: [44, 186], cy: 67, r: 17,
    },
    models: {
        body: 'M14 76.5C9 74 5.5 67 6.5 59C8 54 15 51 28 48C46 44 62 42 76 40C90 31 106 21 122 18C138 15.5 152 15.5 166 17.5C186 21 204 32 220 38C226 40 231 41 234 42C235.5 46 236 53 234 59C231.5 67 226 74 218 77L209.5 77A19.5 19.5 0 0 0 170.5 77L63.5 77A19.5 19.5 0 0 0 24.5 77L17 77C16 77 15 76.8 14 76.5Z',
        glass: 'M81 40.5C94 32 108 23 124 20.5C138 18.4 152 18.4 164 20C178 22.6 192 30 204 37C164 39.8 122 40.5 81 40.5Z',
        pillars: ['M133 19.4L131 40.3'],
        seams: ['M81 41L76 60C75 68 67 73 63.5 77', 'M131 41L133 77', 'M188 35C186 46 178 55 170.5 67'],
        handles: [[112, 46], [170, 45]], crease: 'M24 54C80 47 160 45 232 47',
        light: 'M8 57C15 53 26 50 38 47.8C31 51 20 54.4 9.5 58.2Z', tail: 'M222 39.5L234 42.4C235 45 235.4 47 235.6 49C230 48 225 45 222 39.5Z',
        mirror: [83, 39.5], wheels: [44, 190], cy: 68, r: 16,
    },
    modelx: {
        body: 'M14 73.5C9 71 5.5 63 6.5 54C8 48 17 44 30 41C46 37.5 58 35.5 68 33C80 22 94 10 114 6.5C132 4 152 4.5 170 8C192 13 212 24 224 31C228 33 231 35 234 37C235.5 42 236 50 234.5 57C232 65 227 71 219 74L210 74A20 20 0 0 0 170 74L64 74A20 20 0 0 0 24 74L17 74C16 74 15 73.8 14 73.5Z',
        glass: 'M73 33.5C85 23 99 12.5 116 9.5C134 7 154 7.4 170 11C186 15 202 24 214 31C170 34 120 34 73 33.5Z',
        pillars: ['M124 8.6L122 33.8', 'M172 11.6L170 33.2'],
        seams: ['M73 34L69 55C68 64 66 70 64 74', 'M122 34L124 74', 'M186 32C184 44 177 54 170 65'],
        handles: [[102, 40], [160, 39]], crease: 'M24 48C80 41 160 39 232 41',
        light: 'M8 51.5C15 47 26 43.4 36 41.6C29 45 19 48.4 9.5 52.6Z', tail: 'M222 30.5L234 37C235 39 235.6 41 235.8 43C230 41.6 225 37 222 30.5Z',
        mirror: [75, 32.5], wheels: [44, 190], cy: 66.5, r: 17.5,
    },
    truck: {
        paint: '#c3c8cd',
        body: 'M6 48L96 9L236 33L236 62L230 72L210 72A20 20 0 0 0 170 72L64 72A20 20 0 0 0 24 72L12 72L6 62Z',
        glass: 'M56 31.5L96 14L154 23.5L154 37L56 37Z',
        pillars: ['M106 15.6L106 37'],
        seams: ['M56 37L56 56L64 72', 'M106 37L106 72', 'M154 37L154 64L170 72', 'M154 24L236 33'],
        handles: [], crease: 'M8 52L236 44',
        light: 'M7 47.6L40 33.2L40 35L8 49.4Z', tail: 'M236 34L236 36.5L156 36.5L156 34Z',
        mirror: null, wheels: [44, 190], cy: 67, r: 17,
    },
    roadster: {
        body: 'M8 74C5 70 5 65 8 61C14 56 34 52 56 48C72 44 84 33 104 27C122 23 140 23 156 27C176 32 200 42 222 46C228 47 232 48 234 50C235.5 54 235.5 60 233.5 65C231 71 226 76 217 78L206 78A19 19 0 0 0 168 78L63 78A19 19 0 0 0 25 78L14 78C11 78 9 76 8 74Z',
        glass: 'M76 45.5C86 38 98 32 110 29.5C124 27 138 27 150 30C162 33 172 38 180 42.5C146 45.4 110 45.8 76 45.5Z',
        pillars: ['M132 27.6L131 45'],
        seams: ['M76 46L72 62C71 70 66 75 63 78', 'M170 44C170 54 168 64 168 78'],
        handles: [[150, 50]], crease: 'M20 60C80 52 160 50 232 54',
        light: 'M8 60.5C15 56.5 28 53.5 40 51.6C33 54.6 22 58 9 61.8Z', tail: 'M222 46.5L234 50C235 52 235.6 54 235.8 56C230 54.6 225 51 222 46.5Z',
        mirror: [78, 45], wheels: [44, 187], cy: 68, r: 16, thrusters: true,
    },
};
let carDrawingCount = 0;

function carWheel(cx, cy, r) {
    const spokes = [0, 72, 144, 216, 288].map(deg => {
        const a = (deg - 90) * Math.PI / 180;
        return `<line x1="${cx}" y1="${cy}" x2="${(cx + Math.cos(a) * r * 0.62).toFixed(1)}" y2="${(cy + Math.sin(a) * r * 0.62).toFixed(1)}"/>`;
    }).join('');
    return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#121417"/>
        <circle cx="${cx}" cy="${cy}" r="${(r * 0.68).toFixed(1)}" fill="#23272c" stroke="#a7aeb5" stroke-width="1.1"/>
        <g stroke="#a7aeb5" stroke-width="${(r * 0.14).toFixed(1)}" stroke-linecap="round">${spokes}</g>
        <circle cx="${cx}" cy="${cy}" r="${(r * 0.16).toFixed(1)}" fill="#d3d8dc"/>`;
}

function carSilhouette(car) {
    const d = CAR_DRAWINGS[car && car.body_style] || CAR_DRAWINGS.model3;
    // Unknown paint (e.g. on a fresh sign-in, before Tesla reports it): a clean pearl white
    const paint = (car && car.color_hex) || d.paint || '#eef1f5';
    const n = ++carDrawingCount;
    const line = 'stroke="rgba(0,0,0,0.32)" stroke-width="0.6" fill="none"';
    const handles = d.handles.map(([x, y]) => `<rect x="${x}" y="${y}" width="11" height="1.5" rx="0.75" fill="rgba(0,0,0,0.3)"/>`).join('');
    const mirror = d.mirror ? `<ellipse cx="${d.mirror[0]}" cy="${d.mirror[1]}" rx="4" ry="2" fill="${paint}" stroke="rgba(0,0,0,0.4)" stroke-width="0.5"/>` : '';
    // The new Roadster's SpaceX package: rocket thrusters at the rear
    const thrusters = d.thrusters ? `<polygon points="236,56 268,59 236,62" fill="url(#car-flame-${n})"/><polygon points="236,64 262,66.5 236,69" fill="url(#car-flame-${n})"/>
        <rect x="232" y="55" width="6" height="8" rx="1.5" fill="#2c3035"/><rect x="232" y="63" width="6" height="7" rx="1.5" fill="#2c3035"/>` : '';
    return `<svg viewBox="0 0 ${d.thrusters ? 270 : 240} 90" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${escapeHtml((car && car.model_name) || 'car')}">
        <defs>
            <linearGradient id="car-shade-${n}" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stop-color="#fff" stop-opacity="0.34"/><stop offset="0.42" stop-color="#fff" stop-opacity="0.04"/>
                <stop offset="0.72" stop-color="#000" stop-opacity="0.06"/><stop offset="1" stop-color="#000" stop-opacity="0.38"/>
            </linearGradient>
            <linearGradient id="car-glass-${n}" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stop-color="#3d4852"/><stop offset="1" stop-color="#14191e"/>
            </linearGradient>
            <linearGradient id="car-flame-${n}" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stop-color="#fff4c2"/><stop offset="0.4" stop-color="#ffb52e"/><stop offset="1" stop-color="#ff7a18" stop-opacity="0"/>
            </linearGradient>
        </defs>
        <ellipse cx="120" cy="84.5" rx="114" ry="3.2" fill="#000" opacity="0.16"/>
        ${thrusters}
        <path d="${d.body}" fill="${paint}"/>
        <path d="${d.body}" fill="url(#car-shade-${n})" stroke="rgba(0,0,0,0.35)" stroke-width="0.5"/>
        <path d="${d.glass}" fill="url(#car-glass-${n})"/>
        ${d.pillars.map(p => `<path d="${p}" stroke="${paint}" stroke-width="2.2" fill="none"/>`).join('')}
        <path d="${d.crease}" stroke="rgba(255,255,255,0.28)" stroke-width="0.7" fill="none"/>
        <clipPath id="car-body-${n}"><path d="${d.body}"/></clipPath>
        <g clip-path="url(#car-body-${n})"><rect x="0" y="${d.cy + 1.5}" width="240" height="30" fill="#15181c" opacity="0.82"/></g>
        ${d.seams.map(p => `<path d="${p}" ${line}/>`).join('')}
        ${handles}${mirror}
        <path d="${d.light}" fill="#f4f7fa" stroke="rgba(0,0,0,0.4)" stroke-width="0.4"/>
        <path d="${d.tail}" fill="#b0141f"/>
        ${carWheel(d.wheels[0], d.cy, d.r)}${carWheel(d.wheels[1], d.cy, d.r)}
    </svg>`;
}

function carVisual(car, height) {
    const sil = carSilhouette(car);
    const h = height || 88;
    if (car.image_url) {
        // Tesla's picture, kept by the add-on; if it can't be had, the drawing in the car's paint
        return `<div class="car-visual" style="height:${h}px">
            <img class="car-photo" src="${basePath}api/cars/${car.id}/image?v=${escapeHtml(car.image_key || '')}" alt="${escapeHtml(car.model_name || 'Tesla')}" style="transform:scale(${car.image_scale || 1})" loading="lazy"
                 onerror="var v=this.closest('.car-visual'); this.remove(); v.classList.add('no-photo')">
            <div class="car-sil">${sil}</div></div>`;
    }
    return `<div class="car-visual no-photo" style="height:${h}px"><div class="car-sil">${sil}</div></div>`;
}

// "Model 3 · Standard Range · Deep Blue Metallic · 19" Sport" from a car's readable fields
function carSubtitle(car) {
    return [car.model_name || car.model, car.trim_name, car.color_name, car.wheels_name].filter(Boolean).join(' · ');
}
