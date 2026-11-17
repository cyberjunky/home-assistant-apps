"""Bitcoin Core, read over its JSON-RPC interface.

Every poll asks for the chain, the mempool, the network, the peers and the fee estimates in one
request. A new block is noticed by its hash; its statistics (getblockstats) are read once and kept.
Nothing here changes the node: only methods that read are called, so an RPC user limited by
rpcwhitelist to those methods is enough.
"""

import asyncio
import base64
import json
import logging
import math
import time
from collections.abc import Callable

import aiohttp

from .config import Config
from .database import BLOCKS_KEPT, Database

logger = logging.getLogger(__name__)

HALVING_INTERVAL = 210_000
RETARGET_INTERVAL = 2016
TARGET_SPACING = 600
MAX_SUPPLY = 21_000_000

# The fee estimates asked for: a number of blocks to wait, and its key on the pages.
# Bitcoin Core has no estimate for one block; two is its shortest
FEE_TARGETS = (("next", 2), ("half_hour", 3), ("hour", 6), ("six_hours", 36), ("day", 144))
# Read in one request on every poll; an RPC user must be allowed all of these
FAST_CALLS = (
    ("getblockchaininfo", []),
    ("getnetworkinfo", []),
    ("getmempoolinfo", []),
    ("getnettotals", []),
    ("uptime", []),
    ("getpeerinfo", []),
    *(("estimatesmartfee", [blocks]) for _, blocks in FEE_TARGETS),
)
BLOCK_STATS = ["height", "blockhash", "time", "txs", "total_size", "total_weight", "totalfee", "subsidy"]
BLOCK_STATS += ["feerate_percentiles", "minfeerate", "maxfeerate"]
# Edges of the fee bands of the mempool histogram, in sat/vB
FEE_BANDS = (0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50, 100, 500)
SLOW_SECONDS = 600
HISTOGRAM_SECONDS = 120
# Reading every transaction of a mempool larger than this takes too long to do every two minutes
HISTOGRAM_MAX_TX = 250_000
# Up to this many new blocks are read at once and announced; more is catching up, done in the background
ANNOUNCE_MAX = 6
TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)
# Polls that may fail in a row before the service counts as not answering
MISSES_ALLOWED = 2


class RpcError(Exception):
    """Bitcoin Core can't be reached, or refused a request."""


def subsidy_at(height: int) -> float:
    """The new coins in the block at this height, in BTC."""
    halvings = height // HALVING_INTERVAL
    return 0.0 if halvings >= 64 else (50 * 100_000_000 >> halvings) / 1e8


def issued_at(height: int) -> float:
    """The coins mined up to and including the block at this height, in BTC."""
    total, left, era = 0, height + 1, 0
    while left > 0 and era < 64:
        blocks = min(left, HALVING_INTERVAL)
        total += blocks * (50 * 100_000_000 >> era)
        left -= blocks
        era += 1
    return total / 1e8


def halving_info(height: int, now: float) -> dict:
    target = (height // HALVING_INTERVAL + 1) * HALVING_INTERVAL
    return {
        "height": target,
        "blocks_left": target - height,
        "at": int(now + (target - height) * TARGET_SPACING),
        "subsidy": subsidy_at(height),
        "next_subsidy": subsidy_at(target),
        "progress": (height % HALVING_INTERVAL) / HALVING_INTERVAL,
    }


def duration_text(seconds: float) -> str:
    """A time span in words: 3 days 4 hours, 5 hours 12 minutes."""
    minutes = int(seconds // 60)
    days, hours = minutes // 1440, minutes % 1440 // 60

    def unit(count: int, name: str) -> str:
        return f"{count} {name}{'' if count == 1 else 's'}"

    if days:
        return f"{unit(days, 'day')} {unit(hours, 'hour')}"
    if hours:
        return f"{unit(hours, 'hour')} {unit(minutes % 60, 'minute')}"
    return unit(minutes, "minute")


def retarget_info(height: int, tip_time: float | None, epoch_time: float | None, now: float) -> dict:
    """Where the difficulty period stands and what the next adjustment will be, going by its blocks so far."""
    done = height % RETARGET_INTERVAL
    left = RETARGET_INTERVAL - done
    average = TARGET_SPACING
    if done >= 6 and tip_time is not None and epoch_time is not None and tip_time > epoch_time:
        average = (tip_time - epoch_time) / done
    # The protocol never changes the difficulty by more than a factor of four
    change = min(3.0, max(-0.75, TARGET_SPACING / average - 1))
    return {
        "blocks_done": done,
        "blocks_left": left,
        "avg_interval": round(average, 1),
        "change": change,
        "at": int(now + left * average),
    }


def version_text(version) -> str:
    """320100 as 32.1.0."""
    try:
        v = int(version)
    except (TypeError, ValueError):
        return ""
    return f"{v // 10000}.{v // 100 % 100}.{v % 100}"


def fee_histogram(entries: dict) -> list[dict]:
    """The virtual size waiting per fee band, in MB, from getrawmempool's verbose answer."""
    totals = [0] * (len(FEE_BANDS) - 1)
    for entry in entries.values():
        vsize = entry.get("vsize") or 0
        if vsize <= 0:
            continue
        rate = (entry.get("fees") or {}).get("base", 0) * 1e8 / vsize
        for index in range(len(totals) - 1, -1, -1):
            if rate >= FEE_BANDS[index]:
                totals[index] += vsize
                break
    return [
        {"from": FEE_BANDS[i], "to": FEE_BANDS[i + 1], "vsize": round(total / 1e6, 3)} for i, total in enumerate(totals)
    ]


def block_from_stats(stats: dict) -> dict:
    """One block as the pages show it. Fees in BTC, fee rates in sat/vB."""
    percentiles = stats.get("feerate_percentiles") or [0, 0, 0, 0, 0]
    return {
        "height": stats["height"],
        "hash": stats.get("blockhash", ""),
        "time": stats.get("time", 0),
        "txs": stats.get("txs", 0),
        "size": stats.get("total_size", 0),
        "weight": stats.get("total_weight", 0),
        "fee_total": (stats.get("totalfee") or 0) / 1e8,
        "fee_median": percentiles[2],
        "fee_min": stats.get("minfeerate", 0),
        "fee_max": stats.get("maxfeerate", 0),
        "subsidy": (stats.get("subsidy") or 0) / 1e8,
    }


def peer_view(peer: dict) -> dict:
    return {
        "id": peer.get("id"),
        "addr": peer.get("addr", ""),
        "network": peer.get("network", ""),
        "inbound": bool(peer.get("inbound")),
        "type": peer.get("connection_type") or ("inbound" if peer.get("inbound") else "outbound-full-relay"),
        "subver": peer.get("subver", ""),
        "v2": peer.get("transport_protocol_type") == "v2",
        "ping": peer.get("pingtime"),
        "sent": peer.get("bytessent", 0),
        "recv": peer.get("bytesrecv", 0),
        "since": peer.get("conntime", 0),
    }


class BitcoinClient:
    """Bitcoin Core's state, kept current by run()."""

    def __init__(self, config: Config, database: Database, emit: Callable[..., None]):
        self.config = config
        self.database = database
        self.emit = emit
        self.configured = bool(config.bitcoin_url)
        self.connected = False
        self.error = ""
        self.updated = 0.0
        self.chain: dict = {}
        self.network: dict = {}
        self.mempool: dict = {}
        self.totals: dict = {}
        self.uptime: int | None = None
        self.peers: list[dict] = []
        self.fees: dict[str, float | None] = {}
        self.blocks: list[dict] = database.blocks()  # newest first
        self.histogram: list[dict] = []
        self.hashrate: float | None = None
        self.tx_stats: dict = {}
        self.txindex: bool | None = None
        self.recv_rate: float | None = None
        self.sent_rate: float | None = None
        self._session: aiohttp.ClientSession | None = None
        self._tip_hash: str | None = None
        self._epoch: tuple[int, float] | None = None
        self._slow_at = 0.0
        self._histogram_at = 0.0
        self._backfill: asyncio.Task | None = None
        self._ever_connected = False
        self._misses = 0
        # Bitcoin Core does not tell when the sync began: the app remembers when it first saw it
        self.sync_started: float | None = database.state("sync_started")

    # ------------------------------------------------------------------ RPC
    async def _post(self, payload) -> tuple[int, object]:
        try:
            async with self._session.post(
                self.config.bitcoin_url + "/", data=json.dumps(payload), headers={"Content-Type": "application/json"}
            ) as response:
                status, body = response.status, await response.read()
        except (aiohttp.ClientError, TimeoutError) as e:
            raise RpcError(f"no answer from {self.config.bitcoin_url}: {str(e) or type(e).__name__}") from e
        if status == 401:
            raise RpcError("the RPC username or password is wrong")
        try:
            # A whole mempool is tens of megabytes: parse that off the event loop
            answer = await asyncio.to_thread(json.loads, body) if len(body) > 500_000 else json.loads(body)
        except ValueError:
            answer = None
        return status, answer

    async def call(self, method: str, *params):
        """One method's result. Raises RpcError when the node refuses it."""
        status, answer = await self._post({"jsonrpc": "1.0", "id": method, "method": method, "params": list(params)})
        if status == 403:
            raise RpcError(f"{method} is not allowed for this RPC user (add it to rpcwhitelist)")
        if not isinstance(answer, dict):
            raise RpcError(f"{method}: unexpected answer (HTTP {status})")
        if answer.get("error"):
            raise RpcError(f"{method}: {answer['error'].get('message', answer['error'])}")
        return answer.get("result")

    async def batch(self, calls) -> list:
        """The results of several methods from one request, in their order. A refused one gives None."""
        payload = [{"jsonrpc": "1.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        status, answer = await self._post(payload)
        if status == 403:
            names = ", ".join(sorted({m for m, _ in calls}))
            raise RpcError(f"the RPC user is not allowed every method the app reads; rpcwhitelist needs: {names}")
        if isinstance(answer, dict) and answer.get("error"):
            raise RpcError(str(answer["error"].get("message", answer["error"])))
        if not isinstance(answer, list):
            raise RpcError(f"unexpected answer (HTTP {status})")
        results: list = [None] * len(calls)
        for item in answer:
            if isinstance(item, dict) and isinstance(item.get("id"), int) and not item.get("error"):
                results[item["id"]] = item.get("result")
        return results

    async def _try(self, method: str, *params):
        """An extra the pages can do without: None when the node refuses it."""
        try:
            return await self.call(method, *params)
        except RpcError as e:
            logger.debug(f"Bitcoin Core: {e}")
            return None

    def auth_headers(self) -> dict:
        """The RPC user and password as HTTP Basic authentication."""
        login = f"{self.config.bitcoin_user}:{self.config.bitcoin_password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(login).decode()}

    # ------------------------------------------------------------------ polling
    async def run(self) -> None:
        if not self.configured:
            return
        async with aiohttp.ClientSession(headers=self.auth_headers(), timeout=TIMEOUT) as session:
            self._session = session
            try:
                while True:
                    try:
                        await self.poll()
                        self._set_link(True, "")
                    except RpcError as e:
                        self._set_link(False, str(e))
                    except Exception as e:
                        logger.exception("Bitcoin Core: reading failed")
                        self._set_link(False, repr(e))
                    await asyncio.sleep(self.config.poll_seconds)
            finally:
                if self._backfill:
                    self._backfill.cancel()

    def _set_link(self, connected: bool, error: str) -> None:
        if connected:
            self._misses = 0
        elif self.connected:
            # A busy node answers late now and then: one missed poll is not an outage.
            # What was read last stays on the pages until MISSES_ALLOWED polls in a row failed
            self._misses += 1
            if self._misses <= MISSES_ALLOWED:
                logger.debug(f"Bitcoin Core: missed a poll ({self._misses}): {error}")
                return
        if connected and not self.connected:
            logger.info(f"Bitcoin Core {version_text(self.network.get('version'))} answers, block {self.height}")
            if self._ever_connected:
                self.emit("bitcoin", "node", "ok", "Bitcoin Core answers again")
            self._ever_connected = True
        elif not connected and (self.connected or error != self.error):
            logger.warning(f"Bitcoin Core: {error}")
            if self.connected:
                self.emit("bitcoin", "node", "bad", f"Bitcoin Core does not answer: {error}")
        self.connected, self.error = connected, error
        if not connected:
            self.recv_rate = self.sent_rate = None

    async def poll(self) -> None:
        results = await self.batch(FAST_CALLS)
        chain, network, mempool, totals, uptime, peers = results[:6]
        if not isinstance(chain, dict) or not isinstance(network, dict):
            raise RpcError("Bitcoin Core is starting up")
        was_syncing = self.chain.get("initialblockdownload")
        self.chain, self.network, self.mempool = chain, network, mempool or {}
        self.uptime = uptime
        self.peers = [peer_view(p) for p in peers or []]
        floor = (self.mempool.get("mempoolminfee") or 0) * 1e5
        for (key, _), estimate in zip(FEE_TARGETS, results[6:], strict=True):
            rate = (estimate or {}).get("feerate")
            # Without enough blocks seen there is no estimate: the lowest fee the node accepts is all it knows
            self.fees[key] = round(max(rate * 1e5, floor), 2) if rate else (round(floor, 2) if floor else None)
        self._rates(totals or {})
        self.updated = time.time()
        await self._track_sync(chain, was_syncing)

        new_block = chain.get("bestblockhash") != self._tip_hash
        if new_block and not chain.get("initialblockdownload"):
            await self._sync_blocks(chain["blocks"], announce=self._tip_hash is not None)
        self._tip_hash = chain.get("bestblockhash")
        now = time.time()
        if new_block or now - self._slow_at > SLOW_SECONDS:
            self._slow_at = now
            await self._slow(chain)
        if now - self._histogram_at > HISTOGRAM_SECONDS:
            self._histogram_at = now
            await self._read_histogram()

    async def _track_sync(self, chain: dict, was_syncing: bool | None) -> None:
        """Remember when the sync began, and say how long it took once it is done."""
        if chain.get("initialblockdownload"):
            if self.sync_started is None:
                # A node that was already syncing when the app first saw it began at its own start at the latest
                self.sync_started = self.updated - (self.uptime or 0)
                await asyncio.to_thread(self.database.set_state, "sync_started", self.sync_started)
            return
        if was_syncing:
            took = f", after {duration_text(self.updated - self.sync_started)}" if self.sync_started else ""
            self.emit("bitcoin", "synced", "ok", f"The chain is in sync at block {chain.get('blocks'):,}{took}")
        if self.sync_started is not None:
            self.sync_started = None
            await asyncio.to_thread(self.database.set_state, "sync_started", None)

    def _rates(self, totals: dict) -> None:
        """Traffic in kB/s, from the growth of the node's counters since the last poll."""
        previous, self.totals = self.totals, totals
        try:
            seconds = (totals["timemillis"] - previous["timemillis"]) / 1000
            recv = totals["totalbytesrecv"] - previous["totalbytesrecv"]
            sent = totals["totalbytessent"] - previous["totalbytessent"]
        except KeyError:
            return
        # The counters start again when the node restarts
        if seconds > 0 and recv >= 0 and sent >= 0:
            self.recv_rate, self.sent_rate = round(recv / seconds / 1000, 2), round(sent / seconds / 1000, 2)

    async def _slow(self, chain: dict) -> None:
        mining = await self._try("getmininginfo")
        if mining:
            self.hashrate = mining.get("networkhashps")
        self.tx_stats = await self._try("getchaintxstats") or self.tx_stats
        index = await self._try("getindexinfo")
        if index is not None:
            self.txindex = bool((index.get("txindex") or {}).get("synced"))
        start = chain["blocks"] // RETARGET_INTERVAL * RETARGET_INTERVAL
        if not self._epoch or self._epoch[0] != start:
            block_hash = await self._try("getblockhash", start)
            header = await self._try("getblockheader", block_hash) if block_hash else None
            if header and header.get("time"):
                self._epoch = (start, header["time"])

    async def _read_histogram(self) -> None:
        if (self.mempool.get("size") or 0) > HISTOGRAM_MAX_TX:
            self.histogram = []
            return
        entries = await self._try("getrawmempool", True)
        if isinstance(entries, dict):
            self.histogram = await asyncio.to_thread(fee_histogram, entries)

    # ------------------------------------------------------------------ blocks
    async def _read_block(self, height: int) -> dict | None:
        stats = await self._try("getblockstats", height, BLOCK_STATS)
        if not stats:
            return None
        block = block_from_stats(stats)
        await asyncio.to_thread(self.database.save_block, block)
        self.blocks = sorted([b for b in self.blocks if b["height"] != height] + [block], key=lambda b: -b["height"])[
            : BLOCKS_KEPT + 1
        ]
        return block

    async def _sync_blocks(self, tip: int, announce: bool) -> None:
        if self.blocks and self.blocks[0]["height"] >= tip:
            # The tip changed without growing: a reorganisation. Read the last blocks again
            await asyncio.to_thread(self.database.forget_blocks_from, tip - 2)
            self.blocks = [b for b in self.blocks if b["height"] < tip - 2]
        known = {b["height"] for b in self.blocks}
        missing = [h for h in range(max(0, tip - BLOCKS_KEPT), tip + 1) if h not in known]
        if len(missing) > ANNOUNCE_MAX:
            await self._read_block(tip)
            if not self._backfill or self._backfill.done():
                self._backfill = asyncio.create_task(self._read_older(missing[:-1][::-1]), name="blocks")
            return
        for height in missing:
            block = await self._read_block(height)
            if block and announce:
                self.emit(
                    "bitcoin",
                    "block",
                    "",
                    f"Block {block['height']:,} · {block['txs']:,} transactions · median {block['fee_median']} sat/vB",
                    height=block["height"],
                    transactions=block["txs"],
                    median_fee=block["fee_median"],
                    fees_btc=round(block["fee_total"], 8),
                )

    async def _read_older(self, heights: list[int]) -> None:
        """The day of blocks before the tip, newest first, one at a time so the node stays free for the polls."""
        logger.info(f"Bitcoin Core: reading the statistics of {len(heights)} earlier blocks")
        for height in heights:
            try:
                if await self._read_block(height) is None:
                    break  # a pruned node no longer has it, and then not the ones before either
            except RpcError:
                break
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ what the pages get
    @property
    def height(self) -> int | None:
        return self.chain.get("blocks")

    @property
    def synced(self) -> bool:
        return bool(self.chain) and not self.chain.get("initialblockdownload")

    def onion_address(self) -> dict | None:
        for address in self.network.get("localaddresses") or []:
            if str(address.get("address", "")).endswith(".onion"):
                return address
        return None

    def blocks_view(self) -> list[dict]:
        """The blocks newest first, each with the time since the block before it."""
        out = []
        # No blocks are read while the chain syncs: the list is empty then
        for block, previous in zip(self.blocks, [*self.blocks[1:], None], strict=False):
            follows = previous is not None and previous["height"] == block["height"] - 1
            out.append({**block, "interval": max(0, block["time"] - previous["time"]) if follows else None})
        return out[:BLOCKS_KEPT]

    def node_status(self, now: float) -> dict:
        chain, height = self.chain, self.height
        if height is None:
            return {"configured": self.configured, "connected": False, "error": self.error}
        tip_time = self.blocks[0]["time"] if self.blocks and self.blocks[0]["height"] == height else chain.get("time")
        epoch_time = (
            self._epoch[1]
            if self._epoch and self._epoch[0] == height // RETARGET_INTERVAL * RETARGET_INTERVAL
            else None
        )
        issued = issued_at(height)
        return {
            "configured": True,
            "connected": self.connected,
            "error": self.error,
            "version": version_text(self.network.get("version")),
            "subversion": self.network.get("subversion", ""),
            "chain": chain.get("chain", ""),
            "blocks": height,
            "headers": chain.get("headers"),
            "best_hash": chain.get("bestblockhash", ""),
            "synced": self.synced,
            "progress": chain.get("verificationprogress"),
            "ibd": bool(chain.get("initialblockdownload")),
            "sync_started": self.sync_started,
            "difficulty": chain.get("difficulty"),
            "hashrate": self.hashrate,
            "size_on_disk": chain.get("size_on_disk"),
            "growth": self.database.growth(now=now),
            "pruned": bool(chain.get("pruned")),
            "txindex": self.txindex,
            "uptime": self.uptime,
            "last_block_time": tip_time,
            "tx_total": self.tx_stats.get("txcount"),
            "tx_rate": self.tx_stats.get("txrate"),
            "retarget": retarget_info(height, tip_time, epoch_time, now),
            "halving": halving_info(height, now),
            "supply": {"issued": issued, "max": MAX_SUPPLY, "ratio": issued / MAX_SUPPLY},
        }

    def mempool_status(self) -> dict:
        mempool = self.mempool
        vbytes = mempool.get("bytes") or 0
        return {
            "tx": mempool.get("size"),
            "vsize": round(vbytes / 1e6, 2),
            "usage": mempool.get("usage"),
            "max": mempool.get("maxmempool"),
            "min_fee": round((mempool.get("mempoolminfee") or 0) * 1e5, 3),
            "total_fee": mempool.get("total_fee"),
            # A block holds a little under a million virtual bytes
            "blocks_to_clear": math.ceil(vbytes / 997_500) if vbytes else 0,
            "unbroadcast": mempool.get("unbroadcastcount", 0),
            "fees": dict(self.fees),
            "histogram": self.histogram,
        }

    def net_status(self) -> dict:
        pings = sorted(p["ping"] for p in self.peers if p["ping"] is not None)
        kinds: dict[str, int] = {}
        networks: dict[str, int] = {}
        for peer in self.peers:
            kinds[peer["type"]] = kinds.get(peer["type"], 0) + 1
            networks[peer["network"]] = networks.get(peer["network"], 0) + 1
        return {
            "peers": len(self.peers),
            "in": sum(1 for p in self.peers if p["inbound"]),
            "out": sum(1 for p in self.peers if not p["inbound"]),
            "kinds": kinds,
            "peer_networks": networks,
            "networks": [
                {
                    "name": n.get("name", ""),
                    "reachable": bool(n.get("reachable")),
                    "limited": bool(n.get("limited")),
                    "proxy": n.get("proxy", ""),
                    "peers": networks.get(n.get("name", ""), 0),
                }
                for n in self.network.get("networks") or []
            ],
            "local_addresses": self.network.get("localaddresses") or [],
            "relay_fee": round((self.network.get("relayfee") or 0) * 1e5, 3),
            "recv_total": self.totals.get("totalbytesrecv"),
            "sent_total": self.totals.get("totalbytessent"),
            "recv_rate": self.recv_rate,
            "sent_rate": self.sent_rate,
            "median_ping": pings[len(pings) // 2] if pings else None,
        }
