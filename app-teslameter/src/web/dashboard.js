// TeslaMeter dashboard - Dashboard view: the car's live state, its position and recent activity.

const ICON_DRIVE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7.5a3.5 3.5 0 0 0 0-7h-7a3.5 3.5 0 0 1 0-7H16"/></svg>';
const ICON_CHARGE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>';
const CAR_ARROW = '<svg viewBox="0 0 24 24"><path d="M12 2 4.5 20.5 12 17l7.5 3.5z"/></svg>';

let followCar = localStorage.getItem(LS + 'follow') !== 'false';
let carMarker = null;
let liveTrack = null;
let liveTrackDrive = null;
let liveTrackAfter = 0;
let liveTrackBusy = false;
let overview = null;
let overviewPeriod = localStorage.getItem(LS + 'overview_period') || 'today';
let overviewLoadedAt = 0;
let lastDashState = null;
let lastLiveHtml = '';
let firstFix = true;
const dayTracks = L.layerGroup().addTo(mapLayers.dashboard);

function toggleFollow() {
    followCar = !followCar;
    localStorage.setItem(LS + 'follow', followCar);
    initFollowToggle();
    if (followCar) focusCar(true);
}

function initFollowToggle() {
    const button = document.getElementById('follow-toggle');
    button.classList.toggle('active', followCar);
    button.title = followCar ? 'The dashboard map follows the car - click to stop' : 'Keep the car in view on the dashboard map';
}

function focusCar(zoom) {
    const car = currentCar();
    if (!car || missing(car.lat) || activeTab !== 'dashboard') return;
    if (zoom) map.setView([car.lat, car.lon], Math.max(map.getZoom(), 14));
    else map.panTo([car.lat, car.lon]);
}

function stateLabel(car) {
    return `<span class="state ${escapeHtml(car.state)}">${escapeHtml(car.state)}</span>`;
}

function fact(label, valueHtml, cls) {
    return `<div class="fact"><div class="l">${label}</div><div class="v ${cls || ''}">${valueHtml}</div></div>`;
}

// Join fact() cells for the 2-column grid, padding an odd count with a blank
// cell so the grid's divider background never shows as a grey square.
function factsGrid(items) {
    const cells = items.filter(Boolean);
    if (cells.length % 2) cells.push('<div class="fact" aria-hidden="true"></div>');
    return cells.join('');
}

// "MidnightGrey" / "carbon_fiber" -> "Midnight Grey" / "Carbon Fiber"
function prettyWord(v) {
    return String(v).replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/\b\w/g, c => c.toUpperCase()).trim();
}

const PORT_TYPES = {US: 'NACS (US)', CCS: 'CCS', GB: 'GB/T', EU: 'Type 2 (EU)'};
function portTypeName(v) {
    return PORT_TYPES[String(v).toUpperCase()] || prettyWord(v);
}

function tiresText(car) {
    const t = car.tpms || {};
    if (missing(t.fl)) return '–';
    const p = wheel => num(conv.press(t[wheel]), imperial() ? 0 : 1);
    return `${p('fl')} ${p('fr')} <small>/</small> ${p('rl')} ${p('rr')} <small>${unit.press()}</small>`;
}

function openingsText(car) {
    const open = [];
    if (car.doors_open) open.push('door');
    if (car.windows_open) open.push('window');
    if (car.trunk_open) open.push('trunk');
    if (car.frunk_open) open.push('frunk');
    return open.length ? {text: open.join(', ') + ' open', warn: true} : {text: 'Closed', warn: false};
}

// The part of the panel that depends on what the car is doing right now
function contextBlock(car) {
    if (car.state === 'driving') {
        const destination = car.dest ? `<div class="live-note">→ <b>${escapeHtml(car.dest)}</b>`
            + (missing(car.dest_minutes) ? '' : ` · ${fmtDuration(car.dest_minutes * 60)}`)
            + (missing(car.dest_km) ? '' : ` · ${fmtDist(car.dest_km)}`)
            + (missing(car.dest_soc) ? '' : ` · arriving with ${fmtPct(car.dest_soc)}`) + '</div>' : '';
        return `<div class="block"><div class="block-title"><span>Driving</span><span>${car.streaming ? 'live stream' : ''}</span></div>
            <div class="facts three">
                ${fact('Speed', withUnit(num(conv.dist(car.speed_kmh)), unit.speed()), 'big')}
                ${fact('Power', withUnit(num(car.power_kw), 'kW'), 'big')}
                ${fact('Elevation', missing(car.elevation_m) ? '–' : withUnit(num(conv.elev(car.elevation_m)), unit.elev()), 'big')}
            </div>${destination}</div>`;
    }
    if (car.state === 'charging') {
        // Some cars report a three-phase connection as 2 phases
        const phases = car.charger_phases === 2 ? 3 : car.charger_phases;
        const electrical = car.fast_charger ? escapeHtml(car.charger_brand || 'DC fast')
            : `${phases > 1 ? phases + ' × ' : ''}${num(car.charger_current)} A <small>${num(car.charger_voltage)} V</small>`;
        return `<div class="block"><div class="block-title"><span>Charging</span><span>${car.fast_charger ? 'DC' : 'AC'}</span></div>
            <div class="facts three">
                ${fact('Power', withUnit(num(car.charger_power_kw, 1), 'kW'), 'big')}
                ${fact('Added', withUnit(num(car.charge_energy_added_kwh, 1), 'kWh'), 'big')}
                ${fact('Time left', missing(car.minutes_to_full) ? '–' : fmtDuration(car.minutes_to_full * 60), 'big')}
                ${fact('Charger', electrical)}
                ${fact('Rate', missing(car.charge_rate_kmh) ? '–' : withUnit(num(conv.dist(car.charge_rate_kmh)), unit.speed()))}
                ${fact('Limit', fmtPct(car.charge_limit_soc))}
            </div></div>`;
    }
    if (car.state === 'asleep' || car.state === 'offline') {
        const text = car.state === 'asleep'
            ? 'The car is asleep. The values below are from when it was last awake; it is not woken up to read new ones.'
            : 'The car is offline (no connection). The values below are the last known ones.';
        return `<div class="block"><div class="live-note" style="margin:0 0 10px">${text}</div>
            <button type="button" class="btn" onclick="wakeCar()">Wake up</button></div>`;
    }
    if (car.state === 'updating') {
        return `<div class="block"><div class="live-note" style="margin:0">Installing software ${escapeHtml(car.update_version || '')}`
            + (missing(car.update_progress) ? '' : ` · ${fmtPct(car.update_progress)}`) + '</div></div>';
    }
    if (car.plugged_in && car.scheduled_charging_start) {
        return `<div class="block"><div class="live-note" style="margin:0">Plugged in · charging scheduled for <b>${fmtDateTime(car.scheduled_charging_start)}</b></div></div>`;
    }
    return '';
}

function renderCarPanel(car) {
    const sub = carSubtitle(car);
    const soc = missing(car.soc) ? null : car.soc;
    const meterClass = car.state === 'charging' ? 'charging' : (soc !== null && soc < 20 ? 'low' : '');
    const openings = openingsText(car);
    const software = car.version ? escapeHtml(car.version) + (car.update_status === 'available' || car.update_status === 'scheduled'
        ? ` <small>→ ${escapeHtml(car.update_version || 'update')}</small>` : '') : '–';
    const climate = car.climate_on ? `On${missing(car.target_temp) ? '' : ` <small>${fmtTemp(car.target_temp)}</small>`}` : 'Off';
    return `
        <div class="block"><div class="car-head">
            <div class="ch-left"><h2>${escapeHtml(car.name || 'Tesla')}</h2><div class="sub">${escapeHtml(sub)}</div></div>
            <div class="ch-right">
                <div class="ch-status">${stateLabel(car)}<span class="since">since ${fmtDateTime(car.state_since)}</span></div>
                ${carVisual(car, 54)}
            </div>
        </div></div>
        <div class="block">
            <div class="battery">
                <span class="pct">${soc === null ? '–' : soc}<small>%</small></span>
                <span class="range">${fmtDist(car.range_km, 0)} <small>rated</small></span>
                <span class="est">${missing(car.est_range_km) ? '' : 'est. ' + fmtDist(car.est_range_km, 0)}${missing(car.charge_limit_soc) ? '' : `<br>limit ${fmtPct(car.charge_limit_soc)}`}</span>
            </div>
            <div class="meter ${meterClass}" role="meter" aria-valuenow="${soc || 0}" aria-valuemin="0" aria-valuemax="100" aria-label="Battery">
                <i style="width:${soc || 0}%"></i>
                ${missing(car.charge_limit_soc) ? '' : `<span class="limit" style="left:calc(${car.charge_limit_soc}% - 1px)" title="Charge limit"></span>`}
            </div>
        </div>
        ${contextBlock(car)}
        <div class="block"><div class="block-title"><span>Vehicle</span><span>${car.last_seen ? 'updated ' + fmtTime(car.last_seen) : ''}</span></div>
            <div class="facts">${factsGrid([
                fact('Odometer', missing(car.odometer_km) ? '–' : withUnit(num(conv.dist(car.odometer_km)), unit.dist())),
                fact('Software', software),
                car.usable_soc != null && car.usable_soc !== car.soc ? fact('Usable charge', fmtPct(car.usable_soc)) : '',
                fact('Inside', fmtTemp(car.inside_temp)),
                fact('Outside', fmtTemp(car.outside_temp)),
                fact('Climate', climate),
                fact('Sentry mode', car.sentry ? 'On' : 'Off'),
                fact('Lock', car.locked === false ? 'Unlocked' : car.locked ? 'Locked' : '–', car.locked === false && !car.user_present ? 'warn' : ''),
                fact('Doors & windows', escapeHtml(openings.text), openings.warn ? 'warn' : ''),
                fact('Tires', tiresText(car)),
                fact('Charge port', car.plugged_in ? 'Plugged in' : 'Unplugged'),
                car.charge_port_type ? fact('Charge port type', escapeHtml(portTypeName(car.charge_port_type))) : '',
                car.roof_color && car.roof_color !== 'None' ? fact('Roof', escapeHtml(prettyWord(car.roof_color))) : '',
                car.exterior_trim && car.exterior_trim !== 'None' ? fact('Exterior trim', escapeHtml(prettyWord(car.exterior_trim))) : '',
                car.air_suspension === true ? fact('Suspension', 'Air') : '',
            ])}</div>
        </div>`;
}

function renderOverview() {
    const el = document.getElementById('dash-overview');
    if (!el || !overview) return;
    const totals = overview[overviewPeriod] || overview.today;
    const periodTabs = [['today', 'Today'], ['week', '7 days'], ['month', '30 days']].map(([key, label]) =>
        `<button type="button" class="tab chip${key === overviewPeriod ? ' active' : ''}" onclick="setOverviewPeriod('${key}')">${label}</button>`).join('');
    const rows = overview.activity.map(item => {
        const drive = item.type === 'drive';
        const title = drive
            ? `${escapeHtml(item.place_from || 'Unknown')} <span style="color:var(--muted-text-color)">→</span> ${escapeHtml(item.place || 'Unknown')}`
            : `Charged${item.place ? ' at ' + escapeHtml(item.place) : ''}`;
        const value = drive ? fmtDist(item.distance_km) : '+' + fmtKwh(item.energy_kwh);
        const detail = drive ? fmtKwh(item.energy_kwh) : `${fmtPct(item.start_soc)} → ${fmtPct(item.end_soc)}`;
        return `<button type="button" class="activity-row" onclick="${drive ? 'openTrip' : 'openCharge'}(${item.id})">
            <span class="ico">${drive ? ICON_DRIVE : ICON_CHARGE}</span>
            <span class="title">${title}</span><span class="value">${value}</span>
            <span class="meta">${fmtDateTime(item.start_ts)} · ${fmtDuration(item.duration_s)}</span><span class="meta right">${detail}</span>
        </button>`;
    }).join('');
    el.innerHTML = `
        <div class="block"><div class="block-title"><span>Totals</span><div class="tabs">${periodTabs}</div></div>
            <div class="facts three">
                ${fact('Distance', withUnit(num(conv.dist(totals.distance_km), 1), unit.dist()))}
                ${fact('Trips', num(totals.drives))}
                ${fact('Driving time', fmtDuration(totals.duration_s))}
                ${fact('Energy used', withUnit(num(totals.energy_kwh, 1), 'kWh'))}
                ${fact('Consumption', missing(totals.consumption_wh_km) ? '–' : withUnit(num(conv.cons(totals.consumption_wh_km)), unit.cons()))}
                ${fact('Charged', withUnit(num(totals.added_kwh, 1), 'kWh'))}
            </div>
        </div>
        <div class="block" style="padding-bottom:6px;border-bottom:0"><div class="block-title" style="margin-bottom:0"><span>Recent activity</span></div></div>
        <div class="activity">${rows || '<div class="empty-state">Nothing recorded yet</div>'}</div>`;
}

function setOverviewPeriod(period) {
    overviewPeriod = period;
    localStorage.setItem(LS + 'overview_period', period);
    renderOverview();
}

async function loadOverview() {
    const car = currentCar();
    if (!car) return;
    overviewLoadedAt = Date.now();
    try {
        overview = await api(`cars/${car.id}/overview`);
    } catch (e) {
        return;
    }
    renderOverview();
    dayTracks.clearLayers();
    overview.tracks.forEach(track => {
        // The drive in progress is drawn (and extended) separately
        if (track.id !== car.drive_id && track.points.length > 1) {
            L.polyline(track.points, {weight: 3, opacity: 0.7, className: 'trk-plain', interactive: false}).addTo(dayTracks);
        }
    });
}

async function extendLiveTrack(car) {
    if (liveTrackBusy) return;
    if (liveTrackDrive !== car.drive_id) {
        if (liveTrack) mapLayers.dashboard.removeLayer(liveTrack);
        liveTrack = L.polyline([], {weight: 4, className: 'trk-live', interactive: false}).addTo(mapLayers.dashboard);
        liveTrackDrive = car.drive_id;
        liveTrackAfter = 0;
    }
    liveTrackBusy = true;
    try {
        const data = await api(`cars/${car.id}/track?after=${liveTrackAfter}`);
        if (data.drive_id === liveTrackDrive) {
            data.points.forEach(p => { liveTrack.addLatLng([p[0], p[1]]); liveTrackAfter = Math.max(liveTrackAfter, p[2]); });
        }
    } catch (e) { /* next update tries again */ }
    liveTrackBusy = false;
}

function renderDashboard() {
    const car = currentCar();
    const hint = document.getElementById('setup-hint');
    hint.hidden = app.auth.signed_in;
    if (!app.auth.signed_in) {
        document.getElementById('setup-hint-text').textContent = app.auth.error
            || (car ? 'Signed out of Tesla - sign in again to continue logging.' : 'Sign in to your Tesla account to start logging your car.');
    }
    const scroll = document.getElementById('dash-scroll');
    const kpis = document.getElementById('dash-kpis');
    if (!car) {
        kpis.innerHTML = '';
        const text = app.auth.signed_in ? (app.accountError || 'Looking for the cars in your Tesla account…') : 'No car yet';
        scroll.innerHTML = `<div class="empty-state">${escapeHtml(text)}</div>`;
        lastLiveHtml = '';
        return;
    }
    if (!document.getElementById('dash-live')) {
        scroll.innerHTML = '<div id="dash-live"></div><div id="dash-overview"></div>';
        lastLiveHtml = '';
        renderOverview();
    }
    const html = renderCarPanel(car);
    if (html !== lastLiveHtml) {
        document.getElementById('dash-live').innerHTML = html;
        lastLiveHtml = html;
    }
    kpis.innerHTML = `
        <div class="kpi"><div class="v">${missing(car.soc) ? '–' : car.soc}<small>%</small></div><div class="l">Battery</div></div>
        <div class="kpi"><div class="v">${missing(car.range_km) ? '–' : num(conv.dist(car.range_km))}<small>${unit.dist()}</small></div><div class="l">Range</div></div>
        ${car.state === 'driving' ? `<div class="kpi"><div class="v">${num(conv.dist(car.speed_kmh))}<small>${unit.speed()}</small></div><div class="l">Speed</div></div>` : ''}
        ${car.state === 'charging' ? `<div class="kpi"><div class="v">${num(car.charger_power_kw, 1)}<small>kW</small></div><div class="l">Charging</div></div>` : ''}
        <div class="kpi"><div class="v" style="font-size:15px;line-height:27px">${stateLabel(car)}</div><div class="l">${fmtAgo(car.last_seen)}</div></div>`;

    // The car on the map
    if (!missing(car.lat) && !missing(car.lon)) {
        const icon = L.divIcon({
            className: `car-marker ${car.state}`,
            html: `<div class="car-arrow">${CAR_ARROW.replace('<svg', `<svg style="transform:rotate(${car.heading || 0}deg)"`)}</div>`,
            iconSize: [30, 30], iconAnchor: [15, 15],
        });
        if (!carMarker) {
            carMarker = L.marker([car.lat, car.lon], {icon, zIndexOffset: 1000, interactive: false}).addTo(mapLayers.dashboard);
        } else {
            carMarker.setLatLng([car.lat, car.lon]);
            carMarker.setIcon(icon);
        }
        if (activeTab === 'dashboard' && (firstFix || followCar)) {
            focusCar(firstFix);
            firstFix = false;
        }
    }
    if (car.drive_id) {
        extendLiveTrack(car);
    } else if (liveTrack) {
        mapLayers.dashboard.removeLayer(liveTrack);
        liveTrack = null;
        liveTrackDrive = null;
    }

    // A drive or charge that just ended changes the totals and the activity list
    const stateKey = `${car.id}:${car.state}`;
    if (activeTab === 'dashboard' && (stateKey !== lastDashState || Date.now() - overviewLoadedAt > 60000)) {
        lastDashState = stateKey;
        loadOverview();
    }
}

async function wakeCar() {
    const car = currentCar();
    if (!car) return;
    try {
        await api(`cars/${car.id}/wake`, {method: 'POST'});
        toast('Wake-up sent - the car needs up to a minute', 'success');
    } catch (e) {
        toast(e.message, 'error');
    }
}

onTab('dashboard', () => {
    firstFix = true;
    lastDashState = null;
    renderDashboard();
});
onLive(renderDashboard);
