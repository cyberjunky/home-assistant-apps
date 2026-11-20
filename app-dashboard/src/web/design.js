// Dashboard - design mode: the drawer with your entities, dragging them onto pages, moving and sizing.
'use strict';

const UNDO = [];
let drawerTab = 'entities', entQuery = '', entDomain = '', stageScale = 1;

function snapshot() {
    UNDO.push(JSON.stringify(S.doc));
    if (UNDO.length > 60) UNDO.shift();
}
function changed() {
    renderAll();
    scheduleSave();
}
let saveTimer, saving = false, saveAgain = false;
function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 400);
}
async function save() {
    if (saving) { saveAgain = true; return; }
    saving = true;
    try {
        S.doc.rev = (await api('doc', {rev: S.doc.rev, doc: S.doc})).rev;
    } catch (err) {
        if (err.status === 409) {
            S.doc = err.data.doc;
            UNDO.length = 0;
            S.sel = null;
            renderAll();
            toast('The dashboard was changed somewhere else - this is that version');
        } else toast('Not saved: ' + err.message);
    }
    saving = false;
    if (saveAgain) { saveAgain = false; save(); }
}

function setDesign(on) {
    S.design = on;
    S.sel = null;
    document.body.classList.toggle('design', on);
    $('#drawer').hidden = !on;
    renderAll();
    fitStage();
}
// The whole screen, scaled down to fit next to the drawer
function fitStage() {
    const stage = $('#stage');
    if (!S.design) { stage.style.transform = ''; stageScale = 1; return; }
    const wrap = $('#stagewrap').getBoundingClientRect();
    stageScale = Math.min((wrap.width - 40) / window.innerWidth, (wrap.height - 40) / window.innerHeight);
    stage.style.transform = `translate(${(wrap.width - window.innerWidth * stageScale) / 2}px, ${(wrap.height - window.innerHeight * stageScale) / 2}px) scale(${stageScale})`;
}
window.addEventListener('resize', fitStage);

function select(id) {
    S.sel = id;
    $$('.w.sel').forEach(el => el.classList.remove('sel'));
    if (id) $(`.w[data-id="${id}"]`)?.classList.add('sel');
    renderDrawer();
}

// ---------- The drawer ----------
function renderDrawer() {
    if (!S.design) return;
    const w = S.sel && findWidget(S.sel);
    const head = `<div class="d-head"><span class="lbl">Design</span><button class="d-btn" data-undo ${UNDO.length ? '' : 'disabled'} title="Undo (Ctrl+Z)">${ic('undo')}</button><button class="d-btn primary" data-done>Done</button></div>`;
    if (w) {
        $('#drawer').innerHTML = head + `<div class="d-tabs"><button class="lbl" data-back>‹ Entities</button><button class="lbl on">${esc(BLOCKS[w.type]?.name || w.type)}</button></div><div class="d-body">${inspector(w)}</div>`;
        return;
    }
    const tabs = [['entities', 'Entities'], ['blocks', 'Blocks'], ['page', 'Pages'], ['style', 'Style'], ['tablet', 'Tablet']];
    $('#drawer').innerHTML = head + `<div class="d-tabs">${tabs.map(([id, name]) => `<button class="lbl${drawerTab === id ? ' on' : ''}" data-tab="${id}">${name}</button>`).join('')}</div>
        <div class="d-body">${drawerTab === 'entities' ? entitiesTab() : drawerTab === 'blocks' ? blocksTab() : drawerTab === 'style' ? styleTab() : drawerTab === 'tablet' ? tabletTab() : pageTab()}</div>`;
    if (drawerTab === 'entities') renderEntityList();
}
function entitiesTab() {
    const domains = [...new Set(Object.keys(S.states).map(dom))].sort();
    return `<p class="d-hint">Drag an entity onto the page. On an empty spot it becomes its own block; on a block that takes more, it joins it. On the top bar it becomes a reading there.</p>
        <div class="d-filter"><input class="d-search" type="search" placeholder="Search ${Object.keys(S.states).length} entities" value="${esc(entQuery)}" data-search>
        <select data-domain><option value="">All</option>${domains.map(d => `<option${d === entDomain ? ' selected' : ''}>${esc(d)}</option>`).join('')}</select></div><div id="d-list"></div>`;
}
function renderEntityList() {
    const q = entQuery.trim().toLowerCase();
    const found = Object.keys(S.states).filter(e => !S.hidden.has(e) && (!entDomain || dom(e) === entDomain)
        && (!q || e.includes(q) || ename(e).toLowerCase().includes(q) || (S.areas[e] || '').toLowerCase().includes(q)));
    const groups = {};
    for (const e of found.slice(0, 300)) (groups[S.areas[e] || ''] ||= []).push(e);
    const names = Object.keys(groups).sort((a, b) => !a - !b || a.localeCompare(b));
    $('#d-list').innerHTML = (names.map(area => `<div class="lbl d-area">${esc(area || 'No area')}</div>` + groups[area]
        .sort((a, b) => ename(a).localeCompare(ename(b)))
        .map(e => `<div class="d-ent" data-drag-entity="${esc(e)}"><div><b class="cut">${esc(ename(e))}</b><small class="cut">${esc(e)}</small></div><span>${esc(stateText(e))}</span></div>`).join('')).join('')
        || '<p class="d-hint">Nothing found.</p>') + (found.length > 300 ? `<p class="d-hint">…and ${found.length - 300} more. Search to narrow it down.</p>` : '');
}
function blocksTab() {
    return `<p class="d-hint">Drag a block onto the page, then drag entities onto it. An entity dropped on an empty spot picks its own block; you can change it afterwards.</p>`
        + Object.entries(BLOCKS).map(([type, b]) => `<div class="d-ent" data-drag-block="${type}"><div><b>${b.name}</b><small>${b.hint}</small></div></div>`).join('');
}
function pageTab() {
    const page = currentPage(), i = S.doc.pages.indexOf(page);
    return `<h4 class="lbl">This page</h4>
        <label class="d-field"><span class="lbl">Name</span><input type="text" value="${esc(page.name)}" data-pname maxlength="40"></label>
        <div class="d-acts" style="margin-top:0"><button class="d-btn" data-page-move="-1" ${i ? '' : 'disabled'}>‹ Earlier</button><button class="d-btn" data-page-move="1" ${i < S.doc.pages.length - 1 ? '' : 'disabled'}>Later ›</button>
        <button class="d-btn danger" data-page-del ${S.doc.pages.length > 1 ? '' : 'disabled'}>${ic('trash')} Delete page</button></div>
        <div class="d-acts"><button class="d-btn" data-add-page>${ic('plus')} New page</button><button class="d-btn" data-page-dup>${ic('copy')} Copy this page</button></div>
        <h4 class="lbl">Top bar</h4>
        ${S.doc.chips.map((c, n) => `<div class="d-ent row-only"><div><input class="d-search" type="text" value="${esc(c.label || '')}" placeholder="${esc(ename(c.entity))}" data-chip-label="${n}"><small class="cut">${esc(c.entity)}</small></div><button class="x" data-chip-rm="${n}">${ic('x')}</button></div>`).join('') || ''}
        <p class="d-hint">Drag an entity onto the top bar to show it on every page.</p>
        <h4 class="lbl">Grid</h4>
        <div class="d-row2"><label class="d-field"><span class="lbl">Columns</span><input type="number" min="6" max="48" value="${S.doc.cols}" data-grid="cols"></label>
        <label class="d-field"><span class="lbl">Rows</span><input type="number" min="4" max="48" value="${S.doc.rows}" data-grid="rows"></label></div>
        <p class="d-hint">Every page fills the screen; more columns and rows place things more precisely.</p>`;
}
function styleTab() {
    const style = {...STYLE, ...S.doc.style};
    const fonts = key => `<select data-style="${key}">${Object.entries(FONTS).map(([name, text]) => `<option value="${name}"${style[key] === name ? ' selected' : ''}>${text}</option>`).join('')}</select>`;
    const color = (key, text) => `<div class="d-color"><input type="color" data-style="${key}" value="${esc(style[key])}"><span>${text}</span>${S.doc.style?.[key] ? `<button data-style-reset="${key}">reset</button>` : ''}</div>`;
    return `<p class="d-hint">The look of every page. The fonts are inside the app, so the tablet needs no internet for them.</p>
        <h4 class="lbl">Fonts</h4>
        <label class="d-field"><span class="lbl">Text and numbers</span>${fonts('text')}</label>
        <label class="d-field"><span class="lbl">Labels</span>${fonts('label')}</label>
        <label class="d-field"><span class="lbl">Large numbers</span><select data-style="weight">${[['200', 'Thin'], ['300', 'Light'], ['400', 'Regular'], ['600', 'Bold']].map(([v, name]) => `<option value="${v}"${style.weight === v ? ' selected' : ''}>${name}</option>`).join('')}</select></label>
        <h4 class="lbl">Colours</h4>
        ${color('paper', 'Background by day')}${color('night', 'Background in the dark theme')}${color('tint', 'Accent: what is on')}
        <div class="d-acts"><button class="d-btn" data-style-reset="all">Back to the standard look</button></div>`;
}
// What the tablet does by itself: the parts of the day, the night, going home, the doorbell
function tabletTab() {
    const t = S.doc.tablet || {}, starts = {...PHASE_START, ...t.phases}, night = t.night || {}, idle = t.idle || {}, bell = t.doorbell || {};
    const pages = (key, value, none) => `<select data-tablet="${key}"><option value="">${none}</option>${S.doc.pages.map(p => `<option value="${esc(p.id)}"${p.id === value ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select>`;
    const entities = (key, value, domains, none) => `<select data-tablet="${key}"><option value="">${none}</option>${Object.keys(S.states).filter(e => domains.includes(dom(e))).sort((a, b) => ename(a).localeCompare(ename(b)))
        .map(e => `<option value="${esc(e)}"${e === value ? ' selected' : ''}>${esc(ename(e))} (${esc(e)})</option>`).join('')}</select>`;
    const pick = (key, value, options) => `<select data-tablet="${key}">${options.map(([v, name]) => `<option value="${v}"${String(value ?? '') === String(v) ? ' selected' : ''}>${name}</option>`).join('')}</select>`;
    return `<h4 class="lbl">Parts of the day</h4>
        <p class="d-hint">A block can be set to show only in some parts of the day (select it, then <i>Show during</i>). Each part starts at:</p>
        <div class="d-row2">${PHASES.map(name => `<label class="d-field"><span class="lbl">${name}</span><input type="text" data-tablet="phases.${name}" value="${esc(starts[name])}" maxlength="5" placeholder="${PHASE_START[name]}"></label>`).join('')}</div>
        <label class="d-field"><span class="lbl">Look at the pages as in the</span><select data-preview><option value="">Part of the day it is now (${phase()})</option>${PHASES.map(name => `<option${S.previewPhase === name ? ' selected' : ''}>${name}</option>`).join('')}</select></label>
        <h4 class="lbl">Night</h4>
        <label class="d-field"><span class="lbl">Dark theme</span>${pick('night.dark', night.dark, [['', 'As the tablet or Home Assistant says'], ['night', 'During the night'], ['sun', 'When the sun is down']])}</label>
        <label class="d-field"><span class="lbl">Dim the screen at night</span>${pick('night.dim', night.dim || 0, [[0, 'No'], [30, 'A little'], [50, 'Half'], [70, 'A lot'], [85, 'Almost dark']])}</label>
        <label class="d-field"><span class="lbl">Page at night</span>${pages('night.page', night.page, 'Stay on the page it is on')}</label>
        <p class="d-hint">A touch wakes the tablet for ${AWAKE_MINUTES} minutes: full brightness, and every page.</p>
        <h4 class="lbl">Going back</h4>
        <label class="d-field"><span class="lbl">When nobody touched it for</span>${pick('idle.minutes', idle.minutes || 0, [[0, 'Never go back'], [1, '1 minute'], [2, '2 minutes'], [5, '5 minutes'], [15, '15 minutes'], [60, 'An hour']])}</label>
        <label class="d-field"><span class="lbl">Go to</span>${pages('idle.page', idle.page, 'The first page')}</label>
        <h4 class="lbl">Doorbell</h4>
        <label class="d-field"><span class="lbl">When this goes on</span>${entities('doorbell.trigger', bell.trigger, ['binary_sensor', 'event', 'input_boolean', 'input_button'], 'No doorbell')}</label>
        <label class="d-field"><span class="lbl">Show this camera</span>${entities('doorbell.camera', bell.camera, ['camera'], 'Choose a camera')}</label>
        <label class="d-field"><span class="lbl">For</span>${pick('doorbell.seconds', bell.seconds || 60, [[20, '20 seconds'], [60, 'A minute'], [180, '3 minutes']])}</label>
        <h4 class="lbl">Keeping it</h4>
        <div class="d-acts" style="margin-top:0"><button class="d-btn" data-export>Save as a file</button><button class="d-btn" data-import>Load a file</button><input class="d-file" type="file" accept="application/json,.json" data-import-file></div>
        <p class="d-hint">The file holds the pages, the look and these settings. Add <b>?kiosk</b> to the address on the tablet to leave the pencil out.</p>`;
}
function exportDoc() {
    const {rev, ...doc} = S.doc;
    const link = Object.assign(document.createElement('a'), {href: URL.createObjectURL(new Blob([JSON.stringify(doc, null, 1)], {type: 'application/json'})), download: 'dashboard.json'});
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
}
async function importDoc(file) {
    let doc;
    try {
        doc = JSON.parse(await file.text());
        if (!Array.isArray(doc.pages) || !doc.pages.length) throw new Error('no pages');
    } catch (err) {
        return toast('That file is not a dashboard');
    }
    snapshot();
    S.doc = {cols: 24, rows: 14, chips: [], style: {}, tablet: {}, ...doc, rev: S.doc.rev};
    S.page = S.doc.pages[0].id;
    S.sel = null;
    return changed();
}
function inspector(w) {
    const block = BLOCKS[w.type] || BLOCKS.list;
    const types = w.entities.length ? Object.keys(BLOCKS).filter(t => w.entities.every(e => BLOCKS[t].takes(e))) : [w.type];
    const auto = block.label ? block.label(w) : block.many ? block.name : '';
    const check = (key, text, on) => `<label class="d-check"><input type="checkbox" data-opt="${key}" ${on ? 'checked' : ''}> ${text}</label>`;
    let extra = '';
    if (w.type === 'chart') extra = `<label class="d-field"><span class="lbl">Shape</span><select data-opt="kind"><option value="">Line</option><option value="bars"${w.opts.kind === 'bars' ? ' selected' : ''}>A bar per day</option></select></label>
        <label class="d-field"><span class="lbl">Period</span><select data-opt="hours" data-number>${RANGES.map(([h, name]) => `<option value="${h}"${(w.opts.hours || (w.opts.kind === 'bars' ? 168 : 24)) === h ? ' selected' : ''}>${name}</option>`).join('')}</select></label>`;
    if (w.type === 'heading') extra = `<label class="d-field"><span class="lbl">Title</span><input type="text" data-opt="text" value="${esc(w.opts.text || '')}"></label>
        <label class="d-field"><span class="lbl">Text below</span><input type="text" data-opt="sub" value="${esc(w.opts.sub || '')}"></label>`;
    if (w.type === 'clock') extra = check('h12', '12-hour clock', w.opts.h12);
    const choose = (key, text, value, options, number) => `<label class="d-field"><span class="lbl">${text}</span><select data-opt="${key}"${number ? ' data-number' : ''}>${options.map(([v, name]) => `<option value="${v}"${String(value ?? '') === String(v) ? ' selected' : ''}>${name}</option>`).join('')}</select></label>`;
    if (w.type === 'weather') extra = choose('forecast', 'Below it', w.opts.forecast, [['', 'Nothing'], ['daily', 'The days ahead'], ['hourly', 'The hours ahead']]);
    if (w.type === 'agenda') extra = choose('days', 'Looks ahead', w.opts.days || 2, [[1, 'Today only'], [2, 'Today and tomorrow'], [7, 'A week'], [14, 'Two weeks']], true);
    if (w.type === 'camera') extra = choose('every', 'New picture every', w.opts.every || 10, [[2, '2 seconds'], [5, '5 seconds'], [10, '10 seconds'], [30, '30 seconds'], [60, 'A minute']], true);
    const color = (key, text) => `<div class="d-color"><input type="color" data-opt="${key}" value="${esc(/^#[0-9a-f]{6}$/i.test(w.opts[key] || '') ? w.opts[key] : key === 'bg' ? '#ffffff' : '#e0603f')}"><span>${text}</span>${w.opts[key] ? `<button data-opt-reset="${key}">none</button>` : ''}</div>`;
    const look = `<h4 class="lbl">Colours</h4>${color('color', 'Its own colour: dots, bars, lines')}${w.opts.color ? check('colornum', 'The large number in that colour too', w.opts.colornum) : ''}${color('bg', 'A panel behind it')}
        <h4 class="lbl">Show during</h4><div class="d-phases">${PHASES.map(name => `<label class="d-check"><input type="checkbox" data-when="${name}" ${!w.opts.when?.length || w.opts.when.includes(name) ? 'checked' : ''}> The ${name}</label>`).join('')}</div>`;
    if (w.type === 'covers') extra = check('compact', 'Two large buttons for all of them', w.opts.compact);
    const entities = w.entities.length || block.takes !== BLOCKS.clock.takes ? `<h4 class="lbl">Entities</h4>
        ${w.entities.map((e, n) => `<div class="d-ent row-only"><div><input class="d-search" type="text" value="${esc(w.opts.names?.[e] || '')}" placeholder="${esc(attr(e, 'friendly_name') || e)}" data-ename="${esc(e)}">
            ${w.type === 'actions' ? `<input class="d-search" style="margin-top:4px" type="text" value="${esc(w.opts.subs?.[e] || '')}" placeholder="Text below (${esc(S.areas[e] || cap(dom(e)))})" data-esub="${esc(e)}">` : ''}
            <small class="cut">${esc(e)}</small></div>${n ? `<button class="x" data-eup="${n}" title="Earlier">${ic('up')}</button>` : ''}<button class="x" data-erm="${n}" title="Take off">${ic('x')}</button></div>`).join('')}
        <p class="d-hint">${block.many ? 'Drag more entities onto this block to add them. The field renames one on this dashboard only.' : w.entities.length ? 'The field renames it on this dashboard only.' : 'Drag an entity onto this block.'}</p>` : '';
    return `<label class="d-field"><span class="lbl">Shown as</span><select data-type>${types.map(t => `<option value="${t}"${t === w.type ? ' selected' : ''}>${BLOCKS[t].name} - ${BLOCKS[t].hint}</option>`).join('')}</select></label>
        <label class="d-field"><span class="lbl">Label</span><input type="text" data-label value="${esc(w.label)}" placeholder="${esc(auto)}" maxlength="80"></label>
        ${check('nohead', 'Hide the label line', w.opts.nohead)}${check('rule', 'Line above', w.opts.rule ?? w.y > 0)}${extra}${entities}
        ${look}<h4 class="lbl">Place</h4>
        <div class="d-row2">${['x', 'y', 'w', 'h'].map(k => `<label class="d-field"><span class="lbl">${{x: 'Col', y: 'Row', w: 'Wide', h: 'High'}[k]}</span><input type="number" data-pos="${k}" value="${w[k] + (k === 'x' || k === 'y' ? 1 : 0)}" min="1"></label>`).join('')}</div>
        <p class="d-hint">Drag the block to move it, and the round handle at its corner to size it. Arrow keys nudge it.</p>
        <div class="d-acts"><button class="d-btn" data-dup>${ic('copy')} Duplicate</button><button class="d-btn danger" data-del>${ic('trash')} Delete</button></div>`;
}

// One of the tablet's settings, by its path ("night.dim")
function setTablet(path, value) {
    const [group, key] = path.split('.');
    const t = S.doc.tablet ||= {};
    (t[group] ||= {})[key] = ['dim', 'minutes', 'seconds'].includes(key) ? Number(value) : value;
    if (group === 'phases' && !/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) delete t.phases[key];
    applyNight();
}
function fitWidget(w) {
    w.w = clamp(w.w, 1, S.doc.cols); w.h = clamp(w.h, 1, S.doc.rows);
    w.x = clamp(w.x, 0, S.doc.cols - w.w); w.y = clamp(w.y, 0, S.doc.rows - w.h);
}
function removeSelected() {
    const page = currentPage();
    snapshot();
    page.widgets = page.widgets.filter(w => w.id !== S.sel);
    S.sel = null;
    changed();
}
function undo() {
    if (!UNDO.length) return;
    const rev = S.doc.rev;
    S.doc = {...JSON.parse(UNDO.pop()), rev};
    if (!findWidget(S.sel)) S.sel = null;
    changed();
}
function addPage() {
    snapshot();
    const page = {id: newId(), name: `Page ${S.doc.pages.length + 1}`, widgets: []};
    S.doc.pages.push(page);
    S.sel = null;
    drawerTab = 'page';
    showPage(page.id);
    scheduleSave();
    $('[data-pname]')?.select();
}

// A change that starts in a field counts as one step for undo
let fieldSnap = false;
$('#drawer').addEventListener('focusin', () => { fieldSnap = false; });
function fieldEdit() {
    if (!fieldSnap) { snapshot(); fieldSnap = true; }
}
$('#drawer').addEventListener('input', ev => {
    const t = ev.target, d = t.dataset, w = S.sel && findWidget(S.sel);
    if ('search' in d) { entQuery = t.value; renderEntityList(); return; }
    if ('domain' in d) { entDomain = t.value; renderEntityList(); return; }
    if (t.matches('select, input[type=checkbox], input[type=number]')) return;
    fieldEdit();
    if (d.style) { (S.doc.style ||= {})[d.style] = t.value; applyStyle(); scheduleSave(); return; }
    if (d.tablet) setTablet(d.tablet, t.value);
    else if ('pname' in d) { currentPage().name = t.value || 'Page'; renderNav(); }
    else if ('chipLabel' in d) { S.doc.chips[d.chipLabel].label = t.value; renderNav(); }
    else if (w) {
        if ('label' in d) w.label = t.value;
        else if (d.opt && t.type === 'color') { w.opts[d.opt] = t.value; renderPage(); scheduleSave(); return; }
        else if (d.opt) w.opts[d.opt] = t.value;
        else if (d.ename) (w.opts.names ||= {})[d.ename] = t.value;
        else if (d.esub) (w.opts.subs ||= {})[d.esub] = t.value;
        drawWidget(w.id);
    }
    scheduleSave();
});
$('#drawer').addEventListener('change', ev => {
    const t = ev.target, d = t.dataset, w = S.sel && findWidget(S.sel);
    if ('importFile' in d) { if (t.files[0]) importDoc(t.files[0]); return; }
    if ('preview' in d) { S.previewPhase = t.value; applyNight(); renderAll(); return; }
    if (!t.matches('select, input[type=checkbox], input[type=number]') || 'domain' in d) return;
    snapshot();
    if (d.style) (S.doc.style ||= {})[d.style] = t.value;
    else if (d.tablet) setTablet(d.tablet, t.value);
    else if (w && d.when) {
        const on = new Set(w.opts.when?.length ? w.opts.when : PHASES);
        if (t.checked) on.add(d.when); else on.delete(d.when);
        w.opts.when = on.size === PHASES.length || !on.size ? [] : PHASES.filter(name => on.has(name));
    } else if (d.grid) {
        S.doc[d.grid] = clamp(parseInt(t.value, 10) || 24, d.grid === 'cols' ? 6 : 4, 48);
        S.doc.pages.forEach(p => p.widgets.forEach(fitWidget));
    } else if (w) {
        if ('type' in d) w.type = t.value;
        else if (d.opt) w.opts[d.opt] = t.type === 'checkbox' ? t.checked : 'number' in d ? Number(t.value) : t.value;
        else if (d.pos) { w[d.pos] = (parseInt(t.value, 10) || 1) - (d.pos === 'x' || d.pos === 'y' ? 1 : 0); fitWidget(w); }
    }
    changed();
});
$('#drawer').addEventListener('click', ev => {
    const t = ev.target.closest('button');
    if (!t) return;
    const d = t.dataset, w = S.sel && findWidget(S.sel), page = currentPage();
    if ('done' in d) return setDesign(false);
    if ('undo' in d) return undo();
    if ('back' in d) return select(null);
    if (d.tab) { drawerTab = d.tab; return renderDrawer(); }
    if ('addPage' in d) return addPage();
    if ('del' in d) return removeSelected();
    if ('export' in d) return exportDoc();
    if ('import' in d) return $('[data-import-file]').click();
    snapshot();
    if (d.pageMove) {
        const i = S.doc.pages.indexOf(page), j = i + Number(d.pageMove);
        [S.doc.pages[i], S.doc.pages[j]] = [S.doc.pages[j], S.doc.pages[i]];
    } else if ('pageDel' in d) {
        if (page.widgets.length && !confirm(`Delete the page "${page.name}" and everything on it?`)) return UNDO.pop();
        S.doc.pages = S.doc.pages.filter(p => p !== page);
        S.page = S.doc.pages[0].id;
    } else if ('pageDup' in d) {
        const copy = {...JSON.parse(JSON.stringify(page)), id: newId(), name: page.name + ' 2'};
        copy.widgets.forEach(x => { x.id = newId(); });
        S.doc.pages.splice(S.doc.pages.indexOf(page) + 1, 0, copy);
        S.page = copy.id;
    } else if (w && d.optReset) delete w.opts[d.optReset];
    else if (d.chipRm) S.doc.chips.splice(Number(d.chipRm), 1);
    else if (d.styleReset) { if (d.styleReset === 'all') S.doc.style = {}; else delete S.doc.style[d.styleReset]; }
    else if (w && 'dup' in d) {
        const copy = {...JSON.parse(JSON.stringify(w)), id: newId()};
        copy.y = w.y + w.h + copy.h <= S.doc.rows ? w.y + w.h : w.y;
        copy.x = copy.y === w.y ? clamp(w.x + w.w, 0, S.doc.cols - w.w) : w.x;
        page.widgets.push(copy);
        S.sel = copy.id;
    } else if (w && d.erm) w.entities.splice(Number(d.erm), 1);
    else if (w && d.eup) { const n = Number(d.eup); [w.entities[n - 1], w.entities[n]] = [w.entities[n], w.entities[n - 1]]; }
    else return UNDO.pop();
    changed();
});

// ---------- Dragging ----------
// The grid cell under the pointer (the stage is scaled, the page has padding and gaps)
function cellAt(ev) {
    const el = $('#page'), r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    const px = v => parseFloat(v) * stageScale;
    const left = r.left + px(cs.paddingLeft), top = r.top + px(cs.paddingTop);
    const width = r.width - px(cs.paddingLeft) - px(cs.paddingRight), height = r.height - px(cs.paddingTop) - px(cs.paddingBottom);
    const gx = px(cs.columnGap), gy = px(cs.rowGap);
    return {
        x: clamp(Math.floor((ev.clientX - left) / ((width + gx) / S.doc.cols)), 0, S.doc.cols - 1),
        y: clamp(Math.floor((ev.clientY - top) / ((height + gy) / S.doc.rows)), 0, S.doc.rows - 1),
    };
}
function place(el, w) {
    el.style.gridColumn = `${w.x + 1}/span ${w.w}`;
    el.style.gridRow = `${w.y + 1}/span ${w.h}`;
}
function track(ev, move, done) {
    const onMove = m => { m.preventDefault(); move(m); };
    const onUp = u => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        document.removeEventListener('pointercancel', onUp);
        done(u);
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
}

function dragWidget(ev, el, sizing) {
    const w = findWidget(el.dataset.id);
    const before = JSON.stringify(S.doc), start = cellAt(ev), from = {x: w.x, y: w.y};
    let moved = false;
    track(ev, m => {
        const c = cellAt(m);
        if (sizing) {
            w.w = clamp(c.x - w.x + 1, 1, S.doc.cols - w.x);
            w.h = clamp(c.y - w.y + 1, 1, S.doc.rows - w.y);
        } else {
            w.x = clamp(from.x + c.x - start.x, 0, S.doc.cols - w.w);
            w.y = clamp(from.y + c.y - start.y, 0, S.doc.rows - w.h);
        }
        if (before !== JSON.stringify(S.doc) || moved) {
            moved = true;
            place(el, w);
            if (sizing) drawWidget(w.id);
        }
    }, () => {
        if (before !== JSON.stringify(S.doc)) {
            UNDO.push(before);
            S.sel = w.id;
            changed();
        } else if (!moved) select(w.id);
    });
}

function dragFromDrawer(ev, src) {
    const entity = src.dataset.dragEntity, type = src.dataset.dragBlock || blockFor(entity);
    const size = BLOCKS[type].size;
    const ghost = Object.assign(document.createElement('div'), {id: 'ghost', textContent: entity ? ename(entity) : BLOCKS[type].name});
    const drop = Object.assign(document.createElement('div'), {id: 'drop'});
    let target = null, started = false;
    const clear = () => { $$('.target').forEach(el => el.classList.remove('target')); drop.remove(); target = null; };
    track(ev, m => {
        if (!started) {
            if (Math.hypot(m.clientX - ev.clientX, m.clientY - ev.clientY) < 5) return;
            started = true;
            document.body.append(ghost);
        }
        ghost.style.left = m.clientX + 'px';
        ghost.style.top = m.clientY + 'px';
        clear();
        const under = document.elementFromPoint(m.clientX, m.clientY);
        if (!under) return;
        const nav = under.closest('#nav'), wEl = under.closest('#page .w');
        if (nav && entity) { nav.classList.add('target'); target = {nav: true}; return; }
        if (!under.closest('#page')) return;
        const w = wEl && findWidget(wEl.dataset.id), block = w && BLOCKS[w.type];
        if (entity && block && block.takes(entity) && !w.entities.includes(entity) && (block.many || !w.entities.length)) {
            wEl.classList.add('target');
            target = {widget: w};
            return;
        }
        const c = cellAt(m);
        const spot = {w: Math.min(size[0], S.doc.cols), h: Math.min(size[1], S.doc.rows)};
        spot.x = clamp(c.x, 0, S.doc.cols - spot.w);
        spot.y = clamp(c.y, 0, S.doc.rows - spot.h);
        place(drop, spot);
        $('#page').append(drop);
        target = {spot};
    }, () => {
        const t = target;
        ghost.remove();
        clear();
        if (!t) return;
        snapshot();
        if (t.nav) {
            if (S.doc.chips.length >= 8) { UNDO.pop(); return toast('The top bar is full'); }
            S.doc.chips.push({entity, label: ''});
        } else if (t.widget) t.widget.entities.push(entity);
        else currentPage().widgets.push({id: newId(), type, ...t.spot, label: '', entities: entity ? [entity] : [], opts: {}});
        changed();
    });
}

document.addEventListener('pointerdown', ev => {
    if (!S.design || ev.button > 0) return;
    const src = ev.target.closest('[data-drag-entity],[data-drag-block]');
    if (src) { ev.preventDefault(); return dragFromDrawer(ev, src); }
    if (!ev.target.closest('#page')) return;
    const el = ev.target.closest('.w');
    if (!el) { if (S.sel) select(null); return; }
    ev.preventDefault();
    dragWidget(ev, el, Boolean(ev.target.closest('.w-size')));
});
document.addEventListener('keydown', ev => {
    if (!S.design || ev.target.matches('input, select, textarea')) return;
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'z') { ev.preventDefault(); return undo(); }
    const w = S.sel && findWidget(S.sel);
    if (ev.key === 'Escape') return w ? select(null) : setDesign(false);
    if (!w) return;
    if (ev.key === 'Delete' || ev.key === 'Backspace') { ev.preventDefault(); return removeSelected(); }
    const step = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[ev.key];
    if (!step) return;
    ev.preventDefault();
    snapshot();
    if (ev.shiftKey) { w.w += step[0]; w.h += step[1]; } else { w.x += step[0]; w.y += step[1]; }
    fitWidget(w);
    changed();
});
