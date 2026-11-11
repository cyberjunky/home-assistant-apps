// SDRadio dashboard - knobs and the tune dial.
// A knob: drag up or down, scroll, or use the arrow keys; double-click puts it back to its default.

const SVG_NS = 'http://www.w3.org/2000/svg';
const ARC = 207.3;  // 270° of a circle with radius 44

function knobSvg(size) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 100 100" aria-hidden="true">
        <circle class="k-track" cx="50" cy="50" r="44" stroke-width="5" stroke-dasharray="${ARC} 999" transform="rotate(135 50 50)"/>
        <circle class="k-value" cx="50" cy="50" r="44" stroke-width="5" stroke-dasharray="0 999" transform="rotate(135 50 50)"/>
        <circle class="k-body" cx="50" cy="50" r="35" stroke-width="1.5"/>
        <circle class="k-grip" cx="50" cy="50" r="28" stroke-width="1"/>
        <line class="k-pointer" x1="50" y1="50" x2="50" y2="22" stroke-width="4"/>
        <circle class="k-hub" cx="50" cy="50" r="5"/>
    </svg>`;
}

class Knob {
    // opts: label, size, min, max, step | values (a list to step through), value, defaultValue,
    // format(value) → readout text, onInput(value) while turning, onChange(value) when let go
    constructor(container, opts) {
        this.opts = Object.assign({size: 64, min: 0, max: 100, step: 1}, opts);
        this.value = opts.value ?? this.opts.min;
        this.el = document.createElement('button');
        this.el.type = 'button';
        this.el.className = 'knob';
        this.el.setAttribute('role', 'slider');
        this.el.setAttribute('aria-label', opts.label || 'Knob');
        this.el.innerHTML = knobSvg(this.opts.size)
            + (opts.label ? `<span class="k-label">${escapeHtml(opts.label)}</span><span class="k-readout"></span>` : '');
        container.appendChild(this.el);
        this.arc = this.el.querySelector('.k-value');
        this.pointer = this.el.querySelector('.k-pointer');
        this.readout = this.el.querySelector('.k-readout');
        this.dragging = false;
        this._bind();
        this.render();
    }

    // Position 0..1 of a value
    _fraction(value) {
        const o = this.opts;
        if (o.values) {
            const i = this._index(value);
            return o.values.length > 1 ? i / (o.values.length - 1) : 0;
        }
        return (value - o.min) / ((o.max - o.min) || 1);
    }
    _index(value) {
        const values = this.opts.values;
        let best = 0;
        values.forEach((v, i) => { if (Math.abs(v - value) < Math.abs(values[best] - value)) best = i; });
        return best;
    }
    _clamp(value) {
        const o = this.opts;
        if (o.values) return o.values[Math.max(0, Math.min(o.values.length - 1, this._index(value)))];
        const stepped = Math.round((value - o.min) / o.step) * o.step + o.min;
        return Math.max(o.min, Math.min(o.max, +stepped.toFixed(6)));
    }
    _nudge(count) {
        const o = this.opts;
        if (o.values) {
            const i = Math.max(0, Math.min(o.values.length - 1, this._index(this.value) + count));
            return o.values[i];
        }
        return this._clamp(this.value + count * o.step);
    }

    set(value, quiet) {
        if (this.dragging && quiet) return;
        this.value = this._clamp(value);
        this.render();
    }
    setRange(min, max, step) {
        Object.assign(this.opts, {min, max, step: step || this.opts.step});
        this.render();
    }
    setValues(values) { this.opts.values = values; this.render(); }
    setDisabled(disabled) { this.el.classList.toggle('disabled', !!disabled); }

    render() {
        const f = Math.max(0, Math.min(1, this._fraction(this.value)));
        this.arc.setAttribute('stroke-dasharray', `${(f * ARC).toFixed(1)} 999`);
        this.pointer.setAttribute('transform', `rotate(${(-135 + f * 270).toFixed(1)} 50 50)`);
        const text = this.opts.format ? this.opts.format(this.value) : String(this.value);
        if (this.readout) this.readout.textContent = text;
        this.el.setAttribute('aria-valuetext', text);
        this.el.setAttribute('aria-valuenow', String(this.value));
    }

    _emit(final) {
        if (this.opts.onInput) this.opts.onInput(this.value);
        if (final && this.opts.onChange) this.opts.onChange(this.value);
    }

    _bind() {
        let startY = 0, startValue = 0, moved = false;
        this.el.addEventListener('pointerdown', (e) => {
            this.el.setPointerCapture(e.pointerId);
            this.dragging = true;
            moved = false;
            startY = e.clientY;
            startValue = this.value;
            e.preventDefault();
            this.el.focus();
        });
        this.el.addEventListener('pointermove', (e) => {
            if (!this.dragging) return;
            const dy = startY - e.clientY;
            if (Math.abs(dy) > 2) moved = true;
            const o = this.opts;
            let next;
            if (o.values) {
                const i = Math.max(0, Math.min(o.values.length - 1, this._index(startValue) + Math.round(dy / 14)));
                next = o.values[i];
            } else {
                next = this._clamp(startValue + dy / 160 * (o.max - o.min));
            }
            if (next !== this.value) { this.value = next; this.render(); this._emit(false); }
        });
        const end = () => {
            if (!this.dragging) return;
            this.dragging = false;
            if (moved) this._emit(true);
        };
        this.el.addEventListener('pointerup', end);
        this.el.addEventListener('pointercancel', end);
        this.el.addEventListener('wheel', (e) => {
            e.preventDefault();
            const next = this._nudge(e.deltaY < 0 ? 1 : -1);
            if (next !== this.value) { this.value = next; this.render(); this._emit(true); }
        }, {passive: false});
        this.el.addEventListener('keydown', (e) => {
            const keys = {ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 5, PageDown: -5};
            let next = null;
            if (keys[e.key]) next = this._nudge(keys[e.key]);
            else if (e.key === 'Home') next = this._clamp(this.opts.values ? this.opts.values[0] : this.opts.min);
            else if (e.key === 'End') next = this._clamp(this.opts.values ? this.opts.values.at(-1) : this.opts.max);
            if (next === null) return;
            e.preventDefault();
            e.stopPropagation();
            if (next !== this.value) { this.value = next; this.render(); this._emit(true); }
        });
        this.el.addEventListener('dblclick', () => {
            if (this.opts.defaultValue === undefined) return;
            this.value = this._clamp(this.opts.defaultValue);
            this.render();
            this._emit(true);
        });
    }
}

// The tune dial: turns without end; every 12° is one tuning step
class TuneDial {
    constructor(container, opts) {
        this.opts = Object.assign({size: 220}, opts);
        this.angle = 0;
        this.pending = 0;
        this.el = document.createElement('button');
        this.el.type = 'button';
        this.el.className = 'knob dial';
        this.el.setAttribute('aria-label', 'Tune: drag round, scroll or use the arrow keys');
        this.el.innerHTML = knobSvg(this.opts.size) + '<span class="k-label">Tune</span><span class="k-readout"></span>';
        container.appendChild(this.el);
        this.el.querySelector('.k-value').remove();
        this.pointer = this.el.querySelector('.k-pointer');
        this.readout = this.el.querySelector('.k-readout');
        this._bind();
    }
    setReadout(text) { this.readout.textContent = text; }
    _turn(steps) {
        if (!steps) return;
        this.angle += steps * 12;
        this.pointer.setAttribute('transform', `rotate(${this.angle % 360} 50 50)`);
        this.pending += steps;
        if (!this._timer) {
            // Steps are sent in small batches, so a fast spin doesn't flood the radio
            this._timer = setTimeout(() => {
                this._timer = null;
                const n = this.pending;
                this.pending = 0;
                if (n) this.opts.onSteps(n);
            }, 40);
        }
    }
    _bind() {
        let last = null, rest = 0;
        const angleOf = (e) => {
            const r = this.el.querySelector('svg').getBoundingClientRect();
            return Math.atan2(e.clientY - (r.top + r.height / 2), e.clientX - (r.left + r.width / 2)) * 180 / Math.PI;
        };
        this.el.addEventListener('pointerdown', (e) => {
            this.el.setPointerCapture(e.pointerId);
            last = angleOf(e);
            rest = 0;
            e.preventDefault();
            this.el.focus();
        });
        this.el.addEventListener('pointermove', (e) => {
            if (last === null) return;
            const a = angleOf(e);
            let d = a - last;
            if (d > 180) d -= 360;
            if (d < -180) d += 360;
            last = a;
            rest += d;
            const steps = Math.trunc(rest / 12);
            if (steps) { rest -= steps * 12; this._turn(steps); }
        });
        const end = () => { last = null; };
        this.el.addEventListener('pointerup', end);
        this.el.addEventListener('pointercancel', end);
        this.el.addEventListener('wheel', (e) => { e.preventDefault(); this._turn(e.deltaY < 0 ? 1 : -1); }, {passive: false});
        this.el.addEventListener('keydown', (e) => {
            const keys = {ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10};
            if (!keys[e.key]) return;
            e.preventDefault();
            e.stopPropagation();
            this._turn(keys[e.key]);
        });
    }
}
