// Proxmox dashboard - Guests: every VM and container, and a page per guest.

const gsFilter = {
    kind: stored('gs_kind', 'all'), state: stored('gs_state', 'all'), node: stored('gs_node', ''), text: '',
    sort: stored('gs_sort', 'vmid'), dir: stored('gs_dir', 'asc'),
};
let gsBackups = null;
let gsBackupsAt = 0;
let gdVmid = '';
let gdData = null;
let gdTimeframe = stored('gd_tf', 'hour');
let gdRrd = null;
let gdRrdKey = '';
let gdRrdAt = 0;

function showGuests(sub) {
    gdVmid = sub && guestById(sub) ? String(sub) : '';
    const el = document.getElementById('tab-guests');
    delete el.dataset.view;
    renderGuests();
    loadGsBackups(false);
    if (gdVmid) loadGuestDetails(true);
}

function renderGuests() {
    const el = document.getElementById('tab-guests');
    if (!app.status || !app.status.connected) {
        el.innerHTML = '<div class="empty-state">Waiting for the cluster…</div>';
        delete el.dataset.view;
        return;
    }
    if (gdVmid) renderGuestPage(el);
    else renderGuestList(el);
}

// =====================================================================
// The list
// =====================================================================
function renderGuestList(el) {
    if (el.dataset.view !== 'list') {
        el.dataset.view = 'list';
        el.innerHTML = `
            <div id="gs-tiles" class="tiles kpi-tiles"></div>
            <div class="filters">
                <input class="input" id="gs-search" type="search" placeholder="Name, ID, tag, VLAN, IP or MAC" aria-label="Search guests" value="${attr(gsFilter.text)}">
                ${chipsHtml('gs-kind', [{key: 'all', label: 'All'}, {key: 'qemu', label: 'VMs'}, {key: 'lxc', label: 'Containers'}], gsFilter.kind, 'Type')}
                ${chipsHtml('gs-state', [{key: 'all', label: 'Any state'}, {key: 'running', label: 'Running'}, {key: 'stopped', label: 'Stopped'}], gsFilter.state, 'State')}
                <select class="select" id="gs-node" aria-label="Node"></select>
                <span class="range-text" id="gs-count"></span>
            </div>
            <div class="panel"><div class="panel-body"><div id="gs-table" class="table-wrap"></div></div></div>`;
        document.getElementById('gs-search').addEventListener('input', (e) => { gsFilter.text = e.target.value; renderGsTable(); });
        bindChips('gs-kind', (k) => { gsFilter.kind = k; store('gs_kind', k); renderGsTable(); });
        bindChips('gs-state', (k) => { gsFilter.state = k; store('gs_state', k); renderGsTable(); });
        document.getElementById('gs-node').addEventListener('change', (e) => { gsFilter.node = e.target.value; store('gs_node', e.target.value); renderGsTable(); });
        document.getElementById('gs-table').addEventListener('click', (e) => {
            const th = e.target.closest('th[data-sort]');
            if (!th) return;
            const key = th.dataset.sort;
            gsFilter.dir = gsFilter.sort === key && gsFilter.dir === 'asc' ? 'desc' : 'asc';
            gsFilter.sort = key;
            store('gs_sort', key); store('gs_dir', gsFilter.dir);
            renderGsTable();
        });
    }
    const select = document.getElementById('gs-node');
    const options = `<option value="">All nodes</option>${nodes().map(n => `<option value="${attr(n.name)}"${n.name === gsFilter.node ? ' selected' : ''}>${escapeHtml(n.name)}</option>`).join('')}`;
    if (select.dataset.key !== options) { select.dataset.key = options; select.innerHTML = options; }
    renderGsTiles();
    renderGsTable();
}

function renderGsTiles() {
    const all = guests();
    const gs = all.filter(g => !g.template);
    const online = nodes().filter(n => n.online);
    const threads = online.reduce((a, n) => a + (n.maxcpu || 0), 0);
    const memory = online.reduce((a, n) => a + (n.maxmem || 0), 0);
    const running = gs.filter(g => g.status === 'running');
    const vcpu = running.reduce((a, g) => a + (g.maxcpu || 0), 0);
    const vmem = running.reduce((a, g) => a + (g.maxmem || 0), 0);
    const disk = gs.reduce((a, g) => a + (g.maxdisk || 0), 0);
    const onboot = gs.filter(g => g.conf && g.conf.onboot).length;
    document.getElementById('gs-tiles').innerHTML = [
        tile('VMs', `${gs.filter(g => g.type === 'qemu' && g.status === 'running').length}<small> / ${gs.filter(g => g.type === 'qemu').length}</small>`, 'running'),
        tile('Containers', `${gs.filter(g => g.type === 'lxc' && g.status === 'running').length}<small> / ${gs.filter(g => g.type === 'lxc').length}</small>`, 'running'),
        tile('vCPU', `${num(vcpu)}<small> on ${num(threads)} threads</small>`, threads ? `${num(vcpu / threads, 2)}× overcommit` : '', threads && vcpu / threads > 4 ? 'warn' : ''),
        tile('Memory assigned', bytesHtml(vmem), memory ? `${plainPct(vmem / memory, 0)} of ${fmtBytes(memory)} physical` : '', memory && vmem > memory ? 'warn' : ''),
        tile('Disks', bytesHtml(disk), 'provisioned size'),
        tile('Start at boot', `${num(onboot)}<small> / ${gs.length}</small>`, `${all.length - gs.length} templates`),
    ].join('');
}

// Every address of a guest that the app knows (only from the detail page's guest agent read; else none)
function guestSearchText(g) {
    const nics = (g.conf && g.conf.nics) || [];
    return [g.vmid, g.name, g.node, g.tags, g.pool, ...nics.map(n => `${n.mac} ${n.bridge} vlan${n.tag || ''} ${n.tag || 'untagged'} ${n.ip || ''}`)].join(' ').toLowerCase();
}

function lastBackup(vmid) {
    if (!gsBackups) return null;
    return gsBackups.filter(b => String(b.vmid) === String(vmid)).sort((a, b) => b.ctime - a.ctime)[0] || null;
}

const GS_SORT = {
    vmid: g => g.vmid, name: g => guestName(g).toLowerCase(), node: g => g.node, state: g => g.status,
    cpu: g => (g.cpu || 0) * (g.maxcpu || 1), mem: g => g.mem || 0, maxmem: g => g.maxmem || 0, disk: g => g.maxdisk || 0,
    uptime: g => g.uptime || 0, net: g => (g.netin || 0) + (g.netout || 0), backup: g => (lastBackup(g.vmid) || {}).ctime || 0,
};

function renderGsTable() {
    const text = gsFilter.text.trim().toLowerCase();
    let list = guests().filter(g => (gsFilter.kind === 'all' || g.type === gsFilter.kind)
        && (gsFilter.state === 'all' || (gsFilter.state === 'running' ? g.status === 'running' : g.status !== 'running'))
        && (!gsFilter.node || g.node === gsFilter.node)
        && (!text || text.split(/\s+/).every(w => guestSearchText(g).includes(w))));
    const key = GS_SORT[gsFilter.sort] || GS_SORT.vmid;
    list = list.sort((a, b) => {
        const x = key(a), y = key(b);
        const c = typeof x === 'string' ? x.localeCompare(y, undefined, {numeric: true}) : x - y;
        return gsFilter.dir === 'asc' ? c : -c;
    });
    document.getElementById('gs-count').textContent = `${list.length} of ${guests().length}`;
    const head = [
        {label: 'Guest', sort: 'vmid'}, {label: 'Node', sort: 'node'}, {label: 'State', sort: 'state'}, {label: 'CPU', sort: 'cpu'},
        {label: 'Memory', sort: 'mem'}, {label: 'Disk', sort: 'disk', n: true}, {label: 'Network'}, {label: 'Firewall', title: 'the guest\'s firewall, and whether its NICs use it'},
        {label: 'Uptime', sort: 'uptime', n: true}, {label: 'Last backup', sort: 'backup', n: true}, {label: 'Tags'},
    ].map(h => h.sort === gsFilter.sort ? {...h, sorted: gsFilter.dir} : h);
    const rows = list.map(g => {
        const running = g.status === 'running';
        const nics = (g.conf && g.conf.nics) || [];
        const fw = g.conf && g.conf.firewall && g.conf.firewall.options;
        const fwOn = fw && Number(fw.enable);
        const nicsOn = nics.filter(n => n.firewall).length;
        const b = lastBackup(g.vmid);
        const age = b ? Date.now() / 1000 - b.ctime : null;
        return `<tr onclick="openGuest(${g.vmid})" class="${g.template ? 'off' : ''}">
            ${td(guestLabel(g))}
            ${td(escapeHtml(g.node))}
            ${td(guestState(g) + (g.hastate ? ' <span class="badge mode" title="managed by HA">HA</span>' : ''))}
            ${td(running ? meter(g.cpu, plainPct(g.cpu), {title: `${g.maxcpu} vCPU`}) : `<span class="muted">${num(g.maxcpu)} vCPU</span>`)}
            ${td(running ? meter(g.maxmem ? g.mem / g.maxmem : null, `${fmtBytes(g.mem)} / ${fmtBytes(g.maxmem)}`, {cls: 'stack'}) : `<span class="muted">${escapeHtml(fmtBytes(g.maxmem))}</span>`)}
            ${td(g.type === 'lxc' && g.disk ? `${escapeHtml(fmtBytes(g.disk))}<span class="sub">of ${escapeHtml(fmtBytes(g.maxdisk))}</span>` : escapeHtml(fmtBytes(g.maxdisk)), 'n')}
            ${td(nics.length ? nics.map(n => `${vlanChip(n.tag)}`).join(' ') : (g.conf ? '<span class="muted">none</span>' : '–'))}
            ${td(!g.conf ? '–' : `<span class="fw-flag ${fwOn ? 'on' : 'off'}">${fwOn ? 'ON' : 'OFF'}</span>${nics.length ? ` <span class="muted" title="NICs with firewall=1">${nicsOn}/${nics.length}</span>` : ''}`)}
            ${td(escapeHtml(fmtUptime(g.uptime)), 'n')}
            ${td(gsBackups === null ? '<span class="muted">…</span>' : b ? `<span class="${age > 7 * 86400 ? 'neg' : age > 2 * 86400 ? 'warn-text' : ''}" title="${attr(fmtDateTime(b.ctime))} on ${attr(b.storage)}">${escapeHtml(fmtAgo(b.ctime))}</span>` : (g.template ? '' : '<span class="neg">none</span>'), 'n')}
            ${td(tagsHtml(g), 'wrap-cell')}
        </tr>`;
    });
    document.getElementById('gs-table').innerHTML = table(head, rows, {cls: 'hover', empty: 'No guest matches'});
}

async function loadGsBackups(force) {
    if (!force && gsBackups && Date.now() / 1000 - gsBackupsAt < 120) return;
    gsBackupsAt = Date.now() / 1000;
    try {
        gsBackups = (await api('backups')).backups;
    } catch (e) {
        gsBackups = [];
    }
    if (activeTab === 'guests') renderGuests();
    else if (activeTab === 'storage') renderStorage();
}

// =====================================================================
// One guest
// =====================================================================
async function loadGuestDetails(fresh) {
    const vmid = gdVmid;
    if (fresh) gdData = null;
    try {
        const data = await api(`guest/${vmid}`);
        if (vmid !== gdVmid) return;
        gdData = {...data, _at: Date.now() / 1000};
    } catch (e) {
        if (vmid !== gdVmid) return;
        gdData = {error: e.message, _at: Date.now() / 1000};
    }
    if (activeTab === 'guests') renderGuests();
}

function renderGuestPage(el) {
    const g = guestById(gdVmid);
    if (!g) { el.innerHTML = '<div class="empty-state">This guest is gone</div>'; return; }
    if (el.dataset.view !== 'guest:' + gdVmid) {
        el.dataset.view = 'guest:' + gdVmid;
        el.innerHTML = `
            <a class="back-link" href="#guests" onclick="switchTab('guests');return false">← All guests</a>
            <div id="gd-head" class="page-head" style="margin-top:6px"></div>
            <div id="gd-notes"></div>
            <div id="gd-tiles" class="tiles kpi-tiles"></div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>History</span>${chipsHtml('gd-tf', TIMEFRAMES, gdTimeframe, 'Period')}</div>
                <div class="stats-grid">
                    <div><div class="chart-label">CPU</div><div id="gd-cpu" class="chart"></div></div>
                    <div><div class="chart-label">Memory</div><div id="gd-mem" class="chart"></div></div>
                    <div><div class="chart-label">Network</div><div id="gd-net" class="chart"></div></div>
                    <div><div class="chart-label">Disk IO</div><div id="gd-io" class="chart"></div></div>
                </div>
            </div></div>
            ${panel('Network devices', 'addresses from inside the guest when it tells', '<div id="gd-nics" class="table-wrap"></div>')}
            <div class="dash-grid">
                ${panel('Disks', '', '<div id="gd-disks" class="table-wrap"></div>')}
                ${panel('Firewall', '<a href="#firewall/flow" onclick="switchTab(\'firewall\', \'flow\');return false">open the firewall →</a>', '<div id="gd-fw"></div>')}
            </div>
            <div class="dash-grid">
                ${panel('Snapshots', '', '<div id="gd-snaps" class="table-wrap"></div>')}
                ${panel('Backups', '<span id="gd-backup-hint"></span>', '<div id="gd-backups" class="table-wrap"></div>')}
            </div>
            <div class="dash-grid">
                ${panel('Configuration', '', '<div id="gd-config"></div>')}
                ${panel('Notes', 'the guest\'s description in Proxmox', '<div id="gd-desc"></div>')}
            </div>`;
        bindChips('gd-tf', (key) => { gdTimeframe = key; store('gd_tf', key); loadGdRrd(true); });
    }
    renderGdHead(g);
    renderGdTiles(g);
    renderGdBody(g);
    loadGdRrd(false);
}

function renderGdHead(g) {
    const pool = g.pool ? badge(`pool ${g.pool}`, 'mode') : '';
    document.getElementById('gd-head').innerHTML = `
        <h1>${guestLabel(g)}</h1>
        <div class="badges">${guestState(g)} ${g.hastate ? badge(`HA ${g.hastate}`, 'mode', 'managed by Proxmox HA') : ''} ${pool} ${tagsHtml(g)}</div>
        <span class="meta">on <a href="#nodes/${attr(g.node)}" onclick="openNode('${attr(g.node)}');return false">${escapeHtml(g.node)}</a>${g.uptime ? ` · up ${escapeHtml(fmtUptime(g.uptime))}` : ''}</span>
        <div class="controls">${powerButtons(g)}</div>`;
}

function renderGdTiles(g) {
    const running = g.status === 'running';
    const cur = (gdData && gdData.current) || {};
    document.getElementById('gd-tiles').innerHTML = [
        tile('CPU', running ? escapeHtml(plainPct(g.cpu)) : '–', `${num(g.maxcpu)} vCPU` + (running ? meter(g.cpu, false) : '')),
        tile('Memory', running ? bytesHtml(g.mem) : '–', `of ${fmtBytes(g.maxmem)}` + (running ? meter(g.maxmem ? g.mem / g.maxmem : null, false) : '')),
        tile(g.type === 'lxc' ? 'Root disk' : 'Disks', g.type === 'lxc' && g.disk ? bytesHtml(g.disk) : bytesHtml(g.maxdisk), g.type === 'lxc' && g.disk ? `of ${fmtBytes(g.maxdisk)}` + meter(g.disk / g.maxdisk, false) : 'provisioned'),
        tile('Received', bytesHtml(g.netin), 'since start'),
        tile('Sent', bytesHtml(g.netout), 'since start'),
        tile('Read / written', running && (cur.diskread || g.diskread) ? `${escapeHtml(fmtBytes(cur.diskread || g.diskread))}<small> / ${escapeHtml(fmtBytes(cur.diskwrite || g.diskwrite))}</small>` : '–', 'since start'),
    ].join('');
}

function renderGdBody(g) {
    const d = gdData;
    const loading = '<div class="empty-state">Reading…</div>';
    const ids = ['gd-nics', 'gd-disks', 'gd-fw', 'gd-snaps', 'gd-config', 'gd-desc'];
    if (!d) { ids.forEach(id => { document.getElementById(id).innerHTML = loading; }); renderGdBackups(g); return; }
    if (d.error) { document.getElementById('gd-notes').innerHTML = setupHint(escapeHtml(d.error), 'bad'); return; }
    const notesHtml = [];
    if (d.pending && d.pending.length) {
        notesHtml.push(setupHint(`Pending changes, applied at the next ${g.type === 'qemu' ? 'power cycle' : 'restart'}: ${d.pending.map(p => `<span class="mono">${escapeHtml(p.key)}</span>${p.pending !== undefined ? ` → ${escapeHtml(p.pending)}` : p.delete ? ' (removed)' : ''}`).join(', ')}`, 'info'));
    }
    if (d.config && d.config.lock) notesHtml.push(setupHint(`Locked: <b>${escapeHtml(d.config.lock)}</b>`, 'warn'));
    document.getElementById('gd-notes').innerHTML = notesHtml.join('');

    // Addresses from the guest agent (VM) or the container, matched to a NIC by MAC
    const addrs = new Map();
    (d.addresses || []).forEach(a => {
        const macAddr = String(a['hardware-address'] || a.hwaddr || '').toLowerCase();
        const list = (a['ip-addresses'] || []).map(ip => `${ip['ip-address']}/${ip.prefix}`);
        if (a.inet) list.push(a.inet);
        if (a.inet6) list.push(a.inet6);
        if (macAddr && macAddr !== '00:00:00:00:00:00') addrs.set(macAddr, {name: a.name, list});
    });
    document.getElementById('gd-nics').innerHTML = table([
        {label: 'Device'}, {label: 'Model'}, {label: 'MAC'}, {label: 'Bridge'}, {label: 'VLAN'}, {label: 'Firewall'}, {label: 'Addresses'}, {label: 'Limits'},
    ], d.nics.map(n => {
        const inside = addrs.get(String(n.mac).toLowerCase());
        return `<tr class="${n.link_down ? 'off' : ''}">
            ${td(`<span class="name mono">${escapeHtml(n.key)}</span>${inside ? `<span class="sub">${escapeHtml(inside.name)}</span>` : n.name ? `<span class="sub">${escapeHtml(n.name)}</span>` : ''}`)}
            ${td(escapeHtml(n.model || ''))}${td(escapeHtml(n.mac), 'mono')}${td(escapeHtml(n.bridge), 'mono')}${td(vlanChip(n.tag) + (n.trunks ? ` <span class="muted mono">trunks ${escapeHtml(n.trunks)}</span>` : ''))}
            ${td(`<span class="fw-flag ${n.firewall ? 'on' : 'off'}">${n.firewall ? 'ON' : 'OFF'}</span>`)}
            ${td(inside ? inside.list.map(a => `<span class="mono">${escapeHtml(a)}</span>`).join('<br>') : n.ip ? `<span class="mono">${escapeHtml(n.ip)}</span>${n.gw ? `<span class="sub">gw ${escapeHtml(n.gw)}</span>` : ''}` : '<span class="muted">–</span>', 'wrap-cell')}
            ${td([n.rate ? `${escapeHtml(n.rate)} MB/s` : '', n.mtu ? `MTU ${escapeHtml(n.mtu)}` : '', n.queues ? `${escapeHtml(n.queues)} queues` : '', n.link_down ? 'link down' : ''].filter(Boolean).join(' · ') || '<span class="muted">–</span>')}
        </tr>`;
    }), {empty: 'No network devices'}) + (d.address_note ? `<p class="panel-sub" style="margin-top:8px">${escapeHtml(d.address_note)}</p>` : '');

    document.getElementById('gd-disks').innerHTML = table([{label: 'Device'}, {label: 'Storage'}, {label: 'Volume'}, {label: 'Size', n: true}, {label: 'Options'}], d.disks.map(x => `<tr>
        ${td(`<span class="name mono">${escapeHtml(x.key)}</span>`)}${td(escapeHtml(x.storage || (x.volume.startsWith('/') ? 'bind mount' : '')))}
        ${td(escapeHtml(x.volume), 'mono wrap-cell')}${td(x.cdrom ? 'CD/DVD' : escapeHtml(fmtBytes(x.size)), 'n')}
        ${td([x.mp ? `at ${escapeHtml(x.mp)}` : '', x.ssd === '1' ? 'SSD' : '', x.discard === 'on' ? 'discard' : '', x.iothread === '1' ? 'IO thread' : '', x.cache ? `cache ${escapeHtml(x.cache)}` : '', x.backup === '0' ? '<span class="warn-text">not in backups</span>' : '', x.replicate === '0' ? 'no replication' : ''].filter(Boolean).join(' · ') || '<span class="muted">–</span>')}
    </tr>`), {empty: 'No disks'});

    const fw = (g.conf && g.conf.firewall) || {options: {}, rules: []};
    const o = fw.options || {};
    const dc = app.conf && app.conf.firewall && app.conf.firewall.options;
    const nicsOn = d.nics.filter(n => n.firewall).length;
    const applies = Number(o.enable) && nicsOn && dc && Number(dc.enable);
    document.getElementById('gd-fw').innerHTML = [
        infoRow('Applies', applies ? stateLabel('ok', 'yes') : `<span class="state warn">no</span> <span class="muted">${!dc || !Number(dc.enable) ? 'the datacenter firewall is off' : !Number(o.enable) ? 'the guest\'s firewall is off' : 'no NIC has firewall=1'}</span>`),
        infoRow('Guest firewall', Number(o.enable) ? 'on' : 'off'),
        infoRow('NICs through it', `${nicsOn} of ${d.nics.length}`),
        infoRow('Policy in · out', `${escapeHtml(o.policy_in || 'DROP')} · ${escapeHtml(o.policy_out || 'ACCEPT')}`),
        infoRow('Rules', `${num((fw.rules || []).length)}${(fw.rules || []).filter(r => r.type === 'group').length ? ` · groups ${(fw.rules || []).filter(r => r.type === 'group').map(r => `<span class="mono">${escapeHtml(r.action)}</span>`).join(', ')}` : ''}`),
        infoRow('IP filter · MAC filter', `${Number(o.ipfilter) ? 'on' : 'off'} · ${o.macfilter === undefined || Number(o.macfilter) ? 'on' : 'off'}`),
    ].join('');

    const snaps = (d.snapshots || []).slice().sort((a, b) => (b.snaptime || 0) - (a.snaptime || 0));
    document.getElementById('gd-snaps').innerHTML = table([{label: 'Snapshot'}, {label: 'Taken', n: true}, {label: 'RAM'}, {label: 'Description'}], snaps.map(s => `<tr>
        ${td(`<span class="name mono">${escapeHtml(s.name)}</span>${s.parent ? `<span class="sub">after ${escapeHtml(s.parent)}</span>` : ''}`)}
        ${td(escapeHtml(s.snaptime ? `${fmtDateTime(s.snaptime)} · ${fmtAgo(s.snaptime)}` : '–'), 'n')}${td(s.vmstate ? 'with RAM' : '<span class="muted">disk only</span>')}
        ${td(escapeHtml(s.description || ''), 'wrap-cell muted')}</tr>`), {empty: 'No snapshots'})
        + (snaps.some(s => s.snaptime && Date.now() / 1000 - s.snaptime > 30 * 86400) ? '<p class="panel-sub" style="margin-top:8px">Old snapshots keep growing as the disk changes: remove the ones you no longer need.</p>' : '');

    const cfg = d.config || {};
    const keys = ['cores', 'sockets', 'cpu', 'cpulimit', 'cpuunits', 'memory', 'balloon', 'swap', 'bios', 'machine', 'ostype', 'arch', 'boot', 'scsihw', 'agent', 'onboot', 'startup', 'protection', 'unprivileged', 'features', 'hookscript', 'numa', 'vga', 'hostpci0', 'usb0', 'lock'];
    const label = {memory: 'memory (MiB)', balloon: 'balloon (MiB)', swap: 'swap (MiB)'};
    const rowsHtml = keys.filter(k => cfg[k] !== undefined && cfg[k] !== '').map(k => `<div class="k">${escapeHtml(label[k] || k)}</div><div class="v">${escapeHtml(cfg[k])}</div>`).join('');
    const raw = Object.keys(cfg).filter(k => k !== 'description' && k !== 'digest').sort().map(k => `${k}: ${cfg[k]}`).join('\n');
    document.getElementById('gd-config').innerHTML = `<div class="kv">${rowsHtml}</div>
        <details class="advanced" style="margin-top:12px"><summary>Whole configuration</summary><pre class="task-log" style="min-height:0">${escapeHtml(raw)}</pre></details>`;
    document.getElementById('gd-desc').innerHTML = cfg.description ? `<div class="guest-notes">${escapeHtml(cfg.description)}</div>` : '<div class="empty-state">No notes</div>';
    renderGdBackups(g);
}

function renderGdBackups(g) {
    const el = document.getElementById('gd-backups');
    if (!el) return;
    if (gsBackups === null) { el.innerHTML = '<div class="empty-state">Reading the backups…</div>'; return; }
    const list = gsBackups.filter(b => String(b.vmid) === String(g.vmid)).sort((a, b) => b.ctime - a.ctime);
    const notBacked = ((app.conf && app.conf.not_backed_up) || []).some(x => String(x.vmid) === String(g.vmid));
    document.getElementById('gd-backup-hint').innerHTML = notBacked ? '<span class="neg">in no backup job</span>' : list.length ? `latest ${escapeHtml(fmtAgo(list[0].ctime))}` : '';
    el.innerHTML = table([{label: 'Taken'}, {label: 'Storage'}, {label: 'Size', n: true}, {label: 'Verified'}, {label: 'Notes'}], list.slice(0, 20).map(b => {
        const v = b.verification && b.verification.state;
        return `<tr>${td(`${escapeHtml(fmtDateTime(b.ctime))}<span class="sub">${escapeHtml(fmtAgo(b.ctime))}</span>`)}${td(escapeHtml(b.storage))}${td(escapeHtml(fmtBytes(b.size)), 'n')}
            ${td(v === 'ok' ? stateLabel('ok', 'OK') : v ? stateLabel('bad', v) : '<span class="muted">–</span>')}
            ${td(`${b.protected ? '🔒 ' : ''}${escapeHtml(b.notes || '')}`, 'wrap-cell muted')}</tr>`;
    }), {empty: notBacked ? 'No backups, and no backup job includes this guest' : 'No backups found'});
}

async function loadGdRrd(force) {
    const key = `${gdVmid}:${gdTimeframe}`;
    const maxAge = gdTimeframe === 'hour' ? 30 : 120;
    if (!force && gdRrd && gdRrdKey === key && Date.now() / 1000 - gdRrdAt < maxAge) return;
    gdRrdKey = key;
    gdRrdAt = Date.now() / 1000;
    try {
        const data = await api(`rrd?vmid=${encodeURIComponent(gdVmid)}&timeframe=${gdTimeframe}`);
        if (gdRrdKey !== key) return;
        gdRrd = data.rows;
        renderGdRrd();
    } catch (e) {
        ['gd-cpu', 'gd-mem', 'gd-net', 'gd-io'].forEach(id => { const el = document.getElementById(id); if (el) emptyChart(el, e.message); });
    }
}

function renderGdRrd() {
    const rows = gdRrd || [];
    const el = id => document.getElementById(id);
    if (!el('gd-cpu')) return;
    const x = rows.map(r => r.time);
    const col = k => rows.map(r => missing(r[k]) ? null : r[k]);
    const base = {x, xTicks: x.length > 1 ? timeAxisTicks(x[0], x[x.length - 1], 4) : [], height: 150, zero: true, formatX: timeFmt(gdTimeframe), empty: 'Not running in this period'};
    lineChart(el('gd-cpu'), {...base, max: 0.0001, series: [{name: 'CPU', color: color('series-1'), values: col('cpu'), area: true}], formatY: v => plainPct(v), formatTick: v => `${Math.round(v * 100)}%`});
    lineChart(el('gd-mem'), {...base, series: [{name: 'Used', color: color('series-3'), values: col('mem'), area: true}, {name: 'Assigned', color: color('idle'), values: col('maxmem'), dash: '4 4', width: 1.5}],
        formatY: v => fmtBytes(v), formatTick: v => fmtBytes(v).replace(/\.\d /, ' ')});
    lineChart(el('gd-net'), {...base, series: [{name: 'In', color: color('series-1'), values: col('netin'), area: true}, {name: 'Out', color: color('series-4'), values: col('netout'), width: 1.5}],
        formatY: v => fmtRate(v), formatTick: v => fmtRate(v).replace(/(\d)\.\d/, '$1')});
    lineChart(el('gd-io'), {...base, series: [{name: 'Read', color: color('series-3'), values: col('diskread'), area: true}, {name: 'Write', color: color('series-2'), values: col('diskwrite'), width: 1.5}],
        formatY: v => fmtByteRate(v), formatTick: v => fmtBytes(v).replace(/\.\d /, ' ')});
}

onTab('guests', showGuests, () => {
    renderGuests();
    // A guest's state changes after a power action: read its details again now and then
    if (gdVmid && gdData && Date.now() / 1000 - gdData._at > 30) { gdData._at = Date.now() / 1000; loadGuestDetails(false); }
});
