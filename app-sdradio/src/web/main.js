// SDRadio dashboard - startup. Loaded last.

(async function start() {
    renderThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);
    initRadio();
    initModes();
    initChannels();
    initStats();
    await Promise.all([loadStatus(), loadSettings(), loadChannels()]);
    connectSocket();
    const hashTab = window.location.hash.slice(1);
    switchTab(TABS.includes(hashTab) ? hashTab : lsGet('tab', 'radio'));
})();

window.addEventListener('hashchange', () => {
    const tab = window.location.hash.slice(1);
    if (TABS.includes(tab) && tab !== activeTab) switchTab(tab);
});
