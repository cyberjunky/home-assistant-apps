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
    if (tab === 'databases' && entity) {
        // #databases/<database>[/<schema>/<table>]: a link to a database or a table
        const [database, schema, name] = window.location.hash.slice(1).split('/').slice(1).map(decodeURIComponent);
        const sel = dbs.selected || {};
        if (sel.database !== database || sel.schema !== schema || sel.name !== name) {
            dbs.selected = null;
            if (activeTab === 'databases') showDatabases(); else switchTab('databases');
        }
    } else if (tab === 'entities' && entity) {
        const id = decodeURIComponent(entity);
        if (id !== openEntityId || (range && range !== entityRange)) openEntity(id, range);
    } else if (tab !== activeTab) {
        switchTab(tab);
    }
});
