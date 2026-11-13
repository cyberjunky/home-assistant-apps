// Network dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);

    try {
        const [doc] = await Promise.all([api('doc'), loadStatus(), loadHosts()]);
        app.doc = doc;
        // The 7-day numbers for the tables; the Stats page reads its own period
        loadStats(7).then(() => { if (activeTab && liveHooks[activeTab]) liveHooks[activeTab](); }).catch(() => {});
    } catch (e) {
        toast('Cannot reach the app: ' + e.message);
    }
    const [tab, sub] = parseHash();
    switchTab(tab || stored('tab', 'overview'), sub);
    connectLive();
    // A safety net: every 10 seconds without the stream, every minute with it
    let lastPoll = Date.now();
    setInterval(() => {
        if (live.ok && Date.now() - lastPoll < 60000) return;
        lastPoll = Date.now();
        refreshLive().catch(() => {
            document.getElementById('statusbar').innerHTML = '<span class="sb-bad">● dashboard disconnected</span>';
        });
    }, 10000);
    // The statistics move with time too (the last 24 hours shift)
    setInterval(() => refreshStats().then(() => { if (activeTab && liveHooks[activeTab]) liveHooks[activeTab](); }), 120000);
})();

window.addEventListener('hashchange', () => {
    const [tab, sub] = parseHash();
    if (TABS.includes(tab) && (tab !== activeTab || sub !== activeSub)) switchTab(tab, sub);
});
