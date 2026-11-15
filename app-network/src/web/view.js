// Network dashboard - the map on the Dashboard, to look at, not to change. Two ways to show a page:
// - Cards (the default): every group of the page as a card with its network, how many of its devices
//   are online and the devices themselves, in the order they are drawn. Readable at any size.
// - Drawing: the page as drawn in the editor, at a readable zoom; drag to move around, Ctrl + wheel
//   or the buttons to zoom.
// Either way a click on a device shows its details and on a page tile goes in; "Edit this page"
// opens the editor on it.

const viewState = {
    pageId: stored('view_page', ''),
    mode: stored('view_mode', 'cards'),
    views: {},          // per page {x, y, k}, fitted the first time a page is shown
    drag: null,
    built: false,
    frame: 0,
};

function viewPage() {
    const pages = mapPages();
    return pages.find(p => p.id === viewState.pageId) || pages.find(p => !p.parent) || pages[0] || null;
}
function viewOf(page) {
    if (!viewState.views[page.id]) viewState.views[page.id] = {x: 0, y: 0, k: 1, fresh: true};
    return viewState.views[page.id];
}

function viewBuild(el) {
    el.innerHTML = `<div class="panel dash-map"><div class="panel-body">
        <div class="map-bar view-bar">
            <div class="map-tabs" id="dv-tabs"></div>
            <span class="spacer"></span>
            <span class="view-hint muted" data-drawing>Ctrl + wheel zooms</span>
            <div class="tabs" role="group" aria-label="Zoom" id="dv-zoom" data-drawing>
                <button type="button" class="tab icon-tab" data-act="out" title="Zoom out">−</button>
                <button type="button" class="tab zoom-text" data-act="fit" title="Fit the page">100%</button>
                <button type="button" class="tab icon-tab" data-act="in" title="Zoom in">+</button>
            </div>
            <div class="tabs" role="group" aria-label="Show as" id="dv-mode">
                <button type="button" class="tab chip" data-mode="cards" title="Every group as a card with its devices">Cards</button>
                <button type="button" class="tab chip" data-mode="drawing" title="The page as drawn in the editor">Drawing</button>
            </div>
            <button type="button" class="btn btn-small" data-act="edit" title="Open this page in the editor">Edit this page</button>
        </div>
        <div class="ncards" id="dv-cards"></div>
        <div class="map-canvas view-canvas" data-drawing>
            <svg id="dv-svg" class="view-svg" xmlns="http://www.w3.org/2000/svg" aria-label="Network map">
                <defs><pattern id="dv-grid" width="${GRID}" height="${GRID}" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" class="grid-dot"/></pattern></defs>
                <rect class="map-bg" x="-50000" y="-50000" width="100000" height="100000" fill="url(#dv-grid)"/>
                <g id="dv-vp"><g id="dv-groups"></g><g id="dv-links"></g><g id="dv-nodes"></g><g id="dv-texts"></g></g>
            </svg>
            <div class="map-empty" id="dv-empty" hidden></div>
        </div>
    </div></div>`;
    const bar = el.querySelector('.view-bar');
    bar.addEventListener('click', (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        if (b.dataset.page) return viewOpen(b.dataset.page);
        if (b.dataset.mode) return viewMode(b.dataset.mode);
        const page = viewPage();
        if (!page) return;
        ({
            out: () => viewZoom(0.8), in: () => viewZoom(1.25), fit: () => viewFit(),
            edit: () => switchTab('map', page.id),
        })[b.dataset.act]();
    });
    const svg = el.querySelector('#dv-svg');
    svg.addEventListener('pointerdown', viewDown);
    svg.addEventListener('pointermove', viewMove);
    svg.addEventListener('pointerup', viewUp);
    svg.addEventListener('pointercancel', () => { viewState.drag = null; svg.classList.remove('panning'); });
    // A plain wheel scrolls the dashboard; with Ctrl (or a pinch on a touchpad) it zooms the map
    svg.addEventListener('wheel', (e) => {
        if (!e.ctrlKey && !e.metaKey) return;
        e.preventDefault();
        viewZoom(Math.exp(-e.deltaY * 0.01), e);
    }, {passive: false});
    new ResizeObserver(() => { const p = viewPage(); if (p && viewOf(p).fresh) viewFit(); }).observe(el.querySelector('.view-canvas'));
    el.querySelector('#dv-cards').addEventListener('click', (e) => {
        const item = e.target.closest('[data-node]');
        if (item) viewActivate(item.dataset.node);
    });
    viewState.built = true;
    viewMode(viewState.mode, true);
}

function viewTabs() {
    const el = document.getElementById('dv-tabs');
    if (!el) return;
    const current = viewPage();
    const parent = current && current.parent ? pageById(current.parent) : null;
    const html = `${parent ? `<button type="button" class="btn btn-small back-btn" data-page="${attr(parent.id)}" title="Back to the page this one is a tile on">← ${escapeHtml(parent.name)}</button>` : ''}
        <div class="tabs" role="tablist">${mapPages().map(p => `<button type="button" class="tab${p === current ? ' active' : ''}" data-page="${attr(p.id)}">${p.parent ? '<span class="sub-mark">›</span>' : ''}${escapeHtml(p.name)}</button>`).join('')}</div>`;
    if (el.innerHTML !== html) el.innerHTML = html;
}

function viewMode(mode, quiet) {
    viewState.mode = mode === 'drawing' ? 'drawing' : 'cards';
    if (!quiet) store('view_mode', viewState.mode);
    const drawing = viewState.mode === 'drawing';
    document.querySelectorAll('#ov-map [data-drawing]').forEach(e => { e.hidden = !drawing; });
    document.getElementById('dv-cards').hidden = drawing;
    document.querySelectorAll('#dv-mode [data-mode]').forEach(b => b.classList.toggle('active', b.dataset.mode === viewState.mode));
    viewDraw();
    const page = viewPage();
    if (drawing && page && viewOf(page).fresh) requestAnimationFrame(viewFit);
}

// A device shows its details, a page tile goes in
function viewActivate(nodeId) {
    const n = (viewPage() || {nodes: []}).nodes.find(x => x.id === nodeId);
    if (!n) return;
    if (n.page && pageById(n.page)) viewOpen(n.page);
    else if (n.ref && hostByKey(n.ref)) openHost(n.ref);
}

function viewOpen(id) {
    viewState.pageId = id;
    store('view_page', id);
    viewTabs();
    viewDraw();
    if (viewState.mode === 'drawing' && viewOf(viewPage()).fresh) viewFit();
}

function viewRender() {
    if (viewState.frame) return;
    viewState.frame = requestAnimationFrame(() => { viewState.frame = 0; viewDraw(); });
}
function viewDraw() {
    const page = viewPage();
    const vp = document.getElementById('dv-vp');
    if (!vp) return;
    if (viewState.mode === 'cards') {
        const cards = document.getElementById('dv-cards');
        const html = page ? viewCardsHtml(page) : '<div class="empty-state">No map yet: draw one in the map editor.</div>';
        if (cards.innerHTML !== html) cards.innerHTML = html;
        return;
    }
    const empty = document.getElementById('dv-empty');
    if (!page) {
        ['dv-groups', 'dv-links', 'dv-nodes', 'dv-texts'].forEach(id => { document.getElementById(id).innerHTML = ''; });
        empty.hidden = false;
        empty.innerHTML = '<div><b>No map yet.</b><br>Draw one in the map editor.</div>';
        return;
    }
    const v = viewOf(page);
    vp.setAttribute('transform', `translate(${v.x} ${v.y}) scale(${v.k})`);
    const parts = pageSvg(page, {});
    for (const [id, html] of [['dv-groups', parts.groups], ['dv-links', parts.links], ['dv-nodes', parts.nodes], ['dv-texts', parts.texts]]) {
        const el = document.getElementById(id);
        if (el.innerHTML !== html) el.innerHTML = html;
    }
    const nothing = !page.nodes.length && !page.groups.length && !page.texts.length;
    empty.hidden = !nothing;
    if (nothing) empty.innerHTML = '<div><b>This page is empty.</b><br>Place devices on it in the map editor.</div>';
    const z = document.querySelector('#dv-zoom .zoom-text');
    if (z) z.textContent = `${Math.round(v.k * 100)}%`;
}

function viewFit() {
    const page = viewPage();
    const svg = document.getElementById('dv-svg');
    if (!page || !svg) return;
    const box = svg.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const b = pageBounds(page);
    const v = viewOf(page);
    if (!b) Object.assign(v, {x: 40, y: 40, k: 1});
    else {
        // Whole when it fits at a readable size; else readable, from its top left
        const fit = Math.min((box.width - 40) / b.w, (box.height - 40) / b.h);
        const k = Math.min(1, Math.max(0.6, fit));
        const x = b.w * k <= box.width - 40 ? (box.width - b.w * k) / 2 - b.x * k : 20 - b.x * k;
        const y = b.h * k <= box.height - 40 ? (box.height - b.h * k) / 2 - b.y * k : 20 - b.y * k;
        Object.assign(v, {k, x, y});
    }
    v.fresh = false;
    viewRender();
}

function viewZoom(factor, e) {
    const page = viewPage();
    const svg = document.getElementById('dv-svg');
    if (!page || !svg) return;
    const v = viewOf(page);
    const box = svg.getBoundingClientRect();
    const cx = e ? e.clientX - box.left : box.width / 2, cy = e ? e.clientY - box.top : box.height / 2;
    const k = Math.min(3, Math.max(0.1, v.k * factor));
    v.x = cx - (cx - v.x) * (k / v.k);
    v.y = cy - (cy - v.y) * (k / v.k);
    v.k = k;
    v.fresh = false;
    viewRender();
}

function viewDown(e) {
    if (e.button !== 0) return;
    const page = viewPage();
    if (!page) return;
    const v = viewOf(page);
    viewState.drag = {x0: e.clientX, y0: e.clientY, vx: v.x, vy: v.y, moved: false, target: e.target};
    e.currentTarget.setPointerCapture(e.pointerId);
}
function viewMove(e) {
    const d = viewState.drag;
    if (!d) return;
    const dx = e.clientX - d.x0, dy = e.clientY - d.y0;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    e.currentTarget.classList.add('panning');
    const v = viewOf(viewPage());
    v.x = d.vx + dx;
    v.y = d.vy + dy;
    v.fresh = false;
    viewRender();
}
function viewUp(e) {
    const d = viewState.drag;
    viewState.drag = null;
    e.currentTarget.classList.remove('panning');
    if (!d || d.moved) return;
    const node = d.target.closest && d.target.closest('[data-node]');
    if (node) viewActivate(node.dataset.node);
}

// The dashboard calls this on every change: build once, then only draw
function viewRefresh(el) {
    if (!viewState.built || !el.querySelector('#dv-svg')) { viewState.built = false; viewBuild(el); }
    viewTabs();
    viewDraw();
    const page = viewPage();
    if (viewState.mode === 'drawing' && page && viewOf(page).fresh) requestAnimationFrame(viewFit);
}

// ---------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------
// A group's card: its devices in the order they are drawn (top to bottom, left to right)
function viewCardsHtml(page) {
    const order = (a, b) => (a.y - b.y) || (a.x - b.x);
    const used = new Set();
    const cards = [...page.groups].sort(order).map(g => {
        const inside = page.nodes.filter(n => !used.has(n.id) && inGroup(n, g)).sort(order);
        inside.forEach(n => used.add(n.id));
        const net = g.network ? networkById(g.network) : null;
        const sub = net ? [net.vlan ? `VLAN ${net.vlan}` : '', net.subnet].filter(Boolean).join(' · ') : (g.note || '');
        return viewCardHtml(g.name || 'Group', g.color || (net && net.color) || GROUP_COLORS[7], sub, inside);
    });
    const rest = page.nodes.filter(n => !used.has(n.id)).sort(order);
    if (rest.length) cards.push(viewCardHtml(page.groups.length ? 'Not in a group' : page.name, GROUP_COLORS[7], '', rest));
    if (!cards.length) return '<div class="empty-state">This page is empty: place devices on it in the map editor.</div>';
    return cards.join('');
}

function viewCardHtml(name, color, sub, nodes) {
    let total = 0, up = 0;
    nodes.forEach(n => {
        if (n.page) { const s = pageSummary(pageById(n.page) || {nodes: []}); total += s.total; up += s.up; return; }
        const h = nodeHost(n);
        if (h) { total++; if (h.up) up++; }
    });
    const away = total - up;
    return `<section class="ncard" style="--g:${attr(color)}">
        <header class="ncard-head">
            <span class="ncard-name">${escapeHtml(name)}</span>
            ${sub ? `<span class="ncard-sub">${escapeHtml(sub)}</span>` : ''}
            <span class="spacer"></span>
            ${total ? `<span class="ncard-count${away ? ' has-away' : ''}" title="${away ? `${away} away` : 'All online'}"><b>${up}</b>/${total}</span>` : ''}
        </header>
        ${total ? `<div class="ncard-bar" aria-hidden="true"><i style="width:${(up / total * 100).toFixed(1)}%"></i></div>` : ''}
        <div class="ncard-items">${nodes.map(viewItemHtml).join('')}</div>
    </section>`;
}

function viewItemHtml(n) {
    const h = nodeHost(n);
    const kind = nodeKind(n);
    let state = '', right = '';
    if (n.page) {
        const s = pageSummary(pageById(n.page) || {nodes: []});
        state = s.downInfra ? 'away' : '';
        right = `<span class="nitem-tile">${s.up}/${s.total} ›</span>`;
    } else if (h) {
        state = h.up ? 'up' : h.up === false ? 'away' : 'unknown';
        right = `<span class="sdot ${h.up ? 'ok' : h.up === false ? 'bad' : ''}"></span>`;
    } else if (n.ref) {
        state = 'gone';
    }
    const clickable = n.page || h;
    const title = [nodeLabel(n), h ? [h.ip, h.up ? 'online' : h.up === false ? `away ${fmtAgo(h.last_changed)}` : 'unknown'].filter(Boolean).join(' · ') : ''].filter(Boolean).join('\n');
    const tag = clickable ? 'button type="button"' : 'div';
    return `<${tag} class="nitem ${state}" data-node="${attr(n.id)}" title="${attr(title)}">
        ${iconHtml(nodeIcon(n), 'small', kind)}
        <span class="nitem-text"><span class="nitem-name">${escapeHtml(nodeLabel(n))}</span><span class="nitem-sub">${escapeHtml(nodeSub(n, viewPage()) || kindLabel(kind))}</span></span>
        ${right}
    </${clickable ? 'button' : 'div'}>`;
}
