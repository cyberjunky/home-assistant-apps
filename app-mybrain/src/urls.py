"""Links: finding them in shared text, writing them one way so a link saved twice is seen as the same
one, and telling where they come from."""

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
# Punctuation that ends a sentence, not the link
TRAILING = ".,;:!?)]}'\"\u00bb\u201d\u2019"

# Query parameters that only say who shared or where the click came from
TRACKING = {
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "igsh",
    "si",
    "share_id",
    "shareid",
    "ref_src",
    "ref_url",
    "rdt",
    "smid",
    "cmpid",
    "_hsenc",
    "_hsmi",
    "spm",
    "scm",
    "ocid",
    "trk",
    "sr_share",
}
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "itm_", "at_", "hsa_")
# Per site: parameters that are only about sharing there (t and s on X are share tokens)
SITE_TRACKING = {
    "x.com": {"t", "s"},
    "reddit.com": {"context"},
    "instagram.com": {"img_index"},
    "youtube.com": {"pp", "ab_channel", "feature"},
}

# Hosts written one way: the mobile, old and short forms of a site point at the same thing
HOST_ALIASES = {
    "twitter.com": "x.com",
    "mobile.twitter.com": "x.com",
    "mobile.x.com": "x.com",
    "fxtwitter.com": "x.com",
    "vxtwitter.com": "x.com",
    "nitter.net": "x.com",
    "old.reddit.com": "reddit.com",
    "new.reddit.com": "reddit.com",
    "m.reddit.com": "reddit.com",
    "np.reddit.com": "reddit.com",
    "m.youtube.com": "youtube.com",
    "music.youtube.com": "youtube.com",
    "m.facebook.com": "facebook.com",
    "mobile.facebook.com": "facebook.com",
    "m.wikipedia.org": "wikipedia.org",
}

# Where something comes from: site → source key. Checked on the host and its parents
SOURCES = {
    "reddit.com": "reddit",
    "redd.it": "reddit",
    "x.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "github.com": "github",
    "gist.github.com": "github",
    "news.ycombinator.com": "hackernews",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
    "linkedin.com": "linkedin",
    "tiktok.com": "tiktok",
    "wikipedia.org": "wikipedia",
    "medium.com": "medium",
    "substack.com": "substack",
    "stackoverflow.com": "stackoverflow",
    "stackexchange.com": "stackoverflow",
    "amazon.com": "shop",
    "amazon.nl": "shop",
    "amazon.de": "shop",
    "bol.com": "shop",
    "aliexpress.com": "shop",
    "coolblue.nl": "shop",
    "marktplaats.nl": "shop",
    "ebay.com": "shop",
    "community.home-assistant.io": "forum",
    "home-assistant.io": "docs",
    "spotify.com": "spotify",
    "maps.google.com": "maps",
    "maps.app.goo.gl": "maps",
    "goo.gl": "web",
}
# The Android app something was shared from, when the link itself doesn't say
CALLERS = {
    "com.reddit.frontpage": "reddit",
    "com.twitter.android": "x",
    "com.google.android.youtube": "youtube",
    "com.instagram.android": "instagram",
    "com.facebook.katana": "facebook",
    "com.linkedin.android": "linkedin",
    "com.zhiliaoapp.musically": "tiktok",
    "com.github.android": "github",
    "com.google.android.keep": "keep",
    "com.google.android.gm": "email",
    "com.spotify.music": "spotify",
    "com.google.android.apps.maps": "maps",
    "com.whatsapp": "whatsapp",
    "org.telegram.messenger": "telegram",
}
# What each source is called on the page
SOURCE_NAMES = {
    "reddit": "Reddit",
    "x": "X",
    "youtube": "YouTube",
    "github": "GitHub",
    "hackernews": "Hacker News",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "tiktok": "TikTok",
    "wikipedia": "Wikipedia",
    "medium": "Medium",
    "substack": "Substack",
    "stackoverflow": "Stack Overflow",
    "shop": "Shop",
    "forum": "HA forum",
    "docs": "HA docs",
    "spotify": "Spotify",
    "maps": "Maps",
    "keep": "Keep",
    "email": "Email",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "web": "Web",
    "note": "Note",
}


def find_urls(text: str) -> list[str]:
    """Every link in a piece of text, in order, each once."""
    found: list[str] = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(TRAILING)
        # A closing bracket that belongs to the link (wikipedia's Foo_(bar)) stays
        if url.count("(") > url.count(")") and match[len(url) : len(url) + 1] == ")":
            url += ")"
        if url not in found:
            found.append(url)
    return found


def host_of(url: str) -> str:
    """The host, lower case, without www."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _parents(host: str):
    parts = host.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def normalize(url: str) -> str:
    """One way of writing a link, used to see that two links are the same thing.

    Lower-case host without www, the mobile and old forms of a site folded into one, no tracking
    parameters, no fragment, no trailing slash, youtu.be written out. The link that is opened stays
    the one that was shared; this is only for comparing.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return url.strip()
    host = parts.hostname.lower()
    host = host[4:] if host.startswith("www.") else host
    host = HOST_ALIASES.get(host, host)
    if host.endswith(".wikipedia.org") and host.split(".")[1] == "m":
        host = host.replace(".m.", ".", 1)
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query = parse_qsl(parts.query, keep_blank_values=True)

    if host == "youtu.be" and len(path) > 1:
        query = [("v", path.strip("/").split("/")[0]), *query]
        host, path = "youtube.com", "/watch"
    elif host == "youtube.com" and path.startswith("/shorts/"):
        query = [("v", path.split("/")[2]), *query]
        path = "/watch"

    site_drop = next((SITE_TRACKING[p] for p in _parents(host) if p in SITE_TRACKING), set())
    kept = [
        (k, v)
        for k, v in query
        if k.lower() not in TRACKING and not k.lower().startswith(TRACKING_PREFIXES) and k not in site_drop
    ]
    kept.sort()
    if len(path) > 1:
        path = path.rstrip("/")
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return urlunsplit(("https", host + port, path, urlencode(kept), ""))


def source_of(url: str | None, caller: str = "") -> str:
    """Where something comes from: a source key from SOURCES, 'web' for any other site, 'note' without a link."""
    if url:
        host = HOST_ALIASES.get(host_of(url), host_of(url))
        for parent in _parents(host):
            if parent in SOURCES:
                return SOURCES[parent]
        if host.endswith(".substack.com"):
            return "substack"
    called = CALLERS.get((caller or "").strip())
    if called:
        return called
    return "web" if url else "note"


SHORTENERS = {
    "t.co",
    "bit.ly",
    "tinyurl.com",
    "goo.gl",
    "ow.ly",
    "buff.ly",
    "lnkd.in",
    "is.gd",
    "rebrand.ly",
    "tiny.cc",
    "shorturl.at",
    "cutt.ly",
    "s.id",
    "amzn.to",
    "a.co",
    "dlvr.it",
    "trib.al",
    "maps.app.goo.gl",
    "vm.tiktok.com",
    "share.google",
}


def is_short_link(url: str) -> bool:
    """A link that only forwards to another one: t.co, bit.ly, Reddit's /s/ share links."""
    host = host_of(url)
    if host in SHORTENERS:
        return True
    path = urlsplit(url).path
    return source_of(url) == "reddit" and bool(re.match(r"^/r/[^/]+/s/\w+", path))


def is_private_host(host: str) -> bool:
    """A host on the home network or this machine, which the app doesn't fetch from."""
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".local", ".lan", ".home", ".internal", ".localdomain")):
        return True
    if "." not in host and ":" not in host:
        return True  # a bare name, like homeassistant or supervisor
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not address.is_global


def youtube_id(url: str) -> str:
    """The video id of a YouTube link, or empty."""
    parts = urlsplit(normalize(url))
    if parts.hostname == "youtube.com" and parts.path == "/watch":
        return dict(parse_qsl(parts.query)).get("v", "")
    return ""
