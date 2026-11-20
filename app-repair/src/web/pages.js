// Repair - the pages: Overview, Issues, Log, Clean up, Activity and Info.

function drawOnce(id, key, draw) {
    const el = document.getElementById(id);
    if (!el || el._key === key) return;
    el._key = key;
    draw(el);
}

// =====================================================================
// Overview
// =====================================================================
let trendDays = [7, 30, 90].includes(Number(stored('trend'))) ? Number(stored('trend')) : 30;

function showCategory(key) {
    issueFilter.category = key;
    issueFilter.severity = '';
    switchTab('issues');
}
function showIntegration(domain) {
    issueFilter.category = '';
    issueFilter.severity = '';
    issueFilter.text = domain;
    switchTab('issues');
}

onTab('overview', async () => {
    skeleton('overview', `
        <div class="health">
            <div class="panel score" id="ov-score"></div>
            <div id="ov-tiles" class="tiles kpi-tiles"></div>
        </div>
        <div class="dash-grid">
            ${panel('What is wrong', 'by kind · click to see them', '<div id="ov-kinds" class="rows"></div>')}
            <div class="panel"><div class="panel-body">
                <div class="chart-title"><span>Health over time</span><span class="tabs" role="group" aria-label="Period" id="ov-range"></span></div>
                <div id="ov-trend" class="chart"></div>
                <div class="legend" id="ov-trend-legend"></div>
            </div></div>
        </div>
        <div class="dash-grid">
            ${panel('Needs attention first', '<span id="ov-top-hint"></span>', '<div id="ov-top"></div>')}
            ${panel('Where it comes from', 'findings per integration', '<div id="ov-integrations" class="rows"></div>')}
        </div>`);
    buildChips('ov-range', [{key: '7', label: '7 d'}, {key: '30', label: '30 d'}, {key: '90', label: '90 d'}], String(trendDays), (key) => {
        trendDays = Number(key);
        store('trend', key);
        renderActive();
    });
    const s = app.status;
    const [cls, word] = scoreWord(s.score);
    fill('ov-score', `<div class="n">${missing(s.score) ? '–' : s.score}<small>/ 100</small></div><div class="w">${escapeHtml(word)}</div>
        <div class="d">${s.scanned_at ? `scanned ${escapeHtml(fmtAgo(s.scanned_at))} · every ${num(s.config.scan_minutes)} min` : 'no scan yet'}</div>`);
    document.getElementById('ov-score').className = 'panel score ' + cls;
    if (!s.ready) return;
    const c = s.counts, t = s.totals;
    fill('ov-tiles', [
        tile('Serious', num(c.severity.high), 'broken now', c.severity.high ? 'bad' : ''),
        tile('Needs a look', num(c.severity.medium), 'not working as meant', c.severity.medium ? 'warn' : ''),
        tile('Minor', num(c.severity.low), 'untidy, nothing fails'),
        tile('Ignored', num(s.ignored), 'left out of the score'),
        tile('Entities', num(t.entities), `${num(t.registered)} registered`),
        tile('Devices', num(t.devices), `${num(t.integrations)} integrations`),
        tile('Automations', num(t.automations), `${num(t.dashboards)} dashboards read`),
        tile('Log', num(t.log), 'errors and warnings'),
    ].join(''));

    const most = Math.max(1, ...Object.values(c.category));
    fill('ov-kinds', Object.keys(s.categories).map(key => {
        const n = c.category[key];
        return `<div class="rowline ${n ? '' : 'none'}" ${n ? `onclick="showCategory('${key}')"` : ''}>
            <span class="rl-name">${escapeHtml(s.categories[key])}</span>
            <span class="rl-bar"><i style="width:${(n / most * 100).toFixed(1)}%"></i></span><span class="rl-n">${num(n)}</span></div>`;
    }).join(''));
    const top = activeFindings().slice(0, 7);
    fill('ov-top-hint', c.total ? `${num(c.total)} in all` : '');
    fill('ov-top', top.map(findingRow).join('') || '<div class="empty-state">Nothing is wrong that the app can see.</div>');
    const mostIn = Math.max(1, ...s.by_integration.map(r => r.count));
    fill('ov-integrations', s.by_integration.map(r => `<div class="rowline" onclick="showIntegration('${escapeHtml(r.domain)}')">
        <span class="rl-name">${escapeHtml(r.name)}</span><span class="rl-bar"><i style="width:${(r.count / mostIn * 100).toFixed(1)}%"></i></span><span class="rl-n">${num(r.count)}</span></div>`).join('')
        || '<div class="empty-state">No integration stands out.</div>');

    const trend = await api('trend?days=' + trendDays);
    // One line: the score. The number of findings has another unit, so it goes in the tooltip, not on this axis
    fill('ov-trend-legend', '');
    drawOnce('ov-trend', `${trendDays}:${trend.ts.length}:${trend.ts[trend.ts.length - 1]}`, el => lineChart(el, {
        x: trend.ts,
        series: [{name: 'health', color: color('series-3'), values: trend.score, area: true}],
        height: 220, zero: true, max: 100,
        formatX: fmtDateTime,
        formatY: v => `${num(v)} / 100`,
        extraRows: i => [{value: num(trend.total[i]), name: 'findings'}, {value: num(trend.high[i]), name: 'serious'}],
        xTicks: trend.ts.length > 1 ? timeAxisTicks(trend.ts[0], trend.ts[trend.ts.length - 1], 5) : [],
        empty: 'The trend starts with the second scan',
    }));
});

// =====================================================================
// Issues
// =====================================================================
const issueFilter = {category: '', severity: '', text: '', ignored: false};

function filteredFindings() {
    const words = issueFilter.text.toLowerCase().split(/\s+/).filter(Boolean);
    return app.findings.filter(f => {
        if (f.ignored !== issueFilter.ignored) return false;
        if (issueFilter.category && f.category !== issueFilter.category) return false;
        if (issueFilter.severity && f.severity !== issueFilter.severity) return false;
        const hay = `${f.title} ${f.subject.id} ${f.integration} ${f.kind} ${f.affected.join(' ')}`.toLowerCase();
        return words.every(w => hay.includes(w));
    });
}

function renderIssueList() {
    const list = filteredFindings();
    fill('is-count', `${num(list.length)} of ${num(app.findings.filter(f => f.ignored === issueFilter.ignored).length)}`);
    fill('is-table', table(
        [{label: 'Finding'}, {label: 'Kind'}, {label: 'Where'}, {label: 'Since', n: 1}, {label: 'Repair'}],
        list.map(f => `<tr class="${f.ignored ? 'ignored' : ''}" onclick="openFinding('${f.id}')">
            ${td(`<span class="sev ${f.severity}" title="${SEVERITIES[f.severity]}"></span><span class="name">${escapeHtml(f.title)}</span><span class="sub">${escapeHtml(f.text.split('. ')[0].replace(/\.$/, ''))}.</span>`, 'wide')}
            ${td(`<span class="sev-text ${f.severity}">${escapeHtml(SEVERITIES[f.severity])}</span><span class="sub">${escapeHtml(categoryName(f.category))}</span>`)}
            ${td(`${escapeHtml(f.subject.type)}<span class="sub mono">${escapeHtml(f.integration || '–')}</span>`)}
            ${td(f.since ? escapeHtml(fmtAgo(f.since).replace(' ago', '')) : '–', 'n')}
            ${td(f.fixes.length ? badge('ok', f.fixes.length > 1 ? `${f.fixes.length} repairs` : 'repair') : '<span class="muted">by hand</span>')}
        </tr>`), {cls: 'findings hover', empty: issueFilter.ignored ? 'Nothing is ignored.' : (app.findings.length ? 'Nothing matches these filters.' : 'Nothing is wrong that the app can see.')}));
}

onTab('issues', () => {
    const built = skeleton('issues', `
        <div class="filters">
            <div class="tabs" role="group" aria-label="Kind" id="is-category"></div>
        </div>
        <div class="filters">
            <div class="tabs" role="group" aria-label="How serious" id="is-severity"></div>
            <input class="input" id="is-text" placeholder="Search a name, entity or integration" aria-label="Search" autocomplete="off">
            <div class="tabs" role="group" aria-label="Show" id="is-ignored"></div>
            <span class="range-text" id="is-count"></span>
        </div>
        <div class="panel"><div class="panel-body"><div id="is-table" class="table-wrap"></div></div></div>`);
    if (built) {
        document.getElementById('is-text').addEventListener('input', (e) => { issueFilter.text = e.target.value; renderIssueList(); });
    }
    const input = document.getElementById('is-text');
    if (document.activeElement !== input) input.value = issueFilter.text;
    const s = app.status;
    const shown = app.findings.filter(f => f.ignored === issueFilter.ignored);
    const per = key => shown.filter(f => f.category === key).length;
    buildChips('is-category', [{key: '', label: `All ${shown.length}`}, ...Object.keys(s.categories).filter(key => per(key) || key === issueFilter.category).map(key => ({key, label: `${s.categories[key]} ${per(key)}`}))],
        issueFilter.category, (key) => { issueFilter.category = key; renderIssueList(); });
    buildChips('is-severity', [{key: '', label: 'Any'}, ...Object.keys(SEVERITIES).map(key => ({key, label: SEVERITIES[key]}))],
        issueFilter.severity, (key) => { issueFilter.severity = key; renderIssueList(); });
    buildChips('is-ignored', [{key: 'no', label: 'Open'}, {key: 'yes', label: `Ignored ${app.findings.filter(f => f.ignored).length}`}],
        issueFilter.ignored ? 'yes' : 'no', (key) => { issueFilter.ignored = key === 'yes'; renderActive(); });
    renderIssueList();
});

// =====================================================================
// Log
// =====================================================================
const LOG_KIND_NAMES = {offline: 'not reachable', login: 'login refused', config: 'configuration', deprecated: 'going away', slow: 'slow or blocking', bug: 'error in the code', other: 'other'};
let logFilter = '';

function openLog(key) {
    const e = app.log.find(x => x.key === key);
    if (!e) return;
    const finding = app.findings.find(f => f.id === key);
    openSheet(e.message || 'Log line', `
        <div class="badges">
            <span class="badge ${e.level === 'WARNING' ? 'warn' : 'bad'}">${escapeHtml(e.level)}</span>
            <span class="badge mode">${escapeHtml(LOG_KIND_NAMES[e.kind] || e.kind)}</span>
            ${e.custom ? '<span class="badge warn">custom integration</span>' : ''}
        </div>
        <h3>Logged</h3>
        <p>${num(e.count)} time${e.count === 1 ? '' : 's'}${e.first ? ` · first ${escapeHtml(fmtDateTime(e.first))}` : ''}${e.last ? ` · last ${escapeHtml(fmtDateTime(e.last))}` : ''}</p>
        <h3>Message</h3><ul class="evidence">${e.messages.map(m => `<li>${escapeHtml(m)}</li>`).join('')}</ul>
        <h3>From</h3><ul class="evidence"><li>${escapeHtml(e.logger)}</li>${e.source ? `<li>${escapeHtml(e.source)}</li>` : ''}</ul>
        ${e.exception ? `<h3>Error</h3><div class="trace">${escapeHtml(e.exception)}</div>` : ''}
        ${finding ? `<h3>As a finding</h3><div class="actions"><button type="button" class="btn btn-primary" onclick="openFinding('${finding.id}')">Open the finding</button></div>` : ''}`);
}

onTab('log', () => {
    skeleton('log', `
        <div class="filters">
            <div class="tabs" role="group" aria-label="Kind" id="lg-kind"></div>
            <span class="range-text" id="lg-count"></span>
        </div>
        <div class="panel"><div class="panel-body"><div id="lg-table" class="table-wrap"></div></div></div>
        <div class="note" style="margin-top:14px">These are the errors and warnings Home Assistant holds in memory since it started, each once with how often it came. The full log file is under Settings → System → Logs.</div>`);
    const kinds = [...new Set(app.log.map(e => e.kind))];
    buildChips('lg-kind', [{key: '', label: `All ${app.log.length}`}, ...kinds.map(key => ({key, label: `${LOG_KIND_NAMES[key] || key} ${app.log.filter(e => e.kind === key).length}`}))],
        logFilter, (key) => { logFilter = key; renderActive(); });
    const list = app.log.filter(e => !logFilter || e.kind === logFilter);
    fill('lg-count', `${num(list.reduce((a, e) => a + e.count, 0))} lines in ${num(list.length)} messages`);
    fill('lg-table', table(
        [{label: 'Last'}, {label: 'Level'}, {label: 'From'}, {label: 'About'}, {label: 'Message'}, {label: 'Times', n: 1}],
        list.map(e => `<tr onclick="openLog('${e.key}')">
            ${td(e.last ? escapeHtml(fmtAgo(e.last)) : '–', 'muted')}
            ${td(badge(e.level === 'WARNING' ? 'warn' : 'bad', e.level.toLowerCase()))}
            ${td(`<span class="name mono">${escapeHtml(e.domain || 'core')}</span>${e.custom ? ' ' + badge('warn', 'custom') : ''}`)}
            ${td(escapeHtml(LOG_KIND_NAMES[e.kind] || e.kind))}
            ${td(escapeHtml(e.message), 'wide')}
            ${td(num(e.count), 'n')}
        </tr>`), {cls: 'findings hover', empty: 'Home Assistant has logged no errors or warnings since it started.'}));
});

// =====================================================================
// Clean up: everything Home Assistant holds about one entity or device
// =====================================================================
let cleanupItem = null;
let searchTimer = null;

async function runSearch(text) {
    if (!text.trim()) return fill('cu-results', '');
    try {
        const {results} = await api('search?q=' + encodeURIComponent(text));
        fill('cu-results', results.map(r => `<div class="result" onclick="inspectItem('${r.type}', '${escapeHtml(r.id)}')">
            ${badge('mode', r.type)}<span class="r-name">${escapeHtml(r.name)}</span><span class="r-detail">${escapeHtml(r.detail)}</span></div>`).join('')
            || '<div class="empty-state">Nothing in Home Assistant matches.</div>');
    } catch (e) {
        toast(e.message);
    }
}

async function inspectItem(type, id) {
    try {
        cleanupItem = await api(`inspect?type=${type}&id=${encodeURIComponent(id)}`);
        fill('cu-result', '');
        renderCleanup();
    } catch (e) {
        cleanupItem = null;
        renderCleanup();
        toast('Home Assistant no longer has it');
    }
}

function renderCleanup() {
    const it = cleanupItem;
    if (!it) return fill('cu-item', '');
    const stateText = e => missing(e.state) ? '<span class="muted">no state</span>' : e.restored ? stateLabel('warn', 'left behind') : e.state === 'unavailable' ? stateLabel('bad', 'unavailable') : `<span class="mono">${escapeHtml(e.state)}</span>`;
    const stored = [];
    const registered = it.entities.filter(e => e.registered).length;
    if (it.type === 'device') stored.push('the device in the device registry');
    if (registered) stored.push(`${registered} entr${registered === 1 ? 'y' : 'ies'} in the entity registry`);
    stored.push(`the recorded history of ${it.entities.length} entit${it.entities.length === 1 ? 'y' : 'ies'}`);
    const withStats = it.entities.filter(e => e.statistics).length;
    if (withStats) stored.push(`the long-term statistics of ${withStats}`);
    const related = app.findings.filter(f => it.findings.includes(f.id));
    fill('cu-item', `
        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>${escapeHtml(it.type)} · ${escapeHtml(it.name)}</span><span class="hint mono">${escapeHtml(it.id)}</span></div>
            ${it.type === 'device' ? infoRow('Made by', escapeHtml([it.maker, it.model].filter(Boolean).join(' · ') || '–')) + infoRow('Held by', it.integrations.map(i => escapeHtml(i.title)).join('<br>') || '<span class="neg">no integration: left behind</span>') : ''}
            ${it.device ? infoRow('Part of', `<a class="ha-link" href="#" onclick="inspectItem('device', '${escapeHtml(it.device.id)}'); return false">${escapeHtml(it.device.name)}</a>`) : ''}
            <div class="section-title" style="margin-top:14px">Entities</div>
            <div class="table-wrap">${table(
                [{label: 'Entity'}, {label: 'State'}, {label: 'Registry'}, {label: 'Integration'}, {label: 'Statistics'}],
                it.entities.map(e => `<tr>${td(`<span class="name">${escapeHtml(e.name)}</span><span class="sub mono">${escapeHtml(e.entity_id)}</span>`)}${td(stateText(e))}
                    ${td(e.registered ? (e.disabled ? 'disabled' : 'yes') : '<span class="muted">not registered</span>')}${td(escapeHtml(e.integration || e.platform || '–'), 'muted')}${td(e.statistics ? 'yes' : '<span class="muted">none</span>')}</tr>`),
                {empty: 'It has no entities.'})}</div>
            <div class="section-title" style="margin-top:14px">Used in</div>
            ${it.used_in.length ? `<div class="linklist">${it.used_in.map(u => linkHtml({label: `${u.type}: ${u.name}`, path: u.path})).join('')}</div>
                <div class="note warn" style="margin-top:8px">These keep naming it after it is removed. The app does not edit automations or dashboards: open each and take it out there.</div>`
                : '<p class="muted">No automation, script, scene, group or dashboard the app could read names it.</p>'}
            ${related.length ? `<div class="section-title" style="margin-top:14px">Findings about it</div>${related.map(findingRow).join('')}` : ''}
            <div class="danger-zone">
                <div class="section-title">Remove completely</div>
                <p class="panel-sub">Removes ${escapeHtml(stored.join(', '))}. It can't be undone here: a backup of Home Assistant is the way back. If its integration still offers it, it comes back by itself.</p>
                ${it.type === 'device' && !it.removable ? `<p class="muted">${escapeHtml(it.not_removable)}: remove it on the integration's own page, or reset the device. Its entities alone are not removed from here, because the integration would make them again.</p>` : fixesAllowed() ? `<button type="button" class="btn btn-danger" onclick="removeItem()">Remove ${escapeHtml(it.name)} and everything stored about it</button>` : '<p class="muted">Repairs are turned off in the app\'s configuration.</p>'}
                <div id="cu-result"></div>
            </div>
        </div></div>`);
}

async function removeItem() {
    const it = cleanupItem;
    if (!it) return;
    const ok = await confirmDialog({
        title: `Remove ${it.name}?`,
        message: `Its ${it.type === 'device' ? 'device, its entities, their' : 'registry entry, its'} history and statistics are deleted from Home Assistant. This can't be undone here.`,
        typeToConfirm: 'REMOVE', confirmLabel: 'Remove', danger: true,
    });
    if (!ok) return;
    const target = document.getElementById('cu-result');
    target.innerHTML = '<p class="muted" style="margin-top:10px">Working…</p>';
    try {
        const result = await apiPost('cleanup', {type: it.type, id: it.id, word: 'REMOVE'});
        target.innerHTML = stepsHtml(result);
        toast(result.ok ? 'Removed' : 'Not everything could be removed', result.ok ? 'success' : 'error');
        await loadStatus();
        // What is left of it, if anything
        try {
            cleanupItem = await api(`inspect?type=${it.type}&id=${encodeURIComponent(it.id)}`);
        } catch (e) {
            cleanupItem = null;
            fill('cu-item', `<div class="panel"><div class="panel-body"><div class="section-title">${escapeHtml(it.name)} is gone</div>${stepsHtml(result)}</div></div>`);
            return;
        }
        renderCleanup();
        fill('cu-result', stepsHtml(result));
    } catch (e) {
        target.innerHTML = '';
        toast(e.message);
    }
}

onTab('cleanup', () => {
    const built = skeleton('cleanup', `
        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Find an entity or a device</span><span class="hint">to see everything Home Assistant holds about it, and remove it completely</span></div>
            <div class="search-row"><input class="input" id="cu-text" placeholder="A name or an entity id, for example hallway or sensor.old_power" aria-label="Search" autocomplete="off"></div>
            <div id="cu-results"></div>
        </div></div>
        <div id="cu-item" class="gap-top"></div>
        <div id="cu-storage" class="gap-top"></div>`);
    if (built) {
        document.getElementById('cu-text').addEventListener('input', (e) => {
            clearTimeout(searchTimer);
            const text = e.target.value;
            searchTimer = setTimeout(() => runSearch(text), 200);
        });
    }
    // The storage panel is only drawn again when the scan changed, so a half-made pick isn't lost
    const storageKey = String(app.status.scanned_at) + app.status.config.deep_scan;
    const box = document.getElementById('cu-storage');
    if (box._key !== storageKey) {
        box._key = storageKey;
        box.innerHTML = storageHtml();
        const input = document.getElementById('pg-filter');
        if (input) input.addEventListener('input', (e) => { purgeFilter = e.target.value; renderPurgeTable(); });
        renderPurgeTable();
    }
});

// What the deep scan found in Home Assistant's own storage: leftovers its API doesn't show
const purgePick = new Set();
let purgeFilter = '';

function storageHtml() {
    const s = app.status, st = s.storage;
    if (!s.config.deep_scan) {
        return `<div class="note">The <b>deep scan</b> is off. With it on, the app also reads Home Assistant's database and registry files, and finds recorded history of entities that no longer exist. It only reads them: deleting is still done by Home Assistant. Turn on <b>deep_scan</b> in the app's configuration and restart the app.</div>`;
    }
    if (!st) return '';
    if (!st.available) return `<div class="note warn">Deep scan: ${escapeHtml(st.note || 'the configuration folder can\'t be read')}</div>`;
    const db = st.database, reg = st.registry;
    const orphans = db.orphans || [];
    const rows = orphans.reduce((a, o) => a + (o.rows || 0), 0);
    const dbRows = !db.found ? infoRow('Database', `<span class="muted">${escapeHtml(db.note)}</span>`)
        : db.error ? infoRow('Database', `<span class="neg">${escapeHtml(db.error)}</span>`)
        : [
            infoRow('Database', `${escapeHtml(fmtBytes(db.size, 1))} · ${num(db.entities)} entities recorded`),
            infoRow('History of entities that no longer exist', orphans.length ? `${num(orphans.length)} entities · ${num(rows)} states` : 'none'),
        ].join('');
    return `<div class="panel"><div class="panel-body">
        <div class="chart-title"><span>Leftovers in Home Assistant's storage</span><span class="hint">from the deep scan · read only</span></div>
        ${dbRows}
        ${orphans.length ? `
            <div class="filters" style="margin:12px 0 6px">
                <input class="input" id="pg-filter" placeholder="Filter, for example device_tracker or toon" aria-label="Filter" autocomplete="off" value="${escapeHtml(purgeFilter)}">
                <button type="button" class="btn btn-small" onclick="pickShown(true)">Tick all shown</button>
                <button type="button" class="btn btn-small" onclick="pickShown(false)">Untick all shown</button>
                <span class="range-text" id="pg-picked"></span>
            </div>
            <div class="table-wrap pick-table" id="pg-table"></div>
            <div class="detail-actions" style="margin-top:10px">
                ${fixesAllowed() ? '<button type="button" class="btn btn-danger" id="pg-go" onclick="purgePicked()" disabled>Delete the history of the ticked entities</button>' : '<span class="muted">Repairs are turned off in the app\'s configuration.</span>'}
            </div>
            <div id="pg-result"></div>` : ''}
        <div class="section-title" style="margin-top:16px">What the registries still remember</div>
        ${infoRow('Removed entities', missing(reg.deleted_entities) ? '<span class="muted">not readable</span>' : num(reg.deleted_entities))}
        ${infoRow('Removed devices', missing(reg.deleted_devices) ? '<span class="muted">not readable</span>' : num(reg.deleted_devices))}
        <div class="note" style="margin-top:8px">Home Assistant keeps a note of what was removed, so that a device that comes back gets its names and settings again. They take next to no room and Home Assistant clears them out itself. The app doesn't remove them: that would mean editing a file Home Assistant has open.</div>
    </div></div>`;
}

function shownOrphans() {
    const orphans = (((app.status.storage || {}).database || {}).orphans) || [];
    const words = purgeFilter.toLowerCase().split(/\s+/).filter(Boolean);
    return orphans.filter(o => words.every(w => o.entity_id.includes(w)));
}

function renderPurgeTable() {
    const el = document.getElementById('pg-table');
    if (!el) return;
    const orphans = (((app.status.storage || {}).database || {}).orphans) || [];
    // What is ticked but no longer gone (deleted meanwhile) drops out of the pick
    const gone = new Set(orphans.map(o => o.entity_id));
    [...purgePick].forEach(e => { if (!gone.has(e)) purgePick.delete(e); });
    const shown = shownOrphans();
    el.innerHTML = table(
        [{label: ''}, {label: 'Entity that is gone'}, {label: 'Stored states', n: 1}],
        shown.map(o => `<tr onclick="togglePick('${escapeHtml(o.entity_id)}')">
            ${td(`<input type="checkbox" ${purgePick.has(o.entity_id) ? 'checked' : ''} aria-label="Pick ${escapeHtml(o.entity_id)}" tabindex="-1">`, 'pick')}
            ${td(`<span class="mono">${escapeHtml(o.entity_id)}</span>`)}${td(missing(o.rows) ? '–' : num(o.rows), 'n')}</tr>`),
        {cls: 'hover', empty: 'Nothing matches this filter.'});
    const picked = orphans.filter(o => purgePick.has(o.entity_id));
    const states = picked.reduce((a, o) => a + (o.rows || 0), 0);
    fill('pg-picked', picked.length ? `${num(picked.length)} ticked · ${num(states)} states` : `${num(shown.length)} of ${num(orphans.length)} shown`);
    const go = document.getElementById('pg-go');
    if (go) {
        go.disabled = !picked.length;
        go.textContent = picked.length ? `Delete the history of ${num(picked.length)} ticked entit${picked.length === 1 ? 'y' : 'ies'}` : 'Delete the history of the ticked entities';
    }
}

function togglePick(entityId) {
    if (purgePick.has(entityId)) purgePick.delete(entityId); else purgePick.add(entityId);
    renderPurgeTable();
}

function pickShown(on) {
    shownOrphans().forEach(o => { if (on) purgePick.add(o.entity_id); else purgePick.delete(o.entity_id); });
    renderPurgeTable();
}

async function purgePicked() {
    const picked = [...purgePick];
    if (!picked.length) return;
    const ok = await confirmDialog({
        title: `Delete the history of ${num(picked.length)} entit${picked.length === 1 ? 'y' : 'ies'}?`,
        message: `The recorder is asked to delete everything it stored of ${picked.length === 1 ? `<b>${escapeHtml(picked[0])}</b>` : 'the ticked entities'}. They no longer exist in Home Assistant. This can't be undone here.`,
        typeToConfirm: 'DELETE', confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    const target = document.getElementById('pg-result');
    target.innerHTML = '<p class="muted" style="margin-top:10px">Working…</p>';
    try {
        const result = await apiPost('purge', {entity_ids: picked, word: 'DELETE'});
        purgePick.clear();
        toast(result.ok ? 'Asked the recorder to delete it' : 'Not everything worked', result.ok ? 'success' : 'error');
        await loadStatus();
        document.getElementById('cu-storage')._key = null;
        renderActive();
        const after = document.getElementById('pg-result');
        if (after) after.innerHTML = stepsHtml(result) + '<p class="muted" style="margin-top:6px">The recorder deletes in the background: a big purge shows in the next scans. The database file only gets smaller after Home Assistant\'s nightly purge with repack.</p>';
    } catch (e) {
        target.innerHTML = '';
        toast(e.message);
    }
}

// =====================================================================
// Activity: what the app changed
// =====================================================================
const FIX_NAMES = {reload_entry: 'Reloaded an integration', delete_entry: 'Removed an integration', disable_entity: 'Disabled an entity', enable_entity: 'Enabled an entity',
    clear_statistics: 'Deleted statistics', purge_history: 'Deleted history', cleanup_entity: 'Removed an entity', cleanup_device: 'Removed a device', apply_suggestion: 'Applied a suggestion'};

onTab('activity', async () => {
    skeleton('activity', `<div class="panel"><div class="panel-body">
        <div class="chart-title"><span>What the app changed</span><span class="hint">every repair, with each step and whether it worked</span></div>
        <div id="ac-list"></div></div></div>`);
    app.actions = (await api('actions')).actions;
    fill('ac-list', app.actions.map(a => `<div class="finding" style="cursor:default">
        <span class="sev ${a.ok ? '' : 'high'}" style="${a.ok ? 'background:var(--success-color)' : ''}"></span>
        <div class="f-main"><div class="f-title">${escapeHtml(FIX_NAMES[a.fix] || a.fix)}: ${escapeHtml(a.subject)}</div>
        <div class="f-sub">${escapeHtml(fmtDateTime(a.ts))} · ${a.ok ? 'worked' : 'not everything worked'}</div>${stepsHtml(a)}</div></div>`).join('')
        || '<div class="empty-state">The app has not changed anything yet.</div>');
});

// =====================================================================
// Info
// =====================================================================
const READS = [
    ['Entities and their states', 'get_states, entity registry', 'unavailable, left behind, registered without a state'],
    ['Devices', 'device registry', 'not reachable, without entities, without an integration'],
    ['Integrations', 'config entries, manifests', 'failed to start, retrying, login refused'],
    ['Automations, scripts, scenes, groups, dashboards', 'their configuration', 'names of entities and devices that no longer exist'],
    ['Statistics', 'recorder: validate and list statistics', 'changed units, no longer recorded, left behind'],
    ['Repairs', 'Home Assistant\'s own repairs list', 'shown as they are, with a link'],
    ['Log', 'system_log', 'errors and warnings, sorted by what they are about'],
    ['Configuration', 'check_config', 'whether configuration.yaml is valid'],
    ['System', 'the Supervisor\'s resolution centre', 'unhealthy, unsupported, issues and suggestions'],
    ['Storage, with the deep scan on', 'the database and the registry files, read only', 'history of entities that no longer exist'],
];
const CHANGES = [
    ['Reload an integration', 'config entries: reload'],
    ['Disable an entity', 'entity registry: update'],
    ['Remove an entity', 'recorder.purge_entities, clear statistics, entity registry: remove'],
    ['Remove a device', 'the same for each of its entities, then device registry: remove from its integration'],
    ['Delete statistics', 'recorder: clear statistics'],
    ['Apply a suggestion', 'the Supervisor\'s resolution centre'],
    ['Delete history of gone entities', 'recorder.purge_entities, by pattern'],
];

onTab('info', () => {
    skeleton('info', `
        <div class="info-grid">
            ${panel('This app', '', '<div id="info-app"></div>')}
            <div class="panel"><div class="panel-body">
                <div class="section-title">Its own data</div>
                <div id="info-data"></div>
                <div class="detail-actions" style="margin-top:12px"><a class="btn" href="api/backup" download>Database backup</a></div>
                <div class="danger-zone">
                    <div class="section-title">Danger zone</div>
                    <p class="panel-sub">Deletes the health trend and the list of what the app changed. Home Assistant is not touched, and what you ignored stays ignored.</p>
                    <button type="button" class="btn btn-danger" onclick="deleteAllData()">Delete history</button>
                </div>
            </div></div>
        </div>
        <div class="page-section"><span>What it reads</span><small>on every scan, through Home Assistant's own API</small></div>
        <div class="panel"><div class="panel-body"><div id="info-reads" class="table-wrap"></div></div></div>
        <div class="page-section"><span>What it can change</span><small>only when you press a button, and only these</small></div>
        <div class="panel"><div class="panel-body"><div id="info-changes" class="table-wrap"></div>
            <div class="note" style="margin-top:10px">The app never opens Home Assistant's database or its .storage files. What Home Assistant's API can't do is told as a step that failed, not forced another way. It does not edit automations, scripts or dashboards.</div></div></div>`);
    const s = app.status, db = s.database;
    fill('info-app', [
        infoRow('Version', escapeHtml(s.version)),
        infoRow('Home Assistant', escapeHtml(s.ha_version || '–')),
        infoRow('Scans every', `${num(s.config.scan_minutes)} min`),
        infoRow('Last scan', s.scanned_at ? `${escapeHtml(fmtDateTime(s.scanned_at))} · took ${num(s.scan_seconds, 1)} s` : '–'),
        infoRow('Configuration check', s.config.check_config ? 'on every scan' : 'off'),
        infoRow('Repairs', s.config.allow_fixes ? 'allowed' : 'off <span class="muted">· reports only</span>'),
        infoRow('Supervisor', s.supervisor ? 'read' : '<span class="muted">not readable</span>'),
        infoRow('Deep scan', s.config.deep_scan ? 'on <span class="muted">· reads the database and registry files</span>' : 'off'),
        infoRow('Changelog', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-repair/CHANGELOG.md" target="_blank" rel="noopener">on GitHub</a>'),
        infoRow('Note', 'Unofficial: not made by or affiliated with the Home Assistant project'),
    ].join(''));
    fill('info-data', [
        infoRow('Scans', `${num(db.scans)} <span class="muted">kept a year</span>`),
        infoRow('Repairs done', num(db.actions)),
        infoRow('Ignored findings', num(db.ignored)),
        infoRow('Since', db.oldest ? escapeHtml(fmtDateTime(db.oldest)) : '–'),
        infoRow('Size', escapeHtml(fmtBytes(db.size))),
    ].join(''));
    fill('info-reads', table([{label: 'What'}, {label: 'From'}, {label: 'Looks for'}], READS.map(r => `<tr>${td(`<span class="name">${r[0]}</span>`, 'wrap')}${td(r[1], 'wrap mono muted')}${td(r[2], 'wrap')}</tr>`)));
    fill('info-changes', table([{label: 'Repair'}, {label: 'Through'}], CHANGES.map(r => `<tr>${td(`<span class="name">${r[0]}</span>`)}${td(r[1], 'wrap mono muted')}</tr>`)));
});

async function deleteAllData() {
    const ok = await confirmDialog({
        title: 'Delete the app\'s history?',
        message: 'Deletes the health trend and the list of what the app changed. Home Assistant is not touched.',
        typeToConfirm: 'DELETE', confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    try {
        const answer = await apiPost('data/delete', {confirm: 'DELETE'});
        toast(`Deleted ${num(answer.removed.scans)} scans and ${num(answer.removed.actions)} repairs`, 'success');
        await loadStatus();
    } catch (e) {
        toast(e.message);
    }
}
