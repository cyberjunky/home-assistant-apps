// Library: the games your ROM zips hold, upload, the legal notice, and the Info tab.
let appStatus = null;
let library = {games: [], problems: []};

function switchTab(name) {
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
    document.getElementById('search').hidden = name !== 'library';
}

async function load() {
    try {
        appStatus = await getJson('/api/status');
        library = await getJson('/api/games');
    } catch (e) {
        document.getElementById('games').innerHTML = `<div class="empty">Cannot reach the app: ${escapeHtml(e.message)}</div>`;
        const state = document.getElementById('sb-state');
        state.className = 'sb-warn';
        state.textContent = '● OFFLINE';
        return;
    }
    document.getElementById('legal').hidden = appStatus.legal_accepted;
    document.getElementById('upload').hidden = !appStatus.legal_accepted;
    document.getElementById('rom-folder').textContent = appStatus.rom_folder;
    document.getElementById('brand-sub').textContent = appStatus.mame.version ? `MAME ${appStatus.mame.version}` : '';
    renderInfo();
    renderStatusbar();
    renderProblems();
    renderGames();
}

function acceptLegal() {
    getJson('/api/legal', {method: 'POST'}).then(load).catch(e => toast(e.message, 'error'));
}

const FLAG_CLASS = {'not working': 'bad', 'protection not emulated': 'bad', 'no sound': 'warn'};
function gameCard(g) {
    const flags = g.flags.map(f => `<span class="chip ${FLAG_CLASS[f] || 'warn'}">${escapeHtml(f)}</span>`).join('');
    const state = g.playable
        ? `<a class="btn btn-primary" href="${escapeHtml(api('/play/' + g.name))}">▶ Play</a>`
        : `<span class="chip bad" title="${escapeHtml(g.missing.join(', '))}">${g.missing.length} of ${g.files} files missing${g.parent && !g.parent_zip ? ` · needs ${escapeHtml(g.parent)}.zip` : ''}</span>`;
    return `<div class="game${g.playable ? '' : ' incomplete'}">
        <div class="game-art ${g.rotate === 90 || g.rotate === 270 ? 'vertical' : ''}" aria-hidden="true"><span>${escapeHtml(g.description.slice(0, 1))}</span></div>
        <div class="game-info">
            <div class="game-name" title="${escapeHtml(g.description)}">${escapeHtml(g.description)}</div>
            <div class="game-meta">${escapeHtml(g.year)} · ${escapeHtml(g.company)}</div>
            <div class="game-chips"><span class="chip mono">${escapeHtml(g.name)}</span>${g.has_scores ? '<span class="chip ok" title="High scores are saved">🏆 scores</span>' : ''}${flags}</div>
        </div>
        <div class="game-actions">${state}
            ${g.zip ? `<button type="button" class="btn btn-ghost" title="Remove ${escapeHtml(g.zip)}" data-file="${escapeHtml(g.zip)}" onclick="removeRom(this.dataset.file)">✕</button>` : ''}</div>
    </div>`;
}

function renderGames() {
    const el = document.getElementById('games');
    const q = (document.getElementById('search').value || '').toLowerCase().trim();
    const list = library.games
        .filter(g => !q || [g.description, g.name, g.company, g.year].join(' ').toLowerCase().includes(q))
        .sort((a, b) => (b.playable - a.playable) || a.description.localeCompare(b.description));
    if (!library.games.length) {
        el.innerHTML = `<div class="empty"><b>No games yet.</b><br>Add ROM zip files above. This MAME build runs
            ${appStatus ? appStatus.known_games : ''} machines, see the Info tab.</div>`;
        return;
    }
    el.innerHTML = list.length ? list.map(gameCard).join('') : '<div class="empty">No game matches the search.</div>';
}

function renderProblems() {
    const el = document.getElementById('problems');
    el.hidden = !library.problems.length;
    el.innerHTML = library.problems.map(p => `<div class="problem"><span class="mono">${escapeHtml(p.file)}</span>
        <span class="panel-sub">${escapeHtml(p.error)}</span>
        ${p.rename_to ? `<button type="button" class="btn" data-file="${escapeHtml(p.file)}" data-to="${escapeHtml(p.rename_to)}" onclick="renameRom(this.dataset.file, this.dataset.to)">Rename</button>` : ''}
        <button type="button" class="btn btn-ghost" data-file="${escapeHtml(p.file)}" onclick="removeRom(this.dataset.file)">Remove</button></div>`).join('');
}

function renderStatusbar() {
    const state = document.getElementById('sb-state');
    const ok = appStatus.mame_present;
    state.className = ok ? 'sb-ok' : 'sb-warn';
    state.textContent = ok ? '● READY' : '● MAME MISSING';
    document.getElementById('sb-folder').textContent = appStatus.rom_folder;
    document.getElementById('sb-playable').textContent = library.games.filter(g => g.playable).length;
    document.getElementById('sb-known').textContent = appStatus.known_games;
    document.getElementById('sb-mame').textContent = appStatus.mame.version || '';
}

function renderInfo() {
    const m = appStatus.mame || {};
    document.getElementById('info-version').textContent = 'v' + appStatus.version;
    if (m.tag) document.getElementById('mame-source').href = `https://github.com/mamedev/mame/tree/${encodeURIComponent(m.tag)}`;
    const drivers = (m.drivers || []).map(d => `<span class="chip mono">${escapeHtml(d)}</span>`).join(' ');
    document.getElementById('info-mame').innerHTML = `
        <div><span>MAME</span><b>${escapeHtml(m.version || 'unknown')}</b></div>
        <div><span>Machines</span><b>${appStatus.known_games}</b></div>
        <div><span>Drivers</span><span>${drivers || '—'}</span></div>
        <div><span>ROM folder</span><span class="mono">${escapeHtml(appStatus.rom_folder)}</span></div>
        ${appStatus.mame_present ? '' : '<div class="bad">The MAME build is missing from this app image.</div>'}`;
}

async function uploadFiles(files) {
    const log = document.getElementById('upload-log');
    for (const file of files) {
        const line = document.createElement('div');
        line.textContent = `${file.name}: uploading…`;
        log.prepend(line);
        const form = new FormData();
        form.append('file', file, file.name);
        try {
            const res = await getJson('/api/roms', {method: 'POST', body: form});
            const r = res.results[0] || {ok: false, error: 'no answer'};
            line.className = r.ok ? 'ok' : 'bad';
            line.textContent = r.ok ? `${file.name}: added as ${r.file} (${r.description})` : `${file.name}: ${r.error}`;
        } catch (e) {
            line.className = 'bad';
            line.textContent = `${file.name}: ${e.message}`;
        }
    }
    load();
}

function removeRom(file) {
    if (!confirm(`Remove ${file} from the ROM folder?`)) return;
    getJson('/api/roms/' + encodeURIComponent(file), {method: 'DELETE'}).then(load).catch(e => toast(e.message, 'error'));
}
function renameRom(file, to) {
    getJson('/api/roms/' + encodeURIComponent(file) + '/rename', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({to})})
        .then(load).catch(e => toast(e.message, 'error'));
}

const drop = document.getElementById('upload');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files].filter(f => /\.zip$/i.test(f.name))));

load();
