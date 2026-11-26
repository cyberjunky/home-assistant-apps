// Garmin Connect dashboard - Settings: the Garmin account (sign-in with MFA), the data sets and their
// history import, the data, support and about.

function renderAccount() {
    const a = app.account;
    const el = document.getElementById('account');
    if (!a.signed_in) {
        el.innerHTML = `${a.error ? `<div class="note" style="margin-bottom:10px">${escapeHtml(a.error)}</div>` : ''}
            <p class="panel-sub" style="margin-bottom:12px">Sign in with your Garmin account. Your password is only used for this sign-in and never stored; what stays is the sign-in token Garmin gives, in the app's own data folder.</p>
            <button type="button" class="btn btn-primary" onclick="openSignIn()">Sign in to Garmin</button>`;
        return;
    }
    el.innerHTML = `
        ${infoRow('Signed in as', escapeHtml(a.name || 'your Garmin account'))}
        ${infoRow('Units', a.units === 'imperial' ? 'Imperial' : 'Metric')}
        ${infoRow('Time zone', escapeHtml(a.timezone || '–'))}
        ${infoRow('Account since', a.created ? fmtDay(a.created) : '–')}
        ${infoRow('Requests this run', num(a.requests))}
        ${a.blocked_until ? infoRow('Paused', `<span class="sb-warn">Garmin asked to slow down; continuing ${fmtIn(a.blocked_until)}</span>`) : ''}
        <div class="detail-actions" style="margin-top:12px"><button type="button" class="btn btn-danger" onclick="signOut()">Sign out</button></div>
        <p class="panel-sub" style="margin-top:8px">Signing out deletes the token; what was fetched stays.</p>`;
}

function openSignIn() {
    const overlay = openModal({
        title: 'Sign in to Garmin Connect',
        bodyHTML: `
            <div id="signin-step-1">
                <div class="form-field"><label for="gc-email">Email address</label><input class="input" id="gc-email" type="email" autocomplete="username" spellcheck="false"></div>
                <div class="form-field" style="margin-top:10px"><label for="gc-password">Password</label><input class="input" id="gc-password" type="password" autocomplete="current-password"></div>
            </div>
            <div id="signin-step-2" hidden>
                <p>Garmin sent a code by email or to your authenticator app. Enter it here.</p>
                <div class="form-field"><label for="gc-code">Code</label><input class="input mono" id="gc-code" inputmode="numeric" autocomplete="one-time-code" maxlength="10"></div>
            </div>
            <div class="form-error" id="signin-error"></div>
            <p class="panel-sub">The password goes to Garmin once and is not stored. Garmin may take some seconds to answer.</p>`,
        footHTML: '<button class="btn" data-close>Cancel</button><button class="btn btn-primary" id="signin-go">Sign in</button>',
    });
    const go = overlay.querySelector('#signin-go');
    const error = overlay.querySelector('#signin-error');
    let step = 1;
    const submit = async () => {
        error.textContent = '';
        go.disabled = true;
        go.textContent = step === 1 ? 'Signing in…' : 'Checking…';
        try {
            if (step === 1) {
                const result = await post('auth/login', {email: overlay.querySelector('#gc-email').value, password: overlay.querySelector('#gc-password').value});
                overlay.querySelector('#gc-password').value = '';
                if (result.result === 'mfa') {
                    step = 2;
                    overlay.querySelector('#signin-step-1').hidden = true;
                    overlay.querySelector('#signin-step-2').hidden = false;
                    overlay.querySelector('#gc-code').focus();
                    go.textContent = 'Confirm';
                    go.disabled = false;
                    return;
                }
            } else {
                await post('auth/mfa', {code: overlay.querySelector('#gc-code').value});
            }
            closeModal();
            toast('Signed in. Fetching your data…', 'success');
            await loadStatus();
            if (tabHooks[activeTab]) tabHooks[activeTab]();
        } catch (e) {
            error.textContent = e.message;
            go.disabled = false;
            go.textContent = step === 1 ? 'Sign in' : 'Confirm';
        }
    };
    go.onclick = submit;
    overlay.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); }));
    overlay.addEventListener('click', e => { if ((e.target === overlay || e.target.hasAttribute('data-close')) && step === 2) post('auth/cancel').catch(() => {}); });
    overlay.querySelector('#gc-email').focus();
}

async function signOut() {
    if (!await confirmDialog({title: 'Sign out of Garmin?', message: 'The token is deleted and nothing more is fetched until you sign in again. What was fetched stays.', confirmLabel: 'Sign out', danger: true})) return;
    try {
        await post('auth/signout');
        await loadStatus();
    } catch (e) {
        toast(e.message, 'error');
    }
}

function renderFetching() {
    const c = app.collector;
    const el = document.getElementById('fetching');
    el.innerHTML = `
        ${infoRow('Now', escapeHtml(c.phase || '–') + (c.current ? ` · ${escapeHtml(c.current)}` : ''))}
        ${infoRow('Last update', c.last_update_at ? fmtAgo(c.last_update_at) : '–')}
        ${infoRow('Next update', c.next_update_at ? fmtIn(c.next_update_at) : '–')}
        ${infoRow('Update interval', `${num(c.update_interval_minutes)} min`)}
        ${infoRow('History', c.import_history ? `back to ${fmtDay(c.history_start)}` : 'off')}
        ${infoRow('Activity tracks', c.tracks && c.tracks.enabled ? (c.tracks.missing ? `${num(c.tracks.missing)} still to fetch` : 'all fetched') : 'off')}
        ${c.error ? `<div class="note" style="margin-top:10px">${escapeHtml(c.error)}</div>` : ''}
        <div class="detail-actions" style="margin-top:12px"><button type="button" class="btn" onclick="updateNow()">Update now</button></div>`;
}

function renderDatasets() {
    const c = app.collector;
    const groups = (app.status && app.status.groups) || [];
    const sets = c.datasets || [];
    const el = document.getElementById('datasets');
    el.innerHTML = '<div class="datasets">' + groups.map(g => {
        const mine = sets.filter(s => s.group === g.key);
        let rows = mine.map(s => {
            const covered = s.covered === null || s.covered === undefined ? null : s.covered;
            const state = !s.enabled ? '<span class="muted">off</span>'
                : s.kind === 'snapshot' ? (s.last_fetch ? `fetched ${fmtAgo(s.last_fetch)}` : 'not yet')
                : s.done ? '<span class="sb-ok">complete</span>'
                : !c.import_history ? 'latest days only'
                : s.oldest ? `back to ${fmtDayY(s.oldest)}` : 'waiting';
            return `<div class="ds-row${s.enabled ? '' : ' off'}">
                <span class="ds-name">${escapeHtml(s.title)}</span>
                <span class="ds-bar">${covered === null || !s.enabled ? '' : `<span class="track"><i style="width:${(covered * 100).toFixed(1)}%"></i></span>`}</span>
                <span class="ds-state">${state}${s.last_error ? ` <span class="sb-warn" title="${escapeHtml(s.last_error)}">⚠</span>` : ''}</span>
            </div>`;
        }).join('');
        if (g.key === 'activity_tracks') {
            const t = c.tracks || {};
            rows += `<div class="ds-row${g.enabled ? '' : ' off'}"><span class="ds-name">GPS tracks, one request per activity</span><span class="ds-bar"></span>
                <span class="ds-state">${!g.enabled ? '<span class="muted">off</span>' : t.missing ? `${num(t.missing)} to fetch` : '<span class="sb-ok">complete</span>'}</span></div>`;
        }
        return `<div class="ds-group"><div class="ds-head"><span>${escapeHtml(g.title)}</span><span class="badge${g.enabled ? '' : ' off'}">${g.enabled ? 'on' : 'off'}</span></div>${rows}</div>`;
    }).join('') + '</div>';
}

function renderDataInfo() {
    const counts = (app.status && app.status.counts) || {};
    document.getElementById('data-info').innerHTML = `
        ${infoRow('Days', counts.days ? `${num(counts.days)} · ${fmtDayY(counts.first_day)} – ${fmtDayY(counts.last_day)}` : '–')}
        ${infoRow('Daily values', num(counts.values_))}
        ${infoRow('Activities', `${num(counts.activities)} · ${num(counts.tracks)} tracks`)}
        ${infoRow('Day curves', num(counts.curves))}
        ${infoRow('Events', `${num(counts.events)} (weigh-ins, badges, ECGs, …)`)}
        ${infoRow('Garmin answers kept', num(counts.answers))}
        ${infoRow('Database', `${num((app.status.database_bytes || 0) / 1048576, 1)} MB`)}`;
    document.getElementById('export-daily').href = basePath + 'api/export/daily.csv';
    document.getElementById('export-activities').href = basePath + 'api/export/activities.csv';
    document.getElementById('export-db').href = basePath + 'api/backup';
}

function renderAbout() {
    document.getElementById('support').innerHTML = `
        ${infoRow('Documentation', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-garminconnect/DOCS.md" target="_blank" rel="noopener">Docs on GitHub</a>')}
        ${infoRow('Problem or idea', '<a href="https://github.com/cyberjunky/home-assistant-apps/issues" target="_blank" rel="noopener">Open an issue</a>')}
        ${infoRow('Version', `v${escapeHtml(app.version)} · <a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-garminconnect/CHANGELOG.md" target="_blank" rel="noopener">changelog</a>`)}`;
    document.getElementById('about').innerHTML = `
        ${infoRow('Garmin library', '<a href="https://github.com/cyberjunky/python-garminconnect" target="_blank" rel="noopener">python-garminconnect</a>')}
        ${infoRow('Maps', 'OpenStreetMap, through Home Assistant')}
        <p class="panel-sub" style="margin-top:10px">Everything stays on your Home Assistant: the app talks only to Garmin Connect, and the map tiles come through Home Assistant's own proxy.
        This app is an independent project and is not affiliated with or endorsed by Garmin Ltd.</p>`;
}

async function reparse(button) {
    button.disabled = true;
    try {
        const result = await post('reparse');
        toast(`Read again: ${num(result.rows)} values`, 'success');
        await loadStatus();
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        button.disabled = false;
    }
}

async function deleteAllData() {
    const ok = await confirmDialog({
        title: 'Delete all fetched data?',
        message: 'Everything fetched from Garmin is deleted. Your sign-in stays, and the whole history is fetched again from the start.',
        confirmLabel: 'Delete everything', danger: true, typeToConfirm: 'DELETE',
    });
    if (!ok) return;
    try {
        await post('data/delete', {confirm: 'DELETE'});
        toast('All fetched data deleted', 'success');
        await loadStatus();
    } catch (e) {
        toast(e.message, 'error');
    }
}

function loadSettings() {
    renderAccount();
    renderFetching();
    renderDatasets();
    renderDataInfo();
    renderAbout();
}

onTab('settings', loadSettings);
onStatus(() => { if (activeTab === 'settings' && !document.querySelector('.modal-overlay')) loadSettings(); });
