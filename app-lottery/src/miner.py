"""One miner on the network, read over its AxeOS web API.

Works with the firmware of the Bitaxe family (ESP-Miner, AxeOS), the NerdQAxe fork and the forks other
makers ship, like Lucky Miner's: they all answer /api/system/info. Values a firmware doesn't report are
left out, and so are their sensors.

Besides reading, this keeps what the miner forgets: the work done (hashes), the energy used, every new
best share of a session, and what happened (paused, restarted, a pool lost). Pausing goes the way the
firmware allows: AxeOS pauses and resumes in place; the NerdQAxe firmware can only shut its ASICs down
and needs a restart to mine again.
"""

import asyncio
import contextlib
import json
import logging
import math
import time

import aiohttp

from .config import MinerConfig
from .database import Database
from .odds import format_difficulty, parse_difficulty

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
SAMPLE_SECONDS = 60
SAVE_SECONDS = 60
# A poll later than this many poll intervals is a gap: no work or energy is counted over it
GAP_POLLS = 3


class MinerError(Exception):
    """The miner can't be reached or gave an answer that can't be used."""


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _flag(value) -> bool | None:
    return None if value is None else bool(value)


def _pools(info: dict) -> list[dict]:
    stratum = info.get("stratum")
    pools = stratum.get("pools") if isinstance(stratum, dict) else None
    return [pool for pool in pools if isinstance(pool, dict)] if isinstance(pools, list) else []


def using_fallback(info: dict) -> bool | None:
    if "isUsingFallbackStratum" in info:
        return bool(info["isUsingFallbackStratum"])
    stratum = info.get("stratum")
    if isinstance(stratum, dict) and "usingFallback" in stratum:
        return bool(stratum["usingFallback"])
    return None


def pause_mode(info: dict) -> str | None:
    """How this firmware stops mining: "pause" (and resume), "shutdown" (and restart), or None."""
    if "miningPaused" in info:
        return "pause"
    if "shutdown" in info:
        return "shutdown"
    return None


def chips(info: dict) -> list[dict]:
    """Per ASIC: hash rate (GH/s), errors and temperature, as far as the firmware reports them."""
    temps = [_number(t) for t in info.get("asicTemps") or [] if not isinstance(t, dict)]
    out: list[dict] = []
    monitor = info.get("hashrateMonitor")
    asics = monitor.get("asics") if isinstance(monitor, dict) else None
    if isinstance(asics, list) and asics:
        for asic in asics:
            asic = asic if isinstance(asic, dict) else {}
            out.append({"hashrate": _number(asic.get("total")), "errors": _number(asic.get("errorCount"))})
    elif isinstance(info.get("chips"), list):
        # The NerdQAxe v2 dashboard: valid and error hash rate per chip
        for chip in info["chips"]:
            chip = chip if isinstance(chip, dict) else {}
            valid, error = _number(chip.get("hashRate")), _number(chip.get("errorHashRate"))
            out.append(
                {
                    "hashrate": None if valid is None else round(valid + (error or 0), 2),
                    "error_hashrate": error,
                }
            )
    for i, temp in enumerate(temps):
        if i >= len(out):
            out.append({})
        out[i]["temp"] = temp
    return out


def normalize(info: dict) -> dict:
    """The miner's values under one set of names, whatever its firmware calls them."""
    v: dict = {}

    def put(key: str, value) -> None:
        if value is not None:
            v[key] = value

    n = info.get
    for key, source in (
        ("hashrate", "hashRate"),
        ("hashrate_1m", "hashRate_1m"),
        ("hashrate_10m", "hashRate_10m"),
        ("hashrate_1h", "hashRate_1h"),
        ("hashrate_1d", "hashRate_1d"),
        ("expected_hashrate", "expectedHashrate"),
        ("power", "power"),
        ("max_power", "maxPower"),
        ("voltage", "voltage"),
        ("nominal_voltage", "nominalVoltage"),
        ("core_voltage", "coreVoltage"),
        ("core_voltage_actual", "coreVoltageActual"),
        ("temp", "temp"),
        ("temp2", "temp2"),
        ("vr_temp", "vrTemp"),
        ("vr_temp_int", "vrTempInt"),
        ("target_temp", "temptarget"),
        ("frequency", "frequency"),
        ("core_count", "smallCoreCount"),
        ("asic_count", "asicCount"),
        ("error_rate", "errorPercentage"),
        ("pool_diff", "poolDifficulty"),
        ("block_height", "blockHeight"),
        ("shares_accepted", "sharesAccepted"),
        ("shares_rejected", "sharesRejected"),
        ("total_blocks_found", "totalFoundBlocks"),
        ("fan_speed", "fanspeed"),
        ("fan_rpm", "fanrpm"),
        ("wifi_rssi", "wifiRSSI"),
        ("pool_response", "responseTime"),
        ("pool_ping", "lastpingrtt"),
        ("uptime", "uptimeSeconds"),
        ("free_heap", "freeHeap"),
        ("duplicate_nonces", "duplicateHWNonces"),
    ):
        put(key, _number(n(source)))

    if (current := _number(n("current"))) is not None:
        v["current"] = round(current / 1000, 2)  # reported in mA
    if (loss := _number(n("recentpingloss"))) is not None:
        v["ping_loss"] = round(loss * 100, 1)  # reported as a fraction
    put("blocks_found", _number(n("blockFound", n("foundBlocks"))))
    fans = _number(n("fanCount")) or 0
    if "fan2rpm" in info or fans > 1:
        put("fan2_rpm", _number(n("fan2rpm", n("fanrpm2"))))
    if fans > 1:
        put("fan2_speed", _number(n("fanspeed2")))
    if v.get("power") is not None and (v.get("hashrate") or 0) > 0:
        v["efficiency"] = round(v["power"] / (v["hashrate"] / 1000), 2)

    put("best_diff", parse_difficulty(n("bestDiff")))
    put("best_session_diff", parse_difficulty(n("bestSessionDiff")))
    put("network_diff", parse_difficulty(n("networkDifficulty")))

    if "miningPaused" in info:
        v["mining"] = not bool(info["miningPaused"])
    elif "shutdown" in info:
        v["mining"] = not bool(info["shutdown"])

    fallback = using_fallback(info)
    put("using_fallback", fallback)
    url, port = (n("fallbackStratumURL"), n("fallbackStratumPort")) if fallback else (n("stratumURL"), n("stratumPort"))
    if url:
        v["pool"] = f"{url}:{port}" if port else str(url)
    if user := (n("fallbackStratumUser") if fallback else n("stratumUser")):
        v["pool_user"] = str(user)
    pools = _pools(info)
    if any("connected" in pool for pool in pools):
        v["pool_connected"] = all(pool.get("connected") for pool in pools)
    put("overheat", _flag(n("overheat_mode")))
    put("auto_fan", _flag(n("autofanspeed")))
    put("overclock", _flag(n("overclockEnabled")))

    if n("lastResetReason") is not None:
        v["reset_reason"] = str(n("lastResetReason")).removeprefix("SYSTEM.RESET_")
    elif n("resetReason") is not None:
        v["reset_reason"] = str(n("resetReason"))
    for key, source in (
        ("model", "deviceModel"),
        ("board", "boardVersion"),
        ("asic_model", "ASICModel"),
        ("version", "version"),
        ("hostname", "hostname"),
        ("mac", "macAddr"),
    ):
        if n(source) not in (None, ""):
            v[key] = str(n(source))
    return v


def local_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class Miner:
    """Reads one miner every poll_seconds and keeps its counters."""

    def __init__(self, config: MinerConfig, poll_seconds: int, database: Database, network=None):
        self.config = config
        self.name = config.name
        self.slug = config.slug
        self.poll_seconds = poll_seconds
        self.database = database
        # Gives the network difficulty, for the day's totals
        self.network = network
        self.info: dict = {}
        self.values: dict = {}
        self.connected = False
        self.last_seen: float | None = None
        self.error = ""
        self._dashboard: bool | None = None
        self._wake = asyncio.Event()
        self._last_poll: float | None = None
        self._last_sample = 0.0
        self._last_save = 0.0
        self._load()

    # --- counters kept across restarts of the app ----------------------
    def _load(self) -> None:
        state = self.database.load_state(self.slug)
        self.total_hashes = float(state.get("total_hashes") or 0)
        self.session_hashes = float(state.get("session_hashes") or 0)
        self.energy_kwh = float(state.get("energy_kwh") or 0)
        self.best_ever = state.get("best_ever")
        self.first_seen = state.get("first_seen")
        # What was seen last, to notice changes after the app was away
        self._seen = state.get("seen") or {}
        # The last answer: the sensors are set up from it while the miner is off
        self.snapshot = state.get("snapshot") or {}
        if self.snapshot and not self.values:
            self.values = normalize(self.snapshot)

    def state(self) -> dict:
        return {
            "total_hashes": self.total_hashes,
            "session_hashes": self.session_hashes,
            "energy_kwh": self.energy_kwh,
            "best_ever": self.best_ever,
            "first_seen": self.first_seen,
            "seen": self._seen,
            "snapshot": self.snapshot,
        }

    def save(self) -> None:
        self.database.save_state(self.slug, self.state())

    def reset_counters(self) -> None:
        """After the history was deleted: start counting again from now."""
        self.total_hashes = self.session_hashes = self.energy_kwh = 0.0
        self.best_ever = self.values.get("best_diff")
        self.first_seen = time.time()
        self.save()

    # --- reading -------------------------------------------------------
    async def run(self, session: aiohttp.ClientSession) -> None:
        """Poll until cancelled."""
        try:
            while True:
                await self.poll(session)
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
        finally:
            self.save()

    async def _request(self, session: aiohttp.ClientSession, method: str, path: str) -> tuple[int, str, str]:
        try:
            async with (
                asyncio.timeout(REQUEST_TIMEOUT),
                session.request(method, f"{self.config.url}{path}", allow_redirects=False) as response,
            ):
                return response.status, response.content_type, await response.text(errors="replace")
        except TimeoutError as e:
            raise MinerError(f"no answer from {self.config.host}") from e
        except aiohttp.ClientError as e:
            raise MinerError(f"cannot reach {self.config.host}: {e}") from e

    async def _get_object(self, session, path: str) -> dict:
        status, _, text = await self._request(session, "GET", path)
        if status != 200:
            raise MinerError(f"HTTP {status} from {path}")
        try:
            data = json.loads(text)
        except ValueError as e:
            raise MinerError(f"{path} did not answer with JSON; is this an AxeOS miner?") from e
        if not isinstance(data, dict):
            raise MinerError(f"{path} did not answer with an object")
        return data

    async def _get_dashboard(self, session) -> dict | None:
        """The NerdQAxe v2 dashboard with per-chip hash rates, or None when the firmware has none."""
        status, content_type, text = await self._request(session, "GET", "/api/v2/dashboard")
        # Unknown addresses redirect to the captive portal or serve the web UI
        if 300 <= status < 400 or status == 404 or (status == 200 and content_type != "application/json"):
            return None
        if status != 200:
            raise MinerError(f"HTTP {status} from /api/v2/dashboard")
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def poll(self, session: aiohttp.ClientSession) -> None:
        now = time.time()
        try:
            info = await self._get_object(session, "/api/system/info")
            # Only the NerdQAxe fork reports a deviceModel; its per-chip hash rates are in the v2 dashboard
            if "deviceModel" in info and self._dashboard is not False:
                try:
                    dashboard = await self._get_dashboard(session)
                except MinerError as e:
                    logger.debug(f"{self.name}: dashboard: {e}")
                else:
                    self._dashboard = dashboard is not None
                    if dashboard is not None:
                        performance = dashboard.get("performance")
                        info["chips"] = performance.get("chips", []) if isinstance(performance, dict) else []
        except MinerError as e:
            if self.connected or (self.last_seen is None and not self.error):
                logger.info(f"{self.name}: unreachable, probably switched off: {e}")
                self._event(now, "offline", "warn", "Not answering")
            self.connected = False
            self.error = str(e)
            self._last_poll = None
            return

        was_connected = self.connected
        self.connected = True
        self.error = ""
        self.last_seen = now
        if self.first_seen is None:
            self.first_seen = now
        if not was_connected and self._seen:
            self._event(now, "online", "ok", "Answering again")
        self.info = info
        self.snapshot = info
        values = normalize(info)
        self._account(values, now)
        self.values = values

    def _event(self, ts: float, kind: str, level: str, text: str) -> None:
        self.database.add_event(ts, self.slug, kind, level, text)

    def _account(self, v: dict, now: float) -> None:
        """Count the work and energy since the last poll, and notice what changed."""
        seen = self._seen
        last = self.values if self._last_poll is not None else {}
        dt = now - self._last_poll if self._last_poll is not None else 0.0
        if dt > GAP_POLLS * self.poll_seconds + REQUEST_TIMEOUT:
            dt = 0.0
        self._last_poll = now

        uptime = v.get("uptime")
        restarted = uptime is not None and seen.get("uptime") is not None and uptime + 5 < seen["uptime"]
        if restarted:
            self.session_hashes = 0.0
            self._event(
                now, "restart", "info", f"Restarted ({v['reset_reason']})" if v.get("reset_reason") else "Restarted"
            )

        hashes = kwh = 0.0
        if dt > 0:
            rate = ((last.get("hashrate") or 0) + (v.get("hashrate") or 0)) / 2
            hashes = rate * 1e9 * dt
            if last.get("power") is not None and v.get("power") is not None:
                kwh = (last["power"] + v["power"]) / 2 * dt / 3_600_000
        self.total_hashes += hashes
        self.session_hashes += hashes
        self.energy_kwh += kwh

        # A new best share of this session: one of the tickets that came closest
        best = v.get("best_session_diff")
        new_best = None
        previous = None if restarted else seen.get("best_session_diff")
        if best and (previous is None or best > previous):
            new_best = best
            self.database.add_best(int(now), self.slug, best)
        if (ever := v.get("best_diff")) is not None or new_best is not None:
            top = max(x for x in (ever, new_best) if x is not None)
            if self.best_ever is not None and top > self.best_ever:
                self._event(now, "best", "ok", f"New best share ever: {format_difficulty(top)}")
            self.best_ever = top if self.best_ever is None else max(self.best_ever, top)

        shares = v.get("shares_accepted")
        new_shares = 0
        if shares is not None:
            before = None if restarted else seen.get("shares_accepted")
            if restarted:
                new_shares = int(shares)
            elif before is not None and shares >= before:
                new_shares = int(shares - before)

        blocks = v.get("blocks_found")
        if (
            blocks is not None
            and seen.get("blocks_found") is not None
            and blocks > seen["blocks_found"]
            and not restarted
        ):
            self._event(now, "block", "ok", "BLOCK FOUND! Check your wallet.")

        mining = v.get("mining")
        if mining is not None and seen.get("mining") is not None and mining != seen["mining"]:
            self._event(now, "resumed" if mining else "paused", "info", "Mining again" if mining else "Paused")
        fallback = v.get("using_fallback")
        if fallback is not None and seen.get("using_fallback") is not None and fallback != seen["using_fallback"]:
            self._event(
                now,
                "pool",
                "warn" if fallback else "ok",
                f"On the fallback pool {v.get('pool', '')}".strip()
                if fallback
                else f"Back on {v.get('pool', 'its pool')}",
            )
        connected = v.get("pool_connected")
        if connected is not None and seen.get("pool_connected") is not None and connected != seen["pool_connected"]:
            self._event(now, "pool", "ok" if connected else "warn", "Pool connected" if connected else "Pool lost")
        overheat = v.get("overheat")
        if overheat and not seen.get("overheat"):
            self._event(now, "overheat", "bad", "Overheated: the firmware turned it down")
        version = v.get("version")
        if version and seen.get("version") and version != seen["version"]:
            self._event(now, "firmware", "info", f"Firmware {seen['version']} → {version}")

        network = self.network.difficulty() if self.network else None
        mining_s = dt if dt and mining is not False and (v.get("hashrate") or 0) > 0 else 0.0
        if dt or new_best or new_shares:
            self.database.add_day(
                local_day(now),
                self.slug,
                hashes=hashes,
                kwh=kwh,
                best=new_best,
                shares=max(0, new_shares),
                mining_s=mining_s,
                network=network,
            )

        self._seen = {
            key: v[key]
            for key in (
                "uptime",
                "best_session_diff",
                "shares_accepted",
                "blocks_found",
                "mining",
                "using_fallback",
                "pool_connected",
                "overheat",
                "version",
            )
            if key in v
        }
        if now - self._last_sample >= SAMPLE_SECONDS:
            self._last_sample = now
            sample = dict(v)
            sample["fan"] = v.get("fan_speed")
            sample["mining"] = None if mining is None else int(mining)
            self.database.add_sample(int(now), self.slug, sample)
        if now - self._last_save >= SAVE_SECONDS:
            self._last_save = now
            self.save()

    # --- commands, from the Mining switch and the Restart button -------
    @property
    def pause_mode(self) -> str | None:
        return pause_mode(self.info or self.snapshot)

    async def _post(self, session, path: str) -> None:
        status, _, _ = await self._request(session, "POST", path)
        if status >= 400:
            raise MinerError(f"HTTP {status} from {path}")

    async def set_mining(self, session, on: bool) -> None:
        mode = self.pause_mode
        if mode == "pause":
            await self._post(session, "/api/system/resume" if on else "/api/system/pause")
        elif mode == "shutdown":
            # The NerdQAxe firmware can't resume in place: a restart starts it mining again
            await self._post(session, "/api/system/restart" if on else "/api/system/shutdown")
        else:
            raise MinerError("this firmware can't pause mining")
        logger.info(f"{self.name}: {'resume' if on else 'pause'} sent")
        self._wake.set()

    async def restart(self, session) -> None:
        await self._post(session, "/api/system/restart")
        logger.info(f"{self.name}: restart sent")
        self._wake.set()

    # --- for the pages ---------------------------------------------------
    def summary(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "host": self.config.host,
            "connected": self.connected,
            "last_seen": self.last_seen,
            "error": self.error,
            "values": dict(self.values),
            "chips": chips(self.info or self.snapshot),
            "pause_mode": self.pause_mode,
            "total_hashes": self.total_hashes,
            "session_hashes": self.session_hashes,
            "energy_kwh": round(self.energy_kwh, 4),
            "best_ever": self.best_ever,
            "first_seen": self.first_seen,
        }
