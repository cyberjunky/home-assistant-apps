// Garmin Connect dashboard - Today: one day's numbers against your usual, its curves and activities.

let viewDay = null;  // null = today
let funFacts = [];
let funIndex = 0;

const currentDay = () => viewDay || app.today || isoDay(new Date());

function setDay(day) {
    viewDay = day && day < (app.today || isoDay(new Date())) ? day : null;
    loadToday();
}
function shiftDay(n) { setDay(addDays(currentDay(), n)); }
function pickDay() {
    const overlay = openModal({
        title: 'Pick a day',
        bodyHTML: `<div class="form-field"><label>Day</label><input class="input" type="date" id="pick-day" value="${currentDay()}" max="${app.today}"></div>`,
        footHTML: '<button class="btn" data-close>Cancel</button><button class="btn btn-primary" id="pick-day-go">Show</button>',
    });
    overlay.querySelector('#pick-day-go').onclick = () => { const v = overlay.querySelector('#pick-day').value; closeModal(); if (v) setDay(v); };
}

// A value against the 30 days before: "▲ 6 vs usual", coloured only when higher or lower is clearly better
function versus(value, usual, better, digits, unitText) {
    if (missing(value) || missing(usual)) return '';
    const diff = value - usual;
    const small = Math.abs(diff) < Math.max(Math.abs(usual) * 0.03, Math.pow(10, -(digits || 0)));
    if (small) return '<span class="muted">about your usual</span>';
    const up = diff > 0;
    const good = better === 0 ? null : (up === (better > 0));
    const cls = good === null ? 'muted' : good ? 'good' : 'bad';
    return `<span class="${cls}">${up ? '▲' : '▼'} ${num(Math.abs(diff), digits || 0)}${unitText || ''}</span> <span class="muted">vs usual ${num(usual, digits || 0)}</span>`;
}

// A ring for "how far towards the goal"
function ring(value, goal, color) {
    const share = missing(value) || !goal ? 0 : Math.min(1, value / goal);
    const r = 15.5, c = 2 * Math.PI * r;
    return `<svg class="ring" viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="${r}" class="ring-track" style="stroke:${color}"/>`
        + `<circle cx="18" cy="18" r="${r}" class="ring-fill" style="stroke:${color};stroke-dasharray:${(share * c).toFixed(1)} ${c.toFixed(1)}"/></svg>`;
}

const HRV_STATUS = {1: 'poor', 2: 'low', 3: 'unbalanced', 4: 'balanced'};

function renderTodayTiles(data) {
    const v = data.values, t = {...data.typical};
    // Today isn't over: its totals can't be held against whole days
    const partial = data.day === app.today;
    if (partial) ['steps', 'kcal_active', 'im_moderate', 'bb_drained'].forEach(m => { t[m] = null; });
    const tiles = [];
    const stepsGoal = v.steps_goal || 10000;
    tiles.push(tile('Steps', `${ring(v.steps, stepsGoal, 'var(--series-1)')}${num(v.steps)}`,
        `${missing(v.steps) ? '' : `${num(Math.round((v.steps || 0) / stepsGoal * 100))}% of ${num(stepsGoal)}`}${partial ? ' so far' : ' · ' + versus(v.steps, t.steps, 1)}`, 'with-ring'));
    tiles.push(tile('Body Battery', missing(v.bb_max) ? '–' : `${num(v.bb_min)}<small>–</small>${num(v.bb_max)}`,
        missing(v.bb_charged) ? '' : `+${num(v.bb_charged)} charged · −${num(v.bb_drained)} drained`));
    tiles.push(tile('Sleep', missing(v.sleep_score) ? '–' : withUnit(num(v.sleep_score), 'score'),
        missing(v.sleep_s) ? '' : `${fmtDuration(v.sleep_s)} · ${versus(v.sleep_score, t.sleep_score, 1)}`));
    tiles.push(tile('Stress', missing(v.stress_avg) ? '–' : withUnit(num(v.stress_avg), 'avg'), versus(v.stress_avg, t.stress_avg, -1)));
    tiles.push(tile('Resting heart rate', withUnit(num(v.hr_resting), 'bpm'), versus(v.hr_resting, t.hr_resting, -1)));
    tiles.push(tile('Overnight HRV', withUnit(num(v.hrv_night), 'ms'),
        missing(v.hrv_night) ? '' : `${HRV_STATUS[v.hrv_status] || ''}${missing(v.hrv_low) ? '' : ` · normal ${num(v.hrv_low)}–${num(v.hrv_high)}`}`));
    tiles.push(tile('Training readiness', num(v.readiness), versus(v.readiness, t.readiness, 1)));
    const intensity = (v.im_moderate || 0) + 2 * (v.im_vigorous || 0);
    tiles.push(tile('Intensity minutes', withUnit(num(missing(v.im_moderate) ? null : intensity), 'min'),
        missing(v.im_moderate) ? '' : `${num(v.im_moderate)} moderate + ${num(v.im_vigorous)} vigorous ×2`));
    tiles.push(tile('Active calories', withUnit(num(v.kcal_active), 'kcal'), partial ? 'so far today' : versus(v.kcal_active, t.kcal_active, 0)));
    if (!missing(v.vo2max)) tiles.push(tile('VO2 max', num(v.vo2max, 1), missing(v.fitness_age) ? '' : `fitness age ${num(v.fitness_age)}`));
    if (!missing(v.weight_kg)) tiles.push(tile('Weight', withUnit(num(toWeight(v.weight_kg), 1), weightUnit()), missing(v.body_fat_pct) ? '' : `${num(v.body_fat_pct, 1)}% body fat`));
    document.getElementById('today-tiles').innerHTML = tiles.join('');
}

function renderTodayCurves(data) {
    const hr = curveSeries(data.curves.hr);
    lineChart(document.getElementById('today-hr'), {
        x: hr.x, series: [{name: 'Heart rate', color: 'var(--series-1)', values: hr.v}], height: 170, area: true,
        formatX: fmtMinute, formatY: v => `${num(v)} bpm`, xTicks: clockTicks(),
        empty: app.status && app.status.groups.find(g => g.key === 'intraday' && !g.enabled)
            ? 'Turn on the day curves (intraday) in the app configuration to see this'
            : 'No heart rate curve for this day (yet)',
    });
    // Stress and Body Battery share the 0-100 scale, so they go in one chart
    const minutes = [...new Set([...(data.curves.stress || []), ...(data.curves.bb || [])].map(p => p[0]))].sort((a, b) => a - b);
    const lookup = pts => { const m = new Map(pts || []); return minutes.map(x => m.has(x) ? m.get(x) : null); };
    lineChart(document.getElementById('today-stress'), {
        x: minutes, height: 170, zero: true,
        series: [{name: 'Stress', color: 'var(--series-2)', values: lookup(data.curves.stress)}, {name: 'Body Battery', color: 'var(--series-3)', values: lookup(data.curves.bb)}],
        formatX: fmtMinute, formatY: v => num(v), xTicks: clockTicks(), empty: 'No stress or Body Battery curve for this day (yet)',
    });
}

// The night: stages as a band over time, with SpO2 and heart rate below when Garmin measured them
const STAGE_COLORS = {deep: 'var(--seq-1)', light: 'var(--seq-3)', rem: 'var(--series-5)', awake: 'var(--series-4)'};
const STAGE_ROW = {awake: 0, rem: 1, light: 2, deep: 3};
function nightHtml(night) {
    if (!night || !night.stages || !night.stages.length) return '';
    const total = Math.max(...night.stages.map(s => s[1])) || 1;
    const clock = m => fmtClock(((night.start / 60 + (night.offset_min || 0) + m) / 60) % 24);
    const bars = night.stages.map(([a, b, stage]) => `<i style="left:${(a / total * 100).toFixed(2)}%;width:${Math.max(0.2, (b - a) / total * 100).toFixed(2)}%;top:${STAGE_ROW[stage] * 25}%;background:${STAGE_COLORS[stage]}" title="${stage} ${clock(a)}–${clock(b)}"></i>`).join('');
    return `<div class="hypno">${bars}</div><div class="hypno-axis"><span>${clock(0)}</span><span>${clock(total / 2)}</span><span>${clock(total)}</span></div>`;
}

function renderNightChart(night) {
    const el = document.getElementById('today-night');
    if (!el) return;
    const spo2 = (night && night.spo2) || [], hr = (night && night.hr) || [];
    if (!spo2.length && !hr.length) { el.innerHTML = ''; return; }
    const minutes = [...new Set([...spo2, ...hr].map(p => p[0]))].sort((a, b) => a - b);
    const at = pts => { const m = new Map(pts); return minutes.map(x => m.has(x) ? m.get(x) : null); };
    const clock = m => fmtClock(((night.start / 60 + (night.offset_min || 0) + m) / 60) % 24);
    const series = [];
    if (spo2.length) series.push({name: 'SpO2 %', color: 'var(--series-1)', values: at(spo2)});
    if (hr.length) series.push({name: 'Heart rate', color: 'var(--series-2)', values: at(hr)});
    lineChart(el, {x: minutes, series, height: 130, formatX: clock, formatY: (v, s) => series[s].name === 'SpO2 %' ? `${num(v)}%` : `${num(v)} bpm`,
        xTicks: timeTicks(minutes, 3, clock)});
    document.getElementById('today-night-legend').innerHTML = series.map(s => `<span><i class="line" style="background:${s.color}"></i>${s.name}</span>`).join('');
}

function renderTodaySleep(v, night) {
    const el = document.getElementById('today-sleep');
    if (missing(v.sleep_s)) { el.innerHTML = '<div class="empty-state">No sleep recorded for this night</div>'; return; }
    const stages = [['Deep', v.sleep_deep_s, 'var(--seq-1)'], ['Light', v.sleep_light_s, 'var(--seq-3)'], ['REM', v.sleep_rem_s, 'var(--series-5)'], ['Awake', v.sleep_awake_s, 'var(--series-4)']];
    const total = stages.reduce((a, s) => a + (s[1] || 0), 0) || 1;
    document.getElementById('today-sleep-hint').textContent = `${fmtClock(v.bedtime_h)} → ${fmtClock(v.waketime_h)}`;
    const need = v.sleep_need_min ? v.sleep_need_min * 60 : null;
    el.innerHTML = `
        <div class="share">${stages.filter(s => s[1]).map(s => `<div style="flex:${s[1]};background:${s[2]}" title="${s[0]} ${fmtDuration(s[1])}">${s[1] / total > 0.12 ? s[0] : ''}</div>`).join('')}</div>
        <div class="legend">${stages.map(s => `<span><i style="background:${s[2]}"></i>${s[0]} ${fmtDuration(s[1])}</span>`).join('')}</div>
        ${nightHtml(night)}<div id="today-night" class="chart" style="margin-top:8px"></div><div id="today-night-legend" class="legend"></div>
        <div class="facts three" style="margin-top:12px">
            <div class="fact"><div class="l">Asleep</div><div class="v">${fmtDuration(v.sleep_s)}</div></div>
            <div class="fact"><div class="l">Needed</div><div class="v">${need ? fmtDuration(need) : '–'}</div></div>
            <div class="fact"><div class="l">${need && v.sleep_s < need ? 'Short by' : 'Over by'}</div><div class="v">${need ? fmtDuration(Math.abs(need - v.sleep_s)) : '–'}</div></div>
        </div>`;
    renderNightChart(night);
}

function renderTodaySteps(points) {
    const el = document.getElementById('today-steps');
    const panel = el.closest('.panel');
    panel.hidden = !(points && points.length);
    if (panel.hidden) return;
    const per = new Map(points);
    const data = Array.from({length: 96}, (_, i) => ({label: i % 12 === 0 ? `${String(i / 4).padStart(2, '0')}:00` : '', tipTitle: `${fmtMinute(i * 15)}–${fmtMinute(i * 15 + 15)}`, values: [per.get(i * 15) || 0]}));
    barChart(el, {data, series: [{name: 'Steps', color: 'var(--series-1)'}], height: 140, format: v => `${num(v)} steps`, labelWidth: 4});
}

function activityRow(a) {
    const family = a.family || 'other';
    const meta = [a.start_local.slice(11, 16), fmtDuration(a.duration_s), a.avg_hr ? `${num(a.avg_hr)} bpm` : ''].filter(Boolean).join(' · ');
    return `<button type="button" class="activity-row" onclick="openActivity(${a.id})">
        <span class="ico"><i class="sport-dot" style="background:${SPORT_COLORS[family]}"></i></span>
        <span class="title">${escapeHtml(a.name || typeName(a.type))}</span>
        <span class="value">${a.distance_m ? fmtMeters(a.distance_m) : num(a.calories) + ' kcal'}</span>
        <span class="meta">${escapeHtml(meta)}</span>
        <span class="meta right">${a.training_load ? `load ${num(a.training_load)}` : ''}</span>
    </button>`;
}

async function renderOnThisDay(day) {
    const el = document.getElementById('today-history');
    const first = firstDay();
    const years = [];
    for (let back = 1; back <= 12; back++) {
        const then = `${Number(day.slice(0, 4)) - back}${day.slice(4)}`;
        if (then < first) break;
        years.push(then);
    }
    if (!years.length) { el.innerHTML = '<div class="empty-state">No earlier years recorded yet</div>'; return; }
    const rows = await Promise.all(years.map(async then => {
        try { return {then, data: await api(`day?date=${then}`)}; } catch (e) { return {then, data: null}; }
    }));
    el.innerHTML = rows.map(({then, data}) => {
        if (!data) return '';
        const v = data.values;
        const acts = data.activities.map(a => escapeHtml(a.name || typeName(a.type))).join(', ');
        return `<div class="info-row"><span class="info-label">${then.slice(0, 4)}</span><span class="info-value">${num(v.steps)} steps${missing(v.sleep_score) ? '' : ` · sleep ${num(v.sleep_score)}`}${acts ? ` · ${acts}` : ''}</span></div>`;
    }).join('') || '<div class="empty-state">Nothing recorded on this date before</div>';
}

// Totals turned into something to picture
function buildFunFacts(f) {
    const facts = [];
    if (!f || !f.days) return facts;
    const since = fmtDayY(firstDay());
    if (f.km) facts.push(`Since ${since} you've walked and run ${fmtKm(f.km, 0)} on your watch: <b>${num(f.earth_laps * 100, 1)}%</b> of the way around the Earth${f.moon_share > 0.001 ? `, or ${num(f.moon_share * 100, 2)}% of the way to the Moon` : ''}.`);
    if (f.km) facts.push(`That distance is <b>${num(f.marathons)} marathons</b>. Without the medals, but also without the blisters.`);
    if (f.steps) facts.push(`<b>${num(f.steps)} steps</b>. At about 0.6 seconds a step, that's ${fmtDuration(f.steps * 0.6)} of nothing but walking.`);
    if (f.floors) facts.push(`You climbed <b>${num(f.floors)} floors</b>: Mount Everest ${num(f.everests, 1)} times over, from sea level.`);
    if (f.climb_m) facts.push(`Floors and activity climbs together: <b>${num(f.climb_m)} m</b> up, ${num(f.eiffel_towers)} Eiffel Towers.`);
    if (f.active_kcal) facts.push(`You burned <b>${num(f.active_kcal)} active kcal</b>: ${num(f.pizzas)} large pizzas, ${num(f.beers)} beers, or ${num(f.fat_kg, 1)} kg of pure body fat.`);
    if (f.sleep_h) facts.push(`You slept <b>${num(f.sleep_h)} hours</b> over ${num(f.sleep_nights)} nights${f.sleep_years >= 0.1 ? `: ${num(f.sleep_years, 2)} years` : ''}, about ${num(f.sleep_h / Math.max(1, f.sleep_nights), 1)} h a night.`);
    if (f.heartbeats) facts.push(`Your heart beat about <b>${num(f.heartbeats / 1e6, 1)} million times</b> while the watch was on.`);
    if (f.high_stress_h && f.rest_h) facts.push(`<b>${num(f.high_stress_h)} hours</b> of high stress against ${num(f.rest_h)} hours of rest: ${num(f.rest_h / Math.max(1, f.high_stress_h))} hours of rest for every stressful one.`);
    if (f.vigorous_min) facts.push(`<b>${num(f.vigorous_min + f.moderate_min)} intensity minutes</b>, ${num(f.vigorous_min)} of them vigorous. The WHO asks 150 a week; you averaged ${num((f.moderate_min + 2 * f.vigorous_min) / Math.max(1, f.days) * 7)}.`);
    if (f.activities && f.activities.count) facts.push(`<b>${num(f.activities.count)} activities</b>, ${fmtDuration(f.activities.duration_s || 0)} of sport in total.`);
    return facts;
}

function nextFunFact() {
    const el = document.getElementById('today-fun');
    if (!funFacts.length) { el.innerHTML = '<div class="empty-state">Fun facts appear once there is some history</div>'; return; }
    funIndex = (funIndex + 1) % funFacts.length;
    el.innerHTML = `<p>${funFacts[funIndex]}</p>`;
}

async function loadToday() {
    const day = currentDay();
    document.getElementById('day-label').textContent = day === app.today ? `Today · ${fmtDayShort(day)}` : fmtDay(day);
    document.getElementById('day-today').hidden = !viewDay;
    try {
        const data = await api(`day?date=${day}`);
        renderTodayTiles(data);
        renderTodayCurves(data);
        renderTodaySleep(data.values, data.night);
        renderTodaySteps(data.curves.steps);
        const acts = document.getElementById('today-acts');
        data.activities.forEach(a => { a.family = sportFamily(a.type); });
        acts.innerHTML = data.activities.length ? data.activities.map(activityRow).join('') : '<div class="empty-state">No activities this day</div>';
        const empty = Object.values(data.values).every(missing);
        document.getElementById('day-note').textContent = empty
            ? (app.account.signed_in ? 'Nothing fetched for this day yet' : 'Sign in to fetch your data') : '';
        renderOnThisDay(day);
        if (!funFacts.length) {
            const all = await api(`insights?start=${firstDay()}&end=${app.today}`);
            funFacts = buildFunFacts(all.fun);
            funIndex = Math.floor(Math.random() * Math.max(1, funFacts.length)) - 1;
            nextFunFact();
        }
    } catch (e) {
        toast(e.message, 'error');
    }
}

// The same grouping of sports as the server's stats.family()
function sportFamily(type) {
    const key = (type || '').toLowerCase();
    const groups = [
        ['running', ['running', 'run']], ['cycling', ['cycling', 'biking', 'ride', 'bmx', 'gravel', 'mountain']],
        ['walking', ['walking', 'hiking', 'walk', 'hike']], ['swimming', ['swim']],
        ['strength', ['strength', 'hiit', 'fitness_equipment', 'cardio', 'yoga', 'pilates', 'training']],
    ];
    const hit = groups.find(([, words]) => words.some(w => key.includes(w)));
    return hit ? hit[0] : 'other';
}

onTab('today', loadToday);
onStatus(changed => { if (changed && activeTab === 'today') { funFacts = []; loadToday(); } });
