// The numbers on the pages: difficulties, hash rates, chances and how long things take.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const web = file => fs.readFileSync(path.join(__dirname, '../src/web', file), 'utf8');

function core() {
    const context = vm.createContext({
        localStorage: {getItem: () => null},
        window: {location: {search: ''}, matchMedia: () => ({matches: true, addEventListener: () => {}}), parent: null},
        document: {documentElement: {dataset: {}}},
        setInterval: () => {},
        URLSearchParams,
    });
    context.window.parent = context.window;
    vm.runInContext(web('core.js'), context);
    return expression => vm.runInContext(expression, context);
}

test('difficulties read like AxeOS shows them', () => {
    const run = core();
    assert.equal(run('diff(4.21e9)'), '4.21 G');
    assert.equal(run('diff(149.3e12)'), '149 T');
    assert.equal(run('diff(512)'), '512');
    assert.equal(run('diff(null)'), '–');
});

test('hash rates pick their unit', () => {
    const run = core();
    assert.equal(run('rate(4810)'), '4.81 TH/s');
    assert.equal(run('rate(615.2)'), '615 GH/s');
    assert.equal(run('rate(1068731e6)'), '1.07 ZH/s');
    assert.equal(run('rate(850e9)'), '850 EH/s');
    assert.equal(run('hashes(2.61e15)'), '2.61 P');
});

test('chances read as 1 in N', () => {
    const run = core();
    assert.equal(run('oneIn(1 / 1240000)'), '1 in 1.24 M');
    assert.equal(run('oneIn(1 / 2950)'), '1 in 2,950');
    assert.equal(run('oneIn(0.75)'), '75%');
    assert.equal(run('oneIn(0)'), '–');
});

test('long waits read in years', () => {
    const run = core();
    assert.equal(run('years(2949)'), '2,949 years');
    assert.equal(run('years(0.5)'), '6 months');
    assert.equal(run('fmtDuration(30 * 86400 + 8 * 3600)'), '30d 8h');
    assert.equal(run('siRound(9)'), '1G');
    assert.equal(run('siRound(4)'), '10k');
});
