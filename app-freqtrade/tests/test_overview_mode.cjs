// The overview's trading mode: which bots Live, Dry-run and All are about.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const web = file => fs.readFileSync(path.join(__dirname, '../src/web', file), 'utf8');

function dashboard(mode = undefined) {
    const context = vm.createContext({LS: 'test-', localStorage: {getItem: () => null},
        app: {status: {config: {overview_mode: mode}}}, buildChips: () => {},
        bots: () => [
            {slug: 'live', name: 'Live', config: {dry_run: false, stake_currency: 'EUR'}},
            {slug: 'paper', name: 'Paper', config: {dry_run: true, stake_currency: 'USDT'}},
            {slug: 'unknown', name: 'Unknown', config: {}},
        ], botColor: () => '#123', missing: v => v == null || Number.isNaN(v), onTab: () => {}});
    for (const file of ['fleet.js', 'overview.js']) vm.runInContext(web(file), context);
    vm.runInContext('initOverviewScope()', context);
    return expression => JSON.parse(JSON.stringify(vm.runInContext(expression, context)));
}

test('without a setting every bot shows', () => {
    assert.equal(dashboard()('overviewBots().length'), 3);
    assert.equal(dashboard('invalid')('overviewBots().length'), 3);
});
test('a mode leaves out the other money and the bots that do not say; the currency follows', () => {
    const run = dashboard('live');
    assert.deepEqual(run('overviewBots().map(b => b.slug)'), ['live']);
    assert.equal(run('overviewCurrency()'), 'EUR');
    assert.deepEqual(run("ovScope = 'dry'; overviewBots().map(b => b.slug)"), ['paper']);
    assert.equal(run('overviewCurrency()'), 'USDT');
    assert.equal(run("ovScope = 'all'; overviewBots().length"), 3);
});
test('the configured mode is the start; a manual choice stays through later reads', () => {
    const run = dashboard('dry');
    assert.deepEqual(run('overviewBots().map(b => b.slug)'), ['paper']);
    assert.deepEqual(run("ovScope = 'live'; initOverviewScope(); overviewBots().map(b => b.slug)"), ['live']);
    assert.deepEqual(dashboard('dry')('overviewBots().map(b => b.slug)'), ['paper']);
});
