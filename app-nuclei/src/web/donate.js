// Asks for a donation once in a while; the server (donate.py) decides when. A small card in the
// corner that never blocks the page: no backdrop, no focus taken, and the dashboard keeps running
// and stays usable while it shows. Loaded last and self-contained: it only needs its own <script>
// tag, with data-app and data-api.
(function () {
    const script = document.currentScript;
    const appName = script.dataset.app;
    const apiUrl = script.dataset.api;
    const avatarUrl = new URL('donate-avatar.png', script.src).href;
    const WAIT_AFTER_LOAD_MS = 20000;
    const RETRY_MS = 5000;

    const CSS = `
.donate-card { position: fixed; right: 16px; bottom: 16px; z-index: 10000; width: min(320px, calc(100vw - 32px));
    box-sizing: border-box; padding: 20px 20px 8px; text-align: center; border-radius: 14px;
    background: var(--ha-card-background, var(--card, #12161c)); color: var(--primary-text-color, var(--text, #e7ebf0));
    border: 1px solid var(--divider-color, var(--line, #242b35)); box-shadow: 0 8px 28px rgba(0, 0, 0, .35); }
.donate-close { position: absolute; top: 2px; right: 2px; width: 44px; height: 44px; border: 0; background: none;
    color: var(--secondary-text-color, var(--text2, #9ba5b3)); font-size: 22px; line-height: 1; cursor: pointer; }
.donate-avatar { width: 64px; height: 64px; border-radius: 50%; background: #f2f4f7; object-fit: cover;
    border: 2px solid var(--divider-color, var(--line, #242b35)); }
.donate-title { margin: 8px 0 6px; font-size: 16px; line-height: 1.3; font-weight: 600; }
.donate-text { margin: 0 0 12px; font-size: 13px; line-height: 1.45; color: var(--secondary-text-color, var(--text2, #9ba5b3)); }
.donate-link { display: flex; align-items: center; justify-content: center; min-height: 44px; margin-bottom: 8px;
    box-sizing: border-box; border-radius: 10px; font-size: 14px; font-weight: 500; text-decoration: none;
    color: var(--primary-text-color, var(--text, #e7ebf0)); border: 1px solid var(--divider-color, var(--line, #242b35)); }
.donate-link.primary { font-weight: 600; background: var(--primary-color, var(--s1, #4c8dff)); border-color: transparent; }
.donate-link:hover { filter: brightness(1.1); }
.donate-actions { display: flex; }
.donate-later, .donate-never { flex: 1; min-height: 44px; border: 0; background: none; font: inherit; font-size: 13px; cursor: pointer; }
.donate-later { color: inherit; }
.donate-never { color: var(--secondary-text-color, var(--text2, #9ba5b3)); }`;

    // Don't appear over another dialog that is open.
    function busy() {
        return [...document.querySelectorAll('.modal, dialog[open]')].some(el => el.getClientRects().length > 0);
    }

    // The primary colour differs per app and theme: pick the readable text colour for it.
    function readableOn(element) {
        const [r, g, b] = getComputedStyle(element).backgroundColor.match(/[\d.]+/g).map(Number);
        const linear = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
        const light = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
        return 1.05 / (light + 0.05) >= 4.5 ? '#ffffff' : '#0b0e12';
    }

    function show() {
        const style = document.createElement('style');
        style.textContent = CSS;
        document.head.appendChild(style);

        const card = document.createElement('div');
        card.className = 'donate-card';
        card.setAttribute('role', 'dialog');
        card.setAttribute('aria-labelledby', 'donate-title');
        card.innerHTML = `
            <button type="button" class="donate-close" aria-label="Close">&times;</button>
            <img class="donate-avatar" alt="cyberjunky" src="${avatarUrl}">
            <div class="donate-title" id="donate-title"></div>
            <p class="donate-text">If it's useful to you, please consider supporting its development.</p>
            <a class="donate-link primary" href="https://www.paypal.me/cyberjunkynl/" target="_blank" rel="noopener">Donate via PayPal</a>
            <a class="donate-link" href="https://github.com/sponsors/cyberjunky" target="_blank" rel="noopener">Sponsor on GitHub</a>
            <div class="donate-actions">
                <button type="button" class="donate-later">Maybe later</button>
                <button type="button" class="donate-never">Don't show again</button>
            </div>`;
        card.querySelector('.donate-title').textContent = `${appName} is free and open source`;
        document.body.appendChild(card);

        const finish = action => {
            fetch(apiUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action}),
                keepalive: true,
            }).catch(() => {});
            card.remove();
            style.remove();
        };
        card.querySelector('.donate-close').addEventListener('click', () => finish('later'));
        card.querySelector('.donate-later').addEventListener('click', () => finish('later'));
        card.querySelector('.donate-never').addEventListener('click', () => finish('never'));
        card.querySelectorAll('.donate-link').forEach(link => link.addEventListener('click', () => finish('donated')));

        const primary = card.querySelector('.donate-link.primary');
        primary.style.color = readableOn(primary);
    }

    function tryToShow() {
        if (document.visibilityState === 'visible' && !busy()) show();
        else setTimeout(tryToShow, RETRY_MS);
    }

    setTimeout(async () => {
        try {
            const response = await fetch(apiUrl);
            if (response.ok && (await response.json()).show) tryToShow();
        } catch (e) { /* no prompt then */ }
    }, WAIT_AFTER_LOAD_MS);
})();
