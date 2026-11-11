// SDRadio dashboard - the spectrum, the waterfall, the frequency axis and the band scale.

const PALETTES = {
    classic: [[0, '#0a1638'], [0.25, '#1b3fa0'], [0.45, '#1fa3c9'], [0.62, '#f0e44a'], [0.8, '#f08a24'], [1, '#d42a1f']],
    grey: [[0, '#05070a'], [1, '#ffffff']],
    turbo: [[0, '#30123b'], [0.15, '#4662d7'], [0.3, '#36aaf9'], [0.45, '#1ae4b6'], [0.6, '#72fe5e'], [0.75, '#e4cf34'], [0.88, '#fb7e21'], [1, '#7a0403']],
};
const ZOOMS = [{key: 'full', label: 'Full'}, {key: '1000000', label: '1M'}, {key: '200000', label: '200k'}, {key: '50000', label: '50k'}];

const spec = {
    frame: null, zoom: lsGet('zoom', 'full'), autoRange: lsGet('auto_range', true),
    lo: -120, hi: -30, lut: null, palette: null, hoverX: null, dragFrom: null, dragging: false,
};

function hexRgb(hex) { const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function buildLut(name) {
    const stops = PALETTES[name] || PALETTES.classic;
    const lut = new Uint8ClampedArray(256 * 3);
    for (let i = 0; i < 256; i++) {
        const t = i / 255;
        let k = 0;
        while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
        const [t0, c0] = stops[k], [t1, c1] = stops[k + 1];
        const f = Math.max(0, Math.min(1, (t - t0) / ((t1 - t0) || 1)));
        const a = hexRgb(c0), b = hexRgb(c1);
        for (let j = 0; j < 3; j++) lut[i * 3 + j] = a[j] + (b[j] - a[j]) * f;
    }
    spec.lut = lut;
    spec.palette = name;
}

// The frequencies on screen: the whole span, or a zoomed window round the tuned frequency
function viewWindow(frame) {
    const s = app.state || {};
    const full = {start: frame.center - frame.rate / 2, span: frame.rate};
    if (spec.zoom === 'full') return full;
    const span = Math.min(Number(spec.zoom), frame.rate);
    let start = (s.frequency || frame.center) - span / 2;
    start = Math.max(full.start, Math.min(full.start + full.span - span, start));
    return {start, span};
}

function sizeCanvas(canvas, height) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(50, Math.round(canvas.clientWidth * dpr));
    const h = Math.round(height * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    return {w, h, dpr};
}

// The level of each screen column: the strongest bin it covers
function columns(frame, view, width) {
    const n = frame.bins.length;
    const scale = frame.ceil - frame.floor;
    const out = new Float32Array(width);
    const binOf = f => (f - (frame.center - frame.rate / 2)) / frame.rate * n;
    for (let x = 0; x < width; x++) {
        const b0 = Math.max(0, Math.floor(binOf(view.start + x / width * view.span)));
        const b1 = Math.min(n, Math.max(b0 + 1, Math.ceil(binOf(view.start + (x + 1) / width * view.span))));
        let m = 0;
        for (let b = b0; b < b1; b++) if (frame.bins[b] > m) m = frame.bins[b];
        out[x] = frame.floor + m / 255 * scale;
    }
    return out;
}

function updateRange(levels) {
    if (!spec.autoRange) { spec.lo = -125; spec.hi = -25; return; }
    const sorted = Array.from(levels).sort((a, b) => a - b);
    const floor = sorted[Math.floor(sorted.length * 0.2)] - 6;
    const top = Math.max(floor + 35, sorted[sorted.length - 1] + 6);
    // Smoothly, so the picture doesn't jump with every frame
    spec.lo += (floor - spec.lo) * 0.15;
    spec.hi += (top - spec.hi) * 0.15;
}

onSocket('spectrum', (frame) => {
    spec.frame = frame;
    if (activeTab !== 'radio') return;
    const wf = document.getElementById('waterfall');
    const {w} = sizeCanvas(wf, wf.clientHeight || 240);
    const view = viewWindow(frame);
    const levels = columns(frame, view, w);
    updateRange(levels);
    drawWaterfallRow(levels);
    drawSpectrum(levels, view);
});

function drawWaterfallRow(levels) {
    const canvas = document.getElementById('waterfall');
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    const palette = (app.settings && app.settings.waterfall_palette) || 'classic';
    if (spec.palette !== palette) buildLut(palette);
    const dpr = window.devicePixelRatio || 1;
    const rowHeight = Math.max(1, Math.round(dpr));
    ctx.drawImage(canvas, 0, 0, w, h - rowHeight, 0, rowHeight, w, h - rowHeight);
    const row = ctx.createImageData(w, rowHeight);
    const range = (spec.hi - spec.lo) || 1;
    for (let x = 0; x < w; x++) {
        const v = Math.max(0, Math.min(255, Math.round((levels[x] - spec.lo) / range * 255)));
        for (let y = 0; y < rowHeight; y++) {
            const p = (y * w + x) * 4;
            row.data[p] = spec.lut[v * 3]; row.data[p + 1] = spec.lut[v * 3 + 1]; row.data[p + 2] = spec.lut[v * 3 + 2]; row.data[p + 3] = 255;
        }
    }
    ctx.putImageData(row, 0, 0);
}

function niceStep(span, count) {
    const raw = span / count;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * pow >= raw) return m * pow;
    return 10 * pow;
}
function axisLabel(hz, step) {
    const decimals = step >= 1e6 ? 0 : step >= 1e5 ? 1 : step >= 1e4 ? 2 : step >= 1e3 ? 3 : 4;
    return (hz / 1e6).toFixed(decimals);
}

function drawSpectrum(levels, view) {
    const canvas = document.getElementById('spectrum');
    const {w, h, dpr} = sizeCanvas(canvas, 130);
    const ctx = canvas.getContext('2d');
    const s = app.state || {};
    const colours = {grid: cssVar('--grid-color'), text: cssVar('--muted-text-color'), line: cssVar('--series-1'),
        primary: cssVar('--primary-color'), accent: cssVar('--accent-color'), card: cssVar('--ha-card-background')};
    ctx.clearRect(0, 0, w, h);
    const yOf = db => h - (db - spec.lo) / ((spec.hi - spec.lo) || 1) * h;
    const xOf = f => (f - view.start) / view.span * w;

    // Level grid every 10 dB
    ctx.font = `${10 * dpr}px IBM Plex Mono, monospace`;
    ctx.fillStyle = colours.text;
    ctx.strokeStyle = colours.grid;
    ctx.lineWidth = 1;
    for (let db = Math.ceil(spec.lo / 10) * 10; db < spec.hi; db += 10) {
        const y = Math.round(yOf(db)) + 0.5;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
        ctx.fillText(`${db}`, 4 * dpr, y - 2 * dpr);
    }
    // The passband of the tuned signal
    if (s.frequency) {
        let lo = s.frequency - s.bandwidth / 2, hi = s.frequency + s.bandwidth / 2;
        if (s.mode === 'usb') { lo = s.frequency; hi = s.frequency + s.bandwidth; }
        if (s.mode === 'lsb') { lo = s.frequency - s.bandwidth; hi = s.frequency; }
        ctx.fillStyle = colours.primary + '26';
        ctx.fillRect(xOf(lo), 0, Math.max(2, xOf(hi) - xOf(lo)), h);
    }
    // The trace
    ctx.beginPath();
    for (let x = 0; x < levels.length; x++) {
        const y = yOf(levels[x]);
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = colours.line;
    ctx.lineWidth = 1.2 * dpr;
    ctx.stroke();
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fillStyle = colours.line + '2e';
    ctx.fill();
    // Stored channels in view
    drawChannelLabels(ctx, view, w, dpr, xOf, colours);
    // The tuned frequency
    if (s.frequency) {
        const x = Math.round(xOf(s.frequency)) + 0.5;
        ctx.strokeStyle = colours.accent;
        ctx.lineWidth = 1.5 * dpr;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    // The frequency under the pointer
    if (spec.hoverX !== null) {
        const x = spec.hoverX * dpr;
        const f = view.start + x / w * view.span;
        ctx.strokeStyle = colours.text;
        ctx.setLineDash([3 * dpr, 3 * dpr]);
        ctx.beginPath(); ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, h); ctx.stroke();
        ctx.setLineDash([]);
        const label = fmtMHz(snapToStep(f));
        const tw = ctx.measureText(label).width + 8 * dpr;
        const tx = Math.min(w - tw, x + 6 * dpr);
        ctx.fillStyle = colours.card;
        ctx.fillRect(tx, h - 18 * dpr, tw, 15 * dpr);
        ctx.fillStyle = colours.text;
        ctx.fillText(label, tx + 4 * dpr, h - 7 * dpr);
    }
    renderAxis(view);
}

function drawChannelLabels(ctx, view, w, dpr, xOf, colours) {
    const inView = app.channels.filter(c => c.frequency >= view.start && c.frequency <= view.start + view.span);
    const rows = [-1e9, -1e9, -1e9];
    ctx.font = `${10.5 * dpr}px IBM Plex Sans, sans-serif`;
    inView.sort((a, b) => a.frequency - b.frequency).forEach(c => {
        const x = xOf(c.frequency);
        const text = c.name || `M${chNum(c.number)}`;
        const tw = ctx.measureText(text).width + 8 * dpr;
        const row = rows.findIndex(end => end < x - tw / 2);
        if (row < 0) return;
        rows[row] = x + tw / 2 + 4 * dpr;
        const y = (4 + row * 17) * dpr;
        const current = app.state && app.state.channel && app.state.channel.id === c.id;
        ctx.fillStyle = current ? colours.accent : colours.card;
        ctx.globalAlpha = current ? 1 : 0.85;
        ctx.fillRect(x - tw / 2, y, tw, 15 * dpr);
        ctx.globalAlpha = 1;
        ctx.fillStyle = current ? '#fff' : colours.primary;
        ctx.fillText(text, x - tw / 2 + 4 * dpr, y + 11 * dpr);
    });
}

function renderAxis(view) {
    const el = document.getElementById('freq-axis');
    const step = niceStep(view.span, Math.max(3, Math.floor(el.clientWidth / 90)));
    const first = Math.ceil(view.start / step) * step;
    const tuned = app.state && app.state.frequency;
    let html = '';
    for (let f = first; f <= view.start + view.span; f += step) {
        const left = (f - view.start) / view.span * 100;
        if (left < 3 || left > 97) continue;
        html += `<span style="left:${left.toFixed(2)}%"${tuned && Math.abs(f - tuned) < step / 2 ? ' class="tuned"' : ''}>${axisLabel(f, step)}</span>`;
    }
    el.innerHTML = html;
}

function redrawSpectrum() {
    if (spec.frame && activeTab === 'radio') {
        const view = viewWindow(spec.frame);
        const canvas = document.getElementById('spectrum');
        const {w} = sizeCanvas(canvas, 130);
        drawSpectrum(columns(spec.frame, view, w), view);
    }
    drawScale();
}

function snapToStep(f) {
    const step = (app.state && app.state.step) || 1000;
    return Math.round(f / step) * step;
}

function initSpectrum() {
    buildChips(document.getElementById('zoom'), ZOOMS, spec.zoom, (key) => {
        spec.zoom = key;
        lsSet('zoom', key);
        clearWaterfall();
        redrawSpectrum();
    });
    renderAutoRange();
    const wrap = document.getElementById('spectrum-wrap');
    const freqAt = (clientX) => {
        const r = wrap.getBoundingClientRect();
        const view = spec.frame ? viewWindow(spec.frame) : null;
        return view ? view.start + (clientX - r.left) / r.width * view.span : null;
    };
    wrap.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        wrap.setPointerCapture(e.pointerId);
        spec.dragFrom = {x: e.clientX, frequency: app.state && app.state.frequency};
        spec.dragging = false;
    });
    wrap.addEventListener('pointermove', (e) => {
        const r = wrap.getBoundingClientRect();
        spec.hoverX = e.clientX - r.left;
        if (spec.dragFrom && Math.abs(e.clientX - spec.dragFrom.x) > 4) spec.dragging = true;
        if (!spec.frame) return;
        if (spec.dragging) {
            const view = viewWindow(spec.frame);
            const shift = (e.clientX - spec.dragFrom.x) / r.width * view.span;
            const target = snapToStep(spec.dragFrom.frequency - shift);
            if (target !== (app.state && app.state.frequency)) throttledTune(target);
        }
        if (!spec.frame) return;
        redrawSpectrum();
    });
    wrap.addEventListener('pointerleave', () => { spec.hoverX = null; redrawSpectrum(); });
    wrap.addEventListener('pointerup', (e) => {
        if (spec.dragFrom && !spec.dragging) {
            const f = freqAt(e.clientX);
            if (f !== null) radioCommand('tune', {frequency: snapToStep(f)});
        }
        spec.dragFrom = null;
        spec.dragging = false;
    });
    wrap.addEventListener('wheel', (e) => { e.preventDefault(); radioCommand('step_tune', {steps: e.deltaY < 0 ? 1 : -1}); }, {passive: false});
    initScale();
    window.addEventListener('resize', () => { clearWaterfall(); redrawSpectrum(); });
}

let tuneTimer = null, tuneTarget = null;
function throttledTune(frequency) {
    tuneTarget = frequency;
    if (tuneTimer) return;
    tuneTimer = setTimeout(() => {
        tuneTimer = null;
        radioCommand('tune', {frequency: tuneTarget});
    }, 60);
}

function clearWaterfall() {
    const canvas = document.getElementById('waterfall');
    canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
}

function toggleAutoRange() {
    spec.autoRange = !spec.autoRange;
    lsSet('auto_range', spec.autoRange);
    renderAutoRange();
}
function renderAutoRange() {
    document.getElementById('auto-range').classList.toggle('active', spec.autoRange);
}

// =====================================================================
// The band scale: the whole band of the tuned frequency, like the dial of an old radio
// =====================================================================
function scaleRange() {
    const s = app.state || {};
    const band = app.bands.find(b => b.name === s.band);
    if (band) return {start: band.start, stop: band.stop, name: band.name};
    const f = s.frequency || 100e6;
    return {start: f - 1e6, stop: f + 1e6, name: ''};
}

function drawScale() {
    const canvas = document.getElementById('scale');
    if (!canvas || !canvas.clientWidth) return;
    const {w, h, dpr} = sizeCanvas(canvas, 44);
    const ctx = canvas.getContext('2d');
    const s = app.state || {};
    const {start, stop, name} = scaleRange();
    const span = (stop - start) || 1;
    const pad = 14 * dpr;
    const xOf = f => pad + (f - start) / span * (w - 2 * pad);
    ctx.clearRect(0, 0, w, h);
    const text = cssVar('--secondary-text-color'), minor = cssVar('--divider-color'), major = cssVar('--muted-text-color');
    const step = niceStep(span, Math.max(4, Math.floor(w / dpr / 110)));
    const sub = step / 5;
    ctx.lineWidth = 1;
    for (let f = Math.ceil(start / sub) * sub; f <= stop + 1; f += sub) {
        const x = Math.round(xOf(f)) + 0.5;
        const big = Math.abs(f / step - Math.round(f / step)) < 1e-6;
        ctx.strokeStyle = big ? major : minor;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, (big ? 16 : 9) * dpr); ctx.stroke();
        if (big) {
            ctx.fillStyle = text;
            ctx.font = `${11 * dpr}px IBM Plex Mono, monospace`;
            const label = axisLabel(f, step);
            const lw = ctx.measureText(label).width;
            ctx.fillText(label, Math.max(2 * dpr, Math.min(w - lw - 2 * dpr, x - lw / 2)), 29 * dpr);
        }
    }
    // Stored channels as small marks along the bottom
    ctx.fillStyle = cssVar('--accent-color');
    app.channels.forEach(c => {
        if (c.frequency < start || c.frequency > stop) return;
        const x = xOf(c.frequency);
        ctx.fillRect(x - 1 * dpr, h - 7 * dpr, 2 * dpr, 5 * dpr);
    });
    if (name) {
        ctx.fillStyle = major;
        ctx.font = `${10.5 * dpr}px IBM Plex Sans, sans-serif`;
        ctx.fillText(name, pad, h - 3 * dpr);
    }
    // The pointer
    if (s.frequency >= start && s.frequency <= stop) {
        const x = xOf(s.frequency);
        ctx.fillStyle = cssVar('--danger-color');
        ctx.shadowColor = ctx.fillStyle;
        ctx.shadowBlur = 6 * dpr;
        ctx.fillRect(x - 1 * dpr, 0, 2 * dpr, h);
        ctx.shadowBlur = 0;
    }
}

function initScale() {
    const canvas = document.getElementById('scale');
    let down = false;
    const tuneAt = (e) => {
        const r = canvas.getBoundingClientRect();
        const pad = 14;
        const {start, stop} = scaleRange();
        const f = start + Math.max(0, Math.min(1, (e.clientX - r.left - pad) / (r.width - 2 * pad))) * (stop - start);
        throttledTune(snapToStep(f));
    };
    canvas.addEventListener('pointerdown', (e) => { canvas.setPointerCapture(e.pointerId); down = true; tuneAt(e); });
    canvas.addEventListener('pointermove', (e) => { if (down) tuneAt(e); });
    canvas.addEventListener('pointerup', () => { down = false; });
    canvas.addEventListener('pointercancel', () => { down = false; });
    canvas.addEventListener('wheel', (e) => { e.preventDefault(); radioCommand('step_tune', {steps: e.deltaY < 0 ? 1 : -1}); }, {passive: false});
}
