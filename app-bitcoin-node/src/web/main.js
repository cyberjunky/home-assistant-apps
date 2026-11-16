// Bitcoin Node dashboard - start-up and the poll that keeps the open page current.

async function poll() {
    try {
        await loadStatus();
    } catch (e) {
        document.getElementById('statusbar').innerHTML = `<span class="sb-bad">● the app does not answer</span><span class="spacer"></span><span class="sb-extra">${escapeHtml(e.message)}</span>`;
    }
    setTimeout(poll, ((app.status && app.status.poll_seconds) || 15) * 1000);
}

initThemeToggle();
updateClock();
setInterval(updateClock, 10000);
switchTab(window.location.hash.slice(1) || stored('tab') || 'overview');
poll();
