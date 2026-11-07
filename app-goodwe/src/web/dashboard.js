// GoodWe Monitor dashboard - Dashboard view: live tiles, the energy flow, modes and status, live power, the day's Sankey.

let flowDiagram = null;
let liveLoadedAt = 0;
let liveLoading = false;
let sankeyDate = todayIso();
let sankeyLoading = false;
let lastSideHtml = '';
let lastTilesHtml = '';

// What the battery control is doing, from the controller state: {kind, text, detail}
function holdInfo(c) {
    const floor = dod => `floor ${100 - dod}%`;
    if (c.auto_hold) {
        if (c.held === true) return {kind: 'held', text: 'Held', detail: 'auto-hold · solar expected'};
        if (c.held === false) return {kind: 'discharging', text: 'Discharging allowed', detail: `auto-hold · ${floor(c.night_dod)}`};
        return {kind: 'idle', text: 'Auto-hold deciding', detail: 'first check within 5 minutes'};
    }
    const dod = c.dod_reported;
    if (dod === null || dod === undefined) return {kind: 'idle', text: 'Manual', detail: 'DoD not read yet'};
    if (Number(dod) === 0) return {kind: 'held', text: 'Held', detail: 'manual · no discharge'};
    return {kind: 'discharging', text: 'Normal', detail: `manual · ${floor(Number(dod))}`};
}

function renderDashTiles() {
    const d = app.data, c = app.control;
    if (!d || d.ppv === undefined) {
        document.getElementById('dash-tiles').innerHTML = '';
        return;
    }
    const bs = batteryState(d), gs = gridState(d);
    const pbat = Number(d.pbattery1 || 0), pgrid = Number(d.pgrid || 0);
    const load = Number(d.e_load_day || 0), imp = Number(d.e_day_imp || 0);
    const self = load > 0 ? Math.max(0, Math.min(100, (1 - imp / load) * 100)) : null;
    const html = [
        tile('Solar', wUnit(d.ppv), `${fmtKwh(d.e_day, 2)} today`, 'solar'),
        tile('Battery', withUnit(num(d.battery_soc), '%'), bs === 'charging' ? `charging · ${fmtW(pbat)}` : bs === 'discharging' ? `discharging · ${fmtW(pbat)}` : holdInfo(c).text === 'Held' ? 'idle · held' : 'idle', bs),
        tile('Grid', wUnit(Math.abs(pgrid)), gs === 'importing' ? `importing · ${fmtKwh(d.e_day_imp, 2)} in today` : gs === 'exporting' ? `exporting · ${fmtKwh(d.e_day_exp, 2)} out today` : `idle · ${fmtKwh(d.e_day_imp, 2)} in today`, gs),
        tile('Home', wUnit(d.load_ptotal), `${fmtKwh(d.e_load_day, 2)} today`, 'load'),
        tile('Self-sufficiency', self === null ? '–' : withUnit(num(self), '%'), self === null ? 'today, once the house has used something' : `today · ${fmtKwh(imp, 2)} from the grid`),
    ].join('');
    if (html !== lastTilesHtml) {
        document.getElementById('dash-tiles').innerHTML = html;
        lastTilesHtml = html;
    }
}

function renderDashSide() {
    const d = app.data, c = app.control, f = app.forecast, inv = app.inverter;
    const hold = holdInfo(c);
    const fault = String(d.error_codes || '0') !== '0' && String(d.error_codes || '') !== '';
    const decision = (c.decision && c.decision.text) || '';
    const capText = c.charge_cap_soc ? `${c.charge_cap_soc}%${c.charge_cap_active ? ' · braking' : ''}` : 'off';
    const exportText = c.zero_export
        ? `on${c.export_verified ? '' : ' · checking'}${c.export_limit_reported !== null && c.export_limit_reported !== undefined ? ` · limit ${num(c.export_limit_reported)} W` : ''}`
        : 'off';
    const surplus = c.surplus ? `yes · ≈ ${fmtW(c.surplus_w)}` : `no · ${escapeHtml(c.surplus_reason || '')}`;
    const html = `
        <div class="block"><div class="block-title"><span>Modes</span><span>${inv.online ? 'updated ' + fmtTime(inv.last_read) : 'inverter offline'}</span></div>
            <div class="facts">${factsGrid([
                fact('Work mode', escapeHtml(d.work_mode_label || '–')),
                fact('Battery', `${stateLabel(hold.kind, hold.text)} <small>${escapeHtml(hold.detail)}</small>`),
                fact('Auto-hold', c.auto_hold ? `on <small>≥ ${num(c.threshold_kwh, 2)} kWh/h${c.forecast_offset_pct ? `, ${c.forecast_offset_pct > 0 ? '+' : ''}${c.forecast_offset_pct}%` : ''}</small>` : 'off'),
                fact('Charge cap', capText, c.charge_cap_active ? 'warn' : ''),
                fact('Zero export', exportText),
                fact('Solar surplus', surplus, c.surplus ? 'ok' : ''),
            ])}</div>
            ${decision ? `<div class="live-note">${escapeHtml(decision)}</div>` : ''}
        </div>
        <div class="block"><div class="block-title"><span>Inverter</span><span>${escapeHtml(inv.model || '')}</span></div>
            <div class="facts three">${factsGrid([
                fact('Temperature', fmtTemp(d.temperature), Number(d.temperature) > 60 ? 'warn' : ''),
                fact('Battery temp', fmtTemp(d.battery_temperature), Number(d.battery_temperature) > 45 ? 'warn' : ''),
                fact('Battery health', fmtPct(d.battery_soh)),
                fact('Grid', missing(d.vgrid) ? '–' : `${num(d.vgrid)} V <small>${num(d.fgrid, 2)} Hz</small>`),
                fact('Battery', missing(d.vbattery1) ? '–' : `${num(d.vbattery1, 1)} V <small>${num(d.ibattery1, 1)} A</small>`),
                fact('Status', fault ? escapeHtml(d.diagnose_result_label || `fault ${d.error_codes}`) : escapeHtml(d.grid_mode_label || (inv.online ? 'OK' : 'offline')), fault ? 'bad' : ''),
            ], 3)}</div>
        </div>
        <div class="block"><div class="block-title"><span>Today</span><span>${f.today_kwh !== null && f.today_kwh !== undefined ? `forecast ${fmtKwh(f.today_kwh)}` : ''}</span></div>
            <div class="facts three">${factsGrid([
                fact('Solar', energyUnit(d.e_day, 2)),
                fact('Home', energyUnit(d.e_load_day, 2)),
                fact('Imported', energyUnit(d.e_day_imp, 2)),
                fact('Exported', energyUnit(d.e_day_exp, 2)),
                fact('Charged', energyUnit(d.e_bat_charge_day, 2)),
                fact('Discharged', energyUnit(d.e_bat_discharge_day, 2)),
            ], 3)}</div>
        </div>
        <div class="block"><div class="block-title"><span>All time</span><span>${missing(d.h_total) ? '' : `${num(d.h_total)} hours`}</span></div>
            <div class="facts three">${factsGrid([
                fact('Solar', energyUnit(d.e_total)),
                fact('Imported', energyUnit(d.e_total_imp)),
                fact('Exported', energyUnit(d.e_total_exp)),
            ], 3)}</div>
        </div>`;
    if (html !== lastSideHtml) {
        document.getElementById('dash-side').innerHTML = html;
        lastSideHtml = html;
    }
}

function renderDashboard() {
    const inv = app.inverter, d = app.data;
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
    renderDashSide();
    if (!flowDiagram) flowDiagram = new EnergyFlow(document.getElementById('flow'));
    if (d && d.ppv !== undefined) {
        flowDiagram.update({
            ppv: Number(d.ppv || 0), pbat: Number(d.pbattery1 || 0), pgrid: Number(d.pgrid || 0),
            pload: Number(d.load_ptotal || 0), soc: d.battery_soc, backup: Number(d.backup_ptotal || 0),
            held: holdInfo(app.control).kind === 'held',
        });
        document.getElementById('flow-hint').textContent = inv.online ? '' : 'last known values';
    }
    if (activeTab === 'dashboard' && Date.now() - liveLoadedAt > 60000) loadLive();
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

function flowNav(delta) {
    sankeyDate = delta === 0 ? todayIso() : shiftDate(sankeyDate, delta);
    if (sankeyDate > todayIso()) sankeyDate = todayIso();
    loadSankey();
}

async function loadSankey() {
    document.getElementById('flow-nav').innerHTML = dateNavHtml('flowNav', sankeyDate);
    if (sankeyLoading) return;
    sankeyLoading = true;
    const wrap = document.getElementById('sankey-wrap');
    try {
        const flow = await api(`flow?date=${sankeyDate}`);
        if (flow.date !== sankeyDate) { sankeyLoading = false; return loadSankey(); }
        const srcTotal = Object.values(flow.sources).reduce((a, b) => a + b, 0);
        const dstTotal = Object.values(flow.destinations).reduce((a, b) => a + b, 0);
        const card = (name, color, kwh, total) => `<div class="flow-card"><span class="name"><i style="background:${color}"></i>${name}</span>
            <span class="val">${num(kwh, 2)}<small> kWh</small><br><span class="pct">${total > 0 ? num(kwh / total * 100) : 0}%</span></span></div>`;
        wrap.innerHTML = `<div class="sankey-layout">
            <div class="sankey" id="sankey"></div>
            <div class="flow-cards">
                <div class="section-title">Energy in · ${fmtKwh(srcTotal, 2)}</div>
                ${card('Solar', cssVar('--solar'), flow.sources.solar, srcTotal)}
                ${card('Battery', cssVar('--bat-dis'), flow.sources.battery, srcTotal)}
                ${card('Grid', cssVar('--grid-imp'), flow.sources.grid, srcTotal)}
                <div class="section-title" style="margin-top:6px">Energy out · ${fmtKwh(dstTotal, 2)}</div>
                ${card('Home', cssVar('--load'), flow.destinations.load, dstTotal)}
                ${card('Battery', cssVar('--bat-chg'), flow.destinations.battery, dstTotal)}
                ${card('Grid export', cssVar('--grid-exp'), flow.destinations.grid, dstTotal)}
            </div></div>
            <p class="panel-sub" style="margin-top:10px">Integrated from the recorded power every poll with a priority model (solar → home → battery → grid). Approximate: the inverter doesn't report the flows themselves.</p>`;
        renderSankey(document.getElementById('sankey'), flow);
    } catch (e) {
        wrap.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    }
    sankeyLoading = false;
}

onTab('dashboard', () => {
    lastSideHtml = '';
    lastTilesHtml = '';
    renderDashboard();
    loadLive();
    loadSankey();
    setTimeout(redrawCharts, 0);
});
onLive(renderDashboard);
