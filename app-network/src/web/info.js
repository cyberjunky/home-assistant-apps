// Network dashboard - Info: the connection to Home Assistant, the options, and how it works.

function renderInfo() {
    const el = document.getElementById('tab-info');
    const s = app.status;
    if (!s) return;
    const c = s.config;
    const platforms = new Map();
    app.hosts.forEach(h => h.trackers.forEach(t => platforms.set(t.platform || 'known_devices.yaml', (platforms.get(t.platform || 'known_devices.yaml') || 0) + 1)));
    const legacy = app.hosts.flatMap(h => h.trackers).filter(t => !t.in_registry).length;
    el.innerHTML = `
        <div class="info-grid">
            ${panel('Home Assistant', '', [
                infoRow('Connection', s.connected ? stateLabel('ok', 'connected') : `<span class="state bad">${escapeHtml(s.error || 'not reachable')}</span>`),
                infoRow('Device trackers', `${num(app.hosts.reduce((a, h) => a + h.trackers.length, 0))} <span class="muted">· ${num(app.hosts.length)} devices</span>`),
                infoRow('Read', escapeHtml(fmtAgo(s.loaded_at))),
                infoRow('History', s.backfill.done ? `${num(s.backfill.rows)} changes read from the recorder${s.backfill.error ? ` <span class="neg">· ${escapeHtml(s.backfill.error)}</span>` : ''}` : 'reading…'),
                infoRow('Integrations', [...platforms.entries()].sort((a, b) => b[1] - a[1]).map(([p, n]) => `${escapeHtml(PLATFORM_NAMES[p] || p)} <span class="muted">${n}</span>`).join('<br>') || '–'),
            ].join(''))}
            ${panel('Options', 'change them in the app\'s configuration', [
                infoRow('History read at start', `${num(c.history_days)} days`),
                infoRow('Kept', `${num(c.keep_days)} days`),
                infoRow('Disabled trackers', c.include_disabled ? 'shown' : 'left out'),
                infoRow('Log level', escapeHtml(c.log_level)),
            ].join(''))}
            ${panel('How it works', '', `<div class="guest-notes">Every device tracker in Home Assistant is read live over its websocket. Trackers of different integrations that see the same MAC address (UniFi, Nmap and your router for the same phone) are one device here.

<b>Renaming</b> changes the tracker's name in Home Assistant's entity registry, and its device's name when you tick that. Clear the name to get the integration's own name back. ${legacy ? `${legacy} tracker${legacy > 1 ? 's come' : ' comes'} from known_devices.yaml and can't be renamed from here.` : ''}

<b>Kind, icon, network, connection, notes and the map</b> are kept by this app (in /data/network.json, in Home Assistant's backups of the app). "Use in HA" gives the tracker the icon in Home Assistant too.

<b>Online</b> means a tracker says home. GPS trackers of the mobile app say where a phone is, not whether it is on the network: they are left out of the map and the statistics, and shown under Devices → GPS.

<b>Connections</b>: "Draw connections" on the map uses what Home Assistant knows (the access point and switch port of a UniFi client, the device a device is connected through) and the "Connected to" you set on a device.</div>`, {cls: 'wide'})}
        </div>`;
}

onTab('info', () => renderInfo(), () => renderInfo());
