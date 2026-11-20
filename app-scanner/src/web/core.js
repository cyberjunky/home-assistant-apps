// Scanner - shared state, formatting, theme, views, dialogs and the status.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'scanner_';

// What the server told us. status: /api/status (every second while the scanner works);
// devices: /api/devices; settings: /api/settings; tray: /api/tray
const app = {status: null, devices: null, settings: null, meta: null, tray: null};
// The server's answer as JSON; its errors are JSON ({error}) or plain text
async function answer(response) {
    const text = await response.text();
    let data = null;
    try { data = JSON.parse(text); } catch (e) { /* not JSON */ }
    if (!response.ok) throw new Error((data && data.error) || (text && text.length < 200 && !text.startsWith('<') ? text.replace(/^\d+: /, '') : `Request failed (${response.status})`));
    return data;
}
async function api(path) {
    const response = await fetch('api/' + path);
    return answer(response);
}

// A change, as JSON
async function apiPost(path, body) {
    const response = await fetch('api/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    return answer(response);
}

const color = name => `var(--${name})`;
const SERIES = ['series-1', 'series-2', 'series-3', 'series-4', 'series-5'];

// =====================================================================
// Formatting
// =====================================================================
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(v, digits) {
    if (missing(v)) return '–';
    return Number(v).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
function withUnit(text, unitText) { return text === '–' ? '–' : `${text}<small> ${escapeHtml(unitText)}</small>`; }
const plainPct = (ratio, digits) => missing(ratio) ? '–' : `${num(ratio * 100, digits === undefined ? 1 : digits)}%`;

const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
function bytesParts(bytes) {
    if (missing(bytes)) return ['–', ''];
    let v = Number(bytes), i = 0;
    while (Math.abs(v) >= 1024 && i < UNITS.length - 1) { v /= 1024; i++; }
    return [num(v, i === 0 ? 0 : Math.abs(v) >= 100 ? 0 : 1), UNITS[i]];
}
const fmtBytes = bytes => { const [v, u] = bytesParts(bytes); return u ? `${v} ${u}` : v; };
const bytesHtml = bytes => { const [v, u] = bytesParts(bytes); return u ? `${escapeHtml(v)}<small> ${u}</small>` : v; };
// A rate in bytes per second, in bits
function fmtRate(bytesPerSecond) {
    if (missing(bytesPerSecond)) return '–';
    let v = bytesPerSecond * 8, i = 0;
    const units = ['bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s', 'Tbit/s'];
    while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
    return `${num(v, v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}
const fmtByteRate = v => missing(v) ? '–' : `${fmtBytes(v)}/s`;

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
    if (seconds < 0) return `in ${fmtDuration(-seconds)}`;
    return seconds < 60 ? 'just now' : `${fmtDuration(seconds)} ago`;
}
const fmtUptime = s => !s ? '–' : fmtDuration(s);

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : String(text);
    return div.innerHTML;
}
const attr = escapeHtml;

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
function badge(text, kind, title) {
    return `<span class="badge ${kind || ''}"${title ? ` title="${attr(title)}"` : ''}>${escapeHtml(text)}</span>`;
}
function table(head, rows, opts) {
    opts = opts || {};
    if (!rows.length) return `<div class="empty-state">${escapeHtml(opts.empty || 'Nothing here yet')}</div>`;
    const th = head.map(h => `<th class="${h.n ? 'n' : ''}${h.sort ? ' sortable' : ''}${h.sorted ? ' sorted ' + h.sorted : ''}"${h.sort ? ` data-sort="${attr(h.sort)}"` : ''}${h.title ? ` title="${attr(h.title)}"` : ''}>${h.label}</th>`).join('');
    const foot = opts.foot ? `<tfoot><tr>${opts.foot.map((c, i) => `<td class="${head[i] && head[i].n ? 'n' : ''}">${c}</td>`).join('')}</tr></tfoot>` : '';
    return `<table class="data ${opts.cls || ''}"><thead><tr>${th}</tr></thead><tbody>${rows.join('')}</tbody>${foot}</table>`;
}
function td(html, cls) { return `<td class="${cls || ''}">${html}</td>`; }
function panel(title, hint, body, opts) {
    opts = opts || {};
    return `<div class="panel ${opts.cls || ''}"${opts.id ? ` id="${opts.id}"` : ''}><div class="panel-body">
        ${title ? `<div class="chart-title"><span>${title}</span>${hint ? `<span class="hint">${hint}</span>` : ''}</div>` : ''}${body}</div></div>`;
}
function setupHint(text, kind) {
    return `<div class="setup-hint ${kind || ''}"><span>${text}</span></div>`;
}

// A usage bar: green, then amber from 75% and red from 90%
function usageClass(ratio, warn, bad) {
    if (missing(ratio)) return '';
    return ratio >= (bad || 0.9) ? 'bad' : ratio >= (warn || 0.75) ? 'warn' : 'ok';
}
function meter(ratio, label, opts) {
    opts = opts || {};
    const r = missing(ratio) ? 0 : Math.max(0, Math.min(1, ratio));
    return `<div class="meter ${opts.cls || ''}" title="${attr(opts.title || '')}">
        <div class="meter-bar"><i class="${usageClass(ratio, opts.warn, opts.bad)}" style="width:${(r * 100).toFixed(1)}%"></i></div>
        ${label === false ? '' : `<span class="meter-text">${label === undefined ? plainPct(ratio) : label}</span>`}</div>`;
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
// Views. The address is #tab or #tab/sub (a document).
// onTab(name, show(sub, changed), live()): show when the tab opens or its sub changes,
// live after every new status (every few seconds).
// =====================================================================
const TABS = ['scan', 'archive', 'settings', 'info'];
const tabHooks = {};
const liveHooks = {};
let activeTab = null;
let activeSub = '';

function onTab(name, show, live) {
    tabHooks[name] = show;
    liveHooks[name] = live;
}

function switchTab(tabName, sub) {
    if (!TABS.includes(tabName)) tabName = 'scan';
    sub = sub === undefined ? '' : String(sub);
    const changed = tabName !== activeTab || sub !== activeSub;
    activeTab = tabName;
    activeSub = sub;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === activeTab));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    try { localStorage.setItem(LS + 'tab', tabName); } catch (e) { /* only a convenience */ }
    const hash = sub ? `${tabName}/${sub}` : tabName;
    if (decodeURIComponent(window.location.hash.slice(1)) !== hash) history.replaceState(null, '', '#' + hash);
    if (changed) document.getElementById('pages').scrollTop = 0;
    renderNotices();
    if (tabHooks[tabName]) tabHooks[tabName](sub, changed);
}
// Change the sub of the open tab without leaving it (the open document)
function setSub(sub) {
    activeSub = String(sub || '');
    const hash = activeSub ? `${activeTab}/${activeSub}` : activeTab;
    history.replaceState(null, '', '#' + hash);
}

function parseHash() {
    const raw = decodeURIComponent(window.location.hash.slice(1));
    const at = raw.indexOf('/');
    return at < 0 ? [raw, ''] : [raw.slice(0, at), raw.slice(at + 1)];
}

function buildChips(containerId, options, current, onChange) {
    const el = typeof containerId === 'string' ? document.getElementById(containerId) : containerId;
    el.innerHTML = options.map(o =>
        `<button type="button" class="tab chip${o.key === current ? ' active' : ''}" data-key="${attr(o.key)}"${o.title ? ` title="${attr(o.title)}"` : ''}>${escapeHtml(o.label)}${o.count !== undefined ? ` <span class="chip-count">${escapeHtml(o.count)}</span>` : ''}</button>`
    ).join('');
    el.onclick = (e) => {
        const button = e.target.closest('[data-key]');
        if (!button) return;
        el.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === button));
        onChange(button.dataset.key);
    };
}
// The same, as an HTML string for a page that renders itself; wire it with bindChips
function chipsHtml(id, options, current, label) {
    return `<div class="tabs" role="group" aria-label="${attr(label || '')}" id="${id}">${options.map(o =>
        `<button type="button" class="tab chip${o.key === current ? ' active' : ''}" data-key="${attr(o.key)}"${o.title ? ` title="${attr(o.title)}"` : ''}>${escapeHtml(o.label)}${o.count !== undefined ? ` <span class="chip-count">${escapeHtml(o.count)}</span>` : ''}</button>`).join('')}</div>`;
}
function bindChips(id, onChange) {
    const el = document.getElementById(id);
    if (!el) return;
    el.onclick = (e) => {
        const button = e.target.closest('[data-key]');
        if (!button) return;
        el.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === button));
        onChange(button.dataset.key);
    };
}
function stored(key, fallback) {
    try { const v = localStorage.getItem(LS + key); return v === null ? fallback : v; } catch (e) { return fallback; }
}
function store(key, value) {
    try { localStorage.setItem(LS + key, value); } catch (e) { /* only a convenience */ }
}

// =====================================================================
// Dialogs and toasts
// =====================================================================
// A confirmation. opts.typeToConfirm: a word the user must type before the button works (for what can't be undone)
function confirmDialog(opts) {
    return new Promise((resolve) => {
        const word = opts.typeToConfirm || '';
        const root = document.getElementById('modal-root');
        root.innerHTML = `
            <div class="modal-overlay">
                <div class="modal ${opts.wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${attr(opts.title || 'Are you sure?')}">
                    <div class="modal-head"><span>${escapeHtml(opts.title || 'Are you sure?')}</span>
                        <button class="modal-close" data-close aria-label="Close">✕</button></div>
                    <div class="modal-body"><div>${opts.message || ''}</div>${word ? `
                        <div class="form-field"><label>Type <span class="mono">${escapeHtml(word)}</span> to confirm</label>
                        <input class="input" data-confirm-word autocomplete="off" spellcheck="false" aria-label="Type ${attr(word)} to confirm"></div>` : ''}</div>
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

// A dialog with a form. opts: {title, body (HTML with named inputs), confirmLabel, wide, validate(values) -> error text or '',
// onOpen(root)}. Resolves with the values of the named inputs (checkboxes as booleans), or null when cancelled.
function formDialog(opts) {
    return new Promise((resolve) => {
        const root = document.getElementById('modal-root');
        root.innerHTML = `
            <div class="modal-overlay">
                <form class="modal ${opts.wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${attr(opts.title)}" novalidate>
                    <div class="modal-head"><span>${escapeHtml(opts.title)}</span>
                        <button type="button" class="modal-close" data-close aria-label="Close">✕</button></div>
                    <div class="modal-body">${opts.body}<div class="form-error" data-error></div></div>
                    <div class="modal-foot">
                        <button type="button" class="btn" data-close>Cancel</button>
                        <button type="submit" class="btn ${opts.danger ? 'btn-danger-solid' : 'btn-primary'}">${escapeHtml(opts.confirmLabel || 'Save')}</button>
                    </div>
                </form>
            </div>`;
        const overlay = root.querySelector('.modal-overlay');
        const form = root.querySelector('form');
        const values = () => {
            const out = {};
            form.querySelectorAll('[name]').forEach(el => {
                out[el.name] = el.type === 'checkbox' ? el.checked : el.value.trim();
            });
            return out;
        };
        const finish = (answer) => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); resolve(answer); };
        const onKey = (e) => { if (e.key === 'Escape') finish(null); };
        document.addEventListener('keydown', onKey);
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            const v = values();
            const error = opts.validate ? opts.validate(v) : '';
            if (error) { form.querySelector('[data-error]').textContent = error; return; }
            finish(v);
        });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.hasAttribute('data-close')) finish(null);
        });
        if (opts.onOpen) opts.onOpen(form);
        const first = form.querySelector('input:not([type=hidden]), select');
        if (first) first.focus();
    });
}

function toast(message, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || 'error');
    el.textContent = message;
    document.getElementById('toast-root').appendChild(el);
    setTimeout(() => el.remove(), kind === 'success' ? 3500 : 6000);
}

// =====================================================================
// Scanner helpers
// =====================================================================
const plural = (n, word, many) => `${num(n)} ${n === 1 ? word : (many || word + 's')}`;
const jobRunning = () => Boolean(app.status && app.status.job.state === 'running');
const JOB_NAMES = {scan: 'Scanning', preview: 'Scanning a preview', save: 'Saving the document'};
// Where a document's PDF is; download: as a file to keep
const docUrl = (id, download) => `api/documents/${id}/file${download ? '?download=1' : ''}`;
const SHARE_KINDS = {smb: 'SMB share', nfs: 'NFS share'};

function tagsHtml(tags) {
    return (tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('');
}

// Start a download without leaving the page
function download(url) {
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
}

// =====================================================================
// Loading. The status is asked every second while the scanner works, else every 4 seconds
// =====================================================================
let statusTimer = null;
let lastJobKey = '';
const jobListeners = [];
// fn(job) when a job starts or ends
function onJob(fn) { jobListeners.push(fn); }

async function loadStatus() {
    app.status = await api('status');
    connection.ok = true;
    renderStatusbar();
    renderNotices();
    const job = app.status.job;
    const key = `${job.kind}/${job.state}/${job.started || ''}`;
    if (key !== lastJobKey) {
        const first = lastJobKey === '';
        lastJobKey = key;
        // A job that ended before the page opened is old news
        if (!first || job.state === 'running') jobListeners.forEach(fn => fn(job));
    }
    if (activeTab && liveHooks[activeTab]) liveHooks[activeTab]();
    return app.status;
}

const connection = {ok: true};
function pollStatus() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(async () => {
        try {
            await loadStatus();
        } catch (e) {
            connection.ok = false;
            document.getElementById('statusbar').innerHTML = '<span class="sb-bad">● the app does not answer</span>';
        }
        pollStatus();
    }, jobRunning() ? 700 : 4000);
}
// After starting something: look now, not at the next turn
function statusSoon() {
    clearTimeout(statusTimer);
    loadStatus().catch(() => {}).finally(pollStatus);
}

async function loadDevices() {
    const d = await api('devices');
    app.devices = d;
    return d;
}
async function loadSettings() {
    const s = await api('settings');
    app.settings = s.settings;
    app.meta = s;
    return s;
}
function takeSettings(s) {
    app.settings = s.settings;
    app.meta = s;
}

function renderNotices() {
    const el = document.getElementById('notices');
    if (!el || !app.status) return;
    const s = app.status;
    let html = '';
    if (s.share.folder && !s.share.ok && s.share.auto) {
        html = setupHint(`Documents can't be copied to <span class="mono">${escapeHtml(s.share.folder)}</span>: ${escapeHtml(s.share.problem || 'not reachable')}. They stay in the archive; see <a href="#settings" class="plain-link">Settings</a>.`, 'bad');
    }
    if (el.innerHTML !== html) el.innerHTML = html;
}

function renderStatusbar() {
    const s = app.status;
    if (!s) return;
    const parts = [];
    const job = s.job;
    if (job.state === 'running') {
        const detail = job.kind === 'save' ? (job.pages ? `page ${num(job.page)} of ${num(job.pages)}` : '')
            : job.waiting ? 'waiting for the next page' : job.countdown ? `next page in ${job.countdown} s` : job.page ? `page ${num(job.page)} · ${num(job.percent)}%` : '';
        parts.push(`<span><span class="sb-warn">●</span> <b>${JOB_NAMES[job.kind] || 'Working'}</b>${detail ? ' ' + detail : ''}</span>`);
    } else if (s.searching) {
        parts.push('<span><span class="sb-warn">●</span> looking for scanners</span>');
    } else if (!s.devices_at) {
        parts.push('<span><span class="sb-warn">●</span> starting</span>');
    } else if (s.devices) {
        parts.push(`<span><span class="sb-ok">●</span> <b>${plural(s.devices, 'scanner')}</b></span>`);
    } else {
        parts.push('<span><span class="sb-bad">●</span> no scanner found</span>');
    }
    if (s.tray.pages) parts.push(`<span><b>${plural(s.tray.pages, 'page')}</b> to save</span>`);
    parts.push(`<span class="sb-extra"><b>${plural(s.archive.documents, 'document')}</b> · ${fmtBytes(s.archive.size)}</span>`);
    if (s.share.folder) {
        parts.push(`<span class="sb-extra" title="${attr(s.share.folder)}">${s.share.ok ? '<span class="sb-ok">●</span>' : '<span class="sb-bad">●</span>'} ${escapeHtml(SHARE_KINDS[s.share.kind] || 'folder')}${s.share.auto ? ' (every document)' : ''}</span>`);
    }
    parts.push(`<span class="spacer"></span><span class="sb-extra">Scanner app ${escapeHtml(s.version)}</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
