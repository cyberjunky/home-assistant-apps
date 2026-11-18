// MQTT dashboard - Traffic: the messages as they pass, filtered on the server (an MQTT filter and/or
// text in the topic or payload), with pause. A message opens with its whole payload.

const MAX_ROWS = 1000;
const tr = {
    filter: stored('traffic_filter', '#'),
    q: '',
    retained: stored('traffic_retained', '0') === '1',
    paused: false,
    rows: [],
    held: [],
    seen: 0,
    skipped: 0,
    since: Date.now(),
};

function trafficParams() {
    const p = new URLSearchParams({traffic: '1', retained: tr.retained ? '1' : '0'});
    if (tr.filter && validFilter(tr.filter)) p.set('filter', tr.filter);
    if (tr.q) p.set('q', tr.q);
    return p.toString();
}

// From elsewhere: watch a topic or filter live
function watchTraffic(filter) {
    tr.filter = filter;
    store('traffic_filter', filter);
    tr.rows = [];
    tr.held = [];
    tr.seen = 0;
    tr.since = Date.now();
    const input = document.getElementById('tr-filter');
    if (input) input.value = filter;
    switchTab('traffic');
}

// =====================================================================
// The topics the broker has seen, offered under the filter one level at a time: a branch as
// "path/#", a topic as itself. Choosing a branch lists what is under it.
// =====================================================================
const MAX_SUGGESTIONS = 100;

// "goodwe/#" and "goodwe/" list what is under goodwe; "goodwe/ba" what has "ba" in its name there
function suggestScope(text) {
    const levels = text.split('/');
    let part = levels.pop();
    if (part === '#' || part === '+') part = '';
    if (levels.some(l => l === '+' || l === '#')) return null;
    return {prefix: levels.join('/'), part: part.toLowerCase()};
}

function suggestionsFor(children, part, current) {
    const first = [], rest = [];
    for (const c of children) {
        const at = c.name.toLowerCase().indexOf(part);
        if (at < 0) continue;
        const into = at === 0 ? first : rest;
        const below = c.topics - (c.leaf ? 1 : 0);
        if (c.has_children) into.push({value: c.path + '/#', note: `${num(below)} topic${below === 1 ? '' : 's'}`});
        if (c.leaf) into.push({value: c.path, note: 'topic'});
    }
    return [...first, ...rest].filter(s => s.value !== current).slice(0, MAX_SUGGESTIONS);
}

function suggestTopics(input) {
    const box = document.getElementById('tr-suggest');
    let items = [], at = -1, asked = 0;
    const close = () => { box.hidden = true; at = -1; input.setAttribute('aria-expanded', 'false'); };
    const draw = () => {
        box.innerHTML = items.map((s, i) => `<div class="suggest-item${i === at ? ' active' : ''}" role="option" aria-selected="${i === at}" data-at="${i}"><span>${escapeHtml(s.value)}</span><small>${escapeHtml(s.note)}</small></div>`).join('');
        box.hidden = !items.length;
        input.setAttribute('aria-expanded', String(items.length > 0));
        const active = box.querySelector('.active');
        if (active) active.scrollIntoView({block: 'nearest'});
    };
    const load = async () => {
        const text = input.value.trim();
        const scope = suggestScope(text === '#' ? '' : text);
        const mine = ++asked;
        let children = [];
        if (scope) {
            try { children = (await api('tree?prefix=' + encodeURIComponent(scope.prefix))).children; } catch (e) { /* no list then */ }
        }
        // An answer that comes after a later one, or after the field was left, is dropped
        if (mine !== asked || document.activeElement !== input) return;
        items = scope ? suggestionsFor(children, scope.part, text) : [];
        at = -1;
        draw();
    };
    const pick = (s) => {
        input.value = s.value;
        input.dispatchEvent(new Event('input'));
    };
    input.addEventListener('focus', load);
    input.addEventListener('input', load);
    input.addEventListener('blur', () => { asked++; close(); });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !box.hidden) { e.preventDefault(); asked++; close(); return; }
        if (e.key === 'ArrowDown' && box.hidden) { e.preventDefault(); load(); return; }
        if (box.hidden) return;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            at = (at + (e.key === 'ArrowDown' ? 1 : -1) + items.length + (at < 0 && e.key === 'ArrowUp' ? 1 : 0)) % items.length;
            draw();
        } else if (e.key === 'Enter' && at >= 0) {
            e.preventDefault();
            pick(items[at]);
        }
    });
    // mousedown, so the field keeps the focus and the list stays for the next level
    box.addEventListener('mousedown', (e) => {
        e.preventDefault();
        const item = e.target.closest('[data-at]');
        if (item) pick(items[Number(item.dataset.at)]);
    });
}

function messageRow(m) {
    const t = new Date(m.ts * 1000);
    const time = t.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}) + '.' + String(t.getMilliseconds()).padStart(3, '0');
    return `<tr class="clickable" data-seq="${m.seq}">
        ${td(time, 'mono nowrap muted')}
        ${td(topicHtml(m.topic), 'wrap-cell')}
        ${td(payloadPreview(m.payload, m.binary), 'payload-cell')}
        ${td(escapeHtml(fmtBytes(m.size)), 'n nowrap')}
        ${td(`${m.qos}${m.retain ? ' <span class="ret" title="Retained">R</span>' : ''}`, 'n nowrap')}
    </tr>`;
}

function renderTraffic() {
    const el = document.getElementById('tab-traffic');
    if (!el.querySelector('#tr-body')) {
        el.innerHTML = `
            <div class="page-head">
                <div class="filters">
                    <div class="suggest-wrap">
                        <input id="tr-filter" class="input mono" placeholder="Topic filter, e.g. zigbee2mqtt/#" autocomplete="off" spellcheck="false" role="combobox" aria-controls="tr-suggest" aria-expanded="false" aria-autocomplete="list">
                        <div id="tr-suggest" class="suggest" role="listbox" hidden></div>
                    </div>
                    <input id="tr-q" class="input search" type="search" placeholder="Text in topic or payload" autocomplete="off" spellcheck="false">
                    <label class="check-row" title="Also show the retained messages a new subscriber gets: when the broker or the dashboard restarts, all of them come at once"><input type="checkbox" id="tr-retained"> retained at start</label>
                </div>
                <div class="filters">
                    <span id="tr-count" class="muted"></span>
                    <button class="btn" id="tr-pause"></button>
                    <button class="btn" id="tr-clear">Clear</button>
                    <button class="btn" id="tr-publish">Publish…</button>
                </div>
            </div>
            <div class="form-error" id="tr-error"></div>
            ${panel('', '', `<div class="table-wrap traffic-table"><table class="data compact"><thead><tr><th>Time</th><th>Topic</th><th>Payload</th><th class="n">Size</th><th class="n">QoS</th></tr></thead><tbody id="tr-body"></tbody></table>
                <div id="tr-empty" class="empty-state"></div></div>`)}`;
        const filter = document.getElementById('tr-filter');
        const q = document.getElementById('tr-q');
        filter.value = tr.filter;
        q.value = tr.q;
        document.getElementById('tr-retained').checked = tr.retained;
        let wait = null;
        const changed = () => {
            clearTimeout(wait);
            wait = setTimeout(() => {
                const f = filter.value.trim() || '#';
                document.getElementById('tr-error').textContent = validFilter(f) ? '' : 'Not a valid topic filter: + and # stand alone in a level, # only at the end';
                if (!validFilter(f)) return;
                tr.filter = f;
                tr.q = q.value.trim();
                store('traffic_filter', f);
                tr.rows = [];
                tr.held = [];
                tr.seen = 0;
                tr.since = Date.now();
                connectLive(trafficParams());
                drawTraffic(true);
            }, 300);
        };
        filter.addEventListener('input', changed);
        suggestTopics(filter);
        q.addEventListener('input', changed);
        document.getElementById('tr-retained').addEventListener('change', (e) => { tr.retained = e.target.checked; store('traffic_retained', tr.retained ? '1' : '0'); connectLive(trafficParams()); });
        document.getElementById('tr-pause').addEventListener('click', () => {
            tr.paused = !tr.paused;
            if (!tr.paused) { tr.rows = [...tr.held.reverse(), ...tr.rows].slice(0, MAX_ROWS); tr.held = []; }
            drawTraffic(true);
        });
        document.getElementById('tr-clear').addEventListener('click', () => { tr.rows = []; tr.held = []; tr.seen = 0; tr.since = Date.now(); drawTraffic(true); });
        document.getElementById('tr-publish').addEventListener('click', () => publishDialog(isPattern(tr.filter) ? '' : tr.filter, '', false, 0));
        document.getElementById('tr-body').addEventListener('click', (e) => {
            const row = e.target.closest('tr[data-seq]');
            if (row) showMessage(tr.rows.find(m => String(m.seq) === row.dataset.seq));
        });
    }
    drawTraffic(true);
}

function drawTraffic(full) {
    const body = document.getElementById('tr-body');
    if (!body) return;
    document.getElementById('tr-pause').textContent = tr.paused ? `Resume${tr.held.length ? ` (${num(tr.held.length)} waiting)` : ''}` : 'Pause';
    const seconds = Math.max(1, (Date.now() - tr.since) / 1000);
    document.getElementById('tr-count').textContent = `${num(tr.seen)} message${tr.seen === 1 ? '' : 's'} · ${fmtRateNum(tr.seen / seconds)}/s${tr.skipped ? ` · ${num(tr.skipped)} too fast to show` : ''}`;
    if (full) body.innerHTML = tr.rows.map(messageRow).join('');
    document.getElementById('tr-empty').textContent = tr.rows.length ? '' : `Waiting for messages on ${tr.filter}${tr.q ? ` with "${tr.q}"` : ''}…`;
}

function onMessages(data) {
    if (activeTab !== 'traffic') return;
    const fresh = data.messages;
    if (!tr.seen) tr.since = Date.now();
    tr.seen += fresh.length + (data.skipped || 0);
    tr.skipped += data.skipped || 0;
    if (tr.paused) {
        tr.held.push(...fresh);
        if (tr.held.length > MAX_ROWS) tr.held.splice(0, tr.held.length - MAX_ROWS);
        drawTraffic(false);
        return;
    }
    const newest = fresh.slice().reverse();
    tr.rows = [...newest, ...tr.rows].slice(0, MAX_ROWS);
    const body = document.getElementById('tr-body');
    if (!body) return;
    // Add the new rows on top instead of drawing the table again
    body.insertAdjacentHTML('afterbegin', newest.map(messageRow).join(''));
    while (body.rows.length > MAX_ROWS) body.deleteRow(body.rows.length - 1);
    drawTraffic(false);
}

function showMessage(m) {
    if (!m) return;
    confirmDialog({
        title: 'Message', confirmLabel: 'Open the topic', wide: true,
        message: `<div class="info-grid">
                ${infoRow('Topic', topicHtml(m.topic))}
                ${infoRow('Time', escapeHtml(new Date(m.ts * 1000).toLocaleString()))}
                ${infoRow('Size', `${escapeHtml(fmtBytes(m.size))}, QoS ${m.qos}${m.retain ? ', retained' : ''}`)}
            </div>
            <pre class="payload">${escapeHtml(m.binary ? m.payload : prettyPayload(m.payload)) || '<span class="muted">(empty)</span>'}</pre>
            ${m.payload.length >= 512 ? '<div class="note">The live view keeps the first 512 characters; the topic has the whole last value.</div>' : ''}`,
    }).then(open => { if (open) switchTab('topics', m.topic); });
}

live.onMessages = onMessages;
onTab('traffic', () => {
    renderTraffic();
    if (validFilter(tr.filter)) connectLive(trafficParams());
}, () => drawTraffic(false));
