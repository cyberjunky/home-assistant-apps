// Scanner - Settings: how documents are made (name, quality, text recognition), the folder or
// network share they are copied to, and the saved profiles.

async function saveSettings(changes, quiet) {
    try {
        takeSettings(await apiPost('settings', changes));
        if (!quiet) toast('Saved', 'success');
        statusSoon();
        return true;
    } catch (e) { toast(e.message); return false; }
}

function renderSettings() {
    const el = document.getElementById('tab-settings');
    const s = app.settings, m = app.meta;
    if (!s || !m) { el.innerHTML = '<div class="empty-state">…</div>'; return; }
    const share = app.status ? app.status.share : {};
    const state = !s.share_folder ? '<span class="muted">No folder chosen: documents stay in the archive only.</span>'
        : share.folder !== s.share_folder ? '<span class="muted">Checking…</span>'
        : share.ok ? `<span class="sdot ok"></span> ${escapeHtml(SHARE_KINDS[share.kind] ? `${SHARE_KINDS[share.kind]}, connected by Home Assistant` : 'A folder on this machine')} · can be written to`
        : `<span class="sdot bad"></span> <span class="warn-text">${escapeHtml(share.problem || 'Not reachable')}</span>`;
    el.innerHTML = `
        <div class="dash-grid">
            ${panel('Documents', '', `
                <div class="form-field"><label for="set-name">Name of a new document</label>
                    <input class="input" id="set-name" maxlength="200" value="${attr(s.name_pattern)}" autocomplete="off">
                    <div class="form-hint">Used when you give no name. <span class="mono">{date}</span> <span class="mono">{time}</span> <span class="mono">{year}</span> <span class="mono">{month}</span> <span class="mono">{day}</span> <span class="mono">{n}</span> (pages) are filled in.</div></div>
                <div class="form-field"><label for="set-quality">Picture quality <span class="mono" id="set-quality-value">${num(s.jpeg_quality)}</span></label>
                    <input type="range" id="set-quality" class="opt-range wide" min="30" max="100" step="1" value="${s.jpeg_quality}">
                    <div class="form-hint">JPEG quality of grey and colour pages: higher is sharper and larger. 85 is fine for paper; black-and-white pages are always stored exactly.</div></div>
                <div class="form-field"><label>Text recognition</label>
                    ${m.ocr_available ? `<label class="check-row"><input type="checkbox" id="set-ocr"${s.ocr ? ' checked' : ''}> Recognise the text of new documents</label>
                    <div class="lang-list">${Object.entries(m.languages).map(([code, name]) => `<label class="check-row"><input type="checkbox" data-lang="${code}"${s.ocr_languages.includes(code) ? ' checked' : ''}> ${escapeHtml(name)}</label>`).join('')}</div>
                    <div class="form-hint">The PDF gets an invisible text layer, so it can be searched here and in any PDF reader. Choose the languages your paper is in: fewer is faster and more exact. A page takes a few seconds to half a minute.</div>`
                    : '<div class="form-hint">Text recognition (Tesseract) is not in this image.</div>'}</div>`)}
            ${panel('Copy to a folder or network share', '', `
                <div class="form-field"><label>Folder</label>
                    <div class="opt-control"><input class="input grow mono" id="set-folder" value="${attr(s.share_folder)}" placeholder="none" readonly>
                        <button class="btn" id="set-browse">Choose…</button>${s.share_folder ? '<button class="btn" id="set-nofolder">None</button>' : ''}</div>
                    <div class="share-state">${state}</div></div>
                <div class="form-field"><label class="check-row plain"><input type="checkbox" id="set-auto"${s.share_auto ? ' checked' : ''}${s.share_folder ? '' : ' disabled'}> Copy every saved document there</label>
                    <div class="form-hint">Off: copy a document from the Archive when you want to. The archive always keeps its own copy.</div></div>
                <div class="form-field"><label for="set-pattern">File name</label>
                    <div class="opt-control"><input class="input grow mono" id="set-pattern" maxlength="200" value="${attr(s.share_pattern)}" autocomplete="off"><span class="unit">.pdf</span></div>
                    <div class="form-hint"><span class="mono">{name}</span> <span class="mono">{date}</span> <span class="mono">{time}</span> <span class="mono">{year}</span> <span class="mono">{month}</span> <span class="mono">{day}</span> <span class="mono">{id}</span>; a <span class="mono">/</span> makes folders, such as <span class="mono">{year}/{date} {name}</span>. A file that is already there is never replaced.</div></div>
                <div class="note">
                    <b>A share on a NAS or PC (SMB, NFS):</b> Home Assistant connects to it, the app needs no password. In Home Assistant go to
                    <i>Settings → System → Storage → Add network storage</i>, give it a name, choose usage <i>Share</i> (or <i>Media</i>) and fill in the server, the share and the login.
                    It then shows here as a folder in <span class="mono">/share</span> (or <span class="mono">/media</span>): press <i>Choose…</i>.
                </div>`)}
        </div>
        ${panel('Profiles', 'saved on the Scan page with “Save as profile…”', s.profiles.length ? table(
            [{label: 'Name'}, {label: 'Scanner'}, {label: 'Settings'}, {label: 'Batch'}, {label: ''}],
            s.profiles.map(p => `<tr>${td(`<span class="name">${escapeHtml(p.name)}</span>`)}${td(`<span class="mono">${escapeHtml(p.device || 'any')}</span>`)}
                ${td(Object.entries(p.values).map(([k, v]) => `<span class="tag">${escapeHtml(k)} ${escapeHtml(v)}</span>`).join(' ') || '<span class="muted">the scanner\'s own</span>', 'wrap-cell')}
                ${td(escapeHtml({single: 'One page', feeder: 'Feeder', step: 'Step by step', timed: `Timed, every ${p.batch.interval} s`}[p.batch.mode] || ''))}
                ${td(`<button class="btn btn-small btn-danger" data-profile="${attr(p.name)}">Delete</button>`, 'tight')}</tr>`))
            : '<div class="empty-state">No profiles yet</div>')}`;
    bindSettings(el);
}

function bindSettings(el) {
    const on = (id, event, fn) => { const x = document.getElementById(id); if (x) x.addEventListener(event, fn); };
    on('set-name', 'change', e => saveSettings({name_pattern: e.target.value}));
    on('set-quality', 'input', e => { document.getElementById('set-quality-value').textContent = e.target.value; });
    on('set-quality', 'change', e => saveSettings({jpeg_quality: Number(e.target.value)}));
    on('set-ocr', 'change', e => { tr.ocr = e.target.checked; saveSettings({ocr: e.target.checked}); });
    el.querySelectorAll('[data-lang]').forEach(box => box.addEventListener('change', async () => {
        const chosen = [...el.querySelectorAll('[data-lang]')].filter(b => b.checked).map(b => b.dataset.lang);
        if (!chosen.length) { box.checked = true; toast('At least one language'); return; }
        await saveSettings({ocr_languages: chosen});
    }));
    on('set-auto', 'change', e => saveSettings({share_auto: e.target.checked}));
    on('set-pattern', 'change', async e => { if (await saveSettings({share_pattern: e.target.value})) renderSettings(); });
    on('set-browse', 'click', async () => {
        const folder = await chooseFolder(app.settings.share_folder);
        if (folder === null) return;
        if (await saveSettings({share_folder: folder})) { await loadStatus().catch(() => {}); renderSettings(); }
    });
    on('set-nofolder', 'click', async () => {
        if (await saveSettings({share_folder: '', share_auto: false})) { await loadStatus().catch(() => {}); renderSettings(); }
    });
    el.querySelectorAll('[data-profile]').forEach(button => button.addEventListener('click', async () => {
        try {
            takeSettings(await apiPost('profiles/delete', {name: button.dataset.profile}));
            renderSettings();
        } catch (e) { toast(e.message); }
    }));
}

// Choose a folder in Home Assistant's share or media folder. Resolves with its path, or null.
function chooseFolder(start) {
    return new Promise((resolve) => {
        const root = document.getElementById('modal-root');
        let here = null;
        const finish = (answer) => { root.innerHTML = ''; root.onclick = null; document.removeEventListener('keydown', onKey); resolve(answer); };
        const onKey = (e) => { if (e.key === 'Escape') finish(null); };
        const kind = k => SHARE_KINDS[k] ? `<span class="badge ok">${SHARE_KINDS[k]}</span>` : '';
        const draw = (error) => {
            root.innerHTML = `<div class="modal-overlay"><div class="modal" role="dialog" aria-modal="true" aria-label="Choose a folder">
                <div class="modal-head"><span>Choose a folder</span><button class="modal-close" data-close aria-label="Close">✕</button></div>
                <div class="modal-body">
                    <div class="folder-path mono">${here && here.path ? escapeHtml(here.path) : 'Home Assistant\'s folders'} ${here ? kind(here.kind) : ''}</div>
                    ${error ? `<div class="form-error">${escapeHtml(error)}</div>` : ''}
                    <div class="folder-list">
                        ${here && here.parent !== null ? `<button class="folder" data-path="${attr(here.parent)}">↑ <span class="muted">up</span></button>` : ''}
                        ${here ? here.folders.map(f => `<button class="folder" data-path="${attr(f.path)}"><span class="folder-icon">▸</span> ${escapeHtml(f.name)} ${kind(f.kind)}</button>`).join('') : '<div class="empty-state">…</div>'}
                        ${here && !here.folders.length ? '<div class="empty-state">No folders in here</div>' : ''}
                    </div>
                    ${here && here.path ? `<div class="opt-control new-folder"><input class="input grow" id="folder-new" placeholder="New folder" maxlength="80" autocomplete="off"><button class="btn" data-make>Make</button></div>` : ''}
                    ${here && here.path && !here.writable ? '<div class="form-hint warn-text">The app can\'t write in this folder.</div>' : ''}
                </div>
                <div class="modal-foot"><button class="btn" data-close>Cancel</button>
                    <button class="btn btn-primary" data-choose${here && here.path && here.writable ? '' : ' disabled'}>Use this folder</button></div>
            </div></div>`;
        };
        const go = async (path) => {
            try {
                here = await api(`folders?path=${encodeURIComponent(path)}`);
                draw();
            } catch (e) {
                if (here === null) { here = await api('folders?path=').catch(() => ({path: '', parent: null, folders: []})); }
                draw(e.message);
            }
        };
        root.onclick = async (e) => {
            if (e.target.classList.contains('modal-overlay') || e.target.closest('[data-close]')) return finish(null);
            if (e.target.closest('[data-choose]')) return finish(here.path);
            const folder = e.target.closest('[data-path]');
            if (folder) return go(folder.dataset.path);
            if (e.target.closest('[data-make]')) {
                const name = document.getElementById('folder-new').value.trim();
                if (!name) return;
                try {
                    const made = await apiPost('folders', {path: here.path, name});
                    await go(made.path);
                } catch (x) { draw(x.message); }
            }
        };
        document.addEventListener('keydown', onKey);
        draw();
        go(start || '');
    });
}

onTab('settings', () => {
    renderSettings();
    loadSettings().then(() => { if (activeTab === 'settings') renderSettings(); }).catch(e => toast(e.message));
}, () => {});
