// Chronicle dashboard - Backups: dumps of the databases in /share/chronicle (download, upload,
// restore as a new database, remove), and copying a database from another PostgreSQL server,
// such as the TimescaleDB app, into this one. Restores and copies run in the database helper
// (the superuser), one at a time; the Maintenance page has their logs.

const copyForm = {inspected: null};

async function showBackups() {
    const el = document.getElementById('backups');
    let data, databases, tasks;
    try {
        [data, databases, tasks] = await Promise.all([api('backups'), api('databases'), api('tasks')]);
    } catch (e) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
        return;
    }
    const names = databases.databases.filter(d => d.allow && d.name !== 'postgres').map(d => d.name);
    const recent = tasks.tasks.filter(t => ['dump', 'restore', 'copy', 'use', 'drop'].includes(t.kind)).slice(0, 5);
    const busy = tasks.tasks.some(t => t.state === 'running');
    el.innerHTML = `
        ${data.available ? '' : '<div class="setup-hint">The folder /share/chronicle is missing: the app needs access to Home Assistant\'s share folder for backups.</div>'}
        <div class="stats-grid">
            <div class="panel"><div class="panel-body">
                <div class="section-title">Back up a database</div>
                <p class="panel-sub">A dump of one database in <code>/share/chronicle</code>, which Samba and the File editor can reach too. Home Assistant's own backups of this app hold every database already; these are for keeping a copy elsewhere, or for restoring one database under another name.</p>
                <div class="form-row"><span>Database</span><select class="select" id="bk-database">${names.map(n => `<option${n === databases.chronicle ? ' selected' : ''}>${escapeHtml(n)}</option>`).join('')}</select></div>
                <div class="detail-actions">
                    <button type="button" class="btn btn-primary" onclick="backupDatabase(document.getElementById('bk-database').value)"${data.available && !busy ? '' : ' disabled'}>Back up now</button>
                    <label class="btn"${data.available ? '' : ' hidden'}>Upload a dump…<input type="file" id="bk-upload" accept=".dump" hidden></label>
                </div>
                ${data.available ? `<p class="panel-sub">${escapeHtml(fmtBytes(data.free))} free in /share</p>` : ''}
            </div></div>
            <div class="panel"><div class="panel-body">
                <div class="section-title">Copy from another server</div>
                <p class="panel-sub">Copies a database from another PostgreSQL server, such as the TimescaleDB app, into a new database here. The other server is only read. Then <b>Use for Chronicle</b> makes the copy the one the integration records into.</p>
                <div class="detail-actions"><button type="button" class="btn btn-primary" onclick="openCopy()"${busy ? ' disabled' : ''}>Copy a database…</button></div>
            </div></div>
        </div>
        <div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Backup files</span><span class="hint">${escapeHtml(data.folder || '')}</span></div>
            <div class="table-wrap">${backupFiles(data.files, busy)}</div>
        </div></div>
        ${recent.length ? `<div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Recent</span><span class="hint">all tasks and their logs are on the Maintenance page</span></div>
            ${recent.map(taskRow).join('')}</div></div>` : ''}`;
    const upload = document.getElementById('bk-upload');
    if (upload) upload.addEventListener('change', () => uploadDump(upload.files[0]));
    if (busy) setTimeout(() => { if (activeTab === 'backups') showBackups(); }, 2500);
}

function backupFiles(files, busy) {
    if (!files.length) return '<div class="empty-state">No backup files yet</div>';
    return `<table class="data"><thead><tr><th>File</th><th>Database</th><th class="n">Size</th><th>Made</th><th>TimescaleDB</th><th></th></tr></thead><tbody>
        ${files.map(f => `<tr>
            <td class="mono">${escapeHtml(f.name)}${f.ours ? '' : ' <span class="badge warn" title="Uploaded, or put in /share/chronicle by something else: a restore asks whether you trust it">not made here</span>'}</td><td>${escapeHtml(f.database || '–')}</td>
            <td class="n">${escapeHtml(fmtBytes(f.size))}</td><td>${escapeHtml(fmtDateTime(f.modified))}</td><td>${escapeHtml(f.timescaledb || '–')}</td>
            <td class="nowrap">
                <a class="btn btn-small" href="${basePath}api/backups/file?name=${encodeURIComponent(f.name)}" download>Download</a>
                <button type="button" class="btn btn-small" data-restore="${escapeHtml(f.name)}" data-ours="${f.ours ? 1 : 0}"${busy ? ' disabled' : ''}>Restore…</button>
                <button type="button" class="btn btn-small btn-danger" data-delete="${escapeHtml(f.name)}">Remove</button>
            </td></tr>`).join('')}</tbody></table>`;
}

async function uploadDump(file) {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    toast(`Uploading ${file.name}…`);
    try {
        const response = await fetch(basePath + 'api/backups/upload', {method: 'POST', headers: {'X-Chronicle': '1'}, body: form});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Upload failed (${response.status})`);
        toast(`${data.name} uploaded (${fmtBytes(data.size)})`, 'success');
    } catch (e) {
        toast(e.message, 'error');
    }
    showBackups();
}

function suggestName(base) {
    const taken = new Set((dbs.list || []).map(d => d.name));
    const stem = (base || 'restored').replace(/[^A-Za-z0-9_]/g, '_').replace(/^[^A-Za-z_]/, 'db_$&').slice(0, 40);
    for (let i = 0; i < 100; i++) {
        const name = i ? `${stem}_${i + 1}` : stem;
        if (!taken.has(name)) return name;
    }
    return `${stem}_${Date.now() % 100000}`;
}

async function restoreDump(name, ours) {
    await loadDatabaseList();
    const base = name.replace(/-\d{8}-\d{6}\.dump$|\.dump$/, '');
    actionModal(`Restore ${name}`, `
        <p>Restores the dump into a <b>new</b> database; nothing that exists is changed. To make it the Chronicle database afterwards, use <b>Use for Chronicle</b> on its page under Databases.</p>
        <label class="form-row"><span>New database</span><input class="input" id="rs-name" value="${escapeHtml(suggestName(base + '_restored'))}" autocomplete="off" spellcheck="false"></label>
        ${ours ? '<p class="panel-sub">Made by this app: it is checked to be unchanged before it is restored.</p>' : `
        <div class="form-error">This file was not made by this app (uploaded, or put in /share/chronicle by something else). A dump can hold any SQL, and a restore runs it with full rights over the database server.</div>
        <label class="action-check"><input type="checkbox" id="rs-trust"> I know where this file comes from and trust it</label>`}`,
        'Restore', !ours, async () => {
            const trust = !ours && document.getElementById('rs-trust').checked;
            if (!ours && !trust) throw new Error('Tick that you trust the file, or cancel');
            const task = await api('backups/restore', {file: name, database: document.getElementById('rs-name').value.trim(), trust});
            toast('Restoring (see Maintenance for the log)');
            watchTask(task.id, () => { dbs.trees = {}; loadDatabaseList(); });
            showBackups();
        });
}

// ---------------------------------------------------------------------
// Copy from another server: connect and look first, then copy
// ---------------------------------------------------------------------
function openCopy() {
    copyForm.inspected = null;
    const root = document.getElementById('modal-root');
    root.innerHTML = `<div class="modal-overlay open" id="copy-modal"><div class="modal wide" role="dialog" aria-modal="true" aria-labelledby="copy-title">
        <div class="modal-head"><span class="modal-title" id="copy-title">Copy a database from another server</span>
            <button type="button" class="modal-close" aria-label="Close" onclick="closeActionModal()">×</button></div>
        <div class="modal-body">
            <div class="detail-actions" style="margin-bottom:10px"><button type="button" class="btn btn-small" onclick="presetTimescaleApp()">From the TimescaleDB app</button></div>
            <label class="form-row"><span>Host</span><input class="input" id="cp-host" autocomplete="off" spellcheck="false"></label>
            <label class="form-row"><span>Port</span><input class="input" id="cp-port" type="number" value="5432"></label>
            <label class="form-row"><span>User</span><input class="input" id="cp-user" value="postgres" autocomplete="off" spellcheck="false"></label>
            <label class="form-row"><span>Password</span><input class="input" id="cp-password" type="password" autocomplete="off"></label>
            <label class="form-row"><span>Database</span><input class="input" id="cp-database" value="homeassistant" autocomplete="off" spellcheck="false"></label>
            <div id="cp-result"></div>
            <div class="form-error" id="action-error" hidden></div>
        </div>
        <div class="modal-foot"><button type="button" class="btn" onclick="closeActionModal()">Cancel</button>
            <button type="button" class="btn" id="cp-check" onclick="inspectCopy()">Check</button>
            <button type="button" class="btn btn-primary" id="cp-go" onclick="startCopy()" disabled>Copy</button></div>
    </div></div>`;
    ['cp-host', 'cp-port', 'cp-user', 'cp-password', 'cp-database'].forEach(id =>
        document.getElementById(id).addEventListener('input', () => { copyForm.inspected = null; document.getElementById('cp-go').disabled = true; }));
}

function presetTimescaleApp() {
    document.getElementById('cp-host').value = '1b41fb55-timescaledb';
    document.getElementById('cp-port').value = '5432';
    document.getElementById('cp-user').value = 'postgres';
    document.getElementById('cp-database').value = 'homeassistant';
    document.getElementById('cp-password').focus();
}

function copySource() {
    return {
        host: document.getElementById('cp-host').value.trim(),
        port: Number(document.getElementById('cp-port').value) || 5432,
        username: document.getElementById('cp-user').value.trim(),
        password: document.getElementById('cp-password').value,
        database: document.getElementById('cp-database').value.trim(),
    };
}

async function inspectCopy() {
    const error = document.getElementById('action-error');
    const button = document.getElementById('cp-check');
    error.hidden = true;
    button.disabled = true;
    button.textContent = 'Checking…';
    try {
        const info = await api('backups/inspect', copySource());
        await loadDatabaseList();
        copyForm.inspected = info;
        const others = info.tables;
        document.getElementById('cp-result').innerHTML = `
            <div class="copy-summary">
                ${infoRow('PostgreSQL', escapeHtml(info.postgres))}
                ${infoRow('TimescaleDB', info.timescaledb ? (info.restorable ? escapeHtml(info.timescaledb) : stateLabel('bad', `${info.timescaledb}: this app restores ${info.available.join(', ')}; update it there first`)) : 'not installed')}
                ${infoRow('Size', escapeHtml(fmtBytes(info.size)))}
                ${infoRow('Chronicle history', info.chronicle ? stateLabel('ok', 'yes, schema chronicle') : 'no')}
                ${info.extensions.length ? infoRow('Extensions', escapeHtml(info.extensions.join(', '))) : ''}
            </div>
            ${others.length ? `<div class="section-title" style="margin-top:12px">Other tables</div>
                <p class="panel-sub">Tick what to leave out: it is copied and then removed again, so the new database only keeps what you need.</p>
                <div class="copy-tables">${others.map(t => `<label class="action-check"><input type="checkbox" class="cp-drop" value="${escapeHtml(t.schema + '.' + t.name)}">
                    <span class="mono">${escapeHtml(t.schema)}.${escapeHtml(t.name)}</span> <span class="muted">${escapeHtml(fmtBytes(t.size))} · ≈ ${escapeHtml(fmtCount(t.rows))} rows</span></label>`).join('')}</div>` : ''}
            <label class="form-row" style="margin-top:12px"><span>New database here</span><input class="input" id="cp-target" value="${escapeHtml(suggestName(info.chronicle ? 'homeassistant_copy' : copySource().database + '_copy'))}" autocomplete="off" spellcheck="false"></label>
            ${info.chronicle ? '<p class="panel-sub">The Chronicle integration keeps recording into its current database until you choose <b>Use for Chronicle</b> after the copy. States recorded on the other server after the copy started are not in the copy.</p>' : ''}`;
        document.getElementById('cp-go').disabled = !info.restorable;
    } catch (e) {
        error.textContent = e.message;
        error.hidden = false;
    } finally {
        button.disabled = false;
        button.textContent = 'Check';
    }
}

async function startCopy() {
    if (!copyForm.inspected) return;
    const error = document.getElementById('action-error');
    const go = document.getElementById('cp-go');
    go.disabled = true;
    try {
        const target = document.getElementById('cp-target').value.trim();
        const task = await api('backups/copy', {
            ...copySource(), target,
            drop_tables: [...document.querySelectorAll('.cp-drop:checked')].map(c => c.value),
        });
        const chronicle = copyForm.inspected.chronicle;
        closeActionModal();
        toast('Copying (see Maintenance for the log)');
        showBackups();
        watchTask(task.id, (t) => {
            dbs.trees = {};
            loadDatabaseList();
            if (t.state === 'done' && chronicle) offerUse(t.result);
        });
    } catch (e) {
        error.textContent = e.message;
        error.hidden = false;
        go.disabled = false;
    }
}

function offerUse(result) {
    actionModal('Copy done', `
        <p>The database <b>${escapeHtml(result.database)}</b> holds the copy: ${result.rows === null ? '' : `${escapeHtml(num(result.rows))} rows of Chronicle history, `}${escapeHtml(fmtBytes(result.size))}.</p>
        <p>Make it the database the Chronicle integration records into? The current one is kept under a new name.</p>`,
        'Use for Chronicle', false, async () => {
            const task = await api('databases/use', {database: result.database});
            watchTask(task.id, () => { dbs.trees = {}; loadDatabaseList(); loadStatus(); });
        });
}

document.getElementById('backups').addEventListener('click', (e) => {
    const restore = e.target.closest('[data-restore]');
    const remove = e.target.closest('[data-delete]');
    const log = e.target.closest('[data-log]');
    if (restore) restoreDump(restore.dataset.restore, restore.dataset.ours === '1');
    else if (remove) {
        const name = remove.dataset.delete;
        actionModal(`Remove ${name}`, '<p>The file is removed from /share/chronicle.</p>', 'Remove', true, async () => {
            await api('backups/delete', {name});
            showBackups();
        });
    } else if (log) {
        const id = log.dataset.log;
        if (openLogs.has(id)) openLogs.delete(id); else openLogs.add(id);
        showBackups();
    }
});
onTab('backups', showBackups);
