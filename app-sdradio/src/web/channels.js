// SDRadio dashboard - Channels: the stored channels, new finds, CSV and backups.

const chPage = {view: 'all', selected: null, draft: null, sort: {key: 'number', desc: false}, finds: null, ignored: []};
const CH_VIEWS = [{key: 'all', label: 'All'}, {key: 'fav', label: '★ Favourites'}, {key: 'finds', label: 'New finds'}, {key: 'locked', label: 'Locked out'}];
const CH_COLUMNS = [
    {key: 'number', label: '#'}, {key: 'favourite', label: '★'}, {key: 'frequency', label: 'Frequency'}, {key: 'name', label: 'Name'},
    {key: 'band', label: 'Band'}, {key: 'mode', label: 'Mode'}, {key: 'bandwidth', label: 'Filter'}, {key: 'squelch', label: 'Squelch'},
    {key: 'tags', label: 'Tags'}, {key: 'last_heard', label: 'Last heard'}, {key: 'hits', label: 'Hits', n: true},
];

function initChannels() {
    buildChips(document.getElementById('ch-view'), CH_VIEWS, chPage.view, (key) => { chPage.view = key; renderChannelsPage(); });
    ['ch-search', 'ch-band', 'ch-mode'].forEach(id => document.getElementById(id).addEventListener(id === 'ch-search' ? 'input' : 'change', renderChannelsPage));
    document.getElementById('ch-export').href = basePath + 'api/channels/export';
    document.getElementById('backup-file').addEventListener('change', uploadBackup);
}

function renderFilterSelects() {
    const band = document.getElementById('ch-band');
    const mode = document.getElementById('ch-mode');
    const b = band.value, m = mode.value;
    const bands = [...new Set(app.channels.map(c => c.band).filter(Boolean))].sort();
    band.innerHTML = '<option value="">All bands</option>' + bands.map(x => `<option${x === b ? ' selected' : ''}>${escapeHtml(x)}</option>`).join('');
    mode.innerHTML = '<option value="">All modes</option>' + Object.keys(app.modes).map(x => `<option value="${x}"${x === m ? ' selected' : ''}>${modeLabel(x)}</option>`).join('');
}

function clearChannelFilter() {
    document.getElementById('ch-search').value = '';
    document.getElementById('ch-band').value = '';
    document.getElementById('ch-mode').value = '';
    renderChannelsPage();
}

async function renderChannelsPage() {
    const search = document.getElementById('ch-search').value.trim().toLowerCase();
    const band = document.getElementById('ch-band').value;
    const mode = document.getElementById('ch-mode').value;
    document.getElementById('ch-clear').hidden = !search && !band && !mode;
    if (chPage.view === 'finds') return renderFinds(search);
    let rows = app.channels.filter(c => (!band || c.band === band) && (!mode || c.mode === mode)
        && (chPage.view !== 'fav' || c.favourite) && (chPage.view !== 'locked' || !c.scan)
        && (!search || (c.name || '').toLowerCase().includes(search) || fmtMHz(c.frequency).includes(search)
            || String(c.number).includes(search) || (c.tags || '').toLowerCase().includes(search) || (c.notes || '').toLowerCase().includes(search)));
    const {key, desc} = chPage.sort;
    rows = rows.slice().sort((a, b) => {
        const va = a[key] ?? '', vb = b[key] ?? '';
        const c = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb));
        return desc ? -c : c;
    });
    document.getElementById('ch-count').textContent = `${rows.length} of ${app.channels.length} channels`;
    const head = CH_COLUMNS.map(c => `<th class="sortable${c.n ? ' n' : ''}" data-sort="${c.key}">${c.label}${c.key === key ? (desc ? ' ↓' : ' ↑') : ''}</th>`).join('');
    const body = rows.map(c => `
        <tr class="link${c.id === chPage.selected ? ' sel' : ''}" data-id="${c.id}">
            <td class="num">${chNum(c.number)}</td>
            <td class="star">${c.favourite ? '★' : ''}</td>
            <td class="mono"><b>${fmtMHz(c.frequency)}</b></td>
            <td>${escapeHtml(c.name || '')}${c.scan ? '' : ' <span class="badge warn">L/O</span>'}${c.priority ? ' <span class="badge">PRI</span>' : ''}</td>
            <td>${c.band ? `<span class="badge">${escapeHtml(c.band)}</span>` : ''}</td>
            <td class="mono">${modeLabel(c.mode)}</td>
            <td class="mono muted">${fmtKHz(c.bandwidth)}</td>
            <td class="mono muted">${c.squelch === null ? 'off' : c.squelch}</td>
            <td class="muted">${escapeHtml(c.tags || '')}</td>
            <td class="mono muted">${c.last_heard ? escapeHtml(fmtDateTime(c.last_heard)) : '—'}</td>
            <td class="n">${c.hits ? num(c.hits) : '—'}</td>
        </tr>`).join('');
    const el = document.getElementById('ch-table');
    el.innerHTML = rows.length
        ? `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
        : `<div class="empty-state">${app.channels.length ? 'No channel matches' : 'No channels yet: add one, store one from the Radio page, or import a CSV.'}</div>`;
    el.onclick = (e) => {
        const th = e.target.closest('[data-sort]');
        if (th) {
            const k = th.dataset.sort;
            chPage.sort = {key: k, desc: chPage.sort.key === k ? !chPage.sort.desc : ['last_heard', 'hits', 'favourite'].includes(k)};
            return renderChannelsPage();
        }
        const tr = e.target.closest('[data-id]');
        if (tr) selectChannel(Number(tr.dataset.id));
    };
    if (!chPage.draft) renderEditor();
}

function selectChannel(id) {
    chPage.selected = id;
    chPage.draft = null;
    if (chPage.view === 'finds') {
        chPage.view = 'all';
        buildChips(document.getElementById('ch-view'), CH_VIEWS, 'all', (key) => { chPage.view = key; renderChannelsPage(); });
    }
    renderChannelsPage();
    renderEditor();
}

function newChannel(preset) {
    chPage.selected = null;
    chPage.draft = Object.assign({number: app.nextNumber, mode: 'nfm', scan: 1, favourite: 0, priority: 0}, preset || {});
    renderChannelsPage();
    renderEditor();
}

function renderEditor() {
    const el = document.getElementById('ch-edit');
    const c = chPage.draft || app.channels.find(x => x.id === chPage.selected);
    if (!c) {
        el.innerHTML = '<div class="chart-title"><span>Edit</span></div><p class="panel-sub">Pick a channel in the table to edit it, or add a new one.</p>';
        return;
    }
    const isNew = !c.id;
    const modes = Object.keys(app.modes).map(m => `<option value="${m}"${m === c.mode ? ' selected' : ''}>${modeLabel(m)}</option>`).join('');
    const bws = ((app.modes[c.mode] || {}).bandwidths || []);
    const bwOptions = [...new Set([...bws, c.bandwidth].filter(Boolean))].sort((a, b) => a - b)
        .map(b => `<option value="${b}"${b === c.bandwidth ? ' selected' : ''}>${fmtKHz(b)}</option>`).join('');
    const steps = [''].concat(app.steps).map(s => `<option value="${s}"${String(s) === String(c.step ?? '') ? ' selected' : ''}>${s ? fmtKHz(s) : '(band step)'}</option>`).join('');
    el.innerHTML = `
        <div class="chart-title"><span>${isNew ? 'New channel' : `Edit · M${chNum(c.number)}`}</span>
            ${isNew ? '' : '<button type="button" class="btn btn-small" onclick="tuneEdited()">Tune now</button>'}</div>
        <div class="form-grid">
            <div class="form-field full"><label for="ed-name">Name</label><input id="ed-name" class="input" maxlength="80" value="${escapeHtml(c.name || '')}"></div>
            <div class="form-field"><label for="ed-freq">Frequency (MHz)</label><input id="ed-freq" class="input mono" value="${c.frequency ? (c.frequency / 1e6).toFixed(6) : ''}"></div>
            <div class="form-field"><label for="ed-number">Number</label><input id="ed-number" class="input mono" type="number" min="0" max="99999" value="${c.number ?? ''}"></div>
            <div class="form-field"><label for="ed-mode">Mode</label><select id="ed-mode" class="input">${modes}</select></div>
            <div class="form-field"><label for="ed-bw">Filter</label><select id="ed-bw" class="input">${bwOptions || '<option value="">(mode default)</option>'}</select></div>
            <div class="form-field"><label for="ed-step">Tune step</label><select id="ed-step" class="input">${steps}</select></div>
            <div class="form-field"><label for="ed-sq">Squelch (dB)</label><input id="ed-sq" class="input mono" value="${c.squelch ?? ''}" placeholder="off"></div>
            <div class="form-field"><label for="ed-gain">RF gain (dB)</label><input id="ed-gain" class="input mono" value="${c.gain ?? ''}" placeholder="as it is"></div>
            <div class="form-field"><label for="ed-band">Band</label><select id="ed-band" class="input">${bandOptions(c.band)}</select></div>
            <div class="form-field full"><label for="ed-tags">Tags</label><input id="ed-tags" class="input" value="${escapeHtml(c.tags || '')}"></div>
            <div class="form-field full"><label for="ed-notes">Notes</label><textarea id="ed-notes" class="input">${escapeHtml(c.notes || '')}</textarea></div>
        </div>
        <div class="checks">
            <label><input type="checkbox" id="ed-fav"${c.favourite ? ' checked' : ''}> Favourite</label>
            <label><input type="checkbox" id="ed-scan"${c.scan ? ' checked' : ''}> In the channel scan</label>
            <label><input type="checkbox" id="ed-pri"${c.priority ? ' checked' : ''}> Priority</label>
        </div>
        <div class="form-error" id="ed-error" hidden></div>
        <div class="detail-actions">
            <button type="button" class="btn btn-primary" onclick="saveEdited()">Save</button>
            <button type="button" class="btn" onclick="cancelEdit()">Cancel</button>
            <span class="spacer"></span>
            ${isNew ? '' : '<button type="button" class="btn btn-danger" onclick="deleteEdited()">Delete</button>'}
        </div>`;
    document.getElementById('ed-mode').addEventListener('change', (e) => {
        // Another mode: its filter widths
        const draft = readEditor();
        draft.mode = e.target.value;
        draft.bandwidth = null;
        chPage.draft = Object.assign({}, c, draft);
        renderEditor();
    });
}

function readEditor() {
    const v = id => document.getElementById(id).value;
    const freq = parseFloat(v('ed-freq').replace(',', '.'));
    return {
        name: v('ed-name'), number: v('ed-number'), frequency: Number.isFinite(freq) ? Math.round(freq * 1e6) : v('ed-freq'),
        mode: v('ed-mode'), bandwidth: v('ed-bw') ? Number(v('ed-bw')) : null, step: v('ed-step') || null,
        squelch: v('ed-sq').trim() === '' || v('ed-sq').trim() === 'off' ? null : v('ed-sq').replace(',', '.'),
        gain: v('ed-gain').trim() === '' ? null : v('ed-gain').replace(',', '.'),
        band: v('ed-band'), tags: v('ed-tags'), notes: v('ed-notes'),
        favourite: document.getElementById('ed-fav').checked, scan: document.getElementById('ed-scan').checked,
        priority: document.getElementById('ed-pri').checked,
    };
}

async function saveEdited() {
    const c = chPage.draft || app.channels.find(x => x.id === chPage.selected);
    const data = readEditor();
    const error = document.getElementById('ed-error');
    try {
        const saved = c && c.id
            ? await api(`channels/${c.id}`, {method: 'PUT', json: data})
            : await api('channels', {method: 'POST', json: data});
        if (c && c.from_find) await loadFinds();
        chPage.draft = null;
        chPage.selected = saved.id;
        await loadChannels();
        toast(`Saved M${chNum(saved.number)}`, 'success');
        renderChannelsPage();
        renderEditor();
    } catch (e) {
        error.textContent = e.message;
        error.hidden = false;
    }
}

function cancelEdit() {
    chPage.draft = null;
    if (!app.channels.find(x => x.id === chPage.selected)) chPage.selected = null;
    renderEditor();
}

function tuneEdited() {
    if (chPage.selected) radioCommand('channel', {id: chPage.selected});
    toast('Tuned');
}

function deleteEdited() {
    const c = app.channels.find(x => x.id === chPage.selected);
    if (!c) return;
    confirmModal('Delete channel', `Delete <b>M${chNum(c.number)} ${escapeHtml(c.name || '')}</b> (${fmtMHz(c.frequency)} MHz)? Its hits stay, as an unstored frequency.`, 'Delete', async () => {
        await api(`channels/${c.id}`, {method: 'DELETE'});
        chPage.selected = null;
        await loadChannels();
        renderChannelsPage();
        renderEditor();
    }, true);
}

// ---------------------------------------------------------------- new finds

async function loadFinds() {
    try {
        const data = await api('finds');
        chPage.finds = data.finds;
        chPage.ignored = data.ignored;
        chPage.minHits = data.min_hits;
    } catch (e) {
        toast(e.message, 'error');
        chPage.finds = [];
    }
}

async function renderFinds(search) {
    if (chPage.finds === null) await loadFinds();
    const rows = chPage.finds.filter(f => !search || fmtMHz(f.frequency).includes(search));
    document.getElementById('ch-count').textContent = `${rows.length} new finds · ${chPage.ignored.length} ignored`;
    const el = document.getElementById('ch-table');
    const body = rows.map(f => `
        <tr data-f="${f.frequency}">
            <td class="mono"><b>${fmtMHz(f.frequency)}</b></td><td class="mono">${modeLabel(f.mode)}</td>
            <td class="n">${num(f.hits)}</td><td class="mono">${num(f.peak, 0)} dB</td>
            <td class="mono muted">${escapeHtml(fmtDateTime(f.first))}</td><td class="mono muted">${escapeHtml(fmtDateTime(f.last))}</td>
            <td style="text-align:right">
                <button type="button" class="btn btn-small" data-act="listen">Listen</button>
                <button type="button" class="btn btn-small btn-primary" data-act="label">Label &amp; store</button>
                <button type="button" class="btn btn-small" data-act="ignore">Ignore</button>
            </td>
        </tr>`).join('');
    const ignored = chPage.ignored.length ? `<div class="panel-body"><div class="section-title">Ignored</div>
        <p class="panel-sub" style="margin-bottom:8px">Left out of band and range scans and of this list.</p>
        ${chPage.ignored.map(i => `<span class="compare-chip" style="display:inline-flex;align-items:center;gap:6px;margin:0 6px 6px 0;padding:2px 4px 2px 9px;border:var(--card-border);border-radius:13px;font:12px var(--mono)">${fmtMHz(i.frequency)}<button type="button" class="btn btn-small" data-unignore="${i.frequency}">Restore</button></span>`).join('')}</div>` : '';
    el.innerHTML = (rows.length
        ? `<table class="data"><thead><tr><th>Frequency</th><th>Mode</th><th class="n">Hits</th><th>Peak</th><th>First</th><th>Last</th><th></th></tr></thead><tbody>${body}</tbody></table>`
        : `<div class="empty-state">No new finds. Scanning a band or range lists every frequency with ${chPage.minHits || 3} or more signals that isn't stored yet.</div>`) + ignored;
    el.onclick = async (e) => {
        const un = e.target.closest('[data-unignore]');
        if (un) {
            await api('finds/unignore', {method: 'POST', json: {frequency: Number(un.dataset.unignore)}});
            await loadFinds();
            return renderFinds(search);
        }
        const button = e.target.closest('[data-act]');
        if (!button) return;
        const f = chPage.finds.find(x => x.frequency === Number(button.closest('[data-f]').dataset.f));
        if (button.dataset.act === 'listen') {
            radioCommand('tune', {frequency: f.frequency, mode: f.mode});
            toast(`Tuned to ${fmtMHz(f.frequency)} MHz`);
        } else if (button.dataset.act === 'label') {
            newChannel({frequency: f.frequency, mode: f.mode, from_find: true});
        } else {
            await api('finds/ignore', {method: 'POST', json: {frequency: f.frequency}});
            await loadFinds();
            renderFinds(search);
        }
    };
    if (!chPage.draft) renderEditor();
}

// ---------------------------------------------------------------- CSV

function openImport() {
    openModal('Import channels', `
        <p>A CSV with a header row. SDRadio's own export works, and so do lists from CHIRP and similar programs:
        the columns it knows are number (or location), name, frequency (MHz), mode, bandwidth and step (kHz), squelch, band, tags, notes, favourite, scan and priority.</p>
        <div class="form-field"><label for="imp-file">File</label><input id="imp-file" type="file" class="input" accept=".csv,text/csv,text/plain"></div>
        <label class="checks" style="margin:0"><span><input type="checkbox" id="imp-replace"> Replace all stored channels (a backup is a good idea first)</span></label>
        <p class="panel-sub">Without replace, a row with a number or frequency and mode that is already stored updates that channel; the rest are added.</p>`, [
        {label: 'Cancel'},
        {label: 'Import', cls: 'btn-primary', action: async () => {
            const file = document.getElementById('imp-file').files[0];
            if (!file) { modalError('Pick a file first'); return false; }
            const replace = document.getElementById('imp-replace').checked ? '1' : '0';
            const result = await api(`channels/import?replace=${replace}`, {method: 'POST', body: await file.arrayBuffer()});
            toast(`Imported: ${result.added} added, ${result.updated} updated`, 'success');
            await loadChannels();
            renderFilterSelects();
            renderChannelsPage();
        }},
    ]);
}

// ---------------------------------------------------------------- backups

async function renderBackups() {
    let data;
    try { data = await api('backups'); } catch (e) { return; }
    const settings = app.settings || {};
    document.getElementById('backup-note').textContent =
        `Nightly at ${settings.backup_time || '03:00'}, ${settings.backups_keep || 14} kept. Restoring backs up the current database first.`;
    const el = document.getElementById('backup-list');
    el.innerHTML = data.backups.length ? data.backups.slice(0, 20).map(b => `
        <div class="backup-row" data-name="${escapeHtml(b.name)}">
            <span class="name" title="${escapeHtml(b.name)}">${escapeHtml(fmtDateTime(b.ts))}${b.name.includes('-before-restore') ? ' · before restore' : b.name.includes('-uploaded') ? ' · uploaded' : ''}</span>
            <span class="size">${fmtBytes(b.size)}</span>
            <a class="btn btn-small" href="${basePath}api/backups/${encodeURIComponent(b.name)}" download>Download</a>
            <button type="button" class="btn btn-small" data-act="restore">Restore</button>
        </div>`).join('') : '<p class="panel-sub">No backups yet.</p>';
    el.onclick = (e) => {
        const button = e.target.closest('[data-act="restore"]');
        if (!button) return;
        const name = button.closest('[data-name]').dataset.name;
        confirmModal('Restore backup', `Replace all channels, hits, bands and settings with the backup of <b>${escapeHtml(name)}</b>? The current database is backed up first.`, 'Restore', async () => {
            await api(`backups/${encodeURIComponent(name)}/restore`, {method: 'POST'});
            toast('Restored', 'success');
            await Promise.all([loadChannels(), loadSettings(), loadStatus()]);
            renderChannelsPage();
            renderBackups();
        }, true);
    };
}

async function backupNow() {
    try {
        await api('backups', {method: 'POST'});
        toast('Backup made', 'success');
        renderBackups();
        loadStatus();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function uploadBackup(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    try {
        await api('backups/upload', {method: 'POST', body: await file.arrayBuffer()});
        toast('Backup added: restore it from the list', 'success');
        renderBackups();
    } catch (err) {
        toast(err.message, 'error');
    }
}

onTab('channels', async () => {
    await loadChannels();
    chPage.finds = null;
    renderFilterSelects();
    renderChannelsPage();
    renderBackups();
});
