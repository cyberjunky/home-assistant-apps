// Vigil dashboard - Overview: totals, log volume by severity, the busiest hosts, new patterns and
// the rules firing now.

let overviewTimer = null;

// The overview API knows 1, 6, 24 and 168 hours: the smallest that covers the range
function overviewHours() {
    const hours = rangeSpan() / 3600;
    return [1, 6, 24, 168].find(h => h >= hours - 0.01) || 168;
}

async function showOverview() {
    await loadOverview();
    clearInterval(overviewTimer);
    overviewTimer = setInterval(() => {
        if (activeTab !== 'overview' || document.visibilityState !== 'visible') return;
        if (timeRange.preset) loadOverview();
    }, 30000);
}

async function loadOverview() {
    if (!ready()) return renderOverviewEmpty(app.status && app.status.error ? 'The database is not connected' : 'Nothing has arrived yet');
    const hours = overviewHours();
    let data, volume = null;
    try {
        // A zoomed or custom window: the volume chart shows exactly that window
        const exact = !timeRange.preset || RANGE_PRESETS.find(p => p.key === timeRange.preset).secs !== hours * 3600;
        [data, volume] = await Promise.all([
            api('overview?hours=' + hours),
            exact ? api('logs/histogram?' + qs({...rangeWindow(), buckets: 60})) : Promise.resolve(null),
        ]);
    } catch (e) {
        renderOverviewEmpty('Could not load the overview');
        return handleError(e);
    }
    if (activeTab !== 'overview') return;
    renderOverview(data, volume, hours);
}

function renderOverviewEmpty(text) {
    document.getElementById('overview-tiles').innerHTML = '';
    document.getElementById('overview-summary').innerHTML = '';
    emptyChart(document.getElementById('chart-volume'), text);
    document.getElementById('top-hosts').innerHTML = emptyHtml(text);
    document.getElementById('new-patterns').innerHTML = emptyHtml(text);
    document.getElementById('overview-firing').innerHTML = emptyHtml(text);
}

function renderOverview(data, volume, hours) {
    const t = data.tiles || {};
    const period = hours === 168 ? '7 days' : `${hours} h`;
    document.getElementById('overview-tiles').innerHTML = [
        tile('Log lines', fmtCount(t.logs), `in the last ${period}`),
        tile('Errors', `<span class="${t.errors ? 'txt-bad' : ''}">${fmtCount(t.errors)}</span>`, 'err and worse'),
        tile('Warnings', `<span class="${t.warnings ? 'txt-warn' : ''}">${fmtCount(t.warnings)}</span>`, 'warning'),
        tile('Hosts', num(t.hosts), 'that sent logs'),
        tile('Metric series', fmtCount(t.series), 'collected'),
        tile('Firing', `<span class="${t.firing ? 'txt-bad' : 'txt-ok'}">${num(t.firing || 0)}</span>`, t.firing ? 'rules firing now' : 'all rules ok', t.firing ? 'tile-alert' : ''),
    ].join('');

    const vol = volume || data.volume || {t: [], by_severity: {}};
    const by = vol.by_severity || {};
    const sum = k => (by[k] || []).reduce((a, b) => a + (b || 0), 0);
    const total = Object.keys(by).reduce((a, k) => a + sum(k), 0);
    const bad = Object.keys(by).filter(k => severityNumber(k) <= 3).reduce((a, k) => a + sum(k), 0);
    const peakIdx = (vol.t || []).map((_, i) => Object.keys(by).reduce((a, k) => a + (by[k][i] || 0), 0));
    const peak = peakIdx.length ? Math.max(...peakIdx) : 0;
    document.getElementById('overview-summary').innerHTML = total ? [
        `<span><b>${fmtCount(total)}</b> lines</span>`,
        `<span><b class="${bad ? 'txt-bad' : ''}">${fmtCount(bad)}</b> errors</span>`,
        `<span>peak <b>${fmtCount(peak)}</b> per bucket</span>`,
    ].join('') : '';
    document.getElementById('overview-volume-hint').textContent = volume ? `${rangeText()} · drag to zoom` : `last ${period} · drag to zoom`;
    severityChart(document.getElementById('chart-volume'), vol, {
        height: 170,
        title: 'Log volume',
        onZoom: zoomRange,
        zoomed: rangeZoomed(),
        onReset: rangeBack,
        empty: noDataYet() ? 'Nothing has arrived yet: see Sources to point devices at Vigil' : 'No log lines in this period',
    });

    const hosts = data.top_hosts || [];
    const max = Math.max(1, ...hosts.map(h => h.count));
    const hostEl = document.getElementById('top-hosts');
    hostEl.innerHTML = hosts.length ? hosts.map((h, i) => `
        <div class="rank-row link" data-host="${i}" title="Show the logs of ${escapeHtml(h.host)}">
            <div class="name"><b>${escapeHtml(h.host || '(no host)')}</b><small>${h.errors ? `<span class="txt-bad">${num(h.errors)} errors</span>` : 'no errors'}</small></div>
            <div class="n">${fmtCount(h.count)}<small>lines</small></div>
            <div class="bar-track"><i style="width:${(h.count / max * 100).toFixed(1)}%"></i>${h.errors ? `<i class="bad" style="width:${(h.errors / max * 100).toFixed(1)}%"></i>` : ''}</div>
        </div>`).join('') : emptyHtml('No hosts in this period');
    hostEl.onclick = (e) => {
        const row = e.target.closest('[data-host]');
        if (row) openLogs(`host:${queryValue(hosts[Number(row.dataset.host)].host)}`);
    };

    const patterns = data.new_patterns || [];
    const patEl = document.getElementById('new-patterns');
    patEl.innerHTML = patterns.length ? patterns.map(p => `
        <div class="pattern-row link" data-pattern="${Number(p.id)}" title="Show the lines of this pattern">
            <div class="template">${templateHtml(p.template)}</div>
            <div class="n">${fmtCount(p.count)}<small>${escapeHtml(fmtAgo(p.first_seen))}</small></div>
        </div>`).join('') : emptyHtml('No new patterns in this period');
    patEl.onclick = (e) => {
        const row = e.target.closest('[data-pattern]');
        if (row) openLogs(`pattern:${row.dataset.pattern}`);
    };

    const firing = data.firing || [];
    document.getElementById('overview-firing').innerHTML = firing.length ? `<table class="data"><thead><tr>
            <th>State</th><th>Rule</th><th>Severity</th><th>Since</th><th class="n">Value</th><th>Message</th></tr></thead><tbody>${firing.map(a => `
            <tr class="link" onclick="switchTab('alerts')">
                <td>${statePill(a.state || 'firing')}</td>
                <td><b>${escapeHtml(a.rule_name || a.name || '#' + a.rule)}</b></td>
                <td>${ruleSeverityPill(a.severity)}</td>
                <td class="muted" title="${escapeHtml(a.ts ? fmtDateTime(a.ts) : '')}">${escapeHtml(fmtAgo(a.ts))}</td>
                <td class="n">${escapeHtml(fmtValue(a.value))}</td>
                <td class="wrap">${escapeHtml(a.message || '')}</td>
            </tr>`).join('')}</tbody></table>`
        : `<div class="empty-state"><span class="state ok">Nothing is firing</span></div>`;
}

onTab('overview', showOverview);
onRange('overview', loadOverview);
onLeave('overview', () => clearInterval(overviewTimer));
