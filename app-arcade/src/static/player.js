// Player: runs one game in MAME (compiled to WebAssembly) on this page.
// Before MAME starts, the game's ROM zips, its saved files (high scores, NVRAM, settings,
// save states) and the hiscore plugin are put in MAME's in-memory file system; while the
// game runs, changed files go back to the app every few seconds and when you leave.
// It also reports how long you play (for the Stats tab) and takes the library's thumbnail.
const GAME = document.body.dataset.game;
// The MAME files are cached for long; a new app version gets new URLs
const MAME_V = '?v=' + encodeURIComponent(document.body.dataset.version || '');
const HOME = '/arcade';
const SAVE_DIRS = ['hiscore', 'nvram', 'cfg', 'sta'];
// boot.lua starts MAME's Lua plugins; hiscore needs json
const PLUGIN_FILES = ['boot.lua', 'json/init.lua', 'json/plugin.json', 'hiscore/init.lua', 'hiscore/plugin.json', 'hiscore/hiscore.dat'];
const SYNC_MS = 10000;
// MAME writes NVRAM and settings only when asked (see mame/save-settings.patch)
const SETTINGS_EVERY = 3;   // syncs, so every 30 seconds
let syncCount = 0;
// Play time counts while the game runs in a visible tab; reported every half minute and on leaving
const BEAT_MS = 30000;
// The thumbnail: a snapshot this long after Start was pressed (gameplay, not the title screen),
// or, when nobody presses Start, after this much of the attract mode if the game has none yet
const THUMB_AFTER_START_MS = 30000;
const THUMB_FALLBACK_S = 90;

let game = null;
let roms = [];          // [{file, data}]
let saved = [];         // [{path, data}]
let plugins = [];       // [{path, data}]
let running = false;
let muted = false;
let leaving = false;
const synced = new Map();   // path -> signature of the last version the app has
const log = [];

function setStatus(text) { document.getElementById('start-status').textContent = text; }
function statusLine(text) { document.getElementById('status-line').textContent = text; }

// Browsers only allow sound after a click or key press: keep every audio context MAME
// makes, and wake them on the next press
const audioContexts = [];
const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
if (NativeAudioContext) {
    window.AudioContext = window.webkitAudioContext = class extends NativeAudioContext {
        constructor(...args) { super(...args); audioContexts.push(this); }
    };
}
function wakeAudio() { audioContexts.forEach(c => { if (c.state === 'suspended') c.resume().catch(() => {}); }); }
['pointerdown', 'keydown', 'touchstart'].forEach(ev => window.addEventListener(ev, wakeAudio, true));

async function fetchBytes(path) {
    const r = await fetch(api(path), {cache: 'no-store'});
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return new Uint8Array(await r.arrayBuffer());
}

async function prepare() {
    try {
        const list = await getJson('/api/games');
        game = list.games.find(g => g.name === GAME);
        if (!game) throw new Error(`${GAME} is not in the library`);
        document.title = `${game.description} - Arcade`;
        document.getElementById('game-name').textContent = game.description;
        document.getElementById('game-meta').textContent = `${game.year} · ${game.company}`;
        document.getElementById('start-title').textContent = game.description;
        if (!game.playable) throw new Error(`Files missing: ${game.missing.join(', ')}`);
        setStatus('Loading the game files…');
        // MAME looks for a file in the game's set, its parent's and the BIOS set (neogeo.zip)
        const files = [game.zip, game.parent_zip, game.bios_zip].filter(Boolean);
        roms = await Promise.all(files.map(async f => ({file: f, data: await fetchBytes('/roms/' + f)})));
        const saves = (await getJson('/api/saves/' + GAME)).files;
        saved = await Promise.all(saves.map(async s => ({path: s.path, data: await fetchBytes(`/api/saves/${GAME}/${s.path}`)})));
        saved.forEach(s => synced.set(s.path, signature(s.data)));
        plugins = await Promise.all(PLUGIN_FILES.map(async p => ({path: p, data: await fetchBytes('/mame/plugins/' + p + MAME_V)}))).catch(() => []);
        setStatus('Loading MAME…');
        // Warm the browser cache, so starting is quick after the click
        await fetch(api('/mame/mame.wasm' + MAME_V)).then(r => r.arrayBuffer());
        const scores = saved.some(s => s.path.startsWith('hiscore/'));
        setStatus(scores ? 'Ready. Your high scores are loaded.' : 'Ready.');
        const btn = document.getElementById('start-btn');
        btn.disabled = false;
        btn.focus();
        document.getElementById('load-btn').title = saved.some(s => s.path.startsWith('sta/'))
            ? 'Back to the saved moment (F8)' : 'No saved moment yet';
    } catch (e) {
        setStatus(e.message);
        document.getElementById('start-status').classList.add('bad');
    }
}

function signature(data) {
    // Size plus a cheap rolling sum: enough to see that a save file changed
    let h = data.length;
    for (let i = 0; i < data.length; i += 1) h = (h * 31 + data[i]) | 0;
    return h;
}

function mameFS() { return window.FS || (window.Module && window.Module.FS); }

// MAME's own JSMAME helpers (emscripten_post.js) bind the functions without arguments
// before the WebAssembly has loaded, so those are left undefined: call the exports directly
const MAME_FN = {
    save_settings: '__ZN15running_machine24emscripten_save_settingsEv',
    exit: '__ZN15running_machine15emscripten_exitEv',
    soft_reset: '__ZN15running_machine21emscripten_soft_resetEv',
    get_machine: '__ZN15running_machine30emscripten_get_running_machineEv',
    get_sound: '__ZN15running_machine20emscripten_get_soundEv',
    mute: '__ZN13sound_manager4muteEbh',
};
function mame(name, ...args) {
    const fn = window.Module && window.Module[MAME_FN[name]];
    if (typeof fn !== 'function') throw new Error(`MAME has no ${name}`);
    return fn(...args);
}

function writeFile(fs, path, data) {
    const dir = path.substring(0, path.lastIndexOf('/'));
    if (dir) fs.mkdirTree(dir);
    fs.writeFile(path, data);
}

function mameArguments() {
    const vertical = game.rotate === 90 || game.rotate === 270;
    return [GAME,
        '-rompath', '/roms',
        '-homepath', HOME,
        '-cfg_directory', HOME + '/cfg',
        '-nvram_directory', HOME + '/nvram',
        '-state_directory', HOME + '/sta',
        // Snapshots (F12) go here; the app takes the library's thumbnail with one
        '-snapshot_directory', HOME + '/snap',
        '-pluginspath', '/plugins',
        '-plugin', 'hiscore',
        // SDL3's renderer (WebGL); 'opengl', MAME's default, needs desktop OpenGL calls browsers lack
        '-video', 'accel',
        '-window', '-keepaspect',
        '-resolution', vertical ? '768x1024' : '1024x768',
        '-samplerate', '48000',
        '-skip_gameinfo',
        '-joystick',
        ...biosArgument()];
}

// A BIOS set needs one of its alternative BIOS ROMs. MAME starts with the game's default one
// (Neo Geo: Europe); if that one isn't in your set, choose one that is
function biosArgument() {
    const present = game.bios_present || [];
    if (!game.bios || !present.length || present.includes(game.bios_default)) return [];
    return ['-bios', present[0]];
}

function startGame() {
    if (running) return;
    document.getElementById('start-btn').disabled = true;
    setStatus('Starting…');
    const canvas = document.getElementById('canvas');
    window.Module = {
        canvas,
        arguments: mameArguments(),
        locateFile: (path) => api('/mame/' + (path.endsWith('.wasm') ? 'mame.wasm' : path) + MAME_V),
        preRun: [() => {
            const fs = mameFS();
            roms.forEach(r => writeFile(fs, '/roms/' + r.file, r.data));
            saved.forEach(s => writeFile(fs, `${HOME}/${s.path}`, s.data));
            plugins.forEach(p => writeFile(fs, '/plugins/' + p.path, p.data));
            SAVE_DIRS.forEach(d => fs.mkdirTree(`${HOME}/${d}`));
        }],
        onRuntimeInitialized: () => {
            running = true;
            document.getElementById('start').hidden = true;
            document.querySelectorAll('[data-running]').forEach(b => { b.disabled = false; });
            canvas.focus();
            statusLine('Playing. Esc goes back to the library; high scores and settings are saved.');
            setInterval(sync, SYNC_MS);
            startSession();
        },
        print: (t) => { log.push(t); },
        printErr: (t) => { log.push(t); if (/error|missing|not found/i.test(t)) console.warn('MAME:', t); },
        onAbort: (what) => {
            running = false;
            document.getElementById('start').hidden = false;
            setStatus('MAME stopped: ' + (log.slice(-3).join(' ') || what));
        },
    };
    const script = document.createElement('script');
    script.src = api('/mame/mame.js' + MAME_V);
    script.onerror = () => setStatus('Could not load MAME');
    document.body.appendChild(script);
}

// Send every save file that changed since the app last got it
function saveSettings() {
    if (!running) return;
    try { mame('save_settings'); } catch (e) { console.warn('Saving MAME settings failed:', e); }
}

async function sync(keepalive) {
    const fs = mameFS();
    if (!fs) return;
    if (++syncCount % SETTINGS_EVERY === 0) saveSettings();
    const changed = [];
    const walk = (dir, rel) => {
        let entries = [];
        try { entries = fs.readdir(dir); } catch (e) { return; }
        entries.filter(n => n !== '.' && n !== '..').forEach(n => {
            const full = `${dir}/${n}`, path = `${rel}/${n}`;
            const st = fs.stat(full);
            if (fs.isDir(st.mode)) walk(full, path);
            else {
                const data = fs.readFile(full);
                const sig = signature(data);
                if (synced.get(path) !== sig) changed.push({path, data, sig});
            }
        });
    };
    SAVE_DIRS.forEach(d => walk(`${HOME}/${d}`, d));
    for (const c of changed) {
        try {
            // keepalive lets the last save finish while the page closes (browsers allow up to 64 kB)
            const r = await fetch(api(`/api/saves/${GAME}/${c.path}`), {method: 'PUT', body: c.data, keepalive: Boolean(keepalive) && c.data.length < 60000});
            if (r.ok) synced.set(c.path, c.sig);
        } catch (e) { /* next round */ }
    }
    if (changed.some(c => c.path.startsWith('hiscore/'))) statusLine('High scores saved.');
}


// ------------------------------------------------------------------ play time
let session = null;         // id from the app
let played = 0;             // seconds played in this session
let reported = 0;
let lastTick = 0;
function playing() { return running && !leaving && document.visibilityState === 'visible'; }
async function startSession() {
    try { session = (await getJson(`/api/stats/${GAME}/session`, {method: 'POST'})).session; } catch (e) { return; }
    lastTick = performance.now();
    setInterval(tick, 1000);
    setInterval(() => report(false), BEAT_MS);
}
function tick() {
    const now = performance.now();
    // Count real time between ticks (timers slow down in the background), at most a few seconds
    if (playing()) played += Math.min(5, (now - lastTick) / 1000);
    lastTick = now;
    if (!thumbTimer && !thumbTaken && !game.thumb && played >= THUMB_FALLBACK_S) takeThumbnail();
}
// closing: sendBeacon, which the browser delivers after the page is gone
function report(closing) {
    if (session === null || Math.round(played) === reported) return;
    reported = Math.round(played);
    const body = JSON.stringify({seconds: reported});
    const url = api(`/api/stats/session/${session}`);
    if (closing && navigator.sendBeacon && navigator.sendBeacon(url, body)) return;
    fetch(url, {method: 'POST', body, keepalive: true}).catch(() => { reported = -1; });
}
document.addEventListener('visibilitychange', () => {
    tick();
    if (document.visibilityState === 'hidden') report(true);
});

// ------------------------------------------------------------------ thumbnail
let thumbTimer = null;
let thumbTaken = false;
const SNAP_DIR = `${HOME}/snap/${GAME}`;
const THUMB_SIZE = [480, 360];   // 4:3, the shape of an arcade monitor (turned for vertical games)

function snapFiles(fs) {
    try { return fs.readdir(SNAP_DIR).filter(n => n.endsWith('.png')); } catch (e) { return []; }
}
// MAME's own snapshot (F12): the game screen at its own resolution, the right way up, no borders
async function mameSnapshot() {
    const fs = mameFS();
    const before = new Set(snapFiles(fs));
    sendKey('F12', true);
    await new Promise(r => setTimeout(r, 150));
    sendKey('F12', false);
    for (let i = 0; i < 20; i += 1) {
        await new Promise(r => setTimeout(r, 100));
        const name = snapFiles(fs).find(n => !before.has(n));
        if (name) {
            const data = fs.readFile(`${SNAP_DIR}/${name}`);
            fs.unlink(`${SNAP_DIR}/${name}`);
            return createImageBitmap(new Blob([data], {type: 'image/png'}));
        }
    }
    return null;
}
// If that doesn't come: the canvas, right after MAME drew a frame, without the bars around the game
function canvasFrame() {
    return new Promise(resolve => requestAnimationFrame(() => {
        const c = document.getElementById('canvas');
        const vertical = game.rotate === 90 || game.rotate === 270;
        const aspect = vertical ? 3 / 4 : 4 / 3;
        let w = c.width, h = c.height;
        if (w / h > aspect) w = Math.round(h * aspect); else h = Math.round(w / aspect);
        const out = document.createElement('canvas');
        out.width = w; out.height = h;
        out.getContext('2d').drawImage(c, (c.width - w) / 2, (c.height - h) / 2, w, h, 0, 0, w, h);
        resolve(out);
    }));
}
function encode(canvas) {
    return new Promise(resolve => canvas.toBlob(b => resolve(b), 'image/webp', 0.85));
}
async function takeThumbnail() {
    if (!running || leaving || document.visibilityState !== 'visible') return;
    thumbTaken = true;
    try {
        const src = (await mameSnapshot()) || (await canvasFrame());
        // The monitor's 4:3 (the game's pixels need not be square): scale up sharp first, then down smooth
        const [w, h] = game.rotate === 90 || game.rotate === 270 ? [THUMB_SIZE[1], THUMB_SIZE[0]] : THUMB_SIZE;
        const k = Math.max(1, Math.ceil(Math.max(w / src.width, h / src.height)));
        const big = document.createElement('canvas');
        big.width = src.width * k; big.height = src.height * k;
        const bx = big.getContext('2d');
        bx.imageSmoothingEnabled = false;
        bx.drawImage(src, 0, 0, big.width, big.height);
        const out = document.createElement('canvas');
        out.width = w; out.height = h;
        const ox = out.getContext('2d');
        ox.imageSmoothingQuality = 'high';
        ox.drawImage(big, 0, 0, w, h);
        const blob = await encode(out);   // PNG where the browser can't write WebP
        if (!blob) return;
        const res = await getJson(`/api/thumbs/${GAME}`, {method: 'PUT', body: blob});
        game.thumb = res.thumb;
    } catch (e) { console.warn('Thumbnail failed:', e); }
}
// Start pressed after a coin (keyboard or the touch controls): a game began, so the thumbnail
// comes some time into it rather than from the title screen
let coined = false;
window.addEventListener('keydown', (e) => {
    if (!running) return;
    if (e.code === 'Digit5' || e.code === 'Digit6') coined = true;
    else if (coined && !thumbTimer && (e.code === 'Digit1' || e.code === 'Digit2')) {
        thumbTimer = setTimeout(takeThumbnail, THUMB_AFTER_START_MS);
    }
});

function saveState() {
    if (!running) return;
    JSMAME.save('1');
    setTimeout(() => sync().then(() => {
        toast('Saved. Load brings you back to this moment.', 'success');
        document.getElementById('load-btn').title = 'Back to the saved moment (F8)';
    }), 1200);
}
function loadState() {
    if (!running) return;
    const fs = mameFS();
    try { fs.stat(`${HOME}/sta/${GAME}/1.sta`); } catch (e) { toast('Nothing saved yet: press Save first'); return; }
    JSMAME.load('1');
}
function resetGame() { if (running) mame('soft_reset'); }
function toggleMute() {
    if (!running) return;
    muted = !muted;
    mame('mute', mame('get_sound', mame('get_machine')), muted ? 1 : 0, 0x02);  // 0x02: muted by the UI
    document.getElementById('mute-btn').textContent = muted ? '🔇' : '🔊';
}
function toggleFullscreen() {
    const stage = document.getElementById('stage');
    if (document.fullscreenElement) document.exitFullscreen();
    else (stage.requestFullscreen ? stage.requestFullscreen() : Promise.reject()).catch(() => toast('Full screen is not allowed here'));
}

// Leaving: have MAME write NVRAM and settings, send what changed, stop MAME, go back
async function leave() {
    if (leaving) return;
    leaving = true;
    if (running) {
        statusLine('Saving…');
        saveSettings();
        await sync();
        tick();
        report(true);
        try { mame('exit'); } catch (e) { /* already stopped */ }
    }
    window.location.href = api('/');
}
window.addEventListener('pagehide', () => { if (running && !leaving) { saveSettings(); sync(true); tick(); report(true); } });

// Our keys first: Esc would quit MAME and leave a black page
window.addEventListener('keydown', (e) => {
    if (e.code === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); leave(); }
    else if (e.code === 'F6') { e.preventDefault(); e.stopImmediatePropagation(); saveState(); }
    else if (e.code === 'F8') { e.preventDefault(); e.stopImmediatePropagation(); loadState(); }
    else if (!running && (e.code === 'Enter' || e.code === 'Space') && !document.getElementById('start-btn').disabled) { e.preventDefault(); startGame(); }
}, true);

// On-screen controls for touch screens: they send the same keys as a keyboard
const KEYCODES = {ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, Digit5: 53, Digit1: 49, ControlLeft: 17, AltLeft: 18, F12: 123};
const KEYNAMES = {ArrowUp: 'ArrowUp', ArrowDown: 'ArrowDown', ArrowLeft: 'ArrowLeft', ArrowRight: 'ArrowRight', Digit5: '5', Digit1: '1', ControlLeft: 'Control', AltLeft: 'Alt', F12: 'F12'};
function sendKey(code, down) {
    const ev = new KeyboardEvent(down ? 'keydown' : 'keyup', {code, key: KEYNAMES[code], bubbles: true, cancelable: true});
    Object.defineProperty(ev, 'keyCode', {get: () => KEYCODES[code]});
    Object.defineProperty(ev, 'which', {get: () => KEYCODES[code]});
    window.dispatchEvent(ev);
}
function initTouch() {
    if (!window.matchMedia('(pointer: coarse)').matches) return;
    document.getElementById('touch').hidden = false;
    document.querySelectorAll('#touch [data-key]').forEach(el => {
        const press = (down) => (e) => { e.preventDefault(); el.classList.toggle('down', down); sendKey(el.dataset.key, down); };
        el.addEventListener('pointerdown', press(true));
        ['pointerup', 'pointercancel', 'pointerleave'].forEach(ev => el.addEventListener(ev, press(false)));
    });
}

initTouch();
prepare();
