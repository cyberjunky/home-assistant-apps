"""The Marketplace catalog, the same one Home Assistant reads: which repositories exist, which were removed
and why, when each last changed. And Home Assistant's analytics: how many installations use an integration.

Fetched at most once a day and kept in the data folder, so a scan works without the internet.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

SOURCES = {
    "integration": "https://data-v2.hacs.xyz/integration/data.json",
    "removed": "https://data-v2.hacs.xyz/removed/data.json",
    "installs": "https://analytics.home-assistant.io/custom_integrations.json",
}
REFRESH_EVERY = 24 * 3600


class Catalog:
    def __init__(self, cache_dir: Path, sources: dict[str, str] | None = None):
        self.cache_dir = cache_dir
        self.sources = sources or SOURCES
        self.fetched_at = 0.0
        self.error: str | None = None
        self._by_repository: dict[str, dict] = {}
        self._by_domain: dict[str, dict] = {}
        self._removed: dict[str, dict] = {}
        self._installs: dict[str, int] = {}
        self._load_cache()

    def _path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _load_cache(self) -> None:
        data = {}
        for name in self.sources:
            try:
                data[name] = json.loads(self._path(name).read_text())
                self.fetched_at = max(self.fetched_at, self._path(name).stat().st_mtime)
            except (OSError, ValueError):
                continue
        self._index(data)

    def _index(self, data: dict) -> None:
        integration = data.get("integration")
        if isinstance(integration, dict):
            entries = [e for e in integration.values() if isinstance(e, dict) and e.get("full_name")]
            self._by_repository = {str(e["full_name"]).lower(): e for e in entries}
            self._by_domain = {str(e["domain"]): e for e in entries if e.get("domain")}
        removed = data.get("removed")
        if isinstance(removed, list):
            self._removed = {
                str(r["repository"]).lower(): r for r in removed if isinstance(r, dict) and r.get("repository")
            }
        installs = data.get("installs")
        if isinstance(installs, dict):
            self._installs = {
                d: int(v["total"])
                for d, v in installs.items()
                if isinstance(v, dict) and isinstance(v.get("total"), int)
            }

    async def refresh(self, session: aiohttp.ClientSession, force: bool = False) -> None:
        """Fetch what is older than a day. What fails keeps its earlier copy."""
        if not force and time.time() - self.fetched_at < REFRESH_EVERY and self._by_repository:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        errors = []
        for name, url in self.sources.items():
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=90)) as response:
                    if response.status != 200:
                        raise aiohttp.ClientError(f"answered {response.status}")
                    body = await response.read()
                json.loads(body)
                await asyncio.to_thread(self._path(name).write_bytes, body)
            except (aiohttp.ClientError, TimeoutError, ValueError, OSError) as e:
                errors.append(f"{name}: {type(e).__name__} {e}".strip())
        self.error = "; ".join(errors) or None
        if self.error:
            logger.warning("Catalog not fully refreshed: %s", self.error)
        await asyncio.to_thread(self._load_cache)

    @property
    def loaded(self) -> bool:
        return bool(self._by_repository)

    def entry(self, repository: str | None = None, domain: str | None = None) -> dict | None:
        if repository and (found := self._by_repository.get(repository.lower())):
            return found
        return self._by_domain.get(domain) if domain else None

    def removed(self, repository: str | None) -> dict | None:
        return self._removed.get(repository.lower()) if repository else None

    def installs(self, domain: str) -> int | None:
        return self._installs.get(domain)

    def top(self, count: int) -> list[dict]:
        """The catalog's integrations with the most installations, most first."""
        ranked = sorted(
            (e for e in self._by_domain.values() if self._installs.get(e["domain"])),
            key=lambda e: -self._installs[e["domain"]],
        )
        return ranked[:count]
