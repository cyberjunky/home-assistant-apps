// Chronicle dashboard - Info: the connection, what Chronicle set up, and the app.

async function showInfo() {
    await loadStatus();
    const s = app.status || {};
    const c = s.connection || {};
    document.getElementById('info-database').innerHTML = [
        infoRow('Status', s.connected ? stateLabel('ok', 'connected') : stateLabel('bad', 'not connected')),
        infoRow('Host', escapeHtml(`${c.host || '–'}:${c.port || ''}`)),
        infoRow('Database', escapeHtml(c.database || '–')),
        infoRow('User', escapeHtml(c.username || '–')),
        s.connected ? infoRow('PostgreSQL', escapeHtml(s.server || '–')) : '',
        s.connected ? infoRow('TimescaleDB', escapeHtml(s.timescaledb || 'not installed')) : '',
        s.error ? infoRow('Error', `<span class="sb-bad">${escapeHtml(s.error)}</span>`) : '',
    ].join('');

    const meta = s.meta || {};
    document.getElementById('info-chronicle').innerHTML = !s.connected ? '<p class="panel-sub">Not connected.</p>'
        : !s.schema ? '<p class="panel-sub">No Chronicle tables in this database yet. Install the Chronicle integration and point it at this database.</p>'
        : [
            infoRow('Tables', stateLabel('ok', 'schema chronicle')),
            infoRow('Layout version', escapeHtml(meta.schema_version || '–')),
            infoRow('Averages', s.aggregates ? stateLabel('ok', 'hourly and daily') : stateLabel('', 'switched off')),
            meta.aggregate_time_zone ? infoRow('Days in', escapeHtml(meta.aggregate_time_zone)) : '',
            meta.ltss_import_position ? infoRow('LTSS import', `up to ${escapeHtml(fmtDate(Date.parse(meta.ltss_import_position) / 1000))}`) : '',
        ].join('') + '<p class="panel-sub" style="margin-top:10px">What is recorded, for how long and how compact is set in the Chronicle integration\'s options in Home Assistant.</p>';

    document.getElementById('info-about').innerHTML = [
        infoRow('Version', escapeHtml(app.version)),
        infoRow('Integration', '<a href="https://github.com/cyberjunky/home-assistant-chronicle" target="_blank" rel="noopener">home-assistant-chronicle</a>'),
        infoRow('Source', '<a href="https://github.com/cyberjunky/home-assistant-apps/tree/main/app-chronicle" target="_blank" rel="noopener">home-assistant-apps</a>'),
        infoRow('Issues', '<a href="https://github.com/cyberjunky/home-assistant-apps/issues" target="_blank" rel="noopener">GitHub issues</a>'),
    ].join('');
}

onTab('info', showInfo);
