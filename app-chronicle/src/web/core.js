// Chronicle dashboard - shared state, formatting, theme, tabs and the status bar.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const LS = 'chronicle_';

// What the server told us about the connection; filled by loadStatus()
const app = {version: '', status: null, checkedAt: null};

async function api(path) {
    const response = await fetch(basePath + 'api/' + path);
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) {
        const error = new Error((data && data.error) || `Request failed (${response.status})`);
        error.status = response.status;
        throw error;
    }
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
// A measured value with sensible decimals for its size
function fmtValue(value) {
    if (missing(value)) return '–';
    const a = Math.abs(value);
    // Up to these decimals, without trailing zeros: 21.5, 0.125, 1,234
    return Number(value).toLocaleString(undefined, {maximumFractionDigits: a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : 3});
}
function withUnit(text, unitText) { return text === '–' || !unitText ? text : `${text}<small> ${escapeHtml(unitText)}</small>`; }
// Big counts as 12.3K / 4.56M
function fmtCount(n) {
    if (missing(n)) return '–';
    const a = Math.abs(n);
    if (a >= 1e9) return num(n / 1e9, 2) + 'B';
    if (a >= 1e6) return num(n / 1e6, 2) + 'M';
    if (a >= 1e4) return num(n / 1e3, 1) + 'K';
    return num(n);
}
function bytesParts(bytes) {
    if (missing(bytes)) return ['–', ''];
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = Number(bytes), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return [num(v, i === 0 ? 0 : v < 10 ? 2 : v < 100 ? 1 : 0), units[i]];
}
const fmtBytes = bytes => missing(bytes) ? '–' : bytesParts(bytes).join(' ');
const bytesUnit = bytes => missing(bytes) ? '–' : withUnit(...bytesParts(bytes));

function fmtDuration(seconds) {
    if (missing(seconds)) return '–';
    if (seconds < 1) return `${num(seconds * 1000)} ms`;
    if (seconds < 90) return `${num(seconds, seconds < 10 ? 1 : 0)} s`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 120) return `${minutes} min`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours} h`;
    return `${num(hours / 24, hours % 24 ? 1 : 0)} days`;
}

const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const fmtTimeSec = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
const fmtDay = ts => new Date(ts * 1000).toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'});
const fmtDate = ts => new Date(ts * 1000).toLocaleDateString([], {day: 'numeric', month: 'short', year: 'numeric'});
const fmtShortDate = ts => new Date(ts * 1000).toLocaleDateString([], {day: 'numeric', month: 'short'});
const fmtDateTime = ts => {
    const d = new Date(ts * 1000);
    const sameYear = d.getFullYear() === new Date().getFullYear();
    return d.toLocaleString([], {day: 'numeric', month: 'short', year: sameYear ? undefined : 'numeric', hour: '2-digit', minute: '2-digit'});
};

function fmtAgo(ts) {
    if (!ts) return 'never';
    const seconds = Math.max(0, Date.now() / 1000 - ts);
    if (seconds < 10) return 'just now';
    if (seconds < 90) return `${Math.round(seconds)} s ago`;
    if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
    if (seconds < 86400 * 120) return `${Math.round(seconds / 86400)} days ago`;
    return fmtDate(ts);
}
function fmtIn(ts) {
    if (!ts) return '–';
    const seconds = ts - Date.now() / 1000;
    if (seconds <= 0) return 'due';
    return 'in ' + fmtDuration(seconds);
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
function tile(label, valueHtml, detail, cls) {
    return `<div class="tile ${cls || ''}"><div class="l">${label}</div><div class="v">${valueHtml}</div><div class="d">${detail || '&nbsp;'}</div></div>`;
}
function infoRow(label, valueHtml) {
    return `<div class="info-row"><span class="info-label">${label}</span><span class="info-value">${valueHtml}</span></div>`;
}
function stateLabel(kind, text) {
    return `<span class="state ${kind}">${escapeHtml(text)}</span>`;
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
    try { localStorage.setItem(LS + 'theme', themeChoice); } catch (e) { /* only a convenience */ }
    initThemeToggle();
    applyTheme();
}

applyTheme();
// Home Assistant doesn't tell the iframe when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// =====================================================================
// Toasts
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

// =====================================================================
// Views
// =====================================================================
const TABS = ['overview', 'entities', 'storage', 'query', 'info'];
const tabHooks = {};
let activeTab = null;

function onTab(name, fn) { tabHooks[name] = fn; }

function switchTab(tabName) {
    if (!TABS.includes(tabName)) tabName = 'overview';
    activeTab = tabName;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    try { localStorage.setItem(LS + 'tab', tabName); } catch (e) { /* only a convenience */ }
    if (window.location.hash.slice(1).split('/')[0] !== tabName) history.replaceState(null, '', '#' + tabName);
    if (typeof hideTip === 'function') hideTip();
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

// =====================================================================
// Connection status: once at start, and again after an error
// =====================================================================
async function loadStatus() {
    try {
        app.status = await api('status');
        app.version = app.status.version;
    } catch (e) {
        app.status = {connected: false, error: e.message, connection: {}};
    }
    app.checkedAt = Date.now() / 1000;
    renderChrome();
    return app.status;
}

// Whether the pages have something to show; otherwise the hint says why
function ready() {
    return Boolean(app.status && app.status.connected && app.status.schema);
}

function renderChrome() {
    const s = app.status || {};
    const c = s.connection || {};
    document.getElementById('brand-db').textContent = c.database ? `${c.database}@${c.host}` : '';
    const hint = document.getElementById('setup-hint');
    const text = document.getElementById('setup-hint-text');
    if (!s.connected) {
        hint.hidden = false;
        text.innerHTML = `Cannot connect to the database <b>${escapeHtml(c.database || '')}</b> on <b>${escapeHtml(c.host || '')}</b>: ${escapeHtml(s.error || 'unknown error')}. Check the connection on the app's Configuration tab.`;
    } else if (!s.schema) {
        hint.hidden = false;
        text.innerHTML = `There is no Chronicle history in the database <b>${escapeHtml(c.database)}</b> yet. Install the <a href="https://github.com/cyberjunky/home-assistant-chronicle" target="_blank" rel="noopener">Chronicle integration</a> and point it at this database; it creates its tables on its first start.`;
    } else {
        hint.hidden = true;
    }
    renderStatusbar();
}

function renderStatusbar() {
    const s = app.status || {};
    const c = s.connection || {};
    const parts = [];
    if (s.connected) {
        parts.push(`<span><span class="sb-ok">●</span> <b>${escapeHtml(c.database)}</b> on ${escapeHtml(c.host)}</span>`);
        parts.push(`<span class="sb-extra">PostgreSQL <b>${escapeHtml(s.server || '')}</b> · TimescaleDB <b>${escapeHtml(s.timescaledb || '–')}</b></span>`);
        if (!s.schema) parts.push('<span class="sb-warn">no Chronicle tables</span>');
    } else {
        parts.push(`<span class="sb-bad">● not connected</span>`);
    }
    parts.push(`<span class="spacer"></span><span class="sb-extra">Chronicle ${escapeHtml(app.version)}</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

// Shows a failed request in the page: a lost connection updates the hint, the rest is a toast
async function handleError(e) {
    if (e.status === 503 || e.status === 409) await loadStatus();
    else toast(e.message, 'error');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
