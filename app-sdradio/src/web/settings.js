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
            <p class="panel-sub" style="margin-bottom:8px">BAND steps through the bands with a button; a band sets the mode, step and filter when you go to it, and is what a band scan covers.</p>
            <div class="table-wrap"><table class="data"><thead><tr><th>Band</th><th>From</th><th>To</th><th>Mode</th><th>Step</th><th>Filter</th><th>Button</th><th></th></tr></thead><tbody>
            ${app.bands.map(b => `<tr><td>${escapeHtml(b.name)}</td><td class="mono">${fmtMHz(b.start)}</td><td class="mono">${fmtMHz(b.stop)}</td>
                <td class="mono">${modeLabel(b.mode)}</td><td class="mono">${fmtKHz(b.step)}</td><td class="mono">${fmtKHz(b.bandwidth)}</td>
                <td>${b.button ? '<span class="state ok">yes</span>' : '<span class="muted">no</span>'}</td>
                <td style="text-align:right"><button type="button" class="btn btn-small" onclick="editBand(${b.id})">Edit</button></td></tr>`).join('')}
            </tbody></table></div>
        </div></div>`;

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
