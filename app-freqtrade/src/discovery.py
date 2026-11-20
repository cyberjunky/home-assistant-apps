"""Read-only API discovery. Known bots survive outages; identity changes retire old clients."""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import aiohttp

from .bot import BotClient
from .config import BotConfig
from .discovery_settings import DiscoveryConfig

logger = logging.getLogger(__name__)
MAX_BOTS = 128
IDENTITY_FIELDS = ("bot_name", "strategy", "dry_run", "exchange", "trading_mode", "stake_currency")


def identity(url: str, config: dict) -> str:
    payload = json.dumps([url, {key: config.get(key) for key in IDENTITY_FIELDS}], sort_keys=True)
    return "discovered_" + hashlib.sha256(payload.encode()).hexdigest()[:20]


class DiscoveryService:
    def __init__(self, settings: DiscoveryConfig, registry: Path, poll_seconds, database, manual, register):
        self.settings = settings
        self.path = registry
        self.poll_seconds = poll_seconds
        self.database = database
        self.manual_urls = {bot.url for bot in manual}
        self.register = register
        self.records = {}
        self.clients = {}
        self.tasks = {}
        self.last_scan = None
        self.last_found = 0
        self.scanning = False
        self.error = ""
        self._logged: int | None = None

    def status(self):
        return {
            "enabled": self.settings.enabled,
            "host": self.settings.host,
            "ports": len(self.settings.ports),
            "interval_seconds": self.settings.interval_seconds,
            "last_scan": self.last_scan,
            "last_found": self.last_found,
            "known_bots": len(self.records),
            "scanning": self.scanning,
            "error": self.error,
        }

    async def probe(self, session, port, semaphore):
        url = self.settings.url(port)
        if url in self.manual_urls:
            return None
        async with semaphore:
            try:
                # First identify the protocol without sending credentials.
                async with session.get(url + "/api/v1/ping", allow_redirects=False) as response:
                    if response.status != 200 or (await response.json()).get("status") != "pong":
                        return None
                async with session.get(
                    url + "/api/v1/show_config",
                    auth=aiohttp.BasicAuth(self.settings.username, self.settings.password),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        return None
                    config = await response.json()
                if not isinstance(config, dict) or not isinstance(config.get("dry_run"), bool):
                    return None
                if not isinstance(config.get("strategy"), str) or not isinstance(config.get("exchange"), str):
                    return None
                return {"url": url, "port": port, "config": {key: config.get(key) for key in IDENTITY_FIELDS}}
            except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError, TypeError):
                return None

    async def accept(self, record):
        url, config = record["url"], record["config"]
        slug = identity(url, config)
        if slug not in self.records and len(self.records) >= MAX_BOTS:
            self.error = f"{MAX_BOTS} bots found, which is the limit. Give fewer ports."
            return
        # A different strategy/mode/account description on the same endpoint must
        # not silently continue the old history. A database reset is not detectable.
        for old_slug, old in self.records.items():
            if old["url"] == url and old_slug != slug and not old.get("retired"):
                old["retired"] = True
                task = self.tasks.pop(old_slug, None)
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                client = self.clients.get(old_slug)
                if client:
                    client.connected = client.ws_connected = False
                    client.error = "Another bot answers at this address now. The history of this one is kept."
        if slug not in self.records:
            # "freqtrade" is the name of every bot that was never given one: the strategy says more
            own = config.get("bot_name")
            name = f"{own if own and own != 'freqtrade' else config['strategy']} · {record['port']}"
            self.records[slug] = {**record, "name": name, "retired": False}
        entry = self.records[slug]
        entry["retired"] = False
        if slug not in self.clients:
            cfg = BotConfig(
                entry["name"],
                slug,
                url,
                self.settings.username,
                self.settings.password,
                self.settings.ws_token,
                self.settings.verify_ssl,
            )
            client = BotClient(cfg, self.poll_seconds, self.database)
            client.data["config"] = config
            self.clients[slug] = client
            self.register(client)
        task = self.tasks.get(slug)
        if task is None or task.done():
            if task and not task.cancelled() and task.exception():
                logger.warning("Restarting discovery client %s", slug)
            self.tasks[slug] = asyncio.create_task(self.clients[slug].run(), name=slug)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # This file contains endpoint identities only, never credentials.
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(self.records, stream)
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    async def restore(self):
        try:
            saved = json.loads(self.path.read_text())
            if not isinstance(saved, dict):
                raise ValueError
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            self.error = "The list of found bots could not be read; the search starts over. The history is untouched."
            return
        allowed = {self.settings.url(port) for port in self.settings.ports} - self.manual_urls
        for slug, record in list(saved.items())[:MAX_BOTS]:
            if not isinstance(record, dict) or record.get("url") not in allowed:
                continue
            config = record.get("config")
            if not isinstance(config, dict) or identity(record["url"], config) != slug:
                continue
            if not isinstance(record.get("port"), int) or record["port"] not in self.settings.ports:
                continue
            if record.get("retired"):
                # Keep its identity in the registry, but do not poll a replaced endpoint.
                self.records[slug] = record
                continue
            await self.accept(record)

    async def scan_once(self):
        self.scanning = True
        self.error = ""
        try:
            connector = aiohttp.TCPConnector(limit=8, ssl=None if self.settings.verify_ssl else False)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3), connector=connector) as session:
                semaphore = asyncio.Semaphore(8)
                results = await asyncio.gather(*(self.probe(session, port, semaphore) for port in self.settings.ports))
            found = [record for record in results if record is not None]
            for record in found:
                await self.accept(record)
            self.last_found = len(found)
            self.save()
            self.last_scan = time.time()
            # Every search at debug; at info only when it found something else than before
            (logger.info if self.last_found != self._logged else logger.debug)(
                "Looked for bots on %s: %s ports tried, %s answered, %s known (%s ports left out: configured bots)",
                self.settings.host,
                len(self.settings.ports),
                self.last_found,
                len(self.records),
                sum(self.settings.url(port) in self.manual_urls for port in self.settings.ports),
            )
            self._logged = self.last_found
        finally:
            self.scanning = False

    async def run(self):
        try:
            await self.restore()
            while True:
                try:
                    await self.scan_once()
                except Exception:  # A search that fails must not end the app: the known bots keep running
                    self.error = "The last search failed; the bots that were found keep running. See the app's log."
                    logger.exception("Looking for bots failed")
                await asyncio.sleep(self.settings.interval_seconds)
        finally:
            for task in self.tasks.values():
                task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
