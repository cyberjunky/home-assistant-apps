// Bitcoin Node dashboard - the pages about Bitcoin Core: Overview, Blockchain, Mempool and Peers.

// A chart is only drawn again when what it shows changed, so a tooltip survives the polls in between
function drawOnce(id, key, draw) {
    const el = document.getElementById(id);
    if (!el || el._key === key) return;
    el._key = key;
    draw(el);
}
const historyKey = h => `${h.range}:${h.ts[h.ts.length - 1]}:${h.ts.length}`;
const rangeBox = id => `<span class="tabs" role="group" aria-label="Period" id="${id}"></span>`;
const chartBox = (title, hint, id, extra) =>
    `<div class="panel"><div class="panel-body"><div class="chart-title"><span>${title}</span>${hint}</div><div id="${id}" class="chart"></div>${extra || ''}</div></div>`;
const hintSpan = id => `<span class="hint" id="${id}"></span>`;
function statLine(id, s, fmt) {
    fill(id, missing(s.last) ? '' : `<span>now <b>${fmt(s.last)}</b></span><span>low <b>${fmt(s.min)}</b></span><span>average <b>${fmt(s.avg)}</b></span><span>high <b>${fmt(s.max)}</b></span>`);
}
// Sent and received each on a line of their own, with what went over the line in the chosen period.
// The total counts every stored point as one step, so a gap in the history adds nothing.
function trafficStats(id, h, sentKey, recvKey) {
    const steps = h.ts.slice(1).map((t, i) => t - h.ts[i]).filter(d => d > 0);
    const step = steps.length ? Math.min(...steps) : 0;
    const line = (name, values) => {
        const s = stats(values);
        if (missing(s.last)) return '';
        const total = values.reduce((sum, v) => sum + (missing(v) ? 0 : v), 0) * step * 1000;
        return `<div class="chart-stats"><span>${name}</span><span>now <b>${fmtRate(s.last)}</b></span><span>low <b>${fmtRate(s.min)}</b></span><span>average <b>${fmtRate(s.avg)}</b></span><span>high <b>${fmtRate(s.max)}</b></span><span>total <b>${fmtBytes(total)}</b></span></div>`;
    };
    fill(id, line('sent', h[sentKey]) + line('received', h[recvKey]));
}
const FEE_SERIES = [
    {key: 'fee_next', name: 'next block', color: color('series-2')},
    {key: 'fee_hour', name: 'within 1 hour', color: color('series-1')},
    {key: 'fee_day', name: 'within 1 day', color: color('series-3')},
];
const TRAFFIC_SERIES = [
    {key: 'sent_rate', name: 'sent', color: color('series-1'), area: true},
    {key: 'recv_rate', name: 'received', color: color('series-3')},
];

// =====================================================================
// Overview
// =====================================================================
function blockCard(b) {
    const full = Math.min(1, b.weight / 4e6);
    return `<div class="blk" title="${escapeHtml(b.hash)}">
        <div class="blk-h">${num(b.height)}</div>
        <div class="blk-fee">~${escapeHtml(feeRate(b.fee_median))}<small> sat/vB</small></div>
        <div class="blk-range">${escapeHtml(feeRate(b.fee_min))} – ${escapeHtml(feeRate(b.fee_max))} sat/vB</div>
        <div class="blk-meta">${num(b.txs)} tx · ${escapeHtml(fmtBytes(b.size, 2))}</div>
        <div class="blk-meta">${escapeHtml(btc(b.fee_total, 3))} fees</div>
        <div class="blk-fill" title="${escapeHtml(plainPct(full))} full"><i style="width:${(full * 100).toFixed(1)}%"></i></div>
        <div class="blk-age">${escapeHtml(fmtAgo(b.time))}</div>
    </div>`;
}

onTab('overview', async () => {
    skeleton('overview', `
        <div id="ov-tiles" class="tiles kpi-tiles"></div>
        ${panel('Latest blocks', '<span id="ov-blocks-hint"></span>', '<div id="ov-blocks" class="blocks-strip"></div>')}
        <div class="dash-grid wide-left" style="margin-top:14px">
            ${chartBox('Fee to get confirmed', rangeBox('ov-range'), 'ov-fees', '<div class="legend" id="ov-fees-legend"></div><div class="chart-stats" id="ov-fees-stats"></div>')}
            ${panel('Health', 'checked on every poll', '<div id="ov-health" class="health"></div>')}
        </div>
        <div class="dash-grid wide-left">
            ${chartBox('Mempool size', hintSpan('ov-mempool-hint'), 'ov-mempool', '<div class="chart-stats" id="ov-mempool-stats"></div>')}
            ${panel('Activity', 'blocks and Lightning', '<div id="ov-events" class="events"></div>')}
        </div>
        <div class="dash-grid">
            ${panel('Lightning liquidity', '<span id="ov-liq-hint"></span>', '<div id="ov-liq"></div>')}
            ${chartBox('Traffic', hintSpan('ov-traffic-hint'), 'ov-traffic', '<div class="legend" id="ov-traffic-legend"></div>')}
        </div>`);
    rangeChips('ov-range');
    const s = app.status, n = s.node, m = s.mempool, ln = s.lightning;
    const lnReady = ln.configured && Boolean(ln.pubkey);
    fill('ov-tiles', [
        ...(n.synced ? [
            tile('Block height', num(n.blocks), `last block ${escapeHtml(fmtAgo(n.last_block_time))}`),
            tile('Mempool', num(m.tx), `${num(m.vsize, 1)} MB · about ${num(m.blocks_to_clear)} blocks`),
            tile('Next block fee', feeHtml(m.fees.next), `1 hour ${escapeHtml(feeRate(m.fees.hour))} · 1 day ${escapeHtml(feeRate(m.fees.day))}`),
        ] : [
            // While the chain syncs the newest block is years old: its age, the mempool and the fees say nothing yet
            tile('Syncing', escapeHtml(plainPct(n.progress, 2)), n.last_block_time ? `at ${escapeHtml(new Date(n.last_block_time * 1000).toLocaleDateString([], {month: 'long', year: 'numeric'}))}` : 'reading the block headers', 'warn'),
            tile('Block height', num(n.blocks), `of ${num(n.headers)} known${n.sync_started ? ` · sync started ${escapeHtml(fmtAgo(n.sync_started))}` : ''}`),
            tile('Chain on disk', escapeHtml(fmtBytes(n.size_on_disk, 1)), n.pruned ? 'pruned' : 'grows to the full chain'),
        ]),
        tile('Peers', num(s.net.peers), `${num(s.net.in)} in · ${num(s.net.out)} out${Object.keys(s.net.peer_networks).join() === 'onion' ? ' · all onion' : ''}`),
        ...(n.synced ? [
            tile('Network hashrate', escapeHtml(fmtHashrate(n.hashrate)), `difficulty ${escapeHtml(signed(n.retarget.change * 100, 1))}% in ${escapeHtml(fmtDuration(n.retarget.at - s.now))}`),
        ] : []),
        ...(lnReady ? [
            tile('Lightning can send', satHtml(ln.channels.local), `can receive ${escapeHtml(sat(ln.channels.remote))}`),
            tile('Channels', `${num(ln.channels.active)}<small> of ${num(ln.channels.active + ln.channels.inactive)}</small>`, ln.channels.pending ? `${num(ln.channels.pending)} pending` : 'none pending', ln.channels.inactive ? 'warn' : ''),
            tile('Earned in 30 days', satHtml(ln.fees.month), `${num(ln.forwards_30d)} payments forwarded`, 'pos'),
        ] : [
            tile('Chain on disk', escapeHtml(fmtBytes(n.size_on_disk, 1)), n.pruned ? 'pruned' : 'the full chain'),
            tile('Uptime', escapeHtml(fmtDuration(n.uptime)), `Bitcoin Core ${escapeHtml(n.version)}`),
        ]),
    ].join(''));
    fill('ov-health', s.health.map(h => `<div class="health-row">${stateLabel(h.state, h.name)}<span>${escapeHtml(h.text)}</span></div>`).join(''));
    if (lnReady) {
        const send = ln.channels.local / Math.max(1, ln.channels.local + ln.channels.remote);
        fill('ov-liq-hint', `${num(ln.channels.active + ln.channels.inactive)} channels · ${escapeHtml(sat(ln.channels.capacity))}`);
        fill('ov-liq', `
            <div class="liq big"><i class="out" style="width:${(send * 100).toFixed(1)}%"></i><i class="in" style="width:${((1 - send) * 100).toFixed(1)}%"></i></div>
            <div class="share-row"><span>can send ${escapeHtml(sat(ln.channels.local))} · ${escapeHtml(plainPct(send, 0))}</span><span>can receive ${escapeHtml(sat(ln.channels.remote))} · ${escapeHtml(plainPct(1 - send, 0))}</span></div>
            <div style="margin-top:12px">
            ${infoRow('On-chain wallet', escapeHtml(sat(ln.onchain.confirmed)))}
            ${infoRow('In channels being opened', escapeHtml(sat(ln.channels.pending_local)))}
            ${infoRow('Earned today · 7 days · 30 days', `${escapeHtml(num(ln.fees.day))} · ${escapeHtml(num(ln.fees.week))} · ${escapeHtml(sat(ln.fees.month))}`)}
            ${infoRow('Volume forwarded in 30 days', escapeHtml(btcOfSat(ln.volume_30d)))}
            </div>`);
    } else {
        fill('ov-liq-hint', '');
        fill('ov-liq', `<div class="empty-state">${ln.configured ? 'Waiting for LND…' : 'No Lightning node set up. Add LND under <b>lightning</b> in the app\'s configuration to see your wallet and channels here.'}</div>`);
    }

    const [blocks, events, h] = await Promise.all([api('blocks'), api('events'), loadHistory()]);
    app.blocks = blocks.blocks;
    app.events = events.events;
    const next = `<div class="blk next" title="What the next block would take from the mempool now">
        <div class="blk-h">next</div>
        <div class="blk-fee">~${escapeHtml(feeRate(m.fees.next))}<small> sat/vB</small></div>
        <div class="blk-range">from the mempool</div>
        <div class="blk-meta">${num(m.tx)} tx waiting</div>
        <div class="blk-meta">${num(m.vsize, 1)} MB</div>
        <div class="blk-fill"><i style="width:${Math.min(100, m.vsize * 100).toFixed(0)}%"></i></div>
        <div class="blk-age">in about 10 min</div>
    </div>`;
    if (n.synced) {
        fill('ov-blocks', next + app.blocks.slice(0, 14).map(blockCard).join(''));
        const hour = app.blocks.filter(b => b.time > s.now - 3600).length;
        fill('ov-blocks-hint', `${num(hour)} in the last hour · 6 expected`);
    } else {
        fill('ov-blocks', '<div class="empty-state" style="flex:1">The latest blocks show here once the chain is in sync.</div>');
        fill('ov-blocks-hint', '');
    }
    fill('ov-events', app.events.slice(0, 9).map(eventHtml).join('') || '<div class="empty-state">Nothing happened yet</div>');

    fill('ov-fees-legend', legendHtml(FEE_SERIES.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    statLine('ov-fees-stats', stats(h.fee_next), v => `${feeRate(v)} sat/vB`);
    fill('ov-mempool-hint', rangeLabel());
    statLine('ov-mempool-stats', stats(h.mempool_vsize), v => `${num(v, 1)} MB`);
    fill('ov-traffic-hint', `${rangeLabel()} · through Tor`);
    fill('ov-traffic-legend', legendHtml(TRAFFIC_SERIES.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    const key = historyKey(h);
    drawOnce('ov-fees', key, () => historyChart('ov-fees', h, FEE_SERIES, v => `${feeRate(v)} sat/vB`, {height: 200}));
    drawOnce('ov-mempool', key, () => historyChart('ov-mempool', h, [{key: 'mempool_vsize', name: 'virtual size', color: color('series-4'), area: true}], v => `${num(v, 1)} MB`, {height: 250, hlines: [{at: 1, label: 'one block'}]}));
    drawOnce('ov-traffic', key, () => historyChart('ov-traffic', h, TRAFFIC_SERIES, fmtRate, RATE_AXIS));
});

// =====================================================================
// Blockchain
// =====================================================================
function blockBars(id, blocks, pick, format, seriesColor) {
    barChart(document.getElementById(id), {
        data: blocks.map(b => ({label: fmtTime(b.time), tipTitle: `Block ${num(b.height)} · ${fmtDateTime(b.time)}`, values: [pick(b)]})),
        series: [{name: '', color: seriesColor}],
        format,
        height: 170,
        labelWidth: 76,
    });
}

onTab('node', async () => {
    skeleton('node', `
        <div id="nd-tiles" class="tiles kpi-tiles"></div>
        <div class="stats-grid">
            ${chartBox('Time between blocks', hintSpan('nd-interval-hint'), 'nd-interval')}
            ${chartBox('Transactions per block', hintSpan('nd-txs-hint'), 'nd-txs')}
            ${chartBox('Fees per block', hintSpan('nd-fees-hint'), 'nd-fees')}
            ${chartBox('Median fee rate per block', hintSpan('nd-median-hint'), 'nd-median')}
        </div>
        <div class="info-grid three">
            ${panel('Difficulty adjustment', 'every 2,016 blocks', '<div id="nd-retarget"></div>')}
            ${panel('Halving', 'every 210,000 blocks', '<div id="nd-halving"></div>')}
            ${panel('Supply', 'coins mined so far', '<div id="nd-supply"></div>')}
        </div>
        ${panel('Blocks', 'the latest 15', '<div id="nd-blocks" class="table-wrap"></div>', 'gap-top')}
        ${panel('Node', '', '<div id="nd-details"></div>', 'gap-top')}`);
    const s = app.status, n = s.node;
    fill('nd-tiles', [
        tile('Block height', num(n.blocks), n.synced ? 'in sync with the network' : `${escapeHtml(plainPct(n.progress, 2))} synced · of ${num(n.headers)}`, n.synced ? '' : 'warn'),
        tile('Chain on disk', escapeHtml(fmtBytes(n.size_on_disk, 1)), n.growth ? `+${escapeHtml(fmtBytes(n.growth.bytes, 1))} in ${escapeHtml(fmtDuration(n.growth.seconds))}` : (n.pruned ? 'pruned' : 'the full chain')),
        tile('Network hashrate', n.synced ? escapeHtml(fmtHashrate(n.hashrate)) : '–', n.synced ? `difficulty ${escapeHtml(fmtBig(n.difficulty))}` : 'known once in sync'),
        tile('Transactions ever', escapeHtml(fmtBig(n.tx_total)), missing(n.tx_rate) ? '' : `${num(n.tx_rate, 1)} per second lately`),
        tile('Block reward', `${num(n.halving.subsidy, 3)}<small> BTC</small>`, `${escapeHtml(num(n.halving.next_subsidy, 4))} BTC after the halving`),
        tile('Uptime', escapeHtml(fmtDuration(n.uptime)), `Bitcoin Core ${escapeHtml(n.version)}`),
    ].join(''));

    const r = n.retarget;
    fill('nd-retarget', `
        ${progress(r.blocks_done / 2016, `${num(r.blocks_done)} of 2,016 blocks`, `${num(r.blocks_left)} to go`)}
        <div style="margin-top:10px">
        ${infoRow('Expected change', `${escapeHtml(signed(r.change * 100, 2))}%`)}
        ${infoRow('Expected', `${escapeHtml(fmtDateTime(r.at))} · in ${escapeHtml(fmtDuration(r.at - s.now))}`)}
        ${infoRow('Average block time this period', `${num(r.avg_interval / 60, 2)} min`)}
        </div>`);
    const hv = n.halving;
    fill('nd-halving', `
        ${progress(hv.progress, `${escapeHtml(plainPct(hv.progress))} of this period`, `block ${num(hv.height)}`)}
        <div style="margin-top:10px">
        ${infoRow('Blocks to go', num(hv.blocks_left))}
        ${infoRow('Expected around', `${escapeHtml(fmtDate(hv.at))} · in ${num((hv.at - s.now) / 86400)} days`)}
        ${infoRow('Reward', `${num(hv.subsidy, 3)} → ${num(hv.next_subsidy, 4)} BTC`)}
        </div>`);
    fill('nd-supply', `
        ${progress(n.supply.ratio, `${escapeHtml(plainPct(n.supply.ratio, 2))} mined`, '21,000,000 BTC')}
        <div style="margin-top:10px">
        ${infoRow('Mined', escapeHtml(btc(n.supply.issued, 2)))}
        ${infoRow('Still to mine', escapeHtml(btc(n.supply.max - n.supply.issued, 2)))}
        ${infoRow('New coins per day', `about ${escapeHtml(btc(hv.subsidy * 144, 0))}`)}
        </div>`);
    fill('nd-details', [
        infoRow('Chain', escapeHtml(n.chain)),
        infoRow('Best block', `<span title="${escapeHtml(n.best_hash)}">${escapeHtml(shorten(n.best_hash, 24, 12))}</span>`),
        infoRow('Headers', num(n.headers)),
        infoRow('Verification progress', escapeHtml(plainPct(n.progress, 4))),
        ...(n.sync_started ? [infoRow('Sync started', `${escapeHtml(fmtDateTime(n.sync_started))} · ${escapeHtml(fmtAgo(n.sync_started))}`)] : []),
        infoRow('Pruned', n.pruned ? 'yes' : 'no, the full chain is kept'),
        infoRow('Transaction index', n.txindex ? 'ready' : 'off or still building'),
        infoRow('Version', escapeHtml(n.subversion)),
    ].join(''));

    app.blocks = (await api('blocks')).blocks;
    if (!app.blocks.length) return;
    const recent = app.blocks.slice(0, 48).reverse();
    const avg = list => list.reduce((a, b) => a + b, 0) / Math.max(1, list.length);
    const gaps = recent.map(b => b.interval).filter(v => !missing(v));
    const mean = avg(gaps);
    const longest = Math.max(0, ...gaps);
    fill('nd-interval-hint', `last ${recent.length} blocks · average ${num(mean / 60, 1)} min · longest ${num(longest / 60, 0)} min · target 10`);
    fill('nd-txs-hint', `average ${num(avg(recent.map(b => b.txs)))}`);
    const share = avg(recent.map(b => b.fee_total / (b.fee_total + b.subsidy)));
    fill('nd-fees-hint', `${escapeHtml(plainPct(share))} of what miners earn`);
    fill('nd-median-hint', 'sat/vB');
    const key = String(app.blocks[0].height);
    drawOnce('nd-interval', key, () => blockBars('nd-interval', recent, b => (b.interval || 0) / 60, v => `${num(v, 1)} min`, color('series-1')));
    drawOnce('nd-txs', key, () => blockBars('nd-txs', recent, b => b.txs, v => `${num(v)} transactions`, color('series-3')));
    drawOnce('nd-fees', key, () => blockBars('nd-fees', recent, b => b.fee_total, v => btc(v, 4), color('series-4')));
    drawOnce('nd-median', key, () => lineChart(document.getElementById('nd-median'), {
        x: recent.map(b => b.time),
        series: [{name: 'median fee rate', color: color('series-2'), values: recent.map(b => b.fee_median), area: true}],
        height: 170, zero: true,
        formatX: t => { const b = recent.find(x => x.time === t); return b ? `Block ${num(b.height)} · ${fmtDateTime(t)}` : fmtDateTime(t); },
        formatY: v => `${feeRate(v)} sat/vB`,
        xTicks: timeAxisTicks(recent[0].time, recent[recent.length - 1].time, 5),
    }));
    fill('nd-blocks', table(
        [{label: 'Height'}, {label: 'Mined'}, {label: 'After', n: 1}, {label: 'Transactions', n: 1}, {label: 'Size', n: 1}, {label: 'Full'},
            {label: 'Median fee', n: 1}, {label: 'Fee range', n: 1}, {label: 'Fees', n: 1}, {label: 'Hash'}],
        app.blocks.slice(0, 15).map(b => `<tr>
            ${td(`<span class="name mono">${num(b.height)}</span>`)}
            ${td(`${escapeHtml(fmtTime(b.time))}<span class="sub">${escapeHtml(fmtAgo(b.time))}</span>`)}
            ${td(escapeHtml(fmtDuration(b.interval)), 'n' + (b.interval > 1800 ? ' warn-text' : ''))}
            ${td(num(b.txs), 'n')}
            ${td(escapeHtml(fmtBytes(b.size, 2)), 'n')}
            ${td(`<div class="cell-bar"><i style="left:0;width:${Math.min(100, b.weight / 4e4).toFixed(1)}%;background:${color('series-1')}"></i></div>`)}
            ${td(`${escapeHtml(feeRate(b.fee_median))} sat/vB`, 'n')}
            ${td(`${escapeHtml(feeRate(b.fee_min))} – ${escapeHtml(feeRate(b.fee_max))}`, 'n')}
            ${td(escapeHtml(btc(b.fee_total, 4)), 'n')}
            ${td(`<span class="mono muted" title="${escapeHtml(b.hash)}">${escapeHtml(shorten(b.hash.replace(/^0+/, '0…'), 12, 8))}</span>`)}
        </tr>`), {cls: 'hover'}));
});

// =====================================================================
// Mempool
// =====================================================================
const FEE_TARGETS = [
    {key: 'next', label: 'Next block', wait: 'about 10 min'},
    {key: 'half_hour', label: '3 blocks', wait: 'about 30 min'},
    {key: 'hour', label: '6 blocks', wait: 'about 1 hour'},
    {key: 'six_hours', label: '36 blocks', wait: 'about 6 hours'},
    {key: 'day', label: '144 blocks', wait: 'about 1 day'},
];

onTab('mempool', async () => {
    skeleton('mempool', `
        <div id="mp-tiles" class="tiles kpi-tiles"></div>
        <div class="dash-grid wide-left">
            ${chartBox('Waiting per fee rate', hintSpan('mp-hist-hint'), 'mp-hist')}
            ${panel('What to pay', 'for a simple payment of 140 vB', '<div id="mp-fees" class="table-wrap"></div><div class="note" style="margin-top:10px">Estimates from Bitcoin Core (estimatesmartfee). They look at recent blocks, not at what happens next.</div>')}
        </div>
        <div class="filters"><span class="filter-label" style="margin-left:0">Period</span>${rangeBox('mp-range')}<span class="range-text" id="mp-range-text"></span></div>
        <div class="stats-grid">
            ${chartBox('Mempool size', hintSpan('mp-size-hint'), 'mp-size', '<div class="chart-stats" id="mp-size-stats"></div>')}
            ${chartBox('Transactions waiting', hintSpan('mp-tx-hint'), 'mp-tx', '<div class="chart-stats" id="mp-tx-stats"></div>')}
            <div class="wide">${chartBox('Fee to get confirmed', '<span class="legend" id="mp-fee-legend" style="margin:0"></span>', 'mp-fee', '<div class="chart-stats" id="mp-fee-stats"></div>')}</div>
        </div>`);
    rangeChips('mp-range');
    const m = app.status.mempool;
    fill('mp-tiles', [
        tile('Transactions', num(m.tx), 'waiting for a block'),
        tile('Virtual size', `${num(m.vsize, 1)}<small> MB</small>`, `about ${num(m.blocks_to_clear)} blocks to clear`),
        tile('Next block fee', feeHtml(m.fees.next), `${escapeHtml(sat(Math.round(m.fees.next * 140)))} for a payment`),
        tile('Fees waiting', `${num(m.total_fee, 3)}<small> BTC</small>`, 'offered by everything in the mempool'),
        tile('Memory', escapeHtml(fmtBytes(m.usage, 0)), `${escapeHtml(plainPct(m.usage / m.max, 0))} of ${escapeHtml(fmtBytes(m.max, 0))}`, m.usage / m.max > 0.9 ? 'warn' : ''),
        tile('Lowest fee accepted', feeHtml(m.min_fee), m.unbroadcast ? `${num(m.unbroadcast)} of your own not yet sent on` : 'nothing of your own is stuck'),
    ].join(''));
    fill('mp-fees', table(
        [{label: 'Confirmed within'}, {label: 'Wait'}, {label: 'Fee rate', n: 1}, {label: 'Costs', n: 1}],
        FEE_TARGETS.map(t => `<tr>${td(`<span class="name">${t.label}</span>`)}${td(t.wait, 'muted')}${td(`${escapeHtml(feeRate(m.fees[t.key]))} sat/vB`, 'n')}${td(escapeHtml(sat(Math.round(m.fees[t.key] * 140))), 'n')}</tr>`)));
    const below = m.histogram.filter(b => b.to <= m.fees.next).reduce((a, b) => a + b.vsize, 0);
    fill('mp-hist-hint', `sat/vB · ${num(below / Math.max(0.01, m.vsize) * 100, 0)}% pays less than the next block asks`);
    // The histogram follows the mempool, redrawn when it moved by a tenth of a megabyte
    drawOnce('mp-hist', `${Math.round(m.vsize * 10)}:${m.fees.next}`, el => barChart(el, {
        data: m.histogram.map(b => ({label: `${b.from}–${b.to}`, tipTitle: `${b.from} to ${b.to} sat/vB`, values: [b.vsize]})),
        series: [{name: '', color: color('series-4')}],
        format: v => `${num(v, 2)} MB`,
        height: 210,
        labelWidth: 50,
        empty: 'Read every two minutes; not for a mempool of more than 250,000 transactions',
    }));

    const h = await loadHistory();
    fill('mp-range-text', rangeLabel());
    fill('mp-size-hint', 'MB of virtual size');
    fill('mp-tx-hint', 'count');
    fill('mp-fee-legend', legendHtml(FEE_SERIES.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    statLine('mp-size-stats', stats(h.mempool_vsize), v => `${num(v, 1)} MB`);
    statLine('mp-tx-stats', stats(h.mempool_tx), v => num(v));
    statLine('mp-fee-stats', stats(h.fee_next), v => `${feeRate(v)} sat/vB`);
    const key = historyKey(h);
    drawOnce('mp-size', key, () => historyChart('mp-size', h, [{key: 'mempool_vsize', name: 'virtual size', color: color('series-4'), area: true}], v => `${num(v, 1)} MB`, {hlines: [{at: 1, label: 'one block'}]}));
    drawOnce('mp-tx', key, () => historyChart('mp-tx', h, [{key: 'mempool_tx', name: 'transactions', color: color('series-1'), area: true}], v => num(v)));
    drawOnce('mp-fee', key, () => historyChart('mp-fee', h, FEE_SERIES, v => `${feeRate(v)} sat/vB`, {height: 220}));
});

// =====================================================================
// Peers
// =====================================================================
const PEER_TYPES = {'outbound-full-relay': 'outbound', 'block-relay-only': 'blocks only', inbound: 'inbound'};

onTab('peers', async () => {
    skeleton('peers', `
        <div id="pr-tiles" class="tiles kpi-tiles"></div>
        <div class="filters"><span class="filter-label" style="margin-left:0">Period</span>${rangeBox('pr-range')}<span class="range-text" id="pr-range-text"></span></div>
        <div class="stats-grid">
            ${chartBox('Traffic', '<span class="legend" id="pr-traffic-legend" style="margin:0"></span>', 'pr-traffic', '<div class="chart-stats" id="pr-traffic-stats"></div>')}
            ${chartBox('Peers connected', '<span class="legend" id="pr-count-legend" style="margin:0"></span>', 'pr-count', '<div class="chart-stats" id="pr-count-stats"></div>')}
        </div>
        <div class="dash-grid">
            ${panel('Software of the peers', hintSpan('pr-clients-hint'), '<div id="pr-clients"></div>')}
            ${panel('Networks', 'how this node can be reached', '<div id="pr-networks" class="table-wrap"></div><div id="pr-local" style="margin-top:10px"></div>')}
        </div>
        ${panel('Peers', hintSpan('pr-table-hint'), '<div id="pr-table" class="table-wrap"></div>')}`);
    rangeChips('pr-range');
    const net = app.status.net;
    fill('pr-tiles', [
        tile('Peers', num(net.peers), 'connected now'),
        tile('Inbound', num(net.in), 'others that connected to this node'),
        tile('Outbound', num(net.out), `${num(net.kinds['outbound-full-relay'] || 0)} full relay · ${num(net.kinds['block-relay-only'] || 0)} blocks only`),
        tile('Median ping', missing(net.median_ping) ? '–' : `${num(net.median_ping * 1000)}<small> ms</small>`, 'half of the peers answer faster'),
        tile('Received', escapeHtml(fmtBytes(net.recv_total, 1)), `${escapeHtml(fmtRate(net.recv_rate))} now`),
        tile('Sent', escapeHtml(fmtBytes(net.sent_total, 1)), `${escapeHtml(fmtRate(net.sent_rate))} now`),
    ].join(''));
    fill('pr-networks', table(
        [{label: 'Network'}, {label: 'Reachable'}, {label: 'Proxy'}, {label: 'Peers'}],
        net.networks.map(n => `<tr>${td(`<span class="name mono">${escapeHtml(n.name)}</span>`)}${td(stateLabel(n.reachable ? 'ok' : '', n.reachable ? 'yes' : 'no'))}${td(escapeHtml(n.proxy || '–'), 'mono muted')}${td(n.peers ? `${num(n.peers)} peers` : n.limited ? 'not used' : '', 'muted')}</tr>`)));
    fill('pr-local', net.local_addresses.map(a => infoRow('This node\'s address', `${escapeHtml(a.address)}:${a.port}`)).join('') || infoRow('This node\'s address', 'none announced'));

    const [peers, h] = await Promise.all([api('peers'), loadHistory()]);
    app.peers = peers.peers;
    const clients = new Map();
    app.peers.forEach(p => { const name = p.subver.replace(/^\/|\/$/g, '').replace('/', ' · '); clients.set(name, (clients.get(name) || 0) + 1); });
    const sorted = [...clients.entries()].sort((a, b) => b[1] - a[1]);
    const shades = ['seq-3', 'seq-4', 'seq-2', 'seq-5', 'seq-1', 'series-4', 'series-5', 'series-3'];
    drawOnce('pr-clients', sorted.map(e => e.join(':')).join('|'), el => donutChart(el,
        sorted.map(([name, count], i) => ({name, value: count, color: color(shades[i % shades.length])})),
        {label: 'PEERS', value: String(app.peers.length)}, v => `${num(v)} peers`));
    const current = app.peers.filter(p => p.subver.includes(`Satoshi:${app.status.node.version.split('.')[0]}.`)).length;
    fill('pr-clients-hint', `${num(current / Math.max(1, app.peers.length) * 100, 0)}% runs the same major version`);
    fill('pr-table-hint', `${num(app.peers.filter(p => p.v2).length)} of ${num(app.peers.length)} encrypted (v2 transport)`);
    const order = {'outbound-full-relay': 0, 'block-relay-only': 1, inbound: 2};
    fill('pr-table', table(
        [{label: 'Peer'}, {label: 'Address'}, {label: 'Kind'}, {label: 'Software'}, {label: 'Ping', n: 1}, {label: 'Received', n: 1}, {label: 'Sent', n: 1}, {label: 'Connected', n: 1}],
        [...app.peers].sort((a, b) => order[a.type] - order[b.type] || a.since - b.since).map(p => `<tr>
            ${td(`<span class="mono">${p.id}</span>`)}
            ${td(`<span class="mono" title="${escapeHtml(p.addr)}">${escapeHtml(p.inbound && p.network === 'onion' ? 'through the onion address' : shorten(p.addr, 14, 12))}</span>`)}
            ${td(`${badge(p.inbound ? 'mode' : '', PEER_TYPES[p.type])}${p.v2 ? ' ' + badge('ok', 'v2') : ''}`)}
            ${td(escapeHtml(p.subver.replace(/^\/|\/$/g, '')), 'muted')}
            ${td(missing(p.ping) ? '–' : `${num(p.ping * 1000)} ms`, 'n')}
            ${td(escapeHtml(fmtBytes(p.recv, 1)), 'n')}
            ${td(escapeHtml(fmtBytes(p.sent, 1)), 'n')}
            ${td(escapeHtml(fmtDuration(app.status.now - p.since)), 'n')}
        </tr>`), {cls: 'hover'}));

    const countSeries = [
        {key: 'peers_in', name: 'inbound', color: color('series-1'), area: true},
        {key: 'peers_out', name: 'outbound', color: color('series-2')},
    ];
    fill('pr-range-text', rangeLabel());
    fill('pr-traffic-legend', legendHtml(TRAFFIC_SERIES.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    fill('pr-count-legend', legendHtml(countSeries.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    trafficStats('pr-traffic-stats', h, 'sent_rate', 'recv_rate');
    statLine('pr-count-stats', stats(h.peers_in.map((v, i) => v + h.peers_out[i])), v => num(v));
    const key = historyKey(h);
    drawOnce('pr-traffic', key, () => historyChart('pr-traffic', h, TRAFFIC_SERIES, fmtRate, RATE_AXIS));
    drawOnce('pr-count', key, () => historyChart('pr-count', h, countSeries, v => `${num(v)} peers`));
});
