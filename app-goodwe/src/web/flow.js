// GoodWe Monitor dashboard - the live energy flow diagram and the daily Sankey.

// ---------------------------------------------------------------------
// Energy flow: SVG nodes and arms, a canvas draws dots travelling along the arms.
// Arms run FROM a satellite node TO the junction (or the other way for home and battery):
//   solar: always towards the junction; grid: in = import, reversed = export;
//   home: always away from the junction; bat: away = charging, reversed = discharging.
// ---------------------------------------------------------------------
const FLOW_W = 440, FLOW_H = 364, FLOW_JX = 220, FLOW_JY = 162, FLOW_NR = 44;
const FLOW_NODES = {solar: {x: 220, y: 46}, grid: {x: 62, y: 162}, home: {x: 378, y: 162}, bat: {x: 220, y: 294}};
const FLOW_ARMS = {
    solar: {from: {x: 220, y: 46 + FLOW_NR + 6}, to: {x: 220, y: FLOW_JY - 8}},
    grid: {from: {x: 62 + FLOW_NR + 6, y: FLOW_JY}, to: {x: FLOW_JX - 8, y: FLOW_JY}},
    home: {from: {x: FLOW_JX + 8, y: FLOW_JY}, to: {x: 378 - FLOW_NR - 6, y: FLOW_JY}},
    bat: {from: {x: 220, y: FLOW_JY + 8}, to: {x: 220, y: 294 - FLOW_NR - 6}},
};
const FLOW_ON_W = 30;

function flowSpeed(w) { const a = Math.abs(w); return a < 50 ? 0 : a < 300 ? 0.15 : a < 800 ? 0.22 : a < 2000 ? 0.32 : 0.45; }
function flowCount(w) { const a = Math.abs(w); return a < 50 ? 0 : a < 400 ? 2 : a < 1500 ? 3 : 4; }

const FLOW_ICONS = {
    solar: '<circle cx="0" cy="0" r="4.8"/>' + [0, 45, 90, 135, 180, 225, 270, 315].map(a => { const r = a * Math.PI / 180; return `<line x1="${(6.8 * Math.cos(r)).toFixed(2)}" y1="${(6.8 * Math.sin(r)).toFixed(2)}" x2="${(9.2 * Math.cos(r)).toFixed(2)}" y2="${(9.2 * Math.sin(r)).toFixed(2)}"/>`; }).join(''),
    grid: '<path d="M-6,9 L-2,-9 H2 L6,9 M-4.5,2 H4.5 M-3,-3.5 H3 M-8,9 H8"/>',
    home: '<polyline points="-9,-1 0,-8.5 9,-1"/><path d="M-6.5,-1 V8 H-2 V2.5 H2 V8 H6.5 V-1"/>',
    bat: '<rect x="-8" y="-5" width="14" height="10" rx="1.5"/><path d="M6,-2 H8 V2 H6 M-5,-2 V2 M-2,-2 V2 M1,-2 V2"/>',
};

class EnergyFlow {
    constructor(el) {
        this.el = el;
        this.values = {ppv: 0, pbat: 0, pgrid: 0, pload: 0, soc: 0};
        this.dots = [];
        this.last = 0;
        this.frame = 0;
        el.innerHTML = `<canvas></canvas><svg viewBox="0 0 ${FLOW_W} ${FLOW_H}"></svg>`;
        this.canvas = el.querySelector('canvas');
        this.svg = el.querySelector('svg');
        this.buildSvg();
        this.resize = () => this.fit();
        new ResizeObserver(this.resize).observe(el);
        this.fit();
        this.loop = (now) => this.draw(now);
        this.frame = requestAnimationFrame(this.loop);
    }

    buildSvg() {
        const arm = (key) => { const a = FLOW_ARMS[key]; return `<line id="flow-arm-${key}" class="arm" x1="${a.from.x}" y1="${a.from.y}" x2="${a.to.x}" y2="${a.to.y}"/>`; };
        const node = (key, label, side) => {
            const n = FLOW_NODES[key];
            // Grid and home: label above, sub text below. Solar and battery sit on the vertical arm,
            // so their label goes left of the node and the sub text right of it
            const labelAttrs = side ? `x="${n.x - FLOW_NR - 12}" y="${n.y + 4}" style="text-anchor:end"` : `x="${n.x}" y="${n.y - FLOW_NR - 10}"`;
            const subAttrs = side ? `x="${n.x + FLOW_NR + 12}" y="${n.y + 4}" style="text-anchor:start"` : `x="${n.x}" y="${n.y + FLOW_NR + 16}"`;
            return `<circle id="flow-node-${key}" class="node" cx="${n.x}" cy="${n.y}" r="${FLOW_NR}"/>
                <g id="flow-icon-${key}" class="icon" transform="translate(${n.x},${n.y - (key === 'bat' ? 14 : key === 'solar' ? 10 : 6)})">${FLOW_ICONS[key]}</g>
                <text id="flow-value-${key}" class="value" x="${n.x}" y="${n.y + (key === 'solar' ? 15 : key === 'bat' ? 30 : 20)}">–</text>
                <text class="label" ${labelAttrs}>${label}</text>
                <text id="flow-sub-${key}" class="sub" ${subAttrs}></text>`;
        };
        this.svg.innerHTML = `
            ${arm('solar')}${arm('grid')}${arm('home')}${arm('bat')}
            <circle class="junction" cx="${FLOW_JX}" cy="${FLOW_JY}" r="8"/><circle class="junction-core" cx="${FLOW_JX}" cy="${FLOW_JY}" r="4"/>
            ${node('solar', 'Solar', true)}${node('grid', 'Grid', false)}${node('home', 'Home', false)}${node('bat', 'Battery', true)}
            <circle class="soc-track" cx="${FLOW_NODES.bat.x}" cy="${FLOW_NODES.bat.y}" r="${FLOW_NR - 8}"/>
            <path id="flow-soc-arc" class="soc-arc" d=""/>
            <text id="flow-soc" class="soc-text" x="${FLOW_NODES.bat.x}" y="${FLOW_NODES.bat.y + 12}">–</text>`;
        // The battery's charging/discharging text goes under the node, the SoC sits in its ring
        this.svg.querySelector('#flow-value-bat').setAttribute('y', FLOW_NODES.bat.y + FLOW_NR + 16);
        this.svg.querySelector('#flow-value-bat').setAttribute('font-size', '10');
    }

    fit() {
        const dpr = window.devicePixelRatio || 1;
        const w = this.el.clientWidth || FLOW_W, h = this.el.clientHeight || FLOW_H;
        this.canvas.width = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.scale = {x: w / FLOW_W * dpr, y: h / FLOW_H * dpr};
    }

    colors() {
        return {solar: cssVar('--solar'), imp: cssVar('--grid-imp'), exp: cssVar('--grid-exp'), load: cssVar('--load'), chg: cssVar('--bat-chg'), dis: cssVar('--bat-dis'), idle: cssVar('--idle'), soc: cssVar('--soc'), warn: cssVar('--warning-color'), bad: cssVar('--danger-color')};
    }

    update(v) {
        this.values = v;
        const c = this.colors();
        const pvOn = v.ppv > FLOW_ON_W, gImp = v.pgrid > FLOW_ON_W, gExp = v.pgrid < -FLOW_ON_W;
        const bChg = v.pbat > FLOW_ON_W, bDis = v.pbat < -FLOW_ON_W, hOn = v.pload > FLOW_ON_W;
        const gridColor = gImp ? c.imp : gExp ? c.exp : c.idle;
        const batColor = bChg ? c.chg : bDis ? c.dis : c.idle;
        const set = (key, on, color) => {
            const node = this.svg.querySelector(`#flow-node-${key}`);
            node.classList.toggle('on', on);
            node.style.stroke = on ? color : '';
            node.style.fill = on ? color + '22' : '';
            this.svg.querySelector(`#flow-icon-${key}`).style.stroke = on ? color : c.idle;
            const arm = this.svg.querySelector(`#flow-arm-${key}`);
            arm.classList.toggle('on', on);
            arm.style.stroke = on ? color : '';
            const value = this.svg.querySelector(`#flow-value-${key}`);
            value.classList.toggle('off', !on);
        };
        set('solar', pvOn, c.solar);
        set('grid', gImp || gExp, gridColor);
        set('home', pvOn || gImp || bDis || hOn, c.load);
        set('bat', bChg || bDis, batColor);
        this.svg.querySelector('#flow-value-solar').textContent = fmtW(v.ppv);
        this.svg.querySelector('#flow-value-grid').textContent = fmtW(v.pgrid);
        this.svg.querySelector('#flow-value-home').textContent = fmtW(v.pload);
        this.svg.querySelector('#flow-value-bat').textContent = bChg ? `charging ${fmtW(v.pbat)}` : bDis ? `discharging ${fmtW(v.pbat)}` : 'idle';
        const gridSub = this.svg.querySelector('#flow-sub-grid');
        gridSub.textContent = gImp ? '↓ import' : gExp ? '↑ export' : 'idle';
        gridSub.style.fill = gridColor;
        const homeSub = this.svg.querySelector('#flow-sub-home');
        homeSub.textContent = v.backup > FLOW_ON_W ? `backup ${fmtW(v.backup)}` : '';
        homeSub.style.fill = c.load;
        const batSub = this.svg.querySelector('#flow-sub-bat');
        batSub.textContent = v.held ? 'held' : '';
        batSub.style.fill = c.soc;

        // State of charge ring
        const soc = Math.max(0, Math.min(100, Number(v.soc) || 0));
        const socColor = soc > 60 ? c.chg : soc > 20 ? c.warn : c.bad;
        const arc = this.svg.querySelector('#flow-soc-arc');
        if (soc < 1) {
            arc.setAttribute('d', '');
        } else {
            const r = FLOW_NR - 8, bx = FLOW_NODES.bat.x, by = FLOW_NODES.bat.y;
            const a = Math.min(soc, 99.9) / 100 * 2 * Math.PI;
            arc.setAttribute('d', `M ${bx},${by - r} A ${r},${r} 0 ${soc > 50 ? 1 : 0},1 ${(bx + r * Math.sin(a)).toFixed(2)},${(by - r * Math.cos(a)).toFixed(2)}`);
        }
        arc.style.stroke = socColor;
        const socText = this.svg.querySelector('#flow-soc');
        socText.textContent = missing(v.soc) ? '–' : `${Math.round(soc)}%`;
        socText.style.fill = socColor;

        // Dots: keep each dot's own position across updates
        const wanted = [
            {arm: 'solar', reverse: false, color: c.solar, w: pvOn ? v.ppv : 0},
            {arm: 'grid', reverse: gExp, color: gExp ? c.exp : c.imp, w: gImp || gExp ? v.pgrid : 0},
            {arm: 'home', reverse: false, color: c.load, w: hOn ? v.pload : 0},
            {arm: 'bat', reverse: bDis, color: bChg ? c.chg : c.dis, w: bChg || bDis ? v.pbat : 0},
        ];
        const next = [];
        wanted.forEach(({arm, reverse, color, w}) => {
            const n = flowCount(w), s = flowSpeed(w);
            const existing = this.dots.filter(d => d.arm === arm && d.reverse === reverse);
            for (let i = 0; i < n; i++) next.push({arm, reverse, color, spd: s, t: existing[i] ? existing[i].t : i / Math.max(n, 1)});
        });
        this.dots = next;
    }

    draw(now) {
        this.frame = requestAnimationFrame(this.loop);
        // Nothing to draw while the dashboard tab is hidden
        if (!this.el.isConnected || this.el.offsetParent === null) { this.last = now; return; }
        const ctx = this.canvas.getContext('2d');
        const dt = Math.min((now - (this.last || now)) / 1000, 0.05);
        this.last = now;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.save();
        ctx.scale(this.scale.x, this.scale.y);
        for (const dot of this.dots) {
            dot.t = (dot.t + dot.spd * dt) % 1;
            const arm = FLOW_ARMS[dot.arm];
            const t = dot.reverse ? 1 - dot.t : dot.t;
            const x = arm.from.x + (arm.to.x - arm.from.x) * t, y = arm.from.y + (arm.to.y - arm.from.y) * t;
            const glow = ctx.createRadialGradient(x, y, 0, x, y, 7);
            glow.addColorStop(0, dot.color + 'aa');
            glow.addColorStop(1, dot.color + '00');
            ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fillStyle = glow; ctx.fill();
            ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fillStyle = dot.color; ctx.fill();
            ctx.beginPath(); ctx.arc(x, y, 1, 0, Math.PI * 2); ctx.fillStyle = '#ffffff'; ctx.fill();
        }
        ctx.restore();
    }
}

// ---------------------------------------------------------------------
// Sankey: sources (solar, battery, grid) on the left, destinations (home, battery, grid) on the right.
// flow: {links: {solar_load, solar_batt, solar_grid, batt_load, grid_load, grid_batt}, sources, destinations}
// ---------------------------------------------------------------------
function renderSankey(el, flow) {
    const L = flow.links || {};
    const links = [
        {s: 'solar', t: 'load', v: L.solar_load}, {s: 'solar', t: 'batt', v: L.solar_batt}, {s: 'solar', t: 'grid', v: L.solar_grid},
        {s: 'battery', t: 'load', v: L.batt_load}, {s: 'grid', t: 'load', v: L.grid_load}, {s: 'grid', t: 'batt', v: L.grid_batt},
    ].filter(l => (l.v || 0) > 0.005);
    if (!links.length) { el.innerHTML = '<div class="empty-state">No energy recorded for this day</div>'; return; }
    const colors = {solar: cssVar('--solar'), battery: cssVar('--bat-dis'), grid: cssVar('--grid-imp'), load: cssVar('--load'), batt: cssVar('--bat-chg'), gridOut: cssVar('--grid-exp')};
    const names = {solar: 'Solar', battery: 'Battery', grid: 'Grid', load: 'Home', batt: 'Battery', gridOut: 'Grid export'};
    const left = ['solar', 'battery', 'grid'].map(k => ({key: k, v: links.filter(l => l.s === k).reduce((a, l) => a + l.v, 0)})).filter(n => n.v > 0);
    const right = ['load', 'batt', 'grid'].map(k => ({key: k === 'grid' ? 'gridOut' : k, link: k, v: links.filter(l => l.t === k).reduce((a, l) => a + l.v, 0)})).filter(n => n.v > 0);
    const W = 600, H = 300, pad = 12, gap = 18, nodeW = 14, labelW = 128;
    const total = Math.max(left.reduce((a, n) => a + n.v, 0), right.reduce((a, n) => a + n.v, 0));
    const usable = H - 2 * pad - gap * (Math.max(left.length, right.length) - 1);
    const scale = usable / total;
    const place = (nodes, x) => {
        let y = pad + (usable - nodes.reduce((a, n) => a + n.v, 0) * scale - gap * (Math.max(left.length, right.length) - nodes.length)) / 2;
        nodes.forEach(n => { n.x = x; n.y = y; n.h = Math.max(2, n.v * scale); n.used = 0; y += n.h + gap; });
    };
    place(left, labelW);
    place(right, W - labelW - nodeW);
    const byKey = {};
    left.forEach(n => { byKey['s:' + n.key] = n; });
    right.forEach(n => { byKey['t:' + n.link] = n; });
    let ribbons = '';
    links.forEach(l => {
        const a = byKey['s:' + l.s], b = byKey['t:' + l.t];
        const h = l.v * scale;
        const y0 = a.y + a.used, y1 = b.y + b.used;
        a.used += h; b.used += h;
        const x0 = a.x + nodeW, x1 = b.x, mx = (x0 + x1) / 2;
        const d = `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1} L${x1},${y1 + h} C${mx},${y1 + h} ${mx},${y0 + h} ${x0},${y0 + h} Z`;
        ribbons += `<path class="ribbon" d="${d}" fill="${colors[l.s]}" data-tip="${escapeHtml(names[l.s])} → ${escapeHtml(names[l.t === 'grid' ? 'gridOut' : l.t])}" data-value="${l.v}"></path>`;
    });
    const nodeHtml = (n, isLeft) => {
        const tx = isLeft ? n.x - 8 : n.x + nodeW + 8;
        return `<rect x="${n.x}" y="${n.y}" width="${nodeW}" height="${n.h}" rx="2" fill="${colors[n.key]}"/>
            <text class="node-label" x="${tx}" y="${n.y + n.h / 2 - 2}" text-anchor="${isLeft ? 'end' : 'start'}">${names[n.key]}</text>
            <text class="node-value" x="${tx}" y="${n.y + n.h / 2 + 11}" text-anchor="${isLeft ? 'end' : 'start'}">${num(n.v, 2)} kWh · ${num(n.v / total * 100)}%</text>`;
    };
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Energy flow of the day">${ribbons}${left.map(n => nodeHtml(n, true)).join('')}${right.map(n => nodeHtml(n, false)).join('')}</svg>`;
    el.querySelectorAll('.ribbon').forEach(r => {
        r.addEventListener('pointermove', e => showTip(e.clientX, e.clientY, r.dataset.tip, [{value: fmtKwh(Number(r.dataset.value), 2), name: `${num(Number(r.dataset.value) / total * 100)}% of the day`}]));
        r.addEventListener('pointerleave', hideTip);
    });
}
