// Dashboard - shared state, Home Assistant helpers, theme.
'use strict';

const LS = 'dashboard.';
const LOCALE = navigator.language || 'en';
// doc: the pages; states: entity -> {s, a, c}; areas: entity -> area name
const S = {doc: null, states: {}, areas: {}, hidden: new Set(), page: null, design: false, sel: null};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const newId = () => Math.random().toString(16).slice(2, 10);

async function api(path, body) {
    const res = await fetch('api/' + path, body === undefined ? {} : {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), {status: res.status, data});
    return data;
}

let toastTimer;
function toast(text) {
    const el = $('#toast');
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

// ---------- Icons: thin strokes, drawn on a 24 grid ----------
const ICONS = {
    right: '<path d="M9 5l7 7-7 7"/>', up: '<path d="M5 15l7-7 7 7"/>', down: '<path d="M5 9l7 7 7-7"/>',
    plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>',
    pause: '<path class="f" d="M7 5h3.4v14H7zM13.6 5H17v14h-3.4z"/>', play: '<path class="f" d="M8 5l11 7-11 7z"/>',
    next: '<path class="f" d="M6 5l9 7-9 7zM16.4 5H19v14h-2.6z"/>',
    scene: '<circle cx="12" cy="14" r="4"/><path d="M12 6V4M5.5 8.5L4 7M18.5 8.5L20 7M3 19h18"/>',
    script: '<path d="M14 4h5v16h-5M3 12h11M10 8l4 4-4 4"/>',
    bolt: '<path d="M13 2L5 14h6l-1 8 8-12h-6z"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    undo: '<path d="M8 5L3 10l5 5M3 10h11a5.5 5.5 0 0 1 0 11h-3"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    theme: '<circle cx="12" cy="12" r="8.5"/><path class="f" d="M12 3.5a8.5 8.5 0 0 1 0 17z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 4V2M12 22v-2M4 12H2M22 12h-2M6.3 6.3L4.9 4.9M19.1 19.1l-1.4-1.4M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
};
const ic = name => `<svg class="ic" viewBox="0 0 24 24">${ICONS[name] || ''}</svg>`;

// ---------- Entities ----------
const dom = e => e.split('.', 1)[0];
const st = e => S.states[e];
const attr = (e, key) => st(e)?.a?.[key];
const ename = (e, w) => w?.opts?.names?.[e] || attr(e, 'friendly_name') || e;
const OFFISH = new Set(['off', 'closed', 'unavailable', 'unknown', 'idle', 'paused', 'standby', 'not_home', 'locked', 'docked', '']);
const NEVER_ON = new Set(['scene', 'script', 'sensor', 'weather', 'button', 'input_button', 'number', 'input_number']);
const isOn = e => Boolean(st(e)) && !NEVER_ON.has(dom(e)) && !OFFISH.has(st(e).s);
const isNumeric = e => { const v = st(e)?.s; return v != null && v !== '' && Number.isFinite(Number(v)); };
const num = e => isNumeric(e) ? Number(st(e).s) : null;

function fmt(v, digits) {
    if (v == null || !Number.isFinite(v)) return '–';
    const a = Math.abs(v);
    const d = digits ?? (a >= 100 ? 0 : a >= 10 ? 1 : 2);
    return v.toLocaleString(LOCALE, {maximumFractionDigits: Number.isInteger(v) ? 0 : d});
}
// A number with its unit, large watts and watt-hours shown in kilo
function quantity(v, unit) {
    if (v == null) return {text: '–', unit: unit || ''};
    if ((unit === 'W' || unit === 'Wh') && Math.abs(v) >= 1000) return {text: fmt(v / 1000, 1), unit: 'k' + unit};
    if (unit === '°C' || unit === '°F') return {text: fmt(v, 1), unit};
    return {text: fmt(v), unit: unit || ''};
}
const qty = e => quantity(num(e), attr(e, 'unit_of_measurement'));
const qtyText = e => { const q = qty(e); return q.text + (q.unit ? ' ' + q.unit : ''); };

const BINARY = {
    door: ['Open', 'Closed'], window: ['Open', 'Closed'], garage_door: ['Open', 'Closed'], opening: ['Open', 'Closed'],
    occupancy: ['Detected', 'Clear'], motion: ['Detected', 'Clear'], presence: ['Home', 'Away'], moisture: ['Wet', 'Dry'],
    lock: ['Unlocked', 'Locked'], battery: ['Low', 'OK'], connectivity: ['Connected', 'Offline'], smoke: ['Smoke', 'Clear'],
};
function stateText(e) {
    const s = st(e);
    if (!s) return 'Not found';
    const d = dom(e), v = String(s.s);
    if (v === 'unavailable' || v === 'unknown') return d === 'scene' ? '' : cap(v);
    if (d === 'light' && v === 'on' && s.a.brightness) return Math.round(s.a.brightness / 2.55) + ' %';
    if (d === 'binary_sensor') return (BINARY[s.a.device_class] || ['On', 'Off'])[v === 'on' ? 0 : 1];
    if (d === 'cover') { const p = s.a.current_position; return p > 0 && p < 100 ? p + ' %' : cap(v); }
    if (d === 'scene' || d === 'button' || d === 'input_button') return '';
    if (d === 'climate') return s.a.current_temperature != null ? fmt(s.a.current_temperature, 1) + ' °' : cap(v);
    if (d === 'weather') return s.a.temperature != null ? fmt(s.a.temperature, 1) + ' ' + (s.a.temperature_unit || '°') : cap(v);
    if (isNumeric(e)) return qtyText(e);
    return cap(v.replace(/_/g, ' '));
}

function call(e, service, data) {
    return api('service', {domain: dom(e), service, entity_id: e, data: data || {}}).catch(err => toast(err.message));
}
// What a tap on an entity does
function tap(e) {
    const d = dom(e);
    if (['light', 'switch', 'input_boolean', 'fan'].includes(d)) return call(e, 'toggle');
    if (d === 'cover') return call(e, st(e)?.s === 'closed' ? 'open_cover' : 'close_cover');
    if (d === 'scene' || d === 'script') return call(e, 'turn_on');
    if (d === 'button' || d === 'input_button') return call(e, 'press');
    if (d === 'automation') return call(e, 'trigger');
    if (d === 'media_player') return call(e, 'media_play_pause');
    return null;
}

// ---------- Style: the dashboard's own fonts and colours ----------
const FONTS = {
    'Inter': 'Inter - neutral and clear', 'Archivo': 'Archivo - wide and sturdy', 'Space Grotesk': 'Space Grotesk - a bit technical',
    'Manrope': 'Manrope - round and soft', 'Fraunces': 'Fraunces - a serif', 'JetBrains Mono': 'JetBrains Mono - fixed width',
    'system-ui': "The tablet's own font",
};
const STYLE = {text: 'Inter', label: 'Archivo', weight: '300', paper: '#faf7f1', night: '#0a0a0a', tint: '#e0603f'};
function applyStyle() {
    const style = S.doc?.style || {}, root = document.documentElement.style;
    const font = name => name && FONTS[name] ? (name === 'system-ui' ? name : `'${name}'`) : null;
    const set = (key, value) => value ? root.setProperty(key, value) : root.removeProperty(key);
    set('--font-text', font(style.text));
    set('--font-label', font(style.label));
    set('--num-weight', /^[1-9]00$/.test(style.weight) ? style.weight : null);
    for (const key of ['paper', 'night', 'tint']) set('--' + key, /^#[0-9a-f]{6}$/i.test(style[key]) ? style[key] : null);
}

// ---------- Theme: follows Home Assistant's dark mode (this page runs in its ingress frame) ----------
const THEMES = ['auto', 'light', 'dark'];
function storedTheme() {
    try { return localStorage.getItem(LS + 'theme'); } catch (e) { return null; }
}
let themeChoice = new URLSearchParams(window.location.search).get('theme') || storedTheme();
if (!THEMES.includes(themeChoice)) themeChoice = 'auto';
function haDarkMode() {
    try {
        const ha = window.parent !== window && window.parent.document.querySelector('home-assistant');
        return ha && ha.hass && ha.hass.themes ? Boolean(ha.hass.themes.darkMode) : null;
    } catch (e) { return null; }
}
function applyTheme() {
    let dark = themeChoice === 'dark';
    // The dashboard's own rule comes first: dark at night, or when the sun is down
    if (typeof wantsDark === 'function' && S.doc && themeChoice === 'auto' && wantsDark()) dark = true;
    else if (themeChoice === 'auto') {
        const ha = haDarkMode();
        dark = ha === null ? window.matchMedia('(prefers-color-scheme: dark)').matches : ha;
    }
    const next = dark ? 'dark' : 'light';
    if (document.documentElement.dataset.theme !== next) document.documentElement.dataset.theme = next;
}
function cycleTheme() {
    themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
    try { localStorage.setItem(LS + 'theme', themeChoice); } catch (e) { /* only a convenience */ }
    applyTheme();
    renderNav();
}
applyTheme();
// Home Assistant doesn't tell the frame when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
