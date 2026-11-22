// Lottery dashboard - shared state, formatting, theme and views.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'lottery_';

const app = {status: null};

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
const miners = () => (app.status && app.status.miners) || [];
const minerBySlug = slug => miners().find(m => m.slug === slug);
// A fixed colour per miner, in the order of the configuration
const SERIES = ['series-1', 'series-2', 'series-3', 'series-4', 'series-5'];
const minerColor = slug => color(SERIES[Math.max(0, miners().findIndex(m => m.slug === slug)) % SERIES.length]);

// =====================================================================
// Formatting
// =====================================================================
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(v, digits) {
    if (missing(v)) return '–';
    return Number(v).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
// Three significant digits: 4.21, 42.1, 421
function sig3(v) {
    const abs = Math.abs(v);
    return num(v, abs >= 100 ? 0 : abs >= 10 ? 1 : 2);
}
const SI = ['', 'k', 'M', 'G', 'T', 'P', 'E'];
// A difficulty the way AxeOS shows it: 4.21 G
function diff(v) {
    if (missing(v) || !v) return '–';
    let i = 0;
    while (Math.abs(v) >= 1000 && i < SI.length - 1) { v /= 1000; i++; }
    return `${sig3(v)}${SI[i] ? ' ' + SI[i] : ''}`;
}
// A hash rate given in GH/s, in the unit that reads best
function rateParts(gh) {
    if (missing(gh)) return ['–', ''];
    const units = [[1e12, 'ZH/s'], [1e9, 'EH/s'], [1e6, 'PH/s'], [1e3, 'TH/s'], [1, 'GH/s'], [1e-3, 'MH/s']];
    const [scale, unit] = units.find(([s]) => Math.abs(gh) >= s) || units[units.length - 1];
    return [sig3(gh / scale), unit];
}
const rate = gh => rateParts(gh).join(' ');
const rateHtml = gh => { const [n, u] = rateParts(gh); return `${n}<small> ${u}</small>`; };
const rateHs = hs => rate(missing(hs) ? hs : hs / 1e9);
// A count of hashes with an SI prefix: 65.2 T
function hashes(v) {
    if (missing(v)) return '–';
    const units = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y'];
    let i = 0;
    while (Math.abs(v) >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${sig3(v)}${units[i] ? ' ' + units[i] : ''}`;
}
// A power of ten as a round SI number: 1k, 1M
const siRound = e => e === 0 ? '1' : `${10 ** (e % 3)}${['', 'k', 'M', 'G', 'T', 'P', 'E'][Math.floor(e / 3)]}`;
// Big counts: 1.24 M, 3,400
function big(v) {
    if (missing(v)) return '–';
    if (Math.abs(v) < 100000) return num(v, Math.abs(v) < 10 ? 1 : 0);
    const names = [[1e15, 'quadrillion'], [1e12, 'T'], [1e9, 'G'], [1e6, 'M']];
    const [scale, unit] = names.find(([s]) => Math.abs(v) >= s) || [1, ''];
    return `${sig3(v / scale)} ${unit}`;
}
// A chance as "1 in N"
const oneIn = p => missing(p) || !(p > 0) ? '–' : p >= 0.5 ? `${num(p * 100, 0)}%` : `1 in ${big(1 / p)}`;
// A small percentage with enough digits to see it
function smallPct(v) {
    if (missing(v)) return '–';
    if (v === 0) return '0%';
    const digits = Math.min(10, Math.max(1, 2 - Math.floor(Math.log10(Math.abs(v)))));
    return `${num(v, digits)}%`;
}
function years(y) {
    if (missing(y)) return '–';
    if (y < 1 / 12) return fmtDuration(y * 365.25 * 86400);
    if (y < 2) return `${num(y * 12, 0)} months`;
    return `${big(y)} years`;
}

function fmtDuration(seconds) {
    if (missing(seconds)) return '–';
    const minutes = Math.round(seconds / 60);
    if (seconds >= 0 && seconds < 90) return `${Math.round(seconds)} s`;
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
    const days = Math.floor(hours / 24);
    if (days < 365) return `${days}d ${hours % 24}h`;
    return years(days / 365.25);
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
function table(head, rows, opts) {
    opts = opts || {};
    if (!rows.length) return `<div class="empty-state">${escapeHtml(opts.empty || 'Nothing here yet')}${opts.emptySub ? `<span class="empty-sub">${escapeHtml(opts.emptySub)}</span>` : ''}</div>`;
    const th = head.map(h => `<th class="${h.n ? 'n' : ''}">${h.label}</th>`).join('');
    return `<table class="data ${opts.cls || ''}"><thead><tr>${th}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
}
function td(html, cls) { return `<td class="${cls || ''}">${html}</td>`; }

// What a miner is doing: mining, paused, or not answering
function minerState(m) {
    if (!m.connected) return {kind: 'bad', text: m.last_seen ? 'OFFLINE' : 'NOT SEEN YET'};
    if (m.values.mining === false) return {kind: 'warn', text: 'PAUSED'};
    if (m.values.overheat) return {kind: 'bad', text: 'OVERHEATED'};
    if (!(m.values.hashrate > 0)) return {kind: 'warn', text: 'STARTING'};
    return {kind: 'ok', text: 'MINING'};
}
function stateBadge(m) {
    const s = minerState(m);
    return `<span class="badge ${s.kind}">${s.text}</span>`;
}
// The model as the miner reports it: NerdQAxe+ · 4 × BM1368
function modelText(m) {
    const v = m.values;
    const chips = v.asic_count ? `${num(v.asic_count)} × ${v.asic_model || 'ASIC'}` : v.asic_model;
    return [v.model || v.board, chips].filter(Boolean).join(' · ');
}

const EVENT_ICONS = {best: '🏆', block: '🎉', paused: '⏸', resumed: '▶', restart: '↻', offline: '●', online: '●', pool: '⇄', overheat: '🔥', firmware: '⬆'};
function eventHtml(e, withMiner) {
    const m = withMiner ? minerBySlug(e.miner) : null;
    return `<div class="event">
        <span class="t" title="${escapeHtml(new Date(e.ts * 1000).toLocaleString())}">${escapeHtml(Date.now() / 1000 - e.ts > 86400 ? fmtDay(e.ts) : fmtTime(e.ts))}</span>
        <span class="k ${escapeHtml(e.level || '')}">${escapeHtml(EVENT_ICONS[e.kind] || '•')}</span>
        <span class="x">${m ? `<span class="who" style="color:${minerColor(m.slug)}">${escapeHtml(m.name)}</span>` : ''}${escapeHtml(e.text)}</span>
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
// Views: Overview, one tab per miner (all on one page, #tab-miner), Info
// =====================================================================
const tabHooks = {};
const liveHooks = {};
let activeTab = null;
let currentMiner = null;

function onTab(name, show, live) {
    tabHooks[name] = show;
    liveHooks[name] = live;
}
const tabNames = () => ['overview', ...miners().map(m => 'miner-' + m.slug), 'info'];
const pageOf = tab => tab.startsWith('miner-') ? 'miner' : tab;

function buildMinerTabs() {
    const el = document.getElementById('miner-tabs');
    const key = miners().map(m => m.slug + ':' + m.name).join('|');
    if (el.dataset.key === key) return;
    el.dataset.key = key;
    el.innerHTML = miners().map(m =>
        `<button type="button" class="tab" data-tab="miner-${escapeHtml(m.slug)}" onclick="switchTab('miner-${escapeHtml(m.slug)}')"><span class="tab-dot" style="background:${minerColor(m.slug)}"></span>${escapeHtml(m.name)}</button>`
    ).join('');
    markTabs();
}
function markTabs() {
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === activeTab));
}

function switchTab(tabName) {
    if (!tabNames().includes(tabName)) tabName = 'overview';
    const page = pageOf(tabName);
    const changedMiner = page === 'miner' && currentMiner !== tabName.slice(6);
    activeTab = tabName;
    if (page === 'miner') currentMiner = tabName.slice(6);
    markTabs();
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + page));
    try { localStorage.setItem(LS + 'tab', tabName); } catch (e) { /* only a convenience */ }
    if (window.location.hash.slice(1) !== tabName) history.replaceState(null, '', '#' + tabName);
    hideTip();
    if (changedMiner) document.getElementById('pages').scrollTop = 0;
    if (tabHooks[page]) tabHooks[page](changedMiner);
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
    buildMinerTabs();
    renderStatusbar();
    const page = activeTab ? pageOf(activeTab) : null;
    if (page && liveHooks[page]) liveHooks[page]();
}

function renderStatusbar() {
    const s = app.status;
    if (!s) return;
    const all = miners();
    const mining = s.totals.mining;
    const parts = [];
    if (!all.length) {
        parts.push('<span class="sb-warn">● no miners configured</span>');
    } else {
        parts.push(`<span><span class="${mining === all.length ? 'sb-ok' : mining ? 'sb-warn' : 'sb-bad'}">●</span> <b>${mining} / ${all.length}</b> mining</span>`);
    }
    if (s.network.height) parts.push(`<span class="sb-extra">block <b>${num(s.network.height)}</b></span>`);
    if (s.mqtt.broker) parts.push(`<span class="sb-extra">MQTT <b class="${s.mqtt.connected ? 'sb-ok' : 'sb-bad'}">${s.mqtt.connected ? 'connected' : 'offline'}</b></span>`);
    parts.push(`<span class="spacer"></span><span class="sb-extra">Lottery app ${escapeHtml(s.version)}</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
