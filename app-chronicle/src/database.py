"""Reads the tables of the Chronicle integration (schema "chronicle" in TimescaleDB).

The app never writes: every connection is opened read-only (default_transaction_read_only),
so even a mistake here can't change the history. Queries stay cheap on large histories:
catalog functions and approximate counts for the totals, the (entity, time) index for one
entity, the hourly and daily aggregates for long ranges, and a statement timeout.
"""

import asyncio
import contextlib
import json
import logging
import math
import re
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal

import asyncpg

from .config import Config

logger = logging.getLogger(__name__)

SCHEMA = "chronicle"
STATEMENT_TIMEOUT = "30s"
# Cache of the per-entity row counts of the last 24 hours (shared by Overview and Entities)
ACTIVITY_TTL = 60

# The ranges of the entity chart; "all" starts at the entity's first row. A range never
# starts before the entity's first row: with an hour of history, every range shows that hour.
RANGES: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "1y": timedelta(days=365),
    "all": None,
}
# The data behind a numeric chart follows the span it shows, not the range's name:
#   raw      every row; averaged per few minutes when there are more than RAW_POINTS
#   minutes  buckets of a few minutes, averaged from the rows
#   hourly   chronicle.states_hourly (averaged from the rows when it doesn't exist)
#   daily    chronicle.states_daily (the same)
RESOLUTIONS = (
    (timedelta(days=2), "raw"),
    (timedelta(days=21), "minutes"),
    (timedelta(days=120), "hourly"),
)
RAW_POINTS = 3000  # up to this many rows a numeric chart shows every row
TARGET_POINTS = 1000  # otherwise about this many buckets
STATE_ROWS = 5000  # rows of a non-numeric entity in one range
# The Query page: rows returned at most, and how long a query may run
QUERY_ROWS = 10_000
QUERY_TIMEOUT = "30s"
NICE_BUCKETS = (60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400)


class NotConnectedError(Exception):
    """The database can't be reached (the message says why)."""


class QueryRefusedError(Exception):
    """The Query page does not run this statement (the message says why)."""


# The Query page runs reading statements only. The READ ONLY transaction already stops every
# write; these rules also keep out what a read-only transaction still allows a superuser:
# server files, large objects, other sessions, settings and roles, running programs.
QUERY_FIRST_WORDS = ("select", "with", "table", "values", "explain", "show")
QUERY_DENIED = re.compile(
    r"\b(copy|set_config|pg_read_file|pg_read_binary_file|pg_ls_\w+|pg_stat_file|lo_\w+|"
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|pg_promote|"
    r"pg_switch_wal|pg_create_\w+|dblink\w*|pg_file_\w+|pg_logdir_ls|current_setting\s*\(\s*'role)\b"
    r"|\bu&['\"]",
    re.IGNORECASE,
)


def check_query(sql: str) -> None:
    """Raise QueryRefusedError for a statement the Query page doesn't run."""
    # Comments out of the way first, so they can't hide the first word
    text = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL).strip().lstrip("(").strip()
    first = text.split(None, 1)[0].lower() if text else ""
    if first not in QUERY_FIRST_WORDS:
        raise QueryRefusedError("Only reading statements run here: SELECT, WITH, TABLE, VALUES, EXPLAIN or SHOW")
    match = QUERY_DENIED.search(sql)
    if match:
        raise QueryRefusedError(f"{match.group(0).strip()} is not available on this page")


def resolution(span: timedelta) -> str:
    """The data source for a chart that shows this span (see RESOLUTIONS)."""
    return next((source for limit, source in RESOLUTIONS if span <= limit), "daily")


def bucket_seconds(span: float, points: int = TARGET_POINTS) -> int:
    """A round bucket width that splits span seconds into about `points` buckets."""
    want = max(1.0, span / max(1, points))
    return next((b for b in NICE_BUCKETS if b >= want), NICE_BUCKETS[-1])


def ts(value: datetime | None) -> float | None:
    """Epoch seconds for the browser (it formats in its own time zone)."""
    return None if value is None else round(value.timestamp(), 3)


def _number(value):
    """A float that JSON can carry (no NaN or infinity)."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


class Database:
    """Connection pool and every query the dashboard runs."""

    def __init__(self, config: Config):
        self.config = config
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self._activity: tuple[float, dict[int, int]] | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await asyncpg.create_pool(
                        host=self.config.host,
                        port=self.config.port,
                        user=self.config.username,
                        password=self.config.password,
                        database=self.config.database,
                        min_size=1,
                        max_size=4,
                        timeout=10,
                        command_timeout=60,
                        server_settings={
                            "application_name": "chronicle-app",
                            "default_transaction_read_only": "on",
                            "statement_timeout": STATEMENT_TIMEOUT,
                        },
                    )
                except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
                    self.last_error = _describe(e)
                    raise NotConnectedError(self.last_error) from e
                self.last_error = None
                logger.info(f"Connected to {self.config.database} on {self.config.host}:{self.config.port}")
        return self._pool

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(pool.close(), 5)

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        pool = await self._get_pool()
        try:
            return await pool.fetch(query, *args)
        except (OSError, asyncpg.InterfaceError, asyncpg.exceptions.ConnectionDoesNotExistError) as e:
            self.last_error = _describe(e)
            raise NotConnectedError(self.last_error) from e

    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args):
        row = await self.fetchrow(query, *args)
        return None if row is None else row[0]

    # ------------------------------------------------------------------ status

    async def status(self) -> dict:
        """Server, TimescaleDB and whether the Chronicle tables exist."""
        row = await self.fetchrow(
            """SELECT current_setting('server_version') AS server,
                      (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb') AS timescaledb,
                      to_regclass('chronicle.states') IS NOT NULL AS has_states,
                      to_regclass('chronicle.states_hourly') IS NOT NULL AS has_hourly,
                      to_regclass('chronicle.states_daily') IS NOT NULL AS has_daily"""
        )
        result = {
            "server": row["server"],
            "timescaledb": row["timescaledb"],
            "schema": bool(row["has_states"]),
            "aggregates": bool(row["has_hourly"] and row["has_daily"]),
            "meta": {},
        }
        if result["schema"]:
            result["meta"] = {r["key"]: r["value"] for r in await self.fetch(f"SELECT key, value FROM {SCHEMA}.meta")}
        return result

    # ------------------------------------------------------------------ overview

    async def activity(self) -> dict[int, int]:
        """Rows per entity id in the last 24 hours (cached for a minute)."""
        now = time.monotonic()
        if self._activity and now - self._activity[0] < ACTIVITY_TTL:
            return self._activity[1]
        rows = await self.fetch(
            f"""SELECT entity, count(*) AS rows FROM {SCHEMA}.states
                WHERE time > now() - INTERVAL '24 hours' GROUP BY entity"""
        )
        counts = {r["entity"]: r["rows"] for r in rows}
        self._activity = (now, counts)
        return counts

    async def overview(self, hours: int) -> dict:
        totals = await self.fetchrow(
            f"""SELECT pg_database_size(current_database()) AS database_size,
                       hypertable_size('{SCHEMA}.states') AS history_size,
                       approximate_row_count('{SCHEMA}.states') AS rows,
                       (SELECT count(*) FROM {SCHEMA}.entities) AS entities,
                       (SELECT min(range_start) FROM timescaledb_information.chunks
                         WHERE hypertable_schema = '{SCHEMA}' AND hypertable_name = 'states') AS oldest_chunk,
                       (SELECT count(*) FROM timescaledb_information.chunks
                         WHERE hypertable_schema = '{SCHEMA}' AND hypertable_name = 'states') AS chunks"""
        )
        compression = await self.fetchrow(
            f"""SELECT sum(before_compression_total_bytes) AS before,
                       sum(after_compression_total_bytes) AS after,
                       count(*) FILTER (WHERE compression_status = 'Compressed') AS compressed
                FROM chunk_compression_stats('{SCHEMA}.states')"""
        )
        # The first row lives in the oldest chunk: looking only there keeps this one chunk's work
        oldest = None
        if totals["oldest_chunk"] is not None:
            oldest = await self.fetchval(
                f"""SELECT min(time) FROM {SCHEMA}.states
                    WHERE time >= $1 AND time < $1 + INTERVAL '366 days'""",
                totals["oldest_chunk"],
            )
        bucket = "1 hour"
        per_hour = await self.fetch(
            f"""SELECT time_bucket(INTERVAL '{bucket}', time) AS bucket, count(*) AS rows
                FROM {SCHEMA}.states WHERE time >= date_trunc('hour', now()) - make_interval(hours => $1)
                GROUP BY 1 ORDER BY 1""",
            hours,
        )
        counts = await self.activity()
        entities = {
            r["id"]: r for r in await self.fetch(f"SELECT id, entity_id, domain, name, unit FROM {SCHEMA}.entities")
        }
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:15]
        domains: dict[str, dict] = {}
        for record in entities.values():
            d = domains.setdefault(record["domain"], {"domain": record["domain"], "entities": 0, "rows_24h": 0})
            d["entities"] += 1
        for entity, rows in counts.items():
            record = entities.get(entity)
            if record is not None:
                domains[record["domain"]]["rows_24h"] += rows
        before = int(compression["before"]) if compression["before"] is not None else None
        after = int(compression["after"]) if compression["after"] is not None else None
        return {
            "totals": {
                "entities": totals["entities"],
                "rows": max(totals["rows"] or 0, 0),
                "rows_24h": sum(counts.values()),
                "history_size": totals["history_size"] or 0,
                "database_size": totals["database_size"],
                "compressed_before": before,
                "compressed_after": after,
                "compression_ratio": round(before / after, 1) if before and after else None,
                "chunks": totals["chunks"],
                "compressed_chunks": compression["compressed"] or 0,
                "oldest": ts(oldest or totals["oldest_chunk"]),
            },
            "per_hour": [{"t": ts(r["bucket"]), "rows": r["rows"]} for r in per_hour],
            "top": [
                {
                    "entity_id": entities[e]["entity_id"],
                    "name": entities[e]["name"],
                    "domain": entities[e]["domain"],
                    "rows_24h": rows,
                }
                for e, rows in top
                if e in entities
            ],
            "domains": sorted(domains.values(), key=lambda d: (-d["rows_24h"], -d["entities"], d["domain"])),
        }

    # ------------------------------------------------------------------ entities

    async def entities(self) -> list[dict]:
        """Every entity with its latest state (one index lookup each) and its rows in 24 hours."""
        rows = await self.fetch(
            f"""SELECT e.id, e.entity_id, e.domain, e.name, e.unit, e.device_class, e.state_class,
                       e.first_seen, l.time, l.state, l.value
                FROM {SCHEMA}.entities e
                LEFT JOIN LATERAL (
                    SELECT s.time, s.state, s.value FROM {SCHEMA}.states s
                    WHERE s.entity = e.id ORDER BY s.time DESC LIMIT 1
                ) l ON true
                ORDER BY e.entity_id"""
        )
        counts = await self.activity()
        # When an entity's history starts: the daily aggregate (small) knows it for numbers and
        # on/off; first_seen (when Chronicle met it) for the rest. Imported history is older
        # than first_seen, so the earlier of the two wins.
        since: dict[int, datetime] = {}
        if await self.fetchval("SELECT to_regclass('chronicle.states_daily') IS NOT NULL"):
            since = {
                r["entity"]: r["since"]
                for r in await self.fetch(
                    f"SELECT entity, min(bucket) AS since FROM {SCHEMA}.states_daily GROUP BY entity"
                )
            }
        return [
            {
                "entity_id": r["entity_id"],
                "domain": r["domain"],
                "name": r["name"],
                "unit": r["unit"],
                "device_class": r["device_class"],
                "state_class": r["state_class"],
                "since": ts(min(filter(None, (since.get(r["id"]), r["first_seen"])))),
                "last": ts(r["time"]),
                "state": r["state"],
                "value": _number(r["value"]),
                "rows_24h": counts.get(r["id"], 0),
            }
            for r in rows
        ]

    async def entity(
        self,
        entity_id: str,
        range_key: str,
        aggregates: bool,
        window: tuple[datetime, datetime] | None = None,
    ) -> dict | None:
        """One entity's chart data for a range (see RANGES), or for an explicit window (zoom)."""
        meta = await self.fetchrow(
            f"""SELECT id, entity_id, domain, name, unit, device_class, state_class, first_seen
                FROM {SCHEMA}.entities WHERE entity_id = $1""",
            entity_id,
        )
        if meta is None:
            return None
        ident = meta["id"]
        latest = await self.fetchrow(
            f"""SELECT s.time, s.state, s.value, a.data AS attributes
                FROM {SCHEMA}.states s LEFT JOIN {SCHEMA}.attributes a ON a.id = s.attributes
                WHERE s.entity = $1 ORDER BY s.time DESC LIMIT 1""",
            ident,
        )
        first = await self.fetchval(f"SELECT time FROM {SCHEMA}.states WHERE entity = $1 ORDER BY time LIMIT 1", ident)
        now = datetime.now(UTC)
        if window is not None:
            start, end = window
        else:
            end = now
            length = RANGES[range_key]
            start = now - length if length is not None else (first or now)
        # Nothing to show before the first row: the chart starts there
        if first is not None and start < first:
            start = first
        if end <= start:
            end = start + timedelta(seconds=1)
        # Numbers are stored with an empty state: one such row among the recent ones decides
        numeric = bool(
            await self.fetchval(
                f"""SELECT bool_or(state IS NULL) FROM (
                        SELECT state FROM {SCHEMA}.states WHERE entity = $1 ORDER BY time DESC LIMIT 200
                    ) recent""",
                ident,
            )
        )
        result = {
            "entity": {
                "entity_id": meta["entity_id"],
                "domain": meta["domain"],
                "name": meta["name"],
                "unit": meta["unit"],
                "device_class": meta["device_class"],
                "state_class": meta["state_class"],
                "first_seen": ts(meta["first_seen"]),
                "first": ts(first),
            },
            "latest": None
            if latest is None
            else {
                "t": ts(latest["time"]),
                "state": latest["state"],
                "value": _number(latest["value"]),
                "attributes": json.loads(latest["attributes"]) if latest["attributes"] else None,
            },
            "range": None if window is not None else range_key,
            "zoomed": window is not None,
            "from": ts(start),
            "to": ts(end),
            "kind": "numeric" if numeric else "state",
        }
        if numeric:
            result.update(await self._numeric_series(ident, start, end, aggregates))
        else:
            result.update(await self._state_series(ident, start, end))
        return result

    async def _numeric_series(self, ident: int, start: datetime, end: datetime, aggregates: bool) -> dict:
        span = end - start
        source = resolution(span)
        if source == "raw":
            count = await self.fetchval(
                f"""SELECT count(*) FROM {SCHEMA}.states
                    WHERE entity = $1 AND time >= $2 AND time <= $3 AND value IS NOT NULL""",
                ident,
                start,
                end,
            )
            if count <= RAW_POINTS:
                return {"source": "raw", "points": await self._raw_points(ident, start, end)}
            source = "minutes"
        if source in ("hourly", "daily") and aggregates:
            view = f"{SCHEMA}.states_{source}"
            rows = await self.fetch(
                f"""SELECT bucket, mean, min, max, last, samples FROM {view}
                    WHERE entity = $1 AND bucket >= $2 AND bucket <= $3 ORDER BY bucket""",
                ident,
                start - timedelta(days=1 if source == "daily" else 0, hours=0 if source == "daily" else 1),
                end,
            )
            return {
                "source": source,
                "bucket": 3600 if source == "hourly" else 86400,
                "points": [_bucket_point(r, "bucket") for r in rows],
            }
        # Averaged from the rows: "minutes", or the averages are switched off in the integration
        width = bucket_seconds(span.total_seconds()) if source == "minutes" else (3600 if source == "hourly" else 86400)
        rows = await self.fetch(
            f"""SELECT time_bucket(make_interval(secs => $4), time) AS t,
                       avg(value) AS mean, min(value) AS min, max(value) AS max,
                       last(value, time) AS last, count(*) AS samples
                FROM {SCHEMA}.states
                WHERE entity = $1 AND time >= $2 AND time <= $3 AND value IS NOT NULL
                GROUP BY 1 ORDER BY 1""",
            ident,
            start,
            end,
            float(width),
        )
        return {"source": "buckets", "bucket": width, "points": [_bucket_point(r, "t") for r in rows]}

    async def _raw_points(self, ident: int, start: datetime, end: datetime) -> list[dict]:
        """Every value in the window; unavailable and unknown as gaps (v None).

        The value the entity had when the window starts, and still has when it ends, is
        carried to those edges (marked "carried"): a sensor that changes once an hour still
        draws a line, and isn't counted twice in the statistics or the CSV.
        """
        rows = await self.fetch(
            f"""SELECT time, value, state FROM {SCHEMA}.states
                WHERE entity = $1 AND time >= $2 AND time <= $3
                  AND (value IS NOT NULL OR state IN ('unavailable', 'unknown'))
                ORDER BY time""",
            ident,
            start,
            end,
        )
        points = [{"t": ts(r["time"]), "v": _number(r["value"])} for r in rows]
        before = await self.fetchrow(
            f"""SELECT time, value FROM {SCHEMA}.states
                WHERE entity = $1 AND time < $2 AND (value IS NOT NULL OR state IN ('unavailable', 'unknown'))
                ORDER BY time DESC LIMIT 1""",
            ident,
            start,
        )
        if before is not None and before["value"] is not None and (not points or points[0]["t"] > ts(start)):
            points.insert(0, {"t": ts(start), "v": _number(before["value"]), "carried": True})
        if points and points[-1]["v"] is not None and points[-1]["t"] < ts(end):
            points.append({"t": ts(end), "v": points[-1]["v"], "carried": True})
        return points

    async def _state_series(self, ident: int, start: datetime, end: datetime) -> dict:
        """The changes of a text entity in the window, plus the state it had when it starts.

        Rows that only changed attributes (a device tracker's GPS position, an automation's
        last_triggered) are no change of state: they are merged into the state before them.
        """
        rows = await self.fetch(
            f"""SELECT time, state, value FROM (
                    SELECT time, state, value,
                           lag(COALESCE(state, value::text)) OVER (ORDER BY time) AS previous
                    FROM {SCHEMA}.states WHERE entity = $1 AND time >= $2 AND time <= $3
                ) rows
                WHERE previous IS DISTINCT FROM COALESCE(state, value::text)
                ORDER BY time DESC LIMIT {STATE_ROWS}""",
            ident,
            start,
            end,
        )
        rows.reverse()
        before = await self.fetchrow(
            f"""SELECT time, state, value FROM {SCHEMA}.states
                WHERE entity = $1 AND time < $2 ORDER BY time DESC LIMIT 1""",
            ident,
            start,
        )
        points = [{"t": ts(r["time"]), "state": _text(r)} for r in rows]
        # The first row of the window repeats the state before it when only attributes changed
        if before is not None and points and points[0]["state"] == _text(before):
            points.pop(0)
        updates = await self.fetchval(
            f"SELECT count(*) FROM {SCHEMA}.states WHERE entity = $1 AND time >= $2 AND time <= $3",
            ident,
            start,
            end,
        )
        return {
            "source": "raw",
            "truncated": len(points) == STATE_ROWS,
            "updates": updates,
            "before": None if before is None else {"t": ts(before["time"]), "state": _text(before)},
            "points": points,
        }

    # ------------------------------------------------------------------ storage

    async def storage(self) -> dict:
        chunks = await self.fetch(
            f"""SELECT c.chunk_schema, c.chunk_name, c.range_start, c.range_end, c.is_compressed,
                       d.total_bytes, s.before_compression_total_bytes AS before,
                       s.after_compression_total_bytes AS after
                FROM timescaledb_information.chunks c
                LEFT JOIN chunks_detailed_size('{SCHEMA}.states') d
                       ON d.chunk_schema = c.chunk_schema AND d.chunk_name = c.chunk_name
                LEFT JOIN chunk_compression_stats('{SCHEMA}.states') s
                       ON s.chunk_schema = c.chunk_schema AND s.chunk_name = c.chunk_name
                WHERE c.hypertable_schema = '{SCHEMA}' AND c.hypertable_name = 'states'
                ORDER BY c.range_start DESC"""
        )
        aggregates = await self.fetch(
            """SELECT view_name, materialization_hypertable_schema AS mschema,
                      materialization_hypertable_name AS mname, materialized_only
               FROM timescaledb_information.continuous_aggregates WHERE view_schema = $1""",
            SCHEMA,
        )
        owners = {(a["mschema"], a["mname"]): a["view_name"] for a in aggregates}
        jobs = await self.fetch(
            """SELECT j.job_id, j.proc_name, j.schedule_interval, j.config::text AS config, j.scheduled,
                      j.hypertable_schema, j.hypertable_name,
                      s.last_run_started_at, s.last_successful_finish, s.last_run_status,
                      s.next_start, s.total_runs, s.total_failures, s.last_run_duration
               FROM timescaledb_information.jobs j
               LEFT JOIN timescaledb_information.job_stats s ON s.job_id = j.job_id
               ORDER BY j.job_id"""
        )
        tables = []
        for name in ("entities", "attributes"):
            tables.append(
                {
                    "name": f"{SCHEMA}.{name}",
                    "bytes": await self.fetchval("SELECT pg_total_relation_size($1::regclass)", f"{SCHEMA}.{name}"),
                    "rows": max(
                        await self.fetchval(
                            "SELECT reltuples::bigint FROM pg_class WHERE oid = $1::regclass", f"{SCHEMA}.{name}"
                        )
                        or 0,
                        0,
                    ),
                }
            )
        tables.insert(
            0,
            {
                "name": f"{SCHEMA}.states",
                "bytes": await self.fetchval(f"SELECT hypertable_size('{SCHEMA}.states')"),
                "rows": max(await self.fetchval(f"SELECT approximate_row_count('{SCHEMA}.states')") or 0, 0),
            },
        )
        for a in aggregates:
            tables.append(
                {
                    "name": f"{SCHEMA}.{a['view_name']}",
                    "bytes": await self.fetchval(
                        "SELECT hypertable_size(format('%I.%I', $1::text, $2::text)::regclass)",
                        a["mschema"],
                        a["mname"],
                    ),
                    "rows": max(
                        await self.fetchval(
                            "SELECT approximate_row_count(format('%I.%I', $1::text, $2::text)::regclass)",
                            a["mschema"],
                            a["mname"],
                        )
                        or 0,
                        0,
                    ),
                }
            )
        # An exact count of attribute sets is cheap enough to show when the estimate is unknown
        attribute_sets = tables[2]["rows"] or await self.fetchval(f"SELECT count(*) FROM {SCHEMA}.attributes")
        chronicle_jobs = []
        for j in jobs:
            key = (j["hypertable_schema"], j["hypertable_name"])
            # Newer TimescaleDB names an aggregate's own view here, older its internal hypertable
            if j["hypertable_schema"] == SCHEMA:
                target = f"{SCHEMA}.{j['hypertable_name']}"
            elif key in owners:
                target = f"{SCHEMA}.{owners[key]}"
            else:
                continue
            chronicle_jobs.append(
                {
                    "id": j["job_id"],
                    "kind": _job_kind(j["proc_name"]),
                    "target": target,
                    "schedule": _interval(j["schedule_interval"]),
                    "config": j["config"],
                    "scheduled": j["scheduled"],
                    "last_run": ts(j["last_run_started_at"]),
                    "last_success": ts(j["last_successful_finish"]),
                    "status": j["last_run_status"],
                    "next": ts(j["next_start"]),
                    "runs": j["total_runs"],
                    "failures": j["total_failures"],
                    "duration": _interval(j["last_run_duration"]),
                }
            )
        return {
            "chunks": [
                {
                    "name": c["chunk_name"],
                    "from": ts(c["range_start"]),
                    "to": ts(c["range_end"]),
                    "compressed": c["is_compressed"],
                    "bytes": c["total_bytes"],
                    "before": c["before"],
                    "after": c["after"],
                }
                for c in chunks
            ],
            "jobs": chronicle_jobs,
            "tables": tables,
            "attribute_sets": attribute_sets,
            "aggregates": [{"name": a["view_name"], "realtime": not a["materialized_only"]} for a in aggregates],
        }

    # ------------------------------------------------------------------ query

    async def query(self, sql: str) -> dict:
        """Run one SQL statement read-only and return at most QUERY_ROWS rows.

        It runs in a READ ONLY transaction that is always rolled back, with its own statement
        timeout: PostgreSQL itself refuses anything that writes (INSERT, UPDATE, DELETE, DROP,
        ...), whoever the user is. Only one statement: a prepared statement can't hold more.
        """
        check_query(sql)
        pool = await self._get_pool()
        started = time.monotonic()
        try:
            async with pool.acquire() as conn:
                transaction = conn.transaction(readonly=True)
                await transaction.start()
                try:
                    await conn.execute(f"SET LOCAL statement_timeout = '{QUERY_TIMEOUT}'")
                    # A superuser drops its powers for the query: pg_read_all_data reads every
                    # table but can't read server files, signal other sessions or run programs
                    if await conn.fetchval(
                        "SELECT pg_has_role(current_user, 'pg_read_all_data', 'MEMBER') AND current_setting('is_superuser') = 'on'"
                    ):
                        await conn.execute("SET LOCAL ROLE pg_read_all_data")
                    statement = await conn.prepare(sql)
                    columns = [{"name": a.name, "type": a.type.name} for a in statement.get_attributes()]
                    if columns:
                        cursor = await statement.cursor()
                        rows = await cursor.fetch(QUERY_ROWS + 1)
                    else:
                        rows = []
                        await statement.fetch()
                finally:
                    await transaction.rollback()
        except (OSError, asyncpg.InterfaceError, asyncpg.exceptions.ConnectionDoesNotExistError) as e:
            self.last_error = _describe(e)
            raise NotConnectedError(self.last_error) from e
        truncated = len(rows) > QUERY_ROWS
        return {
            "columns": columns,
            "rows": [[_cell(v) for v in row] for row in rows[:QUERY_ROWS]],
            "truncated": truncated,
            "limit": QUERY_ROWS,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }


def _cell(value):
    """A result value as JSON: times as ISO text, numbers as numbers, the rest as text or JSON."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else str(value)
    if isinstance(value, datetime | date | dt_time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return "\\x" + bytes(value).hex()
    if isinstance(value, list | tuple):
        return [_cell(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _cell(v) for k, v in value.items()}
    return str(value)


def _bucket_point(row: asyncpg.Record, column: str) -> dict:
    return {
        "t": ts(row[column]),
        "mean": _number(row["mean"]),
        "min": _number(row["min"]),
        "max": _number(row["max"]),
        "last": _number(row["last"]),
        "n": row["samples"],
    }


def _text(row: asyncpg.Record) -> str:
    """The state as Home Assistant showed it: numbers are stored as value only."""
    if row["state"] is not None:
        return row["state"]
    value = _number(row["value"])
    if value is None:
        return ""
    return str(int(value)) if value.is_integer() else repr(value)


def _job_kind(proc: str) -> str:
    return {
        "policy_compression": "Compression",
        "policy_retention": "Retention",
        "policy_refresh_continuous_aggregate": "Aggregate refresh",
    }.get(proc, proc)


def _interval(value: timedelta | None) -> float | None:
    return None if value is None else value.total_seconds()


def _describe(error: BaseException) -> str:
    """A readable reason for the Info page and the hint banner."""
    if isinstance(error, asyncpg.InvalidPasswordError):
        return "the password or user name is wrong"
    if isinstance(error, asyncpg.InvalidCatalogNameError):
        return "the database does not exist"
    if isinstance(error, TimeoutError):
        return "the server did not answer in time"
    if isinstance(error, OSError):
        return f"cannot reach the server ({error.strerror or error})"
    return f"{type(error).__name__}: {error}"
