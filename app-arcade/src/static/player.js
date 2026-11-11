// Player: runs one game in MAME (compiled to WebAssembly) on this page.
// Before MAME starts, the game's ROM zips, its saved files (high scores, NVRAM, settings,
// save states) and the hiscore plugin are put in MAME's in-memory file system; while the
// game runs, changed files go back to the app every few seconds and when you leave.
const GAME = document.body.dataset.game;
// The MAME files are cached for long; a new app version gets new URLs
const MAME_V = '?v=' + encodeURIComponent(document.body.dataset.version || '');
const HOME = '/arcade';
const SAVE_DIRS = ['hiscore', 'nvram', 'cfg', 'sta'];
// boot.lua starts MAME's Lua plugins; hiscore needs json
const PLUGIN_FILES = ['boot.lua', 'json/init.lua', 'json/plugin.json', 'hiscore/init.lua', 'hiscore/plugin.json', 'hiscore/hiscore.dat'];
const SYNC_MS = 10000;

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
        const files = [game.zip, game.parent_zip].filter(Boolean);
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
        '-pluginspath', '/plugins',
        '-plugin', 'hiscore',
        // SDL3's renderer (WebGL); 'opengl', MAME's default, needs desktop OpenGL calls browsers lack
        '-video', 'accel',
        '-window', '-keepaspect',
        '-resolution', vertical ? '768x1024' : '1024x768',
        '-samplerate', '48000',
        '-skip_gameinfo',
        '-joystick'];
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
            statusLine('Playing. Esc goes back to the library. High scores are saved as you set them.');
            setInterval(sync, SYNC_MS);
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
async function sync() {
    const fs = mameFS();
    if (!fs) return;
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
            const r = await fetch(api(`/api/saves/${GAME}/${c.path}`), {method: 'PUT', body: c.data});
            if (r.ok) synced.set(c.path, c.sig);
        } catch (e) { /* next round */ }
    }
    if (changed.some(c => c.path.startsWith('hiscore/'))) statusLine('High scores saved.');
}

function machine() { return window.JSMAME && JSMAME.get_machine(); }

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
function resetGame() { if (running) JSMAME.soft_reset(); }
function toggleMute() {
    if (!running) return;
    muted = !muted;
    JSMAME.sound_manager_mute(JSMAME.get_sound(machine()), muted, 0x02);
    document.getElementById('mute-btn').textContent = muted ? '🔇' : '🔊';
}
function toggleFullscreen() {
    const stage = document.getElementById('stage');
    if (document.fullscreenElement) document.exitFullscreen();
    else (stage.requestFullscreen ? stage.requestFullscreen() : Promise.reject()).catch(() => toast('Full screen is not allowed here'));
}

// Leaving: ask MAME to stop, send what changed, go back. (In a browser MAME skips its exit
// step, so NVRAM and settings are not written; high scores and save states are.)
async function leave() {
    if (leaving) return;
    leaving = true;
    if (running) {
        statusLine('Saving…');
        try { JSMAME.exit(); } catch (e) { /* already stopped */ }
        await new Promise(r => setTimeout(r, 700));
        await sync();
    }
    window.location.href = api('/');
}
window.addEventListener('pagehide', () => { if (running && !leaving) sync(); });

// Our keys first: Esc would quit MAME and leave a black page
window.addEventListener('keydown', (e) => {
    if (e.code === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); leave(); }
    else if (e.code === 'F6') { e.preventDefault(); e.stopImmediatePropagation(); saveState(); }
    else if (e.code === 'F8') { e.preventDefault(); e.stopImmediatePropagation(); loadState(); }
    else if (!running && (e.code === 'Enter' || e.code === 'Space') && !document.getElementById('start-btn').disabled) { e.preventDefault(); startGame(); }
}, true);

// On-screen controls for touch screens: they send the same keys as a keyboard
const KEYCODES = {ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39, Digit5: 53, Digit1: 49, ControlLeft: 17, AltLeft: 18};
const KEYNAMES = {ArrowUp: 'ArrowUp', ArrowDown: 'ArrowDown', ArrowLeft: 'ArrowLeft', ArrowRight: 'ArrowRight', Digit5: '5', Digit1: '1', ControlLeft: 'Control', AltLeft: 'Alt'};
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
