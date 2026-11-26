// Garmin Connect dashboard - Insights: what the numbers say together. Streaks, what goes with a good
// night, how regular your nights are, days your body looked off, your average day and fun totals.

const insightsPeriod = periodPicker('insights', '12m', () => loadInsights());
let insightsLoadedAt = 0;

const OUTCOME_NAMES = {sleep_score: 'sleep score', hrv_night: 'overnight HRV', hr_resting: 'resting heart rate', sleep_s: 'sleep', readiness: 'training readiness'};
// For which outcomes higher is better (resting heart rate: lower is better)
const OUTCOME_BETTER = {sleep_score: 1, hrv_night: 1, hr_resting: -1, sleep_s: 1, readiness: 1};
const FACTOR_NAMES = {
    steps: 'steps', stress_avg: 'stress', kcal_active: 'active calories', im_vigorous: 'vigorous minutes', bb_drained: 'Body Battery drained',
    food_kcal: 'food eaten', hydration_ml: 'water drunk', workout: 'a workout', late_workout: 'a late workout',
};

function fmtOutcome(outcome, v) {
    if (outcome === 'sleep_s') return fmtDuration(v);
    if (outcome === 'hrv_night') return `${num(v)} ms`;
    if (outcome === 'hr_resting') return `${num(v, 1)} bpm`;
    return num(v);
}

function renderFunTiles(f) {
    const el = document.getElementById('fun-tiles');
    if (!f || !f.days) { el.innerHTML = ''; return; }
    const cards = [
        ['🌍', fmtKm(f.km, 0), `walked and run: ${num(f.earth_laps * 100, 1)}% around the Earth`],
        ['🏃', num(f.marathons), 'marathons, distance-wise'],
        ['🏔️', num(f.everests, 1), `Everests up the stairs (${num(f.floors)} floors)`],
        ['🍕', num(f.pizzas), `pizzas burned off (${num(f.active_kcal)} active kcal)`],
        ['😴', f.sleep_years >= 0.1 ? `${num(f.sleep_years, 2)} yr` : `${num(f.sleep_h)} h`, `asleep, ${num(f.sleep_h / Math.max(1, f.sleep_nights), 1)} h a night`],
        ['❤️', f.heartbeats ? `${num(f.heartbeats / 1e6, 1)} M` : '–', f.heartbeats ? 'heartbeats while worn' : 'heartbeats (needs the day curves)'],
        ['🧘', `${num(f.rest_h)} h`, `of rest vs ${num(f.high_stress_h)} h of high stress`],
        ['🔥', num(f.fat_kg, 1) + ' kg', 'of fat worth of active calories'],
    ];
    el.innerHTML = cards.map(([icon, v, l]) => `<div class="fun-card"><span class="fun-icon" aria-hidden="true">${icon}</span><div><div class="v">${v}</div><div class="l">${escapeHtml(l)}</div></div></div>`).join('');
}

function renderStreaks(s) {
    const el = document.getElementById('streaks');
    if (!s || !s.worn) { el.innerHTML = '<div class="empty-state">No steps recorded in this period</div>'; return; }
    document.getElementById('streak-hint').textContent = `goal hit on ${num(s.hit / s.worn * 100)}% of the days`;
    el.innerHTML = `<div class="facts three">
        <div class="fact"><div class="l">Current streak</div><div class="v big">${num(s.current)}<small> days</small></div></div>
        <div class="fact"><div class="l">Longest</div><div class="v big">${num(s.longest)}<small> days</small></div></div>
        <div class="fact"><div class="l">Goal hit</div><div class="v big">${num(s.hit)}<small> / ${num(s.worn)}</small></div></div>
    </div>${s.longest_end ? `<p class="panel-sub" style="margin-top:10px">Your longest streak ended ${fmtDayY(s.longest_end)}${s.current && s.current >= s.longest ? ': it is the one you are on now. Keep going!' : '.'}</p>` : ''}`;
}

function renderSleepRhythm(r) {
    const el = document.getElementById('sleep-rhythm');
    if (!r || !r.nights) { el.innerHTML = '<div class="empty-state">No nights recorded in this period</div>'; return; }
    const jet = r.social_jetlag_h;
    const jetText = missing(jet) ? '–' : `${num(Math.abs(jet) * 60)} min ${jet >= 0 ? 'later' : 'earlier'}`;
    el.innerHTML = `<div class="facts">
        <div class="fact"><div class="l">Bedtime, work days</div><div class="v">${fmtClock(r.bedtime_work)}</div></div>
        <div class="fact"><div class="l">Bedtime, weekends</div><div class="v">${fmtClock(r.bedtime_free)}</div></div>
        <div class="fact"><div class="l">Up, work days</div><div class="v">${fmtClock(r.wake_work)}</div></div>
        <div class="fact"><div class="l">Up, weekends</div><div class="v">${fmtClock(r.wake_free)}</div></div>
        <div class="fact"><div class="l">Social jetlag</div><div class="v ${Math.abs(jet || 0) > 1 ? 'warn' : ''}">${jetText}</div></div>
        <div class="fact"><div class="l">Bedtime spread</div><div class="v">± ${num(r.bedtime_spread_min)} min</div></div>
        <div class="fact"><div class="l">Sleep debt</div><div class="v ${(r.debt_h || 0) > 0 ? 'warn' : ''}">${missing(r.debt_h) ? '–' : `${num(Math.abs(r.debt_h))} h ${r.debt_h > 0 ? 'short' : 'extra'}`}</div></div>
        <div class="fact"><div class="l">You are</div><div class="v">${escapeHtml(r.chronotype || '–')}</div></div>
    </div>
    <p class="panel-sub" style="margin-top:10px">Social jetlag: how much later the middle of your sleep falls on weekends than on work days. More than an hour is like flying a time zone west every Friday and back every Monday. Sleep debt adds up what Garmin said you needed minus what you slept.</p>`;
}

function renderEffects(effects) {
    const el = document.getElementById('effects');
    // Differences too small to notice stay out, however steady they are
    const noticeable = {sleep_score: 2, hrv_night: 1.5, hr_resting: 1, sleep_s: 600, readiness: 3};
    const shown = (effects || []).filter(e => Math.abs(e.size) >= 0.15 && Math.abs(e.difference) >= (noticeable[e.outcome] || 0)).slice(0, 10);
    if (!shown.length) { el.innerHTML = '<div class="empty-state">Not enough days yet to see what goes together (it needs a few weeks of sleep and day data)</div>'; return; }
    const max = Math.max(...shown.map(e => Math.abs(e.size)));
    el.innerHTML = '<div class="effects">' + shown.map(e => {
        const better = OUTCOME_BETTER[e.outcome] * e.difference > 0;
        const factor = FACTOR_NAMES[e.factor] || e.factor;
        const when = e.kind === 'yesno' ? `After days ${e.high}` : `After days with ${factor} ${e.high}`;
        const vs = e.kind === 'yesno' ? e.low : `${e.low}`;
        return `<div class="effect">
            <div class="effect-text">${escapeHtml(when)}, your ${OUTCOME_NAMES[e.outcome]} is <b>${fmtOutcome(e.outcome, e.with)}</b> against ${fmtOutcome(e.outcome, e.without)} (${escapeHtml(vs)})</div>
            <div class="effect-bar"><i class="${better ? 'good' : 'bad'}" style="width:${(Math.abs(e.size) / max * 100).toFixed(0)}%"></i></div>
            <div class="effect-diff ${better ? 'good' : 'bad'}">${e.difference > 0 ? '+' : '−'}${fmtOutcome(e.outcome, Math.abs(e.difference))}</div>
        </div>`;
    }).join('') + '</div><p class="panel-sub" style="margin-top:10px">The bar shows how big the difference is next to your normal ups and downs. Days are split at the middle value of the period; the night after counts.</p>';
}

function renderAlerts(alerts) {
    const el = document.getElementById('alerts');
    if (!alerts || !alerts.length) { el.innerHTML = '<div class="empty-state">No days stood out. Your resting heart rate stayed close to its own normal.</div>'; return; }
    el.innerHTML = alerts.slice(0, 8).map(a => `<button type="button" class="alert-row" onclick="openDay('${a.day}')">
        <span class="alert-day">${fmtDayY(a.day)}</span><span class="alert-signs">${a.signs.map(escapeHtml).join(' · ')}</span><span class="alert-rise">+${num(a.rise, 1)}</span>
    </button>`).join('') + (alerts.length > 8 ? `<p class="panel-sub" style="margin-top:8px">and ${alerts.length - 8} more in this period</p>` : '');
}

function renderWeekdays(w) {
    const el = document.getElementById('weekdays');
    const rows = [['steps', 'Steps', v => num(v)], ['sleep_score', 'Sleep score (night before)', v => num(v)], ['stress_avg', 'Stress', v => num(v)], ['bb_charged', 'Body Battery recharge', v => num(v)]];
    const any = rows.some(([m]) => (w[m] || []).some(v => !missing(v)));
    if (!any) { el.innerHTML = '<div class="empty-state">No data in this period</div>'; return; }
    el.innerHTML = rows.map(([m, label, fmt]) => {
        const values = w[m] || [];
        const known = values.filter(v => !missing(v));
        if (!known.length) return '';
        const max = Math.max(...known), min = Math.min(...known);
        const best = values.indexOf(m === 'stress_avg' ? min : max);
        return `<div class="week-row"><span class="week-label">${escapeHtml(label)}</span><div class="week-bars">${values.map((v, i) => `
            <div class="week-day${i === best ? ' best' : ''}" title="${WEEKDAYS[i]}: ${missing(v) ? '–' : fmt(v)}"><i style="height:${missing(v) ? 0 : Math.max(8, (v - min * 0.8) / ((max - min * 0.8) || 1) * 100)}%"></i><span>${WEEKDAYS[i][0]}</span></div>`).join('')}</div></div>`;
    }).join('');
}

function renderRhythm(r) {
    document.getElementById('rhythm-hint').textContent = r && r.days ? `per hour, over ${num(r.days)} days` : '';
    const el = document.getElementById('rhythm-day');
    if (!r || !r.days) {
        emptyChart(el, 'Needs the day curves: turn on "intraday" in the app configuration and give the import some time');
        heatGrid(document.getElementById('stress-grid'), [], v => v);
        return;
    }
    const x = Array.from({length: 24}, (_, h) => h);
    lineChart(el, {
        x, height: 190,
        series: [{name: 'Heart rate', color: 'var(--series-1)', values: r.hr}, {name: 'Stress', color: 'var(--series-2)', values: r.stress}, {name: 'Body Battery', color: 'var(--series-3)', values: r.bb}],
        formatX: h => `${String(h).padStart(2, '0')}:00 – ${String(h + 1).padStart(2, '0')}:00`, formatY: (v, s) => s === 0 ? `${num(v)} bpm` : num(v),
        xTicks: [0, 3, 6, 9, 12, 15, 18, 21, 23].map(h => ({at: h, label: `${String(h).padStart(2, '0')}:00`})),
    });
    heatGrid(document.getElementById('stress-grid'), r.stress_grid || [], v => `stress ${num(v)}`);
}

function renderMoving(r) {
    const el = document.getElementById('steps-grid');
    el.closest('.panel').hidden = !(r && r.steps_grid);
    if (r && r.steps_grid) heatGrid(el, r.steps_grid, v => `${num(v)} steps`);
}

function renderBests(bests) {
    const el = document.getElementById('bests');
    const fmt = {steps: v => num(v), floors_up: v => num(v), kcal_active: v => `${num(v)} kcal`, sleep_s: v => fmtDuration(v), sleep_score: v => num(v),
        hr_resting: v => `${num(v)} bpm`, hrv_night: v => `${num(v)} ms`, bb_charged: v => `+${num(v)}`, stress_avg: v => num(v), vo2max: v => num(v, 1), readiness: v => num(v)};
    el.innerHTML = (bests || []).map(b => `<button type="button" class="record" onclick="openDay('${b.day}')">
        <div class="l">${escapeHtml(b.label)}</div><div class="v">${(fmt[b.metric] || num)(b.value)}</div><div class="d">${fmtDay(b.day)}</div></button>`).join('')
        || '<div class="empty-state">No data in this period</div>';
}

// ----- The calendar: every day of the period as a square ------------
const CALENDAR_METRICS = [['steps', 'Steps'], ['sleep_score', 'Sleep score'], ['stress_avg', 'Stress'], ['hr_resting', 'Resting heart rate'], ['bb_charged', 'Body Battery recharge'], ['kcal_active', 'Active calories'], ['im_vigorous', 'Vigorous minutes']];
async function loadCalendar() {
    const select = document.getElementById('calendar-metric');
    if (!select.options.length) {
        select.innerHTML = CALENDAR_METRICS.map(([k, l]) => `<option value="${k}">${l}</option>`).join('');
        select.value = localStorage.getItem(LS + 'calendar_metric') || 'steps';
    }
    localStorage.setItem(LS + 'calendar_metric', select.value);
    const range = insightsPeriod.days();
    // At most two years of squares, ending at the end of the period
    const start = range.start < addDays(range.end, -730) ? addDays(range.end, -730) : range.start;
    const data = await api(`calendar?metric=${select.value}&start=${start}&end=${range.end}`);
    renderCalendar(document.getElementById('calendar'), data.values, start, range.end, select.value);
}

function renderCalendar(el, values, start, end, metric) {
    const known = Object.values(values);
    if (!known.length) { el.innerHTML = '<div class="empty-state">No data in this period</div>'; return; }
    const sorted = known.slice().sort((a, b) => a - b);
    const q = p => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
    const cuts = [q(0.2), q(0.4), q(0.6), q(0.8)];
    // Lower is better for these: the darkest square is the best day either way
    const invert = metric === 'stress_avg' || metric === 'hr_resting';
    const first = parseDay(start);
    first.setDate(first.getDate() - ((first.getDay() + 6) % 7));
    let html = '<div class="cal">', week = '', month = -1, months = '';
    for (let d = new Date(first), col = 0; isoDay(d) <= end; col++) {
        week = '';
        for (let w = 0; w < 7; w++, d.setDate(d.getDate() + 1)) {
            const day = isoDay(d);
            if (w === 0 && d.getMonth() !== month) {
                month = d.getMonth();
                // A month that starts in the first column of a part-month is left unlabelled: no overlap
                if (col > 0 || d.getDate() <= 7) months += `<span style="grid-column:${col + 1} / span 3">${d.toLocaleDateString([], {month: 'short'})}</span>`;
            }
            if (day < start || day > end) { week += '<i class="out"></i>'; continue; }
            const v = values[day];
            let level = 0;
            if (!missing(v)) { level = 1 + cuts.filter(c => v > c).length; if (invert) level = 6 - level; }
            week += `<i class="${level ? 'l' + level : ''}" title="${fmtDay(day)}: ${missing(v) ? 'no data' : num(v, metric === 'stress_avg' ? 0 : 0)}" data-day="${day}"></i>`;
        }
        html += `<div class="cal-week">${week}</div>`;
    }
    el.innerHTML = `<div class="cal-months" style="grid-template-columns:repeat(${Math.ceil((parseDay(end) - first) / 604800000) + 1}, 13px)">${months}</div>${html}</div>
        <div class="legend cal-legend"><span>${invert ? 'more' : 'less'}</span>${[1, 2, 3, 4, 5].map(l => `<i class="cal-key l${l}"></i>`).join('')}<span>${invert ? 'less' : 'more'}</span></div>`;
    el.querySelector('.cal').onclick = e => { const day = e.target.dataset && e.target.dataset.day; if (day) openDay(day); };
}

// ----- Year over year ----------------------------------------------
const YOY_METRICS = [['steps', 'Steps a day'], ['sleep_score', 'Sleep score'], ['hr_resting', 'Resting heart rate'], ['stress_avg', 'Stress'], ['hrv_night', 'Overnight HRV'], ['vo2max', 'VO2 max'], ['weight_kg', 'Weight']];
async function loadYoy() {
    const select = document.getElementById('yoy-metric');
    if (!select.options.length) {
        select.innerHTML = YOY_METRICS.map(([k, l]) => `<option value="${k}">${l}</option>`).join('');
        select.value = localStorage.getItem(LS + 'yoy_metric') || 'steps';
    }
    localStorage.setItem(LS + 'yoy_metric', select.value);
    const data = await api(`yoy?metric=${select.value}`);
    const years = Object.keys(data.years).sort();
    const colors = ['var(--seq-1)', 'var(--seq-2)', 'var(--seq-3)', 'var(--seq-4)', 'var(--series-5)', 'var(--series-4)', 'var(--series-3)', 'var(--series-2)'];
    // The newest year stands out; older ones fade into the ramp
    const series = years.map((y, i) => ({name: y, color: i === years.length - 1 ? 'var(--series-2)' : colors[(i + colors.length - years.length + 1) % colors.length], width: i === years.length - 1 ? 2.6 : 1.6,
        values: data.years[y].map(v => missing(v) ? null : select.value === 'weight_kg' ? toWeight(v) : v)}));
    const months = Array.from({length: 12}, (_, i) => i);
    lineChart(document.getElementById('yoy'), {
        x: months, series, height: 200,
        formatX: m => new Date(2000, m, 1).toLocaleDateString([], {month: 'long'}), formatY: v => num(v, select.value === 'vo2max' || select.value === 'weight_kg' ? 1 : 0),
        xTicks: months.filter(m => m % 2 === 0).map(m => ({at: m, label: new Date(2000, m, 1).toLocaleDateString([], {month: 'short'})})),
        empty: 'No data yet',
    });
    document.getElementById('yoy-legend').innerHTML = series.map(s => `<span><i class="line" style="background:${s.color}"></i>${s.name}</span>`).join('');
}

function openDay(day) {
    setDay(day);
    switchTab('today');
}

async function loadInsights() {
    insightsPeriod.render();
    const range = insightsPeriod.days();
    document.getElementById('insights-range-text').textContent = rangeText(range);
    insightsLoadedAt = Date.now();
    const grid = document.getElementById('insights-grid');
    grid.classList.add('loading');
    try {
        const [data] = await Promise.all([api(`insights?start=${range.start}&end=${range.end}`), loadCalendar(), loadYoy()]);
        renderFunTiles(data.fun);
        renderStreaks(data.streak);
        renderSleepRhythm(data.sleep_rhythm);
        renderEffects(data.effects);
        renderAlerts(data.alerts);
        renderWeekdays(data.weekdays);
        renderRhythm(data.rhythm);
        renderMoving(data.rhythm);
        renderBests(data.bests);
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        grid.classList.remove('loading');
    }
}

onTab('insights', loadInsights);
onStatus(changed => { if (changed && activeTab === 'insights' && Date.now() - insightsLoadedAt > 120000) loadInsights(); });
