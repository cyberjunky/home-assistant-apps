// Lottery dashboard - Info: the miners, the sensors, the data.

let entityList = null;

onTab('info', () => {
    renderInfo();
    api('entities').then(list => { entityList = list; renderEntities(); }).catch(() => {});
}, renderInfo);

function renderInfo() {
    const s = app.status;
    if (!s) return;
    const rows = miners().map(m => `<tr>
        ${td(`<span style="color:${minerColor(m.slug)}">●</span> <b>${escapeHtml(m.name)}</b>`)}
        ${td(`<a href="http://${escapeHtml(m.host)}" target="_blank" rel="noopener" class="mono">${escapeHtml(m.host)}</a>`)}
        ${td(escapeHtml(modelText(m) || '–'))}
        ${td(escapeHtml(m.values.version || '–'), 'mono')}
        ${td(stateBadge(m))}
        ${td(escapeHtml(m.connected ? 'now' : fmtAgo(m.last_seen)), 'muted')}
    </tr>`);
    document.getElementById('set-miners').innerHTML = table(
        [{label: 'Miner'}, {label: 'Address'}, {label: 'Model'}, {label: 'Firmware'}, {label: 'State'}, {label: 'Last answer'}], rows,
        {empty: 'No miners configured', emptySub: 'Add each one with a name and its IP address under Miners in the app\'s configuration.'});

    const net = s.network;
    document.getElementById('set-odds').innerHTML = `
        <p class="panel-sub">Every block is a draw. Your chance of the next one is your hash rate against the whole network's: the network difficulty × 2³² hashes are needed on average, and the network finds a block every ten minutes. Over a day that is 144 draws. The chance doesn't grow with time spent; the best share you found doesn't bring the next block closer.</p>
        <div class="info-grid" style="margin-top:10px">
            ${infoRow('Network difficulty', `${escapeHtml(diff(net.difficulty))} <span class="muted">${net.difficulty_source === 'miners' ? 'from your miners\' pool' : net.difficulty_source === 'mempool' ? 'from mempool.space' : ''}</span>`)}
            ${infoRow('Network hash rate', escapeHtml(net.difficulty ? rateHs(net.difficulty * 2 ** 32 / 600) : '–'))}
            ${infoRow('Block height', escapeHtml(num(net.height)))}
            ${infoRow('Block reward', missing(net.subsidy) ? '–' : `${num(net.subsidy, 3)} BTC${missing(net.fees) ? '' : ` + about ${num(net.fees, 3)} BTC in fees`}`)}
            ${infoRow('mempool.space', net.enabled ? (net.error ? `<span class="neg">${escapeHtml(net.error)}</span>` : net.updated ? `read ${escapeHtml(fmtAgo(net.updated))}` : 'not read yet') : 'off (network_data)')}
        </div>
        <p class="panel-sub" style="margin-top:10px"><b>Luck</b> is this session's best share against the best share that is usual for the work done since the miner started: half the time you do better, half the time worse. 1× is usual, 10× is very lucky. <b>A new best</b> is the chance of a share above your best ever within a day.</p>`;

    document.getElementById('set-mqtt').innerHTML = `<div class="info-grid">
        ${infoRow('MQTT', !s.config.mqtt_enabled ? 'off' : s.mqtt.broker ? `<span class="${s.mqtt.connected ? 'pos' : 'neg'}">●</span> ${escapeHtml(s.mqtt.broker)}` : 'no broker: install the Mosquitto app or set mqtt.host')}
        ${infoRow('Topics', `<span class="mono">${escapeHtml(s.config.mqtt_base_topic)}/&lt;miner&gt;/state</span>, Mining switch <span class="mono">…/mining/set</span>, Restart <span class="mono">…/restart/set</span>`)}
    </div>
    <p class="panel-sub" style="margin-top:10px">Each miner is a device with the sensors the Bitaxe integration makes, its Mining switch and Restart button, and a Lottery device has the numbers for all of them. Coming from the integration: remove it first, and give each miner here the name its device had. The entities then get the same entity ids, so dashboards and automations keep working.</p>`;

    const counts = s.database;
    document.getElementById('set-data').innerHTML = `<div class="info-grid">
        ${infoRow('Readings', `${num(counts.samples)} <span class="muted">one a minute per miner, the last 31 days</span>`)}
        ${infoRow('Best shares', num(counts.bests))}
        ${infoRow('Days', `${num(counts.days)}${counts.since ? ` <span class="muted">since ${escapeHtml(fmtIsoDate(counts.since))}</span>` : ''}`)}
        ${infoRow('Events', num(counts.events))}
    </div>`;

    document.getElementById('set-about').innerHTML = `<div class="info-grid">
        ${infoRow('Version', escapeHtml(s.version))}
        ${infoRow('Read every', `${num(s.config.poll_seconds)} s`)}
        ${infoRow('Source', '<a href="https://github.com/cyberjunky/home-assistant-apps/tree/main/app-lottery" target="_blank" rel="noopener">github.com/cyberjunky/home-assistant-apps</a>')}
        ${infoRow('Note', 'An unofficial app, not made by or associated with the Bitaxe, NerdQAxe or Lucky Miner makers.')}
    </div>`;
}

function renderEntities() {
    const el = document.getElementById('set-entities');
    if (!entityList) return;
    el.innerHTML = Object.entries(entityList).map(([device, list]) => {
        if (!list.length) return `<details class="entities"><summary><b>${escapeHtml(device)}</b> <span class="muted">not seen yet</span></summary></details>`;
        const counts = {};
        list.forEach(e => { counts[e.platform] = (counts[e.platform] || 0) + 1; });
        const summary = Object.entries(counts).map(([p, n]) => `${n} ${p.replace('_', ' ')}${n > 1 ? 's' : ''}`).join(', ');
        const rows = list.map(e => `<tr>${td(escapeHtml(e.name))}${td(escapeHtml(e.platform.replace('_', ' ')), 'muted')}${td(escapeHtml(e.unit || ''), 'mono')}${td(escapeHtml(e.command ? 'control' : e.category || ''), 'muted')}</tr>`);
        return `<details class="entities"><summary><b>${escapeHtml(device)}</b> <span class="muted">${escapeHtml(summary)}</span></summary>
            ${table([{label: 'Name'}, {label: 'Kind'}, {label: 'Unit'}, {label: ''}], rows, {cls: 'compact'})}</details>`;
    }).join('');
}

async function deleteAllData() {
    const ok = await confirmDialog({
        title: 'Delete the app\'s history?',
        message: 'The readings, best shares, days, events and counters are deleted. The miners keep their own numbers.',
        typeToConfirm: 'DELETE', danger: true, confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
        await apiPost('data/delete', {confirm: 'DELETE'});
        toast('The history was deleted', 'ok');
        loadStatus();
    } catch (e) {
        toast(e.message);
    }
}
