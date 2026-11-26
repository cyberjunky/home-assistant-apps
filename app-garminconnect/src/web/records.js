// Garmin Connect dashboard - Records: sport totals, Eddington numbers, heart rate zones, the map of
// everywhere you've been, personal records, gear, badges and Garmin's lifetime totals.

const recordsPeriod = periodPicker('records', 'all', () => loadRecords());
let recordsBucket = localStorage.getItem(LS + 'records_bucket') || 'month';
let heatMap = null;
let recordsLoadedAt = 0;

function renderSportTiles(s) {
    const total = s.types.reduce((a, t) => ({d: a.d + t.distance_m, h: a.h + t.duration_s, c: a.c + t.climb_m, k: a.k + t.calories}), {d: 0, h: 0, c: 0, k: 0});
    const rec = s.records || {};
    document.getElementById('records-tiles').innerHTML = [
        tile('Activities', num(s.count), `${num(s.types.length)} kinds of sport`),
        tile('Distance', withUnit(num(toDist(total.d / 1000)), distUnit())),
        tile('Time', fmtDuration(total.h)),
        tile('Climbed', fmtElev(total.c)),
        tile('Longest', rec.longest && rec.longest.distance_m ? fmtMeters(rec.longest.distance_m) : '–', rec.longest ? `<a href="#" onclick="openActivity(${rec.longest.id});return false">${escapeHtml(rec.longest.name || typeName(rec.longest.type))}</a>` : ''),
        tile('Hardest', rec.hardest && rec.hardest.training_load ? withUnit(num(rec.hardest.training_load), 'load') : '–', rec.hardest ? `<a href="#" onclick="openActivity(${rec.hardest.id});return false">${escapeHtml(rec.hardest.name || typeName(rec.hardest.type))}</a>` : ''),
    ].join('');
}

function renderSportHours(s, bucket) {
    const families = Object.keys(s.hours);
    const el = document.getElementById('sport-hours');
    barChart(el, {
        data: s.buckets.map((b, i) => ({label: bucketLabel(b, bucket), tipTitle: bucketTitle(b, bucket), values: families.map(f => s.hours[f][i])})),
        series: families.map(f => ({name: SPORT_NAMES[f], color: SPORT_COLORS[f]})),
        height: 200, format: v => `${num(v, 1)} h`, empty: 'No activities in this period',
    });
    document.getElementById('sport-legend').innerHTML = families.map(f => `<span><i style="background:${SPORT_COLORS[f]}"></i>${SPORT_NAMES[f]}</span>`).join('');
}

function renderSportTypes(types) {
    const el = document.getElementById('sport-types');
    if (!types.length) { el.innerHTML = '<div class="empty-state">No activities in this period</div>'; return; }
    const max = Math.max(...types.map(t => t.duration_s)) || 1;
    el.innerHTML = types.slice(0, 12).map(t => `<div class="bar-row"><span class="name">${escapeHtml(typeName(t.type))}</span>
        <span class="track"><i style="width:${(t.duration_s / max * 100).toFixed(1)}%;background:${SPORT_COLORS[t.family]}"></i></span>
        <span class="count"><b>${num(t.count)}×</b> ${fmtDuration(t.duration_s)}${t.distance_m ? ' · ' + fmtMeters(t.distance_m) : ''}</span></div>`).join('');
}

function renderEddington(e) {
    const el = document.getElementById('eddington');
    const families = Object.keys(e || {});
    if (!families.length) { el.innerHTML = '<div class="empty-state">No distance sports in this period</div>'; return; }
    el.innerHTML = `<div class="facts three">${families.map(f => `<div class="fact"><div class="l">${SPORT_NAMES[f]}</div><div class="v big">${num(e[f])}</div></div>`).join('')}</div>
        <p class="panel-sub" style="margin-top:10px">An Eddington number of ${num(Math.max(...families.map(f => e[f])))} means that many days of at least that many kilometres. The next one up needs ${num(Math.max(...families.map(f => e[f])) + 1)} days of ${num(Math.max(...families.map(f => e[f])) + 1)} km: each step is harder than the last. Counted in kilometres, also with imperial units.</p>`;
}

let heatRetry = null;
let recordsHeat = null;
async function renderHeatMap(range, retry) {
    clearTimeout(heatRetry);
    if (!heatMap) {
        heatMap = L.map('heat-map', {zoomControl: true, scrollWheelZoom: false}).setView([52.2, 5.3], 8);
        tileLayer().addTo(heatMap);
        recordsHeat = heatOn(heatMap);
    }
    requestAnimationFrame(() => heatMap.invalidateSize());
    const data = await recordsHeat.show({start: range.start, end: range.end, family: ''});
    const points = data.points || [];
    document.getElementById('heat-hint').textContent = data.pending ? `mapping ${num(data.pending)} tracks…`
        : points.length ? `${num(points.length)} places of about 100 m · zoom in for the tracks` : 'tracks appear as they are fetched';
    if (data.pending) heatRetry = setTimeout(() => renderHeatMap(range, true), 5000);
    // A new period is shown whole; the refreshes while tracks are mapped leave the view alone
    if (points.length && !retry) {
        // Centre on where most of it happened, not on the one holiday far away
        const lats = points.map(p => p[0]).sort((a, b) => a - b), lons = points.map(p => p[1]).sort((a, b) => a - b);
        const at = q => Math.floor(q * (points.length - 1));
        heatMap.fitBounds([[lats[at(0.03)], lons[at(0.03)]], [lats[at(0.97)], lons[at(0.97)]]], {padding: [20, 20], maxZoom: 13});
    }
}

function renderGear(gear) {
    const el = document.getElementById('gear');
    const items = (gear && gear.data) || [];
    if (!items.length) { el.innerHTML = '<div class="empty-state">No gear in Garmin Connect</div>'; return; }
    el.innerHTML = items.map(g => {
        const used = g.distanceUsedMeters || 0, max = g.maxUsageDistanceMeters || 0;
        const share = max ? Math.min(1, used / max) : 0;
        const warn = share > 0.85;
        return `<div class="bar-row"><span class="name">${escapeHtml(g.name || [g.brand, g.model].filter(Boolean).join(' ') || g.gearType)}${g.status === 'RETIRED' ? ' <span class="badge">retired</span>' : ''}</span>
            <span class="track"><i style="width:${(share * 100).toFixed(1)}%;${warn ? 'background:var(--warning-color)' : ''}"></i></span>
            <span class="count"><b>${fmtMeters(used)}</b>${max ? ' / ' + fmtMeters(max) : ''} · ${num(g.numActivitiesLinked)}×</span></div>`;
    }).join('');
}

function renderPrs(prs) {
    const el = document.getElementById('prs');
    const items = (prs && prs.data) || [];
    // The record types confirmed against Garmin Connect's own page (as in python-garminconnect's demo):
    // 1-6 best times, 7 longest run, 12-14 steps, 15-16 goal streaks. Others show their raw value.
    const TYPES = {1: ['1 km', 't'], 2: ['1 mile', 't'], 3: ['5 km', 't'], 4: ['10 km', 't'], 5: ['Half marathon', 't'], 6: ['Marathon', 't'],
        7: ['Longest run', 'd'], 12: ['Most steps in a day', 'n'], 13: ['Most steps in a week', 'n'], 14: ['Most steps in a month', 'n'],
        15: ['Longest goal streak', 'days'], 16: ['Current goal streak', 'days']};
    if (!items.length) { el.innerHTML = '<div class="empty-state">No personal records from Garmin (yet)</div>'; return; }
    el.innerHTML = items.map(p => {
        const [name, kind] = TYPES[p.typeId] || [`${typeName(p.activityType || 'record')} (type ${p.typeId})`, 'n'];
        const value = kind === 't' ? fmtRaceTime(p.value) : kind === 'd' ? fmtMeters(p.value) : kind === 'e' ? fmtElev(p.value) : kind === 'days' ? `${num(p.value)} days` : num(p.value, p.value % 1 ? 1 : 0);
        const when = (p.actStartDateTimeInGMTFormatted || p.prStartTimeGmtFormatted || '').slice(0, 10);
        const link = p.activityId ? `<a href="#" onclick="openActivity(${Number(p.activityId)});return false">${escapeHtml(p.activityName || 'activity')}</a>` : '';
        return infoRow(name, `${value}${when ? ` · ${fmtDayY(when)}` : ''}${link ? ' · ' + link : ''}`);
    }).join('');
}

function renderBadges(badges) {
    const el = document.getElementById('badges');
    if (!badges.length) { emptyChart(el, 'No badges earned (yet)'); return; }
    // Badges per year, and points
    const per = {};
    badges.forEach(b => { const y = (b.day || '').slice(0, 4); if (y) per[y] = (per[y] || 0) + 1; });
    const years = Object.keys(per).sort();
    const points = badges.reduce((a, b) => a + (b.badgePoints || 0), 0);
    document.getElementById('badges-hint').textContent = `${num(badges.length)} earned · ${num(points)} points`;
    barChart(el, {data: years.map(y => ({label: y, values: [per[y]]})), series: [{name: 'Badges', color: 'var(--series-4)'}], height: 150, format: v => `${num(v)} badges`});
    const latest = badges.slice(0, 5).map(b => `<span class="badge">${escapeHtml(b.badgeName || '')}</span>`).join(' ');
    el.insertAdjacentHTML('beforeend', `<div class="legend" style="margin-top:8px">${latest}</div>`);
}

function renderLifetime(lifetime, devices) {
    const el = document.getElementById('lifetime');
    const l = (lifetime && lifetime.data) || {};
    const devs = ((devices && devices.data) || []).map(d => d.productDisplayName).filter(Boolean);
    el.innerHTML = [
        infoRow('Steps', num(l.totalSteps)),
        infoRow('Distance', l.totalDistance ? fmtMeters(l.totalDistance) : '–'),
        infoRow('Calories', l.totalCalories ? `${num(l.totalCalories)} kcal` : '–'),
        infoRow('Active days', num(l.totalActiveDays)),
        infoRow('Days goal met', num(l.totalGoalsMetInDays)),
        infoRow('Devices', escapeHtml(devs.join(', ') || '–')),
    ].join('');
}

function renderPlans(records) {
    const el = document.getElementById('plans');
    const list = (x, ...keys) => { const d = x && x.data; if (Array.isArray(d)) return d; for (const k of keys) if (d && Array.isArray(d[k])) return d[k]; return []; };
    const plans = list(records.training_plans, 'trainingPlanList');
    const upcoming = list(records.upcoming_events, 'events');
    const scheduled = list(records.scheduled_workouts);
    const workouts = list(records.workouts, 'workouts');
    const races = records.races || [];
    const title = e => e.eventName || e.title || e.name || e.workoutName || 'event';
    const when = e => e.date || e.eventDate || e.startDate || e.day || '';
    const rows = [];
    plans.forEach(p => rows.push(infoRow('Training plan', `${escapeHtml(p.name || 'plan')}${p.endDate ? ` · until ${fmtDayY(String(p.endDate).slice(0, 10))}` : ''}`)));
    upcoming.slice(0, 5).forEach(e => rows.push(infoRow('Coming up', `${escapeHtml(title(e))}${when(e) ? ` · ${fmtDayY(String(when(e)).slice(0, 10))}` : ''}`)));
    scheduled.slice(0, 5).forEach(e => rows.push(infoRow('Scheduled', `${escapeHtml(title(e))}${when(e) ? ` · ${fmtDayY(String(when(e)).slice(0, 10))}` : ''}`)));
    races.slice(0, 8).forEach(e => rows.push(infoRow('Race', `${escapeHtml(title(e))} · ${fmtDayY(e.day)}`)));
    if (workouts.length) rows.push(infoRow('Saved workouts', num(workouts.length)));
    el.closest('.panel').hidden = !rows.length;
    el.innerHTML = rows.join('');
}

async function loadRecords() {
    recordsPeriod.render();
    buildChips('records-bucket', [{key: 'week', label: 'Week'}, {key: 'month', label: 'Month'}, {key: 'year', label: 'Year'}], recordsBucket, key => {
        recordsBucket = key;
        localStorage.setItem(LS + 'records_bucket', key);
        loadRecords();
    });
    const range = recordsPeriod.days();
    document.getElementById('records-range-text').textContent = rangeText(range);
    recordsLoadedAt = Date.now();
    try {
        const [summary, records] = await Promise.all([
            api(`activities/summary?start=${range.start}&end=${range.end}&bucket=${recordsBucket}`), api('records'),
        ]);
        renderSportTiles(summary);
        renderSportHours(summary, recordsBucket);
        renderSportTypes(summary.types);
        renderEddington(summary.eddington);
        renderZones(document.getElementById('zones'), summary.hr_zones_s);
        heatGrid(document.getElementById('train-grid'), summary.start_grid, v => `${num(v)} activities`);
        renderPrs(records.personal_records);
        renderGear(records.gear);
        renderBadges(records.badges || []);
        renderLifetime(records.lifetime, records.devices);
        renderPlans(records);
        renderHeatMap(range);
    } catch (e) {
        toast(e.message, 'error');
    }
}

onTab('records', loadRecords);
onStatus(changed => { if (changed && activeTab === 'records' && Date.now() - recordsLoadedAt > 120000) loadRecords(); });
