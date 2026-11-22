// Flipper workbench. Plain script, one scope. Live things (the screen, the console, a build's output and the
// state of the link) arrive over one WebSocket; everything else is asked of the app's api/.
'use strict';
// Names of files and what the Flipper prints are not ours: they only ever reach the page through esc() or textContent
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const $ = id => document.getElementById(id);
const fmtSize = n => n == null ? '–' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1073741824).toFixed(1)} GB`;
const query = o => new URLSearchParams(o).toString();

let state = {flipper: {state: 'absent', info: {}, power: {}, storage: {}}, job: {}, sdk: {}};
let meta = {templates: {}, channels: ['release']};
let activeTab = 'device';

async function api(path, options) {
  const opts = options || {};
  if (opts.json !== undefined) { opts.body = JSON.stringify(opts.json); opts.headers = {'Content-Type': 'application/json'}; }
  const r = await fetch('api/' + path, opts);
  let data = null; try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
function toast(message, error) {
  const el = document.createElement('div'); el.className = 'toast' + (error ? ' err' : ''); el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), error ? 6500 : 3500);
}
// Run something the person asked for; what goes wrong is told, never swallowed
async function attempt(job, done) {
  try { const out = await job(); if (done) toast(done); return out; } catch (e) { toast(e.message, true); return undefined; }
}

// A small dialog: a question, some fields, OK and Cancel. Resolves to the fields, or null.
function ask({title, text, fields = [], ok = 'OK', danger = false}) {
  return new Promise(resolve => {
    const back = document.createElement('div'); back.className = 'modal-back';
    back.innerHTML = `<form class="modal" role="dialog" aria-modal="true"><h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ''}` +
      fields.map(f => `<label for="ask-${esc(f.name)}">${esc(f.label)}</label>` + (f.options
        ? `<select id="ask-${esc(f.name)}">${Object.entries(f.options).map(([v, l]) => `<option value="${esc(v)}"${v === f.value ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`
        : `<input type="text" id="ask-${esc(f.name)}" value="${esc(f.value || '')}" placeholder="${esc(f.placeholder || '')}" autocomplete="off" spellcheck="false" autocapitalize="off">`)).join('') +
      `<div class="foot"><button type="button" class="btn" data-cancel>Cancel</button><button type="submit" class="btn ${danger ? 'danger' : 'primary'}">${esc(ok)}</button></div></form>`;
    const close = value => { back.remove(); document.removeEventListener('keydown', onKey, true); resolve(value); };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close(null); } };
    document.addEventListener('keydown', onKey, true);
    back.addEventListener('mousedown', e => { if (e.target === back) close(null); });
    back.querySelector('[data-cancel]').addEventListener('click', () => close(null));
    back.querySelector('form').addEventListener('submit', e => {
      e.preventDefault();
      close(Object.fromEntries(fields.map(f => [f.name, back.querySelector('#ask-' + CSS.escape(f.name)).value.trim()])));
    });
    $('modal-root').appendChild(back);
    (back.querySelector('input, select') || back.querySelector('[type=submit]')).focus();
  });
}

// ---- theme: the same toggle as the other apps (auto follows Home Assistant, then light, then dark) ----
const THEME_ICONS = {auto: '🌓', light: '☀️', dark: '🌙'};
// ?theme= and ?tab= in the address open the app in a given state (a link from elsewhere, a screenshot)
const PARAMS = new URLSearchParams(window.location.search);
let themeChoice = PARAMS.get('theme');
try { themeChoice = themeChoice || localStorage.getItem('flipper_theme'); } catch (e) { /* only a convenience */ }
if (!THEME_ICONS[themeChoice]) themeChoice = 'auto';
function haDarkMode() {
  try {
    const ha = window.parent !== window && window.parent.document.querySelector('home-assistant');
    return ha && ha.hass && ha.hass.themes ? Boolean(ha.hass.themes.darkMode) : null;
  } catch (e) { return null; }
}
function applyTheme() {
  const root = document.documentElement;
  const ha = themeChoice === 'auto' ? haDarkMode() : null;
  if (themeChoice === 'auto' && ha === null) delete root.dataset.theme;  // the stylesheet follows the system
  else root.dataset.theme = (themeChoice === 'dark' || ha === true) ? 'dark' : 'light';
  $('theme-icon').textContent = THEME_ICONS[themeChoice];
}
$('theme-toggle').addEventListener('click', () => {
  themeChoice = {auto: 'light', light: 'dark', dark: 'auto'}[themeChoice];
  try { localStorage.setItem('flipper_theme', themeChoice); } catch (e) { /* only a convenience */ }
  applyTheme();
});
applyTheme(); setInterval(applyTheme, 2000);

// ---- the live connection to the app ----
let socket = null;
function connect() {
  const url = new URL('api/ws', window.location.href); url.protocol = url.protocol.replace('http', 'ws'); url.search = '';
  socket = new WebSocket(url); socket.binaryType = 'arraybuffer';
  socket.onmessage = e => {
    if (typeof e.data !== 'string') { showFrame(new Uint8Array(e.data)); return; }
    const m = JSON.parse(e.data);
    if (m.t === 'state') { state = m; renderState(); }
    else if (m.t === 'build') addOutput(m.line);
    else if (m.t === 'console') addConsole(m.text);
    else if (m.t === 'notice') toast(m.error, true);
  };
  socket.onclose = () => { socket = null; state.flipper = {...state.flipper, state: 'offline'}; renderState(); setTimeout(connect, 1500); };
}
function send(message) { if (socket && socket.readyState === 1) socket.send(JSON.stringify(message)); }

// ---- the screen ----
// A frame is 1024 bytes: 128 x 64 pixels, a byte is eight pixels below each other with the top one in bit 0.
const LCD = ['lcd-main', 'lcd-side'].map(id => $(id));
const INK = [0x1f, 0x0e, 0x00], PAPER = [0xff, 0x8c, 0x29];
let lastFrame = null, extraTurns = 0;
function showFrame(bytes) {
  if (bytes) lastFrame = bytes;
  if (!lastFrame || lastFrame.length < 1025) return;
  // The picture is the panel as it is built in; an app that draws upright for a turned Flipper is turned back here
  const turns = ([0, 2, 1, 3][lastFrame[0]] + extraTurns) % 4;
  const w = turns % 2 ? 64 : 128, h = turns % 2 ? 128 : 64;
  const image = new ImageData(w, h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const sx = turns === 0 ? x : turns === 1 ? y : turns === 2 ? 127 - x : 127 - y;
    const sy = turns === 0 ? y : turns === 1 ? 63 - x : turns === 2 ? 63 - y : x;
    const c = (lastFrame[1 + (sy >> 3) * 128 + sx] >> (sy & 7)) & 1 ? INK : PAPER;
    const o = (y * w + x) * 4;
    image.data[o] = c[0]; image.data[o + 1] = c[1]; image.data[o + 2] = c[2]; image.data[o + 3] = 255;
  }
  LCD.forEach(box => {
    const canvas = box.querySelector('canvas');
    if (canvas.width !== w) { canvas.width = w; canvas.height = h; }
    canvas.getContext('2d').putImageData(image, 0, 0);
    box.classList.toggle('tall', w < h);
  });
}
$('rotate-btn').addEventListener('click', () => { extraTurns = (extraTurns + 1) % 4; showFrame(); });
$('shot-btn').addEventListener('click', () => {
  if (!lastFrame) { toast('There is no picture yet', true); return; }
  // Four times the Flipper's pixels, so it is not a stamp
  const source = LCD[0].querySelector('canvas'), big = document.createElement('canvas');
  big.width = source.width * 4; big.height = source.height * 4;
  const context = big.getContext('2d'); context.imageSmoothingEnabled = false; context.drawImage(source, 0, 0, big.width, big.height);
  const link = document.createElement('a');
  link.download = `flipper-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.png`; link.href = big.toDataURL('image/png'); link.click();
});

// ---- the buttons ----
// As on the Flipper: press, then "short" when let go soon, or "long" and repeats while held; release last.
const LONG_MS = 350, REPEAT_MS = 160;
const held = {};
function keyDown(key) {
  if (held[key] || state.flipper.state !== 'rpc') return;
  const h = held[key] = {long: false};
  send({t: 'key', key, kind: 'press'});
  h.timer = setTimeout(() => { h.long = true; send({t: 'key', key, kind: 'long'}); h.repeat = setInterval(() => send({t: 'key', key, kind: 'repeat'}), REPEAT_MS); }, LONG_MS);
  document.querySelectorAll(`.k-${key}`).forEach(b => b.classList.add('down'));
}
function keyUp(key) {
  const h = held[key]; if (!h) return;
  delete held[key]; clearTimeout(h.timer); clearInterval(h.repeat);
  if (!h.long) send({t: 'key', key, kind: 'short'});
  send({t: 'key', key, kind: 'release'});
  document.querySelectorAll(`.k-${key}`).forEach(b => b.classList.remove('down'));
}
// The big pad draws its arrows as outlines, like the print on the real one
const REAL_GLYPH = {
  up: '<svg viewBox="0 0 20 20"><path d="M10 4 17 15H3Z"/></svg>', down: '<svg viewBox="0 0 20 20"><path d="M10 16 17 5H3Z"/></svg>',
  left: '<svg viewBox="0 0 20 20"><path d="M4 10 15 3V17Z"/></svg>', right: '<svg viewBox="0 0 20 20"><path d="M16 10 5 3V17Z"/></svg>',
  ok: '', back: '<svg viewBox="0 0 20 20"><path d="M6 6h7a4 4 0 0 1 0 8H7"/><path d="M8.5 3.5 6 6l2.5 2.5"/></svg>',
};
const PAD = [['up', '▲', 'Up'], ['left', '◀', 'Left'], ['ok', 'OK', 'OK'], ['right', '▶', 'Right'], ['down', '▼', 'Down'], ['back', '↩', 'Back']];
['pad-main', 'pad-side'].forEach(id => {
  const real = $(id).classList.contains('real');
  $(id).innerHTML = PAD.map(([key, glyph, name]) => `<button type="button" class="k-${key}" data-key="${key}" aria-label="${name}" title="${name}">${real ? REAL_GLYPH[key] : glyph}</button>`).join('');
  $(id).querySelectorAll('button').forEach(button => {
    const key = button.dataset.key;
    button.addEventListener('pointerdown', e => { e.preventDefault(); button.setPointerCapture(e.pointerId); keyDown(key); });
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => button.addEventListener(type, () => keyUp(key)));
    button.addEventListener('contextmenu', e => e.preventDefault());
  });
});
const KEYBOARD = {ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right', Enter: 'ok', Backspace: 'back', Escape: 'back'};
const typing = e => /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable || document.querySelector('.modal-back');
document.addEventListener('keydown', e => {
  if (activeTab !== 'device' || !KEYBOARD[e.key] || typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
  e.preventDefault(); if (!e.repeat) keyDown(KEYBOARD[e.key]);
});
document.addEventListener('keyup', e => { if (KEYBOARD[e.key]) keyUp(KEYBOARD[e.key]); });
window.addEventListener('blur', () => Object.keys(held).forEach(keyUp));

// ---- what the link and the Flipper say ----
const LINK_TEXT = {rpc: 'Connected', console: 'Console open', connecting: 'Connecting…', absent: 'No Flipper', released: 'Released', offline: 'App not reachable'};
function sdkMismatch() {
  const fw = (state.flipper.info || {}).firmware_version, sdk = state.sdk || {};
  return Boolean(fw && sdk.installed && sdk.version && !sdk.version.includes(fw) && !fw.includes(sdk.version));
}
function renderState() {
  const f = state.flipper, info = f.info || {}, power = f.power || {}, storage = f.storage || {}, sdk = state.sdk || {}, job = state.job || {};
  const on = f.state === 'rpc';
  $('link-state').className = 'link-state' + (on ? ' on' : ['connecting', 'console'].includes(f.state) ? ' wait' : '');
  $('link-text').textContent = LINK_TEXT[f.state] || f.state;
  $('led').classList.toggle('on', on);
  LCD.forEach(box => {
    box.classList.toggle('off', !on || !lastFrame);
    box.querySelector('.lcd-off').textContent = f.state === 'console' ? 'paused: the console is open' : on ? 'no picture' : (LINK_TEXT[f.state] || '');
  });

  const banners = [];
  if (f.state === 'absent') banners.push(['bad', f.error ? `The Flipper does not answer: ${f.error}.` : 'No Flipper found. Plug it into the machine Home Assistant runs on with a USB cable that carries data, and unlock it.']);
  if (f.state === 'released') banners.push(['', 'The Flipper is released: its USB port is free for other uses.', 'connect', 'Connect']);
  if (f.state === 'console' && activeTab !== 'console') banners.push(['', 'The console is open: the screen and files wait.', 'console-close', 'Close console']);
  if (f.state === 'offline') banners.push(['bad', 'The app does not answer. Trying again…']);
  $('banners').innerHTML = banners.map(([c, t, action, label]) =>
    `<div class="banner ${c}"><span>${esc(t)}</span>${action ? `<span class="spacer"></span><button type="button" class="btn" data-device="${action}">${label}</button>` : ''}</div>`).join('');

  const tile = (l, v, d) => `<div class="tile"><div class="l">${l}</div><div class="v">${esc(v)}</div><div class="d">${esc(d)}</div></div>`;
  const charge = power.charge_level;
  $('device-tiles').innerHTML =
    tile('Name', info.hardware_name || '–', info.hardware_model || (on ? '' : LINK_TEXT[f.state] || '')) +
    tile('Firmware', info.firmware_version || '–', [info.firmware_branch, info.firmware_api_major && `API ${info.firmware_api_major}.${info.firmware_api_minor}`].filter(Boolean).join(' · ')) +
    tile('Battery', charge ? `${charge}%` : '–', power.charge_state || '') +
    tile('SD card', storage.total ? fmtSize(storage.free) : '–', storage.total ? `free of ${fmtSize(storage.total)}` : on ? 'no card' : '');
  const pairs = [...Object.entries(info), ...Object.entries(power).map(([k, v]) => ['power_' + k, v])];
  $('device-info').innerHTML = pairs.length
    ? `<div class="kv">${pairs.map(([k, v]) => `<div><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('')}</div>`
    : '<div class="empty-state">What the Flipper says about itself shows here once it is connected.</div>';
  $('release-btn').textContent = f.state === 'released' ? 'Connect' : 'Release';
  $('release-btn').dataset.device = f.state === 'released' ? 'connect' : 'release';
  $('console-toggle').textContent = f.state === 'console' ? 'Close console' : 'Open console';

  // SDK
  const channels = meta.channels.map(c => `<option${c === (sdk.channel || 'release') ? ' selected' : ''}>${esc(c)}</option>`).join('');
  $('sdk-hint').textContent = sdk.installed ? sdk.version : 'not installed';
  $('sdk-box').innerHTML = !sdk.available
    ? '<p class="muted">ufbt, the build tool, is not installed here. JavaScript projects still run.</p>'
    : (sdk.installed
      ? `<p class="muted">Version <b class="mono">${esc(sdk.version)}</b>${sdk.channel ? ` from the ${esc(sdk.channel)} channel` : ''}.${sdk.compiler ? '' : ' The compiler (350 MB) is downloaded at the first build.'}</p>` +
        (sdkMismatch() ? `<p class="pill warn" style="white-space:normal;margin-top:8px">The Flipper runs ${esc(info.firmware_version)}: an app built with another version may not load.</p>` : '')
      : `<p class="muted">Needed to build C apps. It is installed on the Home Assistant machine, in this app's own data, not on the Flipper. With its compiler it downloads about 400 MB and takes 1.5 GB; ${fmtSize(sdk.disk_free)} is free there.</p>`) +
      `<div class="sdk-row"><select id="sdk-channel" aria-label="Channel">${channels}</select>` +
      `<button type="button" class="btn" id="sdk-install"${job.running ? ' disabled' : ''}>${sdk.installed ? 'Update' : 'Install'}</button>` +
      (sdk.installed ? '<button type="button" class="link-btn danger" id="sdk-remove">Remove</button>' : '') + '</div>';

  // The job that runs, and the buttons that start one
  $('job-hint').textContent = job.running ? `${job.what}…` : job.what ? `${job.what}: ${job.ok ? 'done' : 'failed'}` : '';
  $('cancel-btn').hidden = !job.running;
  ['build-btn', 'launch-btn', 'run-btn'].forEach(id => { $(id).disabled = Boolean(job.running || f.busy); });
  if (jobWasRunning && !job.running && project) openProject(project.name, true);
  jobWasRunning = Boolean(job.running);

  const bar = [`<span>${esc(LINK_TEXT[f.state] || f.state)}</span>`];
  if (info.hardware_name) bar.push(`<span>flipper <b>${esc(info.hardware_name)}</b></span>`, `<span>firmware <b>${esc(info.firmware_version || '?')}</b></span>`);
  if (charge) bar.push(`<span>battery <b>${esc(charge)}%</b></span>`);
  if (f.port) bar.push(`<span class="hide-sm">${esc(f.port)}</span>`);
  bar.push(`<span>sdk <b>${esc(sdk.installed ? sdk.version : 'none')}</b></span>`);
  if (f.busy || job.running) bar.push(`<span><b>${esc(f.busy || job.what)}…</b></span>`);
  $('statusbar').innerHTML = bar.join('');
}
let jobWasRunning = false;

document.addEventListener('click', async e => {
  const button = e.target.closest('[data-device]');
  if (button) {
    if (button.dataset.confirm && !await ask({title: button.dataset.confirm, ok: button.textContent.trim()})) return;
    await attempt(() => api('device/' + button.dataset.device, {method: 'POST'}), {clock: 'Clock set', alert: 'Listen…'}[button.dataset.device]);
    return;
  }
  if (e.target.id === 'sdk-install') {
    const channel = $('sdk-channel').value;
    if (!state.sdk.installed && !await ask({title: 'Install the Flipper SDK?', text: `The ${channel} SDK and its compiler are downloaded from Flipper's update server: about 400 MB, 1.5 GB on disk. This takes a few minutes.`, ok: 'Install'})) return;
    clearOutput(); await attempt(() => api('sdk', {method: 'POST', json: {channel}}));
  } else if (e.target.id === 'sdk-remove') {
    if (await ask({title: 'Remove the SDK?', text: 'Frees about 1.5 GB. Projects are kept; building needs the SDK again.', ok: 'Remove', danger: true})) await attempt(() => api('sdk', {method: 'DELETE'}), 'SDK removed');
  }
});
$('close-app-btn').addEventListener('click', () => attempt(async () => {
  const r = await api('device/close-app', {method: 'POST'}); toast(r.closed ? 'App closed' : 'No app was running');
}));

// ---- Code: projects and the editor ----
let projects = [], project = null, filePath = null, dirty = false, loading = false;
const MODES = {c: 'text/x-csrc', h: 'text/x-csrc', cpp: 'text/x-c++src', hpp: 'text/x-c++src', js: 'javascript', json: {name: 'javascript', json: true}, fam: 'python', py: 'python'};
const editor = CodeMirror($('editor'), {
  lineNumbers: true, indentUnit: 4, tabSize: 4, indentWithTabs: false, matchBrackets: true, autoCloseBrackets: true, styleActiveLine: true,
  extraKeys: {'Ctrl-S': () => save(), 'Cmd-S': () => save(), 'Ctrl-/': 'toggleComment', 'Cmd-/': 'toggleComment', 'Ctrl-Enter': () => go(), Tab: cm => cm.somethingSelected() ? cm.indentSelection('add') : cm.replaceSelection('    ', 'end')},
});
editor.on('change', () => { if (!loading) setDirty(true); });
function setDirty(value) { dirty = value; $('file-dirty').hidden = !value; $('save-btn').disabled = !value; }
window.addEventListener('beforeunload', e => { if (dirty) e.preventDefault(); });

async function loadProjects(select) {
  projects = (await api('projects')).projects;
  let wanted = select || (project && project.name);
  try { wanted = wanted || localStorage.getItem('flipper_project'); } catch (e) { /* only a convenience */ }
  if (!projects.some(p => p.name === wanted)) wanted = projects.length ? projects[0].name : null;
  $('project-select').innerHTML = projects.length
    ? projects.map(p => `<option value="${esc(p.name)}"${p.name === wanted ? ' selected' : ''}>${esc(p.name)} (${p.kind === 'fap' ? 'C app' : 'JavaScript'})</option>`).join('')
    : '<option value="">no projects yet</option>';
  if (wanted) await openProject(wanted); else { project = null; renderProject(); }
}
async function openProject(name, keepFile) {
  if (!keepFile) await save();
  project = await api('projects/' + encodeURIComponent(name));
  try { localStorage.setItem('flipper_project', name); } catch (e) { /* only a convenience */ }
  renderProject();
  if (keepFile && filePath && project.files.some(f => f.path === filePath)) return;
  filePath = null;
  const first = project.files.find(f => f.path === `${project.appid}.c`) || project.files.find(f => /\.(c|js)$/.test(f.path)) || project.files[0];
  if (first) await openFile(first.path); else showEditor(false);
}
function renderProject() {
  const has = Boolean(project);
  $('project-actions').hidden = !has;
  $('file-list').innerHTML = has ? project.files.map(f =>
    `<button type="button" data-file="${esc(f.path)}" class="${f.path === filePath ? 'active' : ''}" title="${esc(f.path)}"><span>${esc(f.path)}</span><small>${fmtSize(f.size)}</small></button>`).join('') : '';
  const fap = has && project.kind === 'fap';
  $('build-btn').hidden = $('launch-btn').hidden = !fap;
  $('run-btn').hidden = !has || fap;
  $('fap-btn').hidden = !(fap && project.built);
  if (has) {
    $('project-export').href = `api/projects/${encodeURIComponent(project.name)}/export`;
    $('fap-btn').href = `api/projects/${encodeURIComponent(project.name)}/fap`;
    if (project.built) $('fap-btn').title = `${project.appid}.fap, ${fmtSize(project.built.size)}, built ${new Date(project.built.at * 1000).toLocaleString()}`;
  }
  if (!has) { filePath = null; showEditor(false); }
}
function showEditor(on) {
  $('editor').hidden = !on; $('editor-empty').hidden = on;
  $('file-name').textContent = on ? `${project.name}/${filePath}` : 'no file open';
  if (!on) setDirty(false);
  if (on) editor.refresh();
}
async function openFile(path, line, column) {
  if (path !== filePath) {
    await save();
    let text;
    try { text = (await api(`projects/${encodeURIComponent(project.name)}/file?${query({path})}`)).text; } catch (e) { toast(e.message, true); return; }
    filePath = path; loading = true;
    editor.setOption('mode', MODES[path.split('.').pop().toLowerCase()] || null);
    editor.setValue(text); editor.clearHistory(); loading = false;
    setDirty(false); showEditor(true); renderProject();
  }
  if (line) {
    editor.operation(() => {
      if (flagged != null) editor.removeLineClass(flagged, 'background', 'line-flag');
      flagged = editor.addLineClass(line - 1, 'background', 'line-flag');
    });
    editor.setCursor({line: line - 1, ch: Math.max(0, (column || 1) - 1)}); editor.scrollIntoView(null, 120); editor.focus();
  }
}
let flagged = null;
async function save() {
  if (!dirty || !project || !filePath) return true;
  try { await api(`projects/${encodeURIComponent(project.name)}/file?${query({path: filePath})}`, {method: 'PUT', json: {text: editor.getValue()}}); }
  catch (e) { toast(`Not saved: ${e.message}`, true); return false; }
  setDirty(false); return true;
}
// Build and run what is open: the usual thing to do after an edit (Ctrl+Enter)
async function go(action) {
  if (!project || state.job.running) return;
  if (!await save()) return;
  action = action || (project.kind === 'fap' ? 'launch' : 'run');
  if (action === 'run' && !/\.js$/.test(filePath || '')) { toast('Open the .js file to run', true); return; }
  if (action !== 'build' && state.flipper.state !== 'rpc') { toast(state.flipper.state === 'console' ? 'Close the console first' : 'The Flipper is not connected', true); return; }
  clearOutput();
  await attempt(() => api(`projects/${encodeURIComponent(project.name)}/${action}`, {method: 'POST', json: {path: filePath}}));
}
$('save-btn').addEventListener('click', save);
$('build-btn').addEventListener('click', () => go('build'));
$('launch-btn').addEventListener('click', () => go('launch'));
$('run-btn').addEventListener('click', () => go('run'));
$('cancel-btn').addEventListener('click', () => attempt(() => api('build/cancel', {method: 'POST'})));
$('project-select').addEventListener('change', e => { if (e.target.value) attempt(() => openProject(e.target.value)); });
$('file-list').addEventListener('click', e => { const b = e.target.closest('[data-file]'); if (b) openFile(b.dataset.file); });
$('file-list').addEventListener('contextmenu', async e => {
  const b = e.target.closest('[data-file]'); if (!b) return;
  e.preventDefault(); await fileMenu(b.dataset.file);
});
async function fileMenu(path) {
  const answer = await ask({title: path, text: 'Give another name to rename the file, or delete it.', fields: [{name: 'name', label: 'Name', value: path}, {name: 'what', label: 'Do', options: {rename: 'Rename', delete: 'Delete this file'}, value: 'rename'}]});
  if (!answer) return;
  const base = `projects/${encodeURIComponent(project.name)}`;
  if (answer.what === 'delete') {
    if (!await attempt(() => api(`${base}/file?${query({path})}`, {method: 'DELETE'}))) return;
    if (path === filePath) { filePath = null; setDirty(false); }
  } else if (answer.name && answer.name !== path) {
    if (path === filePath && !await save()) return;
    if (!await attempt(() => api(`${base}/rename`, {method: 'POST', json: {path, new_path: answer.name}}))) return;
    if (path === filePath) filePath = null;
  }
  await attempt(() => openProject(project.name, true));
  if (!filePath && project.files.length) await openFile(project.files[0].path);
}
$('file-new').addEventListener('click', async () => {
  const answer = await ask({title: 'New file', fields: [{name: 'name', label: 'Name', placeholder: project.kind === 'fap' ? 'helpers.c' : 'other.js'}], ok: 'Create'});
  if (!answer || !answer.name) return;
  if (project.files.some(f => f.path === answer.name)) { toast('That name is taken', true); return; }
  if (await attempt(() => api(`projects/${encodeURIComponent(project.name)}/file?${query({path: answer.name})}`, {method: 'PUT', json: {text: ''}}))) {
    await openProject(project.name, true); await openFile(answer.name);
  }
});
$('project-new').addEventListener('click', async () => {
  const answer = await ask({title: 'New project', fields: [
    {name: 'name', label: 'Name (the app id: lower-case letters, digits and _)', placeholder: 'my_app'},
    {name: 'title', label: 'Title, as shown on the Flipper', placeholder: 'My App'},
    {name: 'template', label: 'Start from', options: meta.templates, value: 'screen'}], ok: 'Create'});
  if (!answer) return;
  const made = await attempt(() => api('projects', {method: 'POST', json: answer}));
  if (made) await loadProjects(made.name);
});
$('project-import').addEventListener('click', () => {
  const input = document.createElement('input'); input.type = 'file'; input.accept = '.zip';
  input.addEventListener('change', async () => {
    const file = input.files[0]; if (!file) return;
    const guess = file.name.replace(/\.zip$/i, '').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^[^a-z]+/, '').slice(0, 31);
    const answer = await ask({title: 'Import a project', text: `From ${file.name}: a folder with an application.fam, or JavaScript files.`, fields: [{name: 'name', label: 'Project name', value: guess}], ok: 'Import'});
    if (!answer) return;
    const made = await attempt(() => api(`projects/import?${query({name: answer.name})}`, {method: 'POST', body: file}));
    if (made) await loadProjects(made.name);
  });
  input.click();
});
$('project-delete').addEventListener('click', async () => {
  if (!await ask({title: `Delete ${project.name}?`, text: 'The project and its files are removed from this machine. What is on the Flipper stays.', ok: 'Delete', danger: true})) return;
  setDirty(false);
  if (await attempt(() => api('projects/' + encodeURIComponent(project.name), {method: 'DELETE'}))) { project = null; filePath = null; await loadProjects(); }
});

// ---- a build's output ----
function clearOutput() { $('build-output').textContent = ''; }
function addOutput(line) {
  const out = $('build-output'), row = document.createElement('div');
  const stick = out.scrollHeight - out.scrollTop - out.clientHeight < 30;
  row.className = line.startsWith('$ ') ? 'cmd' : /\berror\b|^Failed|^Flipper:/i.test(line) ? 'err' : /\bwarning\b/i.test(line) ? 'warn' : /^(Done\.|Running on the Flipper)/.test(line) ? 'ok' : '';
  // file.c:12:5: a place in the project, one click away
  const place = /([\w./-]+\.(?:c|h|cpp|hpp|fam|js)):(\d+)(?::(\d+))?/.exec(line);
  const file = place && project && project.files.find(f => place[1] === f.path || place[1].endsWith('/' + f.path));
  if (file) {
    const link = document.createElement('a'); link.textContent = place[0];
    link.addEventListener('click', () => openFile(file.path, Number(place[2]), Number(place[3] || 1)));
    row.append(line.slice(0, place.index), link, line.slice(place.index + place[0].length));
  } else row.textContent = line || ' ';
  out.appendChild(row);
  while (out.childElementCount > 3000) out.firstChild.remove();
  if (stick) out.scrollTop = out.scrollHeight;
}

// ---- Files: what is on the Flipper ----
let dir = '/ext', filesLoaded = false;
async function loadFiles(path) {
  if (path) dir = path;
  if (state.flipper.state !== 'rpc') { $('files-table').innerHTML = `<div class="empty-state">${state.flipper.state === 'console' ? 'The console is open: close it to see the files.' : 'The Flipper is not connected.'}</div>`; renderCrumbs(); return; }
  let listing;
  try { listing = await api('files?' + query({path: dir})); }
  catch (e) { $('files-table').innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; renderCrumbs(); return; }
  filesLoaded = true; renderCrumbs();
  const rows = listing.entries.map(f => {
    const full = `${dir}/${f.name}`, runs = !f.dir && /\.(fap|js)$/.test(f.name);
    return `<tr class="${f.dir ? 'click' : ''}" data-path="${esc(full)}" data-dir="${f.dir ? 1 : ''}"><td class="nm">${f.dir ? '📁' : '📄'} ${esc(f.name)}</td><td class="num">${f.dir ? '' : fmtSize(f.size)}</td>` +
      `<td class="acts">${runs ? '<button class="link-btn" data-act="run">Run</button>' : ''}${f.dir ? '' : '<button class="link-btn" data-act="download">Download</button>'}` +
      `<button class="link-btn" data-act="rename">Rename</button><button class="link-btn danger" data-act="delete">Delete</button></td></tr>`;
  }).join('');
  $('files-table').innerHTML = listing.entries.length
    ? `<table class="list"><thead><tr><th>Name</th><th class="num">Size</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="empty-state">This folder is empty.</div>';
}
function renderCrumbs() {
  const parts = dir.split('/').filter(Boolean);
  $('crumbs').innerHTML = parts.map((p, n) => `${n ? '<span>/</span>' : ''}<button type="button" data-go="/${esc(parts.slice(0, n + 1).join('/'))}">${esc(n ? p : (p === 'ext' ? 'SD card' : 'Internal'))}</button>`).join('');
  document.querySelectorAll('#storage-seg button').forEach(b => b.classList.toggle('active', dir === b.dataset.root || dir.startsWith(b.dataset.root + '/')));
}
$('storage-seg').addEventListener('click', e => { const b = e.target.closest('[data-root]'); if (b) loadFiles(b.dataset.root); });
$('crumbs').addEventListener('click', e => { const b = e.target.closest('[data-go]'); if (b) loadFiles(b.dataset.go); });
$('files-refresh').addEventListener('click', () => loadFiles());
$('files-table').addEventListener('click', async e => {
  const row = e.target.closest('tr[data-path]'); if (!row) return;
  const path = row.dataset.path, name = path.split('/').pop(), act = e.target.closest('[data-act]');
  if (!act) { if (row.dataset.dir) loadFiles(path); return; }
  const post = (action, json, done) => attempt(() => api('files/' + action, {method: 'POST', json}), done);
  if (act.dataset.act === 'download') { const a = document.createElement('a'); a.href = 'api/files/download?' + query({path}); a.download = name; a.click(); }
  else if (act.dataset.act === 'run') await post('run', {path}, `Started ${name}`);
  else if (act.dataset.act === 'rename') {
    const answer = await ask({title: `Rename ${name}`, fields: [{name: 'name', label: 'New name', value: name}], ok: 'Rename'});
    if (answer && answer.name && answer.name !== name && await post('rename', {path, new_path: `${dir}/${answer.name}`})) loadFiles();
  } else if (act.dataset.act === 'delete') {
    if (await ask({title: `Delete ${name}?`, text: row.dataset.dir ? 'The folder and everything in it is removed from the Flipper.' : 'The file is removed from the Flipper.', ok: 'Delete', danger: true}) && await post('delete', {path})) loadFiles();
  }
});
$('files-mkdir').addEventListener('click', async () => {
  const answer = await ask({title: 'New folder', fields: [{name: 'name', label: 'Name'}], ok: 'Create'});
  if (answer && answer.name && await attempt(() => api('files/mkdir', {method: 'POST', json: {path: `${dir}/${answer.name}`}}))) loadFiles();
});
$('files-upload').addEventListener('click', () => $('files-input').click());
$('files-input').addEventListener('change', async e => {
  const files = [...e.target.files]; e.target.value = '';
  for (const file of files) {
    toast(`Uploading ${file.name} (${fmtSize(file.size)})…`);
    if (!await attempt(() => api('files/upload?' + query({path: `${dir}/${file.name}`}), {method: 'POST', body: file}))) break;
  }
  loadFiles();
});

// ---- Console ----
let consoleText = '';
function addConsole(text) {
  // What a terminal would do with it, as far as a log needs: no colours, no carriage returns, backspace rubs out
  consoleText += text.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '').replace(/\r\n?/g, '\n').replace(/\n{3,}/g, '\n\n');
  while (/[^\n]\x08/.test(consoleText)) consoleText = consoleText.replace(/[^\n]\x08/, '');
  consoleText = consoleText.replace(/[\x00-\x08\x0b-\x1f]/g, '');
  if (consoleText.length > 300000) consoleText = consoleText.slice(-200000);
  const out = $('console-output'), stick = out.scrollHeight - out.scrollTop - out.clientHeight < 30;
  out.textContent = consoleText;
  if (stick) out.scrollTop = out.scrollHeight;
}
async function consoleOpen() {
  if (state.flipper.state === 'console') return true;
  return Boolean(await attempt(() => api('device/console-open', {method: 'POST'})));
}
$('console-toggle').addEventListener('click', () => attempt(() => api('device/' + (state.flipper.state === 'console' ? 'console-close' : 'console-open'), {method: 'POST'})));
$('console-form').addEventListener('submit', async e => {
  e.preventDefault();
  const input = $('console-input'), line = input.value;
  if (await consoleOpen()) { send({t: 'console', text: line + '\r'}); input.value = ''; }
});
$('log-btn').addEventListener('click', async () => { if (await consoleOpen()) send({t: 'console', text: `log ${$('log-level').value}\r`}); });
$('ctrlc-btn').addEventListener('click', () => send({t: 'console', text: '\x03'}));
$('console-clear').addEventListener('click', () => { consoleText = ''; $('console-output').textContent = ''; });

// ---- tabs ----
function showTab(name) {
  if (!$('tab-' + name)) name = 'device';
  activeTab = name;
  document.querySelectorAll('.topbar .tab[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(el => el.classList.toggle('active', el.id === 'tab-' + name));
  if (name === 'code') editor.refresh();
  if (name === 'files') loadFiles();
  if (name === 'console') $('console-input').focus();
  renderState();
}
document.querySelectorAll('.topbar .tab[data-tab]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));

(async function start() {
  try { const first = await api('state'); meta = {templates: first.templates, channels: first.channels}; state = first; } catch (e) { toast(e.message, true); }
  renderState();
  connect();
  try { (await api('build/log')).lines.forEach(addOutput); } catch (e) { /* nothing built yet */ }
  await attempt(() => loadProjects());
  showTab(PARAMS.get('tab') || 'device');
})();
