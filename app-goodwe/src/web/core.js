// GoodWe dashboard - shared state, formatting, theme, modals and the live stream.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const LS = 'goodwe_';

// What the server told us; filled by loadStatus() and kept fresh by the live stream
const app = {
    version: '', status: null, inverter: {}, data: {}, control: {}, mqtt: {}, forecast: {}, eventsSeq: 0,
};

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
// Formatting
// =====================================================================
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(value, digits) {
    if (missing(value)) return '–';
    return Number(value).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
// Value with its unit as HTML: the unit sits small behind the number
function withUnit(text, unitText) { return text === '–' ? '–' : `${text}<small> ${unitText}</small>`; }
// Watts as "456 W" or "1.23 kW"
function fmtW(watts, signed) {
    if (missing(watts)) return '–';
    const a = Math.abs(watts);
    const sign = signed && watts < 0 ? '−' : '';
    return a >= 1000 ? `${sign}${num(a / 1000, 2)} kW` : `${sign}${num(a)} W`;
}
function wUnit(watts, signed) {
    if (missing(watts)) return '–';
    const a = Math.abs(watts);
    const sign = signed && watts < 0 ? '−' : '';
    return a >= 1000 ? withUnit(sign + num(a / 1000, 2), 'kW') : withUnit(sign + num(a), 'W');
}
const fmtKw = w => missing(w) ? '–' : `${num(w / 1000, 2)} kW`;
const fmtKwh = (kwh, digits) => missing(kwh) ? '–' : `${num(kwh, energyDigits(kwh, digits))} kWh`;
// kWh with sensible decimals (two below 10 kWh, one above), MWh from 10 MWh
function energyDigits(kwh, digits) { return digits === undefined ? (Math.abs(kwh) < 10 ? 2 : 1) : digits; }
function energyUnit(kwh, digits) {
    if (missing(kwh)) return '–';
    return kwh >= 10000 ? withUnit(num(kwh / 1000, 2), 'MWh') : withUnit(num(kwh, energyDigits(kwh, digits)), 'kWh');
}
const fmtPct = (v, digits) => missing(v) ? '–' : `${num(v, digits || 0)}%`;
const fmtTemp = c => missing(c) ? '–' : `${num(c, 1)} °C`;

const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const fmtDay = ts => new Date(ts * 1000).toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'});
const fmtDateTime = ts => `${fmtDay(ts)} ${fmtTime(ts)}`;
// An ISO date ("2026-11-07") as a readable day
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

// =====================================================================
// Small HTML building blocks shared by the views
// =====================================================================
function fact(label, valueHtml, cls) {
    return `<div class="fact"><div class="l">${label}</div><div class="v ${cls || ''}">${valueHtml}</div></div>`;
}
// Join fact() cells for the 2-column grid, padding an odd count with a blank cell
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
function switchHtml(id, on, onchange, disabled) {
    return `<button type="button" id="${id}" class="switch${on ? ' on' : ''}" role="switch" aria-checked="${on ? 'true' : 'false'}" onclick="${onchange}"${disabled ? ' disabled' : ''}></button>`;
}
// Previous / date / next / today controls for a day picker
function dateNavHtml(prefix, iso) {
    return `<button type="button" class="btn btn-small" onclick="${prefix}(-1)" aria-label="Previous day">‹</button>
        <span class="date">${escapeHtml(fmtIsoDate(iso))}</span>
        <button type="button" class="btn btn-small" onclick="${prefix}(1)"${iso >= todayIso() ? ' disabled' : ''} aria-label="Next day">›</button>
        <button type="button" class="btn btn-small" onclick="${prefix}(0)"${iso >= todayIso() ? ' disabled' : ''}>Today</button>`;
}

// What the battery and the grid are doing right now (30 W dead band, like the flow diagram)
function batteryState(data) {
    const p = Number(data.pbattery1 || 0);
    return p > 30 ? 'charging' : p < -30 ? 'discharging' : 'idle';
}
function gridState(data) {
    const p = Number(data.pgrid || 0);
    return p > 30 ? 'importing' : p < -30 ? 'exporting' : 'idle';
}

// =====================================================================
// Theme: follows Home Assistant's dark mode (this page runs in its ingress
// iframe, same origin), the system outside Home Assistant, or a manual choice
// =====================================================================
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
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
// Views
// =====================================================================
const TABS = ['dashboard', 'stats', 'settings'];
const tabHooks = {};
let activeTab = null;

function onTab(name, fn) { tabHooks[name] = fn; }

function switchTab(tabName) {
    if (!TABS.includes(tabName)) tabName = 'dashboard';
    activeTab = tabName;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    localStorage.setItem(LS + 'tab', tabName);
    if (window.location.hash.slice(1) !== tabName) history.replaceState(null, '', '#' + tabName);
    if (typeof hideTip === 'function') hideTip();
    if (tabHooks[tabName]) tabHooks[tabName]();
}

// =====================================================================
// Period chips (Stats)
// =====================================================================
const RANGES = [
    {key: '7d', label: '7 days', days: 7},
    {key: '30d', label: '30 days', days: 30},
    {key: '90d', label: '90 days', days: 90},
    {key: '12m', label: '12 months', days: 365},
    {key: 'year', label: 'This year'},
    {key: 'all', label: 'All'},
];

// {from, to} as ISO dates (to exclusive) for a preset or a specific year ("y2024")
function rangeBounds(key) {
    const now = new Date();
    const tomorrow = isoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
    const year = /^y(\d{4})$/.exec(key || '');
    if (year) return {from: `${year[1]}-01-01`, to: `${Number(year[1]) + 1}-01-01`};
    const preset = RANGES.find(r => r.key === key) || RANGES[1];
    if (preset.key === 'all') return {from: '2000-01-01', to: tomorrow};
    if (preset.key === 'year') return {from: `${now.getFullYear()}-01-01`, to: tomorrow};
    return {from: isoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() - (preset.days - 1))), to: tomorrow};
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

// Years that have data (newest first)
function availableYears() {
    const now = new Date().getFullYear();
    let first = now;
    const db = app.status && app.status.database;
    if (db && db.first_date) first = Number(db.first_date.slice(0, 4)) || now;
    const years = [];
    for (let y = now; y >= Math.min(first, now); y--) years.push(y);
    return years;
}

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

function applyLive(payload) {
    app.inverter = payload.inverter || {};
    app.data = payload.data || {};
    app.control = payload.control || {};
    app.mqtt = payload.mqtt || {};
    app.forecast = payload.forecast || {};
    app.eventsSeq = payload.events_seq || 0;
    renderChrome();
    liveListeners.forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
}

async function loadStatus() {
    const status = await api('status');
    app.status = status;
    app.version = status.version;
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

// Top bar and status bar
function renderChrome() {
    document.getElementById('brand-model').textContent = app.inverter.model || '';
    renderStatusbar();
}

function renderStatusbar() {
    const inv = app.inverter;
    const parts = [];
    if (!streamOk) parts.push('<span class="sb-bad">● dashboard disconnected</span>');
    if (!inv.connected) {
        parts.push(`<span class="sb-warn">● connecting to ${escapeHtml(inv.host || 'the inverter')}…</span>`);
    } else {
        parts.push(`<span><span class="${inv.online ? 'sb-ok' : 'sb-bad'}">●</span> <b>${escapeHtml(inv.model || 'inverter')}</b> ${inv.online ? 'online' : 'offline'}</span>`);
        parts.push(`<span>read <b>${fmtAgo(inv.last_read)}</b></span>`);
    }
    if (inv.last_error && (!inv.online || inv.stale)) parts.push(`<span class="sb-warn sb-extra">${escapeHtml(inv.last_error)}</span>`);
    const c = app.control;
    if (c.auto_hold) parts.push(`<span class="sb-extra">auto-hold <b>${c.held === null || c.held === undefined ? 'deciding' : c.held ? 'holding' : 'discharging'}</b></span>`);
    if (c.zero_export) parts.push('<span class="sb-extra">zero export</span>');
    if (c.surplus) parts.push('<span class="sb-extra sb-ok">solar surplus</span>');
    if (app.mqtt.enabled) {
        parts.push(`<span class="sb-extra">MQTT <b class="${app.mqtt.connected ? 'sb-ok' : 'sb-bad'}">${app.mqtt.connected ? 'connected' : 'offline'}</b></span>`);
    }
    parts.push(`<span class="spacer"></span><span class="sb-extra">GoodWe ${escapeHtml(app.version)} · unofficial</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
