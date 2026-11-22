"""Vigil's own database on the Chronicle app (schema "vigil"): the connection, the tables, the
writer and every query the pages and the rule engine run.

The database may be down or not yet made when Vigil starts (Chronicle starting, the login not
created yet). Vigil then keeps trying, with a growing pause; the pages show why, and the
receivers keep what they get in the write queue until it is full.

TimescaleDB, when the database has it: logs and points are hypertables (1-day chunks),
compressed after 2 days and dropped after the retention of the options. Without it they are
plain tables and the housekeeping deletes old rows every hour.

Every value in a query is a $n parameter; the few names put into SQL text (aggregates, columns
to group by) come from the fixed tables below, never from a request.
"""

import asyncio
import contextlib
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncpg

from .anomaly import WEEK, band
from .config import Config
from .patterns import PatternMiner, is_spike
from .query import to_where
from .records import SEVERITIES, SEVERITY_GROUPS, LogEntry, Sample, severity_group

logger = logging.getLogger(__name__)

SCHEMA = "vigil"
SCHEMA_VERSION = 1
STATEMENT_TIMEOUT = "30s"
COMPRESS_AFTER = "2 days"
ALERTS_RETENTION_DAYS = 365

QUEUE_SIZE = 50_000
BATCH_SIZE = 2_000
FLUSH_INTERVAL = 1.0
SOURCES_INTERVAL = 5.0
SERIES_CACHE = 100_000
MAX_SOURCES = 2_000
RETRY_DELAYS = (1, 2, 5, 10, 20, 30, 60)
STATUS_TTL = 30

# Metric aggregations: per series and bucket first (inner), then over the series of a group (outer).
# sum adds the series' averages (adding raw points would count a series scraped twice as often
# twice); rate is per second, from the differences between points, a counter reset counting as
# a restart from 0.
METRIC_AGGS = {
    "avg": ("avg(value)", "avg"),
    "min": ("min(value)", "min"),
    "max": ("max(value)", "max"),
    "sum": ("avg(value)", "sum"),
    "last": ("(array_agg(value ORDER BY ts DESC))[1]", "avg"),
    "rate": ("sum(CASE WHEN dv < 0 THEN value ELSE dv END) / nullif(sum(dt), 0)", "sum"),
}
LOG_COLUMNS = ["ts", "received", "source", "host", "app", "severity", "facility", "message", "attrs", "pattern"]
LOG_GROUPS = {"host": "host", "app": "app", "source": "source"}
FIELD_COLUMNS = {"hosts": "host", "apps": "app", "sources": "source", "severities": "severity"}
NICE_STEPS = (10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
MAX_GROUPS = 30
MAX_BUCKETS = 2000

# Connection trouble: worth waiting for and retrying (a bad row or a bad query is not)
CONNECTION_ERRORS = (
    OSError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.AdminShutdownError,
    asyncpg.TooManyConnectionsError,
)


class NotConnectedError(Exception):
    """The database can't be reached (the message says why)."""


def describe(error: BaseException) -> str:
    """A readable reason for the Info page and the hint banner (never the password)."""
    if isinstance(error, asyncpg.InvalidPasswordError):
        return "the password or user name is wrong (make the login in Chronicle, Maintenance, App databases)"
    if isinstance(error, asyncpg.InvalidAuthorizationSpecificationError):
        return f"the login was refused: {error}"
    if isinstance(error, asyncpg.InvalidCatalogNameError):
        return "the database does not exist (make it in Chronicle, Maintenance, App databases)"
    if isinstance(error, TimeoutError):
        return "the server did not answer in time"
    if isinstance(error, OSError):
        return f"cannot reach the server ({error.strerror or error})"
    return f"{type(error).__name__}: {error}"


def is_connection_error(error: BaseException) -> bool:
    """Trouble reaching the database (worth waiting for), as opposed to a refused value or query."""
    if isinstance(error, asyncpg.exceptions._base.DataError):
        return False
    if isinstance(error, (*CONNECTION_ERRORS, asyncpg.InterfaceError, NotConnectedError)):
        return True
    if isinstance(error, asyncpg.PostgresError):
        state = getattr(error, "sqlstate", "") or ""
        # 08 connection, 53 out of resources (disk full, too many connections), 57 shutting down;
        # 55000 is also "database ... is not currently accepting connections"
        return state[:2] in ("08", "53", "57") or (state == "55000" and "connections" in str(error))
    return False


def _is_refused(error: BaseException) -> bool:
    """A record the database will never take (a bad value): retrying it can only block the queue."""
    return isinstance(
        error,
        (
            asyncpg.exceptions._base.DataError,
            asyncpg.DataError,
            asyncpg.IntegrityConstraintViolationError,
            asyncpg.ProgramLimitExceededError,
        ),
    )


def ts(value: datetime | None) -> float | None:
    """Epoch seconds for the browser (it formats in its own time zone)."""
    if value is None or value.year <= 1 or value.year >= 9999:
        return None
    return round(value.timestamp(), 3)


def epoch(value: float) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def _number(value) -> float | None:
    """A float that JSON can carry (no NaN or infinity)."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _json_value(value):
    if value is None or isinstance(value, dict | list):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _clean(text: str | None) -> str | None:
    # PostgreSQL text can't hold NUL characters; a device that sends them would block the writer
    return text.replace("\x00", "") if text and "\x00" in text else text


def _dumps(value) -> str:
    return json.dumps(value, default=str, separators=(",", ":")).replace("\\u0000", "")


def nice_step(span: float, points: int) -> int:
    """A round step that splits span seconds into about `points` buckets."""
    want = max(1.0, span / max(1, points))
    return next((s for s in NICE_STEPS if s >= want), NICE_STEPS[-1])


# ---------------------------------------------------------------------- the schema

SCHEMA_V1 = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
    f"CREATE TABLE IF NOT EXISTS {SCHEMA}.meta (key text PRIMARY KEY, value text)",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.sources (
        id serial PRIMARY KEY, kind text NOT NULL, name text NOT NULL, host text,
        first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz,
        received bigint NOT NULL DEFAULT 0, errors bigint NOT NULL DEFAULT 0, last_error text,
        UNIQUE (kind, name))""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.logs (
        ts timestamptz NOT NULL, received timestamptz NOT NULL DEFAULT now(),
        source text NOT NULL, host text, app text, severity smallint NOT NULL, facility smallint,
        message text NOT NULL, attrs jsonb NOT NULL DEFAULT '{{}}', pattern bigint,
        search tsvector GENERATED ALWAYS AS
            (to_tsvector('simple', coalesce(host, '') || ' ' || coalesce(app, '') || ' ' || message)) STORED)""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.patterns (
        id bigserial PRIMARY KEY, template text NOT NULL UNIQUE,
        first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz NOT NULL DEFAULT now(),
        count bigint NOT NULL DEFAULT 0, sample text, host text, app text)""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.series (
        id serial PRIMARY KEY, name text NOT NULL, labels jsonb NOT NULL, unit text, kind text,
        created timestamptz NOT NULL DEFAULT now(), UNIQUE (name, labels))""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.points (
        ts timestamptz NOT NULL, series integer NOT NULL, value double precision NOT NULL)""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.rules (
        id serial PRIMARY KEY, name text NOT NULL, kind text NOT NULL, query jsonb NOT NULL DEFAULT '{{}}',
        condition jsonb NOT NULL DEFAULT '{{}}', for_seconds integer NOT NULL DEFAULT 0,
        severity text NOT NULL DEFAULT 'warning', enabled boolean NOT NULL DEFAULT true,
        notify boolean NOT NULL DEFAULT true, state text NOT NULL DEFAULT 'ok', state_since timestamptz,
        last_value double precision, last_eval timestamptz,
        created timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now())""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.alerts (
        id bigserial PRIMARY KEY, rule integer REFERENCES {SCHEMA}.rules ON DELETE CASCADE,
        ts timestamptz NOT NULL DEFAULT now(), state text NOT NULL, value double precision, message text)""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.dashboards (
        id serial PRIMARY KEY, name text NOT NULL UNIQUE, panels jsonb NOT NULL DEFAULT '[]',
        created timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now())""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.saved_searches (
        id serial PRIMARY KEY, name text NOT NULL UNIQUE, query text NOT NULL,
        created timestamptz NOT NULL DEFAULT now())""",
    f"CREATE INDEX IF NOT EXISTS logs_ts ON {SCHEMA}.logs (ts DESC)",
    f"CREATE INDEX IF NOT EXISTS logs_host_ts ON {SCHEMA}.logs (host, ts DESC)",
    f"CREATE INDEX IF NOT EXISTS logs_severity_ts ON {SCHEMA}.logs (severity, ts DESC)",
    f"CREATE INDEX IF NOT EXISTS logs_pattern_ts ON {SCHEMA}.logs (pattern, ts DESC)",
    f"CREATE INDEX IF NOT EXISTS logs_search ON {SCHEMA}.logs USING gin (search)",
    f"CREATE INDEX IF NOT EXISTS points_series_ts ON {SCHEMA}.points (series, ts DESC)",
    f"CREATE INDEX IF NOT EXISTS alerts_rule_ts ON {SCHEMA}.alerts (rule, ts DESC)",
    f"CREATE INDEX IF NOT EXISTS alerts_ts ON {SCHEMA}.alerts (ts DESC)",
    f"CREATE INDEX IF NOT EXISTS patterns_first_seen ON {SCHEMA}.patterns (first_seen DESC)",
]
# Later layouts add their statements here: {2: [...], ...}
MIGRATIONS: dict[int, list[str]] = {1: SCHEMA_V1}

# The panels' query is what the Dashboards page sends: {source: 'logs' | 'fields' | 'patterns', q,
# field, sort}, or a metric query {name, filter, agg, group, anomaly}, or {queries: [metric query,
# ...]} for lines overlaid in one chart (see API_NOTES.md)
BUILTIN_DASHBOARDS = {
    "Hosts": [
        {"id": "h1", "title": "Log volume by host", "type": "bar", "query": {"source": "fields", "field": "hosts"}},
        {
            "id": "h2",
            "title": "Errors by host",
            "type": "bar",
            "query": {"source": "fields", "field": "hosts", "q": "severity:<=err"},
        },
        {"id": "h3", "title": "Log volume", "type": "timeseries", "query": {"source": "logs", "q": ""}, "w": 3, "h": 1},
        {"id": "h4", "title": "Errors", "type": "stat", "query": {"source": "logs", "q": "severity:<=err"}, "w": 1},
        {
            "id": "h5",
            "title": "Top patterns",
            "type": "table",
            "query": {"source": "patterns", "sort": "count"},
            "w": 4,
        },
    ],
    "Network": [
        {
            "id": "n1",
            "title": "FRITZ!Box throughput",
            "type": "timeseries",
            "query": {
                "queries": [
                    {"name": "fritz.wan_bytes_received_rate", "agg": "avg"},
                    {"name": "fritz.wan_bytes_sent_rate", "agg": "avg"},
                ]
            },
            "w": 3,
        },
        {
            "id": "n2",
            "title": "WAN link",
            "type": "stat",
            "query": {"name": "fritz.wan_link_up", "agg": "last"},
            "w": 1,
        },
        {
            "id": "n3",
            "title": "FRITZ!Box uptime",
            "type": "stat",
            "query": {"name": "fritz.uptime_seconds", "agg": "last"},
        },
        {
            "id": "n4",
            "title": "MikroTik interface traffic",
            "type": "timeseries",
            # The names of the MKTXP exporter (a scrape target), one line per interface
            "query": {
                "queries": [
                    {"name": "mktxp_interface_rx_byte_total", "agg": "rate", "group": "name"},
                    {"name": "mktxp_interface_tx_byte_total", "agg": "rate", "group": "name"},
                ]
            },
        },
        {
            "id": "n5",
            "title": "Router CPU",
            "type": "timeseries",
            "query": {"name": "mktxp_system_cpu_load", "agg": "avg", "anomaly": True},
        },
        {"id": "n6", "title": "Router log", "type": "logs", "query": {"source": "logs", "q": "app:system"}, "w": 4},
    ],
}
# Panels without a size are 2 x 2 (and 1 high for a stat)
for _panels in BUILTIN_DASHBOARDS.values():
    for _panel in _panels:
        _panel.setdefault("w", 2)
        _panel.setdefault("h", 1 if _panel["type"] == "stat" else 2)


@dataclass
class DatabaseInfo:
    host: str | None = None
    timescaledb: str | None = None
    version: str | None = None


# ---------------------------------------------------------------------- the database


class Database:
    """The connection pool (kept up in the background), the schema and the read queries."""

    def __init__(self, config: Config, resolve_host=None):
        self.config = config
        self._resolve_host = resolve_host  # async () -> host, when database_host is empty
        self._pool: asyncpg.Pool | None = None
        self.ready = asyncio.Event()
        self.last_error: str | None = "connecting"
        self.info = DatabaseInfo(host=config.db_host or None)
        self._status: tuple[float, dict] | None = None
        self.on_series_purged = None  # the writer forgets its series ids

    @property
    def connected(self) -> bool:
        return self._pool is not None and self.ready.is_set()

    @property
    def timescaledb(self) -> bool:
        return bool(self.info.timescaledb)

    # ------------------------------------------------------------------ connecting

    async def run(self) -> None:
        """Connects (and reconnects) for as long as the app runs."""
        attempt = 0
        while True:
            if self._pool is None:
                try:
                    await self._connect()
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.last_error = e.args[0] if isinstance(e, NotConnectedError | RuntimeError) else describe(e)
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    attempt += 1
                    log = logger.warning if attempt in (1, 5) or attempt % 30 == 0 else logger.debug
                    log(f"Cannot use the database: {self.last_error}; trying again in {delay} s")
                    await self._close_pool()
                    await asyncio.sleep(delay)
                    continue
            # Checked often while the connection is lost, so writing resumes soon after it is back
            await asyncio.sleep(30 if self.ready.is_set() else 5)
            await self._check()

    async def _connect(self) -> None:
        host = self.config.db_host
        if not host and self._resolve_host is not None:
            host = await self._resolve_host()
        if not host:
            raise NotConnectedError("no database host")
        self.info.host = host
        self._pool = await asyncpg.create_pool(
            host=host,
            port=self.config.db_port,
            user=self.config.db_user,
            password=self.config.db_password,
            database=self.config.db_name,
            min_size=1,
            max_size=8,
            timeout=10,
            command_timeout=120,
            server_settings={
                "search_path": f"{SCHEMA}, public",
                "application_name": "vigil",
                "statement_timeout": STATEMENT_TIMEOUT,
                "timezone": "UTC",
            },
        )
        await self.ensure_schema()
        self.last_error = None
        self.ready.set()
        logger.info(
            f"Connected to {self.config.db_name} on {host}:{self.config.db_port} as {self.config.db_user}"
            f" ({'TimescaleDB ' + self.info.timescaledb if self.info.timescaledb else 'without TimescaleDB'})"
        )

    async def _check(self) -> None:
        """A cheap query now and then, so the status shows a lost connection without a page asking."""
        if self._pool is None:
            return
        try:
            await self._pool.fetchval("SELECT 1")
            if not self.ready.is_set():
                self.ready.set()
            self.last_error = None
        except Exception as e:
            self.last_error = describe(e)
            if is_connection_error(e):
                logger.warning(f"Lost the database: {self.last_error}")
                self.ready.clear()

    async def _close_pool(self) -> None:
        pool, self._pool = self._pool, None
        self.ready.clear()
        if pool is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(pool.close(), 5)

    async def close(self) -> None:
        await self._close_pool()

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None or not self.ready.is_set():
            raise NotConnectedError(self.last_error or "not connected yet")
        return self._pool

    def lost(self, e: BaseException) -> None:
        """Another part (the writer) lost the connection: show it, and let the checks find it back."""
        self._failed(e)

    async def reset(self) -> None:
        """Connect again from scratch (and set up the schema again)."""
        await self._close_pool()
        self.last_error = "reconnecting"

    def _failed(self, e: BaseException) -> NotConnectedError:
        self.last_error = describe(e)
        self.ready.clear()
        return NotConnectedError(self.last_error)

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        pool = self._require_pool()
        try:
            return await pool.fetch(query, *args)
        except (*CONNECTION_ERRORS, asyncpg.InterfaceError, asyncpg.PostgresError) as e:
            if not is_connection_error(e):
                raise
            raise self._failed(e) from e

    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args):
        row = await self.fetchrow(query, *args)
        return None if row is None else row[0]

    async def execute(self, query: str, *args) -> str:
        pool = self._require_pool()
        try:
            return await pool.execute(query, *args)
        except (*CONNECTION_ERRORS, asyncpg.InterfaceError, asyncpg.PostgresError) as e:
            if not is_connection_error(e):
                raise
            raise self._failed(e) from e

    def acquire(self):
        return self._require_pool().acquire()

    # ------------------------------------------------------------------ schema

    async def ensure_schema(self) -> None:
        """Creates or migrates the tables, then (re)applies the TimescaleDB setup and policies."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT current_setting('server_version') AS version,
                          (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb') AS timescaledb"""
            )
            self.info.version = row["version"]
            self.info.timescaledb = row["timescaledb"]
            async with conn.transaction():
                # Two Vigils on one database (a restart overlapping) must not migrate at once
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext('vigil.schema'))")
                exists = await conn.fetchval("SELECT to_regclass('vigil.meta') IS NOT NULL")
                current = 0
                if exists:
                    value = await conn.fetchval("SELECT value FROM vigil.meta WHERE key = 'schema_version'")
                    current = int(value) if value and value.isdigit() else 0
                if current > SCHEMA_VERSION:
                    raise NotConnectedError(
                        f"the database has Vigil's layout {current}, newer than this version knows ({SCHEMA_VERSION})"
                    )
                for version in sorted(MIGRATIONS):
                    if version > current:
                        for statement in MIGRATIONS[version]:
                            await conn.execute(statement)
                        if version == 1:
                            await self._create_builtins(conn)
                        logger.info(f"Database layout {version} applied")
                await conn.execute(
                    """INSERT INTO vigil.meta (key, value) VALUES ('schema_version', $1)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                    str(SCHEMA_VERSION),
                )
            if self.info.timescaledb:
                await self._timescale(conn)

    async def _create_builtins(self, conn: asyncpg.Connection) -> None:
        for name, panels in BUILTIN_DASHBOARDS.items():
            await conn.execute(
                "INSERT INTO vigil.dashboards (name, panels) VALUES ($1, $2::jsonb) ON CONFLICT (name) DO NOTHING",
                name,
                _dumps(panels),
            )

    async def _timescale(self, conn: asyncpg.Connection) -> None:
        """Hypertables, compression and retention; each step on its own, so a TimescaleDB without
        compression (the Apache-licensed build) still gets its hypertables and retention."""
        retention = {"logs": self.config.logs_retention_days, "points": self.config.metrics_retention_days}
        compress = {"logs": "", "points": "series"}
        for table, days in retention.items():
            steps = [
                (
                    "hypertable",
                    f"""SELECT create_hypertable('vigil.{table}', by_range('ts', INTERVAL '1 day'),
                           create_default_indexes => false, if_not_exists => true, migrate_data => true)""",
                    (),
                ),
                ("compression", None, ()),
                (
                    "retention",
                    f"""SELECT remove_retention_policy('vigil.{table}', if_exists => true),
                              add_retention_policy('vigil.{table}', make_interval(days => $1))""",
                    (days,),
                ),
            ]
            for step, statement, args in steps:
                try:
                    async with conn.transaction():
                        if step == "compression":
                            await self._compression(conn, table, compress[table])
                        else:
                            await conn.execute(statement, *args)
                except asyncpg.PostgresError as e:
                    logger.warning(f"TimescaleDB {step} for vigil.{table} is not set up: {e}")

    async def _compression(self, conn: asyncpg.Connection, table: str, segment_by: str) -> None:
        enabled = await conn.fetchval(
            """SELECT compression_enabled FROM timescaledb_information.hypertables
               WHERE hypertable_schema = 'vigil' AND hypertable_name = $1""",
            table,
        )
        if not enabled:
            options = "timescaledb.compress, timescaledb.compress_orderby = 'ts DESC'"
            if segment_by:
                options += f", timescaledb.compress_segmentby = '{segment_by}'"
            await conn.execute(f"ALTER TABLE vigil.{table} SET ({options})")
        await conn.execute(
            f"SELECT add_compression_policy('vigil.{table}', INTERVAL '{COMPRESS_AFTER}', if_not_exists => true)"
        )

    async def seed_patterns(self, miner: PatternMiner, limit: int = 20_000) -> None:
        rows = await self.fetch(
            "SELECT id, template FROM vigil.patterns ORDER BY last_seen DESC LIMIT $1",
            limit,
        )
        miner.seed([r["template"] for r in rows])
        for r in rows:
            miner.remember(r["template"], r["id"])

    async def housekeeping(self) -> None:
        """Old rows out: logs and points when there is no TimescaleDB to drop chunks, and always
        the patterns, series and alerts nothing refers to any more."""
        logs_days = self.config.logs_retention_days
        metric_days = self.config.metrics_retention_days
        async with self.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '10min'")
            if not self.timescaledb:
                await conn.execute("DELETE FROM vigil.logs WHERE ts < now() - make_interval(days => $1)", logs_days)
                await conn.execute("DELETE FROM vigil.points WHERE ts < now() - make_interval(days => $1)", metric_days)
            await conn.execute(
                "DELETE FROM vigil.patterns WHERE last_seen < now() - make_interval(days => $1)", logs_days
            )
            await conn.execute(
                "DELETE FROM vigil.alerts WHERE ts < now() - make_interval(days => $1)", ALERTS_RETENTION_DAYS
            )
            purged = await conn.execute(
                """DELETE FROM vigil.series s WHERE s.created < now() - INTERVAL '1 day'
                   AND NOT EXISTS (SELECT 1 FROM vigil.points p WHERE p.series = s.id
                                   AND p.ts > now() - make_interval(days => $1))""",
                metric_days,
            )
        if purged != "DELETE 0" and self.on_series_purged:
            self.on_series_purged()

    # ------------------------------------------------------------------ status

    async def counts(self) -> dict:
        """The status page's totals, cached for half a minute."""
        now = time.monotonic()
        if self._status and now - self._status[0] < STATUS_TTL:
            return self._status[1]
        row = await self.fetchrow(
            """SELECT (SELECT count(*) FROM vigil.logs WHERE ts > now() - INTERVAL '24 hours') AS logs_24h,
                      (SELECT count(*) FROM vigil.series) AS series,
                      (SELECT count(*) FROM vigil.rules) AS rules,
                      (SELECT count(*) FROM vigil.rules WHERE state = 'firing') AS firing"""
        )
        counts = dict(row)
        self._status = (now, counts)
        return counts

    # ------------------------------------------------------------------ overview

    async def overview(self, hours: int) -> dict:
        end = time.time()
        start = end - hours * 3600
        step = {1: 60, 6: 300, 24: 900, 168: 3600 * 3}.get(hours, nice_step(hours * 3600, 100))
        origin = math.floor(start / step) * step
        n = math.ceil((end - origin) / step)
        row = await self.fetchrow(
            """SELECT count(*) AS logs, count(*) FILTER (WHERE severity <= 3) AS errors,
                      count(*) FILTER (WHERE severity = 4) AS warnings, count(DISTINCT host) AS hosts
               FROM vigil.logs WHERE ts >= $1""",
            epoch(start),
        )
        series = await self.fetchval("SELECT count(*) FROM vigil.series")
        volume = await self._buckets("TRUE", [], epoch(origin), epoch(end), origin, step, n)
        top = await self.fetch(
            """SELECT host, count(*) AS count, count(*) FILTER (WHERE severity <= 3) AS errors
               FROM vigil.logs WHERE ts >= $1 AND host IS NOT NULL
               GROUP BY host ORDER BY count DESC LIMIT 10""",
            epoch(start),
        )
        new_patterns = await self.fetch(
            """SELECT id, template, count, first_seen FROM vigil.patterns
               WHERE first_seen >= $1 ORDER BY count DESC LIMIT 10""",
            epoch(start),
        )
        firing = await self.firing_alerts()
        return {
            "tiles": {
                "logs": row["logs"],
                "errors": row["errors"],
                "warnings": row["warnings"],
                "hosts": row["hosts"],
                "series": series,
                "firing": len(firing),
            },
            "volume": {"t": volume["t"], "by_severity": volume["by_severity"]},
            "top_hosts": [dict(r) for r in top],
            "new_patterns": [
                {"id": r["id"], "template": r["template"], "count": r["count"], "first_seen": ts(r["first_seen"])}
                for r in new_patterns
            ],
            "firing": firing,
        }

    # ------------------------------------------------------------------ logs

    @staticmethod
    def _log_row(r: asyncpg.Record) -> dict:
        return {
            # Microseconds, so the last row's time can page to the next ones (before=) exactly
            "ts": round(r["ts"].timestamp(), 6),
            "host": r["host"],
            "app": r["app"],
            "source": r["source"],
            "severity": r["severity"],
            "facility": r["facility"],
            "message": r["message"],
            "attrs": _json_value(r["attrs"]) or {},
            "pattern": r["pattern"],
        }

    async def logs(self, q: str, start: float, end: float, limit: int, before: float | None) -> dict:
        started = time.monotonic()
        where = to_where(q, start=4)
        # Half a microsecond less: the row the page ended with (its time went through a float) is not repeated
        upper = min(end, before - 5e-7) if before is not None else end
        rows = await self.fetch(
            f"""SELECT ts, host, app, source, severity, facility, message, attrs, pattern FROM vigil.logs
                WHERE ts >= $1 AND ts < $2 AND {where.sql} ORDER BY ts DESC LIMIT $3""",
            epoch(start),
            epoch(upper),
            limit + 1,
            *where.params,
        )
        return {
            "rows": [self._log_row(r) for r in rows[:limit]],
            "more": len(rows) > limit,
            "took_ms": round((time.monotonic() - started) * 1000, 1),
        }

    async def tail(self, q: str, after: float) -> dict:
        started = time.monotonic()
        where = to_where(q, start=2)
        rows = await self.fetch(
            f"""SELECT ts, host, app, source, severity, facility, message, attrs, pattern FROM vigil.logs
                WHERE ts > $1 AND ts <= now() + INTERVAL '1 day' AND {where.sql} ORDER BY ts DESC LIMIT 201""",
            epoch(after),
            *where.params,
        )
        return {
            "rows": [self._log_row(r) for r in rows[:200]],
            "more": len(rows) > 200,
            "took_ms": round((time.monotonic() - started) * 1000, 1),
        }

    async def _buckets(
        self, where_sql: str, params: list, start: datetime, end: datetime, origin: float, step: float, n: int
    ) -> dict:
        """Log counts per bucket and severity group; params are $5.. of where_sql."""
        rows = await self.fetch(
            f"""SELECT floor((extract(epoch FROM ts)::float8 - $3) / $4)::int AS b, severity, count(*) AS n
                FROM vigil.logs WHERE ts >= $1 AND ts < $2 AND {where_sql} GROUP BY b, severity""",
            start,
            end,
            float(origin),
            float(step),
            *params,
        )
        by_severity = {g: [0] * n for g in SEVERITY_GROUPS}
        total = 0
        for r in rows:
            if 0 <= r["b"] < n:
                by_severity[severity_group(r["severity"])][r["b"]] += r["n"]
                total += r["n"]
        t = [round(origin + i * step, 3) for i in range(n)]
        return {"t": t, "by_severity": by_severity, "total": total}

    async def histogram(self, q: str, start: float, end: float, buckets: int, group: str | None = None) -> dict:
        step = (end - start) / buckets
        where = to_where(q, start=5)
        result = await self._buckets(where.sql, where.params, epoch(start), epoch(end), start, step, buckets)
        if group in LOG_GROUPS:
            column = LOG_GROUPS[group]
            where = to_where(q, start=5)
            rows = await self.fetch(
                f"""SELECT {column} AS g, floor((extract(epoch FROM ts)::float8 - $3) / $4)::int AS b, count(*) AS n
                    FROM vigil.logs WHERE ts >= $1 AND ts < $2 AND {where.sql} GROUP BY g, b""",
                epoch(start),
                epoch(end),
                float(start),
                float(step),
                *where.params,
            )
            totals: dict[str, int] = {}
            for r in rows:
                key = r["g"] or "(none)"
                totals[key] = totals.get(key, 0) + r["n"]
            keep = set(sorted(totals, key=totals.get, reverse=True)[:10])
            groups: dict[str, list[int]] = {}
            for r in rows:
                key = r["g"] or "(none)"
                key = key if key in keep else "(other)"
                if 0 <= r["b"] < buckets:
                    groups.setdefault(key, [0] * buckets)[r["b"]] += r["n"]
            result["groups"] = dict(sorted(groups.items(), key=lambda kv: -sum(kv[1])))
        return result

    async def fields(self, start: float, end: float, q: str = "") -> dict:
        result = {}
        where = to_where(q, start=3)
        for key, column in FIELD_COLUMNS.items():
            rows = await self.fetch(
                f"""SELECT {column} AS value, count(*) AS count FROM vigil.logs
                    WHERE ts >= $1 AND ts < $2 AND {column} IS NOT NULL AND {where.sql}
                    GROUP BY {column} ORDER BY count DESC LIMIT 50""",
                epoch(start),
                epoch(end),
                *where.params,
            )
            if key == "severities":
                result[key] = [
                    {"value": SEVERITIES[r["value"]], "number": r["value"], "count": r["count"]}
                    for r in rows
                    if 0 <= r["value"] <= 7
                ]
            else:
                result[key] = [{"value": r["value"], "count": r["count"]} for r in rows]
        return result

    async def count_logs(self, q: str, start: float, end: float) -> int:
        where = to_where(q, start=3)
        return await self.fetchval(
            f"SELECT count(*) FROM vigil.logs WHERE ts >= $1 AND ts < $2 AND {where.sql}",
            epoch(start),
            epoch(end),
            *where.params,
        )

    async def last_log(self, q: str, lookback: float) -> float | None:
        where = to_where(q, start=2)
        value = await self.fetchval(
            f"SELECT max(ts) FROM vigil.logs WHERE ts > $1 AND {where.sql}",
            epoch(time.time() - lookback),
            *where.params,
        )
        return ts(value)

    # ------------------------------------------------------------------ patterns

    async def patterns(self, start: float, end: float, sort: str, q: str = "", limit: int = 200) -> dict:
        span = end - start
        baseline = max(86400.0, span)
        where = to_where(q, start=4)
        rows = await self.fetch(
            f"""WITH cur AS (
                    SELECT pattern, count(*) AS n FROM vigil.logs
                    WHERE ts >= $1 AND ts < $2 AND pattern IS NOT NULL AND {where.sql} GROUP BY pattern
                    ORDER BY n DESC LIMIT 2000),
                prev AS (
                    SELECT pattern, count(*) AS n FROM vigil.logs
                    WHERE ts >= $3 AND ts < $1 AND pattern IN (SELECT pattern FROM cur) AND {where.sql}
                    GROUP BY pattern)
                SELECT p.id, p.template, cur.n AS count, coalesce(prev.n, 0) AS prev_n, p.first_seen, p.last_seen,
                       p.sample, p.host, p.app
                FROM cur JOIN vigil.patterns p ON p.id = cur.pattern LEFT JOIN prev ON prev.pattern = cur.pattern""",
            epoch(start),
            epoch(end),
            epoch(start - baseline),
            *where.params,
        )
        items = []
        for r in rows:
            prev_count = r["prev_n"] * span / baseline
            new = r["first_seen"] is not None and r["first_seen"].timestamp() >= start
            items.append(
                {
                    "id": r["id"],
                    "template": r["template"],
                    "count": r["count"],
                    "prev_count": round(prev_count, 1),
                    "first_seen": ts(r["first_seen"]),
                    "last_seen": ts(r["last_seen"]),
                    "sample": r["sample"],
                    "host": r["host"],
                    "app": r["app"],
                    "new": new,
                    "spike": not new and is_spike(r["count"], prev_count),
                }
            )
        if sort == "new":
            items.sort(key=lambda p: (p["first_seen"] or 0, p["count"]), reverse=True)
        elif sort == "spike":
            items.sort(key=lambda p: (p["count"] / max(p["prev_count"], 1.0), p["count"]), reverse=True)
        else:
            items.sort(key=lambda p: p["count"], reverse=True)
        items = items[:limit]
        sparks = await self._sparks([p["id"] for p in items], start, end)
        for p in items:
            p["spark"] = sparks.get(p["id"], [0] * 24)
        return {"patterns": items}

    async def _sparks(self, ids: list[int], start: float, end: float) -> dict[int, list[int]]:
        if not ids:
            return {}
        step = (end - start) / 24
        rows = await self.fetch(
            """SELECT pattern, floor((extract(epoch FROM ts)::float8 - $3) / $4)::int AS b, count(*) AS n
               FROM vigil.logs WHERE ts >= $1 AND ts < $2 AND pattern = ANY($5::bigint[]) GROUP BY pattern, b""",
            epoch(start),
            epoch(end),
            float(start),
            float(step),
            ids,
        )
        sparks: dict[int, list[int]] = {}
        for r in rows:
            if 0 <= r["b"] < 24:
                sparks.setdefault(r["pattern"], [0] * 24)[r["b"]] = r["n"]
        return sparks

    async def new_patterns(self, start: float, q: str = "") -> int:
        if not q:
            return await self.fetchval("SELECT count(*) FROM vigil.patterns WHERE first_seen >= $1", epoch(start))
        where = to_where(q, start=2)
        return await self.fetchval(
            f"""SELECT count(*) FROM vigil.patterns p WHERE p.first_seen >= $1 AND EXISTS (
                    SELECT 1 FROM vigil.logs l WHERE l.pattern = p.id AND l.ts >= $1 AND {where.sql})""",
            epoch(start),
            *where.params,
        )

    # ------------------------------------------------------------------ metrics

    async def metric_names(self, q: str = "") -> list[dict]:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = await self.fetch(
            """SELECT name, count(*) AS series, max(unit) AS unit, max(kind) AS kind FROM vigil.series
               WHERE name ILIKE $1 GROUP BY name ORDER BY name LIMIT 1000""",
            pattern,
        )
        return [dict(r) for r in rows]

    async def metric_labels(self, name: str) -> dict:
        rows = await self.fetch(
            """SELECT l.key, array_agg(DISTINCT l.value ORDER BY l.value) AS vals
               FROM vigil.series s, jsonb_each_text(s.labels) AS l(key, value)
               WHERE s.name = $1 GROUP BY l.key ORDER BY l.key""",
            name,
        )
        return {r["key"]: list(r["vals"])[:500] for r in rows}

    async def _metric_buckets(
        self,
        name: str,
        filters: dict,
        agg: str,
        group: str | None,
        start: float,
        end: float,
        origin: float,
        step: float,
    ) -> tuple[dict[str, dict[int, float]], str | None]:
        """{group label: {bucket index: value}} and the unit; bucket i starts at origin + i * step."""
        inner, outer = METRIC_AGGS[agg]
        rate = agg == "rate"
        # A rate needs the point before the window to have a difference for the first one
        lower = start - (step if rate else 0)
        params = [name, _dumps(filters), epoch(lower), epoch(end), float(origin), float(step)]
        group_sql = "''"
        if group:
            params.append(group)
            group_sql = "COALESCE(s.labels ->> $7, '')"
        source = "d" if rate else "p"
        deltas = (
            """, d AS (SELECT series, ts, value, value - lag(value) OVER w AS dv,
                          extract(epoch FROM ts - lag(ts) OVER w)::float8 AS dt
                   FROM p WINDOW w AS (PARTITION BY series ORDER BY ts))"""
            if rate
            else ""
        )
        rows = await self.fetch(
            f"""WITH s AS (SELECT id, labels FROM vigil.series WHERE name = $1 AND labels @> $2::jsonb LIMIT 1000),
                p AS (SELECT series, ts, value FROM vigil.points
                      WHERE series = ANY(ARRAY(SELECT id FROM s)) AND ts >= $3 AND ts < $4){deltas},
                per_series AS (
                    SELECT series, floor((extract(epoch FROM ts)::float8 - $5) / $6)::bigint AS b, {inner} AS v
                    FROM {source} GROUP BY series, b)
                SELECT {group_sql} AS g, b, {outer}(v) AS v
                FROM per_series JOIN s ON s.id = per_series.series GROUP BY g, b ORDER BY g, b""",
            *params,
        )
        result: dict[str, dict[int, float]] = {}
        for r in rows:
            value = _number(r["v"])
            if value is not None:
                result.setdefault(r["g"], {})[int(r["b"])] = value
        unit = await self.fetchval("SELECT max(unit) FROM vigil.series WHERE name = $1", name)
        return result, unit

    async def _metric_interval(self, name: str, filters: dict, end: float) -> float | None:
        """The usual seconds between two points of a metric (the median over its latest points)."""
        value = await self.fetchval(
            """WITH s AS (SELECT id FROM vigil.series WHERE name = $1 AND labels @> $2::jsonb LIMIT 20)
               SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY d) FROM (
                   SELECT extract(epoch FROM q.ts - lag(q.ts) OVER (PARTITION BY s.id ORDER BY q.ts))::float8 AS d
                   FROM s, LATERAL (SELECT ts FROM vigil.points WHERE series = s.id AND ts < $3
                                    ORDER BY ts DESC LIMIT 50) q) x
               WHERE d > 0""",
            name,
            _dumps(filters),
            epoch(end),
        )
        return _number(value)

    async def metric_query(
        self,
        name: str,
        filters: dict,
        agg: str,
        group: str | None,
        start: float,
        end: float,
        step: int | None,
        anomaly: bool,
    ) -> dict:
        span = end - start
        # Never finer than the points arrive: a 15 s step over a 60 s scrape leaves three empty
        # buckets between every two values, and a chart draws no line across a gap
        interval = await self._metric_interval(name, filters, end)
        step = max(
            int(step or 0),
            nice_step(span, 300),
            math.ceil(span / MAX_BUCKETS),
            nice_step(interval * 0.95, 1) if interval else 0,
        )
        origin = math.floor(start / step) * step
        n = max(1, math.ceil((end - origin) / step))
        history_start = origin - WEEK if anomaly else origin
        data, unit = await self._metric_buckets(name, filters, agg, group, history_start, end, origin, step)
        # The busiest groups first; a chart with hundreds of lines says nothing
        labels = sorted(data, key=lambda g: (-len(data[g]), g))[:MAX_GROUPS]
        series = []
        t = [origin + i * step for i in range(n)]
        for g in sorted(labels):
            buckets = data[g]
            values = [buckets.get(i) for i in range(n)]
            item: dict = {"label": (g or "(none)") if group else name, "values": values}
            if anomaly:
                history = [(origin + i * step, v) for i, v in buckets.items() if i < 0]
                lo, hi, anomalies = band(t, values, history)
                item["band"] = {"lo": lo, "hi": hi}
                item["anomalies"] = anomalies
            series.append(item)
        return {"t": t, "series": series, "unit": unit, "step": step}

    async def metric_value(self, name: str, filters: dict, agg: str, window: float) -> float | None:
        """One aggregate over the last `window` seconds (all matching series together)."""
        end = time.time()
        start = end - window
        data, _ = await self._metric_buckets(name, filters, agg, None, start, end, start, window)
        return data.get("", {}).get(0)

    async def metric_recent(self, name: str, filters: dict, agg: str, step: int, lookback: float) -> list:
        """(time, value) per step over the lookback, all matching series together (for anomaly rules)."""
        end = time.time()
        origin = math.floor((end - lookback) / step) * step
        data, _ = await self._metric_buckets(name, filters, agg, None, origin, end, origin, step)
        return sorted((origin + i * step, v) for i, v in data.get("", {}).items())

    async def last_point(self, name: str, filters: dict, lookback: float) -> float | None:
        value = await self.fetchval(
            """SELECT max(p.ts) FROM vigil.points p JOIN vigil.series s ON s.id = p.series
               WHERE s.name = $1 AND s.labels @> $2::jsonb AND p.ts > $3""",
            name,
            _dumps(filters),
            epoch(time.time() - lookback),
        )
        return ts(value)

    # ------------------------------------------------------------------ what changed

    async def change_data(self, start: float, end: float) -> dict:
        """Counts and means of the window and of the window of the same length before it."""
        span = end - start
        prev = start - span
        args = (epoch(prev), epoch(start), epoch(end))
        hosts = await self.fetch(
            """SELECT host, count(*) FILTER (WHERE ts >= $2) AS cur, count(*) FILTER (WHERE ts < $2) AS prev
               FROM vigil.logs WHERE ts >= $1 AND ts < $3 AND host IS NOT NULL GROUP BY host""",
            *args,
        )
        severities = await self.fetch(
            """SELECT severity, count(*) FILTER (WHERE ts >= $2) AS cur, count(*) FILTER (WHERE ts < $2) AS prev
               FROM vigil.logs WHERE ts >= $1 AND ts < $3 GROUP BY severity""",
            *args,
        )
        patterns = await self.fetch(
            """WITH c AS (SELECT pattern, count(*) FILTER (WHERE ts >= $2) AS cur, count(*) FILTER (WHERE ts < $2) AS prev
                          FROM vigil.logs WHERE ts >= $1 AND ts < $3 AND pattern IS NOT NULL GROUP BY pattern)
               SELECT c.pattern AS id, c.cur, c.prev, p.template, p.first_seen FROM c
               JOIN vigil.patterns p ON p.id = c.pattern
               ORDER BY abs(c.cur - c.prev) DESC LIMIT 200""",
            *args,
        )
        metrics = await self.fetch(
            """SELECT s.id, s.name, s.labels, avg(p.value) FILTER (WHERE p.ts >= $2) AS cur,
                      avg(p.value) FILTER (WHERE p.ts < $2) AS prev_mean,
                      stddev_samp(p.value) FILTER (WHERE p.ts < $2) AS prev_std,
                      count(*) FILTER (WHERE p.ts < $2) AS prev_n
               FROM vigil.points p JOIN vigil.series s ON s.id = p.series
               WHERE p.ts >= $1 AND p.ts < $3 AND coalesce(s.kind, '') <> 'counter'
               GROUP BY s.id, s.name, s.labels LIMIT 5000""",
            *args,
        )
        return {
            "hosts": [dict(r) for r in hosts],
            "severities": [dict(r) for r in severities],
            "patterns": [{**dict(r), "first_seen": ts(r["first_seen"])} for r in patterns],
            "metrics": [
                {
                    "name": r["name"],
                    "labels": _json_value(r["labels"]) or {},
                    "cur": _number(r["cur"]),
                    "prev_mean": _number(r["prev_mean"]),
                    "prev_std": _number(r["prev_std"]),
                    "prev_n": r["prev_n"],
                }
                for r in metrics
            ],
        }

    # ------------------------------------------------------------------ sources

    async def sources(self) -> list[dict]:
        rows = await self.fetch(
            """SELECT kind, name, host, first_seen, last_seen, received, errors, last_error
               FROM vigil.sources ORDER BY last_seen DESC NULLS LAST, kind, name LIMIT 1000"""
        )
        return [{**dict(r), "first_seen": ts(r["first_seen"]), "last_seen": ts(r["last_seen"])} for r in rows]

    # ------------------------------------------------------------------ rules and alerts

    @staticmethod
    def _rule(r: asyncpg.Record) -> dict:
        return {
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "query": _json_value(r["query"]) or {},
            "condition": _json_value(r["condition"]) or {},
            "for_seconds": r["for_seconds"],
            "severity": r["severity"],
            "enabled": r["enabled"],
            "notify": r["notify"],
            "state": r["state"],
            "state_since": ts(r["state_since"]),
            "last_value": _number(r["last_value"]),
            "last_eval": ts(r["last_eval"]),
            "created": ts(r["created"]),
            "updated": ts(r["updated"]),
        }

    async def rules(self, enabled_only: bool = False) -> list[dict]:
        rows = await self.fetch(
            "SELECT * FROM vigil.rules WHERE enabled OR NOT $1 ORDER BY name, id",
            enabled_only,
        )
        return [self._rule(r) for r in rows]

    async def rule(self, rule_id: int) -> dict | None:
        row = await self.fetchrow("SELECT * FROM vigil.rules WHERE id = $1", rule_id)
        return self._rule(row) if row else None

    async def save_rule(self, rule: dict) -> int:
        """Inserts or updates a rule; a changed rule starts again from ok."""
        args = (
            rule["name"],
            rule["kind"],
            _dumps(rule["query"]),
            _dumps(rule["condition"]),
            rule["for_seconds"],
            rule["severity"],
            rule["enabled"],
            rule["notify"],
        )
        if rule.get("id"):
            rule_id = await self.fetchval(
                """UPDATE vigil.rules SET name = $2, kind = $3, query = $4::jsonb, condition = $5::jsonb,
                       for_seconds = $6, severity = $7, enabled = $8, notify = $9, updated = now(),
                       state = CASE WHEN kind = $3 AND query = $4::jsonb AND condition = $5::jsonb AND $8
                                    THEN state ELSE 'ok' END,
                       state_since = CASE WHEN kind = $3 AND query = $4::jsonb AND condition = $5::jsonb AND $8
                                          THEN state_since ELSE now() END
                   WHERE id = $1 RETURNING id""",
                int(rule["id"]),
                *args,
            )
            if rule_id is None:
                raise KeyError(rule["id"])
            return rule_id
        return await self.fetchval(
            """INSERT INTO vigil.rules (name, kind, query, condition, for_seconds, severity, enabled, notify,
                                        state, state_since)
               VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7, $8, 'ok', now()) RETURNING id""",
            *args,
        )

    async def delete_rule(self, rule_id: int) -> bool:
        return await self.execute("DELETE FROM vigil.rules WHERE id = $1", rule_id) != "DELETE 0"

    async def set_rule_state(
        self, rule_id: int, state: str, since: float | None, value: float | None, alert: tuple | None
    ) -> None:
        """The state after an evaluation, and the alert row (state, value, message) when it changed."""
        async with self.acquire() as conn, conn.transaction():
            await conn.execute(
                """UPDATE vigil.rules SET state = $2, state_since = $3, last_value = $4, last_eval = now()
                   WHERE id = $1""",
                rule_id,
                state,
                epoch(since) if since else None,
                value,
            )
            if alert:
                await conn.execute(
                    "INSERT INTO vigil.alerts (rule, ts, state, value, message) VALUES ($1, now(), $2, $3, $4)",
                    rule_id,
                    *alert,
                )
        self._status = None

    @staticmethod
    def _alert(r: asyncpg.Record) -> dict:
        return {
            "id": r["id"],
            "rule": r["rule"],
            "rule_name": r["rule_name"],
            "ts": ts(r["ts"]),
            "state": r["state"],
            "value": _number(r["value"]),
            "message": r["message"],
            "severity": r["severity"],
        }

    async def alerts(self, start: float, end: float) -> list[dict]:
        rows = await self.fetch(
            """SELECT a.id, a.rule, r.name AS rule_name, a.ts, a.state, a.value, a.message, r.severity
               FROM vigil.alerts a LEFT JOIN vigil.rules r ON r.id = a.rule
               WHERE a.ts >= $1 AND a.ts < $2 ORDER BY a.ts DESC LIMIT 1000""",
            epoch(start),
            epoch(end),
        )
        return [self._alert(r) for r in rows]

    async def firing_alerts(self) -> list[dict]:
        """The alert row that started each rule's current firing."""
        rows = await self.fetch(
            """SELECT DISTINCT ON (r.id) a.id, r.id AS rule, r.name AS rule_name, a.ts, a.state, a.value,
                      a.message, r.severity
               FROM vigil.rules r JOIN vigil.alerts a ON a.rule = r.id AND a.state = 'firing'
               WHERE r.state = 'firing' ORDER BY r.id, a.ts DESC"""
        )
        return [self._alert(r) for r in rows]

    # ------------------------------------------------------------------ dashboards and searches

    async def dashboards(self) -> list[dict]:
        rows = await self.fetch("SELECT id, name, panels, created, updated FROM vigil.dashboards ORDER BY id")
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "panels": _json_value(r["panels"]) or [],
                "builtin": r["name"] in BUILTIN_DASHBOARDS,
                "created": ts(r["created"]),
                "updated": ts(r["updated"]),
            }
            for r in rows
        ]

    async def save_dashboard(self, dashboard_id: int | None, name: str, panels: list) -> int:
        if dashboard_id:
            result = await self.fetchval(
                "UPDATE vigil.dashboards SET name = $2, panels = $3::jsonb, updated = now() WHERE id = $1 RETURNING id",
                dashboard_id,
                name,
                _dumps(panels),
            )
            if result is None:
                raise KeyError(dashboard_id)
            return result
        return await self.fetchval(
            "INSERT INTO vigil.dashboards (name, panels) VALUES ($1, $2::jsonb) RETURNING id", name, _dumps(panels)
        )

    async def delete_dashboard(self, dashboard_id: int) -> bool:
        return await self.execute("DELETE FROM vigil.dashboards WHERE id = $1", dashboard_id) != "DELETE 0"

    async def searches(self) -> list[dict]:
        rows = await self.fetch("SELECT id, name, query, created FROM vigil.saved_searches ORDER BY name")
        return [{**dict(r), "created": ts(r["created"])} for r in rows]

    async def save_search(self, search_id: int | None, name: str, query: str) -> int:
        if search_id:
            result = await self.fetchval(
                "UPDATE vigil.saved_searches SET name = $2, query = $3 WHERE id = $1 RETURNING id",
                search_id,
                name,
                query,
            )
            if result is None:
                raise KeyError(search_id)
            return result
        return await self.fetchval(
            """INSERT INTO vigil.saved_searches (name, query) VALUES ($1, $2)
               ON CONFLICT (name) DO UPDATE SET query = EXCLUDED.query RETURNING id""",
            name,
            query,
        )

    async def delete_search(self, search_id: int) -> bool:
        return await self.execute("DELETE FROM vigil.saved_searches WHERE id = $1", search_id) != "DELETE 0"


# ---------------------------------------------------------------------- what was received from whom


@dataclass
class _SourceState:
    host: str | None = None
    received: int = 0
    errors: int = 0
    last_error: str | None = None
    last_seen: datetime | None = None
    dirty: bool = False
    minutes: deque = field(default_factory=lambda: deque(maxlen=6))  # [minute, count]


class Sources:
    """Who sent what: counted in memory as it arrives, written to vigil.sources every few seconds."""

    def __init__(self):
        self._state: dict[tuple[str, str], _SourceState] = {}

    def seen(self, kind: str, name: str, host: str | None = None, count: int = 1, error: str | None = None) -> None:
        key = (kind, (name or "?")[:255])
        state = self._state.get(key)
        if state is None:
            if len(self._state) >= MAX_SOURCES:
                return
            state = self._state[key] = _SourceState()
        if host:
            state.host = host[:255]
        if count:
            state.received += count
            state.last_seen = datetime.now(UTC)
            minute = int(time.time() // 60)
            if state.minutes and state.minutes[-1][0] == minute:
                state.minutes[-1][1] += count
            else:
                state.minutes.append([minute, count])
        if error:
            state.errors += 1
            state.last_error = error[:500]
        state.dirty = True

    def rate_per_min(self, kind: str, name: str) -> float:
        """Messages or samples per minute over the last 5 full minutes."""
        state = self._state.get((kind, name))
        if state is None:
            return 0.0
        now = int(time.time() // 60)
        total = sum(c for m, c in state.minutes if now - 5 <= m < now)
        return round(total / 5, 1)

    def pending(self) -> list[tuple]:
        rows = []
        for (kind, name), s in self._state.items():
            if s.dirty:
                rows.append((kind, name, s.host, s.last_seen, s.received, s.errors, s.last_error))
        return rows

    def written(self, rows: list[tuple]) -> None:
        for kind, name, _, _, received, errors, _ in rows:
            state = self._state.get((kind, name))
            if state is not None:
                state.received -= received
                state.errors -= errors
                state.dirty = state.received != 0 or state.errors != 0
                if not state.dirty:
                    state.last_error = None


# ---------------------------------------------------------------------- the writer


@dataclass
class _Prepared:
    logs: list[LogEntry]
    templates: list[str]
    renames: list[tuple[str, str]]
    samples: list[Sample]


class Writer:
    """The one way into the database for logs and points: a bounded queue, written in batches
    with COPY. When the database is away the queue keeps what arrives until it is full; after
    that new records are dropped and counted (dropped)."""

    def __init__(self, database: Database, miner: PatternMiner, sources: Sources, queue_size: int = QUEUE_SIZE):
        self.database = database
        self.miner = miner
        self.sources = sources
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.dropped = 0
        self.written_logs = 0
        self.written_points = 0
        self.last_write_seconds = 0.0
        self.last_write: float | None = None
        self.last_error: str | None = None
        self._series: dict[tuple[str, str], int] = {}
        self._stopping = False
        self._sources_at = 0.0
        database.on_series_purged = self._series.clear

    # ------------------------------------------------------------------ putting

    def put_log(self, entry: LogEntry) -> bool:
        try:
            self.queue.put_nowait(entry)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    def put_samples(self, samples: list[Sample]) -> int:
        added = 0
        for sample in samples:
            try:
                self.queue.put_nowait(sample)
                added += 1
            except asyncio.QueueFull:
                self.dropped += len(samples) - added
                break
        return added

    # ------------------------------------------------------------------ writing

    async def run(self) -> None:
        """Until stop(): batches out of the queue into the database. The queue fills while the
        database is away; the first connection loads the known patterns before anything is matched."""
        while not self._stopping and not self.database.ready.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.database.ready.wait(), 1)
        if self.database.ready.is_set():
            try:
                await self.database.seed_patterns(self.miner)
                logger.debug(f"{len(self.miner)} known log patterns loaded")
            except Exception as e:
                logger.warning(f"Cannot load the known log patterns: {describe(e)}")
        while not self._stopping:
            batch = await self._collect()
            if batch:
                await self._write(self._prepare(batch))
            if time.monotonic() - self._sources_at >= SOURCES_INTERVAL:
                await self._write_sources()

    async def _collect(self) -> list:
        batch = []
        deadline = time.monotonic() + FLUSH_INTERVAL
        while len(batch) < BATCH_SIZE:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(self.queue.get(), timeout))
            except TimeoutError:
                break
            while len(batch) < BATCH_SIZE and not self.queue.empty():
                batch.append(self.queue.get_nowait())
        return batch

    def _prepare(self, batch: list) -> _Prepared:
        """Patterns are matched once per batch, so a retry writes the same templates."""
        now = datetime.now(UTC)
        oldest = now - timedelta(days=self.database.config.logs_retention_days)
        oldest_point = now - timedelta(days=self.database.config.metrics_retention_days)
        # A sender whose clock runs ahead would put its lines past every "now" window (and
        # past the live tail): seconds ahead is the network, more is the clock
        newest = now + timedelta(seconds=2)
        logs, samples, templates, renames = [], [], [], []
        for item in batch:
            if isinstance(item, LogEntry):
                item.message, item.host, item.app = _clean(item.message) or "", _clean(item.host), _clean(item.app)
                if not oldest <= item.ts <= newest:
                    # A sender with a wrong clock: keep the line, at the time it arrived
                    item.attrs["original_ts"] = item.ts.isoformat()
                    item.ts = now
                match = self.miner.match(item.message)
                if match.renamed_from:
                    renames.append((match.renamed_from, match.template))
                logs.append(item)
                templates.append(match.template)
            elif isinstance(item, Sample):
                if item.ts is None or not oldest_point <= item.ts <= newest:
                    item.ts = now
                samples.append(item)
        if renames:
            # A template widened by a later line of the same batch: its earlier lines get the wide one
            final = dict(renames)
            for i, template in enumerate(templates):
                seen = set()
                while template in final and template not in seen:
                    seen.add(template)
                    template = final[template]
                templates[i] = template
        return _Prepared(logs, templates, renames, samples)

    async def _write(self, batch: _Prepared) -> None:
        attempt = 0
        while True:
            if not self.database.ready.is_set():
                if self._stopping:
                    self.dropped += len(batch.logs) + len(batch.samples)
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.database.ready.wait(), 2)
                continue
            started = time.monotonic()
            try:
                await self._write_once(batch)
                self.last_write_seconds = round(time.monotonic() - started, 4)
                self.last_write = time.time()
                self.last_error = None
                if attempt:
                    logger.info(f"Writing to the database again ({self.queue.qsize()} records queued)")
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = describe(e)
                connection = is_connection_error(e)
                if _is_refused(e) or not (connection or isinstance(e, asyncpg.PostgresError)):
                    # Not something waiting cures (a value the database refuses): write the
                    # records one by one, so only the bad ones are lost
                    await self._write_singly(batch, e)
                    return
                if self._stopping:
                    self.dropped += len(batch.logs) + len(batch.samples)
                    return
                if connection:
                    if attempt == 0:
                        logger.warning(f"Cannot write to the database ({self.last_error}); holding the queue")
                    attempt += 1
                    # Waits for the connection checks to find the database back (top of the loop)
                    self.database.lost(e)
                    await asyncio.sleep(1)
                    continue
                if isinstance(e, asyncpg.UndefinedTableError | asyncpg.UndefinedColumnError):
                    # Vigil's tables went away (a restored or emptied database): make them again
                    logger.warning(f"Vigil's tables are missing ({e}); setting them up again")
                    await self.database.reset()
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                attempt += 1
                if attempt == 1:
                    logger.warning(f"Cannot write to the database ({self.last_error}); holding the queue")
                await asyncio.sleep(delay)

    async def _write_singly(self, batch: _Prepared, error: BaseException) -> None:
        parts = [_Prepared([e], [t], [], []) for e, t in zip(batch.logs, batch.templates, strict=True)]
        parts += [_Prepared([], [], [], [s]) for s in batch.samples]
        if len(parts) == 1:
            self.dropped += 1
            logger.warning(f"Dropped a record the database refused: {describe(error)}")
            return
        refused = 0
        for part in parts:
            try:
                await self._write_once(part)
            except Exception as e:
                if is_connection_error(e):
                    refused += len(part.logs) + len(part.samples)
                    continue
                refused += 1
                error = e
        if refused:
            self.dropped += refused
            logger.warning(f"Dropped {refused} records the database refused: {describe(error)}")

    async def _write_once(self, batch: _Prepared) -> None:
        async with self.database.acquire() as conn, conn.transaction():
            if batch.logs:
                ids = await self._pattern_ids(conn, batch)
                now = datetime.now(UTC)
                records = [
                    (
                        e.ts,
                        now,
                        e.source,
                        _clean(e.host),
                        _clean(e.app),
                        e.severity,
                        e.facility,
                        _clean(e.message) or "",
                        _dumps(e.attrs),
                        ids.get(template),
                    )
                    for e, template in zip(batch.logs, batch.templates, strict=True)
                ]
                await conn.copy_records_to_table(
                    "logs",
                    schema_name=SCHEMA,
                    columns=LOG_COLUMNS,
                    records=records,
                )
            if batch.samples:
                series = await self._series_ids(conn, batch.samples)
                now = datetime.now(UTC)
                points = [(s.ts or now, series[i], s.value) for i, s in enumerate(batch.samples)]
                await conn.copy_records_to_table(
                    "points", schema_name=SCHEMA, columns=["ts", "series", "value"], records=points
                )
        self.written_logs += len(batch.logs)
        self.written_points += len(batch.samples)

    async def _pattern_ids(self, conn: asyncpg.Connection, batch: _Prepared) -> dict[str, int]:
        for old, new in batch.renames:
            # The widened template keeps the narrower one's row (and its history)
            await conn.execute(
                """UPDATE vigil.patterns SET template = $2 WHERE template = $1
                   AND NOT EXISTS (SELECT 1 FROM vigil.patterns WHERE template = $2)""",
                old,
                new,
            )
        counts: dict[str, list] = {}
        for entry, template in zip(batch.logs, batch.templates, strict=True):
            item = counts.get(template)
            if item is None:
                counts[template] = [1, _clean(entry.message)[:1000], entry.host, entry.app]
            else:
                item[0] += 1
        now = datetime.now(UTC)
        templates = list(counts)
        rows = await conn.fetch(
            """INSERT INTO vigil.patterns (template, first_seen, last_seen, count, sample, host, app)
               SELECT t, $2, $2, c, s, h, a FROM unnest($1::text[], $3::bigint[], $4::text[], $5::text[], $6::text[])
                   AS u(t, c, s, h, a)
               ON CONFLICT (template) DO UPDATE SET last_seen = EXCLUDED.last_seen,
                   count = vigil.patterns.count + EXCLUDED.count
               RETURNING id, template""",
            templates,
            now,
            [counts[t][0] for t in templates],
            [counts[t][1] for t in templates],
            [_clean(counts[t][2]) for t in templates],
            [_clean(counts[t][3]) for t in templates],
        )
        ids = {r["template"]: r["id"] for r in rows}
        for template, pattern_id in ids.items():
            self.miner.remember(template, pattern_id)
        return ids

    async def _series_ids(self, conn: asyncpg.Connection, samples: list[Sample]) -> list[int]:
        keys = [(s.name, _dumps(dict(sorted(s.labels.items())))) for s in samples]
        missing: dict[tuple[str, str], Sample] = {}
        for key, sample in zip(keys, samples, strict=True):
            if key not in self._series and key not in missing:
                missing[key] = sample
        if missing:
            if len(self._series) + len(missing) > SERIES_CACHE:
                self._series.clear()
            names = [k[0] for k in missing]
            labels = [k[1] for k in missing]
            await conn.execute(
                """INSERT INTO vigil.series (name, labels, unit, kind)
                   SELECT * FROM unnest($1::text[], $2::jsonb[], $3::text[], $4::text[])
                   ON CONFLICT (name, labels) DO NOTHING""",
                names,
                labels,
                [s.unit for s in missing.values()],
                [s.kind for s in missing.values()],
            )
            rows = await conn.fetch(
                """SELECT s.id, u.i FROM unnest($1::text[], $2::jsonb[]) WITH ORDINALITY AS u(name, labels, i)
                   JOIN vigil.series s ON s.name = u.name AND s.labels = u.labels""",
                names,
                labels,
            )
            ordered = list(missing)
            for r in rows:
                self._series[ordered[r["i"] - 1]] = r["id"]
        return [self._series[key] for key in keys]

    async def _write_sources(self) -> None:
        self._sources_at = time.monotonic()
        rows = self.sources.pending()
        if not rows or not self.database.connected:
            return
        try:
            await self.database.execute(
                """INSERT INTO vigil.sources AS s (kind, name, host, first_seen, last_seen, received, errors, last_error)
                   SELECT k, n, h, coalesce(l, now()), l, r, e, m
                   FROM unnest($1::text[], $2::text[], $3::text[], $4::timestamptz[], $5::bigint[], $6::bigint[],
                               $7::text[]) AS u(k, n, h, l, r, e, m)
                   ON CONFLICT (kind, name) DO UPDATE SET host = coalesce(EXCLUDED.host, s.host),
                       last_seen = greatest(s.last_seen, EXCLUDED.last_seen),
                       received = s.received + EXCLUDED.received, errors = s.errors + EXCLUDED.errors,
                       last_error = coalesce(EXCLUDED.last_error, s.last_error)""",
                *[list(column) for column in zip(*rows, strict=True)],
            )
            self.sources.written(rows)
        except Exception as e:
            logger.debug(f"Cannot write the sources: {describe(e)}")

    def self_samples(self) -> list[Sample]:
        now = datetime.now(UTC)
        labels = {"job": "vigil"}
        return [
            Sample("vigil.queue_size", labels, float(self.queue.qsize()), now, None, "gauge"),
            Sample("vigil.dropped_total", labels, float(self.dropped), now, None, "counter"),
            Sample("vigil.write_seconds", labels, float(self.last_write_seconds), now, "s", "gauge"),
        ]

    async def stop(self, task: asyncio.Task | None, timeout: float = 10.0) -> None:
        """Lets the running batch finish, writes what is still queued (for at most `timeout`
        seconds) and stops. The run task is not cancelled mid-batch: that would lose the batch."""
        self._stopping = True
        deadline = time.monotonic() + timeout
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except (TimeoutError, Exception):
                task.cancel()
        while not self.queue.empty() and self.database.connected and time.monotonic() < deadline:
            batch = []
            while len(batch) < BATCH_SIZE and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            try:
                await asyncio.wait_for(self._write(self._prepare(batch)), max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
        left = self.queue.qsize()
        if left:
            logger.warning(f"{left} records were not written before stopping")
        await self._write_sources()
