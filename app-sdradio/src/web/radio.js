// SDRadio dashboard - the Radio page: display, knobs, keypad, favourites, stored channels and audio.

const radio = {knobs: {}, dial: null, entry: null, lastDevice: null, listKey: '', favKey: ''};

// =====================================================================
// Audio: PCM from the app, played with Web Audio
// =====================================================================
const audio = {ctx: null, gain: null, next: 0, on: false, muted: false, volume: lsGet('volume', 65)};

function audioGain() { return audio.muted ? 0 : Math.pow(audio.volume / 100, 2) * 2; }

function toggleListen() {
    if (audio.on) {
        audio.on = false;
        sendSocket({cmd: 'listen', on: false});
        socketWanted.listen = false;
        if (audio.ctx) audio.ctx.close();
        audio.ctx = null;
    } else {
        // Browsers only start audio from a click
        audio.ctx = new (window.AudioContext || window.webkitAudioContext)();
        audio.gain = audio.ctx.createGain();
        audio.gain.gain.value = audioGain();
        audio.gain.connect(audio.ctx.destination);
        audio.next = 0;
        audio.on = true;
        socketWanted.listen = true;
        sendSocket({cmd: 'listen', on: true});
    }
    renderListen();
}
function renderListen() {
    const button = document.getElementById('listen-btn');
    button.classList.toggle('on', audio.on);
    document.getElementById('listen-label').textContent = audio.on ? 'Listening' : 'Listen';
    button.title = audio.on ? 'Stop listening in this browser' : 'Listen in this browser';
}
function setVolume(v) {
    audio.volume = v;
    lsSet('volume', v);
    if (audio.gain) audio.gain.gain.setTargetAtTime(audioGain(), audio.ctx.currentTime, 0.02);
}
function toggleMute() {
    audio.muted = !audio.muted;
    if (audio.gain) audio.gain.gain.setTargetAtTime(audioGain(), audio.ctx.currentTime, 0.02);
    document.getElementById('ind-mute').classList.toggle('on', audio.muted);
}

onSocket('audio', (rate, pcm) => {
    if (!audio.on || !audio.ctx) return;
    const buffer = audio.ctx.createBuffer(1, pcm.length, rate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) data[i] = pcm[i] / 32768;
    const source = audio.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(audio.gain);
    const lead = ((app.settings && app.settings.audio_buffer_ms) || 250) / 1000;
    const now = audio.ctx.currentTime;
    // Start a little ahead; after a gap (squelch closed) or when too far behind, start over
    if (audio.next < now + 0.01 || audio.next > now + lead * 3) audio.next = now + lead;
    source.start(audio.next);
    audio.next += buffer.duration;
});

// =====================================================================
// The front panel
// =====================================================================
function initRadio() {
    const row = document.getElementById('knob-row');
    const k = radio.knobs;
    k.volume = new Knob(row, {label: 'Volume', min: 0, max: 100, step: 1, value: audio.volume, defaultValue: 65,
        format: v => `${v} %`, onInput: setVolume});
    k.squelch = new Knob(row, {label: 'Squelch', min: -140, max: -10, step: 1, value: -140, defaultValue: -140,
        format: v => v <= -140 ? 'off' : `${v} dB`,
        onInput: throttle(v => radioCommand('set', {squelch: v <= -140 ? null : v}), 120)});
    k.gain = new Knob(row, {label: 'RF gain', min: -1, max: 50, step: 1, value: -1,
        // A plain function: called with the knob's options as `this`, the lowest position is "auto"
        format(v) { return v < this.min + this.step / 2 ? 'auto' : `${num(v, 1)} dB`; },
        onChange: v => radioCommand('set', {gain: v < k.gain.opts.min + k.gain.opts.step / 2 ? null : v})});
    k.filter = new Knob(row, {label: 'Filter', values: [12500], value: 12500, format: v => fmtKHz(v),
        onChange: v => radioCommand('set', {bandwidth: v})});
    k.speed = new Knob(row, {label: 'Scan speed', min: 1, max: 20, step: 1, value: 10, defaultValue: 10,
        format: v => `${v} ch/s`, onChange: v => saveSetting('scan_speed', v, true)});

    radio.dial = new TuneDial(document.getElementById('tune-dial'), {size: 140,
        onSteps: n => radioCommand('step_tune', {steps: n})});

    buildKeypad();
    const sources = [{key: 'channels', label: 'Channels'}, {key: 'band', label: 'Band'}];
    radio.scanSource = lsGet('scan_source', 'band');
    if (!sources.some(s => s.key === radio.scanSource)) radio.scanSource = 'band';
    const sourceTitles = {channels: 'SCAN steps through the stored channels', band: 'SCAN steps through the band the radio is in, with its own step and mode'};
    buildChips(document.getElementById('scan-source'), sources, radio.scanSource, (key) => {
        radio.scanSource = key;
        lsSet('scan_source', key);
        renderScanKey();
        restartScan();
    });
    document.querySelectorAll('#scan-source [data-key]').forEach(b => { b.title = sourceTitles[b.dataset.key]; });
    renderScanKey();

    const freq = document.getElementById('lcd-freq');
    freq.addEventListener('wheel', (e) => {
        const digit = e.target.closest('[data-p]');
        if (!digit || !app.state) return;
        e.preventDefault();
        const delta = Math.pow(10, Number(digit.dataset.p)) * (e.deltaY < 0 ? 1 : -1);
        radioCommand('tune', {frequency: Math.max(10000, app.state.frequency + delta)});
    }, {passive: false});
    freq.addEventListener('click', () => { if (radio.entry === null) startEntry(''); else if (radio.entry === '') cancelEntry(); });
    document.addEventListener('pointerdown', (e) => {
        if (radio.entry !== null && !e.target.closest('#lcd-freq, #keypad')) cancelEntry();
    });

    document.getElementById('chlist-search').addEventListener('input', () => renderChannelList(true));
    const filter = document.getElementById('chlist-filter');
    filter.value = lsGet('chlist_filter', 'band');
    filter.addEventListener('change', () => { lsSet('chlist_filter', filter.value); renderChannelList(true); });

    document.addEventListener('keydown', radioKeys);
    initSpectrum();
    renderListen();
}

function throttle(fn, ms) {
    let timer = null, lastArgs = null;
    return (...args) => {
        lastArgs = args;
        if (timer) return;
        timer = setTimeout(() => { timer = null; fn(...lastArgs); }, ms);
    };
}

const KEYS = [
    ['1'], ['2'], ['3'], ['MODE', '', 'fn', () => radioCommand('cycle', {what: 'mode'}), 'mode'],
    ['4'], ['5'], ['6'], ['BAND', '', 'fn', () => radioCommand('band', {direction: 1}), 'band'],
    ['7'], ['8'], ['9'], ['STEP', '', 'fn', () => radioCommand('cycle', {what: 'step'})],
    ['.'], ['0'], ['CLR', '', 'fn', () => cancelEntry()], ['MHz', '', 'go', () => commitEntry()],
];
// Under the keypad; storing and stepping through the stored channels is under their list
const SCAN_KEYS = [
    ['SCAN', '', 'fn', () => toggleScan(), 'scan'], ['HOLD', '', 'fn', () => radioCommand('scan', {action: 'hold'}), 'hold'],
    ['PRI', '', 'fn', () => radioCommand('priority'), 'pri'], ['L/O', '', 'fn', () => radioCommand('lockout'), 'lo'],
];
const KEY_TITLES = {
    MODE: 'Next mode', BAND: 'Next band', STEP: 'Next tuning step', CLR: 'Clear the entry', MHz: 'Tune to the frequency typed',
    SCAN: 'Start or stop scanning; on a signal it goes on to the next channel', HOLD: 'Stay on this channel while scanning', PRI: 'Priority: check the priority channels every few seconds',
    'L/O': 'Lock this channel out of scans (or ignore this frequency)',
};

function buildKeypad() {
    buildKeys(document.getElementById('keypad'), KEYS);
    buildKeys(document.getElementById('scan-keys'), SCAN_KEYS);
}

function buildKeys(pad, keys) {
    pad.innerHTML = '';
    keys.forEach(([label, small, cls, action, id]) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'key' + (cls ? ' ' + cls : '');
        b.innerHTML = escapeHtml(label) + (small ? `<small>${escapeHtml(small)}</small>` : '');
        if (KEY_TITLES[label]) b.title = KEY_TITLES[label];
        if (id) b.dataset.key = id;
        b.onclick = action || (() => typeDigit(label));
        pad.appendChild(b);
    });
}

// Typing a frequency: on the keypad or the keyboard, in MHz
const ENTRY_TIMEOUT = 10000;
function startEntry(text) {
    radio.entry = text;
    radio.entryFrequency = app.state ? app.state.frequency : null;
    entryTimer();
    renderLcd();
}
function typeDigit(d) {
    if (radio.entry === null) return startEntry(d === '.' ? '0.' : d);
    if (d === '.' && radio.entry.includes('.')) return;
    if (radio.entry.length >= 11) return;
    radio.entry += d;
    entryTimer();
    renderLcd();
}
// An entry left alone is dropped, as on a scanner
function entryTimer() {
    clearTimeout(radio.entryTimeout);
    radio.entryTimeout = setTimeout(cancelEntry, ENTRY_TIMEOUT);
}
function cancelEntry() {
    clearTimeout(radio.entryTimeout);
    if (radio.entry === null) return;
    radio.entry = null;
    renderLcd();
}
function commitEntry() {
    if (radio.entry === null || radio.entry === '' || radio.entry === '.') return cancelEntry();
    const mhz = parseFloat(radio.entry);
    cancelEntry();
    if (!(mhz > 0) || mhz > 6000) { toast('Type a frequency in MHz, 0.01 to 6000', 'error'); return renderLcd(); }
    radioCommand('tune', {frequency: Math.round(mhz * 1e6)});
    renderLcd();
}

function radioKeys(e) {
    if (activeTab !== 'radio' || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest('input, textarea, select, .modal')) return;
    if (/^[0-9]$/.test(e.key)) { typeDigit(e.key); e.preventDefault(); }
    else if (e.key === '.' || e.key === ',') { typeDigit('.'); e.preventDefault(); }
    else if (e.key === 'Enter' && radio.entry !== null) { commitEntry(); e.preventDefault(); }
    else if (e.key === 'Escape' && radio.entry !== null) { cancelEntry(); e.preventDefault(); }
    else if (e.key === 'Backspace' && radio.entry) { radio.entry = radio.entry.slice(0, -1); entryTimer(); renderLcd(); e.preventDefault(); }
    else if (e.target.closest('.knob')) { /* the knob handles its own keys */ }
    else if (e.key === 'ArrowUp' || e.key === 'ArrowRight') { radioCommand('step_tune', {steps: 1}); e.preventDefault(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') { radioCommand('step_tune', {steps: -1}); e.preventDefault(); }
    else if ((e.key === 't' || e.key === 'T') && !document.getElementById('lcd-auto').disabled) { radioCommand('autotune'); e.preventDefault(); }
    else if (e.key === 'PageUp') { radioCommand('step_tune', {steps: 10}); e.preventDefault(); }
    else if (e.key === 'PageDown') { radioCommand('step_tune', {steps: -10}); e.preventDefault(); }
}

// SCAN starts, goes on past the signal it stopped on (or the held channel), and stops a scan that is stepping
function scanWouldSkip(s) { return !!(s.scan && (s.busy || s.scan.hold)); }
function toggleScan() {
    const s = app.state || {};
    if (scanWouldSkip(s)) return radioCommand('scan', {action: 'skip'});
    if (s.scan) return radioCommand('scan', {action: 'stop'});
    startScan();
}
// Starting replaces a scan that is running
function startScan() {
    const s = app.state || {};
    const source = radio.scanSource || 'channels';
    if (source === 'channels') {
        const filter = document.getElementById('chlist-filter').value;
        radioCommand('scan', {action: 'start', kind: 'channels', band: filter === 'band' ? s.band : null});
    } else {
        radioCommand('scan', {action: 'start', kind: 'band', band: s.band});
    }
}

// Choosing what to scan while scanning switches the scan over
function restartScan() {
    if (app.state && app.state.scan) startScan();
}
function renderScanKey() {
    const key = document.querySelector('#scan-keys [data-key="scan"]');
    if (!key) return;
    const next = scanWouldSkip(app.state || {});
    const html = `SCAN<small>${next ? 'NEXT' : {channels: 'CH', band: 'BAND'}[radio.scanSource] || 'BAND'}</small>`;
    if (key.innerHTML !== html) key.innerHTML = html;
    key.title = next ? 'Go on to the next channel (stop the scan by tuning anywhere)' : KEY_TITLES.SCAN;
}

function toggleSetting(key) {
    const s = app.state || {};
    radioCommand('set', {[key]: !s[key]});
}

// =====================================================================
// Rendering the state
// =====================================================================
function freqDigitsHtml(hz) {
    const mhz = Math.floor(hz / 1e6);
    const rest = String(hz % 1e6).padStart(6, '0');
    const mhzDigits = String(mhz);
    let html = '';
    for (let i = 0; i < mhzDigits.length; i++) html += `<span class="d" data-p="${6 + mhzDigits.length - 1 - i}">${mhzDigits[i]}</span>`;
    html += '<span class="sep">.</span>';
    for (let i = 0; i < 3; i++) html += `<span class="d" data-p="${5 - i}">${rest[i]}</span>`;
    html += '<span class="sep dim">.</span>';
    for (let i = 3; i < 6; i++) html += `<span class="d dim" data-p="${5 - i}">${rest[i]}</span>`;
    return html;
}

function renderLcd() {
    const s = app.state;
    if (!s) return;
    const freq = document.getElementById('lcd-freq');
    // Tuned some other way (waterfall, dial, scan): the display follows the radio again
    if (radio.entry !== null && s.frequency !== radio.entryFrequency) { radio.entry = null; clearTimeout(radio.entryTimeout); }
    if (radio.entry !== null) {
        freq.classList.add('entry');
        freq.textContent = radio.entry;
    } else {
        freq.classList.remove('entry');
        freq.innerHTML = freqDigitsHtml(s.frequency);
    }
    // In a mode the box shows how the signal is modulated (what you hear with Listen is beside it)
    const dm = s.decoder || {};
    const inMode = !!(dm.key && dm.key !== 'radio' && dm.signal);
    const modeBox = document.getElementById('lcd-mode');
    modeBox.textContent = inMode ? dm.signal : modeLabel(s.mode);
    modeBox.title = inMode ? `${dm.name}: the signal is ${dm.signal}; with Listen you hear it as ${modeLabel(s.mode)}` : 'Mode: click for the next one';
    // In a mode: what decodes it (the mode and filter shown are only how it sounds with Listen)
    const d = s.decoder || {};
    document.getElementById('lcd-bw').textContent = d.key && d.key !== 'radio' && d.decoded_by
        ? `decoded by ${d.decoded_by} · ${d.listen === false ? 'no sound' : `listen as ${modeLabel(s.mode)}`}`
        : `BW ${fmtKHz(s.bandwidth)} · STEP ${fmtKHz(s.step)}`;
    const set = (id, on) => document.getElementById(id).classList.toggle('on', !!on);
    set('ind-scan', s.scan);
    set('ind-hold', s.scan && s.scan.hold);
    set('ind-pri', s.priority);
    set('ind-agc', s.agc);
    set('ind-nb', s.nb);
    set('ind-mute', audio.muted);
    set('ind-lo', (s.channel && !s.channel.scan) || s.ignored);
    const info = [s.band || 'no band'];
    if (s.channel) info.push(`M${chNum(s.channel.number)}`);
    if (s.scan) info.push(`${s.scan.kind === 'channels' ? 'CH' : 'STEP'} ${s.scan.position}/${s.scan.count}`);
    document.getElementById('lcd-info').textContent = info.join(' · ');
    const name = document.getElementById('lcd-name');
    const rds = s.mode === 'wfm' ? s.rds : null;
    const ps = rds && rds.ps;
    name.innerHTML = s.channel
        ? `${escapeHtml(s.channel.name || 'Unnamed')}<small>M${chNum(s.channel.number)}${s.channel.favourite ? ' ★' : ''}${ps && ps !== s.channel.name ? ` · ${escapeHtml(ps)}` : ''}</small>`
        : ps ? `${escapeHtml(ps)}<small>RDS · not stored</small>`
        : `<span style="opacity:.6">${s.busy ? 'Signal · not stored' : 'Not stored'}</span>`;
    // A mode without sound (Video) has no RDS either
    renderRds(s.mode === 'wfm' && !(s.decoder && s.decoder.decoded_by && s.decoder.listen === false), rds);
    // S-meter: -120 to -20 dBFS over 24 segments
    const meter = document.getElementById('smeter');
    if (meter.children.length !== 24) meter.innerHTML = Array.from({length: 24}, (_, i) => `<i class="${i >= 18 ? 'hot' : ''}" style="height:${6 + i * 0.5}px"></i>`).join('');
    const lit = Math.max(0, Math.min(24, Math.round((s.level + 120) / 100 * 24)));
    Array.from(meter.children).forEach((seg, i) => seg.classList.toggle('lit', i < lit));
    const snr = s.noise > -140 ? Math.max(0, s.level - s.noise) : null;
    document.getElementById('lcd-level').textContent = `${num(s.level, 0)} dBFS${snr !== null ? ` · SNR ${num(snr, 0)} dB` : ''}`;
}

// The RDS line: artist and title when the station marks them (RadioText+), else its radio text
function renderRds(wfm, rds) {
    const line = document.getElementById('lcd-rds');
    if (line.hidden === wfm) { line.hidden = !wfm; fitWaterfall(); }
    if (!wfm) return;
    const text = document.getElementById('rds-text');
    let value = '';
    if (rds && rds.artist && rds.title) value = `♪ ${rds.artist} – ${rds.title}`;
    else if (rds && rds.title) value = `♪ ${rds.title}`;
    else if (rds && rds.rt) value = rds.rt;
    const shown = value || (rds && rds.synced ? 'RDS…' : 'No RDS');
    // Laid out again when the text or the line's width changes (window resized, back from another tab)
    if (radio.rdsShown !== shown || radio.rdsWidth !== text.clientWidth) {
        radio.rdsShown = shown;
        text.title = rds && rds.rt ? rds.rt : '';
        layoutRdsText();
    }
    text.classList.toggle('dim', !value);
    document.getElementById('rds-pty').textContent = rds ? [rds.pty, rds.tp ? 'TP' : '', rds.ta ? 'TA' : ''].filter(Boolean).join(' · ') : '';
}

// A text longer than the line scrolls round, as on a car radio
const RDS_SCROLL_SPEED = 40; // px per second
function layoutRdsText() {
    const text = document.getElementById('rds-text');
    const shown = radio.rdsShown || '';
    text.innerHTML = `<span class="rds-run">${escapeHtml(shown)}</span>`;
    radio.rdsWidth = text.clientWidth;
    const run = text.firstChild;
    const width = run.getBoundingClientRect().width;
    if (!text.clientWidth || width <= text.clientWidth) {
        text.classList.remove('scrolling');
        return;
    }
    const distance = width + 48;
    text.classList.add('scrolling');
    run.classList.add('scroll');
    run.style.setProperty('--distance', `-${distance}px`);
    run.style.setProperty('--duration', `${(distance / RDS_SCROLL_SPEED).toFixed(1)}s`);
    run.innerHTML = `${escapeHtml(shown)}<span class="rds-gap"></span>${escapeHtml(shown)}`;
}

function renderControls() {
    const s = app.state;
    if (!s) return;
    const k = radio.knobs;
    k.squelch.set(s.squelch === null || s.squelch === undefined ? -140 : s.squelch, true);
    if (s.gain_range) {
        const [lo, hi, step] = s.gain_range;
        const st = step > 0 ? step : 0.5;
        if (k.gain.opts.max !== hi || k.gain.opts.min !== lo - st) k.gain.setRange(lo - st, hi, st);
    }
    k.gain.set(s.gain === null || s.gain === undefined ? k.gain.opts.min : s.gain, true);
    if (s.bandwidths && String(k.filter.opts.values) !== String(s.bandwidths)) k.filter.setValues(s.bandwidths);
    k.filter.set(s.bandwidth, true);
    if (app.settings && app.settings.scan_speed) k.speed.set(app.settings.scan_speed, true);
    radio.dial.setReadout(`step ${fmtHz(s.step)}`);
    // A mode stays on its own frequency and demodulation: what would change that is off
    const locked = !!(s.decoder && s.decoder.key && s.decoder.key !== 'radio');
    document.querySelectorAll('.front [data-key]').forEach(b => {
        if (['scan', 'hold', 'pri', 'lo', 'mode', 'band'].includes(b.dataset.key)) {
            b.disabled = locked;
            b.title = locked ? `Not in the ${s.decoder.name} mode (Mode → Radio)` : (KEY_TITLES[b.textContent.replace(/(CH|BAND|NEXT)$/, '').trim()] || b.title);
        }
    });
    document.querySelectorAll('#scan-source .tab').forEach(b => { b.disabled = locked; });
    document.getElementById('lcd-mode').disabled = locked;
    document.getElementById('lcd-auto').disabled = locked;
    k.filter.setDisabled(locked);
    k.speed.setDisabled(locked);
    document.querySelectorAll('.front [data-key]').forEach(b => {
        const key = b.dataset.key;
        const on = key === 'scan' ? s.scan : key === 'hold' ? s.scan && s.scan.hold : key === 'pri' ? s.priority
            : key === 'lo' ? (s.channel && !s.channel.scan) || s.ignored : false;
        b.classList.toggle('on', !!on);
    });
    renderScanKey();
    const hint = document.getElementById('spectrum-hint');
    const hintText = s.power === false ? 'Switched off: the power button at the top left of the display switches it on'
        : s.sweep
        ? `Band view of ${s.sweep.label}: the receiver sweeps the band, so the radio is silent · click a station to listen`
        : 'click to tune · scroll to step · drag to move';
    if (hint.textContent !== hintText) hint.textContent = hintText;
    hint.classList.toggle('warn-text', !!s.sweep);
    const badge = document.getElementById('scan-badge');
    badge.hidden = !s.scan;
    if (s.scan) badge.textContent = `${s.scan.hold ? 'HOLD' : 'SCANNING'} · ${s.scan.label || (s.scan.kind === 'channels' ? 'all channels' : s.scan.kind)}`;
    document.getElementById('store-btn').textContent = s.channel ? `M+ Update M${chNum(s.channel.number)}` : 'M+ Store current';
}

onSocket('state', (s) => {
    const off = s.power === false;
    const page = document.getElementById('tab-radio');
    if (page.classList.contains('power-off') !== off) {
        page.classList.toggle('power-off', off);
        const button = document.getElementById('ind-power');
        button.classList.toggle('on', !off);
        button.title = off ? 'Switch the radio on' : 'Switch the radio off (closes the receiver, uses nothing)';
        if (off) {
            clearWaterfall();
            spec.frame = null;  // nothing to redraw while off
            const spectrum = document.getElementById('spectrum');
            spectrum.getContext('2d').clearRect(0, 0, spectrum.width, spectrum.height);
        }
    }
    const deviceKey = JSON.stringify([s.power, s.rate, s.device && s.device.ok, s.device && s.device.error]);
    if (deviceKey !== radio.lastDevice) { radio.lastDevice = deviceKey; renderChrome(); }
    if (activeTab !== 'radio') return;
    fitWaterfall();
    renderLcd();
    renderControls();
    renderChannelList(false);
    renderFavourites();
    drawScale();
});

let channelsReload = null;
function reloadChannelsSoon() {
    if (channelsReload) return;
    channelsReload = setTimeout(async () => {
        channelsReload = null;
        await loadChannels();
        renderChannelList(true);
        renderFavourites(true);
        if (activeTab === 'channels' && typeof renderChannelsPage === 'function') renderChannelsPage();
    }, 1500);
}
onSocket('hit', reloadChannelsSoon);
onSocket('changed', (m) => {
    reloadChannelsSoon();
    if (m.what === 'settings' || m.what === 'bands' || m.what === 'all') loadSettings().then(() => loadStatus());
});
onSocket('notice', (m) => toast(m.message));

// =====================================================================
// Favourites and the stored channels
// =====================================================================
function renderFavourites(force) {
    const s = app.state || {};
    const favs = app.channels.filter(c => c.favourite);
    const current = s.channel ? s.channel.id : null;
    const key = JSON.stringify([favs.map(c => [c.id, c.name, c.frequency]), current]);
    if (!force && key === radio.favKey) return;
    radio.favKey = key;
    const el = document.getElementById('fav-list');
    el.innerHTML = favs.length ? favs.map(c => `
        <button type="button" class="fav${c.id === current ? ' on' : ''}" data-id="${c.id}" title="${escapeHtml(c.name || '')} · ${fmtMHz(c.frequency)} MHz">
            <b>${fmtMHz(c.frequency)}</b><span>${escapeHtml(c.name || 'M' + chNum(c.number))} · ${modeLabel(c.mode)}</span>
        </button>`).join('')
        : '<span class="fav-empty">No favourites yet: tune to a channel and press + ★, or tick Favourite on the Channels page.</span>';
    el.onclick = (e) => {
        const b = e.target.closest('[data-id]');
        if (b) radioCommand('channel', {id: Number(b.dataset.id)});
    };
    document.getElementById('fav-add').textContent = s.channel && s.channel.favourite ? '− ★' : '+ ★';
}

async function favouriteCurrent() {
    const s = app.state || {};
    if (!s.channel) return openStore({favourite: true});
    try {
        await api(`channels/${s.channel.id}`, {method: 'PUT', json: {favourite: !s.channel.favourite}});
        await loadChannels();
        renderFavourites(true);
    } catch (e) {
        toast(e.message, 'error');
    }
}

function renderChannelList(force) {
    const s = app.state || {};
    const search = document.getElementById('chlist-search').value.trim().toLowerCase();
    const filter = document.getElementById('chlist-filter').value;
    const current = s.channel ? s.channel.id : null;
    const key = JSON.stringify([search, filter, s.band, current, app.channels.length]);
    if (!force && key === radio.listKey) return;
    radio.listKey = key;
    let rows = app.channels;
    if (filter === 'band') rows = rows.filter(c => c.band === s.band);
    else if (filter === 'fav') rows = rows.filter(c => c.favourite);
    if (search) {
        rows = rows.filter(c => (c.name || '').toLowerCase().includes(search) || fmtMHz(c.frequency).includes(search)
            || String(c.number).includes(search) || (c.tags || '').toLowerCase().includes(search));
    }
    document.getElementById('chlist-count').textContent = `${filter === 'band' && s.band ? s.band + ' · ' : ''}${rows.length} of ${app.channels.length}`;
    const el = document.getElementById('chlist-items');
    el.innerHTML = rows.length ? rows.map(c => `
        <button type="button" class="ch-item${c.id === current ? ' on' : ''}${c.scan ? '' : ' locked'}" data-id="${c.id}">
            <span class="num">${chNum(c.number)}</span>
            <span class="body"><b>${escapeHtml(c.name || 'Unnamed')}${c.favourite ? ' ★' : ''}</b><small>${fmtMHz(c.frequency)} · ${modeLabel(c.mode)}</small></span>
            <span class="seen" title="last heard">${c.last_heard ? fmtAgo(c.last_heard) : '—'}</span>
        </button>`).join('')
        : `<div class="empty-state">${app.channels.length ? 'No channel matches' : 'No channels stored yet. Tune to something and press Store current, or import a CSV on the Channels page.'}</div>`;
    el.onclick = (e) => {
        const b = e.target.closest('[data-id]');
        if (b) radioCommand('channel', {id: Number(b.dataset.id)});
    };
    const on = el.querySelector('.ch-item.on');
    if (on && force !== true) on.scrollIntoView({block: 'nearest'});
}

function bandOptions(selected) {
    return '<option value="">(automatic)</option>' + app.bands.map(b =>
        `<option value="${escapeHtml(b.name)}"${b.name === selected ? ' selected' : ''}>${escapeHtml(b.name)}</option>`).join('');
}

// Store the current frequency as a channel, or update the stored channel it is on
function openStore(preset) {
    const s = app.state;
    if (!s) return;
    const existing = s.channel ? app.channels.find(c => c.id === s.channel.id) : null;
    const c = Object.assign({number: existing ? existing.number : app.nextNumber, name: '', band: '', tags: '', favourite: false, scan: true},
        existing || {}, preset || {});
    const body = `
        <div class="note">${fmtMHz(s.frequency)} MHz · ${modeLabel(s.mode)} · filter ${fmtKHz(s.bandwidth)}${s.squelch !== null ? ` · squelch ${s.squelch} dB` : ''}</div>
        <div class="form-grid">
            <div class="form-field"><label for="st-number">Number</label><input id="st-number" class="input mono" type="number" min="0" max="99999" value="${c.number ?? ''}"></div>
            <div class="form-field"><label for="st-band">Band</label><select id="st-band" class="input">${bandOptions(c.band)}</select></div>
            <div class="form-field full"><label for="st-name">Name</label><input id="st-name" class="input" maxlength="80" value="${escapeHtml(c.name)}" placeholder="What is on this frequency"></div>
            <div class="form-field full"><label for="st-tags">Tags</label><input id="st-tags" class="input" value="${escapeHtml(c.tags)}" placeholder="amateur, repeater"></div>
        </div>
        <div class="checks">
            <label><input type="checkbox" id="st-fav"${c.favourite ? ' checked' : ''}> Favourite</label>
            <label><input type="checkbox" id="st-scan"${c.scan ? ' checked' : ''}> In the channel scan</label>
        </div>`;
    openModal(existing ? `Update M${chNum(existing.number)}` : 'Store channel', body, [
        {label: 'Cancel'},
        {label: existing ? 'Update' : 'Store', cls: 'btn-primary', action: async () => {
            const data = {
                number: document.getElementById('st-number').value,
                name: document.getElementById('st-name').value,
                band: document.getElementById('st-band').value,
                tags: document.getElementById('st-tags').value,
                favourite: document.getElementById('st-fav').checked,
                scan: document.getElementById('st-scan').checked,
                frequency: s.frequency, mode: s.mode, bandwidth: s.bandwidth, step: s.step, squelch: s.squelch,
            };
            const saved = existing
                ? await api(`channels/${existing.id}`, {method: 'PUT', json: data})
                : await api('channels', {method: 'POST', json: data});
            toast(`Stored as M${chNum(saved.number)}`, 'success');
            await loadChannels();
            renderChannelList(true);
            renderFavourites(true);
        }},
    ]);
}

function editCurrentChannel() {
    const s = app.state || {};
    switchTab('channels');
    if (s.channel) selectChannel(s.channel.id);
    else newChannel({frequency: s.frequency, mode: s.mode, bandwidth: s.bandwidth});
}

// The waterfall fills the screen below it, so the whole radio fits without scrolling
function fitWaterfall() {
    const wf = document.getElementById('waterfall');
    const pages = document.getElementById('pages');
    if (!wf.offsetParent) return;
    // below the waterfall: the panel's padding and the page's bottom padding
    const left = pages.getBoundingClientRect().bottom - (wf.getBoundingClientRect().top + pages.scrollTop) - 48;
    const height = Math.round(Math.max(110, Math.min(640, left)));
    if (wf.style.height !== `${height}px`) {
        wf.style.height = `${height}px`;
        if (typeof clearWaterfall === 'function') clearWaterfall();
    }
}
window.addEventListener('resize', fitWaterfall);

onTab('radio', async () => {
    requestAnimationFrame(fitWaterfall);
    renderLcd();
    renderControls();
    renderChannelList(true);
    renderFavourites(true);
    requestAnimationFrame(redrawSpectrum);
});
