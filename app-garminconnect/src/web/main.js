// Garmin Connect dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    initSplitters();
    updateClock();
    setInterval(updateClock, 10000);

    // "#activities/123" opens that view and that activity; otherwise the last view used
    const [hashTab, hashId] = window.location.hash.slice(1).split('/');
    const tab = TABS.includes(hashTab) ? hashTab : localStorage.getItem(LS + 'tab');
    if (hashTab === 'activities' && Number(hashId)) pendingActivity = Number(hashId);

    await loadStatus();
    if (!statusOk) toast('Cannot reach the app', 'error');
    switchTab(tab || 'today');
    // What is being fetched changes quickly during an import; the rest of the status slowly
    setInterval(loadStatus, 5000);
})();

window.addEventListener('hashchange', () => {
    const [tab, id] = window.location.hash.slice(1).split('/');
    if (!TABS.includes(tab) || (tab === activeTab && !id)) return;
    if (tab === 'activities' && Number(id) && Number(id) !== selectedActivity) pendingActivity = Number(id);
    if (tab !== activeTab || pendingActivity) switchTab(tab);
});
