// Tesla dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    initFollowToggle();
    initSplitters();
    updateClock();
    setInterval(updateClock, 10000);
    // "seen 2 min ago" and the like keep counting between updates
    setInterval(renderStatusbar, 15000);

    // "#trips/12" opens that view (and that trip); otherwise the last view used
    const [hashTab, hashId] = window.location.hash.slice(1).split('/');
    const tab = TABS.includes(hashTab) ? hashTab : localStorage.getItem(LS + 'tab');
    if (hashTab === 'trips' && Number(hashId)) pendingTrip = Number(hashId);
    if (hashTab === 'charging' && Number(hashId)) pendingCharge = Number(hashId);

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
    const [tab, id] = window.location.hash.slice(1).split('/');
    if (!TABS.includes(tab) || (tab === activeTab && !id)) return;
    if (tab === 'trips' && Number(id) && Number(id) !== selectedTrip) pendingTrip = Number(id);
    if (tab === 'charging' && Number(id) && Number(id) !== selectedCharge) pendingCharge = Number(id);
    if (tab !== activeTab || pendingTrip || pendingCharge) switchTab(tab);
});
