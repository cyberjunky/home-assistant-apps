// Freqtrade dashboard - Fleet: sort the overview's bot table and, with many bots, search and filter it.
// What is chosen here is what the whole overview is about: the tiles and the charts follow it.
// It all lives in this browser; the app's server has no part in it.

// The table's columns in order: key, and whether it holds a number
const FLEET_COLUMNS = [
    ['bot'], ['state'], ['exchange'], ['balance', true], ['profit', true], ['today', true],
    ['open', true], ['win', true], ['trades', true], ['link'],
];
const FLEET_KEY = 'freqtrade_fleet_views_v1';
// From this many bots on the search, the filters and the saved views show. Sorting always works
const FLEET_MIN = 6;
const FLEET_STATES = [['running', 'Running'], ['stopped', 'Stopped'], ['paused', 'Paused'], ['offline', 'Offline'], ['unknown', 'Unknown']];
const FLEET_LINKS = [['both', 'REST + WebSocket'], ['rest', 'REST only'], ['ws', 'WebSocket only'], ['offline', 'Offline']];
const fleetEmpty = () => ({search: '', currency: '', filters: {}, sort: '', direction: 'asc'});
function fleetClean(value) {
    const out = fleetEmpty();
    if (!value || typeof value !== 'object') return out;
    for (const key of ['search', 'currency']) if (typeof value[key] === 'string') out[key] = value[key].slice(0, 200);
    for (const [key] of FLEET_COLUMNS) if (typeof value.filters?.[key] === 'string') out.filters[key] = value.filters[key].slice(0, 200);
    if (FLEET_COLUMNS.some(c => c[0] === value.sort)) out.sort = value.sort;
    out.direction = value.direction === 'desc' ? 'desc' : 'asc';
    return out;
}
function fleetRead(storage) {
    try {
        const value = JSON.parse(storage.getItem(FLEET_KEY));
        return {active: fleetClean(value?.active), views: (Array.isArray(value?.views) ? value.views : [])
            .filter(v => v && typeof v.name === 'string' && v.name.trim()).slice(0, 20)
            .map(v => ({name: v.name.slice(0, 60), settings: fleetClean(v.settings)}))};
    } catch (e) { return {active: fleetEmpty(), views: []}; }
}
// Decimal commas are accepted; thousands separators are deliberately unsupported.
function fleetNumberFilter(text) {
    text = text.trim().replace(/,/g, '.');
    if (!text) return () => true;
    const number = '[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)';
    const range = text.match(new RegExp(`^(${number})\\s*\\.\\.\\s*(${number})$`));
    if (range) {
        const lo = Number(range[1]), hi = Number(range[2]);
        return lo <= hi ? v => v != null && Number.isFinite(v) && v >= lo && v <= hi : null;
    }
    const match = text.match(new RegExp(`^(>=|<=|!=|>|<|=)?\\s*(${number})$`));
    if (!match) return null;
    const n = Number(match[2]), op = match[1] || '=';
    return v => v != null && Number.isFinite(v) && ({'>': v > n, '<': v < n, '>=': v >= n,
        '<=': v <= n, '=': v === n, '!=': v !== n})[op];
}
function fleetValue(bot, key) {
    const c = bot.config || {}, p = bot.profit || {};
    if (key === 'bot') return [bot.name, c.strategy, bot.host, c.timeframe].filter(Boolean).join(' ');
    if (key === 'state') return bot.connected ? c.state || 'unknown' : 'offline';
    if (key === 'exchange') return c.exchange || '';
    if (key === 'link') return bot.connected ? (bot.ws_connected ? 'both' : 'rest') : (bot.ws_connected ? 'ws' : 'offline');
    if (!bot.connected) return null; // stale values never participate in numeric filtering/sorting
    const wins = Number(p.winning_trades || 0), losses = Number(p.losing_trades || 0);
    const value = {balance: bot.balance?.total_bot, profit: p.profit_all_coin, today: bot.today?.abs_profit,
        open: bot.count?.current, win: wins + losses ? 100 * wins / (wins + losses) : null, trades: p.closed_trade_count}[key];
    return value == null || !Number.isFinite(Number(value)) ? null : Number(value);
}
function fleetSelect(input, settings) {
    const s = fleetClean(settings), query = s.search.trim().toLocaleLowerCase();
    const rules = FLEET_COLUMNS.map(([key, numeric]) => [key, numeric, numeric ? fleetNumberFilter(s.filters[key] || '') : null]);
    const output = input.filter(b => {
        const c = b.config || {};
        if (query && ![b.name, b.host, c.strategy, c.exchange, c.timeframe].join(' ').toLocaleLowerCase().includes(query)) return false;
        if (s.currency && c.stake_currency !== s.currency) return false;
        return rules.every(([key, numeric, rule]) => {
            const filter = (s.filters[key] || '').trim().toLocaleLowerCase();
            if (!filter) return true;
            if (numeric) return rule ? rule(fleetValue(b, key)) : true;
            const value = String(fleetValue(b, key)).toLocaleLowerCase();
            return ['state', 'link'].includes(key) ? value === filter : value.includes(filter);
        });
    });
    if (!s.sort) return output;
    const numeric = FLEET_COLUMNS.find(c => c[0] === s.sort)[1];
    const monetary = ['balance', 'profit', 'today'].includes(s.sort);
    const direction = s.direction === 'desc' ? -1 : 1;
    return output.sort((a, b) => {
        const av = fleetValue(a, s.sort), bv = fleetValue(b, s.sort);
        if (av == null || bv == null) return av == null && bv == null ? 0 : av == null ? 1 : -1;
        if (monetary) {
            const currency = String(a.config?.stake_currency || '').localeCompare(String(b.config?.stake_currency || ''));
            if (currency) return currency;
        }
        const comparison = numeric ? av - bv : String(av).localeCompare(String(bv), undefined, {numeric: true, sensitivity: 'base'});
        return comparison * direction || String(a.slug).localeCompare(String(b.slug));
    });
}
let fleetStore = (() => { try { return fleetRead(localStorage); } catch (e) { return fleetRead(null); } })();
let fleetBuilt = false;

function fleetOn() { return bots().length >= FLEET_MIN; }
// What applies now: the sorting always, the search and the filters only while they can be seen
function fleetSettings() {
    const s = fleetStore.active;
    return fleetOn() ? s : {...fleetEmpty(), sort: s.sort, direction: s.direction};
}
function fleetFiltered() {
    const s = fleetStore.active;
    return !!(s.search.trim() || s.currency || Object.values(s.filters).some(v => v.trim()));
}
function fleetPersist() {
    try { localStorage.setItem(FLEET_KEY, JSON.stringify(fleetStore)); return true; }
    catch (e) { fleetMessage('This browser doesn\'t let the page store anything: the filters and views last until you close it.'); return false; }
}
function fleetMessage(message) {
    const el = document.getElementById('fleet-message');
    if (el) el.textContent = message;
}
function fleetChanged() {
    fleetPersist();
    renderOverview();
    renderOvHistory();
}

function fleetBuild(root) {
    fleetBuilt = true;
    root.innerHTML = `<div class="filters fleet">
        <input class="input" id="fleet-search" type="search" placeholder="Search bots, strategies, exchanges…" aria-label="Search bots">
        <select class="select" id="fleet-currency" aria-label="Currency"></select>
        <select class="select" id="fleet-view" aria-label="Saved views"></select>
        <input class="input" id="fleet-name" maxlength="60" placeholder="Name of the view" aria-label="Name of the view">
        <button type="button" class="btn" id="fleet-save" title="Keep the search, the filters and the sorting under this name. An existing name is replaced.">Save view</button>
        <button type="button" class="btn" id="fleet-delete">Delete view</button>
        <button type="button" class="btn" id="fleet-reset">Reset</button>
        <span class="range-text" id="fleet-count" role="status"></span>
    </div><div class="hint" id="fleet-message" role="status"></div>`;
    const el = id => document.getElementById(id);
    el('fleet-search').value = fleetStore.active.search;
    el('fleet-search').oninput = e => { fleetStore.active.search = e.target.value; fleetChanged(); };
    el('fleet-currency').onchange = e => { fleetStore.active.currency = e.target.value; fleetChanged(); };
    el('fleet-reset').onclick = () => {
        fleetStore.active = fleetEmpty();
        el('fleet-search').value = '';
        fleetViewOptions();
        fleetMessage('');
        fleetChanged();
    };
    el('fleet-view').onchange = e => {
        const view = fleetStore.views[Number(e.target.value)];
        el('fleet-name').value = view ? view.name : '';
        if (!view) return fleetToolbar(overviewBots(), modeBots());
        fleetStore.active = fleetClean(view.settings);
        el('fleet-search').value = fleetStore.active.search;
        fleetMessage('');
        fleetChanged();
    };
    el('fleet-save').onclick = () => {
        if (FLEET_COLUMNS.some(([key, numeric]) => numeric && fleetNumberFilter(fleetStore.active.filters[key] || '') === null)) {
            return fleetMessage('A number filter (marked red) can\'t be read. Correct it first.');
        }
        const name = el('fleet-name').value.trim();
        if (!name) return fleetMessage('Give the view a name first.');
        let index = fleetStore.views.findIndex(v => v.name === name);
        if (index < 0) {
            if (fleetStore.views.length >= 20) return fleetMessage('There are 20 views already. Delete one first.');
            index = fleetStore.views.push({name, settings: fleetEmpty()}) - 1;
        }
        fleetStore.views[index].settings = fleetClean(fleetStore.active);
        const stored = fleetPersist();
        fleetViewOptions(index);
        if (stored) fleetMessage(`View "${name}" saved in this browser.`);
        fleetToolbar(overviewBots(), modeBots());
    };
    el('fleet-delete').onclick = () => {
        const index = Number(el('fleet-view').value);
        if (!fleetStore.views[index]) return;
        const [gone] = fleetStore.views.splice(index, 1);
        fleetPersist();
        fleetViewOptions();
        el('fleet-name').value = '';
        fleetMessage(`View "${gone.name}" deleted. The filters stay as they are.`);
        fleetToolbar(overviewBots(), modeBots());
    };
    fleetViewOptions();
}
function fleetViewOptions(selected = -1) {
    const select = document.getElementById('fleet-view');
    select.replaceChildren(new Option(fleetStore.views.length ? 'Saved views…' : 'No saved views', '-1'),
        ...fleetStore.views.map((v, i) => new Option(v.name, String(i))));
    select.value = String(selected);
}
// The bar above the notices. selected: the bots after the filters; available: the bots of the chosen mode
function fleetToolbar(selected, available) {
    const root = document.getElementById('ov-fleet');
    root.hidden = !fleetOn();
    if (!fleetOn()) return;
    if (!fleetBuilt) fleetBuild(root);
    const currencies = [...new Set(available.map(b => b.config?.stake_currency).filter(Boolean))].sort();
    if (fleetStore.active.currency && !currencies.includes(fleetStore.active.currency)) currencies.push(fleetStore.active.currency);
    const select = document.getElementById('fleet-currency');
    const signature = JSON.stringify(currencies);
    if (select.dataset.options !== signature) {
        select.replaceChildren(new Option('All currencies', ''), ...currencies.map(c => new Option(c, c)));
        select.dataset.options = signature;
    }
    select.value = fleetStore.active.currency;
    select.hidden = currencies.length < 2;
    document.getElementById('fleet-delete').hidden = document.getElementById('fleet-view').value === '-1';
    document.getElementById('fleet-reset').hidden = !fleetFiltered() && !fleetStore.active.sort;
    document.getElementById('fleet-count').textContent = selected.length === available.length
        ? `${available.length} bots` : `${selected.length} of ${available.length} bots · the tiles and charts show these`;
}

// Puts the table in place, makes its headings sort, and with a fleet adds a row of filters under them.
// The table is rebuilt at every read, so the field being typed in gets its cursor back
function fleetRenderTable(root, html) {
    const active = document.activeElement;
    const focus = active?.dataset?.fleetFilter;
    const sortFocus = active?.dataset?.fleetSort;
    const start = focus && active.selectionStart, end = focus && active.selectionEnd;
    root.innerHTML = html;
    const header = root.querySelector('thead tr');
    if (!header) return;
    const filters = document.createElement('tr');
    filters.className = 'fleet-column-filters';
    FLEET_COLUMNS.forEach(([key, numeric], index) => {
        const th = header.children[index];
        const label = th.textContent;
        const selected = fleetStore.active.sort === key;
        const button = document.createElement('button');
        button.className = 'fleet-sort';
        button.type = 'button';
        button.textContent = label + (selected ? (fleetStore.active.direction === 'asc' ? ' ↑' : ' ↓') : '');
        button.dataset.fleetSort = key;
        button.title = 'Sort: up, down, then the order of the configuration';
        th.replaceChildren(button);
        th.setAttribute('aria-sort', selected ? (fleetStore.active.direction === 'asc' ? 'ascending' : 'descending') : 'none');
        button.onclick = () => {
            const s = fleetStore.active;
            if (s.sort !== key) { s.sort = key; s.direction = numeric ? 'desc' : 'asc'; }
            else if (s.direction === (numeric ? 'desc' : 'asc')) s.direction = numeric ? 'asc' : 'desc';
            else { s.sort = ''; s.direction = 'asc'; }
            fleetChanged();
        };
        if (!fleetOn()) return;
        const cell = document.createElement('td');
        let input;
        if (key === 'state' || key === 'link') {
            input = document.createElement('select');
            input.append(new Option('All', ''), ...(key === 'state' ? FLEET_STATES : FLEET_LINKS).map(([value, text]) => new Option(text, value)));
        } else {
            input = document.createElement('input');
            input.type = 'text';
            input.placeholder = numeric ? (key === 'win' ? '>50 (%)' : '>0, 10..50') : 'contains…';
            if (numeric) input.title = 'A number, or >0, <=100, !=0, 10..50. A decimal comma works; the win rate is in percent.';
        }
        input.className = 'input';
        input.dataset.fleetFilter = key;
        input.setAttribute('aria-label', 'Filter ' + label);
        input.value = fleetStore.active.filters[key] || '';
        if (numeric && fleetNumberFilter(input.value) === null) {
            input.setAttribute('aria-invalid', 'true');
            input.title = 'This filter can\'t be read and is left out';
        }
        input[key === 'state' || key === 'link' ? 'onchange' : 'oninput'] = e => {
            fleetStore.active.filters[key] = e.target.value;
            fleetMessage('');
            fleetChanged();
        };
        cell.append(input);
        filters.append(cell);
    });
    if (fleetOn()) header.after(filters);
    if (sortFocus) root.querySelector(`[data-fleet-sort="${sortFocus}"]`)?.focus({preventScroll: true});
    if (focus) {
        const input = root.querySelector(`[data-fleet-filter="${focus}"]`);
        if (input) { input.focus({preventScroll: true}); if (typeof start === 'number') input.setSelectionRange(start, end); }
    }
}
