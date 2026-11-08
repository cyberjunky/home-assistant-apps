"""Statistics over the recorded drives, charges and states.

All functions take a car and a time range (unix seconds, end exclusive) and
return plain JSON-ready structures. Periods follow the local time zone.
"""

import itertools
import math
import statistics
from datetime import date, timedelta

from .database import Database

# SQLite expression for the first day of the period a timestamp falls in
BUCKETS = {
    "day": "date({ts}, 'unixepoch', 'localtime')",
    "week": "date({ts}, 'unixepoch', 'localtime', 'weekday 0', '-6 days')",
    "month": "strftime('%Y-%m-01', {ts}, 'unixepoch', 'localtime')",
    "year": "strftime('%Y-01-01', {ts}, 'unixepoch', 'localtime')",
}
# Standby loss is measured over parking periods of at least this long
MIN_PARKED_SECONDS = 4 * 3600
# Consumption by temperature only counts drives long enough to be meaningful
MIN_TEMP_DRIVE_KM = 5.0
TEMP_BIN = 5
# Visited cells are read in cell order for periods longer than this, in time order for shorter ones
LONG_PERIOD_S = 120 * 86400


def _next_period(day: date, bucket: str) -> date:
    if bucket == "day":
        return day + timedelta(days=1)
    if bucket == "week":
        return day + timedelta(days=7)
    if bucket == "month":
        return date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return date(day.year + 1, 1, 1)


def summary(db: Database, car_id: int, start: int, end: int) -> dict:
    """Totals of the drives and charges in a range."""
    drives = db.query_one(
        "SELECT COUNT(*) AS drives, COALESCE(SUM(distance_km), 0) AS distance_km,"
        " COALESCE(SUM(duration_s), 0) AS duration_s, SUM(energy_kwh) AS energy_kwh,"
        " SUM(CASE WHEN energy_kwh IS NOT NULL THEN distance_km END) AS energy_distance_km,"
        " MAX(speed_max_kmh) AS speed_max_kmh, MAX(distance_km) AS longest_km,"
        " MIN(start_odometer_km) AS odometer_start_km, MAX(end_odometer_km) AS odometer_end_km"
        " FROM drives WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?",
        (car_id, start, end),
    )
    charges = db.query_one(
        "SELECT COUNT(*) AS charges, COALESCE(SUM(energy_added_kwh), 0) AS added_kwh,"
        " COALESCE(SUM(energy_used_kwh), 0) AS used_kwh, SUM(cost) AS cost,"
        " COALESCE(SUM(duration_s), 0) AS charge_duration_s, MAX(power_max_kw) AS charge_power_max_kw,"
        " COALESCE(SUM(CASE WHEN kind = 'DC' THEN energy_added_kwh END), 0) AS dc_kwh,"
        " COALESCE(SUM(CASE WHEN kind = 'DC' THEN 1 END), 0) AS dc_charges"
        " FROM charges WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?",
        (car_id, start, end),
    )
    result = {**drives, **charges}
    energy, distance = drives["energy_kwh"], drives["energy_distance_km"]
    result["consumption_wh_km"] = energy / distance * 1000 if energy is not None and distance else None
    result["charge_efficiency"] = charges["added_kwh"] / charges["used_kwh"] if charges["used_kwh"] else None
    result["ac_kwh"] = charges["added_kwh"] - charges["dc_kwh"]
    result["cost_per_100km"] = (
        charges["cost"] / drives["distance_km"] * 100 if charges["cost"] and drives["distance_km"] else None
    )
    return result


def periods(db: Database, car_id: int, bucket: str, start: int, end: int) -> list[dict]:
    """Distance, energy and charging per day, week, month or year, without gaps."""
    expression = BUCKETS.get(bucket, BUCKETS["month"]).format(ts="start_ts")
    drive_rows = db.query(
        f"SELECT {expression} AS period, COUNT(*) AS drives, SUM(distance_km) AS distance_km,"
        " SUM(duration_s) AS duration_s, SUM(energy_kwh) AS energy_kwh,"
        " SUM(CASE WHEN energy_kwh IS NOT NULL THEN distance_km END) AS energy_distance_km"
        " FROM drives WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ? GROUP BY 1",
        (car_id, start, end),
    )
    charge_rows = db.query(
        f"SELECT {expression} AS period, COUNT(*) AS charges, SUM(energy_added_kwh) AS added_kwh,"
        " SUM(energy_used_kwh) AS used_kwh, SUM(cost) AS cost,"
        " SUM(CASE WHEN kind = 'DC' THEN energy_added_kwh ELSE 0 END) AS dc_kwh"
        " FROM charges WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ? GROUP BY 1",
        (car_id, start, end),
    )
    by_period: dict[str, dict] = {}
    for row in drive_rows + charge_rows:
        by_period.setdefault(row["period"], {}).update(row)
    if not by_period:
        return []

    result = []
    day, last = date.fromisoformat(min(by_period)), date.fromisoformat(max(by_period))
    while day <= last:
        row = by_period.get(day.isoformat(), {})
        energy, distance = row.get("energy_kwh"), row.get("energy_distance_km")
        added = row.get("added_kwh") or 0.0
        result.append(
            {
                "period": day.isoformat(),
                "drives": row.get("drives", 0),
                "distance_km": row.get("distance_km") or 0.0,
                "duration_s": row.get("duration_s") or 0,
                "energy_kwh": energy or 0.0,
                "consumption_wh_km": energy / distance * 1000 if energy is not None and distance else None,
                "charges": row.get("charges", 0),
                "added_kwh": added,
                "ac_kwh": added - (row.get("dc_kwh") or 0.0),
                "dc_kwh": row.get("dc_kwh") or 0.0,
                "used_kwh": row.get("used_kwh") or 0.0,
                "cost": row.get("cost"),
            }
        )
        day = _next_period(day, bucket)
    return result


def battery(db: Database, car_id: int, start: int, end: int, efficiency_wh_km: float) -> dict:
    """Battery health: the range a full battery would give, after each charge.

    A charge that ends at 80% with 400 km on the dash projects to 500 km at
    100%. Following that number over time shows the battery's degradation.
    The points, the current range and the loss follow the range; "new" is the
    best the battery ever did, whatever the range, as that is what it means.
    """
    rows = db.query(
        "SELECT end_ts AS ts, odometer_km, end_soc AS soc, end_range_km * 100.0 / end_soc AS full_range_km"
        " FROM charges WHERE car_id = ? AND end_ts IS NOT NULL AND end_soc >= 40 AND end_range_km > 0"
        " ORDER BY end_ts",
        (car_id,),
    )
    period = [row for row in rows if start <= row["ts"] < end]
    result: dict = {"points": period, "new_km": None, "now_km": None, "loss_pct": None, "capacity_kwh": None}
    if len(rows) >= 3:
        values = [row["full_range_km"] for row in rows]
        sample = max(3, min(10, len(values) // 4))
        # The best the battery ever did counts as new; single outliers are evened out by the median
        new = max(statistics.median(values[i : i + sample]) for i in range(0, len(values) - sample + 1))
        result["new_km"] = new
        if len(period) >= 3:
            now = statistics.median([row["full_range_km"] for row in period[-sample:]])
            result.update(
                now_km=now,
                loss_pct=max(0.0, (1 - now / new) * 100),
                capacity_kwh=now * efficiency_wh_km / 1000,
            )
    return result


def standby(db: Database, car_id: int, start: int, end: int, efficiency_wh_km: float) -> dict:
    """Range lost while parked ("vampire drain"), from the long stops between drives and charges."""
    events = db.query(
        "SELECT start_ts, end_ts, start_range_km, end_range_km FROM drives"
        " WHERE car_id = :car AND end_ts IS NOT NULL AND end_ts >= :start AND start_ts < :end"
        " UNION ALL"
        " SELECT start_ts, end_ts, start_range_km, end_range_km FROM charges"
        " WHERE car_id = :car AND end_ts IS NOT NULL AND end_ts >= :start AND start_ts < :end"
        " ORDER BY start_ts",
        {"car": car_id, "start": start, "end": end},
    )
    seconds = 0
    lost_km = 0.0
    for before, after in itertools.pairwise(events):
        parked = after["start_ts"] - before["end_ts"]
        if parked < MIN_PARKED_SECONDS or before["end_range_km"] is None or after["start_range_km"] is None:
            continue
        seconds += parked
        # Range can also creep up when the battery warms: that isn't a loss
        lost_km += max(0.0, before["end_range_km"] - after["start_range_km"])
    days = seconds / 86400
    return {
        "parked_hours": seconds / 3600,
        "km_per_day": lost_km / days if days else None,
        "kwh_per_day": lost_km * efficiency_wh_km / 1000 / days if days else None,
        "watts": lost_km * efficiency_wh_km / (seconds / 3600) if seconds else None,
    }


def by_temperature(db: Database, car_id: int, start: int, end: int) -> list[dict]:
    """Consumption per outside temperature band."""
    rows = db.query(
        "SELECT outside_temp_avg AS temp, distance_km, energy_kwh FROM drives"
        " WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?"
        " AND distance_km >= ? AND energy_kwh IS NOT NULL AND outside_temp_avg IS NOT NULL",
        (car_id, start, end, MIN_TEMP_DRIVE_KM),
    )
    bands: dict[int, dict] = {}
    for row in rows:
        band = bands.setdefault(
            math.floor(row["temp"] / TEMP_BIN) * TEMP_BIN, {"drives": 0, "distance_km": 0.0, "energy_kwh": 0.0}
        )
        band["drives"] += 1
        band["distance_km"] += row["distance_km"]
        band["energy_kwh"] += row["energy_kwh"]
    return [
        {
            "temp": temp,
            "drives": band["drives"],
            "distance_km": band["distance_km"],
            "consumption_wh_km": band["energy_kwh"] / band["distance_km"] * 1000,
        }
        for temp, band in sorted(bands.items())
    ]


def destinations(db: Database, car_id: int, start: int, end: int, limit: int = 8) -> list[dict]:
    """Where drives ended most often: by place name, or within about 100 m for places without one."""
    return db.query(
        "SELECT MAX(end_place) AS place, COUNT(*) AS visits, AVG(end_lat) AS lat, AVG(end_lon) AS lon"
        " FROM drives WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?"
        " AND end_lat IS NOT NULL GROUP BY COALESCE(end_place, ROUND(end_lat, 3) || ',' || ROUND(end_lon, 3))"
        " ORDER BY visits DESC, MAX(end_ts) DESC LIMIT ?",
        (car_id, start, end, limit),
    )


def charge_places(db: Database, car_id: int, start: int, end: int, limit: int = 8) -> list[dict]:
    """Where the car charged, by energy."""
    return db.query(
        "SELECT MAX(place) AS place, MAX(kind) AS kind, COUNT(*) AS charges, SUM(energy_added_kwh) AS added_kwh,"
        " SUM(cost) AS cost, AVG(lat) AS lat, AVG(lon) AS lon"
        " FROM charges WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?"
        " GROUP BY COALESCE(place, ROUND(lat, 3) || ',' || ROUND(lon, 3)) ORDER BY added_kwh DESC LIMIT ?",
        (car_id, start, end, limit),
    )


def state_share(db: Database, car_id: int, start: int, end: int, now: int) -> list[dict]:
    """How long the car was in each state (only the part inside the range counts)."""
    return db.query(
        "SELECT state, SUM(MIN(COALESCE(end_ts, :now), :end) - MAX(start_ts, :start)) AS seconds FROM states"
        " WHERE car_id = :car AND start_ts < :end AND COALESCE(end_ts, :now) > :start"
        " GROUP BY state ORDER BY seconds DESC",
        {"car": car_id, "start": start, "end": min(end, now), "now": now},
    )


def weekly_rhythm(db: Database, car_id: int, start: int, end: int) -> list[dict]:
    """Drives per weekday (0 = Monday) and hour of the day."""
    return db.query(
        "SELECT (CAST(strftime('%w', start_ts, 'unixepoch', 'localtime') AS INTEGER) + 6) % 7 AS weekday,"
        " CAST(strftime('%H', start_ts, 'unixepoch', 'localtime') AS INTEGER) AS hour,"
        " COUNT(*) AS drives, SUM(distance_km) AS distance_km"
        " FROM drives WHERE car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?"
        " GROUP BY 1, 2",
        (car_id, start, end),
    )


def records(db: Database, car_id: int, start: int, end: int) -> dict:
    """The extremes: longest and fastest drive, most frugal drive, strongest and biggest charge."""
    scope = "car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?"
    args = (car_id, start, end)

    def best(table: str, columns: str, order: str, where: str = "") -> dict | None:
        return db.query_one(f"SELECT id, start_ts, {columns} FROM {table} WHERE {scope} {where} ORDER BY {order}", args)

    return {
        "longest_drive": best("drives", "distance_km, end_place AS place", "distance_km DESC LIMIT 1"),
        "fastest_drive": best(
            "drives", "speed_max_kmh, end_place AS place", "speed_max_kmh DESC LIMIT 1", "AND speed_max_kmh > 0"
        ),
        "frugal_drive": best(
            "drives",
            "energy_kwh / distance_km * 1000 AS consumption_wh_km, distance_km, end_place AS place",
            "energy_kwh / distance_km LIMIT 1",
            "AND distance_km >= 10 AND energy_kwh > 0",
        ),
        "strongest_charge": best("charges", "power_max_kw, place", "power_max_kw DESC LIMIT 1", "AND power_max_kw > 0"),
        "biggest_charge": best("charges", "energy_added_kwh, place", "energy_added_kwh DESC LIMIT 1"),
    }


def software_updates(db: Database, car_id: int, start: int, end: int) -> list[dict]:
    """The software versions first seen in the range, newest first."""
    return db.query(
        "SELECT version, ts FROM updates WHERE car_id = ? AND ts >= ? AND ts < ? ORDER BY ts DESC",
        (car_id, start, end),
    )


def visited(db: Database, car_id: int, start: int, end: int, limit: int = 40000) -> list[list[float]]:
    """Where the car has driven, as [lat, lon, weight] cells of about 100 m for a heat map; the weight is
    the number of drives through the cell. Reads the cells mapped per drive (Database.map_drive_cells),
    never the positions themselves: those run into the millions after a few years."""
    # Over a long period nearly every cell is in play: walking the cells in order and counting
    # beats collecting the period's rows by time and sorting them (4x on a few years of commuting)
    index = "visited_cells_car_cell" if end - start > LONG_PERIOD_S else "visited_cells_car_ts"
    rows = db.query(
        f"SELECT lat, lon, COUNT(*) AS weight FROM visited_cells INDEXED BY {index}"
        " WHERE car_id = ? AND ts >= ? AND ts < ? GROUP BY 1, 2 ORDER BY weight DESC LIMIT ?",
        (car_id, start, end, limit),
    )
    return [[row["lat"], row["lon"], row["weight"]] for row in rows]


def insights(db: Database, car_id: int, start: int, end: int, now: int, efficiency_wh_km: float) -> dict:
    """Everything the Stats page shows besides the per-period charts."""
    return {
        "summary": summary(db, car_id, start, end),
        "battery": battery(db, car_id, start, end, efficiency_wh_km),
        "standby": standby(db, car_id, start, end, efficiency_wh_km),
        "temperature": by_temperature(db, car_id, start, end),
        "destinations": destinations(db, car_id, start, end),
        "charge_places": charge_places(db, car_id, start, end),
        "states": state_share(db, car_id, start, end, now),
        "rhythm": weekly_rhythm(db, car_id, start, end),
        "records": records(db, car_id, start, end),
        "updates": software_updates(db, car_id, start, end),
    }
