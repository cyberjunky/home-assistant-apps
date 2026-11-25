// Chronicle dashboard - Storage: chunks and their compression, TimescaleDB's background jobs, table sizes.

async function showStorage() {
    if (!ready()) {
        document.getElementById('storage-tiles').innerHTML = '';
        emptyChart(document.getElementById('chart-chunks'), 'Nothing recorded yet');
        ['chunks', 'jobs', 'tables', 'attribute-use', 'attribute-use-note'].forEach(id => { document.getElementById(id).innerHTML = ''; });
        return;
    }
    let data;
    try {
        data = await api('storage');
    } catch (e) {
        return handleError(e);
    }
    renderStorage(data);
    loadAttributeUse();
}

// Counting the sets per entity reads a week of rows, so it comes after the rest of the page
async function loadAttributeUse() {
    const el = document.getElementById('attribute-use');
    if (!el.innerHTML) el.innerHTML = '<div class="empty-state">Counting the attribute sets per entity…</div>';
    let use;
    try {
        use = await api('storage/attributes');
    } catch (e) {
        document.getElementById('attribute-use-note').innerHTML = '';
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
        return;
    }
    renderAttributeUse(use);
}

// "every row" for an entity that never repeats a set, "1 in 12 rows" for one that mostly does
function newSetText(e) {
    const each = e.rows / e.sets;
    return each < 1.5 ? 'every row' : `1 in ${num(each, each < 10 ? 1 : 0)} rows`;
}

function renderAttributeUse(use) {
    const period = `the last ${use.days} days`;
    document.getElementById('attribute-use-hint').textContent = `which entities fill chronicle.attributes · ${period}`;
    const el = document.getElementById('attribute-use');
    const note = document.getElementById('attribute-use-note');
    if (!use.entities.length) {
        note.innerHTML = '';
        el.innerHTML = `<div class="empty-state">No attributes recorded in ${period}</div>`;
        return;
    }
    note.innerHTML = `The table is <b>${escapeHtml(fmtBytes(use.table_bytes))}</b> and is never compressed.
        In ${period}, ${fmtCount(use.entity_count)} entities used ${fmtCount(use.sets)} different sets.
        An entity that writes a new set with nearly every row has an attribute that keeps changing, named in the last column:
        exclude that entity in the Chronicle integration, or switch off storing attributes there.`;
    el.innerHTML = `<table class="data"><thead><tr>
            <th>Entity</th><th class="n">Sets</th><th class="n">Rows</th><th>A new set</th><th class="n">Size</th><th class="n">Share</th><th>Attributes that change</th>
        </tr></thead><tbody>${use.entities.map(e => `<tr>
            <td><a class="plain-link" href="#entities/${encodeURIComponent(e.entity_id)}">${escapeHtml(e.name || e.entity_id)}</a>
                ${e.name ? `<div class="mono muted">${escapeHtml(e.entity_id)}</div>` : ''}</td>
            <td class="n">${fmtCount(e.sets)}</td>
            <td class="n muted">${fmtCount(e.rows)}</td>
            <td>${e.sets > 1 && e.rows / e.sets < 1.5 ? stateLabel('warn', newSetText(e)) : `<span class="muted">${e.sets > 1 ? escapeHtml(newSetText(e)) : 'never'}</span>`}</td>
            <td class="n">${escapeHtml(fmtBytes(e.bytes))}</td>
            <td class="n">${use.bytes ? num(e.bytes / use.bytes * 100, 1) + '%' : '–'}</td>
            <td class="mono wrap">${e.changing.length ? escapeHtml(e.changing.slice(0, 6).join(', ')) + (e.changing.length > 6 ? ` <span class="muted">+${e.changing.length - 6}</span>` : '') : '<span class="muted">–</span>'}</td>
        </tr>`).join('')}</tbody></table>`;
}

function jobConfig(job) {
    let config = {};
    try { config = JSON.parse(job.config || '{}'); } catch (e) { /* shown as is */ }
    if (job.kind === 'Compression') return `after ${escapeHtml(config.compress_after || '?')}`;
    if (job.kind === 'Retention') return `older than ${escapeHtml(config.drop_after || '?')}`;
    if (job.kind === 'Aggregate refresh') return `${escapeHtml(config.start_offset || '?')} back, up to ${escapeHtml(config.end_offset || '?')} ago`;
    return `<span class="mono">${escapeHtml(job.config || '')}</span>`;
}

function jobStatus(job) {
    if (!job.scheduled) return stateLabel('warn', 'paused');
    if (!job.runs) return stateLabel('', 'not run yet');
    if (job.status === 'Success') return stateLabel('ok', 'success');
    return stateLabel('bad', job.status || 'failed');
}

function renderStorage(d) {
    const chunks = d.chunks;
    const compressed = chunks.filter(c => c.compressed);
    const before = compressed.reduce((a, c) => a + (c.before || 0), 0);
    const after = compressed.reduce((a, c) => a + (c.after || 0), 0);
    const states = d.tables.find(t => t.name === 'chronicle.states') || {};
    const aggregateBytes = d.tables.filter(t => /states_(hourly|daily)$/.test(t.name)).reduce((a, t) => a + (t.bytes || 0), 0);
    const retention = d.jobs.find(j => j.kind === 'Retention');
    const compression = d.jobs.find(j => j.kind === 'Compression');
    document.getElementById('storage-tiles').innerHTML = [
        tile('History', bytesUnit(states.bytes), `≈ ${fmtCount(states.rows)} rows`),
        tile('Chunks', num(chunks.length), `${num(compressed.length)} compressed`),
        tile('Compression', after ? withUnit(num(before / after, 1), '×') : '–',
            after ? `saves ${fmtBytes(before - after)}` : 'no chunk compressed yet'),
        tile('Averages', d.aggregates.length ? bytesUnit(aggregateBytes) : '–',
            d.aggregates.length ? 'hourly and daily' : 'switched off in Chronicle'),
        tile('Attribute sets', fmtCount(d.attribute_sets),
            `${fmtBytes((d.tables.find(t => t.name === 'chronicle.attributes') || {}).bytes)} · each stored once`),
        tile('Kept', retention ? jobConfig(retention).replace('older than ', '') : 'for ever',
            compression ? 'compressed ' + jobConfig(compression) : ''),
    ].join('');

    // The size of every chunk at its time, compressed or not
    const ordered = chunks.slice().reverse();
    timeChart(document.getElementById('chart-chunks'), {
        x: ordered.map(c => (c.from + c.to) / 2),
        series: [
            {name: 'compressed', color: cssVar('--series-3'), kind: 'bars', values: ordered.map(c => c.compressed ? (c.after || c.bytes || 0) : null)},
            {name: 'not compressed yet', color: cssVar('--series-4'), kind: 'bars', values: ordered.map(c => c.compressed ? null : (c.bytes || 0))},
        ],
        xRange: ordered.length ? [ordered[0].from, ordered[ordered.length - 1].to] : null,
        height: 180,
        barSize: 40,
        title: 'Chunks',
        filename: 'chronicle-chunks',
        format: v => fmtBytes(v),
        formatAxis: v => fmtBytes(v),
        tipTitle: t => {
            const c = ordered.find(k => (k.from + k.to) / 2 === t);
            return c ? `${fmtDate(c.from)} – ${fmtDate(c.to)}` : fmtDate(t);
        },
        csv: () => csvFrom(['from', 'to', 'compressed', 'bytes', 'bytes_before_compression'],
            ordered.map(c => [isoTime(c.from), isoTime(c.to), c.compressed, c.compressed ? c.after : c.bytes, c.compressed ? c.before : ''])),
        empty: 'No chunks yet',
    });

    document.getElementById('chunks').innerHTML = chunks.length ? `<table class="data"><thead><tr>
            <th>From</th><th>To</th><th>Status</th><th class="n">Size</th><th class="n">Before compression</th><th class="n">Ratio</th><th>Chunk</th>
        </tr></thead><tbody>${chunks.map(c => `<tr>
            <td>${escapeHtml(fmtDate(c.from))}</td>
            <td class="muted">${escapeHtml(fmtDate(c.to))}</td>
            <td>${c.compressed ? stateLabel('ok', 'compressed') : stateLabel('warn', 'not compressed')}</td>
            <td class="n">${escapeHtml(fmtBytes(c.compressed ? c.after : c.bytes))}</td>
            <td class="n muted">${c.compressed ? escapeHtml(fmtBytes(c.before)) : '–'}</td>
            <td class="n">${c.compressed && c.after ? num(c.before / c.after, 1) + '×' : '–'}</td>
            <td class="mono muted">${escapeHtml(c.name)}</td>
        </tr>`).join('')}</tbody></table>` : '<div class="empty-state">No chunks yet</div>';

    document.getElementById('jobs').innerHTML = d.jobs.length ? `<table class="data"><thead><tr>
            <th>Job</th><th>On</th><th>Setting</th><th>Every</th><th>Last run</th><th>Status</th><th class="n">Runs</th><th class="n">Failed</th><th>Next</th>
        </tr></thead><tbody>${d.jobs.map(j => `<tr>
            <td>${escapeHtml(j.kind)}</td>
            <td class="mono">${escapeHtml(j.target)}</td>
            <td>${jobConfig(j)}</td>
            <td class="muted">${escapeHtml(fmtDuration(j.schedule))}</td>
            <td class="muted">${j.last_run ? escapeHtml(fmtAgo(j.last_run)) + (j.duration ? ` · ${escapeHtml(fmtDuration(j.duration))}` : '') : '–'}</td>
            <td>${jobStatus(j)}</td>
            <td class="n">${num(j.runs)}</td>
            <td class="n">${num(j.failures)}</td>
            <td class="muted">${escapeHtml(fmtIn(j.next))}</td>
        </tr>`).join('')}</tbody></table>` : '<div class="empty-state">No background jobs for Chronicle</div>';

    document.getElementById('tables').innerHTML = `<table class="data"><thead><tr>
            <th>Table</th><th>What</th><th class="n">Rows</th><th class="n">Size</th>
        </tr></thead><tbody>${d.tables.map(t => `<tr>
            <td class="mono">${escapeHtml(t.name)}</td>
            <td class="muted wrap">${escapeHtml(TABLE_TEXT[t.name.split('.')[1]] || '')}</td>
            <td class="n">${fmtCount(t.rows)}</td>
            <td class="n">${escapeHtml(fmtBytes(t.bytes))}</td>
        </tr>`).join('')}</tbody></table>`;
}

const TABLE_TEXT = {
    states: 'Every recorded state (a TimescaleDB hypertable)',
    entities: 'One row per entity: name, unit, device class',
    attributes: 'Every distinct set of attributes, stored once',
    states_hourly: 'Mean, minimum, maximum and last value per hour',
    states_daily: 'The same per day, in Home Assistant\'s time zone',
};

onTab('storage', showStorage);
