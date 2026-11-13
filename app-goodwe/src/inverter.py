"""The inverter: connection, polling, normalising, and the only way the app writes to it.

Everything that talks to the inverter goes through one asyncio lock. The AA55/UDP
stack of the ES/EM/BP series is a small embedded implementation with no real
handling of concurrent requests: two coroutines hitting it at once each get a
garbled or missing answer, which looks like "the inverter went offline".

Writes use the goodwe library's dedicated commands, which are what actually
sticks on the ES. Writing the same settings through the generic register write
reports success and is silently ignored (confirmed on a GW3648D-ES):

  work_mode          set_operation_mode
  dod                set_ongrid_battery_dod       AA55 0x0560
  grid_export_limit  set_grid_export_limit        AA55 0x0335
  grid_export        export-limit on/off switch   AA55 0x0353 (what SolarGo sends)
  charge_i           battery charge current       AA55 0x0351 (charge voltage + current together)

Nothing else is written. The inverter's flash has limited write endurance, so
every caller writes only on a change of mind, never on a loop.
"""

import asyncio
import contextlib
import importlib
import logging
import os
import time
from collections import deque
from datetime import date, datetime
from typing import Any

import goodwe
from goodwe import InverterError, OperationMode
from goodwe.protocol import Aa55ProtocolCommand

from .config import Config
from .events import EventBus

logger = logging.getLogger(__name__)

# Work modes of set_operation_mode. Peak shaving and self-use do not exist on ES/EM/BP firmware
# (General mode already self-consumes there: solar → house → battery → grid).
WORK_MODES = {0: "General", 1: "Off-grid", 2: "Backup", 3: "Eco", 4: "Peak shaving", 5: "Self-use"}
ES_WORK_MODES = (0, 1, 2, 3)
# The settings this app can write, each through its dedicated command
WRITABLE = ("work_mode", "dod", "grid_export_limit", "grid_export", "charge_i")
WRITE_NAMES = {
    "work_mode": "work mode",
    "dod": "on-grid DoD",
    "grid_export_limit": "grid export limit",
    "grid_export": "export limit switch",
    "charge_i": "battery charge current",
}

OFFLINE_AFTER = 180  # seconds of failed reads before the inverter counts as offline
STALE_AFTER = 120  # data older than this is not acted upon by the controller
RECONNECT_AFTER_ERRORS = 3
# The inverter's network module dislikes bursts: this much quiet between any two requests,
# and more after a write, when it is busy storing the setting
REQUEST_SPACING = 2.0
WRITE_SETTLE = 5.0
# Reconnect attempts: this long between them, growing up to the maximum while it keeps failing
RECONNECT_DELAY = 10
RECONNECT_DELAY_MAX = 120
# Development: a module providing an inverter stand-in (see tools/mock_inverter.py)
MOCK_MODULE = os.environ.get("GOODWE_MOCK_INVERTER", "")


def _eco_text(slot: Any) -> str:
    """An eco mode schedule slot as text: "00:00-23:59 Mon-Sun, charge 100%, on"."""
    if slot.start_h == 48 or slot.end_h == 48:
        return "unset"
    power = int(slot.power or 0)
    action = f"charge {-power}%" if power < 0 else f"discharge {power}%" if power > 0 else "0%"
    soc = getattr(slot, "soc", 100)
    target = f" to {soc}%" if power < 0 and soc not in (None, 100) else ""
    on = str(slot).rsplit(" ", 1)[-1].lower()
    return (
        f"{slot.start_h:02d}:{slot.start_m:02d}-{slot.end_h:02d}:{slot.end_m:02d} {slot.days or ''},"
        f" {action}{target}, {on}"
    )


def _plain(value: Any) -> Any:
    """A runtime or settings value as something JSON can carry."""
    if hasattr(value, "start_h") and hasattr(value, "power"):
        with contextlib.suppress(Exception):
            return _eco_text(value)
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, int | float | str | bool) or value is None:
        return value
    return str(value)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "on", "yes")


class InverterLink:
    """Connection to one inverter: keeps reading it, and writes on request."""

    def __init__(self, config: Config, events: EventBus):
        self.host = config.inverter_host
        self.poll_seconds = config.poll_seconds
        self.events = events
        self.lock = asyncio.Lock()
        self.inverter: goodwe.Inverter | None = None
        self.online = False
        self.data: dict = {}
        self.data_at = 0.0  # monotonic
        self.read_at = 0  # epoch
        self.polls = 0
        self.connected_at = 0
        self.last_error = ""
        # Day energies derived from the recorded snapshots (the ES has no such counters); merged into the data
        self.extra: dict = {}
        self.extra_day = ""  # the local date that extra's day energies belong to
        # The settings as last read from the inverter (a full AA55 read, so only on request)
        self.settings: dict = {}
        self.settings_at = 0
        self.write_log: deque = deque(maxlen=50)
        self.listeners: list = []
        self._failed_since = 0.0
        self._was_online = False
        self._quiet_until = 0.0  # monotonic: no request before this

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    @property
    def platform(self) -> str:
        return self.inverter.__class__.__name__ if self.inverter else ""

    @property
    def fresh(self) -> bool:
        """Whether the latest data is recent enough to act on."""
        return bool(self.data) and time.monotonic() - self.data_at < STALE_AFTER

    @property
    def is_es(self) -> bool:
        return self.platform == "ES"

    def work_modes(self) -> dict[int, str]:
        return {k: v for k, v in WORK_MODES.items() if not self.is_es or k in ES_WORK_MODES}

    def view(self) -> dict:
        inverter = self.inverter
        return {
            "host": self.host,
            "poll_seconds": self.poll_seconds,
            "connected": inverter is not None,
            "online": self.online,
            "model": getattr(inverter, "model_name", None),
            "serial": getattr(inverter, "serial_number", None),
            "platform": self.platform,
            "firmware": getattr(inverter, "firmware", None),
            "arm_firmware": getattr(inverter, "arm_firmware", None),
            "arm_version": getattr(inverter, "arm_version", None),
            "dsp_version": getattr(inverter, "dsp1_version", None),
            "rated_power": getattr(inverter, "rated_power", None),
            "last_read": self.read_at or None,
            "stale": not self.fresh,
            "last_error": self.last_error,
            "polls": self.polls,
            "connected_at": self.connected_at or None,
            "settings_at": self.settings_at or None,
            "work_modes": self.work_modes(),
        }

    async def _request(self, coroutine_factory, settle: float = REQUEST_SPACING):
        """Run one request against the inverter: serialised, and spaced from the previous one."""
        async with self.lock:
            wait = self._quiet_until - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await coroutine_factory()
            finally:
                self._quiet_until = time.monotonic() + settle

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Connect and keep reading the inverter until cancelled."""
        await self._connect()
        errors = 0
        while True:
            try:
                raw = await self._request(self.inverter.read_runtime_data)
                data = self.normalise({str(k): _plain(v) for k, v in raw.items()})
                # After midnight the day energies are yesterday's until the next derive: stored under
                # the new day they would stick. The lifetime sums stay valid.
                same_day = self.extra_day == date.today().isoformat()
                for key, value in self.extra.items():
                    if not data.get(key) and (same_day or "_total" in key):
                        data[key] = value
                self.data, self.data_at, self.read_at = data, time.monotonic(), int(time.time())
                self.polls += 1
                errors = 0
                self._mark_online()
                for listener in self.listeners:
                    try:
                        listener(data)
                    except Exception as e:
                        logger.warning(f"Data listener failed: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                errors += 1
                self.last_error = f"{type(e).__name__}: {e}".strip(": ")
                logger.warning(f"Inverter read failed ({errors}): {self.last_error}")
                self._mark_failed()
                await asyncio.sleep(min(5 * 2 ** min(errors - 1, 3), 30))
                if errors >= RECONNECT_AFTER_ERRORS:
                    logger.info("Reconnecting to the inverter")
                    await self._connect()
                    errors = 0
                continue
            await asyncio.sleep(self.poll_seconds)

    async def _connect(self) -> None:
        """Connect, retrying until it works (the inverter may be rebooting or its Wi-Fi down).

        Each attempt costs the inverter a discovery exchange and a device-info read, so the
        attempts are spaced further apart the longer it keeps failing.
        """
        delay = RECONNECT_DELAY
        while True:
            try:
                if MOCK_MODULE:

                    async def connect():
                        return importlib.import_module(MOCK_MODULE).connect()
                else:

                    async def connect():
                        return await goodwe.connect(self.host)

                self.inverter = await self._request(connect)
                self.connected_at = int(time.time())
                self.last_error = ""
                logger.info(
                    f"Connected to {self.inverter.model_name} {self.inverter.serial_number} at {self.host}"
                    f" ({self.platform}, firmware {getattr(self.inverter, 'firmware', '?')})"
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}".strip(": ")
                logger.warning(
                    f"Cannot connect to the inverter at {self.host}: {self.last_error}; retrying in {delay} s"
                )
                self._mark_failed()
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_DELAY_MAX)

    def _mark_online(self) -> None:
        self._failed_since = 0.0
        if not self.online:
            self.online = True
            if self._was_online:
                self.events.emit("inverter_online", f"Inverter at {self.host} is answering again", host=self.host)
            else:
                logger.info(f"Inverter at {self.host} is online")
            self._was_online = True

    def _mark_failed(self) -> None:
        now = time.monotonic()
        if not self._failed_since:
            self._failed_since = now
        if self.online and now - self._failed_since >= OFFLINE_AFTER:
            self.online = False
            self.events.emit(
                "inverter_offline",
                f"Inverter at {self.host} has not answered for {OFFLINE_AFTER // 60} minutes ({self.last_error})",
                level="warning",
                host=self.host,
                error=self.last_error,
            )

    def normalise(self, data: dict) -> dict:
        """Platform differences, so the dashboard sees one set of names and signs.

        Conventions: pgrid > 0 imports, < 0 exports; pbattery1 > 0 charges, < 0 discharges;
        load_ptotal is the whole house; energies in kWh.
        """
        d = dict(data)
        # The ES reports the house as house_consumption (PV + grid + battery; balances the flow
        # diagram), the ET as load_ptotal; plant_power is only the inverter's own AC output
        if "load_ptotal" not in d:
            d["load_ptotal"] = d.get("house_consumption") or d.get("pload") or d.get("plant_power") or 0
        if "backup_ptotal" not in d:
            d["backup_ptotal"] = d.get("pback_up") or 0
        label = str(d.get("grid_in_out_label", "")).lower()
        if d.get("pgrid") is not None:
            p = float(d["pgrid"])
            if "import" in label:
                d["pgrid"] = abs(p)
            elif "export" in label:
                d["pgrid"] = -abs(p)
            elif self.is_es:
                # The ES reports grid power negative while importing
                d["pgrid"] = -p
        if self.is_es:
            # "Today's PV Generation" on the ES is not solar: it leaves out what goes straight into the
            # battery and keeps climbing on battery power after dark. The day's solar is derived from
            # the power instead; the counter stays visible in the raw data under Debug.
            d["e_day_counter"] = d.get("e_day")
            d["e_day"] = 0
        if self.is_es and d.get("pbattery1") is not None:
            # The ES reports charging as negative
            d["pbattery1"] = -float(d["pbattery1"])
        for key in (
            "e_day_imp",
            "e_day_exp",
            "e_total_imp",
            "e_total_exp",
            "e_load_day",
            "e_bat_charge_day",
            "e_bat_discharge_day",
            "e_bat_charge_total",
            "e_bat_discharge_total",
        ):
            d.setdefault(key, 0)
        return d

    async def read_settings(self) -> dict:
        """The inverter's settings (a full settings read; the ES answers one such read in a few seconds)."""
        if self.inverter is None:
            raise InverterError("Inverter not connected")
        raw = await self._request(self.inverter.read_settings_data)
        self.settings = {str(k): _plain(v) for k, v in raw.items()}
        self.settings_at = int(time.time())
        return self.settings

    async def read_setting(self, key: str) -> Any:
        """One setting, read fresh (a full settings read on the ES)."""
        settings = await self.read_settings()
        value = settings.get(key)
        if value is None:
            raise InverterError(f"The inverter does not report the setting {key}")
        return value

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    async def apply(self, key: str, value: Any, source: str = "dashboard", notify: bool = True) -> str:
        """Write one setting through its dedicated command. Returns a short description.

        Every write raises an inverter_write (or inverter_write_failed) event, so the
        dashboard and Home Assistant see each command whoever asked for it. `notify=False`
        is for callers that write in a verify-and-retry loop and report once themselves.
        """
        if key not in WRITABLE:
            raise ValueError(f"The setting {key} cannot be written by this app")
        if self.inverter is None:
            raise InverterError("Inverter not connected")
        entry = {"ts": int(time.time()), "key": key, "value": value, "source": source}
        try:
            result = await self._request(lambda: self._apply_locked(key, value), settle=WRITE_SETTLE)
        except Exception as e:
            error = f"{type(e).__name__}: {e}".strip(": ")
            self.write_log.appendleft({**entry, "ok": False, "result": error})
            if notify:
                self.events.emit(
                    "inverter_write_failed",
                    f"{WRITE_NAMES[key]} → {value} failed ({source}): {error}",
                    level="warning",
                    key=key,
                    value=value,
                    source=source,
                    error=error,
                )
            raise
        self.write_log.appendleft({**entry, "ok": True, "result": result})
        self._settings_hint(key, value)
        if notify:
            self.events.emit(
                "inverter_write", f"{result} ({source})", key=key, value=value, source=source, result=result
            )
        return result

    def _settings_hint(self, key: str, value: Any) -> None:
        """Show the written value in the cached settings until the next real read."""
        if not self.settings:
            return
        if key == "grid_export":
            self.settings["grid_export"] = 1 if _truthy(value) else 0
        elif key in ("dod", "grid_export_limit", "charge_i", "work_mode"):
            self.settings[key] = int(value)

    async def _apply_locked(self, key: str, value: Any) -> str:
        inverter = self.inverter
        if key == "work_mode":
            mode = int(value)
            if mode not in WORK_MODES:
                raise ValueError(f"Unknown work mode {value}")
            if self.is_es and mode not in ES_WORK_MODES:
                # The library's ES set_operation_mode has no branch for these and would send nothing
                raise ValueError(f"{WORK_MODES[mode]} mode does not exist on ES firmware")
            await inverter.set_operation_mode(OperationMode(mode))
            return f"work mode → {WORK_MODES[mode]}"

        if key == "dod":
            dod = int(value)
            if not 0 <= dod <= 100:
                raise ValueError("DoD must be between 0 and 100")
            await inverter.set_ongrid_battery_dod(dod)
            return f"on-grid DoD → {dod}% (floor {100 - dod}%)"

        if key == "grid_export_limit":
            watts = int(value)
            if not 0 <= watts <= 65535:
                raise ValueError("Export limit must be between 0 and 65535 W")
            await inverter.set_grid_export_limit(watts)
            return f"grid export limit → {watts} W"

        if key == "grid_export":
            # The limit's on/off switch (settings field grid_export, 1 = limit enforced). The library has
            # no writer; SolarGo sends AA55 0x0353 "BackflowPrevention" (reply 0x03D3)
            if not self.is_es:
                raise ValueError("The export limit switch command is only known for ES firmware")
            on = 1 if _truthy(value) else 0
            await inverter._read_from_socket(Aa55ProtocolCommand(f"035301{on:02x}", "03d3"))
            return f"export limit {'enforced' if on else 'off'}"

        if key == "charge_i":
            # AA55 0x0351 "set battery charge parameters" carries the charge voltage (V x 10) and the
            # charge current (whole amps) together, so the voltage just read is sent back unchanged.
            # 0 A stops all charging, including from solar surplus; the BMS clamps anything above its own
            # ceiling (runtime field battery_charge_limit), so a lower read-back is the clamp, not a lost write
            if not self.is_es:
                raise ValueError("The charge current command is only known for ES firmware")
            amps = int(value)
            if not 0 <= amps <= 500:
                raise ValueError("Charge current must be between 0 and 500 A")
            settings = await inverter.read_settings_data()
            voltage = _plain(settings.get("charge_v"))
            if voltage is None:
                raise InverterError("The inverter did not report its charge voltage")
            v_raw = round(float(voltage) * 10)
            await inverter._read_from_socket(Aa55ProtocolCommand(f"035104{v_raw:04x}{amps:04x}", "03d1"))
            return f"battery charge current → {amps} A"

        raise ValueError(f"The setting {key} cannot be written by this app")
