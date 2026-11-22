// Lottery dashboard - the draw: every miner together, your odds first.

const overview = {range: '24', history: null, bests: [], days: [], events: [], loadedAt: 0};

onTab('overview', () => {
    buildChips('ov-range', [{key: '24', label: '24h'}, {key: '168', label: '7d'}, {key: '720', label: '30d'}], overview.range, (key) => {
        overview.range = key;
        loadOverviewHistory();
    });
    renderOverview();
    loadOverviewData();
}, () => {
    renderOverview();
    // The slower data every minute
    if (Date.now() - overview.loadedAt > 60000) loadOverviewData();
});

async function loadOverviewData() {
    overview.loadedAt = Date.now();
    try {
        const [bests, days, events] = await Promise.all([api('bests?limit=10'), api('days?days=90'), api('events?limit=30')]);
        overview.bests = bests;
        overview.days = days;
        overview.events = events;
        renderOverviewLists();
    } catch (e) {
        toast('Cannot load the history: ' + e.message);
    }
    loadOverviewHistory();
}

async function loadOverviewHistory() {
    try {
        overview.history = await api('history?hours=' + overview.range);
        renderRateChart();
    } catch (e) {
        emptyChart(document.getElementById('ov-rate'), 'Cannot load the history');
    }
}

function renderOverview() {
    const s = app.status;
    if (!s) return;
    const t = s.totals, net = s.network;
    renderNotices(s);
    renderGauge(t, net);

    document.getElementById('ov-odds').innerHTML = [
        oddsTile('This block', oneIn(t.block_chance), 'the next ten minutes', true),
        oddsTile('Today', oneIn(t.day_chance), '144 blocks'),
        oddsTile('This year', oneIn(t.year_chance), '52,560 blocks'),
        oddsTile('On average', years(t.years), 'until a block is yours'),
    ].join('');

    const prize = net.prize;
    const tiles = [
        tile('Hash rate', rateHtml(t.hashrate / 1e9), `${t.mining} of ${t.miners} mining`),
        tile('If you win', missing(prize) ? '–' : `${num(prize, 3)}<small> BTC</small>`,
            missing(net.subsidy) ? 'block reward' : `${num(net.subsidy, 3)} new${missing(net.fees) ? '' : ` + ${num(net.fees, 3)} fees, lately`}`, 'prize'),
        tile('A ticket costs', `${num(t.kwh_per_day, 2)}<small> kWh a day</small>`, missing(t.efficiency) ? `${num(t.power, 0)} W` : `${num(t.power, 0)} W · ${num(t.efficiency, 1)} J/TH`),
        tile('A new best', oneIn(t.beat_best_today), missing(t.beat_best_seconds) ? 'chance today' : `chance today · every ${fmtDuration(t.beat_best_seconds)} on average`),
        tile('Luck', missing(t.luck) ? '–' : `${sig3(t.luck)}<small>×</small>`, 'best share this session against the usual best', !missing(t.luck) && t.luck >= 1 ? 'pos' : ''),
        tile('Work done', `${hashes(t.total_hashes)}<small> hashes</small>`, `since ${s.database.since ? fmtIsoDate(s.database.since) : 'now'}${net.difficulty ? ` · a block takes ${hashes(net.difficulty * 2 ** 32)} on average` : ''}`),
    ];
    document.getElementById('ov-facts').innerHTML = tiles.join('');

    renderTickets(t);
}

function oddsTile(label, value, detail, lead) {
    return `<div class="odds-tile${lead ? ' lead' : ''}"><div class="l">${label}</div><div class="v">${escapeHtml(value)}</div><div class="d">${escapeHtml(detail)}</div></div>`;
}

function renderNotices(s) {
    const notes = [];
    if (!miners().length) notes.push('<div class="note warn">No miners yet. Add each one with its name and IP address under Miners in the app\'s configuration, then restart the app.</div>');
    miners().filter(m => !m.connected && !m.last_seen).forEach(m => {
        notes.push(`<div class="note warn"><b>${escapeHtml(m.name)}</b> hasn't answered at ${escapeHtml(m.host)} yet: ${escapeHtml(m.error || 'waiting')}. Check its address in the app's configuration.</div>`);
    });
    if (s.totals.blocks_found > 0) notes.push(`<div class="note jackpot">🎉 <b>A block was found!</b> ${num(s.totals.blocks_found)} so far. Check your wallet.</div>`);
    const el = document.getElementById('ov-notices');
    const html = notes.join('');
    if (el.innerHTML !== html) el.innerHTML = html;
}

// The best share on a log scale from 1 to the network difficulty: how far from a block
function renderGauge(t, net) {
    const el = document.getElementById('ov-gauge');
    const target = net.difficulty;
    const hint = document.getElementById('ov-gauge-hint');
    if (!target) {
        hint.textContent = '';
        el.innerHTML = '<div class="empty-state">The network difficulty isn\'t known yet</div>';
        return;
    }
    const hi = Math.log10(target);
    const marks = [];
    miners().forEach(m => {
        if (m.values.best_session_diff) marks.push({v: m.values.best_session_diff, color: minerColor(m.slug), name: `${m.name}, this session`});
    });
    const best = t.best;
    const at = v => Math.max(0, Math.min(100, Math.log10(Math.max(1, v)) / hi * 100));
    hint.textContent = best ? `a block needs a share ${big(target / best)}× harder` : '';
    const ticks = [];
    for (let e = 0; e <= hi; e += 3) ticks.push(`<span class="g-tick" style="left:${e / hi * 100}%">${siRound(e)}</span>`);
    el.innerHTML = `
        <div class="gauge">
            <div class="g-big"><span class="mono">${escapeHtml(diff(best))}</span><small>best share ever${t.best_miner && minerBySlug(t.best_miner) ? ', ' + escapeHtml(minerBySlug(t.best_miner).name) : ''}</small></div>
            <div class="g-target"><span class="mono">${escapeHtml(diff(target))}</span><small>what a block needs</small></div>
            <div class="g-track">
                <div class="g-fill" style="width:${best ? at(best) : 0}%"></div>
                ${marks.map(m => `<span class="g-mark" title="${escapeHtml(m.name + ': ' + diff(m.v))}" style="left:${at(m.v)}%;background:${m.color}"></span>`).join('')}
                <span class="g-goal" title="A block"></span>
            </div>
            <div class="g-ticks">${ticks.join('')}</div>
            <div class="g-legend">${best ? `${escapeHtml(smallPct(t.best_vs_network))} of the way, by difficulty. Each step on this scale is 1000× harder than the one before.` : 'No best share seen yet.'}</div>
        </div>`;
}

function renderTickets(t) {
    const el = document.getElementById('ov-tickets');
    if (!miners().length) { el.innerHTML = '<div class="empty-state">No miners configured</div>'; return; }
    el.innerHTML = miners().map(m => {
        const share = t.shares[m.slug] || 0;
        const v = m.values;
        return `<button type="button" class="ticket" onclick="switchTab('miner-${escapeHtml(m.slug)}')">
            <span class="t-name"><i style="background:${minerColor(m.slug)}"></i><b>${escapeHtml(m.name)}</b><span class="muted">${escapeHtml(modelText(m))}</span></span>
            <span class="t-state">${stateBadge(m)}</span>
            <span class="t-rate mono">${m.connected ? rateHtml(v.hashrate) : '–'}</span>
            <span class="t-bar"><span class="share-bar"><i style="width:${share * 100}%;background:${minerColor(m.slug)}"></i></span><span class="mono">${num(share * 100, 0)}%</span></span>
            <span class="t-cell"><small>today</small><span class="mono">${escapeHtml(oneIn(m.odds.day_chance))}</span></span>
            <span class="t-cell"><small>best this session</small><span class="mono">${escapeHtml(diff(v.best_session_diff))}</span></span>
            <span class="t-cell"><small>ASIC</small><span class="mono">${missing(v.temp) ? '–' : num(v.temp, 0) + ' °C'}</span></span>
            <span class="t-cell"><small>power</small><span class="mono">${missing(v.power) || !m.connected ? '–' : num(v.power, 0) + ' W'}</span></span>
        </button>`;
    }).join('');
}

// The miners' hash rate over time, one line each and the total
function renderRateChart() {
    const el = document.getElementById('ov-rate');
    const h = overview.history;
    if (!h || !h.rows.length) {
        document.getElementById('ov-rate-legend').innerHTML = '';
        return emptyChart(el, 'No readings in this period yet');
    }
    const xs = [...new Set(h.rows.map(r => r.ts))].sort((a, b) => a - b);
    const index = new Map(xs.map((x, i) => [x, i]));
    const series = miners().map(m => {
        const values = new Array(xs.length).fill(null);
        h.rows.filter(r => r.miner === m.slug).forEach(r => { values[index.get(r.ts)] = r.hashrate; });
        return {name: m.name, color: minerColor(m.slug), values, area: true, areaOpacity: 0.08};
    });
    if (series.length > 1) {
        const total = xs.map((_, i) => series.some(s => !missing(s.values[i])) ? series.reduce((a, s) => a + (s.values[i] || 0), 0) : null);
        series.push({name: 'together', color: color('brand'), values: total, dash: '4 3', width: 1.5});
    }
    lineChart(el, {
        x: xs, series, height: 200, zero: true,
        formatX: x => fmtDateTime(x), formatY: v => rate(v), formatTick: v => rateParts(v)[0],
        xTicks: timeAxisTicks(xs[0], xs[xs.length - 1], 5),
    });
    document.getElementById('ov-rate-legend').innerHTML = legendHtml(series.map(s => ({name: s.name, color: s.color, kind: s.dash ? 'line' : ''})));
}

function renderOverviewLists() {
    const rows = overview.bests.map((b, i) => {
        const m = minerBySlug(b.miner);
        return `<tr>${td(`<span class="rank">${i + 1}</span>`)}${td(`<b class="mono">${escapeHtml(diff(b.diff))}</b>`, 'n')}${td(m ? `<span style="color:${minerColor(m.slug)}">●</span> ${escapeHtml(m.name)}` : escapeHtml(b.miner))}${td(escapeHtml(fmtDateTime(b.ts)), 'muted')}</tr>`;
    });
    document.getElementById('ov-bests').innerHTML = table(
        [{label: '#'}, {label: 'Share', n: true}, {label: 'Miner'}, {label: 'When'}], rows,
        {empty: 'No best shares yet', emptySub: 'Each new best of a session is kept here.', cls: 'compact'});
    document.getElementById('ov-events').innerHTML = overview.events.length
        ? overview.events.map(e => eventHtml(e, true)).join('')
        : '<div class="empty-state">Nothing yet</div>';
    renderDaysChart(document.getElementById('ov-days'), overview.days, null);
}

// Each miner's best share per day as dots, the network difficulty as a line, on a log scale
function renderDaysChart(el, days, onlyMiner) {
    const rows = days.filter(d => d.best > 0 && (!onlyMiner || d.miner === onlyMiner));
    if (!rows.length) return emptyChart(el, 'No best shares by day yet');
    const dayList = [...new Set(days.map(d => d.day))].sort();
    mountChart(el, () => {
        const values = rows.map(d => Math.log10(d.best));
        const network = days.filter(d => d.network > 0).map(d => Math.log10(d.network));
        const lo = Math.floor(Math.min(...values) / 3) * 3;
        const hi = Math.ceil(Math.max(...values, ...network) / 3) * 3;
        const scale = {lo, hi, values: []};
        for (let v = lo; v <= hi; v += 3) scale.values.push(v);
        const {svg, plot, y} = chartFrame(el, 190, scale, v => diff(10 ** v));
        const slot = plot.w / Math.max(1, dayList.length);
        const x = day => plot.x + slot * dayList.indexOf(day) + slot / 2;
        const byDay = new Map();
        days.forEach(d => { if (d.network > 0) byDay.set(d.day, Math.max(byDay.get(d.day) || 0, d.network)); });
        const line = [...byDay.entries()].sort().map(([day, n], i) => `${i ? 'L' : 'M'}${x(day).toFixed(1)},${y(Math.log10(n)).toFixed(1)}`).join('');
        if (line) {
            svg.appendChild(svgEl('path', {d: line, fill: 'none', stroke: color('brand'), 'stroke-width': 2, 'stroke-dasharray': '5 3'}));
            const label = svgEl('text', {class: 'marker-label', x: plot.x + plot.w - 4, y: y(Math.log10([...byDay.values()].pop())) - 5, 'text-anchor': 'end', style: `fill:${color('brand')}`});
            label.textContent = 'a block';
            svg.appendChild(label);
        }
        const every = Math.max(1, Math.ceil(dayList.length * 48 / plot.w));
        dayList.forEach((day, i) => {
            if (i % every) return;
            const label = svgEl('text', {class: 'tick', x: x(day), y: plot.y + plot.h + 14, 'text-anchor': 'middle'});
            label.textContent = fmtDay(new Date(day + 'T12:00:00').getTime() / 1000);
            svg.appendChild(label);
        });
        rows.forEach(d => {
            const m = minerBySlug(d.miner);
            const dot = svgEl('circle', {cx: x(d.day), cy: y(Math.log10(d.best)), r: 4.5, fill: m ? minerColor(m.slug) : color('series-5'), tabindex: '0'});
            const tip = (e) => {
                const box = dot.getBoundingClientRect();
                showTip(e && e.clientX ? e.clientX : box.left, e && e.clientY ? e.clientY : box.top, fmtIsoDate(d.day), [
                    {color: m ? minerColor(m.slug) : null, name: m ? m.name : d.miner, value: diff(d.best)},
                    {name: 'work', value: `${hashes(d.hashes)} hashes`},
                ]);
            };
            dot.addEventListener('pointermove', tip);
            dot.addEventListener('focus', () => tip(null));
            dot.addEventListener('pointerleave', hideTip);
            dot.addEventListener('blur', hideTip);
            svg.appendChild(dot);
        });
    });
}
