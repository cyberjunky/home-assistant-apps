// Freqtrade dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);

    let stored = null;
    try { stored = localStorage.getItem(LS + 'tab'); } catch (e) { /* only a convenience */ }
    try {
        app.status = await api('status');
        buildBotTabs();
        renderStatusbar();
    } catch (e) {
        toast('Cannot reach the app: ' + e.message);
    }
    switchTab(window.location.hash.slice(1) || stored || 'overview');
    setInterval(() => loadStatus().catch(() => {
        document.getElementById('statusbar').innerHTML = '<span class="sb-bad">● dashboard disconnected</span>';
    }), 5000);
})();

window.addEventListener('hashchange', () => {
    const tab = window.location.hash.slice(1);
    if (tabNames().includes(tab) && tab !== activeTab) switchTab(tab);
});
