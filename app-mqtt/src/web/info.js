// MQTT dashboard - Info: Home Assistant's connection to this broker (and switching it over from another
// broker), how devices connect, the logins, and what the app keeps.

async function loadConnection() {
    app.connection = await api('connection');
    return app.connection;
}

function renderInfo() {
    const el = document.getElementById('tab-info');
    const c = app.connection;
    const s = app.status;
    if (!c || !s) { el.innerHTML = '<div class="empty-state">…</div>'; return; }
    const host = window.parent !== window ? window.parent.location.hostname : window.location.hostname;
    const entry = c.mqtt_entry;
    const haPanel = s.ha.connected
        ? `<div class="all-good"><span class="sdot ok"></span> Home Assistant is connected to this broker, as <span class="mono">${escapeHtml(s.ha.user || 'a user')}</span>.</div>`
        : entry
            ? `<p>Home Assistant's MQTT integration (<b>${escapeHtml(entry.title || 'MQTT')}</b>, ${escapeHtml(entry.state || '')}) uses another broker. Home Assistant only offers a new broker while MQTT isn't set up, so switch it here: the same as its <i>Reconfigure</i>, with this broker's address and the app's own <span class="mono">homeassistant</span> login. Your MQTT devices and entities stay as they are.</p>
               <div class="detail-actions"><button class="btn btn-primary" id="info-switch">Switch Home Assistant to this broker…</button></div>`
            : `<p>MQTT isn't set up in Home Assistant yet. Home Assistant offers it with this broker: <b>Settings → Devices &amp; services</b>, under <i>Discovered</i>. Click <i>Add</i> there.</p>`;
    el.innerHTML = `
        <div class="dash-grid">
            ${panel('Home Assistant', '', `${haPanel}${c.error ? `<p class="warn-text">${escapeHtml(c.error)}</p>` : ''}`)}
            ${panel('Moving from the Mosquitto app', '', `<ol class="steps">
                <li>Copy the <b>logins</b> from the Mosquitto app's configuration to this app's. Home Assistant users work without that.</li>
                <li>Stop the Mosquitto app and turn off its <i>Start on boot</i>: both use ports 1883 and 1884, and only one app can be the <span class="mono">mqtt</span> service of the other apps.</li>
                <li>Start this app. Restart the apps that use MQTT (Zigbee2MQTT, ESPHome…): they find this broker as the <span class="mono">mqtt</span> service.</li>
                <li>Switch Home Assistant to this broker (above). Devices on your network keep their settings: the address and ports stay the same.</li>
                <li>Retained messages don't move: devices send them again when they reconnect. Zigbee2MQTT and Tasmota do; a device that doesn't, sends them at its next restart.</li>
            </ol>`)}
        </div>
        <div class="dash-grid">
            ${panel('Connecting a device', '', `<div class="info-grid">
                ${infoRow('Address', `<span class="mono">${escapeHtml(host)}</span> <span class="muted">(Home Assistant's address)</span>`)}
                ${c.listeners.map(l => infoRow(l.name, `<span class="mono">${l.port}</span>${l.tls ? ' <span class="muted">TLS, with the certificate in /ssl</span>' : ''}`)).join('')}
                ${infoRow('Other apps', `<span class="mono">${escapeHtml(c.hostname)}:1883</span> <span class="muted">or the mqtt service</span>`)}
            </div>
            ${s.config.ssl ? '' : '<p class="note">TLS (8883, 8884) starts when the certificate and key of the options are in Home Assistant\'s ssl folder.</p>'}`)}
            ${panel('Logins', '', `<div class="info-grid prose">
                ${infoRow('Home Assistant users', 'every user can log in with their own name and password; make one for each device under Settings → People → Users')}
                ${infoRow('Local logins', c.logins.length ? c.logins.map(l => `<span class="mono">${escapeHtml(l)}</span>`).join(', ') : '<span class="muted">none (the logins option)</span>')}
                ${infoRow('The app\'s own', '<span class="mono">homeassistant</span>, <span class="mono">addons</span> and <span class="mono">mqtt-dashboard</span>: made by the app, with passwords nobody has to know. Don\'t use these names.')}
                ${infoRow('Anonymous', 'not allowed: every client logs in')}
            </div>`)}
        </div>
        <div class="dash-grid">
            ${panel('What the dashboard keeps', '', `<div class="info-grid prose">
                ${infoRow('Clients', 'from the broker\'s log since it started: address, login, protocol, subscriptions, every connect and disconnect')}
                ${infoRow('Topics', `every topic since the dashboard started, with counts and ${s.config.capture_payloads ? 'its last value and the values before it' : '<b>no values</b> (capture_payloads is off), except Home Assistant\'s discovery and availability'}; in memory only`)}
                ${infoRow('History', `a sample of the broker every minute, ${num(s.config.keep_days)} days, in <span class="mono">/data/history.sqlite3</span> (in Home Assistant's backups)`)}
                ${infoRow('Cost', 'the dashboard is one more subscriber to every message: about one more delivery per message')}
            </div>`)}
            ${panel('About', '', `<div class="info-grid prose">
                ${infoRow('Broker', `Eclipse Mosquitto ${escapeHtml(s.broker.version || '')}, logins by mosquitto-go-auth, the same as the Mosquitto app`)}
                ${infoRow('App', `MQTT ${escapeHtml(s.version)} · <a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-mqtt/DOCS.md" target="_blank" rel="noopener" class="plain-link">documentation</a> · <a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-mqtt/CHANGELOG.md" target="_blank" rel="noopener" class="plain-link">changes</a>`)}
                ${infoRow('Uptime', escapeHtml(fmtUptime(s.broker.uptime)))}
            </div>`)}
        </div>`;
    const button = document.getElementById('info-switch');
    if (button) button.addEventListener('click', switchHomeAssistant);
}

async function switchHomeAssistant() {
    const ok = await confirmDialog({
        title: 'Switch Home Assistant to this broker?', confirmLabel: 'Switch',
        message: `<p>Home Assistant's MQTT integration gets this broker's address and the <span class="mono">homeassistant</span> login, and reloads. Home Assistant tests the connection first; nothing changes when that fails.</p>
            <p>Stop the other broker afterwards (or first, when it uses the same ports).</p>`,
    });
    if (!ok) return;
    try {
        const r = await apiPost('ha/switch', {});
        toast(r.message || 'Done', 'success');
        setTimeout(() => loadConnection().then(() => { if (activeTab === 'info') renderInfo(); }), 3000);
    } catch (e) { toast(e.message); }
}

onTab('info', () => {
    renderInfo();
    loadConnection().then(() => { if (activeTab === 'info') renderInfo(); }).catch(e => toast(e.message));
}, () => {});
