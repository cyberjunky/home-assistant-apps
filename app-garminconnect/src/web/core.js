// Garmin Connect dashboard - shared state, formatting, theme, modals, views and the status.
// Plain scripts sharing one global scope, loaded in the order index.html lists them.

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
const LS = 'garmin_';

// What the server told us; filled by loadStatus() and refreshed every few seconds
const app = {version: '', status: null, account: {signed_in: false}, collector: {}, units: 'metric', today: ''};

async function api(path, options) {
    const opts = options || {};
    if (opts.body && typeof opts.body !== 'string') {
        opts.body = JSON.stringify(opts.body);
        opts.headers = {'Content-Type': 'application/json'};
    }
    const response = await fetch(basePath + 'api/' + path, opts);
    let data = null;
    try { data = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) throw new Error((data && data.error) || `Request failed (${response.status})`);
    return data;
}
const post = (path, body) => api(path, {method: 'POST', body: body || {}});

// =====================================================================
// Units and formatting: the server speaks metric, the Garmin profile picks the display
// =====================================================================
const KM_PER_MILE = 1.609344;
const LB_PER_KG = 2.20462;
const imperial = () => app.units === 'imperial';
const missing = v => v === null || v === undefined || Number.isNaN(v);

function num(value, digits) {
    if (missing(value)) return '–';
    return Number(value).toLocaleString(undefined, {minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0});
}
// Value with its unit as HTML: the unit sits small behind the number
function withUnit(text, unitText) { return text === '–' ? '–' : `${text}<small> ${unitText}</small>`; }
const distUnit = () => imperial() ? 'mi' : 'km';
const toDist = km => imperial() ? km / KM_PER_MILE : km;
const fmtKm = (km, digits) => missing(km) ? '–' : `${num(toDist(km), digits === undefined ? 1 : digits)} ${distUnit()}`;
const fmtMeters = m => missing(m) ? '–' : fmtKm(m / 1000);
const weightUnit = () => imperial() ? 'lb' : 'kg';
const toWeight = kg => imperial() ? kg * LB_PER_KG : kg;
const fmtWeight = kg => missing(kg) ? '–' : `${num(toWeight(kg), 1)} ${weightUnit()}`;
const fmtElev = m => missing(m) ? '–' : imperial() ? `${num(m * 3.28084)} ft` : `${num(m)} m`;

// Seconds as "7h 12m" or "42 min"
function fmtDuration(seconds) {
    if (missing(seconds)) return '–';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
// Seconds as a race time, "1:52:40" or "24:10"
function fmtRaceTime(seconds) {
    if (missing(seconds)) return '–';
    const s = Math.round(seconds);
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`;
}
// Hours on the clock (22.5 → "22:30", 24.5 → "00:30")
function fmtClock(hours) {
    if (missing(hours)) return '–';
    const total = Math.round(((hours % 24) + 24) % 24 * 60);
    return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}
// Pace (min per km or mile) from m/s, for running and walking; speed for everything else
function fmtPace(mps) {
    if (missing(mps) || mps <= 0) return '–';
    const perUnit = (imperial() ? KM_PER_MILE * 1000 : 1000) / mps;
    return `${Math.floor(perUnit / 60)}:${String(Math.round(perUnit % 60)).padStart(2, '0')} /${distUnit()}`;
}
const fmtSpeed = mps => missing(mps) ? '–' : `${num(toDist(mps * 3.6), 1)} ${imperial() ? 'mph' : 'km/h'}`;
const pacedSport = family => family === 'running' || family === 'walking';

// Local dates as 'YYYY-MM-DD' (never through UTC, which can be another day)
function isoDay(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const parseDay = s => new Date(s + 'T12:00:00');
function addDays(day, n) { const d = parseDay(day); d.setDate(d.getDate() + n); return isoDay(d); }
const fmtDay = day => parseDay(day).toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'});
const fmtDayShort = day => parseDay(day).toLocaleDateString([], {day: 'numeric', month: 'short'});
function fmtDayY(day) {
    const d = parseDay(day);
    const opts = {day: 'numeric', month: 'short'};
    if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString([], opts);
}
const fmtMonth = day => parseDay(day).toLocaleDateString([], {month: 'short', year: '2-digit'});

function fmtAgo(ts) {
    if (!ts) return 'never';
    const seconds = Math.max(0, Date.now() / 1000 - ts);
    if (seconds < 10) return 'just now';
    if (seconds < 90) return `${Math.round(seconds)} s ago`;
    if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} days ago`;
}
function fmtIn(ts) {
    const seconds = ts - Date.now() / 1000;
    if (seconds <= 30) return 'now';
    if (seconds < 5400) return `in ${Math.round(seconds / 60)} min`;
    return `in ${Math.round(seconds / 3600)} h`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : String(text);
    return div.innerHTML;
}

// =====================================================================
// Theme: follows Home Assistant's dark mode (this page runs in its ingress
// iframe, same origin), the system outside Home Assistant, or a manual choice
// =====================================================================
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
// "?theme=dark" (or light) in the address wins, e.g. for a wall display
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
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

function initThemeToggle() {
    document.getElementById('theme-icon').textContent = THEME_ICONS[themeChoice];
    document.getElementById('theme-toggle').title = themeChoice === 'auto'
        ? 'Theme follows Home Assistant - click for light'
        : `Theme: ${themeChoice} - click to change`;
}

function cycleTheme() {
    themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
    localStorage.setItem(LS + 'theme', themeChoice);
    initThemeToggle();
    applyTheme();
}

applyTheme();
// Home Assistant doesn't tell the iframe when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// =====================================================================
// Toasts & modals
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

function openModal(opts) {
    closeModal();
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay open';
    overlay.innerHTML = `
        <div class="modal" role="dialog" aria-modal="true">
            <div class="modal-head">
                <span class="modal-title">${escapeHtml(opts.title || '')}</span>
                <button class="modal-close" data-close aria-label="Close">✕</button>
            </div>
            <div class="modal-body">${opts.bodyHTML || ''}</div>
            <div class="modal-foot">${opts.footHTML || ''}</div>
        </div>`;
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.hasAttribute('data-close')) closeModal();
    });
    document.getElementById('modal-root').appendChild(overlay);
    return overlay;
}

function closeModal() {
    document.getElementById('modal-root').innerHTML = '';
}

// opts.typeToConfirm: a word the user must type before the confirm button works (for what can't be undone)
function confirmDialog(opts) {
    return new Promise((resolve) => {
        const word = opts.typeToConfirm || '';
        const overlay = openModal({
            title: opts.title || 'Are you sure?',
            bodyHTML: `<div>${opts.message || ''}</div>` + (word ? `
                <div class="form-field"><label>Type <span class="mono">${escapeHtml(word)}</span> to confirm</label>
                <input class="input" data-confirm-word autocomplete="off" spellcheck="false" aria-label="Type ${escapeHtml(word)} to confirm"></div>` : ''),
            footHTML: `
                <button class="btn" data-close>Cancel</button>
                <button class="btn ${opts.danger ? 'btn-danger-solid' : 'btn-primary'}" data-confirm>${escapeHtml(opts.confirmLabel || 'Confirm')}</button>`
        });
        const button = overlay.querySelector('[data-confirm]');
        if (word) {
            const input = overlay.querySelector('[data-confirm-word]');
            button.disabled = true;
            input.addEventListener('input', () => { button.disabled = input.value.trim() !== word; });
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !button.disabled) button.click(); });
            input.focus();
        }
        button.addEventListener('click', () => { closeModal(); resolve(true); });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.hasAttribute('data-close')) resolve(false);
        });
    });
}

document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

// =====================================================================
// Views
// =====================================================================
const TABS = ['today', 'health', 'insights', 'activities', 'records', 'settings'];
const tabHooks = {};
let activeTab = null;

function onTab(name, fn) { tabHooks[name] = fn; }

function switchTab(tabName) {
    if (!TABS.includes(tabName)) tabName = 'today';
    activeTab = tabName;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    localStorage.setItem(LS + 'tab', tabName);
    if (window.location.hash.slice(1).split('/')[0] !== tabName) history.replaceState(null, '', '#' + tabName);
    hideTip();
    if (tabHooks[tabName]) tabHooks[tabName]();
    // Charts drawn while their view was hidden had no width
    requestAnimationFrame(redrawCharts);
}

function initSplitters() {
    const root = document.documentElement.style;
    document.querySelectorAll('.splitter').forEach(handle => {
        const name = handle.dataset.size;
        const saved = localStorage.getItem(LS + 'size' + name);
        if (saved) root.setProperty(name, saved);

        handle.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            const pane = document.getElementById(handle.dataset.pane);
            const row = handle.classList.contains('row');
            const dir = Number(handle.dataset.dir);
            const start = row ? e.clientY : e.clientX;
            const startSize = row ? pane.offsetHeight : pane.offsetWidth;
            const room = row ? pane.parentElement.offsetHeight : pane.parentElement.offsetWidth;
            handle.setPointerCapture(e.pointerId);
            handle.classList.add('dragging');
            const move = (ev) => {
                const size = startSize + dir * ((row ? ev.clientY : ev.clientX) - start);
                root.setProperty(name, Math.round(Math.max(200, Math.min(room - 200, size))) + 'px');
                map.invalidateSize();
            };
            const up = () => {
                handle.classList.remove('dragging');
                handle.removeEventListener('pointermove', move);
                handle.removeEventListener('pointerup', up);
                localStorage.setItem(LS + 'size' + name, root.getPropertyValue(name));
                window.dispatchEvent(new Event('resize'));
            };
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', up);
        });

        handle.addEventListener('dblclick', () => {
            root.removeProperty(name);
            localStorage.removeItem(LS + 'size' + name);
            map.invalidateSize();
            window.dispatchEvent(new Event('resize'));
        });
    });
}


// =====================================================================
// Periods
// =====================================================================
const RANGES = [
    {key: '30d', label: '30 days', days: 30},
    {key: '90d', label: '90 days', days: 90},
    {key: '12m', label: '12 months', days: 365},
    {key: 'year', label: 'This year'},
    {key: 'all', label: 'All'},
];

// {start, end} as days (both included) for a preset or a specific year ("y2024")
function rangeDays(key) {
    const today = app.today || isoDay(new Date());
    const year = /^y(\d{4})$/.exec(key || '');
    if (year) return {start: `${year[1]}-01-01`, end: `${year[1]}-12-31` < today ? `${year[1]}-12-31` : today};
    const preset = RANGES.find(r => r.key === key) || RANGES[2];
    if (preset.key === 'all') return {start: firstDay(), end: today};
    if (preset.key === 'year') return {start: today.slice(0, 4) + '-01-01', end: today};
    return {start: addDays(today, -(preset.days - 1)), end: today};
}
function firstDay() {
    const counts = (app.status && app.status.counts) || {};
    return counts.first_day || (app.account.created || addDays(app.today || isoDay(new Date()), -365));
}
function rangeText(r) { return `${fmtDayY(r.start)} – ${fmtDayY(r.end)}`; }

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

function availableYears() {
    const now = new Date().getFullYear();
    const first = Number(firstDay().slice(0, 4)) || now;
    const years = [];
    for (let y = now; y >= Math.min(first, now); y--) years.push(y);
    return years;
}

// A year dropdown beside the period chips; picking a year sets the range to that calendar year
function buildYearSelect(selectId, current, onChange) {
    const el = document.getElementById(selectId);
    const match = /^y(\d{4})$/.exec(current || '');
    el.innerHTML = '<option value="">Year…</option>'
        + availableYears().map(y => `<option value="y${y}"${match && Number(match[1]) === y ? ' selected' : ''}>${y}</option>`).join('');
    el.onchange = () => { if (el.value) onChange(el.value); };
}

// Period chips + year dropdown that keep each other in step and remember the choice
function periodPicker(name, fallback, onChange) {
    let current = localStorage.getItem(LS + name + '_range') || fallback;
    const set = (key) => {
        current = key;
        localStorage.setItem(LS + name + '_range', key);
        buildChips(`${name}-range`, RANGES, key, set);
        buildYearSelect(`${name}-year`, key, set);
        onChange(rangeDays(key), key);
    };
    return {
        get: () => current,
        days: () => rangeDays(current),
        render: () => { buildChips(`${name}-range`, RANGES, current, set); buildYearSelect(`${name}-year`, current, set); },
        set,
    };
}

const BUCKETS = [{key: 'day', label: 'Day'}, {key: 'week', label: 'Week'}, {key: 'month', label: 'Month'}];
// Days for a short period, weeks for a few months, months beyond that
function autoBucket(r) {
    const days = (parseDay(r.end) - parseDay(r.start)) / 86400000 + 1;
    return days <= 62 ? 'day' : days <= 400 ? 'week' : 'month';
}
function bucketLabel(day, bucket) {
    if (bucket === 'month') return fmtMonth(day);
    if (bucket === 'year') return day.slice(0, 4);
    return fmtDayShort(day);
}
function bucketTitle(day, bucket) {
    if (bucket === 'week') return `Week of ${fmtDayY(day)}`;
    if (bucket === 'month') return parseDay(day).toLocaleDateString([], {month: 'long', year: 'numeric'});
    return fmtDay(day);
}

// =====================================================================
// Status: the account and what is being fetched, every few seconds
// =====================================================================
const statusListeners = [];
function onStatus(fn) { statusListeners.push(fn); }
let statusOk = true;

async function loadStatus() {
    try {
        const status = await api('status');
        statusOk = true;
        const changed = app.status && app.status.counts && JSON.stringify(app.status.counts) !== JSON.stringify(status.counts);
        app.status = status;
        app.version = status.version;
        app.account = status.account || {};
        app.collector = status.collector || {};
        app.units = app.account.units || 'metric';
        app.today = status.today;
        renderChrome();
        statusListeners.forEach(fn => { try { fn(changed); } catch (e) { console.error(e); } });
    } catch (e) {
        statusOk = false;
        renderStatusbar();
    }
}

function renderChrome() {
    document.getElementById('brand-name').textContent = app.account.name || '';
    const hint = document.getElementById('setup-hint');
    const signedIn = app.account.signed_in;
    hint.hidden = signedIn;
    if (!signedIn) {
        document.getElementById('setup-hint-text').textContent = app.account.error
            || (app.account.has_tokens ? 'Signing in to Garmin Connect…' : 'Sign in to your Garmin account to fetch your data.');
    }
    renderStatusbar();
}

function renderStatusbar() {
    const parts = [];
    const c = app.collector || {};
    if (!statusOk) parts.push('<span class="sb-bad">● app not reachable</span>');
    if (!app.account.signed_in) {
        parts.push('<span class="sb-warn">● not signed in to Garmin</span>');
    } else {
        const cls = c.error ? 'sb-warn' : 'sb-ok';
        const what = c.phase === 'importing history' ? `importing history · ${escapeHtml(c.current)}`
            : c.phase === 'updating' ? `updating · ${escapeHtml(c.current)}` : escapeHtml(c.phase || 'idle');
        parts.push(`<span><span class="${cls}">●</span> ${what}</span>`);
        if (c.last_update_at) parts.push(`<span>updated <b>${fmtAgo(c.last_update_at)}</b></span>`);
        if (c.next_update_at && c.phase !== 'updating') parts.push(`<span class="sb-extra">next ${fmtIn(c.next_update_at)}</span>`);
        if (c.error) parts.push(`<span class="sb-warn sb-extra">${escapeHtml(c.error)}</span>`);
    }
    const counts = (app.status && app.status.counts) || {};
    if (counts.days) parts.push(`<span class="sb-extra"><b>${num(counts.days)}</b> days · <b>${num(counts.activities)}</b> activities</span>`);
    parts.push(`<span class="spacer"></span><span class="sb-extra">Garmin Connect ${escapeHtml(app.version)} · unofficial</span>`);
    document.getElementById('statusbar').innerHTML = parts.join('');
}

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

async function updateNow() {
    if (!app.account.signed_in) { openSignIn(); return; }
    try {
        await post('update');
        toast('Fetching the latest data from Garmin…');
        setTimeout(loadStatus, 1500);
    } catch (e) {
        toast(e.message, 'error');
    }
}

// =====================================================================
// Small shared pieces
// =====================================================================
const SPORT_COLORS = {running: 'var(--series-1)', cycling: 'var(--series-2)', walking: 'var(--series-3)', swimming: 'var(--series-4)', strength: 'var(--series-5)', other: 'var(--muted-text-color)'};
const SPORT_NAMES = {running: 'Running', cycling: 'Cycling', walking: 'Walking & hiking', swimming: 'Swimming', strength: 'Strength & fitness', other: 'Other'};
const typeName = t => (t || 'other').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function tile(label, valueHtml, detail, extraClass) {
    return `<div class="tile ${extraClass || ''}"><div class="l">${escapeHtml(label)}</div><div class="v">${valueHtml}</div>${detail ? `<div class="d">${detail}</div>` : ''}</div>`;
}
function infoRow(label, valueHtml) {
    return `<div class="info-row"><span class="info-label">${escapeHtml(label)}</span><span class="info-value">${valueHtml}</span></div>`;
}

// A weekday x hour grid of values, shaded on the one-hue ramp
function heatGrid(el, grid, format) {
    const values = grid.flat().filter(v => !missing(v) && v > 0);
    if (!values.length) { el.innerHTML = '<div class="empty-state">Nothing in this period</div>'; return; }
    const max = Math.max(...values), min = Math.min(...values);
    let html = '<div class="heat"><span></span>' + Array.from({length: 24}, (_, h) => `<span class="hr">${h % 3 === 0 ? h : ''}</span>`).join('');
    grid.forEach((row, w) => {
        html += `<span class="lbl">${WEEKDAYS[w]}</span>`;
        row.forEach((v, h) => {
            const level = missing(v) || v <= 0 ? 0 : 1 + Math.min(4, Math.floor((v - min) / ((max - min) || 1) * 5));
            const style = level ? `background:var(--seq-${level})` : '';
            html += `<span class="cell" style="${style}" title="${WEEKDAYS[w]} ${String(h).padStart(2, '0')}:00 · ${missing(v) ? 'no data' : format(v)}"></span>`;
        });
    });
    el.innerHTML = html + '</div>';
}

// A day's curve from [[minute, value], ...] as chart rows over 0..1440 minutes
function curveSeries(points) {
    const x = [], v = [];
    (points || []).forEach(([minute, value], i) => {
        // A gap of more than 20 minutes (watch off) breaks the line
        if (i && minute - points[i - 1][0] > 20) { x.push(minute - 1); v.push(null); }
        x.push(minute); v.push(value);
    });
    return {x, v};
}
// Every 3 hours, or every 6 on a narrow screen
const clockTicks = () => (window.innerWidth < 700 ? [0, 360, 720, 1080, 1439] : [0, 180, 360, 540, 720, 900, 1080, 1260, 1439])
    .map(m => ({at: m, label: `${String(Math.floor(m / 60)).padStart(2, '0')}:00`}));
const fmtMinute = m => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(Math.round(m % 60)).padStart(2, '0')}`;
