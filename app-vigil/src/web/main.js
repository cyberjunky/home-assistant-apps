// Vigil dashboard - startup and the hash: #<tab>/<arg>/<from>/<to>. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);

    const h = parseHash();
    if (h.range) timeRange = h.range;
    renderRangeBar();
    let tab = null;
    try { tab = localStorage.getItem(LS + 'tab'); } catch (e) { /* only a convenience */ }
    await loadStatus();
    if (TABS.includes(h.tab)) switchTab(h.tab, h.arg);
    else switchTab(TABS.includes(tab) ? tab : 'overview');

    // The status bar stays current: receivers, queue, dropped, firing
    setInterval(() => {
        if (document.visibilityState !== 'visible') return;
        const wasReady = ready();
        loadStatus().then(() => {
            // Connected again: show what the page couldn't before
            if (!wasReady && ready() && tabHooks[activeTab]) tabHooks[activeTab](hashArgs[activeTab] || '');
        });
    }, 15000);
    // A preset range moves with time: keep its text current
    setInterval(() => { if (timeRange.preset) document.getElementById('range-text').textContent = rangeText(); }, 30000);
})();

// A link or Back/Forward: another tab, argument or range
window.addEventListener('hashchange', () => {
    const h = parseHash();
    if (!TABS.includes(h.tab)) return;
    const rangeChanged = h.range && rangeToHash() !== `${h.range.preset ? h.range.preset + '/now' : Math.round(h.range.from) + '/' + Math.round(h.range.to)}`;
    if (rangeChanged) {
        timeRange = h.range;
        rangeHistory.length = 0;
        renderRangeBar();
    }
    if (h.tab !== activeTab) return switchTab(h.tab, h.arg);
    if (h.arg !== (hashArgs[activeTab] || '') || rangeChanged) {
        hashArgs[activeTab] = h.arg;
        if (h.tab === 'logs') { setLogQuery(h.arg); runLogSearch(); }
        else if (tabHooks[activeTab]) tabHooks[activeTab](h.arg);
    }
});
