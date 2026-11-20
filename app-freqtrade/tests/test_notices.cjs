// The overview's notices: a few bots each say what is wrong, a fleet shares one line.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

const web = file => fs.readFileSync(path.join(__dirname, '../src/web', file), 'utf8');

function notices(bots) {
    const el = {innerHTML: '', hidden: false};
    const ctx = vm.createContext({LS: '', localStorage: {getItem: () => null},
        bots: () => bots.map((b, i) => ({name: `bot${i}`, config: {dry_run: false}, ...b})),
        app: {status: {config: {overview_mode: 'all', poll_seconds: 30}}}, buildChips: () => {},
        escapeHtml: s => String(s), onTab: () => {}, document: {getElementById: () => el}});
    for (const file of ['health.js', 'fleet.js', 'overview.js']) vm.runInContext(web(file), ctx);
    vm.runInContext('initOverviewScope(); renderOvNotices()', ctx);
    return el.innerHTML;
}
const count = html => (html.match(/setup-hint/g) || []).length;

test('a healthy fleet has no notices', () => {
    assert.equal(notices([{connected: true, ws_connected: true}]), '');
});
test('a few bots in trouble each say why', () => {
    const html = notices([{connected: false, error: 'refused'}, {connected: false, error: 'timeout'}, {connected: true, ws_connected: true}]);
    assert.equal(count(html), 2);
    assert.match(html, /bot0<\/b> can't be reached: refused/);
});
test('a fleet in trouble shares one notice', () => {
    const html = notices(Array.from({length: 20}, () => ({connected: false})));
    assert.equal(count(html), 1);
    assert.match(html, /20 of 20 bots/);
});
test('a bot that answers in part is not called unreachable', () => {
    const html = notices([{connected: false, error: 'balance: HTTP 500', diagnostics: {api_status: 'reachable'}}]);
    assert.match(html, /answers, but not everything could be read: balance: HTTP 500/);
    assert.doesNotMatch(html, /reached/);
});
test('a missing WebSocket is its own notice', () => {
    const html = notices([{connected: true, ws_connected: false, ws_error: 'failed'}]);
    assert.match(html, /no live events/);
    assert.doesNotMatch(html, /reached/);
});
