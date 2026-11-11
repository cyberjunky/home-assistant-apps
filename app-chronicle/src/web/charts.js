// Chronicle dashboard - charts. Time charts are drawn with uPlot (vendor/uplot), the state timeline as
// SVG. Every chart follows its container's size, zooms by dragging, has a tooltip, a legend that
// hides and shows a series, and a toolbar: copy as image, download as PNG, copy the data as CSV,
// full screen, and reset zoom while zoomed.

const SVG_NS = 'http://www.w3.org/2000/svg';
const CHART_FONT = '10.5px "IBM Plex Mono", ui-monospace, monospace';

// ---------------------------------------------------------------------
// Tooltip: one element for every chart. Labels go in as text, never as HTML.
// ---------------------------------------------------------------------
let tipEl = null;

function showTip(x, y, title, rows) {
    if (!tipEl) {
        tipEl = document.createElement('div');
        tipEl.className = 'chart-tip';
        document.body.appendChild(tipEl);
    }
    tipEl.textContent = '';
    const head = document.createElement('div');
    head.className = 'tip-title';
    head.textContent = title;
    tipEl.appendChild(head);
    rows.forEach(row => {
        const line = document.createElement('div');
        line.className = 'tip-row';
        if (row.color) {
            const key = document.createElement('i');
            key.className = 'key';
            key.style.background = row.color;
            line.appendChild(key);
        }
        const value = document.createElement('b');
        value.textContent = row.value;
        line.appendChild(value);
        if (row.name) {
            const name = document.createElement('span');
            name.textContent = row.name;
            line.appendChild(name);
        }
        tipEl.appendChild(line);
    });
    tipEl.style.display = 'block';
    const box = tipEl.getBoundingClientRect();
    const left = x + 16 + box.width > window.innerWidth ? x - box.width - 16 : x + 16;
    tipEl.style.left = Math.max(4, left) + 'px';
    tipEl.style.top = Math.max(4, Math.min(window.innerHeight - box.height - 4, y - box.height / 2)) + 'px';
}

function hideTip() {
    if (tipEl) tipEl.style.display = 'none';
}

function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    for (const key in attrs) el.setAttribute(key, attrs[key]);
    return el;
}

// ---------------------------------------------------------------------
// Formatting for axes and tooltips
// ---------------------------------------------------------------------
function axisNumber(value) {
    if (missing(value)) return '';
    const a = Math.abs(value);
    if (a >= 1e6) return num(value / 1e6, a >= 1e7 ? 0 : 1) + 'M';
    if (a >= 1e4) return num(value / 1e3, a >= 1e5 ? 0 : 1) + 'K';
    return fmtValue(value);
}

// The time labels of an axis: hours and minutes within a day (with the date at midnight), the
// date within a few months, the month after that. Always local time, the browser's own format.
function timeAxisValues(_u, splits, _axisIdx, _space, incr) {
    return splits.map(t => {
        const d = new Date(t * 1000);
        if (incr < 60) return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
        if (incr < 86400) {
            const midnight = d.getHours() === 0 && d.getMinutes() === 0;
            return midnight ? fmtShortDate(t) : d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        }
        if (incr < 28 * 86400) return fmtShortDate(t);
        return d.toLocaleDateString([], {month: 'short', year: '2-digit'});
    });
}

// A time as the tooltip title: with seconds when the chart spans less than a day
function tipTime(t, span) {
    if (span < 86400) return `${fmtDay(t)} ${fmtTimeSec(t)}`;
    return fmtDateTime(t);
}

// ---------------------------------------------------------------------
// Registry: charts are redrawn when the theme changes; each follows its container's size
// ---------------------------------------------------------------------
const chartRegistry = new Set();

function redrawCharts() {
    chartRegistry.forEach(el => {
        if (!el.isConnected) {
            destroyChart(el);
            chartRegistry.delete(el);
        } else if (el._build) {
            el._build();
        }
    });
}

function destroyChart(el) {
    if (el._u) { el._u.destroy(); el._u = null; }
    if (el._observer) { el._observer.disconnect(); el._observer = null; }
}

// "Not enough data yet" and the like, in place of a chart
function emptyChart(el, text) {
    destroyChart(el);
    el._build = null;
    chartRegistry.delete(el);
    el.classList.remove('has-chart');
    if (el._tools) { el._tools.remove(); el._tools = null; }
    el.innerHTML = `<div class="empty-state chart-empty">${escapeHtml(text)}</div>`;
}

// The chart shell: a toolbar row above the plot, and the legend below it
function chartShell(el, buttons) {
    el.classList.add('has-chart');
    el.innerHTML = `
        <div class="chart-tools" role="toolbar" aria-label="Chart">
            <button type="button" class="chart-tool" data-tool="reset" title="Reset zoom" hidden>${ICONS.reset}<span>Reset zoom</span></button>
            ${buttons.copyImage ? `<button type="button" class="chart-tool" data-tool="copy-image" title="Copy as image">${ICONS.copy}</button>` : ''}
            ${buttons.png ? `<button type="button" class="chart-tool" data-tool="png" title="Download as PNG">${ICONS.download}</button>` : ''}
            ${buttons.csv ? `<button type="button" class="chart-tool" data-tool="csv" title="Copy the data as CSV">${ICONS.csv}</button>` : ''}
            <button type="button" class="chart-tool" data-tool="full" title="Full screen">${ICONS.expand}</button>
        </div>
        <div class="chart-plot"></div>
        <div class="legend chart-legend"></div>`;
    // In a panel with a title row, the toolbar goes at the end of that row
    const tools = el.querySelector('.chart-tools');
    const title = el.closest('.panel-body') && el.closest('.panel-body').querySelector(':scope > .chart-title');
    if (title) {
        title.querySelectorAll('.chart-tools').forEach(t => t.remove());
        tools.classList.add('in-title');
        title.appendChild(tools);
    }
    return {plot: el.querySelector('.chart-plot'), legend: el.querySelector('.chart-legend'), tools};
}

const ICONS = {
    reset: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
    download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/></svg>',
    csv: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M4 15h16M10 4v16"/></svg>',
    expand: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
};

// Toolbar actions shared by every chart. opts: {png: () => Promise<Blob>, csv: () => string,
// filename, onReset}
function wireTools(el, tools, opts) {
    el._tools = tools;
    tools.onclick = async (e) => {
        const button = e.target.closest('[data-tool]');
        if (!button) return;
        const tool = button.dataset.tool;
        if (tool === 'reset') opts.onReset();
        else if (tool === 'full') toggleFullscreen(el);
        else if (tool === 'png') downloadBlob(await opts.png(), `${opts.filename}.png`);
        else if (tool === 'copy-image') copyImage(await opts.png(), opts.filename);
        else if (tool === 'csv') copyText(opts.csv(), `${opts.filename}.csv`);
    };
}

function setZoomed(el, zoomed) {
    const reset = el._tools && el._tools.querySelector('[data-tool="reset"]');
    if (reset) reset.hidden = !zoomed;
}

// Full screen as an overlay inside the page: the Fullscreen API isn't allowed in Home
// Assistant's ingress frame. Esc or the button closes it.
function toggleFullscreen(el) {
    const panel = el.closest('.panel') || el;
    const on = !panel.classList.contains('chart-fullscreen');
    document.querySelectorAll('.chart-fullscreen').forEach(p => p.classList.remove('chart-fullscreen'));
    panel.classList.toggle('chart-fullscreen', on);
    document.body.classList.toggle('has-fullscreen-chart', on);
    hideTip();
}
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.querySelector('.chart-fullscreen')) {
        document.querySelectorAll('.chart-fullscreen').forEach(p => p.classList.remove('chart-fullscreen'));
        document.body.classList.remove('has-fullscreen-chart');
    }
});

function downloadBlob(blob, filename) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// The clipboard needs permission that Home Assistant's frame may not give: then the image is
// downloaded instead, and a toast says so
async function copyImage(blob, filename) {
    try {
        if (!navigator.clipboard || !window.ClipboardItem) throw new Error('no clipboard');
        await navigator.clipboard.write([new ClipboardItem({'image/png': blob})]);
        toast('Chart copied as an image', 'success');
    } catch (e) {
        downloadBlob(blob, `${filename}.png`);
        toast('This browser doesn\'t allow copying images here: downloaded as PNG instead');
    }
}

async function copyText(text, filename) {
    try {
        if (!navigator.clipboard) throw new Error('no clipboard');
        await navigator.clipboard.writeText(text);
        toast('Data copied as CSV', 'success');
        return;
    } catch (e) { /* try the older way */ }
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    area.remove();
    if (ok) {
        toast('Data copied as CSV', 'success');
    } else {
        downloadBlob(new Blob([text], {type: 'text/csv'}), filename);
        toast('This browser doesn\'t allow copying here: downloaded as CSV instead');
    }
}

function csvCell(value) {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function csvFrom(header, rows) {
    return [header, ...rows].map(r => r.map(csvCell).join(',')).join('\n') + '\n';
}

const isoTime = t => new Date(t * 1000).toISOString();

// A PNG of a chart: title, the plot and its legend, on the card's background
async function chartImage(el, title, legendItems, draw) {
    const ratio = window.devicePixelRatio || 1;
    const plot = el.querySelector('.chart-plot');
    const width = plot.clientWidth;
    const height = plot.clientHeight;
    const pad = 16, head = title ? 26 : 0, foot = legendItems.length ? 26 : 0;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round((width + pad * 2) * ratio);
    canvas.height = Math.round((height + head + foot + pad * 2) * ratio);
    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio);
    ctx.fillStyle = cssVar('--ha-card-background');
    ctx.fillRect(0, 0, width + pad * 2, height + head + foot + pad * 2);
    if (title) {
        ctx.fillStyle = cssVar('--primary-text-color');
        ctx.font = '600 13px "IBM Plex Sans", sans-serif';
        ctx.fillText(title, pad, pad + 14);
    }
    await draw(ctx, pad, pad + head, width, height);
    let x = pad;
    ctx.font = '12px "IBM Plex Sans", sans-serif';
    legendItems.forEach(item => {
        ctx.fillStyle = item.color;
        ctx.fillRect(x, pad + head + height + 12, 12, 3);
        ctx.fillStyle = cssVar('--secondary-text-color');
        ctx.fillText(item.name, x + 18, pad + head + height + 18);
        x += 30 + ctx.measureText(item.name).width;
    });
    return new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
}

// ---------------------------------------------------------------------
// Time chart (uPlot).
// cfg: {
//   x: [t, ...] epoch seconds,
//   series: [{name, color, values: [n|null|undefined], unit, kind: 'line'|'step'|'bars',
//             band: {lo: [...], hi: [...]}, width, dash}],
//   xRange: [from, to]   the full x range (the data may start later)
//   height, title, filename, empty,
//   onZoom(from, to)     zooming asks for new data instead of zooming the drawn data
//   zoomed               the chart already shows a zoomed window (shows Reset zoom)
//   onReset()            with onZoom: back to the whole range
//   csv()                the CSV of what is shown (defaults to the series)
//   barSize              bars: width in px at most
//   format(v)            a value as text in the tooltip (default: the number and its unit)
//   formatAxis(v)        a value as text on the y axis
//   tipTitle(t)          the tooltip's title for a time
//   extraRows(idx)       more tooltip rows
//   gapText              shown in the tooltip for a gap (null), e.g. "unavailable"
//   aligned              the series were aligned on one time axis (compare): the tooltip shows
//                        each series' value at or just before the cursor
// }
// null in values is a gap; undefined (from aligning several series) is skipped and joined.
// ---------------------------------------------------------------------
function timeChart(el, cfg) {
    const hasData = cfg.series.some(s => s.values.some(v => !missing(v)));
    if (!cfg.x.length || !hasData) return emptyChart(el, cfg.empty || 'Not enough data yet');
    chartRegistry.add(el);
    el._build = () => buildTimeChart(el, cfg);
    el._build();
}

function buildTimeChart(el, cfg) {
    destroyChart(el);
    const {plot, legend, tools} = chartShell(el, {copyImage: true, png: true, csv: true});
    const height = cfg.height || 240;
    const grid = cssVar('--grid-color');
    const muted = cssVar('--muted-text-color');
    const hidden = el._hidden || new Set();
    el._hidden = hidden;

    // One y scale per unit, at most two: the first on the left, the second on the right
    const units = [];
    cfg.series.forEach(s => { const u = s.unit || ''; if (!units.includes(u)) units.push(u); });
    const scaleOf = s => `y${units.indexOf(s.unit || '')}`;

    const data = [cfg.x];
    const useries = [{}];
    const bands = [];
    const owner = [];  // uPlot series index -> cfg series index (null for band edges)
    const few = cfg.x.length <= 60;
    cfg.series.forEach((s, i) => {
        if (s.band) {
            data.push(s.band.hi, s.band.lo);
            const edge = {scale: scaleOf(s), stroke: 'transparent', width: 0, points: {show: false}, show: !hidden.has(i)};
            useries.push({...edge, label: `${s.name} max`}, {...edge, label: `${s.name} min`});
            owner.push(null, null);
            bands.push({series: [useries.length - 2, useries.length - 1], fill: colorAlpha(s.color, 0.16)});
        }
        data.push(s.values);
        const kind = s.kind || 'line';
        const single = s.values.filter(v => !missing(v)).length <= 1;
        useries.push({
            label: s.name,
            scale: scaleOf(s),
            stroke: s.color,
            width: s.width || (kind === 'bars' ? 0 : 1.8),
            fill: kind === 'bars' ? colorAlpha(s.color, 0.85) : undefined,
            dash: s.dash,
            spanGaps: false,
            show: !hidden.has(i),
            paths: kind === 'bars' ? uPlot.paths.bars({size: [0.7, cfg.barSize || 28], gap: 1})
                : kind === 'step' ? uPlot.paths.stepped({align: 1}) : undefined,
            points: {show: kind !== 'bars' && (few || single), size: single ? 7 : 4, fill: s.color, stroke: s.color},
        });
        owner.push(i);
    });

    const yScales = {};
    units.forEach((u, k) => {
        const values = cfg.series.filter(s => (s.unit || '') === u).flatMap(s => [
            ...s.values, ...(s.band ? s.band.lo.concat(s.band.hi) : []),
        ]).filter(v => !missing(v));
        const bars = cfg.series.some(s => (s.unit || '') === u && s.kind === 'bars');
        yScales[`y${k}`] = {
            range: (_u, min, max) => {
                if (missing(min) || missing(max)) return [0, 1];
                let lo = min, hi = max;
                if (bars || (lo >= 0 && lo < hi * 0.25)) lo = Math.min(0, lo);
                if (hi === lo) { hi += Math.abs(hi) * 0.1 || 1; lo -= bars ? 0 : Math.abs(lo) * 0.1 || 1; }
                const padY = (hi - lo) * 0.08;
                return [lo === 0 ? 0 : lo - padY, hi + padY];
            },
        };
        if (!values.length) yScales[`y${k}`].range = [0, 1];
    });
    const axes = [{
        stroke: muted, font: CHART_FONT, grid: {stroke: grid, width: 1}, ticks: {stroke: grid, width: 1, size: 4},
        values: timeAxisValues, space: 70,
    }];
    units.forEach((u, k) => {
        axes.push({
            scale: `y${k}`, side: k === 0 ? 3 : 1, stroke: muted, font: CHART_FONT,
            grid: {stroke: grid, width: 1, show: k === 0}, ticks: {show: false},
            values: (_u, splits) => splits.map(cfg.formatAxis || axisNumber),
            size: (_u, values) => Math.max(36, 10 + Math.max(...(values || ['']).map(v => String(v).length)) * 6.5),
            label: u || undefined, labelFont: CHART_FONT, labelSize: u ? 16 : 0, labelGap: 0,
        });
    });

    const span = cfg.xRange ? cfg.xRange[1] - cfg.xRange[0] : cfg.x[cfg.x.length - 1] - cfg.x[0];
    const full = cfg.xRange || [cfg.x[0], cfg.x[cfg.x.length - 1]];
    const xScale = {time: true};
    if (full[1] > full[0]) { xScale.min = full[0]; xScale.max = full[1]; }
    else { xScale.min = full[0] - 1800; xScale.max = full[0] + 1800; }  // one moment: an hour around it

    const opts = {
        width: Math.max(200, plot.clientWidth || el.clientWidth || 600),
        height,
        legend: {show: false},
        padding: [10, units.length > 1 ? 4 : 14, 0, 4],
        scales: {x: xScale, ...yScales},
        axes,
        series: useries,
        bands,
        cursor: {
            drag: {x: true, y: false, setScale: !cfg.onZoom},
            points: {size: 7, width: 2},
            focus: {prox: 24},
        },
        focus: {alpha: 0.4},
        hooks: {
            setCursor: [u => {
                const idx = u.cursor.idx;
                if (idx === null || idx === undefined || u.cursor.left < 0) return hideTip();
                const rows = [];
                cfg.series.forEach((s, i) => {
                    if (hidden.has(i)) return;
                    const v = cfg.aligned ? nearestValue(s.values, idx) : s.values[idx];
                    if (v === undefined || (v === null && !cfg.gapText)) return;
                    const range = s.band && !missing(s.band.lo[idx]) ? ` (${fmtValue(s.band.lo[idx])} – ${fmtValue(s.band.hi[idx])})` : '';
                    rows.push({
                        color: s.color,
                        value: v === null ? cfg.gapText
                            : cfg.format ? cfg.format(v) : `${fmtValue(v)}${s.unit ? ' ' + s.unit : ''}${range}`,
                        name: cfg.series.length > 1 || s.band ? s.name : '',
                    });
                });
                if (cfg.extraRows) rows.push(...cfg.extraRows(idx));
                const box = u.over.getBoundingClientRect();
                showTip(box.left + u.cursor.left, box.top + u.cursor.top, cfg.tipTitle ? cfg.tipTitle(cfg.x[idx]) : tipTime(cfg.x[idx], span), rows);
            }],
            setSelect: [u => {
                if (u.select.width < 4) return;
                const from = u.posToVal(u.select.left, 'x');
                const to = u.posToVal(u.select.left + u.select.width, 'x');
                if (cfg.onZoom) {
                    u.setSelect({left: 0, top: 0, width: 0, height: 0}, false);
                    cfg.onZoom(from, to);
                } else {
                    setZoomed(el, true);
                }
            }],
        },
    };
    const u = new uPlot(opts, data, plot);
    el._u = u;
    u.over.addEventListener('mouseleave', hideTip);
    // Double click resets the zoom, like the button
    u.over.addEventListener('dblclick', () => resetZoom());

    function resetZoom() {
        if (cfg.onZoom) { if (cfg.zoomed && cfg.onReset) cfg.onReset(); return; }
        u.setScale('x', {min: xScale.min, max: xScale.max});
        setZoomed(el, false);
    }

    // Legend: a click hides or shows a series (and its band)
    const legendItems = cfg.series.map((s, i) => ({name: s.name + (s.unit && units.length > 1 ? ` (${s.unit})` : ''), color: s.color, i, band: Boolean(s.band), kind: s.kind}));
    legend.innerHTML = cfg.series.length > 1 || cfg.series.some(s => s.band) ? legendItems.map(it =>
        `<button type="button" class="legend-item${hidden.has(it.i) ? ' off' : ''}" data-series="${it.i}"><i class="${it.kind === 'bars' ? '' : 'line'}" style="background:${it.color}"></i>${escapeHtml(it.name)}${it.band ? '<i class="band" style="background:' + it.color + '"></i><small>min – max</small>' : ''}</button>`).join('') : '';
    legend.onclick = (e) => {
        const item = e.target.closest('[data-series]');
        if (!item) return;
        const i = Number(item.dataset.series);
        if (hidden.has(i)) hidden.delete(i); else hidden.add(i);
        if (cfg.series.filter((_, k) => !hidden.has(k)).length === 0) hidden.delete(i);  // keep one
        owner.forEach((o, k) => {
            const show = o === null ? !hidden.has(owner[k + (owner[k + 1] === null ? 2 : 1)]) : !hidden.has(o);
            u.setSeries(k + 1, {show});
        });
        item.classList.toggle('off', hidden.has(i));
    };

    wireTools(el, tools, {
        filename: cfg.filename || 'chronicle-chart',
        onReset: resetZoom,
        png: () => chartImage(el, cfg.title, legendItems.filter(it => !hidden.has(it.i)), (ctx, x, y, w, h) => ctx.drawImage(u.ctx.canvas, x, y, w, h)),
        csv: () => (cfg.csv ? cfg.csv(u.scales.x.min, u.scales.x.max) : seriesCsv(cfg, u.scales.x.min, u.scales.x.max)),
    });
    setZoomed(el, Boolean(cfg.zoomed));

    // Follow the container: also when it was drawn in a hidden tab (width 0) and shows later
    el._observer = new ResizeObserver(() => {
        const width = plot.clientWidth;
        const fullHeight = el.closest('.chart-fullscreen') ? Math.max(240, window.innerHeight - 170) : height;
        if (width > 0 && (width !== u.width || fullHeight !== u.height)) u.setSize({width, height: fullHeight});
    });
    el._observer.observe(plot);
    el._observer.observe(document.body);
}

// A value at idx, or (for series aligned with others) the nearest one before it within 2 slots
function nearestValue(values, idx) {
    const v = values[idx];
    if (v !== undefined) return v;
    for (let k = idx - 1; k >= Math.max(0, idx - 2); k--) if (values[k] !== undefined) return values[k];
    return undefined;
}

function seriesCsv(cfg, from, to) {
    const header = ['time', ...cfg.series.map(s => s.unit ? `${s.name} (${s.unit})` : s.name)];
    const rows = [];
    cfg.x.forEach((t, i) => {
        if (t < from || t > to) return;
        const values = cfg.series.map(s => s.values[i]);
        if (values.every(v => v === undefined)) return;
        rows.push([isoTime(t), ...values.map(v => (v === undefined || v === null ? '' : v))]);
    });
    return csvFrom(header, rows);
}

// A colour with transparency, from a CSS colour (hex or rgb)
function colorAlpha(color, alpha) {
    const c = color.trim();
    if (c.startsWith('#')) {
        const hex = c.length === 4 ? c.slice(1).split('').map(h => h + h).join('') : c.slice(1, 7);
        const [r, g, b] = [0, 2, 4].map(k => parseInt(hex.slice(k, k + 2), 16));
        return `rgba(${r},${g},${b},${alpha})`;
    }
    const m = c.match(/[\d.]+/g);
    return m ? `rgba(${m[0]},${m[1]},${m[2]},${alpha})` : c;
}

// ---------------------------------------------------------------------
// State timeline (SVG): one bar across the range, a coloured segment per state.
// cfg: {from, to, segments: [{from, to, state}], colorOf, height, title, filename,
//       onZoom(from, to), zoomed, onReset, csv()}
// ---------------------------------------------------------------------
function timelineChart(el, cfg) {
    if (!cfg.segments.length) return emptyChart(el, cfg.empty || 'No state recorded in this period');
    chartRegistry.add(el);
    el._build = () => buildTimeline(el, cfg);
    el._build();
}

function buildTimeline(el, cfg) {
    destroyChart(el);
    const {plot, tools} = chartShell(el, {copyImage: true, png: true, csv: true});
    const draw = () => {
        const width = Math.max(240, plot.clientWidth || el.clientWidth || 600);
        const height = cfg.height || 64;
        const pad = {left: 4, right: 4, top: 6, bottom: 20};
        const box = {x: pad.left, y: pad.top, w: width - pad.left - pad.right, h: height - pad.top - pad.bottom};
        const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', class: 'timeline-svg'});
        const x = t => box.x + (t - cfg.from) / (cfg.to - cfg.from) * box.w;
        const toTime = px => cfg.from + (px - box.x) / box.w * (cfg.to - cfg.from);
        svg.appendChild(svgEl('rect', {x: box.x, y: box.y, width: box.w, height: box.h, rx: 4, fill: cssVar('--grid-color')}));
        const background = cssVar('--ha-card-background');
        cfg.segments.forEach(seg => {
            const left = Math.max(box.x, x(seg.from)), right = Math.min(box.x + box.w, x(seg.to));
            if (right - left < 0.3) return;
            const rect = svgEl('rect', {class: 'seg', x: left, y: box.y, width: right - left, height: box.h, fill: cfg.colorOf(seg.state), stroke: background, 'stroke-width': 1});
            rect.addEventListener('pointermove', e => showTip(e.clientX, e.clientY, `${fmtDateTime(seg.from)} – ${fmtDateTime(seg.to)}`,
                [{color: cfg.colorOf(seg.state), value: seg.state || '(empty)', name: fmtDuration(seg.to - seg.from)}]));
            rect.addEventListener('pointerleave', hideTip);
            svg.appendChild(rect);
        });
        const muted = cssVar('--muted-text-color');
        timeTicks(cfg.from, cfg.to, width).forEach((tick, i, all) => {
            const label = svgEl('text', {x: x(tick.at), y: box.y + box.h + 14, fill: muted, 'font-size': 10.5, 'font-family': 'IBM Plex Mono, monospace',
                'text-anchor': i === 0 && x(tick.at) < 30 ? 'start' : i === all.length - 1 && x(tick.at) > width - 30 ? 'end' : 'middle'});
            label.textContent = tick.label;
            svg.appendChild(label);
        });
        // Drag to zoom
        const select = svgEl('rect', {y: box.y - 2, height: box.h + 4, fill: cssVar('--primary-color'), opacity: 0.18, visibility: 'hidden', 'pointer-events': 'none'});
        svg.appendChild(select);
        let dragFrom = null;
        svg.addEventListener('pointerdown', e => {
            const r = svg.getBoundingClientRect();
            dragFrom = e.clientX - r.left;
            svg.setPointerCapture(e.pointerId);
        });
        svg.addEventListener('pointermove', e => {
            if (dragFrom === null) return;
            const r = svg.getBoundingClientRect();
            const now = e.clientX - r.left;
            select.setAttribute('x', Math.min(dragFrom, now));
            select.setAttribute('width', Math.abs(now - dragFrom));
            select.setAttribute('visibility', 'visible');
            hideTip();
        });
        svg.addEventListener('pointerup', e => {
            if (dragFrom === null) return;
            const r = svg.getBoundingClientRect();
            const a = dragFrom, b = e.clientX - r.left;
            dragFrom = null;
            select.setAttribute('visibility', 'hidden');
            if (Math.abs(b - a) >= 6 && cfg.onZoom) cfg.onZoom(toTime(Math.min(a, b)), toTime(Math.max(a, b)));
        });
        svg.addEventListener('dblclick', () => { if (cfg.zoomed && cfg.onReset) cfg.onReset(); });
        plot.textContent = '';
        plot.appendChild(svg);
        return svg;
    };
    let svg = draw();
    const colours = [...new Set(cfg.segments.map(s => s.state))].map(s => ({name: s || '(empty)', color: cfg.colorOf(s)}));
    wireTools(el, tools, {
        filename: cfg.filename || 'chronicle-states',
        onReset: () => cfg.onReset && cfg.onReset(),
        png: () => chartImage(el, cfg.title, colours, (ctx, x, y, w, h) => new Promise(resolve => {
            const image = new Image();
            image.onload = () => { ctx.drawImage(image, x, y, w, h); resolve(); };
            image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(svg));
        })),
        csv: () => cfg.csv(),
    });
    setZoomed(el, Boolean(cfg.zoomed));
    let lastWidth = plot.clientWidth;
    el._observer = new ResizeObserver(() => {
        if (plot.clientWidth > 0 && plot.clientWidth !== lastWidth) {
            lastWidth = plot.clientWidth;
            svg = draw();
        }
    });
    el._observer.observe(plot);
}

// Time labels at round moments for the timeline, like the time charts
function timeTicks(from, to, width) {
    const span = to - from;
    const count = Math.max(2, Math.min(8, Math.floor((width || 600) / 110)));
    const steps = [60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 2 * 86400, 7 * 86400, 14 * 86400, 30 * 86400, 91 * 86400];
    const step = steps.find(s => span / s <= count) || 182 * 86400;
    const ticks = [];
    const offset = new Date(from * 1000).getTimezoneOffset() * 60;  // round in local time
    let t = Math.ceil((from - offset) / step) * step + offset;
    if (step >= 30 * 86400) {
        const d = new Date(from * 1000);
        d.setDate(1); d.setHours(0, 0, 0, 0); d.setMonth(d.getMonth() + 1);
        t = d.getTime() / 1000;
    }
    for (; t <= to; t = step >= 30 * 86400 ? (() => { const d = new Date(t * 1000); d.setMonth(d.getMonth() + Math.round(step / (30 * 86400))); return d.getTime() / 1000; })() : t + step) {
        ticks.push({at: t, label: timeAxisValues(null, [t], 0, 0, step)[0]});
    }
    return ticks;
}

// Colours for the states of one entity: the usual Home Assistant states keep their own, the
// others get the series colours in the order given (most time first), so none repeat early
const STATE_FIXED = {
    on: '--series-1', off: '--divider-color', home: '--series-3', not_home: '--series-4',
    unavailable: '--danger-color', unknown: '--muted-text-color', open: '--series-2', closed: '--divider-color',
    idle: '--divider-color',
};
const STATE_PALETTE = ['--series-1', '--series-2', '--series-3', '--series-4', '--series-5', '--seq-2', '--seq-4', '--seq-5'];

function stateColors(states) {
    const used = new Set(states.map(s => STATE_FIXED[s]).filter(Boolean));
    const free = STATE_PALETTE.filter(c => !used.has(c));
    const map = {};
    let next = 0;
    states.forEach(s => {
        map[s] = cssVar(STATE_FIXED[s] || free[next++ % Math.max(1, free.length)] || '--series-1');
    });
    return s => map[s] || cssVar('--muted-text-color');
}

function legendHtml(items) {
    return items.map(it => `<span><i class="${it.kind || ''}" style="background:${it.color}"></i>${escapeHtml(it.name)}</span>`).join('');
}
