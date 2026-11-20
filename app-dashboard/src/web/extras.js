// Dashboard - what goes beyond a block on a page: popups, cameras and the doorbell, the agenda,
// the weather ahead, and what the tablet does by itself (time of day, night, going back home).
'use strict';

const KIOSK = new URLSearchParams(location.search).has('kiosk');

// ---------- The time of day ----------
const PHASES = ['morning', 'day', 'evening', 'night'];
const PHASE_START = {morning: '06:00', day: '10:00', evening: '17:00', night: '23:00'};
const tablet = () => S.doc?.tablet || {};
// Which part of the day it is: each one runs from its own start to the next one's
function phase() {
    if (S.design && S.previewPhase) return S.previewPhase;
    const starts = {...PHASE_START, ...tablet().phases};
    const now = new Date(), clock = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
    const order = PHASES.map(name => [starts[name], name]).sort();
    let current = order[order.length - 1][1];
    for (const [start, name] of order) if (clock >= start) current = name;
    return current;
}
// At night the tablet can go dark and dim, and show one page; a touch wakes it for a while
const AWAKE_MINUTES = 3;
let awakeUntil = 0, lastTouch = Date.now(), lastPhase = null;
const isNight = () => phase() === 'night';
const asleep = () => !S.design && isNight() && Date.now() > awakeUntil;
function wantsDark() {
    const mode = tablet().night?.dark;
    if (mode === 'sun') return st('sun.sun')?.s === 'below_horizon';
    return mode === 'night' && isNight();
}
function applyNight() {
    const night = tablet().night || {};
    $('#dim').style.opacity = asleep() ? clamp(Number(night.dim) || 0, 0, 90) / 100 : 0;
    applyTheme();
    if (asleep() && night.page && S.page !== night.page && S.doc.pages.some(p => p.id === night.page)) showPage(night.page);
}
function tick() {
    if (!S.doc) return;
    const now = phase() + (asleep() ? '-asleep' : '');
    if (now !== lastPhase) {
        const first = lastPhase === null;
        lastPhase = now;
        applyNight();
        if (!first) renderAll();
    }
    // Left alone long enough, the tablet goes back to its first page
    const idle = tablet().idle || {};
    if (!S.design && idle.minutes > 0 && Date.now() - lastTouch > idle.minutes * 60000) {
        lastTouch = Date.now();
        const home = S.doc.pages.find(p => p.id === idle.page) || S.doc.pages[0];
        if (!asleep() && !POP && S.page !== home.id) showPage(home.id);
    }
}
setInterval(tick, 5000);
document.addEventListener('pointerdown', () => {
    lastTouch = Date.now();
    if (S.doc && isNight() && !S.design) {
        const was = asleep();
        awakeUntil = Date.now() + AWAKE_MINUTES * 60000;
        if (was) tick();
    }
}, true);

// ---------- Popups ----------
// One at a time: {entities: what it shows, render: its HTML, close: tidying up}
let POP = null, popTimer;
function openPop(pop) {
    closePop();
    POP = {entities: [], ...pop};
    const card = $('#pop .pop-card');
    card.className = 'pop-card' + (pop.wide ? ' wide' : '');
    card.innerHTML = POP.render();
    $('#pop').hidden = false;
    if (pop.seconds) popTimer = setTimeout(closePop, pop.seconds * 1000);
}
function refreshPop() {
    const card = $('#pop .pop-card');
    if (POP && !card.dataset.busy && !POP.still) card.innerHTML = POP.render();
}
function closePop() {
    clearTimeout(popTimer);
    if (!POP) return;
    if (POP.close) POP.close();
    POP = null;
    $('#pop').hidden = true;
    // A live camera stops when its picture goes
    $$('#pop img').forEach(img => { img.src = ''; });
    $('#pop .pop-card').innerHTML = '';
}
const popHead = (label, title, sub) => `<span class="lbl">${esc(label)}</span><h2 class="ttl">${esc(title)}</h2>${sub ? `<p class="sub">${esc(sub)}</p>` : ''}`;
const popStats = stats => `<div class="pop-stats">${stats.map(([label, text, unit]) => `<div><span class="lbl">${label}</span><span class="num">${esc(text)}<small>${esc(unit || '')}</small></span></div>`).join('')}</div>`;
const popButtons = (buttons, current) => `<div class="pop-btns">${buttons.map(([value, text]) => `<button class="box${String(value) === String(current) ? ' on' : ''}" data-pop="${esc(value)}">${text}</button>`).join('')}<button class="box" data-pop-close>Close</button></div>`;

// What an entity did lately: a line, or a bar per day for a counter such as energy
function openDetail(e, hours) {
    if (!isNumeric(e)) return;
    const counter = isCounter(e);
    hours = hours || (counter ? 168 : 24);
    const unit = attr(e, 'unit_of_measurement');
    const of = v => { const q = quantity(v, unit); return [q.text, q.unit]; };
    openPop({
        entities: [e], wide: true, pick: value => openDetail(e, Number(value)),
        render() {
            let stats, plot;
            if (counter) {
                const bars = barPlot(e, hours, refreshPop), days = (bars.points || []).filter(p => p[1] != null);
                const total = days.reduce((sum, p) => sum + p[1], 0), best = days.reduce((a, b) => (b[1] > (a?.[1] ?? -1) ? b : a), null);
                stats = !days.length ? [] : [['Total', ...of(total)], ['Best day', `${new Date(best[0] * 1000).toLocaleDateString(LOCALE, {weekday: 'short'})} · ${of(best[1])[0]}`, of(best[1])[1]],
                    ['Average a day', ...of(total / days.length)], ['Today', ...of(days[days.length - 1][1])]];
                plot = bars.html;
            } else {
                const line = linePlot([e], hours, refreshPop), v = line.values;
                stats = !v ? [['Now', ...of(num(e))]] : [['Now', ...of(num(e))], ['Lowest', ...of(Math.min(...v))], ['Highest', ...of(Math.max(...v))], ['Average', ...of(v.reduce((a, b) => a + b, 0) / v.length)]];
                plot = `<div class="ch">${line.html}</div>`;
            }
            const ranges = counter ? [[168, '7 days'], [336, '14 days'], [720, '30 days']] : [[24, '24 hours'], [168, '7 days'], [720, '30 days']];
            return popHead(S.areas[e] || cap(String(attr(e, 'device_class') || dom(e)).replace(/_/g, ' ')), ename(e)) + popStats(stats) + `<div class="pop-plot">${plot}</div>` + popButtons(ranges, hours);
        },
    });
}

// A long press on a light, blind, fan or speaker: its slider
function openControl(e) {
    if (!SLIDE.includes(dom(e)) || (dom(e) === 'light' && !lightDims(e))) return isNumeric(e) ? openDetail(e) : null;
    openPop({
        entities: [e],
        render() {
            const g = slideGet(e), d = dom(e);
            const buttons = d === 'cover' ? `<button class="box" data-cover="${esc(e)}" data-do="open_cover">${ic('up')} Up</button><button class="box" data-cover="${esc(e)}" data-do="stop_cover">Stop</button><button class="box" data-cover="${esc(e)}" data-do="close_cover">${ic('down')} Down</button>`
                : d === 'media_player' ? `<button class="box" data-media="${esc(e)}" data-do="media_play_pause">${ic(st(e)?.s === 'playing' ? 'pause' : 'play')}</button><button class="box" data-media="${esc(e)}" data-do="media_next_track">${ic('next')}</button>`
                : ['light', 'fan'].includes(d) ? `<button class="box" data-tap="${esc(e)}">${dot(isOn(e))} ${isOn(e) ? 'Turn off' : 'Turn on'}</button>` : '';
            return popHead(S.areas[e] || cap(d.replace(/_/g, ' ')), ename(e)) + `<div class="sliders pop-slider"><div class="slider"><div class="slider-read"><span class="lbl">${d === 'media_player' ? 'Volume' : d === 'cover' ? 'Open' : d === 'light' ? 'Brightness' : 'Set to'}</span><span class="num"><span class="sl-num">${fmt(slideValue(e, g.pct))}</span><small>${esc(g.unit)}</small></span></div>
                <div class="sl" data-slide="${esc(e)}"><i class="sl-fill" style="width:calc(${g.pct}% + 2rem)"></i><span class="box sl-knob" style="left:${g.pct}%"></span></div></div></div><div class="pop-btns">${buttons}<button class="box" data-pop-close>Close</button></div>`;
        },
    });
}
const lightDims = e => (attr(e, 'supported_color_modes') || []).some(mode => mode !== 'onoff');

let held = false, holdTimer;
document.addEventListener('pointerdown', ev => {
    const t = ev.target.closest('[data-hold]');
    clearTimeout(holdTimer);
    if (!t || S.design) return;
    held = false;
    holdTimer = setTimeout(() => { held = true; openControl(t.dataset.hold); }, 550);
});
for (const kind of ['pointerup', 'pointercancel', 'pointermove']) document.addEventListener(kind, ev => {
    if (kind !== 'pointermove' || Math.abs(ev.movementX) + Math.abs(ev.movementY) > 6) clearTimeout(holdTimer);
});
document.addEventListener('click', ev => {
    if (S.design) return;
    if (ev.target.id === 'pop' || ev.target.closest('[data-pop-close]')) return closePop();
    const pick = ev.target.closest('[data-pop]');
    if (pick && POP?.pick) return POP.pick(pick.dataset.pop);
    if (ev.target.closest('[data-tap],[data-cover],[data-media],[data-temp]')) return null;
    const control = ev.target.closest('[data-control]');
    if (control) return openControl(control.dataset.control);
    const cam = ev.target.closest('[data-cam]');
    if (cam) return openCamera(cam.dataset.cam);
    const weather = ev.target.closest('[data-weather]');
    if (weather) return openWeather(weather.dataset.weather);
    const more = ev.target.closest('[data-more]');
    if (more && more.closest('#page, #nav')) return openDetail(more.dataset.more);
    return null;
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && POP) closePop(); });

// ---------- Cameras ----------
// A still picture that renews itself; a tap shows it live
const camUrl = e => `api/camera?entity=${encodeURIComponent(e)}&t=${Date.now()}`;
setInterval(() => {
    for (const img of $$('.cam img, .pop-cam img[data-still]')) {
        const every = Number(img.dataset.every) * 1000;
        if (Date.now() - Number(img.dataset.at) < every || img.dataset.loading) continue;
        img.dataset.loading = '1';
        const next = new Image();
        next.onload = next.onerror = () => {
            delete img.dataset.loading;
            img.dataset.at = Date.now();
            if (next.naturalWidth) img.src = next.src;
        };
        next.src = camUrl(img.dataset.entity);
    }
}, 500);
function openCamera(e, opts = {}) {
    openPop({
        wide: true, still: true, seconds: opts.seconds,
        render: () => popHead(opts.label || S.areas[e] || 'Camera', opts.title || ename(e)) + `<div class="pop-cam"><img src="api/camera-stream?entity=${encodeURIComponent(e)}" data-entity="${esc(e)}" alt=""
            onerror="this.onerror=null;this.dataset.still=1;this.dataset.every=1;this.dataset.at=0;this.src='${camUrl(e)}'"></div><div class="pop-btns"><button class="box" data-pop-close>Close</button></div>`,
    });
}
// The doorbell: when its entity goes on (or an event entity fires), every tablet shows the camera
function stateChanged(e, before, after) {
    const bell = tablet().doorbell || {};
    if (S.design || !bell.trigger || e !== bell.trigger || !st(bell.camera) || !after) return;
    const rang = dom(e) === 'event' ? before && before.s !== after.s && after.s !== 'unavailable' : !OFFISH.has(after.s) && (!before || OFFISH.has(before.s));
    if (!rang) return;
    awakeUntil = Date.now() + AWAKE_MINUTES * 60000;
    tick();
    openCamera(bell.camera, {label: ename(e), title: 'Someone at the door', seconds: clamp(Number(bell.seconds) || 60, 5, 600)});
}

// ---------- The weather ahead ----------
const CONDITION = {
    'clear-night': ['Clear', 'moon'], cloudy: ['Cloudy', 'cloud'], fog: ['Fog', 'fog'], hail: ['Hail', 'rain'], lightning: ['Thunder', 'storm'],
    'lightning-rainy': ['Thunder and rain', 'storm'], partlycloudy: ['Partly cloudy', 'partly'], pouring: ['Heavy rain', 'rain'], rainy: ['Rain', 'rain'],
    snowy: ['Snow', 'snow'], 'snowy-rainy': ['Sleet', 'snow'], sunny: ['Sunny', 'sun'], windy: ['Windy', 'wind'], 'windy-variant': ['Windy', 'wind'], exceptional: ['Exceptional', 'cloud'],
};
const conditionText = s => (CONDITION[s] || [cap(String(s).replace(/[-_]/g, ' '))])[0];
const conditionIcon = s => (CONDITION[s] || [0, 'cloud'])[1];
Object.assign(ICONS, {
    cloud: '<path d="M7 18h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.1 11.6 3.3 3.3 0 0 0 7 18z"/>',
    partly: '<path d="M8 19h9a3.5 3.5 0 0 0 .5-6.96A5.2 5.2 0 0 0 7.6 13.3 2.9 2.9 0 0 0 8 19z"/><path d="M9.5 8.2A3.6 3.6 0 0 1 16 9.6M12.5 3.4v1.5M7.2 5.4l1 1M18 5.6l-1 1"/>',
    rain: '<path d="M7 15h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.1 8.6 3.3 3.3 0 0 0 7 15z"/><path d="M8.5 18l-1 2.5M12.5 18l-1 2.5M16.5 18l-1 2.5"/>',
    snow: '<path d="M7 15h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.1 8.6 3.3 3.3 0 0 0 7 15z"/><path d="M8 19h.01M12 19h.01M16 19h.01M10 21.5h.01M14 21.5h.01"/>',
    storm: '<path d="M7 15h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.1 8.6 3.3 3.3 0 0 0 7 15z"/><path d="M12.5 15.5l-2 3.5h3l-2 3.5"/>',
    fog: '<path d="M7 13h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.1 6.6 3.3 3.3 0 0 0 7 13zM5 16.5h14M7 20h10"/>',
    wind: '<path d="M3 9h10a2.5 2.5 0 1 0-2.5-2.5M3 13h15a2.5 2.5 0 1 1-2.5 2.5M3 17h7"/>',
    camera: '<path d="M4 8h3l1.5-2h7L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
});
const FORECASTS = new Map();
function forecast(e, kind, done) {
    const key = e + '|' + kind;
    let f = FORECASTS.get(key);
    if (!f) FORECASTS.set(key, f = {items: null, at: 0, busy: false});
    if (!f.busy && Date.now() - f.at > 15 * 60000) {
        f.busy = true;
        api(`forecast?entity=${encodeURIComponent(e)}&type=${kind}`).then(r => { f.items = r.forecast; }, () => { f.items = f.items || []; })
            .finally(() => { f.busy = false; f.at = Date.now(); if (done) done(); });
    }
    return f.items;
}
function forecastRow(items, kind, count) {
    const picked = kind === 'hourly' ? items.filter((_, i) => i % 3 === 0).slice(0, count) : items.slice(0, count);
    return `<div class="fc">${picked.map((f, i) => {
        const t = new Date(f.datetime);
        const when = kind === 'hourly' ? t.toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'}) : i === 0 && t.toDateString() === new Date().toDateString() ? 'Today' : t.toLocaleDateString(LOCALE, {weekday: 'short'});
        return `<div class="fc-col"><span class="lbl">${when}</span>${ic(conditionIcon(f.condition))}<span class="fc-hi">${fmt(f.temperature, 0)}°</span>${f.templow != null ? `<span class="fc-lo">${fmt(f.templow, 0)}°</span>` : ''}
            ${f.precipitation ? `<span class="fc-rain">${fmt(f.precipitation, 1)} mm</span>` : '<span class="fc-rain">&nbsp;</span>'}</div>`;
    }).join('')}</div>`;
}
function openWeather(e, kind = 'hourly') {
    openPop({
        entities: [e], wide: true, pick: value => openWeather(e, value),
        render() {
            const s = st(e), items = forecast(e, kind, refreshPop);
            const stats = [['Now', fmt(s?.a.temperature, 1), s?.a.temperature_unit || '°']];
            if (s?.a.humidity != null) stats.push(['Humidity', String(s.a.humidity), '%']);
            if (s?.a.wind_speed != null) stats.push(['Wind', fmt(s.a.wind_speed, 0), s.a.wind_speed_unit || '']);
            if (s?.a.pressure != null) stats.push(['Pressure', fmt(s.a.pressure, 0), s.a.pressure_unit || '']);
            const body = items === null ? '<div class="empty">Reading the forecast…</div>' : items.length ? forecastRow(items, kind, 8) : '<div class="empty">This weather service gives no forecast like this</div>';
            return popHead('Weather', conditionText(s?.s)) + popStats(stats) + `<div class="pop-plot">${body}</div>` + popButtons([['hourly', 'Next 24 hours'], ['daily', 'Days ahead']], kind);
        },
    });
}

// ---------- The agenda ----------
const AGENDAS = new Map();
function agenda(entities, days, done, peek) {
    const key = entities.join(',') + '|' + days;
    let a = AGENDAS.get(key);
    if (peek) return a ? a.events : null;
    if (!a) AGENDAS.set(key, a = {events: null, at: 0, busy: false});
    if (!a.busy && Date.now() - a.at > 5 * 60000) {
        a.busy = true;
        api(`calendar?entities=${encodeURIComponent(entities.join(','))}&days=${days}`).then(r => { a.events = r.events; }, () => { a.events = a.events || []; })
            .finally(() => { a.busy = false; a.at = Date.now(); if (done) done(); });
    }
    return a.events;
}
const dayKey = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
function dayName(key) {
    const today = new Date(), tomorrow = new Date(Date.now() + 86400000);
    if (key === dayKey(today)) return 'Today';
    if (key === dayKey(tomorrow)) return 'Tomorrow';
    return new Date(key + 'T12:00').toLocaleDateString(LOCALE, {weekday: 'long', day: 'numeric', month: 'short'});
}
function untilText(start) {
    const minutes = Math.round((start - Date.now()) / 60000);
    if (minutes <= 0) return 'now';
    if (minutes < 90) return `in ${minutes} min`;
    if (minutes < 24 * 60) return `in ${Math.round(minutes / 60)} h`;
    return '';
}
const CAL_COLORS = ['var(--ink)', 'var(--accent)', '#4c7bd9', 'var(--green)', '#b0862b', '#8a5fbf'];
const EXTRA_BLOCKS = {
    agenda: {
        name: 'Agenda', size: [8, 8], many: true, takes: e => dom(e) === 'calendar', hint: 'What is coming up in your calendars',
        label: w => dayName(dayKey(new Date())),
        aux(w) {
            const events = w.entities.length ? agenda(w.entities, w.opts.days || 2, null, true) : null;
            const today = (events || []).filter(ev => ev.start.slice(0, 10) === dayKey(new Date())).length;
            return events ? `<b>${today}</b> ${today === 1 ? 'event' : 'events'}` : '';
        },
        body(w) {
            if (!w.entities.length) return missing(w);
            const events = agenda(w.entities, w.opts.days || 2, () => drawWidget(w.id));
            if (events === null) return '<div class="empty">Reading the calendars…</div>';
            const now = Date.now(), today = dayKey(new Date());
            // What is over is gone; all-day events stay for their day
            const coming = events.filter(ev => ev.all_day ? ev.end > today : new Date(ev.end || ev.start) > now);
            if (!coming.length) return '<div class="empty">Nothing planned</div>';
            const time = ev => ev.all_day ? '' : new Date(ev.start).toLocaleTimeString(LOCALE, {hour: '2-digit', minute: '2-digit'});
            const sq = ev => `<i class="sq" style="background:${CAL_COLORS[w.entities.indexOf(ev.entity) % CAL_COLORS.length]}"></i>`;
            const next = coming.find(ev => !ev.all_day && ev.start.slice(0, 10) === today) || null;
            let day = today, html = '';
            if (next) html += `<div class="ag-next"><div class="ag-big"><span class="num">${time(next)}</span> <span class="ttl">${esc(next.summary)}</span></div>
                <div class="ag-sub">${sq(next)}<span class="cut">${esc([ename(next.entity, w), next.location].filter(Boolean).join(' · '))}</span>${untilText(new Date(next.start)) ? `<b>${untilText(new Date(next.start))}</b>` : ''}</div></div>`;
            for (const ev of coming) {
                if (ev === next) continue;
                const key = ev.start.slice(0, 10) < today ? today : ev.start.slice(0, 10);
                if (key !== day) { day = key; html += `<div class="ag-day lbl">${esc(dayName(key))}</div>`; }
                html += `<div class="ag-row"><span class="ag-time">${time(ev)}</span><div class="ag-what"><div class="ttl cut">${esc(ev.summary)}</div>
                    <div class="ag-where">${sq(ev)}<span class="lbl">${esc(ename(ev.entity, w))}</span><span class="sub cut">${esc(ev.location || (ev.all_day ? 'All day' : ''))}</span></div></div></div>`;
            }
            return `<div class="agenda">${html}</div>`;
        },
    },
    camera: {
        name: 'Camera', size: [8, 5], many: true, takes: e => dom(e) === 'camera', hint: 'A picture that renews itself; tap for live',
        label: w => w.entities.length === 1 ? ename(first(w), w) : 'Cameras',
        body(w) {
            if (!w.entities.length) return missing(w);
            const cols = Math.ceil(Math.sqrt(w.entities.length * (w.w * 76) / (w.h * 64) / 1.6)) || 1;
            return `<div class="cams" style="grid-template-columns:repeat(${Math.min(cols, w.entities.length)},1fr)">${w.entities.map(e => st(e)
                ? `<div class="cam press" data-cam="${esc(e)}"><img src="${camUrl(e)}" data-entity="${esc(e)}" data-every="${clamp(Number(w.opts.every) || 10, 1, 300)}" data-at="${Date.now()}" alt="">${w.entities.length > 1 ? `<span class="lbl">${esc(ename(e, w))}</span>` : ''}</div>`
                : `<div class="cam"><span class="gone">${esc(e)} is not in Home Assistant</span></div>`).join('')}</div>`;
        },
    },
};
