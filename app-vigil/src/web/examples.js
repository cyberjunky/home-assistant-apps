// Vigil dashboard - example searches and charts (query-examples.json, also checked by the tests):
// described, per kind of source, one click to run.

let queryExamples = null;

async function loadExamples() {
    if (queryExamples) return queryExamples;
    const response = await fetch(basePath + 'web/query-examples.json?v=' + encodeURIComponent(app.version || ''));
    if (!response.ok) throw new Error(`Examples could not be loaded (${response.status})`);
    queryExamples = await response.json();
    return queryExamples;
}

function exampleCode(kind, ex) {
    if (kind === 'logs') return ex.q;
    return ex.queries.map(q => {
        const parts = [q.agg || 'avg'];
        if (q.group) parts.push(`by ${q.group}`);
        if (q.anomaly) parts.push('with anomaly band');
        return `${q.name}  (${parts.join(', ')})`;
    }).join('\n');
}

async function showExamples(kind) {
    let examples;
    try {
        examples = (await loadExamples())[kind] || [];
    } catch (err) {
        return toast(err.message, 'error');
    }
    const rows = examples.map((ex, i) => `<button type="button" class="example-row" data-i="${i}">
        <span class="example-head"><b>${escapeHtml(ex.title)}</b><span class="example-needs">${escapeHtml(ex.needs || '')}</span></span>
        <span class="example-desc">${escapeHtml(ex.description || '')}</span>
        <code class="example-code">${escapeHtml(exampleCode(kind, ex))}</code></button>`).join('');
    openModal({
        title: kind === 'logs' ? 'Example searches' : 'Example charts',
        wide: true,
        body: `<p class="panel-sub" style="margin-bottom:10px">${kind === 'logs'
            ? 'Click one to run it. Words between &lt; and &gt; are for you to fill in.'
            : 'Click one to chart it. A chart stays empty until that source sends data (see Sources).'}</p>
            <div class="example-list">${rows}</div>`,
    });
    document.querySelectorAll('#app-modal .example-row').forEach(el => {
        el.onclick = () => {
            const ex = examples[Number(el.dataset.i)];
            closeModal();
            if (kind === 'logs') {
                setLogQuery(ex.q);
                const q = document.getElementById('log-q');
                // A placeholder to fill in: select it instead of searching for "<host>"
                const at = ex.q.indexOf('<');
                if (at >= 0) {
                    q.focus();
                    q.setSelectionRange(at, ex.q.indexOf('>', at) + 1);
                } else {
                    runLogSearch();
                }
            } else {
                openMetrics(ex.queries.map(qq => ({...qq})));
            }
        };
    });
}
