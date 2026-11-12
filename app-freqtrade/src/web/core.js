// Freqtrade dashboard - shared state, formatting, theme and views.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'freqtrade_';

// What the server told us. status: every bot's summary; bot: the open bot's details; trades: per bot
const app = {status: null, bot: null, trades: {}};

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
const bots = () => (app.status && app.status.bots) || [];
const botBySlug = slug => bots().find(b => b.slug === slug);
const readOnly = () => Boolean(app.status && app.status.config.read_only);
// A fixed colour per bot, in the order of the configuration
const SERIES = ['series-1', 'series-2', 'series-3', 'series-4', 'series-5'];
const botColor = slug => color(SERIES[Math.max(0, bots().findIndex(b => b.slug === slug)) % SERIES.length]);

// =====================================================================
// Formatting
// =====================================================================
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(v, digits) {
    if (missing(v)) return '–';
    return Number(v).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
function withUnit(text, unitText) { return text === '–' ? '–' : `${text}<small> ${escapeHtml(unitText)}</small>`; }
// Digits for an amount in a stake currency: the bot's own, or fewer for big numbers
function moneyDigits(v, decimals) {
    const abs = Math.abs(v || 0);
    if (!missing(decimals)) return abs >= 10000 ? Math.min(decimals, 2) : decimals;
    return abs >= 10000 ? 0 : abs >= 100 ? 2 : abs >= 1 ? 3 : 6;
}
const money = (v, currency, decimals) => missing(v) ? '–' : `${num(v, moneyDigits(v, decimals))}${currency ? ' ' + currency : ''}`;
const signed = (v, digits) => missing(v) ? '–' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${num(Math.abs(v), digits)}`;
const signedMoney = (v, currency, decimals) => missing(v) ? '–' : `${signed(v, moneyDigits(v, decimals))}${currency ? ' ' + currency : ''}`;
// For tiles: the number large, the currency small
const amountHtml = (v, currency, decimals, withSign) => missing(v) ? '–'
    : `${escapeHtml(withSign ? signed(v, moneyDigits(v, decimals)) : num(v, moneyDigits(v, decimals)))}${currency ? `<small> ${escapeHtml(currency)}</small>` : ''}`;
const pct = (ratio, digits) => missing(ratio) ? '–' : `${signed(ratio * 100, digits === undefined ? 2 : digits)}%`;
const plainPct = (ratio, digits) => missing(ratio) ? '–' : `${num(ratio * 100, digits === undefined ? 1 : digits)}%`;
const pnlClass = v => missing(v) || Math.abs(v) < 1e-12 ? '' : v > 0 ? 'pos' : 'neg';
const pnl = (html, v) => `<span class="${pnlClass(v)}">${html}</span>`;
// A price: enough significant digits for a coin of a fraction of a cent
function price(v) {
    if (missing(v)) return '–';
    const abs = Math.abs(v);
    const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : Math.min(10, 2 - Math.floor(Math.log10(abs || 1)) + 2);
    return num(v, digits);
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
const fmtDateTime = ts => missing(ts) || !ts ? '–' : `${fmtDay(ts)} ${fmtTime(ts)}`;
function fmtAgo(ts) {
    if (missing(ts) || !ts) return '–';
    const seconds = Date.now() / 1000 - ts;
    return seconds < 60 ? 'just now' : `${fmtDuration(seconds)} ago`;
}
function fmtIsoDate(iso) {
    const d = new Date(iso + 'T00:00:00');
    const opts = {weekday: 'short', day: 'numeric', month: 'short'};
    if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString([], opts);
}
// Freqtrade's millisecond timestamps
const ms = v => missing(v) || !v ? null : v / 1000;

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
const STATES = {running: 'ok', paused: 'warn', stopped: 'bad', reload_config: 'warn'};
function stateBadge(state) {
    if (!state) return '<span class="badge mode">UNKNOWN</span>';
    const kind = STATES[state] || 'warn';
    return `<span class="badge ${kind === 'ok' ? 'ok' : kind === 'bad' ? 'bad' : 'warn'}">${escapeHtml(state.toUpperCase())}</span>`;
}
function modeBadge(config) {
    if (!config || missing(config.dry_run)) return '';
    return config.dry_run ? '<span class="badge mode">DRY-RUN</span>' : '<span class="badge live">LIVE</span>';
}
function sideTag(isShort) {
    return isShort ? '<span class="side short">short</span>' : '<span class="side long">long</span>';
}
function table(head, rows, opts) {
    opts = opts || {};
    if (!rows.length) return `<div class="empty-state">${escapeHtml(opts.empty || 'Nothing here yet')}</div>`;
    const th = head.map(h => `<th class="${h.n ? 'n' : ''}">${h.label}</th>`).join('');
    const foot = opts.foot ? `<tfoot><tr>${opts.foot.map((c, i) => `<td class="${head[i] && head[i].n ? 'n' : ''}">${c}</td>`).join('')}</tr></tfoot>` : '';
    return `<table class="data ${opts.cls || ''}"><thead><tr>${th}</tr></thead><tbody>${rows.join('')}</tbody>${foot}</table>`;
}
function td(html, cls) { return `<td class="${cls || ''}">${html}</td>`; }

function eventHtml(e, withBot) {
    const bot = withBot ? botBySlug(e.bot) : null;
    const kind = (e.type || '').replace(/_/g, ' ');
    return `<div class="event">
        <span class="t" title="${escapeHtml(new Date(e.ts * 1000).toLocaleString())}">${escapeHtml(Date.now() / 1000 - e.ts > 86400 ? fmtDay(e.ts) : fmtTime(e.ts))}</span>
        <span class="k ${escapeHtml(e.level || '')}">${escapeHtml(kind)}</span>
        <span class="x">${bot ? `<span class="who">${escapeHtml(bot.name)}</span>` : ''}${escapeHtml(e.text)}</span>
    </div>`;
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
// Views: Overview, one tab per bot (all on one page, #tab-bot), Info
// =====================================================================
const tabHooks = {};
const liveHooks = {};
let activeTab = null;
let currentBot = null;

function onTab(name, show, live) {
    tabHooks[name] = show;
    liveHooks[name] = live;
}
const tabNames = () => ['overview', ...bots().map(b => 'bot-' + b.slug), 'info'];
const pageOf = tab => tab.startsWith('bot-') ? 'bot' : tab;

function buildBotTabs() {
    const el = document.getElementById('bot-tabs');
    const key = bots().map(b => b.slug + ':' + b.name).join('|');
    if (el.dataset.key === key) return;
    el.dataset.key = key;
    el.innerHTML = bots().map(b =>
        `<button type="button" class="tab" data-tab="bot-${escapeHtml(b.slug)}" onclick="switchTab('bot-${escapeHtml(b.slug)}')">${escapeHtml(b.name)}</button>`
    ).join('');
    markTabs();
}
function markTabs() {
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === activeTab));
}

function switchTab(tabName) {
    if (!tabNames().includes(tabName)) tabName = 'overview';
    const page = pageOf(tabName);
    const changedBot = page === 'bot' && currentBot !== tabName.slice(4);
    activeTab = tabName;
    if (page === 'bot') currentBot = tabName.slice(4);
    markTabs();
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + page));
    try { localStorage.setItem(LS + 'tab', tabName); } catch (e) { /* only a convenience */ }
    if (window.location.hash.slice(1) !== tabName) history.replaceState(null, '', '#' + tabName);
    hideTip();
    if (changedBot) document.getElementById('pages').scrollTop = 0;
    if (tabHooks[page]) tabHooks[page](changedBot);
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
// Status
// =====================================================================
async function loadStatus() {
    app.status = await api('status');
    buildBotTabs();
    renderStatusbar();
    const page = activeTab ? pageOf(activeTab) : null;
    if (page && liveHooks[page]) liveHooks[page]();
}

function renderStatusbar() {
    const s = app.status;
    if (!s) return;
    const all = bots();
    const up = all.filter(b => b.connected).length;
    const ws = all.filter(b => b.ws_connected).length;
    const parts = [];
    if (!all.length) {
        parts.push('<span class="sb-warn">● no bots configured</span>');
    } else {
        parts.push(`<span><span class="${up === all.length ? 'sb-ok' : up ? 'sb-warn' : 'sb-bad'}">●</span> <b>${up} / ${all.length}</b> bots reachable</span>`);
        parts.push(`<span class="sb-extra">WebSocket <b class="${ws === all.length ? 'sb-ok' : 'sb-warn'}">${ws} / ${all.length}</b></span>`);
    }
    if (s.mqtt.broker) parts.push(`<span class="sb-extra">MQTT <b class="${s.mqtt.connected ? 'sb-ok' : 'sb-bad'}">${s.mqtt.connected ? 'connected' : 'offline'}</b></span>`);
    if (s.config.read_only) parts.push('<span class="sb-extra">read-only</span>');
    parts.push(`<span class="spacer"></span><span class="sb-extra">Freqtrade app ${escapeHtml(s.version)} · unofficial</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

// =====================================================================
// Sums over bots: amounts only add up within one stake currency
// =====================================================================
function byCurrency(list, pick) {
    const out = new Map();
    list.forEach(b => {
        const v = pick(b);
        if (missing(v)) return;
        const c = b.config.stake_currency || '?';
        out.set(c, (out.get(c) || 0) + Number(v));
    });
    return out;
}
// The currency most bots use first; an amount per currency, joined
function currencyText(map, fmt) {
    if (!map.size) return '–';
    return [...map.entries()].sort((a, b) => countIn(b[0]) - countIn(a[0])).map(([c, v]) => fmt(v, c)).join(' · ');
}
const countIn = c => bots().filter(b => b.config.stake_currency === c).length;
const mainCurrency = () => {
    const counts = new Map();
    bots().forEach(b => b.config.stake_currency && counts.set(b.config.stake_currency, (counts.get(b.config.stake_currency) || 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(e => e[0])[0] || '';
};
