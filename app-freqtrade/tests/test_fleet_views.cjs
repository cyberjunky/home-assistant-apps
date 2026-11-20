const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/web/fleet.js'), 'utf8'), ctx);
const select = (bots, settings) => Array.from(ctx.fleetSelect(bots, settings), b => b.slug);
function bot(slug, n, currency = 'USD', connected = true) {
    return {slug, name: slug, host:'server', connected, ws_connected:connected,
        config:{strategy:'Trend', state:'running', exchange:'kraken', stake_currency:currency},
        balance:{total_bot:n}, profit:{profit_all_coin:n, winning_trades:3, losing_trades:1,closed_trade_count:n},
        count:{current:n}, today:{abs_profit:n}};
}
test('numeric comparisons, decimal commas, ranges and invalid expressions', () => {
    for (const [filter, value, result] of [['>0',0,false],['>=0',0,true],['<=-1,5',-2,true],['10..50',10,true],['10..50',51,false],['!=2',3,true],['.5',.5,true]]) {
        assert.equal(ctx.fleetNumberFilter(filter)(value), result, filter);
    }
    for (const filter of ['oops','1..-2','>=','1,000.00','Infinity']) assert.equal(ctx.fleetNumberFilter(filter), null,filter);
    assert.equal(ctx.fleetNumberFilter('>0')(null), false);
});
test('combined search, per-column text, state and currency filters', () => {
    const a = bot('Alpha',10), b = bot('Beta',20,'EUR'), c = bot('Offline',30,'USD',false);
    assert.deepEqual(select([a,b,c],{search:'TREND',currency:'USD',filters:{state:'running',exchange:'KRA'}}),['Alpha']);
    assert.deepEqual(select([a,b,c],{filters:{link:'offline'}}),['Offline']);
    assert.deepEqual(select([a,b,c],{filters:{win:'>=75',bot:'bet'}}),['Beta']);
});
test('numeric sorting uses raw values and puts unknown values last in both directions', () => {
    const values = [bot('ten',10),bot('two',2),bot('zero',0),bot('stale',500,'USD',false)];
    assert.deepEqual(select(values,{sort:'profit',direction:'asc'}),['zero','two','ten','stale']);
    assert.deepEqual(select(values,{sort:'profit',direction:'desc'}),['ten','two','zero','stale']);
    assert.deepEqual(select(values,{filters:{profit:'>=0'}}),['ten','two','zero']);
    assert.deepEqual(values.map(b=>b.slug),['ten','two','zero','stale']);
});
test('amount sorting groups currencies instead of treating unlike currencies as comparable', () => {
    assert.deepEqual(select([bot('USD',100),bot('EUR2',2,'EUR'),bot('EUR9',9,'EUR')],{sort:'balance',direction:'desc'}),['EUR9','EUR2','USD']);
});
test('invalid numeric input is ignored and never silently converted to zero', () => {
    assert.deepEqual(select([bot('one',1),bot('two',2)],{filters:{profit:'broken'}}),['one','two']);
});
test('saved views survive JSON round-trip; active filters and saved settings are independent', () => {
    const settings={search:'Alpha',currency:'USD',filters:{profit:'>0'},sort:'profit',direction:'desc'};
    const stored=JSON.stringify({active:settings,views:[{name:'Winners',settings}]});
    const state=ctx.fleetRead({getItem:()=>stored});
    state.active.filters.profit='<0';
    assert.equal(state.views[0].settings.filters.profit,'>0');
    assert.deepEqual(select([bot('Alpha',2),bot('Beta',3)],state.views[0].settings),['Alpha']);
});
test('malformed or unavailable storage does not stop the dashboard', () => {
    for (const storage of [null,{getItem:()=>'{broken'},{getItem:()=>{throw Error('blocked');}}]) {
        assert.equal(ctx.fleetRead(storage).views.length,0);
    }
    const state=ctx.fleetRead({getItem:()=>JSON.stringify({active:{sort:'__proto__',filters:{unknown:'evil'}},views:'bad'})});
    assert.equal(state.active.sort,'');
    assert.equal(Object.keys(state.active.filters).length,0);
});
test('newly discovered bots join matching views without being pinned to saved bot IDs', () => {
    const settings={filters:{bot:'worker',profit:'>0'}};
    assert.deepEqual(select([bot('worker-1',1)],settings),['worker-1']);
    assert.deepEqual(select([bot('worker-1',1),bot('worker-2',2)],settings),['worker-1','worker-2']);
});
