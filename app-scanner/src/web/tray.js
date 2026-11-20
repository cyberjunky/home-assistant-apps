// Scanner - the pages scanned so far (on the Scan page): look at them, turn, reorder and remove
// them, and save them as one document.

const tr = {name: '', tags: '', ocr: null, download: false, dragId: null};

async function loadTray() {
    app.tray = await api('tray');
    return app.tray;
}

function renderTray() {
    const el = document.getElementById('sc-tray');
    const t = app.tray;
    if (!el || !t) return;
    if (!t.pages.length) {
        el.innerHTML = panel('Pages', '', '<div class="empty-state">Scanned pages come here. Scan as many as you like, then save them as one document.</div>');
        return;
    }
    if (!document.getElementById('tray-pages')) {
        el.innerHTML = `<div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Pages <span class="muted" id="tray-count"></span></span>
                <span class="hint tray-tools">
                    <button class="btn btn-small" data-tool="turn" title="Turn every page a quarter turn">Turn all ↻</button>
                    <button class="btn btn-small" data-tool="reverse" title="Last page first">Reverse</button>
                    <button class="btn btn-small" data-tool="interleave" title="Two-sided paper on a one-sided feeder: scan all fronts, turn the pile over, scan all backs, then press this">Fronts + backs</button>
                    <button class="btn btn-small btn-danger" data-tool="clear">Remove all</button>
                </span></div>
            <div class="tray" id="tray-pages"></div>
            <div class="tray-save" id="tray-save"></div>
        </div></div>`;
        el.querySelector('.tray-tools').addEventListener('click', trayTool);
        bindTrayPages(document.getElementById('tray-pages'));
        renderTraySave(true);
    }
    document.getElementById('tray-count').textContent = `${num(t.pages.length)} · ${fmtBytes(t.size)}`;
    document.getElementById('tray-pages').innerHTML = t.pages.map((p, i) => `
        <div class="page" data-id="${attr(p.id)}" draggable="true">
            <button class="page-thumb" data-act="view" title="Page ${i + 1}: ${num(p.width)} × ${num(p.height)} px, ${num(p.dpi)} dpi, ${escapeHtml(p.mode)}, ${fmtBytes(p.size)}">
                <img src="api/tray/${attr(p.id)}/thumb?t=${p.turned || 0}" alt="Page ${i + 1}" loading="lazy" draggable="false"></button>
            <div class="page-bar"><span class="page-n">${i + 1}</span>
                <button data-act="left" title="Earlier"${i === 0 ? ' disabled' : ''}>◀</button>
                <button data-act="right" title="Later"${i === t.pages.length - 1 ? ' disabled' : ''}>▶</button>
                <button data-act="ccw" title="Turn left">↺</button>
                <button data-act="cw" title="Turn right">↻</button>
                <button data-act="delete" title="Remove this page" class="del">✕</button></div>
        </div>`).join('');
    const name = document.getElementById('tray-name');
    if (name) name.placeholder = t.suggested_name;
    // The save buttons are off while the scanner works: a form built during a batch must get them back
    renderTraySave();
}

// The save form is built once, so typing a name isn't undone when a page comes in
function renderTraySave(build) {
    const el = document.getElementById('tray-save');
    if (!el) return;
    const job = app.status ? app.status.job : {};
    const saving = job.kind === 'save' && job.state === 'running';
    if (build || !document.getElementById('tray-name')) {
        const ocrOk = app.meta && app.meta.ocr_available;
        if (tr.ocr === null) tr.ocr = Boolean(app.settings && app.settings.ocr);
        el.innerHTML = `
            <div class="form-field grow2"><label for="tray-name">Name</label><input class="input" id="tray-name" maxlength="200" autocomplete="off" value="${attr(tr.name)}" placeholder="${attr(app.tray.suggested_name)}"></div>
            <div class="form-field grow1"><label for="tray-tags">Tags</label><input class="input" id="tray-tags" autocomplete="off" value="${attr(tr.tags)}" placeholder="tax, 2026" list="tray-tag-list"><datalist id="tray-tag-list"></datalist></div>
            <label class="check-row" title="${ocrOk ? 'Read the text on the pages, so the document can be searched and its text copied' : 'Text recognition is not in this image'}"><input type="checkbox" id="tray-ocr"${tr.ocr && ocrOk ? ' checked' : ''}${ocrOk ? '' : ' disabled'}> Recognise text</label>
            <div class="tray-buttons"><button class="btn btn-primary" id="tray-save-button">Save to archive</button>
                <button class="btn" id="tray-save-download" title="Save to the archive and download the PDF">Save and download</button></div>
            <div class="tray-progress" id="tray-progress"></div>`;
        document.getElementById('tray-name').addEventListener('input', e => { tr.name = e.target.value; });
        document.getElementById('tray-tags').addEventListener('input', e => { tr.tags = e.target.value; });
        document.getElementById('tray-ocr').addEventListener('change', e => { tr.ocr = e.target.checked; });
        document.getElementById('tray-save-button').addEventListener('click', () => saveTray(false));
        document.getElementById('tray-save-download').addEventListener('click', () => saveTray(true));
        document.getElementById('tray-name').addEventListener('keydown', e => { if (e.key === 'Enter') saveTray(false); });
    }
    const busy = jobRunning();
    document.getElementById('tray-save-button').disabled = busy;
    document.getElementById('tray-save-download').disabled = busy;
    document.getElementById('tray-progress').innerHTML = saving
        ? `<div class="job"><div class="job-line"><span>${escapeHtml(job.message || 'Saving')}${job.pages ? ` · page ${num(job.page)} of ${num(job.pages)}` : ''}</span></div>
            <div class="meter big"><div class="meter-bar"><i style="width:${(job.percent || 0).toFixed(1)}%"></i></div></div></div>`
        : job.kind === 'save' && job.state === 'failed' ? `<div class="job failed"><span class="sdot bad"></span> ${escapeHtml(job.error)}</div>` : '';
}

async function trayChange(path, body) {
    try {
        app.tray = await apiPost(path, body);
        renderTray();
    } catch (e) { toast(e.message); }
}

async function trayTool(e) {
    const button = e.target.closest('[data-tool]');
    if (!button) return;
    const tool = button.dataset.tool;
    const ids = app.tray.pages.map(p => p.id);
    if (tool === 'turn') {
        button.disabled = true;
        await trayChange('tray/rotate', {ids, degrees: 90});
        button.disabled = false;
    } else if (tool === 'reverse') {
        await trayChange('tray/arrange', {how: 'reverse'});
    } else if (tool === 'interleave') {
        await trayChange('tray/arrange', {how: 'interleave'});
    } else if (tool === 'clear') {
        const ok = await confirmDialog({title: 'Remove all pages?', confirmLabel: 'Remove', danger: true,
            message: `${plural(ids.length, 'page')} scanned but not saved will be gone.`});
        if (ok) await trayChange('tray/delete', {all: true});
    }
}

function bindTrayPages(grid) {
    grid.addEventListener('click', (e) => {
        const button = e.target.closest('[data-act]');
        const card = e.target.closest('.page');
        if (!button || !card) return;
        const id = card.dataset.id;
        const ids = app.tray.pages.map(p => p.id);
        const at = ids.indexOf(id);
        const act = button.dataset.act;
        if (act === 'view') return viewPage(at);
        if (act === 'delete') return trayChange('tray/delete', {id});
        if (act === 'cw' || act === 'ccw') return trayChange('tray/rotate', {id, degrees: act === 'cw' ? 90 : 270});
        const to = act === 'left' ? at - 1 : at + 1;
        if (to < 0 || to >= ids.length) return;
        ids.splice(to, 0, ids.splice(at, 1)[0]);
        return trayChange('tray/order', {ids});
    });
    // Drag a page onto another to put it there
    grid.addEventListener('dragstart', (e) => {
        const card = e.target.closest('.page');
        if (!card) return;
        tr.dragId = card.dataset.id;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', tr.dragId);
        card.classList.add('dragging');
    });
    grid.addEventListener('dragend', () => {
        tr.dragId = null;
        grid.querySelectorAll('.page').forEach(c => c.classList.remove('dragging', 'drop'));
    });
    grid.addEventListener('dragover', (e) => {
        const card = e.target.closest('.page');
        if (!tr.dragId || !card) return;
        e.preventDefault();
        grid.querySelectorAll('.page').forEach(c => c.classList.toggle('drop', c === card && c.dataset.id !== tr.dragId));
    });
    grid.addEventListener('drop', (e) => {
        const card = e.target.closest('.page');
        if (!tr.dragId || !card) return;
        e.preventDefault();
        const ids = app.tray.pages.map(p => p.id);
        const from = ids.indexOf(tr.dragId), to = ids.indexOf(card.dataset.id);
        if (from < 0 || to < 0 || from === to) return;
        ids.splice(to, 0, ids.splice(from, 1)[0]);
        trayChange('tray/order', {ids});
    });
}

// A page at full size, with the arrow keys to the next and the one before
function viewPage(at) {
    const pages = app.tray.pages;
    const root = document.getElementById('modal-root');
    const show = () => {
        const p = pages[at];
        root.innerHTML = `<div class="modal-overlay viewer"><div class="viewer-head"><span>Page ${at + 1} of ${pages.length} · ${num(p.width)} × ${num(p.height)} px · ${num(p.dpi)} dpi · ${escapeHtml(p.mode)}</span>
            <span><button class="btn" data-go="-1"${at === 0 ? ' disabled' : ''}>◀</button> <button class="btn" data-go="1"${at === pages.length - 1 ? ' disabled' : ''}>▶</button> <button class="btn" data-close>Close</button></span></div>
            <div class="viewer-body"><img src="api/tray/${attr(p.id)}/image?t=${p.turned || 0}" alt="Page ${at + 1}"></div></div>`;
    };
    const close = () => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); };
    const go = (d) => { if (at + d >= 0 && at + d < pages.length) { at += d; show(); } };
    const onKey = (e) => {
        if (e.key === 'Escape') close();
        if (e.key === 'ArrowLeft') go(-1);
        if (e.key === 'ArrowRight') go(1);
    };
    root.onclick = (e) => {
        if (e.target.closest('[data-go]')) go(Number(e.target.closest('[data-go]').dataset.go));
        else if (e.target.closest('[data-close]') || e.target.classList.contains('viewer-body')) { close(); root.onclick = null; }
    };
    document.addEventListener('keydown', onKey);
    show();
}

async function saveTray(andDownload) {
    if (jobRunning()) return;
    tr.download = andDownload;
    try {
        await apiPost('tray/save', {name: tr.name.trim(), tags: tr.tags.split(',').map(t => t.trim()).filter(Boolean), ocr: Boolean(tr.ocr), device: sc.device});
        statusSoon();
    } catch (e) { toast(e.message); }
}

onJob((job) => {
    if (job.kind !== 'save') return;
    if (job.state === 'running') { renderTraySave(); if (activeTab === 'scan') renderScanForm(); return; }
    if (job.state === 'done' && job.doc) {
        tr.name = '';
        tr.tags = '';
        toast(job.warning || `Saved ${job.doc.name}${job.doc.copied_to && !job.doc.copy_error ? ' and copied it to the share' : ''}`, job.warning ? 'error' : 'success');
        if (tr.download) download(docUrl(job.doc.id, true));
        tr.download = false;
        arch.loaded = false;
    }
    loadTray().then(() => {
        const el = document.getElementById('sc-tray');
        if (el && job.state === 'done') el.innerHTML = '';
        renderTray();
        renderTraySave();
    }).catch(() => {});
    if (activeTab === 'scan') renderScanForm();
    if (activeTab === 'archive') loadArchive().then(renderArchive).catch(() => {});
});
