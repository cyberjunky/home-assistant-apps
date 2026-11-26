// Garmin Connect dashboard - Health: every daily value over a period, per day, week or month.

const H = 3600;
// The charts, in order. kind "bar" stacks its series; "line" shares one axis. scale turns a stored value
// into the shown one; avg makes week and month bars a daily average instead of a total.
const HEALTH_CHARTS = [
    {id: 'steps', title: 'Steps', kind: 'bar', wide: true, avg: true, goal: 'steps_goal', series: [{m: 'steps', name: 'Steps', color: 'var(--series-1)'}], fmt: v => num(v), hint: 'per day · dashed: your goal'},
    {id: 'rhr', title: 'Resting heart rate', kind: 'line', series: [{m: 'hr_resting', name: 'Resting', color: 'var(--series-2)'}], fmt: v => `${num(v)} bpm`, trend: true},
    {id: 'hrv', title: 'Overnight HRV', kind: 'line', band: ['hrv_low', 'hrv_high'], series: [{m: 'hrv_night', name: 'Last night', color: 'var(--series-1)'}, {m: 'hrv_weekly', name: '7-day average', color: 'var(--series-3)', dashed: true}], fmt: v => `${num(v)} ms`, hint: 'shaded: your normal range'},
    {id: 'sleep', title: 'Sleep', kind: 'bar', avg: true, series: [{m: 'sleep_deep_s', name: 'Deep', color: 'var(--seq-1)', scale: 1 / H}, {m: 'sleep_light_s', name: 'Light', color: 'var(--seq-3)', scale: 1 / H}, {m: 'sleep_rem_s', name: 'REM', color: 'var(--series-5)', scale: 1 / H}, {m: 'sleep_awake_s', name: 'Awake', color: 'var(--series-4)', scale: 1 / H}], fmt: v => `${num(v, 1)} h`, hint: 'hours a night'},
    {id: 'sleep_score', title: 'Sleep score', kind: 'line', series: [{m: 'sleep_score', name: 'Score', color: 'var(--series-1)'}], fmt: v => num(v), trend: true},
    {id: 'bedtime', title: 'Bedtime and waking up', kind: 'line', series: [{m: 'bedtime_h', name: 'Asleep', color: 'var(--series-5)'}, {m: 'waketime_h', name: 'Awake', color: 'var(--series-4)', shift: 24}], fmt: v => fmtClock(v), tick: v => fmtClock(v), hint: 'clock time'},
    {id: 'stress', title: 'Stress', kind: 'bar', avg: true, series: [{m: 'stress_rest_s', name: 'Rest', color: 'var(--series-3)', scale: 1 / H}, {m: 'stress_low_s', name: 'Low', color: 'var(--seq-4)', scale: 1 / H}, {m: 'stress_medium_s', name: 'Medium', color: 'var(--series-4)', scale: 1 / H}, {m: 'stress_high_s', name: 'High', color: 'var(--series-2)', scale: 1 / H}], fmt: v => `${num(v, 1)} h`, hint: 'hours a day in each level'},
    {id: 'bb', title: 'Body Battery', kind: 'line', band: ['bb_min', 'bb_max'], series: [{m: 'bb_charged', name: 'Charged', color: 'var(--series-3)'}, {m: 'bb_drained', name: 'Drained', color: 'var(--series-2)'}], fmt: v => num(v), hint: 'shaded: the day\'s low to high', zero: true},
    {id: 'intensity', title: 'Intensity minutes', kind: 'bar', forceBucket: 'week', series: [{m: 'im_moderate', name: 'Moderate', color: 'var(--series-3)'}, {m: 'im_vigorous', name: 'Vigorous (counts double)', color: 'var(--series-2)', scale: 2}], fmt: v => `${num(v)} min`, hint: 'per week · dashed: 150 minutes', fixedGoal: 150},
    {id: 'calories', title: 'Calories', kind: 'bar', avg: true, series: [{m: 'kcal_resting', name: 'Resting', color: 'var(--seq-2)'}, {m: 'kcal_active', name: 'Active', color: 'var(--series-2)'}], fmt: v => `${num(v)} kcal`, hint: 'per day'},
    {id: 'floors', title: 'Floors climbed', kind: 'bar', avg: true, series: [{m: 'floors_up', name: 'Floors', color: 'var(--series-4)'}], fmt: v => num(v), goal: 'floors_goal'},
    {id: 'readiness', title: 'Training readiness', kind: 'line', series: [{m: 'readiness', name: 'Readiness', color: 'var(--series-1)'}], fmt: v => num(v), zero: true},
    {id: 'load', title: 'Training load', kind: 'line', series: [{m: 'load_acute', name: 'Acute (7 days)', color: 'var(--series-2)'}, {m: 'load_chronic', name: 'Chronic (28 days)', color: 'var(--series-1)'}], fmt: v => num(v), hint: 'acute above chronic: building up'},
    {id: 'vo2', title: 'VO2 max', kind: 'line', series: [{m: 'vo2max', name: 'Running / generic', color: 'var(--series-1)'}, {m: 'vo2max_cycling', name: 'Cycling', color: 'var(--series-2)'}], fmt: v => num(v, 1)},
    {id: 'fitness_age', title: 'Fitness age', kind: 'line', series: [{m: 'fitness_age', name: 'Fitness age', color: 'var(--series-3)'}, {m: 'fitness_age_achievable', name: 'Achievable', color: 'var(--series-4)', dashed: true}], fmt: v => num(v, 1), ageLine: true, hint: 'grey: your real age'},
    {id: 'race', title: 'Race predictions', kind: 'line', series: [{m: 'race_5k_s', name: '5 km', color: 'var(--series-1)'}, {m: 'race_10k_s', name: '10 km', color: 'var(--series-2)'}], fmt: v => fmtRaceTime(v), tick: v => fmtRaceTime(v)},
    {id: 'race_long', title: 'Race predictions, long', kind: 'line', series: [{m: 'race_half_s', name: 'Half marathon', color: 'var(--series-3)'}, {m: 'race_marathon_s', name: 'Marathon', color: 'var(--series-4)'}], fmt: v => fmtRaceTime(v), tick: v => fmtRaceTime(v)},
    {id: 'weight', title: 'Weight', kind: 'line', series: [{m: 'weight_kg', name: 'Weight', color: 'var(--series-1)', conv: toWeight}], fmt: v => `${num(v, 1)} ${weightUnit()}`, connect: true},
    {id: 'body_fat', title: 'Body composition', kind: 'line', series: [{m: 'body_fat_pct', name: 'Body fat', color: 'var(--series-2)'}, {m: 'body_water_pct', name: 'Body water', color: 'var(--series-1)'}], fmt: v => `${num(v, 1)}%`, connect: true},
    {id: 'bp', title: 'Blood pressure', kind: 'line', series: [{m: 'bp_systolic', name: 'Systolic', color: 'var(--series-2)'}, {m: 'bp_diastolic', name: 'Diastolic', color: 'var(--series-1)'}], fmt: v => `${num(v)} mmHg`, connect: true},
    {id: 'resp', title: 'Respiration', kind: 'line', series: [{m: 'resp_avg', name: 'Day average', color: 'var(--series-1)'}, {m: 'sleep_resp', name: 'Asleep', color: 'var(--series-5)'}], fmt: v => `${num(v, 1)} brpm`},
    {id: 'spo2', title: 'Pulse Ox', kind: 'line', series: [{m: 'spo2_avg', name: 'Average', color: 'var(--series-1)'}, {m: 'spo2_min', name: 'Lowest', color: 'var(--series-2)'}], fmt: v => `${num(v)}%`},
    {id: 'hydration', title: 'Hydration', kind: 'bar', avg: true, series: [{m: 'hydration_ml', name: 'Drank', color: 'var(--series-1)', scale: 1 / 1000}], fmt: v => `${num(v, 1)} l`, hint: 'litres per day'},
    {id: 'skin', title: 'Skin temperature', kind: 'line', series: [{m: 'skin_temp_c', name: 'Change from normal', color: 'var(--series-2)'}], fmt: v => `${v > 0 ? '+' : ''}${num(v, 1)} °C`, hint: 'overnight, against your baseline'},
    {id: 'nutrition', title: 'Food (Connect+)', kind: 'bar', avg: true, series: [{m: 'food_kcal', name: 'Eaten', color: 'var(--series-4)'}], fmt: v => `${num(v)} kcal`, goal: 'food_kcal_goal'},
    {id: 'acclimation', title: 'Heat acclimation', kind: 'line', series: [{m: 'heat_acclimation', name: 'Heat', color: 'var(--series-2)'}], fmt: v => `${num(v)}%`},
];

const healthPeriod = periodPicker('health', '90d', () => loadHealth());
let healthBucket = localStorage.getItem(LS + 'health_bucket') || 'auto';

function healthMetrics() {
    const set = new Set();
    HEALTH_CHARTS.forEach(c => {
        c.series.forEach(s => set.add(s.m));
        (c.band || []).forEach(m => set.add(m));
        if (c.goal) set.add(c.goal);
    });
    set.add('stress_avg');
    return [...set];
}

// A straight line through the points (least squares) for "going up or down" over the period
function trendOf(values) {
    const pts = values.map((v, i) => [i, v]).filter(p => !missing(p[1]));
    if (pts.length < 6) return null;
    const n = pts.length, sx = pts.reduce((a, p) => a + p[0], 0), sy = pts.reduce((a, p) => a + p[1], 0);
    const sxx = pts.reduce((a, p) => a + p[0] * p[0], 0), sxy = pts.reduce((a, p) => a + p[0] * p[1], 0);
    const slope = (n * sxy - sx * sy) / ((n * sxx - sx * sx) || 1);
    const start = (sy - slope * sx) / n;
    return {values: values.map((_, i) => start + slope * i), change: slope * (values.length - 1)};
}

// Weigh-ins and the like are occasional: draw the line through the days between them
function fillGaps(values) {
    return values.map((v, i) => missing(v) ? interpolate(values, i) : v);
}
function interpolate(values, i) {
    let a = i - 1, b = i + 1;
    while (a >= 0 && missing(values[a])) a--;
    while (b < values.length && missing(values[b])) b++;
    if (a < 0 || b >= values.length) return null;
    return values[a] + (values[b] - values[a]) * (i - a) / (b - a);
}

function renderHealthChart(chart, data, bucket) {
    const el = document.getElementById('hc-' + chart.id);
    const buckets = data.buckets;
    const values = s => {
        // Totals per week or month as the average day where that reads better
        const raw = (chart.avg ? data.means : data.series)[s.m] || [];
        return raw.map(v => {
            if (missing(v)) return null;
            let x = v * (s.scale || 1);
            if (s.conv) x = s.conv(x);
            if (s.shift) x += s.shift;
            return x;
        });
    };
    const hasData = chart.series.some(s => (data.series[s.m] || []).some(v => !missing(v)));
    el.closest('.panel').hidden = !hasData && chart.optional !== false && !['steps', 'rhr', 'sleep', 'stress'].includes(chart.id);
    if (!hasData) { emptyChart(el, 'No data in this period'); return; }
    if (chart.kind === 'bar') {
        const rows = chart.series.map(values);
        const goalRow = chart.goal ? (data.means[chart.goal] || []).map(v => missing(v) ? null : v) : chart.fixedGoal ? buckets.map(() => chart.fixedGoal) : null;
        barChart(el, {
            data: buckets.map((b, i) => ({label: bucketLabel(b, bucket), tipTitle: bucketTitle(b, bucket), values: rows.map(r => r[i] || 0)})),
            series: chart.series.map(s => ({name: s.name, color: s.color})),
            height: chart.wide ? 200 : 180, format: chart.fmt, goal: goalRow,
        });
    } else {
        const x = buckets.map(b => parseDay(b).getTime() / 1000);
        const series = chart.series.map(s => {
            const v = values(s);
            return {name: s.name, color: s.color, dashed: s.dashed, values: chart.connect ? fillGaps(v) : v};
        });
        if (chart.trend && bucket === 'day') {
            const t = trendOf(series[0].values);
            if (t) series.push({name: 'Trend', color: 'var(--muted-text-color)', dashed: true, width: 1.2, values: t.values});
        }
        if (chart.ageLine && app.account.birth_date) {
            const birth = parseDay(app.account.birth_date);
            series.push({name: 'Your age', color: 'var(--muted-text-color)', width: 1.2, values: x.map(ts => (ts * 1000 - birth) / (365.25 * 86400000))});
        }
        const band = chart.band ? {lo: (data.series[chart.band[0]] || []), hi: (data.series[chart.band[1]] || []), color: chart.series[0].color} : null;
        lineChart(el, {
            x, series, band, height: 170, zero: chart.zero,
            formatX: ts => bucketTitle(isoDay(new Date(ts * 1000)), bucket), formatY: chart.fmt, formatTick: chart.tick,
            xTicks: timeTicks(x, 4, ts => bucketLabel(isoDay(new Date(ts * 1000)), bucket)),
        });
    }
    const legend = el.parentElement.querySelector('.legend');
    if (legend) legend.innerHTML = chart.series.length > 1 ? chart.series.map(s => `<span><i class="${chart.kind === 'line' ? 'line' : ''}" style="background:${s.color}"></i>${escapeHtml(s.name)}</span>`).join('') : '';
}

function buildHealthGrid() {
    const grid = document.getElementById('health-grid');
    if (grid.childElementCount) return;
    grid.innerHTML = HEALTH_CHARTS.map(c => `
        <div class="panel${c.wide ? ' wide' : ''}"><div class="panel-body">
            <div class="chart-title"><span>${escapeHtml(c.title)}</span><span class="hint">${escapeHtml(c.hint || '')}</span></div>
            <div id="hc-${c.id}" class="chart"></div><div class="legend"></div>
        </div></div>`).join('') + `
        <div class="panel wide"><div class="panel-body">
            <div class="chart-title"><span>Explore</span><span class="hint">any value that was fetched, also from the optional data sets</span></div>
            <div class="filters" style="margin-bottom:8px"><select class="car-select" id="explore-a" aria-label="First value" onchange="loadExplore()"></select>
            <select class="car-select" id="explore-b" aria-label="Second value" onchange="loadExplore()"></select></div>
            <div id="explore" class="chart"></div><div class="legend" id="explore-legend"></div>
        </div></div>`;
}

let healthLoadedAt = 0;
async function loadHealth() {
    buildHealthGrid();
    healthPeriod.render();
    const range = healthPeriod.days();
    const bucket = healthBucket === 'auto' ? autoBucket(range) : healthBucket;
    buildChips('health-bucket', [{key: 'auto', label: 'Auto'}, ...BUCKETS], healthBucket, (key) => {
        healthBucket = key;
        localStorage.setItem(LS + 'health_bucket', key);
        loadHealth();
    });
    document.getElementById('health-range-text').textContent = rangeText(range);
    healthLoadedAt = Date.now();
    const grid = document.getElementById('health-grid');
    grid.classList.add('loading');
    try {
        const metrics = healthMetrics();
        // The API takes a limited number of metrics per request
        const parts = [];
        for (let i = 0; i < metrics.length; i += 16) parts.push(metrics.slice(i, i + 16));
        const answers = await Promise.all(parts.map(p => api(`series?metrics=${p.join(',')}&start=${range.start}&end=${range.end}&bucket=${bucket}`)));
        const weekly = HEALTH_CHARTS.some(c => c.forceBucket) && bucket !== 'week'
            ? await api(`series?metrics=im_moderate,im_vigorous&start=${range.start}&end=${range.end}&bucket=week`) : null;
        const data = {buckets: answers[0].buckets, series: Object.assign({}, ...answers.map(a => a.series)), means: Object.assign({}, ...answers.map(a => a.means))};
        HEALTH_CHARTS.forEach(chart => {
            if (chart.forceBucket && weekly) renderHealthChart(chart, weekly, 'week');
            else renderHealthChart(chart, data, chart.forceBucket || bucket);
        });
        renderHealthTiles(data);
        await loadExploreChoices();
        loadExplore();
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        grid.classList.remove('loading');
    }
}

function renderHealthTiles(data) {
    // The average day of the period, weighed by the days each bucket has data for
    const avg = m => { const v = (data.means[m] || []).filter(x => !missing(x)); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
    document.getElementById('health-tiles').innerHTML = [
        tile('Steps a day', num(avg('steps')), 'average'),
        tile('Resting heart rate', withUnit(num(avg('hr_resting')), 'bpm'), 'average'),
        tile('Sleep score', num(avg('sleep_score')), 'average'),
        tile('Overnight HRV', withUnit(num(avg('hrv_night')), 'ms'), 'average'),
        tile('Stress', num(avg('stress_avg')), 'average level'),
        tile('Body Battery recharge', withUnit(num(avg('bb_charged')), 'a night'), 'average'),
    ].join('');
}

// ----- Explore: any metric at all ------------------------------------
let exploreCatalog = null;
async function loadExploreChoices() {
    if (exploreCatalog) return;
    exploreCatalog = await api('metrics');
    const options = exploreCatalog.map(m => `<option value="${escapeHtml(m.metric)}">${escapeHtml(metricName(m.metric))} (${num(m.days)} days)</option>`).join('');
    const a = document.getElementById('explore-a'), b = document.getElementById('explore-b');
    a.innerHTML = options;
    b.innerHTML = '<option value="">– second value –</option>' + options;
    a.value = localStorage.getItem(LS + 'explore_a') || 'hr_resting';
    b.value = localStorage.getItem(LS + 'explore_b') || '';
}

const METRIC_NAMES = {
    steps: 'Steps', distance_m: 'Distance walked (m)', floors_up: 'Floors climbed', kcal_active: 'Active calories', kcal_total: 'Total calories',
    hr_resting: 'Resting heart rate', hr_min: 'Lowest heart rate', hr_max: 'Highest heart rate', hr_avg: 'Average heart rate (worn)', stress_avg: 'Stress',
    bb_min: 'Body Battery low', bb_max: 'Body Battery high', bb_charged: 'Body Battery charged', bb_drained: 'Body Battery drained',
    sleep_score: 'Sleep score', sleep_s: 'Sleep (s)', hrv_night: 'Overnight HRV', readiness: 'Training readiness', vo2max: 'VO2 max',
    weight_kg: 'Weight (kg)', body_fat_pct: 'Body fat %', resp_avg: 'Respiration', spo2_avg: 'Pulse Ox', fitness_age: 'Fitness age',
    bedtime_h: 'Bedtime (h)', waketime_h: 'Wake-up time (h)', load_acute: 'Acute load', load_chronic: 'Chronic load', endurance: 'Endurance score', hill: 'Hill score',
};
const metricName = m => METRIC_NAMES[m] || m.replace(/[._]/g, ' ');

async function loadExplore() {
    const a = document.getElementById('explore-a').value, b = document.getElementById('explore-b').value;
    if (!a) return;
    localStorage.setItem(LS + 'explore_a', a);
    localStorage.setItem(LS + 'explore_b', b);
    const range = healthPeriod.days();
    const bucket = healthBucket === 'auto' ? autoBucket(range) : healthBucket;
    const metrics = [a, b].filter(Boolean);
    const data = await api(`series?metrics=${metrics.join(',')}&start=${range.start}&end=${range.end}&bucket=${bucket}`);
    const x = data.buckets.map(d => parseDay(d).getTime() / 1000);
    // Two values with different units can't share an axis: the second is scaled onto the first
    let second = b ? data.series[b] : null;
    let note = '';
    if (second) {
        const ra = data.series[a].filter(v => !missing(v)), rb = second.filter(v => !missing(v));
        if (ra.length && rb.length) {
            const [amin, amax, bmin, bmax] = [Math.min(...ra), Math.max(...ra), Math.min(...rb), Math.max(...rb)];
            if (bmax !== bmin && (bmax > amax * 3 || bmax < amax / 3)) {
                second = second.map(v => missing(v) ? null : amin + (v - bmin) / (bmax - bmin) * (amax - amin));
                note = ' (scaled to fit)';
            }
        }
    }
    const series = [{name: metricName(a), color: 'var(--series-1)', values: data.series[a]}];
    if (second) series.push({name: metricName(b) + note, color: 'var(--series-2)', values: second});
    lineChart(document.getElementById('explore'), {
        x, series, height: 190,
        formatX: ts => bucketTitle(isoDay(new Date(ts * 1000)), bucket),
        formatY: (v, s) => s === 1 && note ? 'scaled' : num(v, Math.abs(v) < 10 ? 1 : 0),
        xTicks: timeTicks(x, 5, ts => bucketLabel(isoDay(new Date(ts * 1000)), bucket)),
    });
    document.getElementById('explore-legend').innerHTML = series.map(s => `<span><i class="line" style="background:${s.color}"></i>${escapeHtml(s.name)}</span>`).join('');
}

onTab('health', loadHealth);
onStatus(changed => { if (changed && activeTab === 'health' && Date.now() - healthLoadedAt > 60000) { exploreCatalog = null; loadHealth(); } });
