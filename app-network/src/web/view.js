// Network dashboard - the map on the Dashboard: the pages as drawn in the editor, to look at.
// Nothing can be moved or changed here: drag to move around, Ctrl + wheel or the buttons to zoom,
// click a device for its details and a page tile to go in. "Edit this page" opens the editor on it.

const viewState = {
    pageId: stored('view_page', ''),
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
            <span class="view-hint muted">Ctrl + wheel zooms</span>
            <div class="tabs" role="group" aria-label="Zoom" id="dv-zoom">
                <button type="button" class="tab icon-tab" data-act="out" title="Zoom out">−</button>
                <button type="button" class="tab zoom-text" data-act="fit" title="Fit the page">100%</button>
                <button type="button" class="tab icon-tab" data-act="in" title="Zoom in">+</button>
            </div>
            <button type="button" class="btn btn-small" data-act="edit" title="Open this page in the editor">Edit this page</button>
        </div>
        <div class="map-canvas view-canvas">
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
    viewState.built = true;
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

function viewOpen(id) {
    viewState.pageId = id;
    store('view_page', id);
    viewTabs();
    viewDraw();
    if (viewOf(viewPage()).fresh) viewFit();
}

function viewRender() {
    if (viewState.frame) return;
    viewState.frame = requestAnimationFrame(() => { viewState.frame = 0; viewDraw(); });
}
function viewDraw() {
    const page = viewPage();
    const vp = document.getElementById('dv-vp');
    if (!vp) return;
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
        const k = Math.min(1.2, Math.max(0.1, Math.min((box.width - 40) / b.w, (box.height - 40) / b.h)));
        Object.assign(v, {k, x: (box.width - b.w * k) / 2 - b.x * k, y: (box.height - b.h * k) / 2 - b.y * k});
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
    // A click: a page tile goes in, a device shows its details
    const node = d.target.closest && d.target.closest('[data-node]');
    if (!node) return;
    const n = viewPage().nodes.find(x => x.id === node.dataset.node);
    if (!n) return;
    if (n.page && pageById(n.page)) viewOpen(n.page);
    else if (n.ref && hostByKey(n.ref)) openHost(n.ref);
}

// The dashboard calls this on every change: build once, then only draw
function viewRefresh(el) {
    if (!viewState.built || !el.querySelector('#dv-svg')) { viewState.built = false; viewBuild(el); }
    viewTabs();
    viewDraw();
    const page = viewPage();
    if (page && viewOf(page).fresh) requestAnimationFrame(viewFit);
}
