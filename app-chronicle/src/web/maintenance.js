// Chronicle dashboard - Maintenance: the tasks (VACUUM, REINDEX, compression, backups, restores,
// copies) with their logs, the read-only logins for Grafana and other tools, and the upkeep
// of a whole database. A table's own upkeep is on its page under Databases.

let maintenanceTimer = null;
const openLogs = new Set();

async function showMaintenance() {
    const el = document.getElementById('maintenance');
    let tasks, readers, databases;
    try {
        [tasks, readers, databases] = await Promise.all([api('tasks'), api('readers'), api('databases')]);
    } catch (e) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
        return;
    }
    const names = databases.databases.filter(d => d.allow && d.name !== 'postgres').map(d => d.name);
    const running = tasks.tasks.some(t => t.state === 'running');
    el.innerHTML = `
        <div class="stats-grid">
            <div class="panel"><div class="panel-body">
                <div class="section-title">Upkeep of a database</div>
                <p class="panel-sub">PostgreSQL's autovacuum normally does this by itself. Run it after removing a lot of history, or when the Databases page shows many dead rows.</p>
                <div class="form-row"><span>Database</span><select class="select" id="mt-database">${names.map(n => `<option${n === databases.chronicle ? ' selected' : ''}>${escapeHtml(n)}</option>`).join('')}</select></div>
                <div class="detail-actions">
                    <button type="button" class="btn" onclick="databaseTask('vacuum')" ${running ? 'disabled' : ''}>VACUUM ANALYZE</button>
                    <button type="button" class="btn" onclick="databaseTask('analyze')" ${running ? 'disabled' : ''}>ANALYZE</button>
                    <button type="button" class="btn" onclick="databaseTask('reindex')" ${running ? 'disabled' : ''}>REINDEX</button>
                </div>
                ${running ? '<p class="panel-sub">A task is running; one at a time.</p>' : ''}
            </div></div>
            <div class="panel"><div class="panel-body">
                <div class="section-title">Read-only logins</div>
                <p class="panel-sub">For Grafana and other tools: a login that reads every table of the databases you pick, also tables the Chronicle integration adds later. To reach the database from another machine, set the port in the app's Network settings in Home Assistant.</p>
                ${readers.readers.length ? `<table class="data"><thead><tr><th>Login</th><th>Databases</th><th></th></tr></thead><tbody>
                    ${readers.readers.map(r => `<tr><td><b>${escapeHtml(r.name)}</b></td><td>${escapeHtml((r.databases || []).join(', ') || '–')}</td>
                        <td><button type="button" class="btn btn-small btn-danger" data-drop-reader="${escapeHtml(r.name)}">Remove</button></td></tr>`).join('')}</tbody></table>`
                    : '<div class="empty-state">No read-only logins yet</div>'}
                <div class="detail-actions" style="margin-top:10px"><button type="button" class="btn btn-primary" onclick="createReader()">New read-only login…</button></div>
            </div></div>
        </div>
        <div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Tasks</span><span class="hint">the last 30, newest first; kept until the app restarts${tasks.helper ? '' : ' · <span class="sb-bad">the database helper is not running</span>'}</span></div>
            ${tasks.tasks.length ? tasks.tasks.map(taskRow).join('') : '<div class="empty-state">No tasks since the app started</div>'}
        </div></div>`;
    clearTimeout(maintenanceTimer);
    if (running) maintenanceTimer = setTimeout(() => { if (activeTab === 'maintenance') showMaintenance(); }, 2000);
}

function taskRow(t) {
    const state = t.state === 'running' ? stateLabel('warn', 'running') : t.state === 'done' ? stateLabel('ok', 'done') : stateLabel('bad', 'failed');
    const took = (t.finished || Date.now() / 1000) - t.started;
    const open = openLogs.has(t.id) || t.state === 'running';
    return `<div class="task-row">
        <button type="button" class="task-head" data-log="${escapeHtml(t.id)}" aria-expanded="${open}">
            ${state}<b>${escapeHtml(t.title)}</b>
            <span class="muted">${escapeHtml(fmtDateTime(t.started))} · ${escapeHtml(fmtDuration(took))}</span>
            ${t.error ? `<span class="sb-bad">${escapeHtml(t.error)}</span>` : ''}
        </button>
        ${open && t.log.length ? `<pre class="code-block task-log">${escapeHtml(t.log.join('\n'))}</pre>` : ''}
    </div>`;
}

function databaseTask(action) {
    const database = document.getElementById('mt-database').value;
    const notes = {
        vacuum: 'Cleans up dead rows and updates the statistics of every table. Everything stays usable meanwhile.',
        analyze: 'Updates the statistics of every table. Quick.',
        reindex: 'Rebuilds every index of the database. Writing waits per table while its indexes are rebuilt; this can take long on a large history.',
    };
    const label = {vacuum: 'VACUUM ANALYZE', analyze: 'ANALYZE', reindex: 'REINDEX'}[action];
    actionModal(`${label} ${database}`, `<p>${notes[action]}</p>`, label, action === 'reindex', async () => {
        const job = await api('maintenance', {action, database});
        watchTask(job.id);
        showMaintenance();
    });
}

async function createReader() {
    let databases;
    try { databases = await api('databases'); } catch (e) { return toast(e.message, 'error'); }
    const names = databases.databases.filter(d => d.allow && d.name !== 'postgres').map(d => d.name);
    actionModal('New read-only login', `
        <label class="form-row"><span>Login name</span><input class="input" id="reader-name" value="grafana" autocomplete="off" spellcheck="false"></label>
        <div class="form-row"><span>Databases</span><div>${names.map(n => `<label class="action-check"><input type="checkbox" class="reader-db" value="${escapeHtml(n)}"${n === databases.chronicle ? ' checked' : ''}> ${escapeHtml(n)}</label>`).join('')}</div></div>
        <p class="panel-sub">The password is made by the app and shown once.</p>`,
        'Create', false, async () => {
            const result = await api('readers/create', {
                name: document.getElementById('reader-name').value.trim(),
                databases: [...document.querySelectorAll('.reader-db:checked')].map(c => c.value),
            });
            setTimeout(() => showReaderPassword(result), 0);
        });
}

function showReaderPassword(r) {
    const conn = app.status && app.status.connection || {};
    actionModal(`Login ${r.name}`, `
        <p>Copy the password now: it is not shown again.</p>
        ${infoRow('Host', `<span class="mono">${escapeHtml(conn.host || '')}</span> <span class="muted">(from another machine: Home Assistant's address and the port you set)</span>`)}
        ${infoRow('Port', '5432')}
        ${infoRow('Login', `<span class="mono">${escapeHtml(r.name)}</span>`)}
        ${infoRow('Password', `<span class="mono">${escapeHtml(r.password)}</span> <button type="button" class="btn btn-small" id="copy-reader-pw">Copy</button>`)}
        ${infoRow('Databases', escapeHtml(r.databases.join(', ')))}
        ${r.problems.length ? `<div class="form-error">Some schemas could not be shared (owned by another role): ${escapeHtml(r.problems.join('; '))}</div>` : ''}`,
        'Done', false, async () => { showMaintenance(); });
    document.getElementById('copy-reader-pw').onclick = () => copyPlain(r.password, 'Password');
}

document.getElementById('maintenance').addEventListener('click', (e) => {
    const log = e.target.closest('[data-log]');
    if (log) {
        const id = log.dataset.log;
        if (openLogs.has(id)) openLogs.delete(id); else openLogs.add(id);
        showMaintenance();
        return;
    }
    const drop = e.target.closest('[data-drop-reader]');
    if (drop) {
        const name = drop.dataset.dropReader;
        actionModal(`Remove the login ${name}`, `<p>Tools that use <b>${escapeHtml(name)}</b> can't connect any more.</p>`, 'Remove', true, async () => {
            await api('readers/drop', {name});
            showMaintenance();
        });
    }
});
onTab('maintenance', showMaintenance);
