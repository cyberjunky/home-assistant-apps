// Library: the games your ROM files hold, upload, the legal notice, the Stats and Info tabs.
let appStatus = null;
let library = {games: [], problems: []};
let stats = null;
let activeTab = 'library';

function switchTab(name) {
    activeTab = name;
    document.querySelectorAll('.tab[data-tab]').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
    document.getElementById('search').hidden = name !== 'library';
    if (name === 'stats') loadStats().catch(e => toast(e.message, 'error'));
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
    renderBios();
    renderGames();
    // For the game details and the Stats tab
    loadStats().catch(() => {});
}

function acceptLegal() {
    getJson('/api/legal', {method: 'POST'}).then(load).catch(e => toast(e.message, 'error'));
}

// ------------------------------------------------------------------ cards
const FLAG_CLASS = {'not working': 'bad', 'protection not emulated': 'bad', 'no sound': 'warn'};
const isVertical = (g) => g.rotate === 90 || g.rotate === 270;

// A stable colour per game, for its tile until it has a thumbnail
function hue(name) {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return h;
}
function thumbUrl(g) { return api(`/thumbs/${encodeURIComponent(g.name)}?v=${g.thumb}`); }

function gameArt(g, big) {
    const cls = `art${isVertical(g) ? ' vertical' : ''}${big ? ' big' : ''}`;
    if (g.thumb) {
        const url = escapeHtml(thumbUrl(g));
        return `<div class="${cls}"><div class="art-blur" style="background-image:url('${url}')"></div>
            <img src="${url}" alt="${escapeHtml(g.description)}" loading="lazy"></div>`;
    }
    return `<div class="${cls}" style="--hue:${hue(g.name)}" aria-hidden="true"><div class="art-ph">
        <span class="ph-title">${escapeHtml(g.description)}</span>
        <span class="ph-sub">${escapeHtml(g.year)} · ${escapeHtml(g.company)}</span>
        <span class="ph-coin">${g.playable ? 'Insert coin' : g.bios_missing && !ownMissing(g).length ? 'Needs BIOS' : 'Files missing'}</span></div></div>`;
}

// The game's own files that are missing (the BIOS set is named separately)
const ownMissing = (g) => g.missing.filter(m => m !== g.bios_missing);
function missingText(g) {
    const own = ownMissing(g);
    const parts = [];
    if (own.length) parts.push(`${own.length} of ${g.files} files missing${g.parent && !g.parent_zip ? ` · needs ${escapeHtml(g.parent)}.zip` : ''}`);
    if (g.bios_missing) parts.push(`needs the ${escapeHtml(g.bios_missing)}`);
    return parts.join(' · ');
}

// BIOS sets the games need (Neo Geo: neogeo.zip): present and complete, or what is missing
function renderBios() {
    const el = document.getElementById('bios-status');
    const list = library.bios || [];
    el.hidden = !list.length;
    el.innerHTML = list.map(b => {
        const users = library.games.filter(g => g.bios === b.name && g.bios_missing).length;
        const state = b.complete
            ? `<span class="ok">✓</span> <span class="mono">${escapeHtml(b.file)}</span>`
            : b.file
                ? `<span class="bad">incomplete</span> <span class="mono">${escapeHtml(b.file)}</span> <span class="panel-sub">misses ${escapeHtml(b.missing.join(', '))}</span>`
                : `<span class="bad">missing</span> <span class="panel-sub">add <span class="mono">${escapeHtml(b.name)}.zip</span> (or .7z) to play ${users} game${users === 1 ? '' : 's'}</span>`;
        return `<div class="bios-line"><b>${escapeHtml(b.label)}</b> ${state}</div>`;
    }).join('');
}

function gameChips(g) {
    const flags = g.flags.map(f => `<span class="chip ${FLAG_CLASS[f] || 'warn'}">${escapeHtml(f)}</span>`).join('');
    return `<span class="chip mono">${escapeHtml(g.name)}</span>`
        + (isVertical(g) ? '<span class="chip">vertical</span>' : '')
        + (g.has_scores ? '<span class="chip ok" title="High scores are saved">🏆 scores</span>' : '') + flags;
}

function playButton(g) {
    return `<a class="btn btn-primary" href="${escapeHtml(api('/play/' + g.name))}" onclick="event.stopPropagation()">▶ Play</a>`;
}

function gameCard(g) {
    const foot = g.playable
        ? `${playButton(g)}<span class="card-played">${g.played ? `${duration(g.played.seconds)} · ${relDay(g.played.last)}` : 'not played yet'}</span>`
        : `<span class="chip bad" title="${escapeHtml(g.missing.join(', '))}">${missingText(g)}</span>`;
    return `<article class="card${g.playable ? '' : ' incomplete'}" tabindex="0" data-game="${escapeHtml(g.name)}"
            onclick="openDetail(this.dataset.game)" onkeydown="if (event.key === 'Enter') openDetail(this.dataset.game)">
        ${gameArt(g, false)}
        <div class="card-body">
            <div class="card-title" title="${escapeHtml(g.description)}">${escapeHtml(g.description)}</div>
            <div class="card-meta">${escapeHtml(g.year)} · ${escapeHtml(g.company)}</div>
            <div class="game-chips">${gameChips(g)}</div>
        </div>
        <div class="card-foot">${foot}</div>
    </article>`;
}

function renderGames() {
    const el = document.getElementById('games');
    const q = (document.getElementById('search').value || '').toLowerCase().trim();
    const list = library.games
        .filter(g => !q || [g.description, g.name, g.company, g.year].join(' ').toLowerCase().includes(q))
        .sort((a, b) => (b.playable - a.playable) || a.description.localeCompare(b.description));
    if (!library.games.length) {
        el.innerHTML = `<div class="empty"><b>No games yet.</b><br>Add ROM files (.zip or .7z) above. This MAME build runs
            ${appStatus ? appStatus.known_games : ''} game machines, see the Info tab.</div>`;
        return;
    }
    el.innerHTML = list.length ? list.map(gameCard).join('') : '<div class="empty">No game matches the search.</div>';
}

// ---------------------------------------------------------- game details
function openDetail(name) {
    const g = library.games.find(x => x.name === name);
    const st = stats && stats.games.find(x => x.name === name);
    if (!g && !st) return;
    const info = g || st;
    const row = (k, v) => `<div><span>${k}</span><span>${v}</span></div>`;
    const played = st
        ? row('Played', `<b>${duration(st.seconds)}</b> in ${st.sessions} session${st.sessions === 1 ? '' : 's'}`)
            + row('Average session', duration(st.seconds / st.sessions))
            + row('Longest session', duration(st.longest))
            + row('First played', dateTime(st.first))
            + row('Last played', dateTime(st.last))
        : row('Played', 'not yet');
    const files = g
        ? row('ROM file', g.zip ? `<span class="mono">${escapeHtml(g.zip)}</span>` : '—')
            + (g.parent ? row('Parent', `<span class="mono">${escapeHtml(g.parent_zip || g.parent + '.zip')}</span>${g.parent_zip ? '' : ' <span class="bad">missing</span>'}`) : '')
            + (g.bios ? row('BIOS', g.bios_missing
                ? `<span class="bad">missing: ${escapeHtml(g.bios_missing)}</span>`
                : `<span class="mono">${escapeHtml(g.bios_zip || g.zip || '')}</span>`) : '')
            + (ownMissing(g).length ? row('Missing', `<span class="bad">${escapeHtml(ownMissing(g).join(', '))}</span>`) : '')
        : row('ROM file', '<span class="panel-sub">no longer in the ROM folder</span>');
    const scores = g ? g.has_scores : st.has_scores;
    document.getElementById('detail-body').innerHTML = `
        ${g ? gameArt(g, true) : ''}
        <div class="detail-info">
            <div class="detail-title">${escapeHtml(info.description)}</div>
            <div class="card-meta">${escapeHtml(info.year)} · ${escapeHtml(info.company)}</div>
            ${g ? `<div class="game-chips">${gameChips(g)}</div>` : ''}
            <div class="kv detail-kv">${played}
                ${row('High scores', scores ? '<span class="ok">🏆 kept</span>' : 'none saved yet')}
                ${files}</div>
            ${g && !g.thumb && g.playable ? '<div class="panel-sub">The picture comes from the game itself: the app takes one while you play.</div>' : ''}
            <div class="detail-actions">
                ${g && g.playable ? playButton(g) : ''}
                <span class="spacer"></span>
                ${g && g.zip ? `<button type="button" class="btn btn-ghost" data-file="${escapeHtml(g.zip)}" onclick="removeRom(this.dataset.file)">Remove ROM file</button>` : ''}
            </div>
        </div>`;
    document.getElementById('detail').hidden = false;
    document.getElementById('detail-close').focus();
}
function closeDetail() { document.getElementById('detail').hidden = true; }
window.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail(); });

// ------------------------------------------------------------------ stats
let statsUnit = 'day';
try { statsUnit = localStorage.getItem('arcade_stats_unit') || 'day'; } catch (e) { /* none */ }

function duration(s) {
    s = Math.round(s || 0);
    if (s < 60) return `${s} s`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m} min`;
    return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}
function dateTime(t) {
    return new Date(t * 1000).toLocaleString([], {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'});
}
function relDay(t) {
    const d = new Date(t * 1000), today = new Date();
    const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
    if (days === 0) return 'today';
    if (days === 1) return 'yesterday';
    if (days < 7) return `${days} days ago`;
    return d.toLocaleDateString([], {day: 'numeric', month: 'short', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric'});
}
const hh = (h) => `${String(h % 24).padStart(2, '0')}:00`;

async function loadStats() {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    stats = await getJson(`/api/stats?tz=${encodeURIComponent(tz)}&offset=${new Date().getTimezoneOffset()}`);
    renderStats();
}

// Columns over time, in the style of the other apps' statistics
function columnChart(el, values, labels, opts = {}) {
    if (!values.some(v => v)) { el.innerHTML = '<div class="empty small">Nothing played in this period</div>'; return; }
    const width = Math.max(280, el.clientWidth || 600), top = 16, height = opts.height || 120, slot = width / values.length;
    const max = Math.max(1, ...values);
    const bars = values.map((v, i) => {
        const x = i * slot;
        const bh = Math.max(v ? 2 : 0, v / max * (height - 4));
        const r = Math.min(4, bh, (slot - 2) / 2);
        return `<g><title>${escapeHtml(labels[i])}: ${duration(v)}</title><rect x="${x}" y="${top}" width="${slot}" height="${height}" fill="transparent"/>`
            + (v ? `<path d="M${x + 1},${top + height} v${r - bh} q0,${-r} ${r},${-r} h${Math.max(0, slot - 2 - 2 * r)} q${r},0 ${r},${r} v${bh - r} z" fill="var(--primary-color)"/>` : '')
            + '</g>';
    }).join('');
    const every = Math.ceil(values.length / Math.max(2, Math.min(opts.labels || 10, Math.floor(width / 70))));
    const ticks = labels.map((l, i) => i % every === 0
        ? `<text x="${i === 0 ? 0 : i * slot + slot / 2}" y="${top + height + 14}" font-size="11" text-anchor="${i === 0 ? 'start' : 'middle'}" fill="var(--secondary-text-color)">${escapeHtml(opts.short ? opts.short(l) : l)}</text>` : '').join('');
    el.innerHTML = `<svg viewBox="0 0 ${width} ${top + height + 18}" style="height:${top + height + 18}px"><line x1="0" y1="${top + height}" x2="${width}" y2="${top + height}" stroke="var(--divider-color)"/>${bars}${ticks}`
        + `<text x="${width}" y="11" font-size="11" text-anchor="end" fill="var(--secondary-text-color)">max ${duration(max)}</text></svg>`;
}

// Bars across: the most played games, with the name and the time next to each bar
function barList(el, rows) {
    if (!rows.length) { el.innerHTML = '<div class="empty small">No games played yet</div>'; return; }
    const width = Math.max(280, el.clientWidth || 500), label = Math.min(210, width * 0.4), value = 84, row = 28, bar = 14;
    const max = Math.max(1, ...rows.map(r => r.seconds));
    const span = width - label - value;
    const items = rows.map((r, i) => {
        const y = i * row, w = Math.max(6, r.seconds / max * span);
        const name = r.description.length > 28 ? r.description.slice(0, 27) + '…' : r.description;
        return `<g class="bar-row" data-game="${escapeHtml(r.name)}" onclick="openDetail(this.dataset.game)"><title>${escapeHtml(r.description)}: ${duration(r.seconds)}, ${r.sessions} session${r.sessions === 1 ? '' : 's'}</title>
            <rect x="0" y="${y}" width="${width}" height="${row}" fill="transparent"/>
            <text x="0" y="${y + row / 2 + 4}" font-size="12" fill="var(--primary-text-color)">${escapeHtml(name)}</text>
            <path d="M${label},${y + (row - bar) / 2} h${w - 4} q4,0 4,4 v${bar - 8} q0,4 -4,4 h${4 - w} z" fill="var(--primary-color)"/>
            <text x="${label + w + 8}" y="${y + row / 2 + 4}" font-size="11.5" font-family="IBM Plex Mono, monospace" fill="var(--secondary-text-color)">${duration(r.seconds)}</text></g>`;
    }).join('');
    el.innerHTML = `<svg viewBox="0 0 ${width} ${rows.length * row}" style="height:${rows.length * row}px">${items}</svg>`;
}

function setStatsUnit(unit) {
    statsUnit = unit;
    try { localStorage.setItem('arcade_stats_unit', unit); } catch (e) { /* none */ }
    renderStats();
}

function renderStats() {
    if (!stats || activeTab !== 'stats') return;
    const s = stats;
    const byName = Object.fromEntries(library.games.map(g => [g.name, g]));
    const playedNames = new Set(s.games.map(g => g.name));
    const never = library.games.filter(g => g.playable && !playedNames.has(g.name));
    document.getElementById('stats-empty').hidden = s.sessions > 0;
    document.getElementById('stats-main').hidden = !s.sessions;
    renderNever(never);
    if (!s.sessions) return;
    const best = s.hours.indexOf(Math.max(...s.hours));
    const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
    const kpi = (v, l, sub) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="s">${sub}</div></div>`;
    document.getElementById('stats-kpis').innerHTML = [
        kpi(duration(s.total_seconds), 'Play time', `on ${plural(s.days_played, 'day')}`),
        kpi(s.games.length, 'Games played', `of ${library.games.filter(g => g.playable).length} in the library`),
        kpi(s.sessions, 'Sessions', `average ${duration(s.total_seconds / s.sessions)}`),
        kpi(duration(s.longest.seconds), 'Longest session', `${escapeHtml(s.longest.description)}, ${relDay(s.longest.at)}`),
        kpi(plural(s.streak.current, 'day'), 'Streak', `best ${plural(s.streak.longest, 'day')} in a row`),
        kpi(hh(best), 'Favourite time', `most played ${hh(best)}–${hh(best + 1)}`),
    ].join('');
    document.querySelectorAll('#stats-unit .tab').forEach(t => t.classList.toggle('active', t.dataset.unit === statsUnit));
    const short = (d) => new Date(d + 'T12:00').toLocaleDateString([], {day: 'numeric', month: 'short'});
    if (statsUnit === 'week') {
        document.getElementById('series-title').textContent = 'Play time per week, last 12 weeks';
        columnChart(document.getElementById('series-chart'), s.weeks.map(w => w.seconds), s.weeks.map(w => `Week of ${short(w.week)}`),
            {short: l => l.replace('Week of ', '')});
    } else {
        document.getElementById('series-title').textContent = 'Play time per day, last 30 days';
        columnChart(document.getElementById('series-chart'), s.days.map(d => d.seconds), s.days.map(d => short(d.day)));
    }
    barList(document.getElementById('top-chart'), s.games.slice(0, 10));
    columnChart(document.getElementById('hours-chart'), s.hours, s.hours.map((_, h) => hh(h)), {labels: 8, height: 90});
    document.getElementById('stats-table').innerHTML = s.games.map(g => {
        const lib = byName[g.name];
        const pic = lib && lib.thumb
            ? `<img src="${escapeHtml(thumbUrl(lib))}" alt="" loading="lazy">`
            : `<span class="t-dot" style="--hue:${hue(g.name)}"></span>`;
        return `<tr data-game="${escapeHtml(g.name)}" onclick="openDetail(this.dataset.game)">
            <td><div class="t-game">${pic}<span><b>${escapeHtml(g.description)}</b><br><span class="panel-sub">${escapeHtml(g.year)} · ${escapeHtml(g.company)}</span></span></div></td>
            <td class="num">${duration(g.seconds)}</td>
            <td class="num">${g.sessions}</td>
            <td class="num">${duration(g.seconds / g.sessions)}</td>
            <td class="num">${duration(g.longest)}</td>
            <td>${relDay(g.last)}</td>
            <td>${g.has_scores ? '<span class="ok">🏆 kept</span>' : '<span class="panel-sub">—</span>'}</td></tr>`;
    }).join('');
}

function renderNever(never) {
    document.getElementById('never-panel').hidden = !never.length;
    document.getElementById('never-list').innerHTML = never.map(g =>
        `<button type="button" class="chip chip-btn" data-game="${escapeHtml(g.name)}" onclick="openDetail(this.dataset.game)">${escapeHtml(g.description)}</button>`).join('');
}

let resizeTimer = null;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderStats, 200); });

// --------------------------------------------------------------- problems
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
        <div><span>Game machines supported</span><b>${appStatus.known_games}</b></div>
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
            line.textContent = r.ok
                ? `${file.name}: added as ${r.file} (${r.description})${r.replaced && r.replaced.length ? `, replacing ${r.replaced.join(', ')}` : ''}`
                : `${file.name}: ${r.error}`;
        } catch (e) {
            line.className = 'bad';
            line.textContent = `${file.name}: ${e.message}`;
        }
    }
    load();
}

function removeRom(file) {
    if (!confirm(`Remove ${file} from the ROM folder?`)) return;
    closeDetail();
    getJson('/api/roms/' + encodeURIComponent(file), {method: 'DELETE'}).then(load).catch(e => toast(e.message, 'error'));
}
function renameRom(file, to) {
    getJson('/api/roms/' + encodeURIComponent(file) + '/rename', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({to})})
        .then(load).catch(e => toast(e.message, 'error'));
}

const drop = document.getElementById('upload');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files].filter(f => /\.(zip|7z)$/i.test(f.name))));

load();
