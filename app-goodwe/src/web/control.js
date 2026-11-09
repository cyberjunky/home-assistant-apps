// GoodWe dashboard - the battery control, on the Settings page: Hold / Normal, the
// forecast-driven auto-hold, zero export and the solar-surplus signal for Home Assistant.

let controlRendered = false;

function numInput(key, value, unit, attrs) {
    return `<span class="num"><input class="input" type="number" ${attrs || ''} value="${value}" data-key="${key}" onchange="saveControlInput(this)"><small>${unit}</small></span>`;
}

function renderControl(force) {
    const c = app.control;
    if (!c || c.auto_hold === undefined) return;
    const el = document.getElementById('battery-control');
    if (controlRendered && !force) {
        // Only the status lines change while the page is open; the inputs keep what is being typed
        const update = (id, html) => { const node = el.querySelector(id); if (node) node.innerHTML = html; };
        update('#control-decision', controlDecisionHtml(c));
        update('#control-export-status', controlExportHtml(c));
        update('#control-surplus-status', controlSurplusHtml(c));
        el.querySelectorAll('[data-dod]').forEach(b => b.classList.toggle('active', Number(b.dataset.dod) === Number(c.dod_reported)));
        return;
    }
    controlRendered = true;
    const row = (label, sub, control, hint, id) => `<div class="control-row"><div class="cl">${label}<small>${sub}</small></div><div>${control}</div><div class="ch" ${id ? `id="${id}"` : ''}>${hint}</div></div>`;
    el.innerHTML = `
        ${c.work_mode_general === false ? `<div class="note warn" style="margin-bottom:12px">The inverter is in <b>${escapeHtml(c.work_mode_name || '?')}</b> mode. Auto-hold only works in General mode: with auto-hold on, the app switches it back (at most once an hour), otherwise it is paused; switch back under <i>Inverter settings</i> below.</div>` : ''}
        <div class="control-group">
            <div class="section-title">Discharge</div>
            <div class="control-rows">
                ${row('Battery', 'writes the on-grid depth of discharge',
                    `<span class="detail-actions"><button type="button" class="btn${Number(c.dod_reported) === 0 ? ' active' : ''}" data-dod="0" onclick="setDod(0)">Hold</button><button type="button" class="btn${Number(c.dod_reported) === Number(c.night_dod) ? ' active' : ''}" data-dod="${c.night_dod}" onclick="setDod(${c.night_dod})">Normal</button></span>`,
                    `<b>Hold</b> keeps the battery for later: the grid covers the house. <b>Normal</b> lets it discharge to ${100 - c.night_dod}%. With auto-hold on, the next decision wins again.`)}
                ${row('Discharge floor', 'when not held · depth of discharge', numInput('night_dod', c.night_dod, '%', 'step="5" min="0" max="100"'), `DoD ${c.night_dod}% = the battery may discharge to ${100 - c.night_dod}%. 80 is the usual for LFP.`)}
            </div>
        </div>
        <div class="control-group">
            <div class="section-title">Auto-hold · by solar forecast</div>
            <div class="control-rows">
                ${row('Auto-hold', 'hold while solar is expected, release otherwise', switchHtml('ctl-auto-hold', c.auto_hold, "saveControl({auto_hold: !app.control.auto_hold})"), controlDecisionHtml(c), 'control-decision')}
                ${row('Threshold', 'forecast for the current hour, or the live production', numInput('threshold_kwh', c.threshold_kwh, 'kWh', 'step="0.05" min="0" max="100"'), 'Hold while at least this much is expected this hour. 0.1 kWh starts the hold at the first light.')}
                ${row('Forecast offset', 'correction before the threshold test', numInput('forecast_offset_pct', c.forecast_offset_pct, '%', 'step="5" min="-50" max="50"'), 'The forecast is multiplied by (100 + offset)%. Use the bias under Stats → Forecast vs actual: −20% when the forecast runs high, +20% when it runs low.')}
                ${row('Release at', 'state of charge', numInput('release_soc', c.release_soc, '%', 'step="1" min="0" max="100"'), 'Once the battery reaches this, the hold is released so it can serve the house as soon as the load exceeds the production. 100 holds all day.')}
            </div>
        </div>
        <div class="control-group">
            <div class="section-title">Zero export</div>
            <div class="control-rows">
                ${row('Zero export', 'export limit 0 W, limit enforced', switchHtml('ctl-zero-export', c.zero_export, "saveControl({zero_export: !app.control.zero_export})"), controlExportHtml(c), 'control-export-status')}
            </div>
        </div>
        <div class="control-group">
            <div class="section-title">Solar surplus · for Home Assistant</div>
            <div class="control-rows">
                ${row('Margin', 'surplus needed to switch on', numInput('surplus_margin_w', c.surplus_margin_w, 'W', 'step="50" min="0" max="50000"'), controlSurplusHtml(c), 'control-surplus-status')}
                ${row('Battery full from', 'state of charge', numInput('surplus_full_soc', c.surplus_full_soc, '%', 'step="1" min="50" max="100"'), 'The signal only switches on once the battery is this full: solar goes into the battery first.')}
                ${row('Off when importing', 'grid import that ends the surplus', numInput('surplus_off_import_w', c.surplus_off_import_w, 'W', 'step="50" min="0" max="50000"'), 'The signal goes off once the house draws more than this from the grid, or drains the battery. That is the hysteresis: a load you switch on (a miner) does not switch the signal off as long as the sun carries it.')}
                ${row('Debounce', 'before the signal flips', numInput('surplus_debounce_s', c.surplus_debounce_s, 's', 'step="10" min="0" max="3600"'), 'Published to MQTT as the binary sensor "Solar surplus" plus the estimated surplus in watts, and as surplus_started / surplus_ended events.')}
            </div>
        </div>`;
}

function controlDecisionHtml(c) {
    if (!c.auto_hold) return 'Off: the Hold and Normal buttons decide.';
    const d = c.decision || {};
    const extra = c.last_tick ? ` <small>· checked ${fmtAgo(c.last_tick)}${c.dod_writes_today ? ` · ${c.dod_writes_today}/${c.dod_writes_per_day} writes today` : ''}</small>` : '';
    return `<b>${escapeHtml(d.text || 'Deciding at the next check (every 5 minutes)')}</b>${extra}`;
}
function controlExportHtml(c) {
    if (!c.zero_export) return 'Off: the inverter exports what the house and battery cannot take.';
    const lim = c.export_limit_reported, sw = c.export_switch_reported;
    const reported = missing(lim) ? 'not read yet' : `inverter reports limit ${num(lim)} W, switch ${sw ? 'on' : 'off'}`;
    return `${c.export_verified ? '<b>Verified</b>' : 'Checking the inverter at the next tick'} · ${reported}${c.export_strikes ? ` · exporting for ${c.export_strikes * 5} min` : ''}. Solar that the house and battery cannot take is curtailed${c.curtailed_w > 30 ? ` (now ≈ ${fmtW(c.curtailed_w)})` : ''}.`;
}
function controlSurplusHtml(c) {
    return `${c.surplus ? '<b class="sb-ok">Surplus available</b>' : '<b>No surplus</b>'} · ${escapeHtml(c.surplus_reason || '')}${c.zero_export ? ' <small>(under zero export the curtailed part is estimated from the forecast)</small>' : ''}`;
}

async function saveControl(changes) {
    try {
        app.control = await api('control', {method: 'POST', body: changes});
        renderControl(true);
        toast('Battery control saved', 'success');
    } catch (e) {
        toast(e.message, 'error');
        renderControl(true);
    }
}

function saveControlInput(input) {
    const value = Number(input.value);
    if (Number.isNaN(value)) return;
    saveControl({[input.dataset.key]: value});
}

async function setDod(dod) {
    const hold = dod === 0;
    const ok = await confirmDialog({
        title: hold ? 'Hold the battery?' : 'Allow discharging?',
        message: hold
            ? 'Sets the on-grid depth of discharge to 0%: the battery stops discharging and the grid covers the house until you release it' + (app.control.auto_hold ? ' or auto-hold decides otherwise.' : '.')
            : `Sets the on-grid depth of discharge to ${dod}%: the battery may discharge down to ${100 - dod}%.`,
        confirmLabel: hold ? 'Hold' : 'Normal',
    });
    if (!ok) return;
    try {
        const result = await api('control/dod', {method: 'POST', body: {dod}});
        app.control = result.control;
        toast(result.result, 'success');
        renderControl(true);
    } catch (e) {
        toast(e.message, 'error');
    }
}
