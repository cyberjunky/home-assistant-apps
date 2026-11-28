import { settings, share } from './share.js';

const $ = (id) => document.getElementById(id);

async function start() {
    const { ha, token } = await settings();
    if (!ha || !token) {
        $('setup').hidden = false;
        $('open-options').addEventListener('click', () => {
            chrome.runtime.openOptionsPage();
            window.close();
        });
        return;
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    $('form').hidden = false;
    $('title').textContent = tab?.title || '';
    $('url').textContent = tab?.url || '';
    $('note').focus();
    if (!/^https?:/i.test(tab?.url || '')) {
        $('save').disabled = true;
        show('Only web pages can be saved.', false);
        return;
    }
    $('form').addEventListener('submit', async (event) => {
        event.preventDefault();
        $('save').disabled = true;
        show('Saving…');
        try {
            await share({ url: tab.url, title: tab.title || '', text: $('note').value });
            show('Saved ✓', true);
            setTimeout(() => window.close(), 900);
        } catch (e) {
            show(e.message, false);
            $('save').disabled = false;
        }
    });
    $('note').addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) $('form').requestSubmit();
    });
}

function show(text, ok) {
    $('status').textContent = text;
    $('status').className = `status ${ok === true ? 'ok' : ok === false ? 'bad' : ''}`;
}

start();
