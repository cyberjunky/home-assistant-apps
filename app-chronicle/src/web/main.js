// Chronicle dashboard - startup. Loaded last.

(async function start() {
    initThemeToggle();
    updateClock();
    setInterval(updateClock, 10000);

    const hashTab = window.location.hash.slice(1).split('/')[0];
    let tab = null;
    try { tab = localStorage.getItem(LS + 'tab'); } catch (e) { /* only a convenience */ }
    await loadStatus();
    switchTab(TABS.includes(hashTab) ? hashTab : tab || 'overview');
})();

window.addEventListener('hashchange', () => {
    const [tab, entity, range] = window.location.hash.slice(1).split('/');
    if (!TABS.includes(tab)) return;
    if (tab === 'entities' && entity) {
        const id = decodeURIComponent(entity);
        if (id !== openEntityId || (range && range !== entityRange)) openEntity(id, range);
    } else if (tab !== activeTab) {
        switchTab(tab);
    }
});
