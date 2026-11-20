// Dashboard - the blocks a page is made of. Each one draws itself from Home Assistant's states.
'use strict';

const TOGGLE = ['light', 'switch', 'input_boolean', 'fan'];
const RUN = ['scene', 'script', 'button', 'input_button', 'automation'];
const SLIDE = ['light', 'media_player', 'cover', 'fan', 'number', 'input_number'];
const RANGES = [[6, '6 h'], [24, '24 h'], [72, '3 days'], [168, '7 days'], [336, '14 days'], [720, '30 days']];
const SERIES = ['var(--series, var(--ink))', 'var(--accent)', 'var(--green)'];
const GREENS = ['var(--green)', 'var(--green2)', 'var(--green3)'];

const dot = on => `<span class="dot${on ? ' on' : ''}"></span>`;
const first = w => w.entities[0];
const missing = w => `<div class="empty">${w.entities.length ? `<span class="gone">${esc(first(w))} is not in Home Assistant</span>` : 'Drag an entity here'}</div>`;

// ---------- Sliders: one reading of 0..100 for everything that slides ----------
function slideGet(e) {
    const s = st(e), d = dom(e);
    if (!s) return {pct: 0, text: '–', unit: ''};
    if (d === 'light') return {pct: s.s === 'on' ? Math.round((s.a.brightness ?? 255) / 2.55) : 0, unit: '%'};
    if (d === 'media_player') return {pct: Math.round((s.a.volume_level ?? 0) * 100), unit: '%'};
    if (d === 'cover') return {pct: s.a.current_position ?? (s.s === 'open' ? 100 : 0), unit: '%'};
    if (d === 'fan') return {pct: s.s === 'on' ? (s.a.percentage ?? 100) : 0, unit: '%'};
    const lo = Number(s.a.min ?? 0), hi = Number(s.a.max ?? 100);
    return {pct: clamp((Number(s.s) - lo) / ((hi - lo) || 1) * 100, 0, 100), unit: s.a.unit_of_measurement || '', lo, hi, step: Number(s.a.step) || 1};
}
function slideValue(e, pct) {
    const g = slideGet(e);
    if (g.lo === undefined) return Math.round(pct);
    return Math.round((g.lo + (g.hi - g.lo) * pct / 100) / g.step) * g.step;
}
function slideSet(e, pct) {
    const d = dom(e), v = slideValue(e, pct);
    if (d === 'light') return v ? call(e, 'turn_on', {brightness_pct: v}) : call(e, 'turn_off');
    if (d === 'media_player') return call(e, 'volume_set', {volume_level: v / 100});
    if (d === 'cover') return call(e, 'set_cover_position', {position: v});
    if (d === 'fan') return call(e, 'set_percentage', {percentage: v});
    return call(e, 'set_value', {value: v});
}

// ---------- History for the charts ----------
const HIST = new Map();
const isCounter = e => ['total_increasing', 'total'].includes(attr(e, 'state_class'));
// The points of an entity's history, or null while they are being read; done() runs when they arrive
function history(e, hours, daily, done) {
    const agg = daily && isCounter(e) ? 'delta' : 'mean';
    const key = `${e}|${hours}|${daily}|${agg}`;
    let h = HIST.get(key);
    if (!h) HIST.set(key, h = {points: null, at: 0, busy: false});
    if (!h.busy && Date.now() - h.at > 5 * 60000) {
        h.busy = true;
        api(`history?entity=${encodeURIComponent(e)}&hours=${hours}&agg=${agg}${daily ? '&step=day' : ''}`)
            .then(r => { h.points = r.points; }, () => { h.points = h.points || []; })
            .finally(() => { h.busy = false; h.at = Date.now(); if (done) done(); });
    }
    return h.points;
}
const WAIT = '<div class="empty">Reading the history…</div>';
const qtyTextOf = (v, unit) => { const q = quantity(v, unit); return q.text + (q.unit ? ' ' + q.unit : ''); };

// A line per entity: {html, values}, or {html} alone while there is nothing to draw
function linePlot(entities, hours, done) {
    const all = entities.slice(0, 3).map(e => history(e, hours, false, done));
    if (all.some(p => p === null)) return {html: WAIT};
    const values = all.flat().map(p => p[1]).filter(v => v != null);
    if (!values.length) return {html: '<div class="empty">No history yet</div>'};
    let lo = Math.min(...values), hi = Math.max(...values);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * .08;
    lo -= pad; hi += pad;
    const paths = all.map((points, i) => {
        let d = '', pen = false;
        points.forEach((p, n) => {
            if (p[1] == null) { pen = false; return; }
            d += `${pen ? 'L' : 'M'}${(n / (points.length - 1) * 1000).toFixed(1)} ${((hi - p[1]) / (hi - lo) * 300).toFixed(1)}`;
            pen = true;
        });
        return `<path d="${d}" style="stroke:${SERIES[i]}"/>`;
    }).join('');
    const t0 = all[0][0][0] * 1000, t1 = Date.now();
    const ticks = [0, .25, .5, .75, 1].map(f => {
        const t = new Date(t0 + (t1 - t0) * f);
        return `<span class="lbl">${hours <= 48 ? t.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'})
            : t.toLocaleDateString(LOCALE, {weekday: 'short', day: 'numeric'})}</span>`;
    }).join('');
    return {values, html: `<div class="ch-plot"><svg viewBox="0 0 1000 300" preserveAspectRatio="none"><line x1="0" x2="1000" y1="100" y2="100"/><line x1="0" x2="1000" y1="200" y2="200"/>${paths}</svg></div>
        <div class="ch-x">${ticks}</div>`};
}
function lineChart(w) {
    const plot = linePlot(w.entities, w.opts.hours || 24, () => drawWidget(w.id));
    if (!plot.values) return plot.html;
    const unit = attr(first(w), 'unit_of_measurement'), q = qty(first(w));
    const top = w.entities.length > 1
        ? `<div class="ch-legend">${w.entities.slice(0, 3).map((e, i) => `<span><i class="sq" style="background:${SERIES[i]}"></i><span class="lbl">${esc(ename(e, w))} <b>${esc(qtyText(e))}</b></span></span>`).join('')}</div>`
        : `<span class="num">${q.text}<small>${esc(q.unit)}</small></span>`;
    return `<div class="ch" data-more="${esc(first(w))}"><div class="ch-top">${top}${w.entities.length > 1 ? '' : `<span class="lbl">${quantity(Math.min(...plot.values), unit).text} – ${esc(qtyTextOf(Math.max(...plot.values), unit))}</span>`}</div>${plot.html}</div>`;
}
// A bar per day: {html, points}
function barPlot(e, hours, done) {
    const points = history(e, Math.max(48, hours), true, done);
    if (points === null) return {html: WAIT};
    const top = Math.max(...points.map(p => p[1] || 0), 1e-9);
    const today = new Date().toDateString(), many = points.length > 16;
    return {points, html: `<div class="chb${many ? ' many' : ''}">${points.map(p => {
        const day = new Date(p[0] * 1000), now = day.toDateString() === today;
        return `<div class="chb-col${now ? ' today' : ''}"><div class="chb-bar"><span>${p[1] == null || many ? '' : fmt(p[1], 1)}</span><i style="height:${(p[1] || 0) / top * 78}%"></i></div>
            <span class="lbl">${now && !many ? 'Today' : day.toLocaleDateString(LOCALE, many ? {day: 'numeric'} : {weekday: 'short'})}</span>${many ? '' : `<small>${day.toLocaleDateString(LOCALE, {day: 'numeric', month: 'numeric'})}</small>`}</div>`;
    }).join('')}</div>`};
}
const barChart = w => `<div class="ch" data-more="${esc(first(w))}">${barPlot(first(w), w.opts.hours || 168, () => drawWidget(w.id)).html}</div>`;

// ---------- The blocks ----------
// name: in the design drawer; size: columns x rows when dropped; many: takes several entities;
// takes(e): whether an entity fits; label/aux: the header's two ends; body: the HTML
const BLOCKS = {
    clock: {
        name: 'Clock', size: [10, 6], takes: () => false, hint: 'The time, the date and the week',
        body(w) {
            const now = new Date();
            const time = now.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit', hour12: Boolean(w.opts.h12)}).replace(/\s?[AP]M/i, '');
            const [h, m] = time.split(/[:.]/);
            const jan4 = new Date(now.getFullYear(), 0, 4);
            const monday = d => { const c = new Date(d); c.setHours(0, 0, 0, 0); c.setDate(c.getDate() - (c.getDay() + 6) % 7); return c; };
            let week = Math.round((monday(now) - monday(jan4)) / 604800000) + 1;
            if (week < 1) week = 52;
            return `<div class="clock"><div class="clock-time num">${h}<i>:</i>${m}</div><div class="clock-foot">
                <span class="clock-date">${esc(now.toLocaleDateString(LOCALE, {weekday: 'long', day: 'numeric', month: 'long'}))}</span><span class="lbl">Wk ${week}</span></div></div>`;
        },
    },
    heading: {
        name: 'Heading', size: [8, 2], takes: () => false, hint: 'A title and a line of text',
        body: w => `<div class="heading"><div class="ttl">${esc(w.opts.text || 'Heading')}</div>${w.opts.sub ? `<div class="sub">${esc(w.opts.sub)}</div>` : ''}</div>`,
    },
    value: {
        name: 'Value', size: [5, 3], takes: () => true, hint: 'One reading, large',
        label: w => ename(first(w), w),
        body(w) {
            const e = first(w);
            if (!st(e)) return missing(w);
            if (!isNumeric(e)) return `<div class="value"><div class="ttl word">${esc(stateText(e))}</div></div>`;
            const q = qty(e);
            return `<div class="value" data-more="${esc(e)}"><div class="num">${q.text}<small>${esc(q.unit)}</small></div></div>`;
        },
    },
    weather: {
        name: 'Weather', size: [9, 2], takes: e => dom(e) === 'weather', hint: 'Now, and the days or hours ahead',
        label: () => '',
        body(w) {
            const e = first(w), s = st(e);
            if (!s) return missing(w);
            const bits = [conditionText(s.s)];
            if (s.a.humidity != null) bits.push(`${s.a.humidity} %`);
            if (s.a.wind_speed != null) bits.push(`wind ${fmt(s.a.wind_speed, 0)} ${s.a.wind_speed_unit || ''}`);
            const now = `<div class="weather"><span class="num">${fmt(s.a.temperature, 0)}°</span>${ic(conditionIcon(s.s))}<span class="sub">${esc(bits.join(' · '))}</span></div>`;
            if (!w.opts.forecast) return `<div class="weather-wrap" data-weather="${esc(e)}">${now}</div>`;
            const ahead = forecast(e, w.opts.forecast, () => drawWidget(w.id));
            return `<div class="weather-wrap" data-weather="${esc(e)}">${now}${ahead ? forecastRow(ahead, w.opts.forecast, w.w >= 12 ? 8 : w.w >= 8 ? 6 : 4) : ''}</div>`;
        },
    },
    tiles: {
        name: 'Tiles', size: [9, 4], many: true, hint: 'Lights and switches to tap',
        takes: e => [...TOGGLE, ...RUN, 'cover'].includes(dom(e)),
        aux(w) { const on = w.entities.filter(isOn).length; return on ? `<b>${on} on</b>` : ''; },
        body(w) {
            if (!w.entities.length) return missing(w);
            // As many columns as keeps the tiles closest to the shape of a playing card on its side
            const n = w.entities.length;
            let cols = 1, best = Infinity;
            for (let c = 1; c <= n; c++) {
                const shape = (w.w * 76 / c) / ((w.h * 64 - 50) / Math.ceil(n / c));
                const off = Math.abs(Math.log(shape / 1.4));
                if (off < best) { best = off; cols = c; }
            }
            return `<div class="tiles" style="grid-template-columns:repeat(${cols},1fr)">${w.entities.map(e => `
                <button class="box tile" data-tap="${esc(e)}" data-hold="${esc(e)}"><span class="tile-top">${dot(isOn(e))}<span class="lbl">${isOn(e) ? `<b>${esc(stateText(e))}</b>` : esc(stateText(e))}</span></span>
                <span><span class="tile-name cut" style="display:block">${esc(ename(e, w))}</span>${S.areas[e] && n <= 6 ? `<span class="sub">${esc(S.areas[e])}</span>` : ''}</span></button>`).join('')}</div>`;
        },
    },
    slider: {
        name: 'Slider', size: [9, 2], many: true, hint: 'Brightness, volume, position',
        takes: e => SLIDE.includes(dom(e)),
        label: () => '',
        body(w) {
            if (!w.entities.length) return missing(w);
            return `<div class="sliders">${w.entities.map(e => {
                const g = slideGet(e);
                return `<div class="slider"><div class="slider-read"><span class="lbl">${esc(ename(e, w))}</span><span class="num"><span class="sl-num">${fmt(slideValue(e, g.pct))}</span><small>${esc(g.unit)}</small></span></div>
                    <div class="sl" data-slide="${esc(e)}"><i class="sl-fill" style="width:calc(${g.pct}% + 2rem)"></i><span class="box sl-knob" style="left:${g.pct}%"></span></div></div>`;
            }).join('')}</div>`;
        },
    },
    climate: {
        name: 'Climate', size: [8, 4], takes: e => dom(e) === 'climate', hint: 'A thermostat: now, target, up and down',
        label: w => ename(first(w), w),
        aux(w) { const s = st(first(w)); return s ? `<b>${esc(cap(String(s.a.hvac_action || s.s)))}</b>` : ''; },
        body(w) {
            const e = first(w), s = st(e);
            if (!s) return missing(w);
            const target = PENDING[e]?.v ?? s.a.temperature;
            return `<div class="climate"><div class="climate-now"><span class="num">${fmt(s.a.current_temperature, 1)}°</span></div>
                <div class="climate-set"><div><span class="lbl">Set to</span><span class="num">${target == null ? '–' : fmt(target, 1)}°</span></div>
                <div class="climate-btns"><button class="box" data-temp="${esc(e)}" data-step="-1">${ic('minus')}</button><button class="box" data-temp="${esc(e)}" data-step="1">${ic('plus')}</button></div></div></div>`;
        },
    },
    covers: {
        name: 'Blinds', size: [8, 3], many: true, takes: e => dom(e) === 'cover', hint: 'Up and down, per blind or all at once',
        aux(w) { return w.opts.compact && w.entities.length ? `<b>${esc(w.entities.some(isOn) ? 'Open' : 'Closed')}</b>` : ''; },
        body(w) {
            if (!w.entities.length) return missing(w);
            if (w.opts.compact) {
                const all = esc(w.entities.join(','));
                return `<div class="covers-big"><button class="box" data-cover="${all}" data-do="open_cover">${ic('up')}<span>Up</span></button><button class="box" data-cover="${all}" data-do="close_cover">${ic('down')}<span>Down</span></button></div>`;
            }
            return `<div class="rows">${w.entities.map(e => `<div class="row cover"><div class="row-main"><div class="cover-name cut">${esc(ename(e, w))}</div><span class="lbl">${esc(stateText(e))}</span></div>
                <button class="box" data-cover="${esc(e)}" data-do="open_cover">${ic('up')} Up</button><button class="box" data-cover="${esc(e)}" data-do="close_cover">${ic('down')} Down</button></div>`).join('')}</div>`;
        },
    },
    actions: {
        name: 'Actions', size: [6, 6], many: true, takes: e => RUN.includes(dom(e)), hint: 'Scenes and scripts as large rows',
        body(w) {
            if (!w.entities.length) return missing(w);
            return `<div class="rows noline">${w.entities.map(e => `<div class="row action press" data-tap="${esc(e)}">${ic(dom(e) === 'scene' ? 'scene' : dom(e) === 'script' ? 'script' : 'bolt')}
                <div class="row-main"><div class="ttl cut">${esc(ename(e, w))}</div><div class="sub cut">${esc(w.opts.subs?.[e] || S.areas[e] || cap(dom(e)))}</div></div>${ic('right')}</div>`).join('')}</div>`;
        },
    },
    list: {
        name: 'List', size: [6, 4], many: true, takes: () => true, hint: 'Names and their states, one per line',
        body(w) {
            if (!w.entities.length) return missing(w);
            return `<div class="rows noline">${w.entities.map(e => `<div class="row item"${tapable(e) ? ` data-tap="${esc(e)}"` : isNumeric(e) ? ` data-more="${esc(e)}"` : ''}>${NEVER_ON.has(dom(e)) ? '' : dot(isOn(e))}
                <span class="ttl cut">${esc(ename(e, w))}</span><span class="sub">${esc(stateText(e))}</span></div>`).join('')}</div>`;
        },
    },
    media: {
        name: 'Media', size: [9, 4], takes: e => dom(e) === 'media_player', hint: 'What is playing, pause and next',
        label(w) { const s = st(first(w)); return s ? cap(String(s.s)) : ''; },
        aux: w => esc(ename(first(w), w)),
        body(w) {
            const e = first(w), s = st(e);
            if (!s) return missing(w);
            const playing = s.s === 'playing';
            return `<div class="media${OFFISH.has(s.s) && s.s !== 'paused' ? ' off' : ''}">${s.a.entity_picture ? `<div class="media-art" style="background-image:url('api/picture?entity=${encodeURIComponent(e)}&v=${esc(s.a.entity_picture)}')"></div>` : '<div class="media-art"><i></i></div>'}<div class="media-info">
                <div class="ttl cut">${esc(s.a.media_title || 'Nothing playing')}</div><div class="sub cut">${esc([s.a.media_artist, s.a.media_album_name].filter(Boolean).join(' · '))}</div>
                <div class="media-btns"><button class="box" data-media="${esc(e)}" data-do="media_play_pause">${ic(playing ? 'pause' : 'play')}</button><button class="box" data-media="${esc(e)}" data-do="media_next_track">${ic('next')}</button>
                ${s.a.volume_level != null ? `<span class="lbl" style="margin-left:1rem" data-control="${esc(e)}">Volume <b>${Math.round(s.a.volume_level * 100)} %</b></span>` : ''}</div></div></div>`;
        },
    },
    bars: {
        name: 'Bars', size: [7, 3], many: true, takes: isNumeric, hint: 'Readings side by side: batteries, levels',
        body(w) {
            if (!w.entities.length) return missing(w);
            const percent = w.entities.every(e => attr(e, 'unit_of_measurement') === '%');
            const top = percent ? 100 : Math.max(...w.entities.map(e => Math.abs(num(e) || 0)), 1e-9);
            return `<div class="bars">${w.entities.map(e => `<div class="bar-row" data-more="${esc(e)}"><span class="lbl">${esc(ename(e, w))}</span>
                <span class="bar-track"><i style="width:${clamp(Math.abs(num(e) || 0) / top * 100, 0, 100)}%"></i></span><span class="bar-val">${esc(qtyText(e))}</span></div>`).join('')}</div>`;
        },
    },
    split: {
        name: 'Split', size: [12, 2], many: true, takes: isNumeric, hint: 'Parts of a whole: where the power comes from',
        aux(w) {
            const unit = attr(first(w), 'unit_of_measurement');
            return w.entities.length ? `Total <b>${esc(qtyTextOf(w.entities.reduce((sum, e) => sum + (num(e) || 0), 0), unit))}</b>` : '';
        },
        body(w) {
            if (!w.entities.length) return missing(w);
            const total = w.entities.reduce((sum, e) => sum + Math.max(0, num(e) || 0), 0);
            const shade = i => GREENS[i % GREENS.length];
            return `<div class="split"><div class="split-bar">${total ? w.entities.map((e, i) => `<i style="flex:${Math.max(0, num(e) || 0)};background:${shade(i)}"></i>`).join('') : '<i style="flex:1;background:var(--line)"></i>'}</div>
                <div class="split-legend">${w.entities.map((e, i) => `<span data-more="${esc(e)}"><i class="sq" style="background:${shade(i)}"></i><span class="lbl">${esc(ename(e, w))}</span><strong>${esc(qtyText(e))}</strong></span>`).join('')}</div></div>`;
        },
    },
    chart: {
        name: 'Chart', size: [10, 5], many: true, takes: isNumeric, hint: 'History as a line, or a bar per day',
        label: w => w.entities.length === 1 ? ename(first(w), w) : '',
        aux(w) {
            const hours = w.opts.kind === 'bars' ? Math.max(48, w.opts.hours || 168) : w.opts.hours || 24;
            return esc((RANGES.find(r => r[0] === hours) || [0, hours + ' h'])[1]) + (w.opts.kind === 'bars' && attr(first(w), 'unit_of_measurement') ? ` · <b>${esc(attr(first(w), 'unit_of_measurement'))}</b>` : '');
        },
        body: w => !w.entities.length || !st(first(w)) ? missing(w) : w.opts.kind === 'bars' ? barChart(w) : lineChart(w),
    },
    ...EXTRA_BLOCKS,
};
const tapable = e => [...TOGGLE, ...RUN].includes(dom(e));

// The block an entity becomes when it is dropped on an empty spot
function blockFor(e) {
    const d = dom(e);
    if (TOGGLE.includes(d)) return 'tiles';
    if (RUN.includes(d)) return 'actions';
    if (d === 'climate' || d === 'weather' || d === 'camera') return d;
    if (d === 'calendar') return 'agenda';
    if (d === 'media_player') return 'media';
    if (d === 'cover') return 'covers';
    if (d === 'number' || d === 'input_number') return 'slider';
    return isNumeric(e) ? 'value' : 'list';
}

// ---------- Drawing ----------
// A thermostat's new target waits a moment, so five taps are one call
const PENDING = {};
const findWidget = id => currentPage()?.widgets.find(w => w.id === id);
const currentPage = () => S.doc?.pages.find(p => p.id === S.page) || S.doc?.pages[0];

function widgetInner(w) {
    const block = BLOCKS[w.type] || BLOCKS.list;
    const label = w.label || (block.label ? block.label(w) : block.many ? block.name : '');
    const aux = block.aux ? block.aux(w) : '';
    const head = w.opts.nohead || (!label && !aux) ? '' : `<div class="wh"><span class="lbl">${esc(label)}</span><span class="lbl">${aux}</span></div>`;
    return `${head}<div class="wb">${block.body(w)}</div><i class="w-size"></i>`;
}
// A block's own colours: its accent, and a panel behind it with ink that reads on it
function blockLook(w) {
    let style = '', cls = '';
    const hex = v => /^#[0-9a-f]{6}$/i.test(v || '');
    if (hex(w.opts.color)) {
        const c = w.opts.color;
        style += `--accent:${c};--series:${c};--green:${c};--green2:color-mix(in srgb, ${c} 68%, var(--bg));--green3:color-mix(in srgb, ${c} 40%, var(--bg));--halo:color-mix(in srgb, ${c} 22%, transparent);`;
        if (w.opts.colornum) cls += ' numc';
    }
    if (hex(w.opts.bg)) {
        const n = parseInt(w.opts.bg.slice(1), 16);
        const light = ((n >> 16) * 299 + ((n >> 8) & 255) * 587 + (n & 255) * 114) / 1000 > 140;
        style += `--bg:${w.opts.bg};`;
        cls += ` panel tone-${light ? 'light' : 'dark'}`;
    }
    return {style, cls};
}
const inPhase = w => !w.opts.when?.length || w.opts.when.includes(phase());
function widgetHtml(w) {
    const look = blockLook(w);
    const rule = !look.cls.includes('panel') && (w.opts.rule ?? w.y > 0);
    return `<section class="w t-${esc(w.type)}${rule ? ' rule' : ''}${S.sel === w.id ? ' sel' : ''}${look.cls}${inPhase(w) ? '' : ' off-phase'}" data-id="${esc(w.id)}"
        style="grid-column:${w.x + 1}/span ${w.w};grid-row:${w.y + 1}/span ${w.h};--h:${w.h};${look.style}">${widgetInner(w)}</section>`;
}
function drawWidget(id) {
    const w = findWidget(id), el = $(`.w[data-id="${id}"]`);
    if (!w || !el || el.dataset.busy) return;
    el.innerHTML = widgetInner(w);
}
function renderPage() {
    const page = currentPage(), el = $('#page');
    el.style.gridTemplateColumns = `repeat(${S.doc.cols}, minmax(0, 1fr))`;
    el.style.gridTemplateRows = `repeat(${S.doc.rows}, minmax(0, 1fr))`;
    // In reading order, which is the order a phone shows them in
    const shown = page.widgets.filter(w => S.design || inPhase(w)).sort((a, b) => a.y - b.y || a.x - b.x);
    el.innerHTML = shown.map(widgetHtml).join('')
        || `<div class="splash"><div><div class="lbl">Empty page</div><p>${S.design ? 'Drag entities or blocks from the left onto the dots.' : 'Press the pencil to design it.'}</p></div></div>`;
}
function renderNav() {
    $('#tabs').innerHTML = S.doc.pages.map(p => `<button class="tab lbl${p.id === currentPage().id ? ' on' : ''}" data-page="${esc(p.id)}">${esc(p.name)}</button>`).join('')
        + (S.design ? '<button class="tab lbl tab-add" data-add-page title="Add a page">+</button>' : '');
    $('#chips').innerHTML = S.doc.chips.map((c, i) => {
        const e = c.entity, binary = !NEVER_ON.has(dom(e)) && !isNumeric(e);
        return `<span class="chip" data-chip="${i}"${isNumeric(e) ? ` data-more="${esc(e)}"` : ''}>${binary && isOn(e) ? dot(true) : ''}<span class="lbl">${esc(c.label || ename(e))}</span><span class="chipv">${esc(stateText(e))}</span></span>`;
    }).join('');
    $('#tools').innerHTML = S.design || KIOSK ? '' : `<button data-design title="Design this dashboard">${ic('pencil')}</button>
        <button data-cycle-theme title="${themeChoice === 'auto' ? 'Theme follows Home Assistant - click for light' : `Theme: ${themeChoice} - click to change`}">${ic({auto: 'theme', light: 'sun', dark: 'moon'}[themeChoice])}</button>`;
}

// Redraw what shows an entity that changed, at most once a frame
const dirty = new Set();
function touch(e) {
    if (!dirty.size) requestAnimationFrame(() => {
        const page = currentPage();
        if (page) for (const w of page.widgets) if (w.entities.some(x => dirty.has(x))) drawWidget(w.id);
        if (S.doc.chips.some(c => dirty.has(c.entity))) renderNav();
        if (POP && POP.entities.some(x => dirty.has(x))) refreshPop();
        dirty.clear();
    });
    dirty.add(e);
}

// ---------- Pressing things ----------
function flash(el) {
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 500);
}
document.addEventListener('click', ev => {
    if (S.design) return;
    if (held) { held = false; return; }
    const t = ev.target.closest('[data-tap],[data-cover],[data-media],[data-temp]');
    if (!t || !t.closest('#page, #pop')) return;
    flash(t);
    if (t.dataset.tap) tap(t.dataset.tap);
    else if (t.dataset.cover) t.dataset.cover.split(',').forEach(e => call(e, t.dataset.do));
    else if (t.dataset.media) call(t.dataset.media, t.dataset.do);
    else if (t.dataset.temp) {
        const e = t.dataset.temp, s = st(e);
        const step = Number(s.a.target_temp_step) || .5;
        const p = PENDING[e] || (PENDING[e] = {v: s.a.temperature ?? s.a.current_temperature ?? 20});
        p.v = clamp(Math.round((p.v + step * Number(t.dataset.step)) / step) * step, s.a.min_temp ?? 5, s.a.max_temp ?? 35);
        clearTimeout(p.timer);
        p.timer = setTimeout(async () => {
            await call(e, 'set_temperature', {temperature: p.v});
            setTimeout(() => { delete PENDING[e]; touch(e); }, 1500);
        }, 700);
        touch(e);
    }
});
// Sliders follow the finger and tell Home Assistant when it lets go
document.addEventListener('pointerdown', ev => {
    const sl = ev.target.closest('.sl');
    if (!sl || S.design) return;
    const e = sl.dataset.slide, host = sl.closest('.w, .pop-card'), row = sl.closest('.slider');
    let pct = 0;
    const move = m => {
        const r = sl.getBoundingClientRect();
        pct = clamp((m.clientX - r.left) / r.width * 100, 0, 100);
        $('.sl-knob', sl).style.left = pct + '%';
        $('.sl-fill', sl).style.width = `calc(${pct}% + 2rem)`;
        $('.sl-num', row).textContent = fmt(slideValue(e, pct));
    };
    const up = () => {
        sl.removeEventListener('pointermove', move);
        slideSet(e, pct);
        setTimeout(() => { delete host.dataset.busy; if (host.dataset.id) drawWidget(host.dataset.id); else refreshPop(); }, 1200);
    };
    host.dataset.busy = '1';
    sl.setPointerCapture(ev.pointerId);
    sl.addEventListener('pointermove', move);
    sl.addEventListener('pointerup', up, {once: true});
    sl.addEventListener('pointercancel', up, {once: true});
    move(ev);
});
