// Proxmox dashboard - Storage: usage and when it fills up, backups, backup and replication jobs.

let stSection = stored('st_section', 'storage');
let stSelected = stored('st_selected', '');
let stTimeframe = stored('st_tf', 'month');
let stForecasts = {};
let stForecastAt = 0;
let stHistory = null;
let stHistoryKey = '';
let stBkFilter = '';
const ST_SECTIONS = [{key: 'storage', label: 'Storage'}, {key: 'backups', label: 'Backups'}, {key: 'jobs', label: 'Jobs'}, {key: 'replication', label: 'Replication'}];

function showStorage(sub) {
    if (ST_SECTIONS.some(s => s.key === sub)) stSection = sub;
    setSub(stSection);
    delete document.getElementById('tab-storage').dataset.built;
    renderStorage();
    if (stSection === 'backups') loadGsBackups(false);
    loadForecasts(false);
}

function renderStorage() {
    const el = document.getElementById('tab-storage');
    if (!app.status || !app.status.connected) {
        el.innerHTML = '<div class="empty-state">Waiting for the cluster…</div>';
        delete el.dataset.built;
        return;
    }
    if (!el.dataset.built) {
        el.dataset.built = '1';
        el.innerHTML = `
            <div class="filters">${chipsHtml('st-sections', ST_SECTIONS, stSection, 'Section')}</div>
            <div class="section" data-section="storage">
                <div id="st-tiles" class="tiles kpi-tiles"></div>
                ${panel('Storage', 'click a storage for its history', '<div id="st-table" class="table-wrap"></div>')}
                <div class="panel"><div class="panel-body">
                    <div class="chart-title"><span id="st-chart-title">History</span>${chipsHtml('st-tf', TIMEFRAMES.slice(1), stTimeframe, 'Period')}</div>
                    <div id="st-chart" class="chart"></div>
                    <p class="panel-sub" id="st-chart-note" style="margin-top:8px"></p>
                </div></div>
            </div>
            <div class="section" data-section="backups">
                <div id="bk-tiles" class="tiles kpi-tiles"></div>
                <div class="filters"><input class="input" id="bk-search" type="search" placeholder="Guest, ID or storage" aria-label="Search backups"></div>
                ${panel('Per guest', 'the latest backup of every guest', '<div id="bk-guests" class="table-wrap"></div>')}
            </div>
            <div class="section" data-section="jobs">
                ${panel('Backup jobs', 'Datacenter → Backup in Proxmox', '<div id="jb-backup" class="table-wrap"></div>')}
            </div>
            <div class="section" data-section="replication">
                ${panel('Replication', 'storage replication jobs (ZFS)', '<div id="jb-repl" class="table-wrap"></div>')}
            </div>`;
        bindChips('st-sections', (key) => {
            stSection = key; store('st_section', key); setSub(key); markStSection();
            if (key === 'backups') loadGsBackups(false);
            renderStorage();
        });
        bindChips('st-tf', (key) => { stTimeframe = key; store('st_tf', key); loadStHistory(true); });
        document.getElementById('bk-search').addEventListener('input', (e) => { stBkFilter = e.target.value.trim().toLowerCase(); renderBackups(); });
        markStSection();
    }
    if (stSection === 'storage') renderStorageList();
    else if (stSection === 'backups') renderBackups();
    else if (stSection === 'jobs') renderJobs();
    else renderReplication();
}

function markStSection() {
    document.querySelectorAll('#tab-storage .section').forEach(s => s.classList.toggle('active', s.dataset.section === stSection));
}

// ---------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------
function storageLabel(s) {
    return s.shared ? s.storage : `${s.storage} · ${s.node}`;
}

function renderStorageList() {
    const list = storages();
    if (!stSelected || !list.some(s => s.key === stSelected)) stSelected = (list.find(s => s.shared) || list[0] || {}).key || '';
    const avail = list.filter(s => s.status === 'available');
    const used = avail.reduce((a, s) => a + (s.disk || 0), 0), total = avail.reduce((a, s) => a + (s.maxdisk || 0), 0);
    const soon = Object.entries(stForecasts).filter(([, f]) => f && f.days !== null && f.days < 90);
    document.getElementById('st-tiles').innerHTML = [
        tile('Stores', `${avail.length}<small> / ${list.length}</small>`, `${list.filter(s => s.shared).length} shared`),
        tile('Used', bytesHtml(used), `of ${fmtBytes(total)}` + meter(total ? used / total : null, false)),
        tile('Free', bytesHtml(total - used), 'on all stores together'),
        tile('Filling up', soon.length ? `${soon.length}` : '0', soon.length ? `within 90 days: ${soon.map(([k]) => escapeHtml(k)).join(', ')}` : 'none within 90 days', soon.length ? 'warn' : ''),
    ].join('');
    const rows = list.map(s => {
        const ratio = s.maxdisk ? s.disk / s.maxdisk : null;
        const f = stForecasts[s.key];
        const forecast = !f ? '<span class="muted">…</span>' : f.days === null ? `<span class="muted">${escapeHtml(f.text)}</span>`
            : `<span class="${f.days < 30 ? 'neg' : f.days < 90 ? 'warn-text' : ''}">${escapeHtml(f.text)}</span>`;
        return `<tr class="${s.key === stSelected ? 'selected' : ''}" data-key="${attr(s.key)}">
            ${td(`<span class="name">${escapeHtml(s.storage)}</span><span class="sub">${s.shared ? `shared · ${s.nodes.length} nodes` : escapeHtml(s.node)}</span>`)}
            ${td(badge(s.plugintype || '?', 'mode'))}
            ${td(escapeHtml(String(s.content || '').split(',').join(', ')), 'wrap-cell muted')}
            ${td(s.status === 'available' ? meter(ratio, `${fmtBytes(s.disk)} / ${fmtBytes(s.maxdisk)}`, {cls: 'stack'}) : stateLabel('bad', s.status || '?'))}
            ${td(escapeHtml(fmtBytes((s.maxdisk || 0) - (s.disk || 0))), 'n')}
            ${td(f && !missing(f.perDay) ? `${f.perDay >= 0 ? '+' : '−'}${escapeHtml(fmtBytes(Math.abs(f.perDay)))}/day` : '<span class="muted">–</span>', 'n')}
            ${td(forecast, 'n')}
        </tr>`;
    });
    const el = document.getElementById('st-table');
    el.innerHTML = table([{label: 'Storage'}, {label: 'Type'}, {label: 'Content'}, {label: 'Used'}, {label: 'Free', n: true}, {label: 'Growth', n: true, title: 'over the last month'}, {label: 'Full', n: true, title: 'if it keeps growing like the last month'}],
        rows, {cls: 'hover'});
    el.onclick = (e) => {
        const tr = e.target.closest('tr[data-key]');
        if (!tr) return;
        stSelected = tr.dataset.key;
        store('st_selected', stSelected);
        renderStorageList();
        loadStHistory(true);
    };
    loadStHistory(false);
}

// A straight line through the month: how fast it grows and when it would be full
function forecast(rows, total) {
    const pts = rows.filter(r => !missing(r.used) && r.used > 0);
    if (pts.length < 10 || !total) return {days: null, perDay: null, text: 'too little history'};
    const n = pts.length;
    const mx = pts.reduce((a, r) => a + r.time, 0) / n, my = pts.reduce((a, r) => a + r.used, 0) / n;
    const sxy = pts.reduce((a, r) => a + (r.time - mx) * (r.used - my), 0), sxx = pts.reduce((a, r) => a + (r.time - mx) ** 2, 0);
    const slope = sxx ? sxy / sxx : 0;
    const perDay = slope * 86400;
    const now = pts[n - 1];
    if (slope <= 0 || Math.abs(perDay) < total * 1e-5) return {days: null, perDay, text: perDay < 0 ? 'shrinking' : 'steady'};
    const days = (total - now.used) / perDay;
    return {days, perDay, text: days > 3650 ? 'over 10 years' : days > 730 ? `in ${num(days / 365, 1)} years` : days > 60 ? `in ${num(days / 30, 0)} months` : `in ${num(Math.max(0, days), 0)} days`};
}

async function loadForecasts(force) {
    if (!force && Date.now() / 1000 - stForecastAt < 600 && Object.keys(stForecasts).length) return;
    stForecastAt = Date.now() / 1000;
    const list = storages().filter(s => s.status === 'available');
    await Promise.all(list.map(async (s) => {
        try {
            const data = await api(`rrd?node=${encodeURIComponent(s.node)}&storage=${encodeURIComponent(s.storage)}&timeframe=month`);
            stForecasts[s.key] = forecast(data.rows, s.maxdisk);
        } catch (e) {
            stForecasts[s.key] = {days: null, perDay: null, text: 'no history'};
        }
    }));
    if (activeTab === 'storage' && stSection === 'storage') renderStorageList();
}

async function loadStHistory(force) {
    const s = storages().find(x => x.key === stSelected);
    if (!s) return;
    const key = `${s.key}:${stTimeframe}`;
    document.getElementById('st-chart-title').textContent = `History of ${storageLabel(s)}`;
    if (!force && stHistoryKey === key && stHistory) { drawStHistory(s); return; }
    stHistoryKey = key;
    try {
        const data = await api(`rrd?node=${encodeURIComponent(s.node)}&storage=${encodeURIComponent(s.storage)}&timeframe=${stTimeframe}`);
        if (stHistoryKey !== key) return;
        stHistory = data.rows;
        drawStHistory(s);
    } catch (e) {
        emptyChart(document.getElementById('st-chart'), e.message);
    }
}

function drawStHistory(s) {
    const rows = (stHistory || []).filter(r => !missing(r.used));
    const x = rows.map(r => r.time);
    const f = stForecasts[s.key];
    lineChart(document.getElementById('st-chart'), {
        x, xTicks: x.length > 1 ? timeAxisTicks(x[0], x[x.length - 1], 5) : [], height: 190, zero: true,
        series: [{name: 'Used', color: color('series-1'), values: rows.map(r => r.used), area: true}, {name: 'Size', color: color('idle'), values: rows.map(r => r.total), dash: '4 4', width: 1.5}],
        formatX: timeFmt(stTimeframe), formatY: v => fmtBytes(v), formatTick: v => fmtBytes(v).replace(/\.\d /, ' '), empty: 'No history for this storage',
    });
    document.getElementById('st-chart-note').textContent = f && f.days !== null
        ? `At the pace of the last month (${fmtBytes(f.perDay)} a day) it is full ${f.text}.`
        : f && f.perDay !== null ? `Over the last month it is ${f.text}.` : '';
}

// ---------------------------------------------------------------------
// Backups
// ---------------------------------------------------------------------
function renderBackups() {
    const el = document.getElementById('bk-guests');
    if (gsBackups === null) { el.innerHTML = '<div class="empty-state">Reading the backups of every backup storage…</div>'; return; }
    const all = gsBackups;
    const byGuest = new Map();
    all.forEach(b => { const k = String(b.vmid); if (!byGuest.has(k)) byGuest.set(k, []); byGuest.get(k).push(b); });
    const gs = guests({noTemplates: true});
    const notBacked = new Set(((app.conf && app.conf.not_backed_up) || []).map(x => String(x.vmid)));
    const day = 86400, now = Date.now() / 1000;
    const failed = all.filter(b => b.verification && b.verification.state && b.verification.state !== 'ok');
    const total = all.reduce((a, b) => a + (b.size || 0), 0);
    const stale = gs.filter(g => { const l = byGuest.get(String(g.vmid)); return !l || now - Math.max(...l.map(b => b.ctime)) > 2 * day; });
    document.getElementById('bk-tiles').innerHTML = [
        tile('Backups', num(all.length), `${byGuest.size} guests · ${fmtBytes(total)}`),
        tile('Not in a job', num(notBacked.size), notBacked.size ? 'guests no backup job includes' : 'every guest is in a job', notBacked.size ? 'warn' : ''),
        tile('Older than 2 days', num(stale.length), 'or no backup at all', stale.length ? 'warn' : ''),
        tile('Failed verification', num(failed.length), failed.length ? 'backups to look at' : 'on PBS, where verified', failed.length ? 'bad' : ''),
    ].join('');
    const rows = gs.filter(g => !stBkFilter || `${g.vmid} ${guestName(g)} ${(byGuest.get(String(g.vmid)) || []).map(b => b.storage).join(' ')}`.toLowerCase().includes(stBkFilter))
        .map(g => {
            const list = (byGuest.get(String(g.vmid)) || []).sort((a, b) => b.ctime - a.ctime);
            const latest = list[0];
            const age = latest ? now - latest.ctime : null;
            const bad = list.filter(b => b.verification && b.verification.state && b.verification.state !== 'ok').length;
            const verified = list.filter(b => b.verification && b.verification.state === 'ok').length;
            return {age: age === null ? Infinity : age, html: `<tr onclick="openGuest(${g.vmid})">
                ${td(guestLabel(g))}
                ${td(latest ? `<span class="${age > 7 * day ? 'neg' : age > 2 * day ? 'warn-text' : ''}">${escapeHtml(fmtAgo(latest.ctime))}</span><span class="sub">${escapeHtml(fmtDateTime(latest.ctime))}</span>` : '<span class="neg">none</span>')}
                ${td(num(list.length), 'n')}
                ${td(list.length ? escapeHtml(fmtAgo(list[list.length - 1].ctime)) : '–', 'n')}
                ${td(latest ? escapeHtml(fmtBytes(latest.size)) : '–', 'n')}
                ${td([...new Set(list.map(b => b.storage))].map(escapeHtml).join(', ') || '–')}
                ${td(bad ? stateLabel('bad', `${bad} failed`) : verified ? stateLabel('ok', `${verified} OK`) : '<span class="muted">–</span>')}
                ${td(notBacked.has(String(g.vmid)) ? badge('NOT IN A JOB', 'bad') : list.some(b => b.protected) ? '🔒 protected' : '')}
            </tr>`};
        }).sort((a, b) => b.age - a.age).map(r => r.html);
    el.innerHTML = permissionNote('backups', 'Backups') + table([
        {label: 'Guest'}, {label: 'Latest'}, {label: 'Count', n: true}, {label: 'Oldest', n: true}, {label: 'Size', n: true, title: 'of the latest backup'},
        {label: 'Storage'}, {label: 'Verified'}, {label: ''},
    ], rows, {cls: 'hover', empty: 'No guest matches'});
}

// ---------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------
function renderJobs() {
    const el = document.getElementById('jb-backup');
    if (!app.conf) { el.innerHTML = '<div class="empty-state">Reading the configuration…</div>'; return; }
    const tasks = (app.status.tasks || []).filter(t => t.type === 'vzdump');
    const lastRun = tasks.filter(t => !t.id)[0] || tasks[0];
    const rows = (app.conf.backup_jobs || []).map(j => {
        const enabled = j.enabled === undefined || Number(j.enabled);
        const sel = j.all ? `all${j.exclude ? ` except ${j.exclude}` : ''}` : j.pool ? `pool ${j.pool}` : j.vmid || '';
        return `<tr class="${enabled ? '' : 'off'}">
            ${td(enabled ? stateLabel('ok', 'on') : stateLabel('', 'off'))}
            ${td(`<span class="mono">${escapeHtml(j.schedule || j.starttime || '')}</span>${j.dow ? `<span class="sub">${escapeHtml(j.dow)}</span>` : ''}`)}
            ${td(escapeHtml(j.storage || ''))}
            ${td(escapeHtml(sel), 'wrap-cell')}
            ${td(escapeHtml(j.node || 'all nodes'))}
            ${td(escapeHtml(j.mode || 'snapshot'))}
            ${td(escapeHtml(j['prune-backups'] || j.maxfiles || 'storage default'), 'mono wrap-cell muted')}
            ${td(escapeHtml(j['next-run'] ? `${fmtDateTime(j['next-run'])} · ${fmtAgo(j['next-run'])}` : '–'), 'n')}
            ${td(escapeHtml(j.comment || ''), 'muted wrap-cell')}
        </tr>`;
    });
    el.innerHTML = permissionNote('backup', 'Backup jobs') + table([
        {label: ''}, {label: 'Schedule'}, {label: 'Storage'}, {label: 'Guests'}, {label: 'Node'}, {label: 'Mode'}, {label: 'Keep'}, {label: 'Next run', n: true}, {label: 'Comment'},
    ], rows, {empty: 'No backup jobs'}) + (lastRun ? `<p class="panel-sub" style="margin-top:10px">Last backup task: ${escapeHtml(fmtDateTime(lastRun.starttime))} on ${escapeHtml(lastRun.node)} · ${taskState(lastRun)}
        <a href="#tasks" onclick="showTask('${attr(lastRun.node)}', '${attr(lastRun.upid)}', 'Backup');return false">its log →</a></p>` : '');
}

function renderReplication() {
    const el = document.getElementById('jb-repl');
    if (!app.conf) { el.innerHTML = '<div class="empty-state">Reading the configuration…</div>'; return; }
    const state = new Map();
    Object.values(app.conf.nodes || {}).forEach(n => (n.replication || []).forEach(r => state.set(r.id, r)));
    const rows = (app.conf.replication || []).map(j => {
        const s = state.get(j.id) || {};
        const g = guestById(j.guest);
        return `<tr class="${j.disable ? 'off' : ''}">
            ${td(`<span class="mono">${escapeHtml(j.id)}</span>`)}${td(g ? guestLink(g) : escapeHtml(j.guest))}
            ${td(`${escapeHtml(j.source || (g && g.node) || '')} → ${escapeHtml(j.target)}`)}${td(escapeHtml(j.schedule || '*/15'), 'mono')}
            ${td(j.rate ? `${escapeHtml(j.rate)} MB/s` : '<span class="muted">unlimited</span>')}
            ${td(escapeHtml(s.last_sync ? fmtAgo(s.last_sync) : '–'), 'n')}${td(missing(s.duration) ? '–' : `${num(s.duration, 1)} s`, 'n')}
            ${td(escapeHtml(s.next_sync ? fmtAgo(s.next_sync) : '–'), 'n')}
            ${td(s.fail_count || s.error ? `<span class="state bad" title="${attr(s.error || '')}">failed ${num(s.fail_count)}×</span>` : s.last_sync ? stateLabel('ok', 'OK') : '<span class="muted">–</span>')}
        </tr>`;
    });
    el.innerHTML = permissionNote('replication', 'Replication') + table([
        {label: 'Job'}, {label: 'Guest'}, {label: 'From → to'}, {label: 'Schedule'}, {label: 'Rate'}, {label: 'Last sync', n: true}, {label: 'Took', n: true}, {label: 'Next', n: true}, {label: 'State'},
    ], rows, {empty: 'No replication jobs. Replication copies ZFS volumes to another node on a schedule.'});
}

onTab('storage', showStorage, renderStorage);
