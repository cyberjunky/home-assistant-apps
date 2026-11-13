// Network dashboard - Mobile: phones and other GPS trackers. Where they are, where they went (as a
// track or a heatmap of where they spend their time), their days per zone, trips, the usual times of
// leaving and coming home, the battery, and whether the phone's Wi-Fi agrees with its GPS.

const mobState = {
    key: stored('mob_key', ''),
    days: Number(stored('mob_days', '1')),
    view: stored('mob_view', 'track'),
    data: null,
    loadedFor: '',
    map: null,
    layers: null,
    heat: null,
    fitted: '',
};
const MOB_DAYS = [{key: '1', label: '24 hours'}, {key: '7', label: '7 days'}, {key: '30', label: '30 days'}];
const ZONE_COLORS = {home: 'var(--success-color)', not_home: 'var(--idle)'};
const ZONE_SERIES = ['--series-1', '--series-4', '--series-5', '--series-2'];
const zoneColor = (state, i) => ZONE_COLORS[state] || `var(${ZONE_SERIES[i % ZONE_SERIES.length]})`;
const gpsHosts = () => app.hosts.filter(h => h.gps_only);
const zones = () => app.zones || [];

function distanceM(lat1, lon1, lat2, lon2) {
    const r = 6371000, rad = Math.PI / 180;
    const a = Math.sin((lat2 - lat1) * rad / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin((lon2 - lon1) * rad / 2) ** 2;
    return 2 * r * Math.asin(Math.min(1, Math.sqrt(a)));
}
const fmtKm = m => missing(m) ? '–' : m < 1000 ? `${num(m)} m` : `${num(m / 1000, m < 10000 ? 1 : 0)} km`;
const fmtHour = h => missing(h) ? '–' : `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60) % 60).padStart(2, '0')}`;
const zoneName = state => { if (state === 'home') return 'Home'; if (state === 'not_home') return 'Away'; return state; };

// The Wi-Fi tracker of the same phone: what you picked, else one with the same name
const normName = text => String(text || '').toLowerCase().replace(/\(app\)|\bapp\b|[^a-z0-9]+/g, '');
function wifiTwin(h) {
    const picked = meta(h.key).same_as;
    if (picked && hostByKey(picked)) return {host: hostByKey(picked), auto: false};
    const want = normName(h.name);
    const twin = want ? networkHosts().find(o => normName(o.name) === want || normName(o.host_name) === want) : null;
    return {host: twin || null, auto: true};
}

function gpsTracker(h) { return h.trackers.find(t => t.gps) || h.trackers[0]; }

function renderMobile() {
    const el = document.getElementById('tab-mobile');
    const phones = gpsHosts();
    if (!phones.length) {
        el.innerHTML = '<div class="panel"><div class="empty-state">No GPS trackers in Home Assistant. Install the Home Assistant app on a phone and allow location, and it shows up here.</div></div>';
        return;
    }
    if (!phones.some(h => h.key === mobState.key)) mobState.key = phones[0].key;
    el.innerHTML = `
        <div class="mob-layout">
            <div class="mob-list"></div>
            <div class="mob-main">
                <div class="filters">
                    ${chipsHtml('mob-days', MOB_DAYS, String(mobState.days), 'Period')}
                    ${chipsHtml('mob-view', [{key: 'track', label: 'Track'}, {key: 'heat', label: 'Heatmap', title: 'Where it spends its time: the longer it stays, the warmer'}], mobState.view, 'Show')}
                    <span class="range-text" id="mob-range"></span>
                </div>
                <div class="panel mob-map-panel"><div id="mob-map" class="mob-map"></div></div>
                <div id="mob-body"><div class="empty-state">Reading the recorder…</div></div>
            </div>
        </div>`;
    el.querySelector('.mob-list').onclick = (e) => {
        const b = e.target.closest('[data-key]');
        if (!b) return;
        mobState.key = b.dataset.key;
        store('mob_key', mobState.key);
        mobState.fitted = '';
        renderMobile();
    };
    bindChips('mob-days', key => { mobState.days = Number(key); store('mob_days', key); mobState.fitted = ''; mobLoad(); });
    bindChips('mob-view', key => { mobState.view = key; store('mob_view', key); mobDrawMap(); });
    renderMobileList();
    mobMountMap();
    mobLoad();
}

function mobMountMap() {
    const slot = document.getElementById('mob-map');
    if (!mobState.map) {
        const div = document.createElement('div');
        div.className = 'mob-map-inner';
        mobState.map = L.map(div, {zoomControl: true, attributionControl: true}).setView([52.1, 5.1], 11);
        L.tileLayer('api/map-tiles/{z}/{x}/{y}.png', {
            attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors', maxZoom: 19,
        }).addTo(mobState.map);
        mobState.layers = L.layerGroup().addTo(mobState.map);
        mobState.mapEl = div;
    }
    slot.appendChild(mobState.mapEl);
    setTimeout(() => mobState.map.invalidateSize(), 0);
}

async function mobLoad() {
    const h = hostByKey(mobState.key);
    if (!h) return;
    const twin = wifiTwin(h).host;
    const want = `${mobState.key}|${mobState.days}|${twin ? twin.key : ''}`;
    const params = new URLSearchParams({entity_id: gpsTracker(h).entity_id, days: String(mobState.days)});
    if (twin) params.set('wifi', twin.key);
    try {
        const data = await api('mobile?' + params);
        if (activeTab !== 'mobile' || want !== `${mobState.key}|${mobState.days}|${(wifiTwin(hostByKey(mobState.key)).host || {}).key || ''}`) return;
        mobState.data = data;
        mobState.loadedFor = want;
        mobDrawMap();
        mobBody();
    } catch (e) {
        const body = document.getElementById('mob-body');
        if (body) body.innerHTML = `<div class="setup-hint bad"><span>${escapeHtml(e.message)}</span></div>`;
    }
}

function mobDrawMap() {
    const d = mobState.data;
    const map = mobState.map;
    if (!map || !d) return;
    const group = mobState.layers;
    group.clearLayers();
    if (mobState.heat) { map.removeLayer(mobState.heat); mobState.heat = null; }
    const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    zones().forEach(z => {
        L.circle([z.lat, z.lon], {radius: z.radius, color: z.state === 'home' ? css('--success-color') : css('--series-1'), weight: 1.5, fillOpacity: 0.08, interactive: false}).addTo(group);
        L.marker([z.lat, z.lon], {icon: L.divIcon({className: '', html: `<div class="zone-label">${escapeHtml(z.name)}</div>`, iconSize: null}), interactive: false}).addTo(group);
    });
    const line = d.track.map(p => [p.lat, p.lon]);
    if (mobState.view === 'heat') {
        const max = Math.max(1, ...d.heat.map(p => p[2]));
        // Hours, squashed so a night at home doesn't drown out the rest
        const points = d.heat.map(p => [p[0], p[1], Math.sqrt(p[2] / max)]);
        // maxZoom low: Leaflet.heat fades points out below it, and a stay is one spot at any zoom
        mobState.heat = L.heatLayer(points, {radius: 22, blur: 18, maxZoom: 4, minOpacity: 0.3, max: 1}).addTo(map);
    } else if (line.length > 1) {
        L.polyline(line, {weight: 7, className: 'trk-casing', interactive: false}).addTo(group);
        L.polyline(line, {weight: 3.5, className: 'trk-line', interactive: false}).addTo(group);
        d.track.filter((p, i) => i % Math.max(1, Math.floor(d.track.length / 300)) === 0).forEach(p => {
            L.circleMarker([p.lat, p.lon], {radius: 3, className: 'trk-dot', weight: 0}).bindTooltip(`${fmtDateTime(p.ts)} · ${zoneName(p.state)}${p.acc ? ` · ±${p.acc} m` : ''}`).addTo(group);
        });
    }
    const last = d.track[d.track.length - 1];
    if (last) L.marker([last.lat, last.lon], {icon: L.divIcon({className: '', html: '<div class="pos-pin"></div>', iconSize: [18, 18], iconAnchor: [9, 9]})})
        .bindTooltip(`Now: ${zoneName(last.state)} · ${fmtAgo(last.ts)}`).addTo(group);
    const want = `${mobState.key}|${mobState.days}`;
    if (mobState.fitted !== want && line.length) {
        map.fitBounds(L.latLngBounds(line).pad(0.15), {maxZoom: 15});
        mobState.fitted = want;
    }
    const range = document.getElementById('mob-range');
    if (range) range.textContent = `${fmtDateTime(d.since)} – ${fmtDateTime(d.until)} · ${num(d.track.length)} positions`;
}

function mobBody() {
    const el = document.getElementById('mob-body');
    const d = mobState.data;
    const h = hostByKey(mobState.key);
    if (!el || !d || !h) return;
    const twin = wifiTwin(h);
    const battery = d.battery.length ? d.battery[d.battery.length - 1][1] : gpsTracker(h).battery;
    const states = [...new Set(d.days.flatMap(x => Object.keys(x.states)))].sort((a, b) => (a === 'home' ? -1 : b === 'home' ? 1 : a === 'not_home' ? 1 : b === 'not_home' ? -1 : a.localeCompare(b)));
    const hb = d.habits;
    const w = d.wifi;
    el.innerHTML = `
        <div class="tiles kpi-tiles">
            ${tile('At home', plainPct(d.home_share, 0), 'of the time')}
            ${tile('Trips', num(d.trip_count), 'times it left home')}
            ${tile('Travelled', fmtKm(d.distance), 'by its positions')}
            ${tile('Furthest', d.furthest ? fmtKm(d.furthest.metres) : '–', d.furthest ? fmtDateTime(d.furthest.ts) : 'from home')}
            ${tile('Battery', missing(battery) ? '–' : `${num(battery)}<small>%</small>`, missing(battery) ? 'not reported' : 'last reported', !missing(battery) && battery < 20 ? 'warn' : '')}
        </div>
        <div class="dash-grid">
            <div class="dash-stack">
                ${panel('Habits', `the median over ${mobState.days === 1 ? 'one day: pick 7 or 30 days' : `${mobState.days} days`}`, `<div class="facts">
                    <div class="fact"><div class="l">Weekdays: leaves</div><div class="v">${fmtHour(hb.weekdays.leaves)}</div></div>
                    <div class="fact"><div class="l">Weekdays: home</div><div class="v">${fmtHour(hb.weekdays.returns)}</div></div>
                    <div class="fact"><div class="l">Weekend: leaves</div><div class="v">${fmtHour(hb.weekend.leaves)}</div></div>
                    <div class="fact"><div class="l">Weekend: home</div><div class="v">${fmtHour(hb.weekend.returns)}</div></div></div>`)}
                ${panel('Per day', 'hours in each zone', d.days.length ? `<div class="day-bars">${d.days.map(day => {
                    const total = Object.values(day.states).reduce((a, b) => a + b, 0) || 1;
                    return `<div class="day-bar"><span class="mono muted">${escapeHtml(new Date(day.day + 'T12:00').toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'}))}</span>
                        <div class="stack">${states.filter(s => day.states[s]).map((s, i) => `<i style="width:${(day.states[s] / total * 100).toFixed(2)}%;background:${zoneColor(s, states.indexOf(s) - 1)}" title="${attr(zoneName(s))}: ${attr(fmtDuration(day.states[s]))}"></i>`).join('')}</div></div>`;
                }).join('')}</div><div class="legend">${states.map((s, i) => `<span><i style="background:${zoneColor(s, i - 1)}"></i>${escapeHtml(zoneName(s))}</span>`).join('')}</div>` : '<div class="empty-state">No history in this period</div>')}
                ${panel('Battery', '', '<div id="mob-battery" class="chart"></div>')}
            </div>
            <div class="dash-stack">
                ${panel('Wi-Fi and GPS', d.wifi && d.wifi.since > d.since + 3600 ? `since ${escapeHtml(fmtDateTime(d.wifi.since))}, as far back as the Wi-Fi history goes` : 'does the phone\'s Wi-Fi tracker agree with its GPS?', `
                    <div class="form-field"><label>Its Wi-Fi tracker</label><select class="select" id="mob-twin"><option value="">${twin.auto && twin.host ? `Same name: ${escapeHtml(hostName(twin.host))}` : 'None'}</option>
                        ${networkHosts().filter(o => ['phone', 'tablet', 'watch', 'laptop'].includes(kindOf(o)) || o.random_mac).sort((a, b) => hostName(a).localeCompare(hostName(b))).map(o => `<option value="${attr(o.key)}"${!twin.auto && twin.host && twin.host.key === o.key ? ' selected' : ''}>${escapeHtml(hostName(o))}</option>`).join('')}</select></div>
                    ${w ? `<div class="facts" style="margin-top:12px">
                        <div class="fact"><div class="l">Agree</div><div class="v ${w.agreement !== null && w.agreement < 0.8 ? 'warn' : 'ok'}">${plainPct(w.agreement, 0)}</div></div>
                        <div class="fact"><div class="l">Home, not on Wi-Fi</div><div class="v">${escapeHtml(fmtDuration(w.home_not_on_wifi))}</div></div>
                        <div class="fact"><div class="l">On Wi-Fi, GPS away</div><div class="v">${escapeHtml(fmtDuration(w.on_wifi_not_home))}</div></div>
                        <div class="fact"><div class="l">Home by GPS</div><div class="v">${escapeHtml(fmtDuration(w.home_seconds))}</div></div></div>
                        <p class="panel-sub" style="margin-top:10px">${w.agreement !== null && w.agreement < 0.8
                            ? 'The phone is home but often not on the Wi-Fi: it may switch Wi-Fi off when asleep, use mobile data, or have a new private (random) address that is another device here now.'
                            : w.on_wifi_not_home > 1800 ? 'It is on the Wi-Fi while its GPS says away: the GPS updates late, or the home zone is too small.' : 'Wi-Fi and GPS agree.'}</p>` : '<p class="panel-sub" style="margin-top:10px">Pick the Wi-Fi tracker of this phone to compare them.</p>'}`)}
                ${panel('Trips', `the latest ${Math.min(d.trips.length, 25)} of ${d.trip_count}`, d.trips.length ? `<div class="table-wrap">${table([{label: 'Left'}, {label: 'Back'}, {label: 'Away', n: true}, {label: 'Where'}],
                    d.trips.slice(-25).reverse().map(t => `<tr>${td(escapeHtml(fmtDateTime(t.left)))}${td(t.back ? escapeHtml(fmtTime(t.back)) : '<span class="muted">still away</span>')}${td(t.seconds ? escapeHtml(fmtDuration(t.seconds)) : '–', 'n')}${td(t.zones.length ? t.zones.map(z => badge(z)).join(' ') : '<span class="muted">no zone</span>')}</tr>`), {cls: 'compact-rows'})}</div>` : '<div class="empty-state">It stayed home</div>')}
            </div>
        </div>`;
    document.getElementById('mob-twin').addEventListener('change', (e) => {
        setMeta(h.key, 'same_as', e.target.value);
        mobLoad();
    });
    if (d.battery.length > 1) {
        lineChart(document.getElementById('mob-battery'), {
            x: d.battery.map(b => b[0]), height: 140, min: 0, max: 100,
            series: [{name: 'Battery', color: 'var(--series-3)', values: d.battery.map(b => b[1])}],
            xTicks: timeAxisTicks(d.battery[0][0], d.battery[d.battery.length - 1][0], 5),
            formatX: t => fmtDateTime(t), formatY: v => `${num(v)}%`, formatTick: v => `${num(v)}%`,
        });
    } else {
        document.getElementById('mob-battery').innerHTML = '<div class="empty-state">The app doesn\'t report the battery</div>';
    }
}

onTab('mobile', () => renderMobile(), () => {
    // The list of phones; the map and the numbers once a minute
    if (document.getElementById('modal-root').children.length) return;
    const list = document.querySelector('#tab-mobile .mob-list');
    if (!list) return;
    renderMobileList();
    if (!mobState.lastLoad || Date.now() - mobState.lastLoad > 60000) { mobState.lastLoad = Date.now(); mobLoad(); }
});
function renderMobileList() {
    const list = document.querySelector('#tab-mobile .mob-list');
    const keep = mobState.key;
    const tmp = document.createElement('div');
    const phones = gpsHosts();
    const home = zones().find(z => z.state === 'home');
    tmp.innerHTML = phones.map(p => {
        const t = gpsTracker(p);
        const away = home && !missing(t.latitude) ? distanceM(home.lat, home.lon, t.latitude, t.longitude) : null;
        const twin = wifiTwin(p).host;
        return `<button type="button" class="mob-card${p.key === keep ? ' active' : ''}" data-key="${attr(p.key)}">
            ${hostIconHtml(p, 'small')}<span class="mob-card-text"><b>${escapeHtml(hostName(p))}</b>
            <span class="muted">${escapeHtml(zoneName(t.state))}${away !== null && t.state !== 'home' ? ` · ${fmtKm(away)} from home` : ''}${missing(t.battery) ? '' : ` · ${num(t.battery)}%`}</span>
            <span class="muted">${escapeHtml(fmtAgo(t.updated || t.last_changed))}${twin ? ` · Wi-Fi ${twin.up ? '<span class="pos">on</span>' : 'off'}` : ''}</span></span></button>`;
    }).join('');
    if (list.innerHTML !== tmp.innerHTML) list.innerHTML = tmp.innerHTML;
}
