"""The FRITZ!Box poller (TR-064, with fritzconnection in a thread: it is a blocking library).

Every 30 s the line's throughput and state as metrics, every 5 min the box's event log, of
which only the lines not seen before become log lines (source fritzbox, host fritz.box). The
last line seen is kept in the data folder, so a restart does not repeat the log.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Config
from .records import LogEntry, Sample

logger = logging.getLogger(__name__)

INTERVAL = 30
LOG_INTERVAL = 300
HOST = "fritz.box"
FIRST_LOG_HOURS = 1  # the first time, only the last hour of the box's log (it can reach back weeks)


def _event_key(when: datetime, message: str) -> str:
    return hashlib.sha1(f"{when.isoformat()}|{message}".encode(), usedforsecurity=False).hexdigest()


def new_events(
    events: list[tuple[datetime, str]], last: datetime | None, seen: set[str], now: datetime
) -> tuple[list[tuple[datetime, str]], datetime | None, set[str]]:
    """The events after `last` (and those at `last` not in `seen`), and the new (last, seen)."""
    if last is None:
        last = now - timedelta(hours=FIRST_LOG_HOURS)
        seen = set()
    fresh = []
    for when, message in sorted(events):
        key = _event_key(when, message)
        if when > last or (when == last and key not in seen):
            fresh.append((when, message))
    if fresh:
        newest = max(when for when, _ in fresh)
        if newest > last:
            seen = set()
        last = max(last, newest)
        seen = seen | {_event_key(w, m) for w, m in fresh if w == last}
    return fresh, last, seen


class FritzBox:
    def __init__(
        self,
        config: Config,
        data_dir: Path,
        put_samples: Callable[[list[Sample]], None],
        put_log: Callable[[LogEntry], None],
        report: Callable[[int, str | None], None],
    ):
        self.config = config
        self.state_path = data_dir / "fritzbox_log.json"
        self.put_samples = put_samples
        self.put_log = put_log
        self.report = report  # (records, error)
        self._status = None
        self._task: asyncio.Task | None = None
        self._log_at = 0.0
        self._last, self._seen = self._load()

    def start(self) -> None:
        if self.config.fritzbox_host:
            self._task = asyncio.create_task(self._loop(), name="fritzbox")
            logger.info(f"Polling the FRITZ!Box at {self.config.fritzbox_host}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    def _load(self) -> tuple[datetime | None, set[str]]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            return datetime.fromisoformat(state["last"]), set(state.get("seen") or [])
        except (OSError, ValueError, KeyError, TypeError):
            return None, set()

    def _save(self) -> None:
        if self._last is None:
            return
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        try:
            tmp.write_text(json.dumps({"last": self._last.isoformat(), "seen": sorted(self._seen)}), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError as e:
            logger.debug(f"Cannot save the FRITZ!Box log position: {e}")

    def _connect(self):
        from fritzconnection.lib.fritzstatus import FritzStatus

        if self._status is None:
            self._status = FritzStatus(
                address=self.config.fritzbox_host,
                user=self.config.fritzbox_user or None,
                password=self.config.fritzbox_password or None,
                timeout=10,
            )
        return self._status

    def _poll(self) -> list[Sample]:
        status = self._connect()
        now = datetime.now(UTC)
        labels = {"host": HOST}
        samples = []

        def add(name, read, unit=None, kind="gauge"):
            try:
                value = read()
            except Exception as e:  # one value the box doesn't have must not stop the others
                logger.debug(f"FRITZ!Box {name}: {e}")
                return
            if isinstance(value, bool | int | float):
                samples.append(Sample(name, dict(labels), float(value), now, unit, kind))

        rates: tuple = ()
        with contextlib.suppress(Exception):
            rates = status.transmission_rate  # (sent, received) bytes/s
        if rates:
            samples.append(Sample("fritz.wan_bytes_sent_rate", dict(labels), float(rates[0]), now, "B/s", "gauge"))
            samples.append(Sample("fritz.wan_bytes_received_rate", dict(labels), float(rates[1]), now, "B/s", "gauge"))
        add("fritz.wan_link_up", lambda: 1 if status.is_linked and status.is_connected else 0)
        add("fritz.uptime_seconds", lambda: status.device_uptime, "s")
        add("fritz.connection_uptime_seconds", lambda: status.connection_uptime, "s")
        add("fritz.max_bitrate_up", lambda: status.max_bit_rate[0], "bit/s")
        add("fritz.max_bitrate_down", lambda: status.max_bit_rate[1], "bit/s")
        if not samples:
            raise RuntimeError("the FRITZ!Box answered nothing (is TR-064 access switched on?)")
        return samples

    def _read_log(self) -> list[tuple[datetime, str]]:
        status = self._connect()
        events = []
        try:
            for event in status.get_avm_device_log():
                with contextlib.suppress(Exception):
                    events.append((event.datetime.astimezone().astimezone(UTC), str(event.msg)))
        except Exception:
            # Older boxes only have the plain log: "dd.mm.yy hh:mm:ss message" per line
            text = status.fc.call_action("DeviceInfo1", "GetDeviceLog").get("NewDeviceLog", "")
            for line in str(text).splitlines():
                with contextlib.suppress(ValueError):
                    when = datetime.strptime(line[:17], "%d.%m.%y %H:%M:%S").astimezone().astimezone(UTC)
                    events.append((when, line[18:].strip()))
        return events

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                samples = await asyncio.to_thread(self._poll)
                self.put_samples(samples)
                count = len(samples)
                if time.monotonic() - self._log_at >= LOG_INTERVAL:
                    self._log_at = time.monotonic()
                    count += await self._log()
                self.report(count, None)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Drop the connection: the box may have restarted or its address changed
                self._status = None
                message = str(e) or type(e).__name__
                logger.debug(f"FRITZ!Box poll failed: {message}")
                self.report(0, message[:300])
            await asyncio.sleep(max(1.0, INTERVAL - (time.monotonic() - started)))

    async def _log(self) -> int:
        events = await asyncio.to_thread(self._read_log)
        fresh, self._last, self._seen = new_events(events, self._last, self._seen, datetime.now(UTC))
        for when, message in fresh:
            self.put_log(
                LogEntry(
                    ts=when, source="fritzbox", host=HOST, app="fritzbox", severity=6, facility=None, message=message
                )
            )
        if fresh:
            self._save()
        return len(fresh)
