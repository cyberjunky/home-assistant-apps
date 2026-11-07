"""SQLite storage for TeslaMeter.

Everything is stored in metric units (km, km/h, kW, kWh, °C) and UTC unix
seconds. One connection, guarded by a lock: the vehicle monitors write from the
event loop, statistics run in a worker thread.
"""

import itertools
import json
import logging
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

logger = logging.getLogger(__name__)

DB_FILE = "teslameter.db"

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

SCHEMA = """
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
    added_at INTEGER NOT NULL
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
    drive_id INTEGER REFERENCES drives(id) ON DELETE SET NULL
);
CREATE INDEX positions_car_ts ON positions(car_id, ts);
CREATE INDEX positions_drive ON positions(drive_id, ts) WHERE drive_id IS NOT NULL;

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
    ascent_m REAL, descent_m REAL
);
CREATE INDEX drives_car_ts ON drives(car_id, start_ts);

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
    billed_currency TEXT
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
    outside_temp REAL
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
INSERT INTO charge_tags (name, price_per_kwh, pos) VALUES ('Home', NULL, 0), ('Free', 0, 1), ('Public', NULL, 2);
INSERT INTO location_tags (place_key, tag) VALUES ('home', 'Home');
"""

# Stored in the database file. There is no upgrade path yet: a database of another
# layout is refused at start, with a message, instead of failing on a missing column later.
SCHEMA_VERSION = 3

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
)


def place_key(place: str | None, lat: float | None, lon: float | None) -> str | None:
    """A stable key for a charging location: its place name, else coordinates to ~100 m."""
    if place and place.strip():
        return place.strip().lower()
    if lat is not None and lon is not None:
        return f"@{round(lat, 3)},{round(lon, 3)}"
    return None


def _stride(rows: list, max_points: int) -> list:
    """Thin a series to about max_points rows, always keeping the first and the last."""
    if max_points <= 0 or len(rows) <= max_points:
        return rows
    step = len(rows) / (max_points - 1)
    picked = [rows[min(len(rows) - 1, round(i * step))] for i in range(max_points - 1)]
    picked.append(rows[-1])
    return picked


class DatabaseLayoutError(Exception):
    """The database file has the layout of another TeslaMeter version."""


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
        elif version != SCHEMA_VERSION:
            self._conn.close()
            raise DatabaseLayoutError(
                f"{self.path} was made by another version of TeslaMeter (layout {version}, this version needs"
                f" {SCHEMA_VERSION}). Remove that file (or uninstall and reinstall the add-on) to start with a new one"
            )

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
        """Set car columns (name, model, trim, color, wheels, efficiency_wh_km); None values are skipped."""
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        self.execute(f"UPDATE cars SET {assignments} WHERE id = ?", (*fields.values(), car_id))

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
        values = [sample.get(column) for column in POSITION_COLUMNS]
        return self.execute(
            f"INSERT INTO positions (car_id, drive_id, {', '.join(POSITION_COLUMNS)})"
            f" VALUES (?, ?, {', '.join('?' * len(POSITION_COLUMNS))})",
            (car_id, drive_id, *values),
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
        ids = [row["id"] for row in self.query(
            "SELECT id FROM positions WHERE car_id = ? AND ts >= ? AND lat IS NOT NULL ORDER BY ts, id",
            (car_id, since_ts),
        )]
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
            return self.query_one("SELECT * FROM drives WHERE id = ?", (drive_id,))

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

    def close_charge(self, charge_id: int, cost_per_kwh: float) -> dict | None:
        """Finish a charging session. Returns the charge, or None when nothing was charged."""
        with self._lock:
            charge = self.query_one("SELECT * FROM charges WHERE id = ?", (charge_id,))
            if not charge:
                return None
            samples = self.charge_samples(charge_id)
            energies = [s["energy_added_kwh"] or 0.0 for s in samples]
            # The car's counter restarts with each session; a first reading can still
            # show the previous session's total, so only count from the lowest reading on
            added = max(energies[energies.index(min(energies)) :], default=0.0)
            if not samples or added < MIN_CHARGE_KWH:
                self.execute("DELETE FROM charges WHERE id = ?", (charge_id,))
                return None

            # Energy drawn from the charger: power over time, skipping gaps in the samples
            used = 0.0
            for previous, current in itertools.pairwise(samples):
                gap = current["ts"] - previous["ts"]
                if 0 < gap <= MAX_SAMPLE_GAP and previous["power_kw"]:
                    used += previous["power_kw"] * gap / 3600.0
            # The car can't have stored more than it drew; whole-kW power readings undercount
            used = max(used, added)

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
            return self.query_one("SELECT * FROM charges WHERE id = ?", (charge_id,))

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
        the median of the last sessions ignores the odd one out.
        """
        rows = self.query(
            "SELECT energy_added_kwh AS kwh, end_range_km - start_range_km AS km FROM charges"
            " WHERE car_id = ? AND end_ts IS NOT NULL AND energy_added_kwh >= ?"
            " AND end_range_km - start_range_km >= ? ORDER BY start_ts DESC LIMIT 25",
            (car_id, MIN_LEARN_KWH, MIN_LEARN_KM),
        )
        ratios = [row["kwh"] / row["km"] * 1000.0 for row in rows]
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
