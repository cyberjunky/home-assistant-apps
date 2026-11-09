"""A stand-in for a GoodWe ES inverter, for developing the dashboard without hardware.

    GOODWE_MOCK_INVERTER=tools.mock_inverter GOODWE_DATA_DIR=/tmp/goodwe python3 -m src

It simulates a sunny day on fast-forward (one simulated day per real hour by default,
MOCK_SPEED sets the factor), a house with a varying load, and a battery that charges
from the surplus and discharges at night, following the DoD, charge current and export
limit that are written to it, so the controller's behaviour can be watched.
"""

import math
import os
import random
import time
from datetime import datetime

SPEED = float(os.environ.get("MOCK_SPEED", "24"))
CAPACITY_WH = 10000.0


def connect():
    return ES()


class ES:
    """Mimics the goodwe library's ES class: the attributes and methods the add-on uses."""

    model_name = "GW3648D-ES"
    serial_number = "9536KESU000MOCK1"
    firmware = "2626G"
    arm_firmware = "V1.0.9 (ARM)"
    arm_version = 22
    dsp1_version = 26
    rated_power = 3648

    def __init__(self):
        self.started = time.time()
        self.soc = 55.0
        self.settings = {
            "dod": 80,
            "grid_export_limit": 6000,
            "grid_export": 1,
            "charge_v": 53.0,
            "charge_i": 50,
            "discharge_v": 44.0,
            "discharge_i": 25,
            "capacity": 200,
            "work_mode": 0,
            "backup_supply": 1,
            "battery_soc_protection": 10,
            "shadow_scan": 0,
            "power_factor": 0,
            "eco_mode_1": "Off",
            "fast_charging": 0,
            "fast_charging_soc": 90,
        }
        self._last = time.time()
        self.e_day = 0.0
        self.day_of = self._now().date()
        self.e_total = 12345.6
        self.e_total_imp = 2345.6
        self.e_total_exp = 456.7
        self.h_total = 24680
        self.errors = 0

    def _now(self) -> datetime:
        elapsed = (time.time() - self.started) * SPEED
        return datetime.fromtimestamp(self.started + elapsed)

    async def read_runtime_data(self) -> dict:
        now = self._now()
        hour = now.hour + now.minute / 60
        dt_h = (time.time() - self._last) * SPEED / 3600
        self._last = time.time()
        if now.date() != self.day_of:
            self.day_of, self.e_day = now.date(), 0.0

        daylight = max(0.0, math.sin((hour - 6) / 13 * math.pi)) if 6 <= hour <= 19 else 0.0
        cloud = 0.75 + 0.25 * math.sin(hour * 2.1 + now.day)
        potential = 3600 * daylight * cloud
        load = 350 + 250 * (1 + math.sin(hour / 24 * 2 * math.pi * 2 + 1)) + random.uniform(-40, 40)
        if 17 <= hour <= 22:
            load += 900
        work_mode = self.settings["work_mode"]
        held = self.settings["dod"] == 0
        floor = 100 - self.settings["dod"]
        charge_allowed = self.settings["charge_i"] > 0 and self.soc < 100
        export_limit = self.settings["grid_export_limit"] if self.settings["grid_export"] else 1e9

        surplus = potential - load
        pbat = 0.0
        if surplus > 0:
            if charge_allowed:
                pbat = min(surplus, 2500 * (1 if self.soc < 95 else 0.3))
            grid = -(surplus - pbat)
            if -grid > export_limit:
                curtail = -grid - export_limit
                potential -= curtail
                grid = -export_limit
        else:
            need = -surplus
            if not held and self.soc > floor and work_mode != 1:
                pbat = -min(need, 1500)
            grid = need + pbat
        self.soc = max(0.0, min(100.0, self.soc + pbat * dt_h / CAPACITY_WH * 100))
        if self.soc >= 100:
            pbat = max(0.0, pbat * 0.05)
        # Like the real ES: the inverter's output, so without what goes into the battery and with what
        # comes out of it
        self.e_day += max(potential - pbat, 0) * dt_h / 1000
        self.e_total += potential * dt_h / 1000
        self.e_total_imp += max(grid, 0) * dt_h / 1000
        self.e_total_exp += max(-grid, 0) * dt_h / 1000
        self.h_total += dt_h
        vbat = 48 + self.soc / 100 * 6
        return {
            "timestamp": now,
            "vpv1": 240 + daylight * 60 if potential else 0,
            "ipv1": potential * 0.55 / 300 if potential else 0,
            "ppv1": round(potential * 0.55),
            "pv1_mode_label": "PV panels connected, producing power" if potential else "PV panels not connected",
            "vpv2": 235 + daylight * 60 if potential else 0,
            "ipv2": potential * 0.45 / 295 if potential else 0,
            "ppv2": round(potential * 0.45),
            "pv2_mode_label": "PV panels connected, producing power" if potential else "PV panels not connected",
            "ppv": round(potential),
            "vbattery1": round(vbat, 1),
            "battery_status": 1,
            "battery_temperature": 24.5 + abs(pbat) / 1000,
            "ibattery1": round(-pbat / vbat, 1),
            "pbattery1": round(-pbat),  # the ES reports charging as negative
            "battery_charge_limit": 50,
            "battery_discharge_limit": 25,
            "battery_error": 0,
            "battery_soc": round(self.soc),
            "battery_soh": 98,
            "battery_mode": 2 if pbat > 10 else 3 if pbat < -10 else 1,
            "battery_mode_label": "Charge" if pbat > 10 else "Discharge" if pbat < -10 else "No battery or standby",
            "battery_warning": 0,
            "meter_status": 1,
            "vgrid": 231.5 + random.uniform(-1, 1),
            "igrid": abs(grid) / 231,
            "pgrid": round(-grid),  # the ES reports import as negative
            "fgrid": 50.0 + random.uniform(-0.02, 0.02),
            "grid_mode_label": "Inverter On",
            "vload": 231.0,
            "iload": 0.3,
            "pload": round(load),
            "fload": 50.0,
            "load_mode_label": "The inverter is connected to a load",
            "work_mode": 1,
            # Like the ES: the runtime label is the energy mode; the work mode itself is only in the settings
            "work_mode_label": "Normal (Off-Grid)" if work_mode == 1 else "Normal (On-Grid)",
            "temperature": 36.0 + potential / 400,
            "error_codes": 0,
            "e_total": round(self.e_total, 1),
            "h_total": int(self.h_total),
            "e_day": round(self.e_day, 2),
            "e_load_day": 0,
            "e_load_total": 0,
            "total_power": round(potential - load),
            "effective_work_mode": work_mode,
            "grid_in_out": 1 if grid > 0 else 2 if grid < 0 else 0,
            "grid_in_out_label": "Importing from grid" if grid > 0 else "Exporting to grid" if grid < 0 else "Idle",
            "pback_up": 0,
            "plant_power": round(potential - pbat),
            "house_consumption": round(load),
            "diagnose_result": 0,
            "diagnose_result_label": "",
            "e_total_exp": 0,
            "e_total_imp": 0,
            "e_bat_charge_total": 0,
            "e_bat_discharge_total": 0,
        }

    async def read_settings_data(self) -> dict:
        return dict(self.settings)

    async def set_operation_mode(self, mode, eco_mode_power=100, eco_mode_soc=100) -> None:
        if int(mode) > 3:
            from goodwe import InverterError

            raise InverterError("Operation not supported.")
        self.settings["work_mode"] = int(mode)

    async def set_ongrid_battery_dod(self, dod: int) -> None:
        self.settings["dod"] = int(dod)

    async def set_grid_export_limit(self, watts: int) -> None:
        self.settings["grid_export_limit"] = int(watts)

    async def _read_from_socket(self, command):
        payload = command.request.hex()
        if payload.startswith("aa55c07f035301"):
            self.settings["grid_export"] = int(payload[14:16], 16)
        elif payload.startswith("aa55c07f035104"):
            self.settings["charge_v"] = int(payload[14:18], 16) / 10
            self.settings["charge_i"] = int(payload[18:22], 16)
        return None
