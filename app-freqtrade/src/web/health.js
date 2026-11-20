// Freqtrade dashboard - Health: whether a bot's API answers, what the bot is doing and how old
// the data is. Three different things: a stopped bot still answers, and a bot that stopped
// answering was running when it was last read.

function healthModel(bot, now = Date.now() / 1000) {
    const d = bot.diagnostics || {};
    const age = stamp => typeof stamp === 'number' ? Math.max(0, now - stamp) : null;
    const dataAge = age(bot.updated), stateAge = age(d.state_updated), detailsAge = age(d.details_updated);
    const stale = dataAge === null || dataAge > (d.stale_after || 90);
    const stateFresh = stateAge !== null && stateAge <= (d.stale_after || 90) && d.api_status === 'reachable';
    return {api: d.api_status || 'unknown', dataAge, stateAge, detailsAge, stale,
        complete: !!bot.connected, stateFresh, state: bot.config?.state || 'unknown',
        detailsStale: detailsAge === null || detailsAge > (d.details_stale_after || 900),
        stoppedOpen: stateFresh && bot.config?.state === 'stopped' && (bot.count?.current || 0) > 0};
}
function healthAge(age) {
    if (age === null) return 'never';
    if (age < 60) return Math.floor(age) + ' s';
    if (age < 3600) return Math.floor(age / 60) + ' min';
    return (age / 3600).toFixed(1) + ' h';
}
const HEALTH_API = {
    reachable: 'API answers', unreachable: 'API can\'t be reached',
    error: 'API refused or gave a wrong answer', unknown: 'API not read yet',
};
function healthApiLabel(model) {
    return HEALTH_API[model.api] || HEALTH_API.unknown;
}
// The state as a badge. A state that could not be read again is not shown as if it still holds:
// healthWas says what it was, to put after the other badges
function healthState(bot) {
    const h = healthModel(bot);
    if (h.stateFresh) return stateBadge(bot.config.state);
    if (h.api === 'unknown') return '<span class="badge mode">CONNECTING</span>';
    return `<span class="badge bad">${h.api === 'reachable' ? 'STALE' : 'OFFLINE'}</span>`;
}
function healthWas(bot) {
    const h = healthModel(bot);
    if (h.stateFresh || h.api === 'unknown' || h.stateAge === null) return '';
    return ` <span class="was">was ${escapeHtml(h.state)} ${healthAge(h.stateAge)} ago</span>`;
}
// The Link column: a dot for the REST API, a dot for the WebSocket, and the age of the data when it matters
function healthConnection(bot) {
    const h = healthModel(bot);
    const rest = h.api === 'reachable' ? (h.complete ? 'ok' : 'warn') : h.api === 'unknown' ? 'warn' : 'bad';
    const restTitle = healthApiLabel(h) + (h.api === 'reachable' && !h.complete ? ', but not everything could be read' : '');
    const wsTitle = 'WebSocket ' + (bot.ws_connected ? 'connected' : bot.ws_error || 'not connected');
    const note = h.dataAge === null ? '' : h.stale ? `<span class="sub neg">${healthAge(h.dataAge)} old</span>` : '';
    return `<span class="sdot ${rest}" title="${escapeHtml(restTitle)}"></span> <span class="sdot ${bot.ws_connected ? 'ok' : 'warn'}" title="${escapeHtml(wsTitle)}"></span>${note}`;
}
// A bot's page: one line that opens to the times behind it. Open by itself when something is off
function renderHealth(bot) {
    const root = document.getElementById('bot-health');
    const h = healthModel(bot), d = bot.diagnostics || {};
    const at = stamp => stamp ? escapeHtml(fmtDateTime(stamp)) : '–';
    const off = h.api !== 'reachable' || h.stale || !h.complete || !!d.details_error;
    const line = [
        healthApiLabel(h),
        `data ${h.dataAge === null ? 'never read' : healthAge(h.dataAge) + ' old'}${h.stale && h.dataAge !== null ? ' (stale)' : ''}`,
        `details ${h.detailsAge === null ? 'never read' : healthAge(h.detailsAge) + ' old'}`,
        `WebSocket ${bot.ws_connected ? 'connected' : 'not connected'}`,
    ].join(' · ');
    const wasOpen = root.firstElementChild ? root.firstElementChild.open : null;
    const userSet = root.dataset.user === bot.slug;
    root.innerHTML = `<details class="health"${(userSet ? wasOpen : off) ? ' open' : ''}>
        <summary><span class="sdot ${off ? (h.api === 'reachable' ? 'warn' : 'bad') : 'ok'}"></span> ${escapeHtml(line)}</summary>
        <div class="health-grid">
            <div><b>State read</b>${at(d.state_updated)}<span class="sub">${h.stateAge === null ? 'not read yet' : escapeHtml(h.state) + (h.stateFresh ? '' : ', as last known')}</span></div>
            <div><b>Everything read</b>${at(bot.updated)}<span class="sub">balance, profit, open trades</span></div>
            <div><b>Details read</b>${at(d.details_updated)}<span class="sub">${d.details_error ? `<span class="neg">${escapeHtml(d.details_error)}</span>` : 'history, performance, pairs'}</span></div>
            <div><b>Last try</b>${at(d.last_attempt)}<span class="sub">took ${d.refresh_duration_ms ?? '–'} ms</span></div>
        </div>
        <p class="hint">The ages say when this app last read the bot, not how old the exchange's prices are. The state and the balances count as stale after ${d.stale_after || 90} seconds without a full read, the details after ${Math.round((d.details_stale_after || 900) / 60)} minutes. A quiet WebSocket says nothing about the bot: it only carries events.</p>
    </details>`;
    root.firstElementChild.addEventListener('toggle', () => { root.dataset.user = bot.slug; });
}
