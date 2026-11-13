// Network dashboard - Devices: every host in one table, and the editor of one host (also used by the map).

const devState = {
    filter: stored('dev_filter', 'all'),
    search: '',
    sort: stored('dev_sort', 'name'),
    dir: stored('dev_dir', 'asc'),
    selected: new Set(),
};

// The 7-day numbers of a host, when the statistics were read
const weekStats = key => ((app.stats[7] || {}).hosts || {})[key] || null;
const onAnyPage = () => new Set(((app.doc && app.doc.pages) || []).flatMap(p => p.nodes.map(n => n.ref)).filter(Boolean));
const DAY_S = 86400;
const isNew = h => h.first_seen && Date.now() / 1000 - h.first_seen < 7 * DAY_S && (app.status && app.status.backfill && app.status.backfill.done);
const isStale = h => !h.up && h.last_changed && Date.now() / 1000 - h.last_changed > 7 * DAY_S;
const isUnstable = h => { const s = weekStats(h.key); return Boolean(s && s.drops_per_day >= 3); };

// Hosts on the same IP address, both on the network now
function ipConflicts() {
    const by = new Map();
    networkHosts().filter(h => h.up && h.ip).forEach(h => {
        if (!by.has(h.ip)) by.set(h.ip, []);
        by.get(h.ip).push(h);
    });
    return [...by.entries()].filter(([, hs]) => hs.length > 1).map(([ip, hosts]) => ({ip, hosts}));
}

const DEV_FILTERS = [
    {key: 'all', label: 'All', test: () => true},
    {key: 'online', label: 'Online', test: h => h.up},
    {key: 'away', label: 'Away', test: h => h.up === false},
    {key: 'infra', label: 'Network gear', test: h => isInfra(h)},
    {key: 'unplaced', label: 'Not on a map', test: h => !onAnyPage().has(h.key)},
    {key: 'new', label: 'New', test: h => isNew(h), title: 'First seen in the last 7 days'},
    {key: 'unstable', label: 'Unstable', test: h => isUnstable(h), title: 'Off the network 3 times a day or more this week'},
    {key: 'random', label: 'Private MAC', test: h => h.random_mac, title: 'A random ("private") Wi-Fi address: it changes per network and after a reset'},
    {key: 'gps', label: 'GPS', test: h => h.gps_only, title: 'Trackers of the mobile app: where a phone is, not whether it is on the network'},
];

function devRows() {
    const f = DEV_FILTERS.find(x => x.key === devState.filter) || DEV_FILTERS[0];
    const q = devState.search.toLowerCase();
    let list = app.hosts.filter(h => (devState.filter === 'gps' || devState.filter === 'all' ? true : !h.gps_only) && f.test(h));
    if (q) {
        list = list.filter(h => [hostName(h), h.name, h.ip, h.mac, h.host_name, h.manufacturer, h.model, h.area, kindLabel(kindOf(h)),
            ...h.trackers.map(t => t.entity_id), (networkOf(h).net || {}).name, meta(h.key).notes].join(' ').toLowerCase().includes(q));
    }
    const val = {
        name: h => hostName(h).toLowerCase(),
        state: h => (h.up ? 0 : h.up === false ? 1 : 2),
        kind: h => kindLabel(kindOf(h)),
        ip: h => ipToInt(h.ip) ?? Infinity,
        network: h => (networkOf(h).net || {}).name || '~',
        changed: h => -(h.last_changed || 0),
        avail: h => { const s = weekStats(h.key); return s && s.availability !== null ? s.availability : 2; },
        drops: h => -((weekStats(h.key) || {}).drops || 0),
    }[devState.sort] || (h => hostName(h));
    list.sort((a, b) => {
        const x = val(a), y = val(b);
        const r = x < y ? -1 : x > y ? 1 : hostName(a).localeCompare(hostName(b));
        return devState.dir === 'desc' ? -r : r;
    });
    return list;
}

function availBar(s) {
    if (!s || s.availability === null || s.availability === undefined) return '<span class="muted">–</span>';
    return meter(s.availability, plainPct(s.availability, s.availability === 1 || s.availability < 0.1 ? 0 : s.availability > 0.999 ? 2 : 1), {warn: 2, bad: 2, cls: s.availability < 0.9 && s.drops_per_day >= 3 ? 'low' : ''});
}

function renderDevices() {
    const el = document.getElementById('tab-devices');
    if (!app.doc) return;
    const rows = devRows();
    const counts = Object.fromEntries(DEV_FILTERS.map(f => [f.key, app.hosts.filter(h => (f.key === 'gps' || f.key === 'all' ? true : !h.gps_only) && f.test(h)).length]));
    const placed = onAnyPage();
    const head = [
        {label: `<input type="checkbox" aria-label="Select all" ${rows.length && rows.every(h => devState.selected.has(h.key)) ? 'checked' : ''} onclick="devSelectAll(this.checked)">`},
        {label: 'Device', sort: 'name'}, {label: 'State', sort: 'state'}, {label: 'Kind', sort: 'kind'}, {label: 'IP', sort: 'ip'},
        {label: 'MAC'}, {label: 'Network', sort: 'network'}, {label: 'Link'}, {label: 'Seen by'}, {label: 'Changed', sort: 'changed'},
        {label: 'Online (7 d)', sort: 'avail', title: 'Share of the last 7 days it was on the network'}, {label: 'Drops', sort: 'drops', n: true, title: 'Times it went off the network in the last 7 days'},
    ].map(h => h.sort ? {...h, sorted: devState.sort === h.sort ? devState.dir : ''} : h);
    const body = rows.map(h => {
        const s = weekStats(h.key);
        const net = networkOf(h);
        const conn = connectionOf(h);
        return `<tr class="${h.up === false ? 'off' : ''}${devState.selected.has(h.key) ? ' selected' : ''}" data-key="${attr(h.key)}">
            <td class="tight" onclick="event.stopPropagation()"><input type="checkbox" ${devState.selected.has(h.key) ? 'checked' : ''} onclick="devSelect('${attr(h.key)}', this.checked)" aria-label="Select"></td>
            <td><span class="host-label">${hostIconHtml(h, 'small')}<span><span class="name">${escapeHtml(hostName(h))}</span>${placed.has(h.key) ? '' : ' <span class="badge" title="Not on any page of the map">not on map</span>'}${isNew(h) ? ' <span class="badge ok">new</span>' : ''}<span class="sub">${escapeHtml([h.host_name && h.host_name !== h.name ? h.host_name : '', h.manufacturer, h.area].filter(Boolean).join(' · '))}</span></span></span></td>
            <td>${hostStateLabel(h)}</td>
            <td>${escapeHtml(kindLabel(kindOf(h)))}${meta(h.key).kind ? '' : ' <span class="muted" title="Guessed from its name and maker">?</span>'}</td>
            <td class="mono">${escapeHtml(h.ip || '–')}</td>
            <td class="mono">${h.mac ? escapeHtml(h.mac) : '<span class="muted">–</span>'}${h.random_mac ? ' <span class="badge warn" title="A random (private) Wi-Fi address">private</span>' : ''}</td>
            <td>${net.net ? netChip(net.net) : '<span class="muted">–</span>'}</td>
            <td>${escapeHtml(conn ? CONNECTIONS[conn] : '–')}${h.ssid ? `<span class="sub">${escapeHtml(h.ssid)}</span>` : ''}</td>
            <td>${escapeHtml(platformsText(h))}${h.trackers.length > 1 ? ` <span class="muted">(${h.trackers.length})</span>` : ''}</td>
            <td title="${attr(fmtDateTime(h.last_changed))}">${escapeHtml(fmtAgo(h.last_changed))}</td>
            <td>${availBar(s)}</td>
            <td class="n">${s ? num(s.drops) : '–'}</td>
        </tr>`;
    });
    const sel = devState.selected.size;
    el.innerHTML = `
        <div class="page-head"><h1>Devices</h1><span class="meta">${num(app.hosts.length)} hosts from ${num(app.hosts.reduce((a, h) => a + h.trackers.length, 0))} device trackers · trackers of different integrations with the same MAC address are one host</span></div>
        <div class="filters">
            ${chipsHtml('dev-filters', DEV_FILTERS.map(f => ({key: f.key, label: f.label, count: counts[f.key], title: f.title})), devState.filter, 'Filter')}
            <input class="input" id="dev-search" type="search" placeholder="Search name, IP, MAC, maker…" value="${attr(devState.search)}">
            <span class="range-text">${num(rows.length)} shown</span>
        </div>
        ${sel ? `<div class="bulk-bar"><b>${sel} selected</b>
            <select class="select" id="bulk-kind"><option value="">Set kind…</option>${Object.entries(app.kinds).map(([k, v]) => `<option value="${attr(k)}">${escapeHtml(v.label)}</option>`).join('')}</select>
            <select class="select" id="bulk-net"><option value="">Set network…</option><option value="-">Automatic (from the IP)</option>${networks().map(n => `<option value="${attr(n.id)}">${escapeHtml(n.name)}</option>`).join('')}</select>
            <select class="select" id="bulk-conn"><option value="">Set connection…</option>${Object.entries(CONNECTIONS).map(([k, v]) => `<option value="${k || '-'}">${escapeHtml(v)}</option>`).join('')}</select>
            <button class="btn btn-small" onclick="devState.selected.clear();renderDevices()">Clear</button></div>` : ''}
        <div class="panel"><div class="table-wrap">${table(head, body, {cls: 'hover dev-table', empty: app.hosts.length ? 'Nothing matches' : 'No device trackers in Home Assistant yet'})}</div></div>`;
    bindChips('dev-filters', key => { devState.filter = key; store('dev_filter', key); renderDevices(); });
    const search = document.getElementById('dev-search');
    search.addEventListener('input', () => {
        devState.search = search.value;
        const at = search.selectionStart;
        renderDevices();
        const again = document.getElementById('dev-search');
        again.focus();
        again.setSelectionRange(at, at);
    });
    el.querySelectorAll('th[data-sort]').forEach(th => th.addEventListener('click', () => {
        const key = th.dataset.sort;
        devState.dir = devState.sort === key && devState.dir === 'asc' ? 'desc' : 'asc';
        devState.sort = key;
        store('dev_sort', key);
        store('dev_dir', devState.dir);
        renderDevices();
    }));
    el.querySelectorAll('tbody tr[data-key]').forEach(tr => tr.addEventListener('click', () => showHostDialog(tr.dataset.key)));
    const bulk = (id, field) => {
        const s = document.getElementById(id);
        if (!s) return;
        s.addEventListener('change', () => {
            if (!s.value) return;
            devState.selected.forEach(key => setMeta(key, field, s.value === '-' ? '' : s.value));
            toast(`${devState.selected.size} devices changed`, 'success');
            renderDevices();
        });
    };
    bulk('bulk-kind', 'kind');
    bulk('bulk-net', 'network');
    bulk('bulk-conn', 'connection');
}
function devSelect(key, on) { if (on) devState.selected.add(key); else devState.selected.delete(key); renderDevices(); }
function devSelectAll(on) { devRows().forEach(h => on ? devState.selected.add(h.key) : devState.selected.delete(h.key)); renderDevices(); }

// =====================================================================
// One host: what Home Assistant knows, and what you say about it
// =====================================================================
function kindOptions(current, guess) {
    return Object.entries(app.kinds).map(([k, v]) => `<option value="${attr(k)}"${k === current ? ' selected' : ''}>${escapeHtml(v.label)}${k === guess ? ' (guessed)' : ''}</option>`).join('');
}
function networkOptions(h) {
    const id = meta(h.key).network || '';
    const auto = networkOfIp(h.ip);
    return `<option value="">Automatic${auto ? ` (${escapeHtml(auto.name)})` : h.ip ? ' (no network has this IP)' : ''}</option>`
        + networks().map(n => `<option value="${attr(n.id)}"${n.id === id ? ' selected' : ''}>${escapeHtml(n.name)}${n.vlan ? ` · VLAN ${escapeHtml(n.vlan)}` : ''}</option>`).join('');
}
function uplinkOptions(h) {
    const current = meta(h.key).uplink || '';
    const candidates = networkHosts().filter(o => o.key !== h.key && isInfra(o)).sort((a, b) => hostName(a).localeCompare(hostName(b)));
    const fromHa = h.ap_mac || h.sw_mac ? hostByMac(h.ap_mac || h.sw_mac) : null;
    return `<option value="">${fromHa ? `From Home Assistant: ${escapeHtml(hostName(fromHa))}` : 'Not set'}</option>`
        + candidates.map(o => `<option value="${attr(o.key)}"${o.key === current ? ' selected' : ''}>${escapeHtml(hostName(o))} · ${escapeHtml(kindLabel(kindOf(o)))}</option>`).join('');
}

// The editor's HTML; bind it with bindHostEditor. opts.compact: the map's side panel
function hostEditorHtml(h, opts) {
    opts = opts || {};
    const m = meta(h.key);
    const main = h.trackers.find(t => t.in_registry) || null;
    const s = weekStats(h.key);
    const ap = h.ap_mac ? hostByMac(h.ap_mac) : null;
    const sw = h.sw_mac ? hostByMac(h.sw_mac) : null;
    const facts = [
        ['IP', h.ip ? `<span class="mono">${escapeHtml(h.ip)}</span>` : '–'],
        ['MAC', h.mac ? `<span class="mono">${escapeHtml(h.mac)}</span>${h.random_mac ? ' <span class="badge warn" title="Phones and laptops use a random address per Wi-Fi network: it shows up as a new device after a reset or on another network">private address</span>' : ''}` : '–'],
        ['Host name', escapeHtml(h.host_name || '–')],
        ['Maker', escapeHtml([h.manufacturer, h.model].filter(Boolean).join(' · ') || '–')],
        ['Area', escapeHtml(h.area || '–')],
        h.ssid ? ['Wi-Fi', `${escapeHtml(h.ssid)}${h.radio ? ` · ${escapeHtml(h.radio)}` : ''}${h.signal !== null && h.signal !== undefined ? ` · ${escapeHtml(h.signal)} dBm` : ''}`] : null,
        ap ? ['Access point', hostLink(ap)] : h.ap_mac ? ['Access point', `<span class="mono">${escapeHtml(h.ap_mac)}</span>`] : null,
        sw ? ['Switch', `${hostLink(sw)}${h.sw_port ? ` · port ${escapeHtml(h.sw_port)}` : ''}`] : null,
        ['State', `${hostStateLabel(h)} <span class="muted">${escapeHtml(fmtAgo(h.last_changed))}</span>`],
        ['First seen', h.first_seen ? escapeHtml(fmtDateTime(h.first_seen)) : '<span class="muted">not in the history</span>'],
        s ? ['Last 7 days', `${plainPct(s.availability)} online · ${num(s.drops)} drops${s.longest_down ? ` · longest away ${escapeHtml(fmtDuration(s.longest_down))}` : ''}`] : null,
    ].filter(Boolean);
    const renameHint = main ? '' : '<div class="form-hint">Not in Home Assistant\'s entity registry (an old known_devices.yaml tracker): rename it there. The label below only changes the name here.</div>';
    const device = main && main.device_id;
    return `<div class="host-editor" data-key="${attr(h.key)}">
        <div class="he-head">${hostIconHtml(h, 'big')}<div><div class="he-name">${escapeHtml(hostName(h))}</div><div class="muted">${escapeHtml(kindLabel(kindOf(h)))} · ${escapeHtml(platformsText(h))}</div></div></div>
        <div class="form-field"><label>Name in Home Assistant</label>
            <div class="input-row"><input class="input" data-f="ha-name" value="${attr(main ? main.name : h.name)}" ${main ? '' : 'disabled'} maxlength="100" spellcheck="false">
            <button type="button" class="btn btn-small btn-primary" data-act="rename" ${main ? '' : 'disabled'}>Rename</button></div>
            ${device ? `<label class="check-row he-check"><input type="checkbox" data-f="rename-device" ${main.has_entity_name || !main.registry_name ? 'checked' : ''}> Rename its device "${escapeHtml(main.device_name)}" too</label>` : ''}
            ${renameHint}</div>
        <div class="form-grid${opts.compact ? ' one' : ''}">
            <div class="form-field"><label>Kind</label><select class="select" data-f="kind">${kindOptions(kindOf(h), h.guess)}</select></div>
            <div class="form-field"><label>Icon</label><div class="input-row">
                <button type="button" class="btn icon-pick" data-act="pick" title="Pick another icon">${hostIconHtml(h, 'small')}<span class="mono">${escapeHtml(hostIcon(h))}</span></button>
                ${main ? `<button type="button" class="btn btn-small" data-act="ha-icon" title="Give the tracker this icon in Home Assistant too">Use in HA</button>` : ''}</div></div>
            <div class="icon-grid full" data-grid hidden>
                <input class="input" data-icon-search type="search" placeholder="Search icons…">
                <div class="icon-cells">${Object.keys(MDI).map(n => `<button type="button" class="icon-cell${n === hostIcon(h) ? ' active' : ''}" data-icon="${attr(n)}" title="mdi:${attr(n)}"><svg viewBox="0 0 24 24">${mdiSvg(n)}</svg></button>`).join('')}</div>
                <button type="button" class="link-btn" data-icon="">Back to the icon of its kind${haIcon(h) ? ' or Home Assistant' : ''}</button>
            </div>
            <div class="form-field"><label>Network</label><select class="select" data-f="network">${networkOptions(h)}</select></div>
            <div class="form-field"><label>Connection</label><select class="select" data-f="connection">${Object.entries(CONNECTIONS).map(([k, v]) => `<option value="${k}"${k === (m.connection || '') ? ' selected' : ''}>${k === '' ? `Automatic${connectionOf(h) && !m.connection ? ` (${CONNECTIONS[connectionOf(h)]})` : ''}` : escapeHtml(v)}</option>`).join('')}</select></div>
            <div class="form-field"><label>Connected to</label><select class="select" data-f="uplink">${uplinkOptions(h)}</select></div>
            <div class="form-field"><label>Port or socket</label><input class="input" data-f="port" value="${attr(m.port || '')}" placeholder="${attr(h.sw_port ? `port ${h.sw_port}` : 'e.g. port 7, wall socket B2')}"></div>
            <div class="form-field"><label>Label on the map</label><input class="input" data-f="label" value="${attr(m.label || '')}" placeholder="${attr(h.name)}"></div>
            <div class="form-field full"><label>Notes</label><textarea class="input textarea" data-f="notes" rows="3" placeholder="Serial number, where it is, who it belongs to…">${escapeHtml(m.notes || '')}</textarea></div>
        </div>
        <div class="section-title he-sub">Home Assistant</div>
        <div class="kv">${facts.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('')}</div>
        <div class="section-title he-sub">Trackers</div>
        <div class="tracker-list">${h.trackers.map(t => `<div class="tracker"><span class="sdot ${t.up ? 'ok' : t.up === false ? '' : 'warn'}"></span>
            <span class="mono">${escapeHtml(t.entity_id)}</span><span class="muted">${escapeHtml(PLATFORM_NAMES[t.platform] || t.platform || 'known_devices.yaml')} · ${escapeHtml(t.state)}</span>
            <a class="plain-link muted" href="/history?entity_id=${attr(encodeURIComponent(t.entity_id))}" target="_top" title="Its history in Home Assistant">history ↗</a></div>`).join('')}</div>
    </div>`;
}

// opts.onChange(field): after a change of what's kept here (not the rename)
function bindHostEditor(root, key, opts) {
    opts = opts || {};
    const ed = root.querySelector(`.host-editor[data-key="${CSS.escape(key)}"]`);
    if (!ed) return;
    ed.querySelectorAll('[data-f]').forEach(input => {
        const field = input.dataset.f;
        if (field === 'ha-name' || field === 'rename-device') return;
        const handler = () => {
            setMeta(key, field, input.value.trim ? (field === 'notes' ? input.value : input.value.trim()) : input.value);
            if (opts.onChange) opts.onChange(field);
        };
        input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', handler);
    });
    const nameInput = ed.querySelector('[data-f="ha-name"]');
    const renameBtn = ed.querySelector('[data-act="rename"]');
    if (renameBtn) {
        const go = async () => {
            const h = hostByKey(key);
            const main = h && h.trackers.find(t => t.in_registry);
            if (!main) return;
            const name = nameInput.value.trim();
            const device = ed.querySelector('[data-f="rename-device"]');
            renameBtn.disabled = true;
            try {
                await apiPost('rename', {entity_id: main.entity_id, name, device: Boolean(device && device.checked)});
                toast(name ? `Renamed to ${name} in Home Assistant` : 'The integration\'s own name is back', 'success');
                await loadHosts();
                if (opts.onRenamed) opts.onRenamed();
            } catch (e) {
                toast(e.message);
            } finally {
                renameBtn.disabled = false;
            }
        };
        renameBtn.addEventListener('click', go);
        nameInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
    }
    const iconBtn = ed.querySelector('[data-act="ha-icon"]');
    if (iconBtn) iconBtn.addEventListener('click', async () => {
        const h = hostByKey(key);
        const main = h && h.trackers.find(t => t.in_registry);
        if (!main) return;
        const mdi = 'mdi:' + hostIcon(h);
        try {
            await apiPost('icon', {entity_id: main.entity_id, icon: mdi});
            toast(`${hostName(h)} has the icon ${mdi} in Home Assistant now`, 'success');
            await loadHosts();
        } catch (e) {
            toast(e.message);
        }
    });
    const grid = ed.querySelector('[data-grid]');
    const pick = ed.querySelector('[data-act="pick"]');
    const showIcon = () => {
        const h = hostByKey(key);
        pick.innerHTML = `${hostIconHtml(h, 'small')}<span class="mono">${escapeHtml(hostIcon(h))}</span>`;
        grid.querySelectorAll('.icon-cell').forEach(c => c.classList.toggle('active', c.dataset.icon === hostIcon(h)));
        const head = ed.querySelector('.he-head .kicon');
        if (head) head.outerHTML = hostIconHtml(h, 'big');
    };
    pick.addEventListener('click', () => { grid.hidden = !grid.hidden; });
    grid.addEventListener('click', (e) => {
        const cell = e.target.closest('[data-icon]');
        if (!cell) return;
        setMeta(key, 'icon', cell.dataset.icon);
        showIcon();
        if (opts.onChange) opts.onChange('icon');
    });
    const search = grid.querySelector('[data-icon-search]');
    search.addEventListener('input', () => {
        const q = search.value.trim().toLowerCase();
        grid.querySelectorAll('.icon-cell').forEach(c => { c.hidden = Boolean(q) && !c.dataset.icon.includes(q); });
    });
    const kindSelect = ed.querySelector('[data-f="kind"]');
    kindSelect.addEventListener('change', showIcon);
}

// The editor in a dialog (from the Devices table and the links elsewhere)
function showHostDialog(key) {
    const h = hostByKey(key);
    if (!h) return;
    const root = document.getElementById('modal-root');
    const draw = () => {
        const host = hostByKey(key);
        root.innerHTML = `<div class="modal-overlay"><div class="modal wide" role="dialog" aria-modal="true" aria-label="${attr(hostName(host))}">
            <div class="modal-head"><span>${escapeHtml(hostName(host))}</span><button class="modal-close" data-close aria-label="Close">✕</button></div>
            <div class="modal-body">${hostEditorHtml(host)}</div>
            <div class="modal-foot"><span class="muted foot-note">Kind, network, connection and notes are kept by this app; the name is changed in Home Assistant.</span>
                <button class="btn" onclick="closeHostDialog();switchTab('map');mapFocusHost('${attr(key)}')">Show on the map</button><button class="btn btn-primary" data-close>Done</button></div>
        </div></div>`;
        bindHostEditor(root, key, {
            onRenamed: draw,
        });
        root.querySelector('.modal-overlay').addEventListener('click', (e) => {
            if (e.target.classList.contains('modal-overlay') || e.target.hasAttribute('data-close')) closeHostDialog();
        });
    };
    draw();
    document.addEventListener('keydown', hostDialogKey);
}
function hostDialogKey(e) { if (e.key === 'Escape') closeHostDialog(); }
function closeHostDialog() {
    document.getElementById('modal-root').innerHTML = '';
    document.removeEventListener('keydown', hostDialogKey);
    if (activeTab && liveHooks[activeTab]) liveHooks[activeTab]();
}

onTab('devices', () => renderDevices(), () => {
    // Not while typing in the search or a dialog is open
    if (document.getElementById('modal-root').children.length) return;
    const focus = document.activeElement;
    if (focus && focus.id === 'dev-search') return;
    renderDevices();
});
