// Chronicle dashboard - Databases: every database as a tree (schemas, tables, hypertables,
// views, continuous aggregates, functions). A database opens its dashboard, a table its
// columns, indexes, statistics, TimescaleDB details and rows, with maintenance buttons.

const ROWS_PAGE = 100;
const dbs = {
    list: [],            // the databases
    trees: {},           // database -> its tree, loaded when opened
    open: new Set(),     // tree nodes that are open: "db", "db/schema"
    selected: null,      // {database} or {database, schema, name}
    relationTab: 'data',
    rows: null,          // the rows page shown: {offset, order, desc, data}
    chronicle: 'homeassistant',
};

async function showDatabases() {
    const [, database, schema, name] = window.location.hash.slice(1).split('/').map(decodeURIComponent);
    await loadDatabaseList();
    if (database && dbs.list.some(d => d.name === database)) {
        dbs.selected = schema && name ? {database, schema, name} : {database};
        dbs.open.add(database);
        if (schema) dbs.open.add(`${database}/${schema}`);
    }
    if (!dbs.selected) dbs.selected = {database: dbs.chronicle};
    dbs.open.add(dbs.selected.database);
    await Promise.all([...dbs.open].filter(k => !k.includes('/')).map(loadTree));
    renderTree();
    renderSelected();
}

async function loadDatabaseList() {
    try {
        const data = await api('databases');
        dbs.list = data.databases;
        dbs.chronicle = data.chronicle;
    } catch (e) {
        handleError(e);
    }
}

async function loadTree(database, force) {
    if (dbs.trees[database] && !force) return;
    try {
        dbs.trees[database] = await api(`tree?database=${encodeURIComponent(database)}&system=${document.getElementById('tree-system').checked ? 1 : 0}`);
    } catch (e) {
        dbs.trees[database] = {error: e.message, schemas: []};
    }
}

// ---------------------------------------------------------------------
// The tree
// ---------------------------------------------------------------------
const KIND_ICONS = {
    table: '▦', hypertable: '⧉', view: '◫', 'materialized view': '◫', 'continuous aggregate': '∑',
    'foreign table': '▤', function: 'ƒ', procedure: 'ƒ', schema: '▸', database: '⛁',
};

function treeKey(...parts) { return parts.join('/'); }

function renderTree() {
    const filter = document.getElementById('tree-search').value.trim().toLowerCase();
    const sel = dbs.selected || {};
    const html = dbs.list.map(d => {
        const open = dbs.open.has(d.name) || Boolean(filter);
        const tree = dbs.trees[d.name];
        const active = sel.database === d.name && !sel.name;
        let children = '';
        if (open && tree) {
            if (tree.error) children = `<div class="tree-note sb-bad">${escapeHtml(tree.error)}</div>`;
            else children = tree.schemas.map(s => schemaNode(d.name, s, filter)).join('') || '<div class="tree-note">No schemas</div>';
        } else if (open) {
            children = '<div class="tree-note">Loading…</div>';
        }
        return `<div class="tree-node">
            <button type="button" class="tree-row db${active ? ' active' : ''}" data-db="${escapeHtml(d.name)}" aria-expanded="${open}">
                <span class="tree-toggle" data-toggle="${escapeHtml(d.name)}">${open ? '▾' : '▸'}</span>
                <span class="tree-icon">${KIND_ICONS.database}</span>
                <span class="tree-name">${escapeHtml(d.name)}</span>
                ${d.chronicle ? '<span class="badge">Chronicle</span>' : ''}
                <span class="tree-meta">${escapeHtml(fmtBytes(d.size))}</span>
            </button>
            ${open ? `<div class="tree-children">${children}</div>` : ''}
        </div>`;
    }).join('');
    document.getElementById('db-tree').innerHTML = html || '<div class="empty-state">No databases</div>';
}

function schemaNode(database, schema, filter) {
    const key = treeKey(database, schema.name);
    const matches = o => !filter || o.name.toLowerCase().includes(filter);
    const objects = schema.objects.filter(matches);
    const functions = schema.functions.filter(matches);
    if (filter && !objects.length && !functions.length) return '';
    const open = dbs.open.has(key) || Boolean(filter);
    const sel = dbs.selected || {};
    const groups = [
        ['Tables', objects.filter(o => ['table', 'hypertable', 'foreign table'].includes(o.kind))],
        ['Views', objects.filter(o => ['view', 'materialized view'].includes(o.kind))],
        ['Continuous aggregates', objects.filter(o => o.kind === 'continuous aggregate')],
    ];
    let body = '';
    if (open) {
        body = groups.filter(([, items]) => items.length).map(([title, items]) => `
            <div class="tree-group">${escapeHtml(title)} <span class="muted">${num(items.length)}</span></div>
            ${items.map(o => `<button type="button" class="tree-row leaf${sel.database === database && sel.schema === schema.name && sel.name === o.name ? ' active' : ''}"
                data-db="${escapeHtml(database)}" data-schema="${escapeHtml(schema.name)}" data-name="${escapeHtml(o.name)}" title="${escapeHtml(o.kind)}">
                <span class="tree-icon">${KIND_ICONS[o.kind] || '·'}</span>
                <span class="tree-name">${escapeHtml(o.name)}</span>
                <span class="tree-meta">${o.kind === 'view' ? '' : escapeHtml(fmtBytes(o.size))}</span>
            </button>`).join('')}`).join('');
        if (functions.length) {
            body += `<div class="tree-group">Functions <span class="muted">${num(functions.length)}</span></div>`
                + functions.map(f => `<div class="tree-row leaf static" title="${escapeHtml(f.kind)} ${escapeHtml(f.name)}(${escapeHtml(f.arguments)})">
                    <span class="tree-icon">${KIND_ICONS[f.kind]}</span><span class="tree-name">${escapeHtml(f.name)}<span class="muted">(${escapeHtml(f.arguments)})</span></span></div>`).join('');
        }
        if (!body) body = '<div class="tree-note">Empty</div>';
    }
    return `<div class="tree-node">
        <button type="button" class="tree-row schema" data-toggle="${escapeHtml(key)}" aria-expanded="${open}">
            <span class="tree-toggle">${open ? '▾' : '▸'}</span>
            <span class="tree-name">${escapeHtml(schema.name)}</span>
            ${schema.system ? '<span class="badge muted-badge">system</span>' : ''}
            <span class="tree-meta">${num(schema.objects.length)}</span>
        </button>
        ${open ? `<div class="tree-children">${body}</div>` : ''}
    </div>`;
}

async function toggleNode(key) {
    if (dbs.open.has(key)) {
        dbs.open.delete(key);
    } else {
        dbs.open.add(key);
        if (!key.includes('/')) {
            renderTree();
            await loadTree(key);
        }
    }
    renderTree();
}

function selectNode(selected) {
    dbs.selected = selected;
    dbs.rows = null;
    const parts = [selected.database, selected.schema, selected.name].filter(Boolean).map(encodeURIComponent);
    history.replaceState(null, '', '#databases/' + parts.join('/'));
    renderTree();
    renderSelected();
}

function renderSelected() {
    const sel = dbs.selected;
    if (!sel) return;
    if (sel.name) loadRelation(sel);
    else loadDashboard(sel.database);
}

// ---------------------------------------------------------------------
// A database's dashboard
// ---------------------------------------------------------------------
async function loadDashboard(database) {
    const el = document.getElementById('db-detail');
    if (!el.dataset.database || el.dataset.database !== database) el.innerHTML = '<div class="empty-state">Loading…</div>';
    el.dataset.database = database;
    let d;
    try {
        d = await api(`dashboard?database=${encodeURIComponent(database)}`);
    } catch (e) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
        return;
    }
    if (dbs.selected.database !== database || dbs.selected.name) return;
    const s = d.stat;
    const info = dbs.list.find(x => x.name === database) || {};
    const c = d.compression;
    const saved = c && c.before ? c.before - c.after : null;
    const actions = [
        `<button type="button" class="btn" onclick="openInSql('${jsString(database)}', '')">Open in SQL</button>`,
        `<button type="button" class="btn" onclick="backupDatabase('${jsString(database)}')">Back up</button>`,
        `<button type="button" class="btn" onclick="maintainDatabase('${jsString(database)}', 'vacuum')">VACUUM ANALYZE</button>`,
        !d.chronicle && database !== 'postgres'
            ? `<button type="button" class="btn" onclick="useForChronicle('${jsString(database)}')">Use for Chronicle…</button>` : '',
        !d.chronicle && database !== 'postgres'
            ? `<button type="button" class="btn btn-danger" onclick="dropDatabase('${jsString(database)}')">Remove…</button>` : '',
    ].join('');
    el.innerHTML = `
        <div class="entity-head">
            <div><h2>${escapeHtml(database)} ${d.chronicle ? '<span class="badge">Chronicle</span>' : ''}</h2>
                <div class="id">${escapeHtml(info.owner ? 'owner ' + info.owner : '')}${info.encoding ? ' · ' + escapeHtml(info.encoding) : ''}${d.timescaledb ? ' · TimescaleDB ' + escapeHtml(d.timescaledb) : ''}</div></div>
            <div class="head-actions">${actions}</div>
        </div>
        <div class="tiles kpi-tiles">
            ${tile('Size', bytesUnit(s.size), `${num(d.counts.tables)} tables · ${num(d.counts.indexes)} indexes`)}
            ${tile('Connections', num(s.connections), `${num(d.counts.schemas)} schemas · ${num(d.counts.views)} views`)}
            ${tile('Cache hits', d.cache_hit === null ? '–' : withUnit(num(d.cache_hit * 100, 1), '%'), 'of the blocks read came from memory')}
            ${tile('Transactions', fmtCount(s.commits), `${fmtCount(s.rollbacks)} rolled back`)}
            ${tile('Rows written', fmtCount((s.tup_inserted || 0) + (s.tup_updated || 0) + (s.tup_deleted || 0)), `${fmtCount(s.tup_inserted)} inserted · ${fmtCount(s.tup_deleted)} deleted`)}
            ${d.timescaledb ? tile('Compression', saved ? withUnit(num(c.before / c.after, 1), '×') : 'none yet', saved ? `saves ${fmtBytes(saved)}` : `${num(d.counts.hypertables)} hypertables`) : tile('Deadlocks', num(s.deadlocks), `${num(s.temp_files)} temp files`)}
            ${d.jobs ? tile('Background jobs', num(d.jobs.jobs), d.jobs.failing ? stateLabel('bad', `${d.jobs.failing} failing`) : 'all fine', d.jobs.failing ? 'warn' : '') : ''}
        </div>
        <div class="stats-grid">
            <div class="panel"><div class="panel-body"><div class="chart-title"><span>Transactions</span><span class="hint">per minute, since the app started</span></div><div id="dash-xacts" class="chart"></div></div></div>
            <div class="panel"><div class="panel-body"><div class="chart-title"><span>Rows written</span><span class="hint">per minute</span></div><div id="dash-rows" class="chart"></div></div></div>
        </div>
        <div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Largest tables</span><span class="hint">a hypertable with all its chunks, a continuous aggregate with its data</span></div>
            <div id="dash-largest" class="rank"></div>
        </div></div>
        <div class="panel" style="margin-top:14px"><div class="panel-body">
            <div class="chart-title"><span>Table health</span><span class="hint">dead rows are cleaned up by (auto)vacuum; many sequential scans on a big table can mean a missing index</span></div>
            <div class="table-wrap">${healthTable(database, d.tables)}</div>
        </div></div>
        <p class="panel-sub" style="margin-top:10px">Counters since ${s.stats_reset ? escapeHtml(fmtDateTime(Date.parse(s.stats_reset) / 1000)) : 'the cluster was created'}. The charts collect while the app runs and start again when it restarts.</p>`;

    const series = d.series;
    const colors = [1, 2, 3, 4].map(i => cssVar(`--series-${i}`));
    timeChart(document.getElementById('dash-xacts'), {
        x: series.t, height: 180, compact: true, title: `Transactions of ${database}`, filename: `${database}-transactions`,
        series: [{name: 'transactions', color: colors[0], kind: 'area', values: series.xacts}],
        format: v => `${fmtValue(v)}/min`, empty: 'Collecting: a point every minute',
    });
    timeChart(document.getElementById('dash-rows'), {
        x: series.t, height: 180, compact: true, title: `Rows written in ${database}`, filename: `${database}-rows`,
        series: [
            {name: 'inserted', color: colors[0], values: series.inserted},
            {name: 'updated', color: colors[1], values: series.updated},
            {name: 'deleted', color: colors[2], values: series.deleted},
        ],
        format: v => `${fmtValue(v)}/min`, empty: 'Collecting: a point every minute',
    });
    const max = Math.max(1, ...d.tables.map(t => t.total || 0));
    document.getElementById('dash-largest').innerHTML = d.tables.slice(0, 12).map(t => `
        <div class="rank-row link" data-open-db="${escapeHtml(database)}" data-open-schema="${escapeHtml(t.schema)}" data-open-name="${escapeHtml(t.name)}" title="Open ${escapeHtml(t.schema)}.${escapeHtml(t.name)}">
            <div class="name"><b>${escapeHtml(t.schema)}.${escapeHtml(t.name)}</b><small>${escapeHtml(t.kind)}${t.indexes ? ` · indexes ${escapeHtml(fmtBytes(t.indexes))}` : ''}</small></div>
            <div class="n">${escapeHtml(fmtBytes(t.total))}${t.rows ? `<small>≈ ${escapeHtml(fmtCount(t.rows))} rows</small>` : ''}</div>
            <div class="bar-track"><i style="width:${(100 * (t.total || 0) / max).toFixed(1)}%"></i></div>
        </div>`).join('') || '<div class="empty-state">No tables</div>';
}

function healthTable(database, tables) {
    const rows = tables.filter(t => t.kind !== 'continuous aggregate');
    if (!rows.length) return '<div class="empty-state">No tables</div>';
    return `<table class="data"><thead><tr><th>Table</th><th class="n">Rows</th><th class="n">Dead rows</th><th>Last vacuum</th><th class="n">Seq. scans</th><th class="n">Index scans</th><th></th></tr></thead><tbody>
        ${rows.map(t => {
            const dead = t.dead || 0;
            const share = t.live ? dead / (t.live + dead) : 0;
            return `<tr>
                <td><a href="#" onclick="selectNode({database: '${jsString(database)}', schema: '${jsString(t.schema)}', name: '${jsString(t.name)}'}); return false">${escapeHtml(t.schema)}.${escapeHtml(t.name)}</a> <small class="muted">${escapeHtml(t.kind === 'table' ? '' : t.kind)}</small></td>
                <td class="n">${escapeHtml(fmtCount(t.rows))}</td>
                <td class="n">${share > 0.2 && dead > 1000 ? stateLabel('warn', fmtCount(dead)) : escapeHtml(fmtCount(dead))}</td>
                <td>${t.last_vacuum ? escapeHtml(fmtAgo(Date.parse(t.last_vacuum) / 1000)) : '<span class="muted">never</span>'}</td>
                <td class="n">${escapeHtml(fmtCount(t.seq_scan))}</td>
                <td class="n">${escapeHtml(fmtCount(t.idx_scan))}</td>
                <td><button type="button" class="btn btn-small" onclick="maintainTable('${jsString(database)}', '${jsString(t.schema)}', '${jsString(t.name)}', 'vacuum')">VACUUM</button></td>
            </tr>`;
        }).join('')}</tbody></table>`;
}

// ---------------------------------------------------------------------
// A table or view
// ---------------------------------------------------------------------
async function loadRelation(sel) {
    const el = document.getElementById('db-detail');
    el.dataset.database = '';
    el.innerHTML = '<div class="empty-state">Loading…</div>';
    let r;
    try {
        r = await api(`relation?database=${encodeURIComponent(sel.database)}&schema=${encodeURIComponent(sel.schema)}&name=${encodeURIComponent(sel.name)}`);
    } catch (e) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
        return;
    }
    if (dbs.selected !== sel) return;
    dbs.relation = r;
    const ts = r.timescale;
    const tabs = [['data', 'Data'], ['columns', `Columns (${r.columns.length})`], ['indexes', `Indexes (${r.indexes.length})`], ['stats', 'Statistics']];
    if (r.definition || (ts && ts.definition)) tabs.push(['definition', 'Definition']);
    if (ts) tabs.push(['timescale', 'TimescaleDB']);
    if (!tabs.some(([k]) => k === dbs.relationTab)) dbs.relationTab = 'data';
    const q = `'${jsString(sel.database)}', '${jsString(sel.schema)}', '${jsString(sel.name)}'`;
    const isTable = ['table', 'hypertable', 'materialized view'].includes(r.kind);
    const actions = [
        `<button type="button" class="btn" onclick="openInSql('${jsString(sel.database)}', selectStatement())">Query in SQL</button>`,
        isTable ? `<button type="button" class="btn" onclick="maintainTable(${q}, 'vacuum')">VACUUM</button>` : '',
        isTable ? `<button type="button" class="btn" onclick="maintainTable(${q}, 'analyze')">ANALYZE</button>` : '',
        isTable && r.kind !== 'hypertable' ? `<button type="button" class="btn" onclick="maintainTable(${q}, 'reindex')">REINDEX</button>` : '',
        r.kind === 'hypertable' && ts.compression !== false ? `<button type="button" class="btn" onclick="compressNow(${q})">Compress now…</button>` : '',
        r.kind === 'continuous aggregate' ? `<button type="button" class="btn" onclick="maintainTable(${q}, 'refresh')">Refresh</button>` : '',
        r.kind === 'hypertable' ? `<button type="button" class="btn btn-danger" onclick="dropChunks(${q})">Remove old history…</button>` : '',
    ].join('');
    const size = r.sizes.total;
    el.innerHTML = `
        <div class="entity-head">
            <div><h2>${escapeHtml(r.schema)}.${escapeHtml(r.name)} <span class="badge">${escapeHtml(r.kind)}</span></h2>
                <div class="id">${escapeHtml(sel.database)} · owner ${escapeHtml(r.owner)}${size !== null ? ' · ' + escapeHtml(fmtBytes(size)) : ''}${r.sizes.rows ? ' · ≈ ' + escapeHtml(fmtCount(r.sizes.rows)) + ' rows' : ''}</div>
                ${r.comment ? `<div class="panel-sub">${escapeHtml(r.comment)}</div>` : ''}</div>
            <div class="head-actions">${actions}</div>
        </div>
        <div class="tabs inline-tabs relation-tabs" id="relation-tabs">${tabs.map(([k, label]) =>
            `<button type="button" class="tab${k === dbs.relationTab ? ' active' : ''}" data-rtab="${k}">${escapeHtml(label)}</button>`).join('')}</div>
        <div class="panel" style="margin-top:10px"><div id="relation-body" class="table-wrap scroll tall"></div></div>`;
    renderRelationTab();
}

function selectStatement() {
    const r = dbs.relation;
    const quote = n => /^[a-z_][a-z0-9_]*$/.test(n) ? n : `"${n.replace(/"/g, '""')}"`;
    const time = r.columns.find(c => c.type.startsWith('timestamp'));
    return `SELECT *\nFROM ${quote(r.schema)}.${quote(r.name)}${time ? `\nORDER BY ${quote(time.name)} DESC` : ''}\nLIMIT 100`;
}

function renderRelationTab() {
    const r = dbs.relation;
    const el = document.getElementById('relation-body');
    document.querySelectorAll('#relation-tabs .tab').forEach(t => t.classList.toggle('active', t.dataset.rtab === dbs.relationTab));
    const tab = dbs.relationTab;
    if (tab === 'data') return loadRows();
    if (tab === 'columns') {
        el.innerHTML = `<table class="data"><thead><tr><th>#</th><th>Column</th><th>Type</th><th>Null</th><th>Default</th><th>Comment</th></tr></thead><tbody>
            ${r.columns.map((c, i) => `<tr><td class="muted">${i + 1}</td><td><b>${escapeHtml(c.name)}</b></td><td class="mono">${escapeHtml(c.type)}</td>
                <td>${c.not_null ? 'not null' : '<span class="muted">null</span>'}</td>
                <td class="mono wrap">${escapeHtml(c.identity ? `identity (${c.identity === 'a' ? 'always' : 'by default'})` : c.generated ? `generated: ${c.default || ''}` : c.default || '')}</td>
                <td>${escapeHtml(c.comment || '')}</td></tr>`).join('')}</tbody></table>`;
    } else if (tab === 'indexes') {
        el.innerHTML = (r.indexes.length ? `<table class="data"><thead><tr><th>Index</th><th>Definition</th><th class="n">Size</th><th class="n">Scans</th></tr></thead><tbody>
            ${r.indexes.map(i => `<tr><td><b>${escapeHtml(i.name)}</b> ${i.primary ? '<span class="badge">primary</span>' : i.unique ? '<span class="badge">unique</span>' : ''}</td>
                <td class="mono wrap">${escapeHtml(i.definition)}</td><td class="n">${escapeHtml(fmtBytes(i.size))}</td><td class="n">${escapeHtml(fmtCount(i.scans))}</td></tr>`).join('')}</tbody></table>`
            : '<div class="empty-state">No indexes</div>')
            + (r.constraints.length ? `<table class="data" style="margin-top:12px"><thead><tr><th>Constraint</th><th>Definition</th></tr></thead><tbody>
                ${r.constraints.map(c => `<tr><td><b>${escapeHtml(c.name)}</b></td><td class="mono wrap">${escapeHtml(c.definition)}</td></tr>`).join('')}</tbody></table>` : '');
    } else if (tab === 'stats') {
        const s = r.stats || {};
        const when = v => v ? escapeHtml(fmtDateTime(Date.parse(v) / 1000)) : '<span class="muted">never</span>';
        el.innerHTML = `<div class="info-grid" style="padding:12px">
            <div>${infoRow('Table', escapeHtml(fmtBytes(r.sizes.table)))}${infoRow('Indexes', escapeHtml(fmtBytes(r.sizes.indexes)))}${infoRow('Total', escapeHtml(fmtBytes(r.sizes.total)))}
                ${infoRow('Live rows', escapeHtml(fmtCount(s.live)))}${infoRow('Dead rows', escapeHtml(fmtCount(s.dead)))}</div>
            <div>${infoRow('Inserted', escapeHtml(fmtCount(s.inserted)))}${infoRow('Updated', escapeHtml(fmtCount(s.updated)))}${infoRow('Deleted', escapeHtml(fmtCount(s.deleted)))}
                ${infoRow('Sequential scans', escapeHtml(fmtCount(s.seq_scan)))}${infoRow('Index scans', escapeHtml(fmtCount(s.idx_scan)))}</div>
            <div>${infoRow('Last vacuum', when(s.last_vacuum))}${infoRow('Last autovacuum', when(s.last_autovacuum))}${infoRow('Last analyze', when(s.last_analyze))}${infoRow('Last autoanalyze', when(s.last_autoanalyze))}</div>
        </div>${r.kind === 'hypertable' ? '<p class="panel-sub" style="padding:0 12px 12px">The counters of a hypertable are of its parent table; its chunks keep their own.</p>' : ''}`;
    } else if (tab === 'definition') {
        el.innerHTML = `<pre class="code-block">${escapeHtml((r.timescale && r.timescale.definition) || r.definition)}</pre>`;
    } else if (tab === 'timescale') {
        const t = r.timescale;
        el.innerHTML = `<div style="padding:12px">
            ${infoRow('Chunks', `${num(t.chunks)} · ${num(t.compressed)} compressed`)}
            ${infoRow('Time range', t.oldest ? `${escapeHtml(fmtDate(Date.parse(t.oldest) / 1000))} – ${escapeHtml(fmtDate(Date.parse(t.newest) / 1000))}` : '–')}
            ${infoRow('Size', escapeHtml(fmtBytes(t.size)))}
            ${t.compression_before ? infoRow('Compression', `${escapeHtml(fmtBytes(t.compression_before))} → ${escapeHtml(fmtBytes(t.compression_after))} (${num(t.compression_before / t.compression_after, 1)}×)`) : ''}
            ${t.dimensions.map(d => infoRow(`Partitioned by`, `${escapeHtml(d.column_name)} · ${escapeHtml(d.time_interval || d.integer_interval || `${d.num_partitions} partitions`)}`)).join('')}
            ${t.continuous_aggregate ? infoRow('Real-time', t.materialized_only ? 'no: only what was refreshed' : 'yes: the newest rows are added when read') : ''}
        </div>
        ${t.jobs.length ? `<table class="data"><thead><tr><th>Job</th><th>Every</th><th>Settings</th><th>Last run</th><th>Next</th></tr></thead><tbody>
            ${t.jobs.map(j => `<tr><td><b>${escapeHtml(j.proc_name)}</b> <span class="muted">#${j.job_id}</span></td><td>${escapeHtml(j.schedule_interval)}</td>
                <td class="mono wrap">${escapeHtml(j.config || '')}</td>
                <td>${j.last_run_status === 'Failed' ? stateLabel('bad', 'failed') : j.last_successful_finish ? escapeHtml(fmtAgo(Date.parse(j.last_successful_finish) / 1000)) : '–'}${j.total_failures ? ` <small class="muted">${num(j.total_failures)} failures</small>` : ''}</td>
                <td>${j.next_start ? escapeHtml(fmtIn(Date.parse(j.next_start) / 1000)) : '–'}</td></tr>`).join('')}</tbody></table>` : ''}`;
    }
}

async function loadRows() {
    const sel = dbs.selected;
    const el = document.getElementById('relation-body');
    if (!dbs.rows) {
        // History reads newest first: a table with a time column starts ordered by it
        const r = dbs.relation;
        const time = r && ['hypertable', 'table'].includes(r.kind) ? r.columns.find(c => c.type.startsWith('timestamp')) : null;
        dbs.rows = {offset: 0, order: time ? time.name : null, desc: Boolean(time), data: null};
    }
    const state = dbs.rows;
    const query = `rows?database=${encodeURIComponent(sel.database)}&schema=${encodeURIComponent(sel.schema)}&name=${encodeURIComponent(sel.name)}`
        + `&offset=${state.offset}&limit=${ROWS_PAGE}${state.order ? `&order=${encodeURIComponent(state.order)}&desc=${state.desc ? 1 : 0}` : ''}`;
    if (!state.data) el.innerHTML = '<div class="empty-state">Loading…</div>';
    try {
        state.data = await api(query);
    } catch (e) {
        el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
        return;
    }
    if (dbs.selected !== sel || dbs.relationTab !== 'data') return;
    const d = state.data;
    const numeric = d.columns.map(c => NUMBER_TYPES.includes(c.type));
    const head = d.columns.map((c, i) => `<th class="${numeric[i] ? 'n ' : ''}sortable" data-order="${escapeHtml(c.name)}" title="${escapeHtml(c.type)} · click to sort">${escapeHtml(c.name)}${state.order === c.name ? (state.desc ? ' ▾' : ' ▴') : ''}</th>`).join('');
    const body = d.rows.map(row => `<tr>${row.map((v, i) =>
        `<td class="${numeric[i] ? 'n' : v !== null && typeof v === 'object' ? 'mono wrap' : ''}">${v === null ? '<span class="muted">NULL</span>' : escapeHtml(cellText(v))}</td>`).join('')}</tr>`).join('');
    el.innerHTML = `<div class="rows-pager">
            <button type="button" class="btn btn-small" data-page="-1"${state.offset ? '' : ' disabled'}>‹ Previous</button>
            <span class="range-text">rows ${num(state.offset + 1)}–${num(state.offset + d.rows.length)}${state.order ? ` · by ${escapeHtml(state.order)}` : ''}</span>
            <button type="button" class="btn btn-small" data-page="1"${d.more ? '' : ' disabled'}>Next ›</button>
        </div>
        ${d.rows.length ? `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` : '<div class="empty-state">No rows</div>'}`;
}

// ---------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------
// A name inside a '…' JavaScript string in an onclick attribute
function jsString(text) {
    // JavaScript first (backslash, quote, line breaks), then HTML: the attribute is decoded
    // before the script runs, so \' stays an escaped quote
    const js = String(text).replace(/\\/g, '\\\\').replace(/'/g, "\\'")
        .replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
    return escapeHtml(js);
}

function openInSql(database, statement) {
    switchTab('query');
    const select = document.getElementById('sql-database');
    if (!sql.inTransaction) select.value = database;
    if (statement) fillEditor(statement);
}

async function startMaintenance(body, title) {
    const job = await api('maintenance', body);
    toast(`${title}: started (see Maintenance)`);
    watchTask(job.id, () => { if (activeTab === 'databases') renderSelected(); });
}

function maintainTable(database, schema, name, action) {
    const label = {vacuum: 'VACUUM ANALYZE', analyze: 'ANALYZE', reindex: 'REINDEX', refresh: 'Refresh'}[action];
    const notes = {
        vacuum: 'Cleans up dead rows so their space is used again, and updates the statistics. The table stays usable meanwhile.',
        analyze: 'Updates the statistics the query planner uses. Quick and harmless.',
        reindex: 'Rebuilds the indexes. Writing to the table waits until it is done.',
        refresh: 'Recalculates the whole continuous aggregate from the history. Can take long on a large history; the averages stay readable meanwhile.',
    };
    const full = action === 'vacuum' ? `<label class="action-check"><input type="checkbox" id="vacuum-full"> VACUUM FULL: also gives the space back to the disk (rewrites the table; it is locked meanwhile)</label>` : '';
    actionModal(`${label} ${schema}.${name}`, `<p>${notes[action]}</p>${full}`, label, false, async () => {
        const options = action === 'vacuum' ? {full: document.getElementById('vacuum-full').checked} : {};
        await startMaintenance({action, database, schema, name, options}, `${label} ${schema}.${name}`);
    });
}

function maintainDatabase(database, action) {
    actionModal(`VACUUM ANALYZE ${database}`, '<p>Cleans up dead rows and updates the statistics of every table in the database. Everything stays usable meanwhile.</p>', 'VACUUM ANALYZE', false, async () => {
        await startMaintenance({action, database}, `VACUUM ANALYZE ${database}`);
    });
}

function compressNow(database, schema, name) {
    actionModal(`Compress ${schema}.${name}`, `
        <p>Compresses the chunks older than this now, instead of waiting for the compression policy. Compressed chunks take a fraction of the space; they can still be read, and written to more slowly.</p>
        <label class="form-row"><span>Older than</span><input class="input" type="number" id="compress-days" value="7" min="0" max="36500"> <span>days</span></label>`,
        'Compress', false, async () => {
            await startMaintenance({action: 'compress', database, schema, name, options: {older_than_days: Number(document.getElementById('compress-days').value)}}, `Compress ${schema}.${name}`);
        });
}

function dropChunks(database, schema, name) {
    actionModal(`Remove old history of ${schema}.${name}`, `
        <p>Removes every chunk older than this: <b>that history is gone</b> (unless it is in a backup). The averages of the Chronicle integration keep what they already have.</p>
        <label class="form-row"><span>Older than</span><input class="input" type="number" id="drop-days" value="365" min="1" max="36500"> <span>days</span></label>
        <label class="form-row"><span>Type <b>${escapeHtml(name)}</b> to confirm</span><input class="input" id="drop-confirm" autocomplete="off" spellcheck="false"></label>`,
        'Remove', true, async () => {
            await startMaintenance({action: 'drop_chunks', database, schema, name, options: {
                older_than_days: Number(document.getElementById('drop-days').value),
                confirm: document.getElementById('drop-confirm').value.trim(),
            }}, `Remove old history of ${schema}.${name}`);
        });
}

function backupDatabase(database) {
    actionModal(`Back up ${database}`, `<p>Writes a dump of the database to <code>/share/chronicle</code>. The Backups page lists it, to download or restore.</p>`, 'Back up', false, async () => {
        const task = await api('backups/dump', {database});
        toast(`Backing up ${database} (see Backups)`);
        watchTask(task.id);
    });
}

function useForChronicle(database) {
    actionModal(`Use ${database} for Chronicle`, `
        <p>The Chronicle integration records into <b>${escapeHtml(database)}</b> from now on: it is renamed to <b>${escapeHtml(dbs.chronicle)}</b>, and the current <b>${escapeHtml(dbs.chronicle)}</b> is kept under a new name (homeassistant_before_…).</p>
        <p class="panel-sub">The integration reconnects by itself; states it records during the switch wait in its queue.</p>`,
        'Use for Chronicle', true, async () => {
            const task = await api('databases/use', {database});
            toast('Switching databases (see Backups)');
            watchTask(task.id, async () => { dbs.trees = {}; dbs.selected = {database: dbs.chronicle}; await showDatabases(); loadStatus(); });
        });
}

function dropDatabase(database) {
    actionModal(`Remove ${database}`, `
        <p>Removes the database <b>${escapeHtml(database)}</b> and everything in it. This can't be undone, except from a backup.</p>
        <label class="form-row"><span>Type <b>${escapeHtml(database)}</b> to confirm</span><input class="input" id="dropdb-confirm" autocomplete="off" spellcheck="false"></label>`,
        'Remove', true, async () => {
            if (document.getElementById('dropdb-confirm').value.trim() !== database) throw new Error('Type the name to confirm');
            const task = await api('databases/drop', {database});
            watchTask(task.id, async () => { delete dbs.trees[database]; dbs.selected = {database: dbs.chronicle}; await showDatabases(); });
        });
}

// Polls a task until it is done, then calls back (and says how it went)
function watchTask(id, done) {
    const poll = async () => {
        let data;
        try { data = await api('tasks'); } catch (e) { return setTimeout(poll, 3000); }
        const task = data.tasks.find(t => t.id === id);
        if (!task || task.state === 'running') return setTimeout(poll, 1500);
        if (task.state === 'done') toast(`${task.title}: done`);
        else toast(`${task.title}: ${task.error || 'failed'}`, 'error');
        if (activeTab === 'maintenance') showMaintenance();
        if (activeTab === 'backups') showBackups();
        if (done) done(task);
    };
    setTimeout(poll, 1000);
}

// ---------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------
document.getElementById('db-tree').addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-toggle]');
    const row = e.target.closest('.tree-row');
    if (!row) return;
    if (row.classList.contains('schema')) return toggleNode(row.dataset.toggle);
    if (toggle && row.classList.contains('db')) return toggleNode(toggle.dataset.toggle);
    if (row.dataset.name) return selectNode({database: row.dataset.db, schema: row.dataset.schema, name: row.dataset.name});
    if (row.dataset.db) {
        dbs.open.add(row.dataset.db);
        loadTree(row.dataset.db).then(renderTree);
        selectNode({database: row.dataset.db});
    }
});
document.getElementById('tree-search').addEventListener('input', async () => {
    // Searching opens every database's tree
    if (document.getElementById('tree-search').value.trim()) await Promise.all(dbs.list.map(d => loadTree(d.name)));
    renderTree();
});
document.getElementById('tree-system').addEventListener('change', async () => {
    dbs.trees = {};
    await Promise.all([...dbs.open].filter(k => !k.includes('/')).map(k => loadTree(k)));
    renderTree();
});
document.getElementById('db-detail').addEventListener('click', (e) => {
    const open = e.target.closest('[data-open-name]');
    if (open) {
        selectNode({database: open.dataset.openDb, schema: open.dataset.openSchema, name: open.dataset.openName});
        return;
    }
    const tab = e.target.closest('[data-rtab]');
    if (tab) {
        dbs.relationTab = tab.dataset.rtab;
        renderRelationTab();
        return;
    }
    const order = e.target.closest('[data-order]');
    if (order && dbs.rows) {
        const column = order.dataset.order;
        dbs.rows.desc = dbs.rows.order === column ? !dbs.rows.desc : false;
        dbs.rows.order = column;
        dbs.rows.offset = 0;
        loadRows();
        return;
    }
    const page = e.target.closest('[data-page]');
    if (page && dbs.rows) {
        dbs.rows.offset = Math.max(0, dbs.rows.offset + Number(page.dataset.page) * ROWS_PAGE);
        loadRows();
    }
});
onTab('databases', showDatabases);
