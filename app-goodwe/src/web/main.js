// GoodWe dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);
    // "read 2 min ago" and the like keep counting between updates
    setInterval(renderStatusbar, 15000);

    const hashTab = window.location.hash.slice(1);
    const tab = TABS.includes(hashTab) ? hashTab : localStorage.getItem(LS + 'tab');

    try {
        await loadStatus();
        streamOk = true;
    } catch (e) {
        toast('Cannot reach the add-on: ' + e.message, 'error');
    }
    switchTab(tab || 'dashboard');
    connectStream();
})();

window.addEventListener('hashchange', () => {
    const tab = window.location.hash.slice(1);
    if (TABS.includes(tab) && tab !== activeTab) switchTab(tab);
});
