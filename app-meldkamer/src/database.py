"""Database operations with parameterized queries and CRUD management."""

import contextlib
import csv
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from . import message_fields, vehicle_types
from .abbreviation_import import DISCIPLINE_ABBREVIATION_OVERRIDES

logger = logging.getLogger(__name__)


class _LockedCursor:
    """Cursor proxy that serializes every call through a shared RLock.

    sqlite3 cursors on a shared connection (check_same_thread=False) corrupt
    each other's result sets when used concurrently from multiple threads,
    so every call goes through the lock.
    """

    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock):
        self._cursor = cursor
        self._lock = lock

    def execute(self, *args, **kwargs) -> "_LockedCursor":
        with self._lock:
            self._cursor.execute(*args, **kwargs)
        return self

    def executemany(self, *args, **kwargs) -> "_LockedCursor":
        with self._lock:
            self._cursor.executemany(*args, **kwargs)
        return self

    def fetchone(self):
        with self._lock:
            return self._cursor.fetchone()

    def fetchall(self) -> list:
        with self._lock:
            return self._cursor.fetchall()

    def close(self) -> None:
        with self._lock:
            self._cursor.close()

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int:
        return self._cursor.lastrowid


class _LockedConnection:
    """Connection proxy that serializes calls through a shared RLock."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _nl_coordinates(latitude, longitude) -> tuple[float | None, float | None]:
    """Coordinates as floats when both are set and inside the Netherlands, else (None, None)."""
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None, None
    if 50.75 <= lat <= 53.55 and 3.36 <= lon <= 7.21:
        return lat, lon
    return None, None


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance in km between two (latitude, longitude) points (equirectangular, fine within the Netherlands)."""
    x = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(x, b[0] - a[0]) * 111.2


def province_key(province: str | None) -> str:
    """A province name for comparing: "Fryslân", "Friesland" and " friesland" are the same."""
    key = unicodedata.normalize("NFKD", province or "").encode("ascii", "ignore").decode().casefold().strip()
    return "friesland" if key == "fryslan" else key


class Database:
    """SQLite database for capcodes, places, streets, and geocode cache."""

    SCHEMA = """
    -- Places/Cities table. A place is its name in its province: Velp (Gelderland) and
    -- Velp (Noord-Brabant) are two places. Places without a province have province ''
    CREATE TABLE IF NOT EXISTS places (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT NOT NULL,
        abbreviation TEXT,
        province TEXT NOT NULL DEFAULT '',
        latitude REAL,
        longitude REAL,
        UNIQUE(city, province)
    );

    -- Streets table (linked to cities for better address matching)
    CREATE TABLE IF NOT EXISTS streets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        street TEXT NOT NULL,
        city_id INTEGER,
        postalcode TEXT,
        latitude REAL,
        longitude REAL,
        FOREIGN KEY (city_id) REFERENCES places(id)
    );
    CREATE INDEX IF NOT EXISTS idx_streets_street ON streets(street);
    CREATE INDEX IF NOT EXISTS idx_streets_city ON streets(city_id);
    CREATE INDEX IF NOT EXISTS idx_streets_lookup ON streets(street, city_id);

    -- Capcodes table
    CREATE TABLE IF NOT EXISTS capcodes (
        capcode TEXT PRIMARY KEY,
        discipline TEXT,
        region TEXT,
        location TEXT,
        description TEXT,
        remark TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_capcodes_discipline ON capcodes(discipline);
    CREATE INDEX IF NOT EXISTS idx_capcodes_region ON capcodes(region);

    -- Messages table (persistent message history)
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        body TEXT,
        raw_message TEXT,
        capcodes TEXT,
        group_id TEXT,
        message_type TEXT,
        priority INTEGER,
        discipline TEXT,
        region TEXT,
        city TEXT,
        street TEXT,
        address TEXT,
        latitude REAL,
        longitude REAL,
        receivers TEXT,
        remarks TEXT,
        distance_km REAL,
        incident_id TEXT,
        repeats INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
    CREATE INDEX IF NOT EXISTS idx_messages_city ON messages(city);
    CREATE INDEX IF NOT EXISTS idx_messages_discipline ON messages(discipline);

    -- Geocodes cache table
    CREATE TABLE IF NOT EXISTS geocodes (
        query TEXT PRIMARY KEY,
        datatype TEXT,
        latitude TEXT,
        longitude TEXT,
        postalcode TEXT,
        street TEXT,
        city TEXT,
        address TEXT,
        mapurl TEXT
    );

    -- Abbreviations table (from Bommel)
    CREATE TABLE IF NOT EXISTS abbreviations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        abbreviation TEXT UNIQUE NOT NULL,
        full_text TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_abbreviations_abbrev ON abbreviations(abbreviation);

    -- TTS replacements table (regex patterns for text-to-speech)
    CREATE TABLE IF NOT EXISTS tts_replacements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern TEXT UNIQUE NOT NULL,
        replacement TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1
    );

    -- Global message filters (wildcard text patterns / exact capcode matches)
    CREATE TABLE IF NOT EXISTS ignore_text (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern TEXT UNIQUE NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS ignore_capcodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        capcode TEXT UNIQUE NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1
    );

    -- Known vehicles: what a vehicle number is. source = 'user' (added or
    -- edited by hand or CSV import), 'capcode' (named in the capcode list)
    -- or 'message' (named with its type in a message, e.g. "TS 07-1782").
    -- Only 'user' rows are kept when the others are refreshed.
    CREATE TABLE IF NOT EXISTS vehicles (
        number TEXT PRIMARY KEY,
        category TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user'
    );

    -- User-uploaded vehicle photos, keyed by exact vehicle number ('number')
    -- or by broad category ('category', e.g. "ambulance") as a fallback.
    CREATE TABLE IF NOT EXISTS vehicle_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_type TEXT NOT NULL,
        key_value TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        mime_type TEXT NOT NULL,
        image BLOB NOT NULL,
        UNIQUE(key_type, key_value)
    );

    -- Place-name codes in messages (UTRECH -> Utrecht); seeded from city_abbreviations.py
    CREATE TABLE IF NOT EXISTS city_abbreviations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        abbreviation TEXT UNIQUE NOT NULL,
        city TEXT NOT NULL
    );

    -- Talk group codes (BRT-01): kind 'discipline' (B -> Brandweer) or 'region' (RT -> Rotterdam)
    CREATE TABLE IF NOT EXISTS talkgroup_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        UNIQUE (kind, code)
    );

    -- Which built-in rows were added once: a default you changed or deleted is not added
    -- again at the next start or update
    CREATE TABLE IF NOT EXISTS speech_seeded (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        PRIMARY KEY (kind, key)
    );
    """

    # Lookup tables with defaults (seeded once): columns, the columns that identify a row,
    # and the ones stored in capitals. The same tables and columns as the FireServiceRota app.
    LOOKUP_TABLES = {
        "city_abbreviations": {"cols": ("abbreviation", "city"), "key": ("abbreviation",), "upper": ("abbreviation",)},
        "talkgroup_codes": {"cols": ("kind", "code", "name"), "key": ("kind", "code"), "upper": ("code",)},
    }

    def __init__(self, db_path: str = "/data/meldkamer.sqlite3"):
        self.db_path = db_path
        self._ensure_database()
        self._lock = threading.RLock()
        self._cursors = {}
        self._text_caches = {}
        self._vehicle_cache: dict[str, dict] | None = None
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.conn = _LockedConnection(self._conn, self._lock)
        self._migrate_schema()
        # Capcodes imported before the vehicles table existed
        self.learn_vehicles_from_capcodes()

    @property
    def cursor(self) -> _LockedCursor:
        """Return a locked cursor bound to the calling thread.

        The connection is shared (check_same_thread=False), but each thread
        gets its own cursor so an execute/fetch sequence cannot be handed
        another thread's result set, and every call is serialized through
        the shared RLock (sqlite3 cursors are not concurrency-safe even
        across per-thread cursors on one connection).
        """
        ident = threading.get_ident()
        cur = self._cursors.get(ident)
        if cur is None:
            with self._lock:
                cur = _LockedCursor(self._conn.cursor(), self._lock)
                self._cursors[ident] = cur
        return cur

    def _commit(self) -> None:
        """Commit the shared connection, serialized across threads."""
        with self._lock:
            self.conn.commit()

    @contextlib.contextmanager
    def _transaction(self):
        """Run a block in one explicit transaction, rolled back on any exception.

        Holds the lock for the whole block so no other thread's statements or
        commits can interleave with (or commit) a half-finished bulk change.
        """
        with self._lock:
            if self._conn.in_transaction:
                self._conn.commit()
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    # =========================================================================
    # TEXT MATCHING CACHES (places / streets / abbreviations)
    # =========================================================================

    def invalidate_text_caches(self) -> None:
        """Drop cached text-matching data after places/streets/abbreviations change."""
        with self._lock:
            self._text_caches.clear()

    @staticmethod
    def fold_accents(text: str) -> str:
        """Drop accents ("Belgiëlaan" -> "Belgielaan", "Röellstraat" -> "Roellstraat").

        P2000 messages are sent without accents, while BAG street and place names have them,
        so names and message text are compared without.
        """
        decomposed = unicodedata.normalize("NFKD", text)
        return "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    @staticmethod
    def _first_word_key(name: str) -> str | None:
        """Lowercased leading word of a name, used as prefilter index key."""
        match = re.match(r"\w+", name)
        return match.group(0).lower() if match else None

    def _get_text_cache(self, name: str, table: str, builder) -> Any:
        """Return cached matching data for a table, rebuilding when stale.

        The cache is rebuilt when the table row count changes (this also
        catches bulk imports that bypass the CRUD methods) or after
        invalidate_text_caches(). Row counts are re-checked at most once
        every 2 seconds to keep per-message overhead bounded.
        """
        with self._lock:
            now = time.monotonic()
            entry = self._text_caches.get(name)
            if entry is not None and now - entry["checked_at"] < 2.0:
                return entry["data"]
            count = self.cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if entry is not None and entry["count"] == count:
                entry["checked_at"] = now
                return entry["data"]
            data = builder()
            self._text_caches[name] = {"count": count, "checked_at": now, "data": data}
            return data

    def _build_match_index(self, rows, name_of, min_length: int = 1) -> dict[str, Any]:
        """Build a first-word prefilter index over rows for word-boundary matching.

        Returns {"index": {first_word: [item, ...]}, "fallback": [item, ...]}
        where each item keeps the original row order, the source row and the
        escaped name. Patterns are compiled lazily at match time so memory
        stays bounded after large (BAG) imports.
        """
        index = {}
        fallback = []
        order = 0
        for row in rows:
            name = name_of(row)
            if not name or len(name) < min_length:
                continue
            name = self.fold_accents(name)
            item = {"order": order, "row": row, "escaped": re.escape(name), "folded": name.lower()}
            order += 1
            key = self._first_word_key(name)
            if key:
                index.setdefault(key, []).append(item)
            else:
                fallback.append(item)
        return {"index": index, "fallback": fallback}

    @classmethod
    def _match_candidates(cls, cache_part: dict[str, Any], text: str) -> list[dict]:
        """Collect candidate entries whose first word appears in the text.

        Candidates keep the original row order so the first pattern hit
        matches the previous per-row scan semantics.
        """
        words = set(re.findall(r"\w+", cls.fold_accents(text).lower()))
        candidates = [item for word in words for item in cache_part["index"].get(word, ())]
        candidates.extend(cache_part["fallback"])
        candidates.sort(key=lambda item: item["order"])
        return candidates

    def _build_places_cache(self) -> dict[str, Any]:
        """Load places once and build first-word prefilter indexes."""
        abbrev_rows = self.cursor.execute(
            "SELECT DISTINCT city, abbreviation FROM places WHERE abbreviation != ''"
        ).fetchall()
        # A name in two provinces (Velp) is one name here; find_place() picks the place
        city_rows = self.cursor.execute("SELECT DISTINCT city FROM places ORDER BY LENGTH(city) DESC").fetchall()
        by_normalized = {}
        for row in city_rows:
            by_normalized.setdefault(self.normalize_city(row[0]), row[0])
        provinces: dict[str, set[str]] = {}
        for row in self.cursor.execute("SELECT city, province FROM places").fetchall():
            provinces.setdefault(row[0], set()).add(province_key(row[1]))
        return {
            "abbrevs": self._build_match_index(abbrev_rows, lambda row: row[1]),
            "cities": self._build_match_index(city_rows, lambda row: row[0]),
            "by_normalized": by_normalized,
            "provinces": provinces,
        }

    def _build_streets_cache(self) -> dict[str, Any]:
        """Load streets once and build a first-word prefilter index."""
        # Check if places has id column for JOIN
        try:
            self.cursor.execute("PRAGMA table_info(places)")
            has_id = "id" in {row[1] for row in self.cursor.fetchall()}
        except sqlite3.Error:
            has_id = False

        if has_id:
            rows = self.cursor.execute(
                """SELECT s.street, p.city, s.postalcode, p.province FROM streets s
                   LEFT JOIN places p ON s.city_id = p.id
                   ORDER BY LENGTH(s.street) DESC"""
            ).fetchall()
        else:
            rows = self.cursor.execute(
                "SELECT street, NULL as city, postalcode, NULL as province FROM streets ORDER BY LENGTH(street) DESC"
            ).fetchall()
        return self._build_match_index(rows, lambda row: row[0], min_length=3)

    def _build_abbreviations_cache(self) -> dict[str, str]:
        """Load all abbreviations once."""
        rows = self.cursor.execute("SELECT abbreviation, full_text FROM abbreviations").fetchall()
        return {row[0]: row[1] for row in rows}

    def _ensure_database(self) -> None:
        """Copy database from app directory if not exists in data."""
        if not os.path.exists(self.db_path):
            source = "/app/data/meldkamer.sqlite3"
            if os.path.exists(source):
                logger.info(f"Installing database to {self.db_path}")
                shutil.copy(source, self.db_path)
            else:
                logger.info(f"Creating new database at {self.db_path}")
                # Will be created when we connect

    def _migrate_schema(self) -> None:
        """Ensure all tables exist with proper schema."""
        try:
            # First, create any missing tables
            self.conn.executescript(self.SCHEMA)
            self._commit()

            # Then, add missing columns to existing tables (for upgrades)
            migrations = [
                # Places table migrations
                ("places", "province", "TEXT"),
                ("places", "latitude", "REAL"),
                ("places", "longitude", "REAL"),
                # Streets table migrations
                ("streets", "postalcode", "TEXT"),
                ("streets", "latitude", "REAL"),
                ("streets", "longitude", "REAL"),
                # Messages table migrations
                ("messages", "distance_km", "REAL"),
                ("messages", "incident_id", "TEXT"),
                ("messages", "repeats", "INTEGER NOT NULL DEFAULT 0"),
            ]

            for table, column, col_type in migrations:
                try:
                    self.cursor.execute(f"SELECT {column} FROM {table} LIMIT 1")
                except sqlite3.OperationalError:
                    # Column doesn't exist, add it
                    try:
                        self.cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                        logger.info(f"Added column {column} to {table}")
                    except sqlite3.OperationalError as e:
                        logger.warning(f"Could not add column {column} to {table}: {e}")

            # Indexes on migrated columns (SCHEMA runs before the columns exist on upgrades)
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_incident ON messages(incident_id)")
            # Places without a province have province '' since 1.4.11 (the column is NOT NULL now)
            self.cursor.execute("UPDATE places SET province = '' WHERE province IS NULL")
            self._commit()

            # SQLite cannot ADD COLUMN a PRIMARY KEY - rebuild legacy tables
            # that predate the id column (rowid is kept as id so existing
            # streets.city_id links survive)
            self._rebuild_table_with_id("places", ["city", "abbreviation", "province", "latitude", "longitude"])
            self._rebuild_table_with_id("streets", ["street", "city_id", "postalcode", "latitude", "longitude"])
            self._rebuild_places_per_province()
            self._ensure_unique_streets()
            self._seed_lookup_tables()
        except sqlite3.Error as e:
            logger.error(f"Schema migration failed: {e}")

    def _seed_lookup_tables(self) -> None:
        """The default place-name codes and talk group codes, each added once."""
        from .city_abbreviations import CITY_ABBREVIATIONS
        from .talkgroups import DEFAULT_TALKGROUP_CODES

        added = self.seed_defaults(
            "city_abbreviations", [{"abbreviation": a, "city": c} for c, a in CITY_ABBREVIATIONS.items()]
        )
        added += self.seed_defaults("talkgroup_codes", DEFAULT_TALKGROUP_CODES)
        if added:
            logger.info(f"Added {added} default place-name and talk group code(s)")

    def _table_sql(self, table: str) -> str:
        """Return the CREATE TABLE statement for a table from SCHEMA."""
        match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n    \);", self.SCHEMA, re.DOTALL)
        return match.group(0)

    def _rebuild_table_with_id(self, table: str, columns: list[str]) -> None:
        """Rebuild a legacy table without an id column (create, copy, drop, rename)."""
        with self._lock:
            existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not existing or "id" in existing:
                return
            copy_cols = [c for c in columns if c in existing]
            col_list = ", ".join(copy_cols)
            create_sql = self._table_sql(table).replace(f"EXISTS {table} (", f"EXISTS {table}_new (", 1)
            try:
                with self._transaction() as conn:
                    conn.execute(f"DROP TABLE IF EXISTS {table}_new")
                    conn.execute(create_sql)
                    # OR IGNORE: legacy rows may violate the new UNIQUE/NOT NULL constraints
                    conn.execute(
                        f"INSERT OR IGNORE INTO {table}_new (id, {col_list}) SELECT rowid, {col_list} FROM {table}"
                    )
                    conn.execute(f"DROP TABLE {table}")
                    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
                logger.info(f"Rebuilt table {table} with id column")
            except sqlite3.Error as e:
                logger.warning(f"Could not rebuild table {table} with id column: {e}")
                return
        # Recreate the indexes that were dropped together with the old table
        self.conn.executescript(self.SCHEMA)

    def _places_unique_on_name(self) -> bool:
        """Whether the places table is unique on the name alone (versions before 1.4.11)."""
        for index in self._conn.execute("PRAGMA index_list(places)").fetchall():
            if index[2]:  # unique
                columns = [row[2] for row in self._conn.execute(f"PRAGMA index_info('{index[1]}')").fetchall()]
                if columns == ["city"]:
                    return True
        return False

    def _rebuild_places_per_province(self) -> None:
        """Make places unique per (name, province) instead of per name (versions before 1.4.11).

        Every row is copied with its id, so streets keep their place and your own places,
        abbreviations and coordinates stay as they are. A place that already holds the
        streets of two same-named places is not split here (that needs the BAG data):
        importing BAG again for those provinces moves the streets to their own place
        (see move_namesake_streets). Such places are reported in the log.
        """
        with self._lock:
            if not self._places_unique_on_name():
                return
            create_sql = self._table_sql("places").replace("EXISTS places (", "EXISTS places_new (", 1)
            try:
                with self._transaction() as conn:
                    conn.execute("DROP TABLE IF EXISTS places_new")
                    conn.execute(create_sql)
                    # A plain INSERT: a row that doesn't fit fails the whole rebuild (rolled
                    # back, the old table stays as it was) instead of being skipped
                    conn.execute(
                        "INSERT INTO places_new (id, city, abbreviation, province, latitude, longitude) "
                        "SELECT id, city, abbreviation, COALESCE(province, ''), latitude, longitude FROM places"
                    )
                    before = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
                    after = conn.execute("SELECT COUNT(*) FROM places_new").fetchone()[0]
                    if before != after:
                        raise sqlite3.Error(f"copied {after} of {before} places")
                    conn.execute("DROP TABLE places")
                    conn.execute("ALTER TABLE places_new RENAME TO places")
                logger.info(f"Places are now unique per name and province ({after} places kept)")
            except sqlite3.Error as e:
                logger.warning(f"Could not make places unique per province: {e}")
                return
        self.conn.executescript(self.SCHEMA)
        mixed = self.mixed_places()
        if mixed:
            names = ", ".join(f"{p['city']} ({p['km']:.0f} km)" for p in mixed)
            logger.warning(
                f"{len(mixed)} place(s) hold the streets of two same-named places in different provinces: "
                f"{names}. Run Import BAG again for the provinces you imported to separate them."
            )

    def mixed_places(self, gap_km: float = 25.0) -> list[dict[str, Any]]:
        """Places whose located streets form groups more than gap_km apart.

        Versions before 1.4.11 stored same-named places in two provinces as one place, with
        the streets of both. The streets are put on a grid of about 5 km; neighbouring cells
        form one group, and a place with groups more than gap_km apart is reported, farthest
        first. Returns dicts with id, city, province and km (the distance between the groups).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT city_id, latitude, longitude FROM streets "
                "WHERE city_id IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"
            ).fetchall()
            places = {row[0]: (row[1], row[2]) for row in self._conn.execute("SELECT id, city, province FROM places")}
        cells: dict[int, set[tuple[int, int]]] = {}
        for city_id, lat, lon in rows:
            cells.setdefault(city_id, set()).add((int(lat // 0.05), int(lon // 0.08)))
        mixed = []
        for city_id, place_cells in cells.items():
            if city_id not in places or len(place_cells) < 2:
                continue
            centres = []
            todo = set(place_cells)
            while todo:
                stack = [todo.pop()]
                group = []
                while stack:
                    cell = stack.pop()
                    group.append(cell)
                    for near in [(cell[0] + dy, cell[1] + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]:
                        if near in todo:
                            todo.remove(near)
                            stack.append(near)
                centres.append(
                    (
                        sum(c[0] + 0.5 for c in group) / len(group) * 0.05,
                        sum(c[1] + 0.5 for c in group) / len(group) * 0.08,
                    )
                )
            if len(centres) < 2:
                continue
            km = max(_km(a, b) for i, a in enumerate(centres) for b in centres[i + 1 :])
            if km > gap_km:
                city, province = places[city_id]
                mixed.append({"id": city_id, "city": city, "province": province, "km": km})
        return sorted(mixed, key=lambda p: -p["km"])

    def _ensure_unique_streets(self) -> None:
        """Deduplicate streets and add the UNIQUE (street, city_id) index."""
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_streets_unique'"
            ).fetchone()
            if exists:
                return
            try:
                with self._transaction() as conn:
                    removed = conn.execute(
                        "DELETE FROM streets WHERE id NOT IN (SELECT MIN(id) FROM streets GROUP BY street, city_id)"
                    ).rowcount
                    conn.execute("CREATE UNIQUE INDEX idx_streets_unique ON streets(street, city_id)")
                if removed:
                    logger.info(f"Removed {removed} duplicate streets")
            except sqlite3.Error as e:
                logger.warning(f"Could not add unique street index: {e}")

    def merge_case_duplicate_streets(self) -> int:
        """Merge streets of one place that only differ in capitalisation ("De Akkers" / "de Akkers").

        BAG has some streets in two spellings. Per place, the variant with a position is kept
        (the oldest one if several have it), it takes over a postcode it lacks from another
        variant, and the others are removed. Runs at the end of a province import, so streets stored
        in two spellings by versions before 1.4.0 are merged when the province is imported
        again. Returns the number of streets removed.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, street, city_id, postalcode, latitude, longitude FROM streets ORDER BY id"
            ).fetchall()
            groups: dict[tuple, list] = {}
            for row in rows:
                groups.setdefault((row[1].casefold(), row[2]), []).append(row)
            duplicates = [group for group in groups.values() if len(group) > 1]
            if not duplicates:
                return 0
            removed = 0
            with self._transaction() as conn:
                for group in duplicates:
                    keep = next((row for row in group if row[4] is not None), group[0])
                    postcode = keep[3] or next((row[3] for row in group if row[3]), "")
                    if postcode != (keep[3] or ""):
                        conn.execute("UPDATE streets SET postalcode = ? WHERE id = ?", (postcode, keep[0]))
                    others = [(row[0],) for row in group if row[0] != keep[0]]
                    conn.executemany("DELETE FROM streets WHERE id = ?", others)
                    removed += len(others)
        self.invalidate_text_caches()
        return removed

    def get_stats(self) -> dict:
        """Get database statistics for WebUI."""
        stats = {"places": 0, "capcodes": 0, "geocodes": 0, "streets": 0, "messages": 0, "texts": 0}
        # Map stat keys to actual table names
        table_map = {"texts": "abbreviations"}
        for key in stats:
            table = table_map.get(key, key)
            try:
                count = self.cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[key] = count
            except sqlite3.OperationalError:
                pass
        # Size on disk: the database file plus its write-ahead log
        stats["bytes"] = sum(
            os.path.getsize(path) for path in (self.db_path, self.db_path + "-wal") if os.path.exists(path)
        )
        return stats

    # =========================================================================
    # CAPCODE CRUD OPERATIONS
    # =========================================================================

    def find_capcode(self, capcode: str) -> dict | None:
        """Find capcode information. Returns dict or None."""
        self.cursor.execute(
            "SELECT capcode, discipline, region, location, description, remark FROM capcodes WHERE capcode = ?",
            (capcode,),
        )
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def list_capcodes(self, page: int = 1, per_page: int = 50, search: str = "") -> dict[str, Any]:
        """List capcodes with pagination and search."""
        offset = (page - 1) * per_page

        if search:
            search_term = f"%{search}%"
            self.cursor.execute(
                "SELECT COUNT(*) FROM capcodes WHERE capcode LIKE ? OR discipline LIKE ? OR region LIKE ? OR description LIKE ?",
                (search_term, search_term, search_term, search_term),
            )
            total = self.cursor.fetchone()[0]

            self.cursor.execute(
                "SELECT capcode, discipline, region, location, description, remark FROM capcodes "
                "WHERE capcode LIKE ? OR discipline LIKE ? OR region LIKE ? OR description LIKE ? "
                "ORDER BY capcode LIMIT ? OFFSET ?",
                (search_term, search_term, search_term, search_term, per_page, offset),
            )
        else:
            self.cursor.execute("SELECT COUNT(*) FROM capcodes")
            total = self.cursor.fetchone()[0]

            self.cursor.execute(
                "SELECT capcode, discipline, region, location, description, remark FROM capcodes "
                "ORDER BY capcode LIMIT ? OFFSET ?",
                (per_page, offset),
            )

        rows = [dict(row) for row in self.cursor.fetchall()]
        return {"items": rows, "total": total, "page": page, "per_page": per_page}

    def add_capcode(
        self,
        capcode: str,
        discipline: str = "",
        region: str = "",
        location: str = "",
        description: str = "",
        remark: str = "",
    ) -> bool:
        """Add a new capcode."""
        try:
            self.cursor.execute(
                "INSERT INTO capcodes (capcode, discipline, region, location, description, remark) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (capcode, discipline, region, location, description, remark),
            )
            self._commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_capcode(
        self,
        capcode: str,
        discipline: str = "",
        region: str = "",
        location: str = "",
        description: str = "",
        remark: str = "",
    ) -> bool:
        """Update an existing capcode."""
        try:
            self.cursor.execute(
                "UPDATE capcodes SET discipline=?, region=?, location=?, description=?, remark=? WHERE capcode=?",
                (discipline, region, location, description, remark, capcode),
            )
            self._commit()
            return self.cursor.rowcount > 0
        except sqlite3.Error:
            return False

    def delete_capcode(self, capcode: str) -> bool:
        """Delete a capcode."""
        self.cursor.execute("DELETE FROM capcodes WHERE capcode=?", (capcode,))
        self._commit()
        return self.cursor.rowcount > 0

    # =========================================================================
    # PLACES CRUD OPERATIONS
    # =========================================================================

    @staticmethod
    def normalize_city(city: str) -> str:
        """Normalise a city name for exact matching.

        Drops apostrophes (so 's-Hertogenbosch, s-Hertogenbosch and
        'S-HERTOGENBOSCH compare equal), surrounding punctuation, extra
        whitespace and case.
        """
        city = Database.fold_accents(city.replace("'", "").replace("\u2019", ""))
        return " ".join(city.split()).strip(".,;:!?()").casefold()

    def find_city_name(self, city: str) -> str | None:
        """Return the stored place name that exactly matches city (normalised), or None."""
        if not city:
            return None
        cache = self._get_text_cache("places", "places", self._build_places_cache)
        return cache["by_normalized"].get(self.normalize_city(city))

    def find_place(self, city: str, province: str = "", street: str = "") -> dict[str, Any] | None:
        """The stored place a message's town means (normalised name), or None.

        A name can be a place in more than one province (Velp in Gelderland and in
        Noord-Brabant). Then, in this order:
        1. the place in the given province (the province of the message's safety region),
        2. among those left, the place that has the street, when the street is given,
        3. among those left, the place with the most streets (then the oldest).
        Returns a dict with id, city, province, latitude and longitude.
        """
        name = self.find_city_name(city)
        if not name:
            return None
        try:
            places = self.cursor.execute(
                "SELECT p.id, p.city, p.province, p.latitude, p.longitude FROM places p WHERE p.city = ? "
                "ORDER BY (SELECT COUNT(*) FROM streets s WHERE s.city_id = p.id) DESC, p.id",
                (name,),
            ).fetchall()
            places = [dict(row) for row in places]
            if len(places) > 1 and province:
                key = province_key(province)
                places = [p for p in places if province_key(p["province"]) == key] or places
            if len(places) > 1 and street:
                with_street = [
                    p
                    for p in places
                    if self.cursor.execute(
                        "SELECT 1 FROM streets WHERE city_id = ? AND street = ? COLLATE NOCASE LIMIT 1",
                        (p["id"], street),
                    ).fetchone()
                ]
                places = with_street or places
        except sqlite3.Error:
            return None
        return places[0] if places else None

    def get_place_coordinates(self, city: str, province: str = "", street: str = "") -> dict[str, float] | None:
        """Approximate coordinates of a town from the places table (see find_place for which place)."""
        place = self.find_place(city, province, street)
        if not place or place["latitude"] is None or place["longitude"] is None:
            return None
        try:
            return {"latitude": float(place["latitude"]), "longitude": float(place["longitude"])}
        except (TypeError, ValueError):
            return None

    def find_city_by_abbreviation(self, abbrev: str) -> str | None:
        """Find full city name from abbreviation."""
        self.cursor.execute("SELECT city FROM places WHERE abbreviation = ?", (abbrev,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def find_city_in_text(self, text: str) -> dict[str, str] | None:
        """
        Find a known city name or abbreviation in the text.
        Returns dict with 'city', 'matched_text' (what was found), and 'remaining_text' (text without city).
        """
        cache = self._get_text_cache("places", "places", self._build_places_cache)
        text = self.fold_accents(text)  # names in the cache are compared without accents

        # First try to match city abbreviations (like HELMOND, ROTTDM, etc.)
        # These are uppercase abbreviations
        for item in self._match_candidates(cache["abbrevs"], text):
            city_name, abbrev = item["row"][0], item["row"][1]
            # Use word boundary matching for abbreviations
            pattern = re.compile(r"\b" + item["escaped"] + r"\b", re.IGNORECASE)
            if pattern.search(text):
                remaining = pattern.sub("", text)
                return {"city": city_name, "matched_text": abbrev, "remaining_text": remaining.strip()}

        # Then try to match full city names (loaded longest first)
        for item in self._match_candidates(cache["cities"], text):
            city_name = item["row"][0]
            # Use word boundary matching for city names
            pattern = re.compile(r"\b" + item["escaped"] + r"\b", re.IGNORECASE)
            if pattern.search(text):
                remaining = pattern.sub("", text)
                return {"city": city_name, "matched_text": city_name, "remaining_text": remaining.strip()}

        return None

    def match_street_in_text(
        self, text: str, excluded_words: list[str] | None = None, city: str | None = None, province: str = ""
    ) -> dict[str, str] | None:
        """
        Try to find a known street name in the text using word boundary matching.
        Returns dict with 'street' and 'city' if found.

        Args:
            text: The text to search in
            excluded_words: Words to exclude from street matching (e.g. already matched city names)
            city: Only match streets of this place (the town already found in the message), so a
                street of the same name elsewhere can't supply the postcode
            province: The province of the message's region. When the town is a place in this
                province and in others (Hoorn), only the streets of the place in this province count
        """
        excluded_lower = [self.fold_accents(w).lower() for w in (excluded_words or [])]
        city_lower = city.lower() if city else None
        only_province = None
        if city and province:
            places = self._get_text_cache("places", "places", self._build_places_cache)
            namesakes = places["provinces"].get(city, set())
            if len(namesakes) > 1 and province_key(province) in namesakes:
                only_province = province_key(province)
        cache = self._get_text_cache("streets", "streets", self._build_streets_cache)
        text = self.fold_accents(text)  # BAG names have accents, P2000 messages don't

        for item in self._match_candidates(cache, text):
            row = item["row"]
            street_name = row[0]

            # Skip streets of other places once the message's town is known
            if city_lower and (row[1] or "").lower() != city_lower:
                continue
            if only_province and province_key(row[3]) != only_province:
                continue

            # Skip if street name is in excluded words (like a city name)
            if item["folded"] in excluded_lower:
                continue

            # Use word boundary matching instead of substring matching
            pattern = re.compile(r"\b" + item["escaped"] + r"\b", re.IGNORECASE)
            if pattern.search(text):
                return {
                    "street": street_name,
                    "city": row[1] if row[1] else None,
                    "postalcode": row[2] if len(row) > 2 and row[2] else None,
                }

        return None

    def list_places(self, page: int = 1, per_page: int = 50, search: str = "") -> dict[str, Any]:
        """List places with pagination and search."""
        offset = (page - 1) * per_page

        # Get columns that exist in the table
        try:
            self.cursor.execute("PRAGMA table_info(places)")
            existing_cols = {row[1] for row in self.cursor.fetchall()}
        except sqlite3.Error:
            existing_cols = {"city", "abbreviation"}

        # Build SELECT based on available columns
        base_cols = ["city", "abbreviation"]
        optional_cols = ["id", "province", "latitude", "longitude"]
        select_cols = base_cols + [c for c in optional_cols if c in existing_cols]

        if search:
            search_term = f"%{search}%"
            where_clause = "WHERE city LIKE ? OR abbreviation LIKE ?"
            params = [search_term, search_term]
            if "province" in existing_cols:
                where_clause += " OR province LIKE ?"
                params.append(search_term)

            self.cursor.execute(f"SELECT COUNT(*) FROM places {where_clause}", params)
            total = self.cursor.fetchone()[0]

            self.cursor.execute(
                f"SELECT {', '.join(select_cols)} FROM places {where_clause} ORDER BY city LIMIT ? OFFSET ?",
                [*params, per_page, offset],
            )
        else:
            self.cursor.execute("SELECT COUNT(*) FROM places")
            total = self.cursor.fetchone()[0]

            self.cursor.execute(
                f"SELECT {', '.join(select_cols)} FROM places ORDER BY city LIMIT ? OFFSET ?", (per_page, offset)
            )

        rows = [dict(row) for row in self.cursor.fetchall()]
        return {"items": rows, "total": total, "page": page, "per_page": per_page}

    def add_place(
        self,
        city: str,
        abbreviation: str = "",
        province: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> bool:
        """Add a new place."""
        try:
            self.cursor.execute(
                "INSERT INTO places (city, abbreviation, province, latitude, longitude) VALUES (?, ?, ?, ?, ?)",
                (city, abbreviation, province or "", latitude, longitude),
            )
            self._commit()
            self.invalidate_text_caches()
            return True
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def _place_id(conn, city: str, province: str) -> int | None:
        """The id of the stored place a (name, province) means, or None when it is new.

        The place with that name in that province; else, for a province, a place of that name
        without a province (it becomes this one); else, without a province, the place of that
        name with the most streets. Provinces compare loosely ("Fryslân" is "Friesland").
        """
        rows = conn.execute(
            "SELECT p.id, p.province FROM places p WHERE p.city = ? "
            "ORDER BY (SELECT COUNT(*) FROM streets s WHERE s.city_id = p.id) DESC, p.id",
            (city,),
        ).fetchall()
        key = province_key(province)
        for row in rows:
            if province_key(row[1]) == key:
                return row[0]
        for row in rows:
            if not key or not row[1]:
                return row[0]
        return None

    @classmethod
    def _upsert_place_row(
        cls, conn, city: str, abbreviation: str, province: str, latitude, longitude
    ) -> tuple[int, bool]:
        """Insert a place, or fill in an existing one (see _place_id); returns (place_id, created).

        Keeps the row id (streets.city_id points at it) and only overwrites
        province/abbreviation/coordinates with non-empty values.
        """
        province = province or ""
        place_id = cls._place_id(conn, city, province)
        if place_id is None:
            cur = conn.execute(
                "INSERT INTO places (city, abbreviation, province, latitude, longitude) VALUES (?, ?, ?, ?, ?)",
                (city, abbreviation or "", province, latitude, longitude),
            )
            return cur.lastrowid, True
        conn.execute(
            "UPDATE places SET abbreviation = COALESCE(NULLIF(?, ''), abbreviation), "
            "province = CASE WHEN province = '' THEN ? ELSE province END, "
            "latitude = COALESCE(?, latitude), longitude = COALESCE(?, longitude) WHERE id = ?",
            (abbreviation or "", province, latitude, longitude, place_id),
        )
        return place_id, False

    def upsert_place(
        self,
        city: str,
        abbreviation: str = "",
        province: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> tuple[int, bool]:
        """Insert a place (name + province), or fill in empty fields of an existing one.

        Returns (place_id, created).
        """
        with self._transaction() as conn:
            place_id, created = self._upsert_place_row(conn, city, abbreviation, province, latitude, longitude)
        self.invalidate_text_caches()
        return place_id, created

    def upsert_places(self, places: list[dict[str, Any]]) -> int:
        """Bulk upsert places (dicts with city/abbreviation/province/latitude/longitude) in one transaction.

        Returns the number of rows processed.
        """
        rows = [p for p in places if p.get("city")]
        with self._transaction() as conn:
            for p in rows:
                self._upsert_place_row(
                    conn,
                    p["city"],
                    p.get("abbreviation", ""),
                    p.get("province", ""),
                    p.get("latitude"),
                    p.get("longitude"),
                )
        self.invalidate_text_caches()
        return len(rows)

    def update_place(
        self,
        place_id: int,
        city: str,
        abbreviation: str = "",
        province: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> bool:
        """Update an existing place."""
        try:
            self.cursor.execute(
                "UPDATE places SET city=?, abbreviation=?, province=?, latitude=?, longitude=? WHERE id=?",
                (city, abbreviation, province or "", latitude, longitude, place_id),
            )
            self._commit()
            self.invalidate_text_caches()
            return self.cursor.rowcount > 0
        except sqlite3.Error:
            return False

    def delete_place(self, place_id: int) -> bool:
        """Delete a place and its associated streets."""
        self.cursor.execute("DELETE FROM streets WHERE city_id=?", (place_id,))
        self.cursor.execute("DELETE FROM places WHERE id=?", (place_id,))
        self._commit()
        self.invalidate_text_caches()
        return self.cursor.rowcount > 0

    # =========================================================================
    # STREETS CRUD OPERATIONS
    # =========================================================================

    def list_streets(
        self, page: int = 1, per_page: int = 50, search: str = "", city_id: int | None = None
    ) -> dict[str, Any]:
        """List streets with pagination and search."""
        offset = (page - 1) * per_page
        params = []
        where_clauses = []

        if search:
            where_clauses.append("(s.street LIKE ? OR s.postalcode LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        if city_id:
            where_clauses.append("s.city_id = ?")
            params.append(city_id)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Check if places table has id column (for JOIN)
        try:
            self.cursor.execute("PRAGMA table_info(places)")
            place_cols = {row[1] for row in self.cursor.fetchall()}
            has_id = "id" in place_cols
        except sqlite3.Error:
            has_id = False

        if has_id:
            # Use JOIN if places has id
            self.cursor.execute(
                f"SELECT COUNT(*) FROM streets s LEFT JOIN places p ON s.city_id = p.id {where_sql}", params
            )
            total = self.cursor.fetchone()[0]

            self.cursor.execute(
                # A town in two provinces shows its province: "Velp (Gelderland)"
                f"SELECT s.id, s.street, s.postalcode, s.city_id, "
                f"CASE WHEN p.province != '' AND EXISTS "
                f"(SELECT 1 FROM places n WHERE n.city = p.city AND n.id != p.id) "
                f"THEN p.city || ' (' || p.province || ')' ELSE p.city END as city_name, s.latitude, s.longitude "
                f"FROM streets s "
                f"LEFT JOIN places p ON s.city_id = p.id {where_sql} "
                f"ORDER BY s.street LIMIT ? OFFSET ?",
                [*params, per_page, offset],
            )
        else:
            # Simple query without JOIN
            self.cursor.execute(f"SELECT COUNT(*) FROM streets s {where_sql}", params)
            total = self.cursor.fetchone()[0]

            self.cursor.execute(
                f"SELECT id, street, postalcode, city_id, '' as city_name, latitude, longitude FROM streets s "
                f"{where_sql} ORDER BY street LIMIT ? OFFSET ?",
                [*params, per_page, offset],
            )

        rows = [dict(row) for row in self.cursor.fetchall()]
        return {"items": rows, "total": total, "page": page, "per_page": per_page}

    def add_street(
        self,
        street: str,
        city_id: int | None = None,
        postalcode: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> bool:
        """Add a new street. Returns False if it already exists for this city or has invalid coordinates."""
        given = latitude is not None or longitude is not None
        latitude, longitude = _nl_coordinates(latitude, longitude)
        if given and latitude is None:
            return False
        try:
            self.cursor.execute(
                "INSERT OR IGNORE INTO streets (street, city_id, postalcode, latitude, longitude) VALUES (?, ?, ?, ?, ?)",
                (street, city_id, postalcode, latitude, longitude),
            )
            added = self.cursor.rowcount > 0
            self._commit()
            self.invalidate_text_caches()
            return added
        except sqlite3.Error:
            return False

    def add_streets(self, city_id: int | None, streets: list[dict[str, str]]) -> int:
        """Bulk add streets (dicts with street/postcode/latitude/longitude) for one city in one transaction.

        Existing streets are kept; an empty postal code or missing coordinates
        are filled in when the new data has them. Returns the number of newly
        added streets.
        """
        rows = [
            (s["street"], city_id, s.get("postcode", ""), *_nl_coordinates(s.get("latitude"), s.get("longitude")))
            for s in streets
            if s.get("street")
        ]
        with self._transaction() as conn:
            added = conn.executemany(
                "INSERT OR IGNORE INTO streets (street, city_id, postalcode, latitude, longitude) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            ).rowcount
            conn.executemany(
                "UPDATE streets SET postalcode = ? WHERE street = ? AND city_id IS ? "
                "AND (postalcode IS NULL OR postalcode = '')",
                [(pc, street, cid) for street, cid, pc, _, _ in rows if pc],
            )
            conn.executemany(
                "UPDATE streets SET latitude = ?, longitude = ? WHERE street = ? AND city_id IS ? AND latitude IS NULL",
                [(lat, lon, street, cid) for street, cid, _, lat, lon in rows if lat is not None],
            )
        self.invalidate_text_caches()
        return added

    def move_namesake_streets(
        self, city_id: int, streets: list[dict[str, Any]], max_km: float = 1.0
    ) -> list[dict[str, Any]]:
        """Move streets of this place that are stored under a same-named place in another province.

        Versions before 1.4.11 stored Velp (Gelderland) and Velp (Noord-Brabant) as one place
        with the streets of both. When a province import fetches the streets of a place, a
        street of that name on a namesake (same name, other province) is moved here when it
        is the same street: its position lies within max_km of the fetched one or, without a
        position, it has the same postcode area. A street that is already here as well is
        removed from the namesake instead. The namesake's own street of that name (far away)
        stays. Returns the namesakes that gave up streets, as dicts with id, province and moved
        (the number of streets moved or removed): a street name both places have was stored only
        once, so the namesake may now miss its own one and should get its streets again.
        """
        fetched = {}
        for s in streets:
            if s.get("street"):
                fetched[s["street"].casefold()] = (
                    *_nl_coordinates(s.get("latitude"), s.get("longitude")),
                    s.get("postcode") or "",
                )
        moved: dict[int, int] = {}
        with self._transaction() as conn:
            namesakes = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT n.id, n.province FROM places p JOIN places n ON n.city = p.city AND n.id != p.id "
                    "WHERE p.id = ?",
                    (city_id,),
                ).fetchall()
            }
            if not namesakes:
                return []
            own = {
                row[0].casefold() for row in conn.execute("SELECT street FROM streets WHERE city_id = ?", (city_id,))
            }
            marks = ",".join("?" * len(namesakes))
            for street_id, namesake_id, name, postcode, lat, lon in conn.execute(
                f"SELECT id, city_id, street, postalcode, latitude, longitude FROM streets WHERE city_id IN ({marks})",
                list(namesakes),
            ).fetchall():
                match = fetched.get(name.casefold())
                if not match:
                    continue
                f_lat, f_lon, f_postcode = match
                if lat is not None and f_lat is not None:
                    same = _km((lat, lon), (f_lat, f_lon)) <= max_km
                else:
                    same = lat is None and bool(postcode) and postcode == f_postcode
                if not same:
                    continue
                if name.casefold() in own:
                    conn.execute("DELETE FROM streets WHERE id = ?", (street_id,))
                else:
                    conn.execute("UPDATE streets SET city_id = ? WHERE id = ?", (city_id, street_id))
                    own.add(name.casefold())
                moved[namesake_id] = moved.get(namesake_id, 0) + 1
        if moved:
            self.invalidate_text_caches()
        return [{"id": i, "province": namesakes[i], "moved": n} for i, n in moved.items()]

    def update_street(
        self,
        street_id: int,
        street: str,
        city_id: int | None = None,
        postalcode: str = "",
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> bool:
        """Update an existing street. Returns False for coordinates that are incomplete or outside the Netherlands."""
        given = latitude is not None or longitude is not None
        latitude, longitude = _nl_coordinates(latitude, longitude)
        if given and latitude is None:
            return False
        try:
            self.cursor.execute(
                "UPDATE streets SET street=?, city_id=?, postalcode=?, latitude=?, longitude=? WHERE id=?",
                (street, city_id, postalcode, latitude, longitude, street_id),
            )
            self._commit()
            self.invalidate_text_caches()
            return self.cursor.rowcount > 0
        except sqlite3.Error:
            return False

    def street_provinces(self) -> dict[str, dict[str, int]]:
        """Streets per province (via their place): {province: {"streets": n, "located": n with a position}}."""
        try:
            rows = self.cursor.execute(
                "SELECT p.province, COUNT(*), COUNT(s.latitude) FROM streets s JOIN places p ON s.city_id = p.id "
                "WHERE p.province IS NOT NULL AND p.province != '' GROUP BY p.province"
            ).fetchall()
        except sqlite3.Error:
            return {}
        return {row[0]: {"streets": row[1], "located": row[2]} for row in rows}

    def get_street_coordinates(self, street: str, city: str, province: str = "") -> dict[str, float] | None:
        """Coordinates of a street in a town from the streets table (BAG centroid or your own), or None.

        For a town in more than one province, the place is chosen as in find_place (the
        province of the message's region first, then the place that has the street).
        """
        place = self.find_place(city, province, street)
        if not place:
            return None
        try:
            self.cursor.execute(
                "SELECT latitude, longitude FROM streets WHERE city_id = ? AND street = ? COLLATE NOCASE "
                "AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 1",
                (place["id"], street),
            )
            row = self.cursor.fetchone()
            if row:
                return {"latitude": float(row[0]), "longitude": float(row[1])}
        except (sqlite3.Error, TypeError, ValueError):
            pass
        return None

    def delete_street(self, street_id: int) -> bool:
        """Delete a street."""
        self.cursor.execute("DELETE FROM streets WHERE id=?", (street_id,))
        self._commit()
        self.invalidate_text_caches()
        return self.cursor.rowcount > 0

    # =========================================================================
    # MESSAGE HISTORY OPERATIONS
    # =========================================================================

    def get_history_by_address(self, address: str, limit: int = 20) -> list[dict]:
        """Get message history for a specific address."""
        self.cursor.execute(
            """SELECT * FROM messages WHERE address LIKE ? ORDER BY timestamp DESC LIMIT ?""", (f"%{address}%", limit)
        )
        rows = self.cursor.fetchall()
        return [self._message_row_to_dict(row) for row in rows]

    def _message_row_to_dict(self, row) -> dict:
        """Convert message row to dict."""
        d = dict(row)
        # Convert capcodes back to list (stored as JSON; legacy rows may
        # hold a plain comma-separated string)
        raw = d.get("capcodes")
        if raw:
            try:
                parsed = json.loads(raw)
                d["capcodes"] = parsed if isinstance(parsed, list) else [str(parsed)]
            except (json.JSONDecodeError, TypeError):
                d["capcodes"] = [c.strip() for c in raw.split(",") if c.strip()]
        else:
            d["capcodes"] = []
        # Rows stored before distances were limited to messages with a street
        # hold the distance to the city centre
        if not d.get("street"):
            d["distance_km"] = None
        # Not stored: recognised from the text, so older rows get them too
        if "body" in d:
            d.update(self.resolve_vehicle(d["body"] or "", d.get("discipline") or ""))
            d.update(message_fields.derived_fields(d["body"] or "", self.decode_talkgroup))
        return d

    def list_messages(
        self, page: int = 1, per_page: int = 50, search: str = "", discipline: str = "", city: str = ""
    ) -> dict:
        """List messages with pagination and search/filter."""
        offset = (page - 1) * per_page

        # Build WHERE clause
        conditions = []
        params = []

        if search:
            conditions.append("(body LIKE ? OR address LIKE ? OR street LIKE ? OR city LIKE ?)")
            search_pattern = f"%{search}%"
            params.extend([search_pattern] * 4)

        if discipline:
            conditions.append("discipline = ?")
            params.append(discipline)

        if city:
            conditions.append("city LIKE ?")
            params.append(f"%{city}%")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Get total count
        count_query = f"SELECT COUNT(*) FROM messages {where_clause}"
        self.cursor.execute(count_query, params)
        total = self.cursor.fetchone()[0]

        # Get paginated results
        query = f"""
            SELECT id, timestamp, body, capcodes, discipline, region, city, street,
                   address, latitude, longitude, message_type, priority
            FROM messages
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        self.cursor.execute(query, [*params, per_page, offset])
        rows = self.cursor.fetchall()

        return {
            "items": [self._message_row_to_dict(row) for row in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
        }

    def delete_message(self, message_id: int) -> bool:
        """Delete a message by ID."""
        self.cursor.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        self._commit()
        return self.cursor.rowcount > 0

    def delete_all_messages(self) -> int:
        """Delete all messages. Returns count deleted."""
        self.cursor.execute("SELECT COUNT(*) FROM messages")
        count = self.cursor.fetchone()[0]
        self.cursor.execute("DELETE FROM messages")
        self._commit()
        return count

    # =========================================================================
    # GEOCODE OPERATIONS (existing)
    # =========================================================================

    @staticmethod
    def _geocode_key(address: str) -> str:
        """Cache key for a geocode query - must be identical for lookup and store."""
        return " ".join(address.split())

    def find_geocode(self, address: str) -> dict | None:
        """Find cached geocode for address."""
        self.cursor.execute(
            "SELECT latitude, longitude, address, mapurl FROM geocodes WHERE query = ?", (self._geocode_key(address),)
        )
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def store_geocode(
        self,
        query: str,
        datatype: str,
        latitude: float,
        longitude: float,
        postalcode: str,
        street: str,
        city: str,
        address: str,
        mapurl: str,
    ) -> None:
        """Store geocode result in cache."""
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO geocodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._geocode_key(query),
                    datatype,
                    str(latitude),
                    str(longitude),
                    postalcode,
                    street,
                    city,
                    address,
                    mapurl,
                ),
            )
            self._commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to store geocode: {e}")

    def list_geocodes(self, page: int = 1, per_page: int = 50, search: str = "") -> dict[str, Any]:
        """List cached geocodes with pagination and search."""
        offset = (page - 1) * per_page
        try:
            if search:
                search_term = f"%{search}%"
                self.cursor.execute(
                    """SELECT COUNT(*) FROM geocodes
                       WHERE query LIKE ? OR city LIKE ? OR street LIKE ? OR address LIKE ?""",
                    (search_term, search_term, search_term, search_term),
                )
                total = self.cursor.fetchone()[0]
                self.cursor.execute(
                    """SELECT query, datatype, latitude, longitude, postalcode, street, city, address, mapurl
                       FROM geocodes
                       WHERE query LIKE ? OR city LIKE ? OR street LIKE ? OR address LIKE ?
                       ORDER BY city, street
                       LIMIT ? OFFSET ?""",
                    (search_term, search_term, search_term, search_term, per_page, offset),
                )
            else:
                self.cursor.execute("SELECT COUNT(*) FROM geocodes")
                total = self.cursor.fetchone()[0]
                self.cursor.execute(
                    """SELECT query, datatype, latitude, longitude, postalcode, street, city, address, mapurl
                       FROM geocodes ORDER BY city, street LIMIT ? OFFSET ?""",
                    (per_page, offset),
                )

            rows = self.cursor.fetchall()
            items = [dict(row) for row in rows]
            return {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page,
            }
        except sqlite3.Error as e:
            logger.error(f"Failed to list geocodes: {e}")
            return {"items": [], "total": 0, "page": 1, "per_page": per_page, "pages": 0}

    def delete_geocode(self, query: str) -> bool:
        """Delete a cached geocode by query."""
        try:
            self.cursor.execute("DELETE FROM geocodes WHERE query = ?", (query,))
            self._commit()
            return self.cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Failed to delete geocode: {e}")
            return False

    def delete_all_geocodes(self) -> int:
        """Delete all cached geocodes. Returns count deleted."""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM geocodes")
            count = self.cursor.fetchone()[0]
            self.cursor.execute("DELETE FROM geocodes")
            self._commit()
            return count
        except sqlite3.Error as e:
            logger.error(f"Failed to delete geocodes: {e}")
            return 0

    # =========================================================================
    # IMPORT/EXPORT OPERATIONS
    # =========================================================================

    def export_capcodes_csv(self) -> str:
        """Export all capcodes to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["capcode", "discipline", "region", "location", "description", "remark"])

        self.cursor.execute(
            "SELECT capcode, discipline, region, location, description, remark FROM capcodes ORDER BY capcode"
        )
        for row in self.cursor.fetchall():
            writer.writerow(list(row))

        return output.getvalue()

    def export_places_csv(self) -> str:
        """Export all places to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["city", "abbreviation", "province", "latitude", "longitude"])

        # Get available columns
        try:
            self.cursor.execute("PRAGMA table_info(places)")
            cols = {row[1] for row in self.cursor.fetchall()}
            select_parts = ["city", "abbreviation"]
            if "province" in cols:
                select_parts.append("province")
            else:
                select_parts.append("'' as province")
            if "latitude" in cols:
                select_parts.append("latitude")
            else:
                select_parts.append("NULL as latitude")
            if "longitude" in cols:
                select_parts.append("longitude")
            else:
                select_parts.append("NULL as longitude")

            self.cursor.execute(f"SELECT {', '.join(select_parts)} FROM places ORDER BY city")
            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export places failed: {e}")

        return output.getvalue()

    def export_streets_csv(self) -> str:
        """Export all streets to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        # province tells same-named towns apart (Velp in Gelderland and in Noord-Brabant)
        writer.writerow(["street", "city", "postalcode", "latitude", "longitude", "province"])

        # Check if places has id for JOIN
        try:
            self.cursor.execute("PRAGMA table_info(places)")
            has_id = "id" in {row[1] for row in self.cursor.fetchall()}

            if has_id:
                self.cursor.execute(
                    "SELECT s.street, p.city, s.postalcode, s.latitude, s.longitude, p.province FROM streets s "
                    "LEFT JOIN places p ON s.city_id = p.id ORDER BY s.street"
                )
            else:
                self.cursor.execute(
                    "SELECT street, '', postalcode, latitude, longitude, '' FROM streets ORDER BY street"
                )

            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export streets failed: {e}")

        return output.getvalue()

    def export_messages_csv(self) -> str:
        """Export all messages to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "timestamp",
                "body",
                "capcodes",
                "discipline",
                "region",
                "city",
                "street",
                "address",
                "priority",
                "message_type",
            ]
        )

        try:
            self.cursor.execute(
                "SELECT timestamp, body, capcodes, discipline, region, city, street, "
                "address, priority, message_type FROM messages ORDER BY timestamp DESC"
            )
            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export messages failed: {e}")

        return output.getvalue()

    def export_abbreviations_csv(self) -> str:
        """Export all abbreviations to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["abbreviation", "full_text"])

        try:
            self.cursor.execute("SELECT abbreviation, full_text FROM abbreviations ORDER BY abbreviation")
            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export abbreviations failed: {e}")

        return output.getvalue()

    def export_tts_csv(self) -> str:
        """Export all TTS replacements to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["pattern", "replacement", "enabled"])

        try:
            self.cursor.execute("SELECT pattern, replacement, enabled FROM tts_replacements ORDER BY id")
            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export TTS replacements failed: {e}")

        return output.getvalue()

    @staticmethod
    def _read_csv(csv_content: str, required: tuple[str, ...]) -> list[dict[str, str]]:
        """Parse and validate CSV content before anything touches the database.

        Accepts comma, semicolon or tab delimiters (picked by which one yields
        the required header columns). Header names are matched
        case-insensitively. Raises ValueError for unusable input.
        """
        if not isinstance(csv_content, str):
            raise ValueError("CSV content must be a string")
        text = csv_content.lstrip("\ufeff")
        if not text.strip():
            raise ValueError("CSV is empty")

        header_line = text.splitlines()[0]
        for delimiter in (",", ";", "\t"):
            header = [h.strip().lower() for h in next(csv.reader([header_line], delimiter=delimiter))]
            if all(col in header for col in required):
                break
        else:
            raise ValueError(f"CSV header must contain column(s): {', '.join(required)} (got: {header_line[:100]!r})")

        reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
        try:
            rows = list(reader)
        except csv.Error as e:
            raise ValueError(f"Invalid CSV near line {reader.line_num}: {e}") from e
        return [dict(zip(header, (v.strip() for v in row), strict=False)) for row in rows[1:] if any(row)]

    def import_capcodes_csv(self, csv_content: str, replace: bool = False) -> dict[str, int]:
        """Import capcodes from CSV. Returns counts of imported/skipped.

        Raises ValueError for invalid CSV; the table is left untouched then.
        """
        rows = self._read_csv(csv_content, ("capcode",))
        imported = 0
        skipped = 0

        with self._transaction() as conn:
            if replace:
                conn.execute("DELETE FROM capcodes")
            for row in rows:
                if not row.get("capcode"):
                    skipped += 1
                    continue
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO capcodes (capcode, discipline, region, location, description, remark) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            row["capcode"],
                            row.get("discipline", ""),
                            row.get("region", ""),
                            row.get("location", ""),
                            row.get("description", ""),
                            row.get("remark", ""),
                        ),
                    )
                    imported += 1
                except sqlite3.Error:
                    skipped += 1

        self.invalidate_text_caches()
        return {"imported": imported, "skipped": skipped}

    def import_places_csv(self, csv_content: str, replace: bool = False) -> dict[str, int]:
        """Import places from CSV. Returns counts of imported/skipped.

        Existing places are updated in place so their id (and the streets
        linked to it) is kept; empty CSV values never wipe stored data.
        With replace=True, places missing from the CSV are removed together
        with their streets. Raises ValueError for invalid CSV; nothing is
        changed then.
        """
        rows = self._read_csv(csv_content, ("city",))
        imported = 0
        skipped = 0
        kept = set()  # ids of the places in the CSV

        with self._transaction() as conn:
            for row in rows:
                try:
                    lat = float(row["latitude"]) if row.get("latitude") else None
                    lon = float(row["longitude"]) if row.get("longitude") else None
                except ValueError:
                    skipped += 1
                    continue
                if not row.get("city"):
                    skipped += 1
                    continue
                try:
                    # A place is its name in its province (Velp in Gelderland and in Noord-Brabant)
                    place_id, _ = self._upsert_place_row(
                        conn, row["city"], row.get("abbreviation", ""), row.get("province", ""), lat, lon
                    )
                    kept.add(place_id)
                    imported += 1
                except sqlite3.Error:
                    skipped += 1

            if replace:
                stale = [(r[0],) for r in conn.execute("SELECT id FROM places").fetchall() if r[0] not in kept]
                conn.executemany("DELETE FROM streets WHERE city_id = ?", stale)
                conn.executemany("DELETE FROM places WHERE id = ?", stale)

        self.invalidate_text_caches()
        return {"imported": imported, "skipped": skipped}

    def import_streets_csv(self, csv_content: str, replace: bool = False) -> dict[str, int]:
        """Import streets from CSV. Returns counts of imported/skipped.

        Streets already present for the same city are skipped, unless the CSV
        has coordinates for them (latitude/longitude columns are optional):
        those are updated. Coordinates outside the Netherlands are ignored.
        Raises ValueError for invalid CSV; the table is left untouched then.
        """
        rows = self._read_csv(csv_content, ("street",))
        imported = 0
        skipped = 0

        with self._transaction() as conn:
            if replace:
                conn.execute("DELETE FROM streets")
            for row in rows:
                if not row.get("street"):
                    skipped += 1
                    continue
                lat, lon = _nl_coordinates(row.get("latitude"), row.get("longitude"))
                try:
                    # Look up city_id: the town in the row's province (optional column; without
                    # it a town in two provinces means the one with the most streets)
                    city_id = None
                    if row.get("city"):
                        city_id = self._place_id(conn, row["city"], row.get("province") or "") or self._place_id(
                            conn, row["city"], ""
                        )

                    cur = conn.execute(
                        "INSERT OR IGNORE INTO streets (street, city_id, postalcode, latitude, longitude) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (row["street"], city_id, row.get("postalcode", ""), lat, lon),
                    )
                    if cur.rowcount == 0 and lat is not None:
                        cur = conn.execute(
                            "UPDATE streets SET latitude = ?, longitude = ? WHERE street = ? AND city_id IS ?",
                            (lat, lon, row["street"], city_id),
                        )
                    if cur.rowcount > 0:
                        imported += 1
                    else:
                        skipped += 1
                except sqlite3.Error:
                    skipped += 1

        self.invalidate_text_caches()
        return {"imported": imported, "skipped": skipped}

    # =====================
    # Message History
    # =====================

    def save_message(self, msg) -> int | None:
        """Save a P2000Message to the database. Returns the new row id, None on failure."""
        try:
            self.cursor.execute(
                """
                INSERT INTO messages (
                    timestamp, body, raw_message, capcodes, group_id, message_type,
                    priority, discipline, region, city, street, address,
                    latitude, longitude, receivers, remarks, distance_km, incident_id, repeats
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    # Stored as ISO string; passing datetime objects straight to
                    # sqlite3 is deprecated on Python 3.12+
                    msg.timestamp.isoformat(),
                    msg.body,
                    msg.raw_message,
                    json.dumps(msg.capcodes),
                    msg.group_id,
                    msg.message_type,
                    msg.priority,
                    msg.discipline,
                    msg.region,
                    msg.city,
                    msg.street,
                    msg.address,
                    msg.latitude,
                    msg.longitude,
                    msg.receivers,
                    msg.remarks,
                    msg.distance_km,
                    msg.incident_id or None,
                    msg.repeats,
                ),
            )
            self._commit()
            return self.cursor.lastrowid
        except Exception as e:
            logger.error(f"Failed to save message: {e}")
            return None

    def set_message_repeats(self, message_id: int, repeats: int) -> None:
        """Store how often a message was resent."""
        self.cursor.execute("UPDATE messages SET repeats = ? WHERE id = ?", (repeats, message_id))
        self._commit()

    def get_messages_since(self, seconds: int) -> list[dict[str, Any]]:
        """Messages received in the last `seconds`, oldest first."""
        since = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
        self.cursor.execute(
            "SELECT * FROM messages WHERE julianday(timestamp) >= julianday(?) ORDER BY julianday(timestamp)",
            (since,),
        )
        return [self._message_row_to_dict(row) for row in self.cursor.fetchall()]

    def get_incident_messages(self, incident_id: str) -> list[dict[str, Any]]:
        """All messages of one incident, oldest first."""
        self.cursor.execute(
            "SELECT * FROM messages WHERE incident_id = ? ORDER BY julianday(timestamp)", (incident_id,)
        )
        return [self._message_row_to_dict(row) for row in self.cursor.fetchall()]

    def get_map_points(self, hours: int, limit: int = 20000) -> list[dict[str, Any]]:
        """Located messages of the last `hours`, oldest first, for the map history layer."""
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        self.cursor.execute(
            """
            SELECT CAST(strftime('%s', timestamp) AS INTEGER) AS time, latitude, longitude, priority
            FROM messages
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND julianday(timestamp) >= julianday(?)
            ORDER BY julianday(timestamp) DESC
            LIMIT ?
            """,
            (since, limit),
        )
        return [dict(row) for row in reversed(self.cursor.fetchall())]

    def get_recent_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent messages from the database (same shape as _message_row_to_dict)."""
        try:
            self.cursor.execute(
                """
                SELECT * FROM messages
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (limit,),
            )
            return [self._message_row_to_dict(row) for row in self.cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get messages: {e}")
            return []

    def day_stats(self, hours: int = 24) -> dict[str, Any]:
        """Messages of the last `hours` hours: per hour (oldest first, empty hours included),
        per discipline kind, the busiest towns and the total."""
        now = datetime.now().astimezone()
        since = (now - timedelta(hours=hours)).isoformat()
        empty = {"hours": [], "disciplines": {}, "places": [], "total": 0}
        try:
            # Timestamps are stored as local ISO strings: the first 13 characters are the hour
            per_hour = dict(
                self.cursor.execute(
                    "SELECT substr(timestamp, 1, 13), COUNT(*) FROM messages WHERE timestamp >= ? GROUP BY 1", (since,)
                ).fetchall()
            )
            disciplines = self.cursor.execute(
                "SELECT discipline, COUNT(*) FROM messages WHERE timestamp >= ? GROUP BY discipline", (since,)
            ).fetchall()
            places = self.cursor.execute(
                "SELECT city, COUNT(*) FROM messages WHERE timestamp >= ? AND city IS NOT NULL AND city != '' "
                "GROUP BY city ORDER BY 2 DESC, city LIMIT 8",
                (since,),
            ).fetchall()
        except sqlite3.Error as e:
            logger.error(f"Failed to get day stats: {e}")
            return empty
        start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours - 1)
        hour_list = []
        for i in range(hours):
            hour = start + timedelta(hours=i)
            hour_list.append({"hour": hour.isoformat(), "count": per_hour.get(hour.isoformat()[:13], 0)})
        by_kind = {"brandweer": 0, "ambulance": 0, "politie": 0, "other": 0}
        total = 0
        for discipline, count in disciplines:
            total += count
            kinds = [k for k in ("brandweer", "ambulance", "politie") if k in (discipline or "").lower()] or ["other"]
            for kind in kinds:
                by_kind[kind] += count
        return {"hours": hour_list, "disciplines": by_kind, "places": [[c, n] for c, n in places], "total": total}

    def get_messages_before(self, before: str = "", search: str = "", limit: int = 100) -> list[dict]:
        """Messages older than `before` (ISO timestamp; empty = newest first), newest first,
        optionally matching search in body/address/street/city. Same shape as get_recent_messages."""
        conditions: list[str] = []
        params: list[Any] = []
        if before:
            conditions.append("timestamp < ?")
            params.append(before)
        if search:
            conditions.append("(body LIKE ? OR address LIKE ? OR street LIKE ? OR city LIKE ?)")
            params.extend([f"%{search}%"] * 4)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        try:
            self.cursor.execute(f"SELECT * FROM messages {where} ORDER BY timestamp DESC LIMIT ?", [*params, limit])
            return [self._message_row_to_dict(row) for row in self.cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Failed to get messages: {e}")
            return []

    def get_message_by_id(self, message_id: int) -> dict | None:
        """One stored message by id (same shape as get_recent_messages), or None."""
        try:
            self.cursor.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
            row = self.cursor.fetchone()
            return self._message_row_to_dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get message {message_id}: {e}")
            return None

    def get_message_count(self) -> int:
        """Get total number of stored messages."""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM messages")
            return self.cursor.fetchone()[0]
        except sqlite3.Error:
            return 0

    # =====================
    # Abbreviations
    # =====================

    def find_abbreviations_in_text(self, text: str, discipline: str = "") -> list[dict[str, str]]:
        """Find all abbreviations that appear in the text.

        `discipline` (e.g. "Ambulance", or a combined "Ambulance, Brandweer")
        is used to override codes that mean different things per discipline
        - see DISCIPLINE_ABBREVIATION_OVERRIDES.
        """
        found = []
        # A code matches a token in capitals ("OVD", "BON-01") or with at least two
        # capitals ("OvD", "HOvD"); a lowercase or capitalised word that happens to
        # spell a code ("bon 170616", "rit:", "Me", "ab") is a word
        words = text.split()
        discipline_lower = (discipline or "").lower()

        def _resolve_text(code: str, default_text: str) -> str:
            if not discipline_lower:
                return default_text
            for (override_code, disc_key), override_text in DISCIPLINE_ABBREVIATION_OVERRIDES.items():
                if override_code == code and disc_key in discipline_lower:
                    return override_text
            return default_text

        try:
            # Get all abbreviations (cached, rebuilt when the table changes)
            all_abbrevs = self._get_text_cache("abbreviations", "abbreviations", self._build_abbreviations_cache)

            for word in words:
                # Clean word of punctuation
                token = word.strip(".,;:!?()[]{}")
                code_like = token.isupper() or sum(c.isupper() for c in token) >= 2
                clean = token.upper() if code_like else token
                if clean in all_abbrevs:
                    found.append({"abbreviation": clean, "full_text": _resolve_text(clean, all_abbrevs[clean])})
                elif "-" in clean and clean.isupper() and (group := self.decode_talkgroup(clean)):
                    # Talk groups like BRT-01: discipline + meldkamer/region + quick-dial number
                    # (NIPV coding, from the talkgroup_codes table)
                    discipline, region, number = group
                    found.append({"abbreviation": clean, "full_text": f"Gespreksgroep {discipline} {region} {number}"})
                elif "-" in clean and clean.isupper():
                    # Other codes with a dash: prefix from the abbreviations, number = channel
                    prefix, _, channel = clean.partition("-")
                    if prefix in all_abbrevs and channel.isdigit():
                        base_text = _resolve_text(prefix, all_abbrevs[prefix])
                        found.append({"abbreviation": clean, "full_text": f"{base_text}, kanaal {channel}"})
                else:
                    # Codes like DP4: letters + digits, no dash
                    m = re.match(r"^([A-Z]+)(\d+)$", clean)
                    if m and m.group(1) in all_abbrevs:
                        base_text = _resolve_text(m.group(1), all_abbrevs[m.group(1)])
                        found.append({"abbreviation": clean, "full_text": f"{base_text} {m.group(2)}"})
        except sqlite3.Error as e:
            logger.error(f"Error finding abbreviations: {e}")
        return found

    def list_abbreviations(self, page: int = 1, per_page: int = 50, search: str = "") -> dict[str, Any]:
        """List abbreviations with pagination and search."""
        offset = (page - 1) * per_page
        try:
            if search:
                search_term = f"%{search}%"
                self.cursor.execute(
                    """SELECT COUNT(*) FROM abbreviations
                       WHERE abbreviation LIKE ? OR full_text LIKE ?""",
                    (search_term, search_term),
                )
                total = self.cursor.fetchone()[0]
                self.cursor.execute(
                    """SELECT id, abbreviation, full_text FROM abbreviations
                       WHERE abbreviation LIKE ? OR full_text LIKE ?
                       ORDER BY abbreviation LIMIT ? OFFSET ?""",
                    (search_term, search_term, per_page, offset),
                )
            else:
                self.cursor.execute("SELECT COUNT(*) FROM abbreviations")
                total = self.cursor.fetchone()[0]
                self.cursor.execute(
                    """SELECT id, abbreviation, full_text FROM abbreviations
                       ORDER BY abbreviation LIMIT ? OFFSET ?""",
                    (per_page, offset),
                )

            items = [{"id": row[0], "abbreviation": row[1], "full_text": row[2]} for row in self.cursor.fetchall()]
            return {"items": items, "total": total, "page": page}
        except Exception as e:
            logger.error(f"Failed to list abbreviations: {e}")
            return {"items": [], "total": 0, "page": page}

    def add_abbreviation(self, abbrev: str, full_text: str) -> bool:
        """Add a new abbreviation."""
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO abbreviations (abbreviation, full_text) VALUES (?, ?)",
                (abbrev.upper(), full_text),
            )
            self._commit()
            self.invalidate_text_caches()
            return True
        except Exception as e:
            logger.error(f"Failed to add abbreviation: {e}")
            return False

    def delete_abbreviation(self, abbrev_id: int) -> bool:
        """Delete an abbreviation by id."""
        try:
            self.cursor.execute("DELETE FROM abbreviations WHERE id = ?", (abbrev_id,))
            self._commit()
            self.invalidate_text_caches()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete abbreviation: {e}")
            return False

    def import_abbreviations(self, abbrevs: list[dict[str, str]], replace: bool = True) -> int:
        """Bulk import abbreviations. Returns count imported.

        `replace=True` (the manual "Import Texts" button) overwrites existing
        rows by abbreviation code - lets a user deliberately refresh from the
        seed data. `replace=False` (startup auto-seed) only adds codes that
        aren't already present, so it never reverts a user's own edits.
        """
        count = 0
        sql = (
            "INSERT OR REPLACE INTO abbreviations (abbreviation, full_text) VALUES (?, ?)"
            if replace
            else "INSERT OR IGNORE INTO abbreviations (abbreviation, full_text) VALUES (?, ?)"
        )
        try:
            with self._transaction() as conn:
                for item in abbrevs:
                    abbrev = item.get("abbreviation", "").upper()
                    full_text = item.get("full_text", "")
                    if abbrev and full_text:
                        cur = conn.execute(sql, (abbrev, full_text))
                        count += cur.rowcount if not replace else 1
            self.invalidate_text_caches()
        except Exception as e:
            count = 0
            logger.error(f"Failed to import abbreviations: {e}")
        return count

    # =========================================================================
    # TTS REPLACEMENTS
    # =========================================================================

    def list_tts_replacements(self, page: int = 1, per_page: int = 50, search: str = "") -> dict:
        """List TTS replacements with pagination and search."""
        try:
            where = ""
            params = []
            if search:
                where = "WHERE pattern LIKE ? OR replacement LIKE ?"
                params = [f"%{search}%", f"%{search}%"]

            self.cursor.execute(f"SELECT COUNT(*) FROM tts_replacements {where}", params)
            total = self.cursor.fetchone()[0]

            offset = (page - 1) * per_page
            self.cursor.execute(
                f"SELECT * FROM tts_replacements {where} ORDER BY id LIMIT ? OFFSET ?", [*params, per_page, offset]
            )
            items = [dict(row) for row in self.cursor.fetchall()]
            return {"items": items, "total": total, "page": page}
        except Exception as e:
            logger.error(f"Failed to list TTS replacements: {e}")
            return {"items": [], "total": 0, "page": page}

    def add_tts_replacement(self, pattern: str, replacement: str = "", enabled: bool = True) -> bool:
        """Add or update a TTS replacement."""
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO tts_replacements (pattern, replacement, enabled) VALUES (?, ?, ?)",
                (pattern, replacement, 1 if enabled else 0),
            )
            self._commit()
            return True
        except Exception as e:
            logger.error(f"Failed to add TTS replacement: {e}")
            return False

    def update_tts_replacement(
        self, replacement_id: int, pattern: str, replacement: str = "", enabled: bool | None = None
    ) -> bool:
        """Update a TTS replacement by id. enabled=None keeps the current value."""
        try:
            if enabled is None:
                self.cursor.execute(
                    "UPDATE tts_replacements SET pattern=?, replacement=? WHERE id=?",
                    (pattern, replacement, replacement_id),
                )
            else:
                self.cursor.execute(
                    "UPDATE tts_replacements SET pattern=?, replacement=?, enabled=? WHERE id=?",
                    (pattern, replacement, 1 if enabled else 0, replacement_id),
                )
            self._commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to update TTS replacement: {e}")
            return False

    def delete_tts_replacement(self, replacement_id: int) -> bool:
        """Delete a TTS replacement by id."""
        try:
            self.cursor.execute("DELETE FROM tts_replacements WHERE id = ?", (replacement_id,))
            self._commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete TTS replacement: {e}")
            return False

    def delete_all_tts_replacements(self) -> int:
        """Delete all TTS replacements. Returns count deleted."""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM tts_replacements")
            count = self.cursor.fetchone()[0]
            self.cursor.execute("DELETE FROM tts_replacements")
            self._commit()
            return count
        except Exception as e:
            logger.error(f"Failed to delete all TTS replacements: {e}")
            return 0

    def get_all_tts_replacements(self) -> list:
        """Get all enabled TTS replacements for applying to text."""
        try:
            self.cursor.execute("SELECT pattern, replacement FROM tts_replacements WHERE enabled = 1 ORDER BY id")
            return [{"pattern": row["pattern"], "replacement": row["replacement"]} for row in self.cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get TTS replacements: {e}")
            return []

    def import_tts_replacements(self, replacements: list) -> int:
        """Bulk import TTS replacements from config. Returns count imported."""
        count = 0
        try:
            with self._transaction() as conn:
                for item in replacements:
                    pattern = item.get("pattern", "")
                    replacement = item.get("replacement", "")
                    if pattern:
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO tts_replacements (pattern, replacement) VALUES (?, ?)",
                            (pattern, replacement),
                        )
                        count += cur.rowcount
        except Exception as e:
            count = 0
            logger.error(f"Failed to import TTS replacements: {e}")
        return count

    def remove_unchanged_defaults(self, marker: str, rules: list[dict]) -> int:
        """Remove old default TTS rules once (marker names the change), only rows still exactly
        as they were added: a rule you changed stays. Returns the number removed."""
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM speech_seeded WHERE kind = 'migration' AND key = ?", (marker,)).fetchone():
                return 0
            removed = 0
            for rule in rules:
                removed += conn.execute(
                    "DELETE FROM tts_replacements WHERE pattern = ? AND replacement = ?",
                    (rule["pattern"], rule["replacement"]),
                ).rowcount
            conn.execute("INSERT OR IGNORE INTO speech_seeded (kind, key) VALUES ('migration', ?)", (marker,))
        return removed

    # =========================================================================
    # LOOKUP TABLES WITH DEFAULTS (city_abbreviations, talkgroup_codes)
    # =========================================================================

    _SEED_SPECS = {
        **LOOKUP_TABLES,
        "tts_replacements": {"cols": ("pattern", "replacement"), "key": ("pattern",), "upper": ()},
    }

    def seed_defaults(self, table: str, rows: list[dict]) -> int:
        """Add the built-in rows that were never added before. Returns the number added.

        Each row is added once: a row you changed keeps your text and a default you deleted
        stays deleted, also after updates (speech_seeded remembers what was added).
        """
        spec = self._SEED_SPECS[table]
        added = 0
        with self._transaction() as conn:
            seeded = {r[0] for r in conn.execute("SELECT key FROM speech_seeded WHERE kind = ?", (table,))}
            for row in rows:
                values = {c: str(row.get(c, "")) for c in spec["cols"]}
                for c in spec["upper"]:
                    values[c] = values[c].upper()
                key = "\x1f".join(values[c] for c in spec["key"])
                if key in seeded:
                    continue
                cols = list(spec["cols"])
                added += conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [values[c] for c in cols],
                ).rowcount
                conn.execute("INSERT OR IGNORE INTO speech_seeded (kind, key) VALUES (?, ?)", (table, key))
        if added:
            self.invalidate_text_caches()
        return added

    def _build_city_abbreviation_cache(self) -> dict[str, dict[str, str]]:
        by_code, by_city = {}, {}
        for row in self.cursor.execute("SELECT abbreviation, city FROM city_abbreviations ORDER BY id").fetchall():
            by_code[row[0]] = row[1]
            by_city.setdefault(row[1], row[0])
        return {"by_code": by_code, "by_city": by_city}

    def _city_abbreviations(self) -> dict[str, dict[str, str]]:
        return self._get_text_cache("city_abbreviations", "city_abbreviations", self._build_city_abbreviation_cache)

    def city_from_abbreviation(self, abbrev: str) -> str:
        """The place for a place-name code ("UTRECH" -> "Utrecht"), "" when unknown."""
        return self._city_abbreviations()["by_code"].get((abbrev or "").upper(), "")

    def abbreviation_for_city(self, city: str) -> str:
        """The place-name code of a place ("Utrecht" -> "UTRECH"), "" when unknown."""
        return self._city_abbreviations()["by_city"].get(city or "", "")

    def _build_talkgroup_cache(self) -> dict[str, dict[str, str]]:
        maps: dict[str, dict[str, str]] = {"discipline": {}, "region": {}}
        for row in self.cursor.execute("SELECT kind, code, name FROM talkgroup_codes").fetchall():
            maps.setdefault(row[0], {})[row[1]] = row[2]
        return maps

    def decode_talkgroup(self, code: str) -> tuple[str, str, str] | None:
        """ "BRT-01" -> ("Brandweer", "Rotterdam", "01"); None when it isn't a talk group code
        or its letters aren't in the talkgroup_codes table."""
        from .talkgroups import TALKGROUP

        m = TALKGROUP.fullmatch(code or "")
        if not m:
            return None
        maps = self._get_text_cache("talkgroups", "talkgroup_codes", self._build_talkgroup_cache)
        discipline, region = maps["discipline"].get(m.group(1)), maps["region"].get(m.group(2))
        if not discipline or not region:
            return None
        return discipline, region, m.group(3)

    def _lookup_values(self, table: str, row: dict) -> dict[str, str]:
        """A row's values for a lookup table, checked. Raises ValueError."""
        from .talkgroups import KINDS

        spec = self.LOOKUP_TABLES[table]
        values = {}
        for c in spec["cols"]:
            value = str(row.get(c) or "").strip()
            if c in spec["upper"]:
                value = value.upper()
            if not value:
                raise ValueError(f"{c} is required")
            values[c] = value
        if table == "talkgroup_codes":
            values["kind"] = values["kind"].lower()
            if values["kind"] not in KINDS:
                raise ValueError("kind must be discipline or region")
            if len(values["code"]) != KINDS[values["kind"]] or not values["code"].isalpha():
                raise ValueError(f"A {values['kind']} code is {KINDS[values['kind']]} letter(s)")
        return values

    def list_lookup(self, table: str, page: int = 1, per_page: int = 50, search: str = "") -> dict:
        spec = self.LOOKUP_TABLES[table]
        where, params = "", []
        if search:
            where = "WHERE " + " OR ".join(f"{c} LIKE ?" for c in spec["cols"])
            params = [f"%{search}%"] * len(spec["cols"])
        total = self.cursor.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0]
        rows = self.cursor.execute(
            f"SELECT * FROM {table} {where} ORDER BY {', '.join(spec['key'])} LIMIT ? OFFSET ?",
            [*params, per_page, (page - 1) * per_page],
        ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}

    def save_lookup(self, table: str, row: dict, row_id: int | None = None) -> int:
        """Add a row (a row with the same key is changed) or change the row with row_id.
        Returns its id; raises ValueError for invalid input."""
        spec = self.LOOKUP_TABLES[table]
        values = self._lookup_values(table, row)
        cols = list(spec["cols"])
        with self._transaction() as conn:
            match = " AND ".join(f"{c} = ?" for c in spec["key"])
            other = conn.execute(f"SELECT id FROM {table} WHERE {match}", [values[c] for c in spec["key"]]).fetchone()
            if other and other[0] != row_id:
                if row_id is not None:
                    raise ValueError("Another row already has this " + " and ".join(spec["key"]))
                row_id = other[0]
            if row_id is None:
                row_id = conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [values[c] for c in cols],
                ).lastrowid
            elif not conn.execute(
                f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                [*(values[c] for c in cols), row_id],
            ).rowcount:
                raise ValueError("Row not found")
        self.invalidate_text_caches()
        return row_id

    def delete_lookup(self, table: str, row_id: int) -> bool:
        self.LOOKUP_TABLES[table]
        with self._transaction() as conn:
            deleted = conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,)).rowcount
        self.invalidate_text_caches()
        return deleted > 0

    def export_lookup_csv(self, table: str) -> str:
        spec = self.LOOKUP_TABLES[table]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(spec["cols"])
        for row in self.cursor.execute(
            f"SELECT {', '.join(spec['cols'])} FROM {table} ORDER BY {', '.join(spec['key'])}"
        ).fetchall():
            writer.writerow(list(row))
        return output.getvalue()

    def import_lookup_csv(self, table: str, csv_content: str, replace: bool = False) -> dict[str, int]:
        """Import a CSV export of a lookup table (this app's or the FireServiceRota app's).
        Raises ValueError for invalid CSV; the table is left untouched then."""
        spec = self.LOOKUP_TABLES[table]
        rows = self._read_csv(csv_content, spec["cols"])
        items, skipped = [], 0
        for row in rows:
            try:
                items.append(self._lookup_values(table, row))
            except ValueError:
                skipped += 1
        cols = list(spec["cols"])
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in spec["key"])
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            f"ON CONFLICT({', '.join(spec['key'])}) DO UPDATE SET {updates}"
        )
        with self._transaction() as conn:
            if replace:
                conn.execute(f"DELETE FROM {table}")
            for values in items:
                conn.execute(sql, [values[c] for c in cols])
        self.invalidate_text_caches()
        return {"imported": len(items), "skipped": skipped}

    def import_city_abbreviations_csv(self, csv_content: str, replace: bool = False) -> dict[str, int]:
        return self.import_lookup_csv("city_abbreviations", csv_content, replace)

    def import_talkgroup_codes_csv(self, csv_content: str, replace: bool = False) -> dict[str, int]:
        return self.import_lookup_csv("talkgroup_codes", csv_content, replace)

    # =========================================================================
    # GLOBAL MESSAGE FILTERS (ignore_text / ignore_capcodes)
    # =========================================================================

    def list_ignore_text(self, page: int = 1, per_page: int = 50, search: str = "") -> dict:
        """List ignore-text patterns with pagination and search."""
        try:
            where = ""
            params = []
            if search:
                where = "WHERE pattern LIKE ?"
                params = [f"%{search}%"]

            self.cursor.execute(f"SELECT COUNT(*) FROM ignore_text {where}", params)
            total = self.cursor.fetchone()[0]

            offset = (page - 1) * per_page
            self.cursor.execute(
                f"SELECT * FROM ignore_text {where} ORDER BY id LIMIT ? OFFSET ?", [*params, per_page, offset]
            )
            items = [dict(row) for row in self.cursor.fetchall()]
            return {"items": items, "total": total, "page": page}
        except Exception as e:
            logger.error(f"Failed to list ignore-text patterns: {e}")
            return {"items": [], "total": 0, "page": page}

    def add_ignore_text(self, pattern: str, enabled: bool = True) -> bool:
        """Add or update an ignore-text pattern."""
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO ignore_text (pattern, enabled) VALUES (?, ?)", (pattern, 1 if enabled else 0)
            )
            self._commit()
            return True
        except Exception as e:
            logger.error(f"Failed to add ignore-text pattern: {e}")
            return False

    def update_ignore_text(self, rule_id: int, pattern: str, enabled: bool | None = None) -> bool:
        """Update an ignore-text pattern by id. enabled=None keeps the current value."""
        try:
            if enabled is None:
                self.cursor.execute("UPDATE ignore_text SET pattern=? WHERE id=?", (pattern, rule_id))
            else:
                self.cursor.execute(
                    "UPDATE ignore_text SET pattern=?, enabled=? WHERE id=?", (pattern, 1 if enabled else 0, rule_id)
                )
            self._commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to update ignore-text pattern: {e}")
            return False

    def delete_ignore_text(self, rule_id: int) -> bool:
        """Delete an ignore-text pattern by id."""
        try:
            self.cursor.execute("DELETE FROM ignore_text WHERE id = ?", (rule_id,))
            self._commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete ignore-text pattern: {e}")
            return False

    def delete_all_ignore_text(self) -> int:
        """Delete all ignore-text patterns. Returns count deleted."""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM ignore_text")
            count = self.cursor.fetchone()[0]
            self.cursor.execute("DELETE FROM ignore_text")
            self._commit()
            return count
        except Exception as e:
            logger.error(f"Failed to delete all ignore-text patterns: {e}")
            return 0

    def get_all_ignore_text(self) -> list[str]:
        """Get all enabled ignore-text patterns for filtering."""
        try:
            self.cursor.execute("SELECT pattern FROM ignore_text WHERE enabled = 1 ORDER BY id")
            return [row["pattern"] for row in self.cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get ignore-text patterns: {e}")
            return []

    def import_ignore_text(self, patterns: list[str]) -> int:
        """Bulk import ignore-text patterns from config. Returns count imported."""
        count = 0
        try:
            with self._transaction() as conn:
                for pattern in patterns:
                    if pattern:
                        cur = conn.execute("INSERT OR IGNORE INTO ignore_text (pattern) VALUES (?)", (pattern,))
                        count += cur.rowcount
        except Exception as e:
            count = 0
            logger.error(f"Failed to import ignore-text patterns: {e}")
        return count

    def export_ignore_text_csv(self) -> str:
        """Export all ignore-text patterns to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["pattern", "enabled"])
        try:
            self.cursor.execute("SELECT pattern, enabled FROM ignore_text ORDER BY id")
            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export ignore-text patterns failed: {e}")
        return output.getvalue()

    def list_ignore_capcodes(self, page: int = 1, per_page: int = 50, search: str = "") -> dict:
        """List ignored capcodes with pagination and search."""
        try:
            where = ""
            params = []
            if search:
                where = "WHERE capcode LIKE ?"
                params = [f"%{search}%"]

            self.cursor.execute(f"SELECT COUNT(*) FROM ignore_capcodes {where}", params)
            total = self.cursor.fetchone()[0]

            offset = (page - 1) * per_page
            self.cursor.execute(
                f"SELECT * FROM ignore_capcodes {where} ORDER BY id LIMIT ? OFFSET ?", [*params, per_page, offset]
            )
            items = [dict(row) for row in self.cursor.fetchall()]
            return {"items": items, "total": total, "page": page}
        except Exception as e:
            logger.error(f"Failed to list ignored capcodes: {e}")
            return {"items": [], "total": 0, "page": page}

    def add_ignore_capcode(self, capcode: str, enabled: bool = True) -> bool:
        """Add or update an ignored capcode."""
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO ignore_capcodes (capcode, enabled) VALUES (?, ?)",
                (capcode, 1 if enabled else 0),
            )
            self._commit()
            return True
        except Exception as e:
            logger.error(f"Failed to add ignored capcode: {e}")
            return False

    def update_ignore_capcode(self, rule_id: int, capcode: str, enabled: bool | None = None) -> bool:
        """Update an ignored capcode by id. enabled=None keeps the current value."""
        try:
            if enabled is None:
                self.cursor.execute("UPDATE ignore_capcodes SET capcode=? WHERE id=?", (capcode, rule_id))
            else:
                self.cursor.execute(
                    "UPDATE ignore_capcodes SET capcode=?, enabled=? WHERE id=?",
                    (capcode, 1 if enabled else 0, rule_id),
                )
            self._commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to update ignored capcode: {e}")
            return False

    def delete_ignore_capcode(self, rule_id: int) -> bool:
        """Delete an ignored capcode by id."""
        try:
            self.cursor.execute("DELETE FROM ignore_capcodes WHERE id = ?", (rule_id,))
            self._commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete ignored capcode: {e}")
            return False

    def delete_all_ignore_capcodes(self) -> int:
        """Delete all ignored capcodes. Returns count deleted."""
        try:
            self.cursor.execute("SELECT COUNT(*) FROM ignore_capcodes")
            count = self.cursor.fetchone()[0]
            self.cursor.execute("DELETE FROM ignore_capcodes")
            self._commit()
            return count
        except Exception as e:
            logger.error(f"Failed to delete all ignored capcodes: {e}")
            return 0

    def get_all_ignore_capcodes(self) -> list[str]:
        """Get all enabled ignored capcodes for filtering."""
        try:
            self.cursor.execute("SELECT capcode FROM ignore_capcodes WHERE enabled = 1 ORDER BY id")
            return [row["capcode"] for row in self.cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get ignored capcodes: {e}")
            return []

    def export_ignore_capcodes_csv(self) -> str:
        """Export all ignored capcodes to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["capcode", "enabled"])
        try:
            self.cursor.execute("SELECT capcode, enabled FROM ignore_capcodes ORDER BY id")
            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export ignored capcodes failed: {e}")
        return output.getvalue()

    # =========================================================================
    # VEHICLE PHOTOS (user-uploaded, keyed by vehicle number or category)
    # =========================================================================

    def get_vehicle_photo(self, key_type: str, key_value: str) -> dict | None:
        """Get a vehicle photo's mime type, label and image bytes. None if not set."""
        try:
            self.cursor.execute(
                "SELECT mime_type, label, image FROM vehicle_photos WHERE key_type = ? AND key_value = ?",
                (key_type, key_value),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            return {"mime_type": row["mime_type"], "label": row["label"], "image": bytes(row["image"])}
        except Exception as e:
            logger.error(f"Failed to get vehicle photo: {e}")
            return None

    def set_vehicle_photo(self, key_type: str, key_value: str, mime_type: str, image: bytes, label: str = "") -> bool:
        """Add or replace a vehicle photo for a vehicle number or category."""
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO vehicle_photos (key_type, key_value, label, mime_type, image) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_type, key_value, label, mime_type, image),
            )
            self._commit()
            return True
        except Exception as e:
            logger.error(f"Failed to set vehicle photo: {e}")
            return False

    def delete_vehicle_photo(self, key_type: str, key_value: str) -> bool:
        """Delete a vehicle photo, reverting that key to the fallback icon."""
        try:
            self.cursor.execute(
                "DELETE FROM vehicle_photos WHERE key_type = ? AND key_value = ?", (key_type, key_value)
            )
            self._commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete vehicle photo: {e}")
            return False

    def list_vehicle_photos(self, page: int = 1, per_page: int = 50, search: str = "") -> dict[str, Any]:
        """List vehicle photos with pagination and search (metadata only, no image bytes)."""
        offset = (page - 1) * per_page
        try:
            if search:
                search_term = f"%{search}%"
                self.cursor.execute(
                    "SELECT COUNT(*) FROM vehicle_photos WHERE key_value LIKE ? OR label LIKE ?",
                    (search_term, search_term),
                )
                total = self.cursor.fetchone()[0]
                self.cursor.execute(
                    """SELECT id, key_type, key_value, label, mime_type FROM vehicle_photos
                       WHERE key_value LIKE ? OR label LIKE ?
                       ORDER BY id LIMIT ? OFFSET ?""",
                    (search_term, search_term, per_page, offset),
                )
            else:
                self.cursor.execute("SELECT COUNT(*) FROM vehicle_photos")
                total = self.cursor.fetchone()[0]
                self.cursor.execute(
                    "SELECT id, key_type, key_value, label, mime_type FROM vehicle_photos ORDER BY id LIMIT ? OFFSET ?",
                    (per_page, offset),
                )
            items = [dict(row) for row in self.cursor.fetchall()]
            return {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page,
            }
        except Exception as e:
            logger.error(f"Failed to list vehicle photos: {e}")
            return {"items": [], "total": 0, "page": 1, "per_page": per_page, "pages": 0}

    # =========================================================================
    # VEHICLES (vehicle number -> what it is)
    # =========================================================================

    def _known_vehicles(self) -> dict[str, dict]:
        """All known vehicles by number (cached; messages look them up per row)."""
        if self._vehicle_cache is None:
            self.cursor.execute("SELECT number, category, description FROM vehicles")
            self._vehicle_cache = {row["number"]: dict(row) for row in self.cursor.fetchall()}
        return self._vehicle_cache

    def resolve_vehicle(self, text: str, discipline: str = "", abbreviations: list | None = None) -> dict:
        """A message's vehicle fields (see vehicle_types.resolve_vehicle)."""
        return vehicle_types.resolve_vehicle(text, discipline, abbreviations, self._known_vehicles())

    def list_vehicles(self, page: int = 1, per_page: int = 50, search: str = "") -> dict:
        """List known vehicles with pagination and search."""
        try:
            where = ""
            params = []
            if search:
                where = "WHERE number LIKE ? OR category LIKE ? OR description LIKE ?"
                params = [f"%{search}%"] * 3
            self.cursor.execute(f"SELECT COUNT(*) FROM vehicles {where}", params)
            total = self.cursor.fetchone()[0]
            offset = (page - 1) * per_page
            self.cursor.execute(
                f"SELECT number, category, description, source FROM vehicles {where} ORDER BY number LIMIT ? OFFSET ?",
                [*params, per_page, offset],
            )
            items = [dict(row) for row in self.cursor.fetchall()]
            return {"items": items, "total": total, "page": page, "per_page": per_page}
        except Exception as e:
            logger.error(f"Failed to list vehicles: {e}")
            return {"items": [], "total": 0, "page": page, "per_page": per_page}

    def set_vehicle(self, number: str, category: str = "", description: str = "") -> bool:
        """Add or replace a vehicle by hand (source 'user', never overwritten by learning)."""
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO vehicles (number, category, description, source) VALUES (?, ?, ?, 'user')",
                (number, category, description),
            )
            self._commit()
            self._vehicle_cache = None
            return True
        except Exception as e:
            logger.error(f"Failed to set vehicle: {e}")
            return False

    def delete_vehicle(self, number: str) -> bool:
        """Delete a vehicle by number."""
        try:
            self.cursor.execute("DELETE FROM vehicles WHERE number = ?", (number,))
            self._commit()
            self._vehicle_cache = None
            return self.cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete vehicle: {e}")
            return False

    def learn_vehicles(self, vehicles: list[tuple[str, str, str]], source: str) -> int:
        """Store (number, category, description) seen in real data; existing rows are kept.

        Only complete numbers ("RR-NNNN", "RR-NNN") - short local numbers like
        "TS 303" can't be matched to the numbers in messages.
        """
        count = 0
        try:
            with self._transaction() as conn:
                for number, category, description in vehicles:
                    if not vehicle_types.is_full_number(number):
                        continue
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO vehicles (number, category, description, source) VALUES (?, ?, ?, ?)",
                        (number, category, description, source),
                    )
                    count += cur.rowcount
        except Exception as e:
            logger.error(f"Failed to store vehicles: {e}")
            return 0
        if count:
            self._vehicle_cache = None
        return count

    def learn_vehicles_from_capcodes(self) -> int:
        """Vehicles named in capcode descriptions, e.g. "TS 01-2632" or "OvD - 15-9591"."""
        self.cursor.execute("SELECT description, region, location FROM capcodes WHERE description != ''")
        found = []
        for row in self.cursor.fetchall():
            description = row["description"].strip()
            place = " ".join(p.strip() for p in (row["region"], row["location"]) if p and p.strip())
            # The type only where the description names it: a code right before
            # the number ("TS 01-2632") or spelled out ("Ambulance-07-101")
            named = dict(vehicle_types.named_vehicles(description))
            spelled = vehicle_types.classify_description(description)
            for number in vehicle_types.full_numbers(description):
                category = named.get(number) or spelled
                found.append((number, category, f"{description} ({place})" if place else description))
        return self.learn_vehicles(found, "capcode")

    def export_vehicles_csv(self) -> str:
        """Export all vehicles to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["number", "category", "description", "source"])
        try:
            self.cursor.execute("SELECT number, category, description, source FROM vehicles ORDER BY number")
            for row in self.cursor.fetchall():
                writer.writerow(list(row))
        except Exception as e:
            logger.error(f"Export vehicles failed: {e}")
        return output.getvalue()

    def import_vehicles_csv(self, csv_content: str, replace: bool = False) -> dict[str, int]:
        """Import vehicles (number, category, description) from CSV as the user's own rows.

        With replace=True all vehicles not in the CSV are removed. Raises
        ValueError for invalid CSV; nothing is changed then.
        """
        rows = self._read_csv(csv_content, ("number",))
        imported = 0
        skipped = 0
        with self._transaction() as conn:
            if replace:
                conn.execute("DELETE FROM vehicles")
            for row in rows:
                number = (row.get("number") or "").strip()
                if not number:
                    skipped += 1
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO vehicles (number, category, description, source) VALUES (?, ?, ?, 'user')",
                    (number, (row.get("category") or "").strip(), (row.get("description") or "").strip()),
                )
                imported += 1
        self._vehicle_cache = None
        return {"imported": imported, "skipped": skipped}

    def clear_table(self, table: str) -> int:
        """Delete all rows from a whitelisted table. Returns count deleted, -1 on error."""
        allowed = {
            "capcodes",
            "places",
            "streets",
            "abbreviations",
            "geocodes",
            "tts_replacements",
            "messages",
            "ignore_text",
            "ignore_capcodes",
            "vehicle_photos",
            "vehicles",
            "city_abbreviations",
            "talkgroup_codes",
        }
        if table not in allowed:
            logger.warning(f"clear_table rejected for table: {table}")
            return -1
        try:
            self.cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = self.cursor.fetchone()[0]
            self.cursor.execute(f"DELETE FROM {table}")
            self._commit()
            if table in ("abbreviations", "city_abbreviations", "talkgroup_codes"):
                self.invalidate_text_caches()
            if table == "vehicles":
                self._vehicle_cache = None
            logger.info(f"Cleared table {table}: {count} rows deleted")
            return count
        except Exception as e:
            logger.error(f"Failed to clear table {table}: {e}")
            return -1

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            for cur in self._cursors.values():
                with contextlib.suppress(sqlite3.Error):
                    cur.close()
            self._cursors.clear()
            self.conn.close()

    def backup_to(self, path: str) -> None:
        """Write a consistent copy of the database to path (SQLite online backup)."""
        with self._lock:
            dest = sqlite3.connect(path)
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

    @staticmethod
    def check_backup(path: str) -> str | None:
        """Why the file at path can't be restored, or None when it's a sound Meldkamer database."""
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    return "The file is a damaged SQLite database"
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            finally:
                conn.close()
        except sqlite3.Error:
            return "The file is not an SQLite database"
        if not {"messages", "places", "capcodes"} <= tables:
            return "The file is an SQLite database, but not a Meldkamer one"
        return None

    def replace_with(self, path: str) -> None:
        """Replace the database file with the (checked) backup at path and reopen it."""
        with self._lock:
            self.close()
            for suffix in ("", "-wal", "-shm", "-journal"):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(self.db_path + suffix)
            os.replace(path, self.db_path)
            self._cursors = {}
            self._text_caches = {}
            self._vehicle_cache = None
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self.conn = _LockedConnection(self._conn, self._lock)
            self._migrate_schema()
            self.learn_vehicles_from_capcodes()
