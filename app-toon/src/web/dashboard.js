// Toon dashboard - the main page: what matters right now, and the last 24 hours.

let dashChartsAt = 0;

function renderNotices() {
    const s = app.status;
    const notes = [];
    if (!s.connected) notes.push(s.error ? `The Toon can't be reached: ${s.error}` : 'Connecting to the Toon…');
    const offset = value('clock_offset');
    if (!missing(offset) && Math.abs(offset) > 120) {
        notes.push(`The Toon's clock is ${fmtDuration(Math.abs(offset))} ${offset < 0 ? 'behind' : 'ahead'}, so its week program runs that much off.`);
    }
    if (value('boiler_fault')) notes.push('The boiler reports a fault.');
    if (value('opentherm_error')) notes.push('The Toon reports an OpenTherm communication error with the boiler.');
    const pressure = value('boiler_pressure');
    if (!missing(pressure) && pressure < PRESSURE_LOW) {
        notes.push(`The boiler's water pressure is low: ${num(pressure, 2)} bar. Usual is 1 to 2 bar; see your boiler's manual.`);
    }
    document.getElementById('dash-notices').innerHTML = notes.map(text => `<div class="setup-hint"><span>⚠ ${escapeHtml(text)}</span></div>`).join('');
}

function renderTiles() {
    const today = (app.today && app.today.usage.today) || {};
    const before = (app.today && app.today.usage.yesterday) || {};
    const burner = value('burner_state');
    const on = ['heating', 'preheating', 'hot_water'].includes(burner);
    const burn = app.today && app.today.burner;
    const pressure = value('boiler_pressure');
    const low = !missing(pressure) && pressure < PRESSURE_LOW;
    const imported = value('power_import') || 0;
    const exported = value('power_export') || 0;
    const exporting = exported > imported;
    const preset = PRESETS[value('active_state')];
    const importToday = sum(today.import_low, today.import_normal);
    document.getElementById('dash-tiles').innerHTML = [
        tile('Room', unit(value('room_temperature'), 1, '°C'), `setpoint ${fmtTemp(value('setpoint'))}${preset ? ' · ' + preset : ''}`),
        tile('Burner', escapeHtml(BURNER[burner] || '–'), on ? `modulation ${num(value('modulation'))}%` : (burn ? `today ${fmtMinutes(burn.heat_minutes + burn.dhw_minutes)}` : ''), on ? 'flame' : ''),
        tile('Water pressure', unit(pressure, 2, 'bar'), low ? '⚠ low: under 1 bar' : 'usual 1 to 2 bar', low ? 'warn' : ''),
        tile(exporting ? 'Power to grid' : 'Power from grid', exporting ? unit(exported, 0, 'W') : unit(imported, 0, 'W'), `${fmtKwh(importToday)} today`),
        tile('Gas today', unit(today.gas, 2, 'm³'), `yesterday ${fmtGas(before.gas)}`),
    ].join('');
}

function renderModes() {
    const s = app.status;
    const active = value('active_state');
    const next = s.next || {};
    const nextText = next.time
        ? `${escapeHtml(fmtTime(next.time))}${PRESETS[next.state] ? ' · ' + PRESETS[next.state] : ''}${missing(value('next_setpoint')) ? '' : ' · ' + fmtTemp(value('next_setpoint'))}`
        : (missing(value('next_setpoint')) ? '–' : fmtTemp(value('next_setpoint')));
    const today = app.today || {usage: {today: {}, yesterday: {}}, burner: {}};
    const usage = today.usage.today;
    const burn = today.burner;
    const presets = [[0, 'preset_comfort'], [1, 'preset_home'], [2, 'preset_sleep'], [3, 'preset_away']];
    const exportToday = sum(usage.export_low, usage.export_normal);
    const importToday = sum(usage.import_low, usage.import_normal);
    const pressure = today.pressure || {};
    document.getElementById('dash-modes').innerHTML = `
        <div class="block">
            <div class="block-title"><span>Thermostat</span><span>${value('preheating') ? 'preheating' : ''}</span></div>
            <div class="facts">${factsGrid([
                fact('Preset', escapeHtml(PRESETS[active] || '–')),
                fact('Program', escapeHtml(PROGRAMS[value('program_state')] || '–')),
                fact('Next change', nextText),
                fact('Hot water', value('dhw_enabled') === 0 ? 'off' : fmtTemp(value('dhw_setpoint'), 0)),
            ])}</div>
            <div class="presets" style="margin-top:8px">${presets.map(([state, key]) =>
                `<div class="preset ${active === state ? 'active' : ''}">${PRESETS[state]}<b>${fmtTemp(value(key))}</b></div>`).join('')}</div>
        </div>
        <div class="block">
            <div class="block-title"><span>Boiler</span><span>${stateLabel(value('boiler_fault') ? 'bad' : 'ok', value('boiler_fault') ? 'fault' : 'no fault')}</span></div>
            <div class="facts three">${factsGrid([
                fact('Burner hours', unit(value('burner_hours'), 0, 'h')),
                fact('Hot water hours', unit(value('dhw_burner_hours'), 0, 'h')),
                fact('Failed starts', num(value('failed_burner_starts'))),
                fact('OpenTherm', value('opentherm_error') ? stateLabel('bad', 'error') : stateLabel('ok', 'ok')),
                fact('Maximum temp.', fmtTemp(value('max_heater_temperature'), 0)),
                fact('Water loss', missing(pressure.loss_per_week) ? '–' : `${num(pressure.loss_per_week, 2)}<small> bar/week</small>`),
            ], 3)}</div>
        </div>
        <div class="block">
            <div class="block-title"><span>Today</span><span>${missing(today.base_load) ? '' : 'night base load ' + fmtW(today.base_load)}</span></div>
            <div class="facts three">${factsGrid([
                fact('Heating', burn.collecting_since ? fmtMinutes(burn.heat_minutes) : 'collecting'),
                fact('Hot water', burn.collecting_since ? fmtMinutes(burn.dhw_minutes) : 'collecting'),
                fact('Burner starts', burn.collecting_since ? num(burn.starts) : 'collecting'),
                fact('Average run', fmtMinutes(burn.average_minutes)),
                fact('From grid', fmtKwh(importToday)),
                fact('To grid', fmtKwh(exportToday)),
            ], 3)}</div>
        </div>`;
}

async function loadDashCharts() {
    const end = Math.floor(Date.now() / 1000);
    const start = end - 24 * 3600;
    const step = 300;
    const keys = 'room_temperature,setpoint,boiler_temperature,boiler_return_temperature,boiler_setpoint,power_import,power_export,burner_code';
    const data = await api(`history?start=${start}&end=${end}&metrics=${keys}`);
    const xs = gridTimes(start, end, step);
    const bands = burnerBands(data.burner_code, start, end, step);
    const ticks = hourTicks(start, end, 4);
    const line = (key) => grid(data[key], start, end, step, 1800);
    const legend = (id, items) => { document.getElementById(id).innerHTML = legendHtml(items); };
    const bandLegend = bands.length ? [{name: 'burner: heating', color: color('flame'), kind: 'band'}, {name: 'burner: hot water', color: color('dhw'), kind: 'band'}] : [];

    const room = [
        {name: 'Room', color: color('series-1'), values: line('room_temperature')},
        {name: 'Setpoint', color: color('series-2'), values: line('setpoint'), width: 1.5},
    ];
    lineChart(document.getElementById('chart-room'), {
        x: xs, series: room, xTicks: ticks, bands, formatX: fmtDateTime, formatY: v => fmtTemp(v, 1), empty: 'No room temperature yet',
    });
    legend('legend-room', [...room.map(s => ({name: s.name, color: s.color, kind: 'line'})), ...bandLegend]);

    const boiler = [
        {name: 'Supply', color: color('supply'), values: line('boiler_temperature')},
        {name: 'Return', color: color('return'), values: line('boiler_return_temperature')},
        {name: 'Boiler setpoint', color: color('series-4'), values: line('boiler_setpoint'), width: 1.5},
    ];
    lineChart(document.getElementById('chart-boiler'), {
        x: xs, series: boiler, xTicks: ticks, bands, formatX: fmtDateTime, formatY: v => fmtTemp(v, 1), empty: 'No boiler temperatures yet',
    });
    legend('legend-boiler', [...boiler.map(s => ({name: s.name, color: s.color, kind: 'line'})), ...bandLegend]);

    const power = [
        {name: 'From grid', color: color('series-1'), values: line('power_import'), area: true},
        {name: 'To grid', color: color('series-3'), values: line('power_export'), area: true},
    ];
    lineChart(document.getElementById('chart-power'), {
        x: xs, series: power, xTicks: ticks, zero: true, formatX: fmtDateTime, formatY: v => fmtW(v), empty: 'No power readings yet',
    });
    legend('legend-power', power.map(s => ({name: s.name, color: s.color, kind: 'line'})));
    dashChartsAt = Date.now();
}

function renderGasHours() {
    const hours = (app.today && app.today.gas_hours) || [];
    barChart(document.getElementById('chart-gas'), {
        data: hours.map(h => ({
            label: `${String(new Date(h.ts * 1000).getHours()).padStart(2, '0')}:00`,
            tipTitle: fmtDateTime(h.ts),
            values: [h.gas],
        })),
        series: [{name: 'Gas', color: color('series-4')}],
        format: fmtGas,
        height: 170,
        labelWidth: 48,
        empty: 'No gas used in the last 24 hours',
    });
}

function renderDashboardLive() {
    renderNotices();
    renderTiles();
    renderHeating();
    renderModes();
}

async function showDashboard() {
    renderDashboardLive();
    try {
        app.today = await api('today');
        renderDashboardLive();
        renderGasHours();
        await loadDashCharts();
    } catch (e) {
        toast('Cannot load the charts: ' + e.message);
    }
}

onTab('dashboard', showDashboard, () => {
    renderDashboardLive();
    if (Date.now() - dashChartsAt > 60000) showDashboard();
});
