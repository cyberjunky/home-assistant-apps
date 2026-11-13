// Proxmox dashboard - Overview: the whole cluster on one page.

let ovTimeframe = stored('ov_tf', 'day');
let ovTop = stored('ov_top', 'cpu');
let ovRrd = null;
let ovRrdKey = '';
let ovRrdAt = 0;

function renderOverview() {
    const el = document.getElementById('tab-overview');
    if (!app.status || !app.status.connected) {
        delete el.dataset.built;
        el.innerHTML = app.status && app.status.config.configured
            ? '<div class="empty-state">Waiting for the cluster…</div>'
            : '<div class="empty-state">Configure the app to see your cluster here.</div>';
        return;
    }
    if (!el.dataset.built) {
        el.dataset.built = '1';
        el.innerHTML = `
            <div id="ov-tiles" class="tiles kpi-tiles"></div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Cluster</span>${chipsHtml('ov-tf', TIMEFRAMES, ovTimeframe, 'Period')}</div>
                <div class="three-charts">
                    <div><div class="chart-label">CPU <span class="muted" id="ov-cpu-hint"></span></div><div id="ov-cpu" class="chart"></div></div>
                    <div><div class="chart-label">Memory <span class="muted" id="ov-mem-hint"></span></div><div id="ov-mem" class="chart"></div></div>
                    <div><div class="chart-label">Network <span class="muted">all nodes</span></div><div id="ov-net" class="chart"></div></div>
                </div>
                <div class="legend" id="ov-legend"></div>
            </div></div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Nodes</span><span class="hint">open a node for its page</span></div>
                <div id="ov-nodes" class="table-wrap"></div>
            </div></div>
            <div class="dash-grid">
                ${panel('Health checks', 'what needs a look', '<div id="ov-health"></div>')}
                ${panel('Storage', 'shared storage once, local per node', '<div id="ov-storage" class="table-wrap"></div>')}
            </div>
            <div class="dash-grid">
                <div class="panel"><div class="panel-body">
                    <div class="chart-title"><span>Top guests</span>${chipsHtml('ov-top', [{key: 'cpu', label: 'CPU'}, {key: 'mem', label: 'Memory'}, {key: 'net', label: 'Network'}], ovTop, 'Sort by')}</div>
                    <div id="ov-top-list" class="table-wrap"></div>
                </div></div>
                <div class="dash-stack" id="ov-side"></div>
            </div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Recent tasks</span><a class="hint" href="#tasks" onclick="switchTab('tasks');return false">all tasks →</a></div>
                <div id="ov-tasks" class="table-wrap"></div>
            </div></div>`;
        bindChips('ov-tf', (key) => { ovTimeframe = key; store('ov_tf', key); loadOvRrd(true); });
        bindChips('ov-top', (key) => { ovTop = key; store('ov_top', key); renderOvTop(); });
    }
    renderOvTiles();
    renderOvNodes();
    renderOvHealth();
    renderOvStorage();
    renderOvTop();
    renderOvSide();
    renderOvTasks();
    loadOvRrd(false);
}

function renderOvTiles() {
    const c = clusterInfo();
    const all = nodes();
    const online = all.filter(n => n.online);
    const gs = guests({noTemplates: true});
    const vms = gs.filter(g => g.type === 'qemu'), cts = gs.filter(g => g.type === 'lxc');
    const run = list => list.filter(g => g.status === 'running').length;
    const cores = online.reduce((a, n) => a + (n.maxcpu || 0), 0);
    const cpu = cores ? online.reduce((a, n) => a + (n.cpu || 0) * (n.maxcpu || 0), 0) / cores : null;
    const mem = online.reduce((a, n) => a + (n.mem || 0), 0), maxmem = online.reduce((a, n) => a + (n.maxmem || 0), 0);
    const st = storages().filter(s => s.status === 'available');
    const used = st.reduce((a, s) => a + (s.disk || 0), 0), total = st.reduce((a, s) => a + (s.maxdisk || 0), 0);
    const assigned = gs.filter(g => g.status === 'running').reduce((a, g) => a + (g.maxcpu || 0), 0);
    const v = app.status.pve || {};
    const ceph = app.status.ceph;
    const haServices = (app.status.ha || []).filter(h => h.type === 'service');
    const tiles = [
        tile('Cluster', escapeHtml(c ? c.name : 'standalone'), c ? (c.quorate ? '<span class="pos">quorate</span>' : '<span class="neg">no quorum</span>') + ` · config v${escapeHtml(c.version)}` : 'single node', c && !c.quorate ? 'bad' : ''),
        tile('Nodes', `${online.length}<small> / ${all.length}</small>`, online.length === all.length ? 'all online' : `<span class="neg">${all.length - online.length} offline</span>`, online.length < all.length ? 'bad' : ''),
        tile('Guests', `${run(gs)}<small> / ${gs.length} running</small>`, `VMs ${run(vms)}/${vms.length} · CTs ${run(cts)}/${cts.length}`),
        tile('CPU', escapeHtml(plainPct(cpu)), `${num(cores)} threads` + meter(cpu, false), usageClass(cpu) === 'bad' ? 'bad' : ''),
        tile('Memory', bytesHtml(mem), `of ${fmtBytes(maxmem)}` + meter(maxmem ? mem / maxmem : null, false), usageClass(maxmem ? mem / maxmem : 0) === 'bad' ? 'bad' : ''),
        tile('Storage', bytesHtml(used), `of ${fmtBytes(total)} · ${st.length} stores` + meter(total ? used / total : null, false)),
    ];
    if (ceph && ceph.health) {
        const h = ceph.health.status.replace('HEALTH_', '');
        tiles.push(tile('Ceph', escapeHtml(h), `${num(cephOsds(ceph).up)}/${num(cephOsds(ceph).total)} OSDs up`, h === 'OK' ? 'pos' : h === 'WARN' ? 'warn' : 'bad'));
    }
    if (haServices.length) {
        const master = (app.status.ha || []).find(h => h.type === 'master');
        tiles.push(tile('HA', `${haServices.filter(h => h.state === 'started').length}<small> / ${haServices.length}</small>`, master ? `master ${escapeHtml(master.node || '')}` : 'no master'));
    }
    tiles.push(tile('Proxmox VE', escapeHtml(v.version || '–'), v.repoid ? `<span class="mono">${escapeHtml(v.repoid.slice(0, 8))}</span>` : ''));
    document.getElementById('ov-tiles').innerHTML = tiles.join('');
}

function cephOsds(ceph) {
    const m = ceph.osdmap || {};
    const o = m.osdmap || m;  // Older Ceph nests it
    return {total: o.num_osds || 0, up: o.num_up_osds || 0, in: o.num_in_osds || 0};
}

function nodeVersion(n) {
    const pv = n.status && n.status.pveversion;
    return pv ? pv.split('/')[1] || pv : '';
}

function renderOvNodes() {
    const gs = guests({noTemplates: true});
    const rows = nodes().map(n => {
        const mine = gs.filter(g => g.node === n.name);
        const load = n.status && n.status.loadavg ? n.status.loadavg[0] : null;
        const upd = n.conf && Array.isArray(n.conf.updates) ? n.conf.updates.length : null;
        const sub = n.conf && n.conf.subscription ? n.conf.subscription.status : '';
        return `<tr onclick="openNode('${attr(n.name)}')">
            ${td(`<span class="name">${escapeHtml(n.name)}</span><span class="sub">${escapeHtml(n.ip)}</span>`)}
            ${td(n.online ? stateLabel('ok', 'online') : stateLabel('bad', 'offline'))}
            ${td(n.online ? meter(n.cpu, undefined, {title: `${num(n.maxcpu)} threads`}) : '–')}
            ${td(missing(load) ? '–' : escapeHtml(load), 'n')}
            ${td(n.online ? meter(n.maxmem ? n.mem / n.maxmem : null, `${fmtBytes(n.mem)} / ${fmtBytes(n.maxmem)}`, {cls: 'stack'}) : '–')}
            ${td(n.online ? meter(n.maxdisk ? n.disk / n.maxdisk : null, undefined, {title: `root ${fmtBytes(n.disk)} of ${fmtBytes(n.maxdisk)}`}) : '–')}
            ${td(`${mine.filter(g => g.status === 'running').length}<span class="muted"> / ${mine.length}</span>`, 'n')}
            ${td(escapeHtml(fmtUptime(n.uptime)), 'n')}
            ${td(escapeHtml(nodeVersion(n) || '–'), 'mono')}
            ${td(upd === null ? '<span class="muted">–</span>' : upd ? badge(`${upd} updates`, 'warn') : '<span class="muted">up to date</span>')}
            ${td(sub === 'active' ? badge('subscribed', 'ok') : sub ? '<span class="muted">none</span>' : '–')}
        </tr>`;
    });
    document.getElementById('ov-nodes').innerHTML = table([
        {label: 'Node'}, {label: 'Status'}, {label: 'CPU'}, {label: 'Load', n: true}, {label: 'Memory'}, {label: 'Root disk'},
        {label: 'Guests', n: true}, {label: 'Uptime', n: true}, {label: 'Version'}, {label: 'Updates'}, {label: 'Subscription'},
    ], rows, {cls: 'hover'});
}

// Everything that needs attention that isn't already a banner
function healthChecks() {
    const out = [];
    const add = (kind, text, go) => out.push({kind, text, go});
    nodes().forEach(n => {
        if (!n.conf) return;
        (n.conf.disks || []).forEach(d => {
            if (d.health && !['PASSED', 'OK', 'UNKNOWN'].includes(String(d.health).toUpperCase())) {
                add('bad', `<b>${escapeHtml(n.name)}</b>: disk ${escapeHtml(d.devpath)} SMART <b>${escapeHtml(d.health)}</b> <span class="muted">${escapeHtml(d.model || '')}</span>`, `nodes/${n.name}`);
            } else if (typeof d.wearout === 'number' && d.wearout <= 20) {
                add('warn', `<b>${escapeHtml(n.name)}</b>: SSD ${escapeHtml(d.devpath)} has ${d.wearout}% life left`, `nodes/${n.name}`);
            }
        });
        (n.conf.zfs || []).forEach(z => {
            if (z.health && z.health !== 'ONLINE') add('bad', `<b>${escapeHtml(n.name)}</b>: ZFS pool ${escapeHtml(z.name)} is <b>${escapeHtml(z.health)}</b>`, `nodes/${n.name}`);
            else if (z.size && z.alloc / z.size >= 0.8) add('warn', `<b>${escapeHtml(n.name)}</b>: ZFS pool ${escapeHtml(z.name)} is ${plainPct(z.alloc / z.size, 0)} full (keep ZFS under 80%)`, `nodes/${n.name}`);
        });
        const failed = (n.conf.services || []).filter(s => s['active-state'] === 'failed' || (s['unit-state'] === 'enabled' && s.state !== 'running' && s['active-state'] !== 'inactive'));
        if (failed.length) add('warn', `<b>${escapeHtml(n.name)}</b>: service${failed.length > 1 ? 's' : ''} ${failed.map(s => `<span class="mono">${escapeHtml(s.name)}</span>`).join(', ')} not running`, `nodes/${n.name}`);
        if (n.conf.network && n.conf.network.changes) add('info', `<b>${escapeHtml(n.name)}</b>: network changes waiting to be applied`, 'network/interfaces');
        const repl = (n.conf.replication || []).filter(r => r.fail_count || r.error);
        repl.forEach(r => add('bad', `<b>${escapeHtml(n.name)}</b>: replication ${escapeHtml(r.id)} failed ${num(r.fail_count)}× ${escapeHtml(r.error || '')}`, 'storage/replication'));
    });
    const upd = nodes().filter(n => n.conf && Array.isArray(n.conf.updates) && n.conf.updates.length);
    if (upd.length) add('info', `Updates available on ${upd.map(n => `<b>${escapeHtml(n.name)}</b> (${n.conf.updates.length})`).join(', ')}`, `nodes/${upd[0].name}`);
    const notBacked = (app.conf && app.conf.not_backed_up) || [];
    if (notBacked.length) add('warn', `${notBacked.length} guest${notBacked.length > 1 ? 's are' : ' is'} in no backup job: ${notBacked.map(g => `<span class="mono">${escapeHtml(g.vmid)}</span> ${escapeHtml(g.name || '')}`).join(', ')}`, 'backups');
    const dayAgo = Date.now() / 1000 - 86400;
    const failedTasks = ((app.status && app.status.tasks) || []).filter(t => t.endtime && t.status && t.status !== 'OK' && !/^WARNINGS/.test(t.status) && t.starttime >= dayAgo);
    if (failedTasks.length) add('bad', `${failedTasks.length} task${failedTasks.length > 1 ? 's' : ''} failed in the last 24 hours: ${[...new Set(failedTasks.map(taskName))].map(escapeHtml).join(', ')}`, 'tasks');
    const ha = (app.status.ha || []).filter(h => h.type === 'service' && ['error', 'fence', 'recovery', 'freeze'].includes(h.state));
    ha.forEach(h => add('bad', `HA ${escapeHtml(h.sid)} is <b>${escapeHtml(h.state)}</b>`, 'overview'));
    pbsAlerts().forEach(a => add(a.kind, a.text, 'backups/server'));
    const fw = app.conf && app.conf.firewall && app.conf.firewall.options;
    if (fw && !Number(fw.enable)) add('info', 'The datacenter firewall is off: no host or guest firewall rules are applied', 'firewall/options');
    return out;
}

function renderOvHealth() {
    const el = document.getElementById('ov-health');
    if (!app.conf) { el.innerHTML = '<div class="empty-state">Reading the configuration…</div>'; return; }
    const checks = healthChecks();
    el.innerHTML = checks.length ? `<div class="checks-list">${checks.map(c => `
        <a class="check ${c.kind}" href="#${attr(c.go)}" onclick="switchTab('${attr(c.go.split('/')[0])}', '${attr(c.go.split('/').slice(1).join('/'))}');return false">
            <span class="sdot ${c.kind === 'info' ? '' : c.kind}"></span><span>${c.text}</span></a>`).join('')}</div>`
        : `<div class="all-good"><span class="sdot ok"></span> All good: disks, ZFS, services, backups, replication and tasks.</div>`;
}

function renderOvStorage() {
    const rows = storages().map(s => {
        const ratio = s.maxdisk ? s.disk / s.maxdisk : null;
        return `<tr onclick="switchTab('storage')">
            ${td(`<span class="name">${escapeHtml(s.storage)}</span><span class="sub">${s.shared ? 'shared' : escapeHtml(s.node)} · ${escapeHtml(s.plugintype || '')}</span>`)}
            ${td(s.status === 'available' ? meter(ratio, `${fmtBytes(s.disk)} / ${fmtBytes(s.maxdisk)}`, {cls: 'stack'}) : stateLabel('bad', s.status || 'unknown'))}
            ${td(escapeHtml(plainPct(ratio, 0)), 'n')}
        </tr>`;
    });
    document.getElementById('ov-storage').innerHTML = table([{label: 'Storage'}, {label: 'Used'}, {label: '', n: true}], rows, {cls: 'hover compact-rows'});
}

function renderOvTop() {
    const running = guests({noTemplates: true}).filter(g => g.status === 'running');
    const key = {cpu: g => (g.cpu || 0) * (g.maxcpu || 1), mem: g => g.mem || 0, net: g => (g.netin || 0) + (g.netout || 0)}[ovTop];
    const list = running.sort((a, b) => key(b) - key(a)).slice(0, 10);
    const rows = list.map(g => `<tr onclick="openGuest(${g.vmid})">
        ${td(guestLabel(g))}
        ${td(escapeHtml(g.node), 'muted')}
        ${td(meter(g.cpu, plainPct(g.cpu), {title: `${g.maxcpu} vCPU`}))}
        ${td(meter(g.maxmem ? g.mem / g.maxmem : null, fmtBytes(g.mem), {title: `of ${fmtBytes(g.maxmem)}`}))}
        ${td(escapeHtml(fmtBytes((g.netin || 0) + (g.netout || 0))), 'n')}
    </tr>`);
    document.getElementById('ov-top-list').innerHTML = table([{label: 'Guest'}, {label: 'Node'}, {label: 'CPU'}, {label: 'Memory'}, {label: 'Traffic', n: true, title: 'received and sent since the guest started'}],
        rows, {cls: 'hover', empty: 'No guest is running'});
}

function renderOvSide() {
    const parts = [];
    const ceph = app.status.ceph;
    if (ceph && ceph.health) {
        const pg = ceph.pgmap || {};
        const osd = cephOsds(ceph);
        const states = (pg.pgs_by_state || []).map(s => `<span class="badge ${s.state_name === 'active+clean' ? 'ok' : 'warn'}">${escapeHtml(s.state_name)} ${num(s.count)}</span>`).join(' ');
        const checks = Object.entries(ceph.health.checks || {}).map(([k, c]) => `<div class="check ${c.severity === 'HEALTH_ERR' ? 'bad' : 'warn'}"><span class="sdot ${c.severity === 'HEALTH_ERR' ? 'bad' : 'warn'}"></span><span><span class="mono">${escapeHtml(k)}</span> ${escapeHtml((c.summary && c.summary.message) || '')}</span></div>`).join('');
        parts.push(panel('Ceph', escapeHtml(ceph.health.status), `
            <div class="facts three">
                <div class="fact"><div class="l">OSDs up / in</div><div class="v">${num(osd.up)} / ${num(osd.in)}<small> of ${num(osd.total)}</small></div></div>
                <div class="fact"><div class="l">Monitors</div><div class="v">${num((ceph.quorum_names || []).length)}<small> in quorum</small></div></div>
                <div class="fact"><div class="l">PGs</div><div class="v">${num(pg.num_pgs)}</div></div>
                <div class="fact"><div class="l">Read</div><div class="v">${escapeHtml(fmtByteRate(pg.read_bytes_sec || 0))}<small> ${num(pg.read_op_per_sec || 0)} IOPS</small></div></div>
                <div class="fact"><div class="l">Write</div><div class="v">${escapeHtml(fmtByteRate(pg.write_bytes_sec || 0))}<small> ${num(pg.write_op_per_sec || 0)} IOPS</small></div></div>
                <div class="fact"><div class="l">Raw used</div><div class="v">${pg.bytes_total ? plainPct(pg.bytes_used / pg.bytes_total, 0) : '–'}<small> ${escapeHtml(fmtBytes(pg.bytes_used))}</small></div></div>
            </div>
            ${pg.bytes_total ? `<div style="margin-top:10px">${meter(pg.bytes_used / pg.bytes_total, `${fmtBytes(pg.bytes_avail)} free`, {cls: 'big'})}</div>` : ''}
            <div class="chips" style="margin-top:10px">${states}</div>
            ${checks ? `<div class="checks-list" style="margin-top:10px">${checks}</div>` : ''}`));
    }
    const ha = app.status.ha || [];
    const services = ha.filter(h => h.type === 'service');
    if (services.length || ha.some(h => h.type === 'master')) {
        const master = ha.find(h => h.type === 'master');
        const lrms = ha.filter(h => h.type === 'lrm');
        parts.push(panel('High availability', master ? `master on ${escapeHtml(master.node || '')}` : 'no master', `
            <div class="chips" style="margin-bottom:10px">${lrms.map(l => `<span class="badge ${l.state === 'active' ? 'ok' : l.state === 'idle' ? 'mode' : 'warn'}" title="${attr(l.status || '')}">${escapeHtml(l.node)} ${escapeHtml(l.state || '')}</span>`).join('')}</div>
            ${table([{label: 'Resource'}, {label: 'Node'}, {label: 'State'}], services.map(h => {
                const g = guestById(String(h.sid || '').split(':')[1]);
                return `<tr>${td(g ? guestLink(g) : escapeHtml(h.sid))}${td(escapeHtml(h.node || ''))}${td(stateLabel(h.state === 'started' ? 'ok' : h.state === 'stopped' || h.state === 'ignored' ? '' : 'warn', h.state || ''))}</tr>`;
            }), {empty: 'No HA resources'})}`));
    }
    if (!parts.length) {
        const jobs = (app.conf && app.conf.backup_jobs) || [];
        parts.push(panel('Backup jobs', '', jobs.length ? table([{label: 'Schedule'}, {label: 'Storage'}, {label: 'Guests'}, {label: 'Next run', n: true}],
            jobs.map(j => `<tr class="${j.enabled === 0 ? 'off' : ''}">${td(escapeHtml(j.schedule || ''), 'mono')}${td(escapeHtml(j.storage || ''))}${td(j.all ? 'all' + (j.exclude ? ` but ${escapeHtml(j.exclude)}` : '') : escapeHtml(j.vmid || j.pool || ''))}${td(escapeHtml(j['next-run'] ? fmtDateTime(j['next-run']) : '–'), 'n')}</tr>`))
            : '<div class="empty-state">No backup jobs</div>'));
    }
    document.getElementById('ov-side').innerHTML = parts.join('');
}

function taskRows(list) {
    return list.map(t => `<tr class="${t.status && t.status !== 'OK' && !/^WARNINGS/.test(t.status) ? 'failed' : ''}" onclick="showTask('${attr(t.node)}', '${attr(t.upid)}', '${attr(taskName(t))}')">
        ${td(escapeHtml(fmtDateTime(t.starttime)), 'mono')}
        ${td(escapeHtml(taskName(t)))}
        ${td(taskTarget(t))}
        ${td(escapeHtml(t.node))}
        ${td(escapeHtml(t.user || ''), 'muted')}
        ${td(escapeHtml(t.endtime ? fmtDuration(t.endtime - t.starttime) : fmtDuration(Date.now() / 1000 - t.starttime)), 'n')}
        ${td(taskState(t))}
    </tr>`);
}
const TASK_HEAD = [{label: 'Started'}, {label: 'Task'}, {label: 'Target'}, {label: 'Node'}, {label: 'User'}, {label: 'Took', n: true}, {label: 'Status'}];

function renderOvTasks() {
    const list = ((app.status && app.status.tasks) || []).slice(0, 10);
    document.getElementById('ov-tasks').innerHTML = table(TASK_HEAD, taskRows(list), {cls: 'hover', empty: noteFor('tasks') || 'No tasks yet'});
}

// ---------------------------------------------------------------------
// History of the whole cluster
// ---------------------------------------------------------------------
async function loadOvRrd(force) {
    const key = ovTimeframe;
    const maxAge = key === 'hour' ? 30 : 120;
    if (!force && ovRrd && ovRrdKey === key && Date.now() / 1000 - ovRrdAt < maxAge) return;
    ovRrdKey = key;
    ovRrdAt = Date.now() / 1000;
    try {
        const data = await api(`rrd/cluster?timeframe=${key}`);
        if (ovRrdKey !== key) return;
        ovRrd = data;
        renderOvRrd();
    } catch (e) {
        ['ov-cpu', 'ov-mem', 'ov-net'].forEach(id => emptyChart(document.getElementById(id), e.message));
    }
}

function renderOvRrd() {
    const d = ovRrd;
    if (!d || !document.getElementById('ov-cpu')) return;
    const x = d.time;
    const fx = timeFmt(ovTimeframe);
    const ticks = () => timeAxisTicks(x[0], x[x.length - 1], 4);
    const last = arr => { for (let i = arr.length - 1; i >= 0; i--) if (!missing(arr[i])) return arr[i]; return null; };
    document.getElementById('ov-cpu-hint').textContent = `now ${plainPct(last(d.cpu))}`;
    lineChart(document.getElementById('ov-cpu'), {
        x, xTicks: x.length > 1 ? ticks() : [], height: 150, zero: true, max: 0.0001,
        series: [{name: 'CPU', color: color('series-1'), values: d.cpu, area: true}, {name: 'IO wait', color: color('series-2'), values: d.iowait, width: 1.5}],
        formatX: fx, formatY: v => plainPct(v), formatTick: v => `${Math.round(v * 100)}%`,
    });
    const memTotal = last(d.memtotal);
    document.getElementById('ov-mem-hint').textContent = memTotal ? `of ${fmtBytes(memTotal)}` : '';
    lineChart(document.getElementById('ov-mem'), {
        x, xTicks: x.length > 1 ? ticks() : [], height: 150, zero: true,
        series: [{name: 'Used', color: color('series-3'), values: d.memused, area: true}, {name: 'Total', color: color('idle'), values: d.memtotal, dash: '4 4', width: 1.5}],
        formatX: fx, formatY: v => fmtBytes(v), formatTick: v => fmtBytes(v).replace(/\.\d /, ' '),
    });
    lineChart(document.getElementById('ov-net'), {
        x, xTicks: x.length > 1 ? ticks() : [], height: 150, zero: true,
        series: [{name: 'In', color: color('series-1'), values: d.netin, area: true}, {name: 'Out', color: color('series-4'), values: d.netout, width: 1.5}],
        formatX: fx, formatY: v => fmtRate(v), formatTick: v => fmtRate(v).replace(/(\d)\.\d/, '$1'),
    });
    document.getElementById('ov-legend').innerHTML = legendHtml([
        {name: 'CPU', color: color('series-1')}, {name: 'IO wait', color: color('series-2'), kind: 'line'},
        {name: 'Memory used', color: color('series-3')}, {name: 'Network in', color: color('series-1')}, {name: 'Network out', color: color('series-4'), kind: 'line'},
    ]);
}

onTab('overview', renderOverview, renderOverview);
