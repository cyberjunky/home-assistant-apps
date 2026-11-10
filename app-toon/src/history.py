"""Reads the history the Toon keeps itself.

The Toon logs meters and boiler values in its own round-robin files, up to 10 years back, and it
keeps doing so while the network is down. So this is used twice: once for all history, and after
every outage to fill the gap.

It comes straight from hcb_rrd over the bus. The Toon has little memory to spare, so all of it goes
in small pieces with a pause in between, and an interrupted import continues where it was.
"""

import asyncio
import itertools
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .boxtalk import BoxTalkClient
from .database import Database
from .metrics import METRIC_BY_KEY, convert

logger = logging.getLogger(__name__)

DAY = 86400
PAUSE_BETWEEN_REQUESTS = 2.0
# A gap in the stored values. While connected every value is stored at least every 15 minutes
# (the monitor's heartbeat), so a longer stretch without one means the app didn't record it.
MIN_GAP = 18 * 60


@dataclass(frozen=True)
class Archive:
    """One of the Toon's history files."""

    logger: str
    rra: str
    metric: str
    # How far back the file goes, how long its bins are and how much to ask for at once
    span: int
    bin_seconds: int
    chunk: int


def _archives() -> tuple[Archive, ...]:
    energy = (
        ("elec_quantity_lt_orig", "energy_import_low"),
        ("elec_quantity_nt_orig", "energy_import_normal"),
        ("elec_quantity_lt_produ", "energy_export_low"),
        ("elec_quantity_nt_produ", "energy_export_normal"),
        ("elec_quantity_lt", "energy_house_low"),
        ("elec_quantity_nt", "energy_house_normal"),
        ("gas_quantity", "gas_total"),
        ("boiler_burner_minutes", "burner_minutes"),
    )
    boiler = (
        ("thermstat_realTemps", "room_temperature"),
        ("thermstat_setpoint", "setpoint"),
        ("thermstat_boilerSetpoint", "boiler_setpoint"),
        ("thermstat_boilerChPressure", "boiler_pressure"),
        ("thermstat_programState", "program_state"),
        ("thermstat_boilerOpMode", "boiler_op_mode"),
    )
    archives = []
    for name, metric in energy:
        archives.append(Archive(name, "10yrdays", metric, 3653 * DAY, DAY, 365 * DAY))
        archives.append(Archive(name, "5yrhours", metric, 1826 * DAY, 3600, 30 * DAY))
    for name, metric in boiler:
        archives.append(Archive(name, "30days", metric, 30 * DAY, 60, DAY))
    archives.append(Archive("elec_flow", "5min", "power_import", 31 * DAY, 300, 3 * DAY))
    archives.append(Archive("elec_produ_flow", "5min", "power_export", 31 * DAY, 300, 3 * DAY))
    # The boiler's real hour counter: 30 daily values is all the Toon keeps of it
    archives.append(Archive("thermstat_boilerBurnerHours", "30days", "burner_hours", 30 * DAY, DAY, 30 * DAY))
    # The gas meter every 5 minutes, for a week. The Toon's own gas_flow history is empty, so this is
    # what shows the flow on days before the app logged it.
    archives.append(Archive("gas_delivered", "5minweek", "gas_delivered", 7 * DAY, 300, DAY))
    return tuple(archives)


ARCHIVES = _archives()
# Archives that fill the gap after an outage: the finest ones
BACKFILL = tuple(archive for archive in ARCHIVES if archive.bin_seconds <= 300)


def find_gaps(times: list[int], start: int, end: int, min_gap: int = MIN_GAP) -> list[tuple[int, int]]:
    """The stretches between start and end without a sample that are longer than min_gap."""
    points = [start, *(t for t in times if start < t < end), end]
    return [(a, b) for a, b in itertools.pairwise(points) if b - a > min_gap]


def rrd_arguments(archive: Archive, start: int, end: int, zone: ZoneInfo) -> dict[str, str]:
    """The request for a time range. The Toon takes local time and answers with epoch timestamps.

    A request without a range returns the whole file, so there always is one.
    """

    def text(ts: int) -> str:
        return datetime.fromtimestamp(ts, zone).strftime("%d-%m-%Y %H:%M:%S")

    return {
        "loggerName": archive.logger,
        "rra": archive.rra,
        "from": text(start),
        "to": text(end),
        "nullForNaN": "1",
    }


def parse_rows(archive: Archive, answer: dict, shift: float = 0) -> list[tuple[str, int, float]]:
    """Rows to store from an answer. The Toon returns {"<epoch>": value} or an error object."""
    metric = METRIC_BY_KEY[archive.metric]
    rows = []
    for key, raw in answer.items():
        if key.isdigit() and raw is not None:
            value = convert(metric, raw)
            if value is not None:
                rows.append((archive.metric, int(key) + int(shift), value))
    return rows


class HistoryImporter:
    """Copies history from a Toon session into the database."""

    def __init__(self, database: Database, timezone: str = "Europe/Amsterdam"):
        self.database = database
        self.zone = ZoneInfo(timezone)
        self.running = False

    def _store(self, archive: Archive, rows: list[tuple[str, int, float]]) -> int:
        """Store rows and return how many. The running bin of a file is stamped with its end, so skip what's ahead."""
        now = time.time()
        rows = [row for row in rows if row[1] <= now]
        if not rows:
            return 0
        if archive.bin_seconds >= 3600:
            self.database.add_hourly(rows)
        else:
            self.database.add_samples(rows)
        return len(rows)

    async def _fetch(
        self,
        bus: BoxTalkClient,
        host: str,
        archive: Archive,
        start: int,
        end: int,
        shift: float = 0,
        keep: tuple[int, int] | None = None,
    ) -> int:
        answer = await bus.action(
            f"{host}:hcb_rrd", "specific1", "GetRraData", rrd_arguments(archive, start, end, self.zone), timeout=30
        )
        try:
            data = json.loads(answer.get("rraData") or "{}")
        except ValueError:
            logger.warning(f"History: unreadable answer for {archive.logger}/{archive.rra}")
            return 0
        rows = parse_rows(archive, data if isinstance(data, dict) else {}, shift)
        if keep:
            rows = [row for row in rows if keep[0] < row[1] < keep[1]]
        return self._store(archive, rows)

    async def import_all(self, bus: BoxTalkClient, host: str) -> None:
        """Import everything not imported yet, continuing an interrupted import."""
        self.running = True
        total = 0
        try:
            for archive in ARCHIVES:
                position, done = self.database.import_position(archive.logger, archive.rra)
                if done:
                    continue
                now = int(time.time())
                start = position if position is not None else now - archive.span
                while start < now:
                    end = min(start + archive.chunk, now)
                    total += await self._fetch(bus, host, archive, start, end)
                    self.database.set_import_position(archive.logger, archive.rra, end, end >= now)
                    start = end
                    await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)
            if total:
                logger.info(f"History import finished, {total} values added")
        finally:
            self.running = False

    async def fill_gap(self, bus: BoxTalkClient, host: str, start: float, end: float, clock_offset: float) -> None:
        """Copy what the Toon logged during an outage.

        The Toon stamps its history with its own clock, so the known offset is taken out here,
        where it matters: these values land right next to live ones.
        """
        rows = 0
        for archive in BACKFILL:
            rows += await self._fetch(
                bus,
                host,
                archive,
                int(start + clock_offset),
                int(end + clock_offset),
                shift=-clock_offset,
            )
            await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)
        self.database.roll_up(int(start))
        logger.info(f"Filled the gap of the outage with {rows} values from the Toon's own history")

    async def fill_missing(
        self, bus: BoxTalkClient, host: str, clock_offset: Callable[[int], float], now: int | None = None
    ) -> int:
        """Fill every gap in the stored values that the Toon's finest files still cover (about 30 days).

        Unlike fill_gap this doesn't rely on the outage record, which an app restart in the middle of
        an outage cuts short. Live values stay as they are: only the gaps get the Toon's values.
        """
        now = int(now or time.time())
        self.running = True
        values = 0
        earliest = None
        try:
            for archive in BACKFILL:
                # Stay a bin inside the oldest end of the file
                start = now - archive.span + archive.bin_seconds
                for a, b in find_gaps(self.database.sample_times(archive.metric, start, now), start, now):
                    at = a
                    while at < b:
                        to = min(at + archive.chunk, b)
                        offset = clock_offset(at)
                        added = await self._fetch(
                            bus, host, archive, int(at + offset), int(to + offset), shift=-offset, keep=(a, b)
                        )
                        if added:
                            values += added
                            earliest = a if earliest is None else min(earliest, a)
                        at = to
                        await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)
            if earliest is not None:
                self.database.roll_up(earliest)
        finally:
            self.running = False
        if values:
            logger.info(f"Filled gaps with {values} values from the Toon's own history")
        return values
