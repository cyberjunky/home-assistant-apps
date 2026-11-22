// Vigil dashboard - Info: the database connection, the receivers, and the app.

async function showInfo() {
    await loadStatus();
    const s = app.status || {};
    const db = s.database || {};
    const c = s.counts || {};
    document.getElementById('info-database').innerHTML = [
        infoRow('Status', s.connected ? stateLabel('ok', 'connected') : stateLabel('bad', 'not connected')),
        infoRow('Host', `<span class="mono">${escapeHtml(db.host || '–')}</span>`),
        infoRow('Database', `<span class="mono">${escapeHtml(db.name || '–')}</span>`),
        infoRow('User', `<span class="mono">${escapeHtml(db.user || '–')}</span>`),
        s.connected ? infoRow('PostgreSQL', escapeHtml(db.version || '–')) : '',
        s.connected ? infoRow('TimescaleDB', db.timescaledb ? escapeHtml(db.timescaledb) : '<span class="sb-warn">not installed: plain tables</span>') : '',
        s.error ? infoRow('Error', `<span class="sb-bad">${escapeHtml(s.error)}</span>`) : '',
    ].join('') + '<p class="panel-sub" style="margin-top:10px">Vigil keeps its data in its own database on the Chronicle app. Make it in <b>Chronicle → Maintenance → App databases</b>; with the host empty, Vigil finds Chronicle by itself.</p>';

    const r = s.receivers || {};
    const q = s.queue || {};
    const on = v => (v === false || v === null || v === undefined || v === 0 ? stateLabel('', 'off') : stateLabel('ok', typeof v === 'number' ? `port ${v}` : 'listening'));
    document.getElementById('info-receivers').innerHTML = [
        infoRow('Syslog UDP', on(r.syslog_udp)),
        infoRow('Syslog TCP', on(r.syslog_tcp)),
        infoRow('OTLP / JSON (HTTP)', on(r.otlp)),
        infoRow('Write queue', `${num(q.size)}${q.size > 10000 ? ' <span class="sb-warn">backing up</span>' : ''}`),
        infoRow('Dropped', q.dropped ? `<span class="sb-bad">${num(q.dropped)}</span>` : '0'),
    ].join('') + '<p class="panel-sub" style="margin-top:10px">The host ports are set on the app\'s Network section; leave one empty to switch that receiver off. <a href="#sources" onclick="switchTab(\'sources\');return false">Sources</a> has the settings for your devices.</p>';

    document.getElementById('info-vigil').innerHTML = !s.connected ? '<p class="panel-sub">Not connected.</p>' : [
        infoRow('Log lines, 24 h', fmtCount(c.logs_24h)),
        infoRow('Metric series', fmtCount(c.series)),
        infoRow('Rules', num(c.rules)),
        infoRow('Firing', c.firing ? `<span class="sb-bad">${num(c.firing)}</span>` : stateLabel('ok', 'none')),
    ].join('') + '<p class="panel-sub" style="margin-top:10px">How long logs and metrics are kept is set on the app\'s Configuration tab (logs_retention_days, metrics_retention_days).</p>';

    document.getElementById('info-about').innerHTML = [
        infoRow('Version', escapeHtml(app.version)),
        infoRow('Storage', '<a href="https://github.com/cyberjunky/home-assistant-apps/tree/main/app-chronicle" target="_blank" rel="noopener">the Chronicle app</a>'),
        infoRow('Source', '<a href="https://github.com/cyberjunky/home-assistant-apps/tree/main/app-vigil" target="_blank" rel="noopener">home-assistant-apps</a>'),
        infoRow('Issues', '<a href="https://github.com/cyberjunky/home-assistant-apps/issues" target="_blank" rel="noopener">GitHub issues</a>'),
    ].join('');
}

onTab('info', showInfo);
