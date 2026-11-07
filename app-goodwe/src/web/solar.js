// GoodWe Monitor dashboard - Solar view: production now and today, PV strings, the forecast and yields per month and year.

let solarInfo = null;
let solarLoadedAt = 0;
let solarDay = null;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function renderSolarTiles() {
    const d = app.data, s = solarInfo || {};
    const months = s.months || [];
    const thisMonth = months.length ? months[months.length - 1] : null;
    const yearKwh = months.reduce((a, m) => a + (m.e_day || 0), 0);
    const today = s.today || {};
    document.getElementById('solar-tiles').innerHTML = [
        tile('Now', wUnit(d.ppv), d.ppv > 30 ? stateLabel('producing', 'producing') : 'no production', 'solar'),
        tile('Today', energyUnit(d.e_day, 2), app.forecast.today_kwh !== null && app.forecast.today_kwh !== undefined ? `forecast ${fmtKwh(app.forecast.today_kwh)}` : ''),
        tile('This month', thisMonth ? energyUnit(thisMonth.e_day) : '–', thisMonth ? `${fmtKwh(thisMonth.e_load_day)} used at home` : ''),
        tile('This year', months.length ? energyUnit(yearKwh) : '–', months.length ? `${months.length} month${months.length === 1 ? '' : 's'} recorded` : ''),
        tile('All time', energyUnit(d.e_total), missing(d.h_total) ? '' : `${num(d.h_total)} run hours`),
        tile('Peak today', wUnit(today.ppv_max), s.peak_pv ? `record ${fmtW(s.peak_pv)}` : ''),
    ].join('');
}

function renderStrings() {
    const d = app.data;
    const strings = [1, 2, 3, 4].map(n => ({n, v: d[`vpv${n}`], i: d[`ipv${n}`], p: d[`ppv${n}`], mode: d[`pv${n}_mode_label`]}))
        .filter(s => !missing(s.v) || !missing(s.p));
    const el = document.getElementById('solar-strings');
    if (!strings.length) {
        el.innerHTML = '<div class="fact"><div class="l">PV strings</div><div class="v">no string data</div></div><div class="fact" aria-hidden="true"></div>';
        return;
    }
    el.innerHTML = factsGrid(strings.flatMap(s => [
        fact(`PV${s.n}`, wUnit(s.p), (s.p || 0) > 5 ? 'ok' : ''),
        fact(`PV${s.n} voltage · current`, missing(s.v) ? '–' : `${num(s.v, 1)} V <small>${num(s.i, 2)} A</small>`),
    ]));
}

function renderForecastInfo() {
    const f = app.forecast, s = solarInfo || {};
    const acc = s.accuracy || {};
    const rows = [];
    if (!f.configured) {
        rows.push(`<div class="note warn">${f.enabled ? 'No location yet: set latitude and longitude in the add-on configuration, or let Home Assistant provide the home location.' : 'The forecast is off in the add-on configuration.'}</div>`);
    } else {
        rows.push(infoRow('Source', f.source ? escapeHtml(f.source === 'open-meteo' ? 'Open-Meteo (fallback, less precise)' : 'Forecast.Solar') : 'not fetched yet'));
        rows.push(infoRow('Fetched', f.fetched_at ? `${fmtAgo(f.fetched_at)} · next ${fmtTime(f.fetched_at + 1800)}` : '–'));
        rows.push(infoRow('Today · tomorrow', `${fmtKwh(f.today_kwh)} · ${fmtKwh(f.tomorrow_kwh)}`));
        rows.push(infoRow('This hour', fmtKwh(f.hour_kwh, 2)));
        rows.push(infoRow('Location', `${num(f.location.lat, 4)}, ${num(f.location.lon, 4)} <small>(${escapeHtml(f.location.source)})</small>`));
        (f.planes || []).forEach(p => rows.push(infoRow(escapeHtml(p.name), `${num(p.kwp, 2)} kWp · tilt ${p.tilt}° · azimuth ${p.azimuth}°`)));
        if (acc.bias_pct !== null && acc.bias_pct !== undefined) {
            const b = acc.bias_pct;
            rows.push(infoRow('Bias (14 days)', `<span class="${Math.abs(b) < 10 ? 'sb-ok' : 'sb-warn'}">${b > 0 ? '+' : ''}${num(b, 1)}%</span>`));
        }
        if (f.error) rows.push(`<p class="form-error" style="margin-top:8px">${escapeHtml(f.error)}</p>`);
    }
    const bias = acc.bias_pct;
    const hint = bias === null || bias === undefined ? ''
        : Math.abs(bias) < 10 ? 'The forecast is well calibrated.'
        : bias > 0 ? 'Production exceeds the forecast: the kWp in the configuration may be too low, or the auto-hold offset can compensate.'
        : 'Production falls short of the forecast: the kWp may be too high, or set a negative auto-hold offset.';
    document.getElementById('solar-forecast-info').innerHTML = rows.join('')
        + (hint ? `<p class="panel-sub" style="margin-top:10px">${hint}</p>` : '')
        + (f.configured ? `<div class="detail-actions" style="margin-top:10px"><button type="button" class="btn btn-small" onclick="refreshForecast(this)">Fetch again</button></div>` : '');
}

async function refreshForecast(button) {
    button.disabled = true;
    try {
        await api('forecast/refresh', {method: 'POST'});
        toast('Forecast refreshed', 'success');
        await loadSolar(true);
    } catch (e) {
        toast(e.message, 'error');
    }
    button.disabled = false;
}

// Forecast watts at any time, linearly interpolated between the hourly points
function forecastAt(points, ts) {
    if (!points.length) return null;
    if (ts < points[0].ts - 3600 || ts > points[points.length - 1].ts + 3600) return 0;
    let prev = null;
    for (const p of points) {
        if (p.ts === ts) return p.watts;
        if (p.ts > ts) {
            if (!prev) return p.watts * (1 - (p.ts - ts) / 3600);
            return prev.watts + (p.watts - prev.watts) * (ts - prev.ts) / (p.ts - prev.ts);
        }
        prev = p;
    }
    return prev ? prev.watts * Math.max(0, 1 - (ts - prev.ts) / 3600) : null;
}

function renderSolarToday() {
    const day = solarDay;
    if (!day) return;
    const start = new Date(day.date + 'T00:00:00').getTime() / 1000;
    const step = (day.bucket_min || 5) * 60;
    const xs = [];
    for (let t = start; t <= start + 86400; t += step) xs.push(t);
    const actual = new Map(day.series.map(r => [r.ts, r.solar]));
    const forecast = day.forecast || [];
    const colors = {solar: cssVar('--solar'), forecast: cssVar('--secondary-text-color')};
    const series = [{name: 'Production', color: colors.solar, values: xs.map(t => actual.has(t) ? actual.get(t) / 1000 : null), area: true}];
    if (forecast.length) series.push({name: 'Forecast', color: colors.forecast, values: xs.map(t => { const w = forecastAt(forecast, t); return w === null ? null : w / 1000; }), dash: '4 4', width: 1.5});
    lineChart(document.getElementById('chart-solar-today'), {
        x: xs, series, height: 220, zero: true,
        xTicks: hourTicks(start, start + 86400, 3), formatX: fmtTime, formatY: v => fmtKw(v * 1000),
        markers: [{at: Math.floor(Date.now() / 1000), label: 'now'}],
        empty: 'No production recorded yet today',
    });
    document.getElementById('legend-solar-today').innerHTML = legendHtml([{name: 'Production', color: colors.solar}, ...(forecast.length ? [{name: 'Forecast', color: colors.forecast, kind: 'line'}] : [])]);
}

function renderSolarCharts() {
    const s = solarInfo;
    if (!s) return;
    const solar = cssVar('--solar'), load = cssVar('--load'), fc = cssVar('--series-1');
    barChart(document.getElementById('chart-forecast-days'), {
        data: (s.daily || []).map(d => ({label: new Date(d.date + 'T00:00:00').toLocaleDateString([], {weekday: 'short'}), tipTitle: fmtIsoDate(d.date), values: [d.kwh]})),
        series: [{name: 'Forecast', color: solar}], format: v => fmtKwh(v, 1), height: 170,
        empty: app.forecast.configured ? 'No forecast fetched yet' : 'Forecast not configured',
    });
    const acc = (s.accuracy && s.accuracy.days) || [];
    document.getElementById('accuracy-hint').textContent = acc.length ? 'last 14 days · kWh' : '';
    barChart(document.getElementById('chart-accuracy'), {
        data: acc.map(d => ({label: d.date.slice(5), tipTitle: `${fmtIsoDate(d.date)}${d.error_pct === null ? '' : ` · ${d.error_pct > 0 ? '+' : ''}${d.error_pct}%`}`, values: [d.forecast_kwh, d.actual_kwh]})),
        series: [{name: 'Forecast', color: fc}, {name: 'Actual', color: solar}], grouped: true, format: v => fmtKwh(v, 1), height: 170, labelWidth: 40,
        empty: 'Fills up from tomorrow on',
    });
    document.getElementById('legend-accuracy').innerHTML = acc.length ? legendHtml([{name: 'Forecast', color: fc}, {name: 'Actual', color: solar}]) : '';
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

async function loadSolar(force) {
    if (!force && solarInfo && Date.now() - solarLoadedAt < 300000) {
        renderSolarTiles(); renderStrings(); renderForecastInfo(); renderSolarToday(); renderSolarCharts();
        return;
    }
    try {
        [solarInfo, solarDay] = await Promise.all([api('solar'), api('day')]);
        solarLoadedAt = Date.now();
    } catch (e) {
        toast(e.message, 'error');
        return;
    }
    renderSolarTiles(); renderStrings(); renderForecastInfo(); renderSolarToday(); renderSolarCharts();
}

onTab('solar', () => loadSolar(false));
onLive(() => {
    if (activeTab !== 'solar') return;
    renderSolarTiles();
    renderStrings();
    // A new poll adds a point to today's curve about every five minutes; the whole page every five
    if (Date.now() - solarLoadedAt > 300000) loadSolar(true);
});
