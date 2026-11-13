// SDRadio dashboard - the map of the modes that find things with a position (aircraft, and later
// ships and APRS stations): Leaflet with OpenStreetMap tiles through Home Assistant's map tile
// proxy, the same map Meldkamer uses. The tiles are darkened only with the dark theme (app.css).

const modeMap = {map: null, el: null, home: null, homeAt: null, viewKey: 'map_view'};
// Where each mode's map opens: satellites see the whole world, the others the neighbourhood
// FT8 stations are hundreds to thousands of km away: a map of Europe and beyond
const MAP_START = {satellites: {zoom: 2, world: true}, ft8: {zoom: 4}, sondes: {zoom: 7}};

// The map in its slot in the mode's view: made once, moved back in when the view is redrawn.
// Each mode keeps its own view (zoomed in on ships, out on satellites)
function ensureMap(slot, mode) {
    const viewKey = `map_view_${mode || 'map'}`;
    if (typeof L === 'undefined') {
        slot.innerHTML = '<div class="empty-state">The map could not be loaded.</div>';
        return null;
    }
    if (!modeMap.map) {
        modeMap.el = document.createElement('div');
        modeMap.el.className = 'map';
        slot.appendChild(modeMap.el);
        const map = L.map(modeMap.el, {zoomControl: true, attributionControl: true, worldCopyJump: true});
        L.tileLayer(basePath + 'api/map-tiles/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
        }).addTo(map);
        map.on('moveend', () => {
            if (modeMap.restoring) return;
            const c = map.getCenter();
            lsSet(modeMap.viewKey, {lat: c.lat, lng: c.lng, zoom: map.getZoom()});
        });
        modeMap.map = map;
        window.addEventListener('resize', () => modeMap.map && modeMap.map.invalidateSize());
    } else if (modeMap.el.parentNode !== slot) {
        slot.appendChild(modeMap.el);
    }
    if (modeMap.viewKey !== viewKey || !modeMap.viewSet) {
        modeMap.viewKey = viewKey;
        modeMap.viewSet = true;
        const saved = lsGet(viewKey, null);
        const start = MAP_START[mode] || {zoom: 7};
        const home = modeMap.homeAt ? modeMap.homeAt.split(',').map(Number) : [52.2, 5.3];
        modeMap.restoring = true;
        if (saved && typeof saved.lat === 'number') modeMap.map.setView([saved.lat, saved.lng], saved.zoom);
        else modeMap.map.setView(start.world ? [25, home[1]] : home, start.zoom);
        modeMap.restoring = false;
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
    const mode = modeMap.viewKey.replace('map_view_', '');
    if (!lsGet(modeMap.viewKey, null) && mode !== 'satellites') map.setView(location, (MAP_START[mode] || {}).zoom || 8);
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

// A ship's colour by its kind, as ship trackers colour them
function shipColour(s) {
    const code = s.ship_type || 0, tens = Math.floor(code / 10);
    if (s.base_station || s.aid !== undefined) return '#607d8b';
    if (tens === 7) return '#4caf50';           // cargo
    if (tens === 8) return '#e53935';           // tanker
    if (tens === 6) return '#1e88e5';           // passenger
    if (code === 30) return '#ff9800';          // fishing
    if (code === 36 || code === 37) return '#ab47bc';  // sailing, pleasure craft
    if ([31, 32, 50, 51, 52, 53, 55, 58].includes(code)) return '#00acc1';  // tugs, pilots, rescue, authorities
    if (tens === 4) return '#fdd835';           // high speed craft
    return '#9e9e9e';
}

const SHIP_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5l5 7.5v11.5H7V10z"/></svg>';
const MOORED_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="5.5"/></svg>';

// Pointing where it sails (heading, else course); a circle when it lies still
function shipIcon(s, colour, selected) {
    const still = (s.speed || 0) < 0.5;
    const angle = Math.round(s.heading ?? s.course ?? 0);
    return L.divIcon({
        className: 'plane-icon ship-icon' + (selected ? ' selected' : ''),
        html: `<span style="transform: rotate(${still ? 0 : angle}deg); color: ${colour}">${still ? MOORED_SVG : SHIP_SVG}</span>`,
        iconSize: [22, 22], iconAnchor: [11, 11],
    });
}

// APRS: the station's symbol (the common ones of the primary table) on a disc
const APRS_SYMBOLS = {'>': '🚗', '-': '🏠', '#': '📡', '&': '📡', '_': '🌤', 'k': '🚚', 'v': '🚐', '[': '🚶', 'b': '🚲',
    'Y': '⛵', 's': '🚤', 'O': '🎈', '<': '🏍', 'j': '🚙', 'R': '🚐', 'U': '🚌', 'X': '🚁', '^': '✈', "'": '✈', 'y': '📡',
    'r': '📡', 'a': '🚑', 'f': '🚒', 'u': '🚚', 'n': '📡', 'l': '💻', '$': '📱'};

function aprsSymbol(s) {
    const symbol = s.symbol || '';
    return (symbol[0] === '/' && APRS_SYMBOLS[symbol[1]]) || '•';
}

function aprsColour(s) {
    if (s.weather) return '#00acc1';
    if (s.speed) return '#4caf50';
    if ((s.symbol || '').match(/[#&ry n]$/)) return '#ab47bc';
    return '#ff9800';
}

function aprsIcon(s, colour, selected) {
    return L.divIcon({
        className: 'aprs-icon' + (selected ? ' selected' : ''),
        html: `<span style="border-color: ${colour}">${aprsSymbol(s)}</span>`,
        iconSize: [24, 24], iconAnchor: [12, 12],
    });
}

// A radiosonde: a balloon, coloured by its altitude (metres, as the sondes report it)
const BALLOON_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a6 6 0 0 1 6 6c0 3.6-3.2 7-5 8l.8 1.5h-3.6L11 16c-1.8-1-5-4.4-5-8a6 6 0 0 1 6-6z"/><path d="M12 17.5v4.5" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>';
function sondeIcon(s, colour, selected) {
    return L.divIcon({
        className: 'plane-icon' + (selected ? ' selected' : ''),
        html: `<span style="color: ${colour}">${BALLOON_SVG}</span>`,
        iconSize: [26, 26], iconAnchor: [13, 22],
    });
}

// An FT8 station: a dot, coloured by how strong it was heard (red weak, green strong)
function snrColour(snr) {
    const t = Math.max(0, Math.min(1, ((snr ?? -24) + 24) / 34));
    return `hsl(${Math.round(t * 120)}, 75%, 45%)`;
}
function dotIcon(colour, selected) {
    return L.divIcon({
        className: 'aprs-icon' + (selected ? ' selected' : ''),
        html: `<span style="border-color: ${colour}; background: ${colour}; width: 14px; height: 14px"></span>`,
        iconSize: [14, 14], iconAnchor: [7, 7],
    });
}
