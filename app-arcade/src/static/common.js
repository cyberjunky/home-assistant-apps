// Shared by the library and the player: base path, theme, escaping, toasts.
const BASE = document.body.dataset.base || '';
const api = (path) => BASE + path;

const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = new URLSearchParams(window.location.search).get('theme');
try { themeChoice = themeChoice || localStorage.getItem('arcade_theme') || 'auto'; } catch (e) { /* none */ }
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
    const icon = document.getElementById('theme-icon');
    if (icon) icon.textContent = THEME_ICONS[themeChoice];
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.title = themeChoice === 'auto' ? 'Theme follows Home Assistant - click for light' : `Theme: ${themeChoice} - click to change`;
    }
}
function cycleTheme() {
    themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
    try { localStorage.setItem('arcade_theme', themeChoice); } catch (e) { /* none */ }
    applyTheme();
}
applyTheme();
setInterval(applyTheme, 2000);

// The clock in the library's top bar, as in the other apps
function updateClock() {
    const clock = document.getElementById('clock');
    if (clock) clock.textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
updateClock();
setInterval(updateClock, 10000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

const HTML_ESCAPES = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text).replace(/[&<>"']/g, ch => HTML_ESCAPES[ch]);
}

function toast(message, type) {
    const root = document.getElementById('toast-root');
    const el = document.createElement('div');
    el.className = 'toast' + (type ? ' ' + type : '');
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => el.classList.add('out'), 4200);
    setTimeout(() => el.remove(), 4700);
}

async function getJson(path, options) {
    const r = await fetch(api(path), options);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `${r.status} ${r.statusText}`);
    return body;
}
