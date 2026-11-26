"""ckpool, the local solo mining pool, read from the status files it writes every minute.

ckpool has no API of its own: it writes its state as small JSON files in its logs folder
(pool/pool.status and one file per payout address under users/). On the node that folder is served
read-only over HTTP, and that address is pool.url. Only GET requests are sent: nothing is changed.
"""

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Callable
from urllib.parse import quote, unquote

import aiohttp

from .config import Config

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
MISSES_ALLOWED = 2
# ckpool rewrites its status every minute; older than this and ckpool itself has stopped
STALE_SECONDS = 180
# A worker without a share for this long counts as offline
WORKER_IDLE_SECONDS = 600
# Payout addresses read per poll, at most
MAX_USERS = 50
BLOCK_SECONDS = 600
SUFFIXES = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18, "Z": 1e21}
HASHRATE_KEYS = ("1m", "5m", "15m", "1hr", "6hr", "1d", "7d")


class PoolError(Exception):
    """The status files can't be read."""


def parse_hashrate(text) -> float | None:
    """ckpool's hashrate text ("4.56T", "870G", "0") as hashes per second."""
    if isinstance(text, int | float):
        return float(text)
    match = re.fullmatch(r"\s*([0-9.]+(?:e[+-]?\d+)?)\s*([KMGTPEZ]?)\s*", str(text or ""), re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1)) * SUFFIXES[match.group(2).upper()]
    except ValueError:
        return None


def hashrates(data: dict) -> dict:
    return {key: parse_hashrate(data[f"hashrate{key}"]) for key in HASHRATE_KEYS if f"hashrate{key}" in data}


def parse_status(text: str) -> dict:
    """pool.status: a JSON object per line (counts, hashrates, shares, and SV2 when built with it), as one dict."""
    merged: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            merged.update(value)
    if "lastupdate" not in merged and "runtime" not in merged:
        raise PoolError("pool/pool.status is not ckpool's status file")
    return merged


def listing_names(html: str) -> list[str]:
    """The file names in a directory listing (Python's http.server, nginx, Apache)."""
    names = []
    for href in re.findall(r'href="([^"]+)"', html):
        name = unquote(href)
        if name.startswith(("?", "/", ".", "#")) or "/" in name.rstrip("/") or name.endswith("/") or ":" in name:
            continue
        if name not in names:
            names.append(name)
    return names


def worker_view(worker: dict, now: float) -> dict:
    name = str(worker.get("workername") or "")
    last = worker.get("lastshare") or None
    return {
        "name": name,
        # The part after the address: what the miner calls itself
        "label": re.split(r"[._]", name, maxsplit=1)[1] if re.search(r"[._]", name) else name,
        "hashrate": hashrates(worker),
        "last_share": last,
        "active": bool(last and now - last < WORKER_IDLE_SECONDS),
        "shares": worker.get("shares"),
        "best_share": worker.get("bestshare"),
        "best_ever": worker.get("bestever"),
    }


def user_view(address: str, data: dict, now: float) -> dict:
    return {
        "address": address,
        "hashrate": hashrates(data),
        "last_share": data.get("lastshare") or None,
        "workers": data.get("workers"),
        "shares": data.get("shares"),
        "best_share": data.get("bestshare"),
        "best_ever": data.get("bestever"),
        "authorised": data.get("authorised") or None,
        "worker": [worker_view(w, now) for w in data.get("worker") or [] if isinstance(w, dict)],
    }


def odds(hashrate: float | None, network_hashrate: float | None) -> dict:
    """The chance of finding a block, going by the pool's share of the network's hashrate."""
    if not hashrate or not network_hashrate:
        return {}
    share = hashrate / network_hashrate
    per_block = min(1.0, share)

    def within(seconds: float) -> float:
        return 1 - math.exp(-per_block * seconds / BLOCK_SECONDS)

    return {
        "share": share,
        "day": within(86400),
        "month": within(30 * 86400),
        "year": within(365 * 86400),
        "expected_seconds": BLOCK_SECONDS / per_block,
    }


class PoolClient:
    """ckpool's state, kept current by run()."""

    def __init__(self, config: Config, emit: Callable[..., None], difficulty: Callable[[], float | None]):
        self.config = config
        self.emit = emit
        # The network's current difficulty: a share at least this hard is a block
        self.difficulty = difficulty
        self.configured = bool(config.pool_url)
        self.connected = False
        self.error = ""
        self.version = ""
        self.pool: dict = {}
        self.users: list[dict] = []
        self.updated: float | None = None
        self._misses = 0
        self._active: dict[str, bool] = {}
        self._best: dict[str, float] = {}

    async def run(self) -> None:
        if not self.configured:
            return
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            while True:
                try:
                    await self.poll(session)
                    self._set_link(True, "")
                except PoolError as e:
                    self._set_link(False, str(e))
                except (aiohttp.ClientError, TimeoutError, OSError) as e:
                    self._set_link(False, f"no answer from {self.config.pool_url}: {str(e) or type(e).__name__}")
                except Exception as e:
                    logger.exception("Pool: reading failed")
                    self._set_link(False, repr(e))
                await asyncio.sleep(max(30, self.config.poll_seconds))

    def _set_link(self, connected: bool, error: str) -> None:
        if connected:
            self._misses = 0
        elif self.connected:
            self._misses += 1
            if self._misses <= MISSES_ALLOWED:
                logger.debug(f"Pool: missed a poll ({self._misses}): {error}")
                return
        if connected and not self.connected:
            logger.info(f"Pool: reading ckpool's status from {self.config.pool_url}")
        elif not connected and (self.connected or error != self.error):
            logger.warning(f"Pool: {error}")
            if self.connected:
                self.emit("pool", "pool", "bad", f"The pool's status can't be read: {error}")
        self.connected, self.error = connected, error

    async def _get(self, session: aiohttp.ClientSession, path: str) -> str:
        async with session.get(f"{self.config.pool_url}/{path}") as response:
            if response.status == 404:
                raise PoolError(f"{path} is not there: pool.url must serve ckpool's logs folder")
            if response.status != 200:
                raise PoolError(f"{path}: HTTP {response.status}")
            return await response.text()

    async def poll(self, session: aiohttp.ClientSession) -> None:
        now = time.time()
        pool = parse_status(await self._get(session, "pool/pool.status"))
        users = []
        try:
            names = listing_names(await self._get(session, "users/"))
        except PoolError:
            # Before the first share ckpool has not made the folder yet
            names = []
        for name in names[:MAX_USERS]:
            try:
                data = json.loads(await self._get(session, "users/" + quote(name)))
            except (PoolError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                users.append(user_view(name, data, now))
        self.pool, self.users, self.updated = pool, users, now
        self._watch(now)

    def _watch(self, now: float) -> None:
        """Tell when a worker stops or starts again, and when a share was hard enough to be a block."""
        difficulty = self.difficulty()
        for user in self.users:
            for worker in user["worker"]:
                name, active = worker["name"], worker["active"]
                was = self._active.get(name)
                if was is not None and was != active:
                    if active:
                        self.emit("pool", "worker_online", "ok", f"{worker['label']} is mining again", worker=name)
                    else:
                        self.emit(
                            "pool",
                            "worker_offline",
                            "warn",
                            f"{worker['label']} sent no share for {WORKER_IDLE_SECONDS // 60} minutes",
                            worker=name,
                        )
                self._active[name] = active
                best = float(worker["best_ever"] or 0)
                previous = self._best.get(name)
                if previous is not None and best > previous and difficulty and best >= difficulty:
                    self.emit(
                        "pool",
                        "block_found",
                        "ok",
                        f"{worker['label']} found a block! Share difficulty {best:,.0f}, the network needed {difficulty:,.0f}",
                        worker=name,
                        difficulty=best,
                    )
                self._best[name] = best

    @property
    def stale(self) -> bool:
        last = self.pool.get("lastupdate")
        return bool(self.updated and last and self.updated - last > STALE_SECONDS)

    def hashrate(self) -> float | None:
        """The pool's hashrate for the odds: the last hour, the last 5 minutes while ckpool just started."""
        rates = hashrates(self.pool)
        if (self.pool.get("runtime") or 0) < 3600:
            return rates.get("5m") or rates.get("1hr")
        return rates.get("1hr") or rates.get("5m")

    def workers(self) -> list[dict]:
        return [dict(w, address=u["address"]) for u in self.users for w in u["worker"]]

    def status(self, network_hashrate: float | None) -> dict:
        if not self.configured:
            return {"configured": False}
        pool = self.pool
        workers = self.workers()
        best_ever = max((float(w["best_ever"] or 0) for w in workers), default=None)
        return {
            "configured": True,
            "connected": self.connected,
            "error": self.error,
            "stale": self.stale,
            "updated": self.updated,
            "last_update": pool.get("lastupdate"),
            "runtime": pool.get("runtime"),
            "users": pool.get("Users"),
            "workers": pool.get("Workers"),
            "idle": pool.get("Idle"),
            "disconnected": pool.get("Disconnected"),
            "hashrate": hashrates(pool),
            "accepted": pool.get("accepted"),
            "rejected": pool.get("rejected"),
            "best_share": pool.get("bestshare"),
            "best_ever": best_ever,
            "sps": pool.get("SPS1m"),
            "active_workers": sum(1 for w in workers if w["active"]),
            "odds": odds(self.hashrate(), network_hashrate) if self.connected else {},
        }

    def details(self) -> dict:
        return {"users": self.users}
