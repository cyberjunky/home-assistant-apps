// Proxmox dashboard - small SVG charts: bars, signed bars, lines and a donut, with a shared tooltip.
// Charts are drawn at the pixel width of their container and redrawn when it changes.

const SVG_NS = 'http://www.w3.org/2000/svg';

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
    const left = x + 14 + box.width > window.innerWidth ? x - box.width - 14 : x + 14;
    tipEl.style.left = Math.max(4, left) + 'px';
    tipEl.style.top = Math.max(4, Math.min(window.innerHeight - box.height - 4, y - box.height / 2)) + 'px';
}

function hideTip() {
    if (tipEl) tipEl.style.display = 'none';
}

// ---------------------------------------------------------------------
// Scales
// ---------------------------------------------------------------------
function niceStep(span, ticks) {
    const raw = span / Math.max(1, ticks);
    const power = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const candidates = [1, 2, 2.5, 5, 10].map(m => m * power);
    return candidates.find(c => c >= raw) || candidates[candidates.length - 1];
}

function axis(min, max, ticks) {
    if (max <= min) max = min + 1;
    const step = niceStep(max - min, ticks);
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const values = [];
    for (let v = lo; v <= hi + step / 1000; v += step) values.push(Math.round(v * 1e6) / 1e6);
    return {lo, hi, values};
}

function tickLabel(value) {
    const abs = Math.abs(value);
    if (abs >= 10000) return (value / 1000).toLocaleString(undefined, {maximumFractionDigits: 1}) + 'K';
    if (abs < 1) return value.toLocaleString(undefined, {maximumFractionDigits: 3});
    return value.toLocaleString(undefined, {maximumFractionDigits: value % 1 ? 1 : 0});
}

function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    for (const key in attrs) el.setAttribute(key, attrs[key]);
    return el;
}

const chartRegistry = new Set();

function mountChart(el, draw) {
    el._draw = draw;
    chartRegistry.add(el);
    draw();
}

let resizeTimer = null;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redrawCharts, 150);
});

function redrawCharts() {
    chartRegistry.forEach(el => {
        if (!el.isConnected) chartRegistry.delete(el);
        else if (el.clientWidth && el._draw) el._draw();
    });
}

function emptyChart(el, text) {
    el._draw = null;
    el.innerHTML = `<div class="empty-state" style="padding:28px 20px">${escapeHtml(text)}</div>`;
}

// Shared frame: y axis with gridlines, returns the plot geometry
function chartFrame(el, height, scale, formatTick) {
    const width = Math.max(240, el.clientWidth || 600);
    const pad = {left: 42, right: 8, top: 8, bottom: 20};
    const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img'});
    const plot = {x: pad.left, y: pad.top, w: width - pad.left - pad.right, h: height - pad.top - pad.bottom};
    const y = v => plot.y + plot.h - (v - scale.lo) / (scale.hi - scale.lo) * plot.h;
    const grid = svgEl('g', {class: 'grid'});
    scale.values.forEach(v => {
        if (v !== scale.lo) grid.appendChild(svgEl('line', {x1: plot.x, x2: plot.x + plot.w, y1: y(v), y2: y(v)}));
        const label = svgEl('text', {class: 'tick', x: plot.x - 6, y: y(v) + 3, 'text-anchor': 'end'});
        label.textContent = (formatTick || tickLabel)(v);
        grid.appendChild(label);
    });
    svg.appendChild(grid);
    const zero = Math.min(scale.hi, Math.max(0, scale.lo));
    svg.appendChild(svgEl('line', {class: 'baseline', x1: plot.x, x2: plot.x + plot.w, y1: y(zero), y2: y(zero)}));
    el.textContent = '';
    el.appendChild(svg);
    return {svg, plot, y, width};
}

// ---------------------------------------------------------------------
// Bar chart. opts: {data: [{label, tipTitle?, values: [n, ...]}], series: [{name, color}],
//                   height, format: n => text, grouped: bool}
// Values are stacked per bar, or drawn side by side with grouped. Bars are capped at 24px wide.
// ---------------------------------------------------------------------
function barChart(el, opts) {
    const data = opts.data || [];
    if (!data.length || !data.some(d => d.values.some(v => v))) return emptyChart(el, opts.empty || 'No data in this period');
    mountChart(el, () => {
        const grouped = Boolean(opts.grouped);
        const maxima = data.map(d => grouped ? Math.max(...d.values.map(v => v || 0)) : d.values.reduce((a, b) => a + (b || 0), 0));
        const scale = axis(0, Math.max(...maxima), 4);
        const {svg, plot, y} = chartFrame(el, opts.height || 190, scale, opts.formatTick);
        const slot = plot.w / data.length;
        const n = grouped ? opts.series.length : 1;
        const barWidth = Math.max(2, Math.min(grouped ? 16 : 24, (slot - 2) / n - (grouped ? 1 : 0)));
        const every = Math.max(1, Math.ceil(data.length * (opts.labelWidth || 44) / plot.w));

        data.forEach((d, i) => {
            const cx = plot.x + slot * i + slot / 2;
            const group = svgEl('g', {class: 'slot', tabindex: '0'});
            group.appendChild(svgEl('rect', {x: plot.x + slot * i, y: plot.y, width: slot, height: plot.h, fill: 'transparent'}));
            const painted = d.values.map((v, s) => ({v: v || 0, s})).filter(p => p.v > 0);
            if (grouped) {
                const total = n * barWidth + (n - 1);
                d.values.forEach((v, s) => {
                    if (!(v > 0)) return;
                    const x = cx - total / 2 + s * (barWidth + 1);
                    const top = y(v), bottom = y(0);
                    const h = Math.max(1, bottom - top);
                    const r = Math.min(3, h, barWidth / 2);
                    group.appendChild(svgEl('path', {class: 'bar', d: `M${x},${bottom} v${-(h - r)} q0,${-r} ${r},${-r} h${barWidth - 2 * r} q${r},0 ${r},${r} v${h - r} z`, fill: opts.series[s].color}));
                });
            } else {
                let base = 0;
                painted.forEach((p, k) => {
                    const top = y(base + p.v), bottom = y(base);
                    const last = k === painted.length - 1;
                    const h = Math.max(1, bottom - top - (last ? 0 : 2));
                    const r = last ? Math.min(4, h, barWidth / 2) : 0;
                    const x = cx - barWidth / 2;
                    group.appendChild(svgEl('path', {class: 'bar', d: `M${x},${bottom} v${-(h - r)} q0,${-r} ${r},${-r} h${barWidth - 2 * r} q${r},0 ${r},${r} v${h - r} z`, fill: opts.series[p.s].color}));
                    base += p.v;
                });
            }
            if (i % every === 0) {
                const label = svgEl('text', {class: 'tick', x: cx, y: plot.y + plot.h + 14, 'text-anchor': 'middle'});
                label.textContent = d.label;
                group.appendChild(label);
            }
            const tip = (e) => {
                const box = group.getBoundingClientRect();
                const rows = d.values.map((v, s) => ({color: opts.series.length > 1 ? opts.series[s].color : null, value: opts.format(v || 0), name: opts.series.length > 1 ? opts.series[s].name : ''}));
                if (!grouped && painted.length > 1) rows.push({value: opts.format(maxima[i]), name: 'total'});
                showTip(e && e.clientX ? e.clientX : box.left + box.width / 2, e && e.clientY ? e.clientY : box.top + 20, d.tipTitle || d.label, rows);
            };
            group.addEventListener('pointermove', tip);
            group.addEventListener('focus', () => tip(null));
            group.addEventListener('pointerleave', hideTip);
            group.addEventListener('blur', hideTip);
            if (d.onClick) {
                group.style.cursor = 'pointer';
                group.addEventListener('click', d.onClick);
            }
            svg.appendChild(group);
        });
    });
}

// ---------------------------------------------------------------------
// Line chart. opts: {x: [n, ...], series: [{name, color, values: [n|null, ...], area?, dash?, width?}],
//                    height, formatX: n => text, formatY: (n, seriesIndex) => text, xTicks: [{at, label}],
//                    zero: bool, bands: [{from, to, color}], markers: [{at, label}], hlines: [{at, color, label}],
//                    min?, max?}
// One y axis for all series: only plot series together that share a unit.
// ---------------------------------------------------------------------
function lineChart(el, opts) {
    const xs = opts.x || [];
    const all = opts.series.flatMap(s => s.values.filter(v => !missing(v)));
    if (xs.length < 2 || !all.length) return emptyChart(el, opts.empty || 'No data');
    mountChart(el, () => {
        let lo = Math.min(...all), hi = Math.max(...all);
        if (opts.zero || (lo > 0 && lo < hi * 0.25)) lo = Math.min(0, lo);
        if (!missing(opts.min)) lo = Math.min(lo, opts.min);
        if (!missing(opts.max)) hi = Math.max(hi, opts.max);
        (opts.hlines || []).forEach(h => { lo = Math.min(lo, h.at); hi = Math.max(hi, h.at); });
        if (hi === lo) hi = lo + 1;
        const scale = axis(lo, hi, 4);
        const {svg, plot, y} = chartFrame(el, opts.height || 170, scale, opts.formatTick);
        const x0 = xs[0], x1 = xs[xs.length - 1];
        const x = v => plot.x + (x1 === x0 ? 0 : (v - x0) / (x1 - x0)) * plot.w;
        const clampX = v => Math.max(plot.x, Math.min(plot.x + plot.w, x(v)));

        (opts.bands || []).forEach(band => {
            const left = clampX(band.from), right = clampX(band.to);
            if (right - left < 0.5) return;
            svg.insertBefore(svgEl('rect', {x: left, y: plot.y, width: right - left, height: plot.h, fill: band.color, opacity: 0.16}), svg.firstChild);
        });

        (opts.xTicks || []).forEach(tick => {
            const label = svgEl('text', {class: 'tick', x: Math.max(plot.x + 14, Math.min(plot.x + plot.w - 14, x(tick.at))), y: plot.y + plot.h + 14, 'text-anchor': 'middle'});
            label.textContent = tick.label;
            svg.appendChild(label);
        });

        opts.series.forEach(series => {
            let d = '', pen = false, first = null, last = null;
            series.values.forEach((v, i) => {
                if (missing(v)) { pen = false; return; }
                d += `${pen ? 'L' : 'M'}${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`;
                pen = true;
                if (first === null) first = i;
                last = i;
            });
            if (!d) return;
            if ((series.area || opts.area) && first !== null) {
                const floor = y(Math.max(scale.lo, Math.min(0, scale.hi)));
                // The area is built per continuous run so gaps stay gaps
                let area = '', runStart = null;
                series.values.forEach((v, i) => {
                    if (missing(v)) {
                        if (runStart !== null) { area += `L${x(xs[i - 1]).toFixed(1)},${floor}Z`; runStart = null; }
                        return;
                    }
                    if (runStart === null) { runStart = i; area += `M${x(xs[i]).toFixed(1)},${floor}`; }
                    area += `L${x(xs[i]).toFixed(1)},${y(v).toFixed(1)}`;
                });
                if (runStart !== null) area += `L${x(xs[last]).toFixed(1)},${floor}Z`;
                svg.appendChild(svgEl('path', {d: area, fill: series.color, opacity: series.areaOpacity || 0.12}));
            }
            const attrs = {d, fill: 'none', stroke: series.color, 'stroke-width': series.width || 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'};
            if (series.dash) attrs['stroke-dasharray'] = series.dash;
            svg.appendChild(svgEl('path', attrs));
            // A value without neighbours has no line: show it as a point
            series.values.forEach((v, i) => {
                if (!missing(v) && missing(series.values[i - 1]) && missing(series.values[i + 1])) {
                    svg.appendChild(svgEl('circle', {cx: x(xs[i]), cy: y(v), r: 2.5, fill: series.color}));
                }
            });
        });

        (opts.hlines || []).forEach(h => {
            svg.appendChild(svgEl('line', {class: 'marker', x1: plot.x, x2: plot.x + plot.w, y1: y(h.at), y2: y(h.at), style: `stroke:${h.color || ''}`}));
            if (h.label) {
                const label = svgEl('text', {class: 'marker-label', x: plot.x + plot.w - 4, y: y(h.at) - 4, 'text-anchor': 'end', style: `fill:${h.color || ''}`});
                label.textContent = h.label;
                svg.appendChild(label);
            }
        });
        (opts.markers || []).forEach(m => {
            if (m.at < x0 || m.at > x1) return;
            svg.appendChild(svgEl('line', {class: 'marker', x1: x(m.at), x2: x(m.at), y1: plot.y, y2: plot.y + plot.h}));
            if (m.label) {
                const label = svgEl('text', {class: 'marker-label', x: x(m.at) + 4, y: plot.y + 10});
                label.textContent = m.label;
                svg.appendChild(label);
            }
        });

        // Crosshair: snaps to the nearest sample, one tooltip for every series
        const cross = svgEl('line', {class: 'cross', y1: plot.y, y2: plot.y + plot.h, visibility: 'hidden'});
        svg.appendChild(cross);
        const dots = opts.series.map(series => {
            const dot = svgEl('circle', {class: 'dot', r: 4.5, fill: series.color, visibility: 'hidden'});
            svg.appendChild(dot);
            return dot;
        });
        const hit = svgEl('rect', {x: plot.x, y: plot.y, width: plot.w, height: plot.h, fill: 'transparent'});
        svg.appendChild(hit);
        const leave = () => {
            cross.setAttribute('visibility', 'hidden');
            dots.forEach(dot => dot.setAttribute('visibility', 'hidden'));
            hideTip();
            if (opts.onHover) opts.onHover(null);
        };
        hit.addEventListener('pointermove', (e) => {
            const box = svg.getBoundingClientRect();
            const target = x0 + (e.clientX - box.left - plot.x) / plot.w * (x1 - x0);
            let a = 0, b = xs.length - 1;
            while (b - a > 1) { const m = (a + b) >> 1; if (xs[m] < target) a = m; else b = m; }
            const i = Math.abs(xs[a] - target) <= Math.abs(xs[b] - target) ? a : b;
            cross.setAttribute('x1', x(xs[i]));
            cross.setAttribute('x2', x(xs[i]));
            cross.setAttribute('visibility', 'visible');
            const rows = [];
            opts.series.forEach((series, s) => {
                const v = series.values[i];
                if (missing(v)) { dots[s].setAttribute('visibility', 'hidden'); return; }
                dots[s].setAttribute('cx', x(xs[i]));
                dots[s].setAttribute('cy', y(v));
                dots[s].setAttribute('visibility', 'visible');
                rows.push({color: series.color, value: opts.formatY(v, s), name: opts.series.length > 1 ? series.name : ''});
            });
            if (opts.extraRows) rows.push(...opts.extraRows(i));
            showTip(e.clientX, e.clientY, opts.formatX(xs[i]), rows);
            if (opts.onHover) opts.onHover(i);
        });
        hit.addEventListener('pointerleave', leave);
    });
}

// Evenly spaced time labels for a line chart's x axis
function timeTicks(xs, count, format) {
    const ticks = [];
    const x0 = xs[0], x1 = xs[xs.length - 1];
    for (let i = 0; i <= count; i++) {
        const at = x0 + (x1 - x0) * i / count;
        ticks.push({at, label: format(at)});
    }
    return ticks;
}

// Whole hours between two timestamps, every `step` hours
function hourTicks(from, to, step) {
    const ticks = [];
    const start = new Date(from * 1000);
    start.setMinutes(0, 0, 0);
    for (let t = start.getTime() / 1000; t <= to; t += 3600) {
        const h = new Date(t * 1000).getHours();
        if (t >= from && h % step === 0) ticks.push({at: t, label: `${String(h).padStart(2, '0')}:00`});
    }
    return ticks;
}

// Consecutive samples with the same state become one background band
function stateBands(xs, states, colors, step) {
    const bands = [];
    for (let i = 0; i < xs.length; i++) {
        const s = states[i];
        if (!colors[s]) continue;
        let j = i;
        while (j + 1 < xs.length && states[j + 1] === s) j++;
        bands.push({from: xs[i], to: xs[j] + step, color: colors[s], state: s});
        i = j;
    }
    return bands;
}

function legendHtml(items) {
    return items.map(it => `<span><i class="${it.kind || ''}" style="background:${it.color}"></i>${escapeHtml(it.name)}</span>`).join('');
}

// ---------------------------------------------------------------------
// Signed bars: profit and loss. opts: {data: [{label, tipTitle?, values: [n, ...]}], series: [{name, color}],
//                                     height, format: n => text, bySign: bool, labelWidth}
// Positive values stack up from zero, negative ones down. bySign colours a single series green or red.
// ---------------------------------------------------------------------
function signedBarChart(el, opts) {
    const data = opts.data || [];
    if (!data.length || !data.some(d => d.values.some(v => v))) return emptyChart(el, opts.empty || 'No profit or loss in this period');
    mountChart(el, () => {
        const ups = data.map(d => d.values.reduce((a, v) => a + (v > 0 ? v : 0), 0));
        const downs = data.map(d => d.values.reduce((a, v) => a + (v < 0 ? v : 0), 0));
        const scale = axis(Math.min(0, ...downs), Math.max(0, ...ups), 4);
        const {svg, plot, y} = chartFrame(el, opts.height || 190, scale, opts.formatTick);
        const slot = plot.w / data.length;
        const barWidth = Math.max(1, Math.min(opts.maxBar || 22, slot - (slot > 4 ? 2 : 0.5)));
        const every = Math.max(1, Math.ceil(data.length * (opts.labelWidth || 48) / plot.w));
        const zero = y(0);

        data.forEach((d, i) => {
            const cx = plot.x + slot * i + slot / 2;
            const group = svgEl('g', {class: 'slot', tabindex: '0'});
            group.appendChild(svgEl('rect', {x: plot.x + slot * i, y: plot.y, width: slot, height: plot.h, fill: 'transparent'}));
            let up = 0, down = 0;
            d.values.forEach((v, s) => {
                if (!v) return;
                const from = v > 0 ? up : down;
                const to = from + v;
                const top = y(Math.max(from, to)), bottom = y(Math.min(from, to));
                const fill = opts.bySign ? (v > 0 ? color('pos') : color('neg')) : opts.series[s].color;
                group.appendChild(svgEl('rect', {class: 'bar', x: cx - barWidth / 2, y: top, width: barWidth, height: Math.max(1, bottom - top), rx: Math.min(2, barWidth / 3), fill}));
                if (v > 0) up = to; else down = to;
            });
            if (i % every === 0 && d.label) {
                const label = svgEl('text', {class: 'tick', x: cx, y: plot.y + plot.h + 14, 'text-anchor': 'middle'});
                label.textContent = d.label;
                group.appendChild(label);
            }
            const tip = (e) => {
                const box = group.getBoundingClientRect();
                const many = opts.series.length > 1;
                const rows = d.values.map((v, s) => ({v, s})).filter(r => !many || r.v).map(r => ({
                    color: many ? opts.series[r.s].color : null, value: opts.format(r.v || 0), name: many ? opts.series[r.s].name : '',
                }));
                if (many && rows.length > 1) rows.push({value: opts.format(d.values.reduce((a, v) => a + (v || 0), 0)), name: 'total'});
                if (d.extra) rows.push(...d.extra);
                showTip(e && e.clientX ? e.clientX : box.left + box.width / 2, e && e.clientY ? e.clientY : box.top + 20, d.tipTitle || d.label, rows);
            };
            group.addEventListener('pointermove', tip);
            group.addEventListener('focus', () => tip(null));
            group.addEventListener('pointerleave', hideTip);
            group.addEventListener('blur', hideTip);
            if (d.onClick) {
                group.style.cursor = 'pointer';
                group.addEventListener('click', d.onClick);
            }
            svg.appendChild(group);
        });
        svg.appendChild(svgEl('line', {class: 'baseline', x1: plot.x, x2: plot.x + plot.w, y1: zero, y2: zero}));
    });
}

// ---------------------------------------------------------------------
// Donut with a legend. items: [{name, value, color, detail?}], center: {label, value}
// Only for parts of one whole: values must be positive and share a unit.
// ---------------------------------------------------------------------
function donutChart(el, items, center, format) {
    const parts = items.filter(it => it.value > 0);
    const total = parts.reduce((a, it) => a + it.value, 0);
    if (!total) {
        el.innerHTML = '<div class="empty-state">Nothing to show</div>';
        return;
    }
    const size = 168, r = 80, inner = 56, c = size / 2;
    const svg = svgEl('svg', {class: 'donut', viewBox: `0 0 ${size} ${size}`, role: 'img'});
    let angle = -Math.PI / 2;
    const point = (a, radius) => `${(c + radius * Math.cos(a)).toFixed(2)},${(c + radius * Math.sin(a)).toFixed(2)}`;
    parts.forEach(it => {
        const sweep = Math.min(it.value / total, 0.99999) * Math.PI * 2;
        const end = angle + sweep;
        const large = sweep > Math.PI ? 1 : 0;
        const d = `M${point(angle, r)} A${r},${r} 0 ${large} 1 ${point(end, r)} L${point(end, inner)} A${inner},${inner} 0 ${large} 0 ${point(angle, inner)} Z`;
        const path = svgEl('path', {d, fill: it.color, tabindex: '0'});
        const tip = (e) => {
            const box = path.getBoundingClientRect();
            showTip(e && e.clientX ? e.clientX : box.left + box.width / 2, e && e.clientY ? e.clientY : box.top, it.name,
                [{color: it.color, value: format(it.value)}, {value: `${num(it.value / total * 100, 1)}%`, name: 'of the total'}]);
        };
        path.addEventListener('pointermove', tip);
        path.addEventListener('focus', () => tip(null));
        path.addEventListener('pointerleave', hideTip);
        path.addEventListener('blur', hideTip);
        svg.appendChild(path);
        angle = end;
    });
    const label = svgEl('text', {class: 'c-label', x: c, y: c - 6});
    label.textContent = center.label;
    const value = svgEl('text', {class: 'c-value', x: c, y: c + 14});
    value.textContent = center.value;
    svg.append(label, value);
    el.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'donut-wrap';
    wrap.appendChild(svg);
    const legend = document.createElement('div');
    legend.className = 'donut-legend';
    legend.innerHTML = items.map(it => `<div class="row"><i style="background:${it.color}"></i><span class="name">${escapeHtml(it.name)}</span>`
        + `<span class="val">${escapeHtml(it.detail || format(it.value))}</span><span class="pct">${it.value > 0 ? num(it.value / total * 100, 1) + '%' : '–'}</span></div>`).join('');
    wrap.appendChild(legend);
    el.appendChild(wrap);
}

// Date labels for a time axis: hours within a day, days within months, months beyond
function timeAxisTicks(x0, x1, count) {
    const span = x1 - x0;
    const fmt = span <= 2 * 86400 ? fmtTime : span <= 120 * 86400 ? fmtDay
        : (t => new Date(t * 1000).toLocaleDateString([], {month: 'short', year: '2-digit'}));
    return timeTicks([x0, x1], count, fmt);
}
