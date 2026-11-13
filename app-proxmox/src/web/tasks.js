// Proxmox dashboard - Tasks: the cluster's task log, to filter, with each task's own log.

const tkFilter = {state: stored('tk_state', 'all'), node: '', type: '', text: ''};

function renderTasks() {
    const el = document.getElementById('tab-tasks');
    if (!app.status || !app.status.connected) {
        el.innerHTML = '<div class="empty-state">Waiting for the cluster…</div>';
        delete el.dataset.built;
        return;
    }
    if (!el.dataset.built) {
        el.dataset.built = '1';
        el.innerHTML = `
            <div id="tk-tiles" class="tiles kpi-tiles"></div>
            ${panel('Tasks per day', 'last 7 days of the tasks Proxmox keeps in its cluster log', '<div id="tk-chart" class="chart"></div><div class="legend" id="tk-legend"></div>')}
            <div class="filters" style="margin-top:14px">
                <input class="input" id="tk-search" type="search" placeholder="Task, guest, user or status" aria-label="Search tasks">
                ${chipsHtml('tk-state', [{key: 'all', label: 'All'}, {key: 'running', label: 'Running'}, {key: 'failed', label: 'Failed'}, {key: 'ok', label: 'OK'}], tkFilter.state, 'Status')}
                <select class="select" id="tk-node" aria-label="Node"></select>
                <select class="select" id="tk-type" aria-label="Task type"></select>
                <span class="range-text" id="tk-count"></span>
            </div>
            <div class="panel"><div class="panel-body"><div id="tk-table" class="table-wrap"></div>
                <p class="panel-sub" style="margin-top:8px">Click a task for its log. Proxmox keeps the latest tasks of each node in the cluster log.</p></div></div>`;
        document.getElementById('tk-search').addEventListener('input', (e) => { tkFilter.text = e.target.value.trim().toLowerCase(); renderTkTable(); });
        bindChips('tk-state', (k) => { tkFilter.state = k; store('tk_state', k); renderTkTable(); });
        document.getElementById('tk-node').addEventListener('change', (e) => { tkFilter.node = e.target.value; renderTkTable(); });
        document.getElementById('tk-type').addEventListener('change', (e) => { tkFilter.type = e.target.value; renderTkTable(); });
    }
    const tasks = app.status.tasks || [];
    const setOptions = (id, all, values, current, label) => {
        const el2 = document.getElementById(id);
        const html = `<option value="">${all}</option>${values.map(v => `<option value="${attr(v)}"${v === current ? ' selected' : ''}>${escapeHtml(label(v))}</option>`).join('')}`;
        if (el2.dataset.key !== html) { el2.dataset.key = html; el2.innerHTML = html; }
    };
    setOptions('tk-node', 'All nodes', nodes().map(n => n.name), tkFilter.node, v => v);
    setOptions('tk-type', 'All tasks', [...new Set(tasks.map(t => t.type))].sort((a, b) => (TASK_NAMES[a] || a).localeCompare(TASK_NAMES[b] || b)), tkFilter.type, v => TASK_NAMES[v] || v);
    renderTkTiles(tasks);
    renderTkChart(tasks);
    renderTkTable();
}

const isFailed = t => Boolean(t.endtime && t.status && t.status !== 'OK' && !/^WARNINGS/.test(t.status));
const isRunning = t => !t.endtime && !t.status;

function renderTkTiles(tasks) {
    const day = Date.now() / 1000 - 86400;
    const today = tasks.filter(t => t.starttime >= day);
    const backups = tasks.filter(t => t.type === 'vzdump');
    const lastBackup = backups[0];
    document.getElementById('tk-tiles').innerHTML = [
        tile('Running', num(tasks.filter(isRunning).length), tasks.filter(isRunning).map(taskName).slice(0, 3).map(escapeHtml).join(', ') || 'nothing now'),
        tile('Last 24 hours', num(today.length), `${today.filter(t => t.status === 'OK').length} OK`),
        tile('Failed (24 h)', num(today.filter(isFailed).length), today.filter(isFailed).map(taskName).slice(0, 3).map(escapeHtml).join(', ') || 'none', today.some(isFailed) ? 'bad' : ''),
        tile('Last backup task', lastBackup ? escapeHtml(fmtAgo(lastBackup.starttime)) : '–', lastBackup ? (isFailed(lastBackup) ? `<span class="neg">${escapeHtml(lastBackup.status)}</span>` : escapeHtml(lastBackup.status || 'running')) : 'none in the log', lastBackup && isFailed(lastBackup) ? 'bad' : ''),
    ].join('');
}

function renderTkChart(tasks) {
    const days = [];
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    for (let i = 6; i >= 0; i--) days.push(start.getTime() / 1000 - i * 86400);
    const data = days.map(d => {
        const inDay = tasks.filter(t => t.starttime >= d && t.starttime < d + 86400);
        return {label: fmtDay(d), tipTitle: new Date(d * 1000).toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'}),
            values: [inDay.filter(t => !isFailed(t) && !isRunning(t)).length, inDay.filter(isFailed).length, inDay.filter(isRunning).length]};
    });
    const series = [{name: 'OK', color: color('series-1')}, {name: 'Failed', color: color('neg')}, {name: 'Running', color: color('warning-color')}];
    barChart(document.getElementById('tk-chart'), {data, series, height: 140, format: v => `${num(v)} tasks`, empty: 'No tasks in the last 7 days'});
    document.getElementById('tk-legend').innerHTML = legendHtml(series);
}

function renderTkTable() {
    const text = tkFilter.text;
    const list = (app.status.tasks || []).filter(t => (tkFilter.state === 'all' || (tkFilter.state === 'failed' ? isFailed(t) : tkFilter.state === 'running' ? isRunning(t) : t.status === 'OK'))
        && (!tkFilter.node || t.node === tkFilter.node) && (!tkFilter.type || t.type === tkFilter.type)
        && (!text || `${taskName(t)} ${t.type} ${t.id} ${guestName(guestById(t.id))} ${t.user} ${t.status || 'running'} ${t.node}`.toLowerCase().includes(text)));
    document.getElementById('tk-count').textContent = `${list.length} of ${(app.status.tasks || []).length}`;
    document.getElementById('tk-table').innerHTML = permissionNote('tasks', 'Tasks') + table(TASK_HEAD, taskRows(list), {cls: 'hover', empty: 'No task matches'});
}

onTab('tasks', renderTasks, renderTasks);
