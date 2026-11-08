// GoodWe dashboard - Stats view: energies per period, grid, battery and surplus, self-sufficiency,
// the day detail with its energy flow (Sankey), the solar forecast and yields, and records.

let statsRange = localStorage.getItem(LS + 'stats_range') || '30d';
let statsBucket = localStorage.getItem(LS + 'stats_bucket') || 'auto';
let statsLoadedFor = null;
let detailDate = todayIso();
let detailLoading = false;
let solarStatsLoadedAt = 0;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

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
    const miners = minersInfo(app.control);
    document.getElementById('stats-tiles').innerHTML = [
        tile('Solar', energyUnit(s.e_day), s.average_kwh ? `${fmtKwh(s.average_kwh)} per day · ${s.days} day${s.days === 1 ? '' : 's'}` : `${s.days} day${s.days === 1 ? '' : 's'}`, 'solar'),
        tile('Home', energyUnit(s.e_load_day), s.days ? `${fmtKwh(s.e_load_day / s.days)} per day` : '', 'load'),
        tile('Imported', energyUnit(s.e_day_imp), s.self_sufficiency === null ? '' : `${fmtPct(100 - s.self_sufficiency)} of the home's energy`, 'importing'),
        tile('Exported', energyUnit(s.e_day_exp), s.self_consumption === null ? '' : `${fmtPct(100 - s.self_consumption)} of the solar energy`, 'exporting'),
        tile('Self-sufficiency', s.self_sufficiency === null ? '–' : withUnit(num(s.self_sufficiency), '%'), 'home energy not from the grid'),
        tile('Self-consumption', s.self_consumption === null ? '–' : withUnit(num(s.self_consumption), '%'), 'solar energy used or stored'),
        tile('Battery charged', energyUnit(s.e_bat_charge_day), s.e_bat_charge_day ? `${fmtPct(s.e_bat_discharge_day / s.e_bat_charge_day * 100)} came back out` : '', 'charging'),
        tile('Battery discharged', energyUnit(s.e_bat_discharge_day), s.e_load_day ? `${fmtPct(s.e_bat_discharge_day / s.e_load_day * 100)} of the home's energy` : '', 'discharging'),
        tile('Battery cycles', missing(s.cycles) ? '–' : num(s.cycles, 1), s.battery_capacity_kwh ? `full cycles of ${num(s.battery_capacity_kwh, 1)} kWh` : 'capacity not read yet', 'charging'),
        tile('Solar surplus', withUnit(num(s.surplus_hours, 1), 'h'), s.days ? `${num(s.surplus_hours / s.days, 1)} h per day · signal on` : '', 'surplus'),
        tile('Solar used in surplus', energyUnit(s.e_surplus_day), 'to the home while the signal was on', 'solar'),
        tile('Curtailed', s.e_curtailed_day ? `≈ ${energyUnit(s.e_curtailed_day)}` : energyUnit(0), 'estimated, under zero export', 'exporting'),
        ...(miners ? [
            tile(`${miners.name} ran`, withUnit(num(s.miners_hours || 0, 1), 'h'), s.days ? `${num((s.miners_hours || 0) / s.days, 1)} h per day` : '', 'miners'),
            tile(`${miners.name} used`, energyUnit(s.e_miners_day || 0), s.e_load_day ? `${fmtPct((s.e_miners_day || 0) / s.e_load_day * 100)} of the home's energy` : '', 'miners'),
        ] : []),
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
        data: labelled.map(p => ({label: p.label, tipTitle: `${p.title}${missing(p.cycles) ? '' : ` · ${num(p.cycles, 2)} cycles`}`, values: [p.e_bat_charge_day, p.e_bat_discharge_day]})),
        series: [{name: 'Charged', color: c.chg}, {name: 'Discharged', color: c.dis}], grouped: true, format: v => fmtKwh(v), labelWidth,
    });
    document.getElementById('chart-battery-hint').textContent = labelled.some(p => !missing(p.cycles)) ? 'kWh · cycles in the tooltip' : 'kWh';
    document.getElementById('legend-battery').innerHTML = legendHtml([{name: 'Charged', color: c.chg}, {name: 'Discharged', color: c.dis}]);
    const miners = minersInfo(app.control);
    const surplusSeries = [{name: 'Solar used during surplus', color: c.solar}, {name: 'Curtailed (estimate)', color: cssVar('--danger-color')}];
    if (miners) surplusSeries.push({name: miners.name, color: cssVar('--miners')});
    barChart(document.getElementById('chart-surplus'), {
        data: labelled.map(p => ({label: p.label, tipTitle: `${p.title} · surplus ${num(p.surplus_hours, 1)} h${miners ? ` · ${miners.name} ${num(p.miners_hours || 0, 1)} h` : ''}`,
            values: miners ? [p.e_surplus_day, p.e_curtailed_day, p.e_miners_day || 0] : [p.e_surplus_day, p.e_curtailed_day]})),
        series: surplusSeries, grouped: true, format: v => fmtKwh(v), labelWidth,
    });
    document.getElementById('legend-surplus').innerHTML = legendHtml([{name: 'Solar to the home while the surplus signal was on', color: c.solar}, {name: 'Curtailed under zero export (estimate)', color: cssVar('--danger-color')},
        ...(miners ? [{name: `${miners.name} (energy used)`, color: cssVar('--miners')}] : [])]);
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
        item('Longest surplus', r.longest_surplus, v => `${num(v, 1)} h`),
        item('Most curtailed', r.most_curtailed, v => `≈ ${fmtKwh(v)}`),
        ...(minersInfo(app.control) ? [item(`${minersInfo(app.control).name}: longest`, r.longest_miners, v => `${num(v, 1)} h`)] : []),
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
        const c = {solar: cssVar('--solar'), load: cssVar('--load'), grid: cssVar('--grid-imp'), bat: cssVar('--bat-chg'), chg: cssVar('--bat-chg'), dis: cssVar('--bat-dis'), hold: cssVar('--soc'), miners: cssVar('--miners')};
        const miners = minersInfo(app.control) || (rows.some(r => r.miners) ? {name: 'Miners'} : null);
        lineChart(el, {
            x: xs,
            series: [
                {name: 'Solar', color: c.solar, values: rows.map(r => r.solar / 1000), area: true},
                {name: 'Home', color: c.load, values: rows.map(r => r.load / 1000)},
                {name: 'Grid (+ import)', color: c.grid, values: rows.map(r => r.grid / 1000), width: 1.5},
                {name: 'Battery (+ charging)', color: c.bat, values: rows.map(r => r.battery / 1000), width: 1.5},
                ...(miners ? [{name: miners.name, color: c.miners, values: rows.map(r => (r.miners || 0) / 1000), dash: '5 3', width: 1.5}] : []),
            ],
            height: 240, zero: true,
            bands: stateBands(xs, rows.map(r => r.state), {charge: c.chg, discharge: c.dis, hold: c.hold}, step),
            xTicks: xs.length > 1 ? hourTicks(xs[0], xs[xs.length - 1], 2) : [], formatX: fmtDateTime, formatY: v => fmtKw(v * 1000),
            extraRows: i => [{value: fmtPct(rows[i].soc), name: `battery · ${rows[i].state}`}],
            empty: `No power recorded for ${fmtIsoDate(detailDate)}`,
        });
        document.getElementById('legend-day').innerHTML = legendHtml([
            {name: 'Solar', color: c.solar}, {name: 'Home', color: c.load}, {name: 'Grid (above 0 = import)', color: c.grid, kind: 'line'},
            {name: 'Battery (above 0 = charging)', color: c.bat, kind: 'line'}, {name: 'Charging', color: c.chg, kind: 'band'},
            {name: 'Discharging', color: c.dis, kind: 'band'}, {name: 'Held', color: c.hold, kind: 'band'},
            ...(miners ? [{name: `${miners.name} (part of home)`, color: c.miners, kind: 'line'}] : []),
        ]);
        const s = day.summary || {};
        document.getElementById('day-summary').innerHTML = s.date ? [
            tile('Solar', energyUnit(s.e_day, 2), missing(s.ppv_max) ? '' : `peak ${fmtW(s.ppv_max)}`, 'solar'),
            tile('Home', energyUnit(s.e_load_day, 2), '', 'load'),
            tile('Imported', energyUnit(s.e_day_imp, 2), '', 'importing'),
            tile('Exported', energyUnit(s.e_day_exp, 2), '', 'exporting'),
            tile('Battery', `${energyUnit(s.e_bat_charge_day, 2)} <small>in</small>`, `${fmtKwh(s.e_bat_discharge_day, 2)} out · SoC ${num(s.soc_min)}–${num(s.soc_max)}%`, 'charging'),
            tile('Solar surplus', withUnit(num(s.surplus_hours, 1), 'h'), `${fmtKwh(s.e_surplus_day, 2)} used · ≈ ${fmtKwh(s.e_curtailed_day, 2)} curtailed`, 'surplus'),
            ...(miners ? [tile(miners.name, withUnit(num(s.miners_hours || 0, 1), 'h'), `${fmtKwh(s.e_miners_day || 0, 2)} used`, 'miners')] : []),
        ].join('') : '';
    } catch (e) {
        emptyChart(el, e.message);
    }
    detailLoading = false;
    loadSankey(detailDate);
}

async function loadSankey(day) {
    const wrap = document.getElementById('sankey-wrap');
    try {
        const flow = await api(`flow?date=${day}`);
        if (day !== detailDate) return;  // the user moved on
        const srcTotal = Object.values(flow.sources).reduce((a, b) => a + b, 0);
        const dstTotal = Object.values(flow.destinations).reduce((a, b) => a + b, 0);
        const card = (name, color, kwh, total) => `<div class="flow-card"><span class="name"><i style="background:${color}"></i>${name}</span>
            <span class="val">${num(kwh, 2)}<small> kWh</small> <span class="pct">${total > 0 ? num(kwh / total * 100) : 0}%</span></span></div>`;
        wrap.innerHTML = `<div class="sankey" id="sankey"></div>
            <div class="flow-cards">
                <div class="flow-col"><div class="section-title">Energy in · ${fmtKwh(srcTotal, 2)}</div>
                    ${card('Solar', cssVar('--solar'), flow.sources.solar, srcTotal)}
                    ${card('Battery', cssVar('--bat-dis'), flow.sources.battery, srcTotal)}
                    ${card('Grid', cssVar('--grid-imp'), flow.sources.grid, srcTotal)}</div>
                <div class="flow-col"><div class="section-title">Energy out · ${fmtKwh(dstTotal, 2)}</div>
                    ${card('Home', cssVar('--load'), flow.destinations.load, dstTotal)}
                    ${card('Battery', cssVar('--bat-chg'), flow.destinations.battery, dstTotal)}
                    ${card('Grid export', cssVar('--grid-exp'), flow.destinations.grid, dstTotal)}</div>
            </div>
            <p class="panel-sub" style="margin-top:10px">Integrated from the recorded power with a priority model (solar → home → battery → grid). Approximate: the inverter doesn't report the flows themselves.</p>`;
        renderSankey(document.getElementById('sankey'), flow);
    } catch (e) {
        wrap.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    }
}

// ---------------------------------------------------------------------
// Solar: the forecast for the next days, its accuracy, yields per month and per year
// ---------------------------------------------------------------------
async function loadSolarStats() {
    if (Date.now() - solarStatsLoadedAt < 300000) return;
    let s;
    try {
        s = await api('solar');
    } catch (e) {
        return;
    }
    solarStatsLoadedAt = Date.now();
    const solar = cssVar('--solar'), load = cssVar('--load'), fc = cssVar('--series-1');
    barChart(document.getElementById('chart-forecast-days'), {
        data: (s.daily || []).map(d => ({label: new Date(d.date + 'T00:00:00').toLocaleDateString([], {weekday: 'short'}), tipTitle: fmtIsoDate(d.date), values: [d.kwh]})),
        series: [{name: 'Forecast', color: solar}], format: v => fmtKwh(v, 1), height: 170,
        empty: app.forecast.configured ? 'No forecast fetched yet' : 'Forecast not configured',
    });
    const acc = (s.accuracy && s.accuracy.days) || [];
    const bias = s.accuracy ? s.accuracy.bias_pct : null;
    document.getElementById('accuracy-hint').textContent = acc.length ? `last 14 days · kWh${missing(bias) ? '' : ` · bias ${bias > 0 ? '+' : ''}${num(bias, 1)}%`}` : '';
    barChart(document.getElementById('chart-accuracy'), {
        data: acc.map(d => ({label: d.date.slice(5), tipTitle: `${fmtIsoDate(d.date)}${d.error_pct === null ? '' : ` · ${d.error_pct > 0 ? '+' : ''}${d.error_pct}%`}`, values: [d.forecast_kwh, d.actual_kwh]})),
        series: [{name: 'Forecast', color: fc}, {name: 'Actual', color: solar}], grouped: true, format: v => fmtKwh(v, 1), height: 170, labelWidth: 40,
        empty: 'Fills up from tomorrow on',
    });
    document.getElementById('legend-accuracy').innerHTML = acc.length ? legendHtml([{name: 'Forecast', color: fc}, {name: 'Actual', color: solar}]) : '';
    document.getElementById('accuracy-note').textContent = missing(bias) ? ''
        : Math.abs(bias) < 10 ? 'The forecast is well calibrated.'
        : bias > 0 ? `Production beats the forecast by ${num(bias)}%: raise the kWp in the configuration, or set a positive forecast offset for auto-hold (Settings → Battery control).`
        : `Production falls ${num(-bias)}% short of the forecast: lower the kWp, or set a negative forecast offset for auto-hold (Settings → Battery control).`;
    const months = s.months || [];
    barChart(document.getElementById('chart-months'), {
        data: MONTHS.map((name, i) => {
            const key = `${new Date().getFullYear()}-${String(i + 1).padStart(2, '0')}`;
            const m = months.find(x => x.month === key);
            return {label: name, tipTitle: `${name} ${new Date().getFullYear()}`, values: [m ? m.e_day : 0, m ? m.e_load_day : 0]};
        }),
        series: [{name: 'Solar', color: solar}, {name: 'Home', color: load}], grouped: true, format: v => fmtKwh(v, 0), height: 170, labelWidth: 30,
    });
    barChart(document.getElementById('chart-years'), {
        data: (s.years || []).map(y => ({label: y.year, values: [y.e_day, y.e_load_day]})),
        series: [{name: 'Solar', color: solar}, {name: 'Home', color: load}], grouped: true, format: v => fmtKwh(v, 0), height: 170,
        empty: 'No full day recorded yet',
    });
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
    loadSolarStats();
});
