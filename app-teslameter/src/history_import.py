"""Import a user's existing history from another logger's PostgreSQL database.

An optional, one-off interoperability tool. The user gives the database
connection once; TeslaMeter reads its drives, positions, charging sessions,
states and software versions straight from it and adds them, matching cars by
VIN. It reads only, never writes to that database. The values are stored in
metric km, as TeslaMeter stores them, so they carry over directly.

The source database's table layout is used purely as the interchange format.
"""

import logging
import time
from datetime import UTC

from . import vehicle_specs
from .database import POSITION_COLUMNS, SAMPLE_COLUMNS, Database

logger = logging.getLogger(__name__)

# Insert positions and charge samples this many drives/sessions at a time
BATCH = 200
# Two rows count as the same session if their start is within this many seconds (re-import safety)
DEDUP_WINDOW = 120
DEFAULT_EFFICIENCY = 160.0


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
SELECT d.id, d.start_date, d.end_date, d.distance, d.duration_min, d.speed_max, d.power_max, d.power_min,
       d.outside_temp_avg, d.inside_temp_avg, d.start_km, d.end_km, d.ascent, d.descent,
       d.start_rated_range_km, d.end_rated_range_km,
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
       rated_battery_range_km, est_battery_range_km, outside_temp, inside_temp
FROM positions WHERE drive_id = %s ORDER BY date, id
"""

_CHARGES = """
SELECT cp.id, cp.start_date, cp.end_date, cp.charge_energy_added, cp.charge_energy_used,
       cp.start_battery_level, cp.end_battery_level, cp.start_rated_range_km, cp.end_rated_range_km,
       cp.duration_min, cp.outside_temp_avg, cp.cost, p.latitude, p.longitude, p.odometer,
       COALESCE(g.name, a.display_name),
       (SELECT MAX(charger_power) FROM charges c WHERE c.charging_process_id = cp.id),
       (SELECT bool_or(fast_charger_present) FROM charges c WHERE c.charging_process_id = cp.id),
       (SELECT MAX(fast_charger_brand) FROM charges c WHERE c.charging_process_id = cp.id)
FROM charging_processes cp
LEFT JOIN positions p ON p.id = cp.position_id
LEFT JOIN addresses a ON a.id = cp.address_id
LEFT JOIN geofences g ON g.id = cp.geofence_id
WHERE cp.car_id = %s ORDER BY cp.start_date
"""

_SAMPLES = """
SELECT date, battery_level, charger_power, charger_voltage, charger_actual_current, charger_phases,
       charge_energy_added, rated_battery_range_km, outside_temp
FROM charges WHERE charging_process_id = %s ORDER BY date, id
"""

_STATES = "SELECT state, start_date, end_date FROM states WHERE car_id = %s ORDER BY start_date"
_UPDATES = "SELECT version, start_date FROM updates WHERE car_id = %s AND version IS NOT NULL ORDER BY start_date"


def import_all(params: dict, database: Database, progress: dict) -> None:
    """Import everything from the source database into TeslaMeter. Updates `progress` as it goes."""
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
            _import_car(cursor, database, car, progress)
        progress.update(phase="done", message="Import finished", running=False, finished_at=int(time.time()))
        logger.info(
            f"History import finished: {progress['drives']} drives, {progress['charges']} charges,"
            f" {progress['positions']} positions"
        )
    finally:
        connection.close()


def _import_car(cursor, database: Database, car, progress: dict) -> None:
    tm_id, vin, name, _model, trim, color, wheels, eid, vid = car
    if not vin:
        return
    # Match the car by VIN, or create it (so history imports before the account is linked too).
    # eid is Tesla's id; a synthetic one is used only if the source never recorded it.
    car_id = database.upsert_car(eid or (900_000_000_000 + tm_id), vid, vin, name)
    database.update_car(car_id, model=vehicle_specs.model_name(None, vin), trim=trim, color=color, wheels=wheels)
    efficiency = (database.get_car(car_id) or {}).get("efficiency_wh_km") or DEFAULT_EFFICIENCY
    progress["message"] = f"Importing {name or vin}…"

    drive_map = _import_drives(cursor, database, car_id, efficiency, progress)
    _import_positions(cursor, database, car_id, drive_map, progress)
    _import_charges(cursor, database, car_id, progress)
    _import_states(cursor, database, car_id, progress)
    _import_updates(cursor, database, car_id)

    # Now that charges are in, learn the real consumption and recompute every drive's energy with it
    learned = database.learn_efficiency(car_id)
    if learned:
        database.apply_efficiency(car_id, learned)


def _import_drives(cursor, database: Database, car_id: int, efficiency: float, progress: dict) -> dict:
    progress["phase"] = "drives"
    cursor.execute(_DRIVES, (car_id,))
    rows = cursor.fetchall()
    drive_map: dict[int, int] = {}
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
            if start_ts is None or db.exists_near("drives", car_id, start_ts, DEDUP_WINDOW):
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
                    "distance_km": _f(dist),
                    "duration_s": (dur or 0) * 60,
                    "start_soc": ssoc,
                    "end_soc": esoc,
                    "start_range_km": _f(srr),
                    "end_range_km": _f(err),
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
            progress["drives"] += 1
    return drive_map


def _import_positions(cursor, database: Database, car_id: int, drive_map: dict, progress: dict) -> None:
    progress["phase"] = "positions"
    columns = ("car_id", "drive_id", *POSITION_COLUMNS)
    pending: list[tuple] = []
    processed = 0
    for tm_drive, new_drive in drive_map.items():
        cursor.execute(_POSITIONS, (tm_drive,))
        for date, lat, lon, elev, speed, power, odo, soc, usoc, rated, est, otemp, itemp in cursor.fetchall():
            ts = _epoch(date)
            if ts is None:
                continue
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
                )
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


def _import_charges(cursor, database: Database, car_id: int, progress: dict) -> None:
    progress["phase"] = "charges"
    cursor.execute(_CHARGES, (car_id,))
    rows = cursor.fetchall()
    sample_columns = ("charge_id", *SAMPLE_COLUMNS)
    for row in rows:
        tm_cp, sd, ed, added, used, ssoc, esoc, srr, err, dur, ota, cost, lat, lon, odo, place, pmax, fast, brand = row
        start_ts = _epoch(sd)
        if start_ts is None:
            continue
        # A charge already here: keep it if it has a power curve (live-recorded or already imported);
        # replace a sparse billing-only one (no SoC/power), keeping its billed cost
        existing = database.charge_near(car_id, start_ts, 300)
        carry_billed = None
        if existing is not None:
            if database.charge_has_samples(existing):
                continue
            prev = database.query_one("SELECT billed_amount, billed_currency FROM charges WHERE id = ?", (existing,))
            if prev and prev["billed_amount"] is not None:
                carry_billed = (prev["billed_amount"], prev["billed_currency"])
            database.execute("DELETE FROM charges WHERE id = ?", (existing,))
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
                "energy_added_kwh": _f(added),
                "energy_used_kwh": _f(used) or _f(added),
                "power_max_kw": _f(pmax),
                "duration_s": (dur or 0) * 60,
                "outside_temp_avg": _f(ota),
                "cost": _f(cost),
                "cost_manual": 0,
            },
        )
        cursor.execute(_SAMPLES, (tm_cp,))
        samples = []
        for date, soc, power, volt, cur, phases, added2, rated, otemp in cursor.fetchall():
            ts = _epoch(date)
            if ts is not None:
                samples.append(
                    (charge_id, ts, soc, _f(power), _f(volt), _f(cur), phases, _f(added2), _f(rated), _f(otemp))
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
