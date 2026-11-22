"""Configuration for Vigil: the app's options, development overrides and finding Chronicle.

The options come from /data/options.json (Home Assistant writes it from the app's
Configuration tab). The VIGIL_* environment variables override them; they are for development
and the tests, the app itself only sets VIGIL_LOG_LEVEL.
"""

import asyncio
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("VIGIL_DATA_DIR", "/data/vigil"))
OPTIONS_PATH = Path(os.environ.get("VIGIL_OPTIONS", "/data/options.json"))
SUPERVISOR_URL = "http://supervisor"
CORE_API_URL = f"{SUPERVISOR_URL}/core/api"
CHRONICLE_SLUG_SUFFIX = "app-chronicle"

# The ports inside the container (config.yaml maps them on the host)
WEB_PORT = 8099
INGEST_PORT = 4318
SYSLOG_PORT = 5514
DEFAULT_SCRAPE_INTERVAL = 60
LOG_LEVELS = ("debug", "info", "warning", "error")


def addon_version() -> str:
    """The app version from config.yaml (copied next to the sources in the image)."""
    for path in (Path("/app/config.yaml"), Path(__file__).resolve().parent.parent / "config.yaml"):
        try:
            for line in path.read_text().splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
        except OSError:
            continue
    return "dev"


ADDON_VERSION = addon_version()


@dataclass
class ScrapeTarget:
    name: str
    url: str
    interval: int = DEFAULT_SCRAPE_INTERVAL


def _int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, number))


def _text(value) -> str:
    return "" if value is None else str(value).strip()


@dataclass
class Config:
    db_host: str = ""  # empty: the Chronicle app, found through the Supervisor
    db_port: int = 5432
    db_name: str = "vigil"
    db_user: str = "vigil"
    db_password: str = field(default="", repr=False)
    ingest_token: str = field(default="", repr=False)
    scrape_targets: list[ScrapeTarget] = field(default_factory=list)
    fritzbox_host: str = ""
    fritzbox_user: str = ""
    fritzbox_password: str = field(default="", repr=False)
    notify_service: str = ""
    logs_retention_days: int = 14
    metrics_retention_days: int = 90
    log_level: str = "info"
    web_port: int = WEB_PORT
    ingest_port: int = INGEST_PORT
    syslog_port: int = SYSLOG_PORT
    # The ports devices send to (the host side of config.yaml's mapping), for the setup snippets
    public_syslog_port: int | None = 514
    public_ingest_port: int | None = 4318

    @classmethod
    def from_options(cls, options: dict, environ: dict | None = None) -> "Config":
        env = os.environ if environ is None else environ
        targets = []
        seen = set()
        for item in options.get("scrape_targets") or []:
            if not isinstance(item, dict) or not item.get("name") or not item.get("url"):
                continue
            name = _text(item["name"])
            if name in seen:
                logger.warning(f"Scrape target {name} is listed twice; only the first is used")
                continue
            seen.add(name)
            interval = _int(item.get("interval"), DEFAULT_SCRAPE_INTERVAL, 10, 3600)
            targets.append(ScrapeTarget(name=name, url=_text(item["url"]), interval=interval))
        log_level = _text(env.get("VIGIL_LOG_LEVEL") or options.get("log_level") or "info").lower()
        return cls(
            db_host=_text(env.get("VIGIL_DB_HOST", options.get("database_host"))),
            db_port=_int(env.get("VIGIL_DB_PORT", options.get("database_port")), 5432, 1, 65535),
            db_name=_text(env.get("VIGIL_DB_NAME", options.get("database_name"))) or "vigil",
            db_user=_text(env.get("VIGIL_DB_USER", options.get("database_user"))) or "vigil",
            db_password=str(env.get("VIGIL_DB_PASSWORD", options.get("database_password")) or ""),
            ingest_token=str(options.get("ingest_token") or ""),
            scrape_targets=targets,
            fritzbox_host=_text(options.get("fritzbox_host")),
            fritzbox_user=_text(options.get("fritzbox_user")),
            fritzbox_password=str(options.get("fritzbox_password") or ""),
            notify_service=_text(options.get("notify_service")),
            logs_retention_days=_int(options.get("logs_retention_days"), 14, 1, 365),
            metrics_retention_days=_int(options.get("metrics_retention_days"), 90, 1, 1825),
            log_level=log_level if log_level in LOG_LEVELS else "info",
            web_port=_int(env.get("VIGIL_PORT"), WEB_PORT, 1, 65535),
            ingest_port=_int(env.get("VIGIL_INGEST_PORT"), INGEST_PORT, 0, 65535),
            syslog_port=_int(env.get("VIGIL_SYSLOG_PORT"), SYSLOG_PORT, 0, 65535),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """The options file (missing or unreadable: the defaults, with a warning) and the overrides."""
        path = path or OPTIONS_PATH
        options: dict = {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                options = loaded
        except FileNotFoundError:
            logger.info(f"No options file at {path}; using the defaults")
        except (OSError, ValueError) as e:
            logger.warning(f"Cannot read the options in {path}: {e}")
        return cls.from_options(options)

    @property
    def notify_target(self) -> str | None:
        """The notify action's name without the domain ('notify.mobile_app_phone' -> 'mobile_app_phone')."""
        name = self.notify_service.removeprefix("notify.")
        return name or None

    def public(self) -> dict:
        """The connection as the Info page and the log show it: never a password or the token."""
        return {"host": self.db_host or None, "port": self.db_port, "name": self.db_name, "user": self.db_user}


# ---------------------------------------------------------------------- the Supervisor


async def _supervisor_get(session: aiohttp.ClientSession, path: str) -> dict:
    async with session.get(f"{SUPERVISOR_URL}{path}") as response:
        if response.status >= 300:
            raise RuntimeError(f"the Supervisor answered {response.status} for {path}")
        body = await response.json(content_type=None)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected answer from the Supervisor for {path}")
    return data


def pick_chronicle(addons: list) -> str | None:
    """The slug of the installed Chronicle app (a repository's slug ends in app-chronicle);
    a started one first when there are several."""
    found = [
        a
        for a in addons
        if isinstance(a, dict)
        and str(a.get("slug", "")).endswith(CHRONICLE_SLUG_SUFFIX)
        and a.get("installed", True) is not False
    ]
    found.sort(key=lambda a: a.get("state") != "started")
    return found[0]["slug"] if found else None


def sibling_chronicle_host(own_hostname: str) -> str | None:
    """Chronicle's host name when it comes from the same repository as Vigil: an app's host name
    is the repository's hash and its slug ("a0d7b954-app-vigil"), so Chronicle's is the same with
    app-chronicle. None when this host name isn't shaped like that."""
    own_suffix = "app-vigil"
    if not own_hostname.endswith(own_suffix) or own_hostname == own_suffix:
        return None
    return own_hostname[: -len(own_suffix)] + CHRONICLE_SLUG_SUFFIX


async def discover_chronicle() -> str:
    """The host name of the Chronicle app on the internal network; raises RuntimeError with a
    reason the Info page can show. First the sibling from the same repository (no Supervisor
    permission needed, just a name that resolves), then the Supervisor's list of apps."""
    sibling = sibling_chronicle_host(socket.gethostname())
    if sibling:
        try:
            await asyncio.get_running_loop().getaddrinfo(sibling, 5432)
            return sibling
        except OSError:
            logger.debug(f"{sibling} doesn't resolve; asking the Supervisor")
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("no database_host set, and not running under Home Assistant to find Chronicle")
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as session:
            slug = pick_chronicle((await _supervisor_get(session, "/addons")).get("addons") or [])
            if not slug:
                raise RuntimeError("the Chronicle app is not installed; install it or set database_host")
            info = await _supervisor_get(session, f"/addons/{slug}/info")
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise RuntimeError(f"cannot ask the Supervisor for the Chronicle app: {e}") from e
    hostname = info.get("hostname")
    if not hostname:
        raise RuntimeError("the Supervisor did not say the Chronicle app's host name")
    if info.get("state") not in (None, "started"):
        logger.warning(f"The Chronicle app ({slug}) is {info.get('state')}")
    return str(hostname)


async def published_ports() -> dict[str, int | None]:
    """The host ports this app's receivers are mapped to (the user may change or clear them in
    the app's Network settings); empty when that can't be asked."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {}
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as session:
            info = await _supervisor_get(session, "/addons/self/info")
    except (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError) as e:
        logger.debug(f"Cannot read this app's port mapping: {e}")
        return {}
    network = info.get("network")
    return dict(network) if isinstance(network, dict) else {}
