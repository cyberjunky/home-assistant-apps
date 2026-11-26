// Bitcoin Node dashboard - the Pool page: ckpool solo mining on this node, its workers and the odds.

// A very small chance as "1 in 2,400"; from 1 in 20 on as a percentage
function chanceText(p) {
    if (missing(p) || !p) return '–';
    if (p >= 0.05) return plainPct(p, p >= 0.995 ? 0 : 1);
    const n = 1 / p;
    return `1 in ${num(n, 0)}`;
}
// A time that can run into years or centuries
function fmtLong(seconds) {
    if (missing(seconds)) return '–';
    const years = seconds / (365.25 * 86400);
    if (years >= 1000) return `${num(years / 1000, 0)} thousand years`;
    if (years >= 2) return `${num(years, 0)} years`;
    if (years >= 1) return `${num(years, 1)} years`;
    return fmtDuration(seconds);
}
const workerName = w => w.label && w.label !== w.name ? w.label : shorten(w.name, 8, 6);
// How close a share came to a block, on a log scale: a share of difficulty 1 is 0, a block is 1
const closeness = (share, difficulty) => !share || !difficulty || share <= 1 ? 0 : Math.min(1, Math.log10(share) / Math.log10(difficulty));

onTab('pool', async () => {
    skeleton('pool', `
        <div id="pool-tiles" class="tiles kpi-tiles"></div>
        <div class="dash-grid wide-left">
            ${chartBox('Pool hashrate', rangeBox('pool-range'), 'pool-hashrate', '<div class="legend" id="pool-hashrate-legend"></div><div class="chart-stats" id="pool-hashrate-stats"></div>')}
            ${panel('How close to a block', 'best share against the difficulty, on a log scale', '<div id="pool-close"></div>')}
        </div>
        ${panel('Workers', hintSpan('pool-workers-hint'), '<div id="pool-workers" class="table-wrap"></div>')}
        <div class="dash-grid wide-left" style="margin-top:14px">
            ${panel('Payout addresses', 'a block found pays the address the miner logs in with', '<div id="pool-users" class="table-wrap"></div>')}
            ${panel('Activity', 'workers and blocks', '<div id="pool-events" class="events"></div>')}
        </div>
        <div class="note" style="margin-top:14px" id="pool-note"></div>`);
    rangeChips('pool-range');
    const s = app.status, p = s.pool, n = s.node;
    const odds = p.odds || {};
    const difficulty = n.difficulty;
    const hr = p.hashrate || {};
    const workersAll = (p.workers || 0) + (p.idle || 0);
    const tiles = [
        tile('Pool hashrate', escapeHtml(fmtHashrate(hr['5m'])), `1 hour ${escapeHtml(fmtHashrate(hr['1hr']))} · 1 day ${escapeHtml(fmtHashrate(hr['1d']))}`),
        tile('Workers mining', `${num(p.active_workers)}`, p.stale ? 'ckpool stopped writing its status' : `${num(p.workers)} connected${p.idle ? ` · ${num(p.idle)} idle` : ''}`, p.stale ? 'warn' : ''),
        tile('Chance per day', escapeHtml(chanceText(odds.day)), odds.share ? `against ${escapeHtml(fmtHashrate(n.hashrate))} for the whole network` : 'needs the pool\'s and the network\'s hashrate'),
        tile('Chance per year', escapeHtml(chanceText(odds.year)), odds.expected_seconds ? `on average a block every ${escapeHtml(fmtLong(odds.expected_seconds))}` : '&nbsp;'),
        tile('Best share ever', escapeHtml(fmtBig(p.best_ever)), difficulty ? `a block needs ${escapeHtml(fmtBig(difficulty))}` : '&nbsp;', p.best_ever && difficulty && p.best_ever >= difficulty ? 'pos' : ''),
        tile('A block pays', escapeHtml(btc(n.halving && n.halving.subsidy, 3)), 'plus the fees in it, all to your address'),
    ];
    fill('pool-tiles', tiles.join(''));
    fill('pool-note', `ckpool runs in solo mode on the node and writes its status every minute; this page reads that${p.runtime ? `. ckpool runs for ${escapeHtml(fmtDuration(p.runtime))}, last status ${escapeHtml(fmtAgo(p.last_update))}` : ''}.
        The chance is the pool's share of the network's hashrate over the last hour, against 144 blocks a day: every share is a fresh draw, nothing builds up over time.
        Shares: ${escapeHtml(fmtBig(p.accepted))} accepted, ${escapeHtml(fmtBig(p.rejected))} rejected (as difficulty).`);

    const [details, events, h] = await Promise.all([api('pool'), api('events?limit=200'), loadHistory()]);
    const users = details.users || [];
    const workers = users.flatMap(u => u.worker.map(w => Object.assign({address: u.address}, w)));
    fill('pool-workers-hint', `${num(workers.filter(w => w.active).length)} of ${num(workers.length)} mining · offline after 10 minutes without a share`);
    fill('pool-workers', table(
        [{label: 'Worker'}, {label: 'State'}, {label: '5 min', n: 1}, {label: '1 hour', n: 1}, {label: '1 day', n: 1}, {label: 'Last share', n: 1}, {label: 'Shares', n: 1}, {label: 'Best share', n: 1}, {label: 'Best ever', n: 1}],
        [...workers].sort((a, b) => (b.hashrate['1hr'] || 0) - (a.hashrate['1hr'] || 0)).map(w => `<tr>
            ${td(`<span class="name">${escapeHtml(workerName(w))}</span>`)}
            ${td(stateLabel(w.active ? 'ok' : 'warn', w.active ? 'mining' : 'offline'))}
            ${td(escapeHtml(fmtHashrate(w.hashrate['5m'])), 'n')}
            ${td(escapeHtml(fmtHashrate(w.hashrate['1hr'])), 'n')}
            ${td(escapeHtml(fmtHashrate(w.hashrate['1d'])), 'n')}
            ${td(escapeHtml(fmtAgo(w.last_share)), 'n')}
            ${td(escapeHtml(fmtBig(w.shares)), 'n')}
            ${td(escapeHtml(fmtBig(w.best_share)), 'n')}
            ${td(escapeHtml(fmtBig(w.best_ever)), 'n')}
        </tr>`), {cls: 'hover', empty: p.connected ? 'No miner has sent a share yet. Point the miners at the node, port 3333, with your address as user.' : 'The pool\'s status can\'t be read'}));
    fill('pool-users', table(
        [{label: 'Address'}, {label: 'Workers', n: 1}, {label: '1 hour', n: 1}, {label: 'Best ever', n: 1}],
        users.map(u => `<tr>
            ${td(`<span class="mono" title="${escapeHtml(u.address)}">${escapeHtml(shorten(u.address, 10, 8))}</span>`)}
            ${td(num(u.workers), 'n')}
            ${td(escapeHtml(fmtHashrate(u.hashrate['1hr'])), 'n')}
            ${td(escapeHtml(fmtBig(u.best_ever)), 'n')}
        </tr>`), {empty: 'No payout address yet'}));
    fill('pool-close', workers.length && difficulty ? workers.map(w => `
        <div class="close-row"><div class="close-name">${escapeHtml(workerName(w))}</div>
        ${progress(closeness(w.best_ever, difficulty), `best ${escapeHtml(fmtBig(w.best_ever))}`, `block ${escapeHtml(fmtBig(difficulty))}`, w.best_ever >= difficulty ? 'pos' : '')}</div>`).join('')
        : '<div class="empty-state">Fills in with the first shares</div>');
    const poolEvents = events.events.filter(e => e.source === 'pool');
    fill('pool-events', poolEvents.slice(0, 9).map(eventHtml).join('') || '<div class="empty-state">Nothing happened yet</div>');

    const series = [
        {key: 'pool_hashrate', name: '5 minutes', color: color('series-1'), area: true},
        {key: 'pool_hashrate_hour', name: '1 hour', color: color('series-3')},
    ];
    fill('pool-hashrate-legend', legendHtml(series.map(d => ({name: d.name, color: d.color, kind: 'line'}))));
    statLine('pool-hashrate-stats', stats(h.pool_hashrate || []), fmtHashrate);
    drawOnce('pool-hashrate', historyKey(h), () => historyChart('pool-hashrate', h, series, fmtHashrate, {height: 200, padLeft: 64, formatTick: v => fmtHashrate(v)}));
});
