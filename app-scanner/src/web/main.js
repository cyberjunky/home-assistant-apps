// Scanner - startup. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);
    try {
        await Promise.all([loadStatus(), loadSettings()]);
    } catch (e) {
        toast('Cannot reach the app: ' + e.message);
    }
    const [tab, sub] = parseHash();
    switchTab(TABS.includes(tab) ? tab : stored('tab', 'scan'), sub);
    pollStatus();
})();

window.addEventListener('hashchange', () => {
    const [tab, sub] = parseHash();
    if (TABS.includes(tab) && (tab !== activeTab || sub !== activeSub)) switchTab(tab, sub);
});
