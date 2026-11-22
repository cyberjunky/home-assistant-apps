// Vigil dashboard - Patterns: message templates with their counts in the range, a sparkline, and
// NEW / SPIKE badges. A click shows the lines of that pattern.

let patternSort = 'count';
try { patternSort = localStorage.getItem(LS + 'pattern_sort') || 'count'; } catch (e) { /* only a convenience */ }
let patternData = null;
let patternTimer = null;

const PATTERN_SORTS = [
    {key: 'count', label: 'Most lines'},
    {key: 'new', label: 'New'},
    {key: 'spike', label: 'Spiking'},
];

// A template with its variable parts (<*>) marked; escaped first, so only our span is markup
function templateHtml(template) {
    return escapeHtml(template).replace(/&lt;\*&gt;/g, '<span class="var">&lt;*&gt;</span>');
}

// Same rules as the server: new = first seen in the window; spiking = >= 5x the preceding
// window with >= 20 lines
function patternBadges(p, from) {
    const out = [];
    if (p.first_seen && p.first_seen >= from) out.push('<span class="badge new">NEW</span>');
    const prev = p.prev_count || 0;
    if (p.count >= 20 && (prev === 0 ? !(p.first_seen >= from) : p.count / prev >= 5)) out.push('<span class="badge spike">SPIKE</span>');
    return out.join('');
}

function showPatterns() {
    buildChips('pattern-sort', PATTERN_SORTS, patternSort, key => {
        patternSort = key;
        try { localStorage.setItem(LS + 'pattern_sort', key); } catch (e) { /* only a convenience */ }
        loadPatterns();
    });
    loadPatterns();
}

async function loadPatterns() {
    const el = document.getElementById('pattern-list');
    if (!ready()) { el.innerHTML = emptyHtml('The database is not connected'); return; }
    if (!patternData) el.innerHTML = loadingHtml();
    const q = document.getElementById('pattern-q').value.trim();
    const w = rangeWindow();
    try {
        patternData = await api('patterns?' + qs({...w, sort: patternSort, q}));
    } catch (e) {
        el.innerHTML = errorHtml(e.message);
        return handleError(e);
    }
    renderPatterns(w);
}

function renderPatterns(w) {
    const el = document.getElementById('pattern-list');
    const rows = (patternData && patternData.patterns) || [];
    document.getElementById('pattern-count').textContent = `${num(rows.length)} patterns · ${rangeText()}`;
    if (!rows.length) {
        el.innerHTML = noDataYet()
            ? '<div class="empty-state">No patterns yet: they are learned from the log lines as they arrive. See <a href="#sources" onclick="switchTab(\'sources\');return false">Sources</a>.</div>'
            : emptyHtml('No patterns in this time range');
        return;
    }
    const max = Math.max(1, ...rows.map(p => p.count || 0));
    el.innerHTML = `<table class="data patterns-table"><thead><tr>
            <th class="n">Lines</th><th class="n">Before</th><th title="Lines over the time range">Trend</th><th>Template</th><th>Host · app</th><th>Last seen</th></tr></thead><tbody>
        ${rows.map((p, i) => {
            const prev = p.prev_count || 0;
            const change = prev ? p.count / prev : null;
            return `<tr class="link" data-i="${i}" title="Show the lines of pattern #${Number(p.id)}">
                <td class="n"><b>${fmtCount(p.count)}</b><div class="mini-bar"><i style="width:${((p.count || 0) / max * 100).toFixed(1)}%"></i></div></td>
                <td class="n muted">${fmtCount(prev)}${change !== null ? `<small class="${change >= 5 ? 'txt-bad' : change < 0.5 ? 'txt-ok' : ''}">${change >= 10 ? '×' + num(change) : '×' + num(change, 1)}</small>` : ''}</td>
                <td>${sparkline(p.spark, {width: 110, height: 24, color: patternBadges(p, w.from).includes('spike') ? 'var(--danger-color)' : 'var(--series-1)'})}</td>
                <td class="wrap template-cell">${patternBadges(p, w.from)}<span class="template">${templateHtml(p.template)}</span>
                    ${p.sample ? `<small class="sample" title="An example line">${escapeHtml(p.sample)}</small>` : ''}</td>
                <td class="muted">${escapeHtml(p.host || '–')}${p.app ? ' · ' + escapeHtml(p.app) : ''}</td>
                <td class="muted" title="first seen ${escapeHtml(p.first_seen ? fmtDateTime(p.first_seen) : '–')}">${escapeHtml(fmtAgo(p.last_seen))}</td>
            </tr>`;
        }).join('')}</tbody></table>`;
    el.onclick = (e) => {
        const tr = e.target.closest('[data-i]');
        if (tr) openLogs(`pattern:${Number(rows[Number(tr.dataset.i)].id)}`);
    };
}

let patternSearchTimer = null;
document.getElementById('pattern-q').addEventListener('input', () => {
    clearTimeout(patternSearchTimer);
    patternSearchTimer = setTimeout(loadPatterns, 300);
});

onTab('patterns', () => {
    showPatterns();
    clearInterval(patternTimer);
    patternTimer = setInterval(() => {
        if (activeTab === 'patterns' && timeRange.preset && document.visibilityState === 'visible') loadPatterns();
    }, 60000);
});
onRange('patterns', loadPatterns);
onLeave('patterns', () => clearInterval(patternTimer));
