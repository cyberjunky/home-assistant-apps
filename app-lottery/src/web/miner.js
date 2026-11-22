// Lottery dashboard - one miner's page.

const minerPage = {range: '24', kind: 'hashrate', history: null, bests: [], events: [], days: [], loadedAt: 0, slug: null, drawnMining: null};

const CHART_KINDS = [
    {key: 'hashrate', label: 'Hash rate'},
    {key: 'temp', label: 'Temperature'},
    {key: 'power', label: 'Power'},
];

onTab('miner', (changed) => {
    buildChips('m-range', [{key: '24', label: '24h'}, {key: '168', label: '7d'}, {key: '720', label: '30d'}], minerPage.range, (key) => {
        minerPage.range = key;
        loadMinerHistory();
    });
    buildChips('m-chart-kind', CHART_KINDS, minerPage.kind, (key) => {
        minerPage.kind = key;
        renderMinerChart();
    });
    if (changed) {
        minerPage.history = null;
        minerPage.bests = [];
        minerPage.events = [];
        document.getElementById('m-chart').innerHTML = '';
    }
    renderMiner();
    loadMinerData();
}, () => {
    renderMiner();
    if (Date.now() - minerPage.loadedAt > 60000) loadMinerData();
    // Paused or mining again: show it now, not at the next reload
    else if (minerPage.history && liveMining(minerBySlug(currentMiner)) !== minerPage.drawnMining) renderMinerChart();
});

const liveMining = m => (!m || !m.connected || m.values.mining === undefined || m.values.mining === null) ? null : (m.values.mining ? 1 : 0);

async function loadMinerData() {
    const slug = currentMiner;
    minerPage.loadedAt = Date.now();
    try {
        const q = 'miner=' + encodeURIComponent(slug);
        const [bests, events] = await Promise.all([api(`bests?limit=10&${q}`), api(`events?limit=30&${q}`)]);
        if (slug !== currentMiner) return;
        minerPage.bests = bests;
        minerPage.events = events;
        renderMinerLists();
    } catch (e) {
        toast('Cannot load the history: ' + e.message);
    }
    loadMinerHistory();
}

async function loadMinerHistory() {
    const slug = currentMiner;
    try {
        const history = await api(`history?hours=${minerPage.range}&miner=${encodeURIComponent(slug)}`);
        if (slug !== currentMiner) return;
        minerPage.history = history;
        renderMinerChart();
    } catch (e) {
        emptyChart(document.getElementById('m-chart'), 'Cannot load the history');
    }
}

// The entity id Home Assistant gives the Mining switch: the device name, made into an id
const entitySlug = name => name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');

function renderMiner() {
    const m = minerBySlug(currentMiner);
    if (!m) return;
    const v = m.values, o = m.odds;
    const state = minerState(m);
    const pool = v.pool ? `${escapeHtml(v.pool)}${v.using_fallback ? ' <span class="badge warn">FALLBACK</span>' : ''}` : '';
    const control = m.pause_mode
        ? `Pause and resume with <span class="mono">switch.${escapeHtml(entitySlug(m.name))}_mining</span> in Home Assistant${m.pause_mode === 'shutdown' ? '; this firmware resumes with a restart' : ''}.`
        : 'This firmware reports no way to pause; restart it with its Restart button in Home Assistant.';
    document.getElementById('m-head').innerHTML = `
        <div class="mh-title"><i style="background:${minerColor(m.slug)}"></i><h1>${escapeHtml(m.name)}</h1>${stateBadge(m)}</div>
        <div class="mh-sub">${[escapeHtml(modelText(m)), `<a href="http://${escapeHtml(m.host)}" target="_blank" rel="noopener">${escapeHtml(m.host)}</a>`, v.version ? 'firmware ' + escapeHtml(v.version) : '', pool].filter(Boolean).join(' · ')}</div>
        <div class="mh-control">${m.connected ? control : `Not answering${m.last_seen ? ' since ' + escapeHtml(fmtAgo(m.last_seen)) : ''}: ${escapeHtml(m.error || '')}`}</div>`;

    const expected = v.expected_hashrate > 0 && v.hashrate ? v.hashrate / v.expected_hashrate * 100 : null;
    const averages = [['1m', v.hashrate_1m], ['10m', v.hashrate_10m], ['1h', v.hashrate_1h], ['1d', v.hashrate_1d]].filter(a => !missing(a[1]));
    document.getElementById('m-rate').innerHTML = `
        <div class="hero-label">HASH RATE</div>
        <div class="hero-value mono" style="color:${state.kind === 'ok' ? minerColor(m.slug) : 'var(--secondary-text-color)'}">${m.connected ? rateHtml(v.hashrate) : '–'}</div>
        <div class="hero-sub">${missing(expected) ? '&nbsp;' : `${num(expected, 0)}% of the ${escapeHtml(rate(v.expected_hashrate))} expected`}</div>
        <div class="averages">${averages.map(([k, x]) => `<span><small>${k}</small><b class="mono">${escapeHtml(rate(x))}</b></span>`).join('')}</div>`;

    document.getElementById('m-odds').innerHTML = [
        tile('Chance today', escapeHtml(oneIn(o.day_chance)), `this year ${escapeHtml(oneIn(o.year_chance))}`),
        tile('On average', escapeHtml(years(o.years)), 'until a block'),
        tile('Best ever', escapeHtml(diff(m.best_ever)), `this session ${escapeHtml(diff(v.best_session_diff))}`, 'prize'),
        tile('Luck', missing(o.luck) ? '–' : `${sig3(o.luck)}<small>×</small>`, 'this session\'s best against the usual', !missing(o.luck) && o.luck >= 1 ? 'pos' : ''),
        tile('A new best', escapeHtml(oneIn(o.beat_best_today)), missing(o.beat_best_seconds) ? 'chance today' : `every ${escapeHtml(fmtDuration(o.beat_best_seconds))} on average`),
    ].join('');

    const stat = (label, value, detail, cls) => missing(value) ? '' : tile(label, value, detail, cls);
    const hot = v.temp >= 70 ? 'bad' : v.temp >= 65 ? 'warn' : '';
    document.getElementById('m-stats').innerHTML = [
        stat('Power', missing(v.power) ? null : `${num(v.power, 1)}<small> W</small>`, [missing(v.efficiency) ? '' : `${num(v.efficiency, 1)} J/TH`, missing(v.voltage) ? '' : `${num(v.voltage / 1000, 2)} V in`].filter(Boolean).join(' · ')),
        stat('ASIC', missing(v.temp) ? null : `${num(v.temp, 1)}<small> °C</small>`, missing(v.vr_temp) ? '' : `VR ${num(v.vr_temp, 0)} °C`, hot),
        stat('Fan', missing(v.fan_speed) ? null : `${num(v.fan_speed, 0)}<small> %</small>`, [missing(v.fan_rpm) ? '' : `${num(v.fan_rpm)} rpm`, v.auto_fan ? 'auto' : ''].filter(Boolean).join(' · ')),
        stat('Frequency', missing(v.frequency) ? null : `${num(v.frequency)}<small> MHz</small>`, missing(v.core_voltage) ? '' : `${num(v.core_voltage)} mV core`),
        stat('Shares', missing(v.shares_accepted) ? null : escapeHtml(big(v.shares_accepted)), missing(v.shares_rejected) ? '' : `${num(v.shares_rejected)} rejected (${num(v.shares_rejected / Math.max(1, v.shares_accepted + v.shares_rejected) * 100, 2)}%)`),
        stat('Error rate', missing(v.error_rate) ? null : `${num(v.error_rate, 2)}<small> %</small>`, 'of the hashes', v.error_rate > 3 ? 'warn' : ''),
        stat('Pool difficulty', missing(v.pool_diff) ? null : escapeHtml(diff(v.pool_diff)), missing(v.pool_ping) && missing(v.pool_response) ? '' : `ping ${num(v.pool_ping ?? v.pool_response, 0)} ms${missing(v.ping_loss) ? '' : ` · ${num(v.ping_loss, 1)}% lost`}`),
        stat('Wi-Fi', missing(v.wifi_rssi) ? null : `${num(v.wifi_rssi)}<small> dBm</small>`, v.wifi_rssi < -75 ? 'weak' : v.wifi_rssi < -65 ? 'fair' : 'good', v.wifi_rssi < -75 ? 'warn' : ''),
        stat('Up', missing(v.uptime) ? null : escapeHtml(fmtDuration(v.uptime)), v.reset_reason ? `last reset: ${escapeHtml(v.reset_reason.toLowerCase())}` : ''),
        stat('Energy', `${num(m.energy_kwh, 2)}<small> kWh</small>`, m.first_seen ? `since ${fmtDay(m.first_seen)}` : ''),
    ].join('');

    renderChips(m);
    renderDetails(m);
}

function renderChips(m) {
    const panel = document.getElementById('m-chips-panel');
    panel.hidden = !m.chips.length;
    if (!m.chips.length) return;
    const top = Math.max(...m.chips.map(c => c.hashrate || 0));
    document.getElementById('m-chips-hint').textContent = `${m.chips.length} × ${m.values.asic_model || 'ASIC'}`;
    document.getElementById('m-chips').innerHTML = m.chips.map((c, i) => `
        <div class="chip-card">
            <div class="l">ASIC ${i + 1}</div>
            <div class="v mono">${missing(c.hashrate) ? '–' : escapeHtml(rate(c.hashrate))}</div>
            <span class="share-bar"><i style="width:${top > 0 && c.hashrate ? c.hashrate / top * 100 : 0}%;background:${minerColor(m.slug)}"></i></span>
            <div class="d">${[missing(c.temp) ? '' : `${num(c.temp, 1)} °C`, missing(c.errors) ? '' : `${num(c.errors)} errors`, missing(c.error_hashrate) ? '' : `${escapeHtml(rate(c.error_hashrate))} errors`].filter(Boolean).join(' · ') || '&nbsp;'}</div>
        </div>`).join('');
}

function renderDetails(m) {
    const v = m.values;
    const rows = [
        ['Model', modelText(m)],
        ['Board', v.board],
        ['Firmware', v.version],
        ['Host name', v.hostname],
        ['MAC address', v.mac],
        ['Pool', v.pool],
        ['Worker', v.pool_user],
        ['Pool connected', missing(v.pool_connected) ? null : v.pool_connected ? 'yes' : 'no'],
        ['Cores', missing(v.core_count) ? null : num(v.core_count)],
        ['Core voltage', missing(v.core_voltage_actual) ? null : `${num(v.core_voltage_actual)} mV measured`],
        ['Current', missing(v.current) ? null : `${num(v.current, 2)} A`],
        ['Power limit', missing(v.max_power) ? null : `${num(v.max_power)} W`],
        ['Free memory', missing(v.free_heap) ? null : `${num(v.free_heap / 1024, 0)} KiB`],
        ['Overclock', missing(v.overclock) ? null : v.overclock ? 'on' : 'off'],
        ['Blocks found', missing(v.blocks_found) ? null : num(v.blocks_found)],
        ['Pausing', m.pause_mode === 'pause' ? 'pauses and resumes in place' : m.pause_mode === 'shutdown' ? 'shuts the ASICs down; a restart resumes' : 'not supported'],
        ['Work done', m.total_hashes ? `${hashes(m.total_hashes)} hashes since ${fmtDay(m.first_seen)}` : null],
    ].filter(r => !missing(r[1]) && r[1] !== '');
    document.getElementById('m-details').innerHTML = rows.map(([l, x]) => infoRow(l, escapeHtml(x))).join('');
}

function renderMinerChart() {
    const el = document.getElementById('m-chart');
    const m = minerBySlug(currentMiner);
    const h = minerPage.history;
    const legend = document.getElementById('m-chart-legend');
    if (!m || !h || !h.rows.length) { legend.innerHTML = ''; return emptyChart(el, 'No readings in this period yet'); }
    // The stored readings are a minute apart and averaged per bucket: end on the live reading
    const rows = h.rows.slice();
    const live = liveMining(m);
    minerPage.drawnMining = live;
    const last = rows[rows.length - 1];
    if (live !== null && m.last_seen && m.last_seen > last.ts) {
        const v = m.values;
        rows.push({ts: Math.floor(m.last_seen), hashrate: live ? v.hashrate : 0, temp: v.temp, vr_temp: v.vr_temp, power: v.power, mining: live});
    }
    const xs = rows.map(r => r.ts);
    const kind = minerPage.kind;
    let series, formatY, formatTick, hlines = [];
    if (kind === 'hashrate') {
        series = [{name: 'Hash rate', color: minerColor(m.slug), values: rows.map(r => r.hashrate), area: true}];
        formatY = v => rate(v);
        formatTick = v => rateParts(v)[0];
        if (m.values.expected_hashrate) hlines = [{at: m.values.expected_hashrate, label: 'expected', color: color('secondary-text-color')}];
    } else if (kind === 'temp') {
        series = [
            {name: 'ASIC', color: color('series-2'), values: rows.map(r => r.temp)},
            {name: 'VR', color: color('series-4'), values: rows.map(r => r.vr_temp)},
        ].filter(s => s.values.some(v => !missing(v)));
        formatY = v => `${num(v, 1)} °C`;
    } else {
        series = [{name: 'Power', color: color('series-3'), values: rows.map(r => r.power), area: true}];
        formatY = v => `${num(v, 1)} W`;
    }
    // Paused stretches as bands
    const bands = [];
    let from = null;
    rows.forEach((r, i) => {
        if (r.mining !== null && r.mining < 0.5) { if (from === null) from = r.ts; }
        // The bucket before a live 'mining' reading mixes both, so the band stops at its start
        else if (from !== null) { bands.push({from, to: rows[i === rows.length - 1 && i > 0 ? i - 1 : i].ts, color: color('warning-color')}); from = null; }
    });
    if (from !== null) bands.push({from, to: rows[rows.length - 1].ts, color: color('warning-color')});
    lineChart(el, {
        x: xs, series, height: 210, zero: kind !== 'temp', bands, hlines,
        formatX: x => fmtDateTime(x), formatY, formatTick,
        xTicks: timeAxisTicks(xs[0], xs[xs.length - 1], 5),
    });
    legend.innerHTML = legendHtml([...series.map(s => ({name: s.name, color: s.color})), ...(bands.length ? [{name: 'paused', color: color('warning-color')}] : [])]);
}

function renderMinerLists() {
    const rows = minerPage.bests.map((b, i) => `<tr>${td(`<span class="rank">${i + 1}</span>`)}${td(`<b class="mono">${escapeHtml(diff(b.diff))}</b>`, 'n')}${td(escapeHtml(fmtDateTime(b.ts)), 'muted')}</tr>`);
    document.getElementById('m-bests').innerHTML = table([{label: '#'}, {label: 'Share', n: true}, {label: 'When'}], rows,
        {empty: 'No best shares yet', emptySub: 'Each new best of a session is kept here.', cls: 'compact'});
    document.getElementById('m-events').innerHTML = minerPage.events.length
        ? minerPage.events.map(e => eventHtml(e, false)).join('')
        : '<div class="empty-state">Nothing yet</div>';
}
