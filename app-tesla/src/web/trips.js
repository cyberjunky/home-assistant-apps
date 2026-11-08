// Tesla dashboard - Trips view: every drive, selectable, drawn on the map with its details.

let tripsRange = localStorage.getItem(LS + 'trips_range') || '30d';
let trips = [];
let tripsLoadedFor = null;
let selectedTrip = null;
let pendingTrip = null;
let tripMetric = localStorage.getItem(LS + 'trip_metric') || 'speed';
let tripData = null;
let tripCursor = null;

const TRIP_METRICS = {
    speed: {label: 'Speed', column: 'speed_kmh', color: 'var(--series-1)', value: v => conv.dist(v), format: v => `${num(v)} ${unit.speed()}`, zero: true},
    power: {label: 'Power', column: 'power_kw', color: 'var(--series-2)', value: v => v, format: v => `${num(v)} kW`, zero: true},
    elevation: {label: 'Elevation', column: 'elevation_m', color: 'var(--series-3)', value: v => conv.elev(v), format: v => `${num(v)} ${unit.elev()}`},
    battery: {label: 'Battery', column: 'soc', color: 'var(--series-4)', value: v => v, format: v => `${num(v)}%`},
};

function consumption(drive) {
    return !missing(drive.energy_kwh) && drive.distance_km > 0 ? drive.energy_kwh / drive.distance_km * 1000 : null;
}

function tripRoute(drive) {
    return `${escapeHtml(placeName(drive.start_place, drive.start_lat, drive.start_lon))}<span class="arrow">→</span>${escapeHtml(placeName(drive.end_place, drive.end_lat, drive.end_lon))}`;
}

function renderTrips(totals) {
    document.getElementById('trips-cons-head').textContent = unit.cons();
    const el = document.getElementById('trips-rows');
    if (!trips.length) {
        el.innerHTML = `<div class="empty-state">${currentCar() ? 'No trips in this period' : 'No trips recorded yet'}</div>`;
        document.getElementById('trips-status').textContent = '';
        return;
    }
    // One header per day with that day's distance
    const days = new Map();
    trips.forEach(drive => {
        const key = new Date(drive.start_ts * 1000).toDateString();
        if (!days.has(key)) days.set(key, {ts: drive.start_ts, km: 0, items: []});
        const day = days.get(key);
        day.km += drive.distance_km || 0;
        day.items.push(drive);
    });
    let html = '';
    days.forEach(day => {
        html += `<div class="feed-day"><span>${fmtDayY(day.ts)}</span><span>${day.items.length > 1 ? day.items.length + ' trips · ' : ''}${fmtDist(day.km)}</span></div>`;
        html += day.items.map(drive => `
            <button type="button" class="feed-row${drive.id === selectedTrip ? ' selected' : ''}" data-id="${drive.id}" onclick="selectTrip(${drive.id})">
                <span class="time">${fmtTime(drive.start_ts)}</span>
                <span class="route">${tripRoute(drive)}</span>
                <span class="num">${num(conv.dist(drive.distance_km), 1)} ${unit.dist()}</span>
                <span class="num col-dur">${fmtDuration(drive.duration_s)}</span>
                <span class="num col-kwh">${num(drive.energy_kwh, 1)}</span>
                <span class="num col-cons">${missing(consumption(drive)) ? '–' : num(conv.cons(consumption(drive)))}</span>
            </button>`).join('');
    });
    el.innerHTML = html;
    // The totals cover the whole period, also when the list itself is cut off
    document.getElementById('trips-status').textContent =
        `${num(totals.drives)} trips · ${fmtDist(totals.distance_km, 0)} · ${fmtKwh(totals.energy_kwh, 0)} · ${fmtCons(totals.consumption_wh_km)}`
        + (totals.drives > trips.length ? ` · latest ${num(trips.length)} listed` : '');
}

async function loadTrips(force) {
    const car = currentCar();
    if (!car) { trips = []; renderTrips({}); return; }
    const key = `${car.id}:${tripsRange}`;
    if (!force && key === tripsLoadedFor && !pendingTrip) return;
    const bounds = rangeBounds(tripsRange);
    let data;
    try {
        data = await api(`drives?car=${car.id}&from=${bounds.from}&to=${bounds.to}&limit=2000`);
    } catch (e) {
        toast(e.message, 'error');
        return;
    }
    tripsLoadedFor = key;
    trips = data.items;
    renderTrips(data.totals);

    if (pendingTrip) {
        const id = pendingTrip;
        // A trip opened from elsewhere may lie outside the chosen period
        if (!trips.some(t => t.id === id) && tripsRange !== 'all') {
            setTripsRange('all');
            return;
        }
        pendingTrip = null;
        selectTrip(id);
    } else if (!trips.some(t => t.id === selectedTrip)) {
        if (trips.length) selectTrip(trips[0].id); else clearTrip();
    }
}

function setTripsRange(key) {
    tripsRange = key;
    localStorage.setItem(LS + 'trips_range', key);
    buildChips('trips-range', RANGES, tripsRange, setTripsRange);
    buildYearSelect('trips-year', tripsRange, setTripsRange);
    loadTrips(true);
}

function clearTrip() {
    selectedTrip = null;
    tripData = null;
    mapLayers.trips.clearLayers();
    showSpeedLegend(false);
    document.getElementById('trip-detail').innerHTML = '<div class="empty-state">Select a trip to see it on the map</div>';
}

async function selectTrip(id) {
    selectedTrip = id;
    document.querySelectorAll('#trips-rows .feed-row').forEach(row => row.classList.toggle('selected', Number(row.dataset.id) === id));
    const row = document.querySelector(`#trips-rows .feed-row[data-id="${id}"]`);
    if (row) row.scrollIntoView({block: 'nearest'});
    let data;
    try {
        data = await api(`drives/${id}`);
    } catch (e) {
        toast(e.message, 'error');
        return;
    }
    if (selectedTrip !== id) return;  // another trip was clicked meanwhile
    tripData = data;
    if (window.location.hash !== `#trips/${id}`) history.replaceState(null, '', `#trips/${id}`);
    drawTrip();
    renderTripDetail();
}

function tripColumn(name) {
    const index = tripData.columns.indexOf(name);
    return tripData.points.map(p => p[index]);
}

function drawTrip() {
    const group = mapLayers.trips;
    group.clearLayers();
    tripCursor = null;
    const lat = tripData.columns.indexOf('lat'), lon = tripData.columns.indexOf('lon'), speed = tripData.columns.indexOf('speed_kmh');
    const points = tripData.points.map(p => [p[lat], p[lon], p[speed]]);
    if (points.length < 2) return;
    const bounds = drawSpeedTrack(group, points);
    L.marker(points[0], {icon: pinIcon('A'), interactive: false}).addTo(group);
    L.marker(points[points.length - 1], {icon: pinIcon('B', 'end'), interactive: false, zIndexOffset: 500}).addTo(group);
    if (activeTab === 'trips') {
        map.fitBounds(bounds, {padding: [30, 30], maxZoom: 16});
        showSpeedLegend(true);
    }
}

function renderTripChart() {
    const metric = TRIP_METRICS[tripMetric] || TRIP_METRICS.speed;
    const ts = tripColumn('ts');
    const values = tripColumn(metric.column).map(v => missing(v) ? null : metric.value(v));
    const lat = tripColumn('lat'), lon = tripColumn('lon');
    lineChart(document.getElementById('trip-chart'), {
        x: ts,
        series: [{name: metric.label, color: metric.color, values}],
        area: true,
        zero: metric.zero,
        height: 170,
        xTicks: timeTicks(ts, 4, fmtTime),
        formatX: t => new Date(t * 1000).toLocaleTimeString(),
        formatY: metric.format,
        empty: tripMetric === 'elevation' ? 'No elevation for this trip (it comes with the live stream)' : 'No data',
        // Show on the map where the pointer is in the chart
        onHover: (i) => {
            if (i === null) {
                if (tripCursor) { mapLayers.trips.removeLayer(tripCursor); tripCursor = null; }
                return;
            }
            if (!tripCursor) tripCursor = L.circleMarker([lat[i], lon[i]], {radius: 7, weight: 2, fillOpacity: 1, className: 'trk-cursor', interactive: false}).addTo(mapLayers.trips);
            else tripCursor.setLatLng([lat[i], lon[i]]);
        },
    });
}

function setTripMetric(key) {
    tripMetric = key;
    localStorage.setItem(LS + 'trip_metric', key);
    renderTripChart();
}

// Climate, heater and tires during a trip. Older trips, and trips imported before these were kept, have none.
function tripConditionsFacts(drive, c) {
    const share = v => `${num(v * 100)}% of the trip`;
    const idealUsed = missing(drive.start_ideal_range_km) || missing(drive.end_ideal_range_km) ? null : drive.start_ideal_range_km - drive.end_ideal_range_km;
    const hasTires = c && !missing(c.tpms_fl);
    const items = [
        missing(idealUsed) ? '' : fact('Ideal range used', withUnit(num(conv.dist(idealUsed)), unit.dist())),
        !c || missing(c.climate_share) ? '' : fact('Climate on', share(c.climate_share)),
        !c || missing(c.driver_temp) ? '' : fact('Set to', fmtTemp(c.driver_temp)),
        !c || missing(c.fan_status) ? '' : fact('Fan', `Speed ${num(c.fan_status, 1)} <small>average</small>`),
        !c || !(c.front_defrost_share > 0 || c.rear_defrost_share > 0) ? '' : fact('Defrost', share(Math.max(c.front_defrost_share || 0, c.rear_defrost_share || 0))),
        !c || !(c.heater_share > 0) ? '' : fact('Battery heater', share(c.heater_share)),
        hasTires ? fact('Tires', tiresText({tpms: {fl: c.tpms_fl, fr: c.tpms_fr, rl: c.tpms_rl, rr: c.tpms_rr}})) : '',
    ];
    return items.some(Boolean) ? `<div class="facts three">${factsGrid(items, 3)}</div>` : '';
}

function renderTripDetail() {
    const drive = tripData.drive;
    const cons = consumption(drive);
    const rangeUsed = missing(drive.start_range_km) || missing(drive.end_range_km) ? null : drive.start_range_km - drive.end_range_km;
    const climb = missing(drive.ascent_m) ? null
        : `↑ ${num(conv.elev(drive.ascent_m))} <small>${unit.elev()}</small> ↓ ${num(conv.elev(drive.descent_m))} <small>${unit.elev()}</small>`;
    document.getElementById('trip-detail').innerHTML = `
        <div class="detail">
            <div class="detail-head">
                <h2>${tripRoute(drive)}</h2>
                <span class="when">${fmtDate(drive.start_ts)} · ${fmtTime(drive.start_ts)} – ${fmtTime(drive.end_ts)}</span>
            </div>
            <div class="facts three">
                ${fact('Distance', withUnit(num(conv.dist(drive.distance_km), 1), unit.dist()), 'big')}
                ${fact('Duration', fmtDuration(drive.duration_s), 'big')}
                ${fact('Energy', withUnit(num(drive.energy_kwh, 1), 'kWh'), 'big')}
                ${fact('Consumption', missing(cons) ? '–' : withUnit(num(conv.cons(cons)), unit.cons()))}
                ${fact('Average speed', missing(drive.speed_avg_kmh) ? '–' : withUnit(num(conv.dist(drive.speed_avg_kmh)), unit.speed()))}
                ${fact('Top speed', missing(drive.speed_max_kmh) ? '–' : withUnit(num(conv.dist(drive.speed_max_kmh)), unit.speed()))}
                ${fact('Battery', `${fmtPct(drive.start_soc)} → ${fmtPct(drive.end_soc)}`)}
                ${fact('Range used', missing(rangeUsed) ? '–' : withUnit(num(conv.dist(rangeUsed)), unit.dist()))}
                ${fact('Peak power', missing(drive.power_max_kw) ? '–' : `${num(drive.power_max_kw)} <small>kW</small>${drive.power_min_kw < 0 ? ` <small>/ regen ${num(-drive.power_min_kw)}</small>` : ''}`)}
                ${fact('Outside', fmtTemp(drive.outside_temp_avg))}
                ${fact('Inside', fmtTemp(drive.inside_temp_avg))}
                ${fact('Climb', climb || '–')}
            </div>
            ${tripConditionsFacts(drive, tripData.conditions)}
            <div>
                <div class="chart-title"><div class="tabs" id="trip-metric"></div></div>
                <div id="trip-chart" class="chart"></div>
            </div>
            <div class="detail-actions">
                <span class="panel-sub">Odometer ${fmtDist(drive.start_odometer_km, 0)} → ${fmtDist(drive.end_odometer_km, 0)}</span>
                <span class="grow"></span>
                <a class="btn btn-small" href="${basePath}api/drives/${drive.id}/gpx" download>GPX</a>
                <button type="button" class="btn btn-small btn-danger" onclick="deleteTrip(${drive.id})">Delete</button>
            </div>
        </div>`;
    buildChips('trip-metric', Object.entries(TRIP_METRICS).map(([key, m]) => ({key, label: m.label})), tripMetric, setTripMetric);
    renderTripChart();
}

async function deleteTrip(id) {
    const ok = await confirmDialog({
        title: 'Delete this trip?',
        message: 'The trip disappears from the list and the statistics. This cannot be undone.',
        confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    try {
        await api(`drives/${id}`, {method: 'DELETE'});
        toast('Trip deleted', 'success');
        selectedTrip = null;
        loadTrips(true);
    } catch (e) {
        toast(e.message, 'error');
    }
}

// Open a trip from another view (dashboard activity, records)
function openTrip(id) {
    pendingTrip = id;
    switchTab('trips');
}

onTab('trips', (force) => {
    buildChips('trips-range', RANGES, tripsRange, setTripsRange);
    buildYearSelect('trips-year', tripsRange, setTripsRange);
    if (tripData && !pendingTrip) {
        drawTrip();
    }
    loadTrips(force);
});
