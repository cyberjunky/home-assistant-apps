// Freqtrade dashboard - one bot's page: summary, trades, performance, balance and the bot itself.

const BOT_SECTIONS = [
    {key: 'summary', label: 'Summary'}, {key: 'chart', label: 'Chart'}, {key: 'trades', label: 'Trades'}, {key: 'performance', label: 'Performance'},
    {key: 'balance', label: 'Balance'}, {key: 'bot', label: 'Bot'},
];
let botSection = new URLSearchParams(window.location.search).get('section')
    || (() => { try { return localStorage.getItem(LS + 'bot_section') || 'summary'; } catch (e) { return 'summary'; } })();
if (!BOT_SECTIONS.some(s => s.key === botSection)) botSection = 'summary';
const TRADES_PER_PAGE = 50;
const trFilter = {search: '', side: 'all', result: 'all', page: 0};
let pfDirection = 'all';
let logLevel = 'all';
let botLogs = null;
let olderEvents = [];
let botEquity = null;  // the app's own history of this bot, when Freqtrade has none

const cur = () => (app.bot && app.bot.config.stake_currency) || '';
const dec = () => app.bot ? app.bot.config.stake_currency_decimals : undefined;
const bm = v => money(v, cur(), dec());
const bsm = v => signedMoney(v, cur(), dec());
const botData = () => (app.bot && app.bot.data) || {};
const botTrades = () => (app.trades[currentBot] && app.trades[currentBot].trades) || [];

// ---------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------
async function loadBot() {
    const slug = currentBot;
    let details;
    try {
        details = await api('bot/' + encodeURIComponent(slug));
    } catch (e) {
        toast('Cannot read the bot: ' + e.message);
        return;
    }
    if (slug !== currentBot) return;
    app.bot = details;
    // The closed trades change only when the bot's details were read again
    const cached = app.trades[slug];
    if (!cached || cached.total !== details.trades_total || cached.slow !== details.slow_updated) {
        try {
            const answer = await api(`bot/${encodeURIComponent(slug)}/trades`);
            app.trades[slug] = {...answer, slow: details.slow_updated};
        } catch (e) { /* keep the ones we have */ }
    }
    if (slug !== currentBot) return;
    renderBot();
}

function renderBot() {
    if (!app.bot) return;
    renderBotHead();
    renderBotNotices();
    const d = app.bot;
    document.getElementById('bot-updated').textContent = d.updated
        ? `read ${fmtAgo(d.updated)}${d.slow_updated ? ` · details ${fmtAgo(d.slow_updated)}` : ''}` : '';
    ({summary: renderSummary, chart: renderChartSection, trades: renderTrades, performance: renderPerformance, balance: renderBalance, bot: renderBotInfo})[botSection]();
}

function showSection(key) {
    botSection = key;
    try { localStorage.setItem(LS + 'bot_section', key); } catch (e) { /* only a convenience */ }
    document.querySelectorAll('.bot-section').forEach(s => s.classList.toggle('active', s.dataset.section === key));
    hideTip();
    renderBot();
}

// ---------------------------------------------------------------------
// Head: who the bot is, and its controls
// ---------------------------------------------------------------------
function renderBotHead() {
    const d = app.bot, c = d.config;
    const meta = [c.exchange, c.strategy, c.timeframe, [c.trading_mode, c.margin_mode].filter(Boolean).join(' · '), c.version && 'Freqtrade ' + c.version].filter(Boolean);
    let controls = '';
    if (!readOnly() && d.connected) {
        const state = c.state;
        controls = `<div class="controls">
            <button type="button" class="btn" onclick="botControl('start')" ${state === 'running' ? 'disabled' : ''}>▶ Start</button>
            <button type="button" class="btn" onclick="botControl('pause')" ${state !== 'running' ? 'disabled' : ''} title="Open no new trades; keep managing the open ones">⏸ Pause entries</button>
            <button type="button" class="btn btn-danger" onclick="botControl('stop')" ${state === 'stopped' ? 'disabled' : ''}>■ Stop</button>
            <button type="button" class="btn" onclick="botControl('reload_config')" title="Read the configuration and strategy again">⟳ Reload config</button>
        </div>`;
    }
    document.getElementById('bot-head').innerHTML = `
        <div><h1>${escapeHtml(d.name)}</h1><div class="meta">${escapeHtml(meta.join(' · ') || d.host)}</div></div>
        <div class="badges">${d.connected ? stateBadge(c.state) + ' ' + modeBadge(c) : '<span class="badge bad">OFFLINE</span>'}</div>
        ${controls}`;
}

function renderBotNotices() {
    const d = app.bot, notes = [];
    if (!d.connected) notes.push(['bad', `This bot can't be reached${d.error ? ': ' + escapeHtml(d.error) : '…'}${d.updated ? `. What you see was read ${escapeHtml(fmtAgo(d.updated))}.` : ''}`]);
    else if (d.error) notes.push(['', escapeHtml(d.error)]);
    if (d.connected && !d.ws_connected && d.ws_error) notes.push(['', `No live events: the WebSocket ${escapeHtml(d.ws_error)}. The state is still read every ${app.status.config.poll_seconds} seconds.`]);
    if (d.connected && d.config.state === 'stopped') notes.push(['', 'The bot is stopped: it opens no trades and its strategy doesn\'t manage the open ones.']);
    if (d.connected && d.config.state === 'paused') notes.push(['', 'Entries are paused: the bot opens no new trades, but still manages and closes the open ones.']);
    document.getElementById('bot-notices').innerHTML = notes.map(([kind, html]) => `<div class="setup-hint ${kind}"><span>${html}</span></div>`).join('');
}

const CONTROL_TEXT = {
    start: ['Start', 'It starts trading: it opens new trades and manages the open ones.', false],
    pause: ['Pause entries of', 'It opens no new trades, but keeps managing and closing the open ones.', false],
    stop: ['Stop', 'It stops trading: no new trades, and the strategy no longer manages the open ones. Stop-loss orders already on the exchange stay.', true],
    reload_config: ['Reload the configuration of', 'Freqtrade reads its configuration file and strategy again and restarts its trading loop.', false],
};

async function botControl(action) {
    const [verb, text, danger] = CONTROL_TEXT[action];
    const d = app.bot;
    const ok = await confirmDialog({title: `${verb} ${d.name}?`, message: escapeHtml(text), confirmLabel: verb.split(' ')[0], danger});
    if (!ok) return;
    try {
        const answer = await apiPost(`bot/${encodeURIComponent(d.slug)}/control`, {action});
        toast((answer.result && answer.result.status) || 'Done', 'success');
    } catch (e) {
        toast(e.message);
    }
    setTimeout(loadBot, 1500);
}

async function forceExit(tradeId) {
    const d = app.bot;
    const trade = (d.data.status || []).find(t => t.trade_id === tradeId);
    if (!trade) return;
    const profit = missing(trade.total_profit_abs) ? trade.profit_abs : trade.total_profit_abs;
    const ok = await confirmDialog({
        title: `Exit trade #${tradeId}?`,
        message: `Closes <b>${escapeHtml(trade.pair)}</b> ${trade.is_short ? 'short' : 'long'} now with a market order, at about <b class="${pnlClass(profit)}">${escapeHtml(pct(trade.profit_ratio))} (${escapeHtml(bsm(profit))})</b>. The exchange's price decides the result.`,
        confirmLabel: 'Exit at market', danger: true,
    });
    if (!ok) return;
    try {
        const answer = await apiPost(`bot/${encodeURIComponent(d.slug)}/forceexit`, {trade_id: tradeId});
        toast((answer.result && answer.result.result) || 'Exit order placed', 'success');
    } catch (e) {
        toast(e.message);
    }
    setTimeout(loadBot, 1500);
}

// ---------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------
function renderSummary() {
    const d = app.bot, data = botData();
    const p = (data.profit_all && data.profit_all.all) || {};
    const count = data.count || {};
    const bal = data.balance || {};
    const today = d.today || {};
    const wl = (p.winning_trades || 0) + (p.losing_trades || 0);
    const ddCls = (p.current_drawdown || 0) >= 0.1 ? 'warn' : '';
    document.getElementById('bot-tiles').innerHTML = [
        tile('Open / max', `${count.current ?? '–'}<small> / ${count.max > 0 ? count.max : '∞'}</small>`, `in use ${escapeHtml(bm(count.total_stake))}`),
        tile('Profit', pnl(amountHtml(p.profit_all_coin, cur(), dec(), true), p.profit_all_coin), `${pnl(pct(p.profit_all_ratio), p.profit_all_ratio)} · closed and open`),
        tile('Closed profit', pnl(amountHtml(p.profit_closed_coin, cur(), dec(), true), p.profit_closed_coin), `${pnl(pct(p.profit_closed_ratio), p.profit_closed_ratio)} · ${num(p.closed_trade_count)} trades`),
        tile('Today', pnl(amountHtml(today.abs_profit, cur(), dec(), true), today.abs_profit), `${today.trade_count ?? 0} trades closed`),
        tile('Win rate', plainPct(missing(p.winrate) && wl ? p.winning_trades / wl : p.winrate), `${p.winning_trades ?? 0} won · ${p.losing_trades ?? 0} lost`),
        tile('Balance', amountHtml(bal.total_bot, cur(), dec()), `start ${escapeHtml(bm(bal.starting_capital))}`),
        tile('Drawdown', plainPct(p.current_drawdown, 2), `max ${plainPct(p.max_drawdown, 2)} · ${escapeHtml(bm(p.max_drawdown_abs))}`, ddCls),
        tile('Best pair', `<span style="font-size:18px">${escapeHtml(p.best_pair || '–')}</span>`, p.best_pair ? `${pnl(pct(p.best_pair_profit_ratio), p.best_pair_profit_ratio)} · ${escapeHtml(bsm(p.best_pair_profit_abs))}` : ''),
    ].join('');
    renderCumulative();
    renderEquity();
    renderBotDaily();
    renderTradeProfits();
    renderOpenTrades();
    const events = (d.events || []).slice(0, 8);
    document.getElementById('bot-events-short').innerHTML = events.length ? events.map(e => eventHtml(e, false)).join('')
        : '<div class="empty-state">No events yet. They show here as they happen.</div>';
}

function closedByTime() {
    return botTrades().filter(t => t.close_timestamp).slice().sort((a, b) => a.close_timestamp - b.close_timestamp);
}

function renderCumulative() {
    const trades = closedByTime();
    const el = document.getElementById('bot-cum');
    if (trades.length < 2) return emptyChart(el, 'Not enough closed trades yet');
    let total = 0;
    const xs = trades.map(t => ms(t.close_timestamp));
    const values = trades.map(t => (total += t.profit_abs || 0));
    const last = values[values.length - 1];
    document.getElementById('bot-cum-hint').textContent = `${trades.length} closed trades · ${bsm(last)}`;
    lineChart(el, {
        x: xs, series: [{name: 'Profit', color: last >= 0 ? color('pos') : color('neg'), values, area: true}],
        height: 200, zero: true, formatX: fmtDateTime, formatY: bsm,
        xTicks: timeAxisTicks(xs[0], xs[xs.length - 1], 5),
        extraRows: i => [{value: `#${trades[i].trade_id} ${trades[i].pair}`, name: pct(trades[i].profit_ratio)}],
    });
}

function renderEquity() {
    const el = document.getElementById('bot-equity');
    const hint = document.getElementById('bot-equity-hint');
    const wallet = botData().historic_balance;
    let xs = [], values = [], source = '';
    if (wallet && wallet.data && wallet.data.length > 1) {
        const cols = wallet.columns || [];
        const ti = cols.indexOf('__date_ts'), vi = cols.indexOf('total_quote');
        if (ti >= 0 && vi >= 0) {
            wallet.data.forEach(row => { xs.push(row[ti] / 1000); values.push(row[vi]); });
            source = `Freqtrade's wallet history · ${wallet.data.length} samples`;
        }
    }
    if (!xs.length && botEquity && botEquity.slug === currentBot && botEquity.points.length > 1) {
        botEquity.points.forEach(p => { xs.push(p[0]); values.push(p[1]); });
        source = 'recorded by this app';
    }
    hint.textContent = source;
    if (xs.length < 2) {
        if (!botEquity || botEquity.slug !== currentBot) loadBotEquity();
        return emptyChart(el, 'No balance history yet: the app records it every 5 minutes from now on.');
    }
    lineChart(el, {
        x: xs, series: [{name: 'Balance', color: color('series-1'), values, area: true}],
        height: 200, formatX: fmtDateTime, formatY: bm, xTicks: timeAxisTicks(xs[0], xs[xs.length - 1], 5),
    });
}

async function loadBotEquity() {
    const slug = currentBot;
    botEquity = {slug, points: []};
    try {
        const answer = await api('history?hours=2160');
        botEquity = {slug, points: (answer.bots[slug] || {}).points || []};
    } catch (e) { return; }
    if (slug === currentBot && botSection === 'summary' && botEquity.points.length > 1) renderEquity();
}

function periodBars(el, rows, labelOf, titleOf, opts) {
    // Oldest first, from a little before the first period with trades (a young bot has months of nothing)
    const ordered = (rows || []).slice().reverse();
    const first = ordered.findIndex(r => r.trade_count);
    const from = first < 0 ? ordered.length : Math.max(0, Math.min(first - 1, ordered.length - 6));
    const data = ordered.slice(from).map(r => ({
        label: labelOf(r.date), tipTitle: titleOf(r.date), values: [r.abs_profit || 0],
        extra: [{value: pct(r.rel_profit), name: 'of the balance'}, {value: String(r.trade_count || 0), name: 'trades'}],
    }));
    signedBarChart(el, {data, series: [{name: 'Profit'}], bySign: true, format: bsm, ...(opts || {})});
}

function renderBotDaily() {
    const rows = ((botData().daily || {}).data || []).slice(0, 30);
    periodBars(document.getElementById('bot-daily'), rows, date => String(Number(date.slice(8))), fmtIsoDate, {labelWidth: 26, empty: 'No closed trades in the last 30 days'});
}

function renderTradeProfits() {
    const trades = closedByTime().slice(-100);
    document.getElementById('bot-tp-hint').textContent = trades.length ? `last ${trades.length} trades` : '';
    signedBarChart(document.getElementById('bot-tp'), {
        data: trades.map(t => ({
            label: '', tipTitle: `#${t.trade_id} ${t.pair} · ${fmtDateTime(ms(t.close_timestamp))}`, values: [t.profit_abs || 0],
            extra: [{value: pct(t.profit_ratio), name: t.exit_reason || ''}],
        })),
        series: [{name: 'Profit'}], bySign: true, format: bsm, maxBar: 10, empty: 'No closed trades yet',
    });
}

function renderOpenTrades() {
    const trades = (botData().status || []).slice().sort((a, b) => (b.open_timestamp || 0) - (a.open_timestamp || 0));
    const futures = app.bot.config.trading_mode === 'futures';
    const canExit = !readOnly() && app.bot.connected;
    const total = trades.reduce((a, t) => a + (missing(t.total_profit_abs) ? t.profit_abs || 0 : t.total_profit_abs), 0);
    document.getElementById('bot-open-hint').innerHTML = trades.length ? `${trades.length} open · ${pnl(escapeHtml(bsm(total)), total)}` : '';
    const rows = trades.map(t => {
        const profit = missing(t.total_profit_abs) ? t.profit_abs : t.total_profit_abs;
        const dist = t.stoploss_current_dist_ratio;
        return `<tr>
            ${td('#' + t.trade_id, 'muted')}
            ${td(`<b>${escapeHtml(t.pair)}</b>${t.has_open_orders ? ' <span class="badge warn">order open</span>' : ''}`)}
            ${td(`${sideTag(t.is_short)}${t.leverage && t.leverage !== 1 ? ` <span class="muted">${num(t.leverage, 0)}×</span>` : ''}`)}
            ${td(escapeHtml(t.enter_tag || '–'))}
            ${td(`${escapeHtml(fmtDateTime(ms(t.open_timestamp)))}<span class="sub">${escapeHtml(fmtDuration(Date.now() / 1000 - ms(t.open_timestamp)))}</span>`)}
            ${td(bm(t.stake_amount), 'n')}
            ${td(`${price(t.open_rate)}<span class="sub">${price(t.current_rate)}</span>`, 'n')}
            ${td(`${price(t.stop_loss_abs)}<span class="sub">${missing(dist) ? '' : pct(dist)}</span>`, 'n')}
            ${futures ? td(price(t.liquidation_price), 'n') : ''}
            ${td(pct(t.profit_ratio), 'n ' + pnlClass(t.profit_ratio))}
            ${td(bsm(profit), 'n ' + pnlClass(profit))}
            ${canExit ? td(`<button type="button" class="btn btn-small" onclick="forceExit(${Number(t.trade_id)})" title="Close this trade now at the market">Exit</button>`) : ''}
        </tr>`;
    });
    const head = [{label: 'ID'}, {label: 'Pair'}, {label: 'Side'}, {label: 'Entry tag'}, {label: 'Opened'}, {label: 'Stake', n: 1},
        {label: 'Open<span class="sub">now</span>', n: 1}, {label: 'Stop-loss<span class="sub">distance</span>', n: 1}];
    if (futures) head.push({label: 'Liquidation', n: 1});
    head.push({label: 'Profit %', n: 1}, {label: 'Profit', n: 1});
    if (canExit) head.push({label: ''});
    document.getElementById('bot-open').innerHTML = table(head, rows, {empty: 'No open trades'});
}

// ---------------------------------------------------------------------
// Trades
// ---------------------------------------------------------------------
function tradesFilterChanged() {
    trFilter.search = document.getElementById('tr-search').value.trim().toLowerCase();
    trFilter.page = 0;
    renderTrades();
}

function renderTrades() {
    const all = closedByTime().reverse();
    const q = trFilter.search;
    const list = all.filter(t =>
        (trFilter.side === 'all' || (trFilter.side === 'short') === Boolean(t.is_short))
        && (trFilter.result === 'all' || (trFilter.result === 'win' ? t.profit_abs > 0 : t.profit_abs <= 0))
        && (!q || [t.pair, t.enter_tag, t.exit_reason, '#' + t.trade_id].some(v => v && String(v).toLowerCase().includes(q))));
    const sumProfit = list.reduce((a, t) => a + (t.profit_abs || 0), 0);
    const wins = list.filter(t => t.profit_abs > 0).length;
    const total = app.trades[currentBot] ? app.trades[currentBot].total : 0;
    document.getElementById('tr-summary').innerHTML = `${num(list.length)} trades · ${pnl(escapeHtml(bsm(sumProfit)), sumProfit)} · win rate ${plainPct(list.length ? wins / list.length : null)}`
        + (total > all.length ? ` · the latest ${num(all.length)} of ${num(total)}` : '');
    const pages = Math.max(1, Math.ceil(list.length / TRADES_PER_PAGE));
    trFilter.page = Math.min(trFilter.page, pages - 1);
    const shown = list.slice(trFilter.page * TRADES_PER_PAGE, (trFilter.page + 1) * TRADES_PER_PAGE);
    const rows = shown.map(t => {
        const opened = ms(t.open_timestamp), closed = ms(t.close_timestamp);
        return `<tr>
            ${td('#' + t.trade_id, 'muted')}
            ${td(`<b>${escapeHtml(t.pair)}</b>`)}
            ${td(`${sideTag(t.is_short)}${t.leverage && t.leverage !== 1 ? ` <span class="muted">${num(t.leverage, 0)}×</span>` : ''}`)}
            ${td(escapeHtml(t.enter_tag || '–'))}
            ${td(escapeHtml(t.exit_reason || '–'))}
            ${td(escapeHtml(fmtDateTime(opened)))}
            ${td(escapeHtml(fmtDateTime(closed)))}
            ${td(escapeHtml(fmtDuration(closed - opened)), 'n')}
            ${td(bm(t.stake_amount), 'n')}
            ${td(`${price(t.open_rate)}<span class="sub">${price(t.close_rate)}</span>`, 'n')}
            ${td(pct(t.profit_ratio), 'n ' + pnlClass(t.profit_ratio))}
            ${td(bsm(t.profit_abs), 'n ' + pnlClass(t.profit_abs))}
        </tr>`;
    });
    document.getElementById('tr-table').innerHTML = table([
        {label: 'ID'}, {label: 'Pair'}, {label: 'Side'}, {label: 'Entry tag'}, {label: 'Exit reason'}, {label: 'Opened'}, {label: 'Closed'},
        {label: 'Duration', n: 1}, {label: 'Stake', n: 1}, {label: 'Open<span class="sub">close</span>', n: 1}, {label: 'Profit %', n: 1}, {label: 'Profit', n: 1},
    ], rows, {empty: all.length ? 'No trades match' : 'No closed trades yet'});
    document.getElementById('tr-pager').innerHTML = list.length > TRADES_PER_PAGE ? `
        <button type="button" class="btn btn-small" onclick="tradesPage(-1)" ${trFilter.page === 0 ? 'disabled' : ''}>‹ Newer</button>
        <span>${num(trFilter.page * TRADES_PER_PAGE + 1)}–${num(Math.min(list.length, (trFilter.page + 1) * TRADES_PER_PAGE))} of ${num(list.length)}</span>
        <button type="button" class="btn btn-small" onclick="tradesPage(1)" ${trFilter.page >= pages - 1 ? 'disabled' : ''}>Older ›</button>` : '';
}

function tradesPage(step) {
    trFilter.page += step;
    renderTrades();
}

// ---------------------------------------------------------------------
// Performance
// ---------------------------------------------------------------------
function renderPerformance() {
    const data = botData();
    const pa = data.profit_all || {};
    const directions = [{key: 'all', label: 'All'}];
    if (pa.long) directions.push({key: 'long', label: 'Long'});
    if (pa.short) directions.push({key: 'short', label: 'Short'});
    if (!pa[pfDirection]) pfDirection = 'all';
    buildChips('pf-direction', directions, pfDirection, key => { pfDirection = key; renderPfMetrics(); });
    document.getElementById('pf-direction').hidden = directions.length < 2;
    renderPfMetrics();
    renderDistribution();
    periodBars(document.getElementById('pf-weekly'), (data.weekly || {}).data, date => fmtDay(new Date(date + 'T00:00:00') / 1000), date => 'Week of ' + fmtIsoDate(date), {height: 150, labelWidth: 52});
    periodBars(document.getElementById('pf-monthly'), (data.monthly || {}).data,
        date => new Date(date + 'T00:00:00').toLocaleDateString([], {month: 'short'}),
        date => new Date(date + 'T00:00:00').toLocaleDateString([], {month: 'long', year: 'numeric'}), {height: 150, labelWidth: 34});
    renderPairs();
    renderTags();
}

function renderPfMetrics() {
    const p = (botData().profit_all || {})[pfDirection] || {};
    const when = v => v ? escapeHtml(fmtDateTime(ms(v))) : '–';
    const ratio = (v, cls) => missing(v) ? '–' : `<span class="${cls ? pnlClass(v) : ''}">${num(v, 2)}</span>`;
    const rows = [
        ['ROI closed trades', `${pnl(escapeHtml(bsm(p.profit_closed_coin)), p.profit_closed_coin)} <span class="muted">(${pct(p.profit_closed_ratio)}, mean ${pct(p.profit_closed_ratio_mean)})</span>`],
        ['ROI all trades', `${pnl(escapeHtml(bsm(p.profit_all_coin)), p.profit_all_coin)} <span class="muted">(${pct(p.profit_all_ratio)})</span>`],
        ['Trades', `${num(p.trade_count)} <span class="muted">(${num(p.closed_trade_count)} closed)</span>`],
        ['Won / lost', `${num(p.winning_trades)} / ${num(p.losing_trades)} <span class="muted">(win rate ${plainPct(p.winrate)})</span>`],
        ['Profit factor', ratio(p.profit_factor)],
        ['Expectancy', `${escapeHtml(bsm(p.expectancy))} <span class="muted">(ratio ${ratio(p.expectancy_ratio)})</span>`],
        ['Average duration', escapeHtml(p.avg_duration || '–')],
        ['Best pair', p.best_pair ? `${escapeHtml(p.best_pair)} <span class="muted">${pct(p.best_pair_profit_ratio)} · ${escapeHtml(bsm(p.best_pair_profit_abs))}</span>` : '–'],
        ['CAGR', missing(p.cagr) ? '–' : pnl(pct(p.cagr), p.cagr)],
        ['Sharpe / Sortino', `${ratio(p.sharpe)} / ${ratio(p.sortino)}`],
        ['Calmar / SQN', `${ratio(p.calmar)} / ${ratio(p.sqn)}`],
        ['Max drawdown', `${plainPct(p.max_drawdown, 2)} · ${escapeHtml(bm(p.max_drawdown_abs))}<span class="sub">${when(p.max_drawdown_start_timestamp)} → ${when(p.max_drawdown_end_timestamp)}</span>`],
        ['Current drawdown', `${plainPct(p.current_drawdown, 2)} · ${escapeHtml(bm(p.current_drawdown_abs))}<span class="sub">since ${when(p.current_drawdown_start_timestamp)}, from ${escapeHtml(bm(p.current_drawdown_high))}</span>`],
        ['Trading volume', escapeHtml(bm(p.trading_volume))],
        ['Bot started', when(p.bot_start_timestamp)],
        ['First trade', when(p.first_trade_timestamp)],
        ['Latest trade', when(p.latest_trade_timestamp)],
    ].map(([label, value]) => `<tr>${td(label)}${td(value, 'n')}</tr>`);
    document.getElementById('pf-metrics').innerHTML = table([{label: 'Metric'}, {label: 'Value', n: 1}], rows);
}

function renderDistribution() {
    const ratios = botTrades().filter(t => !missing(t.profit_ratio)).map(t => t.profit_ratio * 100);
    const el = document.getElementById('pf-dist');
    if (ratios.length < 3) return emptyChart(el, 'Not enough closed trades yet');
    // Bins line up with zero, so no bin holds both wins and losses
    const sorted = ratios.slice().sort((a, b) => a - b);
    const lo = sorted[Math.floor(sorted.length * 0.01)], hi = sorted[Math.ceil(sorted.length * 0.99) - 1];
    const width = niceStep(Math.max(0.1, hi - lo), 20);
    const first = Math.floor(lo / width), last = Math.ceil(hi / width) - 1;
    const bins = [];
    for (let i = first; i <= Math.max(first, last); i++) bins.push({from: i * width, wins: 0, losses: 0});
    ratios.forEach(r => {
        const i = Math.min(bins.length - 1, Math.max(0, Math.floor(r / width) - first));
        bins[i][r > 0 ? 'wins' : 'losses'] += 1;
    });
    const digits = width < 1 ? (width < 0.1 ? 2 : 1) : 0;
    barChart(el, {
        data: bins.map(b => ({
            label: `${num(b.from, digits)}%`, tipTitle: `${num(b.from, digits)}% to ${num(b.from + width, digits)}%${b === bins[0] ? ' and lower' : b === bins[bins.length - 1] ? ' and higher' : ''}`,
            values: [b.losses, b.wins],
        })),
        series: [{name: 'losing trades', color: color('neg')}, {name: 'winning trades', color: color('pos')}],
        format: v => `${num(v)}`, height: 170, labelWidth: 46,
    });
}

function avgPct(row) {
    return row.count ? row.profit_ratio / row.count : null;
}

function renderPairs() {
    const pairs = (botData().performance || []).slice().sort((a, b) => (b.profit_abs || 0) - (a.profit_abs || 0));
    const max = Math.max(1e-9, ...pairs.map(p => Math.abs(p.profit_abs || 0)));
    const rows = pairs.map(p => {
        const share = Math.abs(p.profit_abs || 0) / max * 50;
        const bar = `<div class="cell-bar"><span class="mid"></span><i style="${p.profit_abs >= 0 ? `left:50%;width:${share}%;background:var(--pos)` : `right:50%;width:${share}%;background:var(--neg)`}"></i></div>`;
        return `<tr>${td(`<b>${escapeHtml(p.pair)}</b>`)}${td(num(p.count), 'n')}${td(pct(avgPct(p)), 'n ' + pnlClass(avgPct(p)))}${td(bsm(p.profit_abs), 'n ' + pnlClass(p.profit_abs))}${td(bar)}</tr>`;
    });
    document.getElementById('pf-pairs').innerHTML = table([{label: 'Pair'}, {label: 'Trades', n: 1}, {label: 'Avg profit %', n: 1}, {label: 'Profit', n: 1}, {label: ''}],
        rows, {empty: 'No closed trades yet'});
}

function renderTags() {
    const data = botData();
    const entries = (data.entries || []).slice().sort((a, b) => (b.profit_abs || 0) - (a.profit_abs || 0));
    document.getElementById('pf-entries').innerHTML = table([{label: 'Entry tag'}, {label: 'Trades', n: 1}, {label: 'Avg %', n: 1}, {label: 'Profit', n: 1}],
        entries.map(e => `<tr>${td(escapeHtml(e.enter_tag || 'Other'))}${td(num(e.count), 'n')}${td(pct(avgPct(e)), 'n ' + pnlClass(avgPct(e)))}${td(bsm(e.profit_abs), 'n ' + pnlClass(e.profit_abs))}</tr>`),
        {empty: data.entries ? 'No entry tags yet' : 'This Freqtrade version doesn\'t report entry tags'});
    const reasons = (data.stats && data.stats.exit_reasons) || {};
    const exits = (data.exits || []).slice().sort((a, b) => (b.count || 0) - (a.count || 0));
    document.getElementById('pf-exits').innerHTML = table([{label: 'Exit reason'}, {label: 'Trades', n: 1}, {label: 'Won / lost', n: 1}, {label: 'Avg %', n: 1}, {label: 'Profit', n: 1}],
        exits.map(e => {
            const r = reasons[e.exit_reason];
            return `<tr>${td(escapeHtml(e.exit_reason || 'Other'))}${td(num(e.count), 'n')}${td(r ? `${r.wins} / ${r.losses}${r.draws ? ` <span class="muted">(${r.draws} even)</span>` : ''}` : '–', 'n')}${td(pct(avgPct(e)), 'n ' + pnlClass(avgPct(e)))}${td(bsm(e.profit_abs), 'n ' + pnlClass(e.profit_abs))}</tr>`;
        }),
        {empty: data.exits ? 'No exits yet' : 'This Freqtrade version doesn\'t report exit reasons'});
    const durations = data.stats && data.stats.durations;
    if (durations) {
        document.getElementById('pf-exits').insertAdjacentHTML('beforeend', `<p class="panel-sub" style="margin-top:10px">Average duration: won ${escapeHtml(fmtDuration(durations.wins))} · even ${escapeHtml(fmtDuration(durations.draws))} · lost ${escapeHtml(fmtDuration(durations.losses))}</p>`);
    }
}

// ---------------------------------------------------------------------
// Balance
// ---------------------------------------------------------------------
function renderBalance() {
    if (!app.bot) return;
    const b = botData().balance || {};
    const botOnly = document.getElementById('bal-bot-only').checked;
    const hideSmall = document.getElementById('bal-hide-small').checked;
    const stake = b.stake || cur();
    const threshold = Math.max(1e-8, (b.total_bot || b.total || 0) * 0.001);
    const valueOf = c => botOnly ? (missing(c.est_stake_bot) ? c.est_stake : c.est_stake_bot) : c.est_stake;
    const list = (b.currencies || [])
        .filter(c => !botOnly || c.is_bot_managed)
        .filter(c => !hideSmall || Math.abs(valueOf(c) || 0) >= threshold)
        .sort((x, y) => (valueOf(y) || 0) - (valueOf(x) || 0));
    document.getElementById('bal-note').innerHTML = b.note ? `<div class="setup-hint info"><span>${escapeHtml(b.note)}</span></div>` : '';
    const total = botOnly ? b.total_bot : b.total;
    document.getElementById('bal-tiles').innerHTML = [
        tile(botOnly ? 'Bot balance' : 'Account balance', amountHtml(total, stake, dec()), `start ${escapeHtml(money(b.starting_capital, stake, dec()))}`),
        tile('Value', amountHtml(botOnly ? b.value_bot : b.value, b.symbol), `in ${escapeHtml(b.symbol || '–')}`),
        tile('Against the start', pnl(pct(b.starting_capital_ratio), b.starting_capital_ratio), escapeHtml(signedMoney((b.total_bot || 0) - (b.starting_capital || 0), stake, dec()))),
        tile('Currencies', num(list.length), botOnly ? 'managed by the bot' : 'on the account'),
    ].join('');
    const rows = list.map(c => `<tr>
        ${td(`<b>${escapeHtml(c.currency)}</b>${c.is_position ? ' ' + sideTag(c.side === 'short') : ''}`)}
        ${td(price(c.free), 'n')}
        ${td(money(valueOf(c), '', dec()), 'n')}
        ${td(price(c.used), 'n')}
        ${td(missing(c.bot_owned) || !c.bot_owned ? '–' : price(c.bot_owned), 'n')}
    </tr>`);
    document.getElementById('bal-table').innerHTML = table(
        [{label: 'Currency'}, {label: 'Free', n: 1}, {label: `In ${escapeHtml(stake)}`, n: 1}, {label: 'Used', n: 1}, {label: 'Bot owned', n: 1}],
        rows, {empty: 'No balances', foot: [`Total`, '', escapeHtml(money(total, stake, dec())), '', hideSmall ? `<span class="muted">hiding under ${escapeHtml(money(threshold, stake))}</span>` : '']});
    document.getElementById('bal-breakdown-hint').textContent = stake;
    const palette = ['series-1', 'series-2', 'series-3', 'series-4', 'series-5'];
    donutChart(document.getElementById('bal-donut'),
        list.filter(c => valueOf(c) > 0).map((c, i) => ({name: c.currency, value: valueOf(c), color: color(palette[i % palette.length])})),
        {label: 'TOTAL', value: money(total, '', (total || 0) >= 10000 ? 0 : 2)}, v => money(v, stake, dec()));
    const share = b.total ? b.total_bot / b.total : null;
    document.getElementById('bal-share').innerHTML = missing(share) ? '<div class="empty-state">Not known</div>' : `
        <div class="share-row"><span>Bot-managed share of the account</span><b>${plainPct(share, 2)}</b></div>
        <div class="share-bar"><i style="width:${Math.min(100, Math.max(0, share * 100))}%"></i></div>
        <div class="share-row"><span>Bot ${escapeHtml(money(b.total_bot, stake, dec()))}</span><span>Account ${escapeHtml(money(b.total, stake, dec()))}</span></div>`;
}

// ---------------------------------------------------------------------
// The bot: its configuration, connection, locks, pairlist, activity and log
// ---------------------------------------------------------------------
function renderBotInfo() {
    const d = app.bot, data = botData();
    const c = data.config || {};
    const p = (data.profit_all && data.profit_all.all) || {};
    const health = data.health || {};
    const yes = v => v ? 'yes' : 'no';
    document.getElementById('bi-status').innerHTML = [
        infoRow('Freqtrade', escapeHtml(c.version || '–')),
        infoRow('State', d.connected ? stateBadge(c.state) : '<span class="badge bad">OFFLINE</span>'),
        infoRow('Run mode', `${modeBadge(c)} ${escapeHtml(c.runmode || '')}`),
        infoRow('Exchange', escapeHtml([c.exchange, c.demo_trading ? 'demo' : ''].filter(Boolean).join(' · ') || '–')),
        infoRow('Trading mode', escapeHtml([c.trading_mode, c.margin_mode].filter(Boolean).join(' · ') || '–')),
        infoRow('Strategy', escapeHtml([c.strategy, c.strategy_version].filter(Boolean).join(' · ') || '–')),
        infoRow('Timeframe', escapeHtml(c.timeframe || '–')),
        infoRow('Bot name', escapeHtml(c.bot_name || '–')),
        infoRow('Bot started', escapeHtml(fmtDateTime(ms(p.bot_start_timestamp)))),
        infoRow('Last processed', health.last_process_ts ? escapeHtml(fmtAgo(health.last_process_ts)) : '–'),
        data.sysinfo ? infoRow('Bot\'s machine', `CPU ${num(data.sysinfo.cpu_avg, 0)}% · RAM ${num(data.sysinfo.ram_pct, 0)}%`) : '',
    ].join('');
    const roi = Object.entries(c.minimal_roi || {}).sort((a, b) => Number(a[0]) - Number(b[0]))
        .map(([minutes, ratio]) => `${minutes} min → ${plainPct(ratio, 1)}`).join('<br>');
    const ot = c.order_types || {};
    const ut = c.unfilledtimeout || {};
    document.getElementById('bi-rules').innerHTML = [
        infoRow('Max open trades', escapeHtml(c.max_open_trades === -1 || c.max_open_trades === null ? 'unlimited' : String(c.max_open_trades ?? '–'))),
        infoRow('Stake amount', `${escapeHtml(String(c.stake_amount ?? '–'))} ${escapeHtml(c.stake_currency || '')}${c.available_capital ? ` <span class="muted">of ${escapeHtml(bm(c.available_capital))}</span>` : ''}`),
        infoRow('Stop-loss', `${missing(c.stoploss) ? '–' : plainPct(c.stoploss, 2)}${c.stoploss_on_exchange ? ' · on the exchange' : ''}${c.use_custom_stoploss ? ' · custom' : ''}`),
        infoRow('Trailing stop', c.trailing_stop ? `${plainPct(c.trailing_stop_positive, 2)} from ${plainPct(c.trailing_stop_positive_offset, 2)}${c.trailing_only_offset_is_reached ? ' (only once reached)' : ''}` : 'no'),
        infoRow('ROI', roi || '–'),
        infoRow('Order types', escapeHtml(['entry', 'exit', 'stoploss'].filter(k => ot[k]).map(k => `${k} ${ot[k]}`).join(' · ') || '–')),
        infoRow('Unfilled timeout', ut.entry ? escapeHtml(`entry ${ut.entry} · exit ${ut.exit} ${ut.unit || ''}`) : '–'),
        infoRow('Position adjustment', c.position_adjustment_enable ? `yes${c.max_entry_position_adjustment >= 0 ? `, at most ${c.max_entry_position_adjustment}` : ''}` : 'no'),
        infoRow('Shorting', yes(c.short_allowed)),
        infoRow('Force entry', yes(c.force_entry_enable)),
    ].join('');
    const t = app.trades[currentBot];
    document.getElementById('bi-connection').innerHTML = [
        infoRow('Address', escapeHtml(d.url)),
        infoRow('REST API', d.connected ? `${stateLabel('ok', 'connected')} <span class="muted">${escapeHtml(fmtDuration(Date.now() / 1000 - d.connected_since))}</span>` : stateLabel('bad', d.error || 'not reachable')),
        infoRow('WebSocket', d.ws_connected ? `${stateLabel('ok', 'connected')} <span class="muted">${escapeHtml(fmtDuration(Date.now() / 1000 - d.ws_since))}</span>` : stateLabel('warn', d.ws_error || 'connecting')),
        infoRow('State read', `${escapeHtml(fmtAgo(d.updated))} · every ${app.status.config.poll_seconds} s`),
        infoRow('Details read', `${escapeHtml(fmtAgo(d.slow_updated))} · every 5 min and after a trade`),
        infoRow('Closed trades read', t ? `${num(t.trades.length)} of ${num(t.total)}` : '–'),
    ].join('');
    const locks = (data.locks && data.locks.locks) || [];
    document.getElementById('bi-locks').innerHTML = table([{label: 'Pair'}, {label: 'Side'}, {label: 'Until'}, {label: 'Reason'}],
        locks.map(l => `<tr>${td(`<b>${escapeHtml(l.pair)}</b>${l.active ? '' : ' <span class="muted">(ended)</span>'}`)}${td(escapeHtml(l.side === '*' ? 'both' : l.side || '–'))}${td(escapeHtml(fmtDateTime(ms(l.lock_end_timestamp))))}${td(escapeHtml(l.reason || '–'), 'wrap')}</tr>`),
        {empty: 'No pairs locked'});
    const wl = data.whitelist || {};
    const pairs = wl.whitelist || [];
    document.getElementById('bi-pairs-title').textContent = `Pairlist · ${pairs.length} pairs${wl.method ? ' · ' + wl.method.join(', ') : ''}`;
    document.getElementById('bi-pairs').innerHTML = pairs.length ? pairs.map(p => `<span class="badge">${escapeHtml(p)}</span>`).join('') : '<span class="panel-sub">Not known</span>';
    const events = [...(d.events || []), ...olderEvents];
    document.getElementById('bot-events').innerHTML = events.length ? events.map(e => eventHtml(e, false)).join('')
        : '<div class="empty-state">No events yet. Entries, exits, warnings and protections show here as they happen.</div>';
    renderLogs();
}

async function moreBotEvents() {
    const shown = [...(app.bot.events || []), ...olderEvents];
    const before = shown.length ? shown[shown.length - 1].ts : Date.now() / 1000;
    try {
        const more = await api(`events?bot=${encodeURIComponent(currentBot)}&limit=100&before=${before}`);
        olderEvents.push(...more);
        if (!more.length) {
            document.getElementById('bot-events-more').disabled = true;
            toast('No older events', 'success');
        }
        renderBotInfo();
    } catch (e) {
        toast(e.message);
    }
}

async function loadLogs() {
    const slug = currentBot;
    document.getElementById('bot-logs').innerHTML = '<div class="empty-state">Reading the log…</div>';
    try {
        const answer = await api(`bot/${encodeURIComponent(slug)}/logs?limit=200`);
        if (slug !== currentBot) return;
        if (answer.error) throw new Error(answer.error);
        botLogs = {slug, lines: (answer.logs || []).slice().reverse()};
    } catch (e) {
        botLogs = {slug, error: e.message};
    }
    renderLogs();
}

function renderLogs() {
    const el = document.getElementById('bot-logs');
    if (!botLogs || botLogs.slug !== currentBot) {
        el.innerHTML = '<div class="empty-state">The bot\'s last 200 log lines, read when you ask.</div>';
        return;
    }
    if (botLogs.error) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(botLogs.error)}</div>`;
        return;
    }
    const levels = {all: () => true, warning: l => ['WARNING', 'ERROR', 'CRITICAL'].includes(l), error: l => ['ERROR', 'CRITICAL'].includes(l)};
    // Freqtrade gives [date, timestamp, logger, level, message]
    const lines = botLogs.lines.filter(line => levels[logLevel](line[3]));
    el.innerHTML = lines.length ? lines.map(line => `<div class="line"><span class="src">${escapeHtml(line[0])}</span><span class="lv ${escapeHtml(line[3])}">${escapeHtml(line[3])}</span>`
        + `<span class="msg">${escapeHtml(line[4])} <span class="src">${escapeHtml(line[2])}</span></span></div>`).join('')
        : '<div class="empty-state">No lines at this level</div>';
}

// ---------------------------------------------------------------------
onTab('bot', (changedBot) => {
    buildChips('bot-sections', BOT_SECTIONS, botSection, showSection);
    document.querySelectorAll('.bot-section').forEach(s => s.classList.toggle('active', s.dataset.section === botSection));
    buildChips('tr-side', [{key: 'all', label: 'All'}, {key: 'long', label: 'Long'}, {key: 'short', label: 'Short'}], trFilter.side, k => { trFilter.side = k; trFilter.page = 0; renderTrades(); });
    buildChips('tr-result', [{key: 'all', label: 'All'}, {key: 'win', label: 'Won'}, {key: 'loss', label: 'Lost'}], trFilter.result, k => { trFilter.result = k; trFilter.page = 0; renderTrades(); });
    buildChips('log-level', [{key: 'all', label: 'All'}, {key: 'warning', label: 'Warnings'}, {key: 'error', label: 'Errors'}], logLevel, k => { logLevel = k; renderLogs(); });
    if (changedBot || !app.bot || app.bot.slug !== currentBot) {
        app.bot = null;
        olderEvents = [];
        botEquity = null;
        trFilter.page = 0;
        document.getElementById('bot-events-more').disabled = false;
        const b = botBySlug(currentBot);
        document.getElementById('bot-head').innerHTML = `<div><h1>${escapeHtml(b ? b.name : '')}</h1><div class="meta">Reading…</div></div>`;
        document.getElementById('bot-notices').innerHTML = '';
    } else {
        renderBot();
    }
    loadBot();
}, loadBot);
