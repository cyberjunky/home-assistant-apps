// Repair - what every page shares: the top and bottom of the screen, a finding's sheet, and running a repair.

const SEVERITIES = {high: 'Serious', medium: 'Needs a look', low: 'Minor'};
const categoryName = key => (app.status && app.status.categories[key]) || key;
const activeFindings = () => app.findings.filter(f => !f.ignored);
const fixesAllowed = () => Boolean(app.status && app.status.config.allow_fixes);

function scoreWord(score) {
    if (missing(score)) return ['', 'No scan yet'];
    if (score >= 85) return ['good', 'Healthy'];
    if (score >= 60) return ['fair', 'Needs some care'];
    return ['poor', 'Needs repair'];
}

// A link into Home Assistant opens in the whole window, not in this app's frame
function linkHtml(link) {
    if (link.path) return `<a class="ha-link" href="${escapeHtml(link.path)}" target="_top">${escapeHtml(link.label)} ↗</a>`;
    return `<a class="ha-link" href="${escapeHtml(link.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.label)} ↗</a>`;
}

function renderChrome() {
    const s = app.status;
    const parts = [];
    if (s.error) parts.push(`<div class="setup-hint bad"><span><b>Home Assistant could not be read.</b> ${escapeHtml(s.error)}${s.ready ? ' What you see is from the last scan that worked.' : ''}</span></div>`);
    else if (!s.ready) parts.push('<div class="setup-hint info"><span>Looking Home Assistant over for the first time…</span></div>');
    if (s.ready && s.gaps.length) parts.push(`<div class="setup-hint"><span><b>Not everything could be read:</b> ${escapeHtml(s.gaps.join(', '))}. Those parts are not checked, so "healthy" there means "unknown".</span></div>`);
    if (!s.config.allow_fixes) parts.push('<div class="setup-hint info"><span><b>Repairs are turned off.</b> The app only reports. Turn on <b>allow_fixes</b> in its configuration to let it repair.</span></div>');
    fill('notices', parts.join(''));

    const count = activeFindings().length;
    document.getElementById('nav-issues').textContent = s.ready && count ? String(count) : '';
    const button = document.getElementById('scan-button');
    button.disabled = s.scanning;
    button.textContent = s.scanning ? 'Scanning…' : '⟳ Scan';

    const [cls, word] = scoreWord(s.score);
    const dot = `<span class="${cls === 'good' ? 'sb-ok' : cls === 'fair' ? 'sb-warn' : cls === 'poor' ? 'sb-bad' : ''}">●</span>`;
    const bar = [
        `<span>${dot} ${escapeHtml(word)}${missing(s.score) ? '' : ` <b>${s.score}</b>`}</span>`,
        `<span class="sb-extra">Home Assistant <b>${escapeHtml(s.ha_version || '–')}</b></span>`,
        s.scanned_at ? `<span class="sb-extra">scanned <b>${escapeHtml(fmtAgo(s.scanned_at))}</b></span>` : '',
        s.config.allow_fixes ? '' : '<span class="sb-extra">reports only</span>',
        `<span class="spacer"></span><span class="sb-extra">Repair app ${escapeHtml(s.version)} · unofficial</span>`,
    ];
    document.getElementById('statusbar').innerHTML = bar.join('');
}

async function scanNow() {
    try {
        await apiPost('scan', {});
        app.status.scanning = true;
        renderChrome();
        setTimeout(() => loadStatus().catch(() => {}), 1200);
    } catch (e) {
        toast(e.message);
    }
}

// =====================================================================
// The sheet: slides in from the right with one finding or one log line
// =====================================================================
function openSheet(title, bodyHtml) {
    const root = document.getElementById('sheet-root');
    root.innerHTML = `<div class="sheet-overlay"><div class="sheet" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
        <div class="sheet-head"><h2>${escapeHtml(title)}</h2><button class="modal-close" data-close aria-label="Close">✕</button></div>
        <div class="sheet-body">${bodyHtml}</div></div></div>`;
    const overlay = root.querySelector('.sheet-overlay');
    overlay.addEventListener('click', (e) => { if (e.target === overlay || e.target.closest('[data-close]')) closeSheet(); });
    document.addEventListener('keydown', sheetKey);
}
function sheetKey(e) {
    // Escape closes the sheet, unless a confirmation is open on top of it
    if (e.key === 'Escape' && !document.getElementById('modal-root').innerHTML) closeSheet();
}
function closeSheet() {
    document.getElementById('sheet-root').innerHTML = '';
    document.removeEventListener('keydown', sheetKey);
}

function stepsHtml(result) {
    return `<ul class="steps">${result.steps.map(step => `<li class="${step.ok ? 'ok' : 'fail'}"><span class="mark">${step.ok ? '✓' : '✕'}</span><span>${escapeHtml(step.label)}${step.detail ? `<small>${escapeHtml(step.detail)}</small>` : ''}</span></li>`).join('')}</ul>`;
}

function findingRow(f) {
    return `<div class="finding" onclick="openFinding('${f.id}')">
        <span class="sev ${f.severity}" title="${SEVERITIES[f.severity]}"></span>
        <div class="f-main"><div class="f-title">${escapeHtml(f.title)}</div>
        <div class="f-sub">${escapeHtml(categoryName(f.category))}${f.since ? ` · since ${escapeHtml(fmtAgo(f.since).replace(' ago', ''))}` : ''}${f.fixes.length ? ' · can be repaired here' : ''}</div></div>
    </div>`;
}

function openFinding(id) {
    const f = app.findings.find(x => x.id === id);
    if (!f) return toast('That finding is no longer there');
    const repairs = f.fixes.map((fix, i) =>
        `<button type="button" class="btn ${fix.danger ? 'btn-danger' : 'btn-primary'}" onclick="runRepair('${f.id}', ${i})">${escapeHtml(fix.label)}</button>`).join('');
    const body = `
        <div class="badges">
            <span class="badge ${f.severity === 'high' ? 'bad' : f.severity === 'medium' ? 'warn' : ''}">${escapeHtml(SEVERITIES[f.severity])}</span>
            <span class="badge mode">${escapeHtml(categoryName(f.category))}</span>
            ${f.ignored ? '<span class="badge mode">Ignored</span>' : ''}
        </div>
        <p>${escapeHtml(f.text)}</p>
        ${f.since ? `<h3>Since</h3><p>${escapeHtml(fmtDateTime(f.since))} · ${escapeHtml(fmtAgo(f.since))}</p>` : ''}
        ${f.evidence.length ? `<h3>What the app saw</h3><ul class="evidence">${f.evidence.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>` : ''}
        ${f.affected.length > 1 ? `<h3>Affected (${f.affected.length})</h3><ul class="evidence">${f.affected.slice(0, 30).map(e => `<li>${escapeHtml(e)}</li>`).join('')}${f.affected.length > 30 ? `<li>and ${f.affected.length - 30} more</li>` : ''}</ul>` : ''}
        <h3>Repair</h3>
        ${repairs ? `<div class="actions">${repairs}</div>` : `<p class="muted">${fixesAllowed() ? 'Nothing the app can do by itself here: follow the links below.' : 'Repairs are turned off in the app\'s configuration.'}</p>`}
        <div id="sheet-result"></div>
        ${f.links.length ? `<h3>Go there</h3><div class="linklist">${f.links.map(linkHtml).join('')}</div>` : ''}
        <h3>Not a problem for you?</h3>
        <div class="actions"><button type="button" class="btn" onclick="ignoreFinding('${f.id}', ${!f.ignored})">${f.ignored ? 'Stop ignoring it' : 'Ignore it'}</button></div>`;
    openSheet(f.title, body);
}

async function runRepair(findingId, index) {
    const f = app.findings.find(x => x.id === findingId);
    const fix = f && f.fixes[index];
    if (!fix) return;
    const ok = await confirmDialog({
        title: fix.label + '?',
        message: fix.danger
            ? `This removes data from Home Assistant for <b>${escapeHtml(f.subject.name)}</b> and can't be undone here. A backup of Home Assistant is the way back.`
            : `Home Assistant is asked to do this for <b>${escapeHtml(f.subject.name)}</b>.`,
        typeToConfirm: fix.word || '', confirmLabel: fix.label, danger: Boolean(fix.danger),
    });
    if (!ok) return;
    const target = document.getElementById('sheet-result');
    if (target) target.innerHTML = '<p class="muted" style="margin-top:10px">Working…</p>';
    try {
        const result = await apiPost('repair', {finding: findingId, index, fix: fix.fix, word: fix.word || ''});
        if (target) target.innerHTML = stepsHtml(result) + `<p class="muted" style="margin-top:6px">${result.ok ? 'Done. A new scan has run.' : 'Not everything worked: see the steps above.'}</p>`;
        toast(result.ok ? 'Repair done' : 'The repair did not fully work', result.ok ? 'success' : 'error');
        await loadStatus();
    } catch (e) {
        if (target) target.innerHTML = '';
        toast(e.message);
    }
}

async function ignoreFinding(id, ignored) {
    try {
        await apiPost('ignore', {finding: id, ignored});
        const f = app.findings.find(x => x.id === id);
        if (f) f.ignored = ignored;
        closeSheet();
        toast(ignored ? 'Ignored: it no longer counts' : 'It counts again', 'success');
        app.status = await api('status');
        renderChrome();
        renderActive();
    } catch (e) {
        toast(e.message);
    }
}
