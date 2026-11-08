// Toon dashboard - shared state, formatting, theme and views.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'toon_';

// What the server told us; refreshed by loadStatus()
const app = {status: null, today: null};

async function api(path) {
    const response = await fetch('api/' + path);
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return response.json();
}

async function apiPost(path, body) {
    const response = await fetch('api/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
    return data;
}

// =====================================================================
// What the Toon's numbers mean
// =====================================================================
const PRESETS = {'-1': 'Manual', 0: 'Comfort', 1: 'Home', 2: 'Sleep', 3: 'Away', 4: 'Holiday'};
const PROGRAMS = {0: 'Off', 1: 'Week program', 2: 'Temporary change'};
const BURNER = {off: 'Off', heating: 'Heating', hot_water: 'Hot water', preheating: 'Preheating', error: 'Error'};
const PRESSURE_LOW = 1;

const color = name => `var(--${name})`;
const value = key => (app.status && app.status.values ? app.status.values[key] : undefined);
const externals = () => (app.status && app.status.external) || [];
// Where a configured sensor belongs in the charts: outside with the room, other temperatures with the
// boiler, anything else in a chart of its own unit
const OUTSIDE_KEY = 'outside_temperature';
const externalOutside = () => externals().filter(s => s.key === OUTSIDE_KEY);
const externalTemps = () => externals().filter(s => s.key !== OUTSIDE_KEY && s.unit === '°C');
function externalOthers() {
    const units = new Map();
    for (const s of externals()) {
        // Until a first reading its unit isn't known, and there's nothing to draw
        if (s.key === OUTSIDE_KEY || s.unit === '°C' || !s.updated) continue;
        if (!units.has(s.unit || '')) units.set(s.unit || '', []);
        units.get(s.unit || '').push(s);
    }
    return [...units.entries()];
}
const EXTERNAL_COLORS = ['series-5', 'series-3', 'series-1', 'series-2'];
const externalColor = i => color(EXTERNAL_COLORS[i % EXTERNAL_COLORS.length]);

// =====================================================================
// Formatting
// =====================================================================
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(v, digits) {
    if (missing(v)) return '–';
    return Number(v).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
function withUnit(text, unitText) { return text === '–' ? '–' : `${text}<small> ${unitText}</small>`; }
const unit = (v, digits, unitText) => withUnit(num(v, digits), unitText);
const fmtTemp = (v, digits) => missing(v) ? '–' : `${num(v, digits === undefined ? 1 : digits)} °C`;
function fmtW(watts) {
    if (missing(watts)) return '–';
    return Math.abs(watts) >= 1000 ? `${num(watts / 1000, 2)} kW` : `${num(watts)} W`;
}
function energyDigits(kwh) { return Math.abs(kwh) < 10 ? 2 : Math.abs(kwh) < 1000 ? 1 : 0; }
const fmtKwh = kwh => missing(kwh) ? '–' : `${num(kwh, energyDigits(kwh))} kWh`;
const fmtGas = m3 => missing(m3) ? '–' : `${num(m3, Math.abs(m3) < 10 ? 2 : Math.abs(m3) < 1000 ? 1 : 0)} m³`;
const sum = (a, b) => (missing(a) && missing(b)) ? null : (a || 0) + (b || 0);

function fmtDuration(seconds) {
    if (missing(seconds)) return '–';
    const minutes = Math.round(seconds / 60);
    if (seconds > 0 && seconds < 90) return `${Math.round(seconds)} s`;
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
const fmtMinutes = m => missing(m) ? '–' : fmtDuration(m * 60);

const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const fmtDay = ts => new Date(ts * 1000).toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'});
const fmtDateTime = ts => `${fmtDay(ts)} ${fmtTime(ts)}`;
function fmtIsoDate(iso, withYear) {
    const d = new Date(iso + 'T00:00:00');
    const opts = {weekday: 'short', day: 'numeric', month: 'short'};
    if (withYear || d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString([], opts);
}
function isoDate(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const todayIso = () => isoDate(new Date());
function shiftDate(iso, days) {
    const d = new Date(iso + 'T00:00:00');
    d.setDate(d.getDate() + days);
    return isoDate(d);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : String(text);
    return div.innerHTML;
}

// =====================================================================
// Small HTML building blocks shared by the views
// =====================================================================
function fact(label, valueHtml, cls) {
    return `<div class="fact"><div class="l">${label}</div><div class="v ${cls || ''}">${valueHtml}</div></div>`;
}
function factsGrid(items, columns) {
    const cells = items.filter(Boolean);
    while (cells.length % (columns || 2)) cells.push('<div class="fact" aria-hidden="true"></div>');
    return cells.join('');
}
function tile(label, valueHtml, detail, cls) {
    return `<div class="tile ${cls || ''}"><div class="l">${label}</div><div class="v">${valueHtml}</div><div class="d">${detail || '&nbsp;'}</div></div>`;
}
function infoRow(label, valueHtml) {
    return `<div class="info-row"><span class="info-label">${label}</span><span class="info-value">${valueHtml}</span></div>`;
}
function stateLabel(kind, text) {
    return `<span class="state ${kind}">${escapeHtml(text)}</span>`;
}
function dateNavHtml(prefix, iso) {
    return `<button type="button" class="btn btn-small" onclick="${prefix}(-1)" aria-label="Previous day">‹</button>
        <span class="date">${escapeHtml(fmtIsoDate(iso))}</span>
        <button type="button" class="btn btn-small" onclick="${prefix}(1)"${iso >= todayIso() ? ' disabled' : ''} aria-label="Next day">›</button>
        <button type="button" class="btn btn-small" onclick="${prefix}(0)"${iso >= todayIso() ? ' disabled' : ''}>Today</button>`;
}

// =====================================================================
// Time series: the database keeps changes, a chart wants one row per moment
// =====================================================================
// One value per step: the average within a step, or else the value held since the last reading
function grid(points, start, end, step, holdLimit) {
    const cells = Math.ceil((end - start) / step);
    const out = new Array(cells).fill(null);
    const totals = new Array(cells).fill(0);
    const counts = new Array(cells).fill(0);
    let before = null;
    (points || []).forEach(([ts, v]) => {
        const i = Math.floor((ts - start) / step);
        if (i < 0) { before = [ts, v]; return; }
        if (i < cells) { totals[i] += v; counts[i] += 1; }
    });
    let last = before;
    for (let i = 0; i < cells; i++) {
        const at = start + step * i;
        if (counts[i]) {
            out[i] = totals[i] / counts[i];
            last = [at, out[i]];
        } else if (last && at - last[0] <= holdLimit && at <= Date.now() / 1000) {
            out[i] = last[1];
        }
    }
    return out;
}
function gridTimes(start, end, step) {
    const xs = [];
    for (let t = start; t < end; t += step) xs.push(t);
    return xs;
}
// Periods the burner was on, as background bands for a line chart
function burnerBands(points, start, end, step) {
    const codes = grid(points, start, end, step, 1800);
    const xs = gridTimes(start, end, step);
    const states = codes.map(c => c === null ? 0 : Math.round(c));
    return stateBands(xs, states, {1: color('flame'), 2: color('dhw'), 3: color('flame')}, step);
}

// =====================================================================
// Theme: follows Home Assistant's dark mode (this page runs in its ingress
// iframe, same origin), the system outside Home Assistant, or a manual choice
// =====================================================================
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
function storedTheme() {
    try { return localStorage.getItem(LS + 'theme'); } catch (e) { return null; }
}
let themeChoice = new URLSearchParams(window.location.search).get('theme') || storedTheme();
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
    const next = dark ? 'dark' : 'light';
    if (document.documentElement.dataset.theme !== next) {
        document.documentElement.dataset.theme = next;
        if (typeof redrawCharts === 'function') setTimeout(redrawCharts, 0);
    }
}

function initThemeToggle() {
    document.getElementById('theme-icon').textContent = THEME_ICONS[themeChoice];
    document.getElementById('theme-toggle').title = themeChoice === 'auto'
        ? 'Theme follows Home Assistant - click for light'
        : `Theme: ${themeChoice} - click to change`;
}

function cycleTheme() {
    themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
    try { localStorage.setItem(LS + 'theme', themeChoice); } catch (e) { /* only a convenience */ }
    initThemeToggle();
    applyTheme();
}

applyTheme();
// Home Assistant doesn't tell the iframe when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// =====================================================================
// Views
// =====================================================================
const TABS = ['dashboard', 'stats', 'settings'];
const tabHooks = {};
const liveHooks = {};
let activeTab = null;

function onTab(name, show, live) {
    tabHooks[name] = show;
    liveHooks[name] = live;
}

function switchTab(tabName) {
    if (!TABS.includes(tabName)) tabName = 'dashboard';
    activeTab = tabName;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    try { localStorage.setItem(LS + 'tab', tabName); } catch (e) { /* only a convenience */ }
    if (window.location.hash.slice(1) !== tabName) history.replaceState(null, '', '#' + tabName);
    hideTip();
    if (tabHooks[tabName]) tabHooks[tabName]();
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

function toast(message) {
    const el = document.createElement('div');
    el.className = 'toast error';
    el.textContent = message;
    document.getElementById('toast-root').appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

// =====================================================================
// Status
// =====================================================================
async function loadStatus() {
    app.status = await api('status');
    renderStatusbar();
    if (liveHooks[activeTab]) liveHooks[activeTab]();
}

function renderStatusbar() {
    const s = app.status;
    if (!s) return;
    const parts = [];
    if (s.connected) {
        parts.push(`<span><span class="sb-ok">●</span> <b>Toon</b> connected ${escapeHtml(fmtDuration(Date.now() / 1000 - s.connected_since))}</span>`);
    } else {
        parts.push(`<span class="sb-bad">● Toon not connected</span>`);
        if (s.error) parts.push(`<span class="sb-warn sb-extra">${escapeHtml(s.error)}</span>`);
    }
    if (s.info.vers) parts.push(`<span class="sb-extra">firmware <b>${escapeHtml(s.info.vers)}</b></span>`);
    if (s.import.running) parts.push('<span class="sb-extra">importing history</span>');
    if (s.mqtt.broker) parts.push(`<span class="sb-extra">MQTT <b class="${s.mqtt.connected ? 'sb-ok' : 'sb-bad'}">${s.mqtt.connected ? 'connected' : 'offline'}</b></span>`);
    parts.push(`<span class="spacer"></span><span class="sb-extra">Toon ${escapeHtml(s.version)} · unofficial</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
