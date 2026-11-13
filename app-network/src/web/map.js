// Network dashboard - Map: pages to drag hosts onto, with groups, connections and notes.
//
// A page has nodes (a host, or a shape such as "Internet" that is no tracker), groups (a box with a
// name and colour, often one network or VLAN), links (a cable, Wi-Fi, fiber or VPN between two nodes)
// and texts. Dragging a group takes the nodes inside it along. Everything is saved a moment after
// the last change (see docChanged in core.js); what you set on a host (kind, network, notes) holds
// on every page.

const NODE_W = 184, NODE_H = 54, GRID = 20;
const LINK_KINDS = {
    ethernet: {label: 'Ethernet', dash: ''},
    wifi: {label: 'Wi-Fi', dash: '6 5'},
    fiber: {label: 'Fiber', dash: ''},
    vpn: {label: 'VPN', dash: '2 5'},
    virtual: {label: 'Virtual', dash: '10 4 2 4'},
};
const GROUP_COLORS = ['#3987e5', '#199e70', '#d95926', '#c98500', '#d55181', '#7c5cd6', '#2aa6b8', '#7a8492'];

const mapState = {
    pageId: stored('map_page', ''),
    sel: new Set(),          // "n:id", "g:id", "l:id", "t:id"
    drag: null,
    view: {},                // per page {x, y, k}
    snap: stored('map_snap', '1') === '1',
    undo: [],
    redo: [],
    filter: stored('map_filter', 'unplaced'),
    search: '',
    built: false,
    frame: 0,
};

const mapPages = () => (app.doc && app.doc.pages) || [];
function mapPage() {
    const pages = mapPages();
    return pages.find(p => p.id === mapState.pageId) || pages[0] || null;
}
const nodeById = id => mapPage().nodes.find(n => n.id === id) || null;
const groupById = id => mapPage().groups.find(g => g.id === id) || null;
const linkById = id => mapPage().links.find(l => l.id === id) || null;
const textById = id => mapPage().texts.find(t => t.id === id) || null;
const nodeHost = n => n && n.ref ? hostByKey(n.ref) : null;
const nodeKind = n => { if (n.page) return 'router'; const h = nodeHost(n); return h ? kindOf(h) : (n.kind || 'generic'); };
const nodeIcon = n => { if (n.page) return 'lan'; const h = nodeHost(n); return h ? hostIcon(h) : (n.icon || kindMdi(n.kind || 'generic')); };
const pageById = id => mapPages().find(p => p.id === id) || null;
// The hosts on a page and on the pages it has tiles of
function pageHosts(page, seen) {
    seen = seen || new Set();
    if (!page || seen.has(page.id)) return [];
    seen.add(page.id);
    const out = page.nodes.filter(n => n.ref).map(n => n.ref);
    page.nodes.filter(n => n.page).forEach(n => out.push(...pageHosts(pageById(n.page), seen)));
    return [...new Set(out)];
}
function pageSummary(page) {
    const hosts = pageHosts(page).map(hostByKey).filter(Boolean);
    return {total: hosts.length, up: hosts.filter(h => h.up).length, downInfra: hosts.filter(h => h.up === false && isInfra(h)).length};
}
function nodeLabel(n) {
    if (n.page) return (pageById(n.page) || {}).name || 'A deleted page';
    if (n.label) return n.label;
    const h = nodeHost(n);
    if (h) return hostName(h);
    if (n.ref) return 'Gone from Home Assistant';
    return kindLabel(n.kind || 'generic');
}
function nodeSub(n) {
    if (n.page) {
        const s = pageSummary(pageById(n.page));
        return `${s.total} devices · ${s.up} online`;
    }
    const h = nodeHost(n);
    if (!h) return n.note || '';
    const net = networkOf(h).net;
    return [h.ip || (h.gps_only ? 'GPS' : ''), net && !groupOfNetwork(net.id) ? net.name : ''].filter(Boolean).join(' · ');
}
// Whether this page has a group for the network already (then the node needn't say it)
const groupOfNetwork = id => mapPage().groups.find(g => g.network === id) || null;
const snap = v => mapState.snap ? Math.round(v / GRID) * GRID : Math.round(v);
const inGroup = (n, g) => n.x + NODE_W / 2 >= g.x && n.x + NODE_W / 2 <= g.x + g.w && n.y + NODE_H / 2 >= g.y && n.y + NODE_H / 2 <= g.y + g.h;

function getView() {
    const p = mapPage();
    if (!p) return {x: 40, y: 40, k: 1};
    if (!mapState.view[p.id]) {
        try { mapState.view[p.id] = JSON.parse(stored('map_view_' + p.id, '')); } catch (e) { /* none yet */ }
        if (!mapState.view[p.id] || !Number.isFinite(mapState.view[p.id].k)) mapState.view[p.id] = {x: 40, y: 40, k: 1, fresh: true};
    }
    return mapState.view[p.id];
}
function keepView() {
    const p = mapPage();
    if (p) store('map_view_' + p.id, JSON.stringify(getView()));
}

// =====================================================================
// Undo: a copy of the page before each change
// =====================================================================
function mapSnapshot() { return JSON.stringify(mapPage()); }
function mapCommit(before) {
    if (before === mapSnapshot()) return;
    mapState.undo.push({page: mapPage().id, data: before});
    if (mapState.undo.length > 80) mapState.undo.shift();
    mapState.redo = [];
    docChanged();
    mapToolbar();
}
// Change the page with fn(page), undoable
function mapEdit(fn) {
    const before = mapSnapshot();
    fn(mapPage());
    mapCommit(before);
    mapRender();
}
function mapUndoRedo(from, to) {
    const step = from.pop();
    if (!step) return;
    const at = mapPages().findIndex(p => p.id === step.page);
    if (at < 0) return;
    // Undoing "Move to its own page" takes the new page away again; that can't be redone
    if (!step.removePage) to.push({page: step.page, data: JSON.stringify(mapPages()[at])});
    app.doc.pages[at] = JSON.parse(step.data);
    if (step.removePage) app.doc.pages = app.doc.pages.filter(p => p.id !== step.removePage);
    mapState.pageId = step.page;
    mapState.sel.clear();
    docChanged();
    mapRender();
    mapTabs();
}
const mapUndo = () => mapUndoRedo(mapState.undo, mapState.redo);
const mapRedo = () => mapUndoRedo(mapState.redo, mapState.undo);

// =====================================================================
// The frame: page tabs, the palette, the canvas, the side panel
// =====================================================================
function mapBuild() {
    const el = document.getElementById('tab-map');
    el.innerHTML = `
        <div class="map-shell">
            <aside class="map-palette" id="map-palette">
                <div class="mp-section">
                    <input class="input" id="mp-search" type="search" placeholder="Search devices…" aria-label="Search devices">
                    <div id="mp-filters"></div>
                </div>
                <div class="mp-list" id="mp-list"></div>
                <div class="mp-section mp-shapes">
                    <div class="section-title">Shapes</div>
                    <div class="mp-shape-grid" id="mp-shapes"></div>
                </div>
            </aside>
            <section class="map-main">
                <div class="map-bar">
                    <div class="map-tabs" id="map-tabs"></div>
                    <span class="spacer"></span>
                    <div class="map-tools" id="map-tools"></div>
                </div>
                <div class="map-canvas" id="map-canvas">
                    <svg id="map-svg" xmlns="http://www.w3.org/2000/svg" tabindex="0" aria-label="Network map">
                        <defs>
                            <pattern id="map-grid" width="${GRID}" height="${GRID}" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" class="grid-dot"/></pattern>
                        </defs>
                        <rect class="map-bg" id="map-bg" x="-50000" y="-50000" width="100000" height="100000" fill="url(#map-grid)"/>
                        <g id="map-vp"><g id="map-groups"></g><g id="map-links"></g><g id="map-nodes"></g><g id="map-texts"></g><g id="map-overlay"></g></g>
                    </svg>
                    <div class="map-empty" id="map-empty" hidden></div>
                </div>
            </section>
            <aside class="map-side" id="map-side"></aside>
        </div>`;
    document.getElementById('mp-shapes').innerHTML = PALETTE_SHAPES.map(k => `<div class="mp-shape" draggable="true" data-drag="shape:${k}" title="Drag onto the map: ${attr(kindLabel(k))}">${kindIconHtml(k, 'small')}<span>${escapeHtml(kindLabel(k))}</span></div>`).join('')
        + '<div class="mp-shape" draggable="true" data-drag="group" title="Drag onto the map: a box to group devices, for a room, a rack or a VLAN"><span class="kicon small shape-box"></span><span>Group</span></div>'
        + '<div class="mp-shape" draggable="true" data-drag="text" title="Drag onto the map: a note"><span class="kicon small shape-text">T</span><span>Text</span></div>';
    const search = document.getElementById('mp-search');
    search.addEventListener('input', () => { mapState.search = search.value; mapPalette(); });
    const palette = document.getElementById('map-palette');
    palette.addEventListener('dragstart', (e) => {
        const item = e.target.closest('[data-drag]');
        if (!item) return;
        e.dataTransfer.setData('text/x-network', item.dataset.drag);
        e.dataTransfer.effectAllowed = 'copy';
    });
    palette.addEventListener('click', (e) => {
        const item = e.target.closest('[data-host]');
        if (item && item.dataset.on) mapFocusHost(item.dataset.host);
    });
    palette.addEventListener('dblclick', (e) => {
        const item = e.target.closest('[data-host]');
        if (item && !item.dataset.on) mapAddHosts([item.dataset.host]);
    });
    mapBindCanvas();
    mapState.built = true;
}

function mapTabs() {
    const el = document.getElementById('map-tabs');
    const current = mapPage();
    const parent = current && current.parent ? pageById(current.parent) : null;
    el.innerHTML = `${parent ? `<button type="button" class="btn btn-small back-btn" data-page="${attr(parent.id)}" title="Back to the page this one is a tile on">← ${escapeHtml(parent.name)}</button>` : ''}
        <div class="tabs" role="tablist">${mapPages().map(p => `<button type="button" class="tab${p === current ? ' active' : ''}" data-page="${attr(p.id)}" title="Double-click to rename">${p.parent ? '<span class="sub-mark">›</span>' : ''}${escapeHtml(p.name)}</button>`).join('')}
        <button type="button" class="tab icon-tab" data-act="add-page" title="New page">+</button></div>`;
    el.onclick = (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        if (b.dataset.act === 'add-page') return mapAddPage();
        if (b.dataset.page) {
            mapState.pageId = b.dataset.page;
            store('map_page', b.dataset.page);
            setSub(b.dataset.page);
            mapState.sel.clear();
            mapTabs();
            mapRefresh();
            if (getView().fresh) mapFit();
        }
    };
    el.ondblclick = (e) => {
        const b = e.target.closest('[data-page]');
        if (b) mapPageDialog(b.dataset.page);
    };
}

function mapToolbar() {
    const el = document.getElementById('map-tools');
    if (!el) return;
    const k = getView().k;
    el.innerHTML = `
        <div class="tabs" role="group" aria-label="Edit">
            <button type="button" class="tab icon-tab" data-act="undo" title="Undo (Ctrl+Z)" ${mapState.undo.length ? '' : 'disabled'}>↶</button>
            <button type="button" class="tab icon-tab" data-act="redo" title="Redo (Ctrl+Y)" ${mapState.redo.length ? '' : 'disabled'}>↷</button>
        </div>
        <button type="button" class="btn btn-small" data-act="auto" title="Put every device that isn't on this page yet in a group per network">Auto-place</button>
        <button type="button" class="btn btn-small" data-act="links" title="Draw the connections Home Assistant knows of (access point, switch port) and the ones you set">Draw connections</button>
        <div class="tabs" role="group" aria-label="Zoom">
            <button type="button" class="tab icon-tab" data-act="out" title="Zoom out">−</button>
            <button type="button" class="tab zoom-text" data-act="fit" title="Fit the page">${Math.round(k * 100)}%</button>
            <button type="button" class="tab icon-tab" data-act="in" title="Zoom in">+</button>
        </div>
        <button type="button" class="tab icon-tab snap-btn${mapState.snap ? ' on' : ''}" data-act="snap" title="Snap to the grid: ${mapState.snap ? 'on' : 'off'}">#</button>
        <button type="button" class="btn btn-small" data-act="export" title="Save the page as an image">Export ▾</button>`;
    el.onclick = (e) => {
        const b = e.target.closest('[data-act]');
        if (!b) return;
        ({
            undo: mapUndo, redo: mapRedo, auto: mapAutoPlace, links: mapSuggestLinks, fit: mapFit,
            in: () => mapZoom(1.25), out: () => mapZoom(0.8),
            snap: () => { mapState.snap = !mapState.snap; store('map_snap', mapState.snap ? '1' : '0'); mapToolbar(); },
            export: () => mapExportMenu(b),
        })[b.dataset.act]();
    };
}

const MAP_FILTERS = [
    {key: 'unplaced', label: 'Not here'},
    {key: 'all', label: 'All'},
    {key: 'infra', label: 'Gear'},
];
function mapPalette() {
    const page = mapPage();
    if (!page) return;
    const here = new Set(pageHosts(page));
    const q = mapState.search.toLowerCase();
    let list = networkHosts();
    const counts = {unplaced: list.filter(h => !here.has(h.key)).length, all: list.length, infra: list.filter(isInfra).length};
    if (mapState.filter === 'unplaced') list = list.filter(h => !here.has(h.key));
    if (mapState.filter === 'infra') list = list.filter(isInfra);
    if (q) list = list.filter(h => [hostName(h), h.ip, h.mac, h.host_name, h.manufacturer, kindLabel(kindOf(h))].join(' ').toLowerCase().includes(q));
    list.sort((a, b) => (isInfra(b) - isInfra(a)) || hostName(a).localeCompare(hostName(b)));
    const f = document.getElementById('mp-filters');
    f.innerHTML = chipsHtml('mp-chips', MAP_FILTERS.map(x => ({...x, count: counts[x.key]})), mapState.filter, 'Show');
    bindChips('mp-chips', key => { mapState.filter = key; store('map_filter', key); mapPalette(); });
    document.getElementById('mp-list').innerHTML = list.length ? list.map(h => {
        const on = here.has(h.key);
        return `<div class="mp-item${on ? ' on' : ''}" draggable="true" data-drag="host:${attr(h.key)}" data-host="${attr(h.key)}"${on ? ' data-on="1" title="On this page: click to find it"' : ' title="Drag onto the map, or double-click to add it"'}>
            ${hostIconHtml(h, 'small')}<span class="mp-text"><span class="mp-name">${escapeHtml(hostName(h))}</span><span class="mp-sub">${escapeHtml(h.ip || kindLabel(kindOf(h)))}</span></span>${on ? '<span class="mp-check">✓</span>' : stateDot(h)}</div>`;
    }).join('') : `<div class="empty-state">${mapState.filter === 'unplaced' && !q ? 'Every device is on this page' : 'Nothing matches'}</div>`;
}

// =====================================================================
// Drawing
// =====================================================================
function mapRender() {
    if (mapState.frame) return;
    mapState.frame = requestAnimationFrame(() => { mapState.frame = 0; mapDraw(); });
}
function mapDraw() {
    const page = mapPage();
    if (!page || !document.getElementById('map-vp')) return;
    const v = getView();
    document.getElementById('map-vp').setAttribute('transform', `translate(${v.x} ${v.y}) scale(${v.k})`);
    const sel = mapState.sel;
    document.getElementById('map-groups').innerHTML = page.groups.map(g => {
        const net = g.network ? networkById(g.network) : null;
        const color = g.color || (net && net.color) || GROUP_COLORS[7];
        const sub = net ? [net.vlan ? `VLAN ${net.vlan}` : '', net.subnet].filter(Boolean).join(' · ') : (g.note || '');
        return `<g class="mgroup${sel.has('g:' + g.id) ? ' sel' : ''}" data-group="${attr(g.id)}" style="--g:${attr(color)}">
            <rect class="mg-box" x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}" rx="12"/>
            <rect class="mg-head" x="${g.x}" y="${g.y}" width="${g.w}" height="34" rx="12"/>
            <text class="mg-name" x="${g.x + 14}" y="${g.y + 22}">${escapeHtml(g.name || 'Group')}</text>
            ${sub ? `<text class="mg-sub" x="${g.x + g.w - 14}" y="${g.y + 22}" text-anchor="end">${escapeHtml(sub)}</text>` : ''}
            <path class="mg-resize" data-resize="${attr(g.id)}" d="M${g.x + g.w - 16} ${g.y + g.h - 4}L${g.x + g.w - 4} ${g.y + g.h - 16}M${g.x + g.w - 10} ${g.y + g.h - 4}L${g.x + g.w - 4} ${g.y + g.h - 10}"/>
            <rect class="mg-grip" data-resize="${attr(g.id)}" x="${g.x + g.w - 20}" y="${g.y + g.h - 20}" width="20" height="20"/>
        </g>`;
    }).join('');
    const nodes = new Map(page.nodes.map(n => [n.id, n]));
    document.getElementById('map-links').innerHTML = page.links.map(l => {
        const a = nodes.get(l.from), b = nodes.get(l.to);
        if (!a || !b) return '';
        const [p, q] = linkEnds(a, b);
        const ha = nodeHost(a), hb = nodeHost(b);
        const down = (ha && ha.up === false) || (hb && hb.up === false);
        const kind = LINK_KINDS[l.kind] ? l.kind : 'ethernet';
        const label = [l.label, l.vlan ? `VLAN ${l.vlan}` : ''].filter(Boolean).join(' · ');
        const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
        const w = label.length * 6.4 + 12;
        return `<g class="mlink ${kind}${down ? ' down' : ''}${sel.has('l:' + l.id) ? ' sel' : ''}" data-link="${attr(l.id)}">
            <line class="ml-hit" x1="${p.x}" y1="${p.y}" x2="${q.x}" y2="${q.y}"/>
            <line class="ml-line" x1="${p.x}" y1="${p.y}" x2="${q.x}" y2="${q.y}"${LINK_KINDS[kind].dash ? ` stroke-dasharray="${LINK_KINDS[kind].dash}"` : ''}/>
            ${label ? `<rect class="ml-label-bg" x="${mx - w / 2}" y="${my - 9}" width="${w}" height="18" rx="9"/><text class="ml-label" x="${mx}" y="${my + 4}" text-anchor="middle">${escapeHtml(label)}</text>` : ''}
        </g>`;
    }).join('');
    document.getElementById('map-nodes').innerHTML = page.nodes.map(n => {
        const h = nodeHost(n);
        const ps = n.page ? pageSummary(pageById(n.page)) : null;
        const state = ps ? `pagenode ${ps.downInfra ? 'unknown' : ps.total && ps.up === ps.total ? 'up' : ''}` : h ? hostState(h) : n.ref ? 'gone' : 'shape';
        const kind = nodeKind(n);
        const name = nodeLabel(n);
        const sub = nodeSub(n);
        return `<g class="mnode ${state} fam-${kindFamily(kind)}${sel.has('n:' + n.id) ? ' sel' : ''}" data-node="${attr(n.id)}" transform="translate(${n.x} ${n.y})">
            <title>${escapeHtml([name, h ? `${h.ip || ''} ${h.mac || ''}`.trim() : '', h ? (h.up ? 'online' : h.up === false ? 'away ' + fmtAgo(h.last_changed) : 'unknown') : ''].filter(Boolean).join('\n'))}</title>
            <rect class="mn-bg" width="${NODE_W}" height="${NODE_H}" rx="10"/>
            <rect class="mn-ico" x="9" y="9" width="36" height="36" rx="9"/>
            <svg class="mn-glyph" x="15" y="15" width="24" height="24" viewBox="0 0 24 24">${mdiSvg(nodeIcon(n))}</svg>
            <text class="mn-name" x="54" y="${sub ? 24 : 32}">${escapeHtml(clip(name, 19))}</text>
            ${sub ? `<text class="mn-sub" x="54" y="41">${escapeHtml(clip(sub, 24))}</text>` : ''}
            ${h || n.ref || n.page ? `<circle class="mn-dot" cx="${NODE_W - 12}" cy="12" r="4.5"/>` : ''}
            ${n.page ? `<rect class="mn-stack" x="4" y="${NODE_H}" width="${NODE_W - 8}" height="5" rx="2"/>` : ''}
            <circle class="mn-port" data-port="${attr(n.id)}" cx="${NODE_W}" cy="${NODE_H / 2}" r="6"><title>Drag to another device to connect them</title></circle>
        </g>`;
    }).join('');
    document.getElementById('map-texts').innerHTML = page.texts.map(t => `<text class="mtext${sel.has('t:' + t.id) ? ' sel' : ''}" data-text="${attr(t.id)}" x="${t.x}" y="${t.y}" style="font-size:${Number(t.size) || 14}px">${String(t.text || 'Text').split('\n').map((line, i) => `<tspan x="${t.x}" dy="${i ? '1.25em' : 0}">${escapeHtml(line) || ' '}</tspan>`).join('')}</text>`).join('');
    const empty = document.getElementById('map-empty');
    const nothing = !page.nodes.length && !page.groups.length && !page.texts.length;
    empty.hidden = !nothing;
    if (nothing) empty.innerHTML = `<div><b>An empty page.</b><br>Drag devices from the list on the left onto it, or let the app start:<br>
        <button type="button" class="btn btn-primary" onclick="mapAutoPlace()">Place every device by network</button></div>`;
    mapOverlay();
}
const clip = (text, n) => text.length > n ? text.slice(0, n - 1) + '…' : text;

// Where a line between two nodes meets the edges of their boxes
function linkEnds(a, b) {
    const ca = {x: a.x + NODE_W / 2, y: a.y + NODE_H / 2}, cb = {x: b.x + NODE_W / 2, y: b.y + NODE_H / 2};
    const edge = (c, toward) => {
        const dx = toward.x - c.x, dy = toward.y - c.y;
        if (!dx && !dy) return c;
        const sx = (NODE_W / 2) / Math.abs(dx || 1e-9), sy = (NODE_H / 2) / Math.abs(dy || 1e-9);
        const s = Math.min(sx, sy);
        return {x: c.x + dx * s, y: c.y + dy * s};
    };
    return [edge(ca, cb), edge(cb, ca)];
}

function mapOverlay() {
    const d = mapState.drag;
    const el = document.getElementById('map-overlay');
    if (!d) { el.innerHTML = ''; return; }
    if (d.type === 'connect') {
        const n = nodeById(d.from);
        el.innerHTML = `<line class="connect-line" x1="${n.x + NODE_W}" y1="${n.y + NODE_H / 2}" x2="${d.x}" y2="${d.y}"/>`;
    } else if (d.type === 'marquee') {
        const x = Math.min(d.x0, d.x), y = Math.min(d.y0, d.y);
        el.innerHTML = `<rect class="marquee" x="${x}" y="${y}" width="${Math.abs(d.x - d.x0)}" height="${Math.abs(d.y - d.y0)}"/>`;
    } else {
        el.innerHTML = '';
    }
}

// =====================================================================
// The mouse and the keys
// =====================================================================
function mapPoint(e) {
    const svg = document.getElementById('map-svg');
    const box = svg.getBoundingClientRect();
    const v = getView();
    return {x: (e.clientX - box.left - v.x) / v.k, y: (e.clientY - box.top - v.y) / v.k};
}
const selKind = (prefix) => [...mapState.sel].filter(s => s.startsWith(prefix)).map(s => s.slice(2));

function mapBindCanvas() {
    const svg = document.getElementById('map-svg');
    svg.addEventListener('pointerdown', mapDown);
    svg.addEventListener('pointermove', mapMove);
    svg.addEventListener('pointerup', mapUp);
    svg.addEventListener('pointercancel', () => { mapState.drag = null; mapRender(); });
    svg.addEventListener('dblclick', mapDouble);
    svg.addEventListener('wheel', (e) => {
        e.preventDefault();
        const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
        mapZoom(factor, e);
    }, {passive: false});
    svg.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/x-network')) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } });
    svg.addEventListener('drop', (e) => {
        const what = e.dataTransfer.getData('text/x-network');
        if (!what) return;
        e.preventDefault();
        mapDrop(what, mapPoint(e));
    });
    svg.addEventListener('keydown', mapKey);
}

function mapDown(e) {
    if (e.button !== 0 && e.button !== 1) return;
    const svg = document.getElementById('map-svg');
    svg.focus({preventScroll: true});
    const p = mapPoint(e);
    const t = e.target;
    const port = t.closest('[data-port]');
    const resize = t.closest('[data-resize]');
    const node = t.closest('[data-node]');
    const group = t.closest('[data-group]');
    const link = t.closest('[data-link]');
    const text = t.closest('[data-text]');
    const additive = e.shiftKey || e.ctrlKey || e.metaKey;
    svg.setPointerCapture(e.pointerId);
    if (e.button === 1 || (!port && !resize && !node && !group && !link && !text && !additive)) {
        const v = getView();
        mapState.drag = {type: 'pan', cx: e.clientX, cy: e.clientY, vx: v.x, vy: v.y, moved: false};
        svg.classList.add('panning');
        return;
    }
    if (port) {
        mapState.drag = {type: 'connect', from: port.dataset.port, x: p.x, y: p.y};
        return;
    }
    if (resize) {
        const g = groupById(resize.dataset.resize);
        mapState.drag = {type: 'resize', id: g.id, x0: p.x, y0: p.y, w: g.w, h: g.h, before: mapSnapshot()};
        return;
    }
    const key = node ? 'n:' + node.dataset.node : group ? 'g:' + group.dataset.group : link ? 'l:' + link.dataset.link : text ? 't:' + text.dataset.text : '';
    if (!key) {
        mapState.drag = {type: 'marquee', x0: p.x, y0: p.y, x: p.x, y: p.y, add: additive};
        return;
    }
    if (additive) {
        if (mapState.sel.has(key)) mapState.sel.delete(key); else mapState.sel.add(key);
    } else if (!mapState.sel.has(key)) {
        mapState.sel = new Set([key]);
    }
    mapSide();
    if (key.startsWith('l:')) { mapRender(); return; }
    // Move what is selected, and the nodes and texts inside a selected group
    const page = mapPage();
    const moving = new Map();
    const add = (kind, obj) => moving.set(kind + obj.id, {obj, x: obj.x, y: obj.y});
    selKind('g:').forEach(id => {
        const g = groupById(id);
        if (!g) return;
        add('g', g);
        page.nodes.filter(n => inGroup(n, g)).forEach(n => add('n', n));
        page.texts.filter(x => x.x >= g.x && x.x <= g.x + g.w && x.y >= g.y && x.y <= g.y + g.h).forEach(x => add('t', x));
    });
    selKind('n:').forEach(id => { const n = nodeById(id); if (n) add('n', n); });
    selKind('t:').forEach(id => { const x = textById(id); if (x) add('t', x); });
    mapState.drag = {type: 'move', x0: p.x, y0: p.y, items: [...moving.values()], before: mapSnapshot(), moved: false};
    mapRender();
}

function mapMove(e) {
    const d = mapState.drag;
    const svg = document.getElementById('map-svg');
    if (!d) {
        // Show the port of the node under the pointer
        return;
    }
    if (d.type === 'pan') {
        const v = getView();
        v.x = d.vx + e.clientX - d.cx;
        v.y = d.vy + e.clientY - d.cy;
        v.fresh = false;
        if (Math.abs(e.clientX - d.cx) + Math.abs(e.clientY - d.cy) > 3) d.moved = true;
        document.getElementById('map-vp').setAttribute('transform', `translate(${v.x} ${v.y}) scale(${v.k})`);
        return;
    }
    const p = mapPoint(e);
    if (d.type === 'move') {
        const dx = p.x - d.x0, dy = p.y - d.y0;
        if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true;
        if (!d.moved) return;
        d.items.forEach(it => { it.obj.x = snap(it.x + dx); it.obj.y = snap(it.y + dy); });
    } else if (d.type === 'resize') {
        const g = groupById(d.id);
        g.w = Math.max(160, snap(d.w + p.x - d.x0));
        g.h = Math.max(80, snap(d.h + p.y - d.y0));
    } else {
        d.x = p.x;
        d.y = p.y;
    }
    svg.classList.toggle('connecting', d.type === 'connect');
    mapRender();
}

function mapUp(e) {
    const d = mapState.drag;
    mapState.drag = null;
    const svg = document.getElementById('map-svg');
    svg.classList.remove('panning', 'connecting');
    if (!d) return;
    if (d.type === 'pan') {
        keepView();
        if (!d.moved) { mapState.sel.clear(); mapSide(); mapRender(); }
        return;
    }
    if (d.type === 'move' || d.type === 'resize') {
        if (d.moved !== false) mapCommit(d.before);
    } else if (d.type === 'connect') {
        const under = document.elementFromPoint(e.clientX, e.clientY);
        const target = under && under.closest('[data-node]');
        if (target && target.dataset.node !== d.from) mapConnect(d.from, target.dataset.node);
    } else if (d.type === 'marquee') {
        const x0 = Math.min(d.x0, d.x), x1 = Math.max(d.x0, d.x), y0 = Math.min(d.y0, d.y), y1 = Math.max(d.y0, d.y);
        if (!d.add) mapState.sel.clear();
        const page = mapPage();
        page.nodes.filter(n => n.x + NODE_W > x0 && n.x < x1 && n.y + NODE_H > y0 && n.y < y1).forEach(n => mapState.sel.add('n:' + n.id));
        page.groups.filter(g => g.x >= x0 && g.x + g.w <= x1 && g.y >= y0 && g.y + g.h <= y1).forEach(g => mapState.sel.add('g:' + g.id));
        page.texts.filter(t => t.x >= x0 && t.x <= x1 && t.y >= y0 && t.y <= y1).forEach(t => mapState.sel.add('t:' + t.id));
        mapSide();
    }
    mapRender();
}

function mapDouble(e) {
    const node = e.target.closest('[data-node]');
    const group = e.target.closest('[data-group]');
    const text = e.target.closest('[data-text]');
    const link = e.target.closest('[data-link]');
    if (node && nodeById(node.dataset.node).page) {
        mapOpenPage(nodeById(node.dataset.node).page);
    } else if (node) {
        const input = document.querySelector('#map-side [data-f="ha-name"]:not([disabled]), #map-side [data-node-label]');
        if (input) { input.focus(); input.select(); }
    } else if (text) {
        mapTextDialog(text.dataset.text);
    } else if (link) {
        const input = document.querySelector('#map-side [data-link-label]');
        if (input) input.focus();
    } else if (group) {
        const input = document.querySelector('#map-side [data-group-name]');
        if (input) { input.focus(); input.select(); }
    }
}

function mapKey(e) {
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); return e.shiftKey ? mapRedo() : mapUndo(); }
    if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); return mapRedo(); }
    if (ctrl && e.key.toLowerCase() === 'a') {
        e.preventDefault();
        const p = mapPage();
        mapState.sel = new Set([...p.nodes.map(n => 'n:' + n.id), ...p.groups.map(g => 'g:' + g.id), ...p.texts.map(t => 't:' + t.id)]);
        mapSide();
        return mapRender();
    }
    if (e.key === 'Escape') { mapState.sel.clear(); mapSide(); return mapRender(); }
    if ((e.key === 'Delete' || e.key === 'Backspace') && mapState.sel.size) { e.preventDefault(); return mapDeleteSelection(); }
    const arrows = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
    if (arrows[e.key] && mapState.sel.size) {
        e.preventDefault();
        const step = e.shiftKey ? GRID : (mapState.snap ? GRID : 1);
        const [dx, dy] = arrows[e.key];
        mapEdit(page => {
            selKind('n:').forEach(id => { const n = nodeById(id); if (n) { n.x += dx * step; n.y += dy * step; } });
            selKind('t:').forEach(id => { const t = textById(id); if (t) { t.x += dx * step; t.y += dy * step; } });
            selKind('g:').forEach(id => {
                const g = groupById(id);
                if (!g) return;
                page.nodes.filter(n => inGroup(n, g) && !mapState.sel.has('n:' + n.id)).forEach(n => { n.x += dx * step; n.y += dy * step; });
                g.x += dx * step;
                g.y += dy * step;
            });
        });
    }
}

function mapZoom(factor, e) {
    const v = getView();
    const svg = document.getElementById('map-svg');
    const box = svg.getBoundingClientRect();
    const cx = e ? e.clientX - box.left : box.width / 2, cy = e ? e.clientY - box.top : box.height / 2;
    const k = Math.min(3, Math.max(0.15, v.k * factor));
    v.x = cx - (cx - v.x) * (k / v.k);
    v.y = cy - (cy - v.y) * (k / v.k);
    v.k = k;
    v.fresh = false;
    keepView();
    mapRender();
    const z = document.querySelector('#map-tools .zoom-text');
    if (z) z.textContent = `${Math.round(k * 100)}%`;
}

function pageBounds(page) {
    const xs = [], ys = [];
    page.nodes.forEach(n => { xs.push(n.x, n.x + NODE_W); ys.push(n.y, n.y + NODE_H); });
    page.groups.forEach(g => { xs.push(g.x, g.x + g.w); ys.push(g.y, g.y + g.h); });
    page.texts.forEach(t => { xs.push(t.x, t.x + 200); ys.push(t.y - 20, t.y + 10); });
    if (!xs.length) return null;
    return {x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys)};
}
function mapFit() {
    const b = pageBounds(mapPage());
    const svg = document.getElementById('map-svg');
    const box = svg.getBoundingClientRect();
    const v = getView();
    if (!b || !box.width) { Object.assign(v, {x: 40, y: 40, k: 1}); mapRender(); mapToolbar(); return; }
    const k = Math.min(1.4, Math.max(0.15, Math.min((box.width - 60) / b.w, (box.height - 60) / b.h)));
    Object.assign(v, {k, x: (box.width - b.w * k) / 2 - b.x * k, y: (box.height - b.h * k) / 2 - b.y * k, fresh: false});
    keepView();
    mapRender();
    mapToolbar();
}
// Show a host on the open page, or the first page it is on
function mapFocusHost(key) {
    let page = mapPage();
    if (!page.nodes.some(n => n.ref === key)) page = mapPages().find(p => p.nodes.some(n => n.ref === key)) || page;
    const n = page.nodes.find(x => x.ref === key);
    if (!n) { toast('Not on any page yet: drag it from the list on the left'); return; }
    if (page.id !== mapPage().id) { mapState.pageId = page.id; store('map_page', page.id); mapTabs(); mapRefresh(); }
    const svg = document.getElementById('map-svg');
    const box = svg.getBoundingClientRect();
    const v = getView();
    v.x = box.width / 2 - (n.x + NODE_W / 2) * v.k;
    v.y = box.height / 2 - (n.y + NODE_H / 2) * v.k;
    v.fresh = false;
    keepView();
    mapState.sel = new Set(['n:' + n.id]);
    mapSide();
    mapRender();
}

// =====================================================================
// Changes
// =====================================================================
function mapDrop(what, p) {
    const at = {x: snap(p.x - NODE_W / 2), y: snap(p.y - NODE_H / 2)};
    if (what.startsWith('host:')) {
        const key = what.slice(5);
        const have = mapPage().nodes.find(n => n.ref === key);
        if (have) { mapFocusHost(key); toast('Already on this page'); return; }
        if (pageHosts(mapPage()).includes(key)) { toast('It is on a page that has a tile here already'); }
        const n = {id: uid(), ref: key, ...at};
        mapEdit(page => page.nodes.push(n));
        mapState.sel = new Set(['n:' + n.id]);
    } else if (what.startsWith('shape:')) {
        const n = {id: uid(), ref: null, kind: what.slice(6), ...at};
        mapEdit(page => page.nodes.push(n));
        mapState.sel = new Set(['n:' + n.id]);
    } else if (what === 'group') {
        const g = {id: uid(), name: 'Group', x: snap(p.x - 200), y: snap(p.y - 30), w: 420, h: 220, color: GROUP_COLORS[mapPage().groups.length % GROUP_COLORS.length]};
        mapEdit(page => page.groups.push(g));
        mapState.sel = new Set(['g:' + g.id]);
    } else if (what === 'text') {
        const t = {id: uid(), x: snap(p.x), y: snap(p.y), text: 'Text', size: 14};
        mapEdit(page => page.texts.push(t));
        mapState.sel = new Set(['t:' + t.id]);
        mapTextDialog(t.id);
    }
    mapPalette();
    mapSide();
    mapRender();
}

function mapConnect(from, to) {
    const page = mapPage();
    if (page.links.some(l => (l.from === from && l.to === to) || (l.from === to && l.to === from))) { toast('These two are connected already'); return; }
    const a = nodeHost(nodeById(from)), b = nodeHost(nodeById(to));
    const wifi = [a, b].some(h => h && connectionOf(h) === 'wifi') && [nodeKind(nodeById(from)), nodeKind(nodeById(to))].includes('ap');
    const l = {id: uid(), from, to, kind: wifi ? 'wifi' : 'ethernet'};
    mapEdit(p => p.links.push(l));
    mapState.sel = new Set(['l:' + l.id]);
    mapSide();
}

function mapDeleteSelection() {
    const n = mapState.sel.size;
    mapEdit(page => {
        const nodes = new Set(selKind('n:')), groups = new Set(selKind('g:')), links = new Set(selKind('l:')), texts = new Set(selKind('t:'));
        page.nodes = page.nodes.filter(x => !nodes.has(x.id));
        page.groups = page.groups.filter(x => !groups.has(x.id));
        page.texts = page.texts.filter(x => !texts.has(x.id));
        page.links = page.links.filter(x => !links.has(x.id) && !nodes.has(x.from) && !nodes.has(x.to));
    });
    mapState.sel.clear();
    mapSide();
    mapPalette();
    toast(`Removed from this page (${n}). Ctrl+Z brings ${n > 1 ? 'them' : 'it'} back; nothing changes in Home Assistant.`, 'success');
}

function mapOpenPage(id) {
    if (!pageById(id)) { toast('That page was deleted'); return; }
    mapState.pageId = id;
    store('map_page', id);
    setSub(id);
    mapState.sel.clear();
    mapTabs();
    mapRefresh();
    if (getView().fresh) requestAnimationFrame(mapFit);
}

// A group's devices to a page of their own; here a tile of that page stays, double-click it to go in
function mapGroupToPage(groupId) {
    const page = mapPage();
    const g = groupById(groupId);
    const inside = page.nodes.filter(n => inGroup(n, g));
    if (!inside.length) { toast('There is nothing in this group'); return; }
    const ids = new Set(inside.map(n => n.id));
    const sub = {id: uid(), name: g.name || 'Group', parent: page.id, nodes: [], groups: [], links: [], texts: []};
    const dx = g.x - 40, dy = g.y - 40;
    const before = mapSnapshot();
    sub.nodes = inside.map(n => ({...n, x: n.x - dx, y: n.y - dy}));
    sub.groups = page.groups.filter(o => o.id === g.id || (o.x >= g.x && o.y >= g.y && o.x + o.w <= g.x + g.w && o.y + o.h <= g.y + g.h))
        .map(o => ({...o, x: o.x - dx, y: o.y - dy}));
    sub.texts = page.texts.filter(t => t.x >= g.x && t.x <= g.x + g.w && t.y >= g.y && t.y <= g.y + g.h).map(t => ({...t, x: t.x - dx, y: t.y - dy}));
    sub.links = page.links.filter(l => ids.has(l.from) && ids.has(l.to)).map(l => ({...l}));
    const tile = {id: uid(), page: sub.id, x: snap(g.x + g.w / 2 - NODE_W / 2), y: snap(g.y + g.h / 2 - NODE_H / 2)};
    // Cables from outside into the group end at the tile, once per device outside
    const crossing = [];
    page.links.forEach(l => {
        const a = ids.has(l.from), b = ids.has(l.to);
        if (a === b) return;
        const outside = a ? l.to : l.from;
        if (!crossing.some(c => c.from === outside)) crossing.push({...l, id: uid(), from: outside, to: tile.id});
    });
    const gone = new Set(sub.groups.map(o => o.id)), goneText = new Set(sub.texts.map(t => t.id));
    page.nodes = page.nodes.filter(n => !ids.has(n.id));
    page.nodes.push(tile);
    page.groups = page.groups.filter(o => !gone.has(o.id));
    page.texts = page.texts.filter(t => !goneText.has(t.id));
    page.links = page.links.filter(l => !ids.has(l.from) && !ids.has(l.to)).concat(crossing);
    const at = app.doc.pages.indexOf(page);
    app.doc.pages.splice(at + 1, 0, sub);
    mapState.undo.push({page: page.id, data: before, removePage: sub.id});
    mapState.redo = [];
    mapState.sel = new Set(['n:' + tile.id]);
    docChanged();
    mapTabs();
    mapRefresh();
    toast(`${inside.length} devices moved to the page ${sub.name}. Double-click its tile to go in.`, 'success');
}

function mapAddPage() {
    formDialog({
        title: 'New page', confirmLabel: 'Add',
        body: '<div class="form-field"><label>Name</label><input class="input" name="name" placeholder="e.g. Upstairs, Rack, IoT VLAN" maxlength="80"></div>',
        validate: v => v.name ? '' : 'Give the page a name',
    }).then(v => {
        if (!v) return;
        const page = {id: uid(), name: v.name, nodes: [], groups: [], links: [], texts: []};
        app.doc.pages.push(page);
        mapState.pageId = page.id;
        store('map_page', page.id);
        docChanged();
        mapState.sel.clear();
        mapTabs();
        mapRefresh();
    });
}
function mapPageDialog(id) {
    const page = mapPages().find(p => p.id === id);
    if (!page) return;
    formDialog({
        title: 'Page', confirmLabel: 'Save',
        body: `<div class="form-field"><label>Name</label><input class="input" name="name" value="${attr(page.name)}" maxlength="80"></div>
            ${mapPages().length > 1 ? `<label class="check-row" style="margin-top:14px"><input type="checkbox" name="delete"> Delete this page (${page.nodes.length} devices, ${page.groups.length} groups on it; nothing changes in Home Assistant)</label>` : ''}`,
        validate: v => v.name || v.delete ? '' : 'Give the page a name',
    }).then(v => {
        if (!v) return;
        if (v.delete) {
            app.doc.pages = app.doc.pages.filter(p => p.id !== id);
            app.doc.pages.forEach(p => {
                p.nodes = p.nodes.filter(n => n.page !== id);
                const ids = new Set(p.nodes.map(n => n.id));
                p.links = p.links.filter(l => ids.has(l.from) && ids.has(l.to));
                if (p.parent === id) p.parent = page.parent || undefined;
            });
            mapState.undo = mapState.undo.filter(u => u.page !== id);
            mapState.pageId = app.doc.pages[0].id;
        } else {
            page.name = v.name;
        }
        docChanged();
        mapTabs();
        mapRefresh();
    });
}
function mapTextDialog(id) {
    const t = textById(id);
    if (!t) return;
    formDialog({
        title: 'Text', confirmLabel: 'Save',
        body: `<div class="form-field"><label>Text</label><textarea class="input textarea" name="text" rows="4">${escapeHtml(t.text || '')}</textarea></div>
            <div class="form-field"><label>Size</label><select class="select" name="size">${[11, 14, 18, 24, 32].map(s => `<option value="${s}"${Number(t.size) === s ? ' selected' : ''}>${s}px</option>`).join('')}</select></div>`,
    }).then(v => {
        if (!v) return;
        mapEdit(() => { t.text = v.text || 'Text'; t.size = Number(v.size) || 14; });
    });
}

// Every host that isn't on this page, in a group per network; network gear first
async function mapAutoPlace() {
    if (!networks().length && suggestedNetworks().length) {
        const subnets = suggestedNetworks();
        const ok = await confirmDialog({
            title: 'Networks first?', confirmLabel: `Make ${subnets.length} networks`,
            message: `<p>There are no networks yet, so every device would land in one group. Make a network of each subnet the devices are in (${subnets.map(x => `<span class="mono">${escapeHtml(x.subnet)}</span>`).join(', ')})? You can rename them and add their VLAN on the Networks page.</p>`,
        });
        if (ok) netAddSuggested();
    }
    const page = mapPage();
    const here = new Set(pageHosts(page));
    const todo = networkHosts().filter(h => !here.has(h.key) && !h.disabled);
    if (!todo.length) { toast('Every device is on this page already', 'success'); return; }
    const COLS = 4, GAP_X = 24, GAP_Y = 22, PAD = 20, HEAD = 48;
    const b = pageBounds(page);
    const left = b ? b.x : 0;
    let cy = b ? b.y + b.h + 80 : 0;
    // The network gear as a tree at the top: what it hangs from, as far as Home Assistant or you know
    const gear = todo.filter(isInfra);
    const rest = todo.filter(h => !isInfra(h));
    const gearKeys = new Set(gear.map(h => h.key));
    const ORDER = ['internet', 'cloud', 'modem', 'firewall', 'router', 'switch', 'ap', 'server', 'nas'];
    const rank = h => { const i = ORDER.indexOf(kindOf(h)); return i < 0 ? 99 : i; };
    let groupsAdded = 0;
    mapEdit(p => {
        if (gear.length) {
            const parent = new Map();
            gear.forEach(h => {
                const up = uplinkOf(h);
                if (up && gearKeys.has(up) && up !== h.key) parent.set(h.key, up);
            });
            // Without anything known: the internet above the router, the router above the rest
            const routers = gear.filter(h => ['router', 'firewall'].includes(kindOf(h)));
            const top = routers[0];
            gear.forEach(h => {
                if (parent.has(h.key) || h === top) return;
                const k = kindOf(h);
                if (top && !['internet', 'cloud', 'modem'].includes(k)) parent.set(h.key, top.key);
            });
            if (top && !parent.has(top.key)) {
                const wan = gear.find(h => ['modem', 'internet'].includes(kindOf(h)));
                if (wan) parent.set(top.key, wan.key);
            }
            const depth = new Map();
            const depthOf = (key, seen) => {
                if (depth.has(key)) return depth.get(key);
                if (seen.has(key)) return 0;
                seen.add(key);
                const d = parent.has(key) ? depthOf(parent.get(key), seen) + 1 : 0;
                depth.set(key, d);
                return d;
            };
            gear.forEach(h => depthOf(h.key, new Set()));
            // A tidy tree: every leaf its own column, a parent above the middle of its children
            const children = new Map();
            gear.forEach(h => { const pk = parent.get(h.key) || ''; if (!children.has(pk)) children.set(pk, []); children.get(pk).push(h); });
            children.forEach(list => list.sort((x, y) => rank(x) - rank(y) || hostName(x).localeCompare(hostName(y))));
            const col = new Map();
            let next = 0;
            const place = (h, seen) => {
                if (seen.has(h.key)) return;
                seen.add(h.key);
                const kids = (children.get(h.key) || []).filter(k => !seen.has(k.key));
                if (!kids.length) { col.set(h.key, next++); return; }
                kids.forEach(k => place(k, seen));
                const cols = kids.map(k => col.get(k.key)).filter(c => c !== undefined);
                col.set(h.key, (Math.min(...cols) + Math.max(...cols)) / 2);
            };
            const seen = new Set();
            (children.get('') || []).forEach(h => place(h, seen));
            gear.filter(h => !col.has(h.key)).forEach(h => col.set(h.key, next++));
            const levels = Math.max(...gear.map(h => depth.get(h.key) || 0)) + 1;
            const w = Math.max(1, next) * (NODE_W + GAP_X) - GAP_X + PAD * 2;
            const g = {id: uid(), name: 'Network gear', x: left, y: cy, w, h: HEAD + levels * (NODE_H + 46) - 46 + PAD, color: GROUP_COLORS[0]};
            p.groups.push(g);
            groupsAdded++;
            gear.forEach(h => p.nodes.push({id: uid(), ref: h.key, x: snap(g.x + PAD + col.get(h.key) * (NODE_W + GAP_X)), y: g.y + HEAD + (depth.get(h.key) || 0) * (NODE_H + 46)}));
            cy = g.y + g.h + 70;
        }
        // The rest: a group per network, next to each other, wrapping
        const buckets = new Map();
        rest.forEach(h => {
            const net = networkOf(h).net;
            const id = net ? net.id : '';
            if (!buckets.has(id)) buckets.set(id, []);
            buckets.get(id).push(h);
        });
        const order = [...buckets.entries()].sort((x, y) => (x[0] === '') - (y[0] === '') || y[1].length - x[1].length);
        const maxRowW = Math.max(1400, (NODE_W + GAP_X) * COLS * 2 + 200);
        let cx = left, rowH = 0;
        order.forEach(([netId, hosts]) => {
            // Devices on the same switch or access point next to each other
            hosts.sort((x, y) => (uplinkOf(x) || '~').localeCompare(uplinkOf(y) || '~') || kindLabel(kindOf(x)).localeCompare(kindLabel(kindOf(y))) || hostName(x).localeCompare(hostName(y)));
            let g = netId ? p.groups.find(x => x.network === netId) : p.groups.find(x => x.name === 'Other devices');
            let start = 0;
            if (g) {
                start = p.nodes.filter(n => inGroup(n, g)).length;
            } else {
                const net = networkById(netId);
                const cols = Math.min(COLS, hosts.length);
                const w = cols * (NODE_W + GAP_X) - GAP_X + PAD * 2;
                if (cx > left && cx + w > left + maxRowW) { cx = left; cy += rowH + 60; rowH = 0; }
                g = {id: uid(), name: net ? net.name : 'Other devices', network: netId || undefined, x: cx, y: cy, w, h: HEAD, color: net ? net.color : GROUP_COLORS[7]};
                p.groups.push(g);
                groupsAdded++;
                cx += w + 60;
            }
            const cols = Math.max(1, Math.floor((g.w - PAD * 2 + GAP_X) / (NODE_W + GAP_X)));
            hosts.forEach((h, i) => {
                const at = start + i;
                p.nodes.push({id: uid(), ref: h.key, x: g.x + PAD + (at % cols) * (NODE_W + GAP_X), y: g.y + HEAD + Math.floor(at / cols) * (NODE_H + GAP_Y)});
            });
            const rows = Math.ceil((start + hosts.length) / cols);
            g.h = Math.max(g.h, HEAD + rows * (NODE_H + GAP_Y) - GAP_Y + PAD);
            rowH = Math.max(rowH, g.h);
        });
    });
    mapPalette();
    mapSide();
    mapFit();
    toast(`${todo.length} devices placed in ${groupsAdded} groups. "Draw connections" adds the cabling; Ctrl+Z undoes this.`, 'success');
}

// What a host hangs from: what you set, else the switch or access point Home Assistant names, else its via device
function uplinkOf(h) {
    const m = meta(h.key);
    if (m.uplink && hostByKey(m.uplink)) return m.uplink;
    const sw = h.sw_mac ? hostByMac(h.sw_mac) : null;
    if (sw) return sw.key;
    const ap = h.ap_mac ? hostByMac(h.ap_mac) : null;
    if (ap) return ap.key;
    return h.via || '';
}

// Connections from what Home Assistant knows (the access point or switch port of a UniFi client,
// the device a device is connected through) and from "Connected to" in a host's settings
async function mapSuggestLinks() {
    const opts = await formDialog({
        title: 'Draw connections', confirmLabel: 'Draw',
        body: `<p class="panel-sub">From what Home Assistant knows (the switch port or access point of a UniFi client, the device a device connects through) and the "Connected to" you set on a device. Connections that are there already stay.</p>
            <label class="check-row" style="margin-top:12px"><input type="checkbox" name="gear" checked> Between network gear (routers, switches, access points)</label>
            <label class="check-row" style="margin-top:8px"><input type="checkbox" name="wired" checked> Wired devices to their switch</label>
            <label class="check-row" style="margin-top:8px"><input type="checkbox" name="wifi"> Wi-Fi devices to their access point <span class="muted">(many lines)</span></label>`,
    });
    if (!opts) return;
    const page = mapPage();
    const byRef = new Map(page.nodes.filter(n => n.ref).map(n => [n.ref, n]));
    // A device on a page that has a tile here connects to the tile
    page.nodes.filter(n => n.page).forEach(tile => pageHosts(pageById(tile.page)).forEach(key => { if (!byRef.has(key)) byRef.set(key, tile); }));
    const exists = (a, b) => page.links.some(l => (l.from === a && l.to === b) || (l.from === b && l.to === a));
    const add = [];
    const sources = page.nodes.filter(n => n.ref).map(n => ({n, h: nodeHost(n)}));
    page.nodes.filter(n => n.page).forEach(tile => pageHosts(pageById(tile.page)).forEach(key => sources.push({n: tile, h: hostByKey(key)})));
    sources.forEach(({n, h}) => {
        if (!h) return;
        const m = meta(h.key);
        const candidates = [];
        if (m.uplink) candidates.push({key: m.uplink, kind: connectionOf(h) === 'wifi' ? 'wifi' : (m.connection || 'ethernet'), label: m.port || ''});
        if (h.ap_mac) { const ap = hostByMac(h.ap_mac); if (ap) candidates.push({key: ap.key, kind: 'wifi', label: ''}); }
        if (h.sw_mac) { const sw = hostByMac(h.sw_mac); if (sw) candidates.push({key: sw.key, kind: 'ethernet', label: m.port || (h.sw_port ? `port ${h.sw_port}` : '')}); }
        if (h.via) candidates.push({key: h.via, kind: connectionOf(h) === 'wifi' ? 'wifi' : 'ethernet', label: ''});
        const c = candidates.find(x => byRef.has(x.key));
        if (!c) return;
        const other = byRef.get(c.key);
        const bothGear = isInfra(h) && (!other.ref || isInfra(hostByKey(other.ref) || {key: '', trackers: [], guess: 'generic'}));
        if (bothGear ? !opts.gear : c.kind === 'wifi' ? !opts.wifi : !opts.wired) return;
        if (other.id === n.id || exists(n.id, other.id) || add.some(l => (l.from === n.id && l.to === other.id) || (l.to === n.id && l.from === other.id))) return;
        add.push({id: uid(), from: other.id, to: n.id, kind: c.kind, label: c.label || undefined});
    });
    if (!add.length) {
        toast('No new connections: Home Assistant doesn\'t say where these devices connect. Set "Connected to" on a device, or drag from the dot on its right edge to another device.');
        return;
    }
    mapEdit(p => p.links.push(...add));
    mapSide();
    toast(`${add.length} connections drawn`, 'success');
}

// =====================================================================
// The side panel: what is selected
// =====================================================================
function mapSide() {
    const el = document.getElementById('map-side');
    if (!el) return;
    const page = mapPage();
    const sel = [...mapState.sel];
    if (!sel.length) {
        const hosts = page.nodes.map(nodeHost).filter(Boolean);
        el.innerHTML = `<div class="side-body">
            <div class="section-title">${escapeHtml(page.name)}</div>
            <div class="facts">
                <div class="fact"><div class="l">Devices</div><div class="v">${num(hosts.length)}</div></div>
                <div class="fact"><div class="l">Online</div><div class="v ok">${num(hosts.filter(h => h.up).length)}</div></div>
                <div class="fact"><div class="l">Groups</div><div class="v">${num(page.groups.length)}</div></div>
                <div class="fact"><div class="l">Connections</div><div class="v">${num(page.links.length)}</div></div>
            </div>
            <div class="section-title he-sub">Legend</div>
            <div class="legend-list">
                <div><svg width="44" height="10"><line x1="2" y1="5" x2="42" y2="5" class="lg ethernet"/></svg> Ethernet</div>
                <div><svg width="44" height="10"><line x1="2" y1="5" x2="42" y2="5" class="lg wifi" stroke-dasharray="6 5"/></svg> Wi-Fi</div>
                <div><svg width="44" height="10"><line x1="2" y1="5" x2="42" y2="5" class="lg fiber"/></svg> Fiber</div>
                <div><svg width="44" height="10"><line x1="2" y1="5" x2="42" y2="5" class="lg vpn" stroke-dasharray="2 5"/></svg> VPN</div>
                <div><span class="sdot ok"></span> online <span class="sdot"></span> away</div>
            </div>
            <div class="section-title he-sub">How to</div>
            <ul class="howto">
                <li>Drag devices and shapes from the left onto the page; double-click one to add it.</li>
                <li>Drag the dot on a device's right edge to another device to connect them.</li>
                <li>A group takes the devices inside it along when you drag it. Its corner resizes it.</li>
                <li>Drag the background to move around, the wheel zooms, Shift+drag selects an area.</li>
                <li>Delete removes from this page only. Ctrl+Z undoes, arrows nudge.</li>
            </ul></div>`;
        return;
    }
    if (sel.length > 1) {
        const ns = selKind('n:').map(nodeById).filter(Boolean);
        el.innerHTML = `<div class="side-body">
            <div class="section-title">${sel.length} selected</div>
            <div class="side-actions">
                <button class="btn btn-small" data-act="group" ${ns.length ? '' : 'disabled'}>Put in a new group</button>
                <button class="btn btn-small" data-act="row" ${ns.length > 1 ? '' : 'disabled'}>Line up in a row</button>
                <button class="btn btn-small" data-act="col" ${ns.length > 1 ? '' : 'disabled'}>Line up in a column</button>
                <button class="btn btn-small" data-act="grid" ${ns.length > 2 ? '' : 'disabled'}>Arrange in a grid</button>
                <button class="btn btn-small btn-danger" data-act="del">Remove from the page</button>
            </div>
            ${ns.length ? `<div class="form-field"><label>Kind of all ${ns.filter(n => n.ref).length} devices</label><select class="select" data-bulk="kind"><option value="">Choose…</option>${Object.entries(app.kinds).map(([k, v]) => `<option value="${attr(k)}">${escapeHtml(v.label)}</option>`).join('')}</select></div>
            <div class="form-field"><label>Network of all</label><select class="select" data-bulk="network"><option value="">Choose…</option><option value="-">Automatic (from the IP)</option>${networks().map(n => `<option value="${attr(n.id)}">${escapeHtml(n.name)}</option>`).join('')}</select></div>` : ''}
        </div>`;
        el.querySelector('.side-actions').onclick = (e) => {
            const b = e.target.closest('[data-act]');
            if (!b) return;
            const act = b.dataset.act;
            if (act === 'del') return mapDeleteSelection();
            mapEdit(page => {
                const xs = ns.map(n => n.x), ys = ns.map(n => n.y);
                const x0 = Math.min(...xs), y0 = Math.min(...ys);
                if (act === 'group') {
                    const x1 = Math.max(...xs) + NODE_W, y1 = Math.max(...ys) + NODE_H;
                    const g = {id: uid(), name: 'Group', x: x0 - 20, y: y0 - 48, w: x1 - x0 + 40, h: y1 - y0 + 68, color: GROUP_COLORS[page.groups.length % GROUP_COLORS.length]};
                    page.groups.push(g);
                    mapState.sel = new Set(['g:' + g.id]);
                } else if (act === 'row') {
                    ns.sort((a, b) => a.x - b.x).forEach((n, i) => { n.x = x0 + i * (NODE_W + 24); n.y = y0; });
                } else if (act === 'col') {
                    ns.sort((a, b) => a.y - b.y).forEach((n, i) => { n.x = x0; n.y = y0 + i * (NODE_H + 22); });
                } else if (act === 'grid') {
                    const cols = Math.ceil(Math.sqrt(ns.length));
                    ns.sort((a, b) => a.y - b.y || a.x - b.x).forEach((n, i) => { n.x = x0 + (i % cols) * (NODE_W + 24); n.y = y0 + Math.floor(i / cols) * (NODE_H + 22); });
                }
            });
            mapSide();
        };
        el.querySelectorAll('[data-bulk]').forEach(s => s.addEventListener('change', () => {
            if (!s.value) return;
            ns.filter(n => n.ref).forEach(n => setMeta(n.ref, s.dataset.bulk, s.value === '-' ? '' : s.value));
            mapRender();
            mapPalette();
        }));
        return;
    }
    const [kind, id] = [sel[0].slice(0, 1), sel[0].slice(2)];
    if (kind === 'n') {
        const n = nodeById(id);
        const h = nodeHost(n);
        if (n.page) {
            const target = pageById(n.page);
            const s = pageSummary(target);
            el.innerHTML = `<div class="side-body">
                <div class="he-head">${iconHtml('lan', 'big', 'router')}<div><div class="he-name">${escapeHtml(nodeLabel(n))}</div><div class="muted">A page of the map</div></div></div>
                <div class="facts"><div class="fact"><div class="l">Devices</div><div class="v">${num(s.total)}</div></div><div class="fact"><div class="l">Online</div><div class="v ok">${num(s.up)}</div></div></div>
                ${s.downInfra ? `<div class="note warn" style="margin-top:10px">${s.downInfra} network device${s.downInfra > 1 ? 's are' : ' is'} away on that page</div>` : ''}
                <div class="side-actions">${target ? `<button class="btn btn-small btn-primary" data-act="open">Open the page</button>` : ''}<button class="btn btn-small btn-danger" data-act="del">Remove the tile</button></div>
                <div class="form-hint">Removing the tile keeps the page.</div></div>`;
            const open = el.querySelector('[data-act="open"]');
            if (open) open.onclick = () => mapOpenPage(n.page);
        } else if (h) {
            el.innerHTML = `<div class="side-body">${hostEditorHtml(h, {compact: true})}
                <div class="side-actions"><button class="btn btn-small btn-danger" data-act="del">Remove from the page</button></div></div>`;
            bindHostEditor(el, h.key, {onChange: () => { mapRender(); mapPalette(); }, onRenamed: () => { mapSide(); mapRender(); mapPalette(); }});
        } else {
            el.innerHTML = `<div class="side-body">
                <div class="he-head">${iconHtml(nodeIcon(n), 'big', nodeKind(n))}<div><div class="he-name">${escapeHtml(nodeLabel(n))}</div><div class="muted">${n.ref ? 'This tracker is no longer in Home Assistant' : 'A shape: no tracker in Home Assistant'}</div></div></div>
                <div class="form-field"><label>Label</label><input class="input" data-node-label value="${attr(n.label || '')}" placeholder="${attr(kindLabel(n.kind || 'generic'))}"></div>
                <div class="form-field"><label>Kind</label><select class="select" data-node-kind>${kindOptions(n.kind || 'generic')}</select></div>
                <div class="form-field"><label>Second line</label><input class="input" data-node-note value="${attr(n.note || '')}" placeholder="e.g. 1 Gbit/s fiber, 85.1.2.3"></div>
                <div class="form-field"><label>It is this tracker</label><select class="select" data-node-ref><option value="">None</option>${networkHosts().filter(x => !mapPage().nodes.some(m => m.ref === x.key)).sort((a, b) => hostName(a).localeCompare(hostName(b))).map(x => `<option value="${attr(x.key)}">${escapeHtml(hostName(x))}</option>`).join('')}</select>
                    <div class="form-hint">Turns this shape into that device, keeping its place and connections.</div></div>
                <div class="side-actions"><button class="btn btn-small btn-danger" data-act="del">Remove from the page</button></div></div>`;
            const bind = (sel2, fn, ev) => { const i = el.querySelector(sel2); i.addEventListener(ev || 'input', () => mapEdit(() => fn(i.value))); };
            let before = null;
            const label = el.querySelector('[data-node-label]');
            label.addEventListener('focus', () => { before = mapSnapshot(); });
            label.addEventListener('input', () => { n.label = label.value.trim() || undefined; mapRender(); });
            label.addEventListener('change', () => { if (before) mapCommit(before); before = null; });
            const note = el.querySelector('[data-node-note]');
            note.addEventListener('focus', () => { before = mapSnapshot(); });
            note.addEventListener('input', () => { n.note = note.value.trim() || undefined; mapRender(); });
            note.addEventListener('change', () => { if (before) mapCommit(before); before = null; });
            bind('[data-node-kind]', v => { n.kind = v; }, 'change');
            bind('[data-node-ref]', v => { if (v) { n.ref = v; delete n.kind; delete n.label; } }, 'change');
            el.querySelector('[data-node-ref]').addEventListener('change', () => { mapSide(); mapPalette(); });
            el.querySelector('[data-node-kind]').addEventListener('change', () => mapSide());
        }
    } else if (kind === 'g') {
        const g = groupById(id);
        const inside = mapPage().nodes.filter(n => inGroup(n, g));
        el.innerHTML = `<div class="side-body">
            <div class="section-title">Group · ${inside.length} devices</div>
            <div class="form-field"><label>Name</label><input class="input" data-group-name value="${attr(g.name || '')}" maxlength="80"></div>
            <div class="form-field"><label>Network</label><select class="select" data-group-net><option value="">None</option>${networks().map(n => `<option value="${attr(n.id)}"${n.id === g.network ? ' selected' : ''}>${escapeHtml(n.name)}${n.vlan ? ` · VLAN ${escapeHtml(n.vlan)}` : ''}</option>`).join('')}</select>
                <div class="form-hint">Shows its VLAN and subnet on the group.${networks().length ? '' : ' Add networks on the Networks page.'}</div></div>
            <div class="form-field"><label>Colour</label><div class="swatches">${GROUP_COLORS.map(c => `<button type="button" class="swatch${c === g.color ? ' active' : ''}" style="background:${c}" data-color="${c}" aria-label="${c}"></button>`).join('')}</div></div>
            <div class="form-field"><label>Note</label><input class="input" data-group-note value="${attr(g.note || '')}" placeholder="Shown when it has no network"></div>
            ${g.network && inside.some(n => n.ref) ? `<button class="btn btn-small" data-act="assign">Put its ${inside.filter(n => n.ref).length} devices in this network</button>` : ''}
            <div class="side-actions"><button class="btn btn-small" data-act="to-page" title="Its devices go to a new page; here a tile of that page stays, with how many are online">Move to its own page</button>
                <button class="btn btn-small btn-danger" data-act="del">Remove the group</button></div>
            <div class="form-hint">Removing a group keeps the devices in it.</div></div>`;
        let before = null;
        const name = el.querySelector('[data-group-name]');
        name.addEventListener('focus', () => { before = mapSnapshot(); });
        name.addEventListener('input', () => { g.name = name.value; mapRender(); });
        name.addEventListener('change', () => { if (before) mapCommit(before); before = null; });
        const note = el.querySelector('[data-group-note]');
        note.addEventListener('focus', () => { before = mapSnapshot(); });
        note.addEventListener('input', () => { g.note = note.value; mapRender(); });
        note.addEventListener('change', () => { if (before) mapCommit(before); before = null; });
        el.querySelector('[data-group-net]').addEventListener('change', (e) => {
            mapEdit(() => {
                g.network = e.target.value || undefined;
                const net = networkById(g.network);
                if (net) { if (!g.name || g.name === 'Group') g.name = net.name; if (net.color) g.color = net.color; }
            });
            mapSide();
        });
        el.querySelector('.swatches').onclick = (e) => {
            const s = e.target.closest('[data-color]');
            if (!s) return;
            mapEdit(() => { g.color = s.dataset.color; });
            mapSide();
        };
        el.querySelector('[data-act="to-page"]').onclick = () => mapGroupToPage(g.id);
        const assign = el.querySelector('[data-act="assign"]');
        if (assign) assign.onclick = () => {
            inside.filter(n => n.ref).forEach(n => setMeta(n.ref, 'network', g.network));
            toast(`${inside.filter(n => n.ref).length} devices are in ${networkById(g.network).name} now`, 'success');
            mapRender();
        };
    } else if (kind === 'l') {
        const l = linkById(id);
        const a = nodeById(l.from), b = nodeById(l.to);
        el.innerHTML = `<div class="side-body">
            <div class="section-title">Connection</div>
            <div class="link-ends">${escapeHtml(nodeLabel(a))} <span class="muted">↔</span> ${escapeHtml(nodeLabel(b))}</div>
            <div class="form-field"><label>Kind</label><select class="select" data-link-kind>${Object.entries(LINK_KINDS).map(([k, v]) => `<option value="${k}"${k === (l.kind || 'ethernet') ? ' selected' : ''}>${v.label}</option>`).join('')}</select></div>
            <div class="form-field"><label>Label</label><input class="input" data-link-label value="${attr(l.label || '')}" placeholder="e.g. port 7, 10 Gbit/s, LAG"></div>
            <div class="form-field"><label>VLAN</label><input class="input" data-link-vlan value="${attr(l.vlan || '')}" placeholder="e.g. 10, or trunk"></div>
            <div class="side-actions"><button class="btn btn-small btn-danger" data-act="del">Remove the connection</button></div></div>`;
        el.querySelector('[data-link-kind]').addEventListener('change', (e) => mapEdit(() => { l.kind = e.target.value; }));
        ['label', 'vlan'].forEach(f => {
            const i = el.querySelector(`[data-link-${f}]`);
            let before = null;
            i.addEventListener('focus', () => { before = mapSnapshot(); });
            i.addEventListener('input', () => { l[f] = i.value.trim() || undefined; mapRender(); });
            i.addEventListener('change', () => { if (before) mapCommit(before); before = null; });
        });
    } else if (kind === 't') {
        const t = textById(id);
        el.innerHTML = `<div class="side-body"><div class="section-title">Text</div><div class="guest-notes">${escapeHtml(t.text)}</div>
            <div class="side-actions"><button class="btn btn-small" onclick="mapTextDialog('${attr(t.id)}')">Edit</button><button class="btn btn-small btn-danger" data-act="del">Remove</button></div></div>`;
    }
    const del = el.querySelector('.side-actions [data-act="del"]');
    if (del) del.onclick = mapDeleteSelection;
}

// =====================================================================
// Export: the page as an SVG or PNG image, or printed
// =====================================================================
function mapExportMenu(anchor) {
    formDialog({
        title: `Export ${mapPage().name}`, confirmLabel: 'Export',
        body: `<div class="form-field"><label>As</label><select class="select" name="as">
            <option value="png">PNG image</option><option value="svg">SVG drawing (for Visio, draw.io, Inkscape)</option><option value="print">Print or save as PDF</option></select></div>
            <div class="form-field"><label>Background</label><select class="select" name="bg"><option value="theme">This theme</option><option value="white">White</option></select></div>`,
    }).then(v => { if (v) mapExport(v.as, v.bg); });
}
const STYLE_PROPS = ['fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-opacity', 'opacity', 'font-size', 'font-family', 'font-weight', 'letter-spacing', 'display', 'visibility'];
function mapExportSvg(bgChoice) {
    const page = mapPage();
    const b = pageBounds(page);
    if (!b) return null;
    const svg = document.getElementById('map-svg');
    const saved = new Set(mapState.sel);
    mapState.sel.clear();
    mapDraw();
    const clone = svg.cloneNode(true);
    const src = svg.querySelectorAll('*'), dst = clone.querySelectorAll('*');
    src.forEach((el, i) => {
        const cs = getComputedStyle(el);
        dst[i].setAttribute('style', STYLE_PROPS.map(p => `${p}:${cs.getPropertyValue(p)}`).join(';'));
    });
    mapState.sel = saved;
    mapDraw();
    clone.querySelectorAll('.mn-port, .mg-grip, .mg-resize, #map-bg, defs, title, .ml-hit').forEach(el => el.remove());
    clone.querySelector('#map-vp').removeAttribute('transform');
    const pad = 30;
    const bg = bgChoice === 'white' ? '#ffffff' : getComputedStyle(document.body).getPropertyValue('background-color');
    clone.setAttribute('viewBox', `${b.x - pad} ${b.y - pad} ${b.w + pad * 2} ${b.h + pad * 2}`);
    clone.setAttribute('width', b.w + pad * 2);
    clone.setAttribute('height', b.h + pad * 2);
    clone.removeAttribute('tabindex');
    clone.removeAttribute('class');
    clone.setAttribute('style', `background:${bg}`);
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    Object.entries({x: b.x - pad, y: b.y - pad, width: b.w + pad * 2, height: b.h + pad * 2, fill: bg}).forEach(([k, v]) => rect.setAttribute(k, v));
    clone.insertBefore(rect, clone.firstChild);
    return {text: new XMLSerializer().serializeToString(clone), w: b.w + pad * 2, h: b.h + pad * 2};
}
function downloadBlob(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
function mapExport(as, bg) {
    const out = mapExportSvg(bg);
    if (!out) { toast('Nothing on this page to export'); return; }
    const name = `network-${mapPage().name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
    if (as === 'svg') return downloadBlob(new Blob([out.text], {type: 'image/svg+xml'}), name + '.svg');
    const img = new Image();
    img.onload = () => {
        if (as === 'print') {
            const w = window.open('', '_blank');
            if (!w) { toast('The browser blocked the print window'); return; }
            w.document.write(`<!doctype html><title>${escapeHtml(mapPage().name)}</title><style>@page{size:landscape;margin:10mm}body{margin:0}img{width:100%;height:auto}</style><img src="${img.src}">`);
            w.document.close();
            setTimeout(() => w.print(), 300);
            return;
        }
        const scale = 2;
        const canvas = document.createElement('canvas');
        canvas.width = out.w * scale;
        canvas.height = out.h * scale;
        const ctx = canvas.getContext('2d');
        ctx.scale(scale, scale);
        ctx.drawImage(img, 0, 0);
        canvas.toBlob(blob => downloadBlob(blob, name + '.png'), 'image/png');
    };
    img.onerror = () => toast('Could not make the image');
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(out.text);
}

// =====================================================================
// The tab
// =====================================================================
function mapRefresh() {
    if (!mapPage()) return;
    // Selections of things that are gone
    mapState.sel = new Set([...mapState.sel].filter(s => {
        const id = s.slice(2);
        return {n: nodeById, g: groupById, l: linkById, t: textById}[s[0]](id);
    }));
    mapPalette();
    mapToolbar();
    mapSide();
    mapRender();
}

onTab('map', (sub, changed) => {
    if (!app.doc) return;
    if (!mapState.built) mapBuild();
    if (sub && mapPages().some(p => p.id === sub)) mapState.pageId = sub;
    if (!mapPages().some(p => p.id === mapState.pageId)) mapState.pageId = mapPages()[0].id;
    mapTabs();
    mapRefresh();
    if (getView().fresh) requestAnimationFrame(mapFit);
}, () => {
    if (!mapState.built || mapState.drag) return;
    // The device list and the states; not the side panel while you type in it
    mapPalette();
    mapRender();
    const focus = document.activeElement;
    if (!(focus && focus.closest && focus.closest('#map-side'))) mapSide();
});
onDoc(() => { if (mapState.built) { mapState.undo = []; mapState.redo = []; mapTabs(); mapRefresh(); } });
