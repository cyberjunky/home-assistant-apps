// Network dashboard - Overview: the network now, what needs a look, and what happened lately.

function attentionItems() {
    const out = [];
    const link = (h) => hostLink(h);
    const downGear = networkHosts().filter(h => isInfra(h) && h.up === false);
    if (downGear.length) out.push({kind: 'bad', text: `Network gear away: ${downGear.map(link).join(', ')}`});
    ipConflicts().forEach(c => out.push({kind: 'bad', text: `Two devices on <span class="mono">${escapeHtml(c.ip)}</span>: ${c.hosts.map(link).join(' and ')}`}));
    const unstable = networkHosts().filter(isUnstable).sort((a, b) => weekStats(b.key).drops - weekStats(a.key).drops);
    if (unstable.length) out.push({kind: 'warn', text: `Unstable this week: ${unstable.slice(0, 6).map(h => `${link(h)} <span class="muted">${num(weekStats(h.key).drops_per_day, 1)}×/day</span>`).join(', ')}${unstable.length > 6 ? ` and ${unstable.length - 6} more` : ''}`, href: '#stats'});
    const fresh = networkHosts().filter(isNew);
    if (fresh.length) out.push({kind: 'info', text: `New on the network this week: ${fresh.slice(0, 8).map(link).join(', ')}${fresh.length > 8 ? ` and ${fresh.length - 8} more` : ''}`});
    const stale = networkHosts().filter(isStale);
    if (stale.length) out.push({kind: '', text: `Not seen for over a week: ${stale.slice(0, 8).map(h => `${link(h)} <span class="muted">${escapeHtml(fmtAgo(h.last_changed))}</span>`).join(', ')}${stale.length > 8 ? ` and ${stale.length - 8} more` : ''}`});
    const random = networkHosts().filter(h => h.random_mac && !meta(h.key).kind && !meta(h.key).label && h.name.toUpperCase() === (h.mac || '').toUpperCase());
    if (random.length) out.push({kind: '', text: `${random.length} device${random.length > 1 ? 's' : ''} with a private (random) MAC address and no name: a phone or laptop of a guest, or yours after a reset`});
    const placed = onAnyPage();
    const unplaced = networkHosts().filter(h => !placed.has(h.key));
    if (unplaced.length) out.push({kind: '', text: `${unplaced.length} device${unplaced.length > 1 ? 's are' : ' is'} on no page of the map yet`, href: '#map'});
    const noNet = networks().length ? networkHosts().filter(h => h.ip && isPrivateIp(h.ip) && !networkOf(h).net) : [];
    if (noNet.length) out.push({kind: '', text: `${noNet.length} device${noNet.length > 1 ? 's have' : ' has'} an IP in none of your networks`, href: '#networks'});
    if (!networks().length && app.hosts.length) out.push({kind: '', text: 'No networks yet: the Networks page can make them from the IP addresses it sees', href: '#networks'});
    return out;
}

function renderOverview() {
    const el = document.getElementById('tab-overview');
    if (!app.doc || !app.status) return;
    const hosts = networkHosts();
    const up = hosts.filter(h => h.up).length;
    const gear = hosts.filter(isInfra);
    const week = app.stats[7];
    const drops = week ? Object.values(week.hosts).reduce((a, s) => a + s.drops, 0) : null;
    const items = attentionItems();
    const kinds = new Map();
    hosts.forEach(h => kinds.set(kindOf(h), (kinds.get(kindOf(h)) || 0) + 1));
    const nets = networks().map(n => ({n, hosts: hosts.filter(h => (networkOf(h).net || {}).id === n.id)}));
    const events = week ? week.events.filter(e => { const h = hostByKey(e.key); return h && !h.gps_only; }).slice(0, 14) : [];
    el.innerHTML = `
        <div class="tiles kpi-tiles">
            ${tile('Online now', `${num(up)}<small> of ${num(hosts.length)}</small>`, `${num(hosts.length - up)} away`)}
            ${tile('Network gear', `${num(gear.filter(h => h.up).length)}<small> of ${num(gear.length)}</small>`, gear.some(h => h.up === false) ? 'something is away' : 'all up', gear.some(h => h.up === false) ? 'bad' : '')}
            ${tile('New this week', num(hosts.filter(isNew).length), 'first seen in 7 days')}
            ${tile('Drops this week', drops === null ? '–' : num(drops), 'times a device left the network')}
            ${tile('Unstable', num(hosts.filter(isUnstable).length), '3 or more drops a day', hosts.some(isUnstable) ? 'warn' : '')}
            ${tile('Private MACs', num(hosts.filter(h => h.random_mac).length), 'random Wi-Fi addresses')}
        </div>
        <div class="dash-grid wide-left">
            <div class="dash-stack">
                ${panel('Needs a look', `<a href="#devices" class="plain-link">all devices →</a>`, items.length ? `<div class="checks-list attention">${items.map(i => `<div class="check"><span class="sdot ${i.kind === 'bad' ? 'bad' : i.kind === 'warn' ? 'warn' : i.kind === 'info' ? 'ok' : ''}"></span><span>${i.text}${i.href ? ` <a href="${i.href}" class="plain-link muted">→</a>` : ''}</span></div>`).join('')}</div>`
                    : '<div class="all-good"><span class="sdot ok"></span> Nothing needs a look.</div>')}
                ${panel('Online in the last 24 hours', '', '<div id="ov-online" class="chart"></div>')}
            </div>
            <div class="dash-stack">
                ${panel('Lately', '<a href="#stats" class="plain-link">statistics →</a>', events.length ? `<div class="events compact">${events.map(e => {
                    const h = hostByKey(e.key);
                    return h ? `<div class="event"><span class="t">${escapeHtml(fmtTime(e.ts))}</span><span class="k ${e.up ? 'ok' : ''}">${e.up ? 'joined' : 'left'}</span><span class="x">${hostLink(h)}</span></div>` : '';
                }).join('')}</div>` : '<div class="empty-state">Nothing happened yet</div>')}
                ${panel('Kinds', '', `<div class="bars">${[...kinds.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k, n]) => `<div class="row"><span class="name">${kindIconHtml(k, 'small')}${escapeHtml(kindLabel(k))}</span><div><div class="bar" style="width:${(n / hosts.length * 100).toFixed(1)}%"></div></div><span class="n">${n}</span></div>`).join('')}</div>`)}
                ${nets.length ? panel('Networks', '<a href="#networks" class="plain-link">networks →</a>', `<div class="bars">${nets.map(({n, hosts: hs}) => `<div class="row"><span class="name">${netChip(n)}</span><div><div class="bar" style="width:${hosts.length ? (hs.length / hosts.length * 100).toFixed(1) : 0}%;background:${attr(netColor(n))}"></div></div><span class="n">${hs.filter(h => h.up).length}/${hs.length}</span></div>`).join('')}</div>`) : ''}
            </div>
        </div>`;
    drawOnline('ov-online', 1);
}

// The number of devices online over time
async function drawOnline(id, days) {
    const el = document.getElementById(id);
    if (!el) return;
    let data = app.stats[days];
    if (!data || Date.now() / 1000 - data.until > 120) {
        try { data = await loadStats(days); } catch (e) { el.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`; return; }
    }
    const target = document.getElementById(id);
    if (!target) return;
    lineChart(target, {
        x: data.online.x, height: 180, zero: true,
        series: [{name: 'Online', color: 'var(--series-1)', values: data.online.y, area: true}],
        xTicks: timeAxisTicks(data.online.x[0], data.online.x[data.online.x.length - 1], 6),
        formatX: t => fmtDateTime(t), formatY: v => `${num(v)} devices`, formatTick: v => num(v),
        empty: 'No history yet',
    });
}

onTab('overview', () => renderOverview(), () => {
    if (!document.getElementById('modal-root').children.length) renderOverview();
});
