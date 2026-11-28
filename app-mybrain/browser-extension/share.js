// Sending to MyBrain: the same mobile_app.share event the Home Assistant phone app sends.
// Home Assistant puts the token's user on the event, so the item lands in that person's MyBrain.

export async function settings() {
    const { ha = '', token = '' } = await chrome.storage.local.get(['ha', 'token']);
    return { ha: ha.replace(/\/+$/, ''), token };
}

export function originPattern(ha) {
    return `${new URL(ha).origin}/*`;
}

// Turn Home Assistant's answer into a sentence a person can act on
function explain(status) {
    if (status === 401) return 'Home Assistant refused the token. Make a new one in the options.';
    if (status === 403) return 'This token may not send events: it must belong to an administrator.';
    if (status === 404) return 'No Home Assistant API at that address. Check it in the options.';
    return `Home Assistant answered ${status}.`;
}

async function call(path, init = {}) {
    const { ha, token } = await settings();
    if (!ha || !token) throw new Error('Not set up yet: fill in the options first.');
    if (!(await chrome.permissions.contains({ origins: [originPattern(ha)] }))) {
        throw new Error('The extension may not reach Home Assistant yet: press Save in the options.');
    }
    let response;
    try {
        response = await fetch(`${ha}${path}`, {
            ...init,
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        });
    } catch {
        throw new Error(`Home Assistant at ${ha} does not answer.`);
    }
    if (!response.ok) throw new Error(explain(response.status));
    return response.json();
}

// Is Home Assistant there, and does it take the token?
export async function check() {
    await call('/api/');
}

// url: the link. title: the page's title. text: a note or the selected text, may be empty.
export async function share({ url, title = '', text = '' }) {
    if (!/^https?:/i.test(url || '')) throw new Error('Only web pages can be saved.');
    const data = { url, subject: title.trim(), caller: 'browser' };
    if (text.trim()) data.text = text.trim();
    await call('/api/events/mobile_app.share', { method: 'POST', body: JSON.stringify(data) });
}
