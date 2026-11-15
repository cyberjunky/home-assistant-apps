// Put first in the pages PBS serves through the sidebar (see panel.py). PBS's interface asks for its
// own absolute paths (/api2/json/..., /images/..., the console's websocket); here they live below
// Home Assistant's ingress path, window.PBSAPP_BASE. This sends them there, and keeps PBS's login
// cookie on that path. PBS's own code is unchanged.
(function () {
  'use strict';
  const BASE = window.PBSAPP_BASE;
  if (!BASE) return;
  const ORIGIN = window.location.origin;

  // '/x' -> BASE + '/x', leaving '//host/x' and paths already below BASE alone
  function fixRoot(url) {
    return url.startsWith('/') && !url.startsWith('//') && !url.startsWith(BASE + '/') && url !== BASE ? BASE + url : url;
  }

  // '/api2/json/x' -> BASE + '/api2/json/x'; the same for full URLs on this origin
  function fix(url) {
    if (typeof url !== 'string' || url === '') return url;
    if (url.startsWith('/') && !url.startsWith('//')) {
      return url.startsWith(BASE + '/') || url === BASE ? url : BASE + url;
    }
    try {
      const u = new URL(url, window.location.href);
      const sameHost = u.host === window.location.host;
      if (sameHost && /^(https?|wss?):$/.test(u.protocol) && !u.pathname.startsWith(BASE + '/')) {
        u.pathname = BASE + u.pathname;
        return u.toString();
      }
    } catch (e) { /* not a URL */ }
    return url;
  }
  window.PBSAPP_FIX = fix;

  // XMLHttpRequest (ExtJS's Ajax) and fetch
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    return open.call(this, method, fix(String(url)), ...rest);
  };
  // Home Assistant's ingress passes a Content-Type without its parameters: PBS's
  // "application/json;charset=UTF-8" arrives as "application/json", and PBS's console
  // (pve-xtermjs) only takes JSON with the ';'. Give PBS's code the header as PBS sent it
  const getResponseHeader = XMLHttpRequest.prototype.getResponseHeader;
  const withCharset = type => (typeof type === 'string' && /^application\/json\s*$/i.test(type) ? 'application/json;charset=UTF-8' : type);
  XMLHttpRequest.prototype.getResponseHeader = function (name) {
    const value = getResponseHeader.call(this, name);
    return String(name).toLowerCase() === 'content-type' ? withCharset(value) : value;
  };
  const getAllResponseHeaders = XMLHttpRequest.prototype.getAllResponseHeaders;
  XMLHttpRequest.prototype.getAllResponseHeaders = function () {
    const all = getAllResponseHeaders.call(this);
    return typeof all === 'string' ? all.replace(/^(content-type:\s*)application\/json\s*$/gim, '$1application/json;charset=UTF-8') : all;
  };
  const realFetch = window.fetch;
  if (realFetch) {
    window.fetch = function (input, init) {
      if (typeof input === 'string' || input instanceof URL) return realFetch.call(this, fix(String(input)), init);
      if (input instanceof Request && fix(input.url) !== input.url) return realFetch.call(this, new Request(fix(input.url), input), init);
      return realFetch.call(this, input, init);
    };
  }

  // The console's websocket (ws://host/api2/json/.../vncwebsocket)
  const RealWebSocket = window.WebSocket;
  function PBSWebSocket(url, protocols) {
    return protocols === undefined ? new RealWebSocket(fix(String(url))) : new RealWebSocket(fix(String(url)), protocols);
  }
  PBSWebSocket.prototype = RealWebSocket.prototype;
  Object.assign(PBSWebSocket, {CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3});
  window.WebSocket = PBSWebSocket;

  // New windows (the console) and links PBS builds with root paths
  const realOpen = window.open;
  window.open = function (url, ...rest) { return realOpen.call(this, url === undefined ? url : fix(String(url)), ...rest); };

  // Root paths in elements as PBS creates them, before the browser loads anything: by attribute
  // (src, href) and in HTML strings (ExtJS builds much of its page that way)
  const setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    const low = String(name).toLowerCase();
    return setAttribute.call(this, name, (low === 'src' || low === 'href') && typeof value === 'string' ? fixRoot(value) : value);
  };
  for (const [proto, prop] of [[HTMLIFrameElement.prototype, 'src'], [HTMLImageElement.prototype, 'src'],
    [HTMLScriptElement.prototype, 'src'], [HTMLLinkElement.prototype, 'href'], [HTMLAnchorElement.prototype, 'href']]) {
    const d = Object.getOwnPropertyDescriptor(proto, prop);
    if (d && d.set) {
      Object.defineProperty(proto, prop, {...d, set(value) { d.set.call(this, typeof value === 'string' ? fixRoot(value) : value); }});
    }
  }
  const ROOT_LINK = /\b(src|href)=(["'])(\/[^"']*)/g;
  const fixHtml = html => (typeof html === 'string' ? html.replace(ROOT_LINK, (m, attr, q, url) => `${attr}=${q}${fixRoot(url)}`) : html);
  const insertAdjacentHTML = Element.prototype.insertAdjacentHTML;
  Element.prototype.insertAdjacentHTML = function (where, html) { return insertAdjacentHTML.call(this, where, fixHtml(html)); };
  for (const prop of ['innerHTML', 'outerHTML']) {
    const d = Object.getOwnPropertyDescriptor(Element.prototype, prop);
    if (d && d.set) Object.defineProperty(Element.prototype, prop, {...d, set(value) { d.set.call(this, fixHtml(value)); }});
  }

  // Elements PBS puts in the page with root paths (its logo, icons, download links)
  const ATTRS = {IMG: 'src', SCRIPT: 'src', LINK: 'href', A: 'href', IFRAME: 'src', SOURCE: 'src'};
  function fixElement(el) {
    const attr = ATTRS[el.tagName];
    if (!attr) return;
    const value = el.getAttribute(attr);
    if (value && value.startsWith('/') && !value.startsWith('//') && !value.startsWith(BASE + '/')) {
      el.setAttribute(attr, BASE + value);
    }
  }
  new MutationObserver(records => {
    for (const r of records) {
      if (r.type === 'attributes') { fixElement(r.target); continue; }
      for (const node of r.addedNodes) {
        if (node.nodeType !== 1) continue;
        fixElement(node);
        node.querySelectorAll && node.querySelectorAll('img,script,link,a,iframe,source').forEach(fixElement);
      }
    }
  }).observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ['src', 'href']});

  // The cookies PBS's page sets itself (the readable PBSAuthCookie that says it is logged in, its
  // language and theme) belong to the root path, some with Secure; here they go on the ingress path,
  // without Secure when Home Assistant is on http. Its ticket cookie is set by PBS's server (see panel.py).
  const desc = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
  if (desc && desc.get && desc.set) {
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      get() { return desc.get.call(this); },
      set(value) {
        let parts = String(value).split(';').map(p => p.trim()).filter(Boolean);
        parts[0] = parts[0].replace(/^__Host-/, '');
        parts = parts.filter(p => !/^(path|domain)=/i.test(p) && !(window.location.protocol === 'http:' && /^secure$/i.test(p)));
        parts.push(`path=${BASE}/`);
        desc.set.call(this, parts.join('; '));
      },
    });
  }
})();
