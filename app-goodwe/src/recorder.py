"""Records what the inverter reports and notices the things worth an event.

Every poll is stored as a snapshot. Every five minutes the day energies the ES does
not count itself (import, export, house, battery) are derived from the snapshots
and merged into the daily summary, and offered back to the live data. Once an hour
the day's forecast and actual production are logged for the accuracy table.
"""

import asyncio
import logging
import time
from datetime import date, timedelta

from .database import SNAPSHOT_DAYS, Database
from .events import EventBus
from .forecast import Forecast
from .inverter import InverterLink

logger = logging.getLogger(__name__)

DERIVE_SECONDS = 300
BATTERY_LOW_SOC = 15


class Recorder:
    def __init__(self, database: Database, link: InverterLink, forecast: Forecast, events: EventBus):
        self.database = database
        self.link = link
        self.forecast = forecast
        self.events = events
        self._last: dict = {}
        self._full_day = ""
        self._low = False
        link.listeners.append(self.on_data)

    def on_data(self, data: dict) -> None:
        try:
            self.database.insert_snapshot(data)
        except Exception as e:
            logger.warning(f"Snapshot not stored: {e}")
        self._watch(data)
        self._last = data

    def _watch(self, data: dict) -> None:
        """Edge events: faults, grid outages, battery full/low. (Work mode changes come from the
        controller's settings check: on the ES the runtime label is the energy mode, not the work mode.)"""
        last = self._last
        if not last:
            return
        fault = str(data.get("error_codes") or "0") not in ("0", "", "None")
        was_fault = str(last.get("error_codes") or "0") not in ("0", "", "None")
        if fault and not was_fault:
            self.events.emit(
                "fault_detected",
                f"Inverter fault: {data.get('diagnose_result_label') or data.get('error_codes')}",
                level="warning",
                error_codes=data.get("error_codes"),
                diagnose=data.get("diagnose_result_label"),
            )
        elif was_fault and not fault:
            self.events.emit("fault_cleared", "Inverter fault cleared")

        mode, was_mode = str(data.get("work_mode_label") or ""), str(last.get("work_mode_label") or "")
        off_grid = any(word in mode.lower() for word in ("off-grid", "off grid", "backup"))
        was_off_grid = any(word in was_mode.lower() for word in ("off-grid", "off grid", "backup"))
        if off_grid and not was_off_grid:
            self.events.emit("grid_outage", f"Grid lost: inverter switched to {mode}", level="warning", mode=mode)
        elif was_off_grid and not off_grid:
            self.events.emit("grid_restored", f"Grid back: inverter in {mode}", mode=mode)

        soc = data.get("battery_soc")
        if soc is not None:
            soc = float(soc)
            today = date.today().isoformat()
            if soc >= 100 and self._full_day != today:
                self._full_day = today
                self.events.emit("battery_full", "Battery reached 100%", soc=soc)
            if soc <= BATTERY_LOW_SOC and not self._low:
                self._low = True
                self.events.emit("battery_low", f"Battery low: {soc:.0f}%", level="warning", soc=soc)
            elif soc > BATTERY_LOW_SOC + 5:
                self._low = False

    async def run(self) -> None:
        """Derive the day energies every five minutes; log forecast against production hourly."""
        # Which energies can be trusted depends on the platform, known once connected
        while self.link.inverter is None:
            await asyncio.sleep(5)
        derived_only = self.link.is_es
        try:
            # Older days were derived while they were recent; years of history need no recomputing
            for day in self.database.snapshot_dates(since=int(time.time()) - (SNAPSHOT_DAYS + 1) * 86400):
                self.database.update_derived(day, derived_only)
        except Exception as e:
            logger.warning(f"Derived energy backfill failed: {e}")
        cycles = 0
        while True:
            try:
                today = date.today()
                self.database.update_derived((today - timedelta(days=1)).isoformat(), derived_only)
                self.database.update_derived(today.isoformat(), derived_only)
                self.link.extra = self.database.energy_totals()
                self.link.extra_day = today.isoformat()
            except Exception as e:
                logger.warning(f"Derived energies: {e}")
            if cycles % 12 == 0:
                await self._log_forecast()
            cycles += 1
            await asyncio.sleep(DERIVE_SECONDS)

    async def _log_forecast(self) -> None:
        if not self.forecast.configured:
            return
        try:
            await self.forecast.fetch()
            today = date.today()
            kwh = self.forecast.day_kwh(today)
            if kwh is not None:
                self.database.record_forecast(today.isoformat(), kwh)
            if self.link.data.get("e_day") is not None:
                self.database.record_actual(today.isoformat(), float(self.link.data["e_day"]))
        except Exception as e:
            logger.warning(f"Forecast log: {e}")
