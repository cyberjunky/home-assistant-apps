// Bitcoin Node dashboard - shared state, formatting, theme and views.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'bitcoinnode_';

// What the server told us. status: everything current; the rest is read per page
const app = {status: null, blocks: [], peers: [], lightning: null, tor: null, events: []};

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

const color = name => `var(--${name})`;

function stored(key) {
    try { return localStorage.getItem(LS + key); } catch (e) { return null; }
}
function store(key, value) {
    try { localStorage.setItem(LS + key, value); } catch (e) { /* only a convenience */ }
}

// =====================================================================
// Formatting
// =====================================================================
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(v, digits) {
    if (missing(v)) return '–';
    return Number(v).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
const signed = (v, digits) => missing(v) ? '–' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${num(Math.abs(v), digits)}`;
const plainPct = (ratio, digits) => missing(ratio) ? '–' : `${num(ratio * 100, digits === undefined ? 1 : digits)}%`;
const pnlClass = v => missing(v) || Math.abs(v) < 1e-12 ? '' : v > 0 ? 'pos' : 'neg';

// Amounts: satoshis as whole numbers, bitcoin with eight decimals
const sat = v => missing(v) ? '–' : `${num(v)} sat`;
const signedSat = v => missing(v) ? '–' : `${signed(v)} sat`;
// A routing fee is often a fraction of a satoshi: show that fraction only when there is one
const satFine = (v, withSign) => missing(v) ? '–' : `${withSign && v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString(undefined, {maximumFractionDigits: Math.abs(v) < 100 && v % 1 ? 3 : 0})} sat`;
const btcOfSat = v => missing(v) ? '–' : `${(v / 1e8).toFixed(8)} BTC`;
const btc = (v, digits) => missing(v) ? '–' : `${num(v, digits === undefined ? 4 : digits)} BTC`;
const satHtml = v => missing(v) ? '–' : `${escapeHtml(num(v))}<small> sat</small>`;
// A fee rate: one decimal below 10 sat/vB, whole numbers above
const feeRate = v => missing(v) ? '–' : num(v, v < 10 ? 1 : 0);
const feeHtml = v => missing(v) ? '–' : `${escapeHtml(feeRate(v))}<small> sat/vB</small>`;

function fmtBytes(bytes, digits) {
    if (missing(bytes)) return '–';
    const units = ['B', 'kB', 'MB', 'GB', 'TB'];
    let v = Number(bytes), i = 0;
    while (Math.abs(v) >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${num(v, digits === undefined ? (v >= 100 || i === 0 ? 0 : 1) : digits)} ${units[i]}`;
}
// kB per second, as megabytes once it is large
const fmtRate = kb => missing(kb) ? '–' : kb >= 1000 ? `${num(kb / 1000, 2)} MB/s` : `${num(kb, 0)} kB/s`;
// The y axis of a traffic chart: the unit on every label, in a wider margin
const RATE_AXIS = {
    formatTick: kb => kb >= 1000 ? `${(kb / 1000).toLocaleString(undefined, {maximumFractionDigits: 1})} MB/s` : `${kb.toLocaleString(undefined, {maximumFractionDigits: 0})} kB/s`,
    padLeft: 60,
};
function fmtHashrate(h) {
    if (missing(h)) return '–';
    const units = ['H/s', 'kH/s', 'MH/s', 'GH/s', 'TH/s', 'PH/s', 'EH/s', 'ZH/s'];
    let i = 0;
    while (h >= 1000 && i < units.length - 1) { h /= 1000; i++; }
    return `${num(h, h >= 100 ? 0 : 2)} ${units[i]}`;
}
function fmtBig(v) {
    if (missing(v)) return '–';
    const units = ['', ' K', ' M', ' G', ' T', ' P'];
    let i = 0;
    while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${num(v, 2)}${units[i]}`;
}

function fmtDuration(seconds) {
    if (missing(seconds)) return '–';
    const minutes = Math.round(seconds / 60);
    if (seconds >= 0 && seconds < 90) return `${Math.round(seconds)} s`;
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
const fmtTime = ts => new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
const fmtDay = ts => new Date(ts * 1000).toLocaleDateString([], {day: 'numeric', month: 'short'});
const fmtDate = ts => new Date(ts * 1000).toLocaleDateString([], {day: 'numeric', month: 'short', year: 'numeric'});
const fmtDateTime = ts => missing(ts) || !ts ? '–' : `${fmtDay(ts)} ${fmtTime(ts)}`;
function fmtAgo(ts) {
    if (missing(ts) || !ts) return '–';
    const seconds = Date.now() / 1000 - ts;
    return seconds < 60 ? 'just now' : `${fmtDuration(seconds)} ago`;
}
// The middle of a long address or key left out
const shorten = (text, head, tail) => !text || text.length <= head + tail + 1 ? (text || '') : `${text.slice(0, head)}…${text.slice(-tail)}`;

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : String(text);
    return div.innerHTML;
}

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
function badge(kind, text) {
    return `<span class="badge ${kind}">${escapeHtml(text)}</span>`;
}
function table(head, rows, opts) {
    opts = opts || {};
    if (!rows.length) return `<div class="empty-state">${escapeHtml(opts.empty || 'Nothing here yet')}</div>`;
    const th = head.map(h => `<th class="${h.n ? 'n' : ''}">${h.label}</th>`).join('');
    return `<table class="data ${opts.cls || ''}"><thead><tr>${th}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
}
function td(html, cls) { return `<td class="${cls || ''}">${html}</td>`; }
// A card with a title, an optional hint on the right, and a body
function panel(title, hint, body, cls) {
    return `<div class="panel ${cls || ''}"><div class="panel-body"><div class="chart-title"><span>${title}</span><span class="hint">${hint || ''}</span></div>${body}</div></div>`;
}
// A bar filled to a share of its width, with a line of text under it
function progress(ratio, left, right, cls) {
    const width = Math.max(0, Math.min(100, (ratio || 0) * 100));
    return `<div class="share-bar ${cls || ''}"><i style="width:${width.toFixed(2)}%"></i></div><div class="share-row"><span>${left}</span><span>${right}</span></div>`;
}

function eventHtml(e) {
    return `<div class="event">
        <span class="t" title="${escapeHtml(new Date(e.ts * 1000).toLocaleString())}">${escapeHtml(Date.now() / 1000 - e.ts > 86400 ? fmtDay(e.ts) : fmtTime(e.ts))}</span>
        <span class="k ${escapeHtml(e.level || '')}">${escapeHtml((e.type || '').replace(/_/g, ' '))}</span>
        <span class="x">${escapeHtml(e.text)}</span>
    </div>`;
}

// =====================================================================
// Theme: follows Home Assistant's dark mode (this page runs in its ingress
// iframe, same origin), the system outside Home Assistant, or a manual choice
// =====================================================================
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = new URLSearchParams(window.location.search).get('theme') || stored('theme');
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
    store('theme', themeChoice);
    initThemeToggle();
    applyTheme();
}

applyTheme();
// Home Assistant doesn't tell the iframe when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// =====================================================================
// Views. Each page gives a function that draws it; it runs when the page
// opens and again after every poll while the page is open
// =====================================================================
const TABS = ['overview', 'node', 'mempool', 'peers', 'lightning', 'tor', 'info'];
const pageHooks = {};
let activeTab = null;

function onTab(name, render) { pageHooks[name] = render; }

// Without a first answer from Bitcoin Core there is nothing to draw: only Info has something to say
const hasNode = () => Boolean(app.status && !missing(app.status.node.blocks));

function renderActive() {
    if (!app.status || !pageHooks[activeTab]) return;
    const waiting = !hasNode() && activeTab !== 'info';
    document.getElementById('nodata').hidden = !waiting;
    document.getElementById('pages').classList.toggle('waiting', waiting);
    if (waiting) return;
    Promise.resolve(pageHooks[activeTab]()).catch(e => toast(e.message));
}

function switchTab(tabName) {
    if (!TABS.includes(tabName)) tabName = 'overview';
    const changed = activeTab !== tabName;
    activeTab = tabName;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === activeTab));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    store('tab', tabName);
    if (window.location.hash.slice(1) !== tabName) history.replaceState(null, '', '#' + tabName);
    hideTip();
    if (changed) document.getElementById('pages').scrollTop = 0;
    renderActive();
}

function buildChips(containerId, options, current, onChange) {
    const el = document.getElementById(containerId);
    if (!el) return;
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

// A page's fixed frame is built once; after that only its parts are filled, so charts and scroll stay put
function skeleton(tab, html) {
    const el = document.getElementById('tab-' + tab);
    if (!el.dataset.built) {
        el.innerHTML = html;
        el.dataset.built = '1';
        return true;
    }
    return false;
}
function fill(id, html) {
    const el = document.getElementById(id);
    if (el && el._html !== html) { el.innerHTML = html; el._html = html; }
}

// A confirmation. opts.typeToConfirm: a word the user must type before the button works (for what can't be undone)
function confirmDialog(opts) {
    return new Promise((resolve) => {
        const word = opts.typeToConfirm || '';
        const root = document.getElementById('modal-root');
        root.innerHTML = `
            <div class="modal-overlay">
                <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(opts.title || 'Are you sure?')}">
                    <div class="modal-head"><span>${escapeHtml(opts.title || 'Are you sure?')}</span>
                        <button class="modal-close" data-close aria-label="Close">✕</button></div>
                    <div class="modal-body"><div>${opts.message || ''}</div>${word ? `
                        <div class="form-field"><label>Type <span class="mono">${escapeHtml(word)}</span> to confirm</label>
                        <input class="input" data-confirm-word autocomplete="off" spellcheck="false" aria-label="Type ${escapeHtml(word)} to confirm"></div>` : ''}</div>
                    <div class="modal-foot">
                        <button class="btn" data-close>Cancel</button>
                        <button class="btn ${opts.danger ? 'btn-danger-solid' : 'btn-primary'}" data-confirm>${escapeHtml(opts.confirmLabel || 'Confirm')}</button>
                    </div>
                </div>
            </div>`;
        const overlay = root.querySelector('.modal-overlay');
        const finish = (answer) => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); resolve(answer); };
        const onKey = (e) => { if (e.key === 'Escape') finish(false); };
        document.addEventListener('keydown', onKey);
        const button = root.querySelector('[data-confirm]');
        if (word) {
            const input = root.querySelector('[data-confirm-word]');
            button.disabled = true;
            input.addEventListener('input', () => { button.disabled = input.value.trim() !== word; });
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !button.disabled) button.click(); });
            input.focus();
        } else {
            button.focus();
        }
        button.addEventListener('click', () => finish(true));
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.hasAttribute('data-close')) finish(false);
        });
    });
}

function toast(message, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || 'error');
    el.textContent = message;
    document.getElementById('toast-root').appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

// =====================================================================
// History for the charts: one period for every chart, chosen on any page
// =====================================================================
const RANGES = [{key: '24h', label: '24 h'}, {key: '7d', label: '7 d'}, {key: '30d', label: '30 d'}];
let rangeKey = RANGES.some(r => r.key === stored('range')) ? stored('range') : '24h';
const historyCache = {};

async function loadHistory() {
    const cached = historyCache[rangeKey];
    const maxAge = rangeKey === '24h' ? 30000 : 120000;
    if (cached && Date.now() - cached.at < maxAge) return cached.data;
    const data = await api('history?range=' + rangeKey);
    historyCache[rangeKey] = {at: Date.now(), data};
    return data;
}
function rangeChips(containerId) {
    buildChips(containerId, RANGES, rangeKey, (key) => {
        rangeKey = key;
        store('range', key);
        renderActive();
    });
}
const rangeLabel = () => ({'24h': 'last 24 hours', '7d': 'last 7 days', '30d': 'last 30 days'})[rangeKey];

// A line chart of stored series. defs: [{key, name, color, area?}]
function historyChart(id, h, defs, formatY, opts) {
    const el = document.getElementById(id);
    if (!el) return;
    lineChart(el, Object.assign({
        x: h.ts,
        series: defs.map(d => ({name: d.name, color: d.color, values: h[d.key], area: d.area, dash: d.dash, width: d.width})),
        height: 180,
        zero: true,
        empty: 'Fills in from now on: a point every two minutes',
        formatX: fmtDateTime,
        formatY: v => formatY(v),
        xTicks: timeAxisTicks(h.ts[0], h.ts[h.ts.length - 1], 5),
    }, opts || {}));
}
const stats = values => {
    const list = values.filter(v => !missing(v));
    // One value is not yet a low, an average and a high
    if (list.length < 2) return {min: null, max: null, avg: null, last: null};
    return {min: Math.min(...list), max: Math.max(...list), avg: list.reduce((a, b) => a + b, 0) / list.length, last: list[list.length - 1]};
};

// =====================================================================
// Status
// =====================================================================
async function loadStatus() {
    app.status = await api('status');
    renderStatusbar();
    renderNotices();
    renderActive();
}

function renderNotices() {
    const s = app.status, n = s.node, ln = s.lightning, t = s.tor;
    const parts = [];
    const hint = (kind, html) => parts.push(`<div class="setup-hint ${kind}"><span>${html}</span></div>`);
    if (!n.configured) hint('', '<b>No node set up yet.</b> Enter the address, user and password of Bitcoin Core under <b>bitcoin</b> in this app\'s configuration in Home Assistant, then restart the app.');
    else if (!n.connected) hint('bad', `<b>Bitcoin Core does not answer.</b> ${escapeHtml(n.error || 'Waiting for its first answer.')}`);
    else if (!n.synced) hint('', `<b>Bitcoin Core is still syncing:</b> ${escapeHtml(plainPct(n.progress, 2))} done. The pages fill in as it catches up.`);
    if (ln.configured && !ln.connected) hint('bad', `<b>LND does not answer.</b> ${escapeHtml(ln.error || 'Waiting for its first answer.')}`);
    if (t.configured && !t.connected && t.error) hint('bad', `<b>Tor's control port:</b> ${escapeHtml(t.error)}`);
    fill('notices', parts.join(''));
    fill('nodata', `<div class="panel-body"><div class="empty-state">${n.configured ? 'Waiting for the first answer from Bitcoin Core…' : 'Nothing to show until a node is set up.'}</div></div>`);
    document.querySelectorAll('.tab[data-tab="lightning"]').forEach(tab => { tab.hidden = !ln.configured; });
}

function renderStatusbar() {
    const s = app.status;
    const dot = ok => `<span class="${ok ? 'sb-ok' : 'sb-bad'}">●</span>`;
    const parts = [];
    if (!s.node.configured) parts.push('<span class="sb-warn">● no node set up</span>');
    else {
        parts.push(`<span>${dot(s.node.connected)} Bitcoin Core <b>${escapeHtml(s.node.version || '')}</b></span>`);
        if (hasNode()) parts.push(`<span class="sb-extra">block <b>${num(s.node.blocks)}</b></span>`);
    }
    if (s.lightning.configured) parts.push(`<span class="sb-extra">${dot(s.lightning.connected)} LND <b>${escapeHtml(s.lightning.version || '')}</b></span>`);
    if (s.tor.configured) parts.push(`<span class="sb-extra">${dot(s.tor.connected && s.tor.bootstrap === 100)} Tor <b>${escapeHtml(s.tor.version || '')}</b></span>`);
    if (s.mqtt.broker) parts.push(`<span class="sb-extra">MQTT <b class="${s.mqtt.connected ? 'sb-ok' : 'sb-bad'}">${s.mqtt.connected ? 'connected' : 'offline'}</b></span>`);
    parts.push('<span class="sb-extra">read-only</span>');
    parts.push(`<span class="spacer"></span><span class="sb-extra">Bitcoin Node app ${escapeHtml(s.version)} · unofficial</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
