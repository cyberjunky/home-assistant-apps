// Start: the theme, the clock, the tab the address asks for, and the connection to the app.
'use strict';

applyTheme(); setInterval(applyTheme, 2000);
const tick = () => { $('clock').textContent = fmtClock(Date.now() / 1000).slice(0, 5); };
tick(); setInterval(tick, 10000);

(async () => {
  try { state = {...state, ...await api('state')}; } catch (e) { /* the socket brings it */ }
  socketUp = true; renderState();
  await loadModel(true);
  await loadLive();
  connect();
  // ?tab= and ?address= open the app on a page or on one address's details (a link from elsewhere, a screenshot)
  if (PARAMS.get('tab')) showTab(PARAMS.get('tab')); else renderTab();
  if (PARAMS.get('mode') === 'archive') $('monitor-mode').querySelector('[data-v=archive]').click();
  if (PARAMS.get('address')) openDrawer(PARAMS.get('address'));
})();

// The tables follow the bus: the model is read again while a page that shows it is open
setInterval(() => { if (document.visibilityState === 'visible' && ['overview', 'addresses', 'devices', 'findings', 'ha'].includes(activeTab)) loadModel(false); }, 5000);
