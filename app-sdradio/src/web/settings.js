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
            <p class="panel-sub" style="margin-bottom:8px">Pick a mode with the buttons under the tune dial: the radio tunes to the mode's frequency and decodes what it hears there. You can still tune elsewhere.</p>
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
// DAB's Band III blocks (as src/decoders/dab.py has them), with what is on them in the Netherlands
const DAB_BLOCKS = [['5A', 174.928], ['5B', 176.640], ['5C', 178.352], ['5D', 180.064], ['6A', 181.936], ['6B', 183.648],
    ['6C', 185.360], ['6D', 187.072], ['7A', 188.928], ['7B', 190.640], ['7C', 192.352], ['7D', 194.064], ['8A', 195.936],
    ['8B', 197.648], ['8C', 199.360], ['8D', 201.072], ['9A', 202.928], ['9B', 204.640], ['9C', 206.352], ['9D', 208.064],
    ['10A', 209.936], ['10B', 211.648], ['10C', 213.360], ['10D', 215.072], ['11A', 216.928], ['11B', 218.640],
    ['11C', 220.352], ['11D', 222.064], ['12A', 223.936], ['12B', 225.648], ['12C', 227.360], ['12D', 229.072],
    ['13A', 230.784], ['13B', 232.496], ['13C', 234.208], ['13D', 235.776], ['13E', 237.488], ['13F', 239.200]];
const DAB_NOTES = {'12C': 'NPO', '11C': 'national commercial', '8A': 'regional (Zuid-Holland, Amsterdam)'};
// The satellites the Satellites mode can follow (as src/decoders/satellites.py has them)
const SATELLITES = [
    {id: 'iss', name: 'ISS', frequency: 145_800_000, what: 'voice and SSTV pictures'},
    {id: 'iss-aprs', name: 'ISS APRS', frequency: 145_825_000, what: 'APRS digipeater'},
    {id: 'so50', name: 'SO-50', frequency: 436_795_000, what: 'FM repeater'},
    {id: 'meteor-m2-3', name: 'Meteor-M2-3', frequency: 137_100_000, what: 'weather pictures, recorded'},
    {id: 'meteor-m2-4', name: 'Meteor-M2-4', frequency: 137_900_000, what: 'weather pictures, recorded'},
];
const PAGER_NETWORKS = [
    {name: 'P2000 (emergency services, NL)', frequency: 169_650_000, protocols: ['FLEX']},
    {name: 'KPN Nationaal 1', frequency: 154_987_500, protocols: ['POCSAG512', 'POCSAG1200', 'POCSAG2400']},
    {name: 'KPN Nationaal 2', frequency: 159_990_000, protocols: ['POCSAG512', 'POCSAG1200', 'POCSAG2400']},
    {name: 'KPN Benelux / Entropia', frequency: 164_350_000, protocols: ['POCSAG512', 'POCSAG1200', 'POCSAG2400']},
    {name: 'DAPNET (radio amateurs)', frequency: 439_987_500, protocols: ['POCSAG1200']},
];

const DIGITAL_PROTOCOLS = [['auto', 'Auto'], ['dmr', 'DMR'], ['dstar', 'D-STAR'], ['ysf', 'YSF (Fusion)'], ['nxdn48', 'NXDN 6.25 kHz'],
    ['nxdn96', 'NXDN 12.5 kHz'], ['dpmr', 'dPMR'], ['p25', 'P25 phase 1']];

// FT8's dial frequencies (MHz); FT4 is a few kHz up (14.080, 7.0475 …), typed in by hand
const FT8_BANDS = [[3.573, '80 m'], [7.074, '40 m'], [10.136, '30 m'], [14.074, '20 m'], [18.1, '17 m'], [21.074, '15 m'],
    [24.915, '12 m'], [28.074, '10 m'], [50.313, '6 m'], [144.174, '2 m']];

function modeSettingsHtml(ha) {
    const pager = app.settings.mode_pager || {}, sensors = app.settings.mode_sensors || {}, aprs = app.settings.mode_aprs || {};
    const acars = app.settings.mode_acars || {}, sstv = app.settings.mode_sstv || {}, sats = app.settings.mode_satellites || {};
    const video = app.settings.mode_video || {}, dabSettings = app.settings.mode_dab || {};
    const digital = app.settings.mode_digital || {};
    const vdl2 = app.settings.mode_vdl2 || {}, sondes = app.settings.mode_sondes || {}, ft8 = app.settings.mode_ft8 || {}, wefax = app.settings.mode_wefax || {};
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
        ${setRow('Digital voice', 'MHz: the output of the repeater or hotspot; and the protocol, or Auto to find DMR, D-STAR, YSF and P25 by themselves',
            `<input class="input mono" id="mode-digital-frequency" value="${mhz(digital.frequency)}" inputmode="decimal">
             <select class="select" id="mode-digital-protocol" aria-label="Protocol">${DIGITAL_PROTOCOLS.map(([k, n]) => `<option value="${k}"${(digital.protocol || 'auto') === k ? ' selected' : ''}>${n}</option>`).join('')}</select>`, 'mode-digital-frequency')}
        ${setRow('VDL2 channels', 'MHz, comma separated: 136.725, 136.775, 136.875 and 136.975 in Europe; within 800 kHz of each other',
            `<input class="input mono" id="mode-vdl2-channels" value="${(vdl2.channels || []).map(mhz).join(', ')}">`, 'mode-vdl2-channels')}
        ${setRow('Radiosonde frequency', 'MHz, 400 to 406; De Bilt\'s sondes change from launch to launch: radiosondy.info and sondehub.org show the last ones',
            `<input class="input mono" id="mode-sondes-frequency" list="sonde-frequencies" value="${mhz(sondes.frequency)}" inputmode="decimal">
             <datalist id="sonde-frequencies"><option value="403"><option value="402.7"><option value="404.1"><option value="405.1"></datalist>`, 'mode-sondes-frequency')}
        ${setRow('FT8 band', 'MHz, the dial frequency (upper sideband); below 24 MHz with the HackRF only',
            `<input class="input mono" id="mode-ft8-frequency" list="ft8-frequencies" value="${mhz(ft8.frequency)}" inputmode="decimal">
             <datalist id="ft8-frequencies">${FT8_BANDS.map(([f, b]) => `<option value="${f}" label="${b}">`).join('')}</datalist>
             <select class="select" id="mode-ft8-protocol" aria-label="FT8 or FT4">${['FT8', 'FT4'].map(p => `<option${(ft8.protocol || 'FT8') === p ? ' selected' : ''}>${p}</option>`).join('')}</select>`, 'mode-ft8-frequency')}
        ${setRow('Weather fax frequency', 'MHz, the dial: 1.9 kHz below the station (HackRF). DWD Pinneberg: 3.8531, 7.8781, 13.8806',
            `<input class="input mono" id="mode-wefax-frequency" list="wefax-frequencies" value="${mhz(wefax.frequency)}" inputmode="decimal">
             <datalist id="wefax-frequencies"><option value="3.8531"><option value="7.8781"><option value="13.8806"><option value="2.6166" label="Northwood"><option value="4.6081" label="Northwood"><option value="8.0381" label="Northwood"></datalist>`, 'mode-wefax-frequency')}
        ${setRow('SSTV frequency', 'MHz; 145.8 the ISS, 144.5 on 2 m, 14.23 on HF (upper sideband, HackRF)',
            `<input class="input mono" id="mode-sstv-frequency" list="sstv-frequencies" value="${mhz(sstv.frequency)}" inputmode="decimal">
             <datalist id="sstv-frequencies"><option value="145.8"><option value="144.5"><option value="14.23"><option value="433.4"></datalist>`, 'mode-sstv-frequency')}
        ${setRow('Satellites', 'followed during their passes; the frequency (MHz) as heard without Doppler, the Meteor ones change now and then',
            `<div class="sat-settings">${SATELLITES.map(s => `<label><input type="checkbox" data-sat-follow="${s.id}"${(sats.follow || []).includes(s.id) ? ' checked' : ''}> <b>${escapeHtml(s.name)}</b> <small class="muted">${escapeHtml(s.what)}</small></label>
                <input class="input mono" data-sat-frequency="${s.id}" value="${mhz((sats.frequencies || {})[s.id] || s.frequency)}" inputmode="decimal" aria-label="${escapeHtml(s.name)} frequency">`).join('')}</div>`)}
        ${setRow('DAB+ block', 'the block of DAB+ stations; in the Netherlands 12C (NPO), 11C (national commercial), and regional ones (8A, 8B, 5C …)',
            `<select class="select" id="mode-dab-block">${DAB_BLOCKS.map(([b, f]) => `<option value="${f}"${Math.round(f * 1e6) === dabSettings.frequency ? ' selected' : ''}>${b} · ${f.toFixed(3)} MHz${DAB_NOTES[b] ? ` · ${DAB_NOTES[b]}` : ''}</option>`).join('')}</select>`, 'mode-dab-block')}
        ${setRow('Video frequency', 'MHz (HackRF only); 5.8 GHz FPV (Raceband R1–R8, F4 5800), 1.2 GHz and 2.4 GHz cameras. Only video meant to be seen: drones, your own cameras',
            `<input class="input mono" id="mode-video-frequency" list="video-frequencies" value="${mhz(video.frequency)}" inputmode="decimal">
             <datalist id="video-frequencies">${[5800, 5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917, 5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725, 1080, 1120, 1160, 1200, 1240, 1280, 1320, 2414, 2432, 2450, 2468].map(f => `<option value="${f}">`).join('')}</datalist>`, 'mode-video-frequency')}
        ${setRow('Satellite passes from', 'degrees above the horizon: lower passes are short and far away',
            `<input class="input mono" id="mode-sat-elevation" type="number" min="0" max="60" value="${sats.min_elevation ?? 10}">`, 'mode-sat-elevation')}
        ${setRow('Record Meteor passes', 'as 8-bit IQ at 200 kHz (about 300 MB a pass), to make weather pictures from with SatDump',
            `<input type="checkbox" id="mode-sat-record"${sats.record ? ' checked' : ''}> keep the last <input class="input mono" id="mode-sat-keep" type="number" min="1" max="50" value="${sats.keep_recordings ?? 3}" style="width:60px">`, 'mode-sat-record')}`;
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
    frequency('mode-video-frequency', 'video');
    frequency('mode-sondes-frequency', 'sondes');
    frequency('mode-digital-frequency', 'digital');
    const digitalProtocol = document.getElementById('mode-digital-protocol');
    if (digitalProtocol) digitalProtocol.addEventListener('change', () => saveMode('digital', {protocol: digitalProtocol.value}));
    frequency('mode-ft8-frequency', 'ft8');
    frequency('mode-wefax-frequency', 'wefax');
    const ft8Protocol = document.getElementById('mode-ft8-protocol');
    if (ft8Protocol) ft8Protocol.addEventListener('change', () => saveMode('ft8', {protocol: ft8Protocol.value}));
    const vdl2 = document.getElementById('mode-vdl2-channels');
    if (vdl2) vdl2.addEventListener('change', () => {
        const channels = vdl2.value.split(/[,; ]+/).map(v => parseFloat(v)).filter(v => v > 0).map(v => Math.round(v * 1e6));
        if (!channels.length) return toast('Type one or more frequencies in MHz', 'error');
        saveMode('vdl2', {channels});
    });
    const dabBlock = document.getElementById('mode-dab-block');
    if (dabBlock) dabBlock.addEventListener('change', () => saveMode('dab', {frequency: Math.round(+dabBlock.value * 1e6)}));
    const sats = () => ({...(app.settings.mode_satellites || {})});
    document.querySelectorAll('[data-sat-follow]').forEach(el => el.addEventListener('change', () => {
        const follow = Array.from(document.querySelectorAll('[data-sat-follow]:checked')).map(x => x.dataset.satFollow);
        saveMode('satellites', {follow});
    }));
    document.querySelectorAll('[data-sat-frequency]').forEach(el => el.addEventListener('change', () => {
        const mhz = parseFloat(el.value.replace(',', '.'));
        if (!(mhz > 0)) return toast('Type a frequency in MHz', 'error');
        saveMode('satellites', {frequencies: {...(sats().frequencies || {}), [el.dataset.satFrequency]: Math.round(mhz * 1e6)}});
    }));
    const elevation = document.getElementById('mode-sat-elevation');
    if (elevation) elevation.addEventListener('change', () => saveMode('satellites', {min_elevation: +elevation.value}));
    const record = document.getElementById('mode-sat-record');
    if (record) record.addEventListener('change', () => saveMode('satellites', {record: record.checked}));
    const keep = document.getElementById('mode-sat-keep');
    if (keep) keep.addEventListener('change', () => saveMode('satellites', {keep_recordings: +keep.value}));
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
