// GoodWe Nerd dashboard - Dashboard view: everything live. Tiles, the energy flow, the battery,
// the solar panels and strings, what the controller is doing, live power and today's charts.

let flowDiagram = null;
let liveLoadedAt = 0;
let liveLoading = false;
let todayLoadedAt = 0;
let todayDay = null;
const lastHtml = {};

// Replace an element's HTML only when it changed (keeps hover and selection between polls)
function setHtml(id, html) {
    if (lastHtml[id] === html) return;
    document.getElementById(id).innerHTML = html;
    lastHtml[id] = html;
}

// What the battery control is doing, from the controller state: {kind, text, detail}
function holdInfo(c) {
    const floor = dod => `floor ${100 - dod}%`;
    if (c.auto_hold) {
        if (c.work_mode_general === false) return {kind: 'warn', text: 'Auto-hold paused', detail: `${c.work_mode_name || 'other'} mode`};
        if (c.held === true) return {kind: 'held', text: 'Held', detail: 'auto-hold · solar expected'};
        if (c.held === false) return {kind: 'discharging', text: 'Discharging allowed', detail: `auto-hold · ${floor(c.night_dod)}`};
        return {kind: 'idle', text: 'Auto-hold deciding', detail: 'first check within 5 minutes'};
    }
    const dod = c.dod_reported;
    if (dod === null || dod === undefined) return {kind: 'idle', text: 'Manual', detail: 'DoD not read yet'};
    if (Number(dod) === 0) return {kind: 'held', text: 'Held', detail: 'manual · no discharge'};
    return {kind: 'discharging', text: 'Normal', detail: `manual · ${floor(Number(dod))}`};
}

// Equivalent full cycles since recording began, from the commissioned capacity
function batteryCycles(d, c) {
    const capacity = Number(c.battery_capacity_kwh || 0);
    return capacity > 0 && !missing(d.e_bat_discharge_total) ? Number(d.e_bat_discharge_total) / capacity : null;
}

function renderDashTiles() {
    const d = app.data, c = app.control;
    if (!d || d.ppv === undefined) return setHtml('dash-tiles', '');
    const bs = batteryState(d), gs = gridState(d);
    const pbat = Number(d.pbattery1 || 0), pgrid = Number(d.pgrid || 0);
    const load = Number(d.e_load_day || 0), imp = Number(d.e_day_imp || 0);
    const self = load > 0 ? Math.max(0, Math.min(100, (1 - imp / load) * 100)) : null;
    const f = app.forecast;
    setHtml('dash-tiles', [
        tile('Solar', wUnit(d.ppv), `${fmtKwh(d.e_day, 2)} today${missing(f.today_kwh) ? '' : ` of ${fmtKwh(f.today_kwh, 1)} forecast`}`, 'solar'),
        tile('Battery', withUnit(num(d.battery_soc), '%'), bs === 'charging' ? `charging · ${fmtW(pbat)}` : bs === 'discharging' ? `discharging · ${fmtW(pbat)}` : holdInfo(c).kind === 'held' ? 'idle · held' : 'idle', bs),
        tile('Grid', wUnit(Math.abs(pgrid)), gs === 'importing' ? `importing · ${fmtKwh(d.e_day_imp, 2)} in today` : gs === 'exporting' ? `exporting · ${fmtKwh(d.e_day_exp, 2)} out today` : `idle · ${fmtKwh(d.e_day_imp, 2)} in today`, gs),
        tile('Home', wUnit(d.load_ptotal), `${fmtKwh(d.e_load_day, 2)} today`, 'load'),
        tile('Self-sufficiency', self === null ? '–' : withUnit(num(self), '%'), self === null ? 'today, once the house has used something' : `today · ${fmtKwh(imp, 2)} from the grid`),
    ].join(''));
}

function renderDashBattery() {
    const d = app.data, c = app.control;
    const soc = missing(d.battery_soc) ? null : Number(d.battery_soc);
    const bs = batteryState(d);
    const hold = holdInfo(c);
    const pbat = Number(d.pbattery1 || 0);
    const floor = c.dod_reported === null || c.dod_reported === undefined ? null : 100 - Number(c.dod_reported);
    const meterClass = bs === 'charging' ? 'charging' : bs === 'discharging' ? 'discharging' : hold.kind === 'held' ? 'held' : (soc !== null && soc < 20 ? 'low' : '');
    const cycles = batteryCycles(d, c);
    const cells = [
        missing(d.battery_max_cell_voltage) ? '' : fact('Cell voltage', `${num(d.battery_min_cell_voltage, 3)} – ${num(d.battery_max_cell_voltage, 3)} V`),
        missing(d.battery_max_cell_temp) ? '' : fact('Cell temperature', `${num(d.battery_min_cell_temp, 1)} – ${num(d.battery_max_cell_temp, 1)} °C`),
        missing(d.bms_bat_soc) ? '' : fact('BMS SoC', fmtPct(d.bms_bat_soc)),
    ].filter(Boolean);
    setHtml('dash-battery', `
        <div class="block-title"><span>Battery</span><span>${escapeHtml(d.battery_mode_label || '')}</span></div>
        <div class="battery">
            <span class="pct">${soc === null ? '–' : num(soc)}<small>%</small></span>
            <span class="range">${stateLabel(bs, bs === 'charging' ? `charging ${fmtW(pbat)}` : bs === 'discharging' ? `discharging ${fmtW(pbat)}` : 'idle')}</span>
            <span class="est">${stateLabel(hold.kind, hold.text)}<br>${escapeHtml(hold.detail)}</span>
        </div>
        <div class="meter ${meterClass}" role="meter" aria-valuenow="${soc || 0}" aria-valuemin="0" aria-valuemax="100" aria-label="Battery">
            <i style="width:${soc || 0}%"></i>
            ${floor === null || floor <= 0 ? '' : `<span class="limit" style="left:calc(${floor}% - 1px)" title="Discharge floor ${floor}%"></span>`}
        </div>
        <div class="live-note">${floor === null ? 'Discharge floor not read yet.' : `Discharge floor ${floor}% (DoD ${c.dod_reported}%)`}</div>
        <div class="facts three" style="margin-top:12px">${factsGrid([
            fact('Power', wUnit(d.pbattery1, true), bs === 'charging' ? 'ok' : ''),
            fact('Voltage · current', missing(d.vbattery1) ? '–' : `${num(d.vbattery1, 1)} V <small>${num(d.ibattery1, 1)} A</small>`),
            fact('Temperature', fmtTemp(d.battery_temperature), Number(d.battery_temperature) > 45 ? 'warn' : ''),
            fact('Charged today', energyUnit(d.e_bat_charge_day, 2), 'ok'),
            fact('Discharged today', energyUnit(d.e_bat_discharge_day, 2)),
            fact('Health', fmtPct(d.battery_soh), Number(d.battery_soh) < 80 ? 'warn' : ''),
            fact('BMS limits', missing(d.battery_charge_limit) ? '–' : `${num(d.battery_charge_limit)} A <small>in · ${num(d.battery_discharge_limit)} A out</small>`),
            fact('Cycles', cycles === null ? '–' : `${num(cycles, 1)} <small>since recording</small>`),
            fact('BMS status', `${missing(d.battery_status) ? '–' : escapeHtml(String(d.battery_status))} <small>err ${missing(d.battery_error) ? '–' : d.battery_error} · warn ${missing(d.battery_warning) ? '–' : d.battery_warning}</small>`, Number(d.battery_error) ? 'bad' : ''),
            ...cells,
        ], 3)}</div>`);
}

function renderDashSolar() {
    const d = app.data, f = app.forecast, c = app.control, inv = app.inverter;
    const strings = [1, 2, 3, 4].map(n => ({n, v: d[`vpv${n}`], i: d[`ipv${n}`], p: d[`ppv${n}`]}))
        .filter(s => !missing(s.v) || !missing(s.p));
    const peak = todayDay && todayDay.summary ? todayDay.summary.ppv_max : null;
    const fault = String(d.error_codes || '0') !== '0' && String(d.error_codes || '') !== '';
    const share = !missing(f.today_kwh) && f.today_kwh > 0 ? Math.min(100, Number(d.e_day || 0) / f.today_kwh * 100) : null;
    setHtml('dash-solar', `
        <div class="block-title"><span>Solar</span><span>${Number(d.ppv) > 30 ? stateLabel('producing', 'producing') : 'no production'}</span></div>
        <div class="facts three">${factsGrid([
            fact('Now', wUnit(d.ppv)),
            fact('Today', `${energyUnit(d.e_day, 2)}${share === null ? '' : ` <small>${num(share)}% of forecast</small>`}`),
            fact('Peak today', missing(peak) ? '–' : wUnit(peak)),
            fact('Forecast this hour', missing(f.hour_kwh) ? '–' : withUnit(num(f.hour_kwh, 2), 'kWh')),
            fact('Forecast today · tomorrow', missing(f.today_kwh) ? '–' : `${num(f.today_kwh, 1)} <small>· ${num(f.tomorrow_kwh, 1)} kWh</small>`),
            fact('Curtailed now', c.zero_export ? (c.curtailed_w > 30 ? `≈ ${fmtW(c.curtailed_w)}` : 'none') : '<small>zero export off</small>', c.curtailed_w > 30 ? 'warn' : ''),
        ], 3)}</div>
        ${strings.length ? `<div class="section-title" style="margin-top:14px">PV strings</div>
        <div class="facts three">${factsGrid(strings.map(s => fact(`PV${s.n}`, `${wUnit(s.p)} <small>${missing(s.v) ? '' : `${num(s.v, 1)} V · ${num(s.i, 2)} A`}</small>`, (s.p || 0) > 5 ? 'ok' : '')), 3)}</div>` : ''}
        <div class="section-title" style="margin-top:14px">Inverter</div>
        <div class="facts three">${factsGrid([
            fact('Temperature', fmtTemp(d.temperature), Number(d.temperature) > 60 ? 'warn' : ''),
            fact('Grid', missing(d.vgrid) ? '–' : `${num(d.vgrid)} V <small>${num(d.fgrid, 2)} Hz</small>`),
            fact('Status', fault ? escapeHtml(d.diagnose_result_label || `fault ${d.error_codes}`) : escapeHtml(d.grid_mode_label || (inv.online ? 'OK' : 'offline')), fault ? 'bad' : ''),
        ], 3)}</div>`);
}

function renderDashControl() {
    const d = app.data, c = app.control, inv = app.inverter;
    const hold = holdInfo(c);
    const decision = (c.decision && c.decision.text) || '';
    const mode = c.work_mode_general === false
        ? `<span class="sb-warn">${escapeHtml(c.work_mode_name || '?')}</span> <small>not General</small>`
        : c.work_mode_general ? 'General' : '<small>not read yet</small>';
    const exportText = c.zero_export
        ? `on${c.export_verified ? '' : ' <small>· checking</small>'}${missing(c.export_limit_reported) ? '' : ` <small>· limit ${num(c.export_limit_reported)} W</small>`}`
        : 'off';
    setHtml('dash-control', `
        <div class="block-title"><span>Control</span><span>${inv.online ? 'updated ' + fmtTime(inv.last_read) : 'inverter offline'}</span></div>
        ${c.work_mode_general === false ? `<div class="note warn" style="margin-bottom:10px">The inverter is in <b>${escapeHtml(c.work_mode_name || '?')}</b> mode: auto-hold is paused. Switch back to General on the Settings page.</div>` : ''}
        <div class="facts">${factsGrid([
            fact('Work mode', mode, c.work_mode_general === false ? 'warn' : ''),
            fact('Energy mode', escapeHtml(d.work_mode_label || '–')),
            fact('Battery', `${stateLabel(hold.kind, hold.text)} <small>${escapeHtml(hold.detail)}</small>`),
            fact('Auto-hold', c.auto_hold ? `on <small>≥ ${num(c.threshold_kwh, 2)} kWh/h${c.forecast_offset_pct ? `, ${c.forecast_offset_pct > 0 ? '+' : ''}${c.forecast_offset_pct}%` : ''}</small>` : 'off'),
            fact('Zero export', exportText),
            fact('Solar surplus', c.surplus ? `yes <small>≈ ${fmtW(c.surplus_w)}</small>` : `no <small>${escapeHtml(c.surplus_reason || '')}</small>`, c.surplus ? 'ok' : ''),
            fact('Surplus today', missing(d.surplus_hours) ? '–' : `${num(d.surplus_hours, 1)} h <small>${fmtKwh(d.e_surplus_day, 1)} solar used</small>`),
            fact('Curtailed today', missing(d.e_curtailed_day) ? '–' : `≈ ${fmtKwh(d.e_curtailed_day, 1)}`, Number(d.e_curtailed_day) > 0.5 ? 'warn' : ''),
        ])}</div>
        ${decision ? `<div class="live-note">${escapeHtml(decision)}${c.last_tick ? ` <small>· checked ${fmtAgo(c.last_tick)}</small>` : ''}</div>` : ''}
        <div class="detail-actions" style="margin-top:10px"><button type="button" class="btn btn-small" onclick="switchTab('settings')">Battery control settings</button></div>`);
}

function renderDashboard() {
    const inv = app.inverter, d = app.data, c = app.control;
    const hint = document.getElementById('dash-hint');
    if (!inv.connected) {
        hint.hidden = false;
        document.getElementById('dash-hint-text').textContent = `Connecting to the inverter at ${inv.host || '?'}…${inv.last_error ? ' ' + inv.last_error : ''}`;
    } else if (!inv.online) {
        hint.hidden = false;
        document.getElementById('dash-hint-text').textContent = `The inverter is not answering${inv.last_error ? ': ' + inv.last_error : ''}. Showing the last values.`;
    } else {
        hint.hidden = true;
    }
    renderDashTiles();
    if (!flowDiagram) flowDiagram = new EnergyFlow(document.getElementById('flow'));
    if (d && d.ppv !== undefined) {
        flowDiagram.update({
            ppv: Number(d.ppv || 0), pbat: Number(d.pbattery1 || 0), pgrid: Number(d.pgrid || 0),
            pload: Number(d.load_ptotal || 0), soc: d.battery_soc, held: holdInfo(c).kind === 'held',
            solarToday: d.e_day, homeToday: d.e_load_day,
            curtailed: c.zero_export ? Number(c.curtailed_w || 0) : 0, surplus: Boolean(c.surplus), surplusW: c.surplus_w,
        });
        document.getElementById('flow-hint').textContent = inv.online ? '' : 'last known values';
        renderDashBattery();
        renderDashSolar();
    }
    renderDashControl();
    if (activeTab !== 'dashboard') return;
    if (Date.now() - liveLoadedAt > 60000) loadLive();
    if (Date.now() - todayLoadedAt > 300000) loadToday();
}

async function loadLive() {
    if (liveLoading) return;
    liveLoading = true;
    liveLoadedAt = Date.now();
    try {
        const live = await api('live?minutes=120');
        const rows = live.series || [];
        const colors = {solar: cssVar('--solar'), load: cssVar('--load'), grid: cssVar('--grid-imp'), battery: cssVar('--bat-chg')};
        const xs = rows.map(r => r.ts);
        lineChart(document.getElementById('chart-live'), {
            x: xs,
            series: [
                {name: 'Solar', color: colors.solar, values: rows.map(r => r.solar / 1000), area: true},
                {name: 'Home', color: colors.load, values: rows.map(r => r.load / 1000)},
                {name: 'Grid (+ import)', color: colors.grid, values: rows.map(r => r.grid / 1000)},
                {name: 'Battery (+ charging)', color: colors.battery, values: rows.map(r => r.battery / 1000)},
            ],
            height: 190, zero: true,
            xTicks: xs.length > 1 ? timeTicks(xs, 6, fmtTime) : [],
            formatX: fmtTime, formatY: v => fmtKw(v * 1000),
            extraRows: i => [{value: fmtPct(rows[i].soc), name: 'battery'}],
            empty: 'Collecting the first minutes of data…',
        });
        document.getElementById('legend-live').innerHTML = legendHtml([
            {name: 'Solar', color: colors.solar}, {name: 'Home', color: colors.load},
            {name: 'Grid (above 0 = import)', color: colors.grid}, {name: 'Battery (above 0 = charging)', color: colors.battery},
        ]);
    } catch (e) {
        emptyChart(document.getElementById('chart-live'), e.message);
    }
    liveLoading = false;
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

function renderSolarToday(day) {
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

function renderBatteryToday(day) {
    const rows = day.series || [];
    const xs = rows.map(r => r.ts);
    const step = (day.bucket_min || 5) * 60;
    const colors = {chg: cssVar('--bat-chg'), dis: cssVar('--bat-dis'), soc: cssVar('--soc')};
    const bands = stateBands(xs, rows.map(r => r.state), {charge: colors.chg, discharge: colors.dis, hold: colors.soc}, step);
    const ticks = xs.length > 1 ? hourTicks(xs[0], xs[xs.length - 1], 3) : [];
    lineChart(document.getElementById('chart-battery-day'), {
        x: xs, series: [{name: 'State of charge', color: colors.soc, values: rows.map(r => r.soc), area: true}],
        height: 130, min: 0, max: 100, bands, xTicks: ticks, formatX: fmtTime, formatY: v => fmtPct(v),
        extraRows: i => [{value: rows[i].state, name: ''}],
        empty: 'No battery data recorded yet today',
    });
    lineChart(document.getElementById('chart-battery-day-power'), {
        x: xs, series: [{name: 'Battery power', color: colors.chg, values: rows.map(r => r.battery / 1000), area: true}],
        height: 110, zero: true, bands, xTicks: ticks, formatX: fmtTime,
        formatY: v => `${fmtKw(v * 1000)} ${v > 0 ? 'charging' : v < 0 ? 'discharging' : ''}`,
        empty: ' ',
    });
    document.getElementById('legend-battery-day').innerHTML = legendHtml([
        {name: 'State of charge', color: colors.soc, kind: 'line'}, {name: 'Charging', color: colors.chg, kind: 'band'},
        {name: 'Discharging', color: colors.dis, kind: 'band'}, {name: 'Held (grid covers the house)', color: colors.soc, kind: 'band'},
    ]);
}

async function loadToday() {
    todayLoadedAt = Date.now();
    try {
        todayDay = await api('day');
        renderSolarToday(todayDay);
        renderBatteryToday(todayDay);
        renderDashSolar();
    } catch (e) {
        emptyChart(document.getElementById('chart-solar-today'), e.message);
        emptyChart(document.getElementById('chart-battery-day'), e.message);
    }
}

onTab('dashboard', () => {
    Object.keys(lastHtml).forEach(k => delete lastHtml[k]);
    renderDashboard();
    loadLive();
    loadToday();
    setTimeout(redrawCharts, 0);
});
onLive(renderDashboard);
