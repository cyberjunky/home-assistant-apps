// Scanner - the Archive: every saved document, to find by name, tag or the text on its pages,
// to open, download, copy to the share, rename and delete.

const arch = {q: '', tag: '', sort: stored('sort', 'newest'), documents: [], total: 0, tags: [], summary: null, loaded: false, timer: null};
const SORTS = [{key: 'newest', label: 'Newest'}, {key: 'oldest', label: 'Oldest'}, {key: 'name', label: 'Name'}, {key: 'largest', label: 'Largest'}];

async function loadArchive(more) {
    const offset = more ? arch.documents.length : 0;
    const r = await api(`documents?q=${encodeURIComponent(arch.q)}&tag=${encodeURIComponent(arch.tag)}&sort=${arch.sort}&offset=${offset}`);
    arch.documents = more ? arch.documents.concat(r.documents) : r.documents;
    arch.total = r.total;
    arch.tags = r.tags;
    arch.summary = r.summary;
    arch.loaded = true;
}

// A hit in the text: the words around it, the hit marked
function snippetHtml(text) {
    return escapeHtml(text).replace(/\x02/g, '<mark>').replace(/\x03/g, '</mark>');
}

function docCard(d) {
    return `<a class="doc" href="#archive/${d.id}" data-doc="${d.id}">
        <div class="doc-thumb"><img src="api/documents/${d.id}/thumb" alt="" loading="lazy"><span class="doc-pages">${plural(d.pages, 'page')}</span></div>
        <div class="doc-body">
            <div class="doc-name" title="${attr(d.name)}">${escapeHtml(d.name)}</div>
            <div class="doc-meta">${escapeHtml(fmtDateTime(d.created))} · ${fmtBytes(d.size)}${d.copied_to ? ' · <span title="Copied to ' + attr(d.copied_to) + '">on the share</span>' : ''}${d.copy_error ? ' · <span class="warn-text" title="' + attr(d.copy_error) + '">not copied</span>' : ''}</div>
            ${d.snippet ? `<div class="doc-snippet">${snippetHtml(d.snippet)}</div>` : ''}
            ${d.tags.length ? `<div class="doc-tags">${tagsHtml(d.tags)}</div>` : ''}
        </div></a>`;
}

function renderArchive() {
    const el = document.getElementById('tab-archive');
    if (!arch.loaded) { el.innerHTML = '<div class="empty-state">Reading the archive…</div>'; return; }
    const s = arch.summary;
    if (!document.getElementById('ar-list')) {
        el.innerHTML = `
            <div class="filters">
                <input class="input search" id="ar-q" type="search" placeholder="Search names, tags and the text on the pages" value="${attr(arch.q)}" autocomplete="off">
                ${chipsHtml('ar-sort', SORTS, arch.sort, 'Sort')}
                <span class="range-text" id="ar-count"></span>
            </div>
            <div class="filters" id="ar-tags"></div>
            <div id="ar-list"></div>`;
        document.getElementById('ar-q').addEventListener('input', (e) => {
            arch.q = e.target.value;
            clearTimeout(arch.timer);
            arch.timer = setTimeout(() => loadArchive().then(renderArchive).catch(x => toast(x.message)), 250);
        });
        bindChips('ar-sort', (key) => { arch.sort = key; store('sort', key); loadArchive().then(renderArchive).catch(x => toast(x.message)); });
        document.getElementById('ar-tags').addEventListener('click', (e) => {
            const button = e.target.closest('[data-tag]');
            if (!button) return;
            arch.tag = arch.tag === button.dataset.tag ? '' : button.dataset.tag;
            loadArchive().then(renderArchive).catch(x => toast(x.message));
        });
        document.getElementById('ar-list').addEventListener('click', (e) => {
            if (e.target.closest('#ar-more')) loadArchive(true).then(renderArchive).catch(x => toast(x.message));
        });
    }
    document.getElementById('ar-count').textContent = arch.q || arch.tag
        ? `${num(arch.total)} of ${plural(s.documents, 'document')}`
        : `${plural(s.documents, 'document')} · ${plural(s.pages, 'page')} · ${fmtBytes(s.size)}`;
    document.getElementById('ar-tags').innerHTML = arch.tags.length
        ? `<span class="filter-label">Tags</span>${arch.tags.slice(0, 40).map(t => `<button type="button" class="tag tag-button${t.tag === arch.tag ? ' active' : ''}" data-tag="${attr(t.tag)}">${escapeHtml(t.tag)} <span class="chip-count">${num(t.count)}</span></button>`).join('')}` : '';
    const list = document.getElementById('ar-list');
    if (!arch.documents.length) {
        list.innerHTML = `<div class="empty-state">${s.documents ? 'No document matches' : 'No documents yet. Scan pages on the Scan page and save them: they come here.'}</div>`;
        return;
    }
    list.innerHTML = `<div class="docs">${arch.documents.map(docCard).join('')}</div>
        ${arch.documents.length < arch.total ? `<div class="pager"><button class="btn" id="ar-more">Show more (${num(arch.total - arch.documents.length)})</button></div>` : ''}`;
}

// One document: its pages to read, its facts, and what can be done with it
async function openDocument(id) {
    let d;
    try { d = await api(`documents/${id}`); } catch (e) { toast(e.message); setSub(''); return; }
    const share = app.status ? app.status.share : {};
    const root = document.getElementById('modal-root');
    const close = () => { root.innerHTML = ''; root.onclick = null; document.removeEventListener('keydown', onKey); if (activeTab === 'archive') setSub(''); };
    const onKey = (e) => { if (e.key === 'Escape' && !e.target.matches('input, textarea')) close(); };
    const draw = () => {
        root.innerHTML = `<div class="modal-overlay"><div class="modal doc-modal" role="dialog" aria-modal="true" aria-label="${attr(d.name)}">
            <div class="modal-head"><span class="one-line">${escapeHtml(d.name)}</span><button class="modal-close" data-close aria-label="Close">✕</button></div>
            <div class="modal-body doc-detail">
                <div class="doc-view"><iframe src="${docUrl(d.id)}#view=FitH" title="${attr(d.name)}"></iframe></div>
                <div class="doc-side">
                    <div class="form-field"><label for="doc-name">Name</label><input class="input" id="doc-name" maxlength="200" value="${attr(d.name)}" autocomplete="off"></div>
                    <div class="form-field"><label for="doc-tags">Tags</label><input class="input" id="doc-tags" value="${attr(d.tags.join(', '))}" placeholder="tax, 2026" autocomplete="off"></div>
                    <div class="detail-actions"><button class="btn" id="doc-save">Save name and tags</button></div>
                    <div class="kv">
                        <div class="k">Scanned</div><div class="v">${escapeHtml(fmtDateTime(d.created))}</div>
                        <div class="k">Pages</div><div class="v">${num(d.pages)}</div>
                        <div class="k">Size</div><div class="v">${fmtBytes(d.size)}</div>
                        <div class="k">Text</div><div class="v">${d.has_text ? `recognised${d.languages ? ` (${escapeHtml(d.languages.replace(/\+/g, ', '))})` : ''}${d.ocr ? '' : ', not on every page'}` : d.languages ? 'none found' : 'not recognised'}</div>
                        ${d.device ? `<div class="k">Scanner</div><div class="v">${escapeHtml(d.device)}</div>` : ''}
                        ${d.copied_to ? `<div class="k">Copied</div><div class="v">${escapeHtml(d.copied_to)}<br><span class="muted">${escapeHtml(fmtDateTime(d.copied_at))}</span></div>` : ''}
                        ${d.copy_error ? `<div class="k">Copy failed</div><div class="v warn-text">${escapeHtml(d.copy_error)}</div>` : ''}
                    </div>
                    <div class="detail-actions">
                        <a class="btn btn-primary" href="${docUrl(d.id, true)}" download>Download PDF</a>
                        <a class="btn" href="${docUrl(d.id)}" target="_blank" rel="noopener">Open in a tab</a>
                        <button class="btn" id="doc-copy"${share.folder ? '' : ' disabled'} title="${share.folder ? `Copy to ${attr(share.folder)}` : 'Choose a folder under Settings first'}">${d.copied_to ? 'Copy to the share again' : 'Copy to the share'}</button>
                    </div>
                    ${d.text ? `<details class="advanced"><summary>Text on the pages</summary><pre class="doc-text">${escapeHtml(d.text)}</pre></details>` : ''}
                    <div class="detail-actions doc-delete"><button class="btn btn-danger" id="doc-delete">Delete…</button></div>
                </div>
            </div></div></div>`;
        document.getElementById('doc-save').addEventListener('click', async () => {
            try {
                d = Object.assign(d, await apiPost(`documents/${d.id}`, {name: document.getElementById('doc-name').value, tags: document.getElementById('doc-tags').value}));
                toast('Saved', 'success');
                draw();
                loadArchive().then(renderArchive).catch(() => {});
            } catch (e) { toast(e.message); }
        });
        document.getElementById('doc-copy').addEventListener('click', async (e) => {
            e.target.disabled = true;
            e.target.textContent = 'Copying…';
            try {
                d = Object.assign(d, await apiPost(`documents/${d.id}/copy`, {}));
                toast(`Copied to ${d.copied_to}`, 'success');
            } catch (x) {
                toast(x.message);
                d.copy_error = x.message;
            }
            draw();
            loadArchive().then(renderArchive).catch(() => {});
        });
        document.getElementById('doc-delete').addEventListener('click', async () => {
            const name = d.name;
            close();
            const ok = await confirmDialog({title: 'Delete this document?', confirmLabel: 'Delete', danger: true,
                message: `<p><b>${escapeHtml(name)}</b> (${plural(d.pages, 'page')}) is removed from the archive. This can't be undone.</p>${d.copied_to ? '<p>The copy on the share stays.</p>' : ''}`});
            if (!ok) return;
            try {
                await apiPost(`documents/${d.id}/delete`, {});
                toast('Deleted', 'success');
                await loadArchive();
                renderArchive();
                statusSoon();
            } catch (e) { toast(e.message); }
        });
    };
    root.onclick = (e) => { if (e.target.classList.contains('modal-overlay') || e.target.closest('[data-close]')) close(); };
    document.addEventListener('keydown', onKey);
    draw();
}

onTab('archive', (sub) => {
    renderArchive();
    loadArchive().then(() => { if (activeTab === 'archive') renderArchive(); }).catch(e => toast(e.message));
    if (sub && /^\d+$/.test(sub)) openDocument(Number(sub));
}, () => {});
