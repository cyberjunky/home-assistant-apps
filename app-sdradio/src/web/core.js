// SDRadio dashboard - shared state, the connection to the radio, formatting, theme, tabs, modals.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const LS = 'sdradio_';

// What the server told us: filled by loadStatus() and the radio's state messages
const app = {version: '', status: null, state: null, modes: {}, steps: [], bands: [], channels: [], settings: {}};

async function api(path, options) {
    const opts = Object.assign({}, options || {});
    if (opts.json !== undefined) {
        opts.body = JSON.stringify(opts.json);
        opts.headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
        delete opts.json;
    }
    const response = await fetch(basePath + 'api/' + path, opts);
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) {
        const error = new Error((data && data.error) || `Request failed (${response.status})`);
        error.status = response.status;
        throw error;
    }
    return data;
}

function lsGet(key, fallback) {
    try { const v = localStorage.getItem(LS + key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
}
function lsSet(key, value) {
    try { localStorage.setItem(LS + key, JSON.stringify(value)); } catch (e) { /* only a convenience */ }
}

// =====================================================================
// Formatting
// =====================================================================
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(value, digits) {
    if (missing(value)) return '–';
    return Number(value).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
// 145500000 → "145.500" (MHz, kHz precision; more when the frequency needs it)
function fmtMHz(hz) {
    if (missing(hz)) return '–';
    const mhz = hz / 1e6;
    const decimals = hz % 1000 ? (hz % 100 ? 6 : 4) : 3;
    return mhz.toFixed(decimals);
}
function fmtHz(hz) {
    if (missing(hz)) return '–';
    if (hz >= 1e6) return `${+(hz / 1e6).toFixed(3)} MHz`;
    if (hz >= 1e3) return `${+(hz / 1e3).toFixed(3)} kHz`;
    return `${hz} Hz`;
}
function fmtKHz(hz) {
    if (missing(hz)) return '–';
    return hz >= 1e6 ? `${+(hz / 1e6).toFixed(2)}M` : hz >= 1000 ? `${+(hz / 1000).toFixed(2)}k` : `${hz}`;
}
function fmtDuration(seconds) {
    if (missing(seconds)) return '–';
    if (seconds < 90) return `${num(seconds, seconds < 10 && seconds > 0 ? 1 : 0)} s`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 120) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    return `${hours} h ${minutes % 60} min`;
}
const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const fmtDateTime = ts => {
    const d = new Date(ts * 1000);
    const today = new Date().toDateString() === d.toDateString();
    return today ? fmtTime(ts) : d.toLocaleString([], {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'});
};
function fmtAgo(ts) {
    if (!ts) return '—';
    const seconds = Math.max(0, Date.now() / 1000 - ts);
    if (seconds < 45) return 'now';
    if (seconds < 5400) return `${Math.round(seconds / 60)} m`;
    if (seconds < 172800) return `${Math.round(seconds / 3600)} h`;
    return `${Math.round(seconds / 86400)} d`;
}
function fmtBytes(bytes) {
    if (missing(bytes)) return '–';
    const units = ['B', 'KB', 'MB', 'GB'];
    let v = Number(bytes), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${num(v, i === 0 ? 0 : 1)} ${units[i]}`;
}
const modeLabel = m => (app.modes[m] && app.modes[m].label) || String(m || '').toUpperCase();
const chNum = n => String(n).padStart(3, '0');

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : String(text);
    return div.innerHTML;
}
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function tile(label, valueHtml, detail, cls) {
    return `<div class="tile ${cls || ''}"><div class="l">${label}</div><div class="v">${valueHtml}</div><div class="d">${detail || '&nbsp;'}</div></div>`;
}
function infoRow(label, valueHtml) {
    return `<div class="info-row"><span class="info-label">${label}</span><span class="info-value">${valueHtml}</span></div>`;
}

// =====================================================================
// Theme: light by default, dark as the second choice
// =====================================================================
function setTheme(theme) {
    document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light';
    try { localStorage.setItem(LS + 'theme', document.documentElement.dataset.theme); } catch (e) { /* convenience */ }
    renderThemeToggle();
    if (typeof redrawSpectrum === 'function') redrawSpectrum();
}
function renderThemeToggle() {
    const dark = document.documentElement.dataset.theme === 'dark';
    document.getElementById('theme-light').classList.toggle('active', !dark);
    document.getElementById('theme-dark').classList.toggle('active', dark);
}
function applyLcdColour(colour) {
    if (colour && colour !== 'lcd') document.documentElement.dataset.lcd = colour;
    else delete document.documentElement.dataset.lcd;
}

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

// A dialog: title, body HTML and buttons [{label, cls, action}] where action returning false keeps it open
function openModal(title, bodyHtml, buttons) {
    closeModal();
    const root = document.getElementById('modal-root');
    root.innerHTML = `<div class="modal-overlay open" id="modal-overlay">
        <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
            <div class="modal-head"><span class="modal-title" id="modal-title">${escapeHtml(title)}</span>
                <button type="button" class="modal-close" aria-label="Close" onclick="closeModal()">×</button></div>
            <div class="modal-body">${bodyHtml}<div class="form-error" id="modal-error" hidden></div></div>
            <div class="modal-foot" id="modal-foot"></div>
        </div></div>`;
    const foot = document.getElementById('modal-foot');
    (buttons || []).forEach(b => {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'btn ' + (b.cls || '');
        el.textContent = b.label;
        el.onclick = async () => {
            if (!b.action) return closeModal();
            el.disabled = true;
            try {
                const keep = await b.action();
                if (keep !== false) closeModal();
            } catch (e) {
                modalError(e.message);
            } finally {
                el.disabled = false;
            }
        };
        foot.appendChild(el);
    });
    document.getElementById('modal-overlay').addEventListener('mousedown', e => { if (e.target.id === 'modal-overlay') closeModal(); });
    const first = root.querySelector('.modal-body input, .modal-body select, .modal-body textarea');
    if (first) first.focus();
}
function modalError(message) {
    const el = document.getElementById('modal-error');
    if (!el) return toast(message, 'error');
    el.textContent = message;
    el.hidden = !message;
}
function closeModal() { document.getElementById('modal-root').innerHTML = ''; }
function confirmModal(title, text, label, action, danger) {
    openModal(title, `<p>${text}</p>`, [{label: 'Cancel'}, {label, cls: danger ? 'btn-danger-solid' : 'btn-primary', action}]);
}

// =====================================================================
// Views
// =====================================================================
const TABS = ['radio', 'channels', 'stats', 'settings', 'info'];
const tabHooks = {};
let activeTab = null;

function onTab(name, fn) { tabHooks[name] = fn; }

function switchTab(tabName) {
    if (!TABS.includes(tabName)) tabName = 'radio';
    activeTab = tabName;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    lsSet('tab', tabName);
    if (window.location.hash.slice(1) !== tabName) history.replaceState(null, '', '#' + tabName);
    wantSpectrum(tabName === 'radio' && !document.hidden);
    if (tabHooks[tabName]) tabHooks[tabName]();
}

function buildChips(el, options, current, onChange) {
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
// The live connection to the radio
// =====================================================================
let socket = null;
let socketWanted = {spectrum: false, listen: false, sweep: false};
let reconnectDelay = 1000;
const socketHandlers = {state: [], spectrum: [], audio: [], hit: [], changed: [], notice: []};

function onSocket(kind, fn) { socketHandlers[kind].push(fn); }

function connectSocket() {
    const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + basePath + 'api/ws';
    socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => {
        reconnectDelay = 1000;
        sendSocket({cmd: 'spectrum', on: socketWanted.spectrum});
        sendSocket({cmd: 'listen', on: socketWanted.listen});
        sendSocket({cmd: 'sweep', on: socketWanted.sweep});
        renderStatusbar();
    };
    socket.onmessage = (event) => {
        if (typeof event.data === 'string') {
            let data;
            try { data = JSON.parse(event.data); } catch (e) { return; }
            if (data.type === 'state') {
                app.state = data.state;
                socketHandlers.state.forEach(fn => fn(data.state));
            } else if (socketHandlers[data.type]) {
                socketHandlers[data.type].forEach(fn => fn(data));
            }
            return;
        }
        const view = new DataView(event.data);
        const kind = view.getUint8(0);
        if (kind === 1) {
            const frame = {
                center: view.getFloat64(1, true), rate: view.getFloat64(9, true),
                floor: view.getFloat32(17, true), ceil: view.getFloat32(21, true),
                bins: new Uint8Array(event.data, 29),
            };
            socketHandlers.spectrum.forEach(fn => fn(frame));
        } else if (kind === 2) {
            const rate = view.getFloat32(1, true);
            const pcm = new Int16Array(event.data.slice(5));
            socketHandlers.audio.forEach(fn => fn(rate, pcm));
        }
    };
    socket.onclose = () => {
        socket = null;
        renderStatusbar();
        setTimeout(connectSocket, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    };
}

function sendSocket(message) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}
function radioCommand(cmd, args) { sendSocket(Object.assign({cmd}, args || {})); }
// The band view: the radio sweeps the receiver across the band while a page shows it
function wantSweep(on) {
    if (socketWanted.sweep === on) return;
    socketWanted.sweep = on;
    sendSocket({cmd: 'sweep', on});
}
function wantSpectrum(on) {
    if (socketWanted.spectrum === on) return;
    socketWanted.spectrum = on;
    sendSocket({cmd: 'spectrum', on});
}
document.addEventListener('visibilitychange', () => wantSpectrum(activeTab === 'radio' && !document.hidden));

// =====================================================================
// Status, the hint and the status bar
// =====================================================================
async function loadStatus() {
    try {
        const s = await api('status');
        app.status = s;
        app.version = s.version;
        app.modes = s.modes;
        app.steps = s.steps;
        app.bands = s.bands;
        if (!app.state) app.state = s.state;
    } catch (e) {
        app.status = {error: e.message};
    }
    renderChrome();
    return app.status;
}

async function loadChannels() {
    try {
        const data = await api('channels');
        app.channels = data.channels;
        app.nextNumber = data.next_number;
    } catch (e) {
        toast(e.message, 'error');
    }
    return app.channels;
}

function renderChrome() {
    const s = app.state || {};
    const device = s.device || {};
    const status = app.status || {};
    document.getElementById('brand-device').textContent = device.ok ? (device.hardware || device.driver || status.receiver || '') : (status.receiver || '');
    const hint = document.getElementById('setup-hint');
    const text = document.getElementById('setup-hint-text');
    if (device.error) {
        hint.hidden = false;
        text.innerHTML = `<b>No receiver.</b> ${escapeHtml(device.error)} SDRadio tries again every few seconds; the receiver is set on the app's Configuration tab.`;
    } else {
        hint.hidden = true;
    }
    renderStatusbar();
}

function renderStatusbar() {
    const s = app.state || {};
    const device = s.device || {};
    const status = app.status || {};
    const opts = (status.options || {}).receiver || {};
    const parts = [];
    if (!socket || socket.readyState !== WebSocket.OPEN) parts.push('<span class="sb-bad">● not connected to the app</span>');
    else if (device.ok) parts.push(`<span><span class="sb-ok">●</span> <b>${escapeHtml(device.hardware || device.driver || '')}</b></span>`);
    else parts.push(`<span class="sb-bad">● no receiver</span>`);
    if (s.rate) parts.push(`<span>rate <b>${num(s.rate / 1e6, 1)} MS/s</b></span>`);
    if (opts.ppm_correction) parts.push(`<span class="sb-extra">ppm <b>${opts.ppm_correction > 0 ? '+' : ''}${opts.ppm_correction}</b></span>`);
    if (s.audio_rate) parts.push(`<span class="sb-extra">audio <b>${num(s.audio_rate / 1000, 1)} kHz</b></span>`);
    if (status.counts) parts.push(`<span class="sb-extra">database <b>${num(app.channels.length || status.counts.channels)} channels</b></span>`);
    if (status.last_backup) parts.push(`<span class="sb-extra">last backup <b>${escapeHtml(fmtDateTime(status.last_backup))}</b></span>`);
    parts.push(`<span class="spacer"></span><span class="sb-extra">SDRadio ${escapeHtml(app.version)}</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
