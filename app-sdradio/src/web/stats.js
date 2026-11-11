// SDRadio dashboard - Stats: what was heard.

async function showStats() {
    let s;
    try { s = await api('stats'); } catch (e) { return toast(e.message, 'error'); }
    document.getElementById('stats-tiles').innerHTML = [
        tile('Stored channels', num(s.channels), `${num(s.bands)} bands · ${num(s.favourites)} favourites`),
        tile('Heard today', num(s.heard_today), 'of the stored channels'),
        tile('New finds', `<span style="color:var(--accent-color)">${num(s.finds)}</span>`, 'from scans, not stored yet'),
        tile('Listening', escapeHtml(fmtDuration(s.listening_today)), `today · ${escapeHtml(fmtDuration(s.listening_week))} this week`),
        tile('Hits', num(s.hits_24h), `last 24 hours · ${num(s.hits_total)} kept`),
        tile('Last backup', s.last_backup ? escapeHtml(fmtTime(s.last_backup)) : '–',
            s.last_backup ? `${escapeHtml(new Date(s.last_backup * 1000).toLocaleDateString([], {day: 'numeric', month: 'short'}))} · ${num(s.backups)} kept` : 'none yet'),
    ].join('');

    const max = Math.max(1, ...s.hours.map(h => h.hits));
    document.getElementById('stats-hours').innerHTML = s.hours.map(h =>
        `<i style="height:${(h.hits / max * 100).toFixed(1)}%" title="${escapeHtml(fmtTime(h.ts))}: ${num(h.hits)} hits"></i>`).join('');
    const axis = document.getElementById('stats-hours').nextElementSibling;
    const axisHtml = `<div class="bars-axis">${[0, 6, 12, 18, 23].map(i => `<span>${escapeHtml(fmtTime(s.hours[i].ts))}</span>`).join('')}</div>`;
    if (axis && axis.classList.contains('bars-axis')) axis.outerHTML = axisHtml;
    else document.getElementById('stats-hours').insertAdjacentHTML('afterend', axisHtml);

    const top = Math.max(1, ...s.per_band.map(b => b.channels));
    document.getElementById('stats-bands').innerHTML = s.per_band.length ? s.per_band.map(b => `
        <div class="band-row"><span>${escapeHtml(b.name || '(no band)')}</span>
            <span class="track"><i style="width:${(b.channels / top * 100).toFixed(1)}%;background:var(--seq-2)"></i><i style="width:${(b.heard / top * 100).toFixed(1)}%;background:var(--seq-4)"></i></span>
            <span class="n">${num(b.channels)} <span class="muted">· ${num(b.heard)}</span></span></div>`).join('')
        : '<div class="empty-state">No channels stored yet</div>';

    document.getElementById('stats-recent').innerHTML = s.recent.length ? `<table class="data"><thead><tr><th>Time</th><th>Frequency</th><th>Channel</th><th>Band</th><th>Peak</th><th class="n">Length</th><th></th></tr></thead><tbody>
        ${s.recent.map(h => `<tr><td class="mono muted">${escapeHtml(fmtDateTime(h.ts))}</td><td class="mono">${fmtMHz(h.frequency)}</td>
            <td>${h.channel_id ? escapeHtml(h.name || 'M' + chNum(h.number)) : '<span class="muted">not stored</span>'}</td>
            <td>${h.band ? `<span class="badge">${escapeHtml(h.band)}</span>` : ''}</td><td class="mono">${num(h.peak, 0)} dB</td>
            <td class="n">${escapeHtml(fmtDuration(h.duration))}</td>
            <td style="text-align:right"><button type="button" class="btn btn-small" data-tune="${h.frequency}" data-mode="${h.mode}">Tune</button></td></tr>`).join('')}
        </tbody></table>` : '<div class="empty-state">Nothing heard yet. Hits are logged while a squelch is set, and while scanning.</div>';

    const finds = (await api('finds').catch(() => ({finds: []}))).finds.slice(0, 6);
    document.getElementById('stats-finds').innerHTML = finds.length ? finds.map(f => `
        <div class="find-row"><span class="f">${fmtMHz(f.frequency)}</span>
            <span class="info">${modeLabel(f.mode)} · ${num(f.hits)} hits · peak ${num(f.peak, 0)} dB · last ${escapeHtml(fmtDateTime(f.last))}</span>
            <button type="button" class="btn btn-small" data-tune="${f.frequency}" data-mode="${f.mode}">Listen</button>
            <button type="button" class="btn btn-small btn-primary" data-label="${f.frequency}" data-mode="${f.mode}">Label &amp; store</button></div>`).join('')
        : '<div class="empty-state">No new finds. Scan a band or a range to find signals that aren\'t stored yet.</div>';

    document.getElementById('stats-top').innerHTML = s.top.length ? `<table class="data"><thead><tr><th>#</th><th>Channel</th><th>Frequency</th><th>Band</th><th class="n">Hits</th><th>Last</th><th></th></tr></thead><tbody>
        ${s.top.map(c => `<tr><td class="num">${chNum(c.number)}</td><td>${escapeHtml(c.name || '')}</td><td class="mono">${fmtMHz(c.frequency)}</td>
            <td>${c.band ? `<span class="badge">${escapeHtml(c.band)}</span>` : ''}</td><td class="n">${num(c.hits)}</td>
            <td class="mono muted">${escapeHtml(fmtDateTime(c.last))}</td>
            <td style="text-align:right"><button type="button" class="btn btn-small" data-channel="${c.id}">Tune</button></td></tr>`).join('')}
        </tbody></table>` : '<div class="empty-state">No stored channel was heard in the last 7 days</div>';
}

function initStats() {
    document.getElementById('tab-stats').addEventListener('click', (e) => {
        const tune = e.target.closest('[data-tune]');
        const label = e.target.closest('[data-label]');
        const channel = e.target.closest('[data-channel]');
        if (tune) {
            radioCommand('tune', {frequency: Number(tune.dataset.tune), mode: tune.dataset.mode});
            switchTab('radio');
        } else if (channel) {
            radioCommand('channel', {id: Number(channel.dataset.channel)});
            switchTab('radio');
        } else if (label) {
            switchTab('channels');
            newChannel({frequency: Number(label.dataset.label), mode: label.dataset.mode, from_find: true});
        }
    });
}

onTab('stats', showStats);
