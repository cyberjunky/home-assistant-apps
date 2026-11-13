// Proxmox dashboard - shared state, formatting, theme, views and the cluster's parts.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'proxmox_';

// What the server told us. status: the live state (/api/status); conf: the configuration (/api/config)
const app = {status: null, conf: null, confAt: 0};

async function api(path) {
    const response = await fetch('api/' + path);
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
    return data;
}

// A change. A 409 with a lockout reason is returned as {lockout} so the page can ask and send again.
async function apiPost(path, body) {
    const response = await fetch('api/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (response.status === 409 && data && data.lockout) return {lockout: data.lockout};
    if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
    return data;
}

const color = name => `var(--${name})`;
const readOnly = () => !app.status || Boolean(app.status.config.read_only);
const canChange = () => !readOnly();
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
// A rate in bytes per second, shown in bits as network people read it
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
// Views. The address is #tab or #tab/sub (a node, a guest, a section).
// onTab(name, show(sub, changed), live()): show when the tab opens or its sub changes,
// live after every new status (every few seconds) and new configuration.
// =====================================================================
const TABS = ['overview', 'nodes', 'guests', 'storage', 'network', 'firewall', 'tasks', 'info'];
const tabHooks = {};
const liveHooks = {};
let activeTab = null;
let activeSub = '';

function onTab(name, show, live) {
    tabHooks[name] = show;
    liveHooks[name] = live;
}

function switchTab(tabName, sub) {
    if (!TABS.includes(tabName)) tabName = 'overview';
    sub = sub === undefined ? '' : String(sub);
    const changed = tabName !== activeTab || sub !== activeSub;
    activeTab = tabName;
    activeSub = sub;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === activeTab));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    try { localStorage.setItem(LS + 'tab', tabName); } catch (e) { /* only a convenience */ }
    const hash = sub ? `${tabName}/${sub}` : tabName;
    if (decodeURIComponent(window.location.hash.slice(1)) !== hash) history.replaceState(null, '', '#' + hash);
    hideTip();
    if (changed) document.getElementById('pages').scrollTop = 0;
    renderNotices();
    if (tabHooks[tabName]) tabHooks[tabName](sub, changed);
}
// Change the sub of the open tab without leaving it (a section, the open guest)
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
// The cluster's parts, from the live state and the configuration
// =====================================================================
const resources = () => (app.status && app.status.resources) || [];
const clusterInfo = () => ((app.status && app.status.status) || []).find(s => s.type === 'cluster') || null;

// Every node of the cluster, online or not, with its live numbers and status
function nodes() {
    const s = app.status;
    if (!s) return [];
    const res = new Map(resources().filter(r => r.type === 'node').map(r => [r.node, r]));
    const members = (s.status || []).filter(n => n.type === 'node');
    const list = members.length ? members : [...res.values()].map(r => ({name: r.node, online: r.status === 'online'}));
    return list.map(m => {
        const r = res.get(m.name) || {};
        const st = (s.node_status || {})[m.name] || null;
        return {
            name: m.name, online: Boolean(m.online), ip: m.ip || '', nodeid: m.nodeid, local: m.local,
            cpu: r.cpu, maxcpu: r.maxcpu, mem: r.mem, maxmem: r.maxmem, disk: r.disk, maxdisk: r.maxdisk,
            uptime: r.uptime, status: st, conf: (app.conf && app.conf.nodes && app.conf.nodes[m.name]) || null,
        };
    }).sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
}
const nodeByName = name => nodes().find(n => n.name === name) || null;

// Every VM and container, with its configuration (network devices, disks, firewall) when read
function guests(opts) {
    const confs = (app.conf && app.conf.guests) || {};
    return resources().filter(r => r.type === 'qemu' || r.type === 'lxc')
        .filter(r => !(opts && opts.noTemplates) || !r.template)
        .map(r => ({...r, conf: confs[String(r.vmid)] || null}))
        .sort((a, b) => a.vmid - b.vmid);
}
const guestById = vmid => guests().find(g => String(g.vmid) === String(vmid)) || null;
const guestKind = g => g.type === 'qemu' ? 'VM' : 'CT';
const guestName = g => g ? (g.name || `${guestKind(g)} ${g.vmid}`) : '';
function guestLabel(g) {
    return `<span class="guest-label"><span class="gk ${g.type}">${guestKind(g)}</span><span class="mono muted">${g.vmid}</span> ${escapeHtml(guestName(g))}</span>`;
}
function guestLink(g) {
    return `<a href="#guests/${g.vmid}" class="plain-link" onclick="openGuest(${g.vmid});return false">${guestLabel(g)}</a>`;
}
function openGuest(vmid) { switchTab('guests', String(vmid)); }
function openNode(name) { switchTab('nodes', name); }
const GUEST_STATES = {running: 'ok', stopped: '', paused: 'warn', suspended: 'warn', prelaunch: 'warn'};
function guestState(g) {
    if (g.template) return stateLabel('', 'template');
    if (g.lock) return stateLabel('warn', g.lock);
    return stateLabel(GUEST_STATES[g.status] === undefined ? 'warn' : GUEST_STATES[g.status], g.status || 'unknown');
}
// Proxmox tags, separated by ; , or spaces
const guestTags = g => String(g.tags || '').split(/[;, ]+/).filter(Boolean);
function tagChip(tag) {
    let h = 0;
    for (const ch of tag) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return `<span class="tag-chip" style="--tag-h:${h}">${escapeHtml(tag)}</span>`;
}
const tagsHtml = g => guestTags(g).map(tagChip).join('');

// Storage: a shared storage once for the cluster, the others once per node
function storages() {
    const out = new Map();
    resources().filter(r => r.type === 'storage').forEach(r => {
        const key = r.shared ? r.storage : `${r.node}/${r.storage}`;
        const have = out.get(key);
        if (have) { have.nodes.push(r.node); return; }
        out.set(key, {...r, key, nodes: [r.node]});
    });
    return [...out.values()].sort((a, b) => (b.shared || 0) - (a.shared || 0) || a.storage.localeCompare(b.storage) || a.node.localeCompare(b.node));
}

// The network devices of every guest: [{guest, nic}] with nic {key, bridge, tag, firewall, mac, model}
function guestNics() {
    const out = [];
    guests().forEach(g => ((g.conf && g.conf.nics) || []).forEach(nic => out.push({guest: g, nic})));
    return out;
}
// A fixed colour per VLAN tag; untagged is grey
function vlanColor(tag) {
    if (missing(tag) || tag === '' || tag === 'untagged') return 'var(--idle)';
    // Spread neighbouring and round tags (10, 20, 30) far apart on the colour wheel
    const n = Number(tag);
    return `hsl(${(n * 137.5 + 200) % 360}, 62%, ${44 + (n % 3) * 6}%)`;
}
function vlanChip(tag) {
    return missing(tag) ? '<span class="vlan-chip untagged">untagged</span>'
        : `<span class="vlan-chip" style="--vlan:${vlanColor(tag)}">VLAN ${escapeHtml(tag)}</span>`;
}

// What the token may not read, or what the cluster doesn't have: {part: message}
const notes = () => (app.status && app.status.notes) || {};
function noteFor(prefix) {
    const n = notes();
    const key = Object.keys(n).find(k => k === prefix || k.startsWith(prefix + ':'));
    return key ? n[key] : '';
}
function permissionNote(prefix, what) {
    const text = noteFor(prefix);
    if (!text) return '';
    const denied = /permission|403/i.test(text);
    return `<div class="note ${denied ? 'warn' : ''}">${escapeHtml(what)}: ${escapeHtml(text)}${denied ? ' <span class="muted">· see the token\'s permissions in the documentation</span>' : ''}</div>`;
}

// =====================================================================
// Tasks: Proxmox runs every change as a task with a log
// =====================================================================
const TASK_NAMES = {
    qmstart: 'VM start', qmstop: 'VM stop', qmshutdown: 'VM shutdown', qmreboot: 'VM reboot', qmsuspend: 'VM suspend', qmresume: 'VM resume',
    qmigrate: 'VM migrate', qmclone: 'VM clone', qmcreate: 'VM create', qmdestroy: 'VM destroy', qmsnapshot: 'VM snapshot', qmdelsnapshot: 'VM delete snapshot',
    qmrollback: 'VM rollback', qmrestore: 'VM restore', qmconfig: 'VM configure', qmmove: 'VM move disk', qmresize: 'VM resize',
    vzstart: 'CT start', vzstop: 'CT stop', vzshutdown: 'CT shutdown', vzreboot: 'CT reboot', vzsuspend: 'CT suspend', vzresume: 'CT resume',
    vzmigrate: 'CT migrate', vzcreate: 'CT create', vzdestroy: 'CT destroy', vzsnapshot: 'CT snapshot', vzdelsnapshot: 'CT delete snapshot',
    vzrollback: 'CT rollback', vzrestore: 'CT restore', vzclone: 'CT clone',
    vzdump: 'Backup', vncproxy: 'Console', vncshell: 'Shell', spiceproxy: 'SPICE console', termproxy: 'Console', aptupdate: 'Update package lists',
    imgcopy: 'Copy image', imgdel: 'Delete image', download: 'Download', srvstart: 'Service start', srvstop: 'Service stop', srvrestart: 'Service restart',
    srvreload: 'Service reload', startall: 'Start all', stopall: 'Stop all', migrateall: 'Migrate all', hamigrate: 'HA migrate', hastart: 'HA start',
    hastop: 'HA stop', cephcreateosd: 'Ceph create OSD', reboot: 'Node reboot', shutdown: 'Node shutdown', zfscreate: 'ZFS create', dircreate: 'Directory create',
};
const taskName = t => TASK_NAMES[t.type] || t.type;
function taskState(t) {
    if (!t.endtime && !t.status) return stateLabel('warn', 'running');
    if (t.status === 'OK') return stateLabel('ok', 'OK');
    if (/^WARNINGS/.test(t.status || '')) return stateLabel('warn', t.status);
    return `<span class="state bad" title="${attr(t.status || '')}">${escapeHtml((t.status || 'error').slice(0, 40))}</span>`;
}
function taskTarget(t) {
    const g = t.id && /^\d+$/.test(t.id) ? guestById(t.id) : null;
    return g ? guestLink(g) : escapeHtml(t.id || '');
}

// Show a task's log, read again every 2 seconds while it runs
async function showTask(node, upid, title) {
    const root = document.getElementById('modal-root');
    root.innerHTML = `
        <div class="modal-overlay">
            <div class="modal wide" role="dialog" aria-modal="true" aria-label="Task">
                <div class="modal-head"><span>${escapeHtml(title || 'Task')}</span><button class="modal-close" data-close aria-label="Close">✕</button></div>
                <div class="modal-body"><div class="task-head" data-head>Reading…</div><pre class="task-log" data-log></pre></div>
                <div class="modal-foot"><button class="btn" data-close>Close</button></div>
            </div>
        </div>`;
    let open = true;
    const close = () => { open = false; root.innerHTML = ''; document.removeEventListener('keydown', onKey); };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    root.querySelector('.modal-overlay').addEventListener('click', (e) => {
        if (e.target.classList.contains('modal-overlay') || e.target.hasAttribute('data-close')) close();
    });
    while (open) {
        try {
            const t = await api(`task?node=${encodeURIComponent(node)}&upid=${encodeURIComponent(upid)}`);
            if (!open) return;
            const running = t.status.status === 'running';
            root.querySelector('[data-head]').innerHTML = `${running ? stateLabel('warn', 'running') : t.status.exitstatus === 'OK' ? stateLabel('ok', 'OK') : stateLabel('bad', t.status.exitstatus || 'stopped')}
                <span class="muted">${escapeHtml(TASK_NAMES[t.status.type] || t.status.type || '')} · ${escapeHtml(node)} · ${escapeHtml(t.status.user || '')} · started ${escapeHtml(fmtDateTime(t.status.starttime))}</span>`;
            const log = root.querySelector('[data-log]');
            const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
            log.textContent = t.log.join('\n') || '(no output yet)';
            if (atEnd) log.scrollTop = log.scrollHeight;
            if (!running) return;
        } catch (e) {
            if (!open) return;
            root.querySelector('[data-head]').innerHTML = `<span class="neg">${escapeHtml(e.message)}</span>`;
            return;
        }
        await new Promise(r => setTimeout(r, 2000));
    }
}

// Start, shut down, reboot or stop a guest, after asking
const POWER = {
    start: {label: 'Start', ask: false},
    shutdown: {label: 'Shut down', ask: true, text: 'asks the guest to shut down cleanly (ACPI or the container\'s init)'},
    reboot: {label: 'Reboot', ask: true, text: 'restarts the guest'},
    stop: {label: 'Stop', ask: true, danger: true, text: 'cuts the power at once, like pulling the plug. Unsaved data in the guest is lost'},
    suspend: {label: 'Pause', ask: true, text: 'pauses the guest in memory'},
    resume: {label: 'Resume', ask: false},
};
async function powerGuest(vmid, action) {
    const g = guestById(vmid);
    if (!g) return;
    const p = POWER[action];
    if (p.ask) {
        const ha = g.hastate ? '<p class="panel-sub" style="margin-top:8px">This guest is managed by HA: Proxmox HA will follow the new state.</p>' : '';
        const ok = await confirmDialog({
            title: `${p.label} ${guestName(g)}?`, message: `<p>${escapeHtml(p.label)} ${p.text}.</p>${ha}`,
            confirmLabel: p.label, danger: Boolean(p.danger),
        });
        if (!ok) return;
    }
    try {
        const answer = await apiPost(`guest/${g.vmid}/power`, {action});
        toast(`${p.label}: ${guestName(g)}`, 'success');
        if (answer.upid) showTask(answer.node, answer.upid, `${p.label} ${guestName(g)}`);
        setTimeout(() => loadStatus().catch(() => {}), 1500);
    } catch (e) {
        toast(e.message);
    }
}
function powerButtons(g) {
    if (g.template) return '';
    const off = readOnly() ? ' disabled title="This app is set to read-only"' : '';
    const running = g.status === 'running';
    const paused = g.status === 'paused' || g.status === 'suspended';
    const b = (action, cls) => `<button type="button" class="btn btn-small ${cls || ''}"${off} onclick="powerGuest(${g.vmid}, '${action}')">${POWER[action].label}</button>`;
    if (paused) return b('resume', 'btn-primary') + b('stop', 'btn-danger');
    if (!running) return b('start', 'btn-primary');
    return b('shutdown') + b('reboot') + (g.type === 'qemu' ? b('suspend') : '') + b('stop', 'btn-danger');
}

// =====================================================================
// Status and configuration
// =====================================================================
async function loadStatus() {
    app.status = await api('status');
    if (app.status.conf_at && app.status.conf_at !== app.confAt) {
        app.conf = await api('config');
        app.confAt = app.conf.conf_at;
    }
    renderStatusbar();
    renderBrand();
    renderNotices();
    if (activeTab && liveHooks[activeTab]) liveHooks[activeTab]();
}

function renderBrand() {
    const c = clusterInfo();
    const el = document.getElementById('brand-sub');
    const text = c ? c.name : (nodes()[0] || {}).name || '';
    if (el.textContent !== text) el.textContent = text;
}

// Things that need attention, on every page
function clusterAlerts() {
    const s = app.status;
    const out = [];
    if (!s) return out;
    if (!s.config.configured) {
        out.push({kind: 'warn', text: 'Not configured yet: enter <b>hosts</b>, <b>token_id</b> and <b>token_secret</b> in the app\'s configuration, then restart the app. The Info page tells how to make a token.'});
        return out;
    }
    if (!s.connected) {
        out.push({kind: 'bad', text: `Cannot read the cluster: ${escapeHtml(s.error || 'no answer')}${s.live_at ? ` · last read ${escapeHtml(fmtAgo(s.live_at))}` : ''}`});
        return out;
    }
    const c = clusterInfo();
    if (c && !c.quorate) out.push({kind: 'bad', text: '<b>The cluster has no quorum.</b> Guests keep running, but nothing can be started, changed or migrated until enough nodes are back.'});
    const off = nodes().filter(n => !n.online);
    if (off.length) out.push({kind: 'bad', text: `Offline: ${off.map(n => `<b>${escapeHtml(n.name)}</b>`).join(', ')}`});
    const ceph = s.ceph;
    if (ceph && ceph.health && ceph.health.status && ceph.health.status !== 'HEALTH_OK') {
        const checks = Object.values(ceph.health.checks || {}).map(c => (c.summary && c.summary.message) || '').filter(Boolean);
        out.push({kind: ceph.health.status === 'HEALTH_ERR' ? 'bad' : 'warn', text: `Ceph <b>${escapeHtml(ceph.health.status.replace('HEALTH_', ''))}</b>${checks.length ? ': ' + checks.map(escapeHtml).join(' · ') : ''}`});
    }
    const full = storages().filter(st => st.status === 'available' && st.maxdisk && st.disk / st.maxdisk >= 0.9);
    if (full.length) out.push({kind: 'warn', text: `Storage almost full: ${full.map(st => `<b>${escapeHtml(st.shared ? st.storage : `${st.storage} on ${st.node}`)}</b> ${plainPct(st.disk / st.maxdisk, 0)}`).join(', ')}`});
    return out;
}
function renderNotices() {
    const el = document.getElementById('notices');
    if (!el) return;
    const html = clusterAlerts().filter(a => a.kind === 'bad' || activeTab === 'overview' || !app.status || !app.status.connected)
        .map(a => setupHint(a.text, a.kind)).join('');
    if (el.innerHTML !== html) el.innerHTML = html;
}

function renderStatusbar() {
    const s = app.status;
    if (!s) return;
    const parts = [];
    if (!s.config.configured) {
        parts.push('<span class="sb-warn">● not configured</span>');
    } else if (!s.connected) {
        parts.push(`<span class="sb-bad">● cluster not reachable</span>`);
    } else {
        const all = nodes();
        const up = all.filter(n => n.online).length;
        const c = clusterInfo();
        parts.push(`<span><span class="${up === all.length ? 'sb-ok' : 'sb-warn'}">●</span> <b>${up} / ${all.length}</b> nodes</span>`);
        if (c) parts.push(`<span class="sb-extra">quorum <b class="${c.quorate ? 'sb-ok' : 'sb-bad'}">${c.quorate ? 'OK' : 'lost'}</b></span>`);
        const gs = guests({noTemplates: true});
        parts.push(`<span class="sb-extra"><b>${gs.filter(g => g.status === 'running').length} / ${gs.length}</b> guests running</span>`);
        parts.push(`<span class="sb-extra">via <b>${escapeHtml((s.host || '').replace(/^https?:\/\//, ''))}</b></span>`);
        parts.push(`<span class="sb-extra">read ${escapeHtml(fmtAgo(s.live_at))}</span>`);
    }
    parts.push(`<span class="sb-extra">${s.config.read_only ? 'read-only' : '<span class="sb-warn">changes allowed</span>'}</span>`);
    parts.push(`<span class="spacer"></span><span class="sb-extra">Proxmox app ${escapeHtml(s.version)} · unofficial</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

// Ask the app to read the configuration again (after a change somewhere else)
async function rereadConfig() {
    try {
        await apiPost('refresh', {});
        toast('Reading the configuration again…', 'success');
        setTimeout(() => loadStatus().catch(() => {}), 2500);
    } catch (e) {
        toast(e.message);
    }
}

// History charts for one timeframe: hour, day, week, month, year
const TIMEFRAMES = [{key: 'hour', label: 'Hour'}, {key: 'day', label: 'Day'}, {key: 'week', label: 'Week'}, {key: 'month', label: 'Month'}, {key: 'year', label: 'Year'}];
function timeFmt(timeframe) {
    return timeframe === 'hour' || timeframe === 'day' ? (t => `${fmtDay(t)} ${fmtTime(t)}`) : fmtDay;
}
