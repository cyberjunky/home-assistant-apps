const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({navigator: {language: 'en'}, escapeHtml: s => s});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../src/web/health.js'), 'utf8'), context);
function model(overrides = {}, now = 1000) {
    context.bot = {connected: true, updated: 990, config: {state: 'stopped'}, count: {current: 2},
        diagnostics: {api_status: 'reachable', state_updated: 990, details_updated: 900, stale_after: 90, details_stale_after: 900}, ...overrides};
    return JSON.parse(JSON.stringify(vm.runInContext(`healthModel(bot, ${now})`, context)));
}
test('stopped trader with open positions remains reachable and fresh', () => {
    const h = model(); assert.equal(h.api, 'reachable'); assert.ok(h.stoppedOpen); assert.ok(!h.stale);
});
test('old trading state is last known, not proof of a stopped trader', () => {
    const h = model({}, 1200); assert.ok(h.stale); assert.ok(!h.stateFresh); assert.ok(!h.stoppedOpen);
});
test('unreachable API cannot confirm a recently cached state', () => {
    const h = model({connected: false, diagnostics: {api_status: 'unreachable', state_updated: 990}});
    assert.ok(!h.stateFresh); assert.equal(h.api, 'unreachable');
});
test('never read is unknown and stale; clock skew does not make negative ages', () => {
    assert.equal(model({updated: null}).dataAge, null);
    assert.equal(model({updated: 2000}).dataAge, 0);
});
