// Bitcoin Node dashboard - the pages about LND, Tor and the app itself.

// =====================================================================
// Lightning
// =====================================================================
function liquidityBar(local, remote, capacity, widest) {
    const scale = capacity / Math.max(1, widest) * 100;
    const out = local / Math.max(1, local + remote) * 100;
    return `<div class="liq-track"><div class="liq" style="width:${scale.toFixed(1)}%" title="can send ${escapeHtml(sat(local))} · can receive ${escapeHtml(sat(remote))}">
        <i class="out" style="width:${out.toFixed(1)}%"></i><i class="in" style="width:${(100 - out).toFixed(1)}%"></i></div></div>`;
}
// A channel that can hardly send or hardly receive is worth a look
function balanceNote(local, remote) {
    const share = local / Math.max(1, local + remote);
    if (share < 0.15) return badge('warn', 'cannot send much');
    if (share > 0.85) return badge('warn', 'cannot receive much');
    return '';
}
const LN_KINDS = {forward: 'ok', invoice: 'ok', payment: ''};

function backupHtml(backup) {
    if (backup.error && !backup.available) return `<div class="note warn">The channel backup can't be read: ${escapeHtml(backup.error)}</div>`;
    if (!backup.available) return '<div class="empty-state">No channel backup stored yet. LND is asked for it every five minutes.</div>';
    const state = backup.error ? stateLabel('warn', 'could not be read again')
        : backup.complete === false ? stateLabel('warn', 'misses a channel opened since')
            : backup.complete ? stateLabel('ok', 'holds every channel') : stateLabel('', 'from an earlier run');
    return [
        infoRow('State', state),
        infoRow('Saved', `${escapeHtml(fmtDateTime(backup.saved_at))} · ${escapeHtml(fmtAgo(backup.saved_at))}`),
        infoRow('Channels in it', missing(backup.channels) ? '–' : num(backup.channels)),
        infoRow('Copies kept', `${num(backup.copies)} <span class="muted">· the newest 20, in the app's storage</span>`),
        infoRow('Size', escapeHtml(fmtBytes(backup.size))),
    ].join('') + `<div class="detail-actions" style="margin-top:12px"><a class="btn" href="api/channel-backup" download>Download channel backup</a></div>
        <div class="note" style="margin-top:10px">LND's own backup of all channels (channel.backup), stored again whenever a channel opens or closes. It is encrypted and only of use together with the wallet's 24 words. Restoring is done on the node with <span class="mono">lncli restorechanbackup</span>, never from here: it closes every channel.</div>`;
}

onTab('lightning', async () => {
    const s = app.status, ln = s.lightning, c = ln.channels;
    const el = document.getElementById('tab-lightning');
    if (!ln.configured || !ln.pubkey) {
        delete el.dataset.built;
        el.innerHTML = `<div class="panel"><div class="panel-body"><div class="empty-state">${ln.configured ? 'Waiting for the first answer from LND…' : 'No Lightning node set up.'}</div></div></div>`;
        return;
    }
    skeleton('lightning', `
        <div id="ln-head" class="bot-head"></div>
        <div id="ln-tiles" class="tiles kpi-tiles"></div>
        ${panel('Channels', '<span class="legend" id="ln-chan-legend" style="margin:0"></span>', '<div id="ln-channels" class="table-wrap"></div>')}
        <div class="stats-grid" style="margin-top:14px">
            ${chartBox('Payments forwarded per day', hintSpan('ln-fwd-hint'), 'ln-fwd')}
            ${chartBox('Routing fees earned per day', hintSpan('ln-fee-hint'), 'ln-fee')}
        </div>
        <div class="dash-grid wide-left">
            ${chartBox('Balance', rangeBox('ln-range'), 'ln-balance', '<div class="legend" id="ln-balance-legend"></div>')}
            ${panel('Where the funds are', hintSpan('ln-funds-hint'), '<div id="ln-funds"></div>')}
        </div>
        <div class="dash-grid wide-left">
            ${panel('Recent payments', 'forwarded, received and paid', '<div id="ln-recent" class="table-wrap"></div>')}
            <div class="dash-stack">
                ${panel('This Lightning node', '', '<div id="ln-node"></div>')}
                ${panel('Channel backup', 'kept by the app', '<div id="ln-backup"></div>')}
            </div>
        </div>`);
    rangeChips('ln-range');
    fill('ln-head', `<h1>${escapeHtml(ln.alias)}</h1>
        <span class="badges">${badge(ln.synced_chain ? 'ok' : 'warn', ln.synced_chain ? 'CHAIN IN SYNC' : 'CHAIN BEHIND')}${badge(ln.synced_graph ? 'ok' : 'warn', ln.synced_graph ? 'GRAPH IN SYNC' : 'GRAPH SYNCING')}${badge('mode', 'READ-ONLY')}</span>
        <span class="meta mono" title="${escapeHtml(ln.pubkey)}">${escapeHtml(shorten(ln.pubkey, 12, 10))}</span>`);
    const total = ln.onchain.confirmed + c.local + c.pending_local;
    fill('ln-tiles', [
        tile('Total funds', satHtml(total), escapeHtml(btcOfSat(total))),
        tile('On-chain wallet', satHtml(ln.onchain.confirmed), ln.onchain.unconfirmed ? `${escapeHtml(sat(ln.onchain.unconfirmed))} unconfirmed` : 'all confirmed'),
        tile('Can send', satHtml(c.local), `${escapeHtml(plainPct(c.local / Math.max(1, c.local + c.remote), 0))} of the channel balance`),
        tile('Can receive', satHtml(c.remote), `${escapeHtml(sat(c.capacity))} capacity in all`),
        tile('Channels', `${num(c.active)}<small> of ${num(c.active + c.inactive)}</small>`, c.pending ? `${num(c.pending)} pending` : 'none pending', c.inactive ? 'warn' : ''),
        tile('Earned in 30 days', satHtml(ln.fees.month), `today ${escapeHtml(num(ln.fees.day))} · 7 days ${escapeHtml(num(ln.fees.week))}`, 'pos'),
    ].join(''));
    fill('ln-chan-legend', legendHtml([{name: 'can send', color: color('series-1')}, {name: 'can receive', color: color('series-3')}]));
    fill('ln-node', [
        infoRow('Alias', escapeHtml(ln.alias)),
        infoRow('Public key', `<span title="${escapeHtml(ln.pubkey)}">${escapeHtml(shorten(ln.pubkey, 16, 12))}</span>`),
        infoRow('Address', ln.uris.map(u => `<span title="${escapeHtml(u)}">${escapeHtml(shorten(u.split('@')[1] || u, 18, 16))}</span>`).join('<br>') || 'none announced'),
        infoRow('Peers', num(ln.peers)),
        infoRow('Block height', num(ln.block_height)),
        infoRow('LND', escapeHtml(ln.version)),
        infoRow('Access', 'read-only: this app cannot pay or open channels'),
    ].join(''));
    fill('ln-backup', backupHtml(ln.backup));

    const [data, h] = await Promise.all([api('lightning'), loadHistory()]);
    app.lightning = data;
    const widest = Math.max(1, ...data.channels.map(ch => ch.capacity), ...data.pending.map(p => p.capacity));
    const rows = data.channels.map(ch => `<tr>
        ${td(`<span class="name">${escapeHtml(ch.alias)}</span><span class="sub mono" title="${escapeHtml(ch.pubkey)}">${escapeHtml(shorten(ch.pubkey, 10, 6))}</span>`)}
        ${td(`${stateLabel(ch.active ? 'ok' : 'bad', ch.active ? 'active' : 'offline')}${ch.private ? ' ' + badge('mode', 'private') : ''}`)}
        ${td(escapeHtml(sat(ch.local)), 'n')}
        ${td(liquidityBar(ch.local, ch.remote, ch.capacity, widest), 'liq-cell')}
        ${td(escapeHtml(sat(ch.remote)), 'n')}
        ${td(balanceNote(ch.local, ch.remote))}
        ${td(missing(ch.fee_ppm) ? '–' : `${num(ch.fee_ppm)} ppm<span class="sub">base ${num(ch.fee_base_msat / 1000, ch.fee_base_msat % 1000 ? 3 : 0)} sat</span>`, 'n')}
        ${td(`${escapeHtml(btcOfSat(ch.sent + ch.received).replace(' BTC', ''))}<span class="sub">BTC moved</span>`, 'n')}
        ${td(missing(ch.uptime) ? '–' : escapeHtml(plainPct(ch.uptime)), 'n' + (!missing(ch.uptime) && ch.uptime < 0.9 ? ' neg' : ''))}
        ${td(ch.opened ? `<span title="Going by the block the channel was opened in">${escapeHtml(fmtDate(ch.opened))}</span>` : '–', 'n muted')}
    </tr>`).concat(data.pending.map(p => `<tr>
        ${td(`<span class="name">${escapeHtml(p.alias)}</span><span class="sub">waiting for the chain</span>`)}
        ${td(stateLabel('warn', p.kind))}
        ${td(escapeHtml(sat(p.local)), 'n')}
        ${td(liquidityBar(p.local, p.capacity - p.local, p.capacity, widest), 'liq-cell pending')}
        ${td('–', 'n')}${td('')}${td('–', 'n')}${td('–', 'n')}${td('–', 'n')}${td('–', 'n muted')}
    </tr>`));
    fill('ln-channels', table(
        [{label: 'Peer'}, {label: 'State'}, {label: 'Can send', n: 1}, {label: 'Balance · bar width is the capacity'}, {label: 'Can receive', n: 1}, {label: ''},
            {label: 'Your fee', n: 1}, {label: 'Volume', n: 1}, {label: 'Uptime', n: 1}, {label: 'Opened', n: 1}],
        rows, {cls: 'hover', empty: 'No channels yet'}));

    const days = data.forwards_daily;
    const best = days.reduce((a, d) => d.fees > a.fees ? d : a, days[0]);
    const volume = days.reduce((a, d) => a + d.volume, 0);
    const fees = days.reduce((a, d) => a + d.fees, 0);
    fill('ln-fwd-hint', `${num(ln.forwards_30d)} in 30 days · ${num(ln.forwards_30d / 30, 1)} a day`);
    fill('ln-fee-hint', fees ? `${num(fees / Math.max(1, volume) * 1e6)} ppm on average · best day ${escapeHtml(fmtDay(best.day))} with ${escapeHtml(satFine(best.fees))}` : 'nothing earned yet');
    const dayKey = `${days.length}:${days[0].day}:${days[days.length - 1].count}:${fees}`;
    const dayData = pick => days.map(d => ({label: fmtDay(d.day), tipTitle: new Date(d.day * 1000).toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'}), values: [pick(d)]}));
    drawOnce('ln-fwd', dayKey, el2 => barChart(el2, {data: dayData(d => d.count), series: [{name: '', color: color('series-1')}], format: v => `${num(v)} payments`, height: 170, labelWidth: 56, empty: 'Nothing forwarded in the last 30 days'}));
    drawOnce('ln-fee', dayKey, el2 => barChart(el2, {data: dayData(d => d.fees), series: [{name: '', color: color('pos')}], format: v => satFine(v), height: 170, labelWidth: 56, empty: 'Nothing earned in the last 30 days'}));

    const balance = [
        {key: 'ln_local', name: 'can send', color: color('series-1')},
        {key: 'ln_remote', name: 'can receive', color: color('series-3')},
        {key: 'ln_onchain', name: 'on-chain wallet', color: color('series-4')},
    ];
    fill('ln-balance-legend', legendHtml(balance.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    drawOnce('ln-balance', historyKey(h), () => historyChart('ln-balance', h, balance, v => sat(Math.round(v)), {height: 200, formatTick: v => `${num(v / 1e6, 1)}M`}));
    fill('ln-funds-hint', escapeHtml(btcOfSat(total)));
    drawOnce('ln-funds', `${ln.onchain.confirmed}:${c.local}:${c.pending_local}:${c.unsettled}`, el2 => donutChart(el2, [
        {name: 'In channels, can send', value: Math.max(0, c.local - c.unsettled), color: color('series-1')},
        {name: 'On-chain wallet', value: ln.onchain.confirmed, color: color('series-4')},
        {name: 'In channels being opened', value: c.pending_local, color: color('series-5')},
        {name: 'In payments under way', value: c.unsettled, color: color('series-2')},
    ], {label: 'SAT', value: fmtBig(total).replace(' ', '')}, v => sat(v)));
    fill('ln-recent', table(
        [{label: 'When'}, {label: 'Kind'}, {label: 'Amount', n: 1}, {label: 'Fee', n: 1}, {label: 'Detail'}],
        data.recent.slice(0, 16).map(r => `<tr>
            ${td(`<span title="${escapeHtml(new Date(r.ts * 1000).toLocaleString())}">${escapeHtml(fmtAgo(r.ts))}</span>`, 'muted')}
            ${td(badge(LN_KINDS[r.kind], r.kind === 'forward' ? 'forwarded' : r.kind === 'invoice' ? 'received' : 'paid'))}
            ${td(escapeHtml(r.kind === 'forward' ? sat(r.amount) : signedSat(r.amount)), 'n ' + (r.kind === 'forward' ? '' : pnlClass(r.amount)))}
            ${td(r.fee ? escapeHtml(satFine(r.fee, true)) : '–', 'n ' + pnlClass(r.fee))}
            ${td(escapeHtml(r.text), 'muted')}
        </tr>`), {cls: 'hover', empty: 'No payments yet'}));
});

// =====================================================================
// Tor
// =====================================================================
const CIRCUIT_PURPOSES = {
    GENERAL: 'outgoing connections',
    HS_SERVICE_INTRO: 'own address, way in',
    HS_SERVICE_REND: 'own address, peer connected',
    HS_CLIENT_REND: 'to a peer\'s address',
    HS_CLIENT_INTRO: 'to a peer\'s address, first contact',
    HS_CLIENT_HSDIR: 'looking up a peer\'s address',
    HS_SERVICE_HSDIR: 'publishing the own address',
    HS_VANGUARDS: 'spare, kept ready',
    CONFLUX_LINKED: 'outgoing connections, split in two',
    MEASURE_TIMEOUT: 'measuring',
};

onTab('tor', async () => {
    skeleton('tor', `
        <div id="tor-tiles" class="tiles kpi-tiles"></div>
        ${panel('Onion addresses', 'how others reach this node without knowing where it is', '<div id="tor-services"></div>')}
        <div id="tor-control" class="gap-top">
            <div class="dash-grid wide-left">
                ${chartBox('Traffic through Tor', rangeBox('tor-range'), 'tor-traffic', '<div class="legend" id="tor-traffic-legend"></div><div class="chart-stats" id="tor-traffic-stats"></div>')}
                ${panel('Circuits by purpose', hintSpan('tor-purpose-hint'), '<div id="tor-purpose"></div>')}
            </div>
            ${panel('Circuits', hintSpan('tor-circuits-hint'), '<div id="tor-circuits" class="table-wrap"></div>')}
        </div>
        <div class="note" style="margin-top:14px" id="tor-note"></div>`);
    rangeChips('tor-range');
    const s = app.status, t = s.tor;
    const onion = s.net.networks.find(n => n.name === 'onion');
    const onionPeers = s.net.peer_networks.onion || 0;
    const tiles = [
        tile('Onion addresses', num(t.onion_services.length), t.onion_services.length ? 'this node can be reached through Tor' : 'none announced'),
        tile('Peers through Tor', num(onionPeers), `of ${num(s.net.peers)} peers`),
        tile('Bitcoin Core', onion && onion.reachable ? 'uses Tor' : 'no Tor', onion && onion.proxy ? `proxy ${escapeHtml(onion.proxy)}` : '', onion && onion.reachable ? 'pos' : ''),
    ];
    if (t.configured) {
        tiles.push(
            tile('Tor', t.bootstrap === 100 ? 'ready' : missing(t.bootstrap) ? '–' : `${num(t.bootstrap)}%`, t.connected ? `bootstrap ${escapeHtml((t.bootstrap_text || '').toLowerCase())} · ${escapeHtml(t.version)}` : 'control port does not answer', t.bootstrap === 100 ? 'pos' : 'warn'),
            tile('Received', escapeHtml(fmtBytes(t.read, 1)), `${escapeHtml(fmtRate(t.read_rate))} now`),
            tile('Sent', escapeHtml(fmtBytes(t.written, 1)), `${escapeHtml(fmtRate(t.written_rate))} now`),
        );
    }
    fill('tor-tiles', tiles.join(''));
    fill('tor-services', t.onion_services.map(o => infoRow(escapeHtml(o.name), `${escapeHtml(o.address)}${o.port ? ':' + o.port : ''}`)).join('')
        || '<div class="empty-state">Neither Bitcoin Core nor LND announces an onion address.</div>');
    document.getElementById('tor-control').hidden = !t.configured;
    fill('tor-note', t.configured
        ? 'Bootstrap, circuits and traffic come from Tor\'s control port. The onion addresses and the peers come from Bitcoin Core and LND.'
        : 'Tor\'s own state (bootstrap, circuits, traffic) needs its control port, which is optional: enter it under <b>tor</b> in the app\'s configuration. The onion addresses and the peers above come from Bitcoin Core and LND.');
    if (!t.configured) return;

    const [data, h] = await Promise.all([api('tor'), loadHistory()]);
    app.tor = data;
    const purposes = new Map();
    data.circuits.forEach(c => purposes.set(c.purpose, (purposes.get(c.purpose) || 0) + 1));
    const shades = ['series-1', 'series-3', 'series-4', 'series-2', 'series-5'];
    drawOnce('tor-purpose', [...purposes.entries()].join('|'), el => donutChart(el,
        [...purposes.entries()].sort((a, b) => b[1] - a[1]).map(([purpose, count], i) => ({name: CIRCUIT_PURPOSES[purpose] || purpose.toLowerCase().replace(/_/g, ' '), value: count, color: color(shades[i % shades.length])})),
        {label: 'CIRCUITS', value: String(data.circuits.length)}, v => `${num(v)} circuits`));
    fill('tor-purpose-hint', `${num(t.circuits_built)} of ${num(t.circuits)} built`);
    fill('tor-circuits-hint', `${num(data.circuits.length)} open · each one goes through 3 or 4 relays`);
    fill('tor-circuits', table(
        [{label: 'Circuit', n: 1}, {label: 'State'}, {label: 'Used for'}, {label: 'Relays'}, {label: 'Age', n: 1}],
        [...data.circuits].sort((a, b) => (b.created || 0) - (a.created || 0)).map(c => `<tr>
            ${td(String(c.id), 'n')}
            ${td(stateLabel(c.status === 'BUILT' ? 'ok' : 'warn', c.status === 'BUILT' ? 'built' : 'building'))}
            ${td(escapeHtml(CIRCUIT_PURPOSES[c.purpose] || c.purpose.toLowerCase().replace(/_/g, ' ')))}
            ${td(c.path.map(r => `<span class="hop">${escapeHtml(r)}</span>`).join('<span class="hop-sep">→</span>'))}
            ${td(c.created ? escapeHtml(fmtDuration(Math.max(0, s.now - c.created))) : '–', 'n')}
        </tr>`), {cls: 'hover', empty: 'No circuits open'}));
    const series = [
        {key: 'tor_written_rate', name: 'sent', color: color('series-1'), area: true},
        {key: 'tor_read_rate', name: 'received', color: color('series-3')},
    ];
    fill('tor-traffic-legend', legendHtml(series.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    trafficStats('tor-traffic-stats', h, 'tor_written_rate', 'tor_read_rate');
    drawOnce('tor-traffic', historyKey(h), () => historyChart('tor-traffic', h, series, fmtRate, Object.assign({height: 200}, RATE_AXIS)));
});

// =====================================================================
// Info
// =====================================================================
const SOURCES = [
    ['Bitcoin Core', 'JSON-RPC', 'getblockchaininfo, getnetworkinfo, getmempoolinfo, getnettotals, uptime, getpeerinfo, estimatesmartfee', 'every poll: chain, mempool, fees, peers, traffic'],
    ['Bitcoin Core', 'JSON-RPC', 'getblockstats, getmininginfo, getchaintxstats, getindexinfo, getblockhash, getblockheader', 'at a new block: its statistics, hashrate, difficulty period'],
    ['Bitcoin Core', 'JSON-RPC', 'getrawmempool', 'every two minutes: what waits per fee rate'],
    ['LND', 'REST', 'getinfo, balance, channels, pending channels, fees', 'every poll: wallet and channels'],
    ['LND', 'REST', 'forwarding history, payments, invoices, channel backup', 'every five minutes: routing, payments, the backup'],
    ['Tor', 'control port', 'GETINFO version, bootstrap phase, traffic, circuit status', 'every poll: bootstrap, traffic, circuits'],
    ['ckpool', 'HTTP, its logs folder', 'pool/pool.status, users/&lt;address&gt;', 'every poll, at least 30 s: pool, workers, best shares'],
];

onTab('info', () => {
    skeleton('info', `
        <div class="page-section"><span>Connections</span><small>change these in the app's configuration in Home Assistant · passwords and the macaroon are never shown</small></div>
        <div class="info-grid">
            ${panel('Bitcoin Core', '', '<div id="info-bitcoin"></div>')}
            ${panel('LND', '', '<div id="info-lnd"></div>')}
            ${panel('Tor', '', '<div id="info-tor"></div>')}
            ${panel('Mining pool', '', '<div id="info-pool"></div>')}
            ${panel('MQTT', '', '<div id="info-mqtt"></div>')}
        </div>
        <div class="page-section"><span>Data</span><small>what this app keeps itself</small></div>
        <div class="info-grid">
            <div class="panel"><div class="panel-body">
                <div class="section-title">History</div>
                <div id="info-data"></div>
                <div class="detail-actions" style="margin-top:12px"><a class="btn" href="api/backup" download>Database backup</a></div>
                <div class="danger-zone">
                    <div class="section-title">Danger zone</div>
                    <p class="panel-sub">Deletes the history behind the charts and the list of what happened. The node, the wallet and the channel backups are not touched.</p>
                    <button type="button" class="btn btn-danger" onclick="deleteAllData()">Delete history</button>
                </div>
            </div></div>
            ${panel('About', '', '<div id="info-about"></div>')}
        </div>
        <div class="page-section"><span>Where the numbers come from</span><small>the app only reads</small></div>
        <div class="panel"><div class="panel-body"><div id="info-sources" class="table-wrap"></div></div></div>`);
    const s = app.status, c = s.config, n = s.node, ln = s.lightning, t = s.tor, p = s.pool;
    const link = (configured, connected, error) => !configured ? '<span class="muted">not set up</span>'
        : connected ? stateLabel('ok', 'connected') : `<span class="state bad" title="${escapeHtml(error || '')}">not reachable</span>`;
    fill('info-bitcoin', [
        infoRow('State', link(n.configured, n.connected, n.error)),
        infoRow('Address', escapeHtml(c.rpc_url || '–')),
        infoRow('RPC user', escapeHtml(c.rpc_user || '–')),
        infoRow('Version', escapeHtml(n.version || '–')),
        infoRow('Read every', `${num(s.poll_seconds)} s`),
    ].join(''));
    fill('info-lnd', [
        infoRow('State', link(ln.configured, ln.connected, ln.error)),
        infoRow('Address', escapeHtml(c.lnd_url || '–')),
        infoRow('Certificate', !c.lnd_url ? '–' : !c.lnd_url.startsWith('https') ? '<span class="muted">http</span>' : c.lnd_tls_cert ? 'checked against tls_cert' : '<span class="neg">not checked</span> <span class="muted">· set tls_cert</span>'),
        infoRow('Read-only', c.lnd_read_only ? 'on <span class="muted">· this version only reads either way</span>' : 'off <span class="muted">· this version only reads either way</span>'),
        infoRow('Version', escapeHtml(ln.version || '–')),
    ].join(''));
    fill('info-tor', [
        infoRow('Control port', t.configured ? `${link(true, t.connected, t.error)} · ${escapeHtml(c.tor_control)}` : '<span class="muted">not set up · optional</span>'),
        infoRow('Version', escapeHtml(t.version || '–')),
        infoRow('Onion addresses', num(t.onion_services.length)),
    ].join(''));
    fill('info-pool', [
        infoRow('ckpool status', p.configured ? `${link(true, p.connected, p.error)} · ${escapeHtml(c.pool_url)}` : '<span class="muted">not set up · optional</span>'),
        infoRow('Last status', p.configured ? escapeHtml(fmtAgo(p.last_update)) : '–'),
        infoRow('Workers', p.configured ? num(p.active_workers) : '–'),
    ].join(''));
    fill('info-mqtt', [
        infoRow('Publishing', c.mqtt_enabled ? 'on' : 'off'),
        infoRow('Broker', s.mqtt.broker ? `${escapeHtml(s.mqtt.broker)} · ${s.mqtt.connected ? stateLabel('ok', 'connected') : stateLabel('bad', 'offline')}` : 'none'),
        infoRow('Topics', `<span class="mono">${escapeHtml(c.mqtt_base_topic)}/bitcoin/state</span>, <span class="mono">…/lightning/state</span>, <span class="mono">…/event</span>`),
        infoRow('Discovery prefix', escapeHtml(c.mqtt_discovery_prefix)),
    ].join(''));
    const db = s.database;
    fill('info-data', [
        infoRow('Samples', `${num(db.samples)} <span class="muted">every 2 min, kept ${num(c.history_days)} days</span>`),
        infoRow('Events', `${num(db.events)} <span class="muted">kept 180 days</span>`),
        infoRow('Blocks', `${num(db.blocks)} <span class="muted">the latest day</span>`),
        infoRow('Since', db.oldest ? escapeHtml(fmtDateTime(db.oldest)) : '–'),
        infoRow('Size', escapeHtml(fmtBytes(db.size))),
    ].join(''));
    fill('info-about', [
        infoRow('Version', escapeHtml(s.version)),
        infoRow('Changelog', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-bitcoin-node/CHANGELOG.md" target="_blank" rel="noopener">on GitHub</a>'),
        infoRow('Documentation', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-bitcoin-node/DOCS.md" target="_blank" rel="noopener">on GitHub</a>'),
        infoRow('Log level', escapeHtml(c.log_level)),
        infoRow('Note', 'Unofficial: not made by or affiliated with the Bitcoin Core, LND or Tor projects'),
    ].join(''));
    fill('info-sources', table(
        [{label: 'Service'}, {label: 'Through'}, {label: 'Reads'}, {label: 'For'}],
        SOURCES.map(r => `<tr>${td(`<span class="name">${r[0]}</span>`)}${td(r[1])}${td(r[2], 'wrap mono muted')}${td(r[3], 'wrap')}</tr>`)));
});

async function deleteAllData() {
    const ok = await confirmDialog({
        title: 'Delete the app\'s history?',
        message: 'Deletes the samples behind the charts and the list of what happened. Bitcoin Core, LND, the wallet and the stored channel backups are not touched.',
        typeToConfirm: 'DELETE', confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    try {
        const answer = await apiPost('data/delete', {confirm: 'DELETE'});
        toast(`Deleted ${num(answer.removed.samples)} samples and ${num(answer.removed.events)} events`, 'success');
        Object.keys(historyCache).forEach(key => delete historyCache[key]);
        await loadStatus();
    } catch (e) {
        toast(e.message);
    }
}
