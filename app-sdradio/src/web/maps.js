// SDRadio dashboard - the map of the modes that find things with a position (aircraft, and later
// ships and APRS stations): Leaflet with OpenStreetMap tiles through Home Assistant's map tile
// proxy, the same map Meldkamer uses. The tiles are darkened only with the dark theme (app.css).

const modeMap = {map: null, el: null, home: null, homeAt: null};

// The map in its slot in the mode's view: made once, moved back in when the view is redrawn
function ensureMap(slot) {
    if (typeof L === 'undefined') {
        slot.innerHTML = '<div class="empty-state">The map could not be loaded.</div>';
        return null;
    }
    if (!modeMap.map) {
        modeMap.el = document.createElement('div');
        modeMap.el.className = 'map';
        slot.appendChild(modeMap.el);
        const saved = lsGet('map_view', null);
        const map = L.map(modeMap.el, {zoomControl: true, attributionControl: true, worldCopyJump: true});
        if (saved && typeof saved.lat === 'number') map.setView([saved.lat, saved.lng], saved.zoom);
        else map.setView([52.2, 5.3], 7);
        L.tileLayer(basePath + 'api/map-tiles/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
        }).addTo(map);
        map.on('moveend', () => {
            const c = map.getCenter();
            lsSet('map_view', {lat: c.lat, lng: c.lng, zoom: map.getZoom()});
        });
        modeMap.map = map;
        window.addEventListener('resize', () => modeMap.map && modeMap.map.invalidateSize());
    } else if (modeMap.el.parentNode !== slot) {
        slot.appendChild(modeMap.el);
    }
    requestAnimationFrame(() => modeMap.map.invalidateSize());
    return modeMap.map;
}

// The receiver's place on the map (Home Assistant's home); the first time, the map goes there
function setMapHome(location) {
    const map = modeMap.map;
    if (!map || !location) return;
    const key = location.join();
    if (modeMap.homeAt === key) return;
    modeMap.homeAt = key;
    if (modeMap.home) modeMap.home.remove();
    modeMap.home = L.circleMarker(location, {radius: 6, color: '#fff', weight: 2, fillColor: '#03a9f4', fillOpacity: 1})
        .bindTooltip('The receiver (Home Assistant\'s home)').addTo(map);
    if (!lsGet('map_view', null)) map.setView(location, 8);
}

// An aircraft's colour by its altitude, low (orange) to high (purple), as flight trackers do
function altitudeColour(feet) {
    if (feet === null || feet === undefined) return '#9e9e9e';
    const stops = [[0, [255, 152, 0]], [5000, [205, 220, 57]], [10000, [76, 175, 80]], [20000, [0, 188, 212]],
        [30000, [33, 150, 243]], [40000, [156, 39, 176]]];
    const f = Math.max(0, Math.min(40000, feet));
    for (let i = 1; i < stops.length; i++) {
        if (f <= stops[i][0]) {
            const [a, ca] = stops[i - 1], [b, cb] = stops[i];
            const t = (f - a) / (b - a);
            return `rgb(${ca.map((c, k) => Math.round(c + (cb[k] - c) * t)).join(',')})`;
        }
    }
    return 'rgb(156,39,176)';
}

const PLANE_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2c.8 0 1.3.8 1.3 1.8v5.4l7.7 4.6v2.1l-7.7-2.3v4.6l2.2 1.7V21L12 20l-3.5 1v-1.1l2.2-1.7v-4.6L3 15.9v-2.1l7.7-4.6V3.8C10.7 2.8 11.2 2 12 2z"/></svg>';

function planeIcon(track, colour, selected) {
    return L.divIcon({
        className: 'plane-icon' + (selected ? ' selected' : ''),
        html: `<span style="transform: rotate(${Math.round(track || 0)}deg); color: ${colour}">${PLANE_SVG}</span>`,
        iconSize: [26, 26], iconAnchor: [13, 13],
    });
}
