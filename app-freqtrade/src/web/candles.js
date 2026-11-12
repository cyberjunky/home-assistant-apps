// Freqtrade dashboard - the Chart section of a bot's page: one pair's candles as the strategy sees them,
// with its indicators (from its plot_config, plus any you add), its entry and exit signals, and the trades.

const CANDLE_LIMITS = [{key: '100', label: '100'}, {key: '250', label: '250'}, {key: '500', label: '500'}, {key: '1000', label: '1000'}];
const INDICATOR_COLORS = ['#e8a030', '#8e6cd8', '#2ec4d6', '#d55181', '#3987e5', '#c98500', '#199e70', '#d95926'];
const MARKERS = {
    enter_long: {label: 'Long entry signal', shape: 'up', below: true, color: () => color('pos')},
    exit_long: {label: 'Long exit signal', shape: 'down', below: false, color: () => color('warning-color')},
    enter_short: {label: 'Short entry signal', shape: 'down', below: false, color: () => color('neg')},
    exit_short: {label: 'Short exit signal', shape: 'up', below: true, color: () => color('primary-color')},
};
const chart = {pair: null, limit: '250', data: null, error: '', loading: false, loadedAt: 0, key: ''};

function chartPrefs() {
    try { return JSON.parse(localStorage.getItem(LS + 'chart_' + currentBot)) || {}; } catch (e) { return {}; }
}
function saveChartPrefs(update) {
    const prefs = {...chartPrefs(), ...update};
    try { localStorage.setItem(LS + 'chart_' + currentBot, JSON.stringify(prefs)); } catch (e) { /* only a convenience */ }
    return prefs;
}

// The pairs to choose from: open trades first, then the pairlist, then pairs it traded before
function chartPairs() {
    const data = botData();
    const open = (data.status || []).map(t => t.pair);
    const listed = (data.whitelist && data.whitelist.whitelist) || [];
    const traded = (data.performance || []).map(p => p.pair);
    return [...new Set([...open, ...listed, ...traded])].map(pair => ({pair, open: open.includes(pair), listed: listed.includes(pair)}));
}

function renderChartSection() {
    const pairs = chartPairs();
    const prefs = chartPrefs();
    if (!chart.pair || chart.slug !== currentBot) {
        chart.slug = currentBot;
        chart.data = null;
        chart.limit = prefs.limit || '250';
        chart.pair = pairs.some(p => p.pair === prefs.pair) ? prefs.pair : (pairs[0] && pairs[0].pair) || null;
    }
    const select = document.getElementById('ch-pair');
    const key = currentBot + '|' + pairs.map(p => p.pair + p.open).join(',');
    if (select.dataset.key !== key) {
        select.dataset.key = key;
        const group = (label, list) => list.length ? `<optgroup label="${label}">${list.map(p => `<option value="${escapeHtml(p.pair)}">${p.open ? '● ' : ''}${escapeHtml(p.pair)}</option>`).join('')}</optgroup>` : '';
        select.innerHTML = group('Open trades', pairs.filter(p => p.open)) + group('Pairlist', pairs.filter(p => !p.open && p.listed))
            + group('Traded before', pairs.filter(p => !p.open && !p.listed));
    }
    select.value = chart.pair || '';
    buildChips('ch-limit', CANDLE_LIMITS, chart.limit, k => { chart.limit = k; saveChartPrefs({limit: k}); loadCandles(true); });
    if (!chart.pair) {
        emptyChart(document.getElementById('ch-chart'), 'No pairs known yet');
        return;
    }
    // New candles come once per timeframe: read again after a minute, or when asked
    if (!chart.data || chart.data.pair !== chart.pair || Date.now() - chart.loadedAt > 60000) loadCandles(false);
    // Only draw again for new candles: a redraw under the pointer would hide its tooltip
    else if (chart.drawnAt !== chart.loadedAt) drawCandles();
}

function chartPairChanged() {
    chart.pair = document.getElementById('ch-pair').value;
    saveChartPrefs({pair: chart.pair});
    loadCandles(true);
}

async function loadCandles(show) {
    if (!chart.pair || chart.loading) return;
    const slug = currentBot, pair = chart.pair;
    const extra = (chartPrefs().extra || []);
    chart.loading = true;
    if (show) document.getElementById('ch-info').textContent = 'Reading…';
    try {
        const answer = await api(`bot/${encodeURIComponent(slug)}/candles?pair=${encodeURIComponent(pair)}&limit=${chart.limit}&columns=${encodeURIComponent(extra.join(','))}`);
        if (answer.error) throw new Error(answer.error);
        if (slug !== currentBot || pair !== chart.pair) return;
        chart.data = answer;
        chart.error = '';
    } catch (e) {
        if (slug !== currentBot) return;
        chart.data = null;
        chart.error = e.message;
    } finally {
        chart.loading = false;
        chart.loadedAt = Date.now();
    }
    if (botSection === 'chart') drawCandles();
}

// The indicators: the strategy's plot_config, plus the columns added here
function chartIndicators() {
    const d = chart.data;
    const pc = d.plot_config || {};
    const prefs = chartPrefs();
    const hidden = new Set(prefs.hidden || []);
    const has = name => d.columns.includes(name);
    let n = 0;
    const style = (name, s) => ({name, color: (s && s.color) || INDICATOR_COLORS[n++ % INDICATOR_COLORS.length], type: (s && s.type) || 'line', fill_to: s && s.fill_to});
    const main = Object.entries(pc.main_plot || {}).filter(([name]) => has(name)).map(([name, s]) => style(name, s));
    const subs = Object.entries(pc.subplots || {}).map(([title, plots]) => ({
        title, series: Object.entries(plots || {}).filter(([name]) => has(name)).map(([name, s]) => style(name, s)),
    })).filter(s => s.series.length);
    const builtIn = new Set([...main.map(m => m.name), ...subs.flatMap(s => s.series.map(x => x.name))]);
    // An added column goes on the price when its values are near the price, else in a subplot of its own
    const closes = column('close').filter(v => !missing(v));
    const lo = Math.min(...closes), hi = Math.max(...closes);
    (prefs.extra || []).filter(name => has(name) && !builtIn.has(name)).forEach(name => {
        const values = column(name).filter(v => typeof v === 'number');
        const mid = values.length ? values[Math.floor(values.length / 2)] : null;
        const extra = {...style(name), added: true};
        if (!missing(mid) && mid >= lo * 0.5 && mid <= hi * 2) main.push(extra);
        else subs.push({title: name, series: [extra]});
    });
    const all = [...main, ...subs.flatMap(s => s.series)];
    all.forEach(s => { s.hidden = hidden.has(s.name); });
    return {main, subs, all};
}

function column(name) {
    const d = chart.data;
    const i = d.columns.indexOf(name);
    return i < 0 ? d.data.map(() => null) : d.data.map(row => row[i]);
}

function toggleIndicator(name) {
    const hidden = new Set(chartPrefs().hidden || []);
    if (hidden.has(name)) hidden.delete(name); else hidden.add(name);
    saveChartPrefs({hidden: [...hidden]});
    drawCandles();
}

function addIndicator() {
    const select = document.getElementById('ch-add');
    const name = select.value;
    select.value = '';
    if (!name) return;
    const extra = [...new Set([...(chartPrefs().extra || []), name])];
    saveChartPrefs({extra});
    loadCandles(true);
}

function removeIndicator(name) {
    saveChartPrefs({extra: (chartPrefs().extra || []).filter(n => n !== name)});
    drawCandles();
}

function drawCandles() {
    chart.drawnAt = chart.loadedAt;
    const el = document.getElementById('ch-chart');
    const info = document.getElementById('ch-info');
    const d = chart.data;
    if (!d) {
        info.textContent = '';
        document.getElementById('ch-indicators').innerHTML = '';
        return emptyChart(el, chart.error ? `The bot didn't give the candles: ${chart.error}` : 'Reading…');
    }
    if (d.data.length < 2) {
        info.textContent = '';
        return emptyChart(el, 'The bot has no analysed candles for this pair yet. Pairs outside its pairlist only have them once it analysed them.');
    }
    const ind = chartIndicators();
    const s = d.signals || {};
    info.textContent = `${d.timeframe} · ${d.data.length} candles · signals: ${s.enter_long || 0} long in / ${s.exit_long || 0} out`
        + (s.enter_short || s.exit_short ? ` · ${s.enter_short || 0} short in / ${s.exit_short || 0} out` : '')
        + (d.last_analyzed_ts ? ` · analysed ${fmtAgo(d.last_analyzed_ts)}` : '');
    document.getElementById('ch-indicators').innerHTML = ind.all.map(x =>
        `<span class="ind-chip${x.hidden ? ' off' : ''}"><button type="button" onclick="toggleIndicator('${escapeHtml(x.name)}')" title="Show or hide"><i style="background:${escapeHtml(x.color)}"></i>${escapeHtml(x.name)}</button>`
        + (x.added ? `<button type="button" class="x" onclick="removeIndicator('${escapeHtml(x.name)}')" aria-label="Remove ${escapeHtml(x.name)}">×</button>` : '') + '</span>'
    ).join('') || '<span class="panel-sub">The strategy has no plot_config: add indicators from the list.</span>';
    const add = document.getElementById('ch-add');
    const shown = new Set(ind.all.map(x => x.name));
    add.innerHTML = '<option value="">+ Indicator</option>' + (d.available || []).filter(n => !shown.has(n)).map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');

    const times = column('__date_ts').map(v => v / 1000);
    const model = {
        times, open: column('open'), high: column('high'), low: column('low'), close: column('close'), volume: column('volume'),
        enterTag: column('enter_tag'), exitTag: column('exit_tag'),
        main: ind.main.filter(x => !x.hidden).map(x => ({...x, values: column(x.name), to: x.fill_to ? column(x.fill_to) : null})),
        subs: ind.subs.map(sp => ({title: sp.title, series: sp.series.filter(x => !x.hidden).map(x => ({...x, values: column(x.name)}))})).filter(sp => sp.series.length),
        signals: Object.fromEntries(Object.keys(MARKERS).map(k => [k, column(`_${k}_signal_close`)])),
        trades: chartTrades(times),
    };
    candleChart(el, model);
    const used = Object.keys(MARKERS).filter(k => model.signals[k].some(v => !missing(v)));
    document.getElementById('ch-legend').innerHTML = [
        ...used.map(k => `<span><i style="background:${MARKERS[k].color()};clip-path:polygon(${MARKERS[k].shape === 'up' ? '50% 0,100% 100%,0 100%' : '0 0,100% 0,50% 100%'})"></i>${MARKERS[k].label}</span>`),
        model.trades.length ? `<span><i class="line" style="background:var(--secondary-text-color)"></i>Trades: ● entry, ■ exit</span>` : '',
    ].join('');
}

// This pair's trades that overlap the candles: closed ones, and the open one up to now
function chartTrades(times) {
    const pair = chart.pair;
    const first = times[0], last = times[times.length - 1] + (chart.data.timeframe_ms || 0) / 1000;
    const closed = botTrades().filter(t => t.pair === pair && ms(t.close_timestamp) >= first && ms(t.open_timestamp) <= last);
    const open = (botData().status || []).filter(t => t.pair === pair).map(t => ({...t, open: true}));
    return [...closed, ...open].map(t => ({
        id: t.trade_id, short: t.is_short, open: Boolean(t.open), tag: t.enter_tag, reason: t.exit_reason,
        t0: ms(t.open_timestamp), p0: t.open_rate,
        t1: t.open ? Date.now() / 1000 : ms(t.close_timestamp), p1: t.open ? t.current_rate : t.close_rate,
        ratio: t.profit_ratio,
    }));
}

// ---------------------------------------------------------------------
// The drawing: price with volume, then each subplot, on one time axis (one slot per candle)
// ---------------------------------------------------------------------
function candleChart(el, m) {
    mountChart(el, () => {
        const n = m.times.length;
        const width = Math.max(320, el.clientWidth || 800);
        const pad = {left: 6, right: 64, top: 8, bottom: 22};
        const mainH = width < 600 ? 240 : 320, subH = width < 600 ? 80 : 100, gap = 10;
        const height = pad.top + mainH + m.subs.length * (subH + gap) + pad.bottom;
        const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img'});
        const plotW = width - pad.left - pad.right;
        const slot = plotW / n;
        const x = i => pad.left + (i + 0.5) * slot;
        const tf = (chart.data.timeframe_ms || 0) / 1000 || (m.times[1] - m.times[0]);
        const indexAt = t => Math.min(n - 1, Math.max(0, Math.floor((t - m.times[0]) / tf)));

        // Price pane
        const lows = m.low.filter(v => !missing(v)), highs = m.high.filter(v => !missing(v));
        // The candles set the scale; indicators and trades may widen it by at most a quarter on each side
        const cLo = Math.min(...lows), cHi = Math.max(...highs);
        const room = (cHi - cLo || cHi || 1) * 0.25;
        let lo = cLo, hi = cHi;
        const widen = v => { if (typeof v === 'number' && v >= cLo - room && v <= cHi + room) { lo = Math.min(lo, v); hi = Math.max(hi, v); } };
        m.main.forEach(s => s.values.forEach(widen));
        m.trades.forEach(t => { widen(t.p0); widen(t.p1); });
        const span = hi - lo || hi || 1;
        lo -= span * 0.05; hi += span * 0.05;
        const top = pad.top;
        const y = v => top + (hi - v) / (hi - lo) * mainH;
        const inside = v => !missing(v) && v >= lo && v <= hi;
        const ticks = axis(lo, hi, 5).values.filter(v => v >= lo && v <= hi);
        const grid = svgEl('g', {class: 'grid'});
        ticks.forEach(v => {
            grid.appendChild(svgEl('line', {x1: pad.left, x2: pad.left + plotW, y1: y(v), y2: y(v)}));
            const label = svgEl('text', {class: 'tick', x: pad.left + plotW + 6, y: y(v) + 3});
            label.textContent = price(v);
            grid.appendChild(label);
        });
        svg.appendChild(grid);

        // Volume along the bottom fifth of the price pane
        const vmax = Math.max(...m.volume.filter(v => !missing(v)), 0) || 1;
        const vol = svgEl('g', {opacity: 0.28});
        m.volume.forEach((v, i) => {
            if (missing(v) || !v) return;
            const h = v / vmax * mainH * 0.2;
            vol.appendChild(svgEl('rect', {x: x(i) - slot * 0.4, y: top + mainH - h, width: Math.max(0.6, slot * 0.8), height: h,
                fill: m.close[i] >= m.open[i] ? color('pos') : color('neg')}));
        });
        svg.appendChild(vol);

        // Bands (fill_to) under everything else, inside the price pane
        const bandClip = 'clip-' + Math.random().toString(36).slice(2);
        const bc = svgEl('clipPath', {id: bandClip});
        bc.appendChild(svgEl('rect', {x: pad.left, y: top, width: plotW, height: mainH}));
        svg.appendChild(bc);
        const bands = svgEl('g', {'clip-path': `url(#${bandClip})`});
        svg.appendChild(bands);
        m.main.filter(s => s.to).forEach(s => {
            let d = '', back = '';
            s.values.forEach((v, i) => {
                const w = s.to[i];
                if (typeof v !== 'number' || typeof w !== 'number') return;
                d += `${d ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
                back = `L${x(i).toFixed(1)},${y(w).toFixed(1)}` + back;
            });
            if (d) bands.appendChild(svgEl('path', {d: d + back + 'Z', fill: s.color, opacity: 0.08, stroke: 'none'}));
        });

        // Candles
        const body = Math.max(1, Math.min(14, slot * 0.7));
        const candles = svgEl('g', {});
        for (let i = 0; i < n; i++) {
            const o = m.open[i], c = m.close[i], h = m.high[i], l = m.low[i];
            if ([o, c, h, l].some(missing)) continue;
            const col = c >= o ? color('pos') : color('neg');
            candles.appendChild(svgEl('line', {x1: x(i), x2: x(i), y1: y(h), y2: y(l), stroke: col, 'stroke-width': 1}));
            if (slot >= 2.5) {
                const yt = y(Math.max(o, c)), yb = y(Math.min(o, c));
                candles.appendChild(svgEl('rect', {x: x(i) - body / 2, y: yt, width: body, height: Math.max(1, yb - yt), fill: col}));
            }
        }
        svg.appendChild(candles);

        // Indicators on the price
        const clipId = 'clip-' + Math.random().toString(36).slice(2);
        const clip = svgEl('clipPath', {id: clipId});
        clip.appendChild(svgEl('rect', {x: pad.left, y: top, width: plotW, height: mainH}));
        svg.appendChild(clip);
        const overlay = svgEl('g', {'clip-path': `url(#${clipId})`});
        svg.appendChild(overlay);
        m.main.forEach(s => drawSeries(overlay, s, x, y, y(Math.max(lo, Math.min(0, hi)))));

        // Trades: entry ● to exit ■, green or red by result; an open one runs to now
        m.trades.forEach(t => {
            const inRange = tt => tt >= m.times[0] && tt <= m.times[n - 1] + tf;
            const col = (t.ratio || 0) >= 0 ? color('pos') : color('neg');
            const x0 = inRange(t.t0) ? x(indexAt(t.t0)) : pad.left, x1 = x(indexAt(t.t1));
            if (!inside(t.p0) && !inside(t.p1)) return;
            if (!missing(t.p0) && !missing(t.p1)) {
                overlay.appendChild(svgEl('line', {x1: x0, y1: y(t.p0), x2: x1, y2: y(t.p1), stroke: col, 'stroke-width': 1.5, 'stroke-dasharray': t.open ? '2 3' : '5 3', opacity: 0.9}));
            }
            if (inRange(t.t0) && inside(t.p0)) svg.appendChild(svgEl('circle', {cx: x0, cy: y(t.p0), r: 4, fill: 'var(--ha-card-background)', stroke: col, 'stroke-width': 2}));
            if (!t.open && inRange(t.t1) && inside(t.p1)) svg.appendChild(svgEl('rect', {x: x1 - 4, y: y(t.p1) - 4, width: 8, height: 8, fill: col}));
        });

        // Signals: a triangle under the low or over the high of the candle
        const size = Math.max(4, Math.min(7, slot * 0.8));
        Object.entries(m.signals).forEach(([kind, values]) => {
            const mk = MARKERS[kind];
            values.forEach((v, i) => {
                if (missing(v)) return;
                const cy = mk.below ? y(m.low[i]) + size + 4 : y(m.high[i]) - size - 4;
                const pts = mk.shape === 'up'
                    ? `${x(i)},${cy - size} ${x(i) + size},${cy + size * 0.7} ${x(i) - size},${cy + size * 0.7}`
                    : `${x(i)},${cy + size} ${x(i) + size},${cy - size * 0.7} ${x(i) - size},${cy - size * 0.7}`;
                svg.appendChild(svgEl('polygon', {points: pts, fill: mk.color(), stroke: 'var(--ha-card-background)', 'stroke-width': 1}));
            });
        });

        // The last price, on the axis
        const lastClose = m.close[n - 1];
        if (!missing(lastClose)) {
            svg.appendChild(svgEl('line', {class: 'marker', x1: pad.left, x2: pad.left + plotW, y1: y(lastClose), y2: y(lastClose)}));
            svg.appendChild(svgEl('rect', {x: pad.left + plotW + 2, y: y(lastClose) - 8, width: pad.right - 4, height: 16, rx: 3, fill: lastClose >= m.open[n - 1] ? color('pos') : color('neg')}));
            const label = svgEl('text', {x: pad.left + plotW + 6, y: y(lastClose) + 4, fill: '#fff', 'font-size': 10.5, 'font-family': 'var(--mono)'});
            label.textContent = price(lastClose);
            svg.appendChild(label);
        }

        // Subplots, each with its own scale
        const subScales = m.subs.map((sp, k) => {
            const sTop = pad.top + mainH + gap + k * (subH + gap);
            const vals = sp.series.flatMap(s => s.values.filter(v => typeof v === 'number'));
            let slo = Math.min(...vals, sp.series.some(s => s.type === 'bar') ? 0 : Infinity), shi = Math.max(...vals);
            if (!Number.isFinite(slo) || !Number.isFinite(shi)) { slo = 0; shi = 1; }
            if (shi === slo) shi = slo + 1;
            const sy = v => sTop + (shi - v) / (shi - slo) * subH;
            svg.appendChild(svgEl('rect', {x: pad.left, y: sTop, width: plotW, height: subH, fill: 'none', stroke: 'var(--grid-color)'}));
            axis(slo, shi, 2).values.filter(v => v >= slo && v <= shi).forEach(v => {
                svg.appendChild(svgEl('line', {x1: pad.left, x2: pad.left + plotW, y1: sy(v), y2: sy(v), stroke: 'var(--grid-color)'}));
                const label = svgEl('text', {class: 'tick', x: pad.left + plotW + 6, y: sy(v) + 3});
                label.textContent = tickLabel(v);
                svg.appendChild(label);
            });
            sp.series.forEach(s => drawSeries(svg, s, x, sy, sy(Math.max(slo, Math.min(0, shi))), slot));
            const title = svgEl('text', {class: 'tick', x: pad.left + 6, y: sTop + 12, style: 'font-weight:600'});
            title.textContent = sp.title;
            svg.appendChild(title);
            return sy;
        });

        // Time labels
        const spanT = m.times[n - 1] - m.times[0];
        const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 110))));
        for (let i = Math.floor(every / 2); i < n; i += every) {
            const label = svgEl('text', {class: 'tick', x: x(i), y: height - 6, 'text-anchor': 'middle'});
            label.textContent = spanT > 86400 ? `${fmtDay(m.times[i])} ${fmtTime(m.times[i])}` : fmtTime(m.times[i]);
            svg.appendChild(label);
        }

        // Crosshair over every pane, one tooltip
        const cross = svgEl('line', {class: 'cross', y1: pad.top, y2: height - pad.bottom, visibility: 'hidden'});
        const hline = svgEl('line', {class: 'cross', x1: pad.left, x2: pad.left + plotW, visibility: 'hidden', 'stroke-dasharray': '3 3'});
        svg.append(cross, hline);
        const hit = svgEl('rect', {x: pad.left, y: pad.top, width: plotW, height: height - pad.top - pad.bottom, fill: 'transparent'});
        svg.appendChild(hit);
        hit.addEventListener('pointermove', (e) => {
            const box = svg.getBoundingClientRect();
            const px = (e.clientX - box.left) * width / box.width, py = (e.clientY - box.top) * height / box.height;
            const i = Math.min(n - 1, Math.max(0, Math.floor((px - pad.left) / slot)));
            cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
            if (py <= pad.top + mainH) { hline.setAttribute('y1', py); hline.setAttribute('y2', py); hline.setAttribute('visibility', 'visible'); }
            else hline.setAttribute('visibility', 'hidden');
            const change = m.open[i] ? (m.close[i] - m.open[i]) / m.open[i] : null;
            const rows = [
                {value: `${price(m.open[i])} · ${price(m.high[i])} · ${price(m.low[i])} · ${price(m.close[i])}`, name: 'O H L C'},
                {value: pct(change), name: 'change'},
                {value: tickLabel(m.volume[i] || 0), name: 'volume'},
            ];
            [...m.main, ...m.subs.flatMap(sp => sp.series)].forEach(s => {
                const v = s.values[i];
                rows.push({color: s.color, value: typeof v === 'number' ? (Math.abs(v) >= 1 ? num(v, 2) : price(v)) : (missing(v) ? '–' : String(v)), name: s.name});
            });
            Object.entries(m.signals).forEach(([kind, values]) => {
                if (!missing(values[i])) rows.push({color: MARKERS[kind].color(), value: MARKERS[kind].label, name: kind.startsWith('enter') ? (m.enterTag[i] || '') : (m.exitTag[i] || '')});
            });
            m.trades.forEach(t => {
                if (indexAt(t.t0) === i && t.t0 >= m.times[0]) rows.push({value: `#${t.id} ${t.short ? 'short' : 'long'} in at ${price(t.p0)}`, name: t.tag || ''});
                if (!t.open && indexAt(t.t1) === i) rows.push({value: `#${t.id} out at ${price(t.p1)} ${pct(t.ratio)}`, name: t.reason || ''});
            });
            showTip(e.clientX, e.clientY, fmtDateTime(m.times[i]), rows);
        });
        hit.addEventListener('pointerleave', () => {
            cross.setAttribute('visibility', 'hidden');
            hline.setAttribute('visibility', 'hidden');
            hideTip();
        });
        el.textContent = '';
        el.appendChild(svg);
        return subScales;
    });
}

// A line, bars from zero, or dots, with gaps where the strategy has no value
function drawSeries(svg, s, x, y, zeroY, slot) {
    if (s.type === 'bar') {
        const w = Math.max(0.8, (slot || 4) * 0.6);
        s.values.forEach((v, i) => {
            if (typeof v !== 'number') return;
            const yv = y(v);
            svg.appendChild(svgEl('rect', {x: x(i) - w / 2, y: Math.min(yv, zeroY), width: w, height: Math.max(0.5, Math.abs(zeroY - yv)), fill: s.color, opacity: 0.7}));
        });
        return;
    }
    if (s.type === 'scatter') {
        s.values.forEach((v, i) => { if (typeof v === 'number') svg.appendChild(svgEl('circle', {cx: x(i), cy: y(v), r: 2, fill: s.color})); });
        return;
    }
    let d = '', pen = false;
    s.values.forEach((v, i) => {
        if (typeof v !== 'number') { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
    });
    if (d) svg.appendChild(svgEl('path', {d, fill: 'none', stroke: s.color, 'stroke-width': 1.4, 'stroke-linejoin': 'round'}));
}
