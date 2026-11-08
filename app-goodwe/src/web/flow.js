// GoodWe Monitor dashboard - the live energy flow diagram and the daily Sankey.

// ---------------------------------------------------------------------
// Energy flow: four nodes (solar top, grid left, home right, battery bottom) joined by the
// paths energy can actually take. The live power is split over those paths with the same
// priority model as the daily energies (solar → home, then battery, then grid; battery →
// home; grid → home, then battery), so the diagram shows where the solar goes, not just
// which way each wire points. A canvas draws dots travelling along the active paths.
// ---------------------------------------------------------------------
const FLOW_W = 560, FLOW_H = 462, FLOW_NR = 50;
const FLOW_C = {x: 280, y: 232};
const FLOW_NODES = {solar: {x: 280, y: 82}, grid: {x: 76, y: 232}, home: {x: 484, y: 232}, bat: {x: 280, y: 382}};
// Paths between node centres (the nodes hide the ends). A path through the middle is
// straight; one between neighbours bends towards its outer corner.
const FLOW_LINKS = {
    solar_home: {from: 'solar', to: 'home'},
    solar_bat: {from: 'solar', to: 'bat'},
    solar_grid: {from: 'solar', to: 'grid'},
    grid_home: {from: 'grid', to: 'home'},
    grid_bat: {from: 'grid', to: 'bat'},
    bat_home: {from: 'bat', to: 'home'},
    bat_grid: {from: 'bat', to: 'grid'},
};
const FLOW_ON_W = 30;

function flowControl(a, b) {
    const mid = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
    if (Math.abs(mid.x - FLOW_C.x) < 1 && Math.abs(mid.y - FLOW_C.y) < 1) return mid;  // straight through the middle
    // The outer corner of the two nodes, pulled a third of the way back towards the centre
    const corner = {x: Math.abs(a.x - FLOW_C.x) > Math.abs(b.x - FLOW_C.x) ? a.x : b.x, y: Math.abs(a.y - FLOW_C.y) > Math.abs(b.y - FLOW_C.y) ? a.y : b.y};
    return {x: corner.x + (FLOW_C.x - corner.x) * 0.34, y: corner.y + (FLOW_C.y - corner.y) * 0.34};
}
for (const link of Object.values(FLOW_LINKS)) {
    link.a = FLOW_NODES[link.from];
    link.b = FLOW_NODES[link.to];
    link.c = flowControl(link.a, link.b);
}
function flowPoint(link, t) {
    const u = 1 - t;
    return {x: u * u * link.a.x + 2 * u * t * link.c.x + t * t * link.b.x, y: u * u * link.a.y + 2 * u * t * link.c.y + t * t * link.b.y};
}

// Live power over the paths (W), with the daily energies' priority model
function splitFlows(v) {
    const solar = Math.max(v.ppv, 0), load = Math.max(v.pload, 0);
    const imp = Math.max(v.pgrid, 0), exp = Math.max(-v.pgrid, 0);
    const chg = Math.max(v.pbat, 0), dis = Math.max(-v.pbat, 0);
    let rest = load;
    const solarHome = Math.min(solar, rest); rest -= solarHome;
    const batHome = Math.min(dis, rest); rest -= batHome;
    const gridHome = Math.min(imp, rest);
    let spare = solar - solarHome;
    const solarBat = Math.min(spare, chg); spare -= solarBat;
    const solarGrid = Math.min(spare, exp);
    return {
        solar_home: solarHome, solar_bat: solarBat, solar_grid: solarGrid,
        grid_home: gridHome, grid_bat: Math.max(0, Math.min(imp - gridHome, chg - solarBat)),
        bat_home: batHome, bat_grid: Math.max(0, Math.min(dis - batHome, exp - solarGrid)),
    };
}

const flowWidth = w => 2 + Math.min(5, Math.sqrt(w / 3600) * 5);
const flowSpeed = w => 0.12 + Math.min(0.38, Math.sqrt(w / 3600) * 0.38);
const flowCount = w => w < FLOW_ON_W ? 0 : w < 400 ? 2 : w < 1500 ? 3 : 4;

const FLOW_ICONS = {
    solar: '<circle cx="0" cy="0" r="4.8"/>' + [0, 45, 90, 135, 180, 225, 270, 315].map(a => { const r = a * Math.PI / 180; return `<line x1="${(6.8 * Math.cos(r)).toFixed(2)}" y1="${(6.8 * Math.sin(r)).toFixed(2)}" x2="${(9.2 * Math.cos(r)).toFixed(2)}" y2="${(9.2 * Math.sin(r)).toFixed(2)}"/>`; }).join(''),
    grid: '<path d="M-6,9 L-2,-9 H2 L6,9 M-4.5,2 H4.5 M-3,-3.5 H3 M-8,9 H8"/>',
    home: '<polyline points="-9,-1 0,-8.5 9,-1"/><path d="M-6.5,-1 V8 H-2 V2.5 H2 V8 H6.5 V-1"/>',
    bat: '<rect x="-8" y="-5" width="14" height="10" rx="1.5"/><path d="M6,-2 H8 V2 H6 M-5,-2 V2 M-2,-2 V2 M1,-2 V2"/>',
};
const FLOW_LABELS = {solar: 'Solar', grid: 'Grid', home: 'Home', bat: 'Battery'};

class EnergyFlow {
    constructor(el) {
        this.el = el;
        this.dots = [];
        this.last = 0;
        el.innerHTML = `<canvas></canvas><svg viewBox="0 0 ${FLOW_W} ${FLOW_H}" role="img" aria-label="Live energy flow"></svg>`;
        this.canvas = el.querySelector('canvas');
        this.svg = el.querySelector('svg');
        this.buildSvg();
        new ResizeObserver(() => this.fit()).observe(el);
        this.fit();
        this.loop = (now) => this.draw(now);
        requestAnimationFrame(this.loop);
    }

    buildSvg() {
        const path = l => `M${l.a.x},${l.a.y} Q${l.c.x},${l.c.y} ${l.b.x},${l.b.y}`;
        const tracks = Object.entries(FLOW_LINKS).filter(([k]) => k !== 'bat_grid')
            .map(([k, l]) => `<path id="flow-track-${k}" class="track" d="${path(l)}"/>`).join('');
        const lines = Object.entries(FLOW_LINKS)
            .map(([k, l]) => `<linearGradient id="flow-grad-${k}" gradientUnits="userSpaceOnUse" x1="${l.a.x}" y1="${l.a.y}" x2="${l.b.x}" y2="${l.b.y}"><stop offset="0"/><stop offset="1"/></linearGradient>`).join('');
        const flows = Object.entries(FLOW_LINKS)
            .map(([k, l]) => `<path id="flow-line-${k}" class="line" d="${path(l)}" stroke="url(#flow-grad-${k})"/>`).join('');
        const node = key => {
            const n = FLOW_NODES[key];
            // Labels sit above the nodes, except the battery's: below its state-of-charge ring
            const labelY = key === 'bat' ? n.y + FLOW_NR + 26 : n.y - FLOW_NR - 14;
            return `<g id="flow-node-${key}" class="node-g">
                <circle class="halo" cx="${n.x}" cy="${n.y}" r="${FLOW_NR + 9}"/>
                <circle class="node" cx="${n.x}" cy="${n.y}" r="${FLOW_NR}"/>
                <g class="icon" transform="translate(${n.x},${n.y - 24}) scale(1.15)">${FLOW_ICONS[key]}</g>
                <text class="value" x="${n.x}" y="${n.y + 6}">–</text>
                <text class="sub" x="${n.x}" y="${n.y + 24}"></text>
                <text class="label" x="${n.x}" y="${labelY}">${FLOW_LABELS[key]}</text>
            </g>`;
        };
        const b = FLOW_NODES.bat;
        this.svg.innerHTML = `<defs>${lines}</defs>${tracks}${flows}
            ${node('solar')}${node('grid')}${node('home')}${node('bat')}
            <circle class="soc-track" cx="${b.x}" cy="${b.y}" r="${FLOW_NR + 5}"/>
            <path id="flow-soc-arc" class="soc-arc" d=""/>
            <g id="flow-badge-solar" class="badge"><rect rx="9" height="18"/><text></text></g>
            <g id="flow-badge-home" class="badge"><rect rx="9" height="18"/><text></text></g>`;
    }

    fit() {
        const dpr = window.devicePixelRatio || 1;
        const w = this.el.clientWidth || FLOW_W, h = this.el.clientHeight || FLOW_H;
        this.canvas.width = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.scale = {x: w / FLOW_W * dpr, y: h / FLOW_H * dpr};
    }

    colors() {
        return {solar: cssVar('--solar'), imp: cssVar('--grid-imp'), exp: cssVar('--grid-exp'), load: cssVar('--load'), chg: cssVar('--bat-chg'), dis: cssVar('--bat-dis'), idle: cssVar('--idle'), warn: cssVar('--warning-color'), bad: cssVar('--danger-color'), ok: cssVar('--success-color')};
    }

    setNode(key, on, color, value, sub) {
        const g = this.svg.querySelector(`#flow-node-${key}`);
        g.classList.toggle('on', on);
        g.style.setProperty('--node-color', on ? color : 'var(--divider-color)');
        g.querySelector('.icon').style.stroke = on ? color : 'var(--idle)';
        g.querySelector('.value').textContent = value;
        g.querySelector('.value').classList.toggle('off', !on);
        g.querySelector('.sub').textContent = sub || '';
    }

    badge(id, text, color, anchor) {
        const g = this.svg.querySelector(id);
        if (!text) { g.style.display = 'none'; return; }
        g.style.display = '';
        const label = g.querySelector('text');
        label.textContent = text;
        const width = text.length * 6.4 + 18;
        const rect = g.querySelector('rect');
        rect.setAttribute('x', anchor.x - width / 2);
        rect.setAttribute('y', anchor.y - 9);
        rect.setAttribute('width', width);
        rect.style.fill = color + '26';
        rect.style.stroke = color;
        label.setAttribute('x', anchor.x);
        label.setAttribute('y', anchor.y + 4);
        label.style.fill = color;
    }

    update(v) {
        const c = this.colors();
        const flows = splitFlows(v);
        const source = {solar: c.solar, grid: c.imp, bat: c.dis};
        const target = {home: c.load, bat: c.chg, grid: c.exp};
        const gImp = v.pgrid > FLOW_ON_W, gExp = v.pgrid < -FLOW_ON_W;
        const bChg = v.pbat > FLOW_ON_W, bDis = v.pbat < -FLOW_ON_W;

        // Paths: width and colour from the power each one carries
        for (const [key, link] of Object.entries(FLOW_LINKS)) {
            const w = flows[key] || 0, on = w >= FLOW_ON_W;
            const line = this.svg.querySelector(`#flow-line-${key}`);
            line.style.display = on ? '' : 'none';
            line.style.strokeWidth = flowWidth(w);
            const stops = this.svg.querySelectorAll(`#flow-grad-${key} stop`);
            stops[0].setAttribute('stop-color', source[link.from]);
            stops[1].setAttribute('stop-color', target[link.to]);
            const track = this.svg.querySelector(`#flow-track-${key}`);
            if (track) track.classList.toggle('busy', on);
        }

        // Nodes
        const soc = Math.max(0, Math.min(100, Number(v.soc) || 0));
        this.setNode('solar', v.ppv > FLOW_ON_W, c.solar, fmtW(v.ppv), missing(v.solarToday) ? '' : `${fmtKwh(v.solarToday, 1)} today`);
        this.setNode('grid', gImp || gExp, gImp ? c.imp : c.exp, fmtW(Math.abs(v.pgrid)), gImp ? 'importing' : gExp ? 'exporting' : 'idle');
        this.setNode('home', v.pload > FLOW_ON_W, c.load, fmtW(v.pload), missing(v.homeToday) ? '' : `${fmtKwh(v.homeToday, 1)} today`);
        this.setNode('bat', bChg || bDis, bChg ? c.chg : c.dis, missing(v.soc) ? '–' : `${Math.round(soc)}%`,
            bChg ? `▲ ${fmtW(v.pbat)}` : bDis ? `▼ ${fmtW(v.pbat)}` : v.held ? 'held' : 'idle');

        // State of charge ring around the battery
        const socColor = soc > 60 ? c.chg : soc > 20 ? c.warn : c.bad;
        const arc = this.svg.querySelector('#flow-soc-arc');
        if (soc < 1 || missing(v.soc)) {
            arc.setAttribute('d', '');
        } else {
            const r = FLOW_NR + 5, bx = FLOW_NODES.bat.x, by = FLOW_NODES.bat.y;
            const a = Math.min(soc, 99.9) / 100 * 2 * Math.PI;
            arc.setAttribute('d', `M ${bx},${by - r} A ${r},${r} 0 ${soc > 50 ? 1 : 0},1 ${(bx + r * Math.sin(a)).toFixed(2)},${(by - r * Math.cos(a)).toFixed(2)}`);
        }
        arc.style.stroke = socColor;

        // Badges: solar thrown away by zero export, and the surplus signal for Home Assistant
        this.badge('#flow-badge-solar', v.curtailed > FLOW_ON_W ? `≈ ${fmtW(v.curtailed)} curtailed` : '', c.warn, {x: FLOW_NODES.solar.x + 128, y: FLOW_NODES.solar.y - 20});
        this.badge('#flow-badge-home', v.surplus ? `surplus ≈ ${fmtW(v.surplusW)}` : '', c.ok, {x: FLOW_NODES.home.x, y: FLOW_NODES.home.y + FLOW_NR + 24});

        // Dots: each keeps its own position across updates
        const next = [];
        for (const [key, w] of Object.entries(flows)) {
            const n = flowCount(w), speed = flowSpeed(w);
            const existing = this.dots.filter(d => d.link === key);
            for (let i = 0; i < n; i++) next.push({link: key, color: source[FLOW_LINKS[key].from], speed, t: existing[i] ? existing[i].t : i / n});
        }
        this.dots = next;
    }

    draw(now) {
        requestAnimationFrame(this.loop);
        // Nothing to draw while the dashboard tab is hidden
        if (!this.el.isConnected || this.el.offsetParent === null) { this.last = now; return; }
        const ctx = this.canvas.getContext('2d');
        const dt = Math.min((now - (this.last || now)) / 1000, 0.05);
        this.last = now;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.save();
        ctx.scale(this.scale.x, this.scale.y);
        for (const dot of this.dots) {
            dot.t = (dot.t + dot.speed * dt) % 1;
            const link = FLOW_LINKS[dot.link];
            const p = flowPoint(link, dot.t);
            // Hidden under the nodes at both ends
            if (Math.hypot(p.x - link.a.x, p.y - link.a.y) < FLOW_NR + 4 || Math.hypot(p.x - link.b.x, p.y - link.b.y) < FLOW_NR + 4) continue;
            const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 9);
            glow.addColorStop(0, dot.color + 'cc');
            glow.addColorStop(1, dot.color + '00');
            ctx.beginPath(); ctx.arc(p.x, p.y, 9, 0, Math.PI * 2); ctx.fillStyle = glow; ctx.fill();
            ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, Math.PI * 2); ctx.fillStyle = dot.color; ctx.fill();
            ctx.beginPath(); ctx.arc(p.x, p.y, 1.2, 0, Math.PI * 2); ctx.fillStyle = '#ffffff'; ctx.fill();
        }
        ctx.restore();
    }
}

// ---------------------------------------------------------------------
// Sankey: where the day's energy came from (left) and went to (right).
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
    const W = 1000, H = 340, pad = 10, gap = 26, nodeW = 16, labelW = 170;
    const total = Math.max(left.reduce((a, n) => a + n.v, 0), right.reduce((a, n) => a + n.v, 0));
    const most = Math.max(left.length, right.length);
    const usable = H - 2 * pad - gap * (most - 1);
    const scale = usable / total;
    const place = (nodes, x) => {
        let y = pad + (usable - nodes.reduce((a, n) => a + n.v, 0) * scale + gap * (most - nodes.length)) / 2;
        nodes.forEach(n => { n.x = x; n.y = y; n.h = Math.max(3, n.v * scale); n.used = 0; y += n.h + gap; });
    };
    place(left, labelW);
    place(right, W - labelW - nodeW);
    const byKey = {};
    left.forEach(n => { byKey['s:' + n.key] = n; });
    right.forEach(n => { byKey['t:' + n.link] = n; });
    let defs = '', ribbons = '';
    links.forEach((l, i) => {
        const a = byKey['s:' + l.s], b = byKey['t:' + l.t];
        const h = l.v * scale;
        const y0 = a.y + a.used, y1 = b.y + b.used;
        a.used += h; b.used += h;
        const x0 = a.x + nodeW, x1 = b.x, mx = (x0 + x1) / 2;
        const target = l.t === 'grid' ? 'gridOut' : l.t;
        defs += `<linearGradient id="sankey-grad-${i}" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="${colors[l.s]}"/><stop offset="1" stop-color="${colors[target]}"/></linearGradient>`;
        const d = `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1} L${x1},${y1 + h} C${mx},${y1 + h} ${mx},${y0 + h} ${x0},${y0 + h} Z`;
        ribbons += `<path class="ribbon" d="${d}" fill="url(#sankey-grad-${i})" data-tip="${escapeHtml(names[l.s])} → ${escapeHtml(names[target])}" data-value="${l.v}"></path>`;
    });
    const nodeHtml = (n, isLeft) => {
        const tx = isLeft ? n.x - 12 : n.x + nodeW + 12;
        const anchor = isLeft ? 'end' : 'start';
        return `<rect x="${n.x}" y="${n.y}" width="${nodeW}" height="${n.h}" rx="3" fill="${colors[n.key]}"/>
            <text class="node-label" x="${tx}" y="${n.y + n.h / 2 - 4}" text-anchor="${anchor}">${names[n.key]}</text>
            <text class="node-value" x="${tx}" y="${n.y + n.h / 2 + 14}" text-anchor="${anchor}">${num(n.v, 2)} kWh · ${num(n.v / total * 100)}%</text>`;
    };
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Energy flow of the day"><defs>${defs}</defs>${ribbons}${left.map(n => nodeHtml(n, true)).join('')}${right.map(n => nodeHtml(n, false)).join('')}</svg>`;
    el.querySelectorAll('.ribbon').forEach(r => {
        r.addEventListener('pointermove', e => showTip(e.clientX, e.clientY, r.dataset.tip, [{value: fmtKwh(Number(r.dataset.value), 2), name: `${num(Number(r.dataset.value) / total * 100)}% of the day`}]));
        r.addEventListener('pointerleave', hideTip);
    });
}
