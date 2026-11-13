"""Battery control: forecast-driven discharge hold, zero export and the solar surplus signal.

Everything here runs on a five-minute tick against data that was polled anyway;
the inverter is only written on a change of mind. Its flash has limited write
endurance and its network stack dislikes bursts, so there is a daily write
budget, a verify-and-retry that gives up rather than hammering, and watchdogs
that read the inverter back only when the polled numbers say something is off.

Work mode. All of this assumes General mode, which on ES firmware already
self-consumes: solar → house → battery → grid, never charging from the grid.
The settings are read at start and every hour; in any other mode an event says
so and auto-hold, when on, switches back to General (Backup does not hold the battery on the ES, and Eco
follows its own schedule).

Discharge hold. Battery discharge is timed with the on-grid depth of discharge
(set_ongrid_battery_dod): DoD 0 = floor 100% = hold, the grid covers the house;
DoD 80 = floor 20% = normal. While the hourly solar forecast (adjusted by the
offset setting) or the live PV is at or above the threshold, and SoC is below
the release level, the battery is held so the solar surplus goes into it and the
evening is covered by a full battery; otherwise it is released.

Zero export. The grid export limit is kept at 0 W with the limit switch on, so
solar that neither the house nor the battery can take is curtailed instead of
exported. Checked once when enabled and when the app starts, then only read
back when the polled grid power shows sustained export.

Solar surplus. A signal for Home Assistant (e.g. to start a miner): on when the
battery is full and the estimated surplus is at least the margin; off when the
house imports from the grid or drains the battery. Every poll is stamped with the
signal and the surplus and curtailment estimates, which the recorder turns into
daily statistics.
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path

from .events import EventBus
from .forecast import Forecast
from .inverter import WORK_MODES, InverterLink

logger = logging.getLogger(__name__)

TICK_SECONDS = 300
# The on-grid DoD while held: no discharge, the grid covers the house
HOLD_DOD = 0
# A discharge while held only counts as a violation above this power and after this many
# consecutive ticks: the inverter hunts around the solar/house balance point for minutes
HOLD_DISCHARGE_W = 100
HOLD_STRIKES = 3
# Likewise for export under zero export (the CT regulation overshoots on every load drop)
EXPORT_W = 150
EXPORT_STRIKES = 3
# DoD writes per day. Steady state is about two; past this the controller stops writing until tomorrow
DOD_WRITES_PER_DAY = 12
# Battery power below which it counts as "not charging" for the surplus signal
IDLE_W = 50
# The settings (work mode, battery capacity) are read at start and then this often; a failed read
# is tried again after the retry interval, not on the next tick
SETTINGS_CHECK_SECONDS = 3600
SETTINGS_RETRY_SECONDS = 1800
# A settings read this recent is reused instead of asking the inverter again
SETTINGS_FRESH_SECONDS = 60
GENERAL_MODE = 0
# With auto-hold on, a work mode other than General is put back at most this often (a mode that will
# not stick must not cost a write every tick)
GENERAL_RETRY_SECONDS = 3600
# Nominal voltage of a 16-cell LFP pack, for the commissioned capacity (Ah) in kWh
NOMINAL_PACK_V = 51.2
# Charge current restored when a charge cap from an earlier version left charging braked
# (the BMS clamps it to its own ceiling, so a high value is safe)
RELEASE_CHARGE_A = 50


@dataclass
class ControlSettings:
    # Forecast-driven discharge hold
    auto_hold: bool = True
    threshold_kwh: float = 0.1  # hold while this hour's forecast (or live PV) is at least this
    forecast_offset_pct: int = 0  # correction applied to the forecast before the test (-50..50)
    night_dod: int = 80  # DoD once released (80 = discharge to a floor of 20%)
    release_soc: int = 100  # release the hold once SoC reaches this (100 = hold all day)
    # Zero export
    zero_export: bool = False
    # Solar surplus signal
    surplus_margin_w: int = 500  # on when the estimated surplus is at least this
    surplus_full_soc: int = 97  # the battery counts as full from this SoC
    surplus_off_import_w: int = 150  # off once the house imports more than this
    surplus_debounce_s: int = 120  # the new state must hold this long before the signal flips
    # Controller state (persisted, not settings)
    battery_capacity_kwh: float = 0.0  # from the inverter's commissioned capacity, for the cycle count
    release_charge_brake: bool = False  # an earlier version's charge cap left charging braked
    defaults_version: int = 0  # 1 = auto-hold was switched on once (0.3.5 made it the default)


SETTING_BOUNDS = {
    "threshold_kwh": (0, 100),
    "forecast_offset_pct": (-50, 50),
    "night_dod": (0, 100),
    "release_soc": (0, 100),
    "surplus_margin_w": (0, 50000),
    "surplus_full_soc": (50, 100),
    "surplus_off_import_w": (0, 50000),
    "surplus_debounce_s": (0, 3600),
}
STATE_FIELDS = ("battery_capacity_kwh", "release_charge_brake", "defaults_version")
BOOL_FIELDS = ("auto_hold", "zero_export")


class BatteryController:
    def __init__(self, link: InverterLink, forecast: Forecast, events: EventBus, data_dir: Path):
        self.link = link
        self.forecast = forecast
        self.events = events
        self.file = data_dir / "control.json"
        self.settings = self._load()
        self._save()  # keeps a one-off change of _load
        # Runtime
        self.held: bool | None = None  # what auto-hold last decided
        self.last_dod: int | None = None  # the DoD auto-hold last wrote and confirmed
        self.decision: dict = {}
        self.hold_strikes = 0
        self.export_strikes = 0
        self.export_verified = False
        self.last_tick = 0
        self.budget = {"date": "", "used": 0, "warned": False}
        self.work_mode: int | None = None  # the inverter's work mode, as last read
        self._settings_retry_at = 0.0
        self.settings_error: str | None = None  # why the last settings read failed, until one succeeds
        self.miners = None  # the surplus loads (miners.Miners), set by __main__
        self._brake_retry_at = 0.0
        self._general_at = 0.0  # when the work mode was last put back to General
        # Surplus signal
        self.surplus = False
        self.surplus_w = 0
        self.curtailed_w = 0
        self.surplus_reason = ""
        # The house no longer covered by the panels (importing, or the battery discharging): the
        # miners stop one by one while this holds; the reason in words, empty when covered
        self.overload = ""
        self._surplus_pending: bool | None = None
        self._surplus_pending_since = 0.0
        link.listeners.append(self._on_data)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _load(self) -> ControlSettings:
        settings = ControlSettings()
        try:
            data = json.loads(self.file.read_text())
        except (OSError, ValueError):
            return settings
        if not isinstance(data, dict):
            return settings
        for f in fields(ControlSettings):
            if f.name in data:
                with contextlib.suppress(TypeError, ValueError):
                    setattr(settings, f.name, f.type(data[f.name]) if f.type in (int, float, bool) else data[f.name])
        # Up to 0.1.x a charge cap could brake charging (charge current 0 A); it is gone, so a brake
        # that was on at the update is released once
        if data.get("charge_cap_active"):
            settings.release_charge_brake = True
        # Up to 0.3.4 auto-hold was off by default, so the forecast controlled nothing until switched
        # on: switch it on once
        if settings.defaults_version < 1:
            settings.auto_hold = True
            settings.defaults_version = 1
        return settings

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(asdict(self.settings), indent=2))

    def update(self, changes: dict) -> ControlSettings:
        """Apply settings from the dashboard; the controller's own state fields are not writable."""
        for key, value in changes.items():
            if key in STATE_FIELDS or not hasattr(self.settings, key):
                continue
            if key in BOOL_FIELDS:
                setattr(self.settings, key, bool(value))
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{key} must be a number") from e
            lo, hi = SETTING_BOUNDS[key]
            if not lo <= number <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
            setattr(self.settings, key, number if key == "threshold_kwh" else round(number))
        if "zero_export" in changes:
            self.export_verified = False  # check the inverter on the next tick
        if "auto_hold" in changes and not self.settings.auto_hold:
            self.held, self.last_dod = None, None
        self._save()
        return self.settings

    def state(self) -> dict:
        s = self.settings
        mode = self.link.settings.get("work_mode")
        with contextlib.suppress(TypeError, ValueError):
            mode = int(mode) if mode is not None else None
        return {
            **asdict(s),
            "held": self.held,
            "last_dod": self.last_dod,
            "dod_reported": self.link.settings.get("dod"),
            "export_limit_reported": self.link.settings.get("grid_export_limit"),
            "export_switch_reported": self.link.settings.get("grid_export"),
            "work_mode": mode,
            "work_mode_name": WORK_MODES.get(mode) if isinstance(mode, int) else None,
            "work_mode_general": None if not isinstance(mode, int) else mode == GENERAL_MODE,
            "settings_read_at": self.link.settings_at or None,
            # Only while nothing was read (a manual read from Settings also fills the cache)
            "settings_error": None if self.link.settings_at else self.settings_error,
            "decision": self.decision,
            "dod_writes_today": self.budget["used"] if self.budget["date"] == date.today().isoformat() else 0,
            "dod_writes_per_day": DOD_WRITES_PER_DAY,
            "export_verified": self.export_verified,
            "hold_strikes": self.hold_strikes,
            "export_strikes": self.export_strikes,
            "last_tick": self.last_tick or None,
            "surplus": self.surplus,
            "surplus_w": self.surplus_w,
            "curtailed_w": self.curtailed_w,
            "surplus_reason": self.surplus_reason,
            "miners": self.miners.state() if self.miners else {"enabled": False},
        }

    # ------------------------------------------------------------------
    # Manual control
    # ------------------------------------------------------------------
    async def set_dod(self, dod: int, source: str = "dashboard") -> str:
        """Hold (0) or normal (night_dod) from the dashboard. With auto-hold on, the next decision wins again."""
        result = await self.link.apply("dod", dod, source)
        self.last_dod = dod
        return result

    # ------------------------------------------------------------------
    # The tick
    # ------------------------------------------------------------------
    async def run(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Battery controller: {e}", exc_info=True)
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self) -> None:
        self.last_tick = int(time.time())
        if self.link.inverter is None:
            self.last_dod = None
            return
        await self._settings_check()
        await self._release_charge_brake()
        if self.settings.auto_hold:
            await self._auto_hold_tick()
        await self._zero_export_tick()

    # ------------------------------------------------------------------
    # Daily settings check: work mode and battery capacity
    # ------------------------------------------------------------------
    async def _settings_check(self) -> None:
        now = time.time()
        due = not self.link.settings_at or now - self.link.settings_at >= SETTINGS_CHECK_SECONDS
        if due and now >= self._settings_retry_at:
            try:
                await self.link.read_settings()
                self.settings_error = None
            except Exception as e:
                # Never read yet: the dashboard has no DoD or work mode, so try again on the next tick.
                # Read before: the cached settings still hold, so back off.
                wait = SETTINGS_RETRY_SECONDS if self.link.settings_at else TICK_SECONDS
                self._settings_retry_at = now + wait
                self.settings_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(
                    f"Settings check: read failed ({self.settings_error}), trying again in {wait // 60} minutes"
                )
        self._check_work_mode()
        self._check_dod()
        self._update_capacity()

    def _dod_on_inverter(self) -> int | None:
        """The DoD from the last settings read, when that read is recent enough to trust."""
        if not self.link.settings_at or time.time() - self.link.settings_at > SETTINGS_CHECK_SECONDS:
            return None
        try:
            return int(self.link.settings["dod"])
        except (KeyError, TypeError, ValueError):
            return None

    def _check_dod(self) -> None:
        """A DoD changed behind the controller's back (SolarGo, the cloud) is written again on this tick."""
        if not self.settings.auto_hold or self.last_dod is None:
            return
        with contextlib.suppress(KeyError, TypeError, ValueError):
            dod = int(self.link.settings["dod"])
            if dod != self.last_dod:
                logger.info(f"The inverter's DoD is {dod}%, auto-hold set {self.last_dod}%: setting it again")
                self.last_dod = None

    def _check_work_mode(self) -> None:
        try:
            mode = int(self.link.settings["work_mode"])
        except (KeyError, TypeError, ValueError):
            return
        if mode == self.work_mode:
            return
        was, self.work_mode = self.work_mode, mode
        name = WORK_MODES.get(mode, str(mode))
        if was is not None:
            self.events.emit(
                "work_mode_changed",
                f"Work mode: {WORK_MODES.get(was, str(was))} → {name}",
                mode=name,
                was=WORK_MODES.get(was, str(was)),
            )
        if mode != GENERAL_MODE:
            self.events.emit(
                "work_mode_not_general",
                f"The inverter is in {name} mode, not General: "
                + ("auto-hold switches it back." if self.settings.auto_hold else "auto-hold is off, so it stays."),
                level="warning",
                mode=name,
            )

    def _update_capacity(self) -> None:
        try:
            ah = float(self.link.settings.get("capacity") or 0)
        except (TypeError, ValueError):
            return
        kwh = round(ah * NOMINAL_PACK_V / 1000, 1)
        if ah > 0 and kwh != self.settings.battery_capacity_kwh:
            self.settings.battery_capacity_kwh = kwh
            self._save()

    async def _release_charge_brake(self) -> None:
        """An earlier version's charge cap may have left the charge current at 0 A: restore it once."""
        if not self.settings.release_charge_brake or not self.link.fresh or time.time() < self._brake_retry_at:
            return
        amps = max(RELEASE_CHARGE_A, int(float(self.link.data.get("battery_charge_limit") or 0)))
        try:
            await self.link.apply("charge_i", amps, "update")
        except Exception as e:
            self._brake_retry_at = time.time() + SETTINGS_RETRY_SECONDS
            logger.warning(f"Releasing the old charge cap failed ({e}); trying again in 30 minutes")
            return
        self.settings.release_charge_brake = False
        self._save()
        logger.info("The charge cap of an earlier version had braked charging: charging released")

    # ------------------------------------------------------------------
    # Discharge hold
    # ------------------------------------------------------------------
    async def _auto_hold_tick(self) -> None:
        s = self.settings
        await self.forecast.fetch()  # cached unless older than 30 minutes
        if not self.link.fresh:
            self.decision = {"text": "No fresh inverter data, keeping the DoD as it is"}
            return
        if self.work_mode not in (None, GENERAL_MODE):
            name = WORK_MODES.get(self.work_mode, str(self.work_mode))
            if time.time() - self._general_at < GENERAL_RETRY_SECONDS:
                self.decision = {"text": f"Paused: the inverter is still in {name} mode, trying General again later"}
                return
            self._general_at = time.time()
            try:
                await self.link.apply("work_mode", GENERAL_MODE, "auto-hold")
            except Exception as e:
                self.decision = {"text": f"Paused: the inverter is in {name} mode and switching to General failed"}
                logger.warning(f"Back to General failed: {e}")
                return
            # Read the mode back now rather than at tomorrow's settings check
            self.link.settings_at = 0
            self._settings_retry_at = 0.0
            await self._settings_check()
            if self.work_mode != GENERAL_MODE:
                self.decision = {"text": f"Paused: the inverter stayed in {name} mode after switching to General"}
                return
        if not self.forecast.configured:
            self.decision = {"text": "Forecast not configured, auto-hold cannot decide"}
            return
        hour_kwh = self.forecast.hour_kwh()
        if not self.forecast.summary().get("fetched_at"):
            # No forecast at all (fetch failed, nothing cached): keep the current DoD rather than guess
            self.decision = {"text": "No forecast available, keeping the DoD as it is"}
            return
        adjusted = round(hour_kwh * (1 + s.forecast_offset_pct / 100), 3)
        live_kw = round(float(self.link.data.get("ppv") or 0) / 1000, 3)
        soc = float(self.link.data.get("battery_soc") or 0)
        # Live PV is a network-independent sanity check: if the panels produce the threshold right
        # now, it is daytime whatever the forecast claims
        producing = max(adjusted, live_kw) >= float(s.threshold_kwh)
        held = producing and (s.release_soc >= 100 or soc < s.release_soc)
        desired = HOLD_DOD if held else int(s.night_dod)
        if held:
            reason = f"forecast {adjusted:.2f} kWh this hour, live {live_kw:.2f} kW, SoC {soc:.0f}%"
        elif producing:
            reason = f"SoC {soc:.0f}% reached the release level {s.release_soc}%"
        else:
            reason = f"forecast {adjusted:.2f} kWh this hour and live {live_kw:.2f} kW below {s.threshold_kwh} kWh"
        self.decision = {
            "producing": producing,
            "hour_kwh": hour_kwh,
            "adjusted_kwh": adjusted,
            "live_kw": live_kw,
            "soc": soc,
            "held": held,
            "desired_dod": desired,
            "text": f"{'Hold' if held else 'Discharge'}: {reason}",
        }
        self.held = held
        if not held:
            self.hold_strikes = 0  # discharging is expected once released
        if self.last_dod is None and self._dod_on_inverter() == desired:
            # After a restart: the inverter already has it, so no write (the flash has limited endurance)
            self.last_dod = desired
        if desired != self.last_dod:
            ok = await self._apply_dod_verified(desired, held, reason)
            self.last_dod = desired if ok else None  # unconfirmed: try again next tick
        elif held:
            await self._hold_watchdog(desired)

    def _budget_take(self) -> bool:
        today = date.today().isoformat()
        if self.budget["date"] != today:
            self.budget.update(date=today, used=0, warned=False)
        if self.budget["used"] >= DOD_WRITES_PER_DAY:
            return False
        self.budget["used"] += 1
        return True

    async def _apply_dod_verified(self, target: int, held: bool, reason: str, source: str = "auto-hold") -> bool:
        """Write the DoD and read it back (AA55 writes are occasionally lost). True when confirmed or
        unverifiable (read timed out: assume applied); False when the inverter reports another value."""
        last_read = None
        confirmed = False
        error = ""
        for attempt in range(3):
            if not self._budget_take():
                if not self.budget["warned"]:
                    self.budget["warned"] = True
                    self.events.emit(
                        "write_budget_exhausted",
                        f"{DOD_WRITES_PER_DAY} DoD writes today and the inverter keeps reporting another value;"
                        " not writing again until tomorrow to spare its flash",
                        level="warning",
                        target=target,
                    )
                return False
            try:
                await self.link.apply("dod", target, source, notify=False)
                error = ""
            except Exception as e:
                # The ES's Wi-Fi module drops an answer now and then; the command may still have
                # arrived. Give it a moment, then read back and send again if it did not.
                error = f"{type(e).__name__}: {e}".strip(": ")
                logger.warning(f"DoD → {target}: attempt {attempt + 1} got no answer ({error}), checking")
                await asyncio.sleep(10)
            await asyncio.sleep(3)
            try:
                last_read = int(await self.link.read_setting("dod"))
                if last_read == target:
                    confirmed = True
                    break
            except Exception:
                last_read = None
            await asyncio.sleep(2)
        ok = confirmed or (last_read is None and not error)
        state = (
            "confirmed"
            if confirmed
            else f"no answer after 3 attempts ({error})"
            if error and last_read is None
            else "sent, read-back timed out"
            if last_read is None
            else f"not applied, the inverter still reports {last_read}%"
        )
        description = f"on-grid DoD → {target}% (floor {100 - target}%, {state})"
        if ok:
            self.events.emit(
                "hold_applied" if held else "hold_released",
                f"Battery {'held' if held else 'released'}: {reason}. {description}",
                dod=target,
                reason=reason,
                confirmed=confirmed,
            )
            self.events.emit("inverter_write", f"{description} ({source})", key="dod", value=target, source=source)
        else:
            self.events.emit(
                "inverter_write_failed",
                f"{description} ({source})",
                level="warning",
                key="dod",
                value=target,
                source=source,
                error=state,
            )
        return ok

    async def _hold_watchdog(self, target_dod: int) -> None:
        """The battery discharging while it should be held. Short discharges are normal (the inverter
        hunts around the balance point), so it must exceed HOLD_DISCHARGE_W for HOLD_STRIKES ticks.
        Only then is the DoD read back: a wrong value means the write was lost (re-send it), the right
        value means the firmware ignores its own floor (say so, re-writing would only burn flash)."""
        if not self.link.fresh:
            return
        pbat = float(self.link.data.get("pbattery1") or 0)  # > 0 charging
        soc = float(self.link.data.get("battery_soc") or 0)
        if pbat >= -HOLD_DISCHARGE_W:
            if self.hold_strikes >= HOLD_STRIKES:
                self.events.emit("hold_violation_recovered", f"Battery hold honoured again at {soc:.0f}% SoC")
            self.hold_strikes = 0
            return
        self.hold_strikes += 1
        if self.hold_strikes != HOLD_STRIKES:
            return  # not sustained yet, or already reported
        dod_read = None
        with contextlib.suppress(Exception):
            dod_read = int(await self.link.read_setting("dod"))
        drifted = dod_read is not None and dod_read != target_dod
        self.events.emit(
            "hold_violation",
            f"Battery discharging {-pbat:.0f} W at {soc:.0f}% SoC for {HOLD_STRIKES * 5}+ minutes while held"
            f" (DoD should be {target_dod}, inverter reports {dod_read if dod_read is not None else '?'});"
            f" {'re-sending the DoD' if drifted else 'the firmware is ignoring its own floor'}",
            level="warning",
            soc=soc,
            discharge_w=-pbat,
            dod_reported=dod_read,
            resending=drifted,
        )
        if drifted:
            await self._apply_dod_verified(target_dod, True, "re-sent by the hold watchdog", source="hold watchdog")

    # ------------------------------------------------------------------
    # Zero export
    # ------------------------------------------------------------------
    async def _read_export(self) -> tuple[int | None, int | None]:
        try:
            if time.time() - self.link.settings_at < SETTINGS_FRESH_SECONDS:
                settings = self.link.settings  # just read by the settings check
            else:
                settings = await self.link.read_settings()
            return int(settings["grid_export_limit"]), int(settings["grid_export"])
        except Exception as e:
            logger.info(f"Zero export: settings read failed ({e})")
            return None, None

    async def _write_zero_export(self, limit: int, enabled: int) -> None:
        """Send only what is wrong; each one is a flash write."""
        if limit != 0:
            await self.link.apply("grid_export_limit", 0, "zero export")
        if enabled != 1:
            if limit != 0:
                await asyncio.sleep(3)  # no AA55 commands back-to-back
            await self.link.apply("grid_export", 1, "zero export")
        self.events.emit(
            "zero_export_set",
            "Grid export limit set to 0 W with the limit enforced",
            limit_was=limit,
            switch_was=enabled,
        )

    async def _zero_export_tick(self) -> None:
        if not self.settings.zero_export:
            self.export_strikes, self.export_verified = 0, False
            return
        if not self.export_verified:
            limit, enabled = await self._read_export()
            if limit is None:
                return  # next tick
            if limit != 0 or enabled != 1:
                logger.info(f"Zero export: limit {limit} W, switch {enabled}; setting 0 W and on")
                await self._write_zero_export(limit, enabled)
            self.export_verified = True
            return
        if not self.link.fresh:
            return
        export_w = -float(self.link.data.get("pgrid") or 0)
        if export_w < EXPORT_W:
            if self.export_strikes >= EXPORT_STRIKES:
                self.events.emit("export_violation_recovered", "Export stopped again")
            self.export_strikes = 0
            return
        self.export_strikes += 1
        if self.export_strikes != EXPORT_STRIKES:
            return
        limit, enabled = await self._read_export()
        drifted = limit is not None and (limit != 0 or enabled != 1)
        self.events.emit(
            "export_violation",
            f"Exporting {export_w:.0f} W for {EXPORT_STRIKES * 5}+ minutes with zero export on"
            f" (limit {limit if limit is not None else '?'} W, switch {enabled if enabled is not None else '?'});"
            f" {'re-sending the limit' if drifted else 'the inverter ignores its own limit'}",
            level="warning",
            export_w=export_w,
            limit=limit,
            switch=enabled,
            resending=drifted,
        )
        if drifted:
            await self._write_zero_export(limit, enabled)

    # ------------------------------------------------------------------
    # Solar surplus signal (evaluated on every poll)
    # ------------------------------------------------------------------
    def _on_data(self, data: dict) -> None:
        self._evaluate_surplus(data)
        # For the recorder (it stores the poll after this listener): the daily surplus statistics
        data["surplus"] = 1 if self.surplus else 0
        data["surplus_w"] = self.surplus_w
        data["curtailed_w"] = self.curtailed_w

    def _evaluate_surplus(self, data: dict) -> None:
        s = self.settings
        soc = float(data.get("battery_soc") or 0)
        ppv = float(data.get("ppv") or 0)
        pgrid = float(data.get("pgrid") or 0)
        pbat = float(data.get("pbattery1") or 0)
        full = soc >= s.surplus_full_soc
        exporting_w = max(-pgrid, 0.0)
        # Under zero export a full battery means the inverter curtails the panels: the surplus is
        # invisible in the grid power, so estimate it as the forecast's headroom above the live PV
        curtailed_w = 0.0
        if full and s.zero_export and pbat < IDLE_W and self.forecast.configured:
            headroom = self.forecast.hour_watts() * (1 + s.forecast_offset_pct / 100) - ppv
            curtailed_w = max(headroom, 0.0)
        surplus_w = round(exporting_w + curtailed_w)
        self.surplus_w = surplus_w
        self.curtailed_w = round(curtailed_w)
        importing = pgrid > s.surplus_off_import_w
        draining = pbat < -IDLE_W
        self.overload = (
            f"importing {pgrid:.0f} W" if importing else f"battery discharging {-pbat:.0f} W" if draining else ""
        )
        if self.surplus:
            # Off when the house is no longer covered by the panels
            wanted = not (importing or draining or soc < s.surplus_full_soc - 10)
            reason = (
                f"importing {pgrid:.0f} W"
                if importing
                else f"battery discharging {-pbat:.0f} W"
                if draining
                else f"SoC {soc:.0f}%"
                if soc < s.surplus_full_soc - 10
                else f"surplus about {surplus_w} W"
            )
        else:
            wanted = full and surplus_w >= s.surplus_margin_w
            reason = (
                f"surplus about {surplus_w} W"
                if wanted
                else f"battery {soc:.0f}%, surplus about {surplus_w} W"
                if full
                else f"battery {soc:.0f}% not full"
            )
        self.surplus_reason = reason
        if wanted == self.surplus:
            self._surplus_pending = None
            return
        now = time.monotonic()
        if self._surplus_pending != wanted:
            self._surplus_pending, self._surplus_pending_since = wanted, now
        if now - self._surplus_pending_since < s.surplus_debounce_s:
            return
        self.surplus = wanted
        self._surplus_pending = None
        self.events.emit(
            "surplus_started" if wanted else "surplus_ended",
            f"Solar surplus {'available' if wanted else 'gone'}: {reason}",
            surplus_w=surplus_w,
            soc=soc,
        )
