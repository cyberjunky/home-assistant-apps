// Network dashboard - Networks: your networks and VLANs, which devices are in them, and every
// address of a subnet as a grid: used, free, and two devices on one address.

const NET_PURPOSES = ['LAN', 'Servers', 'IoT', 'Cameras', 'Guests', 'Management', 'VPN', 'WAN', 'Other'];

function netHosts(net) {
    return networkHosts().filter(h => (networkOf(h).net || {}).id === net.id);
}

function isPrivateIp(ip) {
    const n = ipToInt(ip);
    if (n === null) return false;
    const a = n >>> 24, b = (n >>> 16) & 255;
    return a === 10 || (a === 172 && b >= 16 && b < 32) || (a === 192 && b === 168);
}

// /24 subnets the hosts are in that no network covers yet
function suggestedNetworks() {
    const seen = new Map();
    networkHosts().forEach(h => {
        const n = ipToInt(h.ip);
        if (n === null || networkOfIp(h.ip)) return;
        if (!isPrivateIp(h.ip)) return;
        const base = (n & 0xffffff00) >>> 0;
        if (!seen.has(base)) seen.set(base, []);
        seen.get(base).push(h);
    });
    return [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([base, hosts]) => {
        const third = (base >>> 8) & 255;
        const kinds = hosts.map(kindOf);
        const share = k => kinds.filter(x => k.includes(x)).length / kinds.length;
        let purpose = 'LAN';
        if (share(['camera', 'doorbell']) > 0.6) purpose = 'Cameras';
        else if (share(['iot', 'plug', 'light', 'hub', 'thermostat', 'energy', 'car', 'appliance', 'miner']) > 0.5) purpose = 'IoT';
        else if (share(['server', 'nas', 'vm', 'sbc']) > 0.5) purpose = 'Servers';
        else if (hosts.filter(h => h.random_mac).length / hosts.length > 0.6) purpose = 'Guests';
        return {subnet: `${intToIp(base)}/24`, vlan: third > 1 && third < 4095 ? String(third) : '', purpose, hosts};
    });
}

function renderNetworks() {
    const el = document.getElementById('tab-networks');
    if (!app.doc) return;
    const suggestions = suggestedNetworks();
    // Private addresses only: a ping of 1.1.1.1 is no device in your network
    const outside = networkHosts().filter(h => h.ip && isPrivateIp(h.ip) && !networkOf(h).net);
    el.innerHTML = `
        <div class="page-head"><h1>Networks</h1><span class="meta">A device is in the network of its IP address, unless you pick another for it</span>
            <div class="controls"><button class="btn btn-primary" onclick="netEdit()">Add a network</button></div></div>
        ${suggestions.length ? `<div class="setup-hint info"><span><b>${suggestions.length} subnet${suggestions.length > 1 ? 's' : ''} without a network:</b> ${suggestions.map(s => `<span class="mono">${escapeHtml(s.subnet)}</span> <span class="muted">(${s.hosts.length})</span>`).join(', ')}</span>
            <button class="btn btn-small btn-primary" onclick="netAddSuggested()">Add ${suggestions.length > 1 ? 'them' : 'it'}</button></div>` : ''}
        ${networks().length ? `<div class="net-cards">${networks().map(netCard).join('')}</div>` : '<div class="panel"><div class="empty-state">No networks yet. Add one, or let the app make them from the addresses it sees.</div></div>'}
        ${outside.length && networks().length ? `<div class="page-section"><span>In no network</span><small>their IP is in none of the subnets above</small></div>
            <div class="panel"><div class="table-wrap">${table([{label: 'Device'}, {label: 'IP'}, {label: 'Seen by'}], outside.map(h => `<tr>${td(hostLink(h))}${td(escapeHtml(h.ip), 'mono')}${td(escapeHtml(platformsText(h)))}</tr>`))}</div></div>` : ''}`;
    el.querySelectorAll('.ip-cell[data-ip]').forEach(c => c.addEventListener('click', () => { if (c.dataset.key) showHostDialog(c.dataset.key); }));
    el.querySelectorAll('.ip-cell').forEach(c => {
        c.addEventListener('pointermove', (e) => showTip(e.clientX, e.clientY, c.dataset.addr, c.dataset.tip ? [{value: c.dataset.tip}] : [{value: 'free'}]));
        c.addEventListener('pointerleave', hideTip);
    });
}

function netCard(net) {
    const hosts = netHosts(net);
    const up = hosts.filter(h => h.up).length;
    const c = parseCidr(net.subnet);
    let grid = '';
    let free = null;
    if (c && c.size <= 1024) {
        const byIp = new Map();
        networkHosts().forEach(h => {
            const n = ipToInt(h.ip);
            if (n === null || n < c.start || n > c.end) return;
            if (!byIp.has(n)) byIp.set(n, []);
            byIp.get(n).push(h);
        });
        const cells = [];
        free = 0;
        for (let n = c.start; n <= c.end && n < c.start + 256; n++) {
            const hs = byIp.get(n) || [];
            const special = n === c.start || n === c.end;
            const conflict = hs.filter(h => h.up).length > 1;
            const cls = conflict ? 'conflict' : hs.some(h => h.up) ? 'up' : hs.length ? 'down' : special ? 'special' : '';
            if (!hs.length && !special) free++;
            const tip = hs.map(h => `${hostName(h)}${h.up ? '' : ' (away)'}`).join(', ') || (special ? (n === c.start ? 'network address' : 'broadcast') : '');
            cells.push(`<i class="ip-cell ${cls}" data-addr="${intToIp(n)}"${tip ? ` data-tip="${attr(tip)}"` : ''}${hs.length ? ` data-ip="1" data-key="${attr(hs[0].key)}"` : ''}></i>`);
        }
        grid = `<div class="ip-grid">${cells.join('')}</div>
            <div class="ip-legend"><span><i style="background:var(--success-color)"></i>online</span><span><i style="background:var(--idle)"></i>away</span><span><i style="background:var(--grid-color)"></i>free</span><span><i style="background:var(--danger-color)"></i>two devices</span>${c.size > 256 ? `<span>first 256 of ${num(c.size)}</span>` : ''}</div>`;
    }
    return `<div class="panel net-card" style="--net:${attr(netColor(net))}"><div class="panel-body">
        <div class="net-head"><h2>${escapeHtml(net.name)}</h2>${net.vlan ? badge(`VLAN ${net.vlan}`) : ''}${net.purpose && net.purpose !== net.name ? badge(net.purpose) : ''}
            <div class="controls"><button class="btn btn-small" onclick="netEdit('${attr(net.id)}')">Edit</button></div></div>
        <div class="facts net-facts">
            <div class="fact"><div class="l">Subnet</div><div class="v">${escapeHtml(net.subnet || '–')}</div></div>
            <div class="fact"><div class="l">Online</div><div class="v">${num(up)}<small> / ${num(hosts.length)}</small></div></div>
            <div class="fact"><div class="l">Free</div><div class="v">${free === null ? '–' : num(free)}${c ? `<small> of ${num(Math.max(0, Math.min(c.size, 256) - 2))}</small>` : ''}</div></div>
        </div>
        ${net.gateway || net.ssid || net.dhcp || net.notes ? `<div class="kv" style="margin-top:10px">
            ${net.gateway ? `<div class="k">Gateway</div><div class="v">${escapeHtml(net.gateway)}</div>` : ''}
            ${net.ssid ? `<div class="k">Wi-Fi</div><div class="v">${escapeHtml(net.ssid)}</div>` : ''}
            ${net.dhcp ? `<div class="k">DHCP</div><div class="v">${escapeHtml(net.dhcp)}</div>` : ''}
            ${net.notes ? `<div class="k">Notes</div><div class="v">${escapeHtml(net.notes)}</div>` : ''}</div>` : ''}
        ${grid}
    </div></div>`;
}

function netAddSuggested() {
    const list = suggestedNetworks();
    list.forEach((s, i) => {
        const third = s.subnet.split('.')[2];
        app.doc.networks.push({
            id: uid(), name: s.purpose === 'LAN' && s.subnet.startsWith('192.168.1.') ? 'LAN' : `${s.purpose}${list.filter(x => x.purpose === s.purpose).length > 1 ? ` ${third}` : ''}`,
            vlan: s.vlan, subnet: s.subnet, purpose: s.purpose, color: NET_COLORS[(networks().length + i) % NET_COLORS.length],
        });
    });
    docChanged();
    toast(`${list.length} networks added: give them their real names and VLANs with Edit`, 'success');
    if (activeTab === 'networks') renderNetworks();
}

function netEdit(id) {
    const net = id ? networkById(id) : {name: '', vlan: '', subnet: '', purpose: 'LAN', color: NET_COLORS[networks().length % NET_COLORS.length]};
    const inUse = id ? Object.values(app.doc.devices).filter(m => m.network === id).length + mapPages().reduce((a, p) => a + p.groups.filter(g => g.network === id).length, 0) : 0;
    formDialog({
        title: id ? `Network ${net.name}` : 'New network', confirmLabel: 'Save', wide: true,
        body: `<div class="form-grid">
            <div class="form-field"><label>Name</label><input class="input" name="name" value="${attr(net.name)}" placeholder="e.g. IoT" maxlength="60"></div>
            <div class="form-field"><label>Purpose</label><select class="select" name="purpose">${NET_PURPOSES.map(p => `<option${p === net.purpose ? ' selected' : ''}>${p}</option>`).join('')}</select></div>
            <div class="form-field"><label>Subnet</label><input class="input mono" name="subnet" value="${attr(net.subnet || '')}" placeholder="192.168.30.0/24"></div>
            <div class="form-field"><label>VLAN</label><input class="input mono" name="vlan" value="${attr(net.vlan || '')}" placeholder="30, or empty when untagged"></div>
            <div class="form-field"><label>Gateway</label><input class="input mono" name="gateway" value="${attr(net.gateway || '')}" placeholder="192.168.30.1"></div>
            <div class="form-field"><label>DHCP range</label><input class="input mono" name="dhcp" value="${attr(net.dhcp || '')}" placeholder="192.168.30.100 – 199"></div>
            <div class="form-field"><label>Wi-Fi name (SSID)</label><input class="input" name="ssid" value="${attr(net.ssid || '')}"></div>
            <div class="form-field"><label>Colour</label><select class="select" name="color">${NET_COLORS.map((c, i) => `<option value="${c}"${c === net.color ? ' selected' : ''}>Colour ${i + 1}</option>`).join('')}</select></div>
            <div class="form-field full"><label>Notes</label><textarea class="input textarea" name="notes" rows="2">${escapeHtml(net.notes || '')}</textarea></div>
            ${id ? `<label class="check-row full"><input type="checkbox" name="delete"> Delete this network${inUse ? ` (${inUse} devices and groups point to it; they go back to automatic)` : ''}</label>` : ''}
        </div>`,
        validate: v => {
            if (v.delete) return '';
            if (!v.name) return 'Give it a name';
            if (v.subnet && !parseCidr(v.subnet)) return 'A subnet looks like 192.168.30.0/24';
            if (v.vlan && !/^\d{1,4}$/.test(v.vlan)) return 'A VLAN is a number from 1 to 4094';
            return '';
        },
    }).then(v => {
        if (!v) return;
        if (v.delete) {
            app.doc.networks = networks().filter(n => n.id !== id);
            Object.values(app.doc.devices).forEach(m => { if (m.network === id) delete m.network; });
            mapPages().forEach(p => p.groups.forEach(g => { if (g.network === id) delete g.network; }));
        } else {
            const c = v.subnet ? parseCidr(v.subnet) : null;
            const clean = {name: v.name, purpose: v.purpose, subnet: c ? `${intToIp(c.start)}/${c.bits}` : '', vlan: v.vlan, gateway: v.gateway, dhcp: v.dhcp, ssid: v.ssid, color: v.color, notes: v.notes};
            if (id) Object.assign(net, clean);
            else app.doc.networks.push({id: uid(), ...clean});
        }
        docChanged();
        renderNetworks();
    });
}

onTab('networks', () => renderNetworks(), () => {
    if (!document.getElementById('modal-root').children.length) renderNetworks();
});
