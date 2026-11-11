// SDRadio dashboard - Info: the receiver, the app and the keys.

async function showInfo() {
    await loadStatus();
    const s = app.state || {};
    const d = s.device || {};
    const status = app.status || {};
    const r = (status.options || {}).receiver || {};
    const details = Object.entries(d.details || {}).map(([k, v]) => infoRow(escapeHtml(k), escapeHtml(v))).join('');
    document.getElementById('info-receiver').innerHTML = [
        infoRow('Status', d.ok ? '<span class="state ok">receiving</span>' : '<span class="state bad">no receiver</span>'),
        d.error ? `<div class="info-error">${escapeHtml(d.error)}</div>` : '',
        infoRow('Type', escapeHtml(status.receiver || r.type || '–')),
        d.driver ? infoRow('Driver', escapeHtml(d.driver)) : '',
        d.hardware ? infoRow('Hardware', escapeHtml(d.hardware)) : '',
        infoRow('Sample rate', `${num((d.sample_rate || s.rate) / 1e6, 1)} MS/s`),
        d.frequency_range ? infoRow('Tunes', `${fmtHz(d.frequency_range[0])} – ${fmtHz(d.frequency_range[1])}`) : '',
        d.gain_range ? infoRow('Gain', `${num(d.gain_range[0], 1)} – ${num(d.gain_range[1], 1)} dB`) : '',
        r.ppm_correction ? infoRow('ppm correction', escapeHtml(r.ppm_correction)) : '',
        details,
    ].join('') + '<p class="panel-sub" style="margin-top:10px">The receiver is set on the app\'s Configuration tab in Home Assistant.</p>';

    document.getElementById('info-about').innerHTML = [
        infoRow('Version', escapeHtml(app.version)),
        infoRow('Source', '<a href="https://github.com/cyberjunky/home-assistant-apps/tree/main/app-sdradio" target="_blank" rel="noopener">home-assistant-apps</a>'),
        infoRow('Issues', '<a href="https://github.com/cyberjunky/home-assistant-apps/issues" target="_blank" rel="noopener">GitHub issues</a>'),
    ].join('');

    const keys = [
        ['0–9 . Enter', 'type a frequency in MHz, Enter tunes (Esc cancels)'],
        ['↑ ↓ / PgUp PgDn', 'one or ten tuning steps'],
        ['Scroll a digit', 'change that digit of the frequency'],
        ['Click the waterfall', 'tune there; drag to move, scroll to step'],
        ['Knobs', 'drag up or down, scroll, or arrow keys; double-click for the default'],
        ['SCAN', 'scans what is picked under the keypad: stored channels, the band, or a range'],
        ['HOLD · L/O', 'stay on this channel · leave it out of scans'],
        ['PRI', 'look at the priority channels every few seconds'],
        ['◀ ▶ · M+', 'under the stored channels: previous and next stored channel, store this one'],
    ];
    document.getElementById('info-keys').innerHTML = keys.map(([k, v]) => infoRow(escapeHtml(k), escapeHtml(v))).join('');
}

onTab('info', showInfo);
