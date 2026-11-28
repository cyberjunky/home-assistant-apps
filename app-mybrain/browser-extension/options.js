import { check, originPattern, settings } from './share.js';

const $ = (id) => document.getElementById(id);

function show(text, ok) {
    $('status').textContent = text;
    $('status').className = `status ${ok === true ? 'ok' : ok === false ? 'bad' : ''}`;
}

settings().then(({ ha, token }) => {
    $('ha').value = ha;
    $('token').value = token;
});

$('form').addEventListener('submit', async (event) => {
    event.preventDefault();
    let ha;
    try {
        const url = new URL($('ha').value.trim());
        if (!/^https?:$/.test(url.protocol)) throw new Error();
        ha = url.origin + url.pathname.replace(/\/+$/, '');
    } catch {
        show('That is not a web address.', false);
        return;
    }
    // Asked for here, while the click still counts as the person's: Edge only allows it then
    const allowed = await chrome.permissions.request({ origins: [originPattern(ha)] });
    if (!allowed) {
        show('Without that permission the extension cannot reach Home Assistant.', false);
        return;
    }
    await chrome.storage.local.set({ ha, token: $('token').value.trim() });
    $('ha').value = ha;
    $('save').disabled = true;
    show('Testing…');
    try {
        await check();
        show('Connected ✓', true);
    } catch (e) {
        show(e.message, false);
    } finally {
        $('save').disabled = false;
    }
});
