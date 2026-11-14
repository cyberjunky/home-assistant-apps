// The sidebar page: PBS's own interface in a frame, loaded by the browser straight from port 8007 of
// the address Home Assistant is opened on (PBS can't run behind Home Assistant's ingress). Before it
// loads the frame, it checks that the browser can reach PBS and trusts its certificate, and says what
// to do when it can't.
//
// PBS keeps its login in a SameSite=Lax cookie. Browsers only keep that inside a frame when the page
// around it is the same site, which includes the scheme: with Home Assistant on https:// the login
// works in the frame, on http:// it doesn't (tested in Chrome). So on http:// the page offers PBS in
// its own tab instead of a login screen that can't log in.

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
const PROBE_TIMEOUT_MS = 6000;
// PBS is on the same machine as Home Assistant, on its own port (?pbs= only for development)
const PBS_URL = new URLSearchParams(window.location.search).get('pbs')
  || `https://${window.location.hostname}:8007`;
const frame = document.getElementById('pbs');
// Home Assistant opened over https:// (ingress serves this page from Home Assistant's own address)
const FRAME_LOGIN_WORKS = window.location.protocol === 'https:';
let retryTimer = null;

document.getElementById('open-tab').href = PBS_URL;
document.getElementById('msg-open').href = PBS_URL;

function setStatus(kind, text) {
  const el = document.getElementById('status');
  el.className = `status ${kind}`;
  document.getElementById('status-text').textContent = text;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function showMessage(title, html) {
  frame.hidden = true;
  document.getElementById('msg-title').textContent = title;
  document.getElementById('msg-body').innerHTML = html;
  document.getElementById('message').hidden = false;
}

function showFrame() {
  document.getElementById('message').hidden = true;
  if (!frame.src) frame.src = `${PBS_URL}/`;
  frame.hidden = false;
}

async function serverStatus() {
  try {
    const response = await fetch('api/status', {cache: 'no-store'});
    return response.ok ? await response.json() : null;
  } catch (e) {
    return null;
  }
}

// Whether this browser reaches PBS and accepts its certificate. The answer itself can't be read
// (another origin), but a refused certificate or an unreachable port makes the request fail.
async function browserReachesPBS() {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), PROBE_TIMEOUT_MS);
  try {
    await fetch(`${PBS_URL}/api2/json/version`, {mode: 'no-cors', cache: 'no-store', signal: abort.signal});
    return true;
  } catch (e) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function retryLater() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(check, RETRY_MS);
}

async function check() {
  clearTimeout(retryTimer);
  const status = await serverStatus();
  if (status && !status.pbs) {
    setStatus('bad', 'PBS not answering');
    showMessage('PBS is not answering yet',
      '<p>The backup server is starting, or has stopped. Its log is on the app\'s <b>Log</b> tab.</p>'
      + '<p>This page tries again by itself.</p>');
    retryLater();
    return;
  }
  if (!FRAME_LOGIN_WORKS) {
    setStatus(status ? 'ok' : 'bad', status ? 'PBS running' : 'App not answering');
    showMessage('Proxmox Backup Server opens in its own tab',
      `<p>Home Assistant is open over <code>http://</code>, and browsers don't keep PBS's login inside a page
       from an <code>http://</code> address. Open PBS in its own tab; the first time, accept its certificate.</p>
       <p>With Home Assistant on <code>https://</code>, PBS appears right here.</p>`);
    document.getElementById('msg-retry').hidden = true;
    return;
  }
  if (await browserReachesPBS()) {
    setStatus('ok', status ? 'PBS running' : 'PBS');
    showFrame();
    return;
  }
  setStatus(status ? 'ok' : 'bad', status ? 'PBS running' : 'App not answering');
  const url = escapeHtml(PBS_URL);
  const remote = /\.ui\.nabu\.casa$/i.test(window.location.hostname);
  showMessage(remote ? 'PBS is only reachable at home' : 'Accept PBS\'s certificate once',
    remote
      ? `<p>Home Assistant is open through remote access, and PBS's interface is on its own port, which is only
         reachable on your own network. Open Home Assistant on its local address, or use <code>${url}</code> over a VPN.</p>`
      : `<p>Your browser doesn't trust PBS's certificate yet: PBS makes its own, as on any PBS.</p>
         <ol><li>Click <b>Open Proxmox Backup Server</b>: <code>${url}</code> opens in a new tab</li>
         <li>Accept the warning (Advanced → Proceed / Accept the risk)</li>
         <li>Close that tab and come back here: PBS appears by itself</li></ol>
         <p>Does it still not appear? Then this browser can't reach port 8007 of this machine.</p>`);
  retryLater();
}

document.getElementById('msg-retry').addEventListener('click', check);
document.getElementById('reload').addEventListener('click', () => {
  if (frame.src && !frame.hidden) frame.src = `${PBS_URL}/`;
  else check();
});
// Back from the tab where the certificate was accepted
window.addEventListener('focus', () => { if (frame.hidden) check(); });
check();
