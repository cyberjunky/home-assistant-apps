// GoodWe Monitor dashboard - Stats view: energies per period, grid and battery, self-sufficiency, day detail and records.

let statsRange = localStorage.getItem(LS + 'stats_range') || '30d';
let statsBucket = localStorage.getItem(LS + 'stats_bucket') || 'auto';
let statsLoadedFor = null;
let detailDate = todayIso();
let detailLoading = false;

const BUCKETS = [
    {key: 'auto', label: 'Auto'},
    {key: 'day', label: 'Day'},
    {key: 'week', label: 'Week'},
    {key: 'month', label: 'Month'},
];

function effectiveBucket() {
    if (statsBucket !== 'auto') return statsBucket;
    return {'7d': 'day', '30d': 'day', '90d': 'week', '12m': 'month', year: 'month', all: 'month'}[statsRange] || 'month';
}

function periodLabel(period, bucket, index) {
    const date = new Date(period + 'T00:00:00');
    if (bucket === 'year') return String(date.getFullYear());
    if (bucket === 'month') {
        const month = date.toLocaleDateString([], {month: 'short'});
        return date.getMonth() === 0 || index === 0 ? `${month} '${String(date.getFullYear()).slice(2)}` : month;
    }
    return date.toLocaleDateString([], {day: 'numeric', month: 'short'});
}

function periodTitle(period, bucket) {
    const date = new Date(period + 'T00:00:00');
    if (bucket === 'year') return String(date.getFullYear());
    if (bucket === 'month') return date.toLocaleDateString([], {month: 'long', year: 'numeric'});
    const day = date.toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'});
    return bucket === 'week' ? `Week of ${day}` : day;
}

function renderStatTiles(s) {
    document.getElementById('stats-tiles').innerHTML = [
        tile('Solar', energyUnit(s.e_day), s.average_kwh ? `${fmtKwh(s.average_kwh)} per day · ${s.days} day${s.days === 1 ? '' : 's'}` : `${s.days} day${s.days === 1 ? '' : 's'}`, 'solar'),
        tile('Home', energyUnit(s.e_load_day), s.days ? `${fmtKwh(s.e_load_day / s.days)} per day` : '', 'load'),
        tile('Imported', energyUnit(s.e_day_imp), s.self_sufficiency === null ? '' : `${fmtPct(100 - s.self_sufficiency)} of the home's energy`, 'importing'),
        tile('Exported', energyUnit(s.e_day_exp), s.self_consumption === null ? '' : `${fmtPct(100 - s.self_consumption)} of the solar energy`, 'exporting'),
        tile('Self-sufficiency', s.self_sufficiency === null ? '–' : withUnit(num(s.self_sufficiency), '%'), 'home energy not from the grid'),
        tile('Self-consumption', s.self_consumption === null ? '–' : withUnit(num(s.self_consumption), '%'), 'solar energy used or stored'),
        tile('Battery charged', energyUnit(s.e_bat_charge_day), s.e_bat_charge_day ? `${fmtPct(s.e_bat_discharge_day / s.e_bat_charge_day * 100)} came back out` : '', 'charging'),
        tile('Battery discharged', energyUnit(s.e_bat_discharge_day), s.e_load_day ? `${fmtPct(s.e_bat_discharge_day / s.e_load_day * 100)} of the home's energy` : '', 'discharging'),
    ].join('');
}

function renderPeriodCharts(periods, bucket) {
    const labelled = periods.map((p, i) => ({...p, label: periodLabel(p.period, bucket, i), title: periodTitle(p.period, bucket)}));
    const labelWidth = bucket === 'month' ? 52 : bucket === 'year' ? 40 : 46;
    const c = {solar: cssVar('--solar'), load: cssVar('--load'), imp: cssVar('--grid-imp'), exp: cssVar('--grid-exp'), chg: cssVar('--bat-chg'), dis: cssVar('--bat-dis'), s1: cssVar('--series-1'), s3: cssVar('--series-3')};
    document.getElementById('chart-energy-title').textContent = `Solar and home per ${bucket}`;
    barChart(document.getElementById('chart-energy'), {
        data: labelled.map(p => ({label: p.label, tipTitle: p.title, values: [p.e_day, p.e_load_day]})),
        series: [{name: 'Solar', color: c.solar}, {name: 'Home', color: c.load}], grouped: true, format: v => fmtKwh(v), height: 210, labelWidth,
    });
    document.getElementById('legend-energy').innerHTML = legendHtml([{name: 'Solar', color: c.solar}, {name: 'Home', color: c.load}]);
    barChart(document.getElementById('chart-grid'), {
        data: labelled.map(p => ({label: p.label, tipTitle: p.title, values: [p.e_day_imp, p.e_day_exp]})),
        series: [{name: 'Imported', color: c.imp}, {name: 'Exported', color: c.exp}], grouped: true, format: v => fmtKwh(v), labelWidth,
    });
    document.getElementById('legend-grid').innerHTML = legendHtml([{name: 'Imported', color: c.imp}, {name: 'Exported', color: c.exp}]);
    barChart(document.getElementById('chart-battery'), {
        data: labelled.map(p => ({label: p.label, tipTitle: p.title, values: [p.e_bat_charge_day, p.e_bat_discharge_day]})),
        series: [{name: 'Charged', color: c.chg}, {name: 'Discharged', color: c.dis}], grouped: true, format: v => fmtKwh(v), labelWidth,
    });
    document.getElementById('legend-battery').innerHTML = legendHtml([{name: 'Charged', color: c.chg}, {name: 'Discharged', color: c.dis}]);
    const every = Math.max(1, Math.ceil(labelled.length / 8));
    lineChart(document.getElementById('chart-self'), {
        x: labelled.map((p, i) => i),
        series: [
            {name: 'Self-sufficiency', color: c.s1, values: labelled.map(p => p.self_sufficiency)},
            {name: 'Self-consumption', color: c.s3, values: labelled.map(p => p.self_consumption)},
        ],
        height: 190, min: 0, max: 100,
        xTicks: labelled.map((p, i) => ({at: i, label: p.label})).filter((t, i) => i % every === 0),
        formatX: i => labelled[i].title, formatY: v => fmtPct(v),
        empty: 'Needs at least two periods',
    });
    document.getElementById('legend-self').innerHTML = legendHtml([{name: 'Self-sufficiency (home energy not from the grid)', color: c.s1, kind: 'line'}, {name: 'Self-consumption (solar not exported)', color: c.s3, kind: 'line'}]);
}

function renderRecords(records) {
    const item = (label, record, format) => record ? `
        <div class="record"><div class="l">${label}</div><div class="v">${format(record.value)}</div><div class="d">${fmtIsoDate(record.date, true)}</div></div>` : '';
    const r = records || {};
    const html = [
        item('Best solar day', r.best_solar_day, v => fmtKwh(v)),
        item('Highest solar power', r.peak_pv, v => fmtW(v)),
        item('Most used at home', r.most_load, v => fmtKwh(v)),
        item('Most imported', r.most_import, v => fmtKwh(v)),
        item('Most exported', r.most_export, v => fmtKwh(v)),
        item('Most charged', r.most_charged, v => fmtKwh(v)),
    ].join('');
    document.getElementById('records').innerHTML = html || '<div class="empty-state" style="padding:20px;background:var(--ha-card-background)">No data in this period</div>';
}

function dayNav(delta) {
    detailDate = delta === 0 ? todayIso() : shiftDate(detailDate, delta);
    if (detailDate > todayIso()) detailDate = todayIso();
    loadDayDetail();
}

async function loadDayDetail() {
    document.getElementById('day-nav').innerHTML = dateNavHtml('dayNav', detailDate);
    if (detailLoading) return;
    detailLoading = true;
    const el = document.getElementById('chart-day');
    try {
        const day = await api(`day?date=${detailDate}`);
        if (day.date !== detailDate) { detailLoading = false; return loadDayDetail(); }
        const rows = day.series || [];
        const xs = rows.map(r => r.ts);
        const step = (day.bucket_min || 5) * 60;
        const c = {solar: cssVar('--solar'), load: cssVar('--load'), grid: cssVar('--grid-imp'), bat: cssVar('--bat-chg'), chg: cssVar('--bat-chg'), dis: cssVar('--bat-dis'), hold: cssVar('--soc')};
        lineChart(el, {
            x: xs,
            series: [
                {name: 'Solar', color: c.solar, values: rows.map(r => r.solar / 1000), area: true},
                {name: 'Home', color: c.load, values: rows.map(r => r.load / 1000)},
                {name: 'Grid (+ import)', color: c.grid, values: rows.map(r => r.grid / 1000), width: 1.5},
                {name: 'Battery (+ charging)', color: c.bat, values: rows.map(r => r.battery / 1000), width: 1.5},
            ],
            height: 240, zero: true,
            bands: stateBands(xs, rows.map(r => r.state), {charge: c.chg, discharge: c.dis, hold: c.hold}, step),
            xTicks: xs.length > 1 ? hourTicks(xs[0], xs[xs.length - 1], 2) : [], formatX: fmtDateTime, formatY: v => fmtKw(v * 1000),
            extraRows: i => [{value: fmtPct(rows[i].soc), name: `battery · ${rows[i].state}`}],
            empty: `No power recorded for ${fmtIsoDate(detailDate)} (the detail is kept for 14 days)`,
        });
        document.getElementById('legend-day').innerHTML = legendHtml([
            {name: 'Solar', color: c.solar}, {name: 'Home', color: c.load}, {name: 'Grid (above 0 = import)', color: c.grid, kind: 'line'},
            {name: 'Battery (above 0 = charging)', color: c.bat, kind: 'line'}, {name: 'Charging', color: c.chg, kind: 'band'},
            {name: 'Discharging', color: c.dis, kind: 'band'}, {name: 'Held', color: c.hold, kind: 'band'},
        ]);
        const s = day.summary || {};
        document.getElementById('day-summary').innerHTML = s.date ? [
            tile('Solar', energyUnit(s.e_day, 2), missing(s.ppv_max) ? '' : `peak ${fmtW(s.ppv_max)}`, 'solar'),
            tile('Home', energyUnit(s.e_load_day, 2), '', 'load'),
            tile('Imported', energyUnit(s.e_day_imp, 2), '', 'importing'),
            tile('Exported', energyUnit(s.e_day_exp, 2), '', 'exporting'),
            tile('Battery', `${energyUnit(s.e_bat_charge_day, 2)} <small>in</small>`, `${fmtKwh(s.e_bat_discharge_day, 2)} out · SoC ${num(s.soc_min)}–${num(s.soc_max)}%`, 'charging'),
        ].join('') : '';
    } catch (e) {
        emptyChart(el, e.message);
    }
    detailLoading = false;
}

async function loadStats(force) {
    const bucket = effectiveBucket();
    const key = `${statsRange}:${bucket}`;
    if (!force && key === statsLoadedFor) { redrawCharts(); return; }
    const bounds = rangeBounds(statsRange);
    document.getElementById('stats-range-text').textContent = `${fmtIsoDate(bounds.from, true)} – ${fmtIsoDate(shiftDate(bounds.to, -1), true)}`;
    const grid = document.getElementById('stats-grid');
    grid.classList.add('loading');
    try {
        const stats = await api(`stats?from=${bounds.from}&to=${bounds.to}&bucket=${bucket}`);
        statsLoadedFor = key;
        renderStatTiles(stats.summary);
        renderPeriodCharts(stats.periods, bucket);
        renderRecords(stats.summary.records);
    } catch (e) {
        toast(e.message, 'error');
    }
    grid.classList.remove('loading');
}

function setStatsRange(key) {
    statsRange = key;
    localStorage.setItem(LS + 'stats_range', key);
    buildChips('stats-range', RANGES, statsRange, setStatsRange);
    buildYearSelect('stats-year', statsRange, setStatsRange);
    loadStats(true);
}

function setStatsBucket(key) {
    statsBucket = key;
    localStorage.setItem(LS + 'stats_bucket', key);
    loadStats(true);
}

onTab('stats', () => {
    buildChips('stats-range', RANGES, statsRange, setStatsRange);
    buildYearSelect('stats-year', statsRange, setStatsRange);
    buildChips('stats-bucket', BUCKETS, statsBucket, setStatsBucket);
    loadStats(false);
    loadDayDetail();
});
