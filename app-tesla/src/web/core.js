// Tesla dashboard - shared state, formatting, theme, map, modals and the live stream.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const LS = 'tesla_';

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

// Tiles come through the app (and Home Assistant's built-in map tile
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
    parts.push(`<span class="spacer"></span><span class="sb-extra">Tesla ${escapeHtml(app.version)} · unofficial</span>`);
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
        body: 'M15 75.5C10 73 6.5 66 7.5 58C9 52.7 16 50.3 28 47.8C44 44.5 58 42.9 72 41.2C84 34.7 100 26.5 116 23.6C130 21.1 146 21.1 158 22.8C178 25.7 198 35.5 214 40.4C221 42.1 227 42.9 232 43.7C235 47 235.5 52.7 233.5 58C231 66 225 73 216 76L205.5 76A19.5 19.5 0 0 0 166.5 76L64.5 76A19.5 19.5 0 0 0 25.5 76L18 76C17 76 16 75.8 15 75.5Z',
        glass: 'M77 41.6C89 35.5 103 28.1 118 25.7C132 23.9 146 23.9 156 25.3C172 27.7 186 34.7 198 39.6C160 41.2 116 41.6 77 41.6Z',
        pillars: ['M127 24.3L125 41.6'],
        seams: ['M77 42.1L73 58C72 66 67 72 64.5 76', 'M125 42.1L127 76', 'M179 40C178 47.8 172 54.4 166.5 66'],
        handles: [[106, 46.2], [163, 45.3]], crease: 'M24 52.7C80 47 160 45.3 230 47',
        light: 'M9 55.6C16 52.3 26 49.9 36 48.3C29 50.8 19 53.4 10.5 56.6Z', tail: 'M220 42.5L233 44.5C234 46.2 234.6 47.8 234.8 49.4C229 48.6 223 46.2 220 42.5Z',
        mirror: [79, 40.8], wheels: [45, 186], cy: 67.5, r: 16.5,
    },
    modely: {
        body: 'M15 74.5C10 72 6.5 64 7.5 55.2C9 50.3 17 47 30 44.5C44 42.1 58 40.4 70 38C84 29.8 98 19.9 116 17.1C132 14.8 150 15 164 17.5C186 21.6 204 33 220 38.8C226 40.4 230 41.2 233 42.1C235.5 46.2 236 51.9 234 57C231.5 65 225 72 216 75L206 75A20 20 0 0 0 166 75L64 75A20 20 0 0 0 24 75L18 75C17 75 16 74.8 15 74.5Z',
        glass: 'M75 38.4C87 30.6 101 22 118 19.5C134 17.5 150 17.6 162 19.9C178 23.2 194 32.2 206 37.5C164 39.2 118 38.8 75 38.4Z',
        pillars: ['M128 18.4L126 38.8'],
        seams: ['M75 38.8L71 56C70 65 66 71 64 75', 'M126 38.8L128 75', 'M183 38C181 47 173 54.4 166 66'],
        handles: [[106, 43.7], [166, 42.9]], crease: 'M24 50.3C80 44.5 160 42.9 231 44.5',
        light: 'M9 53.1C16 49.4 26 46.7 36 45C29 47.8 19 50.6 10.5 54Z', tail: 'M222 39.2L233.5 42.1C234.6 43.7 235 45.3 235.2 47C230 46.2 225 43.3 222 39.2Z',
        mirror: [77, 37.5], wheels: [44, 186], cy: 67, r: 17,
    },
    models: {
        body: 'M14 76.5C9 74 5.5 67 6.5 59C8 54.4 15 51.9 28 49.4C46 46.2 62 44.5 76 42.9C90 35.5 106 27.3 122 24.8C138 22.8 152 22.8 166 24.4C186 27.3 204 36.3 220 41.2C226 42.9 231 43.7 234 44.5C235.5 47.8 236 53.5 234 59C231.5 67 226 74 218 77L209.5 77A19.5 19.5 0 0 0 170.5 77L63.5 77A19.5 19.5 0 0 0 24.5 77L17 77C16 77 15 76.8 14 76.5Z',
        glass: 'M81 43.3C94 36.3 108 28.9 124 26.9C138 25.2 152 25.2 164 26.5C178 28.6 192 34.7 204 40.4C164 42.7 122 43.3 81 43.3Z',
        pillars: ['M133 26L131 43.1'],
        seams: ['M81 43.7L76 60C75 68 67 73 63.5 77', 'M131 43.7L133 77', 'M188 38.8C186 47.8 178 55.2 170.5 67'],
        handles: [[112, 47.8], [170, 47]], crease: 'M24 54.4C80 48.6 160 47 232 48.6',
        light: 'M8 57C15 53.5 26 51.1 38 49.3C31 51.9 20 54.7 9.5 58.2Z', tail: 'M222 42.5L234 44.8C235 47 235.4 48.6 235.6 50.3C230 49.4 225 47 222 42.5Z',
        mirror: [83, 42.5], wheels: [44, 190], cy: 68, r: 16,
    },
    modelx: {
        body: 'M14 73.5C9 71 5.5 63 6.5 54.4C8 49.4 17 46.2 30 43.7C46 40.8 58 39.2 68 37.1C80 28.1 94 18.3 114 15.4C132 13.4 152 13.8 170 16.6C192 20.7 212 29.8 224 35.5C228 37.1 231 38.8 234 40.4C235.5 44.5 236 51.1 234.5 57C232 65 227 71 219 74L210 74A20 20 0 0 0 170 74L64 74A20 20 0 0 0 24 74L17 74C16 74 15 73.8 14 73.5Z',
        glass: 'M73 37.5C85 28.9 99 20.3 116 17.9C134 15.8 154 16.1 170 19.1C186 22.4 202 29.8 214 35.5C170 38 120 38 73 37.5Z',
        pillars: ['M124 17.1L122 37.8', 'M172 19.6L170 37.3'],
        seams: ['M73 38L69 55.2C68 64 66 70 64 74', 'M122 38L124 74', 'M186 36.3C184 46.2 177 54.4 170 65'],
        handles: [[102, 42.9], [160, 42.1]], crease: 'M24 49.4C80 43.7 160 42.1 232 43.7',
        light: 'M8 52.3C15 48.6 26 45.7 36 44.2C29 47 19 49.8 9.5 53.2Z', tail: 'M222 35.1L234 40.4C235 42.1 235.6 43.7 235.8 45.3C230 44.2 225 40.4 222 35.1Z',
        mirror: [75, 36.7], wheels: [44, 190], cy: 66.5, r: 17.5,
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
        body: 'M8 72L7 66L10 60L58 50L92 33C96 30.6 100 29.6 106 29L140 28.5C146 28.5 150 29.5 154 31L226 46L234 49L235.5 58L232 70L224 78L206 78A19 19 0 0 0 168 78L63 78A19 19 0 0 0 25 78L14 78Z',
        glass: 'M80 46L95 35.4C98 33.4 102 32.2 108 31.8L138 31.4C144 31.4 148 32.2 152 33.4L190 42.6Z',
        pillars: ['M136 31.4L135 44.4'],
        seams: ['M80 46L76 62L63 78', 'M174 43.6L170 60L168 78'],
        handles: [[152, 49]], crease: 'M14 64L232 56',
        light: 'M9 60L44 52.6L46 54L11 62.2Z', tail: 'M220 44.8L234 49L235 53.4L224 49.6Z',
        mirror: [82, 45], wheels: [44, 187], cy: 68, r: 16, thrusters: true,
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
        // Tesla's picture, kept by the app; if it can't be had, the drawing in the car's paint
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
