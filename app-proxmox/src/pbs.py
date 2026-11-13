"""Proxmox Backup Server: its own API on port 8007, read with its own token.

Every minute: the server's status, its datastores (usage, the forecast of when they are full,
garbage collection, what they hold), the verify, prune, sync and garbage collection jobs, and its
latest tasks. The list of updates and the subscription every 30 minutes. A task's log when asked.
"""

import asyncio
import logging
import time
from urllib.parse import quote

import aiohttp

from .config import PBSConfig
from .proxmox import ProxmoxAPI, ProxmoxError

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
SLOW_SECONDS = 1800
# PBS only answers about itself; its API calls its own node "localhost"
NODE = "/nodes/localhost"


def _q(part) -> str:
    return quote(str(part), safe="")


def fingerprint_ssl(fingerprint: str):
    """aiohttp's pinning of a SHA-256 certificate fingerprint ("AB:CD:..."), as Proxmox VE stores it."""
    return aiohttp.Fingerprint(bytes.fromhex(fingerprint.replace(":", "")))


def dedup_factor(gc: dict | None) -> float | None:
    """How much the datastore saves: what the backups would take on their own ÷ what their chunks take on disk."""
    if not gc:
        return None
    data, disk = gc.get("index-data-bytes"), gc.get("disk-bytes")
    return data / disk if data and disk else None


class PBSServer:
    """One backup server and the latest answer of every part of it."""

    def __init__(self, config: PBSConfig, api: ProxmoxAPI | None = None):
        self.config = config
        if config.fingerprint:
            ssl = fingerprint_ssl(config.fingerprint)
        else:
            ssl = None if config.verify_ssl else False
        self.api = api or ProxmoxAPI([config.host], f"PBSAPIToken={config.token_id}:{config.token_secret}", ssl)
        self.state: dict = {}
        self.slow: dict = {}
        self.read_at = 0.0
        self.slow_at = 0.0
        self.connected = False
        self.error = ""
        self.notes: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self.config.name

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.refresh()
                    if not self.connected:
                        logger.info(f"Backup server {self.name}: connected")
                    self.connected, self.error = True, ""
                except ProxmoxError as e:
                    if self.connected or self.error != str(e):
                        logger.warning(f"Backup server {self.name}: {e}")
                    self.connected, self.error = False, str(e)
                await asyncio.sleep(POLL_SECONDS)
        finally:
            await self.api.close()

    async def _try(self, part: str, coro, default=None):
        """One part: a refusal or a missing feature (an older PBS) becomes a note, not a failure."""
        try:
            result = await coro
            self.notes.pop(part, None)
            return result
        except ProxmoxError as e:
            if e.status == 0:
                raise
            self.notes[part] = str(e)
            return default

    async def refresh(self) -> None:
        api = self.api
        version, status, usage, stores = await asyncio.gather(
            api.get("/version"),
            self._try("status", api.get(f"{NODE}/status"), {}),
            self._try("usage", api.get("/status/datastore-usage"), []),
            self._try("datastores", api.get("/admin/datastore"), []),
        )
        names = sorted({s.get("store") for s in usage or [] if s.get("store")} | {s.get("store") for s in stores or []})

        async def store_status(store):
            return await self._try(f"store:{store}", api.get(f"/admin/datastore/{_q(store)}/status", verbose=1), {})

        details, gc, verify, prune, sync, tasks = await asyncio.gather(
            asyncio.gather(*(store_status(s) for s in names)),
            self._try("gc", api.get("/admin/gc"), None),
            self._try("verify", api.get("/admin/verify"), []),
            self._try("prune", api.get("/admin/prune"), []),
            self._try("sync", api.get("/admin/sync"), []),
            self._try("tasks", api.get(f"{NODE}/tasks", limit=100), []),
        )
        if gc is None:
            # Older PBS: no garbage collection job list, but each datastore's last run
            gc = [
                {**(await self._try(f"gc:{s}", api.get(f"/admin/datastore/{_q(s)}/gc"), {}) or {}), "store": s}
                for s in names
            ]
        if time.time() - self.slow_at >= SLOW_SECONDS:
            updates, subscription = await asyncio.gather(
                self._try("updates", api.get(f"{NODE}/apt/update"), None),
                self._try("subscription", api.get(f"{NODE}/subscription"), {}),
            )
            self.slow = {"updates": updates, "subscription": subscription or {}}
            self.slow_at = time.time()
        by_store = {s.get("store"): s for s in usage or []}
        comments = {s.get("store"): s for s in stores or []}
        gc_by_store = {g.get("store"): g for g in gc or []}
        datastores = []
        for name, detail in zip(names, details, strict=True):
            u = by_store.get(name, {})
            d = detail or {}
            g = d.get("gc-status") or u.get("gc-status") or {}
            datastores.append(
                {
                    "store": name,
                    "comment": (comments.get(name) or {}).get("comment", ""),
                    "maintenance": (comments.get(name) or {}).get("maintenance-mode")
                    or (comments.get(name) or {}).get("maintenance", ""),
                    "total": d.get("total", u.get("total")),
                    "used": d.get("used", u.get("used")),
                    "avail": d.get("avail", u.get("avail")),
                    "error": u.get("error", ""),
                    "estimated_full": u.get("estimated-full-date"),
                    "history": u.get("history") or [],
                    "history_start": u.get("history-start"),
                    "history_delta": u.get("history-delta"),
                    "gc_status": g,
                    "dedup": dedup_factor(g),
                    "counts": d.get("counts") or {},
                    "gc_job": gc_by_store.get(name) or {},
                }
            )
        self.state = {
            "version": version or {},
            "status": status or {},
            "datastores": datastores,
            "jobs": {
                "gc": [{**j, "kind": "gc"} for j in gc or []],
                "verify": [{**j, "kind": "verify"} for j in verify or []],
                "prune": [{**j, "kind": "prune"} for j in prune or []],
                "sync": [{**j, "kind": "sync"} for j in sync or []],
            },
            "tasks": sorted(tasks or [], key=lambda t: t.get("starttime", 0), reverse=True),
        }
        self.read_at = time.time()

    def summary(self) -> dict:
        return {
            "name": self.name,
            "host": self.config.host,
            "pinned": bool(self.config.fingerprint),
            "connected": self.connected,
            "error": self.error,
            "read_at": self.read_at,
            "notes": self.notes,
            **self.state,
            **self.slow,
        }

    async def task(self, upid: str) -> dict:
        base = f"{NODE}/tasks/{_q(upid)}"
        status, log = await asyncio.gather(self.api.get(f"{base}/status"), self.api.get(f"{base}/log", limit=5000))
        return {"status": status or {}, "log": [line.get("t", "") for line in log or []]}
