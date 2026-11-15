// Network dashboard - the network on the Dashboard: every group of a page of the map as a card, with
// its network, how many of its devices are online and the devices themselves (away ones in red), in
// the order they are drawn. A device shows its details, a page tile goes in; "Open in the map" opens
// the page on the Network map.

const viewState = {
    pageId: stored('view_page', ''),
    built: false,
};

function viewPage() {
    const pages = mapPages();
    return pages.find(p => p.id === viewState.pageId) || pages.find(p => !p.parent) || pages[0] || null;
}

function viewBuild(el) {
    el.innerHTML = `<div class="panel dash-map"><div class="panel-body">
        <div class="map-bar view-bar">
            <div class="map-tabs" id="dv-tabs"></div>
            <span class="spacer"></span>
            <button type="button" class="btn btn-small" data-act="open" title="This page on the Network map">Open in the map</button>
        </div>
        <div class="ncards" id="dv-cards"></div>
    </div></div>`;
    el.querySelector('.view-bar').addEventListener('click', (e) => {
        const b = e.target.closest('button');
        if (!b) return;
        if (b.dataset.page) return viewOpen(b.dataset.page);
        const page = viewPage();
        if (page && b.dataset.act === 'open') switchTab('map', page.id);
    });
    el.querySelector('#dv-cards').addEventListener('click', (e) => {
        const item = e.target.closest('[data-node]');
        if (item) viewActivate(item.dataset.node);
    });
    viewState.built = true;
}

// A device shows its details, a page tile goes in
function viewActivate(nodeId) {
    const n = (viewPage() || {nodes: []}).nodes.find(x => x.id === nodeId);
    if (!n) return;
    if (n.page && pageById(n.page)) viewOpen(n.page);
    else if (n.ref && hostByKey(n.ref)) openHost(n.ref);
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
}

function viewDraw() {
    const cards = document.getElementById('dv-cards');
    if (!cards) return;
    const page = viewPage();
    const html = page ? viewCardsHtml(page) : '<div class="empty-state">No map yet: draw one on the Network map.</div>';
    if (cards.innerHTML !== html) cards.innerHTML = html;
}

// The dashboard calls this on every change: build once, then only fill
function viewRefresh(el) {
    if (!viewState.built || !el.querySelector('#dv-cards')) { viewState.built = false; viewBuild(el); }
    viewTabs();
    viewDraw();
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
    if (!cards.length) return '<div class="empty-state">This page is empty: place devices on it on the Network map.</div>';
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
