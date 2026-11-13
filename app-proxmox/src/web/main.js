// Proxmox dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);

    try {
        await loadStatus();
    } catch (e) {
        toast('Cannot reach the app: ' + e.message);
    }
    const [tab, sub] = parseHash();
    switchTab(tab || stored('tab', 'overview'), sub);
    setInterval(() => loadStatus().catch(() => {
        document.getElementById('statusbar').innerHTML = '<span class="sb-bad">● dashboard disconnected</span>';
    }), 5000);
})();

window.addEventListener('hashchange', () => {
    const [tab, sub] = parseHash();
    if (TABS.includes(tab) && (tab !== activeTab || sub !== activeSub)) switchTab(tab, sub);
});
