// Toon dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);

    const hashTab = window.location.hash.slice(1);
    let stored = null;
    try { stored = localStorage.getItem(LS + 'tab'); } catch (e) { /* only a convenience */ }
    const tab = TABS.includes(hashTab) ? hashTab : stored;

    try {
        app.status = await api('status');
        renderStatusbar();
    } catch (e) {
        toast('Cannot reach the app: ' + e.message);
    }
    switchTab(tab || 'dashboard');
    setInterval(() => loadStatus().catch(() => {
        document.getElementById('statusbar').innerHTML = '<span class="sb-bad">● dashboard disconnected</span>';
    }), 5000);
})();

window.addEventListener('hashchange', () => {
    const tab = window.location.hash.slice(1);
    if (TABS.includes(tab) && tab !== activeTab) switchTab(tab);
});
