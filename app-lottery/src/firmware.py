"""The newest firmware release of the Bitaxe and NerdQAxe firmware, from GitHub.

Only for miners whose firmware is known for sure: the NerdQAxe fork (it reports a deviceModel) and
Bitaxe boards (numbered board versions like 601). Other makers' forks, like Lucky Miner's, have their own
releases, so they get no firmware entity rather than a wrong one. The app only tells; installing stays
in AxeOS.
"""

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

REPO_ESP_MINER = "bitaxeorg/ESP-Miner"
REPO_NERDQAXE = "shufps/ESP-Miner-NerdQAxePlus"
REFRESH_SECONDS = 6 * 3600
RETRY_SECONDS = 1800


def repo_for(info: dict) -> str | None:
    if info.get("deviceModel"):
        return REPO_NERDQAXE
    board = str(info.get("boardVersion") or "")
    if board.isdigit():
        return REPO_ESP_MINER
    return None


class Firmware:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.releases: dict[str, dict] = {}

    def latest(self, repo: str | None) -> dict | None:
        return self.releases.get(repo) if repo else None

    async def run(self, session: aiohttp.ClientSession) -> None:
        if not self.enabled:
            return
        while True:
            failed = False
            for repo in (REPO_ESP_MINER, REPO_NERDQAXE):
                try:
                    async with (
                        asyncio.timeout(15),
                        session.get(
                            f"https://api.github.com/repos/{repo}/releases/latest",
                            headers={"Accept": "application/vnd.github+json"},
                        ) as r,
                    ):
                        r.raise_for_status()
                        data = await r.json()
                    if isinstance(data, dict) and data.get("tag_name"):
                        self.releases[repo] = {
                            "version": str(data["tag_name"]),
                            "url": data.get("html_url"),
                            "notes": str(data.get("body") or "")[:20000],
                        }
                except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                    logger.debug(f"GitHub {repo}: {e}")
                    failed = True
            await asyncio.sleep(RETRY_SECONDS if failed else REFRESH_SECONDS)
