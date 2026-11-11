"""SQLite storage for the Tesla app.

Everything is stored in metric units (km, km/h, kW, kWh, °C) and UTC unix
seconds. One connection, guarded by a lock: the vehicle monitors write from the
event loop, statistics run in a worker thread.
"""

import itertools
import json
import logging
import math
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

logger = logging.getLogger(__name__)

DB_FILE = "tesla.db"

# Drives and charges shorter than this are noise (creeping in the driveway, a
# charger that refuses to start) and are dropped when they end
MIN_DRIVE_KM = 0.05
MIN_CHARGE_KWH = 0.05
# A gap between two charge samples longer than this doesn't count as charging time
MAX_SAMPLE_GAP = 300
# Elevation changes smaller than this are sensor noise, not climbing
ELEVATION_NOISE_M = 3.0
# Rated consumption is learned from sessions that added at least this much
MIN_LEARN_KWH = 3.0
MIN_LEARN_KM = 15.0
# The car's energy counter dropping by more than this means it started again from zero (a new plug-in)
COUNTER_RESET_KWH = 0.5
# A charge that starts again within this long after the previous one ended, at the same place and
# with the car's counter running on (still plugged in), continues that session instead of opening one
RESUME_GAP = 6 * 3600
SAME_PLACE_KM = 0.15

# Added in layout 4: what Tesla reports and other loggers keep, beyond the essentials. In SCHEMA for a
# new database, added by the migration for an existing one. Nothing else depends on these being filled.
POSITION_EXTRA_COLUMNS = (
    "ideal_range_km",
    "driver_temp",
    "passenger_temp",
    "fan_status",
    "climate_on",
    "front_defrost",
    "rear_defrost",
    "battery_heater",
    "battery_heater_on",
    "battery_heater_no_power",
    "tpms_fl",
    "tpms_fr",
    "tpms_rl",
    "tpms_rr",
)
SAMPLE_EXTRA_COLUMNS = (
    "usable_soc",
    "ideal_range_km",
    "pilot_current",
    "requested_current",
    "battery_heater",
    "battery_heater_on",
    "battery_heater_no_power",
    "not_enough_power_to_heat",
)
NEW_COLUMNS = {
    "cars": ("spoiler_type TEXT", "marketing_name TEXT", "lfp_battery INTEGER", "free_supercharging INTEGER"),
    "drives": ("start_ideal_range_km REAL", "end_ideal_range_km REAL"),
    "positions": tuple(
        f"{column} {'INTEGER' if column == 'fan_status' else 'REAL'}" for column in POSITION_EXTRA_COLUMNS
    ),
    "charges": (
        "conn_charge_cable TEXT",
        "fast_charger_type TEXT",
        "start_ideal_range_km REAL",
        "end_ideal_range_km REAL",
    ),
    "charge_samples": tuple(f"{column} REAL" for column in SAMPLE_EXTRA_COLUMNS),
}
# Only the few positions that carry tire pressures, so the tire chart never reads the others
POSITIONS_TPMS_INDEX = (
    "CREATE INDEX positions_tpms ON positions(car_id, ts, tpms_fl, tpms_fr, tpms_rl, tpms_rr) WHERE tpms_fl IS NOT NULL"
)


def _new_columns(table: str) -> str:
    return "".join(f",\n    {column}" for column in NEW_COLUMNS[table])


# Cars removed by hand: an import must not bring them back
REMOVED_CARS_SQL = "CREATE TABLE removed_cars (vin TEXT PRIMARY KEY, removed_at INTEGER NOT NULL)"

VISITED_CELLS_SQL = """
-- Where the car has driven, as cells of about 100 m (coordinates rounded to 3 decimals), one row
-- per drive and cell. Filled when a drive ends (drives.cell_count says how many; NULL = not yet),
-- so the "where you've been" map never has to read the millions of positions behind it.
CREATE TABLE visited_cells (
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    drive_id INTEGER NOT NULL REFERENCES drives(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    PRIMARY KEY (drive_id, lat, lon)
) WITHOUT ROWID;
CREATE INDEX visited_cells_car_ts ON visited_cells(car_id, ts);
-- Long periods are faster grouped in cell order (no sort of the whole period), short ones in time order
CREATE INDEX visited_cells_car_cell ON visited_cells(car_id, lat, lon, ts);
"""

SCHEMA = f"""
CREATE TABLE cars (
    id INTEGER PRIMARY KEY,
    tesla_id INTEGER NOT NULL UNIQUE,
    vehicle_id INTEGER,
    vin TEXT UNIQUE,
    name TEXT,
    model TEXT,
    trim TEXT,
    color TEXT,
    wheels TEXT,
    efficiency_wh_km REAL,
    snapshot TEXT,
    added_at INTEGER NOT NULL{_new_columns("cars")}
);

CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL,
    lat REAL,
    lon REAL,
    heading INTEGER,
    elevation_m REAL,
    speed_kmh REAL,
    power_kw REAL,
    odometer_km REAL,
    soc INTEGER,
    usable_soc INTEGER,
    range_km REAL,
    est_range_km REAL,
    outside_temp REAL,
    inside_temp REAL,
    drive_id INTEGER REFERENCES drives(id) ON DELETE SET NULL{_new_columns("positions")}
);
CREATE INDEX positions_car_ts ON positions(car_id, ts);
CREATE INDEX positions_drive ON positions(drive_id, ts) WHERE drive_id IS NOT NULL;
{POSITIONS_TPMS_INDEX};

CREATE TABLE drives (
    id INTEGER PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER,
    start_lat REAL, start_lon REAL, end_lat REAL, end_lon REAL,
    start_place TEXT, end_place TEXT,
    start_odometer_km REAL, end_odometer_km REAL,
    distance_km REAL,
    duration_s INTEGER,
    start_soc INTEGER, end_soc INTEGER,
    start_range_km REAL, end_range_km REAL,
    energy_kwh REAL,
    speed_max_kmh REAL, speed_avg_kmh REAL,
    power_max_kw REAL, power_min_kw REAL,
    outside_temp_avg REAL, inside_temp_avg REAL,
    ascent_m REAL, descent_m REAL,
    cell_count INTEGER{_new_columns("drives")}
);
CREATE INDEX drives_car_ts ON drives(car_id, start_ts);

{VISITED_CELLS_SQL}

CREATE TABLE charges (
    id INTEGER PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER,
    lat REAL, lon REAL,
    place TEXT,
    kind TEXT,
    charger_brand TEXT,
    odometer_km REAL,
    start_soc INTEGER, end_soc INTEGER,
    start_range_km REAL, end_range_km REAL,
    energy_added_kwh REAL,
    energy_used_kwh REAL,
    power_max_kw REAL,
    duration_s INTEGER,
    outside_temp_avg REAL,
    cost REAL,
    cost_manual INTEGER NOT NULL DEFAULT 0,
    billed_amount REAL,
    billed_currency TEXT{_new_columns("charges")}
);
CREATE INDEX charges_car_ts ON charges(car_id, start_ts);

CREATE TABLE charge_samples (
    id INTEGER PRIMARY KEY,
    charge_id INTEGER NOT NULL REFERENCES charges(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL,
    soc INTEGER,
    power_kw REAL,
    voltage REAL,
    current REAL,
    phases INTEGER,
    energy_added_kwh REAL,
    range_km REAL,
    outside_temp REAL{_new_columns("charge_samples")}
);
CREATE INDEX charge_samples_charge ON charge_samples(charge_id, ts);

CREATE TABLE states (
    id INTEGER PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER
);
CREATE INDEX states_car_ts ON states(car_id, start_ts);

CREATE TABLE updates (
    id INTEGER PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    ts INTEGER NOT NULL
);

CREATE TABLE addresses (
    key TEXT PRIMARY KEY,
    label TEXT,
    city TEXT,
    country TEXT,
    fetched_at INTEGER NOT NULL
);

-- Charging price tags: a named rate per kWh (NULL = use the default price, 0 = free).
-- A location is mapped to a tag so its estimated sessions use that rate instead of the default.
CREATE TABLE charge_tags (
    name TEXT PRIMARY KEY,
    price_per_kwh REAL,
    pos INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE location_tags (
    place_key TEXT PRIMARY KEY,
    tag TEXT NOT NULL REFERENCES charge_tags(name) ON DELETE CASCADE ON UPDATE CASCADE
);
{REMOVED_CARS_SQL};
INSERT INTO charge_tags (name, price_per_kwh, pos) VALUES ('Home', NULL, 0), ('Free', 0, 1), ('Public', NULL, 2);
INSERT INTO location_tags (place_key, tag) VALUES ('home', 'Home');
"""

# Stored in the database file. A database of an older layout is brought up to date at
# start by the steps below; one of a newer layout (or one so old there is no step for it)
# is refused with a message instead of failing on a missing column later.
SCHEMA_VERSION = 6


def _statements(sql: str) -> list[str]:
    """The statements of a schema snippet, one by one, without the comment lines."""
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in code.split(";") if statement.strip()]


MIGRATIONS = {
    # 3 -> 4 (0.6.0): the visited cells behind the Stats map, and everything else Tesla reports
    3: [
        "ALTER TABLE drives ADD COLUMN cell_count INTEGER",
        *_statements(VISITED_CELLS_SQL),
        *[f"ALTER TABLE {table} ADD COLUMN {column}" for table, columns in NEW_COLUMNS.items() for column in columns],
        POSITIONS_TPMS_INDEX,
    ],
    # 4 -> 5 (0.6.1): cars removed by hand
    4: [REMOVED_CARS_SQL],
    # 5 -> 6 (0.7.1): no new layout; sessions that resumed a paused charge counted its first part twice
    5: [lambda db: db.repair_resumed_charges()],
}

POSITION_COLUMNS = (
    "ts",
    "lat",
    "lon",
    "heading",
    "elevation_m",
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
# Everything stored with a position; POSITION_COLUMNS is what the track of a trip needs
STORED_POSITION_COLUMNS = (*POSITION_COLUMNS, *POSITION_EXTRA_COLUMNS)
SAMPLE_COLUMNS = (
    "ts",
    "soc",
    "power_kw",
    "voltage",
    "current",
    "phases",
    "energy_added_kwh",
    "range_km",
    "outside_temp",
    *SAMPLE_EXTRA_COLUMNS,
)


def place_key(place: str | None, lat: float | None, lon: float | None) -> str | None:
    """A stable key for a charging location: its place name, else coordinates to ~100 m."""
    if place and place.strip():
        return place.strip().lower()
    if lat is not None and lon is not None:
        return f"@{round(lat, 3)},{round(lon, 3)}"
    return None


def counter_energy(readings: list, fragment: bool = False) -> float:
    """The kWh a session added according to the car's charge_energy_added counter.

    The counter runs on as long as the car stays plugged in, also over a pause, and starts again from
    zero with a new plug-in. So a session is the counter's rise over its readings, where every restart
    (a clear drop) begins a new stretch that counts from zero. First readings that don't rise before
    such a drop still show the previous plug-in's total and are left out. `fragment`: the readings are a later part
    of a plug-in whose earlier part was booked already, so the first stretch only counts its rise.
    """
    values = [float(value) for value in readings if value is not None]
    if not values:
        return 0.0
    stretches = [[values[0]]]
    for previous, current in itertools.pairwise(values):
        if current < previous - COUNTER_RESET_KWH:
            stretches.append([current])
        else:
            stretches[-1].append(current)
    while len(stretches) > 1 and max(stretches[0]) - min(stretches[0]) < MIN_CHARGE_KWH:
        stretches.pop(0)
    first, *rest = stretches
    # Small dips (rounding) before the counter really starts: count from the lowest reading on
    lowest = first.index(min(first))
    added = max(first[lowest:]) - (first[lowest] if fragment else 0.0)
    return max(0.0, added) + sum(max(stretch) for stretch in rest)


def _km_apart(lat1, lon1, lat2, lon2) -> float | None:
    """Roughly how far apart two coordinates are (good enough for "the same place")."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    dlat = (lat2 - lat1) * 111.32
    dlon = (lon2 - lon1) * 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def _stride(rows: list, max_points: int) -> list:
    """Thin a series to about max_points rows, always keeping the first and the last."""
    if max_points <= 0 or len(rows) <= max_points:
        return rows
    step = len(rows) / (max_points - 1)
    picked = [rows[min(len(rows) - 1, round(i * step))] for i in range(max_points - 1)]
    picked.append(rows[-1])
    return picked


class DatabaseLayoutError(Exception):
    """The database file has the layout of another version of this app."""


class _Transaction:
    """Holds the database lock and wraps a batch of writes in one SQL transaction."""

    def __init__(self, database: "Database"):
        self._database = database

    def __enter__(self):
        self._database._lock.acquire()
        self._database._conn.execute("BEGIN")
        return self._database

    def __exit__(self, exc_type, exc, tb):
        try:
            self._database._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self._database._lock.release()
        return False


class Database:
    """The add-on's SQLite database."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DATA_DIR / DB_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with self._lock:
                self._conn.executescript(f"BEGIN;{SCHEMA}PRAGMA user_version={SCHEMA_VERSION};COMMIT;")
            logger.info(f"Created database {self.path}")
            version = SCHEMA_VERSION
        elif version > SCHEMA_VERSION or version < min(MIGRATIONS):
            self._conn.close()
            raise DatabaseLayoutError(
                f"{self.path} was made by another version of this app (layout {version}, this version needs"
                f" {SCHEMA_VERSION}). Remove that file (or uninstall and reinstall the add-on) to start with a new one"
            )
        while version < SCHEMA_VERSION:
            with self.transaction():
                for step in MIGRATIONS[version]:
                    # A step is SQL, or a function correcting data the way that version records it
                    if callable(step):
                        step(self)
                    else:
                        self._conn.execute(step)
                self._conn.execute(f"PRAGMA user_version={version + 1}")
            logger.info(f"Database layout {version} brought up to {version + 1}")
            version += 1

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Generic access
    # ------------------------------------------------------------------
    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        """Run a statement; returns the new row id for an INSERT, else the affected row count."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return cursor.lastrowid if sql.lstrip()[:6].upper() == "INSERT" else cursor.rowcount

    def insert(self, table: str, data: dict) -> int:
        """Insert one row (table name is code-controlled, never user input). Returns the new id."""
        columns = [key for key, value in data.items() if value is not None]
        return self.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            tuple(data[column] for column in columns),
        )

    def insert_many(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
        """Insert many rows at once (used by the history import)."""
        if not rows:
            return
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
        with self._lock:
            self._conn.executemany(sql, rows)

    def exists_near(self, table: str, car_id: int, start_ts: int, window: int) -> bool:
        """Whether a row of this car in `table` starts within `window` seconds of start_ts."""
        row = self.query_one(
            f"SELECT 1 FROM {table} WHERE car_id = ? AND ABS(start_ts - ?) < ? LIMIT 1", (car_id, start_ts, window)
        )
        return row is not None

    def transaction(self):
        """A context manager running a batch of writes in one transaction (faster for bulk inserts)."""
        return _Transaction(self)

    def backup_to(self, target: Path) -> None:
        """Write a consistent copy of the database (used for the download in Settings)."""
        with self._lock:
            copy = sqlite3.connect(target)
            try:
                self._conn.backup(copy)
            finally:
                copy.close()

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def delete_recorded_data(self) -> None:
        """Remove everything recorded: drives, charges, positions, states, software versions and the
        address cache. The cars (with their learned consumption) and the price tags stay, so logging
        simply continues; the home zone keeps its tag because it is a setting, not recorded data."""
        with self.transaction() as db:
            # Children first: the foreign keys would otherwise do the same work row by row
            for table in (
                "charge_samples",
                "visited_cells",
                "positions",
                "drives",
                "charges",
                "states",
                "updates",
                "addresses",
            ):
                db.execute(f"DELETE FROM {table}")
            db.execute("DELETE FROM location_tags WHERE place_key != 'home'")
        with self._lock:
            # Hand the space back to the file system: VACUUM rewrites the database (not allowed inside a
            # transaction) and the checkpoint moves that rewrite out of the WAL, so the file shrinks now
            self._conn.execute("VACUUM")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ------------------------------------------------------------------
    # Cars
    # ------------------------------------------------------------------
    def upsert_car(self, tesla_id: int, vehicle_id: int | None, vin: str | None, name: str | None) -> int:
        """Register a vehicle of the account, or refresh its ids and name. Returns our car id."""
        with self._lock:
            # The VIN is what stays the same if Tesla ever hands out a new id
            row = self.query_one("SELECT id FROM cars WHERE vin = ? OR tesla_id = ?", (vin, tesla_id))
            if row:
                self.execute(
                    "UPDATE cars SET tesla_id = ?, vehicle_id = COALESCE(?, vehicle_id), vin = COALESCE(?, vin),"
                    " name = COALESCE(NULLIF(?, ''), name) WHERE id = ?",
                    (tesla_id, vehicle_id, vin, name, row["id"]),
                )
                return row["id"]
            return self.execute(
                "INSERT INTO cars (tesla_id, vehicle_id, vin, name, added_at) VALUES (?, ?, ?, ?, ?)",
                (tesla_id, vehicle_id, vin, name, int(time.time())),
            )

    def list_cars(self) -> list[dict]:
        return self.query("SELECT * FROM cars ORDER BY id")

    def get_car(self, car_id: int) -> dict | None:
        return self.query_one("SELECT * FROM cars WHERE id = ?", (car_id,))

    def update_car(self, car_id: int, **fields: Any) -> None:
        """Set car columns (name, model, trim, color, wheels, efficiency_wh_km, ...); None values are skipped."""
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        self.execute(f"UPDATE cars SET {assignments} WHERE id = ?", (*fields.values(), car_id))

    def car_history_counts(self, car_id: int) -> dict:
        """How much is recorded under a car, to say so before removing it."""
        return self.query_one(
            "SELECT (SELECT COUNT(*) FROM drives WHERE car_id = :car) AS drives,"
            " (SELECT COUNT(*) FROM charges WHERE car_id = :car) AS charges,"
            " (SELECT COALESCE(SUM(energy_added_kwh), 0) FROM charges WHERE car_id = :car) AS added_kwh,"
            " (SELECT SUM(cost) FROM charges WHERE car_id = :car) AS cost",
            {"car": car_id},
        )

    def remove_car(self, car_id: int) -> None:
        """Delete a car with everything recorded under it, and remember not to import it again."""
        with self.transaction() as db:
            car = db.query_one("SELECT vin FROM cars WHERE id = ?", (car_id,))
            if car and car["vin"]:
                db.execute(
                    "INSERT OR REPLACE INTO removed_cars (vin, removed_at) VALUES (?, ?)",
                    (car["vin"], int(time.time())),
                )
            db.execute("DELETE FROM cars WHERE id = ?", (car_id,))

    def car_is_removed(self, vin: str | None) -> bool:
        return bool(vin) and self.query_one("SELECT 1 FROM removed_cars WHERE vin = ?", (vin,)) is not None

    def forget_removal(self, vin: str | None) -> None:
        """The car is on the account (again): it counts as one of ours."""
        if vin:
            self.execute("DELETE FROM removed_cars WHERE vin = ?", (vin,))

    def save_snapshot(self, car_id: int, snapshot: dict) -> None:
        self.execute("UPDATE cars SET snapshot = ? WHERE id = ?", (json.dumps(snapshot), car_id))

    def load_snapshot(self, car_id: int) -> dict:
        row = self.query_one("SELECT snapshot FROM cars WHERE id = ?", (car_id,))
        try:
            return json.loads(row["snapshot"]) if row and row["snapshot"] else {}
        except ValueError:
            return {}

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------
    def add_position(self, car_id: int, sample: dict, drive_id: int | None = None) -> int:
        values = [sample.get(column) for column in STORED_POSITION_COLUMNS]
        return self.execute(
            f"INSERT INTO positions (car_id, drive_id, {', '.join(STORED_POSITION_COLUMNS)})"
            f" VALUES (?, ?, {', '.join('?' * len(STORED_POSITION_COLUMNS))})",
            (car_id, drive_id, *values),
        )

    def fill_positions(self, rows: list[tuple]) -> None:
        """Add the extra values to positions already stored, from (ts, drive_id, *POSITION_EXTRA_COLUMNS).

        Each goes to the drive's position closest in time, within half a minute: a drive this app
        recorded itself has positions of its own. Values already there stay."""
        assignments = ", ".join(f"{column} = COALESCE({column}, ?)" for column in POSITION_EXTRA_COLUMNS)
        sql = (
            f"UPDATE positions SET {assignments} WHERE id = (SELECT id FROM positions"
            " WHERE drive_id = ? AND ts BETWEEN ? AND ? ORDER BY ABS(ts - ?), driver_temp IS NULL, id LIMIT 1)"
        )
        with self.transaction():
            self._conn.executemany(sql, [(*row[2:], row[1], row[0] - 30, row[0] + 30, row[0]) for row in rows])

    def fill_charge_samples(self, charge_id: int, rows: list[tuple]) -> None:
        """Add the extra values to samples already stored, from (ts, *SAMPLE_EXTRA_COLUMNS). As fill_positions."""
        assignments = ", ".join(f"{column} = COALESCE({column}, ?)" for column in SAMPLE_EXTRA_COLUMNS)
        sql = (
            f"UPDATE charge_samples SET {assignments} WHERE id = (SELECT id FROM charge_samples"
            " WHERE charge_id = ? AND ts BETWEEN ? AND ? ORDER BY ABS(ts - ?), id LIMIT 1)"
        )
        with self.transaction():
            self._conn.executemany(sql, [(*row[1:], charge_id, row[0] - 30, row[0] + 30, row[0]) for row in rows])

    def parked_position_times(self, car_id: int) -> set[int]:
        """The times of this car's positions outside a drive, so an import doesn't add them twice."""
        return {
            row["ts"] for row in self.query("SELECT ts FROM positions WHERE car_id = ? AND drive_id IS NULL", (car_id,))
        }

    def covering(self, table: str, car_id: int, ts: int) -> int | None:
        """The id of a finished drive or charge of this car that was going on at ts (table is code-controlled)."""
        row = self.query_one(
            f"SELECT id FROM {table} WHERE car_id = ? AND start_ts <= ? AND end_ts > ? ORDER BY start_ts DESC LIMIT 1",
            (car_id, ts, ts),
        )
        return row["id"] if row else None

    def drive_conditions(self, drive_id: int) -> dict | None:
        """Climate, battery heater and tire pressures during a drive, from the positions that have them."""
        return self.query_one(
            "SELECT COUNT(driver_temp) AS samples, AVG(climate_on) AS climate_share, AVG(driver_temp) AS driver_temp,"
            " AVG(passenger_temp) AS passenger_temp, AVG(fan_status) AS fan_status,"
            " AVG(front_defrost) AS front_defrost_share, AVG(rear_defrost) AS rear_defrost_share,"
            " AVG(COALESCE(battery_heater_on, battery_heater)) AS heater_share,"
            " AVG(tpms_fl) AS tpms_fl, AVG(tpms_fr) AS tpms_fr, AVG(tpms_rl) AS tpms_rl, AVG(tpms_rr) AS tpms_rr"
            " FROM positions WHERE drive_id = ?",
            (drive_id,),
        )

    def drive_positions(self, drive_id: int, max_points: int = 0, after_ts: int = 0) -> list[dict]:
        rows = self.query(
            f"SELECT {', '.join(POSITION_COLUMNS)} FROM positions"
            " WHERE drive_id = ? AND ts > ? AND lat IS NOT NULL ORDER BY ts, id",
            (drive_id, after_ts),
        )
        return _stride(rows, max_points)

    def heat_positions(self, car_id: int, since_ts: int, max_points: int = 5000) -> list[list]:
        """Thinned [lat, lon, weight] points for a heatmap. Stationary points weigh more (that's where the car spends time)."""
        # Choose the sample from the row ids first — an index-only read of plain
        # integers — then read lat/lon only for the ~max_points rows we keep. This
        # yields exactly the same points as striding the full result (the ids are in
        # the same ts, id order), but avoids pulling the whole position history —
        # millions of rows for "all time" — into memory just to discard almost all of it.
        ids = [
            row["id"]
            for row in self.query(
                "SELECT id FROM positions WHERE car_id = ? AND ts >= ? AND lat IS NOT NULL ORDER BY ts, id",
                (car_id, since_ts),
            )
        ]
        ids = _stride(ids, max_points)
        if not ids:
            return []
        by_id: dict[int, dict] = {}
        chunk_size = 900  # keep within SQLite's default host-parameter limit
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            for row in self.query(
                f"SELECT id, lat, lon, speed_kmh FROM positions WHERE id IN ({placeholders})", tuple(chunk)
            ):
                by_id[row["id"]] = row
        return [
            [row["lat"], row["lon"], 1.0 if not row["speed_kmh"] else 0.4]
            for row in (by_id[i] for i in ids if i in by_id)
        ]

    # ------------------------------------------------------------------
    # Drives
    # ------------------------------------------------------------------
    def open_drive(self, car_id: int, sample: dict) -> int:
        return self.execute(
            "INSERT INTO drives (car_id, start_ts, start_lat, start_lon, start_odometer_km, start_soc, start_range_km)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                car_id,
                sample["ts"],
                sample.get("lat"),
                sample.get("lon"),
                sample.get("odometer_km"),
                sample.get("soc"),
                sample.get("range_km"),
            ),
        )

    def _edge(self, drive_id: int, column: str, last: bool) -> Any:
        """The first (or last) recorded value of a position column during a drive."""
        row = self.query_one(
            f"SELECT {column} AS value FROM positions WHERE drive_id = ? AND {column} IS NOT NULL"
            f" ORDER BY ts {'DESC' if last else 'ASC'}, id {'DESC' if last else 'ASC'} LIMIT 1",
            (drive_id,),
        )
        return row["value"] if row else None

    def close_drive(self, drive_id: int, efficiency_wh_km: float) -> dict | None:
        """Finish a drive: summarise its positions. Returns the drive, or None when it was too short to keep."""
        with self._lock:
            drive = self.query_one("SELECT * FROM drives WHERE id = ?", (drive_id,))
            if not drive:
                return None
            agg = self.query_one(
                "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts, MAX(speed_kmh) AS speed_max,"
                " MAX(power_kw) AS power_max, MIN(power_kw) AS power_min,"
                " AVG(outside_temp) AS outside_avg, AVG(inside_temp) AS inside_avg"
                " FROM positions WHERE drive_id = ?",
                (drive_id,),
            )
            start_odo = self._edge(drive_id, "odometer_km", last=False) or drive["start_odometer_km"]
            end_odo = self._edge(drive_id, "odometer_km", last=True)
            distance = (end_odo - start_odo) if start_odo is not None and end_odo is not None else 0.0
            if not agg["n"] or distance < MIN_DRIVE_KM:
                self.delete_drive(drive_id)
                return None

            start_ts = drive["start_ts"]
            end_ts = max(agg["last_ts"], start_ts)
            duration = end_ts - start_ts
            start_range = drive["start_range_km"] or self._edge(drive_id, "range_km", last=False)
            end_range = self._edge(drive_id, "range_km", last=True)
            energy = None
            if start_range is not None and end_range is not None:
                energy = (start_range - end_range) * efficiency_wh_km / 1000.0

            ascent = descent = 0.0
            anchor = None
            for row in self.query(
                "SELECT elevation_m FROM positions WHERE drive_id = ? AND elevation_m IS NOT NULL ORDER BY ts, id",
                (drive_id,),
            ):
                if anchor is None:
                    anchor = row["elevation_m"]
                    continue
                delta = row["elevation_m"] - anchor
                if abs(delta) >= ELEVATION_NOISE_M:
                    if delta > 0:
                        ascent += delta
                    else:
                        descent -= delta
                    anchor = row["elevation_m"]

            self.execute(
                "UPDATE drives SET end_ts = ?, end_lat = ?, end_lon = ?, start_odometer_km = ?, end_odometer_km = ?,"
                " distance_km = ?, duration_s = ?, start_soc = COALESCE(start_soc, ?), end_soc = ?,"
                " start_range_km = ?, end_range_km = ?, energy_kwh = ?, speed_max_kmh = ?, speed_avg_kmh = ?,"
                " power_max_kw = ?, power_min_kw = ?, outside_temp_avg = ?, inside_temp_avg = ?,"
                " ascent_m = ?, descent_m = ? WHERE id = ?",
                (
                    end_ts,
                    self._edge(drive_id, "lat", last=True),
                    self._edge(drive_id, "lon", last=True),
                    start_odo,
                    end_odo,
                    distance,
                    duration,
                    self._edge(drive_id, "soc", last=False),
                    self._edge(drive_id, "soc", last=True),
                    start_range,
                    end_range,
                    energy,
                    agg["speed_max"],
                    distance / duration * 3600 if duration > 0 else None,
                    agg["power_max"],
                    agg["power_min"],
                    agg["outside_avg"],
                    agg["inside_avg"],
                    ascent if anchor is not None else None,
                    descent if anchor is not None else None,
                    drive_id,
                ),
            )
            self.execute(
                "UPDATE drives SET start_ideal_range_km = ?, end_ideal_range_km = ? WHERE id = ?",
                (
                    self._edge(drive_id, "ideal_range_km", last=False),
                    self._edge(drive_id, "ideal_range_km", last=True),
                    drive_id,
                ),
            )
            self.map_drive_cells(drive_id)
            return self.query_one("SELECT * FROM drives WHERE id = ?", (drive_id,))

    def map_drive_cells(self, drive_id: int) -> int:
        """Record the ~100 m cells a drive passed through, for the Stats map. Returns how many."""
        with self._lock:
            self.execute(
                "INSERT OR IGNORE INTO visited_cells (car_id, drive_id, ts, lat, lon)"
                " SELECT DISTINCT p.car_id, p.drive_id, d.start_ts, ROUND(p.lat, 3), ROUND(p.lon, 3)"
                " FROM positions p JOIN drives d ON d.id = p.drive_id WHERE p.drive_id = ? AND p.lat IS NOT NULL",
                (drive_id,),
            )
            count = self.query_one("SELECT COUNT(*) AS n FROM visited_cells WHERE drive_id = ?", (drive_id,))["n"]
            self.execute("UPDATE drives SET cell_count = ? WHERE id = ?", (count, drive_id))
            return count

    def unmapped_drives(self) -> int:
        """How many finished drives still have to be mapped into visited cells."""
        return self.query_one("SELECT COUNT(*) AS n FROM drives WHERE end_ts IS NOT NULL AND cell_count IS NULL")["n"]

    def map_missing_cells(self, chunk: int = 50, pause: float = 0.2) -> int:
        """Map every finished drive that hasn't been yet (after an upgrade or an import), a few at a time
        so the recorder keeps getting at the database in between. Returns the number of drives mapped."""
        mapped = 0
        while True:
            ids = [
                row["id"]
                for row in self.query(
                    "SELECT id FROM drives WHERE end_ts IS NOT NULL AND cell_count IS NULL ORDER BY id LIMIT ?",
                    (chunk,),
                )
            ]
            if not ids:
                return mapped
            with self.transaction():
                for drive_id in ids:
                    self.map_drive_cells(drive_id)
            mapped += len(ids)
            time.sleep(pause)

    def discard_if_nowhere(self, drive_id: int) -> bool:
        """Remove an imported drive that went nowhere together with its positions, so that importing again
        doesn't leave those behind each time as parked ones (close_drive would keep them). True if removed."""
        with self._lock:
            drive = self.query_one("SELECT start_odometer_km FROM drives WHERE id = ?", (drive_id,))
            start = self._edge(drive_id, "odometer_km", last=False) or (drive or {}).get("start_odometer_km")
            end = self._edge(drive_id, "odometer_km", last=True)
            if start is not None and end is not None and end - start >= MIN_DRIVE_KM:
                return False
            self.execute("DELETE FROM positions WHERE drive_id = ?", (drive_id,))
            self.execute("DELETE FROM drives WHERE id = ?", (drive_id,))
            return True

    def delete_drive(self, drive_id: int) -> bool:
        """Remove a drive; its positions stay as plain (parked) positions."""
        with self._lock:
            self.execute("UPDATE positions SET drive_id = NULL WHERE drive_id = ?", (drive_id,))
            return self.execute("DELETE FROM drives WHERE id = ?", (drive_id,)) > 0

    def open_drives(self, car_id: int) -> list[dict]:
        return self.query("SELECT * FROM drives WHERE car_id = ? AND end_ts IS NULL", (car_id,))

    # ------------------------------------------------------------------
    # Charges
    # ------------------------------------------------------------------
    def open_charge(self, car_id: int, sample: dict, kind: str, brand: str | None) -> int:
        return self.execute(
            "INSERT INTO charges (car_id, start_ts, lat, lon, kind, charger_brand, odometer_km, start_soc,"
            " start_range_km) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                car_id,
                sample["ts"],
                sample.get("lat"),
                sample.get("lon"),
                kind,
                brand,
                sample.get("odometer_km"),
                sample.get("soc"),
                sample.get("range_km"),
            ),
        )

    def add_charge_sample(self, charge_id: int, sample: dict) -> None:
        values = [sample.get(column) for column in SAMPLE_COLUMNS]
        self.execute(
            f"INSERT INTO charge_samples (charge_id, {', '.join(SAMPLE_COLUMNS)})"
            f" VALUES (?, {', '.join('?' * len(SAMPLE_COLUMNS))})",
            (charge_id, *values),
        )

    def charge_samples(self, charge_id: int, max_points: int = 0) -> list[dict]:
        rows = self.query(
            f"SELECT {', '.join(SAMPLE_COLUMNS)} FROM charge_samples WHERE charge_id = ? ORDER BY ts, id",
            (charge_id,),
        )
        return _stride(rows, max_points)

    def _charge_totals(self, samples: list[dict], fragment: bool) -> tuple[float, float]:
        """kWh added (the car's counter) and drawn from the charger (power over time) over a session's samples."""
        added = counter_energy([s["energy_added_kwh"] for s in samples], fragment)
        # Energy drawn from the charger: power over time, skipping gaps in the samples
        used = 0.0
        for previous, current in itertools.pairwise(samples):
            gap = current["ts"] - previous["ts"]
            if 0 < gap <= MAX_SAMPLE_GAP and previous["power_kw"]:
                used += previous["power_kw"] * gap / 3600.0
        # The car can't have stored more than it drew; whole-kW power readings undercount
        return added, max(used, added)

    def _counter_at(self, charge_id: int, last: bool) -> float | None:
        """The first (or last) reading of the car's energy counter during a charge."""
        order = "DESC" if last else "ASC"
        row = self.query_one(
            "SELECT energy_added_kwh AS kwh FROM charge_samples WHERE charge_id = ? AND energy_added_kwh IS NOT NULL"
            f" ORDER BY ts {order}, id {order} LIMIT 1",
            (charge_id,),
        )
        return row["kwh"] if row else None

    def previous_plugged_charge(self, car_id: int, before_ts: int, lat, lon, counter: float | None) -> dict | None:
        """The finished charge just before before_ts that a session starting then continues: the car didn't
        drive in between, is at the same place, and its energy counter ran on from that charge's last reading
        (Tesla's counter starts from zero only with a new plug-in)."""
        if counter is None:
            return None
        previous = self.query_one(
            "SELECT * FROM charges WHERE car_id = ? AND end_ts IS NOT NULL AND end_ts <= ?"
            " ORDER BY end_ts DESC LIMIT 1",
            (car_id, before_ts),
        )
        if not previous:
            return None
        drove = self.query_one(
            "SELECT 1 FROM drives WHERE car_id = ? AND start_ts >= ? AND start_ts <= ? LIMIT 1",
            (car_id, previous["end_ts"], before_ts),
        )
        apart = _km_apart(previous["lat"], previous["lon"], lat, lon)
        if drove or apart is None or apart > SAME_PLACE_KM:
            return None
        last = self._counter_at(previous["id"], last=True)
        if last is None or last < COUNTER_RESET_KWH or counter < last - MIN_CHARGE_KWH:
            return None
        return previous

    def resumable_charge(self, car_id: int, sample: dict, counter: float | None) -> int | None:
        """The charge a session starting now picks up again after a pause (charging stopped and resumed
        while plugged in), or None to open a new one."""
        previous = self.previous_plugged_charge(car_id, sample["ts"], sample.get("lat"), sample.get("lon"), counter)
        if previous and sample["ts"] - previous["end_ts"] <= RESUME_GAP and previous["billed_amount"] is None:
            return previous["id"]
        return None

    def reopen_charge(self, charge_id: int) -> None:
        self.execute("UPDATE charges SET end_ts = NULL WHERE id = ?", (charge_id,))

    def close_charge(self, charge_id: int, cost_per_kwh: float, fragment: bool = False) -> dict | None:
        """Finish a charging session. Returns the charge, or None when nothing was charged.

        `fragment`: the samples may be a later part of a session that was logged in pieces (an import of
        sessions the source never finished). The car's counter runs on over those, so only its rise counts.
        A session that continues the plug-in of the charge before it is such a part too."""
        with self._lock:
            charge = self.query_one("SELECT * FROM charges WHERE id = ?", (charge_id,))
            if not charge:
                return None
            samples = self.charge_samples(charge_id)
            if not fragment and samples:
                first_counter = self._counter_at(charge_id, last=False)
                previous = self.previous_plugged_charge(
                    charge["car_id"], charge["start_ts"], charge["lat"], charge["lon"], first_counter
                )
                fragment = previous is not None
            added, used = self._charge_totals(samples, fragment)
            if not samples or added < MIN_CHARGE_KWH:
                self.execute("DELETE FROM charges WHERE id = ?", (charge_id,))
                return None

            first, last = samples[0], samples[-1]
            temps = [s["outside_temp"] for s in samples if s["outside_temp"] is not None]
            cost = charge["cost"]
            if not charge["cost_manual"]:
                rate, have_rate = self.effective_rate(charge["place"], charge["lat"], charge["lon"], cost_per_kwh)
                cost = round(used * rate, 2) if have_rate else None
            self.execute(
                "UPDATE charges SET end_ts = ?, end_soc = ?, start_range_km = COALESCE(start_range_km, ?),"
                " end_range_km = ?, energy_added_kwh = ?, energy_used_kwh = ?, power_max_kw = ?, duration_s = ?,"
                " outside_temp_avg = ?, cost = ? WHERE id = ?",
                (
                    last["ts"],
                    last["soc"],
                    first["range_km"],
                    last["range_km"],
                    added,
                    used,
                    max((s["power_kw"] or 0.0 for s in samples), default=None),
                    last["ts"] - charge["start_ts"],
                    statistics.fmean(temps) if temps else None,
                    cost,
                    charge_id,
                ),
            )
            if first["soc"] is not None:
                self.execute(
                    "UPDATE charges SET start_soc = COALESCE(start_soc, ?) WHERE id = ?", (first["soc"], charge_id)
                )
            ideal = [sample["ideal_range_km"] for sample in samples if sample["ideal_range_km"] is not None]
            if ideal:
                self.execute(
                    "UPDATE charges SET start_ideal_range_km = ?, end_ideal_range_km = ? WHERE id = ?",
                    (ideal[0], ideal[-1], charge_id),
                )
            return self.query_one("SELECT * FROM charges WHERE id = ?", (charge_id,))

    def repair_resumed_charges(self) -> int:
        """Correct sessions recorded before 0.7.1 that continued a paused charge: they booked the car's
        counter from zero, so the kWh charged before the pause counted twice. Only their kWh (and an
        estimated cost, in proportion) change; a session that would end up with nothing is left as it is.
        Returns how many were corrected. Running it again changes nothing."""
        fixed = 0
        with self._lock:
            for charge in self.query("SELECT * FROM charges WHERE end_ts IS NOT NULL ORDER BY car_id, start_ts"):
                first_counter = self._counter_at(charge["id"], last=False)
                if not self.previous_plugged_charge(
                    charge["car_id"], charge["start_ts"], charge["lat"], charge["lon"], first_counter
                ):
                    continue
                added, used = self._charge_totals(self.charge_samples(charge["id"]), fragment=True)
                if added < MIN_CHARGE_KWH or (charge["energy_added_kwh"] or 0.0) - added < MIN_CHARGE_KWH:
                    continue
                cost = charge["cost"]
                if cost is not None and not charge["cost_manual"] and charge["billed_amount"] is None:
                    old_used = charge["energy_used_kwh"] or 0.0
                    cost = round(cost * used / old_used, 2) if old_used > 0 else cost
                self.execute(
                    "UPDATE charges SET energy_added_kwh = ?, energy_used_kwh = ?, cost = ? WHERE id = ?",
                    (added, used, cost, charge["id"]),
                )
                fixed += 1
        if fixed:
            logger.info(f"Corrected {fixed} charging session(s) that counted the energy before a pause twice")
        return fixed

    def note_charge_hardware(self, charge_id: int, cable: str | None, charger_type: str | None) -> None:
        """Remember the cable and charger of a session; the car can report them only after a few readings."""
        if cable or charger_type:
            self.execute(
                "UPDATE charges SET conn_charge_cable = COALESCE(?, conn_charge_cable),"
                " fast_charger_type = COALESCE(?, fast_charger_type) WHERE id = ?",
                (cable, charger_type, charge_id),
            )

    def open_charges(self, car_id: int) -> list[dict]:
        return self.query("SELECT * FROM charges WHERE car_id = ? AND end_ts IS NULL", (car_id,))

    def unbilled_fast_charges(self, since_ts: int) -> list[dict]:
        """Recent fast-charge sessions for which Tesla's bill hasn't been found (yet)."""
        return self.query(
            "SELECT charges.id, charges.car_id, charges.start_ts, charges.end_ts, cars.vin FROM charges"
            " JOIN cars ON cars.id = charges.car_id"
            " WHERE charges.kind = 'DC' AND charges.end_ts IS NOT NULL AND charges.end_ts >= ?"
            " AND charges.billed_amount IS NULL AND cars.vin IS NOT NULL ORDER BY charges.start_ts",
            (since_ts,),
        )

    def set_billed(self, charge_id: int, amount: float, currency: str, as_cost: bool) -> None:
        """Store what Tesla billed for a session; as_cost also makes it the session's cost (unless set by hand)."""
        with self._lock:
            self.execute(
                "UPDATE charges SET billed_amount = ?, billed_currency = ? WHERE id = ?", (amount, currency, charge_id)
            )
            if as_cost:
                self.execute("UPDATE charges SET cost = ? WHERE id = ? AND cost_manual = 0", (amount, charge_id))

    # ------------------------------------------------------------------
    # Charging price tags (a named rate per kWh, mapped to locations)
    # ------------------------------------------------------------------
    def list_tags(self) -> list[dict]:
        return self.query("SELECT name, price_per_kwh, pos FROM charge_tags ORDER BY pos, name")

    def upsert_tag(self, name: str, price_per_kwh: float | None) -> None:
        name = name.strip()
        with self._lock:
            if self.query_one("SELECT 1 FROM charge_tags WHERE name = ?", (name,)):
                self.execute("UPDATE charge_tags SET price_per_kwh = ? WHERE name = ?", (price_per_kwh, name))
            else:
                row = self.query_one("SELECT COALESCE(MAX(pos) + 1, 0) AS p FROM charge_tags")
                self.execute(
                    "INSERT INTO charge_tags (name, price_per_kwh, pos) VALUES (?, ?, ?)",
                    (name, price_per_kwh, row["p"] if row else 0),
                )

    def delete_tag(self, name: str) -> None:
        # The location mappings for this tag are removed too (ON DELETE CASCADE).
        self.execute("DELETE FROM charge_tags WHERE name = ?", (name,))

    def location_tag(self, key: str) -> str | None:
        row = self.query_one("SELECT tag FROM location_tags WHERE place_key = ?", (key,))
        return row["tag"] if row else None

    def set_location_tag(self, key: str, tag: str | None) -> None:
        with self._lock:
            if tag:
                self.execute(
                    "INSERT INTO location_tags (place_key, tag) VALUES (?, ?)"
                    " ON CONFLICT(place_key) DO UPDATE SET tag = excluded.tag",
                    (key, tag),
                )
            else:
                self.execute("DELETE FROM location_tags WHERE place_key = ?", (key,))

    def effective_rate(
        self, place: str | None, lat: float | None, lon: float | None, default_rate: float
    ) -> tuple[float, bool]:
        """The price per kWh for a location: its tag's rate if set, else the default. Second value is whether a rate applies at all (False means leave the cost unknown)."""
        rate, have = default_rate, default_rate > 0
        key = place_key(place, lat, lon)
        if key:
            tag = self.location_tag(key)
            if tag:
                row = self.query_one("SELECT price_per_kwh FROM charge_tags WHERE name = ?", (tag,))
                if row and row["price_per_kwh"] is not None:
                    rate, have = row["price_per_kwh"], True
        return rate, have

    def recompute_charge_costs(self, default_rate: float) -> int:
        """Recalculate the cost of every estimated session from its location's tag. Returns the number changed."""
        with self._lock:
            tags = {t["name"]: t["price_per_kwh"] for t in self.list_tags()}
            locs = {r["place_key"]: r["tag"] for r in self.query("SELECT place_key, tag FROM location_tags")}
            charges = self.query(
                "SELECT id, place, lat, lon, energy_used_kwh, cost FROM charges"
                " WHERE end_ts IS NOT NULL AND cost_manual = 0 AND billed_amount IS NULL"
            )
            changed = 0
            for charge in charges:
                rate, have = default_rate, default_rate > 0
                key = place_key(charge["place"], charge["lat"], charge["lon"])
                tag = locs.get(key) if key else None
                if tag and tags.get(tag) is not None:
                    rate, have = tags[tag], True
                cost = round((charge["energy_used_kwh"] or 0.0) * rate, 2) if have else None
                if cost != charge["cost"]:
                    self.execute("UPDATE charges SET cost = ? WHERE id = ?", (cost, charge["id"]))
                    changed += 1
            return changed

    def charge_near(self, car_id: int, start_ts: int, window: int = 600) -> int | None:
        """The id of a charge of this car that started within `window` seconds of start_ts, if any."""
        row = self.query_one(
            "SELECT id FROM charges WHERE car_id = ? AND ABS(start_ts - ?) < ? ORDER BY ABS(start_ts - ?) LIMIT 1",
            (car_id, start_ts, window, start_ts),
        )
        return row["id"] if row else None

    def charge_has_samples(self, charge_id: int) -> bool:
        """Whether a charge has a recorded power curve (so it's detailed, not a sparse billing-only entry)."""
        return self.query_one("SELECT 1 FROM charge_samples WHERE charge_id = ? LIMIT 1", (charge_id,)) is not None

    def import_charge(self, car_id: int, charge: dict) -> int | None:
        """Insert a charging session read from Tesla's history. Returns its id, or None if already present."""
        with self._lock:
            if self.charge_near(car_id, charge["start_ts"]):
                return None
            columns = (
                "start_ts",
                "end_ts",
                "lat",
                "lon",
                "place",
                "kind",
                "charger_brand",
                "odometer_km",
                "energy_added_kwh",
                "energy_used_kwh",
                "power_max_kw",
                "duration_s",
                "cost",
                "billed_amount",
                "billed_currency",
            )
            values = [charge.get(column) for column in columns]
            return self.execute(
                f"INSERT INTO charges (car_id, cost_manual, {', '.join(columns)})"
                f" VALUES (?, 0, {', '.join('?' * len(columns))})",
                (car_id, *values),
            )

    def learn_efficiency(self, car_id: int) -> float | None:
        """Rated consumption (Wh per km of rated range), from what recent charges added.

        Each session tells how many kWh the car booked for the range it gained;
        the median of the last sessions ignores the odd one out. The car's energy counter
        lags behind its range when charging starts (range already rising, counter still at
        zero), so both are taken over the same samples: from the first one that counts energy.
        """
        ratios = []
        for charge in self.query(
            "SELECT id FROM charges WHERE car_id = ? AND end_ts IS NOT NULL AND energy_added_kwh >= ?"
            " AND end_range_km - start_range_km >= ? ORDER BY start_ts DESC LIMIT 25",
            (car_id, MIN_LEARN_KWH, MIN_LEARN_KM),
        ):
            samples = [
                s
                for s in self.charge_samples(charge["id"])
                if s["range_km"] is not None and s["energy_added_kwh"] is not None
            ]
            start = next((i for i, s in enumerate(samples) if s["energy_added_kwh"] > 0), None)
            if start is None:
                continue
            samples = samples[start:]
            kwh = counter_energy([s["energy_added_kwh"] for s in samples], fragment=True)
            km = samples[-1]["range_km"] - samples[0]["range_km"]
            if kwh >= MIN_LEARN_KWH and km >= MIN_LEARN_KM:
                ratios.append(kwh / km * 1000.0)
        # Outside this band it's a measurement glitch, not a car
        ratios = [r for r in ratios if 90.0 <= r <= 350.0]
        return round(statistics.median(ratios), 1) if ratios else None

    def apply_efficiency(self, car_id: int, efficiency_wh_km: float) -> None:
        """Store the rated consumption and recalculate the energy of every drive with it."""
        with self._lock:
            self.execute("UPDATE cars SET efficiency_wh_km = ? WHERE id = ?", (efficiency_wh_km, car_id))
            self.execute(
                "UPDATE drives SET energy_kwh = (start_range_km - end_range_km) * ? / 1000.0"
                " WHERE car_id = ? AND start_range_km IS NOT NULL AND end_range_km IS NOT NULL",
                (efficiency_wh_km, car_id),
            )

    # ------------------------------------------------------------------
    # States and software versions
    # ------------------------------------------------------------------
    def set_state(self, car_id: int, state: str, ts: int) -> bool:
        """Record a state change. Returns False when the car already was in this state."""
        with self._lock:
            current = self.query_one(
                "SELECT id, state FROM states WHERE car_id = ? AND end_ts IS NULL ORDER BY start_ts DESC LIMIT 1",
                (car_id,),
            )
            if current and current["state"] == state:
                return False
            self.execute("UPDATE states SET end_ts = ? WHERE car_id = ? AND end_ts IS NULL", (ts, car_id))
            self.execute("INSERT INTO states (car_id, state, start_ts) VALUES (?, ?, ?)", (car_id, state, ts))
            return True

    def current_state(self, car_id: int) -> dict | None:
        return self.query_one(
            "SELECT state, start_ts FROM states WHERE car_id = ? AND end_ts IS NULL ORDER BY start_ts DESC LIMIT 1",
            (car_id,),
        )

    def record_version(self, car_id: int, version: str, ts: int) -> bool:
        """Note the car's software version. Returns True when it is a new one."""
        with self._lock:
            last = self.query_one("SELECT version FROM updates WHERE car_id = ? ORDER BY ts DESC LIMIT 1", (car_id,))
            if last and last["version"] == version:
                return False
            self.execute("INSERT INTO updates (car_id, version, ts) VALUES (?, ?, ?)", (car_id, version, ts))
            return True

    # ------------------------------------------------------------------
    # Addresses (reverse geocoding cache)
    # ------------------------------------------------------------------
    def get_address(self, key: str) -> dict | None:
        return self.query_one("SELECT * FROM addresses WHERE key = ?", (key,))

    def put_address(self, key: str, label: str | None, city: str | None, country: str | None) -> None:
        self.execute(
            "INSERT OR REPLACE INTO addresses (key, label, city, country, fetched_at) VALUES (?, ?, ?, ?, ?)",
            (key, label, city, country, int(time.time())),
        )
