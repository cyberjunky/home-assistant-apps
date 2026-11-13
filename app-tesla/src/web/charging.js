// Tesla dashboard - Charging view: every charging session with its power curve and cost.

let chargesRange = localStorage.getItem(LS + 'charges_range') || '90d';
let charges = [];
let chargesLoadedFor = null;
let selectedCharge = null;
let pendingCharge = null;
let chargeData = null;
let chargeTags = null;       // [{name, price_per_kwh, pos}] - charging price tags
let tagDefaultPrice = null;  // the configured default price per kWh

async function loadTags(force) {
    if (chargeTags && !force) return chargeTags;
    try {
        const data = await api('tags');
        chargeTags = data.tags || [];
        tagDefaultPrice = data.default_price;
    } catch (e) { chargeTags = chargeTags || []; }
    return chargeTags;
}

function renderCharges(totals) {
    const el = document.getElementById('charges-rows');
    if (!charges.length) {
        el.innerHTML = `<div class="empty-state">${currentCar() ? 'No charging sessions in this period' : 'No charging sessions recorded yet'}</div>`;
        document.getElementById('charges-status').textContent = '';
        return;
    }
    el.innerHTML = charges.map(charge => `
        <button type="button" class="feed-row${charge.id === selectedCharge ? ' selected' : ''}" data-id="${charge.id}" onclick="selectCharge(${charge.id})">
            <span class="time">${fmtDayY(charge.start_ts)} ${fmtTime(charge.start_ts)}</span>
            <span class="route">${escapeHtml(placeName(charge.place, charge.lat, charge.lon))}</span>
            <span class="col-kind"><span class="badge${charge.kind === 'DC' ? ' dc' : ''}"${charge.estimated ? ' title="Estimated: charged while the app wasn\'t watching"' : ''}>${escapeHtml(charge.estimated ? 'est.' : (charge.kind || '?'))}</span></span>
            <span class="num">${num(charge.energy_added_kwh, 1)} kWh</span>
            <span class="num col-dur">${fmtDuration(charge.duration_s)}</span>
            <span class="num col-soc">${fmtPct(charge.start_soc)} → ${fmtPct(charge.end_soc)}</span>
            <span class="num col-cost">${fmtMoney(charge.cost)}</span>
        </button>`).join('');
    const cost = missing(totals.cost) ? '' : ` · ${fmtMoney(totals.cost)}`;
    document.getElementById('charges-status').textContent =
        `${num(totals.charges)} sessions · ${fmtKwh(totals.added_kwh, 0)} added${cost}`
        + (totals.charges > charges.length ? ` · latest ${num(charges.length)} listed` : '');
}

async function loadCharges(force) {
    const car = currentCar();
    if (!car) { charges = []; renderCharges({}); return; }
    const key = `${car.id}:${chargesRange}`;
    if (!force && key === chargesLoadedFor && !pendingCharge) return;
    const bounds = rangeBounds(chargesRange);
    let data;
    try {
        data = await api(`charges?car=${car.id}&from=${bounds.from}&to=${bounds.to}&limit=2000`);
    } catch (e) {
        toast(e.message, 'error');
        return;
    }
    chargesLoadedFor = key;
    charges = data.items;
    renderCharges(data.totals);

    if (pendingCharge) {
        const id = pendingCharge;
        if (!charges.some(c => c.id === id) && chargesRange !== 'all') {
            setChargesRange('all');
            return;
        }
        pendingCharge = null;
        selectCharge(id);
    } else if (!charges.some(c => c.id === selectedCharge)) {
        if (charges.length) selectCharge(charges[0].id); else clearCharge();
    }
}

function setChargesRange(key) {
    chargesRange = key;
    localStorage.setItem(LS + 'charges_range', key);
    buildChips('charges-range', RANGES, chargesRange, setChargesRange);
    buildYearSelect('charges-year', chargesRange, setChargesRange);
    loadCharges(true);
}

function clearCharge() {
    selectedCharge = null;
    chargeData = null;
    mapLayers.charging.clearLayers();
    document.getElementById('charge-detail').innerHTML = '<div class="empty-state">Select a charging session</div>';
}

async function selectCharge(id) {
    selectedCharge = id;
    document.querySelectorAll('#charges-rows .feed-row').forEach(row => row.classList.toggle('selected', Number(row.dataset.id) === id));
    const row = document.querySelector(`#charges-rows .feed-row[data-id="${id}"]`);
    if (row) row.scrollIntoView({block: 'nearest'});
    let data;
    try {
        data = await api(`charges/${id}`);
    } catch (e) {
        toast(e.message, 'error');
        return;
    }
    if (selectedCharge !== id) return;
    chargeData = data;
    await loadTags();
    if (selectedCharge !== id) return;
    if (window.location.hash !== `#charging/${id}`) history.replaceState(null, '', `#charging/${id}`);
    drawCharge();
    renderChargeDetail();
}

function drawCharge() {
    const group = mapLayers.charging;
    group.clearLayers();
    const charge = chargeData.charge;
    if (missing(charge.lat) || missing(charge.lon)) return;
    L.marker([charge.lat, charge.lon], {icon: pinIcon('⚡', 'charge'), interactive: false}).addTo(group);
    if (activeTab === 'charging') map.setView([charge.lat, charge.lon], 15);
}

// Cable, charger and battery heater of a session. Sessions imported before these were kept have none.
function chargeHardwareFacts(charge, column) {
    const lastKnown = name => { const values = column(name).filter(v => !missing(v)); return values.length ? values[values.length - 1] : null; };
    const heated = (() => {
        const on = column('battery_heater_on');
        const known = on.filter(v => !missing(v));
        return known.length ? known.filter(Boolean).length / known.length : null;
    })();
    const noPower = column('battery_heater_no_power').some(Boolean) || column('not_enough_power_to_heat').some(Boolean);
    const pilot = lastKnown('pilot_current');
    const requested = lastKnown('requested_current');
    const idealAdded = missing(charge.start_ideal_range_km) || missing(charge.end_ideal_range_km) ? null : charge.end_ideal_range_km - charge.start_ideal_range_km;
    const items = [
        charge.conn_charge_cable ? fact('Cable', escapeHtml(charge.conn_charge_cable)) : '',
        charge.fast_charger_type ? fact('Charger type', escapeHtml(charge.fast_charger_type)) : '',
        missing(pilot) || charge.kind === 'DC' ? '' : fact('Charger offered', withUnit(num(pilot), 'A')),
        missing(requested) ? '' : fact('Requested', withUnit(num(requested), 'A')),
        missing(idealAdded) ? '' : fact('Ideal range added', withUnit(num(conv.dist(idealAdded)), unit.dist())),
        missing(heated) ? '' : fact('Battery heater', heated > 0 ? `${num(heated * 100)}% of the session` : 'Off'),
        noPower ? fact('Heating', 'Not enough power', 'warn') : '',
    ];
    return items.some(Boolean) ? `<div class="facts three">${factsGrid(items, 3)}</div>` : '';
}

function renderChargeDetail() {
    const charge = chargeData.charge;
    const column = name => { const i = chargeData.columns.indexOf(name); return chargeData.points.map(p => p[i]); };
    const efficiency = charge.energy_used_kwh > 0 ? charge.energy_added_kwh / charge.energy_used_kwh * 100 : null;
    const rangeAdded = missing(charge.start_range_km) || missing(charge.end_range_km) ? null : charge.end_range_km - charge.start_range_km;
    const average = charge.duration_s > 0 ? charge.energy_added_kwh / (charge.duration_s / 3600) : null;
    const billedKwh = charge.energy_used_kwh || charge.energy_added_kwh;
    const perKwh = !missing(charge.cost) && billedKwh > 0 ? charge.cost / billedKwh : null;
    const site = charge.site || {};
    // Where the cost comes from: typed in, billed by Tesla, or estimated from the price per kWh
    const billedText = missing(charge.billed_amount) ? '' : fmtMoney(charge.billed_amount, charge.billed_currency);
    const billedHere = billedText && charge.billed_currency === app.currency;
    const costNote = charge.cost_manual ? ' <small>set by hand</small>'
        : billedHere ? ' <small>billed by Tesla</small>'
        : missing(charge.cost) ? '' : ' <small>estimate</small>';
    const billedAbroad = billedText && !billedHere ? ` · Tesla billed ${escapeHtml(billedText)}` : '';
    // Supercharger sessions carry Tesla's real bill, so a price tag doesn't apply to them.
    const tagControl = (charge.billed || !charge.location_key) ? '' : `
            <div class="tag-row">
                <label for="charge-tag">Location price tag</label>
                <select id="charge-tag" class="input" onchange="setChargeTag(${charge.id}, this.value)">
                    <option value=""${charge.tag ? '' : ' selected'}>Default price${missing(tagDefaultPrice) || tagDefaultPrice <= 0 ? '' : ' (' + fmtMoney(tagDefaultPrice) + '/kWh)'}</option>
                    ${(chargeTags || []).map(t => `<option value="${escapeHtml(t.name)}"${t.name === charge.tag ? ' selected' : ''}>${escapeHtml(t.name)} · ${tagRateLabel(t.price_per_kwh)}</option>`).join('')}
                </select>
                <span class="hint">Sets the rate for every estimated session at this location</span>
            </div>`;
    document.getElementById('charge-detail').innerHTML = `
        <div class="detail">
            <div class="detail-head">
                <h2>${escapeHtml(placeName(charge.place, charge.lat, charge.lon))}</h2>
                <span class="badge${charge.kind === 'DC' ? ' dc' : ''}">${escapeHtml(charge.estimated ? 'Estimated' : charge.kind === 'DC' ? (charge.charger_brand || 'DC fast charging') : 'AC')}</span>
                <span class="when">${fmtDate(charge.start_ts)} · ${fmtTime(charge.start_ts)} – ${fmtTime(charge.end_ts)}</span>
            </div>
            ${charge.estimated ? `<div class="panel-sub">The car charged while the app wasn't watching (it was offline, or the app wasn't running): somewhere between these times, ${charge.lat == null ? 'not where it was parked' : 'where it was parked'}. The energy is estimated from the battery level gained × the battery's capacity.</div>` : ''}
            <div class="facts three">
                ${fact('Added', withUnit(num(charge.energy_added_kwh, 1), 'kWh'), 'big')}
                ${fact('Battery', `${fmtPct(charge.start_soc)} → ${fmtPct(charge.end_soc)}`, 'big')}
                ${fact('Duration', fmtDuration(charge.duration_s), 'big')}
                ${fact('From the charger', withUnit(num(charge.energy_used_kwh, 1), 'kWh'))}
                ${fact('Efficiency', missing(efficiency) ? '–' : fmtPct(efficiency))}
                ${fact('Range added', missing(rangeAdded) ? '–' : withUnit(num(conv.dist(rangeAdded)), unit.dist()))}
                ${fact('Peak power', missing(charge.power_max_kw) ? '–' : withUnit(num(charge.power_max_kw, 1), 'kW'))}
                ${fact('Average power', missing(average) ? '–' : withUnit(num(average, 1), 'kW'))}
                ${fact('Cost', fmtMoney(charge.cost) + costNote)}
                ${fact('Price / kWh', missing(perKwh) ? '–' : fmtMoney(perKwh) + ' <small>approx</small>')}
                ${site.visits > 1 ? fact('Charged here', `${num(site.visits)}× · ${fmtKwh(site.energy, 0)}` + (missing(site.cost) ? '' : ` · ${fmtMoney(site.cost)}`)) : ''}
            </div>
            ${chargeHardwareFacts(charge, column)}
            ${tagControl}
            <div>
                <div class="chart-title"><span>Charging power</span><span class="hint">kW</span></div>
                <div id="charge-power-chart" class="chart"></div>
            </div>
            <div>
                <div class="chart-title"><span>Battery level</span><span class="hint">%</span></div>
                <div id="charge-soc-chart" class="chart"></div>
            </div>
            <div class="detail-actions">
                <span class="panel-sub">Outside ${fmtTemp(charge.outside_temp_avg)} · odometer ${fmtDist(charge.odometer_km, 0)}${billedAbroad}</span>
                <span class="grow"></span>
                <button type="button" class="btn btn-small" onclick="editChargeCost(${charge.id})">Set cost</button>
                <button type="button" class="btn btn-small btn-danger" onclick="deleteCharge(${charge.id})">Delete</button>
            </div>
        </div>`;
    const ts = column('ts');
    const shared = {x: ts, area: true, height: 140, xTicks: ts.length > 1 ? timeTicks(ts, 4, fmtTime) : [], formatX: t => new Date(t * 1000).toLocaleTimeString()};
    lineChart(document.getElementById('charge-power-chart'), {
        ...shared, zero: true,
        series: [{name: 'Power', color: 'var(--series-3)', values: column('power_kw')}],
        formatY: v => `${num(v, 1)} kW`,
    });
    lineChart(document.getElementById('charge-soc-chart'), {
        ...shared,
        series: [{name: 'Battery', color: 'var(--series-4)', values: column('soc')}],
        formatY: v => `${num(v)}%`,
    });
}

function tagRateLabel(price) {
    if (price === null || price === undefined) return 'default price';
    if (price === 0) return 'free';
    return fmtMoney(price) + '/kWh';
}

async function setChargeTag(id, tag) {
    try {
        await api(`charges/${id}/tag`, {method: 'POST', body: {tag}});
    } catch (e) { toast(e.message, 'error'); return; }
    toast('Price tag updated');
    chargesLoadedFor = null;   // the recalculated costs changed the list and totals
    await loadCharges(true);
    await selectCharge(id);
}

function editChargeCost(id) {
    const charge = chargeData.charge;
    const overlay = openModal({
        title: 'Cost of this charging session',
        bodyHTML: `
            <div class="form-field">
                <label for="charge-cost">Cost (${escapeHtml(app.currency)})</label>
                <input id="charge-cost" class="input" type="number" min="0" step="0.01" value="${missing(charge.cost) ? '' : charge.cost}">
            </div>
            <div class="panel-sub">Leave empty to use what Tesla billed, or else the calculated cost (energy from the charger × the price per kWh in the app configuration).</div>
            <div class="form-error" id="charge-cost-error"></div>`,
        footHTML: '<button class="btn" data-close>Cancel</button><button class="btn btn-primary" data-save>Save</button>',
    });
    const input = overlay.querySelector('#charge-cost');
    input.focus();
    const save = async () => {
        try {
            chargeData.charge = await api(`charges/${id}`, {method: 'PATCH', body: {cost: input.value}});
            closeModal();
            renderChargeDetail();
            loadCharges(true);
        } catch (e) {
            overlay.querySelector('#charge-cost-error').textContent = e.message;
        }
    };
    overlay.querySelector('[data-save]').addEventListener('click', save);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
}

async function deleteCharge(id) {
    const ok = await confirmDialog({
        title: 'Delete this charging session?',
        message: 'The session disappears from the list and the statistics. This cannot be undone.',
        confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    try {
        await api(`charges/${id}`, {method: 'DELETE'});
        toast('Charging session deleted', 'success');
        selectedCharge = null;
        loadCharges(true);
    } catch (e) {
        toast(e.message, 'error');
    }
}

function openCharge(id) {
    pendingCharge = id;
    switchTab('charging');
}

onTab('charging', (force) => {
    buildChips('charges-range', RANGES, chargesRange, setChargesRange);
    buildYearSelect('charges-year', chargesRange, setChargesRange);
    if (chargeData && !pendingCharge) drawCharge();
    loadCharges(force);
});
