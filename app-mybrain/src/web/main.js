// MyBrain - start-up and the poll that keeps the open page current.

// After a change: the status, and the overview's numbers read again on its next draw
async function refreshStatus() {
    overviewLoadedAt = 0;
    await loadStatus();
}

async function loadStatus() {
    app.status = await api('status');
    renderChrome();
}

function renderChrome() {
    const s = app.status;
    const inboxCount = s.counts.by_status.inbox;
    document.getElementById('nav-inbox').textContent = inboxCount ? num(inboxCount) : '';
    const parts = [
        s.listening ? '<span class="sb-ok">● listening for shares</span>'
            : s.config.listen_shares ? '<span class="sb-bad">● not connected to Home Assistant</span>' : '<span class="sb-warn">● shares off</span>',
        s.queue ? `<span>reading <b>${num(s.queue)}</b></span>` : '',
        s.ai.entity ? `<span>sorting with <b>${escapeHtml(s.ai.entity)}</b>${s.ai.error ? ' <span class="sb-warn">(failing)</span>' : ''}</span>` : '<span>sorting by rules</span>',
        '<span class="spacer"></span>',
        s.viewer.name ? `<span>👤 <b>${escapeHtml(s.viewer.name)}</b></span>` : '',
        `<span class="sb-extra">${num(s.counts.total)} items · ${fmtBytes(s.database_size)} · v${escapeHtml(s.version)}</span>`,
    ];
    document.getElementById('statusbar').innerHTML = parts.filter(Boolean).join('');
}

let lastSignature = '';
async function poll() {
    try {
        await loadStatus();
        // Redraw the open page when something came in or finished reading, unless the sheet is open
        const signature = JSON.stringify([app.status.counts, app.status.queue]);
        if (signature !== lastSignature) {
            const first = !lastSignature;
            lastSignature = signature;
            overviewLoadedAt = 0;
            if (first || !sheetItem) renderActive();
        }
    } catch (e) {
        document.getElementById('statusbar').innerHTML = `<span class="sb-bad">● the app does not answer</span><span class="spacer"></span><span class="sb-extra">${escapeHtml(e.message)}</span>`;
    }
    setTimeout(poll, app.status && app.status.queue ? 2000 : 8000);
}

// A link to #inbox, #library, ... opens that page, also when only the part after # changes
window.addEventListener('hashchange', () => {
    const tab = window.location.hash.slice(1);
    if (TABS.includes(tab) && tab !== activeTab) switchTab(tab);
});

initThemeToggle();
updateClock();
setInterval(updateClock, 10000);
// An address can open one item (?item=<id>), search (?q=), or start the Add dialog (?add=<text>)
poll().then(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get('whose')) library.whose = query.get('whose');
    if (query.get('q') || query.get('whose')) { library.q = query.get('q') || ''; if (query.get('whose')) library.status = ''; switchTab('library'); }
    else switchTab(window.location.hash.slice(1) || stored('tab') || 'overview');
    if (query.get('item')) openItem(Number(query.get('item')));
    if (query.get('add')) openAdd(query.get('add'));
});
