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
        ${a.zones_s && a.zones_s.some(z => z) ? '<div><div class="block-title">Heart rate zones</div><div id="act-zones"></div></div>' : ''}
        <div class="detail-actions"><a class="btn btn-small" href="https://connect.garmin.com/modern/activity/${a.id}" target="_blank" rel="noopener">Open in Garmin Connect</a></div>
    </div>`;
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
});
