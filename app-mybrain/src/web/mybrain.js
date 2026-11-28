// MyBrain - items: cards, the detail sheet, adding, and changing status.

const SOURCE_COLORS = {
    reddit: '#ff4500', x: '#6b7785', youtube: '#ff0033', github: '#8957e5', hackernews: '#ff6600',
    instagram: '#d62976', facebook: '#1877f2', linkedin: '#0a66c2', tiktok: '#25f4ee', wikipedia: '#8a96a3',
    medium: '#3e8e41', substack: '#ff6719', stackoverflow: '#f48024', shop: '#f5a524', forum: '#18bcf2',
    docs: '#18bcf2', spotify: '#1db954', maps: '#34a853', keep: '#fbbc04', email: '#ea4335',
    whatsapp: '#25d366', telegram: '#29a9eb', web: '#4c8dff', note: '#2bb3a3',
};
const PURPOSES = {
    read: ['📖', 'To read'], watch: ['▶', 'To watch'], listen: ['🎧', 'To listen'], buy: ['🛒', 'To buy'],
    cook: ['🍳', 'To cook'], try: ['🧪', 'To try'], visit: ['📍', 'To visit'], reference: ['📚', 'Reference'],
    idea: ['💡', 'Idea'], todo: ['☑', 'To do'],
};
const STATUS_LABELS = {inbox: 'Inbox', later: 'Later', done: 'Done', archived: 'Archived'};
const PRIORITY_LABELS = ['Low', 'Normal', 'High'];
const EVENT_LABELS = {
    saved: ['Saved', ''], 'shared again': ['Again', 'warn'], opened: ['Opened', ''], sorted: ['Sorted', ''],
    edited: ['Edited', ''], done: ['Done', 'ok'], archived: ['Archived', 'ok'], later: ['Later', ''],
    inbox: ['To inbox', 'warn'], deleted: ['Deleted', 'bad'],
};

const sourceColor = s => SOURCE_COLORS[s] || SOURCE_COLORS.web;
const sourceName = s => (app.status && app.status.source_names[s]) || (s ? s[0].toUpperCase() + s.slice(1) : 'Web');
const purposeLabel = p => (PURPOSES[p] || ['•', p])[1];
const purposeIcon = p => (PURPOSES[p] || ['•'])[0];

// Items seen on the page, by id, so a card's buttons can act without asking the server first
const itemCache = new Map();

function thumbHtml(item, cls) {
    if (item.image) return `<img class="${cls || ''}" src="thumbs/${encodeURIComponent(item.image)}" alt="" loading="lazy">`;
    const letter = item.kind === 'note' ? '✎' : item.source !== 'web' ? sourceName(item.source)[0] : (item.site || '?')[0].toUpperCase();
    return `<div class="ph ${cls || ''}" style="--src:${sourceColor(item.source)}"><span>${escapeHtml(letter)}</span></div>`;
}

function itemTitle(item) {
    return item.title || item.site || (item.url || '').replace(/^https?:\/\//, '').slice(0, 80) || 'Untitled';
}

function itemText(item) {
    return item.summary || item.note || item.description || '';
}

function metaLine(item) {
    const parts = [`<span class="src-dot" style="background:${sourceColor(item.source)}"></span>${escapeHtml(sourceName(item.source))}`];
    if (item.detail) parts.push(escapeHtml(item.detail));
    else if (item.site && item.source === 'web') parts.push(escapeHtml(item.site));
    parts.push(`<span title="${escapeHtml(fmtDateTime(item.created_at))}">${fmtAge(item.created_at)}</span>`);
    return parts.join('<span class="sep">·</span>');
}

function fmtAge(ts) {
    const s = Date.now() / 1000 - ts;
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 86400 * 45) return `${Math.round(s / 86400)} d ago`;
    if (s < 86400 * 365) return `${Math.round(s / 86400 / 30)} mo ago`;
    return fmtDate(ts);
}

// Dates: 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM', local time
function parseDate(value) {
    if (!value) return null;
    const [day, time] = value.split('T');
    const [y, m, d] = day.split('-').map(Number);
    const [hh, mm] = (time || '0:0').split(':').map(Number);
    return {at: new Date(y, m - 1, d, hh, mm), timed: Boolean(time)};
}
function daysFromToday(value) {
    const p = parseDate(value);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const day = new Date(p.at); day.setHours(0, 0, 0, 0);
    return Math.round((day - today) / 86400000);
}
function fmtWhen(value) {
    const p = parseDate(value);
    if (!p) return '';
    const days = daysFromToday(value);
    const time = p.timed ? ' ' + p.at.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';
    if (days === 0) return 'today' + time;
    if (days === 1) return 'tomorrow' + time;
    if (days === -1) return 'yesterday' + time;
    const sameYear = p.at.getFullYear() === new Date().getFullYear();
    return p.at.toLocaleDateString([], {weekday: Math.abs(days) < 7 ? 'short' : undefined, day: 'numeric', month: 'short', year: sameYear ? undefined : 'numeric'}) + time;
}
// A badge per date: when it is due (red once past, orange in the next two days) and when it happens
function dateBadges(item) {
    const out = [];
    const open = item.status === 'inbox' || item.status === 'later';
    if (item.due) {
        const days = daysFromToday(item.due);
        const kind = !open ? '' : days < 0 ? 'bad' : days <= 2 ? 'warn' : '';
        out.push(`<span class="badge date ${kind}" title="Due ${escapeHtml(item.due.replace('T', ' '))}">⏰ ${days < 0 && open ? 'overdue · ' : 'due '}${escapeHtml(fmtWhen(item.due))}</span>`);
    }
    if (item.happens_on) {
        const days = daysFromToday(item.happens_on);
        out.push(`<span class="badge date ${open && days >= 0 && days <= 2 ? 'warn' : ''}" title="On ${escapeHtml(item.happens_on.replace('T', ' '))}">📅 ${escapeHtml(fmtWhen(item.happens_on))}</span>`);
    }
    return out.join('');
}

function tagChips(item, max) {
    const tags = (item.tags || []).slice(0, max || 3);
    return tags.map(t => `<button type="button" class="tagchip" data-tag="${escapeHtml(t)}">#${escapeHtml(t)}</button>`).join('');
}

function card(item, opts) {
    opts = opts || {};
    itemCache.set(item.id, item);
    const state = item.fetch_state === 'waiting' || item.fetch_state === 'again'
        ? '<span class="badge reading">reading…</span>'
        : item.fetch_state === 'dead' ? '<span class="badge bad" title="The site says the page is gone">gone</span>'
        : item.fetch_state === 'failed' ? `<span class="badge warn" title="${escapeHtml(item.fetch_error)}">not read</span>` : '';
    const prio = item.priority === 2 ? '<span class="prio high" title="High priority">★</span>' : '';
    const again = item.shares > 1 ? `<span class="badge warn" title="Saved ${item.shares} times">${item.shares}×</span>` : '';
    const status = opts.showStatus && item.status !== 'inbox' ? `<span class="badge st-${item.status}">${STATUS_LABELS[item.status]}</span>` : '';
    const actions = item.status === 'done' || item.status === 'archived'
        ? `<button type="button" class="act" data-act="inbox" title="Back to the inbox (i)">↩ Inbox</button>`
        : `<button type="button" class="act" data-act="done" title="Done with it (d)">✓ Done</button>`
          + (item.status === 'later' ? `<button type="button" class="act" data-act="inbox" title="Back to the inbox (i)">↩ Inbox</button>`
                                     : `<button type="button" class="act" data-act="later" title="Keep for later (l)">⏱ Later</button>`)
          + `<button type="button" class="act" data-act="archived" title="Not needed (a)">🗄 Archive</button>`;
    return `<article class="card${item.status === 'done' || item.status === 'archived' ? ' handled' : ''}" data-id="${item.id}" tabindex="0">
        <div class="card-media">${thumbHtml(item)}${prio}</div>
        <div class="card-body">
            <div class="card-meta">${metaLine(item)}</div>
            <h3 class="card-title">${escapeHtml(itemTitle(item))}</h3>
            <p class="card-text">${escapeHtml(itemText(item))}</p>
            <div class="card-tags">
                ${dateBadges(item)}<span class="badge cat">${escapeHtml(item.category)}</span>
                <span class="purpose" title="${escapeHtml(purposeLabel(item.purpose))}">${purposeIcon(item.purpose)} ${escapeHtml(purposeLabel(item.purpose))}</span>
                ${status}${state}${again}${tagChips(item)}
            </div>
        </div>
        <div class="card-actions">${actions}<button type="button" class="act star${item.priority === 2 ? ' on' : ''}" data-act="star" title="High priority (h)">★</button></div>
    </article>`;
}

function rowItem(item, opts) {
    itemCache.set(item.id, item);
    return `<div class="row-item" data-id="${item.id}" tabindex="0">
        <div class="ri-thumb">${thumbHtml(item)}</div>
        <div class="ri-main"><div class="ri-title">${item.priority === 2 ? '<span class="prio-inline">★</span>' : ''}${escapeHtml(itemTitle(item))}</div>
        <div class="card-meta">${metaLine(item)}<span class="sep">·</span>${escapeHtml(item.category)}</div>${opts && opts.dates ? `<div class="ri-dates">${dateBadges(item)}</div>` : ''}</div>
    </div>`;
}

// One click handler for every list of cards: open, or one of the buttons
function bindItems(el, after) {
    if (el._bound) return;
    el._bound = true;
    el.addEventListener('click', async (e) => {
        const tag = e.target.closest('[data-tag]');
        if (tag) { e.stopPropagation(); showTag(tag.dataset.tag); return; }
        const holder = e.target.closest('[data-id]');
        if (!holder) return;
        const id = Number(holder.dataset.id);
        const act = e.target.closest('[data-act]');
        if (act) {
            e.stopPropagation();
            await quickAct(id, act.dataset.act, holder);
            if (after) after();
            return;
        }
        openItem(id);
    });
    el.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.matches('[data-id]')) openItem(Number(e.target.dataset.id));
    });
}

async function quickAct(id, act, holder) {
    const item = itemCache.get(id) || {};
    const change = act === 'star' ? {priority: item.priority === 2 ? 1 : 2} : {status: act};
    try {
        const updated = await apiPost('item', {id, ...change});
        itemCache.set(id, updated);
        if (holder && change.status) holder.classList.add('leaving');
        toast(change.status ? `${STATUS_LABELS[change.status]}: ${itemTitle(updated).slice(0, 60)}` : (change.priority === 2 ? 'High priority' : 'Normal priority'), 'success');
        await refreshStatus();
        return updated;
    } catch (e) {
        toast(e.message);
        return null;
    }
}

// =====================================================================
// The detail sheet
// =====================================================================
let sheetItem = null;

async function openItem(id) {
    let item;
    try {
        item = await api(`item?id=${id}`);
    } catch (e) {
        toast(e.message);
        return;
    }
    sheetItem = item;
    itemCache.set(id, item);
    renderSheet();
}

function segmented(name, options, current) {
    return `<div class="tabs seg" data-seg="${name}">${options.map(([key, label]) =>
        `<button type="button" class="tab${String(key) === String(current) ? ' active' : ''}" data-key="${key}">${label}</button>`).join('')}</div>`;
}

function renderSheet() {
    const item = sheetItem;
    if (!item) return;
    const s = app.status;
    const link = item.url ? `<a class="open-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer" data-open>${escapeHtml(item.url.replace(/^https?:\/\//, '').slice(0, 90))} ↗</a>` : '';
    const extra = item.extra || {};
    const facts = [
        ['Source', `<span class="src-dot" style="background:${sourceColor(item.source)}"></span>${escapeHtml(sourceName(item.source))}${item.detail ? ' · ' + escapeHtml(item.detail) : ''}`],
        item.author ? ['By', escapeHtml(item.author)] : null,
        item.site ? ['Site', escapeHtml(extra.site_name || item.site)] : null,
        ['Saved', `${fmtDateTime(item.created_at)} <span class="muted">(${fmtAge(item.created_at)}, ${item.via === 'share' ? 'shared' + (extra.device ? ' from ' + escapeHtml(extra.device) : '') : item.via === 'add' ? 'added here' : escapeHtml(item.via)})</span>`],
        item.shares > 1 ? ['Saved', `${item.shares} times`] : null,
        extra.linked_url ? ['Links to', `<a href="${escapeHtml(extra.linked_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(extra.linked_url.slice(0, 70))} ↗</a>`] : null,
        ['Sorted', item.sorted_by === 'ai' ? 'by the AI' : item.sorted_by === 'you' ? 'by you' : item.sorted_by === 'rules' ? 'by the rules' : 'not yet'],
        item.fetch_state === 'failed' || item.fetch_state === 'dead' ? ['Page', `<span class="sev-text medium">${escapeHtml(item.fetch_error)}</span>`] : null,
    ].filter(Boolean);
    const history = (item.history || []).map(ev => {
        const [label, kind] = EVENT_LABELS[ev.what] || [ev.what, ''];
        return `<div class="event"><span class="t">${fmtDay(ev.at)}</span><span class="k ${kind}">${escapeHtml(label)}</span><span class="x">${escapeHtml(ev.detail || '')}</span></div>`;
    }).join('');

    document.getElementById('sheet-root').innerHTML = `
    <div class="sheet-overlay" data-close>
        <aside class="sheet item-sheet" role="dialog" aria-modal="true" aria-label="${escapeHtml(itemTitle(item))}">
            <div class="sheet-head">
                <h2>${escapeHtml(itemTitle(item))}</h2>
                <button class="modal-close" data-close aria-label="Close">✕</button>
            </div>
            <div class="sheet-body">
                ${item.image ? `<a class="sheet-media" href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer" data-open>${thumbHtml(item)}</a>` : ''}
                ${link}
                ${item.summary ? `<p class="summary">${escapeHtml(item.summary)}</p>` : ''}
                <div class="form-grid">
                    <label>Status</label>${segmented('status', s.statuses.map(k => [k, STATUS_LABELS[k]]), item.status)}
                    <label>Priority</label>${segmented('priority', PRIORITY_LABELS.map((l, i) => [i, i === 2 ? '★ ' + l : l]), item.priority)}
                    <label>Category</label><select class="select" data-field="category">${s.categories.map(c => `<option${c === item.category ? ' selected' : ''}>${escapeHtml(c)}</option>`).join('')}</select>
                    <label>For</label><select class="select" data-field="purpose">${s.purposes.map(p => `<option value="${p}"${p === item.purpose ? ' selected' : ''}>${purposeIcon(p)} ${escapeHtml(purposeLabel(p))}</option>`).join('')}</select>
                    <label>Tags</label><input class="input" data-field="tags" value="${escapeHtml((item.tags || []).join(', '))}" placeholder="comma, separated" spellcheck="false">
                    <label title="By when it needs doing">Due</label>${dateField('due', item.due)}
                    <label title="When it happens: an event, a trip, a release">On</label>${dateField('happens_on', item.happens_on)}
                </div>
                <h3>Your note</h3>
                <textarea class="input note-edit" data-field="note" rows="3" placeholder="Why did you save this?">${escapeHtml(item.note || '')}</textarea>
                ${item.description && item.description !== item.summary ? `<h3>From the page</h3><p class="desc">${escapeHtml(item.description)}</p>` : ''}
                <h3>Details</h3>
                <div class="facts">${facts.map(([l, v]) => infoRow(l, v)).join('')}</div>
                ${item.text ? `<details class="saved-text"><summary>Saved text of the page <span class="muted">(${num(item.text.length)} characters, searchable)</span></summary><div class="page-text">${escapeHtml(item.text).replace(/\n\n/g, '<br><br>')}</div></details>` : ''}
                <div class="detail-actions">
                    ${item.kind === 'link' ? '<button class="btn btn-small" data-again="refetch" title="Read the page again: title, picture and text">⟳ Read again</button>' : ''}
                    <button class="btn btn-small" data-again="resort" title="Sort it again${s.ai.entity ? ' with ' + escapeHtml(s.ai.entity) : ' by the rules'}">✦ Sort again</button>
                    <span class="spacer"></span>
                    <button class="btn btn-small btn-danger" data-delete>Delete</button>
                </div>
                ${history ? `<h3>History</h3><div class="events">${history}</div>` : ''}
            </div>
        </aside>
    </div>`;
    bindSheet();
}

function dateField(name, value) {
    const [day, time] = (value || '').split('T');
    return `<div class="date-field" data-date="${name}">
        <input class="input" type="date" value="${escapeHtml(day || '')}" aria-label="${name === 'due' ? 'Due date' : 'On date'}">
        <input class="input" type="time" value="${escapeHtml(time || '')}" aria-label="Time (optional)" title="Time (optional)">
        ${value ? `<button type="button" class="btn btn-small" data-clear title="No date">✕</button><span class="muted">${escapeHtml(fmtWhen(value))}</span>` : ''}
    </div>`;
}

function bindSheet() {
    const root = document.getElementById('sheet-root');
    const overlay = root.querySelector('.sheet-overlay');
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest('.modal-close')) closeSheet();
    });
    root.querySelectorAll('[data-open]').forEach(a => a.addEventListener('click', () => apiPost('item/open', {id: sheetItem.id}).catch(() => {})));
    root.querySelectorAll('[data-seg]').forEach(seg => seg.addEventListener('click', (e) => {
        const b = e.target.closest('[data-key]');
        if (!b) return;
        const field = seg.dataset.seg;
        saveField({[field]: field === 'priority' ? Number(b.dataset.key) : b.dataset.key});
    }));
    root.querySelectorAll('select[data-field]').forEach(sel => sel.addEventListener('change', () => saveField({[sel.dataset.field]: sel.value})));
    root.querySelectorAll('input[data-field], textarea[data-field]').forEach(input => {
        input.addEventListener('change', () => saveField({[input.dataset.field]: input.value}, true));
    });
    root.querySelectorAll('[data-date]').forEach(field => {
        const [day, time] = field.querySelectorAll('input');
        const save = () => {
            if (!day.value) return;
            saveField({[field.dataset.date]: day.value + (time.value ? 'T' + time.value : '')});
        };
        day.addEventListener('change', save);
        time.addEventListener('change', save);
        const clear = field.querySelector('[data-clear]');
        if (clear) clear.addEventListener('click', () => saveField({[field.dataset.date]: ''}));
    });
    root.querySelectorAll('[data-again]').forEach(b => b.addEventListener('click', async () => {
        try {
            await apiPost('item/again', {id: sheetItem.id, what: b.dataset.again});
            toast(b.dataset.again === 'refetch' ? 'Reading the page again…' : 'Sorting it again…', 'success');
            setTimeout(() => sheetItem && openItem(sheetItem.id), 4000);
        } catch (e) { toast(e.message); }
    }));
    root.querySelector('[data-delete]').addEventListener('click', async () => {
        const ok = await confirmDialog({title: 'Delete this item?', message: `<p>${escapeHtml(itemTitle(sheetItem))}</p><p class="muted">It is gone for good. Archive keeps it out of the way but findable.</p>`, confirmLabel: 'Delete', danger: true});
        if (!ok) return;
        try {
            await apiPost('item/delete', {id: sheetItem.id});
            itemCache.delete(sheetItem.id);
            closeSheet();
            toast('Deleted', 'success');
            await refreshStatus();
            renderActive();
        } catch (e) { toast(e.message); }
    });
}

async function saveField(change, quiet) {
    try {
        sheetItem = await apiPost('item', {id: sheetItem.id, ...change});
        itemCache.set(sheetItem.id, sheetItem);
        if (!quiet) renderSheet();
        if (change.status) toast(`${STATUS_LABELS[change.status]}`, 'success');
        await refreshStatus();
        listsStale = true;
    } catch (e) {
        toast(e.message);
    }
}

let listsStale = false;
function closeSheet() {
    document.getElementById('sheet-root').innerHTML = '';
    sheetItem = null;
    if (listsStale) { listsStale = false; renderActive(); }
}

// Keys: n adds, / searches, Esc closes; in the sheet d/l/a/i set the status and h the priority
document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.getElementById('modal-root').innerHTML) return;
    if (e.key === 'Escape' && sheetItem) { closeSheet(); return; }
    if (sheetItem) {
        const status = {d: 'done', l: 'later', a: 'archived', i: 'inbox'}[e.key];
        if (status) { saveField({status}); e.preventDefault(); }
        else if (e.key === 'h') { saveField({priority: sheetItem.priority === 2 ? 1 : 2}); e.preventDefault(); }
        else if (e.key === 'o' && sheetItem.url) { window.open(sheetItem.url, '_blank', 'noopener'); apiPost('item/open', {id: sheetItem.id}).catch(() => {}); }
        return;
    }
    if (e.key === 'n') { e.preventDefault(); openAdd(); }
    if (e.key === '/') { e.preventDefault(); switchTab('library'); setTimeout(() => { const q = document.getElementById('lib-q'); if (q) q.focus(); }, 0); }
});

// =====================================================================
// Adding by hand
// =====================================================================
function openAdd(prefill) {
    const root = document.getElementById('modal-root');
    root.innerHTML = `
        <div class="modal-overlay">
            <div class="modal add-modal" role="dialog" aria-modal="true" aria-label="Save to MyBrain">
                <div class="modal-head"><span>Save to MyBrain</span><button class="modal-close" data-close aria-label="Close">✕</button></div>
                <div class="modal-body">
                    <textarea class="input" id="add-text" rows="5" placeholder="Paste a link, several links, or type a note…">${escapeHtml(prefill || '')}</textarea>
                    <div class="muted add-hint">Every link becomes an item of its own; the text around it becomes its note. From your phone: share to the Home Assistant app.</div>
                </div>
                <div class="modal-foot"><button class="btn" data-close>Cancel</button><button class="btn btn-primary" id="add-save">Save</button></div>
            </div>
        </div>`;
    const text = root.querySelector('#add-text');
    const close = () => { root.innerHTML = ''; };
    root.querySelector('.modal-overlay').addEventListener('click', (e) => {
        if (e.target.classList.contains('modal-overlay') || e.target.hasAttribute('data-close')) close();
    });
    const save = async () => {
        if (!text.value.trim()) return;
        try {
            const result = await apiPost('add', {text: text.value});
            close();
            toast(result.ids.length > 1 ? `Saved ${result.ids.length} items` : 'Saved', 'success');
            await refreshStatus();
            renderActive();
        } catch (e) { toast(e.message); }
    };
    root.querySelector('#add-save').addEventListener('click', save);
    text.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') close();
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) save();
    });
    text.focus();
}

function showTag(tag) {
    closeSheet();
    library.tag = tag;
    library.q = '';
    switchTab('library');
}
