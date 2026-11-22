"""The Bitcoin network: difficulty, block height and what a block pays.

The miners report the network difficulty and block height themselves, from their pool. With
network_data on, the app also reads the last blocks from mempool.space: the difficulty for when no
miner is running, and the fees a block paid lately, for the prize. Only public data is asked for;
nothing about you or your miners is sent.
"""

import asyncio
import logging
import time

import aiohttp

from .odds import subsidy

logger = logging.getLogger(__name__)

MEMPOOL_BLOCKS = "https://mempool.space/api/v1/blocks"
REFRESH_SECONDS = 600
RETRY_SECONDS = 120
# Reported by a miner within this long counts as current
FRESH_SECONDS = 900


class Network:
    def __init__(self, enabled: bool, miners: list | None = None):
        self.enabled = enabled
        self.miners = miners or []
        self.mempool: dict = {}
        self.error = ""
        self.updated: float | None = None

    async def run(self, session: aiohttp.ClientSession) -> None:
        if not self.enabled:
            return
        while True:
            try:
                await self.refresh(session)
                wait = REFRESH_SECONDS
            except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as e:
                if not self.error:
                    logger.warning(f"mempool.space: {e}")
                self.error = str(e) or type(e).__name__
                wait = RETRY_SECONDS
            await asyncio.sleep(wait)

    async def refresh(self, session: aiohttp.ClientSession) -> None:
        async with asyncio.timeout(15), session.get(MEMPOOL_BLOCKS, headers={"Accept": "application/json"}) as r:
            r.raise_for_status()
            blocks = await r.json()
        self.mempool = summarize(blocks)
        self.error = ""
        self.updated = time.time()

    def _from_miners(self, key: str) -> float | None:
        now = time.time()
        found = [
            m.values.get(key)
            for m in self.miners
            if m.values.get(key) and m.last_seen and now - m.last_seen < FRESH_SECONDS
        ]
        return max(found) if found else None

    def difficulty(self) -> float | None:
        return self._from_miners("network_diff") or self.mempool.get("difficulty")

    def height(self) -> int | None:
        known = [x for x in (self._from_miners("block_height"), self.mempool.get("height")) if x]
        return int(max(known)) if known else None

    def status(self) -> dict:
        height = self.height()
        reward = subsidy(height)
        fees = self.mempool.get("fees")
        return {
            "difficulty": self.difficulty(),
            "difficulty_source": "miners" if self._from_miners("network_diff") else "mempool" if self.mempool else None,
            "height": height,
            "subsidy": reward,
            "fees": fees,
            "prize": None if reward is None else reward + (fees or 0),
            "last_block": self.mempool.get("last_block"),
            "solo_blocks": self.mempool.get("solo_blocks"),
            "enabled": self.enabled,
            "error": self.error,
            "updated": self.updated,
        }


def summarize(blocks: list) -> dict:
    """The newest difficulty and height, and the average fees of the blocks given, in BTC."""
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("no blocks in the answer")
    newest = max(blocks, key=lambda b: b["height"])
    fees = [
        b["extras"]["totalFees"] for b in blocks if isinstance(b.get("extras"), dict) and "totalFees" in b["extras"]
    ]
    solo = []
    for b in blocks:
        pool = (b.get("extras") or {}).get("pool") or {}
        name = str(pool.get("name") or "")
        if "solo" in name.lower() or "ckpool" in name.lower():
            solo.append({"height": b["height"], "pool": name, "ts": b.get("timestamp")})
    return {
        "difficulty": float(newest["difficulty"]),
        "height": int(newest["height"]),
        "fees": round(sum(fees) / len(fees) / 1e8, 8) if fees else None,
        "last_block": newest.get("timestamp"),
        "solo_blocks": solo,
    }
