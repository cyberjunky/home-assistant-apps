// Chronicle dashboard - Activity: the server and what it is doing. Live charts (collected by
// the app every minute while it runs), every session with its query (stop or disconnect it),
// sessions waiting on a lock, the slowest statements (pg_stat_statements), the main settings.

let activityTimer = null;
let activityShowIdle = false;

async function showActivity() {
    await loadActivity();
    clearInterval(activityTimer);
    // Refreshes while the page is open
    activityTimer = setInterval(() => { if (activeTab === 'activity' && !document.hidden) loadActivity(); else clearInterval(activityTimer); }, 10000);
}

async function loadActivity() {
    const el = document.getElementById('activity');
    let a;
    try {
        a = await api('activity');
    } catch (e) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
        return;
    }
    const s = a.server;
    const started = Date.parse(s.started) / 1000;
    const clients = a.sessions.filter(x => x.type === 'client backend');
    const active = clients.filter(x => x.state === 'active' && !x.own);
    const waiting = a.sessions.filter(x => x.blocked_by && x.blocked_by.length);
    const longest = active.reduce((m, x) => Math.max(m, x.query_start ? Date.now() / 1000 - Date.parse(x.query_start) / 1000 : 0), 0);
    const cp = a.checkpointer || {};
    // The app's own query for this page is left out unless everything is shown
    const shown = a.sessions.filter(x => activityShowIdle || (x.type === 'client backend' && x.state !== 'idle' && !x.own) || x.blocked_by.length);

    el.innerHTML = `
        <div class="tiles kpi-tiles">
            ${tile('PostgreSQL', escapeHtml(s.version), `up ${escapeHtml(fmtDuration(Date.now() / 1000 - started))}`)}
            ${tile('Connections', `${num(s.connections)}<small> / ${num(s.max_connections)}</small>`, `${num(active.length)} running a query`)}
            ${tile('Longest query', longest ? escapeHtml(fmtDuration(longest)) : '–', 'running now')}
            ${tile('Waiting on a lock', num(waiting.length), waiting.length ? 'see below' : 'none', waiting.length ? 'warn' : '')}
            ${tile('Checkpoints', fmtCount((cp.num_timed || 0) + (cp.num_requested || 0)), `${fmtCount(cp.num_requested)} requested early`)}
            ${tile('WAL written', a.wal ? bytesUnit(a.wal.wal_bytes) : '–', a.wal ? `${fmtCount(a.wal.wal_records)} records` : '')}
        </div>
        <div class="stats-grid">
            <div class="panel"><div class="panel-body"><div class="chart-title"><span>Transactions</span><span class="hint">per minute, all databases</span></div><div id="act-xacts" class="chart"></div></div></div>
            <div class="panel"><div class="panel-body"><div class="chart-title"><span>Connections</span><span class="hint">running a query or idle</span></div><div id="act-conns" class="chart"></div></div></div>
        </div>
        <div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Sessions</span><span class="hint">
                <label class="action-check"><input type="checkbox" id="act-idle"${activityShowIdle ? ' checked' : ''}> idle and background ones too</label></span></div>
            <div class="table-wrap">${sessionsTable(shown)}</div>
        </div></div>
        <div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Slowest statements</span><span class="hint">by total time, since the statistics were reset (pg_stat_statements)</span></div>
            <div class="table-wrap scroll">${statementsTable(a.statements)}</div>
        </div></div>
        <div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Settings</span><span class="hint">memory is tuned from the app's Memory option</span></div>
            <div class="table-wrap"><table class="data"><tbody>${a.settings.map(x => `<tr><td class="mono">${escapeHtml(x.name)}</td><td><b>${escapeHtml(settingText(x))}</b></td><td class="muted">${escapeHtml(x.short_desc)}</td></tr>`).join('')}</tbody></table></div>
        </div></div>`;

    const series = a.series;
    const colors = [1, 2, 3].map(i => cssVar(`--series-${i}`));
    timeChart(document.getElementById('act-xacts'), {
        x: series.t, height: 180, compact: true, title: 'Transactions', filename: 'chronicle-transactions',
        series: [{name: 'transactions', color: colors[0], kind: 'area', values: series.xacts}],
        format: v => `${fmtValue(v)}/min`, empty: 'Collecting: a point every minute',
    });
    timeChart(document.getElementById('act-conns'), {
        x: series.t, height: 180, compact: true, title: 'Connections', filename: 'chronicle-connections',
        series: [{name: 'running a query', color: colors[1], values: series.active}, {name: 'idle', color: colors[2], values: series.idle}],
        format: v => fmtValue(v), empty: 'Collecting: a point every minute',
    });
}

function settingText(x) {
    if (!x.unit) return x.setting;
    // Settings in 8 kB pages or kB shown as bytes
    const units = {'8kB': 8192, kB: 1024, MB: 1024 * 1024};
    if (units[x.unit]) return fmtBytes(Number(x.setting) * units[x.unit]);
    return `${x.setting} ${x.unit}`;
}

function sessionsTable(sessions) {
    if (!sessions.length) return '<div class="empty-state">No session is running a query</div>';
    const since = t => t ? escapeHtml(fmtDuration(Date.now() / 1000 - Date.parse(t) / 1000)) : '–';
    return `<table class="data"><thead><tr><th>Session</th><th>Database · role</th><th>State</th><th>For</th><th>Query</th><th></th></tr></thead><tbody>
        ${sessions.map(x => `<tr>
            <td><b>${num(x.pid)}</b><div class="muted small">${escapeHtml(x.application || x.type)}${x.client ? ' · ' + escapeHtml(x.client) : ''}</div></td>
            <td>${escapeHtml(x.database || '–')}<div class="muted small">${escapeHtml(x.role || '')}</div></td>
            <td>${x.blocked_by.length ? stateLabel('warn', `waits for ${x.blocked_by.join(', ')}`) : escapeHtml(x.state || x.type)}${x.wait_event ? `<div class="muted small">${escapeHtml(x.wait_event_type)}: ${escapeHtml(x.wait_event)}</div>` : ''}</td>
            <td>${x.state === 'active' ? since(x.query_start) : since(x.state_change)}</td>
            <td class="mono wrap query-cell">${escapeHtml(x.query || '')}</td>
            <td class="nowrap">${x.own || x.type !== 'client backend' ? '' : `
                <button type="button" class="btn btn-small" data-signal="${x.pid}" title="Stop the running query (the session stays)">Stop</button>
                <button type="button" class="btn btn-small btn-danger" data-terminate="${x.pid}" title="Close the session; the program connects again">Disconnect</button>`}</td>
        </tr>`).join('')}</tbody></table>`;
}

function statementsTable(statements) {
    if (statements === null) return '<div class="empty-state">pg_stat_statements is not available in the Chronicle database</div>';
    if (!statements.length) return '<div class="empty-state">No statements recorded yet</div>';
    return `<table class="data"><thead><tr><th>Statement</th><th>Database</th><th class="n">Calls</th><th class="n">Total</th><th class="n">Mean</th><th class="n">Rows</th><th class="n">Cache hits</th></tr></thead><tbody>
        ${statements.map(x => {
            const blocks = (x.shared_blks_hit || 0) + (x.shared_blks_read || 0);
            return `<tr><td class="mono wrap query-cell">${escapeHtml(x.query)}</td><td>${escapeHtml(x.database || '')}</td>
                <td class="n">${escapeHtml(fmtCount(x.calls))}</td><td class="n">${escapeHtml(fmtDuration(x.total_exec_time / 1000))}</td>
                <td class="n">${escapeHtml(fmtDuration(x.mean_exec_time / 1000))}</td><td class="n">${escapeHtml(fmtCount(x.rows))}</td>
                <td class="n">${blocks ? num(100 * x.shared_blks_hit / blocks, 1) + '%' : '–'}</td></tr>`;
        }).join('')}</tbody></table>`;
}

document.getElementById('activity').addEventListener('click', async (e) => {
    const stop = e.target.closest('[data-signal]');
    const kill = e.target.closest('[data-terminate]');
    if (!stop && !kill) return;
    const pid = Number((stop || kill).dataset[stop ? 'signal' : 'terminate']);
    try {
        const r = await api('activity/signal', {pid, terminate: Boolean(kill)});
        toast(r.ok ? (kill ? `Session ${pid} disconnected` : `Query of session ${pid} stopped`) : `Session ${pid} had nothing to stop`);
    } catch (err) {
        toast(err.message, 'error');
    }
    loadActivity();
});
document.getElementById('activity').addEventListener('change', (e) => {
    if (e.target.id === 'act-idle') {
        activityShowIdle = e.target.checked;
        loadActivity();
    }
});
onTab('activity', showActivity);
