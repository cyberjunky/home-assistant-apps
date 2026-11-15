// The sidebar page: PBS's own interface in a frame, passed through Home Assistant's ingress by the
// app (panel.py), so it is on Home Assistant's own address: no certificate to accept, and PBS's login
// works on http and https alike.

// ---- theme: the same toggle as the other apps (auto follows Home Assistant, then light, then dark) ----
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
let themeChoice = new URLSearchParams(window.location.search).get('theme');
try { themeChoice = themeChoice || localStorage.getItem('pbs_theme'); } catch (e) { /* only a convenience */ }
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
  try { localStorage.setItem('pbs_theme', themeChoice); } catch (e) { /* only a convenience */ }
  initThemeToggle();
  applyTheme();
}
applyTheme();
initThemeToggle();
setInterval(applyTheme, 2000);
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// ---- clock ----
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
updateClock();
setInterval(updateClock, 10000);

// ---- PBS ----
const RETRY_MS = 10000;
const frame = document.getElementById('pbs');
let retryTimer = null;

function setStatus(kind, text) {
  document.getElementById('status').className = `status ${kind}`;
  document.getElementById('status-text').textContent = text;
}

function showMessage(title, html) {
  frame.hidden = true;
  document.getElementById('msg-title').textContent = title;
  document.getElementById('msg-body').innerHTML = html;
  document.getElementById('message').hidden = false;
}

function showFrame() {
  document.getElementById('message').hidden = true;
  if (!frame.getAttribute('src')) frame.src = 'pbs/';
  frame.hidden = false;
}

async function check() {
  clearTimeout(retryTimer);
  let status = null;
  try {
    const response = await fetch('api/status', {cache: 'no-store'});
    status = response.ok ? await response.json() : null;
  } catch (e) { /* shown below */ }
  if (status && status.pbs) {
    setStatus('ok', 'PBS running');
    showFrame();
    return;
  }
  setStatus('bad', status ? 'PBS not answering' : 'App not answering');
  // Once PBS is shown, a short hiccup doesn't take it away; only the status says so
  if (frame.hidden) {
    showMessage('PBS is not answering yet',
      "<p>The backup server is starting, or has stopped. Its log is on the app's <b>Log</b> tab.</p>"
      + '<p>This page tries again by itself.</p>');
  }
  retryTimer = setTimeout(check, RETRY_MS);
}

document.getElementById('msg-retry').addEventListener('click', check);
document.getElementById('reload').addEventListener('click', () => {
  if (frame.getAttribute('src')) frame.src = 'pbs/';
  check();
});
check();
// Keep the status current
setInterval(() => { if (!frame.hidden) check(); }, 30000);
