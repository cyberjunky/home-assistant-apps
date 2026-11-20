// Repair - start-up and the poll that keeps the open page current.

async function loadStatus() {
    const was = app.status && app.status.scanned_at;
    app.status = await api('status');
    // The findings are only read again when a new scan is in
    if (app.status.scanned_at !== was) {
        app.findings = (await api('findings')).findings;
        app.log = (await api('log')).entries;
    }
    renderChrome();
    renderActive();
}

async function poll() {
    try {
        await loadStatus();
    } catch (e) {
        document.getElementById('statusbar').innerHTML = `<span class="sb-bad">● the app does not answer</span><span class="spacer"></span><span class="sb-extra">${escapeHtml(e.message)}</span>`;
    }
    setTimeout(poll, app.status && app.status.scanning ? 1500 : 10000);
}

initThemeToggle();
updateClock();
setInterval(updateClock, 10000);
switchTab(window.location.hash.slice(1) || stored('tab') || 'overview');
// An address can point at one finding (?finding=<id>) or at one entity or device to clean up (?inspect=device:<id>)
poll().then(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get('finding')) openFinding(query.get('finding'));
    const [type, ...rest] = (query.get('inspect') || '').split(':');
    if (rest.length) { switchTab('cleanup'); inspectItem(type, rest.join(':')); }
});
