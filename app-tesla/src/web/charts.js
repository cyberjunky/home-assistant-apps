// Tesla dashboard - small SVG charts: bars (single or stacked) and lines, with a shared tooltip.
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
// Round axis steps: 1, 2, 2.5 or 5 times a power of ten
function niceStep(span, ticks) {
    const raw = span / Math.max(1, ticks);
    const power = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const candidates = [1, 2, 2.5, 5, 10].map(m => m * power);
    return candidates.find(c => c >= raw) || candidates[candidates.length - 1];
}

function axis(min, max, ticks) {
    // A flat series (every value the same, give or take float noise) has no span: give it one,
    // or the step comes out smaller than the numbers can resolve and the loop below never ends
    if (max - min <= Math.abs(max) * 1e-9) max = min + Math.max(1, Math.abs(min) * 0.1);
    const step = niceStep(max - min, ticks);
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const values = [];
    for (let v = lo; v <= hi + step / 1000 && values.length < 100; v += step) values.push(Math.round(v * 1e6) / 1e6);
    return {lo, hi, values};
}

function tickLabel(value) {
    const abs = Math.abs(value);
    if (abs >= 10000) return (value / 1000).toLocaleString(undefined, {maximumFractionDigits: 1}) + 'K';
    return value.toLocaleString(undefined, {maximumFractionDigits: abs < 10 && value % 1 ? 1 : 0});
}

function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    for (const key in attrs) el.setAttribute(key, attrs[key]);
    return el;
}

const chartRegistry = new Set();

// Keep a chart's draw function so it can be redrawn at a new width
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
    const pad = {left: 40, right: 8, top: 8, bottom: 20};
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
    svg.appendChild(svgEl('line', {class: 'baseline', x1: plot.x, x2: plot.x + plot.w, y1: y(Math.max(0, scale.lo)), y2: y(Math.max(0, scale.lo))}));
    el.textContent = '';
    el.appendChild(svg);
    return {svg, plot, y, width};
}

// ---------------------------------------------------------------------
// Bar chart. opts: {data: [{label, tipTitle?, values: [n, ...], onClick?}], series: [{name, color}],
//                   height, format: n => text}
// Several values per bar are stacked. Bars are capped at 24px wide with a rounded data end.
// ---------------------------------------------------------------------
function barChart(el, opts) {
    const data = opts.data || [];
    if (!data.length || !data.some(d => d.values.some(v => v))) return emptyChart(el, opts.empty || 'No data in this period');
    mountChart(el, () => {
        const totals = data.map(d => d.values.reduce((a, b) => a + (b || 0), 0));
        const scale = axis(0, Math.max(...totals), 4);
        const {svg, plot, y} = chartFrame(el, opts.height || 190, scale);
        const slot = plot.w / data.length;
        const barWidth = Math.max(2, Math.min(24, slot - 2));
        // Thin the x labels so they never touch
        const every = Math.max(1, Math.ceil(data.length * (opts.labelWidth || 44) / plot.w));

        data.forEach((d, i) => {
            const cx = plot.x + slot * i + slot / 2;
            const group = svgEl('g', {class: 'slot', tabindex: '0'});
            // The whole column is the hover target, not only the painted bar
            group.appendChild(svgEl('rect', {x: plot.x + slot * i, y: plot.y, width: slot, height: plot.h, fill: 'transparent'}));
            let base = 0;
            const painted = d.values.map((v, s) => ({v: v || 0, s})).filter(p => p.v > 0);
            painted.forEach((p, n) => {
                const top = y(base + p.v), bottom = y(base);
                const last = n === painted.length - 1;
                // 2px surface gap between stacked segments; only the data end is rounded
                const h = Math.max(1, bottom - top - (last ? 0 : 2));
                const r = last ? Math.min(4, h, barWidth / 2) : 0;
                const x = cx - barWidth / 2;
                const path = `M${x},${bottom} v${-(h - r)} q0,${-r} ${r},${-r} h${barWidth - 2 * r} q${r},0 ${r},${r} v${h - r} z`;
                group.appendChild(svgEl('path', {class: 'bar', d: path, fill: opts.series[p.s].color}));
                base += p.v;
            });
            if (i % every === 0) {
                const label = svgEl('text', {class: 'tick', x: cx, y: plot.y + plot.h + 14, 'text-anchor': 'middle'});
                label.textContent = d.label;
                group.appendChild(label);
            }
            const tip = (e) => {
                const box = group.getBoundingClientRect();
                const rows = painted.length
                    ? painted.map(p => ({color: opts.series.length > 1 ? opts.series[p.s].color : null, value: opts.format(p.v), name: opts.series.length > 1 ? opts.series[p.s].name : ''}))
                    : [{value: opts.format(0)}];
                if (painted.length > 1) rows.push({value: opts.format(totals[i]), name: 'total'});
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
// Line chart. opts: {x: [n, ...], series: [{name, color, values: [n|null, ...]}], height, area,
//                    formatX: n => text, formatY: n => text, xTicks: [{at, label}], zero: bool,
//                    onHover: index|null => void}
// One y axis for all series: only plot series together that share a unit.
// ---------------------------------------------------------------------
function lineChart(el, opts) {
    const xs = opts.x || [];
    const all = opts.series.flatMap(s => s.values.filter(v => !missing(v)));
    if (xs.length < 2 || !all.length) return emptyChart(el, opts.empty || 'No data');
    mountChart(el, () => {
        let lo = Math.min(...all), hi = Math.max(...all);
        if (opts.zero || lo > 0 && lo < hi * 0.25) lo = Math.min(0, lo);
        if (hi === lo) hi = lo + 1;
        const scale = axis(lo, hi, 4);
        const {svg, plot, y} = chartFrame(el, opts.height || 170, scale, opts.formatTick);
        const x0 = xs[0], x1 = xs[xs.length - 1];
        const x = v => plot.x + (x1 === x0 ? 0 : (v - x0) / (x1 - x0)) * plot.w;

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
            if (opts.area && first !== null) {
                const floor = y(Math.max(scale.lo, Math.min(0, scale.hi)));
                svg.appendChild(svgEl('path', {d: `${d}L${x(xs[last]).toFixed(1)},${floor}L${x(xs[first]).toFixed(1)},${floor}Z`, fill: series.color, opacity: 0.1}));
            }
            svg.appendChild(svgEl('path', {d, fill: 'none', stroke: series.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
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
            // Binary search for the nearest x
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
