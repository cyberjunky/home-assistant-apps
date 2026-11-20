// Repair - shared state, formatting, theme and views.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'repair_';

// What the server told us. status: the summary of the latest scan; findings: all of them
const app = {status: null, findings: [], log: [], actions: []};

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
function fmtBytes(bytes, digits) {
    if (missing(bytes)) return '–';
    const units = ['B', 'kB', 'MB', 'GB', 'TB'];
    let v = Number(bytes), i = 0;
    while (Math.abs(v) >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${num(v, digits === undefined ? (v >= 100 || i === 0 ? 0 : 1) : digits)} ${units[i]}`;
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
function panel(title, hint, body, cls) {
    return `<div class="panel ${cls || ''}"><div class="panel-body"><div class="chart-title"><span>${title}</span><span class="hint">${hint || ''}</span></div>${body}</div></div>`;
}
function progress(ratio, left, right, cls) {
    const width = Math.max(0, Math.min(100, (ratio || 0) * 100));
    return `<div class="share-bar ${cls || ''}"><i style="width:${width.toFixed(2)}%"></i></div><div class="share-row"><span>${left}</span><span>${right}</span></div>`;
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
const TABS = ['overview', 'issues', 'log', 'cleanup', 'activity', 'info'];
const pageHooks = {};
let activeTab = null;

function onTab(name, render) { pageHooks[name] = render; }

function renderActive() {
    if (!app.status || !pageHooks[activeTab]) return;
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
    if (typeof hideTip === 'function') hideTip();
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

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
