// Tesla dashboard - Stats view: totals, per-period charts, battery health and habits.

let statsRange = localStorage.getItem(LS + 'stats_range') || '12m';
let statsBucket = localStorage.getItem(LS + 'stats_bucket') || 'auto';
let statsLoadedFor = null;      // car:range of the insights and map on screen
let periodsLoadedFor = null;    // car:range:bucket of the per-period charts
let visitedMap = null;
let visitedHeat = null;
let visitedRetry = null;

const BUCKETS = [
    {key: 'auto', label: 'Auto'},
    {key: 'day', label: 'Day'},
    {key: 'week', label: 'Week'},
    {key: 'month', label: 'Month'},
    {key: 'year', label: 'Year'},
];
const STATE_ORDER = ['driving', 'charging', 'online', 'updating', 'asleep', 'offline'];
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// A sensible bar size for the chosen period: about 12 to 31 bars
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

function tile(label, valueHtml, detail) {
    return `<div class="tile"><div class="l">${label}</div><div class="v">${valueHtml}</div><div class="d">${detail || '&nbsp;'}</div></div>`;
}

function renderStatTiles(s) {
    const cost = missing(s.cost) ? null : s.cost;
    document.getElementById('stats-tiles').innerHTML = [
        tile('Distance', withUnit(num(conv.dist(s.distance_km)), unit.dist()), `${num(s.drives)} trips · longest ${fmtDist(s.longest_km, 0)}`),
        tile('Driving time', fmtDuration(s.duration_s), s.duration_s ? `average ${fmtSpeed(s.distance_km / s.duration_s * 3600)}` : ''),
        tile('Energy used', withUnit(num(s.energy_kwh), 'kWh'), 'while driving'),
        tile('Consumption', missing(s.consumption_wh_km) ? '–' : withUnit(num(conv.cons(s.consumption_wh_km)), unit.cons()),
            missing(s.consumption_wh_km) ? '' : `${num(conv.cons(s.consumption_wh_km) / 10, 1)} kWh per 100 ${unit.dist()}`),
        tile('Charged', withUnit(num(s.added_kwh), 'kWh'), `${num(s.charges)} sessions · ${s.added_kwh ? num(s.dc_kwh / s.added_kwh * 100) : 0}% DC`),
        tile('Charging efficiency', missing(s.charge_efficiency) ? '–' : fmtPct(s.charge_efficiency * 100),
            s.used_kwh ? `${fmtKwh(s.used_kwh, 0)} from the charger` : ''),
        tile('Charging cost', cost === null ? '–' : fmtMoney(cost),
            missing(s.cost_per_100km) ? (cost === null ? 'set a price per kWh in the configuration' : '')
                : `${fmtMoney(imperial() ? s.cost_per_100km * KM_PER_MILE : s.cost_per_100km)} per 100 ${unit.dist()}`),
        tile('Top speed', missing(s.speed_max_kmh) ? '–' : withUnit(num(conv.dist(s.speed_max_kmh)), unit.speed()),
            missing(s.charge_power_max_kw) ? '' : `peak charge ${num(s.charge_power_max_kw)} kW`),
    ].join('');
}

function renderPeriodCharts(periods, bucket) {
    const labelled = periods.map((p, i) => ({...p, label: periodLabel(p.period, bucket, i), title: periodTitle(p.period, bucket)}));
    const labelWidth = bucket === 'month' ? 52 : bucket === 'year' ? 40 : 46;
    document.getElementById('chart-distance-title').textContent = `Distance per ${bucket}`;
    document.getElementById('chart-distance-hint').textContent = unit.dist();
    barChart(document.getElementById('chart-distance'), {
        data: labelled.map(p => ({label: p.label, tipTitle: `${p.title} · ${p.drives} trips`, values: [conv.dist(p.distance_km)]})),
        series: [{name: 'Distance', color: 'var(--series-1)'}],
        format: v => `${num(v, v < 100 ? 1 : 0)} ${unit.dist()}`, height: 210, labelWidth,
    });
    barChart(document.getElementById('chart-energy'), {
        data: labelled.map(p => ({label: p.label, tipTitle: p.title, values: [Math.max(0, p.energy_kwh)]})),
        series: [{name: 'Energy used', color: 'var(--series-1)'}],
        format: v => fmtKwh(v), labelWidth,
    });
    barChart(document.getElementById('chart-charged'), {
        data: labelled.map(p => ({label: p.label, tipTitle: `${p.title} · ${p.charges} sessions`, values: [p.ac_kwh, p.dc_kwh]})),
        series: [{name: 'AC', color: 'var(--series-1)'}, {name: 'DC', color: 'var(--series-2)'}],
        format: v => fmtKwh(v), labelWidth,
    });
    document.getElementById('chart-consumption-hint').textContent = `${unit.cons()} per ${bucket}`;
    const every = Math.max(1, Math.ceil(labelled.length / 8));
    lineChart(document.getElementById('chart-consumption'), {
        x: labelled.map((p, i) => i),
        series: [{name: 'Consumption', color: 'var(--series-1)', values: labelled.map(p => missing(p.consumption_wh_km) ? null : conv.cons(p.consumption_wh_km))}],
        height: 190,
        xTicks: labelled.map((p, i) => ({at: i, label: p.label})).filter((t, i) => i % every === 0),
        formatX: i => labelled[i].title,
        formatY: v => `${num(v)} ${unit.cons()}`,
        empty: 'Needs trips in at least two periods',
    });
}

function renderBattery(battery) {
    const facts = document.getElementById('battery-facts');
    const chart = document.getElementById('chart-battery');
    const byCapacity = battery.basis === 'capacity';
    document.getElementById('battery-hint').textContent = byCapacity
        ? 'usable capacity measured by each charge in this period: kWh added ÷ battery % gained · it drops in cold weather and with BMS drift; not temperature-corrected'
        : "the car's own rated range at 100%, after each charge in this period · it drops in cold weather and with BMS drift; not temperature-corrected";
    if (missing(battery.loss_pct)) {
        facts.hidden = true;
        emptyChart(chart, missing(battery.basis) ? 'Shows up after a few charging sessions' : 'Needs at least three charging sessions in this period');
        return;
    }
    facts.hidden = false;
    const newNote = battery.new_source === 'option' ? 'as set in the app options' : 'best seen, all time';
    facts.innerHTML = (byCapacity ? [
        tile('Usable capacity', withUnit(num(battery.capacity_kwh, 1), 'kWh'), 'median of the latest charges in this period'),
        tile('When new', withUnit(num(battery.new_kwh, 1), 'kWh'), newNote),
        tile('Degradation', fmtPct(Math.round(battery.loss_pct * 10) / 10), `${num(100 - battery.loss_pct, 1)}% of the capacity left, this period vs. new`),
        missing(battery.now_km) ? '' : tile('Full range now', withUnit(num(conv.dist(battery.now_km)), unit.dist()), 'rated range at 100%, latest charges'),
    ] : [
        tile('Full range now', withUnit(num(conv.dist(battery.now_km)), unit.dist()), 'median of the latest charges in this period'),
        battery.new_source === 'option'
            ? tile('When new', withUnit(num(battery.new_kwh, 1), 'kWh'), newNote)
            : tile('When new', withUnit(num(conv.dist(battery.new_km)), unit.dist()), newNote),
        tile('Degradation', fmtPct(Math.round(battery.loss_pct * 10) / 10), `${num(100 - battery.loss_pct, 1)}% left, this period vs. new`),
        tile('Usable capacity', withUnit(num(battery.capacity_kwh, 1), 'kWh'), 'estimated from the rated range'),
    ]).join('');
    const points = battery.points.filter(p => !missing(byCapacity ? p.capacity_kwh : p.full_range_km));
    lineChart(chart, {
        x: points.map(p => p.ts),
        series: [byCapacity
            ? {name: 'Usable capacity', color: 'var(--series-1)', values: points.map(p => p.capacity_kwh)}
            : {name: 'Range at 100%', color: 'var(--series-1)', values: points.map(p => conv.dist(p.full_range_km))}],
        height: 200,
        xTicks: timeTicks(points.map(p => p.ts), 5, t => new Date(t * 1000).toLocaleDateString([], {month: 'short', year: '2-digit'})),
        formatX: t => fmtDate(t),
        formatY: v => byCapacity ? `${num(v, 1)} kWh` : `${num(v)} ${unit.dist()}`,
    });
}

function renderStates(states, standby) {
    const total = states.reduce((sum, s) => sum + s.seconds, 0);
    const share = document.getElementById('states-share');
    const legend = document.getElementById('states-legend');
    if (!total) {
        share.innerHTML = '<div class="empty-state" style="padding:20px">No data in this period</div>';
        legend.innerHTML = '';
    } else {
        const ordered = STATE_ORDER.map(name => states.find(s => s.state === name)).filter(Boolean)
            .concat(states.filter(s => !STATE_ORDER.includes(s.state)));
        share.innerHTML = '<div class="share" role="img" aria-label="Share of time per state">' + ordered.map(s =>
            `<div style="flex:${s.seconds};background:var(--state-${STATE_ORDER.includes(s.state) ? s.state : 'offline'})" title="${escapeHtml(s.state)}: ${fmtDuration(s.seconds)}"></div>`).join('') + '</div>';
        legend.innerHTML = ordered.map(s =>
            `<span><i style="background:var(--state-${STATE_ORDER.includes(s.state) ? s.state : 'offline'})"></i>${escapeHtml(s.state[0].toUpperCase() + s.state.slice(1))} <b style="color:var(--primary-text-color);font-weight:500">${num(s.seconds / total * 100, s.seconds / total < 0.1 ? 1 : 0)}%</b> · ${fmtDuration(s.seconds)}</span>`).join('');
        document.getElementById('states-hint').textContent = `${fmtDuration(total)} recorded`;
    }
    const el = document.getElementById('standby');
    if (missing(standby.km_per_day)) {
        el.innerHTML = '<div class="info-row"><span class="info-label">Standby loss</span><span class="info-value">needs a stop of 4 hours or more</span></div>';
    } else {
        el.innerHTML = `
            <div class="info-row"><span class="info-label">Standby loss while parked</span><span class="info-value">${fmtDist(standby.km_per_day, 1)} per day</span></div>
            <div class="info-row"><span class="info-label">That is</span><span class="info-value">${num(standby.kwh_per_day, 2)} kWh per day · ${num(standby.watts)} W</span></div>
            <div class="info-row"><span class="info-label">Measured over</span><span class="info-value">${fmtDuration(standby.parked_hours * 3600)} parked</span></div>`;
    }
}

function renderRhythm(rhythm) {
    const el = document.getElementById('rhythm');
    if (!rhythm.length) {
        el.innerHTML = '<div class="empty-state" style="padding:20px">No trips in this period</div>';
        return;
    }
    const grid = {};
    let max = 0;
    rhythm.forEach(cell => { grid[`${cell.weekday}:${cell.hour}`] = cell; max = Math.max(max, cell.drives); });
    let html = '<div class="heat"><span></span>';
    for (let hour = 0; hour < 24; hour++) html += `<span class="hr">${hour % 3 === 0 ? hour : ''}</span>`;
    for (let day = 0; day < 7; day++) {
        html += `<span class="lbl">${WEEKDAYS[day]}</span>`;
        for (let hour = 0; hour < 24; hour++) {
            const cell = grid[`${day}:${hour}`];
            // Five steps of one hue: more trips, stronger colour
            const step = cell ? Math.max(1, Math.ceil(cell.drives / max * 5)) : 0;
            const style = step ? ` style="background:var(--seq-${step})"` : '';
            html += `<span class="cell" tabindex="0"${style} data-day="${day}" data-hour="${hour}" data-drives="${cell ? cell.drives : 0}" data-km="${cell ? cell.distance_km : 0}"></span>`;
        }
    }
    el.innerHTML = html + '</div><div class="legend"><span>fewer</span>'
        + [1, 2, 3, 4, 5].map(i => `<i style="background:var(--seq-${i});margin-right:2px"></i>`).join('') + '<span>more trips</span></div>';
    const tip = (e) => {
        const cell = e.target.closest('.cell');
        if (!cell) return hideTip();
        const box = cell.getBoundingClientRect();
        const hour = Number(cell.dataset.hour);
        showTip(e.clientX || box.right, e.clientY || box.top, `${WEEKDAYS[cell.dataset.day]} ${String(hour).padStart(2, '0')}:00 – ${String((hour + 1) % 24).padStart(2, '0')}:00`,
            [{value: cell.dataset.drives, name: 'trips'}, {value: fmtDist(Number(cell.dataset.km), 0), name: 'driven'}]);
    };
    const heat = el.querySelector('.heat');
    heat.addEventListener('pointermove', tip);
    heat.addEventListener('focusin', tip);
    heat.addEventListener('pointerleave', hideTip);
    heat.addEventListener('focusout', hideTip);
}

function renderBarsList(id, rows, name, value, text) {
    const el = document.getElementById(id);
    if (!rows.length) {
        el.innerHTML = '<div class="empty-state" style="padding:20px">No data in this period</div>';
        return;
    }
    const max = Math.max(...rows.map(value)) || 1;
    el.innerHTML = rows.map(row => `
        <div class="bar-row">
            <span class="name" title="${escapeHtml(name(row))}">${escapeHtml(name(row))}</span>
            <span class="track"><i style="width:${Math.max(2, value(row) / max * 100)}%"></i></span>
            <span class="count">${text(row)}</span>
        </div>`).join('');
}

function renderRecords(records) {
    const item = (label, record, valueHtml, action) => record ? `
        <button type="button" class="record" onclick="${action}(${record.id})">
            <div class="l">${label}</div><div class="v">${valueHtml}</div>
            <div class="d">${fmtDate(record.start_ts)}${record.place ? ' · ' + escapeHtml(record.place) : ''}</div>
        </button>` : '';
    const r = records;
    const html = [
        item('Longest trip', r.longest_drive, r.longest_drive && fmtDist(r.longest_drive.distance_km), 'openTrip'),
        item('Top speed', r.fastest_drive, r.fastest_drive && fmtSpeed(r.fastest_drive.speed_max_kmh), 'openTrip'),
        item('Most frugal trip', r.frugal_drive, r.frugal_drive && fmtCons(r.frugal_drive.consumption_wh_km), 'openTrip'),
        item('Biggest charge', r.biggest_charge, r.biggest_charge && fmtKwh(r.biggest_charge.energy_added_kwh), 'openCharge'),
        item('Fastest charge', r.strongest_charge, r.strongest_charge && `${num(r.strongest_charge.power_max_kw)} kW`, 'openCharge'),
    ].join('');
    document.getElementById('records').innerHTML = html || '<div class="empty-state" style="padding:20px;background:var(--ha-card-background)">No data in this period</div>';
}

function renderUpdates(updates) {
    document.getElementById('updates').innerHTML = updates.length
        ? updates.map(u => `<div class="info-row"><span class="info-label">${fmtDate(u.ts)}</span><span class="info-value">${escapeHtml(u.version)}</span></div>`).join('')
        : '<div class="empty-state" style="padding:20px">No software version first seen in this period</div>';
}

async function renderVisited(carId, bounds) {
    let points = [];
    clearTimeout(visitedRetry);
    const hint = document.getElementById('visited-hint');
    try {
        const data = await api(`stats/visited?car=${carId}&from=${bounds.from}&to=${bounds.to}`);
        points = data.points;
        // Right after the update (or an import) the trips are still being mapped: fill in as they come
        hint.textContent = data.pending ? `mapping ${num(data.pending)} trips…` : 'every road driven in the period';
        if (data.pending) visitedRetry = setTimeout(() => renderVisited(carId, bounds), 5000);
    } catch (e) { /* the map stays empty */ }
    if (!visitedMap) {
        visitedMap = L.map('visited-map', {scrollWheelZoom: false}).setView([52.2, 5.3], 7);
        tileLayer().addTo(visitedMap);
    }
    if (visitedHeat) visitedMap.removeLayer(visitedHeat);
    visitedMap.invalidateSize();
    if (!points.length) return;
    const max = Math.max(...points.map(p => p[2]));
    visitedHeat = L.heatLayer(points.map(p => [p[0], p[1], Math.min(1, 0.35 + p[2] / max)]), {
        radius: 7, blur: 6, minOpacity: 0.5, maxZoom: 12,
        // One hue, light to dark with density
        gradient: {0.3: '#86b6ef', 0.6: '#3987e5', 1.0: '#184f95'},
    }).addTo(visitedMap);
    visitedMap.fitBounds(L.latLngBounds(points.map(p => [p[0], p[1]])), {padding: [20, 20], maxZoom: 13});
}

async function loadPeriods(car, bounds, bucket) {
    const query = `car=${car.id}&from=${bounds.from}&to=${bounds.to}&bucket=${bucket}`;
    const [periods, tires] = await Promise.all([api(`stats/periods?${query}`), api(`stats/tires?${query}`)]);
    periodsLoadedFor = `${car.id}:${statsRange}:${bucket}`;
    renderPeriodCharts(periods, bucket);
    renderTires(tires, bucket);
}

function renderTires(rows, bucket) {
    const every = Math.max(1, Math.ceil(rows.length / 8));
    document.getElementById('chart-tires-hint').textContent = `${unit.press()} · average per ${bucket}`;
    const wheels = [['fl', 'Front left', 'var(--series-1)'], ['fr', 'Front right', 'var(--series-2)'], ['rl', 'Rear left', 'var(--series-3)'], ['rr', 'Rear right', 'var(--series-4)']];
    lineChart(document.getElementById('chart-tires'), {
        x: rows.map((r, i) => i),
        series: wheels.map(([key, name, color]) => ({name, color, values: rows.map(r => missing(r[key]) ? null : conv.press(r[key]))})),
        height: 190,
        xTicks: rows.map((r, i) => ({at: i, label: periodLabel(r.period, bucket, i)})).filter((t, i) => i % every === 0),
        formatX: i => periodTitle(rows[i].period, bucket),
        formatY: v => `${num(v, imperial() ? 0 : 2)} ${unit.press()}`,
        empty: 'Needs tire pressures in at least two periods. Run the import again to add them from TeslaMate.',
    });
}

async function loadStats(force) {
    const car = currentCar();
    const bucket = effectiveBucket();
    const key = `${car ? car.id : 0}:${statsRange}`;
    if (!force && key === statsLoadedFor) { redrawCharts(); if (visitedMap) visitedMap.invalidateSize(); return; }
    const bounds = rangeBounds(statsRange);
    if (car && key === statsLoadedFor) {
        // Same car and period: at most the bar size changed, and only the per-period charts depend on that
        if (periodsLoadedFor === `${key}:${bucket}`) return;
        const grid = document.getElementById('stats-grid');
        grid.classList.add('loading');
        try { await loadPeriods(car, bounds, bucket); } catch (e) { toast(e.message, 'error'); }
        grid.classList.remove('loading');
        return;
    }
    document.getElementById('stats-range-text').textContent =
        (bounds.from ? fmtDate(bounds.from) : 'Start') + ' – ' + fmtDate(bounds.to);
    if (!car) {
        document.getElementById('stats-tiles').innerHTML = '<div class="empty-state" style="background:var(--ha-card-background)">No car yet - sign in to your Tesla account first</div>';
        return;
    }
    const grid = document.getElementById('stats-grid');
    // Keep the previous charts in place, dimmed, while the new numbers load
    grid.classList.add('loading');
    try {
        const query = `car=${car.id}&from=${bounds.from}&to=${bounds.to}`;
        const [insights] = await Promise.all([api(`stats/insights?${query}`), loadPeriods(car, bounds, bucket)]);
        statsLoadedFor = key;
        renderStatTiles(insights.summary);
        document.getElementById('chart-temp-hint').textContent = `${unit.cons()} · trips of 5 km and longer`;
        barChart(document.getElementById('chart-temp'), {
            data: insights.temperature.map(t => ({
                label: `${num(conv.temp(t.temp))}°`,
                tipTitle: `${num(conv.temp(t.temp))} to ${num(conv.temp(t.temp + 5))} ${unit.temp()} · ${t.drives} trips`,
                values: [conv.cons(t.consumption_wh_km)],
            })),
            series: [{name: 'Consumption', color: 'var(--series-1)'}],
            format: v => `${num(v)} ${unit.cons()}`, labelWidth: 34,
            empty: 'Needs trips of 5 km or longer',
        });
        renderBattery(insights.battery);
        renderStates(insights.states, insights.standby);
        renderRhythm(insights.rhythm);
        renderBarsList('destinations', insights.destinations, d => placeName(d.place, d.lat, d.lon), d => d.visits, d => `<b>${num(d.visits)}</b> trips`);
        renderBarsList('charge-places', insights.charge_places, c => placeName(c.place, c.lat, c.lon), c => c.added_kwh,
            c => `<b>${num(c.added_kwh)}</b> kWh · ${num(c.charges)}×`);
        renderRecords(insights.records);
        renderUpdates(insights.updates);
        renderVisited(car.id, bounds);
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

onTab('stats', (force) => {
    buildChips('stats-range', RANGES, statsRange, setStatsRange);
    buildYearSelect('stats-year', statsRange, setStatsRange);
    buildChips('stats-bucket', BUCKETS, statsBucket, setStatsBucket);
    loadStats(force);
});
