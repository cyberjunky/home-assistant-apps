// MQTT dashboard - Discovery: what Home Assistant gets over MQTT discovery (the retained configs under
// homeassistant/), as devices with their entities: which integration sent them, whether they are
// available, what's wrong with them, and removing leftovers. A device opens as #discovery/<key>.

const dc = {show: stored('discovery_show', 'all'), q: ''};
const DISCOVERY_SHOWS = [{key: 'all', label: 'All'}, {key: 'offline', label: 'Unavailable'}, {key: 'stale', label: 'No news'}, {key: 'problems', label: 'Problems'}];

async function loadDiscovery() {
    const d = await api('discovery');
    d.at = Date.now();
    app.discovery = d;
    return d;
}

function availabilityLabel(a) {
    return a === 'online' ? stateLabel('ok', 'available') : a === 'offline' ? stateLabel('bad', 'unavailable') : stateLabel('off', 'no availability');
}

function deviceRows(d) {
    const q = dc.q.toLowerCase();
    return d.devices.filter(x => {
        if (dc.show === 'offline' && x.available !== 'offline') return false;
        if (dc.show === 'stale' && !x.stale) return false;
        if (dc.show === 'problems' && !x.duplicates) return false;
        if (!q) return true;
        return [x.name, x.manufacturer, x.model, x.origin, x.area].some(v => String(v || '').toLowerCase().includes(q))
            || x.entities.some(e => [e.name, e.unique_id, e.config_topic, e.object_id].some(v => String(v || '').toLowerCase().includes(q)));
    });
}

function renderDiscovery() {
    const el = document.getElementById('tab-discovery');
    const d = app.discovery;
    if (!d) { el.innerHTML = '<div class="empty-state">Reading the discovery configs…</div>'; return; }
    if (activeSub) return renderDiscoveryDevice(el, d, activeSub);
    const offline = d.devices.filter(x => x.available === 'offline').length;
    const stale = d.devices.filter(x => x.stale).length;
    const problems = d.problems.length + d.devices.reduce((a, x) => a + x.duplicates, 0);
    const rows = deviceRows(d);
    const keep = document.activeElement && document.activeElement.id === 'dc-q' ? document.activeElement.selectionStart : null;
    const origins = Object.entries(d.origins).sort((a, b) => b[1] - a[1]);
    el.innerHTML = `
        <div class="tiles kpi-tiles">
            ${tile('Devices', num(d.devices.length), `${num(d.entities)} entities`)}
            ${tile('Unavailable', num(offline), 'their availability says offline', offline ? 'warn' : '')}
            ${tile('No news', num(stale), 'no state change for a day', stale ? 'warn' : '')}
            ${tile('Problems', num(problems), 'configs Home Assistant can\'t use', problems ? 'bad' : '')}
            ${tile('Sent by', num(origins.length), origins.slice(0, 3).map(([o]) => escapeHtml(o)).join(', ') || '–')}
        </div>
        ${d.problems.length ? panel('Configs Home Assistant can\'t read', '', table([{label: 'Config topic'}, {label: 'Problem'}, {label: ''}],
            d.problems.map(p => `<tr>${td(topicLink(p.topic), 'wrap-cell')}${td(escapeHtml(p.problem))}${td(`<button class="btn btn-small btn-danger" data-clear="${attr(p.topic)}">Remove</button>`, 'n')}</tr>`), {cls: 'compact'})) : ''}
        <div class="page-head">
            <div class="filters">
                ${chipsHtml('dc-show', DISCOVERY_SHOWS.map(o => ({...o, count: o.key === 'all' ? d.devices.length : o.key === 'offline' ? offline : o.key === 'stale' ? stale : d.devices.filter(x => x.duplicates).length})), dc.show, 'Show')}
                <input id="dc-q" class="input search" type="search" placeholder="Device, model, entity or unique_id" value="${attr(dc.q)}" autocomplete="off" spellcheck="false">
            </div>
            <span class="muted">${num(rows.length)} device${rows.length === 1 ? '' : 's'}</span>
        </div>
        ${panel('', '', `<div class="table-wrap">${table([{label: 'Device'}, {label: 'Model'}, {label: 'Sent by'}, {label: 'Entities', n: true}, {label: 'Availability'}, {label: 'Last state'}],
            rows.map(x => `<tr class="clickable" data-device="${attr(x.key)}">
                ${td(`${escapeHtml(x.name)}${x.area ? ` <span class="muted">${escapeHtml(x.area)}</span>` : ''}${x.duplicates ? ' ' + badge('duplicate id', 'bad', 'An entity\'s unique_id is used by another config too: Home Assistant ignores one of them') : ''}`, 'wrap-cell')}
                ${td(`${escapeHtml(x.manufacturer)} <span class="muted">${escapeHtml(x.model)}</span>`, 'wrap-cell')}
                ${td(escapeHtml(x.origin || '–'))}
                ${td(num(x.entities.length), 'n')}
                ${td(availabilityLabel(x.available))}
                ${td(x.has_state ? `<span class="${x.stale ? 'warn-text' : ''}">${escapeHtml(fmtAgo(x.last_update))}</span>` : '<span class="muted">–</span>', 'nowrap')}
            </tr>`), {empty: d.devices.length ? 'No device matches' : 'No discovery configs on this broker'})}</div>`)}
        <p class="note">From the retained configs under <span class="mono">${escapeHtml(d.prefix)}/</span>. "No news" counts only after the dashboard has watched for a day (since ${escapeHtml(fmtDateTime(d.watching_since))}).</p>`;
    bindChips('dc-show', key => { dc.show = key; store('discovery_show', key); renderDiscovery(); });
    const input = document.getElementById('dc-q');
    input.addEventListener('input', () => { dc.q = input.value; renderDiscovery(); });
    if (keep !== null) { input.focus(); input.setSelectionRange(keep, keep); }
    el.querySelectorAll('tr[data-device]').forEach(row => row.addEventListener('click', () => switchTab('discovery', row.dataset.device)));
    el.querySelectorAll('[data-clear]').forEach(b => b.addEventListener('click', () => removeConfigs([b.dataset.clear], 'this config')));
}

function renderDiscoveryDevice(el, d, key) {
    const x = d.devices.find(v => v.key === key);
    if (!x) { el.innerHTML = '<a href="#discovery" class="back-link">← Discovery</a><div class="empty-state">This device is no longer discovered</div>'; return; }
    el.innerHTML = `
        <a href="#discovery" class="back-link">← Discovery</a>
        <div class="page-head"><h2>${escapeHtml(x.name)}</h2>${availabilityLabel(x.available)}</div>
        <div class="dash-grid">
            ${panel('Device', '', `<div class="info-grid">
                ${infoRow('Maker', escapeHtml(x.manufacturer || '–'))}
                ${infoRow('Model', escapeHtml(x.model || '–'))}
                ${infoRow('Firmware', escapeHtml(x.sw_version || '–'))}
                ${infoRow('Area', escapeHtml(x.area || '–'))}
                ${infoRow('Sent by', escapeHtml(x.origin || 'not said (no origin in the config)'))}
                ${infoRow('Last state', escapeHtml(x.has_state ? fmtDateTime(x.last_update) : '–'))}
                ${x.url && /^https?:\/\//.test(x.url) ? infoRow('Settings', `<a href="${attr(x.url)}" target="_blank" rel="noopener" class="plain-link">${escapeHtml(x.url)}</a>`) : ''}
            </div>`)}
            ${panel('Remove', '', `<p>Clears the device's ${num(x.entities.length)} retained discovery config${x.entities.length > 1 ? 's' : ''}: Home Assistant removes its entities. For a device that is gone for good; one that is still there sends them again when it or its integration restarts.</p>
                <div class="detail-actions"><button class="btn btn-danger" id="dc-remove">Remove from Home Assistant…</button></div>`)}
        </div>
        ${panel('Entities', `${num(x.entities.length)}`, `<div class="table-wrap">${table([{label: 'Entity'}, {label: 'Type'}, {label: 'State'}, {label: 'Availability'}, {label: 'Config'}],
            x.entities.map(e => `<tr>
                ${td(`${escapeHtml(e.name || e.object_id)}${e.unique_id ? `<div class="muted mono small">${escapeHtml(e.unique_id)}</div>` : ''}${e.duplicate ? ' ' + badge('duplicate id', 'bad') : ''}`, 'wrap-cell')}
                ${td(`${escapeHtml(e.component)}${e.device_class ? ` <span class="muted">${escapeHtml(e.device_class)}</span>` : ''}`)}
                ${td(e.topics.state_topic ? `<div class="one-line">${e.state !== null ? payloadPreview(String(e.state).slice(0, 200)) : '<span class="muted">nothing yet</span>'}${e.unit ? ` <span class="muted">${escapeHtml(e.unit)}</span>` : ''}</div><div class="small">${topicLink(e.topics.state_topic)}</div>` : '<span class="muted">–</span>', 'state-cell')}
                ${td(availabilityLabel(e.available))}
                ${td(topicLink(e.config_topic), 'wrap-cell small')}
            </tr>`), {cls: 'compact'})}</div>`)}`;
    document.getElementById('dc-remove').addEventListener('click', () => removeConfigs(x.entities.map(e => e.config_topic), x.name));
}

async function removeConfigs(topics, what) {
    const unique = [...new Set(topics)];
    const ok = await confirmDialog({
        title: `Remove ${what} from Home Assistant?`, danger: true, confirmLabel: 'Remove', typeToConfirm: unique.length > 3 ? 'remove' : '',
        message: `<p>Clears ${num(unique.length)} retained discovery config${unique.length > 1 ? 's' : ''}. Home Assistant removes the entities; their history stays in the recorder.</p>
            <div class="clear-list">${unique.map(t => `<div class="mono">${escapeHtml(t)}</div>`).join('')}</div>`,
    });
    if (!ok) return;
    try {
        const r = await apiPost('clear', {topics: unique});
        toast(`Removed ${num(r.count)} config${r.count === 1 ? '' : 's'}`, 'success');
        setTimeout(() => loadDiscovery().then(() => { if (activeTab === 'discovery') { if (activeSub) switchTab('discovery'); else renderDiscovery(); } }), 800);
    } catch (e) { toast(e.message); }
}

let dcTimer = 0;
onTab('discovery', () => {
    renderDiscovery();
    loadDiscovery().then(() => { dcTimer = Date.now(); if (activeTab === 'discovery') renderDiscovery(); }).catch(e => toast(e.message));
}, () => {
    if (Date.now() - dcTimer < 10000 || document.getElementById('modal-root').innerHTML) return;
    dcTimer = Date.now();
    loadDiscovery().then(() => { if (activeTab === 'discovery' && (activeSub || document.activeElement.id !== 'dc-q')) renderDiscovery(); }).catch(() => {});
});
