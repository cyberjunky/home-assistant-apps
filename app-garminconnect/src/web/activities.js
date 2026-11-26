// Garmin Connect dashboard - Activities: the list, the track on the map and the details.

const actsPeriod = periodPicker('acts', '12m', () => loadActivities());
let activities = [];
let selectedActivity = null;
let pendingActivity = null;

// One Leaflet map for this view; tiles come through the app and Home Assistant's map tile proxy
let savedView = null;
try { savedView = JSON.parse(localStorage.getItem(LS + 'map_view')); } catch (e) { /* first visit */ }
const map = L.map('map', {zoomControl: true}).setView(savedView ? [savedView.lat, savedView.lng] : [52.2, 5.3], savedView ? savedView.zoom : 8);
map.on('moveend', () => {
    const c = map.getCenter();
    localStorage.setItem(LS + 'map_view', JSON.stringify({lat: c.lat, lng: c.lng, zoom: map.getZoom()}));
});
function tileLayer() {
    return L.tileLayer(basePath + 'api/map-tiles/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
        maxZoom: 19,
    });
}
tileLayer().addTo(map);
const trackLayer = L.layerGroup().addTo(map);

// ----- Heat maps: where you've been -----
// Zoomed out: the ~100 m cells the server keeps per activity, as a heat map. Up close those cells
// would show as a grid of dots, so from CLOSE_ZOOM on the tracks in view are drawn instead, thin and
// see-through, so the roads taken most often come out darkest.
const CLOSE_ZOOM = 14;
const HEAT_OPTIONS = {
    radius: 7, blur: 6, minOpacity: 0.5, maxZoom: 12,
    // One hue, light to dark with how often you passed (as on the Tesla app's map)
    gradient: {0.3: '#86b6ef', 0.6: '#3987e5', 1.0: '#184f95'},
};
function heatLatLngs(points) {
    const max = Math.max(1, ...points.map(p => p[2]));
    return points.map(p => [p[0], p[1], Math.min(1, 0.35 + p[2] / max)]);
}

// The heat of one map. show({start, end, family}) shows a period; show(null) hides it.
function heatOn(target) {
    // The tracks up close get a pane under the selected activity's track
    const pane = target.createPane('heat-lines');
    pane.style.zIndex = 350;
    const renderer = L.canvas({pane});
    const lines = L.layerGroup();
    let cells = null, query = null, points = [], timer = null, request = 0;
    const cellsQuery = () => `start=${query.start}&end=${query.end}${query.family ? '&family=' + query.family : ''}`;

    const drawCells = () => {
        target.removeLayer(lines);
        if (!cells) {
            cells = L.heatLayer(heatLatLngs(points), HEAT_OPTIONS).addTo(target);
            // leaflet.heat always draws into the overlay pane, over the tracks: move it underneath
            const canvas = cells._canvas;
            if (canvas && canvas.parentNode) canvas.parentNode.insertBefore(canvas, canvas.parentNode.firstChild);
        }
    };
    const drawLines = async () => {
        if (cells) { target.removeLayer(cells); cells = null; }
        const b = target.getBounds();
        const mine = ++request;
        try {
            const data = await api(`activities/heat-tracks?${cellsQuery()}&south=${b.getSouth()}&west=${b.getWest()}&north=${b.getNorth()}&east=${b.getEast()}`);
            if (mine !== request || !query) return;
            const color = getComputedStyle(document.documentElement).getPropertyValue('--series-1').trim() || '#2a78d6';
            lines.clearLayers();
            data.lines.forEach(line => L.polyline(line, {renderer, color, weight: 3, opacity: 0.28, interactive: false}).addTo(lines));
            lines.addTo(target);
        } catch (e) { /* the map stays as it was */ }
    };
    const redraw = () => {
        if (!query) return;
        if (target.getZoom() >= CLOSE_ZOOM) drawLines(); else drawCells();
    };
    target.on('moveend', () => { clearTimeout(timer); timer = setTimeout(redraw, 200); });
    // leaflet.heat sizes its canvas when the map moves, not when the map changes size: redraw it then too
    target.on('resize', () => { if (cells) cells._reset(); });

    return {
        async show(next) {
            query = next;
            request++;
            if (cells) { target.removeLayer(cells); cells = null; }
            target.removeLayer(lines);
            lines.clearLayers();
            if (!query) return {points: [], pending: 0};
            const data = await api(`activities/heat?${cellsQuery()}`);
            if (query !== next) return data;
            points = data.points || [];
            redraw();
            return data;
        },
    };
}

let mapHeat = null;
let mapHeatRetry = null;
async function loadMapHeat() {
    const select = document.getElementById('map-heat');
    clearTimeout(mapHeatRetry);
    try { localStorage.setItem(LS + 'map_heat', select.value); } catch (e) { /* private window */ }
    if (!mapHeat) mapHeat = heatOn(map);
    if (select.value === '') {
        mapHeat.show(null);
        return;
    }
    const days = Number(select.value);
    const end = app.today || isoDay(new Date());
    const start = days ? addDays(end, -(days - 1)) : firstDay();
    select.disabled = true;
    map.invalidateSize();
    try {
        const data = await mapHeat.show({start, end, family: document.getElementById('acts-family').value});
        if (!(data.points || []).length && !data.pending) toast('No tracks in this period');
        // Tracks fetched before 0.1.2 are still being mapped: fill in as they come
        if (data.pending) mapHeatRetry = setTimeout(loadMapHeat, 5000);
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        select.disabled = false;
    }
}

function pinIcon(label, kind) {
    return L.divIcon({className: '', html: `<div class="pin ${kind || ''}">${escapeHtml(label)}</div>`, iconSize: [22, 22], iconAnchor: [11, 11]});
}

function activityPace(a) {
    if (!a.distance_m || !a.duration_s) return '–';
    const mps = a.distance_m / (a.moving_s || a.duration_s);
    return pacedSport(a.family) ? fmtPace(mps) : fmtSpeed(mps);
}

function renderActivityRows() {
    const el = document.getElementById('acts-rows');
    if (!activities.length) {
        el.innerHTML = `<div class="empty-state">${app.account.signed_in ? 'No activities in this period' : 'Sign in to fetch your activities'}</div>`;
        return;
    }
    let html = '', month = '';
    activities.forEach(a => {
        const m = a.day.slice(0, 7);
        if (m !== month) {
            month = m;
            const inMonth = activities.filter(x => x.day.startsWith(m));
            const km = inMonth.reduce((s, x) => s + (x.distance_m || 0), 0) / 1000;
            const hours = inMonth.reduce((s, x) => s + (x.duration_s || 0), 0);
            html += `<div class="feed-day"><span>${parseDay(m + '-01').toLocaleDateString([], {month: 'long', year: 'numeric'})}</span><span>${inMonth.length} · ${fmtKm(km, 0)} · ${fmtDuration(hours)}</span></div>`;
        }
        html += `<button type="button" class="feed-row${a.id === selectedActivity ? ' selected' : ''}" data-id="${a.id}">
            <span class="time">${fmtDayShort(a.day)} ${a.start_local.slice(11, 16)}</span>
            <span class="route"><i class="sport-dot" style="background:${SPORT_COLORS[a.family]}"></i>${escapeHtml(a.name || typeName(a.type))}</span>
            <span class="num">${a.distance_m ? fmtMeters(a.distance_m) : '–'}</span>
            <span class="num col-dur">${fmtDuration(a.duration_s)}</span>
            <span class="num col-pace">${activityPace(a)}</span>
            <span class="num col-hr">${num(a.avg_hr)}</span>
            <span class="num col-load">${num(a.training_load)}</span>
        </button>`;
    });
    el.innerHTML = html;
    el.onclick = e => { const row = e.target.closest('[data-id]'); if (row) showActivity(Number(row.dataset.id)); };
}

async function loadActivities() {
    actsPeriod.render();
    const range = actsPeriod.days();
    const family = document.getElementById('acts-family').value;
    try {
        activities = await api(`activities?start=${range.start}&end=${range.end}${family ? '&family=' + family : ''}`);
        const km = activities.reduce((s, a) => s + (a.distance_m || 0), 0) / 1000;
        document.getElementById('acts-status').textContent = `${num(activities.length)} activities · ${fmtKm(km, 0)}`;
        renderActivityRows();
        if (pendingActivity) { const id = pendingActivity; pendingActivity = null; showActivity(id); }
        else if (!selectedActivity && activities.length) showActivity(activities[0].id);
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function showActivity(id) {
    selectedActivity = id;
    document.querySelectorAll('#acts-rows .feed-row').forEach(r => r.classList.toggle('selected', Number(r.dataset.id) === id));
    history.replaceState(null, '', `#activities/${id}`);
    const dock = document.getElementById('act-detail');
    try {
        const a = await api(`activities/${id}`);
        trackLayer.clearLayers();
        // Opened straight from a link, the map may not have its size yet
        map.invalidateSize();
        if (a.track && a.track.length > 1) {
            L.polyline(a.track, {weight: 8, className: 'trk-casing', interactive: false}).addTo(trackLayer);
            L.polyline(a.track, {weight: 4, className: 'trk-plain', interactive: false}).addTo(trackLayer);
            L.marker(a.track[0], {icon: pinIcon('A'), interactive: false}).addTo(trackLayer);
            L.marker(a.track[a.track.length - 1], {icon: pinIcon('B', 'end'), interactive: false}).addTo(trackLayer);
            map.fitBounds(L.latLngBounds(a.track), {padding: [30, 30]});
        } else if (!missing(a.start_lat)) {
            L.marker([a.start_lat, a.start_lon], {icon: pinIcon('A'), interactive: false}).addTo(trackLayer);
            map.setView([a.start_lat, a.start_lon], 13);
        }
        dock.innerHTML = activityDetail(a);
        const zoneEl = dock.querySelector('#act-zones');
        if (zoneEl) renderZones(zoneEl, a.zones_s);
        renderActivityCurves(a);
    } catch (e) {
        dock.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    }
}

function activityDetail(a) {
    const fact = (l, v) => `<div class="fact"><div class="l">${escapeHtml(l)}</div><div class="v">${v}</div></div>`;
    const facts = [
        fact('Distance', a.distance_m ? fmtMeters(a.distance_m) : '–'),
        fact('Time', fmtDuration(a.duration_s)),
        fact(pacedSport(a.family) ? 'Pace' : 'Speed', activityPace(a)),
        fact('Climb', fmtElev(a.elevation_gain_m)),
        fact('Heart rate', a.avg_hr ? `${num(a.avg_hr)}<small> avg · ${num(a.max_hr)} max</small>` : '–'),
        fact('Calories', a.calories ? `${num(a.calories)}<small> kcal</small>` : '–'),
        fact('Training effect', missing(a.aerobic_te) ? '–' : `${num(a.aerobic_te, 1)}<small> aerobic · ${num(a.anaerobic_te, 1)} anaerobic</small>`),
        fact('Training load', num(a.training_load)),
        fact('Body Battery', missing(a.body_battery) ? '–' : `${a.body_battery > 0 ? '+' : ''}${num(a.body_battery)}`),
        fact('VO2 max', missing(a.vo2max) ? '–' : num(a.vo2max)),
    ];
    const extra = a.extra || {};
    const extras = [];
    if (extra.averageRunningCadenceInStepsPerMinute) extras.push(fact('Cadence', `${num(extra.averageRunningCadenceInStepsPerMinute)}<small> spm</small>`));
    if (extra.avgStrideLength) extras.push(fact('Stride', `${num(extra.avgStrideLength / 100, 2)}<small> m</small>`));
    if (extra.averageBikingCadenceInRevPerMinute) extras.push(fact('Cadence', `${num(extra.averageBikingCadenceInRevPerMinute)}<small> rpm</small>`));
    if (extra.avgPower) extras.push(fact('Power', `${num(extra.avgPower)}<small> W avg${extra.normPower ? ` · ${num(extra.normPower)} NP` : ''}</small>`));
    if (extra.waterEstimated) extras.push(fact('Sweat loss', `${num(extra.waterEstimated)}<small> ml</small>`));
    if (extra.lapCount) extras.push(fact('Laps', num(extra.lapCount)));
    if (extra.trainingEffectLabel) extras.push(fact('Benefit', escapeHtml(typeName(extra.trainingEffectLabel.toLowerCase()))));
    return `<div class="detail">
        <div class="detail-head"><i class="sport-dot" style="background:${SPORT_COLORS[a.family]}"></i><h2>${escapeHtml(a.name || typeName(a.type))}</h2>
            <span class="badge">${escapeHtml(typeName(a.type))}</span><span class="when">${fmtDay(a.day)} ${a.start_local.slice(11, 16)}</span></div>
        ${a.location ? `<div class="panel-sub">${escapeHtml(a.location)}</div>` : ''}
        <div class="facts">${facts.join('')}${extras.join('')}</div>
        ${a.weather ? `<div class="panel-sub">${weatherText(a.weather)}</div>` : ''}
        ${a.curves && a.curves.hr ? '<div><div class="block-title">Heart rate</div><div id="act-hr" class="chart"></div></div>' : ''}
        ${a.curves && a.curves.speed ? `<div><div class="block-title">${pacedSport(a.family) ? 'Pace' : 'Speed'}</div><div id="act-speed" class="chart"></div></div>` : ''}
        ${a.curves && a.curves.elevation ? '<div><div class="block-title">Elevation</div><div id="act-elev" class="chart"></div></div>' : ''}
        ${a.zones_s && a.zones_s.some(z => z) ? '<div><div class="block-title">Heart rate zones</div><div id="act-zones"></div></div>' : ''}
        ${lapsTable(a)}
        ${setsTable(a.sets)}
        ${a.details_fetched ? '' : '<p class="panel-sub">Laps, curves and weather appear once this activity\'s details are fetched.</p>'}
        <div class="detail-actions"><a class="btn btn-small" href="https://connect.garmin.com/modern/activity/${a.id}" target="_blank" rel="noopener">Open in Garmin Connect</a></div>
    </div>`;
}

// Garmin's activity weather comes in Fahrenheit and miles per hour
function weatherText(w) {
    const parts = [];
    if (w.description) parts.push(escapeHtml(w.description));
    if (!missing(w.temp)) parts.push(imperial() ? `${num(w.temp)} °F` : `${num((w.temp - 32) * 5 / 9)} °C`);
    if (!missing(w.apparent_temp) && w.apparent_temp !== w.temp) parts.push(`feels ${imperial() ? num(w.apparent_temp) + ' °F' : num((w.apparent_temp - 32) * 5 / 9) + ' °C'}`);
    if (!missing(w.humidity)) parts.push(`${num(w.humidity)}% humidity`);
    if (!missing(w.wind_speed)) parts.push(`wind ${imperial() ? num(w.wind_speed) + ' mph' : num(w.wind_speed * 1.609) + ' km/h'}${w.wind_from ? ' ' + escapeHtml(String(w.wind_from).toUpperCase()) : ''}`);
    return parts.length ? '🌤 ' + parts.join(' · ') : '';
}

function renderActivityCurves(a) {
    const c = a.curves || {};
    const byDistance = c.distance && c.distance.some(v => v) && a.distance_m > 500;
    const x = byDistance ? c.distance.map(m => toDist((m || 0) / 1000)) : (c.time || []).map(s => (s || 0) / 60);
    const formatX = byDistance ? v => `${num(v, 2)} ${distUnit()}` : v => fmtDuration(v * 60);
    const ticks = timeTicks(x, 4, byDistance ? v => `${num(v, 1)} ${distUnit()}` : v => fmtDuration(v * 60));
    const draw = (id, values, color, fmt, area, tick) => {
        const el = document.getElementById(id);
        if (el) lineChart(el, {x, series: [{name: '', color, values}], height: 120, area, formatX, formatY: fmt, formatTick: tick, xTicks: ticks});
    };
    if (c.hr) draw('act-hr', c.hr, 'var(--series-2)', v => `${num(v)} bpm`);
    // Speed in km/h on the axis; for running and walking the axis and tooltip read as pace
    const pace = pacedSport(a.family);
    if (c.speed) draw('act-speed', c.speed.map(v => missing(v) ? null : toDist(v * 3.6)), 'var(--series-1)',
        v => pace ? fmtPace(v / 3.6 * (imperial() ? KM_PER_MILE : 1)) : `${num(v, 1)} ${imperial() ? 'mph' : 'km/h'}`, false,
        v => pace && v > 0 ? fmtPace(v / 3.6 * (imperial() ? KM_PER_MILE : 1)).split(' ')[0] : num(v, 1));
    if (c.elevation) draw('act-elev', c.elevation, 'var(--series-3)', v => fmtElev(v), true);
}

function lapsTable(a) {
    const laps = a.laps || [];
    if (laps.length < 2) return '';
    const paced = pacedSport(a.family);
    const rows = laps.map((l, i) => `<tr><td>${i + 1}</td><td>${l.distance_m ? fmtMeters(l.distance_m) : '–'}</td><td>${fmtDuration(l.duration_s)}</td>
        <td>${missing(l.avg_speed) ? '–' : paced ? fmtPace(l.avg_speed) : fmtSpeed(l.avg_speed)}</td><td>${num(l.avg_hr)}</td><td>${missing(l.climb_m) ? '–' : fmtElev(l.climb_m)}</td></tr>`).join('');
    return `<div><div class="block-title">Laps</div><table class="laps"><thead><tr><th>#</th><th>Distance</th><th>Time</th><th>${paced ? 'Pace' : 'Speed'}</th><th>HR</th><th>Climb</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function setsTable(sets) {
    if (!sets || !sets.length) return '';
    const rows = sets.map((s, i) => `<tr><td>${i + 1}</td><td>${escapeHtml(s.exercise || '–')}</td><td>${num(s.reps)}</td>
        <td>${s.weight ? fmtWeight(s.weight > 1000 ? s.weight / 1000 : s.weight) : '–'}</td><td>${missing(s.duration_s) ? '–' : fmtDuration(s.duration_s)}</td></tr>`).join('');
    return `<div><div class="block-title">Sets</div><table class="laps"><thead><tr><th>#</th><th>Exercise</th><th>Reps</th><th>Weight</th><th>Time</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

const ZONE_COLORS = ['var(--seq-5)', 'var(--seq-3)', 'var(--series-3)', 'var(--series-4)', 'var(--series-2)'];
function renderZones(el, zones) {
    const total = (zones || []).reduce((a, z) => a + (z || 0), 0);
    if (!total) { el.innerHTML = '<div class="empty-state">No heart rate zones</div>'; return; }
    el.innerHTML = `<div class="share">${zones.map((z, i) => z ? `<div style="flex:${z};background:${ZONE_COLORS[i]}" title="Zone ${i + 1}: ${fmtDuration(z)}">${z / total > 0.1 ? 'Z' + (i + 1) : ''}</div>` : '').join('')}</div>
        <div class="legend">${zones.map((z, i) => `<span><i style="background:${ZONE_COLORS[i]}"></i>Zone ${i + 1} · ${fmtDuration(z || 0)} · ${num((z || 0) / total * 100)}%</span>`).join('')}</div>`;
}

function openActivity(id) {
    pendingActivity = id;
    selectedActivity = null;
    // The activity may lie outside the list's period: show all of it
    if (actsPeriod.get() !== 'all') actsPeriod.set('all');
    switchTab('activities');
}

onTab('activities', () => {
    requestAnimationFrame(() => map.invalidateSize());
    loadActivities();
    const heat = document.getElementById('map-heat');
    let saved = '';
    try { saved = localStorage.getItem(LS + 'map_heat') || ''; } catch (e) { /* private window */ }
    if (heat.value !== saved || (saved && !mapHeat)) { heat.value = saved; loadMapHeat(); }
});
