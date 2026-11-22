// Vigil dashboard - Sources: every receiver and scrape target with last seen, rate and errors, and
// setup snippets to copy for the devices and collectors that send to Vigil.

let sourcesData = null;
let setupTab = 'mikrotik';
let sourcesTimer = null;

// What each snippet is for; the snippets themselves come from the server (they hold its address)
const SETUP_INFO = {
    mikrotik: {label: 'MikroTik', text: 'RouterOS logs: a remote logging action and the topics to send to it. Paste in a terminal (Winbox → New Terminal, or SSH). For the router\'s CPU, memory and interfaces, set the MikroTik (RouterOS API) options: see the documentation.'},
    netgear: {label: 'Netgear', text: 'Managed switches: System → Logs → Server Log (Syslog) in the web interface, with these settings.'},
    rsyslog: {label: 'rsyslog', text: 'Linux servers: forward everything over TCP. Put this in /etc/rsyslog.d/90-vigil.conf and restart rsyslog.'},
    vector: {label: 'Vector', text: 'Vector: journald and files to Vigil\'s JSON lines endpoint, with the ingest token.'},
    otelcol: {label: 'OTel Collector', text: 'OpenTelemetry Collector: an otlphttp exporter (JSON encoding) for logs and metrics.'},
    windows: {label: 'Windows', text: 'Windows PCs and servers: install Fluent Bit (fluentbit.io, the Windows installer), put this in its configuration and run it as a service. It sends the Security, System, Application, Defender and (when installed) Sysmon event logs. Vigil names the security events (failed logons, lockouts, new users and admins, cleared logs, new services) and tags them with their MITRE ATT&CK technique: see the Windows examples on the Logs page.'},
    node_exporter: {label: 'node_exporter', text: 'Prometheus exporters: add the exporter as a scrape target in Vigil\'s configuration (scrape_targets).'},
    json_lines: {label: 'JSON lines', text: 'Anything that can POST: one JSON object per line to the ingest port, with the ingest token.'},
    fritzbox: {label: 'FRITZ!Box', text: 'The FRITZ!Box is polled over TR-064: set these options in Vigil\'s configuration. Use a FRITZ!Box user with the "FRITZ!Box settings" permission.'},
};
const FRITZ_SNIPPET = 'fritzbox:\n  host: fritz.box\n  user: vigil\n  password: "…"';

async function showSources() {
    await loadSources();
    clearInterval(sourcesTimer);
    sourcesTimer = setInterval(() => {
        if (activeTab === 'sources' && document.visibilityState === 'visible') loadSources();
    }, 15000);
}

async function loadSources() {
    const table = document.getElementById('sources-table');
    if (!sourcesData) table.innerHTML = loadingHtml();
    try {
        sourcesData = await api('sources');
    } catch (e) {
        table.innerHTML = errorHtml(e.status === 503 ? 'The database is not connected: the receivers keep a queue until it is' : e.message);
        if (!sourcesData) renderSetup({});
        return;
    }
    renderSources();
}

function renderSources() {
    const d = sourcesData || {};
    const list = d.sources || [];
    const setup = d.setup || {};
    const s = app.status || {};
    const r = s.receivers || {};
    const errors = list.reduce((a, x) => a + (x.errors || 0), 0);
    const recent = list.filter(x => x.last_seen && Date.now() / 1000 - x.last_seen < 600).length;
    document.getElementById('sources-tiles').innerHTML = [
        tile('Sources', num(list.length), `${num(recent)} active in 10 min`),
        tile('Syslog', setup.syslog_port ? escapeHtml(setup.syslog_port) : '–', `udp ${r.syslog_udp ? 'on' : 'off'} · tcp ${r.syslog_tcp ? 'on' : 'off'}`),
        tile('OTLP / JSON', setup.otlp_port ? escapeHtml(setup.otlp_port) : '–', setup.token_set ? 'ingest token set' : 'no token: private networks only'),
        tile('Errors', `<span class="${errors ? 'txt-bad' : ''}">${num(errors)}</span>`, 'parse and scrape errors'),
        tile('Dropped', `<span class="${(s.queue || {}).dropped ? 'txt-bad' : ''}">${num((s.queue || {}).dropped)}</span>`, 'queue full or rate cap'),
    ].join('');

    const table = document.getElementById('sources-table');
    if (!list.length) {
        table.innerHTML = '<div class="empty-state">Nothing has sent anything yet. Pick your device below, copy its settings, and it shows up here within a minute.</div>';
    } else {
        const sorted = [...list].sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));
        table.innerHTML = `<table class="data"><thead><tr><th>Kind</th><th>Name</th><th>Host</th><th>Last seen</th><th class="n">Rate</th><th class="n">Received</th><th class="n">Errors</th><th>Last error</th></tr></thead><tbody>
            ${sorted.map(x => {
                const age = x.last_seen ? Date.now() / 1000 - x.last_seen : Infinity;
                const state = age < 300 ? 'ok' : age < 3600 ? 'warn' : 'bad';
                const logsLink = ['syslog', 'otlp', 'json', 'fritzbox'].includes(x.kind) && (x.host || x.name);
                // A scrape target's series carry job=<its name>; the MikroTik ones are named mktxp_*
                const metricSearch = x.kind === 'scrape' ? `job=${x.name}` : x.kind === 'mikrotik' ? 'mktxp_' : '';
                return `<tr>
                    <td><span class="badge muted-badge">${escapeHtml(x.kind)}</span></td>
                    <td>${logsLink ? `<button type="button" class="cell-link" data-host="${escapeHtml(x.host || x.name)}" title="Show its logs">${escapeHtml(x.name)}</button>`
                        : metricSearch ? `<button type="button" class="cell-link" data-metrics="${escapeHtml(metricSearch)}" title="Show its metrics">${escapeHtml(x.name)}</button>`
                        : `<b>${escapeHtml(x.name)}</b>`}</td>
                    <td class="mono">${escapeHtml(x.host || '–')}</td>
                    <td><span class="state ${state}" title="${escapeHtml(x.last_seen ? fmtDateTime(x.last_seen) : '')}">${escapeHtml(fmtAgo(x.last_seen))}</span></td>
                    <td class="n">${missing(x.rate_per_min) ? '–' : `${fmtValue(x.rate_per_min)}<small class="muted"> /min</small>`}</td>
                    <td class="n">${fmtCount(x.received)}</td>
                    <td class="n${x.errors ? ' txt-bad' : ''}">${num(x.errors)}</td>
                    <td class="wrap muted">${escapeHtml(x.last_error || '')}</td>
                </tr>`;
            }).join('')}</tbody></table>`;
        table.onclick = (e) => {
            const m = e.target.closest('[data-metrics]');
            if (m) return openMetricSearch(m.dataset.metrics);
            const b = e.target.closest('[data-host]');
            if (b) openLogs(`host:${queryValue(b.dataset.host)}`);
        };
    }
    renderSetup(setup);
}

// The server can't know Home Assistant's LAN address. The page's own host is it when Home
// Assistant is opened on the LAN (192.168.x.x, homeassistant.local); through the Nabu Casa or
// another remote URL it is a cloud name a router can't send to, so the placeholder stays.
function lanHost(ipOnly) {
    const h = window.location.hostname || '';
    const v4 = h.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
    if (v4) {
        const [a, b] = [Number(v4[1]), Number(v4[2])];
        if (a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)) return h;
        return null;
    }
    if (ipOnly) return null;
    if (/\.(local|lan|home|internal)$/i.test(h) || (h && !h.includes('.'))) return h;
    return null;
}

// Routers and switches take an IP address only (RouterOS' remote= refuses a name)
const IP_ONLY = new Set(['mikrotik', 'netgear']);

function withHost(text, placeholder, key) {
    const host = lanHost(IP_ONLY.has(key));
    return placeholder && host ? text.split(placeholder).join(host) : text;
}

function renderSetup(setup) {
    const examples = {};
    for (const [key, text] of Object.entries(setup.examples || {})) examples[key] = withHost(String(text), setup.host_placeholder, key);
    if (!examples.fritzbox) examples.fritzbox = FRITZ_SNIPPET;
    const keys = [...Object.keys(SETUP_INFO).filter(k => examples[k]), ...Object.keys(examples).filter(k => !SETUP_INFO[k])];
    if (!keys.includes(setupTab)) setupTab = keys[0];
    document.getElementById('setup-hint-ports').textContent = setup.syslog_port
        ? `syslog on port ${setup.syslog_port} · OTLP/JSON on ${setup.otlp_port || '–'}` : '';
    buildChips('setup-tabs', keys.map(k => ({key: k, label: (SETUP_INFO[k] || {label: k}).label})), setupTab, key => {
        setupTab = key;
        renderSetupBody(examples);
    });
    renderSetupBody(examples);
}

function renderSetupBody(examples) {
    const info = SETUP_INFO[setupTab] || {text: ''};
    const text = String(examples[setupTab] || '');
    const body = document.getElementById('setup-body');
    const hostHint = text.includes((sourcesData && sourcesData.setup && sourcesData.setup.host_placeholder) || 'HOME_ASSISTANT_IP')
        ? '<p class="panel-sub setup-text">Replace <b>HOME_ASSISTANT_IP</b> with Home Assistant\'s IP address on your network (such as 192.168.1.10).</p>'
        : '';
    body.innerHTML = `<p class="panel-sub setup-text">${escapeHtml(info.text)}</p>${hostHint}
        <div class="snippet"><button type="button" class="btn btn-small snippet-copy">Copy</button><pre class="code-block">${escapeHtml(text)}</pre></div>
        ${['vector', 'otelcol', 'windows'].includes(setupTab) ? `<p class="panel-sub" style="margin-top:8px">${(sourcesData && sourcesData.setup && sourcesData.setup.token_set)
            ? 'Replace the token placeholder with the <b>ingest_token</b> from Vigil\'s configuration.'
            : 'No <b>ingest_token</b> is set: only senders on private networks are accepted. Set one in Vigil\'s configuration to accept others.'}</p>` : ''}`;
    body.querySelector('.snippet-copy').onclick = () => copyPlain(text, 'Snippet');
}

onTab('sources', showSources);
onLeave('sources', () => clearInterval(sourcesTimer));
