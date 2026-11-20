// Scanner - the Scan page: the scanner and every option it has, a preview to choose the area on,
// one page or a batch, and (in tray.js) the pages scanned so far.

// device: the chosen scanner; values: what the user chose for it (the scanner's own values stay
// the scanner's); opts: the scanner's answer with those applied
const sc = {device: '', values: {}, opts: null, loading: false, error: '', batch: {mode: 'single', count: 0, interval: 10},
    more: stored('more', '') === '1', token: 0, ready: false, drag: null, lastSource: null};

// The options shown first; the rest is under "More options"
const MAIN = ['source', 'mode', 'resolution'];
const GEOMETRY = ['l', 't', 'x', 'y'];
const PAPER = [
    {key: 'a4', label: 'A4', w: 210, h: 297},
    {key: 'a5', label: 'A5', w: 148, h: 210},
    {key: 'a6', label: 'A6', w: 105, h: 148},
    {key: 'letter', label: 'Letter', w: 215.9, h: 279.4},
    {key: 'legal', label: 'Legal', w: 215.9, h: 355.6},
];
const TITLES = {source: 'Source', mode: 'Colour', resolution: 'Resolution'};

const scOption = name => {
    if (!sc.opts) return null;
    for (const g of sc.opts.groups) for (const o of g.options) if (o.name === name) return o;
    return null;
};
const scDevice = () => (app.devices && app.devices.devices || []).find(d => d.id === sc.device) || null;
const settable = o => o && o.active && !o.readonly && !o.array && o.type !== 'button';

async function scanInit() {
    if (!app.devices || !app.devices.at) await loadDevices();
    const devices = app.devices.devices;
    const s = app.settings || {};
    if (!devices.find(d => d.id === sc.device)) {
        sc.device = (devices.find(d => d.id === s.device) || devices[0] || {id: ''}).id;
        sc.values = Object.assign({}, (s.values || {})[sc.device] || {});
        sc.opts = null;
    }
    if (!sc.ready) {
        sc.batch = Object.assign({}, s.batch || sc.batch);
        sc.ready = true;
    }
    renderScan();
    if (sc.device && !sc.opts) await loadOptions();
}

// Ask the scanner for its options with what was chosen. The newest question wins.
async function loadOptions() {
    if (!sc.device) return;
    const token = ++sc.token;
    sc.loading = true;
    sc.error = '';
    renderScanForm();
    try {
        const opts = await apiPost('options', {device: sc.device, values: sc.values});
        if (token !== sc.token) return;
        sc.opts = opts;
        // What the scanner refused is no longer a choice
        for (const name of Object.keys(opts.dropped)) delete sc.values[name];
        const names = Object.keys(opts.dropped);
        if (names.length) toast(`The scanner did not take ${names.join(', ')}: ${opts.dropped[names[0]]}`);
        // Choosing the feeder means a batch, choosing the glass a single page
        const source = (scOption('source') || {}).value || '';
        if (sc.lastSource !== null && source !== sc.lastSource) {
            if (opts.feeder && sc.batch.mode === 'single') sc.batch.mode = 'feeder';
            if (!opts.feeder && sc.batch.mode === 'feeder') sc.batch.mode = 'single';
        }
        sc.lastSource = source;
    } catch (e) {
        if (token !== sc.token) return;
        sc.error = e.message;
    } finally {
        if (token === sc.token) {
            sc.loading = false;
            renderScanForm();
            renderPreview();
        }
    }
}

function setValue(name, value) {
    if (value === null || value === '') delete sc.values[name];
    else sc.values[name] = String(value);
    loadOptions();
}

// ---------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------
function renderScan() {
    const el = document.getElementById('tab-scan');
    const d = app.devices;
    if (!d) { el.innerHTML = '<div class="empty-state">Looking for scanners…</div>'; return; }
    if (!d.devices.length) {
        const searching = app.status && (app.status.searching || !app.status.devices_at);
        el.innerHTML = `${panel('', '', `<div class="no-scanner">
            <h2>${searching ? 'Looking for scanners…' : 'No scanner found'}</h2>
            ${searching ? '' : `<p>${d.error ? escapeHtml(d.error) + '. ' : ''}Connect the scanner to the machine Home Assistant runs on, switch it on and search again.
                A scanner on the network is added in the app's configuration. <a href="#info" class="plain-link"><u>Info</u></a> tells what works and how to try the app without a scanner.</p>
            <div class="detail-actions"><button class="btn btn-primary" id="sc-search">Search again</button></div>`}
        </div>`)}<div id="sc-tray"></div>`;
        const button = document.getElementById('sc-search');
        if (button) button.addEventListener('click', searchDevices);
        renderTray();
        return;
    }
    el.innerHTML = `
        <div class="scan-layout">
            <div class="panel"><div class="panel-body" id="sc-form"></div></div>
            <div class="panel"><div class="panel-body" id="sc-preview"></div></div>
        </div>
        <div id="sc-tray"></div>`;
    renderScanForm();
    renderPreview();
    renderTray();
}

async function searchDevices(event) {
    const button = event && event.currentTarget;
    const label = button ? button.textContent : '';
    if (button) { button.disabled = true; button.textContent = 'Searching…'; }
    try {
        app.devices = await apiPost('devices/search', {});
        if (!app.devices.devices.find(d => d.id === sc.device)) { sc.device = ''; sc.opts = null; }
        if (activeTab === 'scan') await scanInit();
        if (activeTab === 'info') renderInfo();
        toast(app.devices.devices.length ? `Found ${plural(app.devices.devices.length, 'scanner')}` : 'No scanner found', app.devices.devices.length ? 'success' : 'error');
        if (button && button.isConnected) { button.disabled = false; button.textContent = label; }
    } catch (e) {
        toast(e.message);
        if (button) { button.disabled = false; button.textContent = label; }
    }
}

function control(o, main) {
    const id = `opt-${o.name}`;
    const unit = o.unit ? `<span class="unit">${escapeHtml(o.unit)}</span>` : '';
    const value = o.value === null || o.value === undefined ? '' : o.value;
    if (!settable(o)) {
        return `<span class="opt-fixed mono">${escapeHtml(value || '–')}${unit}</span>`;
    }
    if (o.type === 'bool') {
        if (o.auto) {
            return `<select class="select" data-opt="${attr(o.name)}" id="${id}">${['auto', 'yes', 'no'].map(c => `<option value="${c}"${c === value ? ' selected' : ''}>${c}</option>`).join('')}</select>`;
        }
        return `<label class="check-row"><input type="checkbox" data-opt="${attr(o.name)}" data-bool id="${id}"${value === 'yes' ? ' checked' : ''}> <span>${value === 'yes' ? 'on' : 'off'}</span></label>`;
    }
    if (o.type === 'choice') {
        const choices = o.auto ? ['auto', ...o.choices] : o.choices;
        // A few short choices as buttons, more as a list
        if (main && choices.length <= 5 && choices.join('').length <= 46) {
            return `<div class="tabs opt-chips" role="group">${choices.map(c =>
                `<button type="button" class="tab chip${c === value ? ' active' : ''}" data-opt="${attr(o.name)}" data-value="${attr(c)}">${escapeHtml(c)}</button>`).join('')}</div>${unit}`;
        }
        return `<select class="select" data-opt="${attr(o.name)}" id="${id}">${choices.map(c => `<option value="${attr(c)}"${c === value ? ' selected' : ''}>${escapeHtml(c)}</option>`).join('')}</select>${unit}`;
    }
    if (o.type === 'number') {
        const step = o.step || (Number.isInteger(o.min) && Number.isInteger(o.max) && o.max - o.min > 20 ? 1 : 'any');
        const auto = o.auto ? `<label class="check-row auto"><input type="checkbox" data-opt="${attr(o.name)}" data-auto${value === 'auto' ? ' checked' : ''}> auto</label>` : '';
        const number = value === 'auto' ? '' : value;
        // The typed number has no steps of its own: the scanner rounds to what it can do and says so.
        // A browser counting steps from the lowest value would call a fine number wrong.
        const slider = !GEOMETRY.includes(o.name) && o.max > o.min
            ? `<input type="range" class="opt-range" data-range="${attr(o.name)}" min="${o.min}" max="${o.max}" step="${step}" value="${attr(number)}" aria-label="${attr(o.title)}">` : '';
        return `${slider}<input type="number" class="input opt-number" data-opt="${attr(o.name)}" id="${id}" min="${o.min}" max="${o.max}" step="any" value="${attr(number)}">${unit}${auto}`;
    }
    return `<input type="text" class="input" data-opt="${attr(o.name)}" id="${id}" value="${attr(value)}" spellcheck="false">${unit}`;
}

function optionRow(o, main) {
    const title = TITLES[o.name] || o.title;
    const chosen = Object.prototype.hasOwnProperty.call(sc.values, o.name);
    return `<div class="opt${o.active ? '' : ' off'}" title="${attr(o.description)}">
        <label class="opt-label" for="opt-${attr(o.name)}">${escapeHtml(title)}${chosen ? '<i class="opt-set" title="You chose this; the rest is the scanner\'s own"></i>' : ''}</label>
        <div class="opt-control">${control(o, main)}</div>
        ${!main && o.description ? `<div class="opt-hint">${escapeHtml(o.description)}</div>` : ''}
    </div>`;
}

function paperRow() {
    const x = scOption('x'), y = scOption('y');
    const bed = sc.opts.bed;
    if (!settable(x) || !settable(y) || !bed) return '';
    const fits = p => p.w <= bed[0] + 0.5 && p.h <= bed[1] + 0.5;
    const near = (a, b) => Math.abs(a - b) < 1.1;
    const w = Number(x.value), h = Number(y.value);
    const left = Number((scOption('l') || {}).value || 0), top = Number((scOption('t') || {}).value || 0);
    let current = 'custom';
    if (left === 0 && top === 0) {
        if (near(w, bed[0]) && near(h, bed[1])) current = 'all';
        for (const p of PAPER) if (fits(p) && near(w, Math.min(p.w, bed[0])) && near(h, Math.min(p.h, bed[1]))) current = p.key;
    }
    const whole = sc.opts.feeder ? 'Widest page' : 'Whole glass';
    const options = [{key: 'all', label: `${whole} (${num(bed[0])} × ${num(bed[1])} ${bed[2]})`}, ...(bed[2] === 'mm' ? PAPER.filter(fits) : [])];
    if (current === 'custom') options.push({key: 'custom', label: `Own area (${num(w, 1)} × ${num(h, 1)} ${bed[2]})`});
    return `<div class="opt"><label class="opt-label" for="sc-paper">Paper</label>
        <div class="opt-control"><select class="select" id="sc-paper">${options.map(p => `<option value="${p.key}"${p.key === current ? ' selected' : ''}>${escapeHtml(p.label)}</option>`).join('')}</select></div></div>`;
}

function batchRow() {
    const b = sc.batch;
    const modes = [
        {key: 'single', label: 'One page', title: 'Scan one page and add it to the pages below'},
        {key: 'feeder', label: 'Feeder', title: 'Scan every page in the document feeder'},
        {key: 'step', label: 'Step by step', title: 'Scan a page from the glass, then wait until you press Next page'},
        {key: 'timed', label: 'Timed', title: 'Scan again and again from the glass, with time to turn the page'},
    ];
    const count = `<input type="number" class="input opt-number" id="sc-count" min="0" max="500" step="1" value="${b.count || ''}" placeholder="${b.mode === 'feeder' ? 'all' : '∞'}">`;
    let extra = '';
    if (b.mode === 'feeder') extra = `<span class="batch-extra">pages ${count}</span>`;
    if (b.mode === 'step') extra = `<span class="batch-extra">pages ${count}</span>`;
    if (b.mode === 'timed') extra = `<span class="batch-extra">every <input type="number" class="input opt-number" id="sc-interval" min="2" max="600" step="1" value="${b.interval}"> s · pages ${count}</span>`;
    return `<div class="opt"><span class="opt-label">Batch</span>
        <div class="opt-control"><div class="tabs opt-chips" role="group" id="sc-batch">${modes.map(m =>
            `<button type="button" class="tab chip${m.key === b.mode ? ' active' : ''}" data-batch="${m.key}" title="${attr(m.title)}">${m.label}</button>`).join('')}</div>${extra}</div>
        ${b.mode === 'step' ? '<div class="opt-hint">Scans a page and waits. Put the next page down and press Next page; press Stop when you are done.</div>' : ''}
        ${b.mode === 'timed' ? '<div class="opt-hint">Scans a page, waits so you can turn the page or put the next one down, and scans again until you press Stop.</div>' : ''}
        ${b.mode === 'feeder' && sc.opts && !sc.opts.feeder ? '<div class="opt-hint warn-text">The source is not a feeder: choose the feeder above, or give the number of pages.</div>' : ''}
    </div>`;
}

function jobBox() {
    const job = app.status ? app.status.job : {state: 'idle'};
    const mine = job.kind === 'scan' || job.kind === 'preview';
    if (job.state === 'running' && mine) {
        const text = job.waiting ? 'Put the next page down and press Next page'
            : job.countdown ? `Next page in ${job.countdown} s — turn the page`
            : job.kind === 'preview' ? `${job.message || 'Preview'}${job.percent ? ` · ${num(job.percent)}%` : ''}`
            : job.page ? `Page ${num(job.page)}${job.percent ? ` · ${num(job.percent)}%` : ''}` : (job.message || 'Starting');
        const ratio = job.countdown || job.waiting ? 0 : (job.percent || 0) / 100;
        return `<div class="job"><div class="job-line"><span>${escapeHtml(text)}</span><span class="muted">${job.kind === 'scan' ? `${plural(job.pages, 'page')} done` : ''}</span></div>
            <div class="meter big"><div class="meter-bar"><i style="width:${(ratio * 100).toFixed(1)}%"></i></div></div></div>`;
    }
    // A failure stays in view for a few minutes, not for ever
    if (job.state === 'failed' && mine && app.status.now - job.finished < 300) return `<div class="job failed"><span class="sdot bad"></span> ${escapeHtml(job.error)}</div>`;
    return '';
}

function renderScanForm() {
    const el = document.getElementById('sc-form');
    if (!el) return;
    const devices = app.devices.devices;
    const running = jobRunning();
    const profiles = (app.settings && app.settings.profiles) || [];
    // Between two pages of a batch from the glass: waiting for Next page, or counting down
    const between = running && app.status.job.kind === 'scan' && Boolean(app.status.job.waiting || app.status.job.countdown);
    // The options can't be changed while the scanner works or is being asked; Stop always can be pressed
    const busy = sc.loading || running ? ' form-busy' : '';
    let body = '';
    if (sc.error) {
        body = `<div class="job failed"><span class="sdot bad"></span> ${escapeHtml(sc.error)}</div>
            <div class="detail-actions"><button class="btn" id="sc-retry">Try again</button></div>`;
    } else if (!sc.opts) {
        body = '<div class="empty-state">Asking the scanner for its options…</div>';
    } else {
        const main = MAIN.map(scOption).filter(o => o && o.active);
        const rest = sc.opts.groups.map(g => ({title: g.title, options: g.options.filter(o => !MAIN.includes(o.name) && o.type !== 'button' && !o.array)}))
            .filter(g => g.options.length);
        const shown = rest.map(g => ({title: g.title, options: g.options.filter(o => o.active)})).filter(g => g.options.length);
        const count = shown.reduce((a, g) => a + g.options.length, 0);
        const hidden = rest.reduce((a, g) => a + g.options.length, 0) - count;
        const chosen = Object.keys(sc.values).length;
        body = `
            <div class="opts${busy}">${main.map(o => optionRow(o, true)).join('')}${paperRow()}${batchRow()}</div>
            ${sc.opts.notes.length ? `<div class="opt-hint">${sc.opts.notes.map(escapeHtml).join('<br>')}</div>` : ''}
            <div class="scan-actions">
                ${running ? `${between ? `<button class="btn btn-primary scan-button" id="sc-next">${app.status.job.waiting ? 'Next page' : 'Next page now'}</button>` : ''}
                    <button class="btn btn-danger${between ? '' : ' scan-button'}" id="sc-stop"${app.status.job.kind === 'save' ? ' disabled' : ''}>Stop</button>`
                    : `<button class="btn btn-primary scan-button" id="sc-scan">${sc.batch.mode === 'single' ? 'Scan' : 'Scan batch'}</button>
                       <button class="btn" id="sc-preview-button" title="A quick scan of the whole glass, to choose the area on">Preview</button>`}
                <span class="spacer"></span>
                ${chosen ? `<button class="btn" id="sc-reset" title="Back to the scanner's own settings"${running ? ' disabled' : ''}>Reset</button>` : ''}
                <button class="btn" id="sc-save-profile" title="Keep these settings under a name"${running ? ' disabled' : ''}>Save as profile…</button>
            </div>
            <div id="sc-job">${jobBox()}</div>
            <details class="advanced more-opts${busy}"${sc.more ? ' open' : ''} id="sc-more">
                <summary>More options <span class="muted">(${num(count)})</span></summary>
                ${shown.map(g => `<div class="opt-group">${escapeHtml(g.title)}</div><div class="opts">${g.options.map(o => optionRow(o, false)).join('')}</div>`).join('')}
                ${hidden ? `<div class="opt-hint">${plural(hidden, 'option')} of this scanner can't be used with these settings.</div>` : ''}
            </details>`;
    }
    el.innerHTML = `
        <div class="scan-head">
            <div class="opt-control grow"><select class="select grow" id="sc-device" aria-label="Scanner"${running ? ' disabled' : ''}>${devices.map(d =>
                `<option value="${attr(d.id)}"${d.id === sc.device ? ' selected' : ''}>${escapeHtml(d.label)}</option>`).join('')}</select>
                <button class="btn" id="sc-search" title="Look for scanners again"${running ? ' disabled' : ''}>Search</button></div>
            ${profiles.length ? `<select class="select" id="sc-profile" aria-label="Profile"${running ? ' disabled' : ''}><option value="">Profile…</option>${profiles.map(p => `<option value="${attr(p.name)}">${escapeHtml(p.name)}</option>`).join('')}</select>` : ''}
        </div>
        ${body}`;
    bindScanForm(el);
}

function bindScanForm(el) {
    const on = (id, event, fn) => { const x = document.getElementById(id); if (x) x.addEventListener(event, fn); };
    on('sc-device', 'change', (e) => {
        sc.device = e.target.value;
        sc.values = Object.assign({}, ((app.settings || {}).values || {})[sc.device] || {});
        sc.opts = null;
        sc.lastSource = null;
        loadOptions();
    });
    on('sc-search', 'click', searchDevices);
    on('sc-retry', 'click', loadOptions);
    on('sc-profile', 'change', (e) => {
        const p = ((app.settings || {}).profiles || []).find(x => x.name === e.target.value);
        if (!p) return;
        if (p.device && app.devices.devices.find(d => d.id === p.device)) sc.device = p.device;
        sc.values = Object.assign({}, p.values);
        sc.batch = Object.assign({}, p.batch);
        sc.opts = null;
        sc.lastSource = null;
        loadOptions();
    });
    on('sc-more', 'toggle', (e) => { sc.more = e.target.open; store('more', sc.more ? '1' : ''); });
    on('sc-reset', 'click', () => { sc.values = {}; sc.lastSource = null; loadOptions(); });
    on('sc-scan', 'click', startScan);
    on('sc-preview-button', 'click', startPreview);
    on('sc-stop', 'click', stopJob);
    on('sc-next', 'click', nextPage);
    on('sc-save-profile', 'click', saveProfile);
    on('sc-paper', 'change', (e) => choosePaper(e.target.value));
    on('sc-count', 'change', (e) => { sc.batch.count = Math.max(0, Math.min(500, parseInt(e.target.value, 10) || 0)); });
    on('sc-interval', 'change', (e) => { sc.batch.interval = Math.max(2, Math.min(600, parseInt(e.target.value, 10) || 10)); });
    on('sc-batch', 'click', (e) => {
        const button = e.target.closest('[data-batch]');
        if (!button) return;
        sc.batch.mode = button.dataset.batch;
        renderScanForm();
    });
    // The scanner's options
    el.querySelectorAll('[data-opt]').forEach(input => {
        const name = input.dataset.opt;
        if (input.tagName === 'BUTTON') {
            input.addEventListener('click', () => setValue(name, input.dataset.value));
        } else if (input.hasAttribute('data-auto')) {
            input.addEventListener('change', () => setValue(name, input.checked ? 'auto' : null));
        } else if (input.hasAttribute('data-bool')) {
            input.addEventListener('change', () => setValue(name, input.checked ? 'yes' : 'no'));
        } else {
            input.addEventListener('change', () => setValue(name, input.value.trim()));
        }
    });
    // A slider moves its number while dragging and asks the scanner when let go
    el.querySelectorAll('[data-range]').forEach(range => {
        const number = el.querySelector(`[data-opt="${CSS.escape(range.dataset.range)}"].opt-number`);
        range.addEventListener('input', () => { if (number) number.value = range.value; });
        range.addEventListener('change', () => setValue(range.dataset.range, range.value));
    });
}

function choosePaper(key) {
    const bed = sc.opts.bed;
    if (key === 'custom' || !bed) return;
    const p = PAPER.find(x => x.key === key);
    const w = p ? Math.min(p.w, bed[0]) : bed[0];
    const h = p ? Math.min(p.h, bed[1]) : bed[1];
    setArea(0, 0, w, h);
}

// The area to scan, in the scanner's own unit. Left and top first: the width that fits depends on them.
function setArea(left, top, width, height) {
    const r = v => String(Math.round(v * 10) / 10);
    Object.assign(sc.values, {l: r(left), t: r(top), x: r(width), y: r(height)});
    loadOptions();
}

async function startScan() {
    try {
        await apiPost('scan', {device: sc.device, values: sc.values, batch: sc.batch});
        if (app.settings) { app.settings.device = sc.device; app.settings.values[sc.device] = Object.assign({}, sc.values); app.settings.batch = Object.assign({}, sc.batch); }
        statusSoon();
    } catch (e) { toast(e.message); }
}
async function startPreview() {
    try {
        await apiPost('preview', {device: sc.device, values: sc.values});
        statusSoon();
    } catch (e) { toast(e.message); }
}
async function nextPage() {
    try { await apiPost('next', {}); statusSoon(); } catch (e) { toast(e.message); }
}
async function stopJob() {
    try { await apiPost('cancel', {}); statusSoon(); } catch (e) { toast(e.message); }
}

async function saveProfile() {
    const v = await formDialog({
        title: 'Save as profile', confirmLabel: 'Save',
        body: `<p>The scanner, its settings and the batch mode, to choose again from the list above. A profile with the same name is replaced.</p>
            <div class="form-field"><label>Name</label><input class="input" name="name" maxlength="60" placeholder="Letters, two-sided" autocomplete="off"></div>`,
        validate: x => x.name ? '' : 'Give the profile a name',
    });
    if (!v) return;
    try {
        takeSettings(await apiPost('profiles', {name: v.name, device: sc.device, values: sc.values, batch: sc.batch}));
        toast(`Profile ${v.name} saved`, 'success');
        renderScanForm();
    } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------------
// The preview: the whole glass, with the area that will be scanned. Drag to choose another.
// ---------------------------------------------------------------------
function areaNow() {
    const bed = sc.opts && sc.opts.bed;
    const x = scOption('x'), y = scOption('y');
    if (!bed || !x || !y || !x.active) return null;
    const left = Number((scOption('l') || {}).value || 0), top = Number((scOption('t') || {}).value || 0);
    return {left: left / bed[0], top: top / bed[1], width: Number(x.value) / bed[0], height: Number(y.value) / bed[1]};
}

function renderPreview() {
    const el = document.getElementById('sc-preview');
    if (!el) return;
    const p = app.status && app.status.preview;
    const bed = sc.opts && sc.opts.bed;
    const mine = p && p.device === sc.device;
    const ratio = mine ? p.width / p.height : bed ? bed[0] / bed[1] : 210 / 297;
    const area = areaNow();
    const canDrag = area && settable(scOption('x')) && !jobRunning();
    el.innerHTML = `
        <div class="chart-title"><span>Preview</span><span class="hint">${mine ? `${escapeHtml(fmtAgo(p.at))}${canDrag ? ' · drag to choose the area' : ''}` : 'the glass as the scanner sees it'}</span></div>
        <div class="bed-wrap"><div class="bed${canDrag ? ' can-drag' : ''}" id="sc-bed" style="aspect-ratio:${ratio.toFixed(4)}">
            ${mine ? `<img src="api/preview.jpg?t=${p.at}" alt="Preview of the scanner's glass" draggable="false">` : `<div class="bed-empty">${sc.opts ? 'No preview yet.<br>Press Preview for a quick scan of the whole glass.' : ''}</div>`}
            ${area ? `<div class="bed-area" id="sc-area" style="left:${(area.left * 100).toFixed(2)}%;top:${(area.top * 100).toFixed(2)}%;width:${(Math.min(1, area.width) * 100).toFixed(2)}%;height:${(Math.min(1, area.height) * 100).toFixed(2)}%"></div>` : ''}
        </div></div>
        ${bed && area ? `<div class="bed-note mono">${num(area.width * bed[0], 1)} × ${num(area.height * bed[1], 1)} ${escapeHtml(bed[2])} · ${num(sc.opts.resolution)} dpi</div>` : ''}`;
    if (canDrag) bindBed(document.getElementById('sc-bed'));
}

function bindBed(bedEl) {
    const areaEl = document.getElementById('sc-area');
    const at = (e) => {
        const r = bedEl.getBoundingClientRect();
        return {x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))};
    };
    bedEl.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        sc.drag = {from: at(e), to: at(e)};
        bedEl.setPointerCapture(e.pointerId);
        e.preventDefault();
    });
    bedEl.addEventListener('pointermove', (e) => {
        if (!sc.drag) return;
        sc.drag.to = at(e);
        const d = sc.drag;
        Object.assign(areaEl.style, {
            left: `${Math.min(d.from.x, d.to.x) * 100}%`, top: `${Math.min(d.from.y, d.to.y) * 100}%`,
            width: `${Math.abs(d.to.x - d.from.x) * 100}%`, height: `${Math.abs(d.to.y - d.from.y) * 100}%`,
        });
    });
    const end = () => {
        const d = sc.drag;
        sc.drag = null;
        if (!d) return;
        const bed = sc.opts.bed;
        const w = Math.abs(d.to.x - d.from.x), h = Math.abs(d.to.y - d.from.y);
        // A click without dragging changes nothing
        if (w < 0.02 || h < 0.02) { renderPreview(); return; }
        setArea(Math.min(d.from.x, d.to.x) * bed[0], Math.min(d.from.y, d.to.y) * bed[1], w * bed[0], h * bed[1]);
    };
    bedEl.addEventListener('pointerup', end);
    bedEl.addEventListener('pointercancel', () => { sc.drag = null; renderPreview(); });
}

// ---------------------------------------------------------------------
// While the scanner works
// ---------------------------------------------------------------------
let scanWasRunning = false;
let scanPhase = '';
onJob((job) => {
    if (job.kind !== 'scan' && job.kind !== 'preview') return;
    if (job.state === 'running') return;
    if (job.kind === 'scan') {
        renderTraySave();
        loadTray().then(renderTray).catch(() => {});
        if (job.state === 'done' && !job.cancelled && job.pages) toast(`${plural(job.pages, 'page')} scanned`, 'success');
        if (job.state === 'failed' && job.pages) toast(`${job.error} — ${plural(job.pages, 'page')} kept`);
    }
    // On the Scan page the failure is in the form itself
    if (job.state === 'failed' && !job.pages && activeTab !== 'scan') toast(job.error);
    if (activeTab === 'scan') { renderScanForm(); renderPreview(); }
});

let trayPagesSeen = -1;
onTab('scan', () => {
    scanInit().catch(e => { document.getElementById('tab-scan').innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`; });
    loadTray().then(renderTray).catch(() => {});
}, () => {
    const s = app.status;
    if (!s) return;
    const running = jobRunning();
    // The scanners appear a moment after the app starts
    if ((!app.devices || !app.devices.devices.length) && s.devices && !s.searching) {
        loadDevices().then(() => scanInit()).catch(() => {});
        return;
    }
    if (running || scanWasRunning) {
        const box = document.getElementById('sc-job');
        // The buttons are only drawn again when they change: a button replaced under the mouse loses its click
        const phase = !running ? '' : s.job.waiting ? 'wait' : s.job.countdown ? 'count' : 'scan';
        if (running && box && document.getElementById('sc-stop') && phase === scanPhase) box.innerHTML = jobBox();
        else renderScanForm();
        scanPhase = phase;
    }
    scanWasRunning = running;
    // Pages come in one by one during a batch
    if (s.tray.pages !== trayPagesSeen) {
        trayPagesSeen = s.tray.pages;
        loadTray().then(renderTray).catch(() => {});
    } else if (running && s.job.kind === 'save') {
        renderTraySave();
    }
});
