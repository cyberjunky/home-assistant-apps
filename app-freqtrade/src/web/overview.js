// Freqtrade dashboard - Overview: every bot together, and each bot in one row.

const OV_RANGES = [
    {key: '24', label: '24h'}, {key: '168', label: '7d'}, {key: '720', label: '30d'},
    {key: '2160', label: '90d'}, {key: '8760', label: '1y'}, {key: '87840', label: 'All'},
];
let ovHours = (() => { try { return localStorage.getItem(LS + 'ov_hours') || '168'; } catch (e) { return '168'; } })();
let ovMode = (() => { try { return localStorage.getItem(LS + 'ov_mode') || 'total'; } catch (e) { return 'total'; } })();
// Which bots the overview is about: a trading mode, then (with a fleet) the filters and the sorting.
// The configured mode is the one every page load starts with; a click on a chip lasts until the reload.
let ovScope = null;
const OV_SCOPES = [{key: 'live', label: 'Live'}, {key: 'dry', label: 'Dry-run'}, {key: 'all', label: 'All'}];
function initOverviewScope() {
    if (ovScope !== null || !app.status) return;
    const mode = app.status.config.overview_mode;
    ovScope = OV_SCOPES.some(o => o.key === mode) ? mode : 'all';
    buildChips('ov-scope', OV_SCOPES, ovScope, key => {
        ovScope = key;
        renderOverview();
        renderOvHistory();
    });
}
function modeBots() {
    return bots().filter(b => ovScope === null || ovScope === 'all' || b.config.dry_run === (ovScope === 'dry'));
}
function overviewBots() {
    return fleetSelect(modeBots(), fleetSettings());
}
function overviewCurrency() {
    const counts = new Map();
    overviewBots().forEach(b => {
        const c = b.config.stake_currency;
        if (c) counts.set(c, (counts.get(c) || 0) + 1);
    });
    return [...counts].sort((a, b) => b[1] - a[1])[0]?.[0] || '';
}
// More bots than this with the same trouble share one notice
const OV_NOTICES = 3;
let ovHistory = null;
let ovHistoryAt = 0;

function renderOverview() {
    initOverviewScope();
    fleetToolbar(overviewBots(), modeBots());
    renderOvNotices();
    renderOvTiles();
    renderOvBots();
    renderOvAllocation();
    renderOvDaily();
    renderOvEvents();
    renderOvOpen();
}

function renderOvNotices() {
    const notes = [];
    if (!bots().length && app.status.discovery && app.status.discovery.enabled) {
        notes.push(['info', `No bots found yet. The app looks on <span class="mono">${escapeHtml(app.status.discovery.host)}</span> every ${app.status.discovery.interval_seconds} seconds; <b>Info</b> shows the last time and what went wrong.`]);
    } else if (!bots().length) {
        notes.push(['info', 'No bots yet. Add each bot under <b>bots</b> in the app\'s configuration: a name, the address of its API server (for example <span class="mono">http://192.168.1.10:8080</span>), and the username and password of its <span class="mono">api_server</span>. Then restart the app.']);
    }
    // With a few bots each one says what is wrong; a fleet that is down gets one line, not a wall of them
    const selected = overviewBots();
    const offline = selected.filter(b => !b.connected);
    const noEvents = selected.filter(b => b.connected && !b.ws_connected && b.ws_error);
    if (offline.length > OV_NOTICES) {
        notes.push(['bad', `<b>${offline.length} of ${selected.length} bots</b> can't be read. The totals leave them out; the table below says what is wrong with each.`]);
    } else {
        offline.forEach(b => {
            const lead = healthModel(b).api === 'reachable' ? 'answers, but not everything could be read' : 'can\'t be reached';
            notes.push(['bad', `<b>${escapeHtml(b.name)}</b> ${lead}${b.error ? ': ' + escapeHtml(b.error) : '…'}`]);
        });
    }
    if (noEvents.length > OV_NOTICES) {
        notes.push(['', `<b>${noEvents.length} bots</b> send no live events. Their state is still read every ${app.status.config.poll_seconds} seconds.`]);
    } else {
        noEvents.forEach(b => notes.push(['', `<b>${escapeHtml(b.name)}</b>: no live events, its WebSocket ${escapeHtml(b.ws_error)}. Its state is still read every ${app.status.config.poll_seconds} seconds.`]));
    }
    // The mode chips only when there is something to choose
    const modes = new Set(bots().map(b => b.config.dry_run).filter(v => typeof v === 'boolean'));
    document.getElementById('ov-scope-row').hidden = modes.size < 2 && ovScope === 'all';
    const unknown = bots().filter(b => typeof b.config.dry_run !== 'boolean' && b.updated).length;
    if (ovScope !== 'all' && unknown) notes.push(['info', `${unknown} ${unknown === 1 ? 'bot doesn\'t' : 'bots don\'t'} say whether they trade live or dry-run. <b>All</b> shows them too.`]);
    document.getElementById('ov-mixed-mode').hidden = !(selected.some(b => b.config.dry_run === true) && selected.some(b => b.config.dry_run === false));
    document.getElementById('ov-notices').innerHTML = notes.map(([kind, html]) => `<div class="setup-hint ${kind}"><span>${html}</span></div>`).join('');
}

// The futures bots' open trades together: the position they make with leverage, and the highest leverage in use
function ovLeverageTile(up) {
    const futures = up.filter(b => b.config.trading_mode === 'futures');
    const open = futures.flatMap(b => b.open_trades.map(t => ({...t, cur: b.config.stake_currency})));
    if (!open.length) return tile('Leverage', '–', `${futures.length} futures ${futures.length === 1 ? 'bot' : 'bots'}, no open trades`);
    const exposure = byCurrency(futures, b => b.open_trades.reduce((a, t) => a + position(t), 0));
    const stake = open.reduce((a, t) => a + (Number(t.stake_amount) || 0), 0), weighted = open.reduce((a, t) => a + position(t), 0);
    const most = Math.max(...open.map(lev));
    const nearest = Math.min(...open.map(liquidationDistance).filter(v => v != null));
    return tile('Leverage', `${num(stake ? weighted / stake : 1, 1)}×<small> avg · max ${num(most, most % 1 ? 1 : 0)}×</small>`,
        `position ${escapeHtml(currencyText(exposure, (v, c) => money(v, c, 0)))}${Number.isFinite(nearest) ? ` · liquidation ${plainPct(nearest)}` : ''}`,
        most >= 10 || nearest < 0.1 ? 'warn' : '');
}

function renderOvTiles() {
    const all = overviewBots();
    const up = all.filter(b => b.connected);
    const running = up.filter(b => b.config.state === 'running').length;
    const balance = byCurrency(up, b => b.balance.total_bot);
    const start = byCurrency(up, b => b.balance.starting_capital);
    const profit = byCurrency(up, b => b.profit.profit_all_coin);
    const today = byCurrency(up, b => b.today ? b.today.abs_profit : null);
    const openProfit = byCurrency(up, b => b.open_profit);
    const inUse = byCurrency(up, b => b.count.total_stake);
    const main = overviewCurrency();
    const ratioOf = (amounts) => main && start.get(main) ? amounts.get(main) / start.get(main) : null;
    const wins = up.reduce((a, b) => a + (b.profit.winning_trades || 0), 0);
    const losses = up.reduce((a, b) => a + (b.profit.losing_trades || 0), 0);
    const closed = up.reduce((a, b) => a + (b.profit.closed_trade_count || 0), 0);
    const tradesToday = up.reduce((a, b) => a + (b.today ? b.today.trade_count || 0 : 0), 0);
    const openNow = up.reduce((a, b) => a + (b.count.current || 0), 0);
    const openMax = up.reduce((a, b) => a + (b.count.max > 0 ? b.count.max : 0), 0);
    const first = Math.min(...up.map(b => ms(b.profit.first_trade_timestamp) || Infinity));
    const fmtMoney = (v, c) => money(v, c);
    const tileMoney = (v, c) => amountHtml(v, c);
    const tileSigned = (v, c) => amountHtml(v, c, undefined, true);
    const profitMain = profit.get(main);
    const todayMain = today.get(main);
    document.getElementById('ov-tiles').innerHTML = [
        tile('Bots', `${running}<small> / ${all.length} running</small>`, all.length - up.length ? `<span class="neg">${all.length - up.length} can't be read</span>` : `${up.filter(b => b.config.dry_run === false).length} live · ${up.filter(b => b.config.dry_run).length} dry-run`),
        tile('Balance', currencyText(balance, tileMoney), `start ${escapeHtml(currencyText(start, fmtMoney))}`),
        tile('Profit', pnl(currencyText(profit, tileSigned), profitMain), `${pnl(pct(ratioOf(profit)), profitMain)} of the starting capital`),
        tile('Today', pnl(currencyText(today, tileSigned), todayMain), `${tradesToday} ${tradesToday === 1 ? 'trade' : 'trades'} closed`),
        tile('Open trades', `${openNow}<small> / ${openMax || '∞'}</small>`, `in use ${escapeHtml(currencyText(inUse, fmtMoney))}`),
        tile('Open profit', pnl(currencyText(openProfit, tileSigned), openProfit.get(main)), 'not yet realised'),
        tile('Win rate', plainPct(wins + losses ? wins / (wins + losses) : null), `${wins} won · ${losses} lost`),
        tile('Closed trades', num(closed), Number.isFinite(first) ? `since ${escapeHtml(fmtDay(first))}` : ''),
        ...(up.some(b => b.config.trading_mode === 'futures') ? [ovLeverageTile(up)] : []),
    ].join('');
}

function renderOvBots() {
    const all = overviewBots();
    const rows = all.map(b => {
        const c = b.config, p = b.profit, cur = c.stake_currency || '', dec = c.stake_currency_decimals;
        if (!b.updated) {
            return `<tr onclick="switchTab('bot-${escapeHtml(b.slug)}')">
                ${td(`<span class="name" style="border-left:3px solid ${botColor(b.slug)};padding-left:7px">${escapeHtml(b.name)}</span><span class="sub" style="padding-left:10px">${escapeHtml(b.host)}</span>`)}
                ${td(healthState(b))}
                <td colspan="7" class="muted wrap">${escapeHtml(b.error || 'connecting…')}</td>
                ${td(healthConnection(b))}
            </tr>`;
        }
        const today = b.today ? b.today.abs_profit : null;
        const wl = (p.winning_trades || 0) + (p.losing_trades || 0);
        return `<tr onclick="switchTab('bot-${escapeHtml(b.slug)}')">
            ${td(`<span class="name" style="border-left:3px solid ${botColor(b.slug)};padding-left:7px">${escapeHtml(b.name)}</span><span class="sub" style="padding-left:10px">${escapeHtml(c.strategy || b.host)}${c.timeframe ? ' · ' + escapeHtml(c.timeframe) : ''}</span>`)}
            ${td(`${healthState(b)} ${modeBadge(c)}${healthWas(b)}`)}
            ${td(`${escapeHtml(c.exchange || '–')}<span class="sub">${escapeHtml([c.trading_mode, c.margin_mode].filter(Boolean).join(' · '))}</span>`)}
            ${td(`${money(b.balance.total_bot, cur, dec)}<span class="sub">start ${money(b.balance.starting_capital, '', dec)}</span>`, 'n')}
            ${td(`${pnl(signedMoney(p.profit_all_coin, '', dec), p.profit_all_coin)}<span class="sub">${pnl(pct(p.profit_all_ratio), p.profit_all_ratio)}</span>`, 'n')}
            ${td(`${pnl(signedMoney(today, '', dec), today)}<span class="sub">${b.today ? b.today.trade_count + ' trades' : '–'}</span>`, 'n')}
            ${td(`${b.count.current ?? '–'} / ${b.count.max > 0 ? b.count.max : '∞'}<span class="sub">${pnl(signedMoney(b.open_profit, '', dec), b.open_profit)}</span>`, 'n')}
            ${td(`${plainPct(wl ? p.winning_trades / wl : null)}<span class="sub">${p.winning_trades ?? 0} / ${p.losing_trades ?? 0}</span>`, 'n')}
            ${td(`${num(p.closed_trade_count)}<span class="sub">${escapeHtml(fmtAgo(ms(p.latest_trade_timestamp)))}</span>`, 'n')}
            ${td(healthConnection(b))}
        </tr>`;
    });
    let foot = null;
    const main = overviewCurrency();
    const same = all.filter(b => b.connected && b.config.stake_currency === main);
    if (same.length > 1) {
        const total = f => same.reduce((a, b) => a + (Number(f(b)) || 0), 0);
        const prof = total(b => b.profit.profit_all_coin), today = total(b => b.today && b.today.abs_profit);
        const open = total(b => b.open_profit);
        foot = [`All ${escapeHtml(main)} bots`, '', '', money(total(b => b.balance.total_bot), main), pnl(signedMoney(prof), prof), pnl(signedMoney(today), today),
            `${total(b => b.count.current)}<span class="sub">${pnl(signedMoney(open), open)}</span>`, '', num(total(b => b.profit.closed_trade_count)), ''];
    }
    const head = [
        {label: 'Bot'}, {label: 'State'}, {label: 'Exchange'}, {label: 'Balance', n: 1}, {label: 'Profit', n: 1},
        {label: 'Today', n: 1}, {label: 'Open', n: 1}, {label: 'Win rate', n: 1}, {label: 'Trades', n: 1}, {label: 'Link'},
    ];
    const none = bots().length ? 'No bots match' : 'No bots configured';
    // With filters the table stays, also when it is empty: the filters are in its head
    fleetRenderTable(document.getElementById('ov-bots'), table(head,
        rows.length || !fleetOn() ? rows : [`<tr><td colspan="${head.length}" class="muted">${none}</td></tr>`], {cls: 'bots', foot, empty: none}));
}

function renderOvAllocation() {
    const main = overviewCurrency();
    const same = overviewBots().filter(b => b.connected && b.config.stake_currency === main);
    const others = overviewBots().filter(b => b.connected && b.config.stake_currency !== main).length;
    document.getElementById('ov-alloc-hint').textContent = `balance per bot${main ? ' · ' + main : ''}${others ? ` · ${others} in another currency left out` : ''}`;
    const total = same.reduce((a, b) => a + (b.balance.total_bot || 0), 0);
    donutChart(document.getElementById('ov-alloc'),
        same.map(b => ({name: b.name, value: b.balance.total_bot || 0, color: botColor(b.slug)})),
        {label: 'TOTAL', value: money(total, '', total >= 10000 ? 0 : 2)}, v => money(v, main));
}

function renderOvDaily() {
    const main = overviewCurrency();
    const same = overviewBots().filter(b => b.config.stake_currency === main && b.daily && b.daily.length);
    const days = [];
    const today = new Date();
    for (let i = 29; i >= 0; i--) {
        const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
        days.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`);
    }
    const lookup = same.map(b => new Map(b.daily.map(([date, profit, count]) => [date, {profit, count}])));
    const data = days.map(date => ({
        label: String(Number(date.slice(8))),
        tipTitle: fmtIsoDate(date),
        values: lookup.map(m => (m.get(date) || {}).profit || 0),
        extra: [{value: String(lookup.reduce((a, m) => a + ((m.get(date) || {}).count || 0), 0)), name: 'trades closed'}],
    }));
    const series = same.map(b => ({name: b.name, color: botColor(b.slug)}));
    document.getElementById('ov-daily-hint').textContent = `last 30 days${main ? ' · ' + main : ''}`;
    signedBarChart(document.getElementById('ov-daily'), {data, series, format: v => signedMoney(v, main), labelWidth: 28, empty: 'No closed trades in the last 30 days'});
    document.getElementById('ov-daily-legend').innerHTML = same.length > 1 ? legendHtml(series) : '';
}

function renderOvEvents() {
    const events = overviewBots().flatMap(b => b.events || []).sort((a, b) => b.ts - a.ts).slice(0, 14);
    document.getElementById('ov-events').innerHTML = events.length
        ? events.map(e => eventHtml(e, true)).join('')
        : '<div class="empty-state">No events yet. Entries, exits, warnings and protections show here as they happen.</div>';
}

function renderOvOpen() {
    const trades = overviewBots().flatMap(b => b.open_trades.map(t => ({...t, bot: b})))
        .sort((a, b) => (b.open_timestamp || 0) - (a.open_timestamp || 0));
    const anyFutures = trades.some(t => t.bot.config.trading_mode === 'futures');
    const rows = trades.map(t => {
        const c = t.bot.config, cur = c.stake_currency || '';
        const profit = missing(t.total_profit_abs) ? t.profit_abs : t.total_profit_abs;
        return `<tr onclick="switchTab('bot-${escapeHtml(t.bot.slug)}')">
            ${td(`<span style="color:${botColor(t.bot.slug)}">●</span> ${escapeHtml(t.bot.name)}`)}
            ${td(`<b>${escapeHtml(t.pair)}</b>${t.has_open_orders ? ' <span class="badge warn">order open</span>' : ''}`)}
            ${td(sideTag(t.is_short))}
            ${anyFutures ? td(c.trading_mode === 'futures' ? levTag(t) : '<span class="muted">spot</span>') : ''}
            ${td(escapeHtml(fmtAgo(ms(t.open_timestamp))))}
            ${td(c.trading_mode === 'futures' ? `${money(t.stake_amount, cur, c.stake_currency_decimals)}<span class="sub">${escapeHtml(money(position(t), cur, c.stake_currency_decimals))}</span>` : money(t.stake_amount, cur, c.stake_currency_decimals), 'n')}
            ${td(pct(t.profit_ratio), 'n ' + pnlClass(t.profit_ratio))}
            ${td(signedMoney(profit, cur, c.stake_currency_decimals), 'n ' + pnlClass(profit))}
        </tr>`;
    });
    document.getElementById('ov-open-hint').textContent = trades.length ? `${trades.length} on ${new Set(trades.map(t => t.bot.slug)).size} bots` : '';
    document.getElementById('ov-open').innerHTML = table([
        {label: 'Bot'}, {label: 'Pair'}, {label: 'Side'}, ...(anyFutures ? [{label: 'Leverage'}] : []), {label: 'Opened'},
        {label: anyFutures ? 'Stake<span class="sub">position</span>' : 'Stake', n: 1}, {label: 'Profit %', n: 1}, {label: 'Profit', n: 1},
    ], rows, {cls: 'hover', empty: 'No open trades', emptySub: 'The running bots are watching the market for their next entries.'});
}

// ---------------------------------------------------------------------
// Balance over time, from what the app records every 5 minutes
// ---------------------------------------------------------------------
async function loadOvHistory(force) {
    if (!force && ovHistory && Date.now() - ovHistoryAt < 60000) return renderOvHistory();
    try {
        ovHistory = await api(`history?hours=${ovHours}`);
        ovHistoryAt = Date.now();
    } catch (e) {
        ovHistory = null;
    }
    renderOvHistory();
}

function renderOvHistory() {
    const el = document.getElementById('ov-equity');
    const legend = document.getElementById('ov-equity-legend');
    const main = overviewCurrency();
    const known = overviewBots().filter(b => b.config.stake_currency === main || !b.config.stake_currency);
    const hist = ovHistory && ovHistory.bots;
    if (!hist || !known.some(b => hist[b.slug] && hist[b.slug].points.length > 1)) {
        legend.innerHTML = '';
        return emptyChart(el, 'The app records every bot\'s balance every 5 minutes; this chart fills from the first hours on. A bot\'s own page shows the balance history Freqtrade keeps.');
    }
    const step = ovHistory.step;
    const times = [...new Set(known.flatMap(b => (hist[b.slug] ? hist[b.slug].points : []).map(p => p[0])))].sort((a, b) => a - b);
    // A bot's last balance holds until its next sample, for up to 6 hours (a bot that was away)
    const lines = known.filter(b => hist[b.slug]).map(b => {
        const points = new Map(hist[b.slug].points.map(p => [p[0], p[1]]));
        let last = null, lastAt = -Infinity;
        const values = times.map(t => {
            if (points.has(t) && !missing(points.get(t))) { last = points.get(t); lastAt = t; return last; }
            return t - lastAt <= Math.max(6 * 3600, 2 * step) ? last : null;
        });
        return {name: b.name, color: botColor(b.slug), values, width: 1.5};
    });
    const total = times.map((_, i) => lines.some(l => !missing(l.values[i])) ? lines.reduce((a, l) => a + (l.values[i] || 0), 0) : null);
    // Together, or each bot on a scale of its own size: the total would flatten them
    document.getElementById('ov-mode').hidden = lines.length < 2;
    const series = lines.length < 2 ? lines.map(l => ({...l, width: 2, area: true}))
        : ovMode === 'total' ? [{name: 'All bots', color: color('series-1'), values: total, width: 2, area: true}]
            : lines.map(l => ({...l, width: 1.8}));
    const span = times[times.length - 1] - times[0];
    lineChart(el, {
        x: times, series, height: 230,
        formatX: t => span > 2 * 86400 ? fmtDateTime(t) : fmtTime(t),
        formatY: v => money(v, main),
        xTicks: timeAxisTicks(times[0], times[times.length - 1], 5),
    });
    legend.innerHTML = series.length > 1 ? legendHtml(series.map(s => ({name: s.name, color: s.color, kind: 'line'}))) : '';
}

onTab('overview', () => {
    buildChips('ov-mode', [{key: 'total', label: 'All bots'}, {key: 'bots', label: 'Per bot'}], ovMode, (key) => {
        ovMode = key;
        try { localStorage.setItem(LS + 'ov_mode', key); } catch (e) { /* only a convenience */ }
        renderOvHistory();
    });
    buildChips('ov-range', OV_RANGES, ovHours, (key) => {
        ovHours = key;
        try { localStorage.setItem(LS + 'ov_hours', key); } catch (e) { /* only a convenience */ }
        loadOvHistory(true);
    });
    if (app.status) renderOverview();
    loadOvHistory(true);
}, () => {
    renderOverview();
    loadOvHistory(false);
});
