// Network dashboard - shared state, formatting, theme, views, dialogs, and the hosts and networks.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const LS = 'network_';

// What the server told us. status: /api/status; hosts and kinds: /api/hosts; doc: the maps, the hosts'
// settings and the networks (/api/doc); stats: /api/stats per number of days
const app = {status: null, hosts: [], kinds: {}, doc: null, stats: {}};

async function api(path) {
    const response = await fetch('api/' + path);
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
    return data;
}

// A change. A 409 (the document was saved elsewhere) is returned as {conflict, doc}.
async function apiPost(path, body) {
    const response = await fetch('api/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (response.status === 409 && data && data.doc) return {conflict: true, doc: data.doc};
    if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
    return data;
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
// Views. The address is #tab or #tab/sub (a page of the map, a host).
// onTab(name, show(sub, changed), live()): show when the tab opens or its sub changes,
// live after every new status (every few seconds) and new configuration.
// =====================================================================
const TABS = ['overview', 'map', 'devices', 'networks', 'mobile', 'stats', 'info'];
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
// Change the sub of the open tab without leaving it (a page of the map, the open host)
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
// Hosts: one device on the network, with the trackers that see it
// =====================================================================
let hostIndex = new Map();
const hostByKey = key => hostIndex.get(key) || null;
const hostByMac = mac => mac ? app.hosts.find(h => h.mac === mac) || null : null;
const meta = key => (app.doc && app.doc.devices[key]) || {};
const kindOf = h => meta(h.key).kind || h.guess || 'generic';
const kindLabel = kind => (app.kinds[kind] || {}).label || kind;
const isInfra = h => Boolean((app.kinds[kindOf(h)] || {}).infra);
const hostName = h => h ? (meta(h.key).label || h.name) : '';
// GPS trackers of the mobile app say where a phone is, not whether it's on the network
const networkHosts = () => app.hosts.filter(h => !h.gps_only);

function hostState(h) {
    if (!h) return '';
    if (h.up === null) return 'unknown';
    return h.up ? 'up' : 'down';
}
function stateDot(h) {
    const s = hostState(h);
    return `<span class="sdot ${s === 'up' ? 'ok' : s === 'down' ? '' : 'warn'}" title="${s === 'up' ? 'on the network' : s === 'down' ? 'away' : 'unknown'}"></span>`;
}
function hostStateLabel(h) {
    const s = hostState(h);
    if (s === 'up') return stateLabel('ok', h.gps_only ? 'home' : 'online');
    if (s === 'down') return stateLabel('', h.gps_only ? (h.trackers[0].state || 'away') : 'away');
    return stateLabel('warn', 'unknown');
}
function hostLink(h) {
    return `<a href="#devices/${attr(encodeURIComponent(h.key))}" class="plain-link host-label" onclick="openHost('${attr(h.key)}');return false">${hostIconHtml(h, 'small')}<span>${escapeHtml(hostName(h))}</span></a>`;
}
function openHost(key) { showHostDialog(key); }
const platformsText = h => h.platforms.map(p => PLATFORM_NAMES[p] || p).join(', ');
const PLATFORM_NAMES = {unifi: 'UniFi', nmap_tracker: 'Nmap', fritz: 'FRITZ!Box', mobile_app: 'Mobile app', ping: 'Ping', asuswrt: 'ASUSWRT',
    netgear: 'Netgear', tplink_omada: 'Omada', huawei_lte: 'Huawei LTE', keenetic_ndms2: 'Keenetic', mikrotik: 'MikroTik', luci: 'OpenWrt',
    ubus: 'OpenWrt', bluetooth_le_tracker: 'Bluetooth LE', ruckus_unleashed: 'Ruckus', arpscan_tracker: 'ARP scan', opnsense: 'OPNsense', pfsense: 'pfSense',
    vodafone_station: 'Vodafone Station', freebox: 'Freebox', synology_srm: 'Synology SRM', tado: 'tado', icloud: 'iCloud', owntracks: 'OwnTracks', person: 'Person'};

// =====================================================================
// Networks: a name, VLAN and subnet; a host belongs to the one you picked, else to the subnet of its IP
// =====================================================================
function ipToInt(ip) {
    const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(ip || '').trim());
    if (!m) return null;
    const p = m.slice(1).map(Number);
    if (p.some(x => x > 255)) return null;
    return ((p[0] << 24) >>> 0) + (p[1] << 16) + (p[2] << 8) + p[3];
}
const intToIp = n => [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join('.');
function parseCidr(text) {
    const m = /^\s*([\d.]+)\s*\/\s*(\d{1,2})\s*$/.exec(String(text || ''));
    if (!m) return null;
    const base = ipToInt(m[1]);
    const bits = Number(m[2]);
    if (base === null || bits > 32) return null;
    const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
    const start = (base & mask) >>> 0;
    return {start, end: (start + 2 ** (32 - bits) - 1) >>> 0, bits, size: 2 ** (32 - bits)};
}
const networks = () => (app.doc && app.doc.networks) || [];
const networkById = id => networks().find(n => n.id === id) || null;
function networkOfIp(ip) {
    const n = ipToInt(ip);
    if (n === null) return null;
    let best = null;
    networks().forEach(net => {
        const c = parseCidr(net.subnet);
        if (c && n >= c.start && n <= c.end && (!best || c.bits > best.c.bits)) best = {net, c};
    });
    return best ? best.net : null;
}
// {net, auto}: auto when it follows from the IP
function networkOf(h) {
    const id = meta(h.key).network;
    if (id && networkById(id)) return {net: networkById(id), auto: false};
    const net = networkOfIp(h.ip);
    return net ? {net, auto: true} : {net: null, auto: true};
}
const NET_COLORS = ['#3987e5', '#199e70', '#d95926', '#c98500', '#d55181', '#7c5cd6', '#2aa6b8', '#8a8f2a'];
const netColor = net => (net && net.color) || 'var(--idle)';
function netChip(net) {
    if (!net) return '<span class="vlan-chip untagged">no network</span>';
    return `<span class="vlan-chip" style="--vlan:${attr(netColor(net))}">${escapeHtml(net.name)}${net.vlan ? ` · ${escapeHtml(net.vlan)}` : ''}</span>`;
}
// How a host connects: what you set, else what the integration says
function connectionOf(h) {
    const m = meta(h.key).connection;
    if (m) return m;
    if (h.wired === true) return 'wired';
    if (h.wired === false || h.ssid || h.ap_mac) return 'wifi';
    return '';
}
const CONNECTIONS = {wired: 'Wired', wifi: 'Wi-Fi', fiber: 'Fiber', vpn: 'VPN', virtual: 'Virtual', '': 'Unknown'};

// =====================================================================
// The document: saved a moment after the last change. A save from an older revision is
// refused; then the newer one is read and the change is lost (with a message)
// =====================================================================
let saveTimer = null;
let saving = false;
let saveAgain = false;
const docListeners = [];
function onDoc(fn) { docListeners.push(fn); }
function docChanged(immediate) {
    clearTimeout(saveTimer);
    setSaveState('pending');
    saveTimer = setTimeout(saveDoc, immediate ? 0 : 700);
}
async function saveDoc() {
    if (saving) { saveAgain = true; return; }
    saving = true;
    try {
        const answer = await apiPost('doc', {rev: app.doc.rev, doc: app.doc});
        if (answer.conflict) {
            app.doc = answer.doc;
            toast('The map was changed in another window: that version is shown now');
            docListeners.forEach(fn => fn('conflict'));
        } else {
            app.doc.rev = answer.rev;
        }
        setSaveState('saved');
    } catch (e) {
        setSaveState('error');
        toast('Not saved: ' + e.message);
    } finally {
        saving = false;
        if (saveAgain) { saveAgain = false; saveDoc(); }
    }
}
function setSaveState(state) {
    const el = document.getElementById('save-state');
    if (!el) return;
    el.dataset.state = state;
    el.textContent = {pending: 'saving…', saved: 'saved', error: 'not saved'}[state] || '';
}
function setMeta(key, field, value) {
    const devices = app.doc.devices;
    const m = {...(devices[key] || {})};
    if (value === '' || value === null || value === undefined || value === false) delete m[field];
    else m[field] = value;
    if (Object.keys(m).length) devices[key] = m; else delete devices[key];
    docChanged();
}
const uid = () => Math.random().toString(36).slice(2, 10);

// =====================================================================
// Reading
// =====================================================================
async function loadStatus() {
    app.status = await api('status');
    renderStatusbar();
    renderNotices();
}
async function loadHosts() {
    const data = await api('hosts');
    app.hosts = data.hosts;
    app.kinds = data.kinds;
    app.zones = data.zones || [];
    hostIndex = new Map(app.hosts.map(h => [h.key, h]));
}
async function loadStats(days) {
    const data = await api(`stats?days=${days}`);
    app.stats[days] = data;
    return data;
}
async function refreshLive() {
    const before = new Map(app.hosts.map(h => [h.key, h.up]));
    await Promise.all([loadStatus(), loadHosts()]);
    // Someone came or went: the statistics (drops, online %, the latest comings and goings) too
    const flipped = app.hosts.some(h => before.has(h.key) && before.get(h.key) !== h.up) || app.hosts.length !== before.size;
    if (flipped) await refreshStats();
    if (activeTab && liveHooks[activeTab]) liveHooks[activeTab]();
}
// At most every 3 seconds; the server computes them again after a change
let statsAt = 0;
let statsTimer = null;
async function refreshStats() {
    const wait = statsAt + 3000 - Date.now();
    if (wait > 0) {
        clearTimeout(statsTimer);
        statsTimer = setTimeout(() => refreshStats().then(() => { if (activeTab && liveHooks[activeTab]) liveHooks[activeTab](); }), wait);
        return;
    }
    statsAt = Date.now();
    const days = new Set([7, ...Object.keys(app.stats).map(Number)]);
    await Promise.all([...days].map(d => loadStats(d).catch(() => {})));
}

// =====================================================================
// Live: the app tells the page the moment a tracker changes (server-sent events);
// without the stream the page asks every 10 seconds
// =====================================================================
const live = {source: null, ok: false, timer: null, changed: new Set()};
function connectLive() {
    if (live.source) live.source.close();
    live.source = new EventSource('api/live');
    live.source.addEventListener('hello', () => { live.ok = true; scheduleLive(); });
    live.source.addEventListener('ping', () => { live.ok = true; });
    live.source.onmessage = (e) => {
        live.ok = true;
        try { JSON.parse(e.data).changed.forEach(id => live.changed.add(id)); } catch (err) { /* only a hint */ }
        scheduleLive();
    };
    live.source.onerror = () => {
        live.ok = false;
        // The browser retries by itself unless the connection was closed for good
        if (live.source.readyState === EventSource.CLOSED) setTimeout(connectLive, 5000);
    };
}
// Changes come in bursts (a rename touches several entities): read once, a moment after the last
function scheduleLive() {
    clearTimeout(live.timer);
    live.timer = setTimeout(() => {
        const changed = [...live.changed];
        live.changed.clear();
        refreshLive().then(() => liveListeners.forEach(fn => fn(changed))).catch(() => {});
    }, 400);
}
const liveListeners = [];
function onLive(fn) { liveListeners.push(fn); }

function renderNotices() {
    const el = document.getElementById('notices');
    if (!el || !app.status) return;
    let html = '';
    if (!app.status.connected) {
        html = setupHint(`Cannot reach Home Assistant: ${escapeHtml(app.status.error || 'no answer')}. The app keeps trying.`, 'bad');
    } else if (app.status.loaded_at && !app.status.hosts) {
        html = setupHint('Home Assistant has no device trackers. Add an integration that tracks devices on your network (your router, UniFi, FRITZ!Box, Nmap, Ping…), and they show up here.', 'info');
    }
    if (el.innerHTML !== html) el.innerHTML = html;
}

function renderStatusbar() {
    const s = app.status;
    if (!s) return;
    const parts = [];
    if (!s.connected) {
        parts.push('<span class="sb-bad">● Home Assistant not reachable</span>');
    } else {
        parts.push(`<span><span class="sb-ok">●</span> <b>${s.up} / ${s.hosts}</b> online</span>`);
        const placed = new Set(((app.doc && app.doc.pages) || []).flatMap(p => p.nodes.map(n => n.ref)).filter(Boolean));
        const unplaced = networkHosts().filter(h => !placed.has(h.key)).length;
        if (app.hosts.length) parts.push(`<span class="sb-extra">${unplaced ? `<b>${unplaced}</b> not on a map` : 'all on a map'}</span>`);
        if (s.backfill && !s.backfill.done) parts.push('<span class="sb-extra sb-warn">reading the history…</span>');
    }
    parts.push('<span class="sb-extra"><span id="save-state" class="save-state"></span></span>');
    parts.push(`<span class="sb-extra" title="${live.ok ? 'Changes show the moment Home Assistant reports them' : 'No live connection: read every 10 seconds'}">${live.ok ? '<span class="sb-ok">●</span> live' : '<span class="sb-warn">●</span> every 10 s'}</span>`);
    parts.push(`<span class="spacer"></span><span class="sb-extra">Network app ${escapeHtml(s.version)}</span>`);
    const bar = document.getElementById('statusbar');
    const saveText = (document.getElementById('save-state') || {}).outerHTML;
    bar.innerHTML = parts.join('');
    if (saveText) document.getElementById('save-state').outerHTML = saveText;
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
