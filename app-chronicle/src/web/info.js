// Chronicle dashboard - Info: the connection, what Chronicle set up, and the app.

async function showInfo() {
    await loadStatus();
    const s = app.status || {};
    const c = s.connection || {};
    document.getElementById('info-database').innerHTML = [
        infoRow('Status', s.connected ? stateLabel('ok', 'running') : stateLabel('bad', 'not reachable')),
        s.connected ? infoRow('PostgreSQL', escapeHtml(s.server || '–')) : '',
        s.connected ? infoRow('TimescaleDB', escapeHtml(s.timescaledb || 'not installed')) : '',
        infoRow('Chronicle database', escapeHtml(c.database || '–')),
        s.error ? infoRow('Error', `<span class="sb-bad">${escapeHtml(s.error)}</span>`) : '',
    ].join('') + '<p class="panel-sub" style="margin-top:10px">The database runs in this app. Its memory is set on the app\'s Configuration tab; Home Assistant\'s backups of the app hold all its databases.</p>';

    document.getElementById('info-integration').innerHTML = [
        infoRow('Host', `<span class="mono">${escapeHtml(c.host || '–')}</span>`),
        infoRow('Port', escapeHtml(c.port || 5432)),
        infoRow('User name', `<span class="mono">${escapeHtml(c.username || '–')}</span>`),
        infoRow('Password', '<button type="button" class="btn btn-small" id="info-password">Show</button>'),
        infoRow('Database', `<span class="mono">${escapeHtml(c.database || '–')}</span>`),
    ].join('') + '<p class="panel-sub" style="margin-top:10px">Home Assistant normally offers the Chronicle integration with this filled in (Settings → Devices &amp; services → Discovered). Set up by hand, or moving an existing integration here with Reconfigure: use these.</p>';
    document.getElementById('info-password').onclick = showIntegrationPassword;

    const meta = s.meta || {};
    document.getElementById('info-chronicle').innerHTML = !s.connected ? '<p class="panel-sub">Not connected.</p>'
        : !s.schema ? '<p class="panel-sub">No Chronicle history yet. Install the Chronicle integration in Home Assistant: it creates its tables on its first start.</p>'
        : [
            infoRow('Tables', stateLabel('ok', 'schema chronicle')),
            infoRow('Layout version', escapeHtml(meta.schema_version || '–')),
            infoRow('Averages', s.aggregates ? stateLabel('ok', 'hourly and daily') : stateLabel('', 'switched off')),
            meta.aggregate_time_zone ? infoRow('Days in', escapeHtml(meta.aggregate_time_zone)) : '',
        ].join('') + '<p class="panel-sub" style="margin-top:10px">What is recorded, for how long and how compact is set in the Chronicle integration\'s options in Home Assistant.</p>';

    document.getElementById('info-about').innerHTML = [
        infoRow('Version', escapeHtml(app.version)),
        infoRow('Integration', '<a href="https://github.com/cyberjunky/home-assistant-chronicle" target="_blank" rel="noopener">home-assistant-chronicle</a>'),
        infoRow('Source', '<a href="https://github.com/cyberjunky/home-assistant-apps/tree/main/app-chronicle" target="_blank" rel="noopener">home-assistant-apps</a>'),
        infoRow('Issues', '<a href="https://github.com/cyberjunky/home-assistant-apps/issues" target="_blank" rel="noopener">GitHub issues</a>'),
    ].join('');
}

async function showIntegrationPassword() {
    const button = document.getElementById('info-password');
    let data;
    try { data = await api('integration'); } catch (e) { return toast(e.message, 'error'); }
    if (!data.password) return toast('The password could not be read', 'error');
    button.outerHTML = `<span class="mono">${escapeHtml(data.password)}</span> <button type="button" class="btn btn-small" id="info-password-copy">Copy</button>`;
    document.getElementById('info-password-copy').onclick = () => copyPlain(data.password, 'Password');
}

onTab('info', showInfo);
