// MQTT dashboard - Clients: everything that connected since the broker started, from its log.
// A client opens as #clients/<id>: its connection, its subscriptions and what happened to it.

const cl = {
    show: stored('clients_show', 'connected'),
    q: '',
    sort: stored('clients_sort', 'id'),
    loadedAt: 0,
    events: {},
};
const CLIENT_SHOWS = [{key: 'connected', label: 'Connected'}, {key: 'left', label: 'Left'}, {key: 'all', label: 'All'}];

function clientState(c) {
    if (c.connected) return stateLabel('ok', 'connected');
    return stateLabel(c.reason === 'not authorised' ? 'bad' : 'off', c.reason || 'left');
}

function clientRows() {
    const q = cl.q.toLowerCase();
    let rows = app.clients.filter(c => cl.show === 'all' || (cl.show === 'connected') === c.connected);
    if (q) rows = rows.filter(c => [c.id, c.user, c.ip, c.protocol, c.listener_name].some(v => String(v || '').toLowerCase().includes(q))
        || c.subs.some(s => s.topic.toLowerCase().includes(q)));
    const keys = {
        id: c => c.id.toLowerCase(),
        user: c => (c.user || '~').toLowerCase(),
        ip: c => (c.ip || '').split('.').map(n => n.padStart(3, '0')).join('.'),
        since: c => -(c.connected ? c.since : c.left_at || 0),
        subs: c => -c.subs.length,
        connects: c => -c.connects,
    };
    const key = keys[cl.sort] || keys.id;
    return rows.sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
}

function renderClients() {
    const el = document.getElementById('tab-clients');
    if (activeSub) return renderClient(el, activeSub);
    const rows = clientRows();
    const counts = {connected: app.clients.filter(c => c.connected).length, left: app.clients.filter(c => !c.connected).length, all: app.clients.length};
    const head = (label, sort, n) => ({label, sort, n, sorted: cl.sort === sort ? 'asc' : ''});
    const keep = document.activeElement && document.activeElement.id === 'cl-q' ? document.activeElement.selectionStart : null;
    el.innerHTML = `
        <div class="page-head">
            <div class="filters">
                ${chipsHtml('cl-show', CLIENT_SHOWS.map(o => ({...o, count: counts[o.key]})), cl.show, 'Show')}
                <input id="cl-q" class="input search" type="search" placeholder="Client ID, user, address or subscription" value="${attr(cl.q)}" autocomplete="off" spellcheck="false">
            </div>
            <span class="muted">${num(rows.length)} client${rows.length === 1 ? '' : 's'}</span>
        </div>
        ${panel('', '', `<div class="table-wrap">${table([
            head('Client', 'id'), head('Login', 'user'), head('Address', 'ip'), {label: 'Over'}, {label: 'MQTT'},
            head('Since', 'since'), head('Subscriptions', 'subs', true), head('Connects', 'connects', true),
        ], rows.map(c => `<tr class="clickable" data-client="${attr(c.id)}">
            ${td(`<span class="sdot ${c.connected ? 'ok' : c.reason === 'not authorised' ? 'bad' : ''}"></span> <span class="mono">${escapeHtml(c.id)}</span>${c.clean ? '' : ' ' + badge('persistent', '', 'A persistent session: the broker keeps its subscriptions and queues its messages while it is away')}`, 'wrap-cell')}
            ${td(c.user ? `${escapeHtml(c.user)} <span class="muted">${escapeHtml(LOGIN_LABELS[c.login] || '')}</span>` : '<span class="muted">–</span>')}
            ${td(`<span class="mono">${escapeHtml(c.ip || '–')}</span>`)}
            ${td(escapeHtml(c.listener_name || '–'))}
            ${td(escapeHtml(c.protocol || '–'))}
            ${td(c.connected ? escapeHtml(fmtAgo(c.since).replace(' ago', '')) : `<span class="muted">left ${escapeHtml(fmtAgo(c.left_at))}</span>`)}
            ${td(num(c.subs.length), 'n')}
            ${td(`${num(c.connects)}${c.drops ? ` <span class="warn-text" title="Times it dropped off (not a goodbye from the client)">${num(c.drops)}↯</span>` : ''}`, 'n')}
        </tr>`), {empty: cl.q ? 'No client matches' : cl.show === 'connected' ? 'No client is connected' : 'No clients yet'})}</div>`)}
        <p class="note">From the broker's log since it started${app.status && app.status.clients.broker_started ? ` (${escapeHtml(fmtDateTime(app.status.clients.broker_started))})` : ''}. The subscriptions of a persistent session made before that are not in it.</p>`;
    bindChips('cl-show', key => { cl.show = key; store('clients_show', key); renderClients(); });
    const input = document.getElementById('cl-q');
    input.addEventListener('input', () => { cl.q = input.value; renderClients(); });
    if (keep !== null) { input.focus(); input.setSelectionRange(keep, keep); }
    el.querySelectorAll('th[data-sort]').forEach(th => th.addEventListener('click', () => { cl.sort = th.dataset.sort; store('clients_sort', cl.sort); renderClients(); }));
    el.querySelectorAll('tr[data-client]').forEach(tr => tr.addEventListener('click', () => switchTab('clients', tr.dataset.client)));
}

async function renderClient(el, id) {
    const c = clientById(id);
    if (!c) {
        el.innerHTML = `<a href="#clients" class="back-link">← Clients</a><div class="empty-state">No client <span class="mono">${escapeHtml(id)}</span> since the broker started</div>`;
        return;
    }
    if (!cl.events[id] || Date.now() - cl.events[id].at > 5000) {
        api('events?client=' + encodeURIComponent(id)).then(r => { cl.events[id] = {at: Date.now(), list: r.events}; if (activeTab === 'clients' && activeSub === id) renderClient(el, id); }).catch(() => {});
    }
    const events = (cl.events[id] || {list: []}).list;
    el.innerHTML = `
        <a href="#clients" class="back-link">← Clients</a>
        <div class="page-head"><h2 class="mono">${escapeHtml(c.id)}</h2>${clientState(c)}</div>
        <div class="dash-grid">
            ${panel('Connection', '', `<div class="info-grid">
                ${infoRow('Login', c.user ? `${escapeHtml(c.user)} <span class="muted">${escapeHtml(LOGIN_LABELS[c.login] || '')}</span>` : 'no username')}
                ${infoRow('Address', `<span class="mono">${escapeHtml(c.ip || '–')}${c.port ? `:${c.port}` : ''}</span>`)}
                ${infoRow('Over', `${escapeHtml(c.listener_name || '–')}${c.listener ? ` <span class="muted">port ${c.listener}</span>` : ''}`)}
                ${infoRow('MQTT version', escapeHtml(c.protocol || '–'))}
                ${infoRow('Session', c.clean ? 'clean: nothing kept while away' : 'persistent: subscriptions and queued messages kept while away')}
                ${infoRow('Keepalive', c.keepalive ? `${num(c.keepalive)} s` : 'off')}
                ${infoRow(c.connected ? 'Connected' : 'Left', escapeHtml(fmtDateTime(c.connected ? c.since : c.left_at)) + (c.connected ? '' : ` <span class="muted">${escapeHtml(c.reason)}</span>`))}
                ${infoRow('First seen', escapeHtml(fmtDateTime(c.first_seen)))}
                ${infoRow('Connects', `${num(c.connects)}${c.drops ? `, <span class="warn-text">dropped off ${num(c.drops)}×</span>` : ''}`)}
            </div>`)}
            ${panel('Subscriptions', `${num(c.subs.length)}`, c.subs.length ? table([{label: 'Topic filter'}, {label: 'QoS', n: true}],
                c.subs.map(s => `<tr>${td(isPattern(s.topic) ? `<a href="#traffic" class="plain-link mono" data-watch="${attr(s.topic)}">${escapeHtml(s.topic)}</a>` : topicLink(s.topic), 'wrap-cell')}${td(String(s.qos), 'n')}</tr>`), {cls: 'compact'})
                : `<div class="empty-state">${c.connected ? 'No subscriptions: it only publishes' : 'None while away'}</div>`)}
        </div>
        ${panel('What happened', '', eventsList(events))}`;
    el.querySelectorAll('[data-watch]').forEach(a => a.addEventListener('click', (e) => { e.preventDefault(); watchTraffic(a.dataset.watch); }));
}

let clTimer = 0;
onTab('clients', () => {
    renderClients();
    loadClients().then(() => { clTimer = Date.now(); if (activeTab === 'clients') renderClients(); }).catch(e => toast(e.message));
}, () => {
    if (Date.now() - clTimer < 4000) return;
    clTimer = Date.now();
    // Not while typing in the search box: it would jump
    loadClients().then(() => { if (activeTab === 'clients' && (activeSub || document.activeElement.id !== 'cl-q')) renderClients(); }).catch(() => {});
});
