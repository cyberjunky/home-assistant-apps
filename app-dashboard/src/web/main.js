// Dashboard - start, the live stream from Home Assistant, and moving between pages.
'use strict';

function renderAll() {
    applyStyle();
    renderNav();
    renderPage();
    renderDrawer();
}
const slug = text => String(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
function showPage(id) {
    S.page = id;
    S.sel = null;
    try { history.replaceState(null, '', '#' + slug(currentPage().name)); } catch (e) { /* the address is only a convenience */ }
    renderAll();
}
// #energy in the address opens that page: a tablet can start on any of them
function pageFromAddress() {
    const want = decodeURIComponent(location.hash.slice(1));
    return S.doc.pages.find(p => p.id === want || slug(p.name) === want) || S.doc.pages[0];
}

async function boot() {
    let data;
    try {
        data = await api('boot');
    } catch (err) {
        data = {error: err.message};
    }
    if (!data.doc) {
        $('#page').innerHTML = `<div class="splash"><div><div class="lbl">Waiting for Home Assistant</div><p>${esc(data.error || '')}</p></div></div>`;
        setTimeout(boot, 2500);
        return;
    }
    S.states = data.states;
    S.areas = data.areas;
    S.hidden = new Set(data.hidden);
    // While designing, the page being worked on stays; a version from elsewhere shows up on saving
    if (!S.doc || !S.design) S.doc = data.doc;
    if (!S.doc.pages.some(p => p.id === S.page)) S.page = pageFromAddress().id;
    renderAll();
    tick();
}

function listen() {
    const stream = new EventSource('api/live');
    let lost = false;
    stream.onmessage = ev => {
        for (const m of JSON.parse(ev.data)) {
            if (m.type === 'state') {
                const before = S.states[m.id];
                if (m.state) S.states[m.id] = m.state; else delete S.states[m.id];
                touch(m.id);
                stateChanged(m.id, before, m.state);
                if (m.id === 'sun.sun') applyTheme();
            } else if (m.type === 'reload' || (m.type === 'doc' && !S.design && m.rev !== S.doc?.rev)) boot();
        }
    };
    stream.onerror = () => { lost = true; };
    stream.onopen = () => { if (lost) { lost = false; boot(); } };
}

document.addEventListener('click', ev => {
    const t = ev.target.closest('[data-page],[data-add-page],[data-design],[data-cycle-theme],[data-chip]');
    if (!t || t.closest('#drawer') || (t.dataset.chip && !S.design)) return;
    if (t.dataset.page) showPage(t.dataset.page);
    else if ('addPage' in t.dataset) addPage();
    else if ('design' in t.dataset) setDesign(true);
    else if ('cycleTheme' in t.dataset) cycleTheme();
    else if (S.design) { drawerTab = 'page'; select(null); }
});

// A swipe goes to the next page
let swipe = null;
document.addEventListener('touchstart', ev => {
    swipe = S.design || POP || ev.target.closest('.sl') ? null : {x: ev.touches[0].clientX, y: ev.touches[0].clientY};
}, {passive: true});
document.addEventListener('touchend', ev => {
    if (!swipe) return;
    const dx = ev.changedTouches[0].clientX - swipe.x, dy = ev.changedTouches[0].clientY - swipe.y;
    if (Math.abs(dx) < 120 || Math.abs(dy) > 80) return;
    const pages = S.doc.pages, next = pages[pages.indexOf(currentPage()) + (dx < 0 ? 1 : -1)];
    if (next) showPage(next.id);
}, {passive: true});

// The clocks, when the minute turns
let minute = -1;
setInterval(() => {
    const now = new Date().getMinutes();
    if (now === minute || !S.doc) return;
    minute = now;
    currentPage().widgets.filter(w => w.type === 'clock').forEach(w => drawWidget(w.id));
}, 1000);
// Charts get their newer history
setInterval(() => { if (S.doc) currentPage().widgets.filter(w => w.type === 'chart').forEach(w => drawWidget(w.id)); }, 5 * 60000);

// ?design in the address opens the drawer straight away
boot().then(() => {
    listen();
    if (new URLSearchParams(location.search).has('design') && S.doc) setDesign(true);
});
