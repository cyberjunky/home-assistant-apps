// MQTT dashboard - startup. Loaded last.

// Leaving the Traffic page stops its messages: the stream then carries the status only
const showTab = switchTab;
switchTab = function (tabName, sub) {
    showTab(tabName, sub);
    if (activeTab !== 'traffic' && live.params) connectLive('');
};

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
    switchTab(TABS.includes(tab) ? tab : stored('tab', 'overview'), sub);
    if (activeTab !== 'traffic') connectLive('');
    // A safety net when the stream is down
    setInterval(() => {
        if (live.ok) return;
        loadStatus().then(() => { if (activeTab && liveHooks[activeTab]) liveHooks[activeTab](); }).catch(() => {
            document.getElementById('statusbar').innerHTML = '<span class="sb-bad">● dashboard disconnected</span>';
        });
    }, 10000);
})();

window.addEventListener('hashchange', () => {
    const [tab, sub] = parseHash();
    if (TABS.includes(tab) && (tab !== activeTab || sub !== activeSub)) switchTab(tab, sub);
});
