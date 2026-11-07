// GoodWe Monitor dashboard - Battery view: state of charge, battery details, the controls and the day's battery history.

let controlRendered = false;
let batteryDayLoadedAt = 0;
let inverterSettings = null;

function renderBatteryState() {
    const d = app.data, c = app.control;
    const soc = missing(d.battery_soc) ? null : Number(d.battery_soc);
    const bs = batteryState(d);
    const hold = holdInfo(c);
    const meterClass = bs === 'charging' ? 'charging' : bs === 'discharging' ? 'discharging' : hold.kind === 'held' ? 'held' : (soc !== null && soc < 20 ? 'low' : '');
    const pbat = Number(d.pbattery1 || 0);
    const floor = c.dod_reported === null || c.dod_reported === undefined ? null : 100 - Number(c.dod_reported);
    const stateText = bs === 'charging' ? `charging ${fmtW(pbat)}` : bs === 'discharging' ? `discharging ${fmtW(pbat)}` : 'idle';
    document.getElementById('battery-state').innerHTML = `
        <div class="block-title"><span>State of charge</span><span>${escapeHtml(d.battery_mode_label || '')}</span></div>
        <div class="battery">
            <span class="pct">${soc === null ? '–' : num(soc)}<small>%</small></span>
            <span class="range">${stateLabel(bs, stateText)}</span>
            <span class="est">${stateLabel(hold.kind, hold.text)}<br>${escapeHtml(hold.detail)}</span>
        </div>
        <div class="meter ${meterClass}" role="meter" aria-valuenow="${soc || 0}" aria-valuemin="0" aria-valuemax="100" aria-label="Battery">
            <i style="width:${soc || 0}%"></i>
            ${floor === null || floor <= 0 ? '' : `<span class="limit" style="left:calc(${floor}% - 1px)" title="Discharge floor ${floor}%"></span>`}
            ${c.charge_cap_soc ? `<span class="limit" style="left:calc(${c.charge_cap_soc}% - 1px);background:var(--warning-color)" title="Charge cap ${c.charge_cap_soc}%"></span>` : ''}
        </div>
        <div class="live-note">${floor === null ? 'Discharge floor not read yet.' : `Discharge floor ${floor}% (DoD ${c.dod_reported}%)`}${c.charge_cap_soc ? ` · charge cap ${c.charge_cap_soc}%` : ''}${c.surplus ? ` · <b>solar surplus ≈ ${fmtW(c.surplus_w)}</b>` : ''}</div>
        <div class="facts three" style="margin-top:12px">${factsGrid([
            fact('Charged today', energyUnit(d.e_bat_charge_day, 2), 'ok'),
            fact('Discharged today', energyUnit(d.e_bat_discharge_day, 2)),
            fact('Health', fmtPct(d.battery_soh), Number(d.battery_soh) < 80 ? 'warn' : ''),
        ], 3)}</div>`;
}

function renderBatteryFacts() {
    const d = app.data, s = inverterSettings || app.inverter.settings_cache || {};
    document.getElementById('battery-facts').innerHTML = factsGrid([
        fact('Voltage', missing(d.vbattery1) ? '–' : withUnit(num(d.vbattery1, 1), 'V')),
        fact('Current', missing(d.ibattery1) ? '–' : withUnit(num(d.ibattery1, 1), 'A')),
        fact('Temperature', fmtTemp(d.battery_temperature), Number(d.battery_temperature) > 45 ? 'warn' : ''),
        fact('Power', wUnit(d.pbattery1, true)),
        fact('Charge limit (BMS)', missing(d.battery_charge_limit) ? '–' : withUnit(num(d.battery_charge_limit), 'A')),
        fact('Discharge limit (BMS)', missing(d.battery_discharge_limit) ? '–' : withUnit(num(d.battery_discharge_limit), 'A')),
        fact('Charged all time', energyUnit(d.e_bat_charge_total)),
        fact('Discharged all time', energyUnit(d.e_bat_discharge_total)),
    ]);
    const bms = [
        fact('Status', missing(d.battery_status) ? '–' : escapeHtml(String(d.battery_status))),
        fact('Error · warning', `${missing(d.battery_error) ? '–' : d.battery_error} · ${missing(d.battery_warning) ? '–' : d.battery_warning}`, Number(d.battery_error) ? 'bad' : ''),
        missing(d.battery_max_cell_voltage) ? '' : fact('Cell voltage', `${num(d.battery_min_cell_voltage, 3)} – ${num(d.battery_max_cell_voltage, 3)} V`),
        missing(d.battery_max_cell_temp) ? '' : fact('Cell temperature', `${num(d.battery_min_cell_temp, 1)} – ${num(d.battery_max_cell_temp, 1)} °C`),
        missing(d.bms_bat_soc) ? '' : fact('BMS SoC', fmtPct(d.bms_bat_soc)),
        s.capacity === undefined ? '' : fact('Capacity', withUnit(num(s.capacity), 'Ah')),
        s.charge_v === undefined ? '' : fact('Charge voltage · current', `${num(s.charge_v, 1)} V <small>${num(s.charge_i)} A</small>`),
        s.discharge_v === undefined ? '' : fact('Discharge voltage · current', `${num(s.discharge_v, 1)} V <small>${num(s.discharge_i)} A</small>`),
        s.battery_soc_protection === undefined ? '' : fact('SoC protection', fmtPct(s.battery_soc_protection)),
    ];
    document.getElementById('battery-bms').innerHTML = factsGrid(bms)
        + (s.capacity === undefined ? '<div class="fact" style="grid-column:1/-1"><div class="l">Commissioning profile</div><div class="v"><small>read in Settings → Debug → Read settings</small></div></div>' : '');
}

// ---------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------
function numInput(key, value, unit, attrs) {
    return `<span class="num"><input class="input" type="number" ${attrs || ''} value="${value}" data-key="${key}" onchange="saveControlInput(this)"><small>${unit}</small></span>`;
}

function renderControl(force) {
    const c = app.control;
    if (!c || c.auto_hold === undefined) return;
    const el = document.getElementById('battery-control');
    if (controlRendered && !force) {
        // Only the status lines change while the page is open; the inputs keep what is being typed
        const dec = el.querySelector('#control-decision');
        if (dec) dec.innerHTML = controlDecisionHtml(c);
        const cap = el.querySelector('#control-cap-status');
        if (cap) cap.innerHTML = controlCapHtml(c);
        const exp = el.querySelector('#control-export-status');
        if (exp) exp.innerHTML = controlExportHtml(c);
        const sur = el.querySelector('#control-surplus-status');
        if (sur) sur.innerHTML = controlSurplusHtml(c);
        el.querySelectorAll('[data-dod]').forEach(b => b.classList.toggle('active', Number(b.dataset.dod) === Number(c.dod_reported)));
        return;
    }
    controlRendered = true;
    const row = (label, sub, control, hint, id) => `<div class="control-row"><div class="cl">${label}<small>${sub}</small></div><div>${control}</div><div class="ch" ${id ? `id="${id}"` : ''}>${hint}</div></div>`;
    el.innerHTML = `
        <div class="control-group">
            <div class="section-title">Discharge</div>
            <div class="control-rows">
                ${row('Battery', 'writes the on-grid depth of discharge',
                    `<span class="detail-actions"><button type="button" class="btn${Number(c.dod_reported) === 0 ? ' active' : ''}" data-dod="0" onclick="setDod(0)">Hold</button><button type="button" class="btn${Number(c.dod_reported) === Number(c.night_dod) ? ' active' : ''}" data-dod="${c.night_dod}" onclick="setDod(${c.night_dod})">Normal</button></span>`,
                    `<b>Hold</b> keeps the battery for later: the grid covers the house. <b>Normal</b> lets it discharge to ${100 - c.night_dod}%. With auto-hold on, the next decision wins again.`)}
            </div>
        </div>
        <div class="control-group">
            <div class="section-title">Auto-hold · by solar forecast</div>
            <div class="control-rows">
                ${row('Auto-hold', 'hold while solar is expected, release otherwise', switchHtml('ctl-auto-hold', c.auto_hold, "saveControl({auto_hold: !app.control.auto_hold})"), controlDecisionHtml(c), 'control-decision')}
                ${row('Threshold', 'forecast for the current hour, or the live production', numInput('threshold_kwh', c.threshold_kwh, 'kWh', 'step="0.05" min="0" max="100"'), 'Hold while at least this much is expected this hour. 0.1 kWh starts the hold at the first light.')}
                ${row('Forecast offset', 'correction before the threshold test', numInput('forecast_offset_pct', c.forecast_offset_pct, '%', 'step="5" min="-50" max="50"'), 'The forecast is multiplied by (100 + offset)%. Use the bias on the Solar page: −20% when the forecast runs high, +20% when it runs low.')}
                ${row('Release at', 'state of charge', numInput('release_soc', c.release_soc, '%', 'step="1" min="0" max="100"'), 'Once the battery reaches this, the hold is released so it can serve the house as soon as the load exceeds the production. 100 holds all day.')}
                ${row('Depth of discharge', 'while held · when released', `${numInput('day_dod', c.day_dod, '%', 'step="5" min="0" max="100"')} ${numInput('night_dod', c.night_dod, '%', 'step="5" min="0" max="100"')}`, 'DoD 0 = no discharge (floor 100%), DoD 80 = discharge to a floor of 20%.')}
            </div>
        </div>
        <div class="control-group">
            <div class="section-title">Charge cap</div>
            <div class="control-rows">
                ${row('Stop charging at', 'state of charge · 0 = off', numInput('charge_cap_soc', c.charge_cap_soc, '%', 'step="1" min="0" max="100"'), controlCapHtml(c), 'control-cap-status')}
                ${row('Release current', 'restored when the brake comes off', numInput('charge_cap_release_current', c.charge_cap_release_current, 'A', 'step="1" min="1" max="500"'), `The BMS clamps it to its own limit (${missing(app.data.battery_charge_limit) ? 'unknown' : num(app.data.battery_charge_limit) + ' A'}), so a high value is safe.`)}
                ${row('Full charge every', 'days · 0 = never', numInput('charge_cap_full_every_days', c.charge_cap_full_every_days, 'days', 'step="1" min="0" max="365"'), 'LFP batteries need a periodic 100% charge for cell balancing; the cap pauses for a day this often.')}
            </div>
        </div>
        <div class="control-group">
            <div class="section-title">Zero export</div>
            <div class="control-rows">
                ${row('Zero export', 'export limit 0 W, limit enforced', switchHtml('ctl-zero-export', c.zero_export, "saveControl({zero_export: !app.control.zero_export})"), controlExportHtml(c), 'control-export-status')}
            </div>
        </div>
        <div class="control-group">
            <div class="section-title">Solar surplus · for Home Assistant</div>
            <div class="control-rows">
                ${row('Margin', 'surplus needed to switch on', numInput('surplus_margin_w', c.surplus_margin_w, 'W', 'step="50" min="0" max="50000"'), controlSurplusHtml(c), 'control-surplus-status')}
                ${row('Battery full from', 'state of charge', numInput('surplus_full_soc', c.surplus_full_soc, '%', 'step="1" min="50" max="100"'), 'The battery counts as full from here, or whenever the charge cap is braking.')}
                ${row('Off when importing', 'grid import that ends the surplus', numInput('surplus_off_import_w', c.surplus_off_import_w, 'W', 'step="50" min="0" max="50000"'), 'The signal goes off once the house draws more than this from the grid, or drains the battery. That is the hysteresis: a load you switch on does not switch the signal off as long as the sun carries it.')}
                ${row('Debounce', 'before the signal flips', numInput('surplus_debounce_s', c.surplus_debounce_s, 's', 'step="10" min="0" max="3600"'), 'Published to MQTT as the binary sensor "Solar surplus" plus the estimated surplus in watts, and as surplus_started / surplus_ended events.')}
            </div>
        </div>`;
}

function controlDecisionHtml(c) {
    if (!c.auto_hold) return 'Off: the Hold and Normal buttons decide.';
    const d = c.decision || {};
    const extra = c.last_tick ? ` <small>· checked ${fmtAgo(c.last_tick)}${c.dod_writes_today ? ` · ${c.dod_writes_today}/${c.dod_writes_per_day} writes today` : ''}</small>` : '';
    return `<b>${escapeHtml(d.text || 'Deciding at the next check (every 5 minutes)')}</b>${extra}`;
}
function controlCapHtml(c) {
    if (!c.charge_cap_soc) return 'Off: the battery charges to 100%.';
    return `Brakes all charging (charge current 0 A) at ${c.charge_cap_soc}%, releases at ${c.charge_cap_soc - 5}%${c.charge_cap_active ? ' · <b>braking now</b>' : ''}${c.charge_cap_last_full ? ` · last full charge ${escapeHtml(c.charge_cap_last_full)}` : ''}.${c.zero_export ? ' <span class="sb-warn">With zero export, braked solar is curtailed, not exported.</span>' : ' The surplus exports instead.'}`;
}
function controlExportHtml(c) {
    if (!c.zero_export) return 'Off: the inverter exports what the house and battery cannot take.';
    const lim = c.export_limit_reported, sw = c.export_switch_reported;
    const reported = lim === null || lim === undefined ? 'not read yet' : `inverter reports limit ${num(lim)} W, switch ${sw ? 'on' : 'off'}`;
    return `${c.export_verified ? '<b>Verified</b>' : 'Checking the inverter at the next tick'} · ${reported}${c.export_strikes ? ` · exporting for ${c.export_strikes * 5} min` : ''}. Surplus that the house and battery cannot take is curtailed.`;
}
function controlSurplusHtml(c) {
    return `${c.surplus ? '<b class="sb-ok">Surplus available</b>' : '<b>No surplus</b>'} · ${escapeHtml(c.surplus_reason || '')}${c.zero_export ? ' <small>(under zero export the curtailed part is estimated from the forecast)</small>' : ''}`;
}

async function saveControl(changes) {
    try {
        const state = await api('control', {method: 'POST', body: changes});
        app.control = state;
        renderControl(true);
        renderBatteryState();
        toast('Battery control saved', 'success');
    } catch (e) {
        toast(e.message, 'error');
        renderControl(true);
    }
}

function saveControlInput(input) {
    const value = Number(input.value);
    if (Number.isNaN(value)) return;
    saveControl({[input.dataset.key]: value});
}

async function setDod(dod) {
    const hold = dod === 0;
    const ok = await confirmDialog({
        title: hold ? 'Hold the battery?' : 'Allow discharging?',
        message: hold
            ? 'Sets the on-grid depth of discharge to 0%: the battery stops discharging and the grid covers the house until you release it' + (app.control.auto_hold ? ' or auto-hold decides otherwise.' : '.')
            : `Sets the on-grid depth of discharge to ${dod}%: the battery may discharge down to ${100 - dod}%.`,
        confirmLabel: hold ? 'Hold' : 'Normal',
    });
    if (!ok) return;
    try {
        const result = await api('control/dod', {method: 'POST', body: {dod}});
        app.control = result.control;
        toast(result.result, 'success');
        renderControl(true);
        renderBatteryState();
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ---------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------
async function loadBatteryDay() {
    batteryDayLoadedAt = Date.now();
    const el = document.getElementById('chart-battery-day');
    let power = document.getElementById('chart-battery-day-power');
    if (!power) {
        power = document.createElement('div');
        power.id = 'chart-battery-day-power';
        power.className = 'chart';
        power.style.marginTop = '8px';
        el.after(power);
    }
    try {
        const day = await api('day');
        const rows = day.series || [];
        const xs = rows.map(r => r.ts);
        const step = (day.bucket_min || 5) * 60;
        const colors = {chg: cssVar('--bat-chg'), dis: cssVar('--bat-dis'), hold: cssVar('--soc'), soc: cssVar('--soc')};
        const bands = stateBands(xs, rows.map(r => r.state), {charge: colors.chg, discharge: colors.dis, hold: colors.hold}, step);
        lineChart(el, {
            x: xs, series: [{name: 'State of charge', color: colors.soc, values: rows.map(r => r.soc), area: true}],
            height: 150, min: 0, max: 100, bands,
            xTicks: xs.length > 1 ? hourTicks(xs[0], xs[xs.length - 1], 3) : [], formatX: fmtTime, formatY: v => fmtPct(v),
            extraRows: i => [{value: rows[i].state, name: ''}],
            empty: 'No battery data recorded yet today',
        });
        lineChart(power, {
            x: xs, series: [{name: 'Battery power', color: colors.chg, values: rows.map(r => r.battery / 1000), area: true}],
            height: 130, zero: true, bands,
            xTicks: xs.length > 1 ? hourTicks(xs[0], xs[xs.length - 1], 3) : [], formatX: fmtTime, formatY: v => `${fmtKw(v * 1000)} ${v > 0 ? 'charging' : v < 0 ? 'discharging' : ''}`,
            empty: ' ',
        });
        document.getElementById('legend-battery-day').innerHTML = legendHtml([
            {name: 'State of charge', color: colors.soc, kind: 'line'}, {name: 'Charging', color: colors.chg, kind: 'band'},
            {name: 'Discharging', color: colors.dis, kind: 'band'}, {name: 'Held (grid covers the house)', color: colors.hold, kind: 'band'},
        ]);
    } catch (e) {
        emptyChart(el, e.message);
    }
    try {
        const bounds = rangeBounds('30d');
        const stats = await api(`stats?from=${bounds.from}&to=${bounds.to}&bucket=day`);
        const chg = cssVar('--bat-chg'), dis = cssVar('--bat-dis');
        barChart(document.getElementById('chart-battery-days'), {
            data: stats.periods.map(p => ({label: p.period.slice(8), tipTitle: fmtIsoDate(p.period), values: [p.e_bat_charge_day, p.e_bat_discharge_day]})),
            series: [{name: 'Charged', color: chg}, {name: 'Discharged', color: dis}], grouped: true, format: v => fmtKwh(v, 2), height: 170, labelWidth: 26,
        });
        document.getElementById('legend-battery-days').innerHTML = legendHtml([{name: 'Charged', color: chg}, {name: 'Discharged', color: dis}]);
    } catch (e) { /* the chart stays empty */ }
}

onTab('battery', () => {
    renderBatteryState();
    renderBatteryFacts();
    renderControl(true);
    loadBatteryDay();
    if (!inverterSettings) api('inverter/settings?cached=1').then(r => { inverterSettings = r.settings; renderBatteryFacts(); }).catch(() => {});
});
onLive(() => {
    if (activeTab !== 'battery') return;
    renderBatteryState();
    renderBatteryFacts();
    renderControl(false);
    if (Date.now() - batteryDayLoadedAt > 300000) loadBatteryDay();
});
