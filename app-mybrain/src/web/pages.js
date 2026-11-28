// MyBrain - the pages: Overview, Inbox, Library, History, Info.

const DAY = 86400;

// =====================================================================
// Overview
// =====================================================================
let overviewDays = Number(stored('days')) || 90;

onTab('overview', async () => {
    if (skeleton('overview', `
        <div id="ov-tiles"></div>
        <div class="dash-grid wide-left">
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Saved and handled</span><span class="hint"><span class="tabs" id="ov-days"></span></span></div>
                <div id="ov-trend" class="chart"></div><div id="ov-legend" class="legend"></div>
            </div></div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>What is waiting</span><span class="hint">open items, by what they are for</span></div>
                <div id="ov-purposes" class="rows"></div>
                <div class="chart-title gap-top"><span>Inbox by age</span><span class="hint" id="ov-age-hint"></span></div>
                <div id="ov-age"></div>
            </div></div>
        </div>
        <div class="dash-grid">
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Categories</span><span class="hint">everything saved · click to browse</span></div>
                <div id="ov-categories"></div>
            </div></div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Where it comes from</span><span class="hint">click to browse</span></div>
                <div id="ov-sources" class="rows"></div>
            </div></div>
        </div>
        <div class="panel" id="ov-forgotten-panel"><div class="panel-body">
            <div class="chart-title"><span>Rediscover</span><span class="hint">saved weeks ago, never opened · <button type="button" class="linkbtn" id="ov-shuffle">↻ others</button></span></div>
            <div id="ov-forgotten" class="card-grid small"></div>
        </div></div>
        <div class="dash-grid">
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>📅 Coming up</span><span class="hint"><button type="button" class="linkbtn" onclick="browse({dated: true, sort: 'date'})">all dates →</button></span></div>
                <div id="ov-upcoming" class="row-list"></div>
            </div></div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>★ High priority</span><span class="hint">open</span></div>
                <div id="ov-high" class="row-list"></div>
            </div></div>
        </div>
        <div class="dash-grid">
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Recently saved</span><span class="hint"><button type="button" class="linkbtn" onclick="switchTab('library')">all →</button></span></div>
                <div id="ov-recent" class="row-list"></div>
            </div></div>
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Activity</span><span class="hint"><button type="button" class="linkbtn" onclick="switchTab('history')">history →</button></span></div>
                <div id="ov-activity" class="events"></div>
            </div></div>
        </div>
        <div class="dash-grid">
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Topics</span><span class="hint">your most used tags · click to browse</span></div>
                <div id="ov-tags" class="tag-cloud"></div>
            </div></div>
            <div class="panel" id="ov-household-panel"><div class="panel-body">
                <div class="chart-title"><span>👪 Household</span><span class="hint">what the others share · click to browse</span></div>
                <div id="ov-household" class="rows"></div>
            </div></div>
        </div>`)) {
        buildChips('ov-days', [{key: '30', label: '30 d'}, {key: '90', label: '90 d'}, {key: '365', label: '1 y'}], String(overviewDays), (k) => {
            overviewDays = Number(k);
            store('days', k);
            loadOverview(true);
        });
        ['ov-forgotten', 'ov-upcoming', 'ov-high', 'ov-recent', 'ov-tags'].forEach(id => bindItems(document.getElementById(id), () => loadOverview(true)));
        document.getElementById('ov-shuffle').onclick = () => loadOverview(true);
        document.getElementById('ov-activity').addEventListener('click', (e) => {
            const row = e.target.closest('[data-item]');
            if (row) openItem(Number(row.dataset.item));
        });
    }
    await loadOverview(false);
});

let overviewLoadedAt = 0;
async function loadOverview(force) {
    // The numbers are read again at most every 30 seconds, or at once after a change
    if (!force && app.stats && Date.now() - overviewLoadedAt < 30000 && app.stats.days === overviewDays) return drawOverview();
    app.stats = await api(`stats?days=${overviewDays}`);
    overviewLoadedAt = Date.now();
    drawOverview();
}

function drawOverview() {
    const st = app.stats, s = app.status;
    if (!st || !s) return;
    const c = st.counts.by_status;
    const age = st.inbox_age || {};
    const old = (age.month || 0) + (age.quarter || 0) + (age.older || 0);
    const sortedAi = st.sorted.ai || 0;
    fill('ov-tiles', `<div class="tiles">
        ${tile('In your brain', num(st.counts.total), st.first ? `since ${fmtDate(st.first)}` : 'nothing saved yet')}
        ${tile('Inbox', num(c.inbox), old ? `${num(old)} waiting over a week` : 'all fresh', c.inbox > 50 ? 'warn' : '')}
        ${tile('Later', num(c.later), 'kept to look at')}
        ${tile('Saved this week', num(st.week.saved), st.week.again ? `${num(st.week.again)} saved again` : '&nbsp;')}
        ${tile('Handled this week', num(st.week.handled), 'done or archived', st.week.handled >= st.week.saved && st.week.handled ? 'pos' : '')}
        ${tile('Sorted by AI', st.counts.total ? `${num(sortedAi / st.counts.total * 100)}<small>%</small>` : '–', s.ai.entity ? escapeHtml(s.ai.entity) : 'no AI Task entity set: rules only')}
    </div>`);

    // Saved and handled, per day for 30 days, per week beyond
    const weekly = overviewDays > 31;
    const bucket = weekly ? 7 : 1;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const start = new Date(today.getTime() - (overviewDays - 1) * DAY * 1000);
    if (weekly) start.setDate(start.getDate() - ((start.getDay() + 6) % 7));  // from a Monday
    const slots = [];
    for (let t = new Date(start); t <= today; t.setDate(t.getDate() + bucket)) slots.push(new Date(t));
    const dayKey = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const index = new Map();
    slots.forEach((d, i) => { for (let k = 0; k < bucket; k++) { const x = new Date(d); x.setDate(x.getDate() + k); index.set(dayKey(x), i); } });
    const saved = slots.map(() => 0), done = slots.map(() => 0);
    st.per_day.saved.forEach(r => { if (index.has(r.day)) saved[index.get(r.day)] += r.n; });
    st.per_day.done.forEach(r => { if (index.has(r.day)) done[index.get(r.day)] += r.n; });
    const fmtSlot = d => d.toLocaleDateString([], {day: 'numeric', month: 'short'});
    barChart(document.getElementById('ov-trend'), {
        data: slots.map((d, i) => ({label: fmtSlot(d), tipTitle: weekly ? `Week of ${fmtSlot(d)}` : d.toLocaleDateString([], {weekday: 'short', day: 'numeric', month: 'short'}), values: [saved[i], done[i]]})),
        series: [{name: 'saved', color: color('series-1')}, {name: 'handled', color: color('series-3')}],
        grouped: true, height: 200, format: v => num(v), empty: 'Nothing saved in this period yet',
    });
    fill('ov-legend', legendHtml([{name: 'saved', color: color('series-1')}, {name: 'done or archived', color: color('series-3')}]));

    const purposes = st.purposes || [];
    const maxP = Math.max(1, ...purposes.map(p => p.n));
    fill('ov-purposes', purposes.length ? purposes.map(p => `<div class="rowline" data-purpose="${p.name}">
        <span class="rl-name">${purposeIcon(p.name)} ${escapeHtml(purposeLabel(p.name))}</span>
        <span class="rl-bar"><i style="width:${(p.n / maxP * 100).toFixed(1)}%"></i></span><span class="rl-n">${num(p.n)}</span></div>`).join('')
        : '<div class="empty-state">Nothing open: well done</div>');
    document.getElementById('ov-purposes').onclick = (e) => {
        const r = e.target.closest('[data-purpose]');
        if (r) browse({purpose: r.dataset.purpose, status: 'open'});
    };
    const ageParts = [['week', 'this week', 'series-1'], ['month', 'this month', 'series-4'], ['quarter', '1–3 months', 'series-2'], ['older', 'older', 'series-5']];
    const ageTotal = ageParts.reduce((a, [k]) => a + (age[k] || 0), 0);
    fill('ov-age-hint', ageTotal ? `${num(ageTotal)} items` : '');
    fill('ov-age', ageTotal ? `<div class="stack-bar">${ageParts.map(([k, label, col]) => age[k] ? `<i style="width:${(age[k] / ageTotal * 100).toFixed(2)}%;background:${color(col)}" title="${label}: ${age[k]}"></i>` : '').join('')}</div>
        <div class="stack-legend">${ageParts.map(([k, label, col]) => `<span><i style="background:${color(col)}"></i>${label} <b>${num(age[k] || 0)}</b></span>`).join('')}</div>` : '<div class="empty-state">The inbox is empty</div>');

    const palette = ['series-1', 'series-2', 'series-3', 'series-4', 'series-5', 'seq-2', 'seq-4', 'idle'];
    const cats = st.categories || [];
    const shown = cats.slice(0, 7);
    const rest = cats.slice(7).reduce((a, c) => a + c.n, 0);
    const donutItems = shown.map((c, i) => ({name: c.name, value: c.n, color: color(palette[i]), detail: `${num(c.n)} · ${num(c.open)} open`}));
    if (rest) donutItems.push({name: 'Other categories', value: rest, color: color('idle')});
    const catEl = document.getElementById('ov-categories');
    if (catEl._key !== JSON.stringify(donutItems)) {
        catEl._key = JSON.stringify(donutItems);
        donutChart(catEl, donutItems, {label: 'items', value: num(st.counts.total)}, v => num(v));
        catEl.querySelectorAll('.donut-legend .row').forEach((row, i) => {
            if (!shown[i]) return;
            row.classList.add('clickable');
            row.onclick = () => browse({category: shown[i].name, status: ''});
        });
    }

    const sources = st.sources || [];
    const maxS = Math.max(1, ...sources.map(x => x.n));
    fill('ov-sources', sources.length ? sources.slice(0, 10).map(x => `<div class="rowline" data-source="${x.name}">
        <span class="rl-name"><span class="src-dot" style="background:${sourceColor(x.name)}"></span>${escapeHtml(sourceName(x.name))}</span>
        <span class="rl-bar"><i style="width:${(x.n / maxS * 100).toFixed(1)}%;background:${sourceColor(x.name)}"></i></span><span class="rl-n">${num(x.n)}</span></div>`).join('')
        : '<div class="empty-state">Nothing saved yet</div>');
    document.getElementById('ov-sources').onclick = (e) => {
        const r = e.target.closest('[data-source]');
        if (r) browse({source: r.dataset.source, status: ''});
    };

    document.getElementById('ov-forgotten-panel').hidden = !st.forgotten.length;
    fill('ov-forgotten', st.forgotten.map(it => card(it)).join(''));
    fill('ov-upcoming', st.upcoming.length ? st.upcoming.slice(0, 6).map(it => rowItem(it, {dates: true})).join('')
        : '<div class="empty-state">Nothing with a date. Give an item a due or an on date and it shows here and in the MyBrain calendar.</div>');
    fill('ov-high', st.high.length ? st.high.map(rowItem).join('') : '<div class="empty-state">Nothing marked ★ — press h on an item to mark it</div>');
    fill('ov-recent', st.recent.length ? st.recent.map(rowItem).join('') : `<div class="empty-state">Nothing saved yet. Share a link to the Home Assistant app on your phone, or press <b>+ Add</b>.</div>`);
    const maxT = Math.max(1, ...st.tags.map(t => t.n));
    fill('ov-tags', st.tags.length ? st.tags.map(t => `<button type="button" class="tagchip size${Math.ceil(t.n / maxT * 4)}" data-tag="${escapeHtml(t.name)}">#${escapeHtml(t.name)} <small>${t.n}</small></button>`).join('') : '<div class="empty-state">No tags yet</div>');
    // Sorting is noise here: what was saved, opened and handled (History has everything)
    const happened = st.activity.filter(ev => ev.what !== 'sorted');
    const others = (st.household || []).filter(h => h.owner !== s.viewer.id);
    document.getElementById('ov-household-panel').hidden = !others.length;
    const maxH = Math.max(1, ...others.map(h => h.n));
    fill('ov-household', others.map(h => `<div class="rowline" data-owner="${escapeHtml(h.owner)}">
        <span class="rl-name">👤 ${escapeHtml(nameOf(h.owner))}</span>
        <span class="rl-bar"><i style="width:${(h.n / maxH * 100).toFixed(1)}%"></i></span><span class="rl-n">${num(h.n)}</span></div>`).join(''));
    document.getElementById('ov-household').onclick = (e) => {
        const r = e.target.closest('[data-owner]');
        if (r) browse({whose: 'user:' + r.dataset.owner, status: ''});
    };
    fill('ov-activity', happened.length ? happened.slice(0, 14).map(eventRow).join('') : '<div class="empty-state">Nothing happened yet</div>');
}

function eventRow(ev) {
    const [label, kind] = EVENT_LABELS[ev.what] || [ev.what, ''];
    const title = ev.title || ev.url || ev.detail || '';
    const clickable = ev.item_id && ev.title !== null && ev.title !== undefined;
    return `<div class="event${clickable ? ' clickable' : ''}"${clickable ? ` data-item="${ev.item_id}"` : ''}>
        <span class="t">${fmtTime(ev.at)}</span><span class="k ${kind}">${escapeHtml(label)}</span>
        <span class="x">${ev.source ? `<span class="src-dot" style="background:${sourceColor(ev.source)}"></span>` : ''}${escapeHtml(title.slice(0, 120))}${ev.what === 'sorted' || ev.what === 'edited' || ev.what === 'shared again' ? ` <span class="muted">· ${escapeHtml(ev.detail)}</span>` : ''}</span></div>`;
}

// =====================================================================
// Inbox: what came in and was not looked at yet
// =====================================================================
const inbox = {sort: stored('inbox_sort') || 'new', purpose: '', items: [], total: 0};

onTab('inbox', async () => {
    if (skeleton('inbox', `
        <div class="page-head">
            <div><h1>Inbox</h1><div class="muted" id="in-sub"></div></div>
            <span class="spacer"></span>
            <div class="tabs" id="in-sort"></div>
        </div>
        <div class="chips-row" id="in-purpose"></div>
        <div id="in-grid" class="card-grid"></div>
        <div class="pager" id="in-more"></div>
        <div class="muted key-hint">Open an item and press <kbd>d</kbd> done · <kbd>l</kbd> later · <kbd>a</kbd> archive · <kbd>h</kbd> high priority · <kbd>o</kbd> open the link · <kbd>Esc</kbd> close</div>`)) {
        buildChips('in-sort', [{key: 'new', label: 'Newest'}, {key: 'old', label: 'Oldest'}, {key: 'priority', label: '★ First'}], inbox.sort, (k) => {
            inbox.sort = k;
            store('inbox_sort', k);
            loadInbox();
        });
        bindItems(document.getElementById('in-grid'), settleInbox);
    }
    await loadInbox();
});

async function loadInbox(more) {
    const offset = more ? inbox.items.length : 0;
    const params = new URLSearchParams({status: 'inbox', sort: inbox.sort, limit: '48', offset: String(offset)});
    if (inbox.purpose) params.set('purpose', inbox.purpose);
    const result = await api('items?' + params);
    inbox.items = more ? inbox.items.concat(result.items) : result.items;
    inbox.total = result.total;
    const facets = result.facets.purpose || {};
    const inboxCount = (result.facets.status || {}).inbox || 0;
    fill('in-sub', inboxCount ? `${num(inboxCount)} waiting to be looked at` : '');
    const purposeOptions = [{key: '', label: 'All'}].concat(Object.keys(PURPOSES).filter(p => facets[p]).map(p => ({key: p, label: `${purposeIcon(p)} ${purposeLabel(p)}`})));
    buildChips('in-purpose', purposeOptions, inbox.purpose, (k) => { inbox.purpose = k; loadInbox(); });
    fill('in-grid', inbox.items.length ? inbox.items.map(it => card(it)).join('')
        : `<div class="empty-state big">🎉<br>Inbox zero.<br><span class="muted">Share something to the Home Assistant app on your phone to fill it again.</span></div>`);
    pager('in-more', inbox.items.length, inbox.total, () => loadInbox(true));
}

function settleInbox() {
    // A card that was handled slides out; the rest close up
    setTimeout(() => {
        document.querySelectorAll('#in-grid .card.leaving').forEach(el => {
            inbox.items = inbox.items.filter(it => it.id !== Number(el.dataset.id));
            el.remove();
        });
        if (!inbox.items.length) loadInbox();
    }, 260);
}

function pager(id, shown, total, more) {
    const el = document.getElementById(id);
    if (shown >= total) { el.innerHTML = total > 12 ? `<span class="muted">All ${num(total)} shown</span>` : ''; return; }
    el.innerHTML = `<button class="btn">Show more <span class="muted">(${num(shown)} of ${num(total)})</span></button>`;
    el.querySelector('button').onclick = more;
}

// =====================================================================
// Library: everything, searchable
// =====================================================================
const library = {q: '', whose: 'mine', status: 'open', category: '', source: '', purpose: '', tag: '', dated: false, sort: 'new', view: stored('view') || 'grid', items: [], total: 0};

function browse(filters) {
    Object.assign(library, {q: '', whose: 'mine', status: 'open', category: '', source: '', purpose: '', tag: '', dated: false, sort: 'new'}, filters);
    switchTab('library');
}

onTab('library', async () => {
    if (skeleton('library', `
        <div class="search-row">
            <input class="input" id="lib-q" type="search" placeholder="Search titles, notes, summaries and the saved text of every page…  ( / )" autocomplete="off" spellcheck="false">
            <select class="select" id="lib-sort" aria-label="Order">
                <option value="new">Newest first</option><option value="old">Oldest first</option>
                <option value="priority">★ First</option><option value="date">By date</option><option value="touched">Last changed</option>
            </select>
            <div class="tabs" id="lib-view"></div>
        </div>
        <div class="filters lib-filters">
            <select class="select" id="lib-whose" aria-label="Whose"></select>
            <div class="tabs" id="lib-status"></div>
            <select class="select" id="lib-category" aria-label="Category"></select>
            <select class="select" id="lib-source" aria-label="Source"></select>
            <select class="select" id="lib-purpose" aria-label="For"></select>
            <div class="tabs"><button type="button" class="tab" id="lib-dated" title="Only items with a due or an on date">📅 With a date</button></div>
            <span id="lib-tag"></span>
            <span class="spacer"></span>
            <span class="muted" id="lib-count"></span>
        </div>
        <div id="lib-grid"></div>
        <div class="pager" id="lib-more"></div>`)) {
        const q = document.getElementById('lib-q');
        let timer = null;
        q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { library.q = q.value; loadLibrary(); }, 250); });
        q.addEventListener('keydown', (e) => { if (e.key === 'Escape') { q.value = ''; library.q = ''; loadLibrary(); q.blur(); } });
        document.getElementById('lib-sort').onchange = (e) => { library.sort = e.target.value; loadLibrary(); };
        ['category', 'source', 'purpose'].forEach(k => {
            document.getElementById('lib-' + k).onchange = (e) => { library[k] = e.target.value; loadLibrary(); };
        });
        buildChips('lib-view', [{key: 'grid', label: '▦'}, {key: 'list', label: '☰'}], library.view, (k) => { library.view = k; store('view', k); drawLibrary(); });
        bindItems(document.getElementById('lib-grid'), () => setTimeout(() => loadLibrary(), 260));
        document.getElementById('lib-whose').onchange = (e) => { library.whose = e.target.value; loadLibrary(); };
        document.getElementById('lib-dated').onclick = () => { library.dated = !library.dated; loadLibrary(); };
    }
    document.getElementById('lib-q').value = library.q;
    document.getElementById('lib-sort').value = library.sort;
    await loadLibrary();
});

async function loadLibrary(more) {
    const offset = more ? library.items.length : 0;
    const params = new URLSearchParams({sort: library.sort, limit: '60', offset: String(offset)});
    ['q', 'whose', 'status', 'category', 'source', 'purpose', 'tag'].forEach(k => { if (library[k]) params.set(k, library[k]); });
    if (library.dated) params.set('dated', '1');
    const result = await api('items?' + params);
    library.items = more ? library.items.concat(result.items) : result.items;
    library.total = result.total;
    library.facets = result.facets;
    drawLibrary();
}

function drawLibrary() {
    const f = library.facets || {};
    const sc = f.status || {};
    const all = Object.values(sc).reduce((a, b) => a + b, 0);
    buildChips('lib-status', [
        {key: 'open', label: `Open ${num((sc.inbox || 0) + (sc.later || 0))}`},
        {key: 'inbox', label: `Inbox ${num(sc.inbox || 0)}`},
        {key: 'later', label: `Later ${num(sc.later || 0)}`},
        {key: 'done', label: `Done ${num(sc.done || 0)}`},
        {key: 'archived', label: `Archived ${num(sc.archived || 0)}`},
        {key: '', label: `All ${num(all)}`},
    ], library.status, (k) => { library.status = k; loadLibrary(); });
    const options = (id, label, values, names) => {
        const el = document.getElementById(id);
        const keys = Object.keys(values).sort((a, b) => values[b] - values[a]);
        const current = library[id.slice(4)];
        if (current && !keys.includes(current)) keys.unshift(current);
        el.innerHTML = `<option value="">${label}</option>` + keys.map(k => `<option value="${escapeHtml(k)}"${k === current ? ' selected' : ''}>${escapeHtml(names(k))} (${num(values[k] || 0)})</option>`).join('');
        el.classList.toggle('active', Boolean(current));
    };
    options('lib-category', 'Every category', f.category || {}, k => k);
    options('lib-source', 'Every source', f.source || {}, sourceName);
    options('lib-purpose', 'Any purpose', f.purpose || {}, k => `${purposeIcon(k)} ${purposeLabel(k)}`);
    document.getElementById('lib-dated').classList.toggle('active', library.dated);
    // Whose: mine, everyone's, or one person's (only what they share)
    const others = (app.status.household || []).filter(h => h.owner !== app.status.viewer.id);
    const whoseEl = document.getElementById('lib-whose');
    whoseEl.hidden = !others.length && library.whose === 'mine';
    whoseEl.innerHTML = [['mine', 'Mine'], ['everyone', 'Everyone']].concat(others.map(h => [`user:${h.owner}`, `👤 ${h.name} (${num(h.n)})`]))
        .map(([k, l]) => `<option value="${escapeHtml(k)}"${k === library.whose ? ' selected' : ''}>${escapeHtml(l)}</option>`).join('');
    whoseEl.classList.toggle('active', library.whose !== 'mine');
    document.getElementById('lib-sort').value = library.sort;
    const tagEl = document.getElementById('lib-tag');
    tagEl.innerHTML = library.tag ? `<button type="button" class="tagchip active" title="Show all tags">#${escapeHtml(library.tag)} ✕</button>` : '';
    tagEl.onclick = () => { library.tag = ''; loadLibrary(); };
    fill('lib-count', `${num(library.total)} ${library.total === 1 ? 'item' : 'items'}${library.q ? ` for “${escapeHtml(library.q)}”` : ''}`);
    const grid = document.getElementById('lib-grid');
    grid.className = library.view === 'list' ? 'row-list boxed' : 'card-grid';
    fill('lib-grid', library.items.length
        ? library.items.map(it => library.view === 'list' ? rowItem(it, {dates: true}) : card(it, {showStatus: true})).join('')
        : `<div class="empty-state big">${library.q ? 'Nothing matches. Words are matched as the start of a word; try fewer.' : 'Nothing here.'}</div>`);
    pager('lib-more', library.items.length, library.total, () => loadLibrary(true));
}

// =====================================================================
// History: everything that happened, newest first, by day
// =====================================================================
const historyPage = {events: [], done: false};

onTab('history', async () => {
    if (skeleton('history', `
        <div class="page-head"><div><h1>History</h1><div class="muted">What was saved, opened, sorted and handled, newest first</div></div></div>
        <div class="panel"><div class="panel-body"><div id="hi-list"></div></div></div>
        <div class="pager" id="hi-more"></div>`)) {
        document.getElementById('hi-list').addEventListener('click', (e) => {
            const row = e.target.closest('[data-item]');
            if (row) openItem(Number(row.dataset.item));
        });
    }
    await loadHistory(false);
});

async function loadHistory(more) {
    const before = more && historyPage.events.length ? historyPage.events[historyPage.events.length - 1].at : '';
    const result = await api(`history?limit=150${before ? '&before=' + before : ''}`);
    historyPage.events = more ? historyPage.events.concat(result.events) : result.events;
    historyPage.done = result.events.length < 150;
    let lastDay = '';
    const html = historyPage.events.map(ev => {
        const day = new Date(ev.at * 1000).toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
        const head = day !== lastDay ? `<div class="day-head">${day}</div>` : '';
        lastDay = day;
        return head + eventRow(ev);
    }).join('');
    fill('hi-list', html ? `<div class="events">${html}</div>` : '<div class="empty-state">Nothing happened yet</div>');
    const moreEl = document.getElementById('hi-more');
    moreEl.innerHTML = historyPage.done ? '' : '<button class="btn">Older</button>';
    if (!historyPage.done) moreEl.querySelector('button').onclick = () => loadHistory(true);
}

// =====================================================================
// Info
// =====================================================================
function calendarLine(c, what, name) {
    if (!c || !c.entity) return '';
    const state = !c.found
        ? `doesn't exist yet: add a <b>Local Calendar</b> named <b>${escapeHtml(name)}</b>`
        : `${num(c.events || 0)} events${c.synced_at ? `, checked ${fmtAgo(c.synced_at)}` : ''}${c.error ? ` <span class="sev-text medium">(${escapeHtml(c.error)})</span>` : ''}`;
    return infoRow(what, `<span class="mono">${escapeHtml(c.entity)}</span> · ${state}`);
}
function calendarText(s) {
    const e = s.entities || {};
    if (!s.config.calendar_sync) return '<p>Off in the app options.</p>';
    return (e.mine ? calendarLine(e.mine, 'Yours', e.name_hint) : '') + calendarLine(e.shared, 'Household', 'MyBrain');
}
onTab('info', () => {
    const s = app.status;
    const c = s.counts;
    const ai = s.ai;
    const aiText = ai.entity ? `Sorting with <b>${escapeHtml(ai.entity)}</b>${ai.answers ? ` (${num(ai.answers)} answers since the app started)` : ''}.${ai.error ? `<div class="note warn">Last try failed: ${escapeHtml(ai.error)}. The rules sort for now; it tries again in a few minutes.</div>` : ''}`
        : 'No AI Task entity set, so the built-in rules sort everything. Add an AI integration to Home Assistant (DeepSeek, OpenAI, Google Generative AI, Anthropic or Ollama) and put its AI Task entity, such as ai_task.deepseek, in the app option <b>AI Task entity</b>.';
    fill('tab-info', `
    <div class="info-grid">
        <div class="panel"><div class="panel-body">
            <div class="section-title">Save from your phone</div>
            <ol class="steps-list">
                <li>In any app, tap <b>Share</b>.</li>
                <li>Pick <b>Home Assistant</b>. That's it: it lands in the inbox here, the page is read and sorted in the background.</li>
            </ol>
            <p class="muted">Needs the Home Assistant companion app on Android (it fires a <span class="mono">mobile_app.share</span> event that this app listens for). Long-press Home Assistant in the share sheet to pin it to the top. On iPhone, sharing works too but brings less (no app name).</p>
            ${infoRow('Listening', s.listening ? '<span class="state ok">connected to Home Assistant</span>' : s.config.listen_shares ? '<span class="state bad">not connected</span>' : '<span class="state warn">off in the options</span>')}
            ${s.last_share && s.last_share.at ? infoRow('Last share', `${fmtAgo(s.last_share.at)}${s.last_share.caller ? ' from ' + escapeHtml(s.last_share.caller) : ''}${s.last_share.device ? ' on ' + escapeHtml(s.last_share.device) : ''}`) : ''}
        </div></div>
        <div class="panel"><div class="panel-body">
            <div class="section-title">Sorting</div>
            <p>${aiText}</p>
            <p class="muted">The rules look at the site, the subreddit and words in the title. Categories are set in the app options; anything you change by hand stays as you set it.</p>
            ${infoRow('Categories', s.categories.map(x => `<span class="badge cat">${escapeHtml(x)}</span>`).join(' '))}
        </div></div>
        <div class="panel"><div class="panel-body">
            <div class="section-title">Calendar</div>
            ${calendarText(s)}
            <p class="muted">Open items with a <b>due</b> date (⏰) or an <b>on</b> date (📅) are kept in step: changed when the item changes, removed when it is done or archived. Yours has all of yours; the household calendar has what everyone shares. Add one in Home Assistant with <b>Settings → Devices &amp; services → Add integration → Local Calendar</b>. Events you add there yourself are left alone.</p>
            ${infoRow('Sensor', `<span class="mono">${escapeHtml((s.entities || {}).next_sensor || 'sensor.mybrain_next')}</span>: your next item with a date (overdue ones first), with when, how many days, the link and the next five`)}
        </div></div>
        <div class="panel"><div class="panel-body">
            <div class="section-title">In Home Assistant</div>
            ${infoRow('You', s.viewer.id ? `${escapeHtml(s.viewer.name || 'signed in')}: your own inbox, library and history; ${s.config.share_by_default ? 'the household sees your items unless you mark them 🔒 private' : 'what you save is only yours until you share it with the household (👪 in an item)'}` : 'one collection for everyone (the app does not know who is looking)')}
            ${infoRow('Sensor', `<span class="mono">${escapeHtml((s.entities || {}).inbox_sensor || 'sensor.mybrain_inbox')}</span>: items in your inbox; later, done, archived and total as attributes`)}
            ${infoRow('Home Assistant', escapeHtml(s.ha_version || '–'))}
            <p class="muted">Use it for a badge on a dashboard, or an automation that reminds you when the inbox grows.</p>
        </div></div>
        <div class="panel"><div class="panel-body">
            <div class="section-title">Storage</div>
            ${infoRow('Items', `${num(c.total)} (${num(c.by_status.inbox)} inbox, ${num(c.by_status.later)} later, ${num(c.by_status.done)} done, ${num(c.by_status.archived)} archived)`)}
            ${infoRow('Being read', num(s.queue))}
            ${infoRow('Database', fmtBytes(s.database_size))}
            ${infoRow('Read pages', s.config.fetch_pages ? 'yes: title, picture and text are kept, so a link that dies is still findable' : 'off in the options')}
            <div class="detail-actions"><a class="btn btn-small" href="api/export" download>⇩ Export everything (JSON)</a></div>
            <p class="muted">Everything stays in this app's storage on your Home Assistant, and is in its backups. Links into your home network are never fetched.</p>
        </div></div>
    </div>
    <div class="muted version-line">MyBrain ${escapeHtml(s.version)}</div>`);
});
