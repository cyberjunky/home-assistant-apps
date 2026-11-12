// SDRadio dashboard - Settings. Every change is saved at once.

async function loadSettings() {
    try {
        const data = await api('settings');
        app.settings = data.settings;
        app.settingsData = data;
        app.bands = data.bands;
        applyLcdColour(app.settings.lcd_colour);
    } catch (e) {
        toast(e.message, 'error');
    }
    return app.settings;
}

async function saveSetting(key, value, quiet) {
    try {
        const data = await api('settings', {method: 'PUT', json: {[key]: value}});
        app.settings = data.settings;
        applyLcdColour(app.settings.lcd_colour);
        if (!quiet) toast('Saved', 'success');
        return true;
    } catch (e) {
        toast(e.message, 'error');
        if (activeTab === 'settings') renderSettings();
        return false;
    }
}

function yamlHtml(options) {
    const c = text => `<span class="c"># ${escapeHtml(text)}</span>`;
    const r = options.receiver, t = options.tune;
    const lines = [
        'receiver:',
        `  type: ${r.type}        ${c('rtl-sdr | hackrf | soapysdr | network')}`,
        `  network_host: "${escapeHtml(r.network_host)}"`,
        `  network_port: ${r.network_port}`,
        `  ppm_correction: ${r.ppm_correction}`,
        `  sdr_gain: ${r.sdr_gain}           ${c('0 = automatic')}`,
        `  hackrf_lna_gain: ${r.hackrf_lna_gain}`,
        `  hackrf_vga_gain: ${r.hackrf_vga_gain}`,
        `  hackrf_amp_enable: ${r.hackrf_amp_enable}`,
        `  bias_tee: ${r.bias_tee}`,
        'tune:',
        `  frequency: ${t.frequency}     ${c('MHz, at start')}`,
        `  mode: ${t.mode}`,
        `  restore_last: ${t.restore_last}   ${c('start where you left off')}`,
        `log_level: ${options.log_level}`,
    ];
    return lines.join('\n');
}

function selectHtml(key, options, labels) {
    const value = app.settings[key];
    return `<select class="select" data-setting="${key}">${options.map(o =>
        `<option value="${escapeHtml(JSON.stringify(o))}"${JSON.stringify(o) === JSON.stringify(value) ? ' selected' : ''}>${escapeHtml(labels ? labels(o) : o)}</option>`).join('')}</select>`;
}
function numberHtml(key, step) {
    return `<input class="input mono" type="number" step="${step || 1}" data-setting="${key}" data-number="1" value="${escapeHtml(app.settings[key])}">`;
}
function checkHtml(key, disabled) {
    return `<input type="checkbox" data-setting="${key}"${app.settings[key] ? ' checked' : ''}${disabled ? ' disabled' : ''}>`;
}
function setRow(label, help, control, forId) {
    return `<div class="set-row"><label${forId ? ` for="${forId}"` : ''}>${label}${help ? `<small>${help}</small>` : ''}</label>${control}</div>`;
}

function renderSettings() {
    const data = app.settingsData;
    if (!data) return;
    const spec = data.spec;
    const ha = data.ha;
    const days = d => d ? `${d} days` : 'forever';
    document.getElementById('settings').innerHTML = `
        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Receiver · app configuration</span><span class="badge">read only</span></div>
            <p class="panel-sub" style="margin-bottom:10px">The receiver, its gain and ppm, where the radio starts and the logging come from the app's Configuration tab in Home Assistant. Changing them restarts the app.</p>
            <pre class="yaml">${yamlHtml(data.options)}</pre>
        </div></div>

        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Scanner</span></div>
            ${setRow('Scan speed', 'channels or steps looked at per second', numberHtml('scan_speed'))}
            ${setRow('Resume after', 'seconds of quiet before the scan moves on', numberHtml('scan_resume', 0.5))}
            ${setRow('Priority check', 'with PRI on: look at the priority channels every … seconds', numberHtml('priority_interval', 0.5))}
            ${setRow('New find after', 'hits on a frequency that isn\'t stored before it is listed as a new find', numberHtml('find_min_hits'))}
            <p class="panel-sub" style="margin-top:8px">The scan stops on a signal above the squelch; with the squelch off, on a signal ${12} dB above the noise.</p>
        </div></div>

        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Spectrum &amp; audio</span></div>
            ${setRow('FFT size', 'more bins: finer detail, more work', selectHtml('fft_size', spec.fft_size.options))}
            ${setRow('Waterfall speed', '', selectHtml('spectrum_rate', spec.spectrum_rate.options, v => `${v} lines a second`))}
            ${setRow('Waterfall colours', '', selectHtml('waterfall_palette', spec.waterfall_palette.options, v => v[0].toUpperCase() + v.slice(1)))}
            ${setRow('Audio buffer', 'more: fewer dropouts, more delay', selectHtml('audio_buffer_ms', spec.audio_buffer_ms.options, v => `${v} ms`))}
            ${setRow('Display colour', 'the colour of the radio\'s display', selectHtml('lcd_colour', spec.lcd_colour.options, v => v === 'lcd' ? 'LCD (grey-green)' : v[0].toUpperCase() + v.slice(1)))}
        </div></div>

        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Modes</span></div>
            <p class="panel-sub" style="margin-bottom:8px">Pick a mode in the top bar: the radio tunes to the mode's frequency and decodes what it hears there. You can still tune elsewhere.</p>
            ${modeSettingsHtml(ha)}
            ${setRow('Keep decoded for', 'pager messages and sensor readings; older ones are removed nightly', selectHtml('decoded_keep_days', spec.decoded_keep_days.options, days))}
        </div></div>

        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Database &amp; backups</span></div>
            ${setRow('Keep hits for', 'older hits are removed nightly; channels are always kept', selectHtml('hits_keep_days', spec.hits_keep_days.options, days))}
            ${setRow('Nightly backup at', '', `<input class="input mono" data-setting="backup_time" value="${escapeHtml(app.settings.backup_time)}">`)}
            ${setRow('Backups to keep', '', numberHtml('backups_keep'))}
            <div class="detail-actions" style="margin-top:10px">
                <button type="button" class="btn btn-primary" onclick="backupNow()">Back up now</button>
                <button type="button" class="btn" onclick="switchTab('channels')">Restore…</button>
                <a class="btn" href="${basePath}api/channels/export" download>Export channels CSV</a>
                <button type="button" class="btn" onclick="switchTab('channels'); openImport()">Import channels CSV…</button>
            </div>
            <p class="panel-sub" style="margin-top:8px">The database and its backups are in the app's data folder, so Home Assistant backups include them.</p>
        </div></div>

        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Home Assistant</span></div>
            ${setRow('Sensors', 'sensor.sdradio_frequency, _signal and _scan', checkHtml('ha_sensors', !ha))}
            ${setRow('Events', 'sdradio_signal for every transmission on a stored channel, for automations', checkHtml('ha_events', !ha))}
            ${ha ? '' : '<p class="panel-sub">Only when SDRadio runs as a Home Assistant app.</p>'}
        </div></div>

        <div class="panel wide"><div class="panel-body">
            <div class="chart-title"><span>Bands</span>
                <span class="detail-actions"><button type="button" class="btn btn-small" onclick="resetBands()">Reset to defaults</button>
                <button type="button" class="btn btn-small btn-primary" onclick="editBand()">+ Band</button></span></div>
            <p class="panel-sub" style="margin-bottom:8px">BAND steps through the bands with a button; a band sets the mode, step and filter when you go to it, and is what a band scan and the band view cover. Only the bands the ${escapeHtml(receiverName())} can tune to are shown${hiddenBands() ? ` (${hiddenBands()} more are out of its range: <a href="#" onclick="toggleAllBands(event)">${app.showAllBands ? 'hide' : 'show'} them</a>)` : ''}.</p>
            <div class="table-wrap"><table class="data"><thead><tr><th>Band</th><th>From</th><th>To</th><th>Mode</th><th>Step</th><th>Filter</th><th>Button</th><th></th></tr></thead><tbody>
            ${app.bands.filter(b => app.showAllBands || !outOfRange(b)).map(b => `<tr${outOfRange(b) ? ' class="muted" title="Out of the receiver\'s range: BAND skips it"' : ''}><td>${escapeHtml(b.name)}${outOfRange(b) ? ' <span class="badge warn">out of range</span>' : ''}</td><td class="mono">${fmtMHz(b.start)}</td><td class="mono">${fmtMHz(b.stop)}</td>
                <td class="mono">${modeLabel(b.mode)}</td><td class="mono">${fmtKHz(b.step)}</td><td class="mono">${fmtKHz(b.bandwidth)}</td>
                <td>${b.button ? '<span class="state ok">yes</span>' : '<span class="muted">no</span>'}</td>
                <td style="text-align:right"><button type="button" class="btn btn-small" onclick="editBand(${b.id})">Edit</button></td></tr>`).join('')}
            </tbody></table></div>
        </div></div>`;

    bindModeSettings();
    document.querySelectorAll('#settings [data-setting]').forEach(el => {
        el.addEventListener('change', async () => {
            const key = el.dataset.setting;
            let value;
            if (el.type === 'checkbox') value = el.checked;
            else if (el.tagName === 'SELECT') value = JSON.parse(el.value);
            else if (el.dataset.number) value = Number(el.value);
            else value = el.value;
            await saveSetting(key, value);
        });
    });
}

function editBand(id) {
    const b = app.bands.find(x => x.id === id) || {name: '', start: 0, stop: 0, mode: 'nfm', step: 12500, bandwidth: 12500, button: 1};
    const modes = Object.keys(app.modes).map(m => `<option value="${m}"${m === b.mode ? ' selected' : ''}>${modeLabel(m)}</option>`).join('');
    openModal(id ? `Band ${b.name}` : 'New band', `
        <div class="form-grid">
            <div class="form-field full"><label for="bd-name">Name</label><input id="bd-name" class="input" maxlength="40" value="${escapeHtml(b.name)}"></div>
            <div class="form-field"><label for="bd-start">From (MHz)</label><input id="bd-start" class="input mono" value="${b.start ? fmtMHz(b.start) : ''}"></div>
            <div class="form-field"><label for="bd-stop">To (MHz)</label><input id="bd-stop" class="input mono" value="${b.stop ? fmtMHz(b.stop) : ''}"></div>
            <div class="form-field"><label for="bd-mode">Mode</label><select id="bd-mode" class="input">${modes}</select></div>
            <div class="form-field"><label for="bd-step">Step (kHz)</label><input id="bd-step" class="input mono" value="${b.step / 1000}"></div>
            <div class="form-field"><label for="bd-bw">Filter (kHz)</label><input id="bd-bw" class="input mono" value="${b.bandwidth / 1000}"></div>
        </div>
        <label class="checks" style="margin:0"><span><input type="checkbox" id="bd-button"${b.button ? ' checked' : ''}> BAND steps to this band</span></label>`,
    [
        ...(id ? [{label: 'Delete', cls: 'btn-danger', action: async () => { await api(`bands/${id}`, {method: 'DELETE'}); await afterBands(); }}] : []),
        {label: 'Cancel'},
        {label: 'Save', cls: 'btn-primary', action: async () => {
            const mhz = v => Math.round(parseFloat(document.getElementById(v).value.replace(',', '.')) * 1e6);
            const khz = v => Math.round(parseFloat(document.getElementById(v).value.replace(',', '.')) * 1000);
            const data = {name: document.getElementById('bd-name').value, start: mhz('bd-start'), stop: mhz('bd-stop'),
                mode: document.getElementById('bd-mode').value, step: khz('bd-step'), bandwidth: khz('bd-bw'),
                button: document.getElementById('bd-button').checked};
            await api(id ? `bands/${id}` : 'bands', {method: id ? 'PUT' : 'POST', json: data});
            await afterBands();
        }},
    ]);
}

function resetBands() {
    confirmModal('Reset bands', 'Put back the default band plan? Bands you added or changed are lost; stored channels stay.', 'Reset', async () => {
        await api('bands/reset', {method: 'POST'});
        await afterBands();
    });
}

async function afterBands() {
    await loadSettings();
    renderSettings();
}

onTab('settings', async () => {
    await loadSettings();
    renderSettings();
});

// A band the receiver can't tune to (an RTL-SDR takes 24 to 1766 MHz)
function outOfRange(band) {
    const range = app.state && app.state.device && app.state.device.frequency_range;
    return !!range && (band.stop < range[0] || band.start > range[1]);
}

function hiddenBands() { return app.bands.filter(outOfRange).length; }
function receiverName() {
    const d = app.state && app.state.device;
    return (d && d.receiver) || 'receiver';
}
function toggleAllBands(e) {
    e.preventDefault();
    app.showAllBands = !app.showAllBands;
    renderSettings();
}

// ---------------------------------------------------------------- modes

const PAGER_PROTOCOLS = ['FLEX', 'POCSAG512', 'POCSAG1200', 'POCSAG2400'];
const SENSOR_FREQUENCIES = [433.92, 868.3, 868.95, 915, 315, 345];
// Pager networks (as SDR Studio has them): a network sets the frequency and the protocols
const PAGER_NETWORKS = [
    {name: 'P2000 (emergency services, NL)', frequency: 169_650_000, protocols: ['FLEX']},
    {name: 'KPN Nationaal 1', frequency: 154_987_500, protocols: ['POCSAG512', 'POCSAG1200', 'POCSAG2400']},
    {name: 'KPN Nationaal 2', frequency: 159_990_000, protocols: ['POCSAG512', 'POCSAG1200', 'POCSAG2400']},
    {name: 'KPN Benelux / Entropia', frequency: 164_350_000, protocols: ['POCSAG512', 'POCSAG1200', 'POCSAG2400']},
    {name: 'DAPNET (radio amateurs)', frequency: 439_987_500, protocols: ['POCSAG1200']},
];

function modeSettingsHtml(ha) {
    const pager = app.settings.mode_pager || {}, sensors = app.settings.mode_sensors || {}, aprs = app.settings.mode_aprs || {};
    const acars = app.settings.mode_acars || {}, sstv = app.settings.mode_sstv || {};
    const mhz = hz => +(hz / 1e6).toFixed(6);
    return `
        ${setRow('Pager network', 'sets the frequency and the protocols; or type your own below',
            `<select class="select" id="mode-pager-network">${PAGER_NETWORKS.map((n, i) => `<option value="${i}"${n.frequency === pager.frequency ? ' selected' : ''}>${escapeHtml(n.name)} · ${mhz(n.frequency)}</option>`).join('')}
             ${PAGER_NETWORKS.some(n => n.frequency === pager.frequency) ? '' : '<option value="" selected>Other</option>'}</select>`, 'mode-pager-network')}
        ${setRow('Pager frequency', 'MHz; any frequency, also one not in the list',
            `<input class="input mono" id="mode-pager-frequency" value="${mhz(pager.frequency)}" inputmode="decimal">`, 'mode-pager-frequency')}
        ${setRow('Pager protocols', 'FLEX, and POCSAG at 512, 1200 and 2400 baud',
            `<span class="checks" style="margin:0">${PAGER_PROTOCOLS.map(p => `<label><input type="checkbox" data-pager-protocol="${p}"${(pager.protocols || []).includes(p) ? ' checked' : ''}> ${p.replace('POCSAG', '')}</label>`).join('')}</span>`)}
        ${setRow('Pager events', 'sdradio_pager in Home Assistant for every message, for automations',
            `<input type="checkbox" id="mode-pager-ha"${pager.ha_events ? ' checked' : ''}${ha ? '' : ' disabled'}>`, 'mode-pager-ha')}
        ${setRow('Sensors frequency', 'MHz; 433.92 for most, 868.3 for many weather stations, 868.95 for wM-Bus meters',
            `<input class="input mono" id="mode-sensors-frequency" list="sensor-frequencies" value="${mhz(sensors.frequency)}" inputmode="decimal">
             <datalist id="sensor-frequencies">${SENSOR_FREQUENCIES.map(f => `<option value="${f}">`).join('')}</datalist>`, 'mode-sensors-frequency')}
        ${setRow('APRS frequency', 'MHz; 144.8 in Europe, 144.39 in North America, 145.825 for the ISS',
            `<input class="input mono" id="mode-aprs-frequency" list="aprs-frequencies" value="${mhz(aprs.frequency)}" inputmode="decimal">
             <datalist id="aprs-frequencies"><option value="144.8"><option value="144.39"><option value="145.825"></datalist>`, 'mode-aprs-frequency')}
        ${setRow('ACARS channels', 'MHz, comma separated: 131.525, 131.725 and 131.825 in Europe; they must lie within 370 kHz of each other (one receiver)',
            `<input class="input mono" id="mode-acars-channels" value="${(acars.channels || []).map(mhz).join(', ')}">`, 'mode-acars-channels')}
        ${setRow('SSTV frequency', 'MHz; 145.8 the ISS, 144.5 on 2 m, 14.23 on HF (upper sideband, HackRF)',
            `<input class="input mono" id="mode-sstv-frequency" list="sstv-frequencies" value="${mhz(sstv.frequency)}" inputmode="decimal">
             <datalist id="sstv-frequencies"><option value="145.8"><option value="144.5"><option value="14.23"><option value="433.4"></datalist>`, 'mode-sstv-frequency')}`;
}

function saveMode(key, patch) {
    return saveSetting(`mode_${key}`, {...(app.settings[`mode_${key}`] || {}), ...patch});
}

function bindModeSettings() {
    const frequency = (id, key) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('change', () => {
            const mhz = parseFloat(el.value.replace(',', '.'));
            if (!(mhz > 0)) return toast('Type a frequency in MHz', 'error');
            saveMode(key, {frequency: Math.round(mhz * 1e6)});
        });
    };
    frequency('mode-pager-frequency', 'pager');
    const network = document.getElementById('mode-pager-network');
    if (network) network.addEventListener('change', async () => {
        const n = PAGER_NETWORKS[+network.value];
        if (!n) return;
        await saveMode('pager', {frequency: n.frequency, protocols: n.protocols});
        renderSettings();
    });
    frequency('mode-sensors-frequency', 'sensors');
    frequency('mode-aprs-frequency', 'aprs');
    frequency('mode-sstv-frequency', 'sstv');
    const acars = document.getElementById('mode-acars-channels');
    if (acars) acars.addEventListener('change', () => {
        const channels = acars.value.split(/[,; ]+/).map(v => parseFloat(v.replace(',', '.'))).filter(v => v > 0).map(v => Math.round(v * 1e6));
        if (!channels.length) return toast('Type one or more frequencies in MHz', 'error');
        saveMode('acars', {channels});
    });
    document.querySelectorAll('[data-pager-protocol]').forEach(el => el.addEventListener('change', () => {
        const protocols = Array.from(document.querySelectorAll('[data-pager-protocol]:checked')).map(x => x.dataset.pagerProtocol);
        saveMode('pager', {protocols});
    }));
    const ha = document.getElementById('mode-pager-ha');
    if (ha) ha.addEventListener('change', () => saveMode('pager', {ha_events: ha.checked}));
}
