// Proxmox dashboard - Network: the VLANs as lanes to drag guests between, a map of each node's
// interfaces, every interface in a table, and the SDN.

const NET_SECTIONS = [
    {key: 'vlans', label: 'VLANs'},
    {key: 'map', label: 'Map'},
    {key: 'ifaces', label: 'Interfaces'},
    {key: 'sdn', label: 'SDN'},
];
const netState = {
    section: stored('net_section', 'vlans'),
    mapNode: stored('net_map_node', 'all'),
    ifNode: stored('net_if_node', ''),
    search: '',
    hideStopped: stored('net_hide_stopped', '0') === '1',
    drag: null,          // the network device being dragged between lanes
    boxDrag: null,       // the box being dragged on the map
    layout: null,        // where the map's boxes were dragged to, from /api/layout
    sig: '',
};
if (!NET_SECTIONS.some(s => s.key === netState.section)) netState.section = 'vlans';

// =====================================================================
// Shared bits
// =====================================================================
const netBusy = () => Boolean(netState.drag || netState.boxDrag || document.getElementById('modal-root').children.length);
const netIfaces = node => (app.conf && app.conf.nodes && app.conf.nodes[node] && app.conf.nodes[node].network
    && app.conf.nodes[node].network.ifaces) || [];
const netIsBridge = i => i.type === 'bridge' || i.type === 'OVSBridge';
const netTagOf = t => missing(t) || t === '' ? null : Number(t);
const netWords = text => String(text || '').split(/\s+/).filter(Boolean);
const netComment = i => String(i.comments || i.comments6 || '').trim();

// The VLAN id of a VLAN interface: vlan-id, or the number after the last dot (vmbr0.10)
function netVlanOf(i) {
    if (!missing(i['vlan-id']) && i['vlan-id'] !== '') return Number(i['vlan-id']);
    const m = /\.(\d+)$/.exec(i.iface || '');
    return m && (i.type === 'vlan' || i.type === 'OVSIntPort') ? Number(m[1]) : null;
}
function netRawDevice(i) {
    if (i['vlan-raw-device']) return i['vlan-raw-device'];
    if (i.type === 'OVSIntPort' && i.ovs_bridge) return i.ovs_bridge;
    const m = /^(.+)\.\d+$/.exec(i.iface || '');
    return m ? m[1] : '';
}

// The same netN line with another tag; mirrors set_nic_tag in src/proxmox.py
function netSetTag(value, tag) {
    const props = String(value).split(',').filter(Boolean).map(p => {
        const at = p.indexOf('=');
        return at < 0 ? ['', p] : [p.slice(0, at), p.slice(at + 1)];
    });
    let at = props.findIndex(([k]) => k === 'tag');
    if (at >= 0) {
        props.splice(at, 1);
    } else if (tag !== null) {
        const fw = props.findIndex(([k]) => k === 'firewall');
        const br = props.findIndex(([k]) => k === 'bridge');
        at = fw >= 0 ? fw + 1 : br >= 0 ? br + 1 : props.length;
    }
    if (tag !== null) props.splice(at, 0, ['tag', String(tag)]);
    return props.map(([k, v]) => k === '' ? v : `${k}=${v}`).join(',');
}

// A unified diff (or two lines) with the .diff classes
function netDiffHtml(text) {
    return `<div class="diff">${String(text).split('\n').map(line => {
        const cls = line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@') ? 'ctx'
            : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : 'ctx';
        return `<div class="${cls}">${escapeHtml(line) || '&nbsp;'}</div>`;
    }).join('')}</div>`;
}

function netNodeChips(id, current, withAll) {
    const opts = nodes().filter(n => n.conf).map(n => ({key: n.name, label: n.name}));
    if (withAll) opts.unshift({key: 'all', label: 'All nodes'});
    return chipsHtml(id, opts, current, 'Node');
}

// =====================================================================
// Segments: one per bridge and VLAN tag, from the guests, the hosts and the SDN
// =====================================================================
function netVnets() {
    const sdn = (app.conf && app.conf.sdn) || {};
    const zones = new Map((sdn.zones || []).map(z => [z.zone, z]));
    return (sdn.vnets || []).map(v => {
        const zone = zones.get(v.zone) || {};
        return {...v, zoneType: zone.type || '', bridge: zone.bridge || '', subnets: (sdn.subnets || {})[v.vnet] || []};
    });
}

function netSegments() {
    const vnets = netVnets();
    const vnetNames = new Map(vnets.map(v => [v.vnet, v]));
    const bridges = new Map();   // bridge -> {name, nodes: Set, vlanAware, vnet, lanes: Map}
    const bridge = (name) => {
        if (!bridges.has(name)) bridges.set(name, {name, nodes: new Set(), vlanAware: false, vnet: vnetNames.get(name) || null, lanes: new Map(), hostNodes: new Set()});
        return bridges.get(name);
    };
    const lane = (b, tag) => {
        const key = tag === null ? 'u' : String(tag);
        if (!b.lanes.has(key)) b.lanes.set(key, {bridge: b.name, tag, cards: [], hosts: [], nodes: new Set(), vnets: []});
        return b.lanes.get(key);
    };
    // The hosts' bridges and their VLAN interfaces
    nodes().forEach(n => {
        const ifaces = netIfaces(n.name);
        const names = new Map(ifaces.map(i => [i.iface, i]));
        ifaces.filter(netIsBridge).forEach(i => {
            const b = bridge(i.iface);
            b.hostNodes.add(n.name);
            if (Number(i.bridge_vlan_aware) || i.type === 'OVSBridge') b.vlanAware = true;
            const l = lane(b, null);
            if (i.cidr) l.hosts.push({node: n.name, cidr: i.cidr, iface: i.iface, gateway: i.gateway});
        });
        ifaces.filter(i => netVlanOf(i) !== null).forEach(i => {
            const raw = netRawDevice(i);
            if (!names.has(raw) || !netIsBridge(names.get(raw))) return;   // a VLAN on a bond or NIC is no bridge segment
            const l = lane(bridge(raw), netVlanOf(i));
            l.hosts.push({node: n.name, cidr: i.cidr || '', iface: i.iface, gateway: i.gateway});
            l.nodes.add(n.name);
        });
    });
    // The SDN's VLAN zones: a vnet is a tag on its zone's bridge
    vnets.forEach(v => {
        if (v.bridge && !missing(v.tag) && v.tag !== '') lane(bridge(v.bridge), Number(v.tag)).vnets.push(v);
        else if (v.bridge && v.zoneType === 'simple') lane(bridge(v.vnet), null).vnets.push(v);
    });
    // Every guest's network devices
    guestNics().forEach(({guest, nic}) => {
        if (!nic.bridge) return;
        const b = bridge(nic.bridge);
        const l = lane(b, netTagOf(nic.tag));
        l.cards.push({guest, nic});
        l.nodes.add(guest.node);
        b.nodes.add(guest.node);
    });
    // A guest on a vnet: the vnet is its bridge
    bridges.forEach(b => {
        if (b.vnet) lane(b, null).vnets.push(b.vnet);
        if (b.vlanAware || b.lanes.size > 1) lane(b, null);
    });
    return [...bridges.values()].sort((a, b) => Boolean(a.vnet) - Boolean(b.vnet) || a.name.localeCompare(b.name, undefined, {numeric: true}))
        .map(b => ({...b, lanes: [...b.lanes.values()].sort((x, y) => (x.tag === null ? -1 : x.tag) - (y.tag === null ? -1 : y.tag))}));
}

// =====================================================================
// VLANs: lanes and cards
// =====================================================================
function netCardHtml(card, movable) {
    const {guest: g, nic} = card;
    const run = g.status === 'running' ? 'ok' : g.status === 'stopped' ? '' : 'warn';
    const missingBridge = nodes().some(n => n.name === g.node && n.conf) && !netIfaces(g.node).some(i => i.iface === nic.bridge)
        && !netVnets().some(v => v.vnet === nic.bridge);
    const search = `${g.vmid} ${guestName(g)} ${nic.key} ${nic.mac} ${nic.tag || 'untagged'} ${g.node} ${guestTags(g).join(' ')}`.toLowerCase();
    return `<div class="net-card${g.status !== 'running' ? ' stopped' : ''}" ${movable ? 'draggable="true"' : ''}
            data-vmid="${g.vmid}" data-nic="${attr(nic.key)}" data-bridge="${attr(nic.bridge)}" data-tag="${nic.tag === null || missing(nic.tag) ? '' : attr(nic.tag)}"
            data-search="${attr(search)}" data-running="${g.status === 'running' ? 1 : 0}">
        <div class="net-card-top"><span class="sdot ${run}" title="${attr(g.status || '')}"></span>${guestLink(g)}
            ${movable ? `<button type="button" class="net-move" title="Move to another VLAN" aria-label="Move ${attr(guestName(g))} ${attr(nic.key)} to another VLAN" data-move>⇄</button>` : ''}</div>
        <div class="net-card-sub"><span class="mono">${escapeHtml(nic.key)}</span>
            <span class="fw-flag ${nic.firewall ? 'on' : 'off'}" title="${nic.firewall ? 'Firewall on this device' : 'No firewall on this device'}">FW</span>
            ${nic.link_down ? '<span class="badge warn">link down</span>' : ''}
            ${missingBridge ? `<span class="badge bad" title="${attr(g.node)} has no ${attr(nic.bridge)}: the guest can't start or migrate there">no bridge</span>` : ''}
            <span class="muted">${escapeHtml(g.node)}</span></div>
        <div class="net-card-mac mono muted">${escapeHtml(nic.mac || '')}${nic.rate ? ` · ${escapeHtml(nic.rate)} MB/s` : ''}</div>
    </div>`;
}

function netLaneHtml(b, l) {
    const nodesIn = [...new Set([...l.nodes, ...l.hosts.map(h => h.node)])].sort();
    const vnets = [...new Map(l.vnets.map(v => [v.vnet, v])).values()];
    const subnet = vnets.flatMap(v => v.subnets || []);
    const head = l.tag === null ? (b.vnet ? `<span class="vlan-chip" style="--vlan:var(--brand)">VNet ${escapeHtml(b.name)}</span>` : vlanChip(null)) : vlanChip(l.tag);
    const movable = canChange() && !b.vnet;
    const label = vnets.length ? vnets.map(v => `<b>${escapeHtml(v.alias || v.vnet)}</b>${v.alias ? ` <span class="muted mono">${escapeHtml(v.vnet)}</span>` : ''}`).join(', ') : '';
    return `<div class="net-lane" data-bridge="${attr(b.name)}" data-tag="${l.tag === null ? '' : l.tag}" style="--vlan:${l.tag === null ? (b.vnet ? 'var(--brand)' : 'var(--idle)') : vlanColor(l.tag)}">
        <div class="net-lane-head">
            <div class="net-lane-title">${head}${label ? `<span class="net-lane-name">${label}</span>` : ''}</div>
            ${subnet.length ? `<div class="net-lane-line mono">${subnet.map(s => `${escapeHtml(s.cidr)}${s.gateway ? ` <span class="muted">gw ${escapeHtml(s.gateway)}</span>` : ''}${s.snat ? ' <span class="badge">SNAT</span>' : ''}`).join('<br>')}</div>` : ''}
            ${l.hosts.length ? `<div class="net-lane-line" title="Addresses of the hosts on this segment">${l.hosts.map(h => `<span class="mono">${escapeHtml(h.cidr || h.iface)}</span> <span class="muted">${escapeHtml(h.node)}</span>`).join('<br>')}</div>` : ''}
            <div class="net-lane-line muted"><span class="net-count">${l.cards.length}</span> ${l.cards.length === 1 ? 'device' : 'devices'}${nodesIn.length ? ` · ${nodesIn.map(escapeHtml).join(', ')}` : ''}</div>
        </div>
        <div class="net-lane-cards">${l.cards.length ? l.cards.sort((x, y) => x.guest.vmid - y.guest.vmid || x.nic.key.localeCompare(y.nic.key)).map(c => netCardHtml(c, movable)).join('')
            : `<div class="net-lane-empty">${movable ? 'Drop a guest here' : 'No guests'}</div>`}</div>
    </div>`;
}

function netRenderVlans(body) {
    const segs = netSegments();
    if (!segs.length) {
        body.innerHTML = panel('', '', '<div class="empty-state">No bridges and no guest network devices found.</div>');
        return;
    }
    const total = guestNics().length;
    body.innerHTML = `
        <div class="filters">
            <input class="input" id="net-search" type="search" placeholder="Guest, VMID, VLAN, MAC or node" aria-label="Search the network devices" value="${attr(netState.search)}">
            <label class="check-row"><input type="checkbox" id="net-hide-stopped"${netState.hideStopped ? ' checked' : ''}> Hide stopped guests</label>
            <span class="range-text" id="net-shown">${total} network devices</span>
        </div>
        ${canChange() ? '<p class="panel-sub net-help">Drag a guest to another VLAN of the same bridge, or use ⇄. You see the change before it is made.</p>'
            : '<p class="panel-sub net-help">Changes are off: this app is set to read-only (the <span class="mono">read_only</span> option).</p>'}
        ${segs.map(b => `
            <div class="panel net-bridge"><div class="panel-body">
                <div class="chart-title"><span>${b.vnet ? 'SDN VNet' : 'Bridge'} <span class="mono net-bridge-name">${escapeHtml(b.name)}</span></span>
                    <span class="hint">${b.vnet ? `zone ${escapeHtml(b.vnet.zone)}` : b.vlanAware ? 'VLAN aware' : 'not VLAN aware'}${b.hostNodes.size ? ` · on ${[...b.hostNodes].sort().map(escapeHtml).join(', ')}` : ''}</span></div>
                <div class="net-lanes">${b.lanes.map(l => netLaneHtml(b, l)).join('')}
                    ${canChange() && !b.vnet ? `<div class="net-lane net-lane-new" data-bridge="${attr(b.name)}" data-new="1"><div class="net-lane-head"><div class="net-lane-title"><span class="vlan-chip" style="--vlan:var(--muted-text-color)">+ VLAN</span></div>
                        <div class="net-lane-line muted">Drop here for a VLAN that has no lane yet</div></div><div class="net-lane-cards"></div></div>` : ''}
                </div>
            </div></div>`).join('')}`;
    const search = document.getElementById('net-search');
    search.addEventListener('input', () => { netState.search = search.value; netApplyFilter(); });
    document.getElementById('net-hide-stopped').addEventListener('change', (e) => {
        netState.hideStopped = e.target.checked;
        store('net_hide_stopped', netState.hideStopped ? '1' : '0');
        netApplyFilter();
    });
    netApplyFilter();
    netBindDrag(body);
}

function netApplyFilter() {
    const words = netState.search.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0, all = 0;
    document.querySelectorAll('#tab-network .net-card').forEach(card => {
        all++;
        const ok = words.every(w => card.dataset.search.includes(w)) && !(netState.hideStopped && card.dataset.running !== '1');
        card.hidden = !ok;
        if (ok) shown++;
    });
    document.querySelectorAll('#tab-network .net-lane:not(.net-lane-new)').forEach(lane => {
        const cards = lane.querySelectorAll('.net-card');
        lane.classList.toggle('filtered', cards.length > 0 && [...cards].every(c => c.hidden));
    });
    const el = document.getElementById('net-shown');
    if (el) el.textContent = shown === all ? `${all} network devices` : `${shown} of ${all} network devices`;
}

function netBindDrag(body) {
    const clear = () => {
        netState.drag = null;
        body.querySelectorAll('.drop-ok, .drop-no, .over, .dragging').forEach(el => el.classList.remove('drop-ok', 'drop-no', 'over', 'dragging'));
    };
    body.querySelectorAll('.net-card[draggable]').forEach(card => {
        card.addEventListener('dragstart', (e) => {
            netState.drag = {vmid: Number(card.dataset.vmid), nic: card.dataset.nic, bridge: card.dataset.bridge, tag: card.dataset.tag};
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', `${card.dataset.vmid}/${card.dataset.nic}`);
            card.classList.add('dragging');
            body.querySelectorAll('.net-lane').forEach(lane => {
                const ok = lane.dataset.bridge === netState.drag.bridge && (lane.dataset.new || lane.dataset.tag !== netState.drag.tag);
                lane.classList.add(ok ? 'drop-ok' : 'drop-no');
            });
            hideTip();
        });
        card.addEventListener('dragend', () => setTimeout(clear, 0));
    });
    body.querySelectorAll('.net-lane').forEach(lane => {
        lane.addEventListener('dragover', (e) => {
            if (!netState.drag || !lane.classList.contains('drop-ok')) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            lane.classList.add('over');
        });
        lane.addEventListener('dragleave', (e) => { if (!lane.contains(e.relatedTarget)) lane.classList.remove('over'); });
        lane.addEventListener('drop', (e) => {
            e.preventDefault();
            const drag = netState.drag;
            clear();
            if (!drag || lane.dataset.bridge !== drag.bridge) return;
            if (lane.dataset.new) netAskTag(drag.vmid, drag.nic);
            else netMove(drag.vmid, drag.nic, lane.dataset.tag === '' ? null : Number(lane.dataset.tag));
        });
    });
    body.querySelectorAll('[data-move]').forEach(button => button.addEventListener('click', (e) => {
        e.preventDefault();
        const card = button.closest('.net-card');
        netAskTag(Number(card.dataset.vmid), card.dataset.nic);
    }));
}

// Pick the VLAN by hand: for the ⇄ button, touch screens, and a VLAN without a lane
async function netAskTag(vmid, nicKey) {
    const g = guestById(vmid);
    const nic = g && g.conf && g.conf.nics.find(n => n.key === nicKey);
    if (!nic) return;
    const seg = netSegments().find(b => b.name === nic.bridge);
    const tags = seg ? seg.lanes.map(l => l.tag) : [null];
    const options = tags.map(t => `<option value="${t === null ? '' : t}"${netTagOf(nic.tag) === t ? ' selected' : ''}>${t === null ? 'untagged' : `VLAN ${t}`}${seg && seg.lanes.find(l => l.tag === t).vnets.length ? ` – ${escapeHtml(seg.lanes.find(l => l.tag === t).vnets[0].alias || seg.lanes.find(l => l.tag === t).vnets[0].vnet)}` : ''}</option>`).join('');
    const values = await formDialog({
        title: `${guestName(g)} ${nicKey}: VLAN`,
        confirmLabel: 'Next',
        body: `<div class="form-grid">
            <div class="form-field"><label>VLAN on ${escapeHtml(nic.bridge)}</label><select class="select" name="pick"><option value="other">Another tag…</option>${options}</select></div>
            <div class="form-field"><label>Tag (1 to 4094)</label><input class="input" name="tag" inputmode="numeric" placeholder="for example 60" autocomplete="off"></div>
        </div><p class="form-hint">Choose a VLAN that exists, or type a new tag.</p>`,
        onOpen: (form) => {
            const pick = form.querySelector('[name=pick]');
            const tag = form.querySelector('[name=tag]');
            if (![...pick.options].some(o => o.selected && o.value !== 'other')) pick.value = 'other';
            const sync = () => { tag.disabled = pick.value !== 'other'; };
            pick.addEventListener('change', sync);
            sync();
        },
        validate: (v) => {
            if (v.pick !== 'other') return '';
            const n = Number(v.tag);
            return Number.isInteger(n) && n >= 1 && n <= 4094 ? '' : 'A VLAN tag is a whole number from 1 to 4094';
        },
    });
    if (!values) return;
    const tag = values.pick === 'other' ? Number(values.tag) : values.pick === '' ? null : Number(values.pick);
    // The dialog closes before the next one opens
    setTimeout(() => netMove(vmid, nicKey, tag), 0);
}

async function netMove(vmid, nicKey, tag) {
    const g = guestById(vmid);
    const conf = app.conf && app.conf.guests && app.conf.guests[String(vmid)];
    const nic = conf && conf.nics.find(n => n.key === nicKey);
    if (!g || !nic) return;
    if (netTagOf(nic.tag) === tag) return;
    const before = nic.raw;
    const after = netSetTag(before, tag);
    const to = tag === null ? 'untagged' : `VLAN ${tag}`;
    const running = g.status === 'running';
    const message = `
        <p>${guestLabel(g)} <span class="mono">${escapeHtml(nicKey)}</span> on <span class="mono">${escapeHtml(nic.bridge)}</span>:
            ${vlanChip(netTagOf(nic.tag))} → ${vlanChip(tag)}</p>
        <div style="margin-top:10px">${netDiffHtml(`- ${nicKey}: ${before}\n+ ${nicKey}: ${after}`)}</div>
        ${running ? `<div class="note warn" style="margin-top:10px">${escapeHtml(guestName(g))} is running: the device moves at once. The guest
            will likely need a new address (DHCP), and open connections over this device drop.</div>` : ''}
        ${nic.firewall && conf.firewall && conf.firewall.options && Number(conf.firewall.options.ipfilter) ? `<div class="note" style="margin-top:8px">The guest's IP filter is on: check its ipfilter IP set for the new subnet.</div>` : ''}`;
    const ok = await confirmDialog({title: `Move ${guestName(g)} to ${to}?`, message, confirmLabel: `Move to ${to}`, wide: true});
    if (!ok) return;
    try {
        const answer = await apiPost(`guest/${vmid}/vlan`, {nic: nicKey, tag, digest: conf.digest || ''});
        // Show it at once; the app reads the configuration again by itself after a change
        nic.raw = answer.after || after;
        nic.tag = tag;
        conf.digest = '';
        toast(`${guestName(g)} ${nicKey} is now on ${to}`, 'success');
        netState.sig = '';
        netRender();
        setTimeout(() => loadStatus().catch(() => {}), 2500);
    } catch (e) {
        toast(e.message);
    }
}

// =====================================================================
// Map: each node's interfaces as boxes, from the NICs to the guests
// =====================================================================
const NET_BOX_W = 176;
const NET_COL_GAP = 64;
const NET_ROW_GAP = 14;
const NET_GUEST_ROWS = 8;

function netColumn(i, names) {
    const t = i.type;
    if (t === 'eth' || t === 'unknown' || t === 'alias') return 0;
    if (t === 'bond' || t === 'OVSBond') return 1;
    if (netIsBridge(i)) return 2;
    if (netVlanOf(i) !== null) {
        const raw = names.get(netRawDevice(i));
        return raw && netIsBridge(raw) ? 3 : 1;
    }
    return 3;
}

// The boxes and lines of one node, before the saved layout is applied
function netGraph(node) {
    const ifaces = netIfaces(node);
    const names = new Map(ifaces.map(i => [i.iface, i]));
    const boxes = [];
    const edges = [];
    ifaces.forEach(i => {
        const col = netColumn(i, names);
        const vlan = netVlanOf(i);
        const sub = [i.type === 'eth' ? 'NIC' : i.type, vlan !== null ? `VLAN ${vlan}` : '', i.cidr || ''].filter(Boolean).join(' · ');
        boxes.push({id: i.iface, col, title: i.iface, sub, kind: i.type, iface: i, inactive: !Number(i.active), h: 44,
            vlan: vlan !== null ? vlan : null});
    });
    const add = (from, to, tag) => { if (names.has(from) || from.startsWith('g:')) edges.push({from, to, tag}); };
    ifaces.forEach(i => {
        netWords(i.slaves || i['bond-slaves'] || i.ovs_bonds).forEach(s => add(s, i.iface));
        if (i.type === 'OVSBond') netWords(i.ovs_bonds).forEach(s => add(s, i.iface));
        if (netIsBridge(i)) netWords(i.bridge_ports || i.ovs_ports).forEach(p => { if (names.has(p) && netColumn(names.get(p), names) < 2) add(p, i.iface); });
        if (netVlanOf(i) !== null) {
            const raw = netRawDevice(i);
            if (names.has(raw)) add(raw, i.iface, netVlanOf(i));
        }
    });
    // The guests of this node, grouped per bridge and tag
    const groups = new Map();
    guestNics().filter(({guest}) => guest.node === node).forEach(({guest, nic}) => {
        const key = `g:${nic.bridge}:${missing(nic.tag) ? 'u' : nic.tag}`;
        if (!groups.has(key)) groups.set(key, {bridge: nic.bridge, tag: netTagOf(nic.tag), rows: []});
        groups.get(key).rows.push({guest, nic});
    });
    const bridgeOrder = ifaces.filter(netIsBridge).map(i => i.iface);
    [...groups.entries()].sort((a, b) => (bridgeOrder.indexOf(a[1].bridge) - bridgeOrder.indexOf(b[1].bridge))
        || ((a[1].tag === null ? -1 : a[1].tag) - (b[1].tag === null ? -1 : b[1].tag))).forEach(([key, grp]) => {
        const shown = Math.min(grp.rows.length, NET_GUEST_ROWS);
        boxes.push({id: key, col: 4, title: grp.tag === null ? 'untagged' : `VLAN ${grp.tag}`, sub: grp.bridge, kind: 'guests', group: grp,
            h: 26 + shown * 17 + (grp.rows.length > shown ? 17 : 0) + 6, vlan: grp.tag});
        // A bridge the guest needs but this node doesn't have: shown, so the problem is visible
        if (!names.has(grp.bridge) && !boxes.some(b => b.id === grp.bridge)) {
            boxes.push({id: grp.bridge, col: 2, title: grp.bridge, sub: 'missing on this node', kind: 'missing', h: 44, inactive: true});
        }
        edges.push({from: grp.bridge, to: key, tag: grp.tag});
    });
    // Default places: columns left to right, empty columns left out
    const cols = [...new Set(boxes.map(b => b.col))].sort((a, b) => a - b);
    const ys = {};
    const order = (b) => b.kind === 'guests' ? 0 : b.iface && netVlanOf(b.iface) !== null ? netVlanOf(b.iface) : 0;
    boxes.sort((a, b) => a.col - b.col || (a.kind === 'guests' ? 0 : String(a.id).localeCompare(String(b.id), undefined, {numeric: true})) || order(a) - order(b));
    boxes.forEach(b => {
        const c = cols.indexOf(b.col);
        b.x = 12 + c * (NET_BOX_W + NET_COL_GAP);
        b.y = ys[c] === undefined ? 34 : ys[c];
        ys[c] = b.y + b.h + NET_ROW_GAP;
    });
    return {boxes, edges, cols};
}

const NET_COL_NAMES = ['Physical', 'Bonds', 'Bridges', 'VLANs', 'Guests'];

function netEdgePath(a, b) {
    const x1 = a.x + NET_BOX_W, y1 = a.y + Math.min(a.h, 44) / 2;
    const x2 = b.x, y2 = b.y + Math.min(b.h, 44) / 2;
    const dx = Math.max(30, Math.abs(x2 - x1) / 2);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
}

function netBoxSvg(b) {
    const cls = `net-box k-${b.kind}${b.inactive ? ' inactive' : ''}`;
    const stripe = b.vlan !== null && b.vlan !== undefined ? vlanColor(b.vlan) : b.kind === 'guests' ? 'var(--idle)' : '';
    let inner = `<rect class="net-box-bg" width="${NET_BOX_W}" height="${b.h}" rx="5"/>`;
    if (stripe) inner += `<rect x="0" y="0" width="4" height="${b.h}" rx="2" fill="${stripe}"/>`;
    inner += `<text class="net-box-title" x="12" y="18">${escapeHtml(b.title)}</text>`;
    if (b.kind === 'guests') {
        inner += `<text class="net-box-sub" x="${NET_BOX_W - 8}" y="18" text-anchor="end">${escapeHtml(b.sub)}</text>`;
        const rows = b.group.rows.slice().sort((x, y) => x.guest.vmid - y.guest.vmid);
        rows.slice(0, NET_GUEST_ROWS).forEach((r, k) => {
            const y = 26 + k * 17 + 12;
            const run = r.guest.status === 'running';
            inner += `<g class="net-guest" data-vmid="${r.guest.vmid}"><circle cx="15" cy="${y - 4}" r="3.5" class="${run ? 'run' : 'off'}"/>
                <text class="net-guest-text" x="25" y="${y}"><tspan class="mono-t">${r.guest.vmid}</tspan> ${escapeHtml(guestName(r.guest).slice(0, 18))}</text>
                ${r.nic.firewall ? `<text class="net-guest-fw" x="${NET_BOX_W - 8}" y="${y}" text-anchor="end">FW</text>` : ''}</g>`;
        });
        if (rows.length > NET_GUEST_ROWS) inner += `<text class="net-box-sub" x="25" y="${26 + NET_GUEST_ROWS * 17 + 12}">+${rows.length - NET_GUEST_ROWS} more</text>`;
    } else {
        inner += `<text class="net-box-sub" x="12" y="34">${escapeHtml(b.sub.slice(0, 30))}</text>`;
    }
    return `<g class="${cls}" data-id="${attr(b.id)}" transform="translate(${b.x},${b.y})" tabindex="0">${inner}</g>`;
}

function netMapPanel(node) {
    const graph = netGraph(node);
    const saved = (netState.layout && netState.layout.map && netState.layout.map[node]) || {};
    graph.boxes.forEach(b => {
        const p = saved[b.id];
        if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) { b.x = p.x; b.y = p.y; }
    });
    if (!graph.boxes.length) return panel(escapeHtml(node), '', '<div class="empty-state">No interfaces read for this node.</div>');
    const width = Math.max(...graph.boxes.map(b => b.x + NET_BOX_W)) + 16;
    const height = Math.max(...graph.boxes.map(b => b.y + b.h)) + 16;
    const heads = graph.cols.map((c, k) => `<text class="net-col-head" x="${12 + k * (NET_BOX_W + NET_COL_GAP)}" y="16">${NET_COL_NAMES[c] || ''}</text>`).join('');
    const byId = new Map(graph.boxes.map(b => [b.id, b]));
    const edges = graph.edges.filter(e => byId.has(e.from) && byId.has(e.to)).map((e, k) =>
        `<path class="net-edge" data-from="${attr(e.from)}" data-to="${attr(e.to)}" data-k="${k}" d="${netEdgePath(byId.get(e.from), byId.get(e.to))}"
            ${e.tag !== null && e.tag !== undefined ? `style="stroke:${vlanColor(e.tag)}"` : ''}/>`).join('');
    const n = nodeByName(node);
    const hint = `${n && n.ip ? escapeHtml(n.ip) + ' · ' : ''}${graph.boxes.filter(b => b.kind !== 'guests').length} interfaces`;
    return `<div class="panel net-map-panel" data-node="${attr(node)}"><div class="panel-body">
        <div class="chart-title"><span>${escapeHtml(node)}</span><span class="hint">${hint}</span></div>
        <div class="net-map-scroll"><svg class="net-map" data-node="${attr(node)}" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Network of ${attr(node)}">
            ${heads}<g class="net-edges">${edges}</g><g class="net-boxes">${graph.boxes.map(netBoxSvg).join('')}</g></svg></div>
    </div></div>`;
}

async function netRenderMap(body) {
    const list = nodes().filter(n => n.conf);
    if (!list.length) {
        body.innerHTML = panel('', '', '<div class="empty-state">No node\'s network has been read yet.</div>');
        return;
    }
    if (netState.mapNode !== 'all' && !list.some(n => n.name === netState.mapNode)) netState.mapNode = 'all';
    if (netState.layout === null) {
        try { netState.layout = await api('layout'); } catch (e) { netState.layout = {}; }
        if (!netState.layout || typeof netState.layout !== 'object') netState.layout = {};
    }
    const shown = netState.mapNode === 'all' ? list : list.filter(n => n.name === netState.mapNode);
    body.innerHTML = `
        <div class="filters">${netNodeChips('net-map-node', netState.mapNode, true)}
            <span class="range-text net-map-tools"><span class="net-legend"><i class="run"></i> running <i class="off"></i> stopped · drag boxes to arrange</span>
            <button type="button" class="btn btn-small" id="net-map-reset">Reset layout</button></span></div>
        ${shown.map(n => netMapPanel(n.name)).join('')}`;
    bindChips('net-map-node', (key) => { netState.mapNode = key; store('net_map_node', key); netRender(); });
    document.getElementById('net-map-reset').addEventListener('click', async () => {
        const layout = netState.layout || {};
        if (layout.map) shown.forEach(n => delete layout.map[n.name]);
        netState.layout = layout;
        await netSaveLayout(true);
        netRender();
    });
    body.querySelectorAll('svg.net-map').forEach(netBindMap);
}

let netSaveTimer = null;
function netSaveLayout(now) {
    clearTimeout(netSaveTimer);
    const save = () => apiPost('layout', netState.layout || {}).catch(e => toast('Cannot save the layout: ' + e.message));
    if (now) return save();
    netSaveTimer = setTimeout(save, 600);
    return null;
}

function netBindMap(svg) {
    const node = svg.dataset.node;
    const edges = [...svg.querySelectorAll('.net-edge')];
    const boxEl = id => svg.querySelector(`.net-box[data-id="${CSS.escape(id)}"]`);
    const pos = id => {
        const el = boxEl(id);
        const m = /translate\(([-\d.]+),([-\d.]+)\)/.exec(el.getAttribute('transform'));
        return {x: Number(m[1]), y: Number(m[2]), h: Number(el.querySelector('.net-box-bg').getAttribute('height'))};
    };
    const redraw = id => edges.filter(e => e.dataset.from === id || e.dataset.to === id)
        .forEach(e => e.setAttribute('d', netEdgePath(pos(e.dataset.from), pos(e.dataset.to))));
    // Everything a box hangs from, and everything that hangs from it
    const path = id => {
        const seen = new Set([id]);
        const walk = (dir) => {
            const todo = [id];
            while (todo.length) {
                const cur = todo.pop();
                edges.forEach(e => {
                    const [a, b] = dir ? [e.dataset.from, e.dataset.to] : [e.dataset.to, e.dataset.from];
                    if (a === cur && !seen.has(b)) { seen.add(b); todo.push(b); }
                });
            }
        };
        walk(true);
        walk(false);
        return seen;
    };
    const tipFor = (id, e) => {
        if (id.startsWith('g:')) {
            const [, bridge, tag] = id.split(':');
            const rows = guestNics().filter(({guest, nic}) => guest.node === node && nic.bridge === bridge && String(missing(nic.tag) ? 'u' : nic.tag) === tag);
            showTip(e.clientX, e.clientY, `${tag === 'u' ? 'untagged' : 'VLAN ' + tag} on ${bridge}`,
                rows.slice(0, 14).map(r => ({value: `${r.guest.vmid} ${guestName(r.guest)}`, name: `${r.nic.key} · ${r.guest.status}`, color: r.guest.status === 'running' ? 'var(--success-color)' : 'var(--idle)'}))
                    .concat(rows.length > 14 ? [{value: `+${rows.length - 14} more`}] : []));
            return;
        }
        const i = netIfaces(node).find(x => x.iface === id);
        if (!i) { showTip(e.clientX, e.clientY, id, [{value: 'missing', name: `no ${id} on ${node}`}]); return; }
        const rows = [{value: i.type, name: Number(i.active) ? 'active' : 'not active'}];
        if (i.cidr) rows.push({value: i.cidr, name: 'address'});
        if (i.gateway) rows.push({value: i.gateway, name: 'gateway'});
        if (i.cidr6) rows.push({value: i.cidr6, name: 'IPv6'});
        if (i.bridge_ports || i.ovs_ports) rows.push({value: i.bridge_ports || i.ovs_ports, name: 'ports'});
        if (i.slaves) rows.push({value: i.slaves, name: 'slaves'});
        if (i.bond_mode) rows.push({value: i.bond_mode, name: 'bond mode'});
        if (Number(i.bridge_vlan_aware)) rows.push({value: i.bridge_vids || '2-4094', name: 'VLAN aware'});
        if (netVlanOf(i) !== null) rows.push({value: `${netVlanOf(i)} on ${netRawDevice(i)}`, name: 'VLAN'});
        if (i.mtu) rows.push({value: i.mtu, name: 'MTU'});
        if (netComment(i)) rows.push({value: netComment(i)});
        showTip(e.clientX, e.clientY, id, rows);
    };
    svg.querySelectorAll('.net-box').forEach(el => {
        const id = el.dataset.id;
        el.addEventListener('pointerenter', (e) => {
            if (netState.boxDrag) return;
            const on = path(id);
            svg.classList.add('hl');
            svg.querySelectorAll('.net-box').forEach(b => b.classList.toggle('on', on.has(b.dataset.id)));
            edges.forEach(p => p.classList.toggle('on', on.has(p.dataset.from) && on.has(p.dataset.to)));
            tipFor(id, e);
        });
        el.addEventListener('pointermove', (e) => { if (!netState.boxDrag) tipFor(id, e); });
        el.addEventListener('pointerleave', () => {
            svg.classList.remove('hl');
            svg.querySelectorAll('.on').forEach(b => b.classList.remove('on'));
            hideTip();
        });
        el.addEventListener('pointerdown', (e) => {
            if (e.button !== 0) return;
            const start = pos(id);
            const ctm = svg.getScreenCTM();
            netState.boxDrag = {id, x0: e.clientX, y0: e.clientY, start, scale: ctm ? ctm.a : 1, moved: false, target: e.target};
            el.setPointerCapture(e.pointerId);
            hideTip();
        });
        el.addEventListener('pointermove', (e) => {
            const d = netState.boxDrag;
            if (!d || d.id !== id) return;
            const dx = (e.clientX - d.x0) / d.scale, dy = (e.clientY - d.y0) / d.scale;
            if (!d.moved && Math.hypot(dx, dy) < 4) return;
            d.moved = true;
            el.classList.add('moving');
            const x = Math.max(0, Math.round(d.start.x + dx)), y = Math.max(22, Math.round(d.start.y + dy));
            el.setAttribute('transform', `translate(${x},${y})`);
            redraw(id);
            netGrowSvg(svg);
        });
        const end = () => {
            const d = netState.boxDrag;
            if (!d || d.id !== id) return;
            netState.boxDrag = null;
            el.classList.remove('moving');
            if (!d.moved) {
                const row = d.target.closest && d.target.closest('.net-guest');
                if (row) openGuest(Number(row.dataset.vmid));
                return;
            }
            const p = pos(id);
            const layout = netState.layout || (netState.layout = {});
            layout.map = layout.map || {};
            layout.map[node] = layout.map[node] || {};
            layout.map[node][id] = {x: p.x, y: p.y};
            netSaveLayout();
        };
        el.addEventListener('pointerup', end);
        el.addEventListener('pointercancel', end);
    });
}

function netGrowSvg(svg) {
    let w = 0, h = 0;
    svg.querySelectorAll('.net-box').forEach(el => {
        const m = /translate\(([-\d.]+),([-\d.]+)\)/.exec(el.getAttribute('transform'));
        w = Math.max(w, Number(m[1]) + NET_BOX_W + 16);
        h = Math.max(h, Number(m[2]) + Number(el.querySelector('.net-box-bg').getAttribute('height')) + 16);
    });
    if (w > Number(svg.getAttribute('width')) || h > Number(svg.getAttribute('height'))) {
        w = Math.max(w, Number(svg.getAttribute('width')));
        h = Math.max(h, Number(svg.getAttribute('height')));
        svg.setAttribute('width', w);
        svg.setAttribute('height', h);
        svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    }
}

// =====================================================================
// Interfaces: every interface per node, the bridges across nodes, pending changes
// =====================================================================
function netRenderIfaces(body) {
    const list = nodes().filter(n => n.conf);
    if (!list.length) {
        body.innerHTML = panel('', '', '<div class="empty-state">No node\'s network has been read yet.</div>');
        return;
    }
    if (!list.some(n => n.name === netState.ifNode)) netState.ifNode = list[0].name;
    const node = netState.ifNode;
    const net = list.find(n => n.name === node).conf.network || {};
    const byName = new Map((net.ifaces || []).map(i => [i.iface, i]));
    const ifaces = (net.ifaces || []).slice().sort((x, y) => netColumn(x, byName) - netColumn(y, byName) || x.iface.localeCompare(y.iface, undefined, {numeric: true}));
    const yes = v => Number(v) ? '<span class="pos">yes</span>' : '<span class="muted">no</span>';
    const rows = ifaces.map(i => {
        const vlan = netVlanOf(i);
        const ports = i.bridge_ports || i.ovs_ports || i.slaves || i.ovs_bonds || '';
        return `<tr class="${Number(i.active) ? '' : 'off'}">
            ${td(`<span class="name mono">${escapeHtml(i.iface)}</span>`)}
            ${td(escapeHtml(i.type === 'eth' ? 'NIC' : i.type))}
            ${td(Number(i.active) ? stateLabel('ok', 'active') : stateLabel('', 'not active'))}
            ${td(yes(i.autostart))}
            ${td(escapeHtml(i.method || ''))}
            ${td(escapeHtml(i.cidr || '') + (i.cidr6 ? `<span class="sub">${escapeHtml(i.cidr6)}</span>` : ''), 'mono')}
            ${td(escapeHtml(i.gateway || ''), 'mono')}
            ${td(escapeHtml(ports), 'mono')}
            ${td(escapeHtml(i.bond_mode || i['bond-mode'] || ''))}
            ${td(vlan !== null ? `${vlanChip(vlan)} <span class="muted mono">${escapeHtml(netRawDevice(i))}</span>` : '')}
            ${td(netIsBridge(i) ? (Number(i.bridge_vlan_aware) ? `yes <span class="muted mono">${escapeHtml(i.bridge_vids || '')}</span>` : '<span class="muted">no</span>') : '')}
            ${td(escapeHtml(i.mtu || ''), 'n')}
            ${td(escapeHtml(netComment(i)), 'wrap-cell')}
        </tr>`;
    });
    // Bridges that exist on one node but not another: a guest can't migrate there
    const bridges = [...new Set(list.flatMap(n => netIfaces(n.name).filter(netIsBridge).map(i => i.iface)))].sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
    const used = new Set(guestNics().map(({nic}) => nic.bridge));
    const matrix = bridges.map(b => `<tr>${td(`<span class="mono name">${escapeHtml(b)}</span>${used.has(b) ? '' : ' <span class="muted">no guests</span>'}`)}${list.map(n => {
        const i = netIfaces(n.name).find(x => x.iface === b);
        if (!i) return td(used.has(b) ? '<span class="neg" title="Guests on this bridge can\'t run on this node">missing</span>' : '<span class="muted">–</span>');
        return td(`${Number(i.active) ? '<span class="sdot ok"></span>' : '<span class="sdot"></span>'} <span class="mono">${escapeHtml(i.cidr || '')}</span>${Number(i.bridge_vlan_aware) ? ' <span class="badge">VLAN</span>' : ''}${i.mtu && i.mtu !== '1500' ? ` <span class="muted mono">MTU ${escapeHtml(i.mtu)}</span>` : ''}`);
    }).join('')}</tr>`);
    const pending = list.filter(n => n.conf.network && n.conf.network.changes);
    body.innerHTML = `
        <div class="filters">${netNodeChips('net-if-node', node, false)}
            <span class="range-text">${ifaces.length} interfaces${pending.length ? ` · <span class="sb-warn">pending changes on ${pending.map(n => escapeHtml(n.name)).join(', ')}</span>` : ''}</span></div>
        ${permissionNote('network', 'Network')}
        ${net.changes ? `<div class="panel net-pending"><div class="panel-body">
            <div class="chart-title"><span>Pending network changes (not applied)</span><span class="hint">/etc/network/interfaces.new on ${escapeHtml(node)}</span></div>
            <p class="panel-sub" style="margin-bottom:8px">Someone changed the network of ${escapeHtml(node)} without applying it. Apply or revert it in Proxmox (Node → Network); the table below shows what runs now.</p>
            ${netDiffHtml(net.changes.replace(/\n$/, ''))}</div></div>` : ''}
        ${panel(`Interfaces of ${escapeHtml(node)}`, 'dimmed: not active', `<div class="table-wrap">${table([
            {label: 'Name'}, {label: 'Type'}, {label: 'State'}, {label: 'Autostart'}, {label: 'Method'}, {label: 'Address'}, {label: 'Gateway'},
            {label: 'Ports / slaves'}, {label: 'Bond mode'}, {label: 'VLAN'}, {label: 'VLAN aware'}, {label: 'MTU', n: true}, {label: 'Comment'},
        ], rows, {empty: 'No interfaces'})}</div>`)}
        ${list.length > 1 ? panel('Bridges across nodes', 'a guest only migrates to a node that has its bridges', `<div class="table-wrap">${table(
            [{label: 'Bridge'}, ...list.map(n => ({label: escapeHtml(n.name)}))], matrix, {empty: 'No bridges'})}</div>`) : ''}`;
    bindChips('net-if-node', (key) => { netState.ifNode = key; store('net_if_node', key); netRender(); });
}

// =====================================================================
// SDN: zones, vnets and subnets, and who uses them
// =====================================================================
function netRenderSdn(body) {
    const sdn = (app.conf && app.conf.sdn) || {};
    const zones = sdn.zones || [], vnets = netVnets();
    const note = permissionNote('sdn', 'SDN');
    if (!zones.length && !vnets.length) {
        body.innerHTML = `${note}${panel('Software-defined network', '', '<div class="empty-state">This cluster has no SDN zones or VNets. Define them in Proxmox under Datacenter → SDN.</div>')}`;
        return;
    }
    const nics = guestNics();
    const usersOf = v => {
        const attached = nics.filter(({nic}) => nic.bridge === v.vnet);
        const same = !missing(v.tag) && v.tag !== '' && v.bridge ? nics.filter(({nic}) => nic.bridge === v.bridge && Number(nic.tag) === Number(v.tag)) : [];
        return {attached, same};
    };
    const guestList = list => [...new Map(list.map(({guest}) => [guest.vmid, guest])).values()].map(guestLink).join('<br>');
    const zoneRows = zones.map(z => `<tr>${td(`<span class="name mono">${escapeHtml(z.zone)}</span>`)}${td(escapeHtml(z.type || ''))}${td(escapeHtml(z.bridge || ''), 'mono')}
        ${td(escapeHtml(z.tag || z['vlan-protocol'] || ''), 'mono')}${td(escapeHtml(z.mtu || ''), 'n')}${td(escapeHtml(z.nodes || 'all'))}${td(escapeHtml(z.ipam || ''))}
        ${td(String(vnets.filter(v => v.zone === z.zone).length), 'n')}${td(z.pending ? '<span class="badge warn">pending</span>' : z.state ? escapeHtml(z.state) : '')}</tr>`);
    const vnetRows = vnets.map(v => {
        const u = usersOf(v);
        return `<tr>${td(`<span class="name mono">${escapeHtml(v.vnet)}</span>${v.alias ? `<span class="sub">${escapeHtml(v.alias)}</span>` : ''}`)}
            ${td(escapeHtml(v.zone))}${td(missing(v.tag) || v.tag === '' ? '' : vlanChip(v.tag))}${td(escapeHtml(v.bridge || ''), 'mono')}
            ${td(Number(v.vlanaware) ? 'yes' : '<span class="muted">no</span>')}
            ${td((v.subnets || []).map(s => `<span class="mono">${escapeHtml(s.cidr)}</span>`).join('<br>'))}
            ${td(guestList(u.attached) || '<span class="muted">–</span>', 'wrap-cell')}
            ${td(guestList(u.same) || '<span class="muted">–</span>', 'wrap-cell')}</tr>`;
    });
    const subnetRows = vnets.flatMap(v => (v.subnets || []).map(s => `<tr>${td(`<span class="mono">${escapeHtml(s.cidr)}</span>`)}${td(`<span class="mono">${escapeHtml(v.vnet)}</span>`)}
        ${td(escapeHtml(s.gateway || ''), 'mono')}${td(Number(s.snat) ? '<span class="badge">SNAT</span>' : '<span class="muted">no</span>')}
        ${td((s['dhcp-range'] || []).map(r => `<span class="mono">${escapeHtml(r['start-address'] || '')} – ${escapeHtml(r['end-address'] || '')}</span>`).join('<br>') || '<span class="muted">–</span>')}
        ${td(escapeHtml(s['dns-zone-prefix'] || s.dnszoneprefix || ''))}</tr>`));
    body.innerHTML = `${note}
        ${panel('Zones', zones.length === 1 ? '1 zone' : `${zones.length} zones`, `<div class="table-wrap">${table([{label: 'Zone'}, {label: 'Type'}, {label: 'Bridge'}, {label: 'Tag'}, {label: 'MTU', n: true}, {label: 'Nodes'}, {label: 'IPAM'}, {label: 'VNets', n: true}, {label: 'State'}], zoneRows, {empty: 'No zones'})}</div>`)}
        ${panel('VNets', 'attached: the VNet is the guest\'s bridge · same VLAN: the zone\'s bridge with the VNet\'s tag', `<div class="table-wrap">${table([{label: 'VNet'}, {label: 'Zone'}, {label: 'Tag'}, {label: 'Bridge'}, {label: 'VLAN aware'}, {label: 'Subnets'}, {label: 'Attached'}, {label: 'Same VLAN'}], vnetRows, {empty: 'No VNets'})}</div>`)}
        ${panel('Subnets', '', `<div class="table-wrap">${table([{label: 'Subnet'}, {label: 'VNet'}, {label: 'Gateway'}, {label: 'SNAT'}, {label: 'DHCP range'}, {label: 'DNS prefix'}], subnetRows, {empty: 'No subnets'})}</div>`)}`;
}

// =====================================================================
// The page
// =====================================================================
function netSignature() {
    return `${app.confAt}|${netState.section}|${guests().map(g => g.vmid + (g.status || '')).join(',')}`;
}

function netRender() {
    const root = document.getElementById('tab-network');
    if (!root.querySelector('#net-body')) {
        root.innerHTML = `<div class="filters net-top">${chipsHtml('net-sections', NET_SECTIONS, netState.section, 'Section')}
            <span class="range-text" id="net-read"></span></div><div id="net-body"></div>`;
        bindChips('net-sections', (key) => {
            netState.section = key;
            store('net_section', key);
            setSub(key);
            netState.sig = '';
            netRender();
        });
    }
    root.querySelectorAll('#net-sections .tab').forEach(t => t.classList.toggle('active', t.dataset.key === netState.section));
    const read = document.getElementById('net-read');
    read.textContent = app.confAt ? `configuration read ${fmtAgo(app.confAt)}` : '';
    const body = document.getElementById('net-body');
    netState.sig = netSignature();
    if (!app.conf) {
        body.innerHTML = panel('', '', '<div class="empty-state">Reading the configuration…</div>');
        return;
    }
    const focused = document.activeElement && document.activeElement.id === 'net-search';
    hideTip();
    if (netState.section === 'vlans') netRenderVlans(body);
    else if (netState.section === 'map') netRenderMap(body);
    else if (netState.section === 'ifaces') netRenderIfaces(body);
    else netRenderSdn(body);
    if (focused) {
        const input = document.getElementById('net-search');
        if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    }
}

onTab('network', (sub) => {
    if (sub && NET_SECTIONS.some(s => s.key === sub)) {
        netState.section = sub;
        store('net_section', sub);
    } else {
        setSub(netState.section);
    }
    netState.sig = '';
    netRender();
}, () => {
    const read = document.getElementById('net-read');
    if (read && app.confAt) read.textContent = `configuration read ${fmtAgo(app.confAt)}`;
    if (netBusy() || netSignature() === netState.sig) return;
    netRender();
});
