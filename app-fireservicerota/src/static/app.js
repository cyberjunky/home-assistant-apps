// FireServiceRota dashboard. Everything comes from the app's own API, which reads it from
// Home Assistant's FireServiceRota integration; nothing here talks to FireServiceRota,
// except the browser playing message_to_speech_url when you press Play.
'use strict';

const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';

// =====================================================================
// Theme: follows Home Assistant's dark mode (this page runs in its ingress
// iframe, same origin), the system outside Home Assistant, or a manual choice
// =====================================================================
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = localStorage.getItem('fsr_theme');
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
    localStorage.setItem('fsr_theme', themeChoice);
    initThemeToggle();
    applyTheme();
}

applyTheme();
// Home Assistant doesn't tell the iframe when its theme changes
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// =====================================================================
// Helpers
// =====================================================================
const HTML_ESCAPES = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'};
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text).replace(/[&<>"']/g, ch => HTML_ESCAPES[ch]);
}
function formatTime(ts) {
    return new Date(ts).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hour12: false});
}
function formatDate(ts) {
    return new Date(ts).toLocaleString([], {day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
}
function formatShortDate(ts) {
    return new Date(ts).toLocaleString([], {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false});
}
function isToday(ts) {
    const d = new Date(ts), n = new Date();
    return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}
function rowTime(ts) {
    return isToday(ts) ? formatTime(ts) : formatShortDate(ts);
}
function ago(ts) {
    const mins = Math.max(0, Math.floor((Date.now() - new Date(ts)) / 60000));
    if (mins < 60) return `${mins}m`;
    if (mins < 48 * 60) return `${Math.floor(mins / 60)}h ${mins % 60}m`;
    return `${Math.floor(mins / 1440)}d`;
}
function formatAgo(ts) {
    if (!ts) return 'never';
    const secs = Math.max(0, Math.round((Date.now() - new Date(ts)) / 1000));
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ${Math.floor(secs % 3600 / 60)}m ago`;
    return formatShortDate(ts);
}
function incidentTime(i) { return i.created_at || i.first_seen; }

// FireServiceRota priorities: "a1"/"a2" are ambulance codes, p1/b1 urgent, p2/b2 normal
function prioLabel(i) { return (i && i.prio ? String(i.prio) : '').toUpperCase(); }
function prioKind(i) {
    const p = (i && i.prio ? String(i.prio) : '').toLowerCase();
    if (!p) return 'other';
    if (p.startsWith('a')) return 'amb';
    if (/1$/.test(p)) return 'p1';
    if (/2$/.test(p)) return 'p2';
    return 'other';
}
function prioChip(i) {
    const label = prioLabel(i);
    if (!label) return '<span class="prio">—</span>';
    const cls = {amb: 'amb', p1: 'p1', p2: 'p2'}[prioKind(i)] || '';
    return `<span class="prio ${cls}" title="${prioKind(i) === 'amb' ? 'Ambulance' : 'Fire brigade'}">${escapeHtml(label)}</span>`;
}
const RESP_LABELS = {acknowledged: 'Acknowledged', rejected: 'Rejected', none: 'No response'};
function respChip(resp, big) {
    const status = (resp && resp.status) || 'none';
    const icon = {acknowledged: '✔', rejected: '✖', none: '…'}[status] || '';
    return `<span class="resp ${status}${big ? ' big' : ''}">${icon} ${RESP_LABELS[status] || escapeHtml(status)}</span>`;
}
function km(i) {
    return i && i.distance_km !== null && i.distance_km !== undefined ? `${i.distance_km} km from home` : '';
}

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
        <div class="modal">
            <div class="modal-head">
                <span class="modal-title">${escapeHtml(opts.title || '')}</span>
                <button class="modal-close" data-close>✕</button>
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

function confirmDialog(opts) {
    return new Promise((resolve) => {
        const overlay = openModal({
            title: opts.title || 'Are you sure?',
            bodyHTML: `<div>${opts.message || ''}</div>`,
            footHTML: `
                <button class="btn" data-close>Cancel</button>
                <button class="btn ${opts.confirmClass || (opts.danger ? 'btn-danger-solid' : 'btn-primary')}" data-confirm>${escapeHtml(opts.confirmLabel || 'Confirm')}</button>`
        });
        overlay.querySelector('[data-confirm]').addEventListener('click', () => { closeModal(); resolve(true); });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.hasAttribute('data-close')) resolve(false);
        });
        overlay.querySelector('[data-confirm]').focus();
    });
}

// =====================================================================
// Sound, speech, notifications, auto-focus (quiet hours from the app options)
// =====================================================================
let soundEnabled = localStorage.getItem('fsr_sound_enabled') !== 'false';
let audioContext = null;
let quietNow = false;
let quietHours = '';
function quietSuffix() { return quietNow ? ` · quiet hours (${quietHours})` : ''; }
function applyQuiet(status) {
    const now = Boolean(status.quiet_now);
    if (now === quietNow && (status.quiet_hours || '') === quietHours) return;
    quietNow = now;
    quietHours = status.quiet_hours || '';
    ['sound-toggle', 'tts-toggle', 'notify-toggle'].forEach(id => document.getElementById(id).classList.toggle('quiet', quietNow));
    initSoundToggle(); initTtsToggle(); initNotifyToggle();
}

function initSoundToggle() {
    const btn = document.getElementById('sound-toggle');
    btn.classList.toggle('active', soundEnabled);
    btn.classList.toggle('off', !soundEnabled);
    document.getElementById('sound-icon').textContent = soundEnabled ? '🔔' : '🔕';
    btn.title = (soundEnabled ? 'Sound ON - a new incident beeps - click to disable' : 'Sound OFF - click to enable') + quietSuffix();
}

function toggleSound() {
    soundEnabled = !soundEnabled;
    localStorage.setItem('fsr_sound_enabled', soundEnabled);
    initSoundToggle();
    if (soundEnabled) { unlockAudio(); playNotificationSound(); }
}

function unlockAudio() {
    if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === 'suspended') audioContext.resume();
}
document.addEventListener('click', unlockAudio, {once: true});
document.addEventListener('touchstart', unlockAudio, {once: true});

function playNotificationSound() {
    if (!soundEnabled || quietNow) return;
    try {
        unlockAudio();
        // Three rising beeps: more insistent than a plain message tone
        [0, 0.18, 0.36].forEach((offset, n) => {
            const oscillator = audioContext.createOscillator();
            const gain = audioContext.createGain();
            oscillator.connect(gain);
            gain.connect(audioContext.destination);
            const t = audioContext.currentTime + offset;
            oscillator.frequency.setValueAtTime(760 + n * 180, t);
            gain.gain.setValueAtTime(0.3, t);
            gain.gain.exponentialRampToValueAtTime(0.01, t + 0.16);
            oscillator.start(t);
            oscillator.stop(t + 0.16);
        });
    } catch (e) {
        console.log('Audio playback failed:', e);
    }
}

let notifyEnabled = localStorage.getItem('fsr_notify_enabled') === 'true'
    && 'Notification' in window && Notification.permission === 'granted';

function initNotifyToggle() {
    const btn = document.getElementById('notify-toggle');
    btn.classList.toggle('active', notifyEnabled);
    btn.classList.toggle('off', !notifyEnabled);
    btn.title = (notifyEnabled
        ? 'Browser notifications ON - new incidents notify while this page is in the background'
        : 'Browser notifications OFF - click to enable') + quietSuffix();
}

function toggleNotify() {
    if (notifyEnabled) {
        notifyEnabled = false;
        localStorage.setItem('fsr_notify_enabled', 'false');
        initNotifyToggle();
        return;
    }
    if (!('Notification' in window) || !window.isSecureContext) {
        toast('Notifications need Home Assistant over HTTPS (or localhost): open it via your HTTPS address, or allow this address under chrome://flags "Insecure origins treated as secure"', 'error');
        return;
    }
    if (Notification.permission === 'denied') {
        toast('Notifications are blocked for the Home Assistant site: click the lock icon in the address bar, set Notifications to Allow, then try again', 'error');
        return;
    }
    Notification.requestPermission().then(permission => {
        if (permission !== 'granted') {
            toast(permission === 'denied'
                ? 'Notifications were blocked: click the lock icon in the address bar, set Notifications to Allow, then try again'
                : 'No permission yet: look for the notification (bell) icon at the right of the address bar and allow it', 'error');
            return;
        }
        notifyEnabled = true;
        localStorage.setItem('fsr_notify_enabled', 'true');
        initNotifyToggle();
        toast('Browser notifications on: shown while this page is in the background', 'success');
    });
}

function showBrowserNotification(i) {
    if (!notifyEnabled || quietNow || !document.hidden || !i) return;
    try {
        const title = [prioLabel(i), i.type, 'FireServiceRota'].filter(Boolean).join(' · ');
        const n = new Notification(title, {body: [i.body, i.formatted_address].filter(Boolean).join('\n'), tag: 'fsr-' + i.id, renotify: true});
        n.onclick = () => { window.focus(); openIncident(i.id); n.close(); };
    } catch (e) {
        console.warn('Notification failed', e);
    }
}

let ttsEnabled = localStorage.getItem('fsr_tts_enabled') === 'true';
function initTtsToggle() {
    const btn = document.getElementById('tts-toggle');
    btn.classList.toggle('active', ttsEnabled);
    btn.classList.toggle('off', !ttsEnabled);
    document.getElementById('tts-icon').textContent = ttsEnabled ? '🗣️' : '🔇';
    btn.title = (ttsEnabled ? 'Speech ON - new incidents are read aloud by this browser' : 'Speech OFF - click to enable') + quietSuffix();
}
function toggleTts() {
    ttsEnabled = !ttsEnabled;
    localStorage.setItem('fsr_tts_enabled', ttsEnabled);
    initTtsToggle();
    if (!ttsEnabled && window.speechSynthesis) speechSynthesis.cancel();
}
function speakIncident(i) {
    if (!ttsEnabled || quietNow || !window.speechSynthesis || !i || !i.body) return;
    const utterance = new SpeechSynthesisUtterance([i.prio && 'Prio ' + prioLabel(i), i.body].filter(Boolean).join('. '));
    utterance.lang = 'nl-NL';
    speechSynthesis.cancel();
    speechSynthesis.speak(utterance);
}

let autoMapEnabled = localStorage.getItem('fsr_automap_enabled') !== 'false';
function initAutoMapToggle() {
    const btn = document.getElementById('automap-toggle');
    btn.classList.toggle('active', autoMapEnabled);
    btn.classList.toggle('off', !autoMapEnabled);
    document.getElementById('automap-icon').textContent = autoMapEnabled ? '📍' : '📌';
    btn.title = autoMapEnabled ? 'Auto-focus ON - a new incident opens on the map' : 'Auto-focus OFF - click to enable';
}
function toggleAutoMap() {
    autoMapEnabled = !autoMapEnabled;
    localStorage.setItem('fsr_automap_enabled', autoMapEnabled);
    initAutoMapToggle();
}

initSoundToggle();
initNotifyToggle();
initAutoMapToggle();
initTtsToggle();
initThemeToggle();

// =====================================================================
// Map: one Leaflet map, moved into the active view's map slot
// =====================================================================
const FOCUS_ZOOM = 14;
let savedView = null;
try { savedView = JSON.parse(localStorage.getItem('fsr_map_view')); } catch (e) { /* none */ }
const map = savedView
    ? L.map('map').setView([savedView.lat, savedView.lng], savedView.zoom)
    : L.map('map').setView([52.2, 5.3], 8);
const WALL_ZOOM = 16;
let focusZoom = parseInt(localStorage.getItem('fsr_map_zoom'), 10) || FOCUS_ZOOM;
let wallZoom = parseInt(localStorage.getItem('fsr_wall_zoom'), 10) || WALL_ZOOM;
// Only zooms you make yourself are remembered (per view), not the app's own moves
let autoMove = false;
function moveMap(fn) { autoMove = true; try { fn(); } finally { autoMove = false; } }
map.on('zoomend', () => {
    if (autoMove) return;
    if (activeView === 'wallboard') { wallZoom = map.getZoom(); localStorage.setItem('fsr_wall_zoom', wallZoom); }
    else { focusZoom = map.getZoom(); localStorage.setItem('fsr_map_zoom', focusZoom); }
});
map.on('moveend', () => {
    const c = map.getCenter();
    localStorage.setItem('fsr_map_view', JSON.stringify({lat: c.lat, lng: c.lng, zoom: map.getZoom()}));
});
// Tiles come through the app (and Home Assistant's built-in map tile proxy);
// OSM blocks browsers requesting them directly from here.
L.tileLayer(basePath + 'api/map-tiles/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors',
    maxZoom: 19
}).addTo(map);

const homeLayer = L.layerGroup().addTo(map);
let homeSig = null;
let homeLatLng = null;
function renderHome(home) {
    const sig = JSON.stringify(home || null);
    if (sig === homeSig) return;
    homeSig = sig;
    homeLayer.clearLayers();
    homeLatLng = null;
    if (!home) return;
    homeLatLng = [home.latitude, home.longitude];
    const house = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9h13v-9"/><path d="M10 19v-5h4v5"/></svg>';
    L.marker(homeLatLng, {icon: L.divIcon({className: 'home-marker', html: house, iconSize: [24, 24], iconAnchor: [12, 12]}), keyboard: false})
        .addTo(homeLayer).bindTooltip(escapeHtml(home.name || 'Home'), {sticky: true});
    if (!savedView) moveMap(() => map.setView(homeLatLng, 11, {animate: false}));
}

const markers = [];
let currentMarker = null;
function updateMarkers(items) {
    markers.forEach(m => map.removeLayer(m));
    markers.length = 0;
    items.filter(i => i.latitude && i.longitude).slice(0, 25).forEach((i, n) => {
        const urgent = prioKind(i) === 'p1' || prioKind(i) === 'amb';
        const m = L.circleMarker([i.latitude, i.longitude], {
            radius: n === 0 ? 8 : 6, fillColor: urgent ? '#ff5252' : '#00d4ff', color: '#fff', weight: 1, fillOpacity: n === 0 ? 0.95 : 0.7
        }).addTo(map);
        m.bindPopup(`<b>${escapeHtml([prioLabel(i), i.type].filter(Boolean).join(' · '))}</b><br><div style="max-width:220px;word-wrap:break-word;">${escapeHtml(i.body || '')}</div>`);
        m.on('click', () => openIncident(i.id));
        markers.push(m);
    });
}

function focusIncident(i, openPopup) {
    if (!i || !i.latitude || !i.longitude) return;
    if (currentMarker) map.removeLayer(currentMarker);
    currentMarker = L.marker([i.latitude, i.longitude]).addTo(map);
    currentMarker.bindPopup(`<b>${escapeHtml([prioLabel(i), i.type].filter(Boolean).join(' · '))}</b><br><small>${escapeHtml(i.formatted_address || '')}</small><br><div style="margin-top:6px;max-width:250px;word-wrap:break-word;">${escapeHtml(i.body || '')}</div>`);
    // The Wallboard zooms in to street level (16) on the incident; zoom out by hand and it remembers that
    moveMap(() => map.setView([i.latitude, i.longitude], activeView === 'wallboard' ? wallZoom : focusZoom, {animate: false}));
    if (openPopup) currentMarker.openPopup();
}

// =====================================================================
// Views
// =====================================================================
const VIEWS = ['console', 'wallboard'];
const PAGES = ['stats', 'data', 'info'];
let activeView = 'console';

function switchTab(tabName) {
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tabName));
    localStorage.setItem('fsr_tab', tabName);
    document.getElementById('top-kpis').hidden = tabName !== 'console';
    if (tabName === 'stats') loadStats();
    if (tabName === 'data') dbLoadInfo();
    if (VIEWS.includes(tabName)) {
        activeView = tabName;
        document.getElementById('slot-' + tabName).appendChild(document.getElementById('map-root'));
        document.getElementById('detail-dock').appendChild(document.getElementById('detail'));
        setTimeout(() => {
            map.invalidateSize();
            if (tabName === 'wallboard' && lastData && lastData.current.incident) {
                focusedId = lastData.current.incident.id;
                focusIncident(lastData.current.incident, false);
            }
        }, 50);
        if (tabName === 'wallboard') loadTimeline();
        if (evtSource) resyncIfStale();
    }
}

document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (['INPUT', 'SELECT', 'TEXTAREA'].includes(t.tagName) || t.isContentEditable)) return;
    if (document.querySelector('.modal-overlay.open')) return;
    if (e.key === '1') switchTab('console');
    else if (e.key === '2') switchTab('wallboard');
    else if (activeView === 'console' && document.querySelector('#tab-console.active')) {
        if (e.key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); if (selected) stepDetail(1); else openAt(0); }
        else if (e.key === 'k' || e.key === 'ArrowUp') { e.preventDefault(); if (selected) stepDetail(-1); }
        else if (e.key === '/') { e.preventDefault(); document.getElementById('feed-search').focus(); }
    }
});

// Draggable dividers: each sets a CSS size variable, kept per browser; double-click resets
function initSplitters() {
    const root = document.documentElement.style;
    document.querySelectorAll('.splitter').forEach(handle => {
        const name = handle.dataset.size;
        const saved = localStorage.getItem('fsr_size' + name);
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
                localStorage.setItem('fsr_size' + name, root.getPropertyValue(name));
            };
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', up);
        });
        handle.addEventListener('dblclick', () => {
            root.removeProperty(name);
            localStorage.removeItem('fsr_size' + name);
            map.invalidateSize();
        });
    });
}
initSplitters();

function updateClock() {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
}
updateClock();
setInterval(updateClock, 1000);

// =====================================================================
// Responding: Acknowledge / Reject through the integration's response switch
// =====================================================================
function windowState(incident) {
    const until = incident && incident.can_respond_until ? new Date(incident.can_respond_until) : null;
    if (!until || isNaN(until)) return {until: null, left: null, closed: false};
    const left = until - Date.now();
    return {until, left, closed: left <= 0};
}

function respondReason() {
    if (!lastData) return 'Connecting…';
    const c = lastData.current;
    if (c.can_respond) return c.can_respond;
    if (windowState(c.incident).closed) return 'The response window has closed';
    return '';
}

// Your answer on the current incident: 'acknowledged', 'rejected' or null
function answered() {
    const r = lastData && lastData.current.response;
    return r && (r.status === 'acknowledged' || r.status === 'rejected') ? r.status : null;
}
// Before you answer the countdown is the deadline; after, it is how long you can still change your mind
function respondLabel() {
    return answered() ? 'You can change your answer for' : 'Respond within';
}

function countdownHTML(incident) {
    const w = windowState(incident);
    if (!w.until) return '<span class="countdown closed">no response deadline</span>';
    return `<span class="countdown" data-until="${escapeHtml(incident.can_respond_until)}"></span>`;
}

function windowBarHTML(incident) {
    if (!incident || !incident.can_respond_until) return '';
    const from = incident.created_at || incident.first_seen;
    return `<div class="window-bar" data-from="${escapeHtml(from)}" data-until="${escapeHtml(incident.can_respond_until)}"><i></i></div>`;
}

function tickCountdowns() {
    const done = answered();
    document.querySelectorAll('.countdown[data-until]').forEach(el => {
        const left = new Date(el.dataset.until) - Date.now();
        if (left <= 0) {
            el.textContent = 'window closed';
            el.className = 'countdown closed';
            return;
        }
        const s = Math.floor(left / 1000);
        const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
        el.textContent = (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + ':' + String(sec).padStart(2, '0');
        el.className = 'countdown' + (done ? ' answered' : (left < 5 * 60000 ? ' closing' : ''));
        el.title = (done ? 'You can change your answer until ' : 'Respond before ') + formatTime(el.dataset.until);
    });
    document.querySelectorAll('[data-respond-label]').forEach(el => { el.textContent = respondLabel(); });
    document.querySelectorAll('.window-bar[data-until]').forEach(el => {
        const from = new Date(el.dataset.from), until = new Date(el.dataset.until);
        const left = until - Date.now();
        const share = until > from ? Math.max(0, Math.min(1, left / (until - from))) : 0;
        el.firstElementChild.style.width = (share * 100).toFixed(1) + '%';
        el.classList.toggle('closing', !done && left < 5 * 60000);
        el.classList.toggle('answered', Boolean(done));
    });
    // The buttons close with the window, without waiting for the server.
    // After answering, both stay usable so you can change your mind; your answer is outlined.
    document.querySelectorAll('[data-respond]').forEach(btn => {
        const reason = respondReason();
        const mine = done && (btn.dataset.respond === 'acknowledge') === (done === 'acknowledged');
        btn.disabled = Boolean(reason) || responding;
        btn.classList.toggle('chosen', Boolean(mine));
        btn.classList.toggle('not-chosen', Boolean(done && !mine));
        btn.title = reason || (mine ? 'Your current answer'
            : btn.dataset.respond === 'acknowledge' ? 'Acknowledge: switch the response switch on' : 'Reject: switch the response switch off');
    });
    document.querySelectorAll('[data-respond-reason]').forEach(el => { el.textContent = respondReason(); });
}
setInterval(tickCountdowns, 1000);

function respondButtonsHTML() {
    return `<button type="button" class="btn btn-ack" data-respond="acknowledge" onclick="respond('acknowledge')">✔ Acknowledge</button>`
        + `<button type="button" class="btn btn-rej" data-respond="reject" onclick="respond('reject')">✖ Reject</button>`;
}

let responding = false;
function respond(action) {
    const incident = lastData && lastData.current.incident;
    if (!incident) return;
    const verb = action === 'acknowledge' ? 'Acknowledge' : 'Reject';
    confirmDialog({
        title: `${verb} this incident?`,
        message: `<div class="detail-body" style="margin-bottom: 10px;">${escapeHtml(incident.body || '')}</div>`
            + `${action === 'acknowledge' ? 'You answer that you are <strong>coming</strong>.' : 'You answer that you are <strong>not coming</strong>.'} `
            + 'Home Assistant switches the FireServiceRota response switch, which sends it.',
        confirmLabel: verb,
        confirmClass: action === 'acknowledge' ? 'btn-ack' : 'btn-danger-solid',
    }).then(ok => {
        if (!ok) return;
        responding = true;
        tickCountdowns();
        fetch(basePath + 'api/respond', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action})})
            .then(r => r.json().then(body => ({ok: r.ok, body})))
            .then(({ok, body}) => {
                if (!ok) toast(body.error || 'Failed', 'error');
                else toast(`${verb} sent to Home Assistant`, 'success');
            })
            .catch(() => toast('Could not reach the app', 'error'))
            .finally(() => { responding = false; tickCountdowns(); });
    });
}

// =====================================================================
// Rendering: current incident, Console, Wallboard
// =====================================================================
let lastData = null;
let latest = [];

function dutyText(duty) {
    const a = duty.attributes || {};
    const on = duty.state === 'on';
    const shift = a.start_time && a.end_time ? `${formatShortDate(a.start_time)} – ${formatTime(a.end_time)}` : '';
    return {on, shift, func: a.assigned_function || '', a};
}

// Key figures strip: today, the last week, answer rate and speed over 30 days, duty
let kpiStats = {week: null, month: null};
function loadTopKpis() {
    Promise.all(['7', '30'].map(p => fetch(`${basePath}api/stats?period=${p}`).then(r => r.json())))
        .then(([week, month]) => { kpiStats = {week, month}; renderTopKpis(); }).catch(() => {});
}
function renderTopKpis() {
    const el = document.getElementById('top-kpis');
    if (!el || !lastData) return;
    const today = latest.filter(x => isToday(incidentTime(x)));
    const ackToday = today.filter(x => x.response && x.response.status === 'acknowledged').length;
    const {week, month} = kpiStats;
    const pct = (n, t) => t ? Math.round(n / t * 100) + '%' : '—';
    const d = dutyText(lastData.current.duty);
    const known = lastData.current.duty.state !== undefined && lastData.current.duty.state !== null;
    const last = latest[0];
    const cell = (label, value, sub, cls = '') => `<div class="top-kpi"><div class="l">${label}</div>
        <div class="v ${cls}">${value}</div><div class="s">${sub || '&nbsp;'}</div></div>`;
    el.innerHTML = [
        cell('Today', today.length, `${ackToday} acknowledged`),
        cell('Last 7 days', week ? week.total : '—', week ? `${(week.total / 7).toFixed(1)} per day` : ''),
        cell('Acknowledged · 30 d', month ? pct(month.responses.acknowledged, month.total) : '—',
            month ? `${month.responses.rejected} rejected · ${month.responses.none} unanswered` : '', 'ok'),
        cell('Median answer · 30 d', month && month.median_answer_minutes !== null ? month.median_answer_minutes + ' min' : '—', 'after the alarm'),
        cell('Duty', known ? (d.on ? 'On duty' : 'Off duty') : '—', [d.func, d.shift].filter(Boolean).join(' · '), d.on ? 'ok' : ''),
        cell('Last incident', last ? escapeHtml(formatAgo(incidentTime(last))) : '—', last ? escapeHtml(last.type || '') : 'none yet'),
    ].join('');
}

function renderDutyPill(duty) {
    const el = document.getElementById('duty-pill');
    const d = dutyText(duty);
    el.className = 'duty-pill' + (d.on ? ' on' : '');
    el.textContent = duty.state === undefined || duty.state === null ? 'Duty —' : (d.on ? 'On duty' : 'Off duty');
    el.title = [d.on ? 'On duty' : 'Off duty', d.func, d.shift].filter(Boolean).join(' · ');
}

function answerHTML(resp, switchInfo) {
    const parts = [respChip(resp, true)];
    if (resp && resp.responded_at) parts.push(`<span>at ${escapeHtml(formatTime(resp.responded_at))}</span>`);
    if (resp && resp.reported_status) parts.push(`<span>reported: ${escapeHtml(resp.reported_status)}</span>`);
    if (resp && resp.arrived_at_station && resp.arrived_at_station !== '0' && resp.arrived_at_station !== 'False') parts.push('<span>🏠 arrived at station</span>');
    if (switchInfo && switchInfo.state === 'unknown') parts.push('<span>no response yet</span>');
    return parts.join('');
}

function isLive(incident) {
    if (!incident) return false;
    const w = windowState(incident);
    if (w.until) return !w.closed;
    return Date.now() - new Date(incidentTime(incident)) < 30 * 60000;
}

function renderCurrent() {
    const c = lastData.current;
    const i = c.incident;
    const el = document.getElementById('current-card');
    if (!i) {
        el.className = 'current-card';
        el.innerHTML = '<div class="where">No incident yet. New incidents appear here the moment the integration gets them.</div>';
        return;
    }
    el.className = 'current-card' + (isLive(i) ? ' live' : '');
    el.innerHTML = `
        <div style="min-width: 0; display: flex; flex-direction: column; gap: 4px; cursor: pointer;" onclick="openIncident('${escapeHtml(i.id)}')">
            <div class="top"><span class="label">${isLive(i) ? 'Current incident' : 'Last incident'}</span>${prioChip(i)}<b>${escapeHtml(i.type || '')}</b>
                <span class="where">${escapeHtml(rowTime(incidentTime(i)))} · ${ago(incidentTime(i))} ago</span></div>
            <div class="body">${escapeHtml(i.body || '(text not available)')}</div>
            <div class="where">${escapeHtml([i.formatted_address, km(i)].filter(Boolean).join(' · '))}</div>
            <div class="respond-row">${answerHTML(c.response, c.switch)}</div>
        </div>
        <div class="act">
            <div class="respond-row"><span class="where" data-respond-label>${respondLabel()}</span> ${countdownHTML(i)}</div>
            <div class="respond-row">${respondButtonsHTML()}</div>
            <div class="respond-reason" data-respond-reason></div>
        </div>`;
}

function renderWallboard() {
    const c = lastData.current;
    const i = c.incident;
    const el = document.getElementById('wall-incident');
    if (!i) {
        el.className = 'wall-incident';
        el.innerHTML = '<div class="empty-state" style="font-size: 18px;">No incidents yet</div>';
    } else {
        el.className = 'wall-incident' + (isLive(i) ? ' live' : '');
        el.innerHTML = `
            <div class="wall-head">${prioChip(i)}<span class="type">${escapeHtml(i.type || 'Incident')}</span>
                <span class="age">${escapeHtml(formatTime(incidentTime(i)))} · ${ago(incidentTime(i))} ago</span></div>
            <div class="wall-body">${escapeHtml(i.body || '(text not available)')}</div>
            <div class="wall-where"><b>${escapeHtml(i.formatted_address || 'No address')}</b>${km(i) ? ' · ' + escapeHtml(km(i)) : ''}</div>
            ${wallFactsHTML(i, c)}
            <div class="wall-answer">${answerHTML(c.response, null)}${i.message_to_speech_url
                ? `<audio controls preload="none" src="${escapeHtml(i.message_to_speech_url)}" title="Spoken message, played by this browser from FireServiceRota"></audio>` : ''}</div>
            <div class="wall-respond">
                <div><div class="sub" data-respond-label>${respondLabel()}</div>${countdownHTML(i)}</div>
                <div class="wall-buttons">${respondButtonsHTML()}</div>
                ${windowBarHTML(i)}
                <div class="sub" data-respond-reason style="grid-column: 1 / -1;"></div>
            </div>`;
    }
    const d = dutyText(c.duty);
    document.getElementById('wall-duty').innerHTML = `
        <span class="duty-pill${d.on ? ' on' : ''}">${d.on ? 'On duty' : (c.duty.state ? 'Off duty' : 'Duty unknown')}</span>
        ${d.func ? `<b>${escapeHtml(d.func)}</b>` : ''}
        ${d.shift ? `<span class="shift">${escapeHtml(d.shift)}</span>` : ''}
        ${d.a.available !== undefined ? `<span class="panel-sub">${d.a.available ? 'available' : 'not available'}${d.a.active !== undefined ? ' · ' + (d.a.active ? 'active' : 'not active') : ''}</span>` : ''}`;

    const today = latest.filter(x => isToday(incidentTime(x)));
    const answered = today.filter(x => x.response && x.response.status === 'acknowledged').length;
    document.getElementById('wall-kpis').innerHTML = `
        <div class="wall-kpi"><div class="v">${today.length}</div><div class="l">Today</div></div>
        <div class="wall-kpi"><div class="v">${answered}</div><div class="l">Acknowledged</div></div>
        <div class="wall-kpi"><div class="v">${i && i.distance_km !== null && i.distance_km !== undefined ? i.distance_km : '—'}</div><div class="l">km</div></div>`;

    const recent = latest.filter(x => !i || x.id !== i.id).slice(0, 10);
    document.getElementById('wall-recent').innerHTML = recent.length ? recent.map(x => `
        <button type="button" class="recent-row" onclick="switchTab('console'); openIncident('${escapeHtml(x.id)}')"
            title="${escapeHtml([formatDate(incidentTime(x)), x.formatted_address, x.body].filter(Boolean).join('\n'))}">
            <span class="time">${escapeHtml(rowTime(incidentTime(x)))}</span>${prioChip(x)}<span class="type">${escapeHtml(x.type || '')}</span>
            <span class="text">${escapeHtml(x.body || '(text not available)')}</span>${respChip(x.response)}
        </button>`).join('') : '<div class="panel-sub" style="padding: 8px 24px;">No earlier calls</div>';
}

// The incident's and your response's attributes, as small facts under the address
// (your function and shift are in the duty row below)
const RESPONDER_MODES = {available_in_schedule_is_acknowledgment: 'on schedule = acknowledged'};
function wallFactsHTML(i, c) {
    const sw = (c.switch && c.switch.attributes) || {};
    const yesNo = v => (v === true || v === 'True' || v === 'true' || v === 1 || v === '1') ? 'yes'
        : (v === false || v === 'False' || v === 'false' || v === 0 || v === '0') ? 'no' : String(v);
    const has = v => v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && !v.length);
    const facts = [
        ['Alarm', i.created_at && formatTime(i.created_at)],
        ['Deadline', i.can_respond_until && formatTime(i.can_respond_until)],
        ['Status', i.state],
        ['Mode', i.responder_mode && (RESPONDER_MODES[i.responder_mode] || String(i.responder_mode).replace(/_/g, ' '))],
        ['Location', i.address_type && String(i.address_type).replace(/_/g, ' ')],
        ['Tasks', has(i.task_ids) ? (Array.isArray(i.task_ids) ? i.task_ids.length : i.task_ids) : null],
        ['Updates', i.updates > 1 ? i.updates : null],
        ['Responder', sw.user_name],
        ['Reported', sw.reported_status],
        ['Available at alarm', has(sw.available_at_incident_creation) ? yesNo(sw.available_at_incident_creation) : null],
    ].filter(([, v]) => has(v));
    if (!facts.length) return '';
    return `<div class="wall-facts">${facts.map(([k, v]) => `<span class="fact" title="${escapeHtml(k)}: ${escapeHtml(v)}"><span class="k">${escapeHtml(k)}</span><span class="v">${escapeHtml(v)}</span></span>`).join('')}</div>`;
}

// History strip: the incident sensor over the last 48 hours
let timelineItems = [];
function loadTimeline() {
    fetch(basePath + 'api/timeline?hours=48').then(r => r.json()).then(data => { timelineItems = data.items; renderTimeline(); }).catch(() => {});
}
// One bar per clock hour, stacked by priority, drawn at the element's real pixel size
// (no stretching, so lines and text stay sharp). Updates of an incident are not counted again.
const PRIO_ORDER = ['p1', 'amb', 'p2', 'other'];
const PRIO_FILL = {p1: 'var(--prio-1)', amb: 'var(--disc-ambulance)', p2: 'var(--prio-2)', other: 'var(--primary-color)'};
const PRIO_NAME = {p1: 'Prio 1', amb: 'Ambulance', p2: 'Prio 2', other: 'Other'};
function renderTimeline() {
    const el = document.getElementById('wall-history');
    if (!el || !el.clientWidth) return;
    const hours = 48, width = el.clientWidth, full = el.clientHeight || 80;
    const top = 12, bottom = 16, height = Math.max(24, full - top - bottom);
    const end = new Date(); end.setMinutes(0, 0, 0); end.setHours(end.getHours() + 1);
    const start = end - hours * 3600000;
    const slots = Array.from({length: hours}, (_, n) => ({t: start + n * 3600000, kinds: {}, items: []}));
    const seen = new Set();
    timelineItems.forEach(u => {
        if (u.trigger === 'update' || seen.has(u.incident_id)) return;
        seen.add(u.incident_id);
        const n = Math.floor((new Date(u.ts) - start) / 3600000);
        if (n < 0 || n >= hours) return;
        const k = prioKind(u);
        slots[n].kinds[k] = (slots[n].kinds[k] || 0) + 1;
        slots[n].items.push(u);
    });
    const max = Math.max(1, ...slots.map(s => s.items.length));
    const unit = height / max, slot = width / hours, base = top + height;
    const hh = t => new Date(t).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hour12: false});
    let grid = '';
    // Horizontal guides at whole counts (at most 3)
    const step = Math.max(1, Math.ceil(max / 3));
    for (let v = step; v <= max; v += step) {
        const y = Math.round(base - v * unit) + 0.5;
        grid += `<line x1="0" y1="${y}" x2="${width}" y2="${y}" stroke="var(--divider-color)" stroke-dasharray="2 3"/>`
            + `<text x="${width}" y="${y - 2}" font-size="10" text-anchor="end" fill="var(--secondary-text-color)">${v}</text>`;
    }
    // Time labels every 6 hours, midnight drawn as a day boundary
    slots.forEach((s, n) => {
        const d = new Date(s.t);
        const x = Math.round(n * slot) + 0.5;
        if (d.getHours() === 0) {
            grid += `<line x1="${x}" y1="${top - 4}" x2="${x}" y2="${base}" stroke="var(--secondary-text-color)" stroke-opacity="0.5"/>`
                + `<text x="${x + 3}" y="${top - 3}" font-size="10" fill="var(--secondary-text-color)">${escapeHtml(d.toLocaleDateString([], {weekday: 'short', day: 'numeric'}))}</text>`;
        }
        if (d.getHours() % 6 === 0 && x > 14 && x < width - 20) {
            grid += `<text x="${x}" y="${base + 12}" font-size="10" text-anchor="middle" fill="var(--secondary-text-color)">${hh(s.t)}</text>`;
        }
    });
    const gap = slot > 6 ? 1.5 : 0.5;
    const bars = slots.map((s, n) => {
        const x = n * slot + gap / 2, w = Math.max(1, slot - gap);
        let y = base, rects = '';
        PRIO_ORDER.forEach(k => {
            const c = s.kinds[k];
            if (!c) return;
            const h = c * unit;
            y -= h;
            rects += `<rect class="bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(1.5, w / 4).toFixed(1)}" fill="${PRIO_FILL[k]}"/>`;
        });
        const tip = `${hh(s.t)}–${hh(s.t + 3600000)}: ${s.items.length ? s.items.length + ' incident' + (s.items.length > 1 ? 's' : '') : 'none'}`
            + s.items.map(u => `\n${formatTime(u.ts)} · ${[prioLabel(u), u.type].filter(Boolean).join(' · ')}${u.body ? ' — ' + u.body.slice(0, 80) : ''}`).join('');
        const click = s.items.length ? ` onclick="switchTab('console'); openIncident('${escapeHtml(s.items[s.items.length - 1].incident_id)}')" style="cursor:pointer"` : '';
        return `<g class="slot"${click}><title>${escapeHtml(tip)}</title><rect class="hit" x="${(n * slot).toFixed(1)}" y="${top}" width="${slot.toFixed(1)}" height="${height}" fill="transparent"/>${rects}</g>`;
    }).join('');
    const nowX = Math.round((Date.now() - start) / (end - start) * width) + 0.5;
    const used = PRIO_ORDER.filter(k => slots.some(s => s.kinds[k]));
    const legendEl = document.getElementById('wall-history-legend');
    if (legendEl) legendEl.innerHTML = used.map(k => `<span><i style="background:${PRIO_FILL[k]}"></i>${PRIO_NAME[k]}</span>`).join('');
    el.innerHTML = `<svg width="${width}" height="${full}" viewBox="0 0 ${width} ${full}" shape-rendering="crispEdges">
        ${grid}<line x1="0" y1="${base + 0.5}" x2="${width}" y2="${base + 0.5}" stroke="var(--divider-color)"/>
        <g shape-rendering="geometricPrecision">${bars}</g>
        <line x1="${nowX}" y1="${top}" x2="${nowX}" y2="${base}" stroke="var(--primary-color)" stroke-width="1.5"><title>now</title></line>
        ${seen.size ? '' : `<text x="${width / 2}" y="${top + height / 2 + 4}" font-size="12" text-anchor="middle" fill="var(--secondary-text-color)">No incidents in the last 48 hours</text>`}
    </svg>`;
}
if (window.ResizeObserver) new ResizeObserver(() => renderTimeline()).observe(document.getElementById('wall-history'));

// Console feed: the live list (plus older incidents on demand) or, while
// searching, results from the database; both narrowed by the chips
const FEED_BATCH = 50;
let feedSearch = '';
let feedArchive = null;
let feedOlder = [];
let feedHasMore = true;
let feedNextBefore = null;
let feedLoading = false;
let feedRequest = 0;
let consoleShown = [];
const feedPrio = new Set();
const feedType = new Set();
const feedResp = new Set();

function feedBase() {
    if (feedArchive) return feedArchive;
    const live = new Set(latest.map(i => i.id));
    return latest.concat(feedOlder.filter(i => !live.has(i.id)));
}
function consoleList() {
    return feedBase().filter(i => (!feedPrio.size || feedPrio.has(prioKind(i)))
        && (!feedType.size || feedType.has(i.type || ''))
        && (!feedResp.size || feedResp.has((i.response && i.response.status) || 'none')));
}
function toggleFeedChip(btn, group, value) {
    const set = {prio: feedPrio, type: feedType, resp: feedResp}[group];
    if (set.has(value)) set.delete(value); else set.add(value);
    btn.classList.toggle('active', set.has(value));
    renderConsole();
}
let chipsSig = null;
function renderChips() {
    const types = [...new Set(feedBase().map(i => i.type).filter(Boolean))].sort().slice(0, 8);
    const sig = types.join('|');
    if (sig === chipsSig) return;
    chipsSig = sig;
    document.getElementById('prio-chips').innerHTML = [['p1', 'Prio 1'], ['p2', 'Prio 2'], ['amb', 'Ambulance'], ['other', 'Other']]
        .map(([v, l]) => `<button type="button" class="tab chip${feedPrio.has(v) ? ' active' : ''}" onclick="toggleFeedChip(this, 'prio', '${v}')">${l}</button>`).join('');
    document.getElementById('type-chips').innerHTML = types
        .map(t => `<button type="button" class="tab chip${feedType.has(t) ? ' active' : ''}" data-type="${escapeHtml(t)}" onclick="toggleFeedChip(this, 'type', this.dataset.type)">${escapeHtml(t)}</button>`).join('');
}
function resetFeed() {
    feedArchive = null; feedOlder = []; feedHasMore = true; feedNextBefore = null; feedLoading = false; feedRequest++;
}
let feedSearchTimer = null;
function onFeedSearch(value) {
    clearTimeout(feedSearchTimer);
    feedSearchTimer = setTimeout(() => {
        const v = value.trim();
        if (v === feedSearch) return;
        feedSearch = v;
        resetFeed();
        if (feedSearch) loadOlder(); else renderConsole();
    }, 300);
}
function loadOlder() {
    if (feedLoading || !feedHasMore) return;
    const base = feedBase();
    const before = feedNextBefore || (base.length ? base[base.length - 1].first_seen : '');
    if (!feedSearch && !before) return;
    const q = new URLSearchParams({limit: FEED_BATCH});
    if (feedSearch) q.set('search', feedSearch);
    if (before) q.set('before', before);
    const request = ++feedRequest;
    feedLoading = true;
    renderConsole();
    fetch(`${basePath}api/incidents?${q}`)
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(data => {
            if (request !== feedRequest) return;
            if (feedSearch) feedArchive = (feedArchive || []).concat(data.items);
            else feedOlder = feedOlder.concat(data.items);
            feedHasMore = data.has_more;
            feedNextBefore = data.next_before;
        })
        .catch(e => { if (request === feedRequest) toast(`Failed to load incidents (${e.message})`, 'error'); })
        .then(() => { if (request === feedRequest) { feedLoading = false; renderConsole(); } });
}

function renderConsole() {
    renderChips();
    const el = document.getElementById('console-rows');
    const scroll = el.scrollTop;
    consoleShown = consoleList();
    const currentId = lastData && lastData.current.incident ? lastData.current.incident.id : null;
    const rows = consoleShown.map((i, idx) => `
        <button type="button" class="feed-row${i.id === currentId ? ' current' : ''}${selected && selected.id === i.id ? ' selected' : ''}" data-id="${escapeHtml(i.id)}" onclick="openAt(${idx})">
            <span class="time">${escapeHtml(rowTime(incidentTime(i)))}</span>
            <span>${prioChip(i)}</span>
            <span class="type">${escapeHtml(i.type || '—')}</span>
            <span class="text">${escapeHtml(i.body || '(text not available)')}${i.updates > 1 ? `<span class="badge" title="${i.updates - 1} update(s)">✎ ${i.updates - 1}</span>` : ''}</span>
            <span class="address">${escapeHtml(i.formatted_address || '')}</span>
            <span>${respChip(i.response)}</span>
        </button>`).join('');
    const base = feedBase().length;
    const empty = feedSearch
        ? (feedLoading ? 'Searching…' : `No incidents found for “${escapeHtml(feedSearch)}”`)
        : (base ? 'No incidents match the selected chips' : 'No incidents yet');
    const more = feedHasMore && (feedSearch || base)
        ? `<button type="button" class="btn feed-more" onclick="loadOlder()"${feedLoading ? ' disabled' : ''}>${feedLoading ? 'Loading…' : 'Older incidents'}</button>`
        : '';
    el.innerHTML = (rows || `<div class="empty-state">${empty}</div>`) + more;
    el.scrollTop = scroll;
    document.getElementById('feed-status').textContent = feedSearch
        ? `${consoleShown.length} found`
        : (feedOlder.length ? `live + ${feedOlder.length} older` : 'live');
}

// =====================================================================
// Incident detail (docked under the Console map)
// =====================================================================
let selected = null;
let detailRequest = 0;

function openAt(idx) {
    if (consoleShown[idx]) openIncident(consoleShown[idx].id);
}
function stepDetail(step) {
    const idx = consoleShown.findIndex(i => selected && i.id === selected.id) + step;
    if (idx >= 0 && idx < consoleShown.length) openAt(idx);
}

function openIncident(id) {
    if (id === null || id === undefined) return;
    const request = ++detailRequest;
    fetch(`${basePath}api/incidents/${encodeURIComponent(id)}`)
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(i => { if (request === detailRequest) showDetails(i); })
        .catch(() => toast(`Incident ${id} not found`, 'error'));
}

function showDetails(i) {
    selected = i;
    document.querySelectorAll('.feed-row').forEach(el => el.classList.toggle('selected', el.dataset.id === String(i.id)));
    const isCurrent = lastData && lastData.current.incident && lastData.current.incident.id === i.id;
    const attrs = i.attributes || {};
    const fields = [
        ['Type', i.type || '—'],
        ['Priority', prioLabel(i) || '—'],
        ['Trigger', i.trigger || '—'],
        i.state ? ['State', i.state] : null,
        ['Responder mode', (i.responder_mode || '—').replace(/_/g, ' ')],
        ['Created', i.created_at ? formatDate(i.created_at) : '—'],
        ['Respond until', i.can_respond_until ? formatDate(i.can_respond_until) : '—'],
        ['Address', [i.formatted_address, i.address_type && `(${i.address_type})`].filter(Boolean).join(' ') || '—', true],
        (i.latitude && i.longitude) ? ['Position', `${Number(i.latitude).toFixed(5)}, ${Number(i.longitude).toFixed(5)}${km(i) ? ' · ' + km(i) : ''}`, true] : null,
        i.task_ids && i.task_ids.length ? ['Tasks', i.task_ids.join(', ')] : null,
        ['Incident id', i.id],
        ['Stored', `${formatDate(i.first_seen)} (${i.source === 'history' ? 'from the recorder history' : 'live'})`, true],
    ].filter(Boolean);
    const audio = i.message_to_speech_url
        // preload="none": the browser fetches the spoken message only when you press play
        ? `<audio controls preload="none" src="${escapeHtml(i.message_to_speech_url)}" title="Spoken message, played by your browser from FireServiceRota"></audio>`
        : '';
    const extra = Object.keys(attrs).filter(k => !['id', 'trigger', 'state', 'created_at', 'message_to_speech_url', 'prio', 'type', 'responder_mode',
        'can_respond_until', 'task_ids', 'latitude', 'longitude', 'address_type', 'formatted_address', 'friendly_name', 'icon'].includes(k));
    document.getElementById('detail-content').innerHTML = `
        <div class="detail-head">
            ${prioChip(i)}
            <h2>${escapeHtml(i.type || 'Incident')}</h2>
            ${respChip(i.response)}
            <span class="when">${escapeHtml(formatDate(incidentTime(i)))}${km(i) ? ' · ' + escapeHtml(km(i)) : ''}</span>
        </div>
        <div class="detail-body">${escapeHtml(i.body || '(the text was longer than Home Assistant can store in a state)')}</div>
        <div class="detail-actions">
            ${isCurrent ? respondButtonsHTML() + countdownHTML(i) + '<span class="respond-reason" data-respond-reason></span>' : ''}
            ${audio}
        </div>
        <dl class="detail-fields">${fields.map(([label, value, wide]) =>
            `<dt${wide ? ' class="wide"' : ''}>${escapeHtml(label)}</dt><dd${wide ? ' class="wide"' : ''}>${escapeHtml(value)}</dd>`).join('')}</dl>
        ${extra.length ? `<div class="raw-block"><span class="raw-label">Other attributes</span><pre>${escapeHtml(JSON.stringify(Object.fromEntries(extra.map(k => [k, attrs[k]])), null, 1))}</pre></div>` : ''}`;

    // One timeline: the incident's updates and every change of the response switch
    const events = (i.timeline || []).map(u => ({ts: u.ts, what: u.trigger === 'update' ? 'Update' : 'Incident', text: [u.state && `[${u.state}]`, u.body].filter(Boolean).join(' ')}))
        .concat((i.responses || []).map(r => ({ts: r.ts, what: 'Response', text: [
            r.status ? RESP_LABELS[r.status] || r.status : ({on: 'on', off: 'off', unknown: 'no response yet', unavailable: 'unavailable (not on duty)'}[r.state] || r.state),
            r.responded_at && 'answered ' + formatTime(r.responded_at),
            r.reported_status && 'reported ' + r.reported_status,
            r.arrived_at_station && r.arrived_at_station !== '0' && r.arrived_at_station !== 'False' && 'arrived at station',
        ].filter(Boolean).join(' · ')})))
        .sort((a, b) => a.ts < b.ts ? -1 : 1);
    document.getElementById('timeline-section').hidden = !events.length;
    document.getElementById('timeline-list').innerHTML = events.map(e => `
        <div class="timeline-item"><span class="time">${escapeHtml(formatShortDate(e.ts))}</span><span class="what">${escapeHtml(e.what)}</span><span class="text" title="${escapeHtml(e.text)}">${escapeHtml(e.text)}</span></div>`).join('');

    const idx = consoleShown.findIndex(x => x.id === i.id);
    document.getElementById('detail-nav').hidden = idx < 0;
    document.getElementById('detail-pos').textContent = `Incident ${idx + 1} of ${consoleShown.length}`;
    history.replaceState(null, '', '#incident=' + i.id);
    // A refresh of the open detail keeps the map where you put it
    if (!refreshDetail) focusIncident(i, true);
    refreshDetail = false;
    tickCountdowns();
}

// Deep links: #incident=<id> opens that incident. Inside Home Assistant the
// dashboard runs in the ingress iframe, so the outer page's hash counts too.
function deepLinkedId() {
    let hash = location.hash;
    try {
        if (window.parent !== window && window.parent.location.hash) hash = window.parent.location.hash;
    } catch (e) { /* not inside Home Assistant */ }
    const m = /[#&]incident=([^&]+)/.exec(hash || '');
    return m ? decodeURIComponent(m[1]) : null;
}
let pendingId = deepLinkedId();
window.addEventListener('hashchange', () => { pendingId = deepLinkedId(); openDeepLink(); });
function openDeepLink() {
    if (pendingId === null || !lastData) return;
    const id = pendingId;
    pendingId = null;
    switchTab('console');
    openIncident(id);
}

// =====================================================================
// Live stream
// =====================================================================
let lastTopKey = null;
function checkForNew(current) {
    const i = current.incident;
    const key = i ? `${i.id}|${i.updates}` : '';
    if (lastTopKey === null) { lastTopKey = key; return; }
    if (i && key !== lastTopKey) {
        const isNewIncident = !lastTopKey.startsWith(i.id + '|');
        if (isNewIncident) {
            playNotificationSound();
            speakIncident(i);
            showBrowserNotification(i);
            if (autoMapEnabled && activeView === 'console') openIncident(i.id);
        } else {
            toast(`Incident updated: ${i.body || ''}`.slice(0, 160));
        }
        if (activeView === 'wallboard') focusIncident(i, false);
        loadTimeline();
    }
    lastTopKey = key;
}

let markersSig = null;
let lastWallSig = null;
let focusedId = null;
let currentSig = null;
let refreshDetail = false;
function update(data) {
    lastData = data;
    latest = data.latest || [];
    const status = data.status;
    applyQuiet(status);
    renderHome(status.home);
    checkForNew(data.current);
    renderDutyPill(data.current.duty);
    renderStatus(status);
    renderCurrent();
    // The Wallboard is redrawn only when something on it changed: an audio player keeps playing
    const wallSig = JSON.stringify([data.current, latest.slice(0, 5)]);
    if (wallSig !== lastWallSig) { lastWallSig = wallSig; renderWallboard(); }
    const cur0 = data.current.incident;
    if (activeView === 'wallboard' && cur0 && cur0.id !== focusedId) { focusedId = cur0.id; focusIncident(cur0, false); }
    renderConsole();
    renderTopKpis();
    const sig = JSON.stringify(latest.map(i => [i.id, i.latitude, i.longitude]));
    if (sig !== markersSig) { markersSig = sig; updateMarkers(latest); }
    updateInfo(status, data.counts);
    // The open detail of the current incident follows its updates and your answer
    const cur = data.current.incident;
    const curSig = cur ? JSON.stringify([cur.id, cur.updates, data.current.response, data.current.switch.state]) : '';
    if (selected && cur && selected.id === cur.id && curSig !== currentSig && !document.querySelector('.modal-overlay.open')) {
        refreshDetail = true;
        openIncident(cur.id);
    }
    currentSig = curSig;
    tickCountdowns();
    openDeepLink();
}

const INTEGRATION_DOCS = 'https://www.home-assistant.io/integrations/fireservicerota/';
const SENSOR_NAMES = {incidents: 'incidents sensor', duty: 'duty sensor', response: 'response switch'};

// This app only shows what Home Assistant's FireServiceRota integration reports, so without that
// integration (or with some of its entities missing) there is nothing to show: say so, and say what to do.
function renderIntegrationWarning(status) {
    const el = document.getElementById('integration-warning');
    const ents = (status && status.entities) || {};
    const missing = ['incidents', 'duty', 'response'].filter(k => !(ents[k] && ents[k].entity_id));
    // Offline or not yet connected says nothing about the sensors
    if (!status || !status.connected || !missing.length) { el.hidden = true; el.innerHTML = ''; return; }
    const notInstalled = !status.config_entries && status.entity_source !== 'options';
    const unreadable = /^Could not read/.test(status.discovery_error || '');
    let html;
    if (unreadable) {
        html = `<div class="iw-title">Could not look for the FireServiceRota sensors</div>
            <div>${escapeHtml(status.discovery_error)}. The app tries again every minute.</div>`;
    } else if (notInstalled) {
        html = `<div class="iw-title">FireServiceRota sensors not detected</div>
            <div>This dashboard shows what Home Assistant's FireServiceRota integration reports, so install and configure that integration first:</div>
            <ol>
                <li>Go to <b>Settings → Devices &amp; services → Add integration</b> and choose <b>FireServiceRota</b>.</li>
                <li>Sign in with your FireServiceRota account (the integration is <a href="${INTEGRATION_DOCS}" target="_blank" rel="noopener">documented here</a>).</li>
                <li>Come back here: this page finds the sensors by itself within a minute.</li>
            </ol>`;
    } else {
        html = `<div class="iw-title">Some FireServiceRota sensors are missing</div>
            <div>Not found in Home Assistant: <b>${missing.map(k => SENSOR_NAMES[k]).join(', ')}</b>. The integration is set up, but did not create ${missing.length === 1 ? 'it' : 'them'}.
            Check that it works under <b>Settings → Devices &amp; services → FireServiceRota</b>, or set the entity IDs in this app's options. The app looks again every minute.</div>`;
    }
    el.innerHTML = `<span class="iw-icon" aria-hidden="true">⚠</span><div>${html}</div>`;
    el.hidden = false;
}

let streamState = 'CONNECTING…';
function renderStatus(status) {
    renderIntegrationWarning(status);
    const el = document.getElementById('ha-status');
    let cls, text, title;
    if (streamState) {
        [cls, text, title] = ['decoder-warn', streamState, 'No live connection to the app'];
    } else if (!status) {
        [cls, text, title] = ['decoder-warn', 'CONNECTING…', ''];
    } else if (!status.connected) {
        [cls, text, title] = ['decoder-bad', 'HOME ASSISTANT OFFLINE', status.ws_error || 'No websocket connection to Home Assistant'];
    } else if (status.discovery_error) {
        [cls, text, title] = ['decoder-warn', 'INTEGRATION NOT FOUND', status.discovery_error];
    } else {
        [cls, text, title] = ['decoder-ok', 'LIVE', 'Following the FireServiceRota integration in Home Assistant'];
    }
    el.className = cls;
    el.textContent = '● ' + text;
    el.title = title;
    if (status) {
        const ents = status.entities || {};
        document.getElementById('entities-info').textContent = ['incidents', 'duty', 'response'].map(k => ents[k] && ents[k].entity_id).filter(Boolean).join(' · ');
    }
}

let evtSource = null;
const STREAM_TIMEOUT_MS = 45000;
let lastStreamActivity = Date.now();

function connectStream() {
    if (evtSource) evtSource.close();
    lastStreamActivity = Date.now();
    evtSource = new EventSource(basePath + 'api/stream');
    evtSource.onopen = () => { lastStreamActivity = Date.now(); streamState = ''; renderStatus(lastData && lastData.status); };
    evtSource.onmessage = (e) => { lastStreamActivity = Date.now(); streamState = ''; update(JSON.parse(e.data)); };
    evtSource.addEventListener('ping', () => { lastStreamActivity = Date.now(); });
    evtSource.onerror = () => {
        streamState = 'RECONNECTING…';
        renderStatus(lastData && lastData.status);
        const source = evtSource;
        if (source.readyState === EventSource.CLOSED) {
            setTimeout(() => { if (evtSource === source) connectStream(); }, 5000);
        }
    };
}
setInterval(() => {
    if (evtSource && Date.now() - lastStreamActivity > STREAM_TIMEOUT_MS) connectStream();
}, 15000);
function resyncIfStale() {
    if (!evtSource || evtSource.readyState === EventSource.CLOSED) connectStream();
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) resyncIfStale(); });
// Ages keep counting between updates
setInterval(() => {
    if (!lastData) return;
    renderCurrent();
    // Not while the spoken message plays
    const playing = document.querySelector('.wall-answer audio');
    if (!playing || playing.paused) renderWallboard();
    tickCountdowns();
}, 30000);
setInterval(() => { if (activeView === 'wallboard') loadTimeline(); }, 5 * 60000);
setInterval(loadTopKpis, 60000);
loadTopKpis();

// =====================================================================
// Info tab
// =====================================================================
function setText(id, value) { const el = document.getElementById(id); if (el) el.textContent = value; }
function updateInfo(status, counts) {
    setText('info-connection', status.connected ? 'connected' : ('offline' + (status.ws_error ? ' · ' + status.ws_error : '')));
    setText('info-ha-version', status.ha_version || '-');
    setText('info-reconnects', String(status.reconnects || 0));
    setText('info-last-event', formatAgo(status.last_event_at));
    setText('info-home', status.home ? `${status.home.name} (${status.home.latitude.toFixed(3)}, ${status.home.longitude.toFixed(3)})` : 'not set');
    setText('info-integration', status.discovery_error || 'found');
    ['incidents', 'duty', 'response'].forEach(k => {
        const e = (status.entities || {})[k] || {};
        setText('info-ent-' + k, e.entity_id || 'not found');
        document.getElementById('info-ent-' + k).title = e.entity_id ? `State: ${e.state}, changed ${e.last_changed ? formatDate(e.last_changed) : '-'}` : '';
    });
    setText('info-ent-source', status.entity_source === 'options' ? 'app options' : 'entity registry');
    const b = status.backfill || {};
    setText('info-backfill', b.running ? 'running…' : (b.error ? 'failed · ' + b.error : (b.done ? `done · ${b.imported} new · ${status.history_days} days` : '-')));
    setText('info-first', counts.first_incident ? formatDate(counts.first_incident) : '-');
    setText('info-events', status.ha_events ? 'fireservicerota_app_incident' : 'off');
    setText('info-announce', status.announce.enabled ? `${status.announce.tts_entity} → ${status.announce.media_players.join(', ')}` : 'off');
    setText('info-quiet', status.quiet_hours ? `${status.quiet_hours}${status.quiet_now ? ' (now)' : ''}` : 'none');
    setText('info-db-incidents', counts.incidents);
    setText('info-db-updates', counts.incident_updates);
    setText('info-db-responses', counts.responses);
    setText('info-db-duty', counts.duty);
    setText('info-db-size', counts.bytes ? (counts.bytes / 1048576).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' MB' : '-');
    setText('total-incidents', counts.incidents);
    setText('kpi-today', latest.filter(x => isToday(incidentTime(x))).length);
    ['incidents', 'incident_updates', 'responses', 'duty'].forEach(t => setText('db-count-' + t, counts[t]));
    setText('db-backfill', b.running ? 'Importing…' : (b.finished_at ? `Last import ${formatAgo(b.finished_at)}: ${b.imported} new` : (b.error ? 'Failed: ' + b.error : '')));
    const btn = document.getElementById('reimport-btn');
    if (btn) btn.disabled = Boolean(b.running);
}

// =====================================================================
// Stats tab
// =====================================================================
let statsPeriod = localStorage.getItem('fsr_stats_period') || '30';
const COLORS = ['#4c8dff', '#f5a524', '#5fd68a', '#ff6159', '#b18cff', '#4fc3c8'];

function barChart(el, values, labels, opts = {}) {
    if (!values.length || !values.some(v => v)) { el.innerHTML = '<div class="empty-state" style="padding: 20px;">No incidents in this period</div>'; return; }
    // Drawn at the element's own width, so the text keeps its size
    const width = Math.max(280, el.clientWidth || 600), top = 14, height = opts.height || 120, slot = width / values.length;
    const max = Math.max(1, ...values);
    const bars = values.map((v, i) => {
        const x = i * slot;
        const bh = Math.max(v ? 2 : 0, v / max * (height - 4));
        const r = Math.min(2, bh, slot / 4);
        return `<g><title>${escapeHtml(labels[i])}: ${v}</title><rect x="${x}" y="${top}" width="${slot}" height="${height}" fill="transparent"/>`
            + `<path d="M${x + 1},${top + height} v${r - bh} q0,${-r} ${r},${-r} h${Math.max(0, slot - 2 - 2 * r)} q${r},0 ${r},${r} v${bh - r} z" fill="var(--primary-color)"/></g>`;
    }).join('');
    const every = Math.ceil(values.length / Math.max(2, Math.min(opts.labels || 10, Math.floor(width / 70))));
    const ticks = labels.map((l, i) => i % every === 0
        ? `<text x="${i === 0 ? 0 : i * slot + slot / 2}" y="${top + height + 13}" font-size="11" text-anchor="${i === 0 ? 'start' : 'middle'}" fill="var(--secondary-text-color)">${escapeHtml(opts.short ? opts.short(l) : l)}</text>` : '').join('');
    el.innerHTML = `<svg viewBox="0 0 ${width} ${top + height + 18}" style="height:${top + height + 18}px"><line x1="0" y1="${top + height}" x2="${width}" y2="${top + height}" stroke="var(--divider-color)"/>${bars}${ticks}`
        + `<text x="${width}" y="10" font-size="11" text-anchor="end" fill="var(--secondary-text-color)">max ${max}</text></svg>`;
}

function renderPieChart(data, el) {
    const sorted = Object.entries(data || {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
    const total = sorted.reduce((sum, [, v]) => sum + v, 0);
    if (total === 0) { el.innerHTML = '<div class="empty-state" style="padding: 20px;">No data</div>'; return; }
    let cumulative = 0, paths = '', legend = '';
    sorted.forEach(([label, value], i) => {
        const percent = value / total;
        const sx = Math.cos(2 * Math.PI * cumulative), sy = Math.sin(2 * Math.PI * cumulative);
        cumulative += percent;
        const ex = Math.cos(2 * Math.PI * cumulative), ey = Math.sin(2 * Math.PI * cumulative);
        paths += percent >= 1
            ? `<circle r="1" fill="${COLORS[i % COLORS.length]}" opacity="0.85"/>`
            : `<path d="M 0 0 L ${sx} ${sy} A 1 1 0 ${percent > 0.5 ? 1 : 0} 1 ${ex} ${ey} Z" fill="${COLORS[i % COLORS.length]}" opacity="0.85"/>`;
        legend += `<div class="chart-legend-item" title="${escapeHtml(label)}: ${value} (${Math.round(percent * 100)}%)"><span class="swatch" style="background:${COLORS[i % COLORS.length]}"></span>`
            + `<span class="label">${escapeHtml(label)}</span><span class="count">${value}</span></div>`;
    });
    el.innerHTML = `<div class="chart-row"><svg viewBox="-1.1 -1.1 2.2 2.2" style="width:110px;height:110px;flex-shrink:0;transform:rotate(-90deg)">${paths}</svg><div class="chart-legend">${legend}</div></div>`;
}

function minutes(v) {
    if (v === null || v === undefined) return '—';
    return v < 1 ? `${Math.round(v * 60)} s` : `${v.toLocaleString(undefined, {maximumFractionDigits: 1})} min`;
}

function loadStats(period) {
    if (period) { statsPeriod = period; localStorage.setItem('fsr_stats_period', period); }
    document.querySelectorAll('#period-tabs .tab').forEach(t => t.classList.toggle('active', t.dataset.period === statsPeriod));
    fetch(`${basePath}api/stats?period=${statsPeriod}`).then(r => r.json()).then(renderStats).catch(() => toast('Failed to load statistics', 'error'));
}

function renderStats(s) {
    const r = s.responses;
    const pct = n => s.total ? Math.round(n / s.total * 100) + '%' : '—';
    document.getElementById('stats-kpis').innerHTML = `
        <div class="kpi"><div class="v">${s.total}</div><div class="l">Incidents</div><div class="s">${(s.total / Math.max(1, (new Date(s.end) - new Date(s.start)) / 86400000)).toFixed(1)} per day</div></div>
        <div class="kpi"><div class="v ack">${pct(r.acknowledged)}</div><div class="l">Acknowledged</div><div class="s">${r.acknowledged} incidents</div></div>
        <div class="kpi"><div class="v rej">${pct(r.rejected)}</div><div class="l">Rejected</div><div class="s">${r.rejected} incidents</div></div>
        <div class="kpi"><div class="v">${pct(r.none)}</div><div class="l">No response</div><div class="s">${r.none} incidents</div></div>
        <div class="kpi"><div class="v">${minutes(s.median_ack_minutes)}</div><div class="l">Median time to acknowledge</div><div class="s">any answer ${minutes(s.median_answer_minutes)}</div></div>
        <div class="kpi"><div class="v">${s.duty_hours.toLocaleString()} h</div><div class="l">On duty</div><div class="s">${s.duty_share}% of the time</div></div>`;
    const unitName = {day: 'day', week: 'week', month: 'month'}[s.unit];
    document.getElementById('series-title').textContent = `Incidents per ${unitName}`;
    barChart(document.getElementById('series-chart'), s.series.map(x => x.count), s.series.map(x => x.key), {
        short: k => s.unit === 'day' ? new Date(k + 'T12:00').toLocaleDateString([], {day: 'numeric', month: 'short'}) : k,
    });
    const total = r.acknowledged + r.rejected + r.none;
    const w = n => total ? (n / total * 100).toFixed(2) : 0;
    document.getElementById('resp-chart').innerHTML = `
        <div class="resp-bar"><i style="width:${w(r.acknowledged)}%;background:var(--ack)"></i><i style="width:${w(r.rejected)}%;background:var(--rej)"></i><i style="width:${w(r.none)}%;background:var(--none)"></i></div>
        <div class="chart-legend">
            <div class="chart-legend-item"><span class="swatch" style="background:var(--ack)"></span><span class="label">Acknowledged</span><span class="count">${r.acknowledged}</span></div>
            <div class="chart-legend-item"><span class="swatch" style="background:var(--rej)"></span><span class="label">Rejected</span><span class="count">${r.rejected}</span></div>
            <div class="chart-legend-item"><span class="swatch" style="background:var(--none)"></span><span class="label">No response</span><span class="count">${r.none}</span></div>
        </div>
        <div class="panel-sub" style="margin-top: 10px;">An incident counts as answered when the response switch changed after it came in. Time to answer runs from the incident's creation to your answer.</div>`;
    renderPieChart(s.by_type, document.getElementById('type-chart'));
    renderPieChart(s.by_prio, document.getElementById('prio-chart'));
    barChart(document.getElementById('hours-chart'), s.hours, s.hours.map((_, h) => `${String(h).padStart(2, '0')}:00`), {labels: 8, height: 100});
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    barChart(document.getElementById('weekday-chart'), s.weekdays, days, {labels: 7, height: 100});
    const periods = s.duty_periods || [];
    document.getElementById('duty-table').innerHTML = periods.length
        ? '<table class="sensors-table duty-table"><thead><tr><th>On duty from</th><th>Until</th><th class="num">Hours</th><th>Function</th><th>Shift</th></tr></thead><tbody>'
            + periods.map(p => {
                const hours = ((p.end ? new Date(p.end) : new Date()) - new Date(p.start)) / 3600000;
                return `<tr><td>${escapeHtml(formatShortDate(p.start))}</td><td>${p.end ? escapeHtml(formatShortDate(p.end)) : '<b>now</b>'}</td><td class="num">${hours.toFixed(1)}</td>`
                    + `<td>${escapeHtml(p.assigned_function || '—')}</td><td class="mono">${p.shift_start && p.shift_end ? escapeHtml(formatShortDate(p.shift_start) + ' – ' + formatShortDate(p.shift_end)) : '—'}</td></tr>`;
            }).join('') + '</tbody></table>'
        : '<div class="panel-sub">No duty periods in this period.</div>';
}

// =====================================================================
// Data tab
// =====================================================================
function dbLoadInfo() {
    fetch(basePath + 'api/db/info').then(r => r.json()).then(info => {
        ['incidents', 'incident_updates', 'responses', 'duty'].forEach(t => setText('db-count-' + t, info[t]));
    }).catch(() => {});
}
function dbExport(table) { window.location.href = `${basePath}api/db/export/${table}.csv`; }
function dbDownloadBackup() { window.location.href = `${basePath}api/db/backup`; }

function dbReimport() {
    fetch(basePath + 'api/db/reimport', {method: 'POST'}).then(r => r.json()).then(result => {
        if (result.error) toast(result.error, 'error');
        else toast(`Importing the last ${result.days} days from Home Assistant's history…`, 'success');
    }).catch(() => toast('Import failed', 'error'));
}

function dbRestoreDialog() {
    const overlay = openModal({
        title: 'Restore from backup',
        bodyHTML: `
            <div class="panel-sub">Replaces the whole database with the uploaded file (a backup downloaded here). Everything currently in the database is lost - download a backup first if in doubt. The dashboard reloads afterwards.</div>
            <div class="form-field" style="margin-top: 12px;">
                <label>Backup file</label>
                <input type="file" class="input" data-file accept=".sqlite3,.sqlite,.db" style="padding-top: 4px;">
            </div>`,
        footHTML: '<button class="btn" data-close>Cancel</button><button class="btn btn-danger" data-restore disabled>Restore</button>'
    });
    const input = overlay.querySelector('[data-file]');
    const button = overlay.querySelector('[data-restore]');
    input.addEventListener('change', () => { button.disabled = !input.files.length; });
    button.addEventListener('click', () => {
        const file = input.files[0];
        if (!file) return;
        button.disabled = true;
        button.textContent = 'Restoring…';
        const form = new FormData();
        form.append('file', file);
        fetch(`${basePath}api/db/restore`, {method: 'POST', body: form})
            .then(r => r.json())
            .then(result => {
                if (result.error) { toast(result.error, 'error'); button.disabled = false; button.textContent = 'Restore'; return; }
                closeModal();
                toast(`Database restored (${result.incidents} incidents) - reloading…`, 'success');
                setTimeout(() => location.reload(), 1500);
            })
            .catch(() => { toast('Restore failed', 'error'); button.disabled = false; button.textContent = 'Restore'; });
    });
}

function dbResetDatabase() {
    confirmDialog({
        title: 'Reset database',
        message: 'Delete ALL incidents, updates, responses and duty changes from the app\'s database?<br><br><strong>This cannot be undone.</strong> Home Assistant\'s own history is kept.',
        confirmLabel: 'Reset database',
        danger: true
    }).then(ok => {
        if (!ok) return;
        fetch(`${basePath}api/db/reset`, {method: 'DELETE'})
            .then(r => r.json())
            .then(result => {
                if (result.error) { toast(result.error, 'error'); return; }
                toast('Database reset. Import history reads back what Home Assistant still has.', 'success');
                selected = null;
                resetFeed();
                dbLoadInfo();
            })
            .catch(() => toast('Reset failed', 'error'));
    });
}

// =====================================================================
// Start
// =====================================================================
// #tab=wallboard opens a view directly, e.g. for a screen that always shows the Wallboard
const hashTab = (/[#&]tab=([a-z]+)/.exec(location.hash) || [])[1];
const savedTab = hashTab || localStorage.getItem('fsr_tab');
switchTab(VIEWS.concat(PAGES).includes(savedTab) ? savedTab : 'console');
if (/[#&]incident=/.test(location.hash)) switchTab('console');
fetch(basePath + 'api/state').then(r => r.json()).then(update).catch(() => toast('Failed to load the dashboard', 'error'));
connectStream();
