"""How current the scanners are: the installed nuclei, naabu and template versions, the newest CVE the
templates check, and the newest releases ProjectDiscovery has published.

The latest releases are asked of GitHub's public API at most every few hours, without any detail about
this installation. nuclei and naabu are part of the app's image, so a newer engine arrives with an app
update; the templates update themselves.
"""

import asyncio
import logging
import time

import aiohttp

from .scanner import Scanner

logger = logging.getLogger(__name__)

RELEASES = {
    "nuclei": "projectdiscovery/nuclei",
    "naabu": "projectdiscovery/naabu",
    "templates": "projectdiscovery/nuclei-templates",
}
CHECK_LATEST_EVERY = 6 * 3600
LOCAL_EVERY = 600


async def github_latest(repo: str) -> str | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "home-assistant-app-nuclei"}
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session,
            session.get(url, headers=headers) as resp,
        ):
            if resp.status != 200:
                return None
            tag = (await resp.json()).get("tag_name") or ""
            return tag.lstrip("v") or None
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.debug("Latest release of %s unknown: %s", repo, e)
        return None


def _newer(latest: str | None, installed: str | None) -> bool:
    def parts(v: str) -> tuple:
        return tuple(int(x) for x in v.split(".") if x.isdigit())

    try:
        return bool(latest and installed and parts(latest) > parts(installed))
    except ValueError:
        return False


class Freshness:
    def __init__(self, scanner: Scanner, latest=github_latest):
        self.scanner = scanner
        self._latest = latest
        self._local: dict = {}
        self._local_at = 0.0
        self._remote: dict = {}
        self._remote_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._local_at = 0.0

    async def report(self) -> dict:
        async with self._lock:
            now = time.time()
            updated_at = self.scanner.templates_updated_at()
            if now - self._local_at > LOCAL_EVERY or (updated_at or 0) > self._local_at:
                stats = await asyncio.to_thread(self.scanner.template_stats)
                self._local = {"versions": await self.scanner.versions(), "templates": stats}
                self._local_at = now
            if now - self._remote_at > CHECK_LATEST_EVERY:
                found = await asyncio.gather(*(self._latest(repo) for repo in RELEASES.values()))
                fetched = dict(zip(RELEASES, found, strict=True))
                # A failed check keeps what an earlier one found
                self._remote = {k: v or self._remote.get(k) for k, v in fetched.items()}
                self._remote_at = now
        versions = self._local.get("versions", {})
        return {
            "components": [
                {
                    "key": key,
                    "installed": versions.get(key),
                    "latest": self._remote.get(key),
                    "outdated": _newer(self._remote.get(key), versions.get(key)),
                }
                for key in RELEASES
            ],
            "templates": {**self._local.get("templates", {}), "updated_at": updated_at},
            "latest_checked_at": int(self._remote_at) or None,
        }
