"""Import a user's existing history from another logger's PostgreSQL database.

An optional, one-off interoperability tool. The user gives the database
connection once; TeslaMeter reads its drives, positions, charging sessions,
states and software versions straight from it and adds them, matching cars by
VIN. It reads only, never writes to that database. The values are stored in
metric km, as TeslaMeter stores them, so they carry over directly.

The source database's table layout is used purely as the interchange format.
"""

import bisect
import itertools
import logging
import time
from datetime import UTC

from . import vehicle_specs
from .database import (
    MIN_CHARGE_KWH,
    MIN_DRIVE_KM,
    POSITION_EXTRA_COLUMNS,
    SAMPLE_COLUMNS,
    SAMPLE_EXTRA_COLUMNS,
    STORED_POSITION_COLUMNS,
    Database,
)

logger = logging.getLogger(__name__)

# Insert positions and charge samples this many drives/sessions at a time
BATCH = 200
# Two rows count as the same session if their start is within this many seconds (re-import safety)
DEDUP_WINDOW = 120
DEFAULT_EFFICIENCY = 160.0
# Parked positions are read this many at a time
PARKED_PAGE = 50000

# Our column: the source's. Not every version of the source has all of them.
SOURCE_POSITION_COLUMNS = {
    "ideal_range_km": "ideal_battery_range_km",
    "driver_temp": "driver_temp_setting",
    "passenger_temp": "passenger_temp_setting",
    "fan_status": "fan_status",
    "climate_on": "is_climate_on",
    "front_defrost": "is_front_defroster_on",
    "rear_defrost": "is_rear_defroster_on",
    "battery_heater": "battery_heater",
    "battery_heater_on": "battery_heater_on",
    "battery_heater_no_power": "battery_heater_no_power",
    "tpms_fl": "tpms_pressure_fl",
    "tpms_fr": "tpms_pressure_fr",
    "tpms_rl": "tpms_pressure_rl",
    "tpms_rr": "tpms_pressure_rr",
}
SOURCE_SAMPLE_COLUMNS = {
    "usable_soc": "usable_battery_level",
    "ideal_range_km": "ideal_battery_range_km",
    "pilot_current": "charger_pilot_current",
    "battery_heater": "battery_heater",
    "battery_heater_on": "battery_heater_on",
    "battery_heater_no_power": "battery_heater_no_power",
    "not_enough_power_to_heat": "not_enough_power_to_heat",
}


class HistoryImportError(Exception):
    """The database could not be reached, or doesn't have the expected tables."""


def _epoch(value) -> int | None:
    """A datetime from pg8000 as unix seconds.

    The source stores times as naive UTC (`timestamp without time zone`), which
    pg8000 returns without a timezone. `datetime.timestamp()` on a naive value
    assumes the *container's* local timezone, so it would shift every imported row
    by the local UTC offset (and by DST across the year) — enough to push a session
    hours away from the same session in Tesla's own charging history and defeat the
    de-duplicating match. Pin a naive value to UTC first; leave an aware one as is.
    """
    try:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return int(value.timestamp())
    except (AttributeError, TypeError, ValueError):
        return None


def _f(value) -> float | None:
    """A Decimal/number from pg8000 as float, or None."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def connect(params: dict):
    """Open a connection to the source database. Raises HistoryImportError on failure."""
    try:
        import pg8000.dbapi
    except ImportError as e:  # pragma: no cover - the add-on image ships it
        raise HistoryImportError("the PostgreSQL driver is not installed") from e
    try:
        return pg8000.dbapi.connect(
            host=str(params.get("host") or "").strip(),
            port=int(params.get("port") or 5432),
            database=str(params.get("database") or "").strip(),
            user=str(params.get("user") or "").strip(),
            password=str(params.get("password") or "") or None,
            timeout=20,
            ssl_context=True if params.get("ssl") else None,
        )
    except Exception as e:
        raise HistoryImportError(f"cannot connect to the database: {e}") from e


_CARS = "SELECT id, vin, name, model, trim_badging, exterior_color, wheel_type, eid, vid FROM cars"

_DRIVES = """
SELECT d.id, d.start_date, d.end_date,
       COALESCE(d.distance::float8, CASE WHEN d.end_date IS NULL THEN
           (SELECT MAX(p.odometer) - MIN(p.odometer) FROM positions p WHERE p.drive_id = d.id) END), d.duration_min, d.speed_max, d.power_max, d.power_min,
       d.outside_temp_avg, d.inside_temp_avg, d.start_km, d.end_km, {ascent}, {descent},
       d.start_rated_range_km, d.end_rated_range_km, d.start_ideal_range_km, d.end_ideal_range_km,
       sp.latitude, sp.longitude, sp.battery_level, ep.latitude, ep.longitude, ep.battery_level,
       COALESCE(sg.name, sa.display_name), COALESCE(eg.name, ea.display_name)
FROM drives d
LEFT JOIN positions sp ON sp.id = d.start_position_id
LEFT JOIN positions ep ON ep.id = d.end_position_id
LEFT JOIN addresses sa ON sa.id = d.start_address_id
LEFT JOIN addresses ea ON ea.id = d.end_address_id
LEFT JOIN geofences sg ON sg.id = d.start_geofence_id
LEFT JOIN geofences eg ON eg.id = d.end_geofence_id
WHERE d.car_id = %s ORDER BY d.start_date
"""

_POSITIONS = """
SELECT date, latitude, longitude, elevation, speed, power, odometer, battery_level, usable_battery_level,
       rated_battery_range_km, est_battery_range_km, outside_temp, inside_temp, {extras}
FROM positions WHERE drive_id = %s ORDER BY date, id
"""

# The values added later to a drive that is here already: only the positions that have any
_POSITION_EXTRAS = "SELECT date, {extras} FROM positions WHERE drive_id = %s AND ({any_extra}) ORDER BY date, id"

_PARKED = """
SELECT id, date, latitude, longitude, elevation, speed, power, odometer, battery_level, usable_battery_level,
       rated_battery_range_km, est_battery_range_km, outside_temp, inside_temp, {extras}
FROM positions WHERE car_id = %s AND drive_id IS NULL AND id > %s ORDER BY id LIMIT %s
"""

_CHARGES = """
SELECT cp.id, cp.start_date, cp.end_date, cp.charge_energy_added, cp.charge_energy_used,
       cp.start_battery_level, cp.end_battery_level, cp.start_rated_range_km, cp.end_rated_range_km,
       cp.duration_min, cp.outside_temp_avg, cp.cost, p.latitude, p.longitude, p.odometer,
       COALESCE(g.name, a.display_name),
       (SELECT MAX(charger_power) FROM charges c WHERE c.charging_process_id = cp.id),
       (SELECT bool_or(fast_charger_present) FROM charges c WHERE c.charging_process_id = cp.id),
       (SELECT MAX(fast_charger_brand) FROM charges c WHERE c.charging_process_id = cp.id),
       (SELECT MAX(conn_charge_cable) FROM charges c
        WHERE c.charging_process_id = cp.id AND conn_charge_cable <> '<invalid>'),
       (SELECT MAX(fast_charger_type) FROM charges c
        WHERE c.charging_process_id = cp.id AND fast_charger_type <> '<invalid>'),
       cp.start_ideal_range_km, cp.end_ideal_range_km,
       CASE WHEN cp.end_date IS NULL THEN
           (SELECT MAX(charge_energy_added) - MIN(charge_energy_added) FROM charges c WHERE c.charging_process_id = cp.id)
       END
FROM charging_processes cp
LEFT JOIN positions p ON p.id = cp.position_id
LEFT JOIN addresses a ON a.id = cp.address_id
LEFT JOIN geofences g ON g.id = cp.geofence_id
WHERE cp.car_id = %s ORDER BY cp.start_date
"""

_SAMPLES = """
SELECT date, battery_level, charger_power, charger_voltage, charger_actual_current, charger_phases,
       charge_energy_added, rated_battery_range_km, outside_temp, {extras}
FROM charges WHERE charging_process_id = %s ORDER BY date, id
"""

_STATES = "SELECT state, start_date, end_date FROM states WHERE car_id = %s ORDER BY start_date"
_UPDATES = "SELECT version, start_date FROM updates WHERE car_id = %s AND version IS NOT NULL ORDER BY start_date"


def import_all(params: dict, database: Database, progress: dict, cost_per_kwh: float = 0.0) -> None:
    """Import everything from the source database into TeslaMeter. Updates `progress` as it goes.

    `cost_per_kwh` prices the sessions the source never finished, which have no cost of their own."""
    progress.update(phase="connecting", message="Connecting to the database…")
    connection = connect(params)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT to_regclass('drives'), to_regclass('cars'), to_regclass('charging_processes')")
        if not all(cursor.fetchone()):
            raise HistoryImportError("the expected tables weren't found - check the database name and host")
        cursor.execute(_CARS)
        cars = cursor.fetchall()
        progress["message"] = f"Found {len(cars)} car(s) in the source database"
        for car in cars:
            _import_car(cursor, database, car, progress, cost_per_kwh)
        progress.update(phase="done", message="Import finished", running=False, finished_at=int(time.time()))
        logger.info(
            f"History import finished: {progress['drives']} drives, {progress['charges']} charges,"
            f" {progress['positions']} positions added, {progress['updated']} rows filled in"
        )
    finally:
        connection.close()


def _import_car(cursor, database: Database, car, progress: dict, cost_per_kwh: float) -> None:
    tm_id, vin, name, _model, trim, color, wheels, eid, vid = car
    if not vin:
        return
    # Match the car by VIN, or create it (so history imports before the account is linked too).
    # eid is Tesla's id; a synthetic one is used only if the source never recorded it.
    car_id = database.upsert_car(eid or (900_000_000_000 + tm_id), vid, vin, name)
    database.update_car(car_id, model=vehicle_specs.model_name(None, vin), trim=trim, color=color, wheels=wheels)
    efficiency = (database.get_car(car_id) or {}).get("efficiency_wh_km") or DEFAULT_EFFICIENCY
    progress["message"] = f"Importing {name or vin}…"

    _import_car_details(cursor, database, car_id, tm_id)
    drive_map, known_drives = _import_drives(cursor, database, car_id, efficiency, progress)
    _import_positions(cursor, database, car_id, drive_map, progress)
    _fill_positions(cursor, database, known_drives, progress)
    _import_parked_positions(cursor, database, car_id, tm_id, progress)
    _finish_drives(database, car_id, efficiency, progress)
    progress["message"] = f"Mapping the drives of {name or vin} for the Stats map…"
    database.map_missing_cells(pause=0.0)
    _import_charges(cursor, database, car_id, progress)
    _finish_charges(database, car_id, cost_per_kwh, progress)
    _import_states(cursor, database, car_id, progress)
    _import_updates(cursor, database, car_id)

    # Now that charges are in, learn the real consumption and recompute every drive's energy with it
    learned = database.learn_efficiency(car_id)
    if learned:
        database.apply_efficiency(car_id, learned)


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        (table, column),
    )
    return cursor.fetchone() is not None


def _table_columns(cursor, table: str) -> set[str]:
    cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    return {row[0] for row in cursor.fetchall()}


def _select_extras(ours: tuple[str, ...], source: dict[str, str], available: set[str]) -> str:
    """The source's columns for our extra columns, in our order; NULL for those it doesn't have."""
    return ", ".join(source[c] if source.get(c) in available else "NULL" for c in ours)


def _match_existing(existing: list[dict], starts: list[int], window: int) -> dict[int, int]:
    """Which rows already here are the source's sessions: {source start: our id}, at most one each.

    The same start is the same session: imported before. What remains can be a session TeslaMeter recorded
    itself, which starts a little differently, so the closest within `window` counts. One row can't stand
    for two: the source can log one trip in several pieces, moments apart, that are each their own."""
    free = {row["start_ts"]: row["id"] for row in existing}
    matches = {ts: free.pop(ts) for ts in starts if ts in free}
    times = sorted(free)
    for ts in starts:
        if ts in matches or not times:
            continue
        at = bisect.bisect_left(times, ts)
        closest = min(times[max(0, at - 1) : at + 1], key=lambda t: abs(t - ts))
        if abs(closest - ts) < window:
            matches[ts] = free[closest]
            times.remove(closest)
    return matches


def _finish_drives(database: Database, car_id: int, efficiency: float, progress: dict) -> None:
    """Sum up the drives the source never finished (no end, no distance) from their positions."""
    open_drives = database.query(
        "SELECT id FROM drives WHERE car_id = ? AND end_ts IS NOT NULL AND distance_km IS NULL", (car_id,)
    )
    for index, row in enumerate(open_drives, 1):
        progress["message"] = f"Finishing unfinished trips: {index} of {len(open_drives)}"
        if not database.discard_if_nowhere(row["id"]):
            database.close_drive(row["id"], efficiency)
    # The source doesn't keep the average speed
    database.execute(
        "UPDATE drives SET speed_avg_kmh = distance_km / duration_s * 3600.0"
        " WHERE car_id = ? AND speed_avg_kmh IS NULL AND duration_s > 0",
        (car_id,),
    )


def _finish_charges(database: Database, car_id: int, cost_per_kwh: float, progress: dict) -> None:
    """Sum up the sessions the source never finished from their samples; a bill from Tesla stays the cost."""
    open_charges = database.query(
        "SELECT id, billed_amount, billed_currency FROM charges c WHERE car_id = ? AND end_ts IS NOT NULL"
        " AND energy_added_kwh IS NULL AND EXISTS (SELECT 1 FROM charge_samples s WHERE s.charge_id = c.id)",
        (car_id,),
    )
    for index, row in enumerate(open_charges, 1):
        progress["message"] = f"Finishing unfinished charging sessions: {index} of {len(open_charges)}"
        if database.close_charge(row["id"], cost_per_kwh, fragment=True) and row["billed_amount"] is not None:
            database.set_billed(row["id"], row["billed_amount"], row["billed_currency"], as_cost=True)


def _import_car_details(cursor, database: Database, car_id: int, tm_id: int) -> None:
    """What the source knows about the car beyond its identity; the car's own reports keep what they set."""
    columns = _table_columns(cursor, "cars")
    cursor.execute(
        f"SELECT {'spoiler_type' if 'spoiler_type' in columns else 'NULL'},"
        f" {'marketing_name' if 'marketing_name' in columns else 'NULL'} FROM cars WHERE id = %s",
        (tm_id,),
    )
    spoiler, marketing = cursor.fetchone()
    lfp = free = None
    settings = _table_columns(cursor, "car_settings")
    if "settings_id" in columns and {"lfp_battery", "free_supercharging"} <= settings:
        cursor.execute(
            "SELECT s.lfp_battery, s.free_supercharging FROM cars c JOIN car_settings s ON s.id = c.settings_id"
            " WHERE c.id = %s",
            (tm_id,),
        )
        row = cursor.fetchone()
        if row:
            lfp, free = (None if value is None else int(value) for value in row)
    database.update_car(
        car_id, spoiler_type=spoiler, marketing_name=marketing, lfp_battery=lfp, free_supercharging=free
    )


def _import_drives(cursor, database: Database, car_id: int, efficiency: float, progress: dict) -> tuple[dict, dict]:
    """Import the drives. Returns two {source drive id: our drive id}: the new drives and the ones that
    were here already. Drives without an ascent are noted in `_NEEDS_ELEVATION` (see _import_positions)."""
    progress["phase"] = "drives"
    # Older TeslaMate databases (before 1.28) have no ascent/descent columns; those are then
    # worked out from the positions' elevations later
    has_elevation = _column_exists(cursor, "drives", "ascent")
    cursor.execute(
        _DRIVES.format(
            ascent="d.ascent" if has_elevation else "NULL", descent="d.descent" if has_elevation else "NULL"
        ),
        (car_id,),
    )
    rows = cursor.fetchall()
    drive_map: dict[int, int] = {}
    known_drives: dict[int, int] = {}
    existing = database.query("SELECT id, start_ts FROM drives WHERE car_id = ?", (car_id,))
    matches = _match_existing(existing, [ts for row in rows if (ts := _epoch(row[1])) is not None], DEDUP_WINDOW)
    with database.transaction() as db:
        for row in rows:
            (
                tm_drive,
                sd,
                ed,
                dist,
                dur,
                smax,
                pmax,
                pmin,
                ota,
                ita,
                skm,
                ekm,
                asc,
                desc,
                srr,
                err,
                sir,
                eir,
                slat,
                slon,
                ssoc,
                elat,
                elon,
                esoc,
                splace,
                eplace,
            ) = row
            start_ts = _epoch(sd)
            if start_ts is None:
                continue
            known = matches.get(start_ts) or db.covering("drives", car_id, start_ts)
            if known is not None:
                # Here already (recorded live or imported before): only add what it doesn't have yet
                known_drives[tm_drive] = known
                db.execute(
                    "UPDATE drives SET start_ideal_range_km = COALESCE(start_ideal_range_km, ?),"
                    " end_ideal_range_km = COALESCE(end_ideal_range_km, ?) WHERE id = ?",
                    (_f(sir), _f(eir), known),
                )
                continue
            # A drive the source never finished that went nowhere: not a trip. Not stored at all, or
            # the positions it leaves behind would come back with every import.
            if ed is None and (_f(dist) or 0.0) < MIN_DRIVE_KM:
                continue
            energy = None
            if srr is not None and err is not None:
                energy = (_f(srr) - _f(err)) * efficiency / 1000.0
            new_id = db.insert(
                "drives",
                {
                    "car_id": car_id,
                    "start_ts": start_ts,
                    "end_ts": _epoch(ed) or start_ts,
                    "start_lat": _f(slat),
                    "start_lon": _f(slon),
                    "end_lat": _f(elat),
                    "end_lon": _f(elon),
                    "start_place": splace,
                    "end_place": eplace,
                    "start_odometer_km": _f(skm),
                    "end_odometer_km": _f(ekm),
                    # Summed up from the positions afterwards when the source never finished it
                    "distance_km": _f(dist) if ed is not None else None,
                    "duration_s": (dur or 0) * 60,
                    "start_soc": ssoc,
                    "end_soc": esoc,
                    "start_range_km": _f(srr),
                    "end_range_km": _f(err),
                    "start_ideal_range_km": _f(sir),
                    "end_ideal_range_km": _f(eir),
                    "energy_kwh": energy,
                    "speed_max_kmh": _f(smax),
                    "power_max_kw": _f(pmax),
                    "power_min_kw": _f(pmin),
                    "outside_temp_avg": _f(ota),
                    "inside_temp_avg": _f(ita),
                    "ascent_m": _f(asc),
                    "descent_m": _f(desc),
                },
            )
            drive_map[tm_drive] = new_id
            if asc is None:
                _NEEDS_ELEVATION.add(new_id)
            progress["drives"] += 1
    return drive_map, known_drives


# New drive ids whose ascent and descent must come from their positions (older TeslaMate schemas)
_NEEDS_ELEVATION: set[int] = set()


def _climb(elevations: list[float]) -> tuple[float, float]:
    """Metres climbed and descended along a list of elevations."""
    ascent = descent = 0.0
    for previous, current in itertools.pairwise(elevations):
        delta = current - previous
        if delta > 0:
            ascent += delta
        else:
            descent -= delta
    return ascent, descent


def _import_positions(cursor, database: Database, car_id: int, drive_map: dict, progress: dict) -> None:
    progress["phase"] = "positions"
    columns = ("car_id", "drive_id", *STORED_POSITION_COLUMNS)
    extras = _select_extras(POSITION_EXTRA_COLUMNS, SOURCE_POSITION_COLUMNS, _table_columns(cursor, "positions"))
    query = _POSITIONS.format(extras=extras)
    pending: list[tuple] = []
    processed = 0
    for tm_drive, new_drive in drive_map.items():
        cursor.execute(query, (tm_drive,))
        elevations: list[float] = []
        for date, lat, lon, elev, speed, power, odo, soc, usoc, rated, est, otemp, itemp, *extra in cursor.fetchall():
            ts = _epoch(date)
            if ts is None:
                continue
            if elev is not None:
                elevations.append(_f(elev))
            pending.append(
                (
                    car_id,
                    new_drive,
                    ts,
                    _f(lat),
                    _f(lon),
                    None,
                    _f(elev),
                    _f(speed),
                    _f(power),
                    _f(odo),
                    soc,
                    usoc,
                    _f(rated),
                    _f(est),
                    _f(otemp),
                    _f(itemp),
                    *[_f(value) for value in extra],
                )
            )
        if new_drive in _NEEDS_ELEVATION:
            _NEEDS_ELEVATION.discard(new_drive)
            if len(elevations) >= 2:
                ascent, descent = _climb(elevations)
                database.execute(
                    "UPDATE drives SET ascent_m = ?, descent_m = ? WHERE id = ?",
                    (round(ascent), round(descent), new_drive),
                )
        processed += 1
        if processed % BATCH == 0 and pending:
            with database.transaction() as db:
                db.insert_many("positions", columns, pending)
            progress["positions"] += len(pending)
            pending = []
    if pending:
        with database.transaction() as db:
            db.insert_many("positions", columns, pending)
        progress["positions"] += len(pending)


def _fill_positions(cursor, database: Database, known_drives: dict, progress: dict) -> None:
    """Add the climate, heater and tire values to drives that were here before they were kept."""
    available = _table_columns(cursor, "positions")
    present = [c for c in POSITION_EXTRA_COLUMNS if SOURCE_POSITION_COLUMNS[c] in available]
    if not present:
        return
    progress["phase"] = "positions"
    extras = _select_extras(POSITION_EXTRA_COLUMNS, SOURCE_POSITION_COLUMNS, available)
    any_extra = " OR ".join(f"{SOURCE_POSITION_COLUMNS[c]} IS NOT NULL" for c in present)
    query = _POSITION_EXTRAS.format(extras=extras, any_extra=any_extra)
    pending: list[tuple] = []
    for processed, (tm_drive, drive) in enumerate(known_drives.items(), 1):
        cursor.execute(query, (tm_drive,))
        for date, *extra in cursor.fetchall():
            ts = _epoch(date)
            if ts is not None:
                pending.append((ts, drive, *[_f(value) for value in extra]))
        if processed % BATCH == 0 and pending:
            database.fill_positions(pending)
            progress["updated"] += len(pending)
            pending = []
    if pending:
        database.fill_positions(pending)
        progress["updated"] += len(pending)


def _import_parked_positions(cursor, database: Database, car_id: int, tm_id: int, progress: dict) -> None:
    """The positions outside a drive: these hold most of the tire pressure history."""
    progress["phase"] = "positions"
    columns = ("car_id", "drive_id", *STORED_POSITION_COLUMNS)
    extras = _select_extras(POSITION_EXTRA_COLUMNS, SOURCE_POSITION_COLUMNS, _table_columns(cursor, "positions"))
    query = _PARKED.format(extras=extras)
    known = database.parked_position_times(car_id)
    last_id = 0
    while True:
        cursor.execute(query, (tm_id, last_id, PARKED_PAGE))
        rows = cursor.fetchall()
        if not rows:
            return
        pending = []
        for row_id, date, lat, lon, elev, speed, power, odo, soc, usoc, rated, est, otemp, itemp, *extra in rows:
            last_id = row_id
            ts = _epoch(date)
            if ts is None or ts in known:
                continue
            known.add(ts)
            pending.append(
                (
                    car_id,
                    None,
                    ts,
                    _f(lat),
                    _f(lon),
                    None,
                    _f(elev),
                    _f(speed),
                    _f(power),
                    _f(odo),
                    soc,
                    usoc,
                    _f(rated),
                    _f(est),
                    _f(otemp),
                    _f(itemp),
                    *[_f(value) for value in extra],
                )
            )
        with database.transaction() as db:
            db.insert_many("positions", columns, pending)
        progress["positions"] += len(pending)


def _import_charges(cursor, database: Database, car_id: int, progress: dict) -> None:
    progress["phase"] = "charges"
    cursor.execute(_CHARGES, (car_id,))
    rows = cursor.fetchall()
    sample_columns = ("charge_id", *SAMPLE_COLUMNS)
    existing_charges = database.query("SELECT id, start_ts FROM charges WHERE car_id = ?", (car_id,))
    matches = _match_existing(existing_charges, [ts for row in rows if (ts := _epoch(row[1])) is not None], 300)
    samples_query = _SAMPLES.format(
        extras=_select_extras(SAMPLE_EXTRA_COLUMNS, SOURCE_SAMPLE_COLUMNS, _table_columns(cursor, "charges"))
    )
    for row in rows:
        tm_cp, sd, ed, added, used, ssoc, esoc, srr, err, dur, ota, cost, lat, lon, odo, place, pmax, fast, brand = row[
            :19
        ]
        cable, charger_type, start_ideal, end_ideal, unfinished_rise = row[19:]
        start_ts = _epoch(sd)
        if start_ts is None:
            continue
        # A charge already here: keep it if it has a power curve (live-recorded or already imported);
        # replace a sparse billing-only one (no SoC/power), keeping its billed cost
        existing = matches.get(start_ts) or database.covering("charges", car_id, start_ts)
        carry_billed = None
        if existing is not None:
            if database.charge_has_samples(existing):
                # Here with its power curve (recorded live or imported before): only add what it doesn't have yet
                database.execute(
                    "UPDATE charges SET conn_charge_cable = COALESCE(conn_charge_cable, ?),"
                    " fast_charger_type = COALESCE(fast_charger_type, ?),"
                    " start_ideal_range_km = COALESCE(start_ideal_range_km, ?),"
                    " end_ideal_range_km = COALESCE(end_ideal_range_km, ?) WHERE id = ?",
                    (cable, charger_type, _f(start_ideal), _f(end_ideal), existing),
                )
                cursor.execute(samples_query, (tm_cp,))
                fills = [
                    (ts, *[_f(value) for value in sample[9:]])
                    for sample in cursor.fetchall()
                    if (ts := _epoch(sample[0])) is not None
                ]
                database.fill_charge_samples(existing, fills)
                progress["updated"] += len(fills)
                continue
            prev = database.query_one("SELECT billed_amount, billed_currency FROM charges WHERE id = ?", (existing,))
            if prev and prev["billed_amount"] is not None:
                carry_billed = (prev["billed_amount"], prev["billed_currency"])
            database.execute("DELETE FROM charges WHERE id = ?", (existing,))
        # A session the source never finished that added nothing: not a charge, don't store it to drop it again
        if ed is None and (_f(unfinished_rise) or 0.0) < MIN_CHARGE_KWH:
            continue
        dc = bool(fast) or (pmax is not None and pmax > 25)
        charge_id = database.insert(
            "charges",
            {
                "car_id": car_id,
                "start_ts": start_ts,
                "end_ts": _epoch(ed) or start_ts,
                "lat": _f(lat),
                "lon": _f(lon),
                "place": place,
                "kind": "DC" if dc else "AC",
                "charger_brand": (brand or "Tesla") if dc else brand,
                "odometer_km": _f(odo),
                "start_soc": ssoc,
                "end_soc": esoc,
                "start_range_km": _f(srr),
                "end_range_km": _f(err),
                "conn_charge_cable": cable,
                "fast_charger_type": charger_type,
                "start_ideal_range_km": _f(start_ideal),
                "end_ideal_range_km": _f(end_ideal),
                "energy_added_kwh": _f(added),
                "energy_used_kwh": _f(used) or _f(added),
                "power_max_kw": _f(pmax),
                "duration_s": (dur or 0) * 60,
                "outside_temp_avg": _f(ota),
                "cost": _f(cost),
                "cost_manual": 0,
            },
        )
        cursor.execute(samples_query, (tm_cp,))
        samples = []
        for date, soc, power, volt, cur, phases, added2, rated, otemp, *extra in cursor.fetchall():
            ts = _epoch(date)
            if ts is not None:
                samples.append(
                    (
                        charge_id,
                        ts,
                        soc,
                        _f(power),
                        _f(volt),
                        _f(cur),
                        phases,
                        _f(added2),
                        _f(rated),
                        _f(otemp),
                        *[_f(value) for value in extra],
                    )
                )
        with database.transaction() as db:
            db.insert_many("charge_samples", sample_columns, samples)
        # Tesla's billed amount (from the earlier billing-history entry) beats the imported estimated cost
        if carry_billed:
            database.set_billed(charge_id, carry_billed[0], carry_billed[1], as_cost=True)
        progress["charges"] += 1


def _import_states(cursor, database: Database, car_id: int, progress: dict) -> None:
    progress["phase"] = "states"
    cursor.execute(_STATES, (car_id,))
    with database.transaction() as db:
        for state, sd, ed in cursor.fetchall():
            start_ts = _epoch(sd)
            # The source records online/offline/asleep; TeslaMeter adds driving/charging/updating from its own logging
            if start_ts is None or str(state) not in ("online", "offline", "asleep"):
                continue
            if db.exists_near("states", car_id, start_ts, 2):
                continue
            db.insert("states", {"car_id": car_id, "state": str(state), "start_ts": start_ts, "end_ts": _epoch(ed)})


def _import_updates(cursor, database: Database, car_id: int) -> None:
    cursor.execute(_UPDATES, (car_id,))
    known = {row["version"] for row in database.query("SELECT version FROM updates WHERE car_id = ?", (car_id,))}
    for version, sd in cursor.fetchall():
        ts = _epoch(sd)
        if version and version not in known and ts is not None:
            database.insert("updates", {"car_id": car_id, "version": version, "ts": ts})
            known.add(version)
