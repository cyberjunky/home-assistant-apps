// Vigil dashboard - shared state, formatting, theme, tabs, the global time range and the status bar.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const LS = 'vigil_';

// What the server told us about the connection; filled by loadStatus()
const app = {version: '', status: null, checkedAt: null};

async function api(path, body) {
    // Everything that changes something is a POST with this header (the server refuses it without)
    const options = body === undefined ? {} : {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Vigil': '1'},
        body: JSON.stringify(body),
    };
    const response = await fetch(basePath + 'api/' + path, options);
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) {
        const error = new Error((data && data.error) || `Request failed (${response.status})`);
        error.status = response.status;
        throw error;
    }
    return data;
}

// A query string from an object; empty values are left out
function qs(params) {
    return Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(typeof v === 'object' ? JSON.stringify(v) : v)}`)
        .join('&');
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

// A metric value with its unit: bytes and byte rates scaled, the rest as numbers
function fmtMetric(value, unit) {
    if (missing(value)) return '–';
    if (unit === 'bytes' || unit === 'B') return fmtBytes(value);
    if (unit === 'bytes/s' || unit === 'B/s') return fmtBytes(value) + '/s';
    if (unit === 'bits/s' || unit === 'bit/s') {
        const units = ['bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s'];
        let v = Number(value), i = 0;
        while (Math.abs(v) >= 1000 && i < units.length - 1) { v /= 1000; i++; }
        return `${fmtValue(v)} ${units[i]}`;
    }
    if (unit === 's' || unit === 'seconds') return fmtDuration(value);
    return `${fmtValue(value)}${unit ? ' ' + unit : ''}`;
}

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
// A log line's time: with milliseconds within today, with the date before
function fmtLogTime(ts) {
    const d = new Date(ts * 1000);
    const time = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false})
        + '.' + String(d.getMilliseconds()).padStart(3, '0');
    return d.toDateString() === new Date().toDateString() ? time : `${fmtShortDate(ts)} ${time}`;
}

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

// Safe in text and in attribute values ("…" and '…'): log lines and names can hold any character
function escapeHtml(text) {
    return (text === null || text === undefined ? '' : String(text))
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// =====================================================================
// Severities: syslog numbers 0 emerg .. 7 debug
// =====================================================================
const SEVERITIES = ['emerg', 'alert', 'crit', 'err', 'warning', 'notice', 'info', 'debug'];
const SEVERITY_ALIASES = {error: 'err', warn: 'warning', critical: 'crit', fatal: 'crit', emergency: 'emerg', trace: 'debug', information: 'info'};
function severityName(sev) {
    if (typeof sev === 'number') return SEVERITIES[sev] || String(sev);
    const s = String(sev || '').toLowerCase();
    if (/^\d$/.test(s)) return SEVERITIES[Number(s)];
    return SEVERITY_ALIASES[s] || s;
}
function severityNumber(sev) {
    const i = SEVERITIES.indexOf(severityName(sev));
    return i < 0 ? 6 : i;
}
const severityColor = sev => cssVar(`--sev-${severityNumber(sev)}`);
function sevPill(sev) {
    const n = severityNumber(sev);
    return `<span class="sev sev-${n}">${escapeHtml(severityName(sev))}</span>`;
}
// The order severities stack in a chart: the worst at the bottom
const severityOrder = keys => [...keys].sort((a, b) => severityNumber(a) - severityNumber(b));

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
// A rule's state: ok, pending, firing (and off for a switched-off rule)
function statePill(state, enabled) {
    const s = enabled === false ? 'off' : (state || 'ok');
    return `<span class="pill pill-${escapeHtml(s)}">${escapeHtml(s)}</span>`;
}
const emptyHtml = text => `<div class="empty-state">${escapeHtml(text)}</div>`;
const loadingHtml = text => `<div class="empty-state loading">${escapeHtml(text || 'Loading…')}</div>`;
const errorHtml = text => `<div class="empty-state error-state">${escapeHtml(text)}</div>`;

// =====================================================================
// Theme: follows Home Assistant's dark mode (this page runs in its ingress
// iframe, same origin), the system outside Home Assistant, or a manual choice
// =====================================================================
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = new URLSearchParams(window.location.search).get('theme');
try { themeChoice = themeChoice || localStorage.getItem(LS + 'theme'); } catch (e) { /* only a convenience */ }
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
// Toasts and modals
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

// A dialog. opts: {title, body (HTML the caller escaped), wide, label, danger, cancel,
// onConfirm: async () => {...} (throw to show the error in the dialog), foot (extra buttons HTML)}
function openModal(opts) {
    const root = document.getElementById('modal-root');
    root.innerHTML = `<div class="modal-overlay open" id="app-modal"><div class="modal${opts.wide ? ' wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="app-modal-title">
        <div class="modal-head"><span class="modal-title" id="app-modal-title">${escapeHtml(opts.title)}</span>
            <button type="button" class="modal-close" aria-label="Close" data-close>×</button></div>
        <div class="modal-body">${opts.body}<div class="form-error" id="modal-error" hidden></div></div>
        <div class="modal-foot">${opts.foot || ''}<span class="spacer"></span>
            <button type="button" class="btn" data-close>${escapeHtml(opts.cancel || (opts.onConfirm ? 'Cancel' : 'Close'))}</button>
            ${opts.onConfirm ? `<button type="button" class="btn ${opts.danger ? 'btn-danger-solid' : 'btn-primary'}" id="modal-go">${escapeHtml(opts.label || 'Save')}</button>` : ''}</div>
    </div></div>`;
    const overlay = document.getElementById('app-modal');
    overlay.addEventListener('mousedown', ev => { if (ev.target === overlay) closeModal(); });
    overlay.querySelectorAll('[data-close]').forEach(b => { b.onclick = closeModal; });
    const go = document.getElementById('modal-go');
    if (go) {
        go.onclick = async () => {
            go.disabled = true;
            go.textContent = 'Working…';
            try {
                await opts.onConfirm();
                closeModal();
            } catch (err) {
                modalError(err.message);
                go.disabled = false;
                go.textContent = opts.label || 'Save';
            }
        };
    }
    const first = overlay.querySelector('.modal-body input, .modal-body select, .modal-body textarea');
    if (first) setTimeout(() => first.focus(), 0);
    return overlay;
}
function modalError(text) {
    const el = document.getElementById('modal-error');
    if (!el) return;
    el.textContent = text || '';
    el.hidden = !text;
}
function closeModal() { document.getElementById('modal-root').innerHTML = ''; }
function confirmModal(title, bodyHtml, label, onConfirm) {
    openModal({title, body: bodyHtml, label, danger: true, onConfirm});
}
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.getElementById('app-modal') && !document.querySelector('.chart-fullscreen')) closeModal();
});

// =====================================================================
// The global time range: a preset (the last 15 min .. 7 days, moving with now) or a fixed
// window; zooming a time chart narrows it and Back returns to the one before
// =====================================================================
const RANGE_PRESETS = [
    {key: '15m', label: '15 min', secs: 900},
    {key: '1h', label: '1 h', secs: 3600},
    {key: '6h', label: '6 h', secs: 6 * 3600},
    {key: '24h', label: '24 h', secs: 86400},
    {key: '7d', label: '7 d', secs: 7 * 86400},
];
let timeRange = {preset: '1h'};
try {
    const saved = localStorage.getItem(LS + 'range');
    if (RANGE_PRESETS.some(p => p.key === saved)) timeRange = {preset: saved};
} catch (e) { /* only a convenience */ }
const rangeHistory = [];   // the ranges before a zoom, for Back

function rangeWindow() {
    if (timeRange.preset) {
        const p = RANGE_PRESETS.find(r => r.key === timeRange.preset) || RANGE_PRESETS[1];
        const to = Math.round(Date.now() / 1000);
        return {from: to - p.secs, to};
    }
    return {from: timeRange.from, to: timeRange.to};
}
const rangeSpan = () => { const w = rangeWindow(); return w.to - w.from; };
const rangeParams = () => rangeWindow();
const rangeZoomed = () => rangeHistory.length > 0;

function rangeToHash() {
    if (timeRange.preset) return `${timeRange.preset}/now`;
    return `${Math.round(timeRange.from)}/${Math.round(timeRange.to)}`;
}
function rangeFromHash(from, to) {
    if (RANGE_PRESETS.some(p => p.key === from)) return {preset: from};
    const f = Number(from), t = Number(to);
    if (Number.isFinite(f) && Number.isFinite(t) && t > f) return {from: f, to: t};
    return null;
}

// The view's own part of the hash: #<tab>/<arg>/<from>/<to>
const hashArgs = {};
function parseHash() {
    const parts = window.location.hash.slice(1).split('/');
    return {tab: parts[0], arg: parts[1] ? decodeURIComponent(parts[1]) : '', range: rangeFromHash(parts[2], parts[3])};
}
function updateHash(arg) {
    if (arg !== undefined) hashArgs[activeTab] = arg;
    const a = hashArgs[activeTab] || '';
    const next = `#${activeTab}/${encodeURIComponent(a)}/${rangeToHash()}`;
    if (window.location.hash !== next) history.replaceState(null, '', next);
}

// Sets the range and reloads the page that shows. zoom: keep the one before for Back
function setRange(range, zoom) {
    if (zoom) rangeHistory.push(timeRange);
    else rangeHistory.length = 0;
    if (range.preset) {
        timeRange = {preset: range.preset};
        try { localStorage.setItem(LS + 'range', range.preset); } catch (e) { /* only a convenience */ }
    } else {
        timeRange = {from: Math.floor(range.from), to: Math.ceil(range.to)};
    }
    renderRangeBar();
    updateHash();
    if (rangeHooks[activeTab]) rangeHooks[activeTab]();
    if (typeof changesOpen !== 'undefined' && changesOpen) openChanges();
}
function zoomRange(from, to) {
    if (to - from < 10) return;
    setRange({from, to}, true);
}
function rangeBack() {
    if (!rangeHistory.length) return;
    timeRange = rangeHistory.pop();
    renderRangeBar();
    updateHash();
    if (rangeHooks[activeTab]) rangeHooks[activeTab]();
}

function rangeText() {
    const w = rangeWindow();
    if (timeRange.preset) return `last ${RANGE_PRESETS.find(p => p.key === timeRange.preset).label}`;
    const sameDay = new Date(w.from * 1000).toDateString() === new Date(w.to * 1000).toDateString();
    const span = w.to - w.from;
    const f = span < 3600 ? fmtTimeSec : fmtTime;
    return sameDay ? `${fmtShortDate(w.from)} ${f(w.from)} – ${f(w.to)}` : `${fmtDateTime(w.from)} – ${fmtDateTime(w.to)}`;
}

function renderRangeBar() {
    const chips = document.getElementById('range-chips');
    chips.innerHTML = RANGE_PRESETS.map(p =>
        `<button type="button" class="tab chip${timeRange.preset === p.key ? ' active' : ''}" data-key="${p.key}">${escapeHtml(p.label)}</button>`
    ).join('') + `<button type="button" class="tab chip${timeRange.preset ? '' : ' active'}" data-key="custom">Custom…</button>`;
    chips.onclick = (e) => {
        const button = e.target.closest('[data-key]');
        if (!button) return;
        if (button.dataset.key === 'custom') return openCustomRange();
        setRange({preset: button.dataset.key});
    };
    document.getElementById('range-text').textContent = rangeText();
    document.getElementById('range-back').hidden = !rangeHistory.length;
}

// datetime-local wants the local time as YYYY-MM-DDTHH:MM
function toLocalInput(ts) {
    const d = new Date(ts * 1000);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function openCustomRange() {
    const w = rangeWindow();
    openModal({
        title: 'Custom time range',
        body: `<div class="form-row"><span>From</span><input type="datetime-local" class="input" id="range-from" value="${toLocalInput(w.from)}"></div>
            <div class="form-row"><span>To</span><input type="datetime-local" class="input" id="range-to" value="${toLocalInput(w.to)}"></div>
            <p class="panel-sub">Your browser's local time. Dragging across any time chart narrows the range too.</p>`,
        label: 'Apply',
        onConfirm: async () => {
            const from = new Date(document.getElementById('range-from').value).getTime() / 1000;
            const to = new Date(document.getElementById('range-to').value).getTime() / 1000;
            if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) throw new Error('"To" must be after "From"');
            setRange({from, to});
        },
    });
}

// =====================================================================
// Views
// =====================================================================
const TABS = ['overview', 'logs', 'patterns', 'metrics', 'dashboards', 'alerts', 'sources', 'info'];
// Pages with the time range above them, and those with the "What changed?" button
const RANGE_TABS = ['overview', 'logs', 'patterns', 'metrics', 'dashboards', 'alerts'];
const CHANGES_TABS = ['overview', 'logs', 'metrics'];
const tabHooks = {};
const rangeHooks = {};
const leaveHooks = {};
let activeTab = null;

function onTab(name, fn) { tabHooks[name] = fn; }
function onRange(name, fn) { rangeHooks[name] = fn; }
function onLeave(name, fn) { leaveHooks[name] = fn; }

function switchTab(tabName, arg) {
    if (!TABS.includes(tabName)) tabName = 'overview';
    if (activeTab && activeTab !== tabName && leaveHooks[activeTab]) leaveHooks[activeTab]();
    if (activeTab !== tabName) {
        if (typeof closeChanges === 'function') closeChanges();
        closeModal();
    }
    activeTab = tabName;
    if (arg !== undefined) hashArgs[tabName] = arg;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    document.getElementById('range-bar').hidden = !RANGE_TABS.includes(tabName);
    document.getElementById('changes-btn').hidden = !CHANGES_TABS.includes(tabName);
    try { localStorage.setItem(LS + 'tab', tabName); } catch (e) { /* only a convenience */ }
    updateHash();
    if (typeof hideTip === 'function') hideTip();
    if (tabHooks[tabName]) tabHooks[tabName](hashArgs[tabName] || '');
}

function buildChips(containerId, options, current, onChange) {
    const el = document.getElementById(containerId);
    el.innerHTML = options.map(o =>
        `<button type="button" class="tab chip${o.key === current ? ' active' : ''}" data-key="${escapeHtml(o.key)}">${escapeHtml(o.label)}</button>`
    ).join('');
    el.onclick = (e) => {
        const button = e.target.closest('[data-key]');
        if (!button) return;
        el.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === button));
        onChange(button.dataset.key);
    };
}

// =====================================================================
// Connection status: at start, every 15 s for the status bar, and after an error
// =====================================================================
async function loadStatus() {
    try {
        app.status = await api('status');
        app.version = app.status.version || '';
    } catch (e) {
        app.status = {connected: false, error: e.message, database: {}};
    }
    app.checkedAt = Date.now() / 1000;
    renderChrome();
    return app.status;
}

// Whether the pages have something to show; otherwise the hint says why
function ready() {
    return Boolean(app.status && app.status.connected);
}
// Connected, but nothing has arrived yet
function noDataYet() {
    const c = (app.status && app.status.counts) || {};
    return ready() && !c.logs_24h && !c.series;
}

function renderChrome() {
    const s = app.status || {};
    const db = s.database || {};
    document.getElementById('brand-db').textContent = db.name || '';
    const hint = document.getElementById('setup-hint');
    const text = document.getElementById('setup-hint-text');
    if (!s.connected) {
        hint.hidden = false;
        text.innerHTML = `The database is not reachable: ${escapeHtml(s.error || 'unknown error')}. Vigil keeps its data in a database on the Chronicle app: make it in <b>Chronicle → Maintenance → App databases</b> and put its password in Vigil's configuration. The app's log in Home Assistant says more.`;
    } else if (noDataYet()) {
        hint.hidden = false;
        text.innerHTML = 'Connected, but nothing has arrived yet. Point your router, switches and servers at Vigil: <a href="#sources" onclick="switchTab(\'sources\');return false">Sources</a> has the settings to copy.';
    } else {
        hint.hidden = true;
    }
    renderStatusbar();
}

function renderStatusbar() {
    const s = app.status || {};
    const db = s.database || {};
    const parts = [];
    if (s.connected) {
        parts.push(`<span><span class="sb-ok">●</span> <b>${escapeHtml(db.name || 'vigil')}</b>${db.host ? ' @ ' + escapeHtml(db.host) : ''}</span>`);
        parts.push(`<span class="sb-extra">PostgreSQL <b>${escapeHtml(db.version || '–')}</b> · TimescaleDB <b>${escapeHtml(db.timescaledb || '–')}</b></span>`);
    } else {
        parts.push('<span class="sb-bad">● database not connected</span>');
    }
    const r = s.receivers || {};
    const recv = (label, v) => {
        const on = v !== false && v !== null && v !== undefined && v !== 0;
        return `<span class="${on ? '' : 'sb-muted'}">${label} <b class="${on ? '' : 'sb-warn'}">${on ? (typeof v === 'number' ? escapeHtml(v) : 'on') : 'off'}</b></span>`;
    };
    if (s.receivers) {
        parts.push(`<span class="sb-group">${recv('syslog/udp', r.syslog_udp)} ${recv('tcp', r.syslog_tcp)} ${recv('otlp', r.otlp)}</span>`);
    }
    const q = s.queue || {};
    if (s.queue) {
        parts.push(`<span>queue <b class="${q.size > 10000 ? 'sb-warn' : ''}">${num(q.size)}</b></span>`);
        parts.push(`<span>dropped <b class="${q.dropped > 0 ? 'sb-bad' : ''}">${num(q.dropped)}</b></span>`);
    }
    const c = s.counts || {};
    if (c.firing) parts.push(`<span class="sb-bad">● ${num(c.firing)} firing</span>`);
    parts.push(`<span class="spacer"></span><span class="sb-extra">Vigil ${escapeHtml(app.version)}</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
    const firing = document.getElementById('alerts-count');
    if (firing) { firing.textContent = c.firing ? String(c.firing) : ''; firing.hidden = !c.firing; }
}

// Shows a failed request in the page: a lost connection updates the hint, the rest is a toast
async function handleError(e) {
    if (e.status === 503 || e.status === 409) await loadStatus();
    else toast(e.message, 'error');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

// Copies text (a snippet, a query) to the clipboard; Home Assistant's frame may not allow
// the clipboard API, then the older way. Says whether it worked.
async function copyPlain(text, what) {
    let ok = false;
    try {
        await navigator.clipboard.writeText(text);
        ok = true;
    } catch (e) {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
        area.remove();
    }
    toast(ok ? `${what} copied` : `This browser doesn't allow copying here: select the ${what.toLowerCase()} and copy it`, ok ? 'success' : 'error');
}

// Quotes a filter value for the query language when it holds spaces or quotes
function queryValue(v) {
    const s = String(v);
    return /[\s"]/.test(s) || s === '' ? `"${s.replace(/"/g, '\\"')}"` : s;
}

// A stable colour per index from the series palette
const SERIES_VARS = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--series-6', '--series-7', '--series-8'];
const seriesColor = i => cssVar(SERIES_VARS[i % SERIES_VARS.length]);
