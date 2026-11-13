// Proxmox dashboard - Backups: are the guests protected, how old is the latest good backup (RPO), how long a
// restore would take (RTO), what each night's job did, and what the backups take on storage.

let bkSection = stored('bk_section', 'glance');
let bkHistory = null;
let bkHistoryAt = 0;
let bkHistoryError = '';
let bkGuest = '';
const bkSort = {key: stored('bk_sort', 'status'), dir: stored('bk_dir', 'desc')};
const BK_SECTIONS_ALL = [{key: 'glance', label: 'At a glance'}, {key: 'guests', label: 'Per guest'}, {key: 'storage', label: 'Storage'}, {key: 'jobs', label: 'Jobs'}, {key: 'server', label: 'Backup server'}];
// The backup server's section only when there is one
const bkSections = () => BK_SECTIONS_ALL.filter(s => s.key !== 'server' || pbsVisible());
const BK_RPO = [{key: 'auto', label: 'Target: from the job'}, {key: '4', label: 'Target: 4 hours'}, {key: '12', label: 'Target: 12 hours'},
    {key: '24', label: 'Target: 1 day'}, {key: '48', label: 'Target: 2 days'}, {key: '168', label: 'Target: 1 week'}];
const bkSettings = () => ({rpo: stored('bk_rpo', 'auto'), speed: Math.max(1, Number(stored('bk_speed', '150')) || 150)});

function showBackups(sub) {
    if (sub && /^\d+$/.test(sub) && guestById(sub)) { bkGuest = sub; }
    else { bkGuest = ''; if (BK_SECTIONS_ALL.some(s => s.key === sub)) bkSection = sub; setSub(bkSection); }
    delete document.getElementById('tab-backups').dataset.view;
    renderBackupsPage();
    loadGsBackups(false);
    loadBkHistory(false);
}

async function loadBkHistory(force) {
    if (!force && bkHistory && Date.now() / 1000 - bkHistoryAt < 300) return;
    bkHistoryAt = Date.now() / 1000;
    try {
        bkHistory = await api('backups/history');
        bkHistoryError = '';
    } catch (e) {
        bkHistoryError = e.message;
        bkHistory = bkHistory || {runs: [], tasks: [], days: 14};
    }
    if (activeTab === 'backups') renderBackupsPage();
}

// =====================================================================
// The model: per guest what protects it and how well
// =====================================================================

// How often a Proxmox calendar event runs, in hours ("02:00" daily, "sat 04:00" weekly, "*/6:00", "mon..fri 22:00", "hourly")
function scheduleHours(text) {
    const s = String(text || '').trim().toLowerCase();
    if (!s) return 24;
    const words = {minutely: 1 / 60, hourly: 1, daily: 24, weekly: 168, monthly: 720, quarterly: 2190, semiannually: 4380, yearly: 8760, annually: 8760};
    if (words[s]) return words[s];
    const days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
    let perWeek = 7;
    const dayPart = s.match(/^([a-z.,]+)\s/);
    if (dayPart) {
        perWeek = 0;
        dayPart[1].split(',').forEach(part => {
            const [a, b] = part.split('..');
            const i = days.indexOf(a.slice(0, 3)), j = b ? days.indexOf(b.slice(0, 3)) : i;
            if (i >= 0 && j >= 0) perWeek += (j >= i ? j - i : j + 7 - i) + 1;
        });
        perWeek = perWeek || 7;
    }
    const time = s.replace(/^([a-z.,]+)\s+/, '');
    const [hours] = time.split(':');
    let perDay = 1;
    if (/^\*\/(\d+)$/.test(hours)) perDay = 24 / Number(hours.slice(2));
    else if (hours === '*') perDay = 24;
    else if (hours && hours.includes(',')) perDay = hours.split(',').length;
    else if (/^\d+\.\.\d+$/.test(hours)) { const [a, b] = hours.split('..').map(Number); perDay = b - a + 1; }
    return 168 / (perWeek * perDay);
}

// The enabled jobs that back up a guest
function jobsFor(g) {
    return ((app.conf && app.conf.backup_jobs) || []).filter(j => (j.enabled === undefined || Number(j.enabled)) && jobCovers(j, g));
}
function jobCovers(j, g) {
    if (j.node && j.node !== g.node) return false;
    const ids = String(j.vmid || '').split(',').map(x => x.trim()).filter(Boolean);
    const excluded = String(j.exclude || '').split(',').map(x => x.trim());
    if (Number(j.all)) return !excluded.includes(String(g.vmid));
    if (j.pool) return g.pool === j.pool;
    return ids.includes(String(g.vmid));
}

function fmtHours(h) {
    if (missing(h)) return '–';
    if (h < 1) return `${Math.round(h * 60)} min`;
    if (h < 48) return `${num(h, h < 10 && h % 1 ? 1 : 0)} h`;
    return `${num(h / 24, h % 24 ? 1 : 0)} days`;
}
const BK_STATES = {
    ok: {label: 'OK', cls: 'ok', rank: 0},
    late: {label: 'late', cls: 'warn', rank: 2},
    missed: {label: 'RPO missed', cls: 'bad', rank: 3},
    none: {label: 'no backup', cls: 'bad', rank: 4},
    unprotected: {label: 'in no job', cls: 'bad', rank: 5},
};

function backupModel() {
    const set = bkSettings();
    const now = Date.now() / 1000;
    const points = gsBackups || [];
    const runs = (bkHistory && bkHistory.runs) || [];
    return guests({noTemplates: true}).map(g => {
        const jobs = jobsFor(g);
        const mine = points.filter(b => String(b.vmid) === String(g.vmid)).sort((a, b) => b.ctime - a.ctime);
        const good = mine.find(b => !(b.verification && b.verification.state === 'failed'));
        const myRuns = runs.filter(r => String(r.vmid) === String(g.vmid));
        const ok = myRuns.filter(r => r.ok === true), failed = myRuns.filter(r => r.ok === false);
        const interval = jobs.length ? Math.min(...jobs.map(j => scheduleHours(j.schedule || j.starttime))) : null;
        const target = set.rpo === 'auto' ? interval : Number(set.rpo);
        const age = good ? (now - good.ctime) / 3600 : null;
        let state = 'ok';
        if (!good) state = jobs.length ? 'none' : 'unprotected';
        else if (target && age > target * 2 + 1) state = 'missed';
        else if (target && age > target * 1.25 + 1) state = 'late';
        else if (!jobs.length) state = 'unprotected';
        const size = good ? good.size || 0 : 0;
        const durations = ok.map(r => r.duration).filter(v => !missing(v));
        const lastRun = myRuns[0] || null;
        const storagesUsed = [...new Set(mine.map(b => b.storage))];
        return {
            g, jobs, interval, target, age, state, points: mine, good, latest: mine[0] || null,
            runs: myRuns, ok: ok.length, failed: failed.length, lastRun,
            total: mine.reduce((a, b) => a + (b.size || 0), 0), size,
            rto: size ? size / (set.speed * 1024 * 1024) : null,
            avgDuration: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null,
            verifiedFailed: mine.filter(b => b.verification && b.verification.state === 'failed').length,
            verified: mine.filter(b => b.verification && b.verification.state === 'ok').length,
            storages: storagesUsed,
        };
    });
}

function bkStateChip(m) {
    const s = BK_STATES[m.state];
    return `<span class="state ${s.cls}">${escapeHtml(s.label)}</span>`;
}
function rpoText(m) {
    if (missing(m.age)) return '<span class="neg">none</span>';
    const cls = m.state === 'missed' ? 'neg' : m.state === 'late' ? 'warn-text' : '';
    return `<span class="${cls}">${escapeHtml(fmtHours(m.age))}</span>`;
}
const bkTick = v => fmtBytes(v).replace(/\.\d /, ' ');
const fmtRto = seconds => missing(seconds) ? '–' : `≈ ${fmtDuration(Math.max(60, seconds))}`;

// =====================================================================
// The page
// =====================================================================
function renderBackupsPage() {
    const el = document.getElementById('tab-backups');
    if (!app.status || !app.status.connected) {
        el.innerHTML = '<div class="empty-state">Waiting for the cluster…</div>';
        delete el.dataset.view;
        return;
    }
    if (bkGuest) return renderBkGuest(el);
    if (!bkSections().some(s => s.key === bkSection)) bkSection = 'glance';
    const view = `main:${bkSections().length}`;
    if (el.dataset.view !== view) {
        el.dataset.view = view;
        const set = bkSettings();
        el.innerHTML = `
            <div class="filters">
                ${chipsHtml('bk-sections', bkSections(), bkSection, 'Section')}
                <span class="bk-settings">
                    <select class="select" id="bk-rpo" aria-label="RPO target">${BK_RPO.map(o => `<option value="${o.key}"${o.key === set.rpo ? ' selected' : ''}>${escapeHtml(o.label)}</option>`).join('')}</select>
                    <label class="bk-speed" title="How fast a restore reads and writes; used for the restore time estimate">Restore speed <input class="input" id="bk-speed" type="number" min="1" max="10000" value="${set.speed}"> MB/s</label>
                    <button type="button" class="btn btn-small" onclick="loadGsBackups(true);loadBkHistory(true)" title="Read the backups and job logs again">⟳</button>
                </span>
            </div>
            <div id="bk-note"></div>
            <div class="section" data-section="glance">
                <div id="bk-tiles" class="tiles kpi-tiles"></div>
                <div class="dash-grid wide-left">
                    ${panel('Last 14 days', '<span class="bk-legend"><i class="ok"></i>backed up <i class="bad"></i>failed <i class="none"></i>nothing</span>', '<div id="bk-calendar" class="table-wrap"></div>')}
                    ${panel('Needs a look', '', '<div id="bk-attention"></div>')}
                </div>
            </div>
            <div class="section" data-section="guests">
                <div class="filters"><input class="input" id="bk-search" type="search" placeholder="Guest, ID, storage or state" aria-label="Search">
                    <span class="range-text">RPO: age of the latest good backup · RTO: latest backup ÷ restore speed</span></div>
                ${panel('', '', '<div id="bk-table" class="table-wrap"></div>')}
            </div>
            <div class="section" data-section="storage">
                ${panel('Backup storage', 'what the backups take, and what is left', '<div id="bk-stores" class="table-wrap"></div>')}
                <div class="dash-grid">
                    ${panel('Data moved per night', 'what the jobs transferred, from their logs', '<div id="bk-moved" class="chart"></div>')}
                    ${panel('Largest guests', 'all restore points together', '<div id="bk-largest" class="chart"></div>')}
                </div>
            </div>
            <div class="section" data-section="server"><div id="bk-server"></div></div>
            <div class="section" data-section="jobs">
                ${panel('Backup jobs', 'Datacenter → Backup in Proxmox', '<div id="bk-jobs" class="table-wrap"></div>')}
                ${panel('Job runs per node', 'every backup task of the last 14 days; click one for its log', '<div id="bk-runs" class="table-wrap"></div>')}
            </div>`;
        bindChips('bk-sections', (key) => { bkSection = key; store('bk_section', key); setSub(key); markBkSection(); renderBackupsPage(); });
        document.getElementById('bk-rpo').addEventListener('change', (e) => { store('bk_rpo', e.target.value); renderBackupsPage(); });
        document.getElementById('bk-speed').addEventListener('change', (e) => { store('bk_speed', String(Math.max(1, Number(e.target.value) || 150))); renderBackupsPage(); });
        document.getElementById('bk-search').addEventListener('input', () => renderBkTable(backupModel()));
        document.getElementById('bk-table').addEventListener('click', (e) => {
            const th = e.target.closest('th[data-sort]');
            if (!th) return;
            bkSort.dir = bkSort.key === th.dataset.sort && bkSort.dir === 'desc' ? 'asc' : 'desc';
            bkSort.key = th.dataset.sort;
            store('bk_sort', bkSort.key); store('bk_dir', bkSort.dir);
            renderBkTable(backupModel());
        });
        markBkSection();
    }
    const notes = [];
    if (gsBackups === null || !bkHistory) notes.push(setupHint('Reading the backups on every backup storage and the logs of the backup jobs…', 'info'));
    if (bkHistoryError) notes.push(setupHint(`The job history can't be read: ${escapeHtml(bkHistoryError)}`, 'warn'));
    notes.push(permissionNote('backups', 'Backups on storage'), permissionNote('backuplog', 'Backup job logs'), permissionNote('backuptasks', 'Backup tasks'));
    document.getElementById('bk-note').innerHTML = notes.join('');
    const model = backupModel();
    if (bkSection === 'glance') { renderBkTiles(model); renderBkCalendar(model); renderBkAttention(model); }
    else if (bkSection === 'guests') renderBkTable(model);
    else if (bkSection === 'storage') renderBkStorage(model);
    else if (bkSection === 'server') renderPbsSection(document.getElementById('bk-server'));
    else renderBkJobs(model);
}

function markBkSection() {
    document.querySelectorAll('#tab-backups .section').forEach(s => s.classList.toggle('active', s.dataset.section === bkSection));
}

function renderBkTiles(model) {
    const total = model.length;
    const covered = model.filter(m => m.jobs.length).length;
    const met = model.filter(m => m.state === 'ok').length;
    const worst = model.filter(m => !missing(m.age)).sort((a, b) => b.age - a.age)[0];
    const none = model.filter(m => !m.good).length;
    const day = Date.now() / 1000 - 26 * 3600;
    const lastNight = ((bkHistory && bkHistory.runs) || []).filter(r => r.ts >= day);
    const nightFailed = lastNight.filter(r => r.ok === false);
    const points = model.reduce((a, m) => a + m.points.length, 0);
    const data = model.reduce((a, m) => a + m.total, 0);
    const longest = model.filter(m => m.rto).sort((a, b) => b.rto - a.rto)[0];
    const failedVerify = model.reduce((a, m) => a + m.verifiedFailed, 0);
    const jobs = ((app.conf && app.conf.backup_jobs) || []).filter(j => j.enabled === undefined || Number(j.enabled));
    const next = jobs.map(j => j['next-run']).filter(Boolean).sort()[0];
    document.getElementById('bk-tiles').innerHTML = [
        tile('Protected', `${covered}<small> / ${total}</small>`, covered === total ? 'every guest is in a job' : `<span class="neg">${total - covered} in no job</span>`, covered < total ? 'warn' : ''),
        tile('RPO met', `${met}<small> / ${total}</small>`, none ? `<span class="neg">${none} without a backup</span>` : 'latest backup on time', met < total ? 'warn' : 'pos'),
        tile('Worst RPO', worst ? escapeHtml(fmtHours(worst.age)) : '–', worst ? escapeHtml(guestName(worst.g)) : '', worst && worst.state !== 'ok' ? 'bad' : ''),
        tile('Last night', lastNight.length ? `${lastNight.length - nightFailed.length}<small> / ${lastNight.length}</small>` : '–',
            nightFailed.length ? `<span class="neg">${nightFailed.length} failed: ${nightFailed.map(r => escapeHtml(guestName(guestById(r.vmid)) || r.vmid)).join(', ')}</span>` : lastNight.length ? 'all backed up' : 'no runs in 26 h', nightFailed.length ? 'bad' : ''),
        tile('Restore points', num(points), `${fmtBytes(data)} in all`),
        tile('Longest restore', longest ? escapeHtml(fmtRto(longest.rto)) : '–', longest ? `${escapeHtml(guestName(longest.g))} · ${fmtBytes(longest.size)}` : ''),
        tile('Verification', failedVerify ? `${failedVerify}<small> failed</small>` : 'OK', failedVerify ? 'restore points to look at' : 'no failed verification', failedVerify ? 'bad' : ''),
        tile('Next job', next ? escapeHtml(fmtTime(next)) : '–', next ? escapeHtml(fmtAgo(next)) : 'no scheduled job'),
        ...pbsServers().map(s => {
            const issues = pbsAlerts().filter(a => a.kind !== 'info' && a.text.includes(`<b>${escapeHtml(s.name)}</b>`)).length;
            return tile('Backup server', !s.connected ? 'offline' : issues ? `${issues}<small> to look at</small>` : 'OK', escapeHtml(s.name), !s.connected ? 'bad' : issues ? 'warn' : 'pos');
        }),
    ].join('');
}

function bkDays(count) {
    const out = [];
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    for (let i = count - 1; i >= 0; i--) out.push(d.getTime() / 1000 - i * 86400);
    return out;
}

function renderBkCalendar(model) {
    const days = bkDays(14);
    const head = `<tr><th>Guest</th>${days.map(d => `<th class="bk-day" title="${attr(new Date(d * 1000).toLocaleDateString())}">${escapeHtml(new Date(d * 1000).toLocaleDateString([], {weekday: 'narrow'}))}<br><span class="muted">${new Date(d * 1000).getDate()}</span></th>`).join('')}<th class="n">RPO</th></tr>`;
    const rows = model.slice().sort((a, b) => BK_STATES[b.state].rank - BK_STATES[a.state].rank || a.g.vmid - b.g.vmid).map(m => {
        const cells = days.map(d => {
            const runs = m.runs.filter(r => r.ts >= d && r.ts < d + 86400);
            const pts = m.points.filter(b => b.ctime >= d && b.ctime < d + 86400);
            const okRuns = runs.filter(r => r.ok === true), bad = runs.filter(r => r.ok === false);
            const cls = okRuns.length || (!runs.length && pts.length) ? 'ok' : bad.length ? 'bad' : 'none';
            const tip = [new Date(d * 1000).toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'}),
                ...okRuns.map(r => `backed up at ${fmtTime(r.ts)} in ${fmtDuration(r.duration)}${r.transferred ? `, ${fmtBytes(r.transferred)} moved` : ''}`),
                ...bad.map(r => `FAILED at ${fmtTime(r.ts)}: ${r.error}`),
                ...(!runs.length && pts.length ? [`restore point at ${fmtTime(pts[0].ctime)}`] : []),
                ...(!runs.length && !pts.length ? ['no backup'] : [])].join('\n');
            const upid = (bad[0] || okRuns[0] || {}).upid;
            const node = (bad[0] || okRuns[0] || {}).node;
            return `<td class="bk-cell"><span class="bk-dot ${cls}" title="${attr(tip)}"${upid ? ` onclick="event.stopPropagation();showTask('${attr(node)}', '${attr(upid)}', 'Backup')"` : ''}></span></td>`;
        }).join('');
        return `<tr onclick="switchTab('backups', '${m.g.vmid}')">${td(guestLabel(m.g))}${cells}${td(rpoText(m), 'n')}</tr>`;
    });
    document.getElementById('bk-calendar').innerHTML = rows.length ? `<table class="data hover bk-cal"><thead>${head}</thead><tbody>${rows.join('')}</tbody></table>` : '<div class="empty-state">No guests</div>';
}

function renderBkAttention(model) {
    const items = [];
    const add = (kind, html, vmid) => items.push(`<a class="check ${kind}" href="#backups/${vmid || ''}" onclick="${vmid ? `switchTab('backups', '${vmid}')` : `switchTab('backups', 'jobs')`};return false"><span class="sdot ${kind}"></span><span>${html}</span></a>`);
    model.filter(m => m.state === 'unprotected').forEach(m => add('bad', `<b>${escapeHtml(guestName(m.g))}</b> is in no backup job${m.good ? ` (latest backup ${escapeHtml(fmtAgo(m.good.ctime))})` : ''}`, m.g.vmid));
    model.filter(m => m.state === 'none').forEach(m => add('bad', `<b>${escapeHtml(guestName(m.g))}</b> has no backup at all`, m.g.vmid));
    model.filter(m => m.state === 'missed' || m.state === 'late').forEach(m => add(m.state === 'missed' ? 'bad' : 'warn',
        `<b>${escapeHtml(guestName(m.g))}</b>: latest good backup ${escapeHtml(fmtHours(m.age))} old, target ${escapeHtml(fmtHours(m.target))}${m.lastRun && m.lastRun.ok === false ? ` · last run failed: <span class="muted">${escapeHtml(m.lastRun.error)}</span>` : ''}`, m.g.vmid));
    model.filter(m => m.state === 'ok' && m.lastRun && m.lastRun.ok === false).forEach(m => add('warn', `<b>${escapeHtml(guestName(m.g))}</b>: the last run failed <span class="muted">${escapeHtml(m.lastRun.error)}</span>`, m.g.vmid));
    model.filter(m => m.verifiedFailed).forEach(m => add('bad', `<b>${escapeHtml(guestName(m.g))}</b>: ${m.verifiedFailed} restore point${m.verifiedFailed > 1 ? 's' : ''} failed verification`, m.g.vmid));
    model.filter(m => m.points.length && !m.verified && m.points.some(b => String(b.format || '').startsWith('pbs'))).forEach(m => add('info', `<b>${escapeHtml(guestName(m.g))}</b>: no restore point is verified yet`, m.g.vmid));
    const disabled = ((app.conf && app.conf.backup_jobs) || []).filter(j => j.enabled !== undefined && !Number(j.enabled));
    disabled.forEach(j => add('info', `Backup job <span class="mono">${escapeHtml(j.schedule || j.id)}</span> to ${escapeHtml(j.storage || '')} is switched off`));
    pbsAlerts().forEach(a => items.push(`<a class="check ${a.kind}" href="#backups/server" onclick="${a.upid ? `showTask('${attr(a.server)}', '${attr(a.upid)}', 'Backup server', '${attr(a.server)}')` : `switchTab('backups', 'server')`};return false"><span class="sdot ${a.kind === 'info' ? '' : a.kind}"></span><span>${a.text}</span></a>`));
    document.getElementById('bk-attention').innerHTML = items.length ? `<div class="checks-list">${items.join('')}</div>`
        : '<div class="all-good"><span class="sdot ok"></span> Every guest has a recent, good backup.</div>';
}

const BK_SORT = {
    guest: m => m.g.vmid, status: m => BK_STATES[m.state].rank * 1e9 + (m.age || 1e8), rpo: m => missing(m.age) ? Infinity : m.age,
    target: m => m.target || Infinity, points: m => m.points.length, size: m => m.size, total: m => m.total, rto: m => m.rto || 0,
    duration: m => m.avgDuration || 0, success: m => (m.ok + m.failed) ? m.ok / (m.ok + m.failed) : -1,
};

function renderBkTable(model) {
    const text = (document.getElementById('bk-search') || {}).value || '';
    const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const key = BK_SORT[bkSort.key] || BK_SORT.status;
    const list = model.filter(m => !words.length || words.every(w => `${m.g.vmid} ${guestName(m.g)} ${m.storages.join(' ')} ${BK_STATES[m.state].label}`.toLowerCase().includes(w)))
        .sort((a, b) => { const c = key(a) - key(b); return bkSort.dir === 'asc' ? c : -c; });
    const head = [
        {label: 'Guest', sort: 'guest'}, {label: 'State', sort: 'status'}, {label: 'RPO', sort: 'rpo', n: true, title: 'age of the latest good backup'},
        {label: 'Target', sort: 'target', n: true, title: 'how often its job runs, or the target you chose'}, {label: 'Last run'},
        {label: '14 days', sort: 'success', n: true, title: 'runs that worked of all runs, from the job logs'}, {label: 'Points', sort: 'points', n: true, title: 'restore points'},
        {label: 'Oldest', n: true}, {label: 'Latest size', sort: 'size', n: true}, {label: 'All points', sort: 'total', n: true},
        {label: 'Takes', sort: 'duration', n: true, title: 'average time of a backup run'}, {label: 'RTO', sort: 'rto', n: true, title: 'estimated restore time of the latest backup'},
        {label: 'Verified'}, {label: 'Storage'},
    ].map(h => h.sort === bkSort.key ? {...h, sorted: bkSort.dir} : h);
    const rows = list.map(m => {
        const lr = m.lastRun;
        const oldest = m.points[m.points.length - 1];
        return `<tr onclick="switchTab('backups', '${m.g.vmid}')">
            ${td(guestLabel(m.g))}${td(bkStateChip(m))}${td(rpoText(m), 'n')}
            ${td(m.target ? escapeHtml(fmtHours(m.target)) : '<span class="muted">–</span>', 'n')}
            ${td(!lr ? '<span class="muted">–</span>' : lr.ok === false ? `<span class="state bad" title="${attr(lr.error)}">failed ${escapeHtml(fmtAgo(lr.ts))}</span>` : lr.ok ? `<span class="state ok">${escapeHtml(fmtAgo(lr.ts))}</span>` : stateLabel('warn', 'running'))}
            ${td(m.ok + m.failed ? `<span class="${m.failed ? 'warn-text' : ''}">${m.ok}/${m.ok + m.failed}</span>` : '<span class="muted">–</span>', 'n')}
            ${td(num(m.points.length), 'n')}${td(oldest ? escapeHtml(fmtAgo(oldest.ctime)) : '–', 'n')}
            ${td(m.size ? escapeHtml(fmtBytes(m.size)) : '–', 'n')}${td(m.total ? escapeHtml(fmtBytes(m.total)) : '–', 'n')}
            ${td(m.avgDuration ? escapeHtml(fmtDuration(m.avgDuration)) : '–', 'n')}${td(escapeHtml(fmtRto(m.rto)), 'n')}
            ${td(m.verifiedFailed ? stateLabel('bad', `${m.verifiedFailed} failed`) : m.verified ? stateLabel('ok', `${m.verified}`) : '<span class="muted">–</span>')}
            ${td(m.storages.map(escapeHtml).join(', ') || '–')}
        </tr>`;
    });
    document.getElementById('bk-table').innerHTML = table(head, rows, {cls: 'hover', empty: 'No guest matches'});
}

function renderBkStorage(model) {
    const points = gsBackups || [];
    const byStore = new Map();
    points.forEach(b => {
        const s = byStore.get(b.storage) || {count: 0, size: 0, guests: new Set(), oldest: Infinity, newest: 0};
        s.count++; s.size += b.size || 0; s.guests.add(String(b.vmid));
        s.oldest = Math.min(s.oldest, b.ctime); s.newest = Math.max(s.newest, b.ctime);
        byStore.set(b.storage, s);
    });
    const rows = [...byStore.entries()].map(([name, s]) => {
        const st = storages().find(x => x.storage === name) || {};
        const used = st.disk || 0;
        const dedup = st.plugintype === 'pbs' && used ? s.size / used : null;
        return `<tr>
            ${td(`<span class="name">${escapeHtml(name)}</span><span class="sub">${escapeHtml(st.plugintype || '')}${st.shared ? ' · shared' : st.node ? ` · ${escapeHtml(st.node)}` : ''}</span>`)}
            ${td(num(s.count), 'n')}${td(num(s.guests.size), 'n')}${td(escapeHtml(fmtBytes(s.size)), 'n')}
            ${td(st.maxdisk ? meter(used / st.maxdisk, `${fmtBytes(used)} / ${fmtBytes(st.maxdisk)}`, {cls: 'stack'}) : '–')}
            ${td(dedup ? `<span title="logical size of the backups ÷ what the datastore uses">${num(dedup, 1)}×</span>` : '<span class="muted">–</span>', 'n')}
            ${td(escapeHtml(fmtAgo(s.oldest)), 'n')}${td(escapeHtml(fmtAgo(s.newest)), 'n')}
        </tr>`;
    });
    document.getElementById('bk-stores').innerHTML = table([
        {label: 'Storage'}, {label: 'Backups', n: true}, {label: 'Guests', n: true}, {label: 'Backup data', n: true, title: 'the full size of every restore point'},
        {label: 'Storage used'}, {label: 'Dedup', n: true, title: 'Proxmox Backup Server stores each chunk once'}, {label: 'Oldest', n: true}, {label: 'Newest', n: true},
    ], rows, {empty: gsBackups === null ? 'Reading…' : 'No backups found'})
        + '<p class="panel-sub" style="margin-top:8px">Backup data is the full size of every restore point. On Proxmox Backup Server they share their chunks, so the datastore uses much less; the storage used is the whole storage, other content included.</p>';
    const days = bkDays(14);
    const runs = (bkHistory && bkHistory.runs) || [];
    barChart(document.getElementById('bk-moved'), {
        data: days.map(d => ({label: fmtDay(d), values: [runs.filter(r => r.ts >= d && r.ts < d + 86400 && r.transferred).reduce((a, r) => a + r.transferred, 0)]})),
        series: [{name: 'Moved', color: color('series-1')}], height: 170, format: v => fmtBytes(v), formatTick: bkTick, empty: 'No job logs with transfer sizes',
    });
    const top = model.filter(m => m.total).sort((a, b) => b.total - a.total).slice(0, 12);
    barChart(document.getElementById('bk-largest'), {
        data: top.map(m => ({label: String(m.g.vmid), tipTitle: guestName(m.g), values: [m.size, Math.max(0, m.total - m.size)], onClick: () => switchTab('backups', String(m.g.vmid))})),
        series: [{name: 'Latest', color: color('series-1')}, {name: 'Older points', color: color('idle')}], height: 170, format: v => fmtBytes(v), formatTick: bkTick, labelWidth: 30,
    });
}

function renderBkJobs(model) {
    const el = document.getElementById('bk-jobs');
    if (!app.conf) { el.innerHTML = '<div class="empty-state">Reading the configuration…</div>'; return; }
    const rows = (app.conf.backup_jobs || []).map(j => {
        const enabled = j.enabled === undefined || Number(j.enabled);
        const sel = Number(j.all) ? `all${j.exclude ? ` except ${j.exclude}` : ''}` : j.pool ? `pool ${j.pool}` : j.vmid || '';
        const covers = model.filter(m => jobCovers(j, m.g)).length;
        return `<tr class="${enabled ? '' : 'off'}">
            ${td(enabled ? stateLabel('ok', 'on') : stateLabel('', 'off'))}
            ${td(`<span class="mono">${escapeHtml(j.schedule || j.starttime || '')}</span><span class="sub">every ${escapeHtml(fmtHours(scheduleHours(j.schedule || j.starttime)))}</span>`)}
            ${td(escapeHtml(j.storage || ''))}${td(`${escapeHtml(sel)}<span class="sub">${covers} guests${enabled ? '' : ' when on'}</span>`, 'wrap-cell')}
            ${td(escapeHtml(j.node || 'all nodes'))}${td(escapeHtml(j.mode || 'snapshot'))}
            ${td(escapeHtml(j['prune-backups'] || j.maxfiles || 'storage default'), 'mono wrap-cell muted')}
            ${td(escapeHtml(j['next-run'] ? `${fmtDateTime(j['next-run'])} · ${fmtAgo(j['next-run'])}` : '–'), 'n')}
            ${td(escapeHtml(j.comment || ''), 'muted wrap-cell')}
        </tr>`;
    });
    el.innerHTML = permissionNote('backup', 'Backup jobs') + table([
        {label: ''}, {label: 'Schedule'}, {label: 'Storage'}, {label: 'Guests'}, {label: 'Node'}, {label: 'Mode'}, {label: 'Keep'}, {label: 'Next run', n: true}, {label: 'Comment'},
    ], rows, {empty: 'No backup jobs'});
    const tasks = (bkHistory && bkHistory.tasks) || [];
    const runRows = nodes().map(n => {
        const mine = tasks.filter(t => t.node === n.name).slice(0, 28).reverse();
        const done = mine.filter(t => t.endtime);
        const avg = done.length ? done.reduce((a, t) => a + (t.endtime - t.starttime), 0) / done.length : null;
        const dots = mine.map(t => {
            const cls = !t.endtime ? 'run' : t.status === 'OK' ? 'ok' : /^WARNINGS/.test(t.status || '') ? 'warn' : 'bad';
            return `<span class="bk-dot ${cls}" title="${attr(`${fmtDateTime(t.starttime)} · ${t.status || 'running'}${t.endtime ? ` · ${fmtDuration(t.endtime - t.starttime)}` : ''}${t.id ? ` · guest ${t.id}` : ''}`)}" onclick="showTask('${attr(t.node)}', '${attr(t.upid)}', 'Backup')"></span>`;
        }).join('');
        const failed = mine.filter(t => t.endtime && t.status !== 'OK' && !/^WARNINGS/.test(t.status || '')).length;
        return `<tr>${td(`<span class="name">${escapeHtml(n.name)}</span>`)}${td(`<div class="bk-runs">${dots || '<span class="muted">no backup tasks</span>'}</div>`)}
            ${td(`${mine.length - failed}/${mine.length}`, 'n')}${td(avg ? escapeHtml(fmtDuration(avg)) : '–', 'n')}</tr>`;
    });
    document.getElementById('bk-runs').innerHTML = table([{label: 'Node'}, {label: 'Runs, oldest first'}, {label: 'OK', n: true}, {label: 'Takes', n: true}], runRows);
}

// =====================================================================
// One guest
// =====================================================================
function renderBkGuest(el) {
    const m = backupModel().find(x => String(x.g.vmid) === bkGuest);
    if (!m) { el.innerHTML = '<div class="empty-state">This guest is gone</div>'; return; }
    if (el.dataset.view !== 'guest:' + bkGuest) {
        el.dataset.view = 'guest:' + bkGuest;
        el.innerHTML = `
            <a class="back-link" href="#backups/guests" onclick="switchTab('backups', 'guests');return false">← All backups</a>
            <div id="bkg-head" class="page-head" style="margin-top:6px"></div>
            <div id="bkg-tiles" class="tiles kpi-tiles"></div>
            <div class="dash-grid">
                ${panel('Restore point size', 'full size of each backup', '<div id="bkg-sizes" class="chart"></div>')}
                ${panel('Runs', 'time per run, from the job logs; red failed', '<div id="bkg-durations" class="chart"></div>')}
            </div>
            ${panel('Restore points', '', '<div id="bkg-points" class="table-wrap"></div>')}
            ${panel('Runs of the last 14 days', 'click one for its log', '<div id="bkg-runs" class="table-wrap"></div>')}`;
    }
    const g = m.g;
    document.getElementById('bkg-head').innerHTML = `<h1>${guestLabel(g)}</h1><div class="badges">${bkStateChip(m)}${m.jobs.map(j => badge(`${j.schedule || ''} → ${j.storage || ''}`, 'mode')).join('')}</div>
        <span class="meta">on ${escapeHtml(g.node)} · <a href="#guests/${g.vmid}" onclick="openGuest(${g.vmid});return false">the guest's page</a></span>`;
    const lr = m.lastRun;
    document.getElementById('bkg-tiles').innerHTML = [
        tile('RPO', missing(m.age) ? '–' : escapeHtml(fmtHours(m.age)), m.target ? `target ${escapeHtml(fmtHours(m.target))}` : 'no job, no target', m.state === 'ok' ? 'pos' : m.state === 'late' ? 'warn' : 'bad'),
        tile('Last run', lr ? (lr.ok === false ? 'failed' : lr.ok ? 'OK' : 'running') : '–', lr ? `${escapeHtml(fmtAgo(lr.ts))}${lr.error ? ` · ${escapeHtml(lr.error)}` : ''}` : 'none in 14 days', lr && lr.ok === false ? 'bad' : ''),
        tile('14 days', m.ok + m.failed ? `${m.ok}<small> / ${m.ok + m.failed}</small>` : '–', 'runs that worked', m.failed ? 'warn' : ''),
        tile('Restore points', num(m.points.length), m.points.length ? `oldest ${escapeHtml(fmtAgo(m.points[m.points.length - 1].ctime))}` : ''),
        tile('Latest size', m.size ? bytesHtml(m.size) : '–', `${fmtBytes(m.total)} all points`),
        tile('RTO', escapeHtml(fmtRto(m.rto)), `at ${bkSettings().speed} MB/s, plus boot`),
        tile('Backup takes', m.avgDuration ? escapeHtml(fmtDuration(m.avgDuration)) : '–', 'on average'),
    ].join('');
    const pts = m.points.slice().reverse();
    lineChart(document.getElementById('bkg-sizes'), {
        x: pts.map(b => b.ctime), xTicks: pts.length > 1 ? timeAxisTicks(pts[0].ctime, pts[pts.length - 1].ctime, 4) : [], height: 160, zero: true,
        series: [{name: 'Size', color: color('series-1'), values: pts.map(b => b.size), area: true}], formatX: fmtDateTime, formatY: v => fmtBytes(v),
        formatTick: v => fmtBytes(v).replace(/\.\d /, ' '), empty: 'Fewer than two restore points',
    });
    const runs = m.runs.slice().reverse();
    signedBarChart(document.getElementById('bkg-durations'), {
        data: runs.map(r => ({label: fmtDay(r.ts), tipTitle: fmtDateTime(r.ts), values: [r.ok === false ? -1 : (r.duration || 0)],
            extra: r.ok === false ? [{value: r.error, name: 'failed'}] : r.transferred ? [{value: fmtBytes(r.transferred), name: 'moved'}] : []})),
        series: [{name: 'Run', color: color('series-1')}], bySign: true, height: 160, labelWidth: 56,
        format: v => v < 0 ? 'failed' : fmtDuration(v), formatTick: v => v < 0 ? '' : `${Math.round(v)}s`, empty: 'No runs in the job logs',
    });
    document.getElementById('bkg-points').innerHTML = table([{label: 'Taken'}, {label: 'Storage'}, {label: 'Size', n: true}, {label: 'Format'}, {label: 'Verified'}, {label: 'Protected'}, {label: 'Encrypted'}, {label: 'Notes'}],
        m.points.map(b => {
            const v = b.verification && b.verification.state;
            return `<tr>${td(`${escapeHtml(fmtDateTime(b.ctime))}<span class="sub">${escapeHtml(fmtAgo(b.ctime))}</span>`)}${td(escapeHtml(b.storage))}${td(escapeHtml(fmtBytes(b.size)), 'n')}
                ${td(escapeHtml(b.format || ''), 'mono muted')}${td(v === 'ok' ? stateLabel('ok', 'OK') : v ? stateLabel('bad', v) : '<span class="muted">–</span>')}
                ${td(b.protected ? '🔒 yes' : '<span class="muted">no</span>')}${td(b.encrypted ? 'yes' : '<span class="muted">no</span>')}${td(escapeHtml(b.notes || ''), 'wrap-cell muted')}</tr>`;
        }), {empty: 'No restore points'});
    document.getElementById('bkg-runs').innerHTML = table([{label: 'Started'}, {label: 'Result'}, {label: 'Took', n: true}, {label: 'Moved', n: true}, {label: 'Speed', n: true}, {label: 'Node'}, {label: 'Message'}],
        m.runs.map(r => `<tr onclick="showTask('${attr(r.node)}', '${attr(r.upid)}', 'Backup of ${attr(guestName(g))}')">
            ${td(escapeHtml(fmtDateTime(r.ts)))}${td(r.ok === false ? stateLabel('bad', 'failed') : r.ok ? stateLabel('ok', 'OK') : stateLabel('warn', 'running'))}
            ${td(missing(r.duration) ? '–' : escapeHtml(fmtDuration(r.duration)), 'n')}${td(r.transferred ? escapeHtml(fmtBytes(r.transferred)) : '–', 'n')}
            ${td(r.transferred && r.duration ? escapeHtml(fmtByteRate(r.transferred / r.duration)) : '–', 'n')}${td(escapeHtml(r.node))}${td(escapeHtml(r.error || ''), 'wrap-cell muted')}</tr>`),
        {cls: 'hover', empty: 'No runs in the job logs of the last 14 days'});
}

onTab('backups', showBackups, renderBackupsPage);
