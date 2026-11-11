"""Vehicle monitoring: follows each car and records its drives, charges and states.

Reading a car's data keeps it awake, so the monitor backs off when nothing
happens. A car that has been idle for a while is left alone for a window in
which only its cheap summary (online / asleep) is checked; the streaming
channel, which doesn't keep the car awake, reports when it moves or starts
drawing power in the meantime.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from . import vehicle_specs
from .config import KM_PER_MILE, Config
from .database import Database
from .places import Places
from .streaming import VehicleStream
from .tesla_api import RateLimited, TeslaApi, TeslaApiError, VehicleUnavailable
from .tesla_auth import AuthError, TeslaAuth

logger = logging.getLogger(__name__)

DRIVING_SHIFTS = ("D", "R", "N")
# A drive (or charge) survives this long without data: tunnels, garages, add-on restarts
DRIVE_GAP = 300
CHARGE_GAP = 900
# While parked a position is stored at most this often
PARKED_INTERVAL = 60
# With the stream delivering positions, the API only has to add temperatures and the like
POLL_DRIVING_STREAMED = 10
DISCOVERY_INTERVAL = 6 * 3600
# What the stream reports about the car; a poll that is older than the last streamed values doesn't undo them
STREAM_KEYS = ("lat", "lon", "heading", "speed_kmh", "power_kw", "odometer_km", "soc", "range_km", "est_range_km")

MODEL_NAMES = {
    "model3": "Model 3",
    "modely": "Model Y",
    "models": "Model S",
    "models2": "Model S",
    "lychee": "Model S",
    "modelx": "Model X",
    "tamarind": "Model X",
    "cybertruck": "Cybertruck",
}
# Rated consumption (Wh/km) until it has been learned from a few charging sessions
DEFAULT_EFFICIENCY = {"Model 3": 150.0, "Model Y": 165.0, "Model S": 185.0, "Model X": 210.0, "Cybertruck": 290.0}
FALLBACK_EFFICIENCY = 160.0


def _km(miles) -> float | None:
    return None if miles is None else round(float(miles) * KM_PER_MILE, 3)


def _seconds(millis, default: float) -> int:
    try:
        return int(float(millis) / 1000) if millis else int(default)
    except (TypeError, ValueError):
        return int(default)


def charge_power_kw(charge: dict) -> float | None:
    """Power drawn from the charger in kW, or None when the car doesn't report it.

    The API reports whole kilowatts. On AC, volts x amps x phases is more
    precise; some cars report a three-phase connection as 2 phases, so the
    phase count is picked that matches the reported power best.
    """
    reported = charge.get("charger_power")
    reported = None if reported is None else float(reported)
    volts, amps, phases = (charge.get(k) for k in ("charger_voltage", "charger_actual_current", "charger_phases"))
    if charge.get("fast_charger_present") or not (volts and amps and phases):
        return reported
    if reported is None:
        return volts * amps * phases / 1000.0
    candidates = {phases, 3} if phases == 2 else {phases}
    return min((volts * amps * n / 1000.0 for n in candidates), key=lambda kw: abs(kw - reported))


def _word(value) -> str | None:
    """A value Tesla reports as text, or None for its placeholders."""
    text = str(value).strip() if value is not None else ""
    return text if text and text not in ("<invalid>", "None", "Unknown") else None


def normalize(data: dict, now: float) -> dict:
    """Tesla's vehicle_data as one flat, metric snapshot of the car."""
    drive = data.get("drive_state") or {}
    charge = data.get("charge_state") or {}
    climate = data.get("climate_state") or {}
    # Doors, windows and the like are in closures_state on some cars, in vehicle_state on others
    vehicle = {**(data.get("closures_state") or {}), **(data.get("vehicle_state") or {})}
    config = data.get("vehicle_config") or {}
    gui = data.get("gui_settings") or {}
    update = vehicle.get("software_update") or {}
    speed_limit = vehicle.get("speed_limit_mode") or {}

    charging_state = charge.get("charging_state")
    doors = [vehicle.get(k) for k in ("df", "dr", "pf", "pr")]
    windows = [vehicle.get(k) for k in ("fd_window", "fp_window", "rd_window", "rp_window")]
    tpms = {
        wheel: vehicle.get(f"tpms_pressure_{wheel}")
        for wheel in ("fl", "fr", "rl", "rr")
        if vehicle.get(f"tpms_pressure_{wheel}") is not None
    }
    tpms_seen = [vehicle.get(f"tpms_last_seen_pressure_time_{wheel}") for wheel in ("fl", "fr", "rl", "rr")]
    return {
        "ts": _seconds(drive.get("timestamp") or vehicle.get("timestamp"), now),
        "name": vehicle.get("vehicle_name") or data.get("display_name") or "",
        "model": MODEL_NAMES.get(str(config.get("car_type") or "").lower(), config.get("car_type")),
        "car_type": config.get("car_type"),
        "trim": config.get("trim_badging"),
        "color": config.get("exterior_color"),
        "wheels": config.get("wheel_type"),
        # Extra build details Tesla exposes, shown only when present
        "roof_color": config.get("roof_color") or None,
        "exterior_trim": config.get("exterior_trim") or None,
        "charge_port_type": config.get("charge_port_type") or None,
        "air_suspension": config.get("has_air_suspension"),
        "spoiler_type": _word(config.get("spoiler_type")),
        "third_row_seats": _word(config.get("third_row_seats")),
        "right_hand_drive": config.get("rhd"),
        # How the car itself is set to show things
        "car_unit_distance": gui.get("gui_distance_units"),
        "car_unit_temperature": gui.get("gui_temperature_units"),
        "car_unit_charge_rate": gui.get("gui_charge_rate_units"),
        "car_unit_range": gui.get("gui_range_display"),
        "car_24h_time": gui.get("gui_24_hour_time"),
        # Driving
        "shift": drive.get("shift_state"),
        "speed_kmh": _km(drive.get("speed")) or 0.0,
        "power_kw": drive.get("power"),
        "lat": drive.get("latitude"),
        "lon": drive.get("longitude"),
        "heading": drive.get("heading"),
        "odometer_km": _km(vehicle.get("odometer")),
        "dest": drive.get("active_route_destination"),
        "dest_minutes": drive.get("active_route_minutes_to_arrival"),
        "dest_km": _km(drive.get("active_route_miles_to_arrival")),
        "dest_soc": drive.get("active_route_energy_at_arrival"),
        # Battery and charging
        "soc": charge.get("battery_level"),
        "usable_soc": charge.get("usable_battery_level"),
        "range_km": _km(charge.get("battery_range")),
        "est_range_km": _km(charge.get("est_battery_range")),
        "ideal_range_km": _km(charge.get("ideal_battery_range")),
        "charging_state": charging_state,
        "plugged_in": charging_state not in (None, "Disconnected"),
        "charge_limit_soc": charge.get("charge_limit_soc"),
        "charge_limit_min": charge.get("charge_limit_soc_min"),
        "charge_limit_max": charge.get("charge_limit_soc_max"),
        "charge_current_request": charge.get("charge_current_request"),
        "charge_current_max": charge.get("charge_current_request_max"),
        "pilot_current": charge.get("charger_pilot_current"),
        "cable": _word(charge.get("conn_charge_cable")),
        "charger_type": _word(charge.get("fast_charger_type")),
        "charge_port_latch": _word(charge.get("charge_port_latch")),
        "scheduled_charging_mode": _word(charge.get("scheduled_charging_mode")),
        "scheduled_departure_minutes": charge.get("scheduled_departure_time_minutes"),
        "preconditioning_enabled": charge.get("preconditioning_enabled"),
        "off_peak_enabled": charge.get("off_peak_charging_enabled"),
        "off_peak_end_minutes": charge.get("off_peak_hours_end_time"),
        "charger_power_kw": None if (power := charge_power_kw(charge)) is None else round(power, 2),
        "charger_voltage": charge.get("charger_voltage"),
        "charger_current": charge.get("charger_actual_current"),
        "charger_phases": charge.get("charger_phases"),
        "charge_energy_added_kwh": charge.get("charge_energy_added"),
        "charge_rate_kmh": _km(charge.get("charge_rate")),
        "minutes_to_full": charge.get("minutes_to_full_charge"),
        "charge_port_open": charge.get("charge_port_door_open"),
        "fast_charger": bool(charge.get("fast_charger_present")),
        "charger_brand": charge.get("fast_charger_brand") or None,
        "scheduled_charging_start": charge.get("scheduled_charging_start_time"),
        "battery_heater_on": charge.get("battery_heater_on"),
        "battery_heater": climate.get("battery_heater"),
        "battery_heater_no_power": climate.get("battery_heater_no_power"),
        "not_enough_power_to_heat": charge.get("not_enough_power_to_heat"),
        # Climate
        "inside_temp": climate.get("inside_temp"),
        "outside_temp": climate.get("outside_temp"),
        "climate_on": bool(climate.get("is_climate_on")),
        "preconditioning": bool(climate.get("is_preconditioning")),
        "target_temp": climate.get("driver_temp_setting"),
        "passenger_temp": climate.get("passenger_temp_setting"),
        "fan_status": climate.get("fan_status"),
        "front_defrost": climate.get("is_front_defroster_on"),
        "rear_defrost": climate.get("is_rear_defroster_on"),
        "seat_heater_left": climate.get("seat_heater_left"),
        "seat_heater_right": climate.get("seat_heater_right"),
        "seat_heater_rear_left": climate.get("seat_heater_rear_left"),
        "seat_heater_rear_center": climate.get("seat_heater_rear_center"),
        "seat_heater_rear_right": climate.get("seat_heater_rear_right"),
        "steering_wheel_heater": climate.get("steering_wheel_heater"),
        "mirror_heaters": climate.get("side_mirror_heaters"),
        "wiper_heater": climate.get("wiper_blade_heater"),
        "cabin_overheat": _word(climate.get("cabin_overheat_protection")),
        "climate_keeper": _word(climate.get("climate_keeper_mode")),
        # Vehicle
        "locked": vehicle.get("locked"),
        "sentry": bool(vehicle.get("sentry_mode")),
        "user_present": bool(vehicle.get("is_user_present")),
        "doors_open": any(doors),
        "windows_open": any(windows),
        "door_driver_front": bool(vehicle.get("df")),
        "door_driver_rear": bool(vehicle.get("dr")),
        "door_passenger_front": bool(vehicle.get("pf")),
        "door_passenger_rear": bool(vehicle.get("pr")),
        "window_driver_front": bool(vehicle.get("fd_window")),
        "window_driver_rear": bool(vehicle.get("rd_window")),
        "window_passenger_front": bool(vehicle.get("fp_window")),
        "window_passenger_rear": bool(vehicle.get("rp_window")),
        "sunroof_open_pct": vehicle.get("sun_roof_percent_open"),
        "valet_mode": vehicle.get("valet_mode"),
        "speed_limit_active": speed_limit.get("active"),
        "speed_limit_kmh": _km(speed_limit.get("current_limit_mph")),
        "remote_start": vehicle.get("remote_start"),
        "homelink_nearby": vehicle.get("homelink_nearby"),
        "dashcam": _word(vehicle.get("dashcam_state")),
        "tpms_warning_fl": vehicle.get("tpms_soft_warning_fl"),
        "tpms_warning_fr": vehicle.get("tpms_soft_warning_fr"),
        "tpms_warning_rl": vehicle.get("tpms_soft_warning_rl"),
        "tpms_warning_rr": vehicle.get("tpms_soft_warning_rr"),
        "tpms_seen": max((seen for seen in tpms_seen if seen), default=None),
        "frunk_open": bool(vehicle.get("ft")),
        "trunk_open": bool(vehicle.get("rt")),
        "tpms": tpms,
        "version": str(vehicle.get("car_version") or "").split(" ")[0] or None,
        "update_status": update.get("status") or None,
        "update_version": str(update.get("version") or "").strip() or None,
        "update_progress": update.get("install_perc"),
    }


def _flag(value) -> bool | None:
    """Tesla reports a defroster as off/on or as a level (front: 0 to 3, rear: 0 or 2): on or off is enough."""
    return None if value is None else bool(value)


def position_of(snapshot: dict) -> dict:
    """The part of a snapshot that is stored as a position."""
    keys = (
        "ts",
        "lat",
        "lon",
        "heading",
        "speed_kmh",
        "power_kw",
        "odometer_km",
        "soc",
        "usable_soc",
        "range_km",
        "est_range_km",
        "outside_temp",
        "inside_temp",
    )
    tpms = snapshot.get("tpms") or {}
    return {
        **{key: snapshot.get(key) for key in keys},
        "ideal_range_km": snapshot.get("ideal_range_km"),
        "driver_temp": snapshot.get("target_temp"),
        "passenger_temp": snapshot.get("passenger_temp"),
        "fan_status": snapshot.get("fan_status"),
        "climate_on": snapshot.get("climate_on"),
        "front_defrost": _flag(snapshot.get("front_defrost")),
        "rear_defrost": _flag(snapshot.get("rear_defrost")),
        "battery_heater": snapshot.get("battery_heater"),
        "battery_heater_on": snapshot.get("battery_heater_on"),
        "battery_heater_no_power": snapshot.get("battery_heater_no_power"),
        **{f"tpms_{wheel}": tpms.get(wheel) for wheel in ("fl", "fr", "rl", "rr")},
    }


def can_sleep(snapshot: dict) -> bool:
    """Whether nothing is going on that keeps the car awake by itself."""
    busy = (
        snapshot.get("shift") in DRIVING_SHIFTS
        or snapshot.get("climate_on")
        or snapshot.get("preconditioning")
        or snapshot.get("sentry")
        or snapshot.get("user_present")
        or snapshot.get("doors_open")
        or snapshot.get("update_status") in ("installing", "downloading")
    )
    return not busy


class CarMonitor:
    """Follows one vehicle."""

    def __init__(
        self,
        car: dict,
        config: Config,
        database: Database,
        auth: TeslaAuth,
        api: TeslaApi,
        places: Places,
        on_update: Callable[["CarMonitor"], None] | None = None,
    ):
        self.car_id: int = car["id"]
        self.tesla_id: int = car["tesla_id"]
        self.vin: str = car.get("vin") or ""
        self.config = config
        self.database = database
        self.auth = auth
        self.api = api
        self.places = places
        self.on_update = on_update

        # What is known of the car, kept across restarts so the dashboard is never empty
        self.snapshot: dict = database.load_snapshot(self.car_id)
        self.snapshot.setdefault("name", car.get("name") or "")
        # False: Tesla doesn't list this car on the account (a car from an import, a car no longer yours).
        # Its history stays, but there is nothing to ask Tesla about it. None: not known yet.
        self.on_account: bool | None = None
        self._car_row: dict = {}
        self._car_details = self._read_car_details()
        current = database.current_state(self.car_id)
        self.state: str = current["state"] if current else "unknown"
        self.state_since: int = current["start_ts"] if current else int(time.time())
        self.efficiency: float = (
            config.efficiency_wh_km
            or car.get("efficiency_wh_km")
            or DEFAULT_EFFICIENCY.get(car.get("model") or "", FALLBACK_EFFICIENCY)
        )

        self.drive_id: int | None = None
        self.charge_id: int | None = None
        self.last_seen: float = 0.0
        self.last_error: str = ""

        # "awake": reading data; "resting": idle, left alone to fall asleep;
        # "asleep": asleep or offline; "unknown": find out first
        self._mode = "unknown"
        self._rest_until = 0.0
        self._idle_since: float | None = None
        self._activity = False
        self._last_sample_at = 0.0
        self._last_parked_position = 0.0
        self._failures = 0
        # No request to Tesla before this moment (after a 429), whatever wakes the loop
        self._not_before = 0.0
        self._last_poll_at = 0.0
        # The odometer only goes up: a rounded streamed value or an older poll can't take it back
        self._odometer_km: float | None = self._known_odometer()
        # Seen unplugged since the last charge ended: charging again is a new session, not a resumed one
        self._unplugged_since_charge = False
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

        self.stream: VehicleStream | None = None
        if config.streaming and car.get("vehicle_id"):
            self.stream = VehicleStream(
                auth, car["vehicle_id"], self._on_stream_sample, driving=lambda: self.drive_id is not None
            )

    # ------------------------------------------------------------------
    # What the dashboard and MQTT see
    # ------------------------------------------------------------------
    def view(self) -> dict:
        snapshot = self.snapshot
        return {
            **snapshot,
            "id": self.car_id,
            "vin": self.vin,
            "state": self.state,
            "state_since": self.state_since,
            "drive_id": self.drive_id,
            "charge_id": self.charge_id,
            "last_seen": int(self.last_seen) or snapshot.get("ts"),
            "error": self.last_error,
            "streaming": bool(self.stream and self.stream.live),
            "efficiency_wh_km": self.efficiency,
            "on_account": self.on_account,
            **self._car_details,
            # Readable model/trim/colour/wheels, the paint colour and a picture of the car. A car that
            # never reported itself (imported) goes by what the import knew.
            **vehicle_specs.describe(
                snapshot.get("car_type"),
                snapshot.get("trim") or self._car_row.get("trim"),
                snapshot.get("color") or self._car_row.get("color"),
                snapshot.get("wheels") or self._car_row.get("wheels"),
                self.vin,
            ),
        }

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _changed(self) -> None:
        if self.on_update:
            try:
                self.on_update(self)
            except Exception:
                logger.exception("Update listener failed")

    def _read_car_details(self) -> dict:
        """What the car doesn't report itself; known from an import. The car's own spoiler report wins."""
        car = self.database.get_car(self.car_id) or {}
        self._car_row = car
        details = {key: car.get(key) for key in ("marketing_name", "lfp_battery", "free_supercharging")}
        details["spoiler_type"] = self.snapshot.get("spoiler_type") or _word(car.get("spoiler_type"))
        return details

    def refresh_details(self) -> None:
        self._car_details = self._read_car_details()
        self._changed()

    def poke(self) -> None:
        """Read the car's data now instead of at the next scheduled moment."""
        self._car_details = self._read_car_details()
        self._request_read()

    def _request_read(self) -> None:
        """Have the loop read the car's data soon (as soon as _hold_off allows)."""
        self._activity = True
        self._wake.set()

    async def wake_up(self) -> None:
        """Wake a sleeping car on request, then follow it again."""
        await self.api.wake_up(self.tesla_id)
        self._mode = "unknown"
        self._idle_since = None
        self._wake.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        self._resume()
        stream_task = asyncio.create_task(self.stream.run()) if self.stream else None
        try:
            while True:
                delay = await self._step()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                hold = self._hold_off(time.time())
                if hold > 0:
                    await asyncio.sleep(hold)
                self._wake.clear()
        finally:
            if stream_task:
                stream_task.cancel()

    def _hold_off(self, now: float) -> float:
        """How long to wait before asking Tesla again, whatever woke the loop. After a 429 Tesla's wait is
        kept; during a drive the stream can ask for a read with every frame, at most one per driving interval."""
        earliest = self._not_before
        if self.drive_id is not None:
            earliest = max(earliest, self._last_poll_at + self.config.poll_driving)
        return max(0.0, earliest - now)

    async def _step(self) -> float:
        """One round of the loop. Returns the seconds to wait before the next."""
        if self.on_account is False:
            if self.stream:
                self.stream.wanted.clear()
            if self.last_error:
                self.last_error = ""
                self._changed()
            return 3600
        try:
            delay = await self._poll()
        except AuthError as e:
            # Signed out: nothing to do until the user signs in again (which wakes us)
            self._fail(str(e))
            if self.stream:
                self.stream.wanted.clear()
            return 300
        except RateLimited as e:
            self._not_before = time.time() + e.retry_after
            self._fail(str(e))
            logger.warning(f"{self.label}: {e}")
            return e.retry_after
        except TeslaApiError as e:
            self._failures += 1
            self._fail(str(e))
            logger.warning(f"{self.label}: {e}")
            return min(15 * 2 ** min(self._failures, 5), 300)
        except Exception:
            logger.exception(f"{self.label}: unexpected error")
            self._fail("Internal error, see the add-on log")
            return 60
        self._failures = 0
        if self.last_error:
            self.last_error = ""
            self._changed()
        return delay

    def _fail(self, message: str) -> None:
        if message != self.last_error:
            self.last_error = message
            self._changed()

    @property
    def label(self) -> str:
        return self.snapshot.get("name") or self.vin or f"car {self.car_id}"

    async def _poll(self) -> float:
        now = time.time()
        self._last_poll_at = now
        if self._mode != "awake":
            # The summary is free: it doesn't wake the car or keep it awake
            summary = await self.api.vehicle(self.tesla_id)
            self.last_seen = now
            api_state = summary.get("state") or "offline"
            if api_state != "online":
                return self._went_away(api_state, now)
            if self._mode == "resting" and now < self._rest_until and not self._activity:
                return self.config.poll_asleep

        try:
            data = await self.api.vehicle_data(self.tesla_id)
        except VehicleUnavailable:
            # Fell asleep, or out of reach for a moment: the summary tells which
            self._mode = "unknown"
            return self.config.poll_driving if self.drive_id else 5
        self.last_seen = now
        self._mode = "awake"
        self._activity = False
        return self._process(normalize(data, now), now)

    def _went_away(self, api_state: str, now: float) -> float:
        """The car is asleep or offline."""
        if self.drive_id and now - self._last_sample_at < DRIVE_GAP:
            # Probably a tunnel or a parking garage: keep the drive open for a while
            self._mode = "unknown"
            return self.config.poll_driving * 2
        if self.charge_id and now - self._last_sample_at < CHARGE_GAP:
            self._mode = "unknown"
            return self.config.poll_charging
        if self.drive_id:
            self._end_drive()
        if self.charge_id:
            self._end_charge()
        self._mode = "asleep"
        self._idle_since = None
        self._activity = False
        if self.stream:
            self.stream.wanted.clear()
        if self._set_state("asleep" if api_state == "asleep" else "offline", int(now)):
            self._changed()
        return self.config.poll_asleep

    def _known_odometer(self) -> float | None:
        """The highest odometer reading known: the saved snapshot's or the last stored position's."""
        row = self.database.query_one(
            "SELECT odometer_km FROM positions WHERE car_id = ? AND odometer_km IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (self.car_id,),
        )
        known = [v for v in (self.snapshot.get("odometer_km"), row and row["odometer_km"]) if v is not None]
        return max(known, default=None)

    def _odometer(self, value: float | None) -> float | None:
        """The odometer to store and show: never below the last one (Home Assistant counts it as a meter)."""
        if value is None:
            return self._odometer_km
        if self._odometer_km is not None and value < self._odometer_km:
            return self._odometer_km
        self._odometer_km = value
        return value

    def _keep_newer_stream_values(self, snapshot: dict) -> None:
        """A poll can be older than the frames the stream sent meanwhile: keep what those said."""
        previous = self.snapshot
        if snapshot["ts"] < (previous.get("ts") or 0):
            for key in STREAM_KEYS:
                if previous.get(key) is not None:
                    snapshot[key] = previous[key]
            snapshot["ts"] = previous["ts"]

    def _process(self, snapshot: dict, now: float) -> float:
        """Handle fresh data of an awake car. Returns the seconds until the next read."""
        if self.drive_id is not None and snapshot["shift"] in DRIVING_SHIFTS:
            self._keep_newer_stream_values(snapshot)
        snapshot["odometer_km"] = self._odometer(snapshot["odometer_km"])
        sample = position_of(snapshot)
        driving = snapshot["shift"] in DRIVING_SHIFTS
        charging = not driving and (
            snapshot["charging_state"] == "Charging" or (self.charge_id and snapshot["charging_state"] == "Starting")
        )

        if driving:
            if self.charge_id:
                self._end_charge()
            if not self.drive_id:
                self._start_drive(sample)
            self.database.add_position(self.car_id, sample, self.drive_id)
            self._last_sample_at = now
        elif self.drive_id:
            self.database.add_position(self.car_id, sample, self.drive_id)
            self._end_drive()

        if charging:
            if not self.charge_id:
                self._start_charge(snapshot, sample)
            self.database.add_charge_sample(
                self.charge_id,
                {
                    "ts": int(now),
                    "soc": snapshot["soc"],
                    "power_kw": snapshot["charger_power_kw"],
                    "voltage": snapshot["charger_voltage"],
                    "current": snapshot["charger_current"],
                    "phases": snapshot["charger_phases"],
                    "energy_added_kwh": snapshot["charge_energy_added_kwh"],
                    "range_km": snapshot["range_km"],
                    "outside_temp": snapshot["outside_temp"],
                    "usable_soc": snapshot["usable_soc"],
                    "ideal_range_km": snapshot["ideal_range_km"],
                    "pilot_current": snapshot["pilot_current"],
                    "requested_current": snapshot["charge_current_request"],
                    "battery_heater": snapshot["battery_heater"],
                    "battery_heater_on": snapshot["battery_heater_on"],
                    "battery_heater_no_power": snapshot["battery_heater_no_power"],
                    "not_enough_power_to_heat": snapshot["not_enough_power_to_heat"],
                },
            )
            self.database.note_charge_hardware(self.charge_id, snapshot["cable"], snapshot["charger_type"])
            self._last_sample_at = now
        elif self.charge_id:
            self._end_charge()
        if not snapshot["plugged_in"]:
            self._unplugged_since_charge = True

        if snapshot["update_status"] == "installing":
            state = "updating"
        else:
            state = "driving" if driving else "charging" if charging else "online"
        state_changed = self._set_state(state, int(now))
        if not driving and (state_changed or now - self._last_parked_position >= PARKED_INTERVAL):
            self.database.add_position(self.car_id, sample)
            self._last_parked_position = now

        self._remember_car(snapshot)
        if snapshot["version"] and self.database.record_version(self.car_id, snapshot["version"], int(now)):
            logger.info(f"{self.label}: software version {snapshot['version']}")
        # The stream knows the elevation, the API doesn't
        snapshot["elevation_m"] = self.snapshot.get("elevation_m") if driving else None
        self.snapshot = snapshot
        self.database.save_snapshot(self.car_id, snapshot)
        if self.stream:
            self.stream.wanted.set()
        self._changed()

        if state == "online" and can_sleep(snapshot):
            self._idle_since = self._idle_since or now
            if now - self._idle_since >= self.config.idle_minutes * 60:
                # Idle long enough: stop reading its data so it can fall asleep
                self._mode = "resting"
                self._rest_until = now + self.config.sleep_window_minutes * 60
                logger.debug(f"{self.label}: idle, leaving it alone to fall asleep")
                return self.config.poll_asleep
        else:
            self._idle_since = None

        if driving:
            streamed = self.stream and self.stream.live
            return max(self.config.poll_driving, POLL_DRIVING_STREAMED) if streamed else self.config.poll_driving
        return self.config.poll_charging if charging else self.config.poll_online

    def _on_stream_sample(self, sample: dict) -> None:
        """A position pushed by the car over the streaming channel."""
        driving = sample.pop("shift", None) in DRIVING_SHIFTS
        if self.drive_id:
            if not driving:
                # Parked (or a frame without a gear): read the full data to close the drive. The stream
                # can send such frames every second; the loop reads at most once per driving interval.
                self._request_read()
                return
            if sample.get("lat") is None:
                return
            sample["odometer_km"] = self._odometer(sample.get("odometer_km"))
            self.database.add_position(self.car_id, sample, self.drive_id)
            self._last_sample_at = time.time()
            self.last_seen = self._last_sample_at
            self.snapshot.update({k: v for k, v in sample.items() if v is not None})
            self._changed()
        elif driving or (self._mode == "resting" and sample.get("power_kw")):
            # Driving off, or drawing power while we weren't looking (charging, climate)
            self._request_read()

    # ------------------------------------------------------------------
    # Drives, charges, states
    # ------------------------------------------------------------------
    def _set_state(self, state: str, ts: int) -> bool:
        if state == self.state and self.database.current_state(self.car_id):
            return False
        self.database.set_state(self.car_id, state, ts)
        logger.info(f"{self.label}: {self.state} -> {state}")
        self.state = state
        self.state_since = ts
        return True

    def _start_drive(self, sample: dict) -> None:
        self.drive_id = self.database.open_drive(self.car_id, sample)
        logger.info(f"{self.label}: drive {self.drive_id} started")

    def _end_drive(self) -> None:
        drive_id, self.drive_id = self.drive_id, None
        drive = self.database.close_drive(drive_id, self.efficiency)
        if not drive:
            logger.debug(f"{self.label}: drive {drive_id} too short, dropped")
            return
        logger.info(
            f"{self.label}: drive {drive_id} ended: {drive['distance_km']:.1f} km in {drive['duration_s'] // 60} min"
        )
        self._background(self.places.fill_missing())

    def _start_charge(self, snapshot: dict, sample: dict) -> None:
        if not self._unplugged_since_charge:
            # Charging again after a pause, still plugged in: the same session, the car's counter runs on
            resumed = self.database.resumable_charge(self.car_id, sample, snapshot["charge_energy_added_kwh"])
            if resumed:
                self.database.reopen_charge(resumed)
                self.charge_id = resumed
                logger.info(f"{self.label}: charge {resumed} resumed at {snapshot['soc']}%")
                return
        kind = "DC" if snapshot["fast_charger"] else "AC"
        self.charge_id = self.database.open_charge(self.car_id, sample, kind, snapshot["charger_brand"])
        logger.info(f"{self.label}: {kind} charge {self.charge_id} started at {snapshot['soc']}%")

    def _end_charge(self) -> None:
        charge_id, self.charge_id = self.charge_id, None
        self._unplugged_since_charge = False
        charge = self.database.close_charge(charge_id, self.config.cost_per_kwh)
        if not charge:
            logger.debug(f"{self.label}: charge {charge_id} added nothing, dropped")
            return
        logger.info(
            f"{self.label}: charge {charge_id} ended: {charge['energy_added_kwh']:.1f} kWh added,"
            f" {charge['start_soc']}% -> {charge['end_soc']}%"
        )
        self._learn_efficiency()
        self._background(self.places.fill_missing())

    def _learn_efficiency(self) -> None:
        """Update the car's rated consumption from its charging sessions (unless set in the options)."""
        if self.config.efficiency_wh_km:
            return
        learned = self.database.learn_efficiency(self.car_id)
        if learned and abs(learned - self.efficiency) >= 0.1:
            logger.info(f"{self.label}: rated consumption {learned} Wh/km (was {self.efficiency})")
            self.efficiency = learned
            self.database.apply_efficiency(self.car_id, learned)

    def _remember_car(self, snapshot: dict) -> None:
        """Keep the car's name and configuration in the database up to date."""
        fields = {key: snapshot.get(key) for key in ("name", "model", "trim", "color", "wheels")}
        if any(value and value != self.snapshot.get(key) for key, value in fields.items()):
            self.database.update_car(self.car_id, **{k: v for k, v in fields.items() if v})
            if not self.config.efficiency_wh_km and not self.database.get_car(self.car_id)["efficiency_wh_km"]:
                self.efficiency = DEFAULT_EFFICIENCY.get(fields["model"] or "", FALLBACK_EFFICIENCY)

    def _resume(self) -> None:
        """After a restart: continue a drive or charge that was going on, close the stale ones."""
        now = time.time()
        if self.config.efficiency_wh_km:
            self.database.apply_efficiency(self.car_id, self.config.efficiency_wh_km)
        for drive in self.database.open_drives(self.car_id):
            last = self.database.query_one("SELECT MAX(ts) AS ts FROM positions WHERE drive_id = ?", (drive["id"],))
            if not self.drive_id and last["ts"] and now - last["ts"] < DRIVE_GAP:
                self.drive_id = drive["id"]
                self._last_sample_at = last["ts"]
                logger.info(f"{self.label}: continuing drive {drive['id']}")
            else:
                self.database.close_drive(drive["id"], self.efficiency)
        for charge in self.database.open_charges(self.car_id):
            last = self.database.query_one(
                "SELECT MAX(ts) AS ts FROM charge_samples WHERE charge_id = ?", (charge["id"],)
            )
            if not self.charge_id and last["ts"] and now - last["ts"] < CHARGE_GAP:
                self.charge_id = charge["id"]
                self._last_sample_at = last["ts"]
                logger.info(f"{self.label}: continuing charge {charge['id']}")
            else:
                self.database.close_charge(charge["id"], self.config.cost_per_kwh)


class Garage:
    """The vehicles of the account: finds them and runs a monitor for each."""

    def __init__(self, config: Config, database: Database, auth: TeslaAuth, api: TeslaApi, places: Places):
        self.config = config
        self.database = database
        self.auth = auth
        self.api = api
        self.places = places
        self.monitors: dict[int, CarMonitor] = {}
        self.listeners: list[Callable[[CarMonitor], None]] = []
        self.removal_listeners: list[Callable[[int, str], None]] = []
        self.last_error = ""
        self._tasks: dict[int, asyncio.Task] = {}
        self._account_vins: set[str] | None = None

    def follow_new_cars(self) -> None:
        """Show the cars that came in through an import right away, not after the next restart."""
        for car in self.database.list_cars():
            if car["id"] not in self.monitors:
                self._add(car)

    async def remove(self, car_id: int) -> None:
        """Stop following a car and delete it with its history."""
        monitor = self.monitors.pop(car_id, None)
        task = self._tasks.pop(car_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(self.database.remove_car, car_id)
        for listener in self.removal_listeners:
            listener(car_id, monitor.vin if monitor else "")

    def _notify(self, monitor: CarMonitor) -> None:
        for listener in self.listeners:
            listener(monitor)

    def views(self) -> list[dict]:
        return [monitor.view() for monitor in self.monitors.values()]

    def _add(self, car: dict) -> None:
        monitor = CarMonitor(car, self.config, self.database, self.auth, self.api, self.places, self._notify)
        if self._account_vins is not None:
            monitor.on_account = monitor.vin in self._account_vins
        self.monitors[car["id"]] = monitor
        self._tasks[car["id"]] = asyncio.create_task(monitor.run())

    async def run(self) -> None:
        """Run until cancelled."""
        # Cars seen before are followed (and shown) right away
        for car in self.database.list_cars():
            self._add(car)
        try:
            while True:
                self.auth.changed.clear()
                wait = DISCOVERY_INTERVAL
                if self.auth.signed_in:
                    try:
                        await self._discover()
                        self.last_error = ""
                    except (AuthError, TeslaApiError) as e:
                        self.last_error = str(e)
                        logger.warning(f"Could not list the account's vehicles: {e}")
                        wait = 120
                    for monitor in self.monitors.values():
                        monitor.poke()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.auth.changed.wait(), timeout=wait)
        finally:
            for task in self._tasks.values():
                task.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _discover(self) -> None:
        """Start following vehicles of the account that aren't followed yet, and note which aren't on it."""
        vehicles = await self.api.vehicles()
        # An empty list says more about the answer than about the account: leave things as they are
        if vehicles:
            self._account_vins = {vehicle.get("vin") for vehicle in vehicles}
            for monitor in self.monitors.values():
                monitor.on_account = monitor.vin in self._account_vins
                monitor.refresh_details()
        for vehicle in vehicles:
            self.database.forget_removal(vehicle.get("vin"))
            car_id = self.database.upsert_car(
                vehicle["id"], vehicle.get("vehicle_id"), vehicle.get("vin"), vehicle.get("display_name")
            )
            if car_id not in self.monitors:
                logger.info(f"Following {vehicle.get('display_name') or vehicle.get('vin')}")
                self._add(self.database.get_car(car_id))
