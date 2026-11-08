// Toon dashboard - the heating system: boiler, pipes to the radiators and back, hot water.

function renderHeating() {
    const el = document.getElementById('heating');
    const burner = value('burner_state');
    const heating = burner === 'heating' || burner === 'preheating';
    const hotWater = burner === 'hot_water';
    const on = heating || hotWater;
    const supply = value('boiler_temperature');
    const ret = value('boiler_return_temperature');
    const delta = missing(supply) || missing(ret) ? null : supply - ret;
    const modulation = value('modulation') || 0;
    const pressure = value('boiler_pressure');
    const low = !missing(pressure) && pressure < PRESSURE_LOW;
    const ring = 2 * Math.PI * 62;
    document.getElementById('heating-hint').textContent = BURNER[burner] || '';

    el.innerHTML = `<svg viewBox="0 66 600 345" role="img" aria-label="The heating system: boiler, radiators and hot water">
        <path class="pipe supply ${heating ? 'flowing' : ''}" d="M178 110 H420"/>
        <path class="stream ${heating ? 'flowing' : ''}" d="M178 110 H420" stroke="${color('supply')}"/>
        <path class="pipe return ${heating ? 'flowing' : ''}" d="M420 230 H178"/>
        <path class="stream ${heating ? 'flowing' : ''}" d="M420 230 H178" stroke="${color('return')}"/>
        <path class="pipe dhw ${hotWater ? 'flowing' : ''}" d="M110 240 V320"/>
        <path class="stream ${hotWater ? 'flowing' : ''}" d="M110 240 V320" stroke="${color('dhw')}"/>

        <text class="label" x="299" y="86">Supply</text>
        <text class="value" x="299" y="145">${fmtTemp(supply)}</text>
        <text class="label" x="299" y="206">Return</text>
        <text class="value" x="299" y="265">${fmtTemp(ret)}</text>
        <text class="sub" x="299" y="178">difference ${missing(delta) ? '–' : num(delta, 1) + ' °C'}</text>

        <g class="${on ? 'on' : ''}" style="--node-color:${on ? color('flame') : 'var(--divider-color)'}">
            <circle class="node" cx="110" cy="170" r="72"/>
            <circle class="ring-track" cx="110" cy="170" r="62"/>
            <circle class="ring" cx="110" cy="170" r="62" stroke-dasharray="${ring}" stroke-dashoffset="${ring * (1 - modulation / 100)}" transform="rotate(-90 110 170)"/>
            <path class="icon" style="stroke:${on ? color('flame') : ''}" transform="translate(94 124) scale(1.33)" d="M12 3c1 4 6 5 6 11a6 6 0 0 1-12 0c0-3 2-4 3-7 1 1 2 2 3 2 0-2 0-4 0-6z"/>
            <text class="value" x="110" y="184">${on ? num(modulation) + '%' : 'Off'}</text>
            <text class="sub" x="110" y="202">setpoint ${fmtTemp(value('boiler_setpoint'), 0)}</text>
        </g>
        <text class="label" x="110" y="84">Boiler</text>

        <g class="${heating ? 'on' : ''}" style="--node-color:${heating ? color('supply') : 'var(--divider-color)'}">
            <circle class="node" cx="490" cy="170" r="72"/>
            <path class="icon" style="stroke:${heating ? color('supply') : ''}" transform="translate(462 118)" d="M6 6v30M18 6v30M30 6v30M42 6v30M56 6v30M1 12h60M1 30h60" stroke-width="2"/>
            <text class="value" x="490" y="182">${fmtTemp(value('room_temperature'))}</text>
            <text class="sub" x="490" y="200">setpoint ${fmtTemp(value('setpoint'))}</text>
        </g>
        <text class="label" x="490" y="84">Radiators and room</text>

        <g class="${hotWater ? 'on' : ''}" style="--node-color:${hotWater ? color('dhw') : 'var(--divider-color)'}">
            <circle class="node" cx="110" cy="360" r="42"/>
            <text class="value" x="110" y="366" style="font-size:15px">${fmtTemp(value('dhw_setpoint'), 0)}</text>
        </g>
        <text class="label" x="205" y="356" style="text-anchor:start">Hot water</text>
        <text class="sub" x="205" y="373" style="text-anchor:start">${value('dhw_enabled') === 0 ? 'off' : hotWater ? 'running' : 'setpoint'}</text>

        <text class="label" x="490" y="340">Water pressure</text>
        <text class="value" x="490" y="366" style="${low ? 'fill:var(--warning-color)' : ''}">${missing(pressure) ? '–' : num(pressure, 2) + ' bar'}</text>
        <text class="sub" x="490" y="384">${low ? '⚠ low: under ' + PRESSURE_LOW + ' bar' : 'usual 1 to 2 bar'}</text>
    </svg>`;
}
