// Proxmox dashboard - the Proxmox Backup Servers: their health, datastores, jobs and tasks.
// Shown as a section of the Backups page; its alerts also go to the Overview's health checks.

const pbsServers = () => (app.status && app.status.pbs) || [];
// Backup servers the cluster uses (from its storage settings), whether or not this app has a token for them
const pbsStorages = () => (app.conf && app.conf.pbs_storages) || [];
const pbsVisible = () => pbsServers().length > 0 || pbsStorages().length > 0;

const PBS_TASKS = {
    backup: 'Backup', reader: 'Restore / read', garbage_collection: 'Garbage collection', prune: 'Prune', prunejob: 'Prune job',
    verify: 'Verify', verificationjob: 'Verify job', verify_group: 'Verify group', verify_snapshot: 'Verify snapshot', sync: 'Sync',
    syncjob: 'Sync job', aptupdate: 'Update package lists', logrotate: 'Log rotation', 'tape-backup-job': 'Tape backup',
    'tape-backup': 'Tape backup', 'tape-restore': 'Tape restore', 'zfscreate': 'ZFS create', 'dircreate': 'Directory create',
};
const PBS_JOB_KINDS = {gc: 'Garbage collection', verify: 'Verify', prune: 'Prune', sync: 'Sync'};
const pbsTaskName = t => PBS_TASKS[t.worker_type] || t.worker_type;
const pbsTaskOk = t => t.status === 'OK' || /^WARNINGS/.test(t.status || '');
const pbsJobOk = j => !j['last-run-state'] || j['last-run-state'] === 'ok' || j['last-run-state'] === 'OK';
const pbsWorker = id => String(id || '').replace(/\\x3a/g, ':');
let pbsPick = stored('pbs_pick', '');
let pbsTaskView = stored('pbs_tasks', 'jobs');

// The PVE storages that point at this server's datastores
function pbsStorageFor(server, store) {
    let host = '';
    try { host = new URL(server.host).hostname; } catch (e) { /* not a URL */ }
    return pbsStorages().filter(s => s.server === host && (!store || s.datastore === store));
}

// What needs a look on the backup servers
function pbsAlerts() {
    const out = [];
    const now = Date.now() / 1000;
    pbsServers().forEach(s => {
        const who = `<b>${escapeHtml(s.name)}</b>`;
        if (!s.connected) { out.push({kind: 'bad', text: `Backup server ${who} can't be reached: ${escapeHtml(s.error || 'no answer')}`}); return; }
        (s.datastores || []).forEach(d => {
            const ratio = d.total ? d.used / d.total : 0;
            const days = d.estimated_full ? (d.estimated_full - now) / 86400 : null;
            if (d.error) out.push({kind: 'bad', text: `${who}: datastore ${escapeHtml(d.store)}: ${escapeHtml(d.error)}`});
            if (ratio >= 0.85) out.push({kind: ratio >= 0.95 ? 'bad' : 'warn', text: `${who}: datastore ${escapeHtml(d.store)} is ${plainPct(ratio, 0)} full`});
            else if (days !== null && days > 0 && days < 90) out.push({kind: days < 30 ? 'bad' : 'warn', text: `${who}: datastore ${escapeHtml(d.store)} is full in about ${num(days)} days at its current pace`});
            if ((d.gc_status || {})['still-bad']) out.push({kind: 'bad', text: `${who}: datastore ${escapeHtml(d.store)} has ${num(d.gc_status['still-bad'])} bad chunks`});
            if (d.maintenance) out.push({kind: 'info', text: `${who}: datastore ${escapeHtml(d.store)} is in maintenance mode (${escapeHtml(d.maintenance)})`});
        });
        Object.entries(s.jobs || {}).forEach(([kind, jobs]) => jobs.forEach(j => {
            if (!pbsJobOk(j)) out.push({kind: 'bad', text: `${who}: ${escapeHtml(PBS_JOB_KINDS[kind])} ${escapeHtml(j.id || j.store)} failed${j['last-run-endtime'] ? ` ${escapeHtml(fmtAgo(j['last-run-endtime']))}` : ''}: <span class="muted">${escapeHtml(j['last-run-state'])}</span>`, upid: j['last-run-upid'], server: s.name});
        }));
        const failed = (s.tasks || []).filter(t => t.endtime && !pbsTaskOk(t) && t.starttime > now - 86400);
        if (failed.length) out.push({kind: 'warn', text: `${who}: ${failed.length} task${failed.length > 1 ? 's' : ''} failed in the last 24 hours: ${[...new Set(failed.map(pbsTaskName))].map(escapeHtml).join(', ')}`});
        if (Array.isArray(s.updates) && s.updates.length) out.push({kind: 'info', text: `${who}: ${s.updates.length} update${s.updates.length > 1 ? 's' : ''} available`});
    });
    const configured = new Set(pbsServers().map(s => { try { return new URL(s.host).hostname; } catch (e) { return ''; } }));
    [...new Set(pbsStorages().filter(st => !Number(st.disable) && !configured.has(st.server)).map(st => st.server))].forEach(server => {
        out.push({kind: 'info', text: `The cluster backs up to the backup server <b>${escapeHtml(server)}</b>: add it under <span class="mono">pbs</span> in the app's configuration to see it here`});
    });
    return out;
}

function renderPbsSection(el) {
    const servers = pbsServers();
    if (!servers.length) {
        const st = pbsStorages();
        el.innerHTML = panel('Backup server', '', st.length
            ? `<p class="panel-sub" style="margin-bottom:10px">The cluster backs up to these Proxmox Backup Servers. Add one under <span class="mono">pbs</span> in the app's configuration, with its own API token, to see its datastores, jobs and tasks here. DOCS.md tells how to make the token.</p>
               ${table([{label: 'Storage'}, {label: 'Server'}, {label: 'Datastore'}, {label: 'Fingerprint', title: 'copy it into fingerprint to pin the server\'s certificate'}],
                   st.map(s => `<tr>${td(`<span class="name">${escapeHtml(s.storage)}</span>`)}${td(`<span class="mono">${escapeHtml(s.server)}${s.port ? ':' + escapeHtml(s.port) : ''}</span>`)}${td(escapeHtml(s.datastore))}${td(escapeHtml(s.fingerprint || '–'), 'mono wrap-cell muted')}</tr>`))}`
            : '<div class="empty-state">No Proxmox Backup Server configured, and the cluster backs up to none.</div>');
        delete el.dataset.server;
        return;
    }
    if (!servers.some(s => s.name === pbsPick)) pbsPick = servers[0].name;
    const s = servers.find(x => x.name === pbsPick);
    if (el.dataset.server !== pbsPick) {
        el.dataset.server = pbsPick;
        el.innerHTML = `
            ${servers.length > 1 ? `<div class="filters">${chipsHtml('pbs-pick', servers.map(x => ({key: x.name, label: x.name})), pbsPick, 'Backup server')}</div>` : ''}
            <div id="pbs-head" class="page-head"></div>
            <div id="pbs-notes"></div>
            <div id="pbs-tiles" class="tiles kpi-tiles"></div>
            ${panel('Datastores', 'as the backup server sees them', '<div id="pbs-stores" class="table-wrap"></div>')}
            <div class="dash-grid wide-left">
                ${panel('Jobs', 'click one for its last run\'s log', '<div id="pbs-jobs" class="table-wrap"></div>')}
                ${panel('Datastore usage', 'last month', '<div id="pbs-usage" class="chart"></div><div class="legend" id="pbs-usage-legend"></div>')}
            </div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Tasks</span><span class="filters-inline"><span class="hint" id="pbs-tasks-hint"></span> ${chipsHtml('pbs-task-view', [{key: 'jobs', label: 'Jobs and problems'}, {key: 'all', label: 'All'}], pbsTaskView, 'Tasks')}</span></div>
                <div id="pbs-tasks" class="table-wrap"></div>
            </div></div>`;
        bindChips('pbs-pick', (key) => { pbsPick = key; store('pbs_pick', key); renderPbsSection(el); });
        bindChips('pbs-task-view', (key) => { pbsTaskView = key; store('pbs_tasks', key); renderPbsTasks(pbsServers().find(x => x.name === pbsPick)); });
    }
    renderPbsHead(s);
    if (!s.connected && !s.read_at) {
        document.getElementById('pbs-notes').innerHTML = setupHint(`Can't reach ${escapeHtml(s.name)}: ${escapeHtml(s.error || 'connecting…')}`, s.error ? 'bad' : 'info');
        ['pbs-tiles', 'pbs-stores', 'pbs-jobs', 'pbs-usage', 'pbs-tasks'].forEach(id => { document.getElementById(id).innerHTML = ''; });
        return;
    }
    const notes = [];
    if (!s.connected) notes.push(setupHint(`Can't reach ${escapeHtml(s.name)} now: ${escapeHtml(s.error)} · showing what was read ${escapeHtml(fmtAgo(s.read_at))}`, 'bad'));
    const refused = Object.entries(s.notes || {});
    if (refused.length) notes.push(`<div class="note warn">The token can't read everything: ${refused.map(([k, v]) => `<span class="mono">${escapeHtml(k)}</span> ${escapeHtml(v)}`).join(' · ')}</div>`);
    document.getElementById('pbs-notes').innerHTML = notes.join('');
    renderPbsTiles(s);
    renderPbsStores(s);
    renderPbsJobs(s);
    renderPbsUsage(s);
    renderPbsTasks(s);
}

function renderPbsHead(s) {
    const v = s.version || {};
    const sub = s.subscription || {};
    document.getElementById('pbs-head').innerHTML = `
        <h1>${escapeHtml(s.name)}</h1>
        <div class="badges">${s.connected ? badge('ONLINE', 'ok') : badge('UNREACHABLE', 'bad')}
            ${v.version ? badge(`PBS ${v.version}`, 'mode') : ''}
            ${sub.status === 'active' ? badge('SUBSCRIBED', 'ok') : ''}
            ${s.pinned ? badge('CERTIFICATE PINNED', 'mode', 'the fingerprint in the configuration is checked') : ''}</div>
        <span class="meta"><span class="mono">${escapeHtml(s.host)}</span>${(s.status || {}).uptime ? ` · up ${escapeHtml(fmtUptime(s.status.uptime))}` : ''} · read ${escapeHtml(fmtAgo(s.read_at))}</span>`;
}

function renderPbsTiles(s) {
    const st = s.status || {};
    const mem = st.memory || {}, root = st.root || {};
    const now = Date.now() / 1000;
    const tasks = (s.tasks || []).filter(t => t.starttime > now - 86400);
    const failed = tasks.filter(t => t.endtime && !pbsTaskOk(t));
    const jobs = Object.values(s.jobs || {}).flat();
    const badJobs = jobs.filter(j => !pbsJobOk(j));
    const stores = s.datastores || [];
    const used = stores.reduce((a, d) => a + (d.used || 0), 0), total = stores.reduce((a, d) => a + (d.total || 0), 0);
    document.getElementById('pbs-tiles').innerHTML = [
        tile('Datastores', `${stores.length}`, `${fmtBytes(used)} of ${fmtBytes(total)}` + meter(total ? used / total : null, false)),
        tile('Jobs', badJobs.length ? `${badJobs.length}<small> failed</small>` : `${jobs.length}<small> OK</small>`, `${jobs.length} jobs`, badJobs.length ? 'bad' : ''),
        tile('Tasks · 24 h', `${tasks.length - failed.length}<small> / ${tasks.length}</small>`, failed.length ? `<span class="neg">${failed.length} failed</span>` : 'all OK', failed.length ? 'warn' : ''),
        tile('CPU', escapeHtml(plainPct(st.cpu)), `IO wait ${plainPct(st.wait, 1)}` + meter(st.cpu, false)),
        tile('Memory', bytesHtml(mem.used), `of ${fmtBytes(mem.total)}` + meter(mem.total ? mem.used / mem.total : null, false)),
        tile('Root disk', bytesHtml(root.used), `of ${fmtBytes(root.total)}` + meter(root.total ? root.used / root.total : null, false)),
        tile('Updates', Array.isArray(s.updates) ? num(s.updates.length) : '–', Array.isArray(s.updates) ? (s.updates.length ? s.updates.slice(0, 2).map(u => escapeHtml(u.Package)).join(', ') : 'up to date') : 'not readable', Array.isArray(s.updates) && s.updates.length ? 'warn' : ''),
    ].join('');
}

function pbsCounts(c) {
    const parts = ['vm', 'ct', 'host'].filter(k => c && c[k]).map(k => `${num(c[k].snapshots)} ${k === 'vm' ? 'VM' : k === 'ct' ? 'CT' : 'host'}`);
    const groups = ['vm', 'ct', 'host', 'other'].reduce((a, k) => a + ((c && c[k] && c[k].groups) || 0), 0);
    return parts.length ? `${parts.join(' · ')}<span class="sub">${num(groups)} groups</span>` : '<span class="muted">–</span>';
}

function renderPbsStores(s) {
    const now = Date.now() / 1000;
    const rows = (s.datastores || []).map(d => {
        const ratio = d.total ? d.used / d.total : null;
        const days = d.estimated_full ? (d.estimated_full - now) / 86400 : null;
        const g = d.gc_status || {};
        const job = d.gc_job || {};
        const pve = pbsStorageFor(s, d.store);
        return `<tr>
            ${td(`<span class="name">${escapeHtml(d.store)}</span><span class="sub">${escapeHtml(d.comment || '')}${pve.length ? `${d.comment ? ' · ' : ''}PVE storage ${pve.map(p => escapeHtml(p.storage)).join(', ')}` : ''}</span>`)}
            ${td(d.error ? stateLabel('bad', d.error) : meter(ratio, `${fmtBytes(d.used)} / ${fmtBytes(d.total)}`, {cls: 'stack', warn: 0.8, bad: 0.9}))}
            ${td(escapeHtml(fmtBytes(d.avail)), 'n')}
            ${td(days === null ? '<span class="muted">–</span>' : days <= 0 ? '<span class="neg">now</span>' : `<span class="${days < 30 ? 'neg' : days < 90 ? 'warn-text' : ''}" title="${attr(new Date(d.estimated_full * 1000).toLocaleDateString())}">in ${escapeHtml(days > 730 ? `${num(days / 365, 1)} years` : days > 60 ? `${num(days / 30)} months` : `${num(days)} days`)}</span>`, 'n')}
            ${td(d.dedup ? `${num(d.dedup, 2)}×<span class="sub">${escapeHtml(fmtBytes(g['index-data-bytes']))} in backups</span>` : '<span class="muted">–</span>', 'n')}
            ${td(pbsCounts(d.counts))}
            ${td(job['last-run-endtime'] ? `${pbsJobOk(job) ? stateLabel('ok', fmtAgo(job['last-run-endtime'])) : `<span class="state bad" title="${attr(job['last-run-state'])}">failed</span>`}<span class="sub">freed ${escapeHtml(fmtBytes(g['removed-bytes'] || 0))} · pending ${escapeHtml(fmtBytes(g['pending-bytes'] || 0))}</span>` : '<span class="muted">never</span>')}
            ${td(g['still-bad'] ? stateLabel('bad', `${num(g['still-bad'])} bad`) : '<span class="muted">none</span>')}
            ${td(d.maintenance ? badge(d.maintenance, 'warn') : '')}
        </tr>`;
    });
    document.getElementById('pbs-stores').innerHTML = table([
        {label: 'Datastore'}, {label: 'Used'}, {label: 'Free', n: true}, {label: 'Full', n: true, title: 'the backup server\'s own estimate'},
        {label: 'Dedup', n: true, title: 'what the backups would take on their own ÷ what their chunks take on disk'}, {label: 'Snapshots'},
        {label: 'Garbage collection'}, {label: 'Bad chunks'}, {label: ''},
    ], rows, {empty: 'No datastores'});
}

function renderPbsJobs(s) {
    const rows = Object.entries(s.jobs || {}).flatMap(([kind, jobs]) => jobs.map(j => {
        const ok = pbsJobOk(j);
        const what = kind === 'sync' ? `${escapeHtml(j.remote ? `${j.remote}:` : '')}${escapeHtml(j['remote-store'] || '')} → ${escapeHtml(j.store)}`
            : kind === 'prune' ? `${escapeHtml(j.store)}<span class="sub mono">${['keep-last', 'keep-hourly', 'keep-daily', 'keep-weekly', 'keep-monthly', 'keep-yearly'].filter(k => j[k]).map(k => `${k.slice(5)}=${j[k]}`).join(' ')}</span>`
            : escapeHtml(j.store || '');
        const click = j['last-run-upid'] ? ` onclick="showTask('${attr(s.name)}', '${attr(j['last-run-upid'])}', '${attr(PBS_JOB_KINDS[kind])} ${attr(j.id || j.store)}', '${attr(s.name)}')"` : '';
        return `<tr${click}>
            ${td(badge(PBS_JOB_KINDS[kind], 'mode'))}
            ${td(`<span class="name mono">${escapeHtml(j.id || j.store)}</span>`)}
            ${td(what)}
            ${td(escapeHtml(j.schedule || '–'), 'mono')}
            ${td(!j['last-run-endtime'] ? '<span class="muted">never ran</span>' : ok ? stateLabel('ok', fmtAgo(j['last-run-endtime'])) : `<span class="state bad" title="${attr(j['last-run-state'])}">failed ${escapeHtml(fmtAgo(j['last-run-endtime']))}</span><span class="sub">${escapeHtml(j['last-run-state'])}</span>`, 'wrap-cell')}
            ${td(missing(j.duration) ? '–' : escapeHtml(fmtDuration(j.duration)), 'n')}
            ${td(j['next-run'] ? escapeHtml(fmtAgo(j['next-run'])) : '–', 'n')}
        </tr>`;
    }));
    document.getElementById('pbs-jobs').innerHTML = table([{label: 'Kind'}, {label: 'Job'}, {label: 'What'}, {label: 'Schedule'}, {label: 'Last run'}, {label: 'Took', n: true}, {label: 'Next', n: true}],
        rows, {cls: 'hover', empty: 'No jobs'});
}

function renderPbsUsage(s) {
    const stores = (s.datastores || []).filter(d => d.history && d.history.length && d.history_start);
    if (!stores.length) { emptyChart(document.getElementById('pbs-usage'), 'No usage history'); return; }
    const first = stores[0];
    const x = first.history.map((_, i) => first.history_start + i * (first.history_delta || 86400));
    lineChart(document.getElementById('pbs-usage'), {
        x, xTicks: timeAxisTicks(x[0], x[x.length - 1], 4), height: 170, zero: true, max: 1,
        series: stores.map((d, i) => ({name: d.store, color: color(SERIES[i % SERIES.length]), values: d.history.map(v => missing(v) ? null : v), area: i === 0})),
        hlines: [{at: 0.9, color: 'var(--danger-color)', label: '90%'}],
        formatX: fmtDay, formatY: v => plainPct(v), formatTick: v => `${Math.round(v * 100)}%`,
    });
    document.getElementById('pbs-usage-legend').innerHTML = legendHtml(stores.map((d, i) => ({name: d.store, color: color(SERIES[i % SERIES.length])})));
}

function renderPbsTasks(s) {
    // Backups and restores come from the cluster, one per guest; the rest is the server's own work
    const all = s.tasks || [];
    const tasks = (pbsTaskView === 'all' ? all : all.filter(t => !['backup', 'reader'].includes(t.worker_type) || (t.endtime && !pbsTaskOk(t)))).slice(0, 40);
    const failed = all.filter(t => t.endtime && !pbsTaskOk(t)).length;
    document.getElementById('pbs-tasks-hint').textContent = `${failed ? `${failed} failed · ` : ''}click one for its log`;
    document.getElementById('pbs-tasks').innerHTML = table([{label: 'Started'}, {label: 'Task'}, {label: 'What'}, {label: 'User'}, {label: 'Took', n: true}, {label: 'Status'}],
        tasks.map(t => `<tr class="${t.endtime && !pbsTaskOk(t) ? 'failed' : ''}" onclick="showTask('${attr(s.name)}', '${attr(t.upid)}', '${attr(pbsTaskName(t))}', '${attr(s.name)}')">
            ${td(escapeHtml(fmtDateTime(t.starttime)), 'mono')}${td(escapeHtml(pbsTaskName(t)))}${td(escapeHtml(pbsWorker(t.worker_id)), 'mono')}
            ${td(escapeHtml(t.user || ''), 'muted')}${td(t.endtime ? escapeHtml(fmtDuration(t.endtime - t.starttime)) : '–', 'n')}
            ${td(!t.endtime ? stateLabel('warn', 'running') : pbsTaskOk(t) ? stateLabel('ok', t.status) : `<span class="state bad" title="${attr(t.status)}">${escapeHtml(String(t.status).replace(/^TASK ERROR: /, '').slice(0, 50))}</span>`)}
        </tr>`), {cls: 'hover', empty: 'No tasks'});
}
