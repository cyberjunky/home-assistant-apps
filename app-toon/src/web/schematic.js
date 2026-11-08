// Toon dashboard - the heating system, drawn the same way as GoodWe's energy flow: four nodes joined
// by the paths heat can take, with dots travelling along the paths in use.

const FLOW_W = 560, FLOW_H = 462, FLOW_NR = 50;
const FLOW_NODES = {gas: {x: 280, y: 82}, boiler: {x: 76, y: 232}, rooms: {x: 484, y: 232}, water: {x: 280, y: 382}};
const FLOW_LINKS = {
    gas: {from: 'gas', to: 'boiler', c: {x: 145, y: 120}},
    supply: {from: 'boiler', to: 'rooms', c: {x: 280, y: 176}},
    return: {from: 'rooms', to: 'boiler', c: {x: 280, y: 288}},
    water: {from: 'boiler', to: 'water', c: {x: 145, y: 344}},
};
for (const link of Object.values(FLOW_LINKS)) {
    link.a = FLOW_NODES[link.from];
    link.b = FLOW_NODES[link.to];
}
const FLOW_ICONS = {
    gas: '<circle cx="0" cy="0" r="8"/><path d="M0,0 L4,-5 M-5,3 H5"/>',
    boiler: '<path d="M0,-9 C1,-5 6,-3 6,2 A6,6 0 0 1 -6,2 C-6,-1 -4,-2 -3,-5 C-2,-4 -1,-3 -1,-2 C0,-4 0,-6 0,-9 Z"/>',
    rooms: '<path d="M-8,-7 V7 M-4,-7 V7 M0,-7 V7 M4,-7 V7 M8,-7 V7 M-10,-4 H10 M-10,4 H10"/>',
    water: '<path d="M0,-9 C3,-4 6,-1 6,3 A6,6 0 0 1 -6,3 C-6,-1 -3,-4 0,-9 Z"/>',
};
const FLOW_LABELS = {gas: 'Gas', boiler: 'Boiler', rooms: 'Radiators and room', water: 'Hot water'};
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function flowPoint(link, t) {
    const u = 1 - t;
    return {x: u * u * link.a.x + 2 * u * t * link.c.x + t * t * link.b.x, y: u * u * link.a.y + 2 * u * t * link.c.y + t * t * link.b.y};
}

class HeatingFlow {
    constructor(el) {
        this.el = el;
        this.dots = [];
        this.last = 0;
        el.innerHTML = `<canvas></canvas><svg viewBox="0 0 ${FLOW_W} ${FLOW_H}" role="img" aria-label="The heating system: gas, boiler, radiators and hot water"></svg>`;
        this.canvas = el.querySelector('canvas');
        this.svg = el.querySelector('svg');
        this.buildSvg();
        new ResizeObserver(() => this.fit()).observe(el);
        this.fit();
        this.loop = now => this.draw(now);
        requestAnimationFrame(this.loop);
    }

    buildSvg() {
        const path = l => `M${l.a.x},${l.a.y} Q${l.c.x},${l.c.y} ${l.b.x},${l.b.y}`;
        const links = Object.entries(FLOW_LINKS);
        const node = key => {
            const n = FLOW_NODES[key];
            const labelY = key === 'water' ? n.y + FLOW_NR + 26 : n.y - FLOW_NR - 14;
            return `<g id="flow-node-${key}" class="node-g">
                <circle class="halo" cx="${n.x}" cy="${n.y}" r="${FLOW_NR + 9}"/>
                <circle class="node" cx="${n.x}" cy="${n.y}" r="${FLOW_NR}"/>
                <g class="icon" transform="translate(${n.x},${n.y - 24}) scale(1.15)">${FLOW_ICONS[key]}</g>
                <text class="value" x="${n.x}" y="${n.y + 6}">–</text>
                <text class="sub" x="${n.x}" y="${n.y + 24}"></text>
                <text class="label" x="${n.x}" y="${labelY}">${FLOW_LABELS[key]}</text>
            </g>`;
        };
        const b = FLOW_NODES.boiler;
        this.svg.innerHTML = `
            ${links.map(([k, l]) => `<path id="flow-track-${k}" class="track" d="${path(l)}"/>`).join('')}
            ${links.map(([k, l]) => `<path id="flow-line-${k}" class="line" d="${path(l)}"/>`).join('')}
            ${Object.keys(FLOW_NODES).map(node).join('')}
            <circle class="soc-track" cx="${b.x}" cy="${b.y}" r="${FLOW_NR + 5}"/>
            <path id="flow-modulation" class="soc-arc" d=""/>
            <text id="flow-supply" class="value" x="280" y="186"></text>
            <text class="sub" x="280" y="166">supply</text>
            <text id="flow-delta" class="sub" x="280" y="236"></text>
            <text id="flow-return" class="value" x="280" y="292"></text>
            <text class="sub" x="280" y="310">return</text>
            <g id="flow-badge-pressure" class="badge"><rect rx="9" height="18"/><text></text></g>
            <g id="flow-badge-outside" class="badge"><rect rx="9" height="18"/><text></text></g>`;
    }

    fit() {
        const dpr = window.devicePixelRatio || 1;
        const w = this.el.clientWidth || FLOW_W, h = this.el.clientHeight || FLOW_H;
        this.canvas.width = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.scale = {x: w / FLOW_W * dpr, y: h / FLOW_H * dpr};
    }

    setNode(key, on, nodeColor, valueText, sub) {
        const g = this.svg.querySelector(`#flow-node-${key}`);
        g.classList.toggle('on', on);
        g.style.setProperty('--node-color', on ? nodeColor : 'var(--divider-color)');
        g.querySelector('.icon').style.stroke = on ? nodeColor : 'var(--idle)';
        g.querySelector('.value').textContent = valueText;
        g.querySelector('.value').classList.toggle('off', !on);
        g.querySelector('.sub').textContent = sub || '';
    }

    badge(id, text, badgeColor, anchor) {
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
        rect.style.fill = badgeColor + '26';
        rect.style.stroke = badgeColor;
        label.setAttribute('x', anchor.x);
        label.setAttribute('y', anchor.y + 4);
        label.style.fill = badgeColor;
    }

    update(v) {
        const c = {flame: cssVar('--flame'), supply: cssVar('--supply'), ret: cssVar('--return'), dhw: cssVar('--dhw'), warn: cssVar('--warning-color'), ok: cssVar('--success-color'), idle: cssVar('--secondary-text-color')};
        const heating = v.burner === 'heating' || v.burner === 'preheating';
        const hotWater = v.burner === 'hot_water';
        const on = heating || hotWater;
        const modulation = Math.max(0, Math.min(100, Number(v.modulation) || 0));
        const delta = missing(v.supply) || missing(v.ret) ? null : v.supply - v.ret;
        const active = {gas: on, supply: heating, return: heating, water: hotWater};
        const colors = {gas: c.flame, supply: c.supply, return: c.ret, water: c.dhw};
        const width = 2 + modulation / 100 * 5;

        for (const key of Object.keys(FLOW_LINKS)) {
            const line = this.svg.querySelector(`#flow-line-${key}`);
            line.style.display = active[key] ? '' : 'none';
            line.style.stroke = colors[key];
            line.style.strokeWidth = width;
            this.svg.querySelector(`#flow-track-${key}`).classList.toggle('busy', active[key]);
        }

        this.setNode('gas', on, c.flame, missing(v.gasFlow) ? '–' : `${num(v.gasFlow, 2)}`, on ? `${v.gasUnit || ''}` : (missing(v.gasToday) ? '' : `${num(v.gasToday, 2)} m³ today`));
        this.setNode('boiler', on, c.flame, on ? `${num(modulation)}%` : 'Off', `set ${fmtTemp(v.boilerSetpoint, 0)}`);
        this.setNode('rooms', heating, c.supply, fmtTemp(v.room), `set ${fmtTemp(v.setpoint)}`);
        this.setNode('water', hotWater, c.dhw, fmtTemp(v.dhwSetpoint, 0), v.dhwEnabled === 0 ? 'off' : hotWater ? 'running' : 'setpoint');

        const arc = this.svg.querySelector('#flow-modulation');
        if (!on || modulation < 1) {
            arc.setAttribute('d', '');
        } else {
            const r = FLOW_NR + 5, bx = FLOW_NODES.boiler.x, by = FLOW_NODES.boiler.y;
            const a = Math.min(modulation, 99.9) / 100 * 2 * Math.PI;
            arc.setAttribute('d', `M ${bx},${by - r} A ${r},${r} 0 ${modulation > 50 ? 1 : 0},1 ${(bx + r * Math.sin(a)).toFixed(2)},${(by - r * Math.cos(a)).toFixed(2)}`);
            arc.style.stroke = c.flame;
        }

        this.svg.querySelector('#flow-supply').textContent = fmtTemp(v.supply);
        this.svg.querySelector('#flow-return').textContent = fmtTemp(v.ret);
        this.svg.querySelector('#flow-delta').textContent = missing(delta) ? '' : `difference ${num(delta, 1)} °C`;

        const low = !missing(v.pressure) && v.pressure < PRESSURE_LOW;
        const below = key => ({x: FLOW_NODES[key].x, y: FLOW_NODES[key].y + FLOW_NR + 24});
        this.badge('#flow-badge-pressure', missing(v.pressure) ? '' : `${low ? '⚠ ' : ''}${num(v.pressure, 2)} bar`, low ? c.warn : c.idle, below('boiler'));
        this.badge('#flow-badge-outside', missing(v.outside) ? '' : `outside ${fmtTemp(v.outside)}`, c.idle, below('rooms'));

        const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const next = [];
        for (const key of Object.keys(FLOW_LINKS)) {
            if (!active[key] || calm) continue;
            const count = modulation < 25 ? 2 : modulation < 60 ? 3 : 4;
            const speed = 0.12 + modulation / 100 * 0.38;
            const existing = this.dots.filter(d => d.link === key);
            for (let i = 0; i < count; i++) next.push({link: key, color: colors[key], speed, t: existing[i] ? existing[i].t : i / count});
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

let heatingFlow = null;

function renderHeating() {
    if (!heatingFlow) heatingFlow = new HeatingFlow(document.getElementById('heating'));
    const burner = value('burner_state');
    document.getElementById('heating-hint').textContent = BURNER[burner] || '';
    const usage = (app.today && app.today.usage.today) || {};
    const metric = (app.status.metrics || {}).gas_flow || {};
    const outside = externals().find(s => s.key === 'outside_temperature');
    heatingFlow.update({
        burner,
        modulation: value('modulation'),
        supply: value('boiler_temperature'),
        ret: value('boiler_return_temperature'),
        boilerSetpoint: value('boiler_setpoint'),
        room: value('room_temperature'),
        setpoint: value('setpoint'),
        dhwSetpoint: value('dhw_setpoint'),
        dhwEnabled: value('dhw_enabled'),
        pressure: value('boiler_pressure'),
        gasFlow: value('gas_flow'),
        gasUnit: metric.unit,
        gasToday: usage.gas,
        outside: outside ? outside.value : null,
    });
}
