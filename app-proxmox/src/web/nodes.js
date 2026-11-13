// Proxmox dashboard - Nodes: one node at a time, with its history, hardware, disks, services and updates.

let ndNode = '';
let ndSection = stored('nd_section', 'summary');
let ndTimeframe = stored('nd_tf', 'hour');
let ndRrd = null;
let ndRrdKey = '';
let ndRrdAt = 0;
const ND_SECTIONS = [{key: 'summary', label: 'Summary'}, {key: 'disks', label: 'Disks'}, {key: 'system', label: 'System'}, {key: 'tasks', label: 'Tasks'}];

function showNodes(sub) {
    const all = nodes();
    if (sub && all.some(n => n.name === sub)) ndNode = sub;
    if (!ndNode || !all.some(n => n.name === ndNode)) ndNode = stored('nd_node', '') || (all[0] || {}).name || '';
    if (!all.some(n => n.name === ndNode)) ndNode = (all[0] || {}).name || '';
    if (ndNode && sub !== ndNode) setSub(ndNode);
    renderNodes(true);
}

function renderNodes(fresh) {
    const el = document.getElementById('tab-nodes');
    if (!app.status || !app.status.connected) {
        el.innerHTML = '<div class="empty-state">Waiting for the cluster…</div>';
        delete el.dataset.node;
        return;
    }
    const n = nodeByName(ndNode);
    if (!n) { el.innerHTML = '<div class="empty-state">No nodes</div>'; return; }
    if (fresh || el.dataset.node !== ndNode) {
        el.dataset.node = ndNode;
        const picker = nodes().map(x => ({key: x.name, label: x.name, title: x.online ? 'online' : 'offline'}));
        el.innerHTML = `
            <div class="filters">${chipsHtml('nd-pick', picker, ndNode, 'Node')}${chipsHtml('nd-sections', ND_SECTIONS, ndSection, 'Section')}</div>
            <div id="nd-head" class="page-head"></div>
            <div class="section" data-section="summary">
                <div id="nd-tiles" class="tiles kpi-tiles"></div>
                <div class="panel"><div class="panel-body">
                    <div class="chart-title"><span>History</span>${chipsHtml('nd-tf', TIMEFRAMES, ndTimeframe, 'Period')}</div>
                    <div class="stats-grid">
                        <div><div class="chart-label">CPU and IO wait</div><div id="nd-cpu" class="chart"></div></div>
                        <div><div class="chart-label">Memory</div><div id="nd-mem" class="chart"></div></div>
                        <div><div class="chart-label">Load average</div><div id="nd-load" class="chart"></div></div>
                        <div><div class="chart-label">Network</div><div id="nd-net" class="chart"></div></div>
                    </div>
                </div></div>
                <div class="dash-grid">
                    ${panel('Hardware', '', '<div id="nd-hw"></div>')}
                    ${panel('Software', '', '<div id="nd-sw"></div>')}
                </div>
                ${panel('Guests on this node', '', '<div id="nd-guests" class="table-wrap"></div>')}
            </div>
            <div class="section" data-section="disks">
                ${panel('Disks', 'SMART health and SSD wear, as Proxmox reads them', '<div id="nd-disks" class="table-wrap"></div>')}
                ${panel('ZFS pools', '', '<div id="nd-zfs" class="table-wrap"></div>')}
                ${panel('Storage on this node', '', '<div id="nd-storage" class="table-wrap"></div>')}
            </div>
            <div class="section" data-section="system">
                ${panel('Updates', '<span id="nd-upd-hint"></span>', '<div id="nd-updates" class="table-wrap"></div>')}
                <div class="dash-grid">
                    ${panel('Services', '', '<div id="nd-services" class="table-wrap"></div>')}
                    <div class="dash-stack">
                        ${panel('Subscription', '', '<div id="nd-sub"></div>')}
                        ${panel('Replication', 'jobs this node runs', '<div id="nd-repl" class="table-wrap"></div>')}
                    </div>
                </div>
            </div>
            <div class="section" data-section="tasks">
                ${panel('Tasks', 'the cluster\'s recent tasks on this node', '<div id="nd-tasks" class="table-wrap"></div>')}
            </div>`;
        bindChips('nd-pick', (key) => { ndNode = key; store('nd_node', key); setSub(key); renderNodes(true); });
        bindChips('nd-sections', (key) => { ndSection = key; store('nd_section', key); markNdSection(); renderNodes(false); });
        bindChips('nd-tf', (key) => { ndTimeframe = key; store('nd_tf', key); loadNdRrd(true); });
        markNdSection();
    }
    renderNdHead(n);
    if (ndSection === 'summary') {
        renderNdTiles(n);
        renderNdHardware(n);
        renderNdGuests(n);
        loadNdRrd(false);
    } else if (ndSection === 'disks') {
        renderNdDisks(n);
    } else if (ndSection === 'system') {
        renderNdSystem(n);
    } else {
        const list = ((app.status && app.status.tasks) || []).filter(t => t.node === n.name);
        document.getElementById('nd-tasks').innerHTML = table(TASK_HEAD, taskRows(list), {cls: 'hover', empty: 'No recent tasks on this node'});
    }
}

function markNdSection() {
    document.querySelectorAll('#tab-nodes .section').forEach(s => s.classList.toggle('active', s.dataset.section === ndSection));
}

function renderNdHead(n) {
    const st = n.status || {};
    const sub = n.conf && n.conf.subscription;
    const boot = st['boot-info'] || {};
    document.getElementById('nd-head').innerHTML = `
        <h1>${escapeHtml(n.name)}</h1>
        <div class="badges">
            ${n.online ? badge('ONLINE', 'ok') : badge('OFFLINE', 'bad')}
            ${n.local ? badge('this app reads through it', 'mode', 'The node whose API this app uses right now') : ''}
            ${sub && sub.status === 'active' ? badge('SUBSCRIBED', 'ok', sub.productname || '') : ''}
            ${boot.mode ? badge(boot.mode === 'efi' ? (boot.secureboot ? 'UEFI · SECURE BOOT' : 'UEFI') : 'LEGACY BIOS', 'mode') : ''}
        </div>
        <span class="meta">${escapeHtml(n.ip)}${n.uptime ? ` · up ${escapeHtml(fmtUptime(n.uptime))}` : ''}${nodeVersion(n) ? ` · PVE ${escapeHtml(nodeVersion(n))}` : ''}</span>`;
}

function renderNdTiles(n) {
    const st = n.status;
    const el = document.getElementById('nd-tiles');
    if (!n.online || !st) { el.innerHTML = tile('Status', 'offline', 'no live numbers', 'bad'); return; }
    const mem = st.memory || {}, swap = st.swap || {}, root = st.rootfs || {};
    const load = st.loadavg || [];
    const threads = (st.cpuinfo || {}).cpus || n.maxcpu;
    const mine = guests({noTemplates: true}).filter(g => g.node === n.name);
    const assigned = mine.filter(g => g.status === 'running').reduce((a, g) => a + (g.maxcpu || 0), 0);
    const memAssigned = mine.filter(g => g.status === 'running').reduce((a, g) => a + (g.maxmem || 0), 0);
    el.innerHTML = [
        tile('CPU', escapeHtml(plainPct(st.cpu)), `${num(threads)} threads · ${num(assigned)} vCPU` + meter(st.cpu, false), usageClass(st.cpu) === 'bad' ? 'bad' : ''),
        tile('IO wait', escapeHtml(plainPct(st.wait, 2)), 'waiting on disks', st.wait > 0.1 ? 'warn' : ''),
        tile('Load', escapeHtml(load[0] || '–'), `5 min ${escapeHtml(load[1] || '–')} · 15 min ${escapeHtml(load[2] || '–')}`, threads && Number(load[0]) > threads ? 'warn' : ''),
        tile('Memory', bytesHtml(mem.used), `of ${fmtBytes(mem.total)}` + meter(mem.total ? mem.used / mem.total : null, false)),
        tile('Swap', bytesHtml(swap.used), swap.total ? `of ${fmtBytes(swap.total)}` + meter(swap.used / swap.total, false) : 'none'),
        tile('Root disk', bytesHtml(root.used), `of ${fmtBytes(root.total)}` + meter(root.total ? root.used / root.total : null, false)),
        tile('KSM shared', bytesHtml((st.ksm || {}).shared || 0), 'merged by KSM'),
        tile('Guests', `${mine.filter(g => g.status === 'running').length}<small> / ${mine.length}</small>`, `${fmtBytes(memAssigned)} assigned`),
    ].join('');
}

function renderNdHardware(n) {
    const st = n.status || {};
    const cpu = st.cpuinfo || {};
    const boot = st['boot-info'] || {};
    const kernel = st['current-kernel'] || {};
    document.getElementById('nd-hw').innerHTML = !st.cpuinfo ? '<div class="empty-state">The node is offline</div>' : [
        infoRow('CPU', escapeHtml(cpu.model || '–')),
        infoRow('Sockets · cores · threads', `${num(cpu.sockets)} · ${num(cpu.cores)} · ${num(cpu.cpus)}`),
        infoRow('Clock', cpu.mhz ? `${num(Number(cpu.mhz), 0)} MHz` : '–'),
        infoRow('Virtualisation', cpu.hvm === '1' || cpu.hvm === 1 ? 'available' : '<span class="neg">not available</span>'),
        infoRow('Memory', escapeHtml(fmtBytes((st.memory || {}).total))),
        infoRow('Boot', boot.mode ? `${boot.mode === 'efi' ? 'UEFI' : 'legacy BIOS'}${boot.mode === 'efi' ? ` · secure boot ${boot.secureboot ? 'on' : 'off'}` : ''}` : '–'),
        infoRow('Architecture', escapeHtml(kernel.machine || '–')),
    ].join('');
    const disks = (n.conf && n.conf.disks) || [];
    document.getElementById('nd-sw').innerHTML = [
        infoRow('Proxmox VE', escapeHtml(st.pveversion || '–')),
        infoRow('Kernel', escapeHtml(kernel.release || (st.kversion || '').split(' ')[1] || '–')),
        infoRow('Uptime', escapeHtml(fmtUptime(n.uptime))),
        infoRow('Node ID', escapeHtml(n.nodeid || '–')),
        infoRow('Address', escapeHtml(n.ip || '–')),
        infoRow('Disks', disks.length ? `${disks.length} · ${escapeHtml(fmtBytes(disks.reduce((a, d) => a + (d.size || 0), 0)))}` : '–'),
        infoRow('Updates', n.conf && Array.isArray(n.conf.updates) ? (n.conf.updates.length ? `<span class="warn-text">${n.conf.updates.length} available</span>` : 'up to date') : '<span class="muted">not readable</span>'),
    ].join('');
}

function renderNdGuests(n) {
    const rows = guests().filter(g => g.node === n.name).map(g => `<tr onclick="openGuest(${g.vmid})">
        ${td(guestLabel(g))}${td(guestState(g))}
        ${td(g.status === 'running' ? meter(g.cpu, `${plainPct(g.cpu)}`, {title: `${g.maxcpu} vCPU`}) : '–')}
        ${td(g.status === 'running' ? meter(g.maxmem ? g.mem / g.maxmem : null, `${fmtBytes(g.mem)} / ${fmtBytes(g.maxmem)}`, {cls: 'stack'}) : `<span class="muted">${escapeHtml(fmtBytes(g.maxmem))}</span>`)}
        ${td(escapeHtml(fmtUptime(g.uptime)), 'n')}
        ${td(tagsHtml(g), 'wrap-cell')}
    </tr>`);
    document.getElementById('nd-guests').innerHTML = table([{label: 'Guest'}, {label: 'State'}, {label: 'CPU'}, {label: 'Memory'}, {label: 'Uptime', n: true}, {label: 'Tags'}], rows, {cls: 'hover', empty: 'No guests on this node'});
}

function renderNdDisks(n) {
    const c = n.conf;
    if (!c) {
        ['nd-disks', 'nd-zfs'].forEach(id => { document.getElementById(id).innerHTML = '<div class="empty-state">Reading the configuration…</div>'; });
    } else {
        const rows = (c.disks || []).map(d => {
            const health = String(d.health || '').toUpperCase();
            const wear = typeof d.wearout === 'number' ? d.wearout : null;
            return `<tr>
                ${td(`<span class="name mono">${escapeHtml(d.devpath)}</span><span class="sub">${escapeHtml(d.serial || '')}</span>`)}
                ${td(badge((d.type || '').toUpperCase() || 'DISK', 'mode'))}
                ${td(escapeHtml(d.model || d.vendor || ''))}
                ${td(escapeHtml(fmtBytes(d.size)), 'n')}
                ${td(escapeHtml(d.used || 'unused'), d.used ? '' : 'muted')}
                ${td(health === 'PASSED' || health === 'OK' ? stateLabel('ok', health) : health ? stateLabel('bad', health) : '<span class="muted">–</span>')}
                ${td(wear === null ? '<span class="muted">–</span>' : meter(wear / 100, `${wear}%`, {warn: 2, bad: 2, title: 'life left', cls: wear <= 20 ? 'low' : ''}))}
            </tr>`;
        });
        document.getElementById('nd-disks').innerHTML = permissionNote(`disks:${n.name}`, 'Disks') + table([
            {label: 'Device'}, {label: 'Type'}, {label: 'Model'}, {label: 'Size', n: true}, {label: 'Used by'}, {label: 'SMART'}, {label: 'Life left', title: 'SSD wear: 100% is new'},
        ], rows, {empty: 'No disks reported'});
        const zrows = (c.zfs || []).map(z => `<tr>
            ${td(`<span class="name">${escapeHtml(z.name)}</span>`)}
            ${td(stateLabel(z.health === 'ONLINE' ? 'ok' : 'bad', z.health || '?'))}
            ${td(meter(z.size ? z.alloc / z.size : null, `${fmtBytes(z.alloc)} / ${fmtBytes(z.size)}`, {cls: 'stack', warn: 0.7, bad: 0.8}))}
            ${td(escapeHtml(fmtBytes(z.free)), 'n')}
            ${td(`${num(z.frag)}%`, 'n')}
            ${td(`${num(z.dedup, 2)}×`, 'n')}
        </tr>`);
        document.getElementById('nd-zfs').innerHTML = table([{label: 'Pool'}, {label: 'Health'}, {label: 'Allocated'}, {label: 'Free', n: true}, {label: 'Fragmentation', n: true}, {label: 'Dedup', n: true}],
            zrows, {empty: 'No ZFS pools on this node'});
    }
    const srows = resources().filter(r => r.type === 'storage' && r.node === n.name).map(s => `<tr>
        ${td(`<span class="name">${escapeHtml(s.storage)}</span><span class="sub">${escapeHtml(s.plugintype || '')}${s.shared ? ' · shared' : ''}</span>`)}
        ${td(escapeHtml(String(s.content || '').split(',').join(', ')), 'wrap-cell muted')}
        ${td(s.status === 'available' ? meter(s.maxdisk ? s.disk / s.maxdisk : null, `${fmtBytes(s.disk)} / ${fmtBytes(s.maxdisk)}`, {cls: 'stack'}) : stateLabel('bad', s.status || '?'))}
    </tr>`);
    document.getElementById('nd-storage').innerHTML = table([{label: 'Storage'}, {label: 'Content'}, {label: 'Used'}], srows, {empty: 'No storage'});
}

function renderNdSystem(n) {
    const c = n.conf;
    if (!c) {
        ['nd-updates', 'nd-services', 'nd-sub', 'nd-repl'].forEach(id => { document.getElementById(id).innerHTML = '<div class="empty-state">Reading the configuration…</div>'; });
        return;
    }
    const upd = c.updates;
    document.getElementById('nd-upd-hint').textContent = Array.isArray(upd) ? (upd.length ? `${upd.length} packages` : 'up to date') : '';
    document.getElementById('nd-updates').innerHTML = !Array.isArray(upd)
        ? (permissionNote(`updates:${n.name}`, 'Updates') || '<div class="empty-state">Not readable</div>')
        : table([{label: 'Package'}, {label: 'Installed'}, {label: 'New'}, {label: 'Origin'}, {label: 'Description'}], upd.map(p => `<tr>
            ${td(`<span class="name mono">${escapeHtml(p.Package)}</span>`)}${td(escapeHtml(p.OldVersion || ''), 'mono muted')}${td(escapeHtml(p.Version || ''), 'mono')}
            ${td(escapeHtml(p.Origin || ''))}${td(escapeHtml(p.Title || ''), 'wrap-cell muted')}</tr>`),
        {empty: 'Up to date. The list is as fresh as the node\'s last "apt update" (Proxmox runs it daily).'});
    const svc = (c.services || []).slice().sort((a, b) => (a.state === 'running') - (b.state === 'running') || a.name.localeCompare(b.name));
    document.getElementById('nd-services').innerHTML = permissionNote(`services:${n.name}`, 'Services') + table([{label: 'Service'}, {label: 'State'}, {label: 'Enabled'}],
        svc.map(s => `<tr>${td(`<span class="name mono">${escapeHtml(s.name)}</span><span class="sub">${escapeHtml(s.desc || '')}</span>`)}
            ${td(stateLabel(s.state === 'running' ? 'ok' : s['active-state'] === 'failed' ? 'bad' : '', s['active-state'] === 'failed' ? 'failed' : s.state || '?'))}
            ${td(escapeHtml(s['unit-state'] || ''), 'muted')}</tr>`), {empty: 'No services reported'});
    const sub = c.subscription || {};
    document.getElementById('nd-sub').innerHTML = [
        infoRow('Status', sub.status === 'active' ? stateLabel('ok', 'active') : escapeHtml(sub.status === 'notfound' ? 'no subscription' : sub.status || '–')),
        sub.productname ? infoRow('Product', escapeHtml(sub.productname)) : '',
        sub.nextduedate ? infoRow('Next due', escapeHtml(sub.nextduedate)) : '',
        sub.status !== 'active' ? infoRow('Repository', '<span class="muted">use the no-subscription repository for updates</span>') : '',
    ].join('');
    const repl = c.replication || [];
    document.getElementById('nd-repl').innerHTML = table([{label: 'Job'}, {label: 'Target'}, {label: 'Last sync', n: true}, {label: 'Took', n: true}, {label: 'State'}], repl.map(r => {
        const g = guestById(r.guest);
        return `<tr>${td(g ? guestLink(g) : escapeHtml(r.id))}${td(escapeHtml(r.target))}${td(escapeHtml(fmtAgo(r.last_sync)), 'n')}${td(missing(r.duration) ? '–' : `${num(r.duration, 1)} s`, 'n')}
            ${td(r.fail_count || r.error ? `<span class="state bad" title="${attr(r.error || '')}">failed ${num(r.fail_count)}×</span>` : stateLabel('ok', r.state || 'OK'))}</tr>`;
    }), {empty: 'No replication from this node'});
}

// ---------------------------------------------------------------------
async function loadNdRrd(force) {
    const key = `${ndNode}:${ndTimeframe}`;
    const maxAge = ndTimeframe === 'hour' ? 30 : 120;
    if (!force && ndRrd && ndRrdKey === key && Date.now() / 1000 - ndRrdAt < maxAge) { renderNdRrd(); return; }
    ndRrdKey = key;
    ndRrdAt = Date.now() / 1000;
    const n = nodeByName(ndNode);
    if (!n || !n.online) { ['nd-cpu', 'nd-mem', 'nd-load', 'nd-net'].forEach(id => emptyChart(document.getElementById(id), 'The node is offline')); return; }
    try {
        const data = await api(`rrd?node=${encodeURIComponent(ndNode)}&timeframe=${ndTimeframe}`);
        if (ndRrdKey !== key) return;
        ndRrd = data.rows.filter(r => !missing(r.cpu));
        renderNdRrd();
    } catch (e) {
        ['nd-cpu', 'nd-mem', 'nd-load', 'nd-net'].forEach(id => emptyChart(document.getElementById(id), e.message));
    }
}

function renderNdRrd() {
    const rows = ndRrd || [];
    const el = id => document.getElementById(id);
    if (!el('nd-cpu')) return;
    const x = rows.map(r => r.time);
    const col = k => rows.map(r => missing(r[k]) ? null : r[k]);
    const base = {x, xTicks: x.length > 1 ? timeAxisTicks(x[0], x[x.length - 1], 4) : [], height: 150, zero: true, formatX: timeFmt(ndTimeframe)};
    lineChart(el('nd-cpu'), {...base, max: 0.0001, series: [{name: 'CPU', color: color('series-1'), values: col('cpu'), area: true}, {name: 'IO wait', color: color('series-2'), values: col('iowait'), width: 1.5}],
        formatY: v => plainPct(v), formatTick: v => `${Math.round(v * 100)}%`});
    lineChart(el('nd-mem'), {...base, series: [{name: 'Used', color: color('series-3'), values: col('memused'), area: true}, {name: 'Total', color: color('idle'), values: col('memtotal'), dash: '4 4', width: 1.5},
        {name: 'Swap', color: color('series-5'), values: col('swapused'), width: 1.5}], formatY: v => fmtBytes(v), formatTick: v => fmtBytes(v).replace(/\.\d /, ' ')});
    lineChart(el('nd-load'), {...base, series: [{name: 'Load', color: color('series-4'), values: col('loadavg'), area: true}],
        hlines: rows.length && rows[0].maxcpu ? [{at: rows[0].maxcpu, label: `${rows[0].maxcpu} threads`}] : [], formatY: v => num(v, 2)});
    lineChart(el('nd-net'), {...base, series: [{name: 'In', color: color('series-1'), values: col('netin'), area: true}, {name: 'Out', color: color('series-4'), values: col('netout'), width: 1.5}],
        formatY: v => fmtRate(v), formatTick: v => fmtRate(v).replace(/(\d)\.\d/, '$1')});
}

onTab('nodes', showNodes, () => renderNodes(false));
