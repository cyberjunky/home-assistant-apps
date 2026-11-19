"""The web addresses written in an integration's code: which are on the internet, and which deserve a look."""

import ipaddress
import re

# Addresses that are in code as documentation, namespaces or examples, not as something to call
REFERENCE = {
    "github.com", "www.github.com", "home-assistant.io", "www.home-assistant.io", "my.home-assistant.io",
    "developers.home-assistant.io", "community.home-assistant.io", "brands.home-assistant.io", "hacs.xyz",
    "www.hacs.xyz", "w3.org", "www.w3.org", "schema.org", "json-schema.org", "python.org", "docs.python.org",
    "pypi.org", "wikipedia.org", "en.wikipedia.org", "creativecommons.org", "apache.org", "www.apache.org",
    "gnu.org", "www.gnu.org", "opensource.org", "purl.org", "xmlsoap.org", "schemas.xmlsoap.org", "upnp.org",
    "schemas.upnp.org", "www.upnp.org", "tools.ietf.org", "ietf.org", "www.ietf.org", "datatracker.ietf.org",
    "stackoverflow.com", "example.com", "example.org", "example.net", "www.example.com", "buymeacoffee.com",
    "www.buymeacoffee.com", "paypal.me", "www.paypal.me", "ko-fi.com", "mozilla.org", "developer.mozilla.org",
    "schemas.microsoft.com", "schemas.openxmlformats.org", "openid.net", "specs.openid.net", "ns.adobe.com",
}  # fmt: skip
LOCAL_NAMES = {"localhost", "homeassistant", "supervisor", "hassio", "host.docker.internal", "core"}
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".localhost")

# Places made to receive whatever is sent to them
DROPBOX = (
    "webhook.site", "requestbin.com", "requestbin.net", "pipedream.net", "ngrok.io", "ngrok-free.app", "ngrok.app",
    "interact.sh", "oast.fun", "oast.pro", "oast.live", "oast.site", "burpcollaborator.net", "transfer.sh", "0x0.st",
    "file.io", "pastebin.com", "paste.ee", "hastebin.com", "anonfiles.com", "serveo.net", "localtunnel.me",
    "loca.lt", "trycloudflare.com", "beeceptor.com", "requestcatcher.com",
)  # fmt: skip
CHAT = ("hooks.slack.com", "api.telegram.org", "discord.com/api/webhooks", "discordapp.com/api/webhooks")
TELEMETRY = (
    "google-analytics.com", "googletagmanager.com", "sentry.io", "posthog.com", "mixpanel.com", "segment.io",
    "segment.com", "amplitude.com", "datadoghq.com", "bugsnag.com", "rollbar.com", "newrelic.com",
)  # fmt: skip
DOWNLOAD = re.compile(r"\.(py|sh|zip|tar\.gz|tgz|whl|exe|bin|deb|rpm|so)([?#]|$)", re.I)
TWO_PART_SUFFIXES = {"co.uk", "com.au", "co.jp", "com.br", "co.nz", "com.cn", "co.za", "com.tr", "org.uk", "co.kr"}
HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


def _matches(host: str, names: tuple[str, ...] | set[str]) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def site(host: str) -> str:
    """The name a service goes by: api.eu.vendor.com -> vendor.com."""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    return ".".join(parts[-3:] if ".".join(parts[-2:]) in TWO_PART_SUFFIXES else parts[-2:])


def classify(scheme: str, rest: str) -> dict | None:
    """What an address is. None when it has no fixed host (it is filled in while running)."""
    authority, _, path = rest.partition("/")
    host = authority.rsplit("@", 1)[-1].lower()
    if host.startswith("["):
        host = host[1:].split("]")[0]
    elif host.count(":") == 1:
        host = host.split(":")[0]
    host = host.strip(".")
    if not host or any(c in host for c in "{}%$<>*"):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None and (not HOST.match(host) or ("." not in host and host not in LOCAL_NAMES)):
        return None
    full = f"{host}/{path}"
    insecure = scheme in ("http", "ws", "ftp", "mqtt")
    if address is not None:
        local = not address.is_global
        kind = "local" if local else "ip"
    elif host in LOCAL_NAMES or host.endswith(LOCAL_SUFFIXES):
        kind = "local"
    elif _matches(host, DROPBOX):
        kind = "dropbox"
    elif any(marker in full for marker in CHAT):
        kind = "chat"
    elif _matches(host, TELEMETRY):
        kind = "telemetry"
    elif host in REFERENCE or host.startswith("schemas."):
        kind = "reference"
    else:
        kind = "external"
    download = bool(DOWNLOAD.search(path)) and kind != "local"
    if download and kind == "reference":
        kind = "external"
    return {
        "host": host,
        "site": host if address is not None else site(host),
        "kind": kind,
        "insecure": insecure and kind != "local",
        "download": download,
    }
