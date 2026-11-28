// The right-click menu and the keyboard shortcut. The button itself opens popup.html.
import { share } from './share.js';

chrome.runtime.onInstalled.addListener(async ({ reason }) => {
    chrome.contextMenus.removeAll(() => {
        chrome.contextMenus.create({ id: 'page', title: 'Save page to MyBrain', contexts: ['page'] });
        chrome.contextMenus.create({ id: 'link', title: 'Save link to MyBrain', contexts: ['link'] });
        chrome.contextMenus.create({ id: 'selection', title: 'Save to MyBrain with this text', contexts: ['selection'] });
        chrome.contextMenus.create({ id: 'image', title: 'Save image to MyBrain', contexts: ['image'] });
    });
    if (reason === 'install') chrome.runtime.openOptionsPage();
});

// A mark on the button for a few seconds: ✓ saved, ! not saved (the reason is in its tooltip)
async function mark(ok, tabId, message = '') {
    await chrome.action.setBadgeBackgroundColor({ color: ok ? '#15803d' : '#b91c1c' });
    await chrome.action.setBadgeText({ text: ok ? '✓' : '!', tabId });
    await chrome.action.setTitle({ title: ok ? 'Saved to MyBrain' : `Not saved: ${message}`, tabId });
    setTimeout(() => {
        chrome.action.setBadgeText({ text: '', tabId });
        chrome.action.setTitle({ title: 'Save to MyBrain', tabId });
    }, ok ? 2500 : 8000);
}

async function save(what, tab) {
    try {
        await share(what);
        await mark(true, tab?.id);
    } catch (e) {
        await mark(false, tab?.id, e.message);
    }
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === 'link') {
        save({ url: info.linkUrl, title: info.selectionText || '' }, tab);
    } else if (info.menuItemId === 'image') {
        save({ url: info.srcUrl, title: tab?.title || '' }, tab);
    } else if (info.menuItemId === 'selection') {
        save({ url: info.pageUrl || tab?.url, title: tab?.title || '', text: info.selectionText || '' }, tab);
    } else {
        save({ url: info.pageUrl || tab?.url, title: tab?.title || '' }, tab);
    }
});

chrome.commands.onCommand.addListener(async (command, tab) => {
    if (command !== 'save-page') return;
    tab = tab || (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
    if (tab) save({ url: tab.url, title: tab.title || '' }, tab);
});
