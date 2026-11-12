// Freqtrade dashboard - Info: the configured bots, the app's options, its data and about.

function fmtBytes(bytes) {
    if (missing(bytes)) return '–';
    return bytes >= 1048576 ? `${num(bytes / 1048576, 1)} MB` : `${num(bytes / 1024, 0)} kB`;
}

function renderInfo() {
    const s = app.status;
    if (!s) return;
    const c = s.config;
    const yes = v => v ? 'on' : 'off';
    const rows = c.bots.map(cfg => {
        const b = botBySlug(cfg.slug) || {};
        return `<tr onclick="switchTab('bot-${escapeHtml(cfg.slug)}')">
            ${td(`<span class="name">${escapeHtml(cfg.name)}</span><span class="sub">${escapeHtml(cfg.slug)}</span>`)}
            ${td(escapeHtml(cfg.url), 'mono')}
            ${td(escapeHtml(cfg.username))}
            ${td(cfg.ws_token ? 'ws_token' : 'login token')}
            ${td(cfg.url.startsWith('https') ? (cfg.verify_ssl ? 'checked' : '<span class="neg">not checked</span>') : '<span class="muted">http</span>')}
            ${td(b.connected ? stateLabel('ok', 'connected') : `<span class="state bad" title="${escapeHtml(b.error || '')}">not reachable</span>`)}
            ${td(b.ws_connected ? stateLabel('ok', 'connected') : `<span class="state warn" title="${escapeHtml(b.ws_error || '')}">${b.ws_error ? 'not connected' : 'connecting'}</span>`)}
            ${td(escapeHtml((b.config && b.config.version) || '–'))}
        </tr>`;
    });
    document.getElementById('set-bots').innerHTML = table([
        {label: 'Bot'}, {label: 'Address'}, {label: 'User'}, {label: 'WebSocket login'}, {label: 'Certificate'},
        {label: 'REST API'}, {label: 'WebSocket'}, {label: 'Freqtrade'},
    ], rows, {cls: 'bots', empty: 'No bots configured. Add them under bots in the app\'s configuration.'});
    document.getElementById('set-app').innerHTML = [
        infoRow('Bots', String(c.bots.length)),
        infoRow('Read every', `${c.poll_seconds} s, and at once after an event`),
        infoRow('Details read every', '5 min, and after a trade'),
        infoRow('Read-only', c.read_only ? 'on <span class="muted">· nothing can be changed</span>' : 'off <span class="muted">· bots can be controlled</span>'),
        infoRow('Log level', escapeHtml(c.log_level)),
    ].join('');
    document.getElementById('set-mqtt').innerHTML = [
        infoRow('Publishing', yes(c.mqtt_enabled)),
        infoRow('Broker', s.mqtt.broker ? `${escapeHtml(s.mqtt.broker)} · ${s.mqtt.connected ? stateLabel('ok', 'connected') : stateLabel('bad', 'offline')}` : 'none'),
        infoRow('Topics', `<span class="mono">${escapeHtml(c.mqtt_base_topic)}/&lt;bot&gt;/state</span>, <span class="mono">…/event</span>`),
        infoRow('Discovery prefix', escapeHtml(c.mqtt_discovery_prefix)),
    ].join('');
    const db = s.database;
    document.getElementById('set-data').innerHTML = [
        infoRow('Balance samples', `${num(db.snapshots)} <span class="muted">every 5 min per bot</span>`),
        infoRow('Events', `${num(db.events)} <span class="muted">kept 180 days</span>`),
        infoRow('Since', db.oldest ? escapeHtml(fmtDateTime(db.oldest)) : '–'),
        infoRow('Size', fmtBytes(db.size)),
    ].join('');
    document.getElementById('set-about').innerHTML = [
        infoRow('Version', escapeHtml(s.version)),
        infoRow('Changelog', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-freqtrade/CHANGELOG.md" target="_blank" rel="noopener">on GitHub</a>'),
        infoRow('Freqtrade', '<a href="https://www.freqtrade.io/" target="_blank" rel="noopener">freqtrade.io</a>'),
        infoRow('Note', 'Unofficial: not made by or affiliated with the Freqtrade project'),
    ].join('');
}

async function deleteAllData() {
    const ok = await confirmDialog({
        title: 'Delete the app\'s history?',
        message: 'Deletes the balance history and the events this app kept. The bots and their trades are not touched.',
        typeToConfirm: 'DELETE', confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    try {
        const answer = await apiPost('data/delete', {confirm: 'DELETE'});
        toast(`Deleted ${num(answer.removed.snapshots)} samples and ${num(answer.removed.events)} events`, 'success');
        await loadStatus();
    } catch (e) {
        toast(e.message);
    }
}

onTab('info', renderInfo, renderInfo);
