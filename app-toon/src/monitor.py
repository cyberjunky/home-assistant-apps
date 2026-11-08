"""Keeps the connection to the Toon and turns everything it reports into stored, current values."""

import asyncio
import contextlib
import logging
import sqlite3
import time
from collections.abc import Callable

import asyncssh

from .boxtalk import BoxTalkClient, BoxTalkError, Device
from .config import Config
from .database import Database
from .history import HistoryImporter
from .metrics import BINDINGS, BURNER_STATES, METRIC_BY_KEY, Binding, convert, device_matches
from .toon_link import CredentialsRejected, HostKeyChanged, ToonLink, ToonSession

logger = logging.getLogger(__name__)

RECONNECT_MIN = 5
RECONNECT_MAX = 60
# Credentials or a changed key won't fix themselves; don't keep knocking
RECONNECT_REFUSED = 300
DISCOVERY_WAIT = 3
# The meters report about every 10 seconds; this long without anything means the bus has gone quiet
BUS_SILENCE_LIMIT = 300
FAST_POLL = 10
SLOW_POLL = 300
# An unchanged value is stored again after this long, so a chart can tell "flat" from "no data"
HEARTBEAT = 900
FLUSH_INTERVAL = 30
ROLLUP_INTERVAL = 3600
# Outages this short have nothing worth reading back
BACKFILL_MIN_OUTAGE = 120
# Margin when deciding from the Toon's uptime whether it restarted during an outage
REBOOT_MARGIN = 180

# What the bus can't tell. The web server's and the bus's use of resources are watched because more
# web requests seem to bring the Toon's hangs closer, and this would show what they leak.
SYSTEM_COMMAND = (
    "cat /proc/uptime /proc/loadavg; grep -E '^(MemFree|Cached):' /proc/meminfo; "
    'echo "Now: $(date +%s)"; '
    "grep VmRSS /proc/$(pidof qt-gui)/status; "
    'echo "Web: $(grep VmRSS /proc/$(pidof lighttpd)/status)"; '
    "P=$(ps | awk '/[{]hcb_comm[}]/ {print $1; exit}'); "
    'echo "BusThreads: $(grep Threads /proc/$P/status)"; '
    'echo "BusFds: $(ls /proc/$P/fd | wc -l)"; '
    'echo "Wifi: $(grep wlan0 /proc/net/wireless)"; '
    "grep wlan0 /proc/net/dev"
)


def classify_outage(duration: float, toon_uptime: float | None) -> str:
    """What happened in an outage: the Toon restarted, or only the connection was gone."""
    if toon_uptime is None:
        return "unknown"
    return "reboot" if toon_uptime < duration + REBOOT_MARGIN else "network"


def parse_system(output: str) -> dict[str, float]:
    """Values from SYSTEM_COMMAND's output, in the units of the metrics."""
    values: dict[str, float] = {}
    lines = output.splitlines()
    if lines and (first := lines[0].split()):
        with contextlib.suppress(ValueError):
            values["uptime_seconds"] = float(first[0])
            values["uptime"] = round(float(first[0]) / 3600, 2)
    if len(lines) > 1 and (second := lines[1].split()):
        with contextlib.suppress(ValueError):
            values["load"] = float(second[0])
    for line in lines[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if not fields:
            continue
        name = name.strip()
        with contextlib.suppress(ValueError, IndexError):
            if name == "MemFree":
                values["memory_free"] = round(int(fields[0]) / 1024, 1)
            elif name == "Cached":
                values["memory_cached"] = round(int(fields[0]) / 1024, 1)
            elif name == "Now":
                values["toon_time"] = int(fields[0])
            elif name == "VmRSS":
                values["memory_gui"] = round(int(fields[0]) / 1024, 1)
            elif name == "Web":
                values["memory_web"] = round(int(fields[1]) / 1024, 1)
            elif name == "BusThreads":
                values["bus_threads"] = int(fields[1])
            elif name == "BusFds":
                values["bus_fds"] = int(fields[0])
            elif name == "Wifi":
                values["wifi_signal"] = float(fields[3].rstrip("."))
            elif name == "wlan0":
                values["wifi_rx_dropped"] = int(fields[3])
    return values


class ToonMonitor:
    """Runs one session after the other and holds the Toon's current state."""

    def __init__(self, config: Config, database: Database, link: ToonLink):
        self.config = config
        self.database = database
        self.link = link
        self.importer = HistoryImporter(database)
        self.connected = False
        self.last_error = ""
        self.connected_since: float | None = None
        self.host_name = ""
        self.info: dict[str, str] = {}
        self.adapters: dict[str, dict[str, str]] = {}
        # What comes next in the week program, from the thermostat's dataset
        self.schedule: dict[str, str] = {}
        self.values: dict[str, float | str] = {}
        self.updated: dict[str, float] = {}
        self.listeners: list[Callable[[], None]] = []
        self._stored: dict[str, tuple[float, float]] = {}
        self._dispatch: dict[tuple[str, str], str] = {}
        self._subscribed: set[tuple[str, str]] = set()
        self._bus: BoxTalkClient | None = None
        # An outage the app itself slept through can't be classified or dated
        if database.current_outage():
            database.close_outage(time.time(), "app_stopped")

    # Current values

    def set_value(self, key: str, raw, now: float | None = None) -> None:
        """Take in a reading of a metric; a change is stored and announced."""
        metric = METRIC_BY_KEY.get(key)
        if metric is None:
            return
        value = convert(metric, raw)
        if value is None:
            return
        now = now or time.time()
        changed = self.values.get(key) != value
        self.values[key] = value
        self.updated[key] = now
        if not metric.text:
            stored = self._stored.get(key)
            if changed or stored is None or now - stored[1] >= HEARTBEAT:
                self.database.record(key, now, float(value))
                self._stored[key] = (float(value), now)
        if changed:
            self._announce()

    def _announce(self) -> None:
        for listener in self.listeners:
            listener()

    # The connection

    async def run(self) -> None:
        """Connect, and connect again after whatever goes wrong, until cancelled."""
        delay = RECONNECT_MIN
        while True:
            reason = ""
            try:
                session = await self.link.connect()
            except CredentialsRejected:
                self._down("the Toon refused the SSH username or password")
                await asyncio.sleep(RECONNECT_REFUSED)
                continue
            except HostKeyChanged:
                self._down("the Toon's SSH key changed: if you expect that, turn on ssh.trust_new_host_key and restart")
                await asyncio.sleep(RECONNECT_REFUSED)
                continue
            except (OSError, TimeoutError, asyncssh.Error) as e:
                self._down(_describe(e))
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)
                continue
            except Exception as e:
                logger.exception("Unexpected error while connecting to the Toon")
                self._down(_describe(e))
                await asyncio.sleep(RECONNECT_MAX)
                continue

            delay = RECONNECT_MIN
            try:
                await self._serve(session)
                reason = "the session ended"
            except (OSError, TimeoutError, asyncssh.Error, BoxTalkError) as e:
                reason = _describe(e)
            except Exception as e:
                # A bug must not end the logging: the finish script stops the app on a failed exit
                logger.exception("Unexpected error during the session with the Toon")
                reason = _describe(e)
            finally:
                session.close()
            self._down(reason)
            await asyncio.sleep(RECONNECT_MIN)

    def _down(self, reason: str) -> None:
        if self.connected:
            logger.warning(f"Lost the connection to the Toon: {reason}")
        elif reason != self.last_error:
            logger.warning(f"Cannot connect to the Toon: {reason}")
        self.database.open_outage(
            time.time(),
            reason,
            self.values.get("memory_free"),
            self.values.get("memory_gui"),
        )
        self.connected = False
        self.connected_since = None
        self.last_error = reason
        self._bus = None
        self._subscribed.clear()
        self._announce()

    def _take_system(self, system: dict[str, float]) -> None:
        for key, value in system.items():
            self.set_value(key, value)
        if "toon_time" in system:
            self.set_value("clock_offset", round(system["toon_time"] - time.time()))

    async def _up(self, session: ToonSession) -> float | None:
        """Mark the connection as up and settle the outage it ends. Returns when that outage started."""
        output = await session.run(SYSTEM_COMMAND)
        system = parse_system(output)
        self._take_system(system)
        outage = self.database.current_outage()
        started = None
        if outage:
            started = outage["start_ts"]
            duration = time.time() - started
            kind = classify_outage(duration, system.get("uptime_seconds"))
            self.database.close_outage(time.time(), kind, system.get("uptime_seconds"))
            logger.info(f"Connected to the Toon again after {round(duration)} seconds ({kind})")
        else:
            logger.info("Connected to the Toon")
        self.connected = True
        self.connected_since = time.time()
        self.last_error = ""
        self._announce()
        return started

    async def _serve(self, session: ToonSession) -> None:
        """One session: runs until something ends it."""
        reader, writer = await session.open_bus()
        bus = BoxTalkClient(reader, writer, self._on_notify, self._on_discovery)
        self._bus = bus
        reader_task = asyncio.create_task(bus.read_forever(), name="bus-reader")
        try:
            bus.hello()
            await asyncio.sleep(DISCOVERY_WAIT)
            started = await self._up(session)
            await self._read_identity(session, bus)
            self._subscribe_new(bus)
            await self._query_all(bus)
            await self._poll_fast(bus)

            tasks = [
                reader_task,
                asyncio.create_task(session.wait_closed(), name="ssh-closed"),
                asyncio.create_task(self._loop(FAST_POLL, self._poll_fast, bus), name="poll-fast"),
                asyncio.create_task(self._slow_loop(session, bus), name="poll-slow"),
                asyncio.create_task(self._watch_bus(bus), name="bus-watch"),
                asyncio.create_task(self._history(bus, started), name="history"),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader_task
            with contextlib.suppress(Exception):
                writer.close()

    async def _watch_bus(self, bus: BoxTalkClient) -> None:
        while True:
            await asyncio.sleep(30)
            bus_silent = time.monotonic() - bus.last_notify
            if bus_silent > BUS_SILENCE_LIMIT:
                raise BoxTalkError(f"nothing from the bus for {round(bus_silent)} seconds")

    @staticmethod
    async def _loop(interval: float, poll, *args) -> None:
        while True:
            await asyncio.sleep(interval)
            await poll(*args)

    async def _slow_loop(self, session: ToonSession, bus: BoxTalkClient) -> None:
        """Slow polls, and the values the bus only reports when they change, read once more."""
        await self._poll_slow(session, bus)
        while True:
            await asyncio.sleep(SLOW_POLL)
            await self._query_all(bus)
            await self._poll_slow(session, bus)

    async def _history(self, bus: BoxTalkClient, outage_start: float | None) -> None:
        """Fill the gap of the outage just ended, then import what's left. Never ends the session itself."""
        try:
            if outage_start and time.time() - outage_start >= BACKFILL_MIN_OUTAGE:
                offset = float(self.values.get("clock_offset") or 0)
                await self.importer.fill_gap(bus, self.host_name, outage_start, time.time(), offset)
            if self.config.import_history:
                await self.importer.import_all(bus, self.host_name)
        except (OSError, TimeoutError, asyncssh.Error, ValueError, BoxTalkError) as e:
            logger.info(f"History import interrupted, continuing later: {_describe(e)}")
        await asyncio.Event().wait()

    # The bus

    def _on_discovery(self, devices: list[Device]) -> None:
        """A daemon on the Toon announced itself, possibly after a restart: subscribe to it (again)."""
        for device in devices:
            for service in device.services:
                self._subscribed.discard((device.uuid, service))
        if self._bus and self.connected:
            self._subscribe_new(self._bus)

    def _subscribe_new(self, bus: BoxTalkClient) -> None:
        for device in bus.devices.values():
            matching = [b for b in BINDINGS if device_matches(b.device, device.address, device.type)]
            for binding in matching:
                self._dispatch[(device.uuid, binding.variable)] = binding.metric
            if matching:
                for service in device.services:
                    if (device.uuid, service) not in self._subscribed:
                        bus.subscribe(device.uuid, service)
                        self._subscribed.add((device.uuid, service))

    def _on_notify(self, uuid: str, values: dict[str, str]) -> None:
        for variable, raw in values.items():
            metric = self._dispatch.get((uuid, variable))
            if metric:
                self.set_value(metric, raw)

    def _devices_of(self, bus: BoxTalkClient, binding: Binding) -> list[Device]:
        return [
            device
            for device in bus.devices.values()
            if binding.service in device.services and device_matches(binding.device, device.address, device.type)
        ]

    async def _query_all(self, bus: BoxTalkClient) -> None:
        for binding in BINDINGS:
            for device in self._devices_of(bus, binding):
                try:
                    self.set_value(binding.metric, await bus.query(device.uuid, binding.service, binding.variable))
                except TimeoutError:
                    logger.debug(f"No answer for {binding.variable} of {device.address}")
                await asyncio.sleep(0.05)

    # The web server and the system, through the session

    async def _read_identity(self, session: ToonSession, bus: BoxTalkClient) -> None:
        """Who this Toon is. The name is what bus actions to its daemons are addressed with."""
        self.host_name = (await session.run("hostname")).strip()
        with contextlib.suppress(TimeoutError):
            self.info.update(await bus.action(f"{self.host_name}:hcb_config", "specific1", "GetVersionInfo"))
        for name, destination, service in (
            ("boiler_adapter", f"{self.host_name}:happ_thermstat", "Thermostat"),
            ("meter_adapter", f"{self.host_name}:hdrv_p1", "DeviceInfo"),
        ):
            with contextlib.suppress(TimeoutError):
                self.adapters[name] = await bus.action(destination, service, "GetDeviceInformation")
        self._announce()

    async def _poll_fast(self, bus: BoxTalkClient) -> None:
        """The thermostat's dataset: what the Toon's own screen shows, and all that isn't pushed."""
        try:
            info = await bus.action(
                f"{self.host_name}:happ_thermstat", "specific1", "GetDataSet", {"dataSet": "thermostatInfo"}
            )
        except TimeoutError:
            logger.debug("No answer for the thermostat's dataset")
            return
        self.schedule = {key: info[key] for key in ("nextTime", "nextState", "nextProgram") if key in info}
        self.set_value("modulation", info.get("currentModulationLevel"))
        self.set_value("active_state", info.get("activeState"))
        self.set_value("opentherm_error", info.get("otCommError"))
        self.set_value("boiler_fault", info.get("hasBoilerFault"))
        self.set_value("boiler_module_connected", info.get("boilerModuleConnected"))
        self.set_value("setpoint_by_load_shifting", info.get("setByLoadShifting"))
        burner = str(info.get("burnerInfo"))
        self.set_value("burner_code", info.get("burnerInfo"))
        self.set_value("burner_state", BURNER_STATES.get(burner, burner))

    async def _poll_slow(self, session: ToonSession, bus: BoxTalkClient) -> None:
        self._take_system(parse_system(await session.run(SYSTEM_COMMAND)))
        destination = f"{self.host_name}:happ_thermstat"
        with contextlib.suppress(TimeoutError):
            dhw = await bus.action(destination, "Thermostat", "GetDhwSettings")
            self.set_value("dhw_enabled", dhw.get("dhwEnabled"))
            self.set_value("dhw_setpoint", dhw.get("dhwSetpoint"))
        with contextlib.suppress(TimeoutError):
            heating = await bus.action(destination, "Thermostat", "GetChSettings")
            self.set_value("max_heater_temperature", heating.get("maxHeaterTemp"))

    # Upkeep, independent of the connection

    async def upkeep(self) -> None:
        """Write samples out regularly and keep the hourly values up to date."""
        last_rollup = 0.0
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            try:
                self.database.flush()
                if time.time() - last_rollup >= ROLLUP_INTERVAL:
                    last_rollup = time.time()
                    self.database.prune(self.config.raw_retention_days)
                    self.database.roll_up(int(time.time()) - 2 * ROLLUP_INTERVAL)
            except sqlite3.Error as e:
                logger.warning(f"Database upkeep failed, trying again later: {e}")


def _describe(error: Exception) -> str:
    return str(error) or type(error).__name__
