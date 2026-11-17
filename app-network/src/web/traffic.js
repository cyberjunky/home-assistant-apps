// Network dashboard - Traffic: what flows now, along the cables the app knows of. Measured where an
// integration has a traffic sensor for a switch port (MikroTik Network, NETGEAR Plus) and on the
// internet connection (the router, internet.js). Nobody here tells who talks to whom: a line is
// what goes through one link, to and from everything behind it.

const trafficState = {data: null, timer: null, loading: null};
const TRAFFIC_EVERY = 5000;

async function loadTraffic() {
    if (trafficState.loading) return trafficState.loading;
    trafficState.loading = api('traffic').then(data => {
        trafficState.data = data;
        app.traffic = data.ports;
        return data;
    }).finally(() => { trafficState.loading = null; });
    return trafficState.loading;
}

// The tree of what hangs from what, with the traffic on every link that is measured.
// A node: {id, host, name, sub, kind, parent, down, up, measured, port}; down goes to the node (and
// what is behind it), up comes from it.
function trafficModel() {
    const data = trafficState.data || {ports: app.traffic, internet: null};
    const rate = new Map(data.ports.map(t => [`${t.owner}|${t.port}`, t]));
    const hosts = networkHosts();
    const nodes = new Map();
    const add = (id, props) => {
        const n = {id, host: null, name: '', sub: '', kind: '', parent: '', down: null, up: null, measured: false, port: '', ...props};
        nodes.set(id, n);
        return n;
    };
    hosts.forEach(h => add(h.key, {host: h, name: hostName(h), kind: kindOf(h)}));

    // What every device hangs from, and the devices that sit on a switch port themselves
    const onPort = new Map();
    hosts.forEach(h => {
        const n = nodes.get(h.key);
        const up = uplinkOf(h);
        if (up && up !== h.key && nodes.has(up)) n.parent = up;
        const sw = h.sw_mac ? hostByMac(h.sw_mac) : null;
        if (sw && sw.key === n.parent && h.sw_port) {
            const k = `${h.sw_mac}|${h.sw_port}`;
            if (!onPort.has(k)) onPort.set(k, []);
            onPort.get(k).push(h);
        }
    });
    const used = new Set();
    onPort.forEach((members, k) => {
        const t = rate.get(k);
        if (!t) return;
        used.add(k);
        const port = k.slice(k.indexOf('|') + 1);
        if (members.length === 1) {
            Object.assign(nodes.get(members[0].key), {down: t.tx, up: t.rx, measured: true, port});
            return;
        }
        // Several devices on one port (a switch the app does not know of): the port's traffic is theirs together
        const sw = hostByMac(members[0].sw_mac);
        const group = add(`port:${k}`, {name: `Port ${port}`, sub: `${members.length} devices`, kind: 'unmanaged_switch', parent: sw.key, down: t.tx, up: t.rx, measured: true, port});
        members.forEach(m => { nodes.get(m.key).parent = group.id; });
    });
    // A switch's own port up to what it hangs from, and its ports with a link where nothing is known
    Object.entries(app.ports || {}).forEach(([mac, ports]) => {
        const sw = hostByMac(mac);
        const n = sw && nodes.get(sw.key);
        if (!n) return;
        const key = p => `${mac}|${p.port}`;
        const open = ports.filter(p => p.kind === 'ethernet' && p.up && rate.has(key(p)) && !used.has(key(p)));
        const uplinks = open.filter(p => !p.connected.length && ((p.seen || 0) > 0 || p.neighbor));
        if (!n.measured && n.parent && uplinks.length === 1) {
            const t = rate.get(key(uplinks[0]));
            Object.assign(n, {down: t.rx, up: t.tx, measured: true, port: `its ${uplinks[0].port}`});
            used.add(key(uplinks[0]));
        }
        open.filter(p => !used.has(key(p))).forEach(p => {
            const t = rate.get(key(p));
            const isUplink = uplinks.includes(p);
            add(`port:${key(p)}`, {name: `Port ${p.port}`, sub: isUplink ? `uplink${p.neighbor ? ` to ${p.neighbor}` : ''}` : 'unknown device', kind: isUplink ? 'unmanaged_switch' : 'generic', parent: sw.key, down: t.tx, up: t.rx, measured: true, port: p.port});
        });
    });
    // The internet connection, on the router that tells it
    if (data.internet) {
        const routers = hosts.filter(h => kindOf(h) === 'router');
        const router = hosts.find(h => h.ip && h.ip === data.internet.address) || (routers.length === 1 ? routers[0] : null);
        const net = add('internet', {name: 'Internet', kind: 'internet'});
        if (router) Object.assign(nodes.get(router.key), {parent: 'internet', down: data.internet.down, up: data.internet.up, measured: true, port: 'internet connection'});
        else Object.assign(net, {down: data.internet.down, up: data.internet.up, measured: true, alone: true});
    }

    // Shown: what is measured, and what it hangs from, up to the top
    const shown = new Set();
    nodes.forEach(n => {
        if (!n.measured) return;
        for (let x = n, guard = 0; x && !shown.has(x.id) && guard < 50; x = nodes.get(x.parent), guard++) shown.add(x.id);
    });
    const total = n => (n.down || 0) + (n.up || 0);
    const kids = new Map();
    shown.forEach(id => {
        const n = nodes.get(id);
        const parent = shown.has(n.parent) ? n.parent : '';
        if (!kids.has(parent)) kids.set(parent, []);
        kids.get(parent).push(n);
    });
    kids.forEach(list => list.sort((a, b) => total(b) - total(a) || a.name.localeCompare(b.name)));
    // Left to right by depth; a leaf takes a row, what it hangs from sits in the middle of its leaves
    let rows = 0, depth = 0;
    const placed = new Set();
    const place = (n, d) => {
        if (placed.has(n.id)) return;
        placed.add(n.id);
        n.depth = d;
        depth = Math.max(depth, d);
        const below = (kids.get(n.id) || []).filter(k => !placed.has(k.id));
        if (!below.length) { n.row = rows++; return; }
        below.forEach(k => place(k, d + 1));
        n.row = (below[0].row + below[below.length - 1].row) / 2;
    };
    const roots = kids.get('') || [];
    // The internet first: everything hangs from it
    roots.sort((a, b) => (b.id === 'internet') - (a.id === 'internet'));
    roots.forEach(r => place(r, 0));
    const list = [...placed].map(id => nodes.get(id));
    return {
        nodes: list, byId: nodes, rows, depth,
        measured: list.filter(n => n.measured),
        left: hosts.filter(h => !placed.has(h.key)).length,
        internet: data.internet,
    };
}

const trafficRates = n => `<span class="tf-down" title="To it">↓ ${fmtRate(n.down)}</span> <span class="tf-up" title="From it">↑ ${fmtRate(n.up)}</span>`;
function trafficNodeName(n) {
    if (n.host) return `<a href="#devices/${attr(encodeURIComponent(n.host.key))}" class="plain-link" onclick="openHost('${attr(n.host.key)}');return false">${escapeHtml(n.name)}</a>`;
    return escapeHtml(n.name);
}

// The diagram: boxes in columns by how far they are from the top, a line to what each hangs from
function trafficDiagram(m) {
    const COL = 300, BOX = 240, ROW = 58, H = 44;
    const width = m.depth * COL + BOX, height = Math.max(1, m.rows) * ROW;
    const max = Math.max(1, ...m.measured.filter(n => !n.alone).map(n => (n.down || 0) + (n.up || 0)));
    const y = n => n.row * ROW + ROW / 2;
    // The moving dashes keep their place between two readings
    const phase = -((Date.now() % 1200) / 1000).toFixed(3);
    const lines = m.nodes.filter(n => n.parent && m.byId.get(n.parent) && m.byId.get(n.parent).depth !== undefined && n.depth > 0).map(n => {
        const p = m.byId.get(n.parent);
        const x1 = p.depth * COL + BOX, x2 = n.depth * COL, y1 = y(p), y2 = y(n);
        const d = `M${x1},${y1} C${x1 + 30},${y1} ${x2 - 30},${y2} ${x2},${y2}`;
        if (!n.measured) return `<path class="tf-line none" d="${d}"/>`;
        const sum = (n.down || 0) + (n.up || 0);
        const w = (2 + 12 * Math.sqrt(sum / max)).toFixed(1);
        const tip = `${n.name}${n.port ? ` · ${n.port}` : ''}\n↓ ${fmtRate(n.down)} to it\n↑ ${fmtRate(n.up)} from it`;
        return `<path class="tf-line" d="${d}" stroke-width="${w}"><title>${escapeHtml(tip)}</title></path>`
            + (sum > 0 ? `<path class="tf-flow${(n.up || 0) > (n.down || 0) ? ' back' : ''}" d="${d}" stroke-width="${Math.max(1, w / 3).toFixed(1)}" style="animation-delay:${phase}s"/>` : '');
    }).join('');
    const boxes = m.nodes.map(n => {
        // The top of a tree has no link above it to measure
        const top = !n.parent || !m.byId.get(n.parent) || m.byId.get(n.parent).depth === undefined;
        const sub = n.measured ? trafficRates(n) : `<span class="muted">${n.id === 'internet' ? 'through your router' : top ? 'uplink not known' : 'not measured'}</span>`;
        const extra = n.host ? '' : n.sub;
        return `<div class="tf-node${n.measured ? '' : ' none'}${n.host && n.host.up === false ? ' away' : ''}" style="left:${n.depth * COL}px;top:${y(n) - H / 2}px;width:${BOX}px;height:${H}px" title="${attr([n.name, n.port, extra].filter(Boolean).join(' · '))}">
            ${n.host ? hostIconHtml(n.host, 'small') : kindIconHtml(n.kind, 'small')}
            <div class="tf-text"><div class="tf-name">${trafficNodeName(n)}${extra ? ` <span class="muted">${escapeHtml(extra)}</span>` : ''}</div><div class="tf-sub mono">${sub}</div></div></div>`;
    }).join('');
    return `<div class="tf-scroll"><div class="tf-diagram" style="width:${width}px;height:${height}px">
        <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">${lines}</svg>${boxes}</div></div>
        <div class="legend"><span>↓ to the device and what is behind it</span><span>↑ from it</span><span><i class="line" style="background:var(--series-1)"></i>wider with more traffic</span><span><i class="line tf-legend-none"></i>no measurement on this link</span></div>`;
}

function trafficTable(m) {
    const rows = m.measured.filter(n => !n.alone).sort((a, b) => ((b.down || 0) + (b.up || 0)) - ((a.down || 0) + (a.up || 0)));
    const max = Math.max(1, ...rows.map(n => (n.down || 0) + (n.up || 0)));
    return table(
        [{label: 'Device or port'}, {label: 'Through'}, {label: '↓ To it', n: true}, {label: '↑ From it', n: true}, {label: ''}],
        rows.map(n => {
            const p = m.byId.get(n.parent);
            const through = [p ? (p.host ? hostLink(p.host) : escapeHtml(p.name)) : '', n.port && n.port !== n.name.replace(/^Port /, '') ? `<span class="mono muted">${escapeHtml(n.port)}</span>` : ''].filter(Boolean).join(' ');
            const share = ((n.down || 0) + (n.up || 0)) / max * 100;
            return `<tr>${td(n.host ? hostLink(n.host) : `<span class="host-label">${kindIconHtml(n.kind, 'small')}<span>${escapeHtml(n.name)} <span class="muted">${escapeHtml(n.sub)}</span></span></span>`)}${td(through)}${td(fmtRate(n.down), 'n mono')}${td(fmtRate(n.up), 'n mono')}${td(`<span class="tf-bar"><i style="width:${share.toFixed(1)}%"></i></span>`)}</tr>`;
        }),
        {empty: 'No link is measured yet'},
    );
}

// Switches whose ports the app knows, without a traffic sensor on any of them
function trafficMissing() {
    const have = new Set(app.traffic.map(t => t.owner));
    return Object.entries(app.ports || {})
        .filter(([mac, ports]) => ports.some(p => p.kind === 'ethernet') && !have.has(mac))
        .map(([mac]) => hostByMac(mac)).filter(Boolean);
}

function renderTraffic() {
    const el = document.getElementById('tab-traffic');
    if (!app.doc || !app.status) return;
    const m = trafficModel();
    const busiest = m.measured.filter(n => !n.alone && n.id !== 'internet').sort((a, b) => ((b.down || 0) + (b.up || 0)) - ((a.down || 0) + (a.up || 0)))[0];
    const missing = trafficMissing();
    const sources = `<div class="tf-notes">The <b>internet connection</b> comes from your router, over UPnP or TR-064 (see the Dashboard). A <b>switch port</b> is measured when its integration has a traffic sensor for it in Home Assistant:
        <a href="https://github.com/cyberjunky/home-assistant-mikrotik" target="_blank" rel="noopener">MikroTik Network</a> has two per port, off by default (turn on <i>received</i> and <i>sent</i> of the ports you want, under the MikroTik's entities), and
        <a href="https://github.com/cyberjunky/home-assistant-netgear" target="_blank" rel="noopener">NETGEAR Switch</a> and
        <a href="https://github.com/ckarrie/ha-netgear-plus" target="_blank" rel="noopener">NETGEAR Plus</a> have them for every port of a NETGEAR Plus switch. A FRITZ!Box tells its internet traffic, not the traffic per device.
        A line is what goes through one cable, to and from everything behind it; no integration tells which device talks to which.</div>`;
    el.innerHTML = `
        <div class="tiles kpi-tiles">
            ${tile('Internet down', m.internet ? fmtRate(m.internet.down) : '–', m.internet ? 'now, from your router' : 'no router read')}
            ${tile('Internet up', m.internet ? fmtRate(m.internet.up) : '–', '&nbsp;')}
            ${tile('Links measured', num(m.measured.filter(n => !n.alone).length), m.left ? `${num(m.left)} devices without a measurement` : 'every device')}
            ${tile('Busiest now', busiest ? escapeHtml(busiest.name) : '–', busiest ? `↓ ${fmtRate(busiest.down)} ↑ ${fmtRate(busiest.up)}` : '&nbsp;')}
        </div>
        ${missing.length ? setupHint(`No traffic sensors for ${missing.map(hostLink).join(', ')}: its ports are known, what goes through them is not. With MikroTik Network, turn on the <i>received</i> and <i>sent</i> sensors of its ports in Home Assistant.`, 'info') : ''}
        ${m.nodes.length
            ? panel('Traffic now', `<span id="tf-age">every ${TRAFFIC_EVERY / 1000} seconds</span>`, trafficDiagram(m)) + panel('Busiest links', 'now', trafficTable(m), {cls: 'flush'})
            : '<div class="empty-state">Nothing is measured yet: no router is read and no switch port has a traffic sensor.</div>'}
        ${panel('Where this comes from', '', sources)}`;
}

onTab('traffic', () => {
    renderTraffic();
    loadTraffic().then(() => { if (activeTab === 'traffic') renderTraffic(); }).catch(() => {});
    if (!trafficState.timer) {
        trafficState.timer = setInterval(() => {
            if (activeTab !== 'traffic' || document.hidden) return;
            loadTraffic().then(() => { if (activeTab === 'traffic') renderTraffic(); }).catch(() => {});
        }, TRAFFIC_EVERY);
    }
}, () => renderTraffic());
