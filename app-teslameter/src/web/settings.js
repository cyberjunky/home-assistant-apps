// TeslaMeter dashboard - Settings view: Tesla account (sign-in), vehicles, data export and info.

function infoRow(label, valueHtml) {
    return `<div class="info-row"><span class="info-label">${label}</span><span class="info-value">${valueHtml}</span></div>`;
}

function renderAccount() {
    const el = document.getElementById('account');
    const auth = app.auth;
    if (auth.signed_in) {
        el.innerHTML = `
            ${infoRow('Status', '<span class="sb-ok">Signed in</span>')}
            ${auth.email ? infoRow('Account', escapeHtml(auth.email)) : ''}
            ${infoRow('Access token renews', auth.expires_at ? 'before ' + fmtDateTime(auth.expires_at) : '–')}
            ${auth.region === 'cn' ? infoRow('Region', 'China') : ''}
            ${auth.source === 'app' ? infoRow('Access token', 'from the Tesla Auth app, until it expires') : ''}
            ${(auth.audience || []).length ? infoRow('Token valid for', `<span class="${auth.owner_api === false ? 'sb-warn' : ''}">${escapeHtml(auth.audience.join(', '))}</span>`) : ''}
            <p class="panel-sub" style="margin:10px 0">TeslaMeter renews its access by itself. You only sign in again if you change your Tesla password or sign out here.</p>
            <button type="button" class="btn btn-danger" onclick="signOut()">Sign out</button>`;
    } else {
        el.innerHTML = `
            ${infoRow('Status', '<span class="sb-warn">Not signed in</span>')}
            ${auth.error ? `<p class="form-error" style="margin:10px 0">${escapeHtml(auth.error)}</p>` : ''}
            <p class="panel-sub" style="margin:10px 0">Sign in once with your Tesla account. You do that on Tesla's own page; TeslaMeter does not store your password.</p>
            <button type="button" class="btn btn-primary" onclick="openSignIn()">Sign in with Tesla</button>`;
    }
}

// What the Tesla Auth app shows: both tokens under banners, or a token on its own
function looksLikeToken(text) {
    return /REFRESH TOKEN/.test(text || '') || /^[A-Za-z0-9._~+\/=-]{80,}$/.test((text || '').trim());
}

const TESLA_AUTH_URL = 'https://github.com/adriankumpf/tesla_auth/releases/latest';

// Sign in to Tesla. Two ways: in the add-on's own browser, shown here (nothing to copy),
// or with a refresh token made by the Tesla Auth app.
function openSignIn() {
    const hasBrowser = Boolean(app.status && app.status.signin_browser);
    const methods = [
        ...(hasBrowser ? [{key: 'here', label: 'Sign in here'}] : []),
        {key: 'token', label: 'With a refresh token'},
    ];
    const saved = localStorage.getItem(LS + 'signin_method');
    let method = methods.some(m => m.key === saved) ? saved : methods[0].key;
    const overlay = openModal({
        title: 'Sign in with Tesla',
        bodyHTML: `
            <div class="tabs" id="signin-method" role="group" aria-label="How to sign in" style="align-self:flex-start"></div>
            <div id="signin-here" class="signin-here">
                <p class="panel-sub">Tesla's own sign-in page, opened in a browser inside the add-on. Click in it and sign in as usual; TeslaMeter connects when you are done.</p>
                <canvas id="signin-screen" class="signin-screen" width="480" height="640" tabindex="0" aria-label="Tesla sign-in page"></canvas>
                <div class="signin-status"><span id="signin-here-status">Starting…</span><button type="button" class="btn btn-small" id="signin-restart" hidden>Start again</button></div>
            </div>
            <div class="steps" id="signin-token-steps">
                <div class="step"><span class="step-n">1</span><div>
                    <b>Get the Tesla Auth app</b>
                    <p>A small, free and open-source program for Windows, macOS and Linux that shows Tesla's own sign-in in a window and hands you the resulting tokens. Download it, unpack it and start <span class="mono">tesla_auth</span>.</p>
                    <a class="btn btn-primary" href="${TESLA_AUTH_URL}" target="_blank" rel="noopener noreferrer">Download Tesla Auth ↗</a>
                </div></div>
                <div class="step"><span class="step-n">2</span><div>
                    <b>Sign in in its window</b>
                    <p>Use your Tesla account, including the verification code if you use one. When it is done the window shows an access token and a refresh token.</p>
                </div></div>
                <div class="step"><span class="step-n">3</span><div>
                    <b>Copy everything the app shows and paste it here</b>
                    <p>Select all in the app's window, copy, then press <span class="mono">Ctrl+V</span> anywhere in this window; TeslaMeter connects by itself. The refresh token on its own works too.</p>
                    <textarea id="signin-token" class="input" rows="3" placeholder="Refresh token" spellcheck="false"></textarea>
                </div></div>
            </div>
            <div class="form-error" id="signin-error"></div>
            <div class="note" id="signin-note"></div>`,
        footHTML: '<button class="btn" data-close>Cancel</button><button class="btn btn-primary" data-connect>Connect</button>',
    });
    const button = overlay.querySelector('[data-connect]');
    const tokenField = overlay.querySelector('#signin-token');
    const error = overlay.querySelector('#signin-error');
    const NOTES = {
        here: 'You type on Tesla\'s own page, shown from a browser inside the add-on. Your keystrokes travel through Home Assistant to that browser; TeslaMeter does not store or log them, and the browser is discarded afterwards. TeslaMeter only keeps the access and refresh token Tesla hands out.',
        token: 'You type your password in the Tesla Auth app, on Tesla\'s own page, never into TeslaMeter. TeslaMeter only keeps the access and refresh token Tesla hands out, in the add-on\'s private storage.',
    };

    // ----- "Sign in here": the add-on's browser, its screen on a canvas, our mouse and keyboard sent to it
    const screen = overlay.querySelector('#signin-screen');
    const hereStatus = overlay.querySelector('#signin-here-status');
    const restart = overlay.querySelector('#signin-restart');
    let remote = null;
    const sendRemote = (event) => { if (remote && remote.readyState === WebSocket.OPEN) remote.send(JSON.stringify(event)); };
    const stopRemote = () => { if (remote) { remote.onclose = null; remote.close(); remote = null; } };
    const startRemote = () => {
        stopRemote();
        restart.hidden = true;
        hereStatus.textContent = 'Starting the browser…';
        const socket = new WebSocket((window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + basePath + 'api/auth/browser');
        socket.binaryType = 'blob';
        remote = socket;
        let finished = false;
        socket.onmessage = async (message) => {
            if (typeof message.data !== 'string') {
                // A picture of the page
                const bitmap = await createImageBitmap(message.data);
                screen.getContext('2d').drawImage(bitmap, 0, 0, screen.width, screen.height);
                return;
            }
            const data = JSON.parse(message.data);
            if (data.type === 'ready') {
                screen.width = data.width;
                screen.height = data.height;
                hereStatus.textContent = 'Click in the page above and sign in.';
                screen.focus();
            } else if (data.type === 'status') {
                hereStatus.textContent = data.message;
            } else if (data.type === 'done') {
                finished = true;
                closeModal();
                toast('Signed in - looking for your cars', 'success');
                loadStatus();
            } else if (data.type === 'error') {
                finished = true;
                hereStatus.textContent = data.message;
                restart.hidden = false;
            }
        };
        socket.onclose = () => {
            if (finished || socket !== remote) return;
            hereStatus.textContent = 'The connection to the sign-in browser was lost.';
            restart.hidden = false;
        };
    };
    restart.addEventListener('click', startRemote);

    const modifiers = e => (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
    const at = (e) => {
        const box = screen.getBoundingClientRect();
        return {x: (e.clientX - box.left) * screen.width / box.width, y: (e.clientY - box.top) * screen.height / box.height};
    };
    const BUTTONS = ['left', 'middle', 'right'];
    screen.addEventListener('pointerdown', (e) => {
        screen.focus();
        try { screen.setPointerCapture(e.pointerId); } catch (x) { /* a pointer that can't be captured still clicks */ }
        sendRemote({t: 'mouse', type: 'mousePressed', ...at(e), button: BUTTONS[e.button] || 'left', buttons: e.buttons, clickCount: e.detail || 1, modifiers: modifiers(e)});
        e.preventDefault();
    });
    screen.addEventListener('pointerup', (e) => {
        sendRemote({t: 'mouse', type: 'mouseReleased', ...at(e), button: BUTTONS[e.button] || 'left', buttons: e.buttons, clickCount: e.detail || 1, modifiers: modifiers(e)});
    });
    let lastMove = 0;
    screen.addEventListener('pointermove', (e) => {
        if (Date.now() - lastMove < 40) return;
        lastMove = Date.now();
        sendRemote({t: 'mouse', type: 'mouseMoved', ...at(e), button: e.buttons & 1 ? 'left' : 'none', buttons: e.buttons, modifiers: modifiers(e)});
    });
    screen.addEventListener('wheel', (e) => {
        sendRemote({t: 'wheel', ...at(e), dx: e.deltaX, dy: e.deltaY});
        e.preventDefault();
    }, {passive: false});
    screen.addEventListener('contextmenu', e => e.preventDefault());
    screen.addEventListener('keydown', (e) => {
        // Escape belongs to the page being typed in, not to this dialog
        e.stopPropagation();
        // Ctrl+V: let the browser raise its paste event, which is passed on as text below
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') return;
        e.preventDefault();
        const printable = e.key.length === 1 && !e.ctrlKey && !e.metaKey;
        const text = printable ? e.key : e.key === 'Enter' ? '\r' : '';
        sendRemote({t: 'key', type: text ? 'keyDown' : 'rawKeyDown', key: e.key, code: e.code, keyCode: e.keyCode, modifiers: modifiers(e), text});
    });
    screen.addEventListener('keyup', (e) => {
        e.stopPropagation();
        e.preventDefault();
        sendRemote({t: 'key', type: 'keyUp', key: e.key, code: e.code, keyCode: e.keyCode, modifiers: modifiers(e)});
    });
    // The dialog can be closed in several ways; whichever it was, stop the browser
    const watch = setInterval(() => { if (!overlay.isConnected) { clearInterval(watch); stopRemote(); } }, 500);

    // ----- "With a refresh token"
    let busy = false;
    const connect = async () => {
        const token = tokenField.value.trim();
        if (busy) return;
        if (!token) {
            error.textContent = 'Paste the refresh token from the Tesla Auth app.';
            return;
        }
        busy = true;
        button.disabled = true;
        button.textContent = 'Connecting…';
        error.textContent = '';
        try {
            await api('auth/token', {method: 'POST', body: {refresh_token: token}});
            closeModal();
            toast('Signed in - looking for your cars', 'success');
            await loadStatus();
        } catch (e) {
            error.textContent = e.message;
            button.disabled = false;
            button.textContent = 'Connect';
        }
        busy = false;
    };
    button.addEventListener('click', connect);

    overlay.addEventListener('paste', (e) => {
        const text = (e.clipboardData ? e.clipboardData.getData('text') : '').trim();
        if (method === 'here') {
            // Pasting into Tesla's page, e.g. a password from a password manager
            sendRemote({t: 'text', text});
            e.preventDefault();
        } else if (looksLikeToken(text)) {
            // A pasted token connects right away
            tokenField.value = text;
            e.preventDefault();
            connect();
        }
    });

    const showMethod = (key) => {
        method = key;
        localStorage.setItem(LS + 'signin_method', key);
        overlay.querySelector('#signin-here').hidden = key !== 'here';
        overlay.querySelector('#signin-token-steps').hidden = key !== 'token';
        overlay.querySelector('#signin-note').textContent = NOTES[key];
        button.hidden = key === 'here';
        error.textContent = '';
        if (key === 'here') startRemote(); else stopRemote();
    };
    buildChips('signin-method', methods, method, showMethod);
    // With one way only there is nothing to choose
    overlay.querySelector('#signin-method').hidden = methods.length < 2;
    showMethod(method);
}

async function signOut() {
    const ok = await confirmDialog({
        title: 'Sign out of Tesla?',
        message: 'TeslaMeter stops logging until you sign in again. Everything recorded so far is kept.',
        confirmLabel: 'Sign out', danger: true,
    });
    if (!ok) return;
    try {
        await api('auth/signout', {method: 'POST'});
        await loadStatus();
    } catch (e) {
        toast(e.message, 'error');
    }
}

function renderVehicles() {
    const el = document.getElementById('vehicles');
    if (!app.cars.length) {
        el.innerHTML = `<div class="panel-sub">${app.auth.signed_in ? escapeHtml(app.accountError || 'Looking for the cars in your account…') : 'Your cars appear here after signing in.'}</div>`;
        return;
    }
    el.innerHTML = app.cars.map(car => `
        <div style="margin-bottom:14px">
            <div class="car-head" style="margin-bottom:6px">
                <div><h2 style="font-size:15px">${escapeHtml(car.name || 'Tesla')}</h2><div class="sub">${escapeHtml(carSubtitle(car))}</div></div>
                <div class="right">${stateLabel(car)}</div>
            </div>
            ${carVisual(car, 78)}
            ${car.color_name ? infoRow('Colour', escapeHtml(car.color_name)) : ''}
            ${car.wheels_name ? infoRow('Wheels', escapeHtml(car.wheels_name)) : ''}
            ${infoRow('VIN', escapeHtml(car.vin || '–'))}
            ${infoRow('Last contact', fmtAgo(car.last_seen))}
            ${infoRow('Rated consumption', `${num(conv.cons(car.efficiency_wh_km), 1)} ${unit.cons()}`)}
            ${infoRow('Live stream', car.streaming ? 'receiving' : 'idle')}
            ${car.error ? infoRow('Problem', `<span class="sb-warn">${escapeHtml(car.error)}</span>`) : ''}
            <div class="detail-actions" style="margin-top:8px">
                <button type="button" class="btn btn-small" onclick="refreshCar(${car.id})" title="Read the car's data now. An asleep car stays asleep.">Read now</button>
            </div>
        </div>`).join('')
        + '<p class="panel-sub">The rated consumption turns range into energy. It is learned from your charging sessions, or fixed with <span class="mono">efficiency_wh_km</span> in the add-on configuration.</p>';
}

// Import history from a PostgreSQL database (one-off). Fields aren't stored.
function openHistoryImport() {
    const saved = (() => { try { return JSON.parse(localStorage.getItem(LS + 'db_import')) || {}; } catch (e) { return {}; } })();
    const overlay = openModal({
        title: 'Import history',
        bodyHTML: `
            <p class="panel-sub">Reads your existing drives, charging sessions, positions, states and software history straight from another logger's PostgreSQL database and adds them here, matching cars by VIN. It only reads; nothing in that database changes. Safe to run again — it never duplicates.</p>
            <div class="form-field"><label>Database host</label><input id="tm-host" class="input" placeholder="e.g. the database add-on's hostname" value="${escapeHtml(saved.host || '')}"></div>
            <div style="display:flex; gap:10px">
                <div class="form-field" style="flex:2"><label>Database name</label><input id="tm-db" class="input" value="${escapeHtml(saved.database || '')}" placeholder="database name"></div>
                <div class="form-field" style="flex:1"><label>Port</label><input id="tm-port" class="input" type="number" value="${escapeHtml(saved.port || 5432)}"></div>
            </div>
            <div style="display:flex; gap:10px">
                <div class="form-field" style="flex:1"><label>User</label><input id="tm-user" class="input" value="${escapeHtml(saved.user || 'postgres')}"></div>
                <div class="form-field" style="flex:1"><label>Password</label><input id="tm-pass" class="input" type="password"></div>
            </div>
            <div id="tm-progress" class="note" hidden></div>
            <div class="form-error" id="tm-error"></div>`,
        footHTML: '<button class="btn" data-close>Close</button><button class="btn btn-primary" data-start>Start import</button>',
    });
    const start = overlay.querySelector('[data-start]');
    const error = overlay.querySelector('#tm-error');
    const progress = overlay.querySelector('#tm-progress');
    let poll = null;
    const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };
    const watch = setInterval(() => { if (!overlay.isConnected) { clearInterval(watch); stopPoll(); } }, 500);

    const render = (p) => {
        progress.hidden = false;
        if (p.phase === 'error') {
            // Part of it may be in by now, and it can be started again after fixing what's wrong
            stopPoll();
            progress.hidden = true;
            error.textContent = p.error;
            start.disabled = false;
            start.textContent = 'Start import';
            tripsLoadedFor = null; chargesLoadedFor = null; statsLoadedFor = null;
            return;
        }
        const done = !p.running && p.phase === 'done';
        progress.innerHTML = `<b>${done ? 'Done' : escapeHtml(p.message || 'Working…')}</b><br>`
            + `${num(p.drives)} drives · ${num(p.charges)} charges · ${num(p.positions)} positions imported`
            + (p.updated ? ` · ${num(p.updated)} existing readings filled in` : '');
        if (done) {
            stopPoll(); toast('Import finished', 'success'); start.disabled = false; start.textContent = 'Import again';
            // The import replaces sparse billing-only sessions by full ones with new ids: lists held in
            // memory would open sessions that no longer exist
            tripsLoadedFor = null; chargesLoadedFor = null; statsLoadedFor = null;
            loadStatus().catch(() => {});
        }
    };

    start.addEventListener('click', async () => {
        const params = {
            host: overlay.querySelector('#tm-host').value.trim(),
            port: Number(overlay.querySelector('#tm-port').value) || 5432,
            database: overlay.querySelector('#tm-db').value.trim(),
            user: overlay.querySelector('#tm-user').value.trim(),
            password: overlay.querySelector('#tm-pass').value,
        };
        if (!params.host || !params.user) { error.textContent = 'Enter at least the host and user.'; return; }
        localStorage.setItem(LS + 'db_import', JSON.stringify({host: params.host, port: params.port, database: params.database, user: params.user}));
        error.textContent = '';
        start.disabled = true;
        start.textContent = 'Importing…';
        try {
            await api('import/history', {method: 'POST', body: params});
        } catch (e) {
            error.textContent = e.message;
            start.disabled = false;
            start.textContent = 'Start import';
            return;
        }
        stopPoll();
        poll = setInterval(async () => {
            try { render(await api('import/history')); } catch (e) { /* keep polling */ }
        }, 1000);
    });
}

async function importCharges(button) {
    button.disabled = true;
    const label = button.textContent;
    button.textContent = 'Fetching…';
    try {
        const result = await api('charging/import', {method: 'POST'});
        toast(result.imported ? `Imported ${result.imported} charging session${result.imported === 1 ? '' : 's'}`
            : 'No new charging sessions to import', 'success');
        if (result.imported) { chargesLoadedFor = null; statsLoadedFor = null; }
    } catch (e) {
        toast(e.message, 'error');
    }
    button.disabled = false;
    button.textContent = label;
}

async function refreshCar(id) {
    try {
        await api(`cars/${id}/refresh`, {method: 'POST'});
        toast('Reading the car\'s data', 'success');
    } catch (e) {
        toast(e.message, 'error');
    }
}

function renderDataInfo() {
    const status = app.status || {};
    const counts = status.counts || {};
    document.getElementById('data-info').innerHTML = `
        ${infoRow('Trips', num(counts.drives))}
        ${infoRow('Charging sessions', num(counts.charges) + (counts.charges ? ` <small>(${num(counts.charges_dc || 0)} Supercharger · ${num(counts.charges - (counts.charges_dc || 0))} other)</small>` : ''))}
        ${infoRow('Positions', num(counts.positions))}
        ${infoRow('Database size', `${num((status.database_bytes || 0) / 1048576, 1)} MB`)}`;
    const car = currentCar();
    const query = car ? `?car=${car.id}` : '';
    document.getElementById('export-drives').href = `${basePath}api/export/drives.csv${query}`;
    document.getElementById('export-charges').href = `${basePath}api/export/charges.csv${query}`;
    document.getElementById('export-db').href = `${basePath}api/backup`;
}

// Name the recorded places inside a Home Assistant zone after the zone (also fixes wrong addresses there)
async function applyZones(button) {
    button.disabled = true;
    try {
        const r = await api('places/apply-zones', {method: 'POST'});
        toast(r.drives || r.charges
            ? `Renamed ${r.drives} trip${r.drives === 1 ? '' : 's'} and ${r.charges} charging session${r.charges === 1 ? '' : 's'} after your ${r.zones} zone${r.zones === 1 ? '' : 's'}`
            : `Nothing to rename: no recorded place lies inside your ${r.zones} zone${r.zones === 1 ? '' : 's'} (a larger zone radius may help)`, 'success');
        tripsLoadedFor = null; chargesLoadedFor = null; statsLoadedFor = null;
    } catch (e) {
        toast(e.message, 'error');
    }
    button.disabled = false;
}

// Rename a place everywhere it was recorded, e.g. a street Nominatim got wrong
function openRenamePlace() {
    const overlay = openModal({
        title: 'Rename a place',
        bodyHTML: `
            <p class="panel-sub">Gives every trip, charging session and cached address with this exact name a new name. Copy the current name from a trip; the price tag of a charging location moves along.</p>
            <div class="form-field"><label>Current name</label><input id="rp-from" class="input" placeholder="e.g. Willem Egmondstraat, Charlois, Rotterdam"></div>
            <div class="form-field"><label>New name</label><input id="rp-to" class="input" placeholder="e.g. Willingestraat, Rotterdam" maxlength="120"></div>
            <div class="form-error" id="rp-error"></div>`,
        footHTML: '<button class="btn" data-close>Cancel</button><button class="btn btn-primary" data-rename>Rename</button>',
    });
    overlay.querySelector('[data-rename]').addEventListener('click', async () => {
        const from = overlay.querySelector('#rp-from').value.trim();
        const to = overlay.querySelector('#rp-to').value.trim();
        const error = overlay.querySelector('#rp-error');
        if (!from || !to) { error.textContent = 'Enter both names.'; return; }
        try {
            const r = await api('places/rename', {method: 'POST', body: {from, to}});
            closeModal();
            toast(r.drives || r.charges || r.addresses
                ? `Renamed: ${r.drives} trip place${r.drives === 1 ? '' : 's'}, ${r.charges} charging session${r.charges === 1 ? '' : 's'}, ${r.addresses} cached address${r.addresses === 1 ? '' : 'es'}`
                : 'No trip, charging session or cached address has exactly that name', 'success');
            tripsLoadedFor = null; chargesLoadedFor = null; statsLoadedFor = null;
        } catch (e) {
            error.textContent = e.message;
        }
    });
    setTimeout(() => overlay.querySelector('#rp-from').focus(), 50);
}

// Danger zone: wipe everything recorded. The cars and the Tesla sign-in stay.
async function deleteAllData() {
    const ok = await confirmDialog({
        title: 'Delete all recorded data?',
        message: 'Every trip, charging session, position, state and software version is removed. <b>This cannot be undone.</b><br><br>Your cars, your Tesla sign-in and your charging prices stay, so logging continues right away.',
        confirmLabel: 'Delete all recorded data', danger: true, typeToConfirm: 'DELETE',
    });
    if (!ok) return;
    try {
        await api('data/delete', {method: 'POST'});
    } catch (e) {
        toast(e.message, 'error');
        return;
    }
    // The other views still hold the old lists; make them load again when opened
    tripsLoadedFor = null;
    chargesLoadedFor = null;
    statsLoadedFor = null;
    toast('All recorded data deleted', 'success');
    try { await loadStatus(); } catch (e) { /* the next refresh brings the counts */ }
    renderSettings();
}

function superchargerCostsText(costs) {
    if (!costs || costs.state === 'off') return 'off';
    if (costs.state === 'ok') return '<span class="sb-ok">read from Tesla</span>';
    if (costs.state === 'unavailable') return `<span class="sb-warn" title="${escapeHtml(costs.detail)}">not available from Tesla</span>`;
    return 'not checked yet';
}

function renderAbout() {
    const status = app.status || {};
    const polling = status.polling || {};
    const mqtt = app.mqtt.enabled
        ? `<span class="${app.mqtt.connected ? 'sb-ok' : 'sb-bad'}">${app.mqtt.connected ? 'connected' : 'offline'}</span> · ${escapeHtml(app.mqtt.broker)}`
        : 'off';
    document.getElementById('support').innerHTML = `
        ${infoRow('Documentation', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-teslameter/DOCS.md" target="_blank" rel="noopener">Docs on GitHub</a>')}
        ${infoRow('Problem or idea', '<a href="https://github.com/cyberjunky/home-assistant-apps/issues" target="_blank" rel="noopener">Open an issue</a>')}
        ${infoRow('Version', `v${escapeHtml(app.version)} · <a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-teslameter/CHANGELOG.md" target="_blank" rel="noopener">changelog</a>`)}`;
    document.getElementById('about').innerHTML = `
        ${infoRow('Units', app.units === 'imperial' ? 'Imperial' : 'Metric')}
        ${infoRow('Live stream', status.streaming ? 'on' : 'off')}
        ${infoRow('Address lookup', status.geocoding ? 'OpenStreetMap Nominatim' : 'off')}
        ${infoRow('Home Assistant zones', (status.zones || []).length
            ? escapeHtml(status.zones.map(z => `${z.name} (${z.radius} m)`).join(', '))
            : '<span class="sb-warn">none loaded</span> · see the add-on log')}
        ${infoRow('Sleep', `left alone after ${num(polling.idle_minutes)} min idle`)}
        ${infoRow('MQTT', mqtt)}
        ${infoRow('Price per kWh', status.cost_per_kwh ? fmtMoney(status.cost_per_kwh) : 'not set')}
        ${infoRow('Supercharger costs', superchargerCostsText(status.supercharger_costs))}
        <p class="panel-sub" style="margin-top:10px">These are set on the add-on's Configuration tab in Home Assistant.
        TeslaMeter is an independent project and is not affiliated with or endorsed by Tesla, Inc.</p>`;
}

let tagList = null;

async function renderChargeTags(force) {
    const el = document.getElementById('charge-tags');
    if (!el) return;
    if (!tagList || force) {
        try {
            const data = await api('tags');
            tagList = data.tags || [];
            chargeTags = tagList;  // keep the Charging view's cache in sync
        } catch (e) {
            el.innerHTML = '<div class="panel-sub">Could not load charging price tags.</div>';
            return;
        }
    }
    const rows = tagList.map((t, i) => `
        <div class="info-row">
            <span class="info-label">${escapeHtml(t.name)}</span>
            <span class="info-value tag-edit">
                <input class="input" type="number" min="0" step="0.01" placeholder="default"
                       value="${t.price_per_kwh === null || t.price_per_kwh === undefined ? '' : t.price_per_kwh}"
                       onchange="saveTagAt(${i}, this.value)" aria-label="Price per kWh for ${escapeHtml(t.name)}">
                <span class="hint">/kWh</span>
                <button type="button" class="btn btn-small btn-danger" onclick="deleteTagAt(${i})">Delete</button>
            </span>
        </div>`).join('');
    el.innerHTML = rows + `
        <div class="detail-actions" style="margin-top:10px">
            <input id="new-tag" class="input" placeholder="New tag name" style="flex:1" maxlength="40">
            <button type="button" class="btn" onclick="addTag()">Add tag</button>
        </div>`;
}

async function saveTagAt(index, price) {
    const name = tagList[index] && tagList[index].name;
    if (!name) return;
    try {
        const data = await api('tags', {method: 'POST', body: {name, price}});
        tagList = data.tags; chargeTags = tagList;
    } catch (e) { toast(e.message, 'error'); return; }
    toast('Charging prices updated');
    renderChargeTags();
}

async function addTag() {
    const input = document.getElementById('new-tag');
    const name = (input.value || '').trim();
    if (!name) return;
    try {
        const data = await api('tags', {method: 'POST', body: {name, price: ''}});
        tagList = data.tags; chargeTags = tagList;
    } catch (e) { toast(e.message, 'error'); return; }
    input.value = '';
    renderChargeTags();
}

async function deleteTagAt(index) {
    const name = tagList[index] && tagList[index].name;
    if (!name) return;
    if (!window.confirm(`Delete the "${name}" tag? Locations using it go back to the default price.`)) return;
    try {
        const data = await api('tags/delete', {method: 'POST', body: {name}});
        tagList = data.tags; chargeTags = tagList;
    } catch (e) { toast(e.message, 'error'); return; }
    renderChargeTags();
}

function renderSettings() {
    renderAccount();
    renderVehicles();
    renderDataInfo();
    renderChargeTags();
    renderAbout();
}

onTab('settings', async () => {
    renderSettings();
    // The counts and sizes aren't part of the live stream
    try { await loadStatus(); } catch (e) { /* keep what we have */ }
    renderSettings();
});
onLive(() => { if (activeTab === 'settings') { renderAccount(); renderVehicles(); } });
