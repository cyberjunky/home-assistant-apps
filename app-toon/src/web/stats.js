// Toon dashboard - Stats: everything to analyse heating, smart meter and boiler.
// Charts are defined as data, so another source (such as sensors on the pipes) only needs entries.

const RANGES = [
    {key: '7d', label: '7 days', days: 7},
    {key: '30d', label: '30 days', days: 30},
    {key: '90d', label: '90 days', days: 90},
    {key: '12m', label: '12 months', days: 365},
    {key: 'year', label: 'This year'},
    {key: 'all', label: 'All'},
];
const GROUPS = [{key: 'day', label: 'Day'}, {key: 'week', label: 'Week'}, {key: 'month', label: 'Month'}, {key: 'year', label: 'Year'}];
const DEFAULT_GROUP = {'7d': 'day', '30d': 'day', '90d': 'week', '12m': 'month', year: 'month', all: 'year'};
const MAX_BUCKETS = 370;

const stats = {range: '30d', year: '', bucket: 'day', report: null, day: todayIso(), loading: 0};

function statsBounds() {
    const today = todayIso();
    if (stats.year) return {from: `${stats.year}-01-01`, to: `${stats.year}-12-31`};
    const preset = RANGES.find(r => r.key === stats.range);
    if (preset.key === 'all') return {from: '2000-01-01', to: today};
    if (preset.key === 'year') return {from: `${today.slice(0, 4)}-01-01`, to: today};
    return {from: shiftDate(today, -(preset.days - 1)), to: today};
}

function bucketLabel(key, bucket) {
    if (bucket === 'year') return key;
    if (bucket === 'month') {
        const d = new Date(key + '-01T00:00:00');
        return d.toLocaleDateString([], {month: 'short', year: '2-digit'});
    }
    return new Date(key + 'T00:00:00').toLocaleDateString([], {day: 'numeric', month: 'short'});
}
function bucketTitle(key, bucket) {
    if (bucket === 'year') return key;
    if (bucket === 'month') return new Date(key + '-01T00:00:00').toLocaleDateString([], {month: 'long', year: 'numeric'});
    if (bucket === 'week') return `Week of ${fmtIsoDate(key, true)}`;
    return fmtIsoDate(key, true);
}

// =====================================================================
// Chart definitions. kind: bars or line; rows: which fields of a period
// =====================================================================
const COLLECTING = 'Collecting since this version: the Toon has no history of this.';
const METER_CHARTS = [
    {id: 'gas', title: 'Gas', unit: 'm³', wide: true, kind: 'bars', grouped: true, format: fmtGas, series: [
        {name: 'This period', field: 'gas', color: 'series-4'},
        {name: 'A year earlier', field: 'gas_year_before', color: 'muted-text-color', optional: true},
    ]},
    {id: 'import', title: 'Electricity from the grid', unit: 'kWh', kind: 'bars', format: fmtKwh, series: [
        {name: 'Low tariff', field: 'import_low', color: 'series-1'},
        {name: 'Normal tariff', field: 'import_normal', color: 'series-2'},
    ]},
    {id: 'export', title: 'Electricity to the grid', unit: 'kWh', kind: 'bars', format: fmtKwh, series: [
        {name: 'Low tariff', field: 'export_low', color: 'series-3'},
        {name: 'Normal tariff', field: 'export_normal', color: 'series-5'},
    ]},
    {id: 'power', title: 'Power from the grid', unit: 'W', kind: 'line', format: fmtW, zero: true, empty: 'The Toon keeps power for 31 days', series: [
        {name: 'Highest', gauge: 'power_import', part: 'max', color: 'muted-text-color', width: 1.5, dash: '4 4'},
        {name: 'Average', gauge: 'power_import', part: 'avg', color: 'series-1'},
    ]},
    {id: 'power-export', title: 'Power to the grid', unit: 'W', kind: 'line', format: fmtW, zero: true, empty: 'The Toon keeps power for 31 days', series: [
        {name: 'Highest', gauge: 'power_export', part: 'max', color: 'muted-text-color', width: 1.5, dash: '4 4'},
        {name: 'Average', gauge: 'power_export', part: 'avg', color: 'series-3'},
    ]},
    {id: 'base', title: 'Night base load', unit: 'W, between 2:00 and 5:00', wide: true, kind: 'line', format: fmtW, zero: true, series: [
        {name: 'Base load', field: 'base_load', color: 'series-1'},
    ]},
];
const HEATING_CHARTS = [
    {id: 'room', title: 'Room temperature and setpoint', unit: '°C', wide: true, kind: 'line', format: v => fmtTemp(v, 1), empty: 'The Toon keeps these for 30 days; from then on this app does', series: [
        {name: 'Room, highest', gauge: 'room_temperature', part: 'max', color: 'muted-text-color', width: 1.5, dash: '4 4'},
        {name: 'Room, average', gauge: 'room_temperature', part: 'avg', color: 'series-1'},
        {name: 'Room, lowest', gauge: 'room_temperature', part: 'min', color: 'muted-text-color', width: 1, dash: '2 4'},
        {name: 'Setpoint, average', gauge: 'setpoint', part: 'avg', color: 'series-2'},
    ]},
    {id: 'supply', title: 'Boiler supply temperature', unit: '°C', kind: 'line', format: v => fmtTemp(v, 1), empty: COLLECTING, series: [
        {name: 'Highest', gauge: 'boiler_temperature', part: 'max', color: 'muted-text-color', width: 1.5, dash: '4 4'},
        {name: 'Average', gauge: 'boiler_temperature', part: 'avg', color: 'supply'},
    ]},
    {id: 'return', title: 'Boiler return temperature', unit: '°C', kind: 'line', format: v => fmtTemp(v, 1), empty: COLLECTING, series: [
        {name: 'Highest', gauge: 'boiler_return_temperature', part: 'max', color: 'muted-text-color', width: 1.5, dash: '4 4'},
        {name: 'Average', gauge: 'boiler_return_temperature', part: 'avg', color: 'return'},
    ]},
    {id: 'boiler-setpoint', title: 'Boiler setpoint', unit: '°C', kind: 'line', format: v => fmtTemp(v, 1), series: [
        {name: 'Highest', gauge: 'boiler_setpoint', part: 'max', color: 'muted-text-color', width: 1.5, dash: '4 4'},
        {name: 'Average', gauge: 'boiler_setpoint', part: 'avg', color: 'series-4'},
    ]},
    {id: 'limits', title: 'Hot water setpoint and maximum boiler temperature', unit: '°C', kind: 'line', format: v => fmtTemp(v, 0), empty: COLLECTING, series: [
        {name: 'Hot water setpoint', gauge: 'dhw_setpoint', part: 'avg', color: 'dhw'},
        {name: 'Maximum boiler temperature', gauge: 'max_heater_temperature', part: 'avg', color: 'series-2'},
    ]},
];
const BOILER_CHARTS = [
    {id: 'hours', title: "The boiler's own hour counters", unit: 'hours', wide: true, kind: 'bars', grouped: true, format: v => `${num(v, 1)} h`, empty: COLLECTING, series: [
        {name: 'Burner hours', field: 'burner_hours', color: 'flame'},
        {name: 'Hot water burner hours', field: 'dhw_hours', color: 'dhw'},
        {name: "Burner minutes in the Toon's history, in hours", derive: r => r.burner_minutes === null ? null : r.burner_minutes / 60, color: 'series-5', optional: true},
    ]},
    {id: 'on-time', title: 'Burner time per state', unit: 'hours', kind: 'bars', format: v => `${num(v, 1)} h`, empty: COLLECTING, series: [
        {name: 'Heating', derive: r => r.heat_minutes === null ? null : r.heat_minutes / 60, color: 'flame'},
        {name: 'Preheating', derive: r => r.preheat_minutes === null ? null : r.preheat_minutes / 60, color: 'series-2'},
        {name: 'Hot water', derive: r => r.dhw_minutes === null ? null : r.dhw_minutes / 60, color: 'dhw'},
    ]},
    {id: 'cycles', title: 'Burner starts', unit: 'count', kind: 'bars', format: v => num(v), empty: COLLECTING, series: [
        {name: 'Runs of 10 minutes or more', derive: r => r.starts === null ? null : r.starts - r.short_cycles, color: 'series-1'},
        {name: 'Short runs, under 10 minutes', field: 'short_cycles', color: 'series-2'},
    ]},
    {id: 'cycle-length', title: 'Average burner run', unit: 'minutes', kind: 'line', format: v => `${num(v, 1)} min`, zero: true, empty: COLLECTING, series: [
        {name: 'Average run', field: 'average_cycle', color: 'series-1'},
    ]},
    {id: 'failed', title: 'Failed burner starts', unit: 'count', kind: 'bars', format: v => num(v), empty: 'None in this period, or still collecting', series: [
        {name: 'Failed starts', field: 'failed_starts', color: 'series-2'},
    ]},
    {id: 'while-burning', title: 'Temperatures while heating', unit: '°C, weighted by time', wide: true, kind: 'line', format: v => fmtTemp(v, 1), empty: COLLECTING, hlines: [{at: 55, label: 'condensing below about 55 °C'}], series: [
        {name: 'Supply', field: 'supply_while_burning', color: 'supply'},
        {name: 'Boiler setpoint', field: 'setpoint_while_burning', color: 'series-4'},
        {name: 'Return', field: 'return_while_burning', color: 'return'},
    ]},
    {id: 'delta', title: 'Supply minus return while heating', unit: '°C', kind: 'line', format: v => fmtTemp(v, 1), zero: true, empty: COLLECTING, series: [
        {name: 'Difference', derive: r => missing(r.supply_while_burning) || missing(r.return_while_burning) ? null : r.supply_while_burning - r.return_while_burning, color: 'series-1'},
    ]},
    {id: 'overshoot', title: 'Supply above boiler setpoint while heating', unit: '°C', kind: 'line', format: v => fmtTemp(v, 1), empty: COLLECTING, hlines: [{at: 0}], series: [
        {name: 'Overshoot', derive: r => missing(r.supply_while_burning) || missing(r.setpoint_while_burning) ? null : r.supply_while_burning - r.setpoint_while_burning, color: 'series-2'},
    ]},
    {id: 'pressure', title: 'Water pressure, lowest of the period', unit: 'bar', kind: 'line', format: v => `${num(v, 2)} bar`, hlines: [{at: PRESSURE_LOW, label: '1 bar'}], series: [
        {name: 'Pressure', field: 'pressure_min', color: 'series-1'},
    ]},
];
// Day detail: stacked on one timeline, one unit per chart
const DAY_CHARTS = [
    {id: 'day-temp', title: 'Boiler temperatures · °C', height: 170, format: v => fmtTemp(v, 1), bands: true, series: [
        {name: 'Supply', metric: 'boiler_temperature', color: 'supply'},
        {name: 'Return', metric: 'boiler_return_temperature', color: 'return'},
        {name: 'Boiler setpoint', metric: 'boiler_setpoint', color: 'series-4', width: 1.5},
    ]},
    {id: 'day-mod', title: 'Modulation · %', height: 110, format: v => `${num(v)}%`, zero: true, bands: true, series: [
        {name: 'Modulation', metric: 'modulation', color: 'flame', area: true},
    ]},
    {id: 'day-room', title: 'Room · °C', height: 130, format: v => fmtTemp(v, 1), series: [
        {name: 'Room', metric: 'room_temperature', color: 'series-1'},
        {name: 'Setpoint', metric: 'setpoint', color: 'series-2', width: 1.5},
    ]},
    {id: 'day-pressure', title: 'Water pressure · bar', height: 100, format: v => `${num(v, 2)} bar`, series: [
        {name: 'Pressure', metric: 'boiler_pressure', color: 'series-1'},
    ]},
    {id: 'day-power', title: 'Electricity · W', height: 120, format: fmtW, zero: true, series: [
        {name: 'From grid', metric: 'power_import', color: 'series-1'},
        {name: 'To grid', metric: 'power_export', color: 'series-3'},
    ]},
    {id: 'day-gas', title: 'Gas flow · m³/h', height: 100, format: v => `${num(v, 2)} m³/h`, zero: true, series: [
        {name: 'Gas flow', metric: 'gas_flow', color: 'series-4'},
    ]},
];

// The day charts with the configured external sensors added
function dayCharts() {
    const add = (extra, list) => extra.map((s, i) => ({name: s.name, metric: s.key, color: list[i % list.length]}));
    const charts = DAY_CHARTS.map(c => {
        if (c.id === 'day-temp') return {...c, series: [...c.series, ...add(externalTemps(), EXTERNAL_COLORS)]};
        if (c.id === 'day-room') return {...c, series: [...c.series, ...add(externalOutside(), ['series-3'])]};
        return c;
    });
    externalOthers().forEach(([u, group], i) => charts.push({
        id: `day-external-${i}`, title: `External sensors${u ? ' · ' + u : ''}`, height: 110,
        format: v => `${num(v, 1)}${u ? ' ' + u : ''}`, series: add(group, EXTERNAL_COLORS),
    }));
    return charts;
}

// The period charts of the external sensors, one per unit
function externalPeriodCharts() {
    const units = new Map();
    for (const s of externals().filter(s => s.updated)) {
        if (!units.has(s.unit || '')) units.set(s.unit || '', []);
        units.get(s.unit || '').push(s);
    }
    return [...units.entries()].map(([u, group], i) => ({
        id: `external-${i}`, title: 'External sensors, average', unit: u, kind: 'line',
        format: v => `${num(v, 1)}${u ? ' ' + u : ''}`, empty: 'No readings in this period',
        series: group.map((s, n) => ({name: s.name, gauge: s.key, part: 'avg', color: EXTERNAL_COLORS[n % EXTERNAL_COLORS.length]})),
    }));
}

// =====================================================================
// Drawing
// =====================================================================
function fieldOf(row, s) {
    if (s.derive) return s.derive(row);
    if (s.gauge) return row.gauges[s.gauge] ? row.gauges[s.gauge][s.part] : null;
    return row[s.field];
}

function panelsHtml(charts) {
    return charts.map(c => `<div class="panel ${c.wide ? 'wide' : ''}"><div class="panel-body">
        <div class="chart-title"><span>${escapeHtml(c.title)}</span><span class="hint">${escapeHtml(c.unit)}</span></div>
        <div id="stat-${c.id}" class="chart"></div>
        <div class="legend" id="stat-legend-${c.id}"></div>
    </div></div>`).join('');
}

function drawDefinedChart(c, periods, bucket) {
    // A line needs two moments; a single period gets an empty neighbour
    const rows = c.kind === 'line' && periods.length === 1 ? [...periods, {key: periods[0].key, gauges: {}}] : periods;
    const el = document.getElementById('stat-' + c.id);
    const series = c.series.filter(s => !s.optional || rows.some(r => !missing(fieldOf(r, s))));
    const legend = document.getElementById('stat-legend-' + c.id);
    legend.innerHTML = series.length > 1 ? legendHtml(series.map(s => ({name: s.name, color: color(s.color), kind: c.kind === 'line' ? 'line' : ''}))) : '';
    if (c.kind === 'bars') {
        barChart(el, {
            data: rows.map(r => ({label: bucketLabel(r.key, bucket), tipTitle: bucketTitle(r.key, bucket), values: series.map(s => fieldOf(r, s) || 0)})),
            series: series.map(s => ({name: s.name, color: color(s.color)})),
            grouped: c.grouped, format: c.format, height: c.wide ? 210 : 180, labelWidth: 52, empty: c.empty || 'No data in this period',
        });
        return;
    }
    const xs = rows.map((r, i) => i);
    const every = Math.max(1, Math.ceil(rows.length / 7));
    lineChart(el, {
        x: xs,
        series: series.map(s => ({name: s.name, color: color(s.color), values: rows.map(r => fieldOf(r, s)), width: s.width, dash: s.dash})),
        xTicks: rows.map((r, i) => i % every === 0 ? {at: i, label: bucketLabel(r.key, bucket)} : null).filter(Boolean),
        formatX: i => bucketTitle(rows[i].key, bucket), formatY: c.format, zero: c.zero, height: c.wide ? 210 : 180,
        hlines: (c.hlines || []).map(h => ({at: h.at, label: h.label, color: 'var(--secondary-text-color)'})),
        empty: c.empty || 'No data in this period',
    });
}

function renderStatsTiles(r) {
    const t = r.totals;
    const net = missing(t.import) && missing(t.export) ? null : (t.import || 0) - (t.export || 0);
    const change = !missing(t.gas) && t.gas_year_before ? Math.round((t.gas - t.gas_year_before) / t.gas_year_before * 100) : null;
    const c = r.cycles;
    const since = c.collecting_since ? `since ${fmtIsoDate(c.collecting_since, true)}` : '';
    // The tiles are totals of the period; "Per" adds what that comes to per day, week, month or year
    const periods = r.buckets.length;
    const per = (v, unitText) => missing(v) || periods < 2 ? '' : `≈ ${num(v / periods, Math.abs(v / periods) < 10 ? 1 : 0)} ${unitText} per ${r.bucket}`;
    const joined = (...parts) => parts.filter(Boolean).join(' · ');
    // The boiler's own hour counter, which only exists here since logging started. The Toon's history
    // of burner minutes is far too low to be real burner time, so it isn't used.
    const hoursSince = r.burner_hours_since ? `since ${fmtIsoDate(r.burner_hours_since, true)}` : '';
    const e = r.energy;
    document.getElementById('stats-tiles').innerHTML = [
        tile('Energy in gas', unit(e.gas_kwh, 0, 'kWh'), per(e.gas_kwh, 'kWh') || `at ${num(e.kwh_per_m3, 2)} kWh per m³`),
        tile('Heat, estimated', missing(e.heat_kwh) ? '–' : `≈ ${unit(e.heat_kwh, 0, 'kWh')}`, missing(e.efficiency) ? 'needs return temperatures' : joined(`≈ ${num(e.efficiency * 100)}%`, 'not measured')),
        tile('Gas', unit(t.gas, t.gas < 10 ? 2 : 0, 'm³'), joined(per(t.gas, 'm³'), change === null ? '' : `${change > 0 ? '+' : ''}${change}% on a year earlier`)),
        tile('From grid', unit(t.import, t.import < 10 ? 2 : 0, 'kWh'), per(t.import, 'kWh')),
        tile('To grid', unit(t.export, t.export < 10 ? 2 : 0, 'kWh'), per(t.export, 'kWh')),
        tile('Net electricity', unit(net, Math.abs(net) < 10 ? 2 : 0, 'kWh'), per(net, 'kWh') || 'from minus to grid'),
        tile('Burner hours', unit(t.burner_hours, 0, 'h'), joined(hoursSince, `hot water ${num(t.dhw_hours)} h`)),
        tile('Night base load', unit(r.base_load, 0, 'W'), 'recent week'),
        tile('Condensing', c.condensing_share === null ? '–' : unit(c.condensing_share, 0, '%'), joined('return under 55 °C', since)),
    ].join('');
}

function renderBoilerFacts(r) {
    const c = r.cycles;
    const p = r.pressure;
    const since = c.collecting_since ? `since ${fmtIsoDate(c.collecting_since, true)}` : 'collecting from this version on';
    document.getElementById('stats-boiler-facts').innerHTML = factsGrid([
        fact(`Burner starts · ${escapeHtml(since)}`, c.collecting_since ? num(c.starts) : '–'),
        fact('Average run', fmtMinutes(c.average_minutes)),
        fact('Short runs, under 10 min', c.collecting_since ? num(c.short) : '–'),
        fact('Longest run', fmtMinutes(c.longest_minutes)),
        fact('Burning', c.collecting_since ? unit(c.burning_hours, 1, 'h') : '–'),
        fact('Condensing share', c.condensing_share === null ? '–' : `${c.condensing_share}%`),
        fact('Return while heating', fmtTemp(c.return_average)),
        fact('Supply while heating', fmtTemp(c.supply_average)),
        fact('Setpoint while heating', fmtTemp(c.setpoint_average)),
        fact('Pressure, 30 days', missing(p.change_30d) ? '–' : `${p.change_30d > 0 ? '+' : ''}${num(p.change_30d, 2)}<small> bar</small>`),
        fact('Loss since last top-up', missing(p.loss_per_week) ? '–' : `${num(p.loss_per_week, 2)}<small> bar/week</small>`),
        fact('Last top-up seen', p.last_top_up ? escapeHtml(fmtIsoDate(p.last_top_up, true)) : '–'),
    ], 3);
}

function renderModulation(r) {
    const el = document.getElementById('chart-modulation');
    const rows = r.cycles.modulation || [];
    barChart(el, {
        data: rows.map(b => ({label: b.label, values: [b.minutes / 60]})),
        series: [{name: 'Time', color: color('flame')}], format: v => `${num(v, 1)} h`, height: 180, labelWidth: 50, empty: COLLECTING,
    });
}

function renderHeatmap(r) {
    const el = document.getElementById('heatmap');
    const rows = r.heatmap;
    const all = rows.flat().filter(v => !missing(v));
    if (!all.some(v => v > 0)) {
        el.innerHTML = '<div class="empty-state">No gas use per hour in this period</div>';
        document.getElementById('heatmap-scale').innerHTML = '';
        return;
    }
    const max = Math.max(...all);
    const dayNames = [...Array(7)].map((_, i) => new Date(2024, 0, 1 + i).toLocaleDateString([], {weekday: 'short'}));
    const shade = v => {
        if (missing(v)) return '';
        if (v <= 0) return 'background:var(--grid-color)';
        return `background:var(--seq-${Math.min(5, Math.floor(v / max * 5) + 1)})`;
    };
    let html = '<span></span>' + [...Array(24)].map((_, h) => `<span class="h">${h % 3 === 0 ? h : ''}</span>`).join('');
    rows.forEach((hours, d) => {
        html += `<span class="d">${escapeHtml(dayNames[d])}</span>`;
        hours.forEach((v, h) => {
            const title = `${dayNames[d]} ${String(h).padStart(2, '0')}:00: ${missing(v) ? 'no data' : num(v, 3) + ' m³ per hour on average'}`;
            html += `<div class="cell" style="${shade(v)}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"></div>`;
        });
    });
    el.innerHTML = html;
    document.getElementById('heatmap-scale').innerHTML = `<span>0</span>${[1, 2, 3, 4, 5].map(i => `<i style="background:var(--seq-${i})"></i>`).join('')}<span>${num(max, 2)} m³ per hour</span>`;
}

function renderGaugeTable(r) {
    const metrics = app.status.metrics;
    const rows = Object.entries(r.gauges);
    const fmt = (v, key) => missing(v) ? '–' : num(v, ['power_import', 'power_export'].includes(key) ? 0 : 2);
    document.getElementById('gauge-table').innerHTML = `<table class="data"><thead><tr><th>Value</th><th class="n">Lowest</th><th class="n">Average</th><th class="n">Highest</th><th>Unit</th></tr></thead><tbody>${rows.map(([key, g]) => `<tr>
        <td>${escapeHtml(metrics[key] ? metrics[key].name : key)}</td>
        <td class="n">${g ? fmt(g.min, key) : '–'}</td><td class="n">${g ? fmt(g.avg, key) : '–'}</td><td class="n">${g ? fmt(g.max, key) : '–'}</td>
        <td>${escapeHtml(metrics[key] && metrics[key].unit || '')}</td>
    </tr>`).join('')}</tbody></table>`;
}

function renderRecords(r) {
    document.getElementById('records').innerHTML = r.records.length ? r.records.map(rec => `<div class="record">
        <div class="l">${escapeHtml(rec.label)}</div>
        <div class="v">${num(rec.value, rec.value < 10 ? 2 : rec.value < 100 ? 1 : 0)} <small>${escapeHtml(rec.unit)}</small></div>
        <div class="d">${escapeHtml(fmtIsoDate(rec.day, true))}</div>
    </div>`).join('') : '<div class="empty-state">No records in this period</div>';
}

function renderExports() {
    const {from, to} = statsBounds();
    const query = `from=${from}&to=${to}`;
    document.getElementById('export-periods').href = `api/export/periods.csv?${query}&bucket=${stats.bucket}`;
    document.getElementById('export-samples').href = `api/export/values.csv?${query}`;
    document.getElementById('export-hourly').href = `api/export/values.csv?${query}&source=hourly`;
}

function renderFaults(r) {
    const faults = r.faults || [];
    const codes = list => list && list.length ? list.join(', ') : '–';
    document.getElementById('fault-log').innerHTML = faults.length ? `<table class="data"><thead><tr>
            <th>Started</th><th>Duration</th><th class="n">Code on the Toon</th><th class="n">Boiler's own code</th><th class="n">Lowest water pressure</th>
        </tr></thead><tbody>${faults.map(f => `<tr>
            <td>${escapeHtml(fmtDateTime(f.start))}</td>
            <td>${escapeHtml(fmtDuration(f.minutes * 60) + (f.end ? '' : ', ongoing'))}</td>
            <td class="n">${escapeHtml(codes(f.error_codes))}</td>
            <td class="n">${escapeHtml(codes(f.oem_codes))}</td>
            <td class="n">${missing(f.pressure_min) ? '–' : num(f.pressure_min, 2) + ' bar'}</td>
        </tr>`).join('')}</tbody></table>` : '<div class="empty-state">No boiler faults in this period</div>';
}

function renderReport(r) {
    renderStatsTiles(r);
    const rows = r.buckets;
    document.getElementById('stats-range-text').textContent = `${fmtIsoDate(r.first, true)} to ${fmtIsoDate(r.last, true)}`;
    document.getElementById('stats-period-note').textContent = `${fmtIsoDate(r.first, true)} to ${fmtIsoDate(r.last, true)}`;
    document.getElementById('stats-meter').innerHTML = panelsHtml(METER_CHARTS) + `<div class="panel wide"><div class="panel-body">
        <div class="chart-title"><span>When gas is used</span><span class="hint">average per weekday and hour · up to the last 12 months</span></div>
        <div id="heatmap" class="heatmap"></div><div id="heatmap-scale" class="scale"></div></div></div>`;
    const extraCharts = externalPeriodCharts();
    document.getElementById('stats-heating').innerHTML = panelsHtml([...HEATING_CHARTS, ...extraCharts]);
    document.getElementById('stats-boiler').innerHTML = panelsHtml(BOILER_CHARTS) + `<div class="panel"><div class="panel-body">
        <div class="chart-title"><span>Modulation while heating</span><span class="hint">hours at each level</span></div>
        <div id="chart-modulation" class="chart"></div></div></div>`;
    [...METER_CHARTS, ...HEATING_CHARTS, ...extraCharts, ...BOILER_CHARTS].forEach(c => drawDefinedChart(c, rows, r.bucket));
    renderHeatmap(r);
    renderModulation(r);
    renderBoilerFacts(r);
    renderFaults(r);
    renderRecords(r);
    renderGaugeTable(r);
    renderExports();
}

async function loadReport() {
    const ticket = ++stats.loading;
    const {from, to} = statsBounds();
    try {
        const report = await api(`stats?from=${from}&to=${to}&bucket=${stats.bucket}`);
        if (ticket !== stats.loading) return;
        stats.report = report;
        renderReport(report);
    } catch (e) {
        toast('Cannot load the statistics: ' + e.message);
    }
}

// Flow in m³/h between meter readings, skipping gaps and counter jumps
function flowFromMeter(points) {
    const flow = [];
    (points || []).forEach((point, i) => {
        if (!i) return;
        const [before, was] = points[i - 1];
        const hours = (point[0] - before) / 3600;
        const used = point[1] - was;
        if (hours > 0 && hours <= 0.25 && used >= 0) flow.push([point[0], Math.round(used / hours * 1000) / 1000]);
    });
    return flow;
}

async function loadDay() {
    document.getElementById('day-nav').innerHTML = dateNavHtml('dayStep', stats.day);
    document.getElementById('day-title').textContent = 'Timeline';
    const start = Math.floor(new Date(stats.day + 'T00:00:00').getTime() / 1000);
    const end = Math.floor(new Date(shiftDate(stats.day, 1) + 'T00:00:00').getTime() / 1000);
    const step = 120;
    const charts = dayCharts();
    const metrics = [...charts.flatMap(c => c.series.map(s => s.metric)), 'gas_delivered'];
    const [data, gasHours] = await Promise.all([
        api(`history?start=${start}&end=${end}&metrics=${[...metrics, 'burner_code'].join(',')}`),
        api(`gas-hours?day=${stats.day}`),
    ]);
    // The Toon keeps no history of the gas flow. Days without live readings get it from the meter's
    // 5-minute history, which only goes back about a week before the app was installed.
    if (!(data.gas_flow || []).length) data.gas_flow = flowFromMeter(data.gas_delivered);
    const shown = charts.filter(c => c.id !== 'day-gas' || (data.gas_flow || []).length);
    const gasTotal = gasHours.reduce((total, h) => total + h.gas, 0);
    const xs = gridTimes(start, end, step);
    const bands = burnerBands(data.burner_code, start, end, step);
    const ticks = hourTicks(start, end, 3);
    const host = document.getElementById('day-charts');
    host.innerHTML = `<div class="chart-label">Gas per hour · m³${gasHours.length ? ` · ${num(gasTotal, 2)} m³ this day` : ''}</div><div id="day-gas-hours" class="chart"></div>` + shown.map(c => `<div class="chart-label">${escapeHtml(c.title)}</div><div id="${c.id}" class="chart"></div><div class="legend" id="${c.id}-legend" style="margin-bottom:12px"></div>`).join('');
    barChart(document.getElementById('day-gas-hours'), {
        data: gasHours.map(h => ({
            label: `${String(new Date(h.ts * 1000).getHours()).padStart(2, '0')}:00`,
            tipTitle: fmtDateTime(h.ts),
            values: [h.gas],
        })),
        series: [{name: 'Gas', color: color('series-4')}],
        format: fmtGas,
        height: 110,
        labelWidth: 48,
        empty: gasHours.length ? 'No gas used on this day' : 'No hourly gas for this day: the Toon keeps about five years',
    });
    shown.forEach(c => {
        const series = c.series.map(s => ({name: s.name, color: color(s.color), width: s.width, area: s.area, values: grid(data[s.metric], start, end, step, 1800)}));
        lineChart(document.getElementById(c.id), {
            x: xs, series, xTicks: ticks, zero: c.zero, height: c.height, formatX: fmtDateTime, formatY: c.format,
            bands: c.bands ? bands : [], empty: 'No values on this day',
        });
        const items = series.map(s => ({name: s.name, color: s.color, kind: 'line'}));
        if (c.bands && bands.length) items.push({name: 'burner: heating', color: color('flame'), kind: 'band'}, {name: 'burner: hot water', color: color('dhw'), kind: 'band'});
        if (items.length > 1) document.getElementById(`${c.id}-legend`).innerHTML = legendHtml(items);
    });
    const peak = values => {
        const known = (values || []).map(p => p[1]);
        return known.length ? {min: Math.min(...known), max: Math.max(...known)} : null;
    };
    const supply = peak(data.boiler_temperature);
    const ret = peak(data.boiler_return_temperature);
    const room = peak(data.room_temperature);
    const pressure = peak(data.boiler_pressure);
    const burning = bands.reduce((total, b) => total + (b.to - b.from), 0);
    document.getElementById('day-facts').innerHTML = factsGrid([
        fact('Burner on', bands.length ? escapeHtml(fmtDuration(burning)) : '–'),
        fact('Burner periods', bands.length ? num(bands.length) : '–'),
        fact('Highest supply', supply ? fmtTemp(supply.max) : '–'),
        fact('Highest return', ret ? fmtTemp(ret.max) : '–'),
        fact('Room', room ? `${num(room.min, 1)} to ${num(room.max, 1)}<small> °C</small>` : '–'),
        fact('Pressure', pressure ? `${num(pressure.min, 2)} to ${num(pressure.max, 2)}<small> bar</small>` : '–'),
    ], 3);
}

function dayStep(days) {
    stats.day = days === 0 ? todayIso() : shiftDate(stats.day, days);
    if (stats.day > todayIso()) stats.day = todayIso();
    loadDay().catch(e => toast('Cannot load this day: ' + e.message));
}

function buildYearSelect() {
    const el = document.getElementById('stats-year');
    const now = new Date().getFullYear();
    const oldest = app.status && app.status.database.oldest;
    const first = oldest ? new Date(oldest * 1000).getFullYear() : now;
    let html = '<option value="">Year…</option>';
    for (let y = now; y >= first; y--) html += `<option value="${y}"${String(y) === stats.year ? ' selected' : ''}>${y}</option>`;
    el.innerHTML = html;
    el.onchange = () => {
        stats.year = el.value;
        if (stats.year) {
            stats.bucket = 'month';
            document.querySelectorAll('#stats-range .tab').forEach(t => t.classList.remove('active'));
            buildGroups();
        }
        loadReport();
    };
}

function buildGroups() {
    buildChips('stats-bucket', GROUPS, stats.bucket, key => { stats.bucket = key; loadReport(); });
}

function buildRanges() {
    buildChips('stats-range', RANGES, stats.range, key => {
        stats.range = key;
        stats.year = '';
        document.getElementById('stats-year').value = '';
        stats.bucket = DEFAULT_GROUP[key];
        buildGroups();
        loadReport();
    });
}

function showStats() {
    buildRanges();
    buildYearSelect();
    buildGroups();
    loadReport();
    loadDay().catch(e => toast('Cannot load this day: ' + e.message));
}

onTab('stats', showStats, () => {});
