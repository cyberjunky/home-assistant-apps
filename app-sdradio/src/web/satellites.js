// SDRadio dashboard - the Satellites mode: where they are on the map, the passes of the coming day,
// the one being followed, and the passes heard before (with their pictures and recordings).

const satView = {live: null, tracks: {}, layers: new Map(), trackLayers: [], list: lsGet('sat_list', 'passes'), tracksAt: 0};
const SAT_BRINGS = {sstv: 'voice, SSTV pictures', aprs: 'APRS packets', voice: 'FM voice (repeater)', recording: 'weather pictures (recording)'};
const SAT_COLOURS = {iss: '#ff9800', 'iss-aprs': '#ff9800', so50: '#ab47bc', 'meteor-m2-3': '#03a9f4', 'meteor-m2-4': '#00bcd4'};

async function loadSatellites() {
    const [live, history] = await Promise.all([api('decoded/live?mode=satellites'),
        api(`decoded?${new URLSearchParams({mode: 'satellites', limit: 300, search: modeView.search})}`)]);
    if (modeView.key !== 'satellites') return;
    modeView.location = live.location;
    if (live.live) {
        satView.live = live.live;
        satView.tracks = live.live.tracks || {};
        satView.tracksAt = Date.now();
    }
    modeView.items = history.items;
}

function onSatelliteLive(m) {
    satView.live = {...(satView.live || {}), ...m};
    // The ground tracks move on: fetch them again every ten minutes (soon, while there are none yet)
    const none = !Object.keys(satView.tracks || {}).length;
    if (Date.now() - satView.tracksAt > (none ? 5_000 : 600_000)) {
        satView.tracksAt = Date.now();
        api('decoded/live?mode=satellites').then(d => { if (d.live) { satView.tracks = d.live.tracks || {}; drawSatTracks(); } }).catch(() => {});
    }
    drawSatellites();
    queueModeRender();
}

function clearSatellites() {
    for (const l of satView.layers.values()) { l.marker.remove(); l.circle.remove(); }
    satView.layers.clear();
    for (const l of satView.trackLayers) l.remove();
    satView.trackLayers = [];
    satView.live = null;
    satView.tracks = {};
}

// How far a satellite at this height sees: the circle on the ground it is above the horizon for
function footprintMetres(heightKm) {
    const r = 6371;
    return r * Math.acos(r / (r + heightKm)) * 1000;
}

function drawSatellites() {
    const map = modeMap.map;
    if (!map || !satView.live) return;
    const seen = new Set();
    for (const s of satView.live.satellites || []) {
        seen.add(s.id);
        const colour = SAT_COLOURS[s.id] || '#607d8b';
        let l = satView.layers.get(s.id);
        if (!l) {
            l = {
                marker: L.marker([s.lat, s.lon], {icon: L.divIcon({className: 'aprs-icon sat-icon', html: `<span style="border-color:${colour}">🛰</span>`, iconSize: [24, 24], iconAnchor: [12, 12]}), keyboard: false})
                    .bindTooltip('', {permanent: true, direction: 'right', offset: [10, 0], className: 'plane-label'}).addTo(map),
                circle: L.circle([s.lat, s.lon], {radius: footprintMetres(s.height_km), color: colour, weight: 1, opacity: 0.6, fillOpacity: 0.05, interactive: false}).addTo(map),
            };
            satView.layers.set(s.id, l);
        }
        l.marker.setLatLng([s.lat, s.lon]);
        l.marker.setTooltipContent(`${escapeHtml(s.name)}${s.elevation > 0 ? ` · ${num(s.elevation, 0)}°` : ''}`);
        l.circle.setLatLng([s.lat, s.lon]).setRadius(footprintMetres(s.height_km));
    }
    for (const [id, l] of satView.layers) {
        if (!seen.has(id)) { l.marker.remove(); l.circle.remove(); satView.layers.delete(id); }
    }
}

function drawSatTracks() {
    const map = modeMap.map;
    if (!map) return;
    for (const l of satView.trackLayers) l.remove();
    satView.trackLayers = [];
    for (const [id, points] of Object.entries(satView.tracks || {})) {
        // Split where the track crosses the date line, so it doesn't draw across the map
        let part = [];
        const parts = [part];
        points.forEach((p, i) => {
            if (i && Math.abs(p[1] - points[i - 1][1]) > 180) { part = []; parts.push(part); }
            part.push(p);
        });
        for (const segment of parts) {
            satView.trackLayers.push(L.polyline(segment, {color: SAT_COLOURS[id] || '#607d8b', weight: 1.5, opacity: 0.7, dashArray: '4 4', interactive: false}).addTo(map));
        }
    }
}

const inTime = (ts, now) => {
    const s = ts - now;
    if (s <= 0) return 'now';
    if (s < 3600) return `in ${Math.round(s / 60)} min`;
    return `in ${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
};
const compass = deg => ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(((deg % 360) + 360) % 360 / 45) % 8];

function renderSatellites(body) {
    const split = body.querySelector('.mode-split');
    if (!split || split.dataset.mode !== 'satellites') {
        body.innerHTML = `<div class="mode-split" data-mode="satellites">
            <div class="map-slot" id="map-slot"></div>
            <div class="mode-list">
                <div class="sat-now" id="sat-now" hidden></div>
                <div class="tabs inline-tabs list-tabs" id="sat-tabs" role="group" aria-label="Which passes"></div>
                <div class="mode-list-body" id="mode-list"></div>
            </div></div>`;
        buildChips(document.getElementById('sat-tabs'), [{key: 'passes', label: 'Passes'}, {key: 'history', label: 'Heard'}],
            satView.list, (key) => { satView.list = key; lsSet('sat_list', key); renderModeBody(); });
        ensureMap(document.getElementById('map-slot'), 'satellites');
        drawSatTracks();
        drawSatellites();
    }
    setMapHome(modeView.location);
    if (!satView.trackLayers.length && Object.keys(satView.tracks || {}).length) drawSatTracks();
    drawSatellites();
    const live = satView.live || {};
    const now = live.now || Date.now() / 1000;
    const p = live.pass;
    const box = document.getElementById('sat-now');
    box.hidden = !p;
    if (p) {
        box.innerHTML = `<b>${escapeHtml(p.name)}</b> overhead · ${num(p.elevation, 0)}° ${compass(p.azimuth || 0)} (${num(p.azimuth, 0)}°)
            · Doppler ${p.doppler > 0 ? '+' : ''}${num((p.doppler || 0) / 1000, 1)} kHz · sets ${inTime(p.los, now)}
            · ${escapeHtml(SAT_BRINGS[p.brings] || '')}${p.pictures && p.pictures.length ? ` · ${p.pictures.length} picture${p.pictures.length > 1 ? 's' : ''}` : ''}${p.packets ? ` · ${p.packets} packets` : ''}${p.recording ? ' · recording' : ''}`;
    }
    document.getElementById('mode-list').innerHTML = satView.list === 'history' ? satHistoryHtml() : satPassesHtml(live, now);
    renderModeHead();
}

function satPassesHtml(live, now) {
    const passes = (live.passes || []).filter(p => !modeView.search || p.name.toLowerCase().includes(modeView.search.toLowerCase()));
    if (!live.passes) return emptyHtml('Working out the passes…');
    if (!passes.length) return emptyHtml('No passes above the minimum elevation in the coming day for the satellites followed (Settings → Modes).');
    return `<table class="data aircraft"><thead><tr><th>Satellite</th><th>Rises</th><th>Highest</th><th>Across</th><th>Brings</th></tr></thead><tbody>
        ${passes.map(p => `<tr class="${p.aos <= now && now <= p.los ? 'on' : ''}">
            <td><span class="plane-dot" style="background:${SAT_COLOURS[p.id] || '#607d8b'}"></span><b>${escapeHtml(p.name)}</b><small class="muted mono">${fmtMHz(p.frequency)} MHz</small></td>
            <td class="mono">${fmtTime(p.aos)} <small class="muted">${inTime(p.aos, now)} · ${Math.round((p.los - p.aos) / 60)} min</small></td>
            <td class="mono">${num(p.max_elevation, 0)}°</td>
            <td class="mono">${compass(p.aos_azimuth)} → ${compass(p.los_azimuth)}</td>
            <td>${escapeHtml(SAT_BRINGS[p.brings] || '')}</td>
        </tr>`).join('')}
    </tbody></table>`;
}

function satHistoryHtml() {
    const passes = modeView.items.filter(i => i.type === 'pass' && matchesSatSearch(i));
    if (!passes.length) return emptyHtml('Passes are listed here once they are over.');
    return passes.map(i => `<div class="msg">
        <span class="msg-time mono">${timeOf(i.aos || i.ts)}</span>
        <span class="badge">${num(i.max_seen ?? i.max_elevation, 0)}°</span>
        <span class="msg-addr mono">${escapeHtml(i.name)}</span>
        <span class="msg-text">${escapeHtml(SAT_BRINGS[i.brings] || '')}${i.packets ? ` · ${i.packets} packets` : ''}
            ${(i.pictures || []).map(f => `<a href="${fileUrl('satellites', f)}" target="_blank" rel="noopener"><img class="sat-thumb" src="${fileUrl('satellites', f)}" alt="SSTV picture"></a>`).join('')}
            ${i.recording ? ` · <a href="${fileUrl('satellites', i.recording)}" download>${escapeHtml(i.recording)}</a> (${num(i.recording_mb, 0)} MB, for SatDump)` : ''}</span>
    </div>`).join('');
}

function matchesSatSearch(i) {
    const s = modeView.search.toLowerCase();
    return !s || [i.name, i.brings].some(v => v && String(v).toLowerCase().includes(s));
}
