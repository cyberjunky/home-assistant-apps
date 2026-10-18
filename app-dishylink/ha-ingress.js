// Home Assistant ingress shim for the Dishylink dashboard.
//
// Injected as the first script in <head>, so it runs before the page's own
// first-paint theme script and before the module bundle. Two jobs:
//
// 1. Path prefix. Under ingress the page is served below
//    /api/hassio_ingress/<token>/ and the Supervisor strips that prefix before
//    forwarding to the app. The bundle is built with a relative base so its
//    assets follow the page, but the app itself asks for /api/..., /dishy/...,
//    /router/..., /cloud/... by absolute path. Those would land on Home
//    Assistant Core instead of here. Every same-origin absolute request is
//    rewritten onto the ingress prefix before it leaves the browser.
//
// 2. Theme. Dishylink's "system" theme follows the browser's
//    prefers-color-scheme, which is the operating system, not Home Assistant.
//    The ingress iframe is same-origin with the Home Assistant frontend, so the
//    shim reads Home Assistant's own dark-mode state from the parent window and
//    answers the dashboard's prefers-color-scheme query with it. The theme
//    toggle in the dashboard still works: "System" means "same as Home
//    Assistant", light and dark stay fixed.
//
// A page served at / (the optional direct port) is left untouched.
(function () {
  var match = /^(\/api\/hassio_ingress\/[^/]+)(?:\/|$)/.exec(window.location.pathname);
  if (!match) return;
  var prefix = match[1];

  // --------------------------------------------------------------------------
  // 1. Path prefix
  // --------------------------------------------------------------------------
  function rewrite(path) {
    if (typeof path !== "string") return path;
    // Relative and protocol-relative paths are left alone.
    if (path.charAt(0) !== "/" || path.charAt(1) === "/") return path;
    if (path === prefix || path.indexOf(prefix + "/") === 0) return path;
    return prefix + path;
  }

  function rewriteUrl(url) {
    if (url.origin !== window.location.origin) return null;
    return rewrite(url.pathname + url.search + url.hash);
  }

  var nativeFetch = window.fetch;
  window.fetch = function (input, init) {
    if (typeof input === "string") {
      input = rewrite(input);
    } else if (typeof URL !== "undefined" && input instanceof URL) {
      var rewrittenUrl = rewriteUrl(input);
      if (rewrittenUrl !== null) input = rewrittenUrl;
    } else if (typeof Request !== "undefined" && input instanceof Request) {
      var parsed = new URL(input.url);
      var original = parsed.pathname + parsed.search + parsed.hash;
      var target = rewriteUrl(parsed);
      if (target !== null && target !== original) {
        input = new Request(window.location.origin + target, input);
      }
    }
    return nativeFetch.call(this, input, init);
  };

  var nativeOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    var args = Array.prototype.slice.call(arguments);
    if (typeof url === "string") {
      args[1] = rewrite(url);
    } else if (typeof URL !== "undefined" && url instanceof URL) {
      var rewrittenUrl = rewriteUrl(url);
      if (rewrittenUrl !== null) args[1] = rewrittenUrl;
    }
    return nativeOpen.apply(this, args);
  };

  // --------------------------------------------------------------------------
  // 2. Theme: follow Home Assistant's dark/light mode
  // --------------------------------------------------------------------------
  var DARK_QUERY = "(prefers-color-scheme: dark)";
  var THEME_KEY = "dishylink-theme";

  /** Relative luminance of a CSS colour (#rgb, #rrggbb, rgb()/rgba()), or null. */
  function luminance(color) {
    var r, g, b, m;
    if (!color) return null;
    color = color.trim().toLowerCase();
    if ((m = /^#([0-9a-f]{3})$/.exec(color))) {
      r = parseInt(m[1].charAt(0) + m[1].charAt(0), 16);
      g = parseInt(m[1].charAt(1) + m[1].charAt(1), 16);
      b = parseInt(m[1].charAt(2) + m[1].charAt(2), 16);
    } else if ((m = /^#([0-9a-f]{6})/.exec(color))) {
      r = parseInt(m[1].slice(0, 2), 16);
      g = parseInt(m[1].slice(2, 4), 16);
      b = parseInt(m[1].slice(4, 6), 16);
    } else if ((m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/.exec(color))) {
      r = +m[1];
      g = +m[2];
      b = +m[3];
    } else {
      return null;
    }
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  }

  /**
   * Home Assistant's dark-mode state, or null when it cannot be read (not
   * embedded, or a cross-origin parent). Preferred source is the frontend's
   * own hass.themes.darkMode; fallback is the background colour the frontend
   * applies to its <html> element, which also covers custom themes; last
   * resort is the parent's prefers-color-scheme.
   */
  function haDarkMode() {
    try {
      var top = window.parent;
      if (!top || top === window) return null;
      var doc = top.document; // throws when cross-origin
      var ha = doc.querySelector("home-assistant");
      var themes = ha && ha.hass && ha.hass.themes;
      if (themes && typeof themes.darkMode === "boolean") return themes.darkMode;
      var background = top
        .getComputedStyle(doc.documentElement)
        .getPropertyValue("--primary-background-color");
      var lum = luminance(background);
      if (lum !== null) return lum < 0.5;
      return top.matchMedia(DARK_QUERY).matches;
    } catch (error) {
      return null;
    }
  }

  var current = haDarkMode();
  if (current === null) return;

  // The dashboard defaults to a fixed dark theme. Inside Home Assistant the
  // sensible default is to follow it; an explicit choice made later in the
  // dashboard's theme toggle is kept.
  try {
    if (localStorage.getItem(THEME_KEY) === null) localStorage.setItem(THEME_KEY, "system");
  } catch (error) {
    /* storage unavailable: the app falls back to its own default */
  }

  var listeners = [];
  var mediaQueryList = {
    media: DARK_QUERY,
    onchange: null,
    get matches() {
      return current;
    },
    addEventListener: function (type, listener) {
      if (type === "change" && listener && listeners.indexOf(listener) < 0) listeners.push(listener);
    },
    removeEventListener: function (type, listener) {
      var index = listeners.indexOf(listener);
      if (index >= 0) listeners.splice(index, 1);
    },
    addListener: function (listener) {
      this.addEventListener("change", listener);
    },
    removeListener: function (listener) {
      this.removeEventListener("change", listener);
    },
    dispatchEvent: function () {
      return true;
    },
  };

  function normalizeQuery(query) {
    return String(query).replace(/\s+/g, "").toLowerCase();
  }

  var nativeMatchMedia = window.matchMedia.bind(window);
  window.matchMedia = function (query) {
    if (normalizeQuery(query) === normalizeQuery(DARK_QUERY)) return mediaQueryList;
    return nativeMatchMedia(query);
  };

  function refresh() {
    var next = haDarkMode();
    if (next === null || next === current) return;
    current = next;
    var event = { type: "change", media: DARK_QUERY, matches: current, target: mediaQueryList };
    if (typeof mediaQueryList.onchange === "function") mediaQueryList.onchange(event);
    listeners.slice().forEach(function (listener) {
      try {
        if (typeof listener === "function") listener(event);
        else if (listener && typeof listener.handleEvent === "function") listener.handleEvent(event);
      } catch (error) {
        /* one listener failing must not stop the others */
      }
    });
  }

  // Home Assistant applies a theme change as inline style on its <html>
  // element; "auto" mode additionally follows the OS. A slow poll backs both
  // up, since hass.themes.darkMode can flip without either signal.
  try {
    var top = window.parent;
    new MutationObserver(refresh).observe(top.document.documentElement, {
      attributes: true,
      attributeFilter: ["style", "class"],
    });
    top.matchMedia(DARK_QUERY).addEventListener("change", refresh);
  } catch (error) {
    /* fall through to polling */
  }
  setInterval(refresh, 5000);
})();
