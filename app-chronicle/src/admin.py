"""The database pages: browse every database, its dashboard, the server's activity, and
maintenance. As the web UI's role (chronicle_ui): it reads the statistics of the whole server
(pg_monitor), can stop queries (pg_signal_backend), run VACUUM, ANALYZE and REINDEX on every
table (pg_maintain), manage what the Chronicle integration owns, and create read-only logins.

Names from the page (databases, schemas, tables, columns) never go into SQL as they come:
a database must exist, a table is looked up in the catalog and used by its oid, a column must
be one of the table's columns, and everything else is a query parameter.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import itertools
import logging
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

import asyncpg

from .config import INTEGRATION_ROLE, Config
from .database import _cell

logger = logging.getLogger(__name__)

# Schemas of PostgreSQL and TimescaleDB itself, hidden in the tree unless asked for
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
SYSTEM_PREFIXES = ("_timescaledb_", "timescaledb_", "pg_temp_", "pg_toast_temp_", "toolkit_")
# Roles the app manages itself; never shown as logins to remove
APP_ROLES = {"postgres", INTEGRATION_ROLE, "chronicle_ui"}
READER_NAME = re.compile(r"^[a-z][a-z0-9_]{2,30}$")
ROWS_LIMIT = 500
STATEMENT_TIMEOUT = "30s"
SAMPLE_SECONDS = 60
SAMPLES_KEPT = 24 * 60  # a day of samples, in memory only
# The web UI's session settings, sent when it connects: they win over what a database's owner
# set with ALTER DATABASE ... SET (a search_path with its own functions first, read-only, ..)
UI_SESSION = {
    "search_path": '"$user", public',
    "default_transaction_read_only": "off",
    "lock_timeout": "0",
    "idle_in_transaction_session_timeout": "0",
}
RELKINDS = {"r": "table", "p": "table", "v": "view", "m": "materialized view", "f": "foreign table"}


class AdminError(Exception):
    """A request the page can't do; the message is for the page."""


def is_system_schema(name: str) -> bool:
    return name in SYSTEM_SCHEMAS or name.startswith(SYSTEM_PREFIXES)


@dataclass
class Job:
    """A maintenance task the web UI runs itself (VACUUM, refreshing the averages, ..)."""

    kind: str
    title: str
    id: str = field(default_factory=lambda: secrets.token_hex(6))
    state: str = "running"
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str | None = None

    def public(self) -> dict:
        return {
            k: getattr(self, k)
            for k in ("id", "kind", "title", "state", "started", "finished", "log", "result", "error")
        }


class Admin:
    def __init__(self, config: Config):
        self.config = config
        self._pools: dict[str, asyncpg.Pool] = {}
        self._lock = asyncio.Lock()
        self.samples: deque = deque(maxlen=SAMPLES_KEPT)
        self.jobs: list[Job] = []
        self._tasks: set[asyncio.Task] = set()
        self._sampler: asyncio.Task | None = None

    # ------------------------------------------------------------------ connections

    async def pool(self, database: str) -> asyncpg.Pool:
        """A small pool per database; the database must exist (the name is checked first)."""
        if database in self._pools:
            return self._pools[database]
        # Checked before taking the lock: the check itself uses the postgres pool
        if database != "postgres" and database not in await self._database_names():
            raise AdminError(f"There is no database {database}")
        async with self._lock:
            if database not in self._pools:
                self._pools[database] = await asyncpg.create_pool(
                    host=self.config.host,
                    port=self.config.port,
                    user=self.config.username,
                    password=self.config.password or None,
                    database=database,
                    min_size=0,
                    max_size=3,
                    timeout=10,
                    command_timeout=120,
                    max_inactive_connection_lifetime=120,
                    server_settings={
                        **UI_SESSION,
                        "application_name": "chronicle-app",
                        "statement_timeout": STATEMENT_TIMEOUT,
                    },
                )
        return self._pools[database]

    async def _database_names(self) -> set[str]:
        pool = await self.pool("postgres")
        rows = await pool.fetch("SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate")
        return {r["datname"] for r in rows}

    async def forget(self, database: str) -> None:
        """Closes the pool of a database that was removed or renamed."""
        pool = self._pools.pop(database, None)
        if pool is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(pool.close(), 5)

    async def close(self) -> None:
        if self._sampler:
            self._sampler.cancel()
        for name in list(self._pools):
            await self.forget(name)

    # ------------------------------------------------------------------ databases

    async def databases(self) -> list[dict]:
        pool = await self.pool("postgres")
        rows = await pool.fetch(
            """SELECT d.datname AS name, pg_get_userbyid(d.datdba) AS owner, pg_database_size(d.oid) AS size,
                      pg_encoding_to_char(d.encoding) AS encoding, d.datallowconn AS allow,
                      (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = d.datname) AS connections,
                      shobj_description(d.oid, 'pg_database') AS comment
               FROM pg_database d WHERE NOT d.datistemplate ORDER BY d.datname = $1 DESC, d.datname""",
            self.config.database,
        )
        return [{**dict(r), "chronicle": r["name"] == self.config.database} for r in rows]

    async def tree(self, database: str, system: bool) -> dict:
        """Schemas with their tables, views, hypertables, continuous aggregates and functions."""
        pool = await self.pool(database)
        async with pool.acquire() as conn:
            timescale = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            schemas = await conn.fetch(
                "SELECT nspname AS name, pg_get_userbyid(nspowner) AS owner FROM pg_namespace ORDER BY nspname"
            )
            objects = await conn.fetch(
                """SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS kind,
                          greatest(c.reltuples, 0)::bigint AS rows, pg_total_relation_size(c.oid) AS size
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition
                   ORDER BY n.nspname, c.relname"""
            )
            functions = await conn.fetch(
                """SELECT n.nspname AS schema, p.proname AS name, p.prokind::text AS kind,
                          pg_get_function_identity_arguments(p.oid) AS arguments
                   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   LEFT JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
                   WHERE d.objid IS NULL AND p.prokind IN ('f', 'p')
                     AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                   ORDER BY n.nspname, p.proname"""
            )
            hypertables: dict[tuple, dict] = {}
            caggs: set[tuple] = set()
            if timescale:
                for r in await conn.fetch(
                    """SELECT h.hypertable_schema AS schema, h.hypertable_name AS name, h.num_chunks AS chunks,
                              h.compression_enabled AS compression,
                              hypertable_size(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass) AS size
                       FROM timescaledb_information.hypertables h"""
                ):
                    hypertables[(r["schema"], r["name"])] = dict(r)
                for r in await conn.fetch(
                    "SELECT view_schema AS schema, view_name AS name FROM timescaledb_information.continuous_aggregates"
                ):
                    caggs.add((r["schema"], r["name"]))

        result = []
        for s in schemas:
            name = s["name"]
            hidden = is_system_schema(name)
            if hidden and not system:
                continue
            items = []
            for o in objects:
                if o["schema"] != name:
                    continue
                key = (name, o["name"])
                kind = RELKINDS.get(o["kind"], o["kind"])
                item = {"name": o["name"], "kind": kind, "rows": o["rows"], "size": o["size"]}
                if key in hypertables:
                    h = hypertables[key]
                    item.update(kind="hypertable", chunks=h["chunks"], compression=h["compression"], size=h["size"])
                elif key in caggs:
                    item["kind"] = "continuous aggregate"
                items.append(item)
            funcs = [
                {
                    "name": f["name"],
                    "kind": "procedure" if f["kind"] == "p" else "function",
                    "arguments": f["arguments"],
                }
                for f in functions
                if f["schema"] == name
            ]
            result.append({"name": name, "owner": s["owner"], "system": hidden, "objects": items, "functions": funcs})
        return {"database": database, "timescaledb": timescale, "schemas": result}

    async def _relation(self, conn: asyncpg.Connection, schema: str, name: str) -> asyncpg.Record:
        row = await conn.fetchrow(
            """SELECT c.oid, c.relkind::text AS relkind, c.oid::regclass::text AS quoted, pg_get_userbyid(c.relowner) AS owner
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind IN ('r', 'p', 'v', 'm', 'f')""",
            schema,
            name,
        )
        if row is None:
            raise AdminError(f"There is no table or view {schema}.{name}")
        return row

    async def relation(self, database: str, schema: str, name: str) -> dict:
        """Everything about one table or view: columns, indexes, constraints, sizes, statistics,
        and for TimescaleDB its dimensions, chunks and compression."""
        pool = await self.pool(database)
        async with pool.acquire() as conn:
            rel = await self._relation(conn, schema, name)
            oid = rel["oid"]
            columns = await conn.fetch(
                """SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS not_null,
                          pg_get_expr(d.adbin, d.adrelid) AS default, col_description(a.attrelid, a.attnum) AS comment,
                          nullif(a.attidentity::text, '') AS identity, nullif(a.attgenerated::text, '') AS generated
                   FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                   WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""",
                oid,
            )
            indexes = await conn.fetch(
                """SELECT i.indexrelid::regclass::text AS name, pg_get_indexdef(i.indexrelid) AS definition,
                          i.indisprimary AS primary, i.indisunique AS unique, pg_relation_size(i.indexrelid) AS size,
                          s.idx_scan AS scans
                   FROM pg_index i LEFT JOIN pg_stat_all_indexes s ON s.indexrelid = i.indexrelid
                   WHERE i.indrelid = $1 ORDER BY i.indisprimary DESC, 1""",
                oid,
            )
            constraints = await conn.fetch(
                """SELECT conname AS name, contype::text AS type, pg_get_constraintdef(oid) AS definition
                   FROM pg_constraint WHERE conrelid = $1 ORDER BY contype, conname""",
                oid,
            )
            stats = await conn.fetchrow(
                """SELECT seq_scan, idx_scan, n_live_tup AS live, n_dead_tup AS dead, n_tup_ins AS inserted,
                          n_tup_upd AS updated, n_tup_del AS deleted, last_vacuum, last_autovacuum, last_analyze,
                          last_autoanalyze, vacuum_count, autovacuum_count, analyze_count, autoanalyze_count
                   FROM pg_stat_all_tables WHERE relid = $1""",
                oid,
            )
            sizes = await conn.fetchrow(
                """SELECT pg_table_size($1::oid) AS table, pg_indexes_size($1::oid) AS indexes, pg_total_relation_size($1::oid) AS total,
                          (SELECT greatest(reltuples, 0)::bigint FROM pg_class WHERE oid = $1) AS rows""",
                oid,
            )
            definition = None
            if rel["relkind"] in ("v", "m"):
                definition = await conn.fetchval("SELECT pg_get_viewdef($1::oid, true)", oid)
            comment = await conn.fetchval("SELECT obj_description($1::oid, 'pg_class')", oid)
            timescale = None
            if await conn.fetchval("SELECT EXISTS (SELECT FROM pg_extension WHERE extname = 'timescaledb')"):
                timescale = await self._timescale(conn, schema, name, rel["quoted"])
        result = {
            "database": database,
            "schema": schema,
            "name": name,
            "kind": RELKINDS.get(rel["relkind"], rel["relkind"]),
            "owner": rel["owner"],
            "comment": comment,
            "columns": [dict(c) for c in columns],
            "indexes": [dict(i) for i in indexes],
            "constraints": [dict(c) for c in constraints],
            "stats": dict(stats) if stats else None,
            "sizes": dict(sizes),
            "definition": definition,
            "timescale": timescale,
        }
        if timescale and timescale.get("hypertable"):
            result["kind"] = "hypertable"
            result["sizes"]["total"] = timescale["size"]
        elif timescale and timescale.get("continuous_aggregate"):
            result["kind"] = "continuous aggregate"
        return result

    async def _timescale(self, conn: asyncpg.Connection, schema: str, name: str, quoted: str) -> dict | None:
        hypertable = await conn.fetchrow(
            """SELECT num_chunks AS chunks, compression_enabled AS compression
               FROM timescaledb_information.hypertables WHERE hypertable_schema = $1 AND hypertable_name = $2""",
            schema,
            name,
        )
        cagg = await conn.fetchrow(
            """SELECT materialized_only, materialization_hypertable_schema AS mschema,
                      materialization_hypertable_name AS mname, view_definition
               FROM timescaledb_information.continuous_aggregates WHERE view_schema = $1 AND view_name = $2""",
            schema,
            name,
        )
        if hypertable is None and cagg is None:
            return None
        target = (schema, name) if hypertable else (cagg["mschema"], cagg["mname"])
        result: dict = {"hypertable": hypertable is not None, "continuous_aggregate": cagg is not None}
        result["dimensions"] = [
            dict(r)
            for r in await conn.fetch(
                """SELECT column_name, dimension_type, time_interval, integer_interval, num_partitions
                   FROM timescaledb_information.dimensions WHERE hypertable_schema = $1 AND hypertable_name = $2
                   ORDER BY dimension_number""",
                *target,
            )
        ]
        chunks = await conn.fetchrow(
            """SELECT count(*) AS chunks, count(*) FILTER (WHERE is_compressed) AS compressed,
                      min(range_start) AS oldest, max(range_end) AS newest
               FROM timescaledb_information.chunks WHERE hypertable_schema = $1 AND hypertable_name = $2""",
            *target,
        )
        result.update(dict(chunks))
        regclass = f'"{target[0]}"."{target[1]}"' if cagg else quoted
        result["size"] = await conn.fetchval("SELECT hypertable_size($1::regclass)", regclass)
        compression = await conn.fetchrow(
            """SELECT sum(before_compression_total_bytes) AS before, sum(after_compression_total_bytes) AS after
               FROM hypertable_compression_stats($1::regclass)""",
            regclass,
        )
        result["compression_before"] = compression["before"]
        result["compression_after"] = compression["after"]
        result["jobs"] = [
            dict(r)
            for r in await conn.fetch(
                """SELECT j.job_id, j.proc_name, j.schedule_interval, j.config::text AS config, s.last_run_status,
                          s.last_successful_finish, s.next_start, s.total_failures
                   FROM timescaledb_information.jobs j LEFT JOIN timescaledb_information.job_stats s USING (job_id)
                   WHERE j.hypertable_schema = $1 AND j.hypertable_name = $2 ORDER BY j.job_id""",
                *target,
            )
        ]
        if cagg:
            result["materialized_only"] = cagg["materialized_only"]
            result["definition"] = cagg["view_definition"]
        return result

    async def rows(
        self, database: str, schema: str, name: str, offset: int, limit: int, order: str | None, desc: bool
    ) -> dict:
        """A page of a table's rows, ordered by one of its columns."""
        limit = max(1, min(limit, ROWS_LIMIT))
        offset = max(0, offset)
        pool = await self.pool(database)
        async with pool.acquire() as conn:
            rel = await self._relation(conn, schema, name)
            columns = [
                r["attname"]
                for r in await conn.fetch(
                    "SELECT attname FROM pg_attribute WHERE attrelid = $1 AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
                    rel["oid"],
                )
            ]
            clause = ""
            if order:
                if order not in columns:
                    raise AdminError("Unknown column")
                quoted = await conn.fetchval("SELECT quote_ident($1)", order)
                clause = f" ORDER BY {quoted} {'DESC' if desc else 'ASC'} NULLS LAST"
            async with conn.transaction(readonly=True):
                statement = await conn.prepare(f"SELECT * FROM {rel['quoted']}{clause} LIMIT $1 OFFSET $2")
                types = [{"name": a.name, "type": a.type.name} for a in statement.get_attributes()]
                rows = await statement.fetch(limit + 1, offset)
        return {
            "columns": types,
            "rows": [[_cell(v) for v in row] for row in rows[:limit]],
            "more": len(rows) > limit,
            "offset": offset,
            "limit": limit,
        }

    # ------------------------------------------------------------------ dashboard and activity

    async def dashboard(self, database: str) -> dict:
        pool = await self.pool(database)
        async with pool.acquire() as conn:
            stat = await conn.fetchrow(
                """SELECT numbackends AS connections, xact_commit AS commits, xact_rollback AS rollbacks,
                          blks_read, blks_hit, tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted,
                          conflicts, temp_files, temp_bytes, deadlocks, stats_reset,
                          pg_database_size(current_database()) AS size
                   FROM pg_stat_database WHERE datname = current_database()"""
            )
            counts = await conn.fetchrow(
                """SELECT count(*) FILTER (WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition) AS tables,
                          count(*) FILTER (WHERE c.relkind IN ('v', 'm')) AS views,
                          count(*) FILTER (WHERE c.relkind = 'i') AS indexes,
                          count(DISTINCT n.oid) AS schemas
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                     AND n.nspname NOT LIKE '\\_timescaledb\\_%' AND n.nspname NOT LIKE 'timescaledb\\_%'
                     AND n.nspname NOT LIKE 'pg\\_temp%' AND n.nspname NOT LIKE 'pg\\_toast%'"""
            )
            timescale = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            # The largest tables: a hypertable with all its chunks, a continuous aggregate with its
            # materialized data; TimescaleDB's own chunks are not listed separately
            largest = await conn.fetch(
                """SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS relkind,
                          pg_table_size(c.oid) AS table, pg_indexes_size(c.oid) AS indexes,
                          pg_total_relation_size(c.oid) AS total, greatest(c.reltuples, 0)::bigint AS rows,
                          s.n_dead_tup AS dead, s.n_live_tup AS live, s.last_autovacuum, s.last_vacuum,
                          s.seq_scan, s.idx_scan
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid
                   WHERE c.relkind IN ('r', 'p', 'm') AND NOT c.relispartition
                     AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
                     AND n.nspname NOT LIKE '\\_timescaledb\\_%'"""
            )
            hyper: dict[tuple, dict] = {}
            if timescale:
                for r in await conn.fetch(
                    """SELECT h.hypertable_schema AS schema, h.hypertable_name AS name, d.table_bytes, d.index_bytes,
                              d.total_bytes, h.num_chunks, h.compression_enabled
                       FROM timescaledb_information.hypertables h,
                            LATERAL hypertable_detailed_size(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass) d"""
                ):
                    hyper[(r["schema"], r["name"])] = dict(r)
                for r in await conn.fetch(
                    """SELECT a.view_schema AS schema, a.view_name AS name, d.table_bytes, d.index_bytes, d.total_bytes
                       FROM timescaledb_information.continuous_aggregates a,
                            LATERAL hypertable_detailed_size(format('%I.%I', a.materialization_hypertable_schema,
                                                                    a.materialization_hypertable_name)::regclass) d"""
                ):
                    hyper[(r["schema"], r["name"])] = {**dict(r), "cagg": True}
                compression = await conn.fetchrow(
                    """SELECT sum(s.before_compression_total_bytes) AS before, sum(s.after_compression_total_bytes) AS after
                       FROM timescaledb_information.hypertables h,
                            LATERAL hypertable_compression_stats(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass) s"""
                )
                jobs = await conn.fetchrow(
                    """SELECT count(*) AS jobs, count(*) FILTER (WHERE s.last_run_status = 'Failed') AS failing
                       FROM timescaledb_information.jobs j LEFT JOIN timescaledb_information.job_stats s USING (job_id)
                       WHERE j.job_id >= 1000"""
                )
            else:
                compression = jobs = None

        tables = []
        for r in largest:
            key = (r["schema"], r["name"])
            h = hyper.get(key)
            if r["relkind"] == "m" and not h:
                kind = "materialized view"
            elif h:
                kind = "hypertable"
            else:
                kind = "table"
            tables.append(
                {
                    "schema": r["schema"],
                    "name": r["name"],
                    "kind": kind,
                    "table": h["table_bytes"] if h else r["table"],
                    "indexes": h["index_bytes"] if h else r["indexes"],
                    "total": h["total_bytes"] if h else r["total"],
                    "rows": r["rows"],
                    "dead": r["dead"],
                    "live": r["live"],
                    "last_vacuum": max(filter(None, (r["last_vacuum"], r["last_autovacuum"])), default=None),
                    "seq_scan": r["seq_scan"],
                    "idx_scan": r["idx_scan"],
                }
            )
        # Continuous aggregates are views; their data is in TimescaleDB's own schema
        for (schema, name), h in hyper.items():
            if h.get("cagg"):
                tables.append(
                    {
                        "schema": schema,
                        "name": name,
                        "kind": "continuous aggregate",
                        "table": h["table_bytes"],
                        "indexes": h["index_bytes"],
                        "total": h["total_bytes"],
                        "rows": None,
                        "dead": None,
                        "live": None,
                        "last_vacuum": None,
                        "seq_scan": None,
                        "idx_scan": None,
                    }
                )
        tables.sort(key=lambda t: t["total"] or 0, reverse=True)
        hits, reads = stat["blks_hit"] or 0, stat["blks_read"] or 0
        return {
            "database": database,
            "chronicle": database == self.config.database,
            "timescaledb": timescale,
            "stat": dict(stat),
            "cache_hit": hits / (hits + reads) if hits + reads else None,
            "counts": {**dict(counts), "hypertables": sum(1 for t in tables if t["kind"] == "hypertable")},
            "tables": tables[:25],
            "compression": dict(compression) if compression else None,
            "jobs": dict(jobs) if jobs else None,
            "series": self.series(database),
        }

    async def activity(self) -> dict:
        pool = await self.pool("postgres")
        async with pool.acquire() as conn:
            server = await conn.fetchrow(
                """SELECT current_setting('server_version') AS version, pg_postmaster_start_time() AS started,
                          (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend') AS connections,
                          current_setting('max_connections')::int AS max_connections"""
            )
            settings = await conn.fetch(
                """SELECT name, setting, unit, short_desc FROM pg_settings
                   WHERE name IN ('shared_buffers', 'effective_cache_size', 'work_mem', 'maintenance_work_mem',
                                  'max_connections', 'max_worker_processes', 'max_parallel_workers',
                                  'timescaledb.max_background_workers', 'wal_level', 'max_wal_size',
                                  'checkpoint_timeout', 'autovacuum', 'random_page_cost', 'jit')
                   ORDER BY name"""
            )
            sessions = await conn.fetch(
                """SELECT pid, datname AS database, usename AS role, application_name AS application,
                          host(client_addr) AS client, backend_type AS type, state, backend_start, xact_start,
                          query_start, state_change, wait_event_type, wait_event, left(query, 2000) AS query,
                          pg_blocking_pids(pid) AS blocked_by, pid = pg_backend_pid() AS own
                   FROM pg_stat_activity ORDER BY backend_type <> 'client backend', query_start NULLS LAST"""
            )
            checkpointer = await conn.fetchrow(
                "SELECT num_timed, num_requested, write_time, sync_time, buffers_written, stats_reset FROM pg_stat_checkpointer"
            )
            wal = await conn.fetchrow("SELECT wal_records, wal_bytes, stats_reset FROM pg_stat_wal")
        statements = None
        try:
            chronicle = await self.pool(self.config.database)
            statements = [
                dict(r)
                for r in await chronicle.fetch(
                    """SELECT d.datname AS database, left(s.query, 1000) AS query, s.calls, s.total_exec_time,
                              s.mean_exec_time, s.rows, s.shared_blks_hit, s.shared_blks_read
                       FROM pg_stat_statements s LEFT JOIN pg_database d ON d.oid = s.dbid
                       ORDER BY s.total_exec_time DESC LIMIT 25"""
                )
            ]
        except (AdminError, asyncpg.PostgresError) as e:
            logger.debug(f"No statement statistics: {e}")
        return {
            "server": dict(server),
            "settings": [dict(s) for s in settings],
            "sessions": [dict(s) for s in sessions],
            "checkpointer": dict(checkpointer) if checkpointer else None,
            "wal": dict(wal) if wal else None,
            "statements": statements,
            "series": self.series(None),
        }

    async def signal(self, pid: int, terminate: bool) -> bool:
        pool = await self.pool("postgres")
        function = "pg_terminate_backend" if terminate else "pg_cancel_backend"
        try:
            return bool(await pool.fetchval(f"SELECT {function}($1)", pid))
        except asyncpg.InsufficientPrivilegeError as e:
            raise AdminError("That session belongs to the superuser; the app does not stop it") from e

    # ------------------------------------------------------------------ samples (in memory)

    def start_sampler(self) -> None:
        if self._sampler is None or self._sampler.done():
            self._sampler = asyncio.create_task(self._sample_loop(), name="sampler")

    async def _sample_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self.sample()
            await asyncio.sleep(SAMPLE_SECONDS)

    async def sample(self) -> None:
        """Counters of every database once a minute; the charts show the change per minute."""
        pool = await self.pool("postgres")
        rows = await pool.fetch(
            """SELECT datname, numbackends, xact_commit + xact_rollback AS xacts, tup_inserted, tup_updated,
                      tup_deleted, tup_fetched, blks_hit, blks_read, pg_database_size(datid) AS size
               FROM pg_stat_database WHERE datname IS NOT NULL"""
        )
        states = await pool.fetch(
            """SELECT coalesce(state, 'other') AS state, count(*) AS n FROM pg_stat_activity
               WHERE backend_type = 'client backend' GROUP BY 1"""
        )
        self.samples.append(
            {
                "t": time.time(),
                "db": {r["datname"]: dict(r) for r in rows},
                "states": {r["state"]: r["n"] for r in states},
            }
        )

    def series(self, database: str | None) -> dict:
        """Per minute: transactions, rows written and read, cache hits, connections, size."""
        points = list(self.samples)
        out: dict[str, list] = {
            k: []
            for k in (
                "t",
                "xacts",
                "inserted",
                "updated",
                "deleted",
                "fetched",
                "hit",
                "connections",
                "size",
                "active",
                "idle",
            )
        }
        for before, after in itertools.pairwise(points):
            minutes = max((after["t"] - before["t"]) / 60, 1e-6)
            names = [database] if database else list(after["db"])

            def delta(key, names=names, before=before, after=after, minutes=minutes):
                total = 0
                for n in names:
                    a, b = after["db"].get(n), before["db"].get(n)
                    if a and b:
                        total += max(a[key] - b[key], 0)
                return round(total / minutes, 2)

            hit, read = delta("blks_hit"), delta("blks_read")
            out["t"].append(after["t"])
            out["xacts"].append(delta("xacts"))
            out["inserted"].append(delta("tup_inserted"))
            out["updated"].append(delta("tup_updated"))
            out["deleted"].append(delta("tup_deleted"))
            out["fetched"].append(delta("tup_fetched"))
            out["hit"].append(round(hit / (hit + read), 4) if hit + read else None)
            out["connections"].append(sum((after["db"].get(n) or {}).get("numbackends", 0) for n in names))
            out["size"].append(sum((after["db"].get(n) or {}).get("size", 0) for n in names))
            out["active"].append(after["states"].get("active", 0))
            out["idle"].append(after["states"].get("idle", 0) + after["states"].get("idle in transaction", 0))
        return out

    # ------------------------------------------------------------------ maintenance

    def start_job(self, job: Job, work) -> Job:
        if any(j.state == "running" for j in self.jobs):
            raise AdminError("Another maintenance task is running; wait until it is done")
        self.jobs.append(job)
        del self.jobs[:-30]

        async def runner():
            try:
                job.result = await work(job) or {}
                job.state = "done"
            except AdminError as e:
                job.error, job.state = str(e), "failed"
            except asyncpg.PostgresError as e:
                job.error, job.state = str(e), "failed"
            except Exception as e:
                logger.exception(f"Maintenance task {job.kind} failed")
                job.error, job.state = f"Unexpected error: {e}", "failed"
            finally:
                job.finished = time.time()

        task = asyncio.create_task(runner(), name=f"job-{job.kind}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _own_connection(self, database: str) -> asyncpg.Connection:
        """A connection for one maintenance task: no statement timeout (VACUUM FULL takes long)."""
        await self.pool(database)  # checks that the database exists
        return await asyncpg.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.username,
            password=self.config.password or None,
            database=database,
            timeout=10,
            server_settings={**UI_SESSION, "application_name": "chronicle-maintenance", "statement_timeout": "0"},
        )

    async def maintenance(self, action: str, database: str, schema: str | None, name: str | None, options: dict) -> Job:
        """Starts one maintenance task; checks everything before it starts."""
        target = oid = None
        pool = await self.pool(database)
        async with pool.acquire() as conn:
            if name is not None:
                rel = await self._relation(conn, schema or "", name)
                target, oid = rel["quoted"], int(rel["oid"])
            quoted_database = await conn.fetchval("SELECT quote_ident($1)", database)
        label = target or database

        if action == "vacuum":
            full = bool(options.get("full"))
            sql = f"VACUUM ({'FULL, ' if full else ''}ANALYZE{', VERBOSE' if not full else ''}) {target or ''}".strip()
            title = f"VACUUM{' FULL' if full else ''} {label}"
        elif action == "analyze":
            sql, title = f"ANALYZE {target or ''}".strip(), f"ANALYZE {label}"
        elif action == "reindex":
            sql = f"REINDEX TABLE {target}" if target else f"REINDEX DATABASE {quoted_database}"
            title = f"REINDEX {label}"
        elif action == "compress":
            if not target:
                raise AdminError("Pick a hypertable")
            days = _int_option(options, "older_than_days", 7, 0, 36500)
            sql = (
                f"SELECT count(compress_chunk(c, if_not_compressed => true)) "
                f"FROM show_chunks({oid}::oid::regclass, older_than => INTERVAL '{days} days') c"
            )
            title = f"Compress the chunks of {label} older than {days} days"
        elif action == "refresh":
            if not target:
                raise AdminError("Pick a continuous aggregate")
            sql, title = f"CALL refresh_continuous_aggregate({oid}::oid::regclass, NULL, NULL)", f"Refresh {label}"
        elif action == "drop_chunks":
            if not target:
                raise AdminError("Pick a hypertable")
            days = _int_option(options, "older_than_days", None, 1, 36500)
            if options.get("confirm") != name:
                raise AdminError("Type the table's name to confirm")
            sql = f"SELECT count(*) FROM drop_chunks({oid}::oid::regclass, older_than => INTERVAL '{days} days')"
            title = f"Remove the history of {label} older than {days} days"
        else:
            raise AdminError("Unknown maintenance task")

        async def work(job: Job) -> dict:
            conn = await self._own_connection(database)
            conn.add_log_listener(lambda _c, m: job.log.append(f"{m.severity}: {m.message}"))
            try:
                job.log.append(sql)
                started = time.monotonic()
                if sql.startswith("SELECT"):
                    count = await conn.fetchval(sql)
                    job.log.append(f"{count} chunks")
                    result = {"chunks": count}
                else:
                    job.log.append(await conn.execute(sql))
                    result = {}
                job.log.append(f"Done in {time.monotonic() - started:.1f} s")
                del job.log[:-300]
                return result
            finally:
                await conn.close()

        return self.start_job(Job(action, title), work)

    # ------------------------------------------------------------------ read-only logins

    async def readers(self) -> list[dict]:
        pool = await self.pool("postgres")
        rows = await pool.fetch(
            """SELECT r.rolname AS name, r.rolvaliduntil AS valid_until,
                      array(SELECT d.datname FROM pg_database d
                            WHERE has_database_privilege(r.rolname, d.datname, 'CONNECT')
                              AND NOT d.datistemplate AND d.datname <> 'postgres' ORDER BY 1) AS databases
               FROM pg_roles r
               WHERE r.rolcanlogin AND NOT r.rolsuper AND r.rolname <> ALL($1::text[])
                 AND pg_has_role('chronicle_ui', r.oid, 'USAGE')
               ORDER BY 1""",
            list(APP_ROLES),
        )
        return [dict(r) for r in rows]

    async def create_reader(self, name: str, databases: list[str]) -> dict:
        """A login that can read every table in the given databases (also tables made later by
        the Chronicle integration), for Grafana and other tools. The password is shown once."""
        if not READER_NAME.match(name) or name in APP_ROLES or name.startswith("pg_"):
            raise AdminError("A login name has 3 to 31 lowercase letters, digits and underscores")
        existing = await self._database_names()
        databases = [d for d in databases if d in existing]
        if not databases:
            raise AdminError("Pick at least one database")
        password = secrets.token_urlsafe(18)
        pool = await self.pool("postgres")
        async with pool.acquire() as conn:
            if await conn.fetchval("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = $1)", name):
                raise AdminError(f"There is already a role {name}")
            # Only the SCRAM verifier reaches the server, never the password itself (which the
            # statement statistics and logs would keep)
            await conn.execute(f"CREATE ROLE {name} LOGIN PASSWORD '{scram_verifier(password)}'")
        problems = []
        for database in databases:
            problems += await self._grant_reader(name, database)
        return {"name": name, "password": password, "databases": databases, "problems": problems}

    async def _grant_reader(self, name: str, database: str) -> list[str]:
        problems = []
        pool = await self.pool(database)
        async with pool.acquire() as conn:
            # Connecting is for who is granted it, not for every role (as PostgreSQL has it by
            # default for a new database); the app's start does the same for every database
            with contextlib.suppress(asyncpg.InsufficientPrivilegeError):
                quoted = await conn.fetchval("SELECT quote_ident($1)", database)
                await conn.execute(f"REVOKE CONNECT, TEMPORARY ON DATABASE {quoted} FROM PUBLIC")
            quoted_database = await conn.fetchval("SELECT quote_ident($1)", database)
            await conn.execute(f"GRANT CONNECT ON DATABASE {quoted_database} TO {name}")
            schemas = [
                r["nspname"]
                for r in await conn.fetch("SELECT nspname FROM pg_namespace ORDER BY 1")
                if not is_system_schema(r["nspname"])
            ]
            for schema in schemas:
                quoted = await conn.fetchval("SELECT quote_ident($1)", schema)
                try:
                    await conn.execute(f"GRANT USAGE ON SCHEMA {quoted} TO {name}")
                    await conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {quoted} TO {name}")
                    # Tables the integration makes later are readable too
                    if await conn.fetchval("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = $1)", INTEGRATION_ROLE):
                        await conn.execute(
                            f"ALTER DEFAULT PRIVILEGES FOR ROLE {INTEGRATION_ROLE} IN SCHEMA {quoted} "
                            f"GRANT SELECT ON TABLES TO {name}"
                        )
                except asyncpg.InsufficientPrivilegeError as e:
                    problems.append(f"{database}.{schema}: {e}")
        return problems

    async def drop_reader(self, name: str) -> None:
        if name not in {r["name"] for r in await self.readers()}:
            raise AdminError("Not a read-only login of this app")
        for database in await self._database_names():
            pool = await self.pool(database)
            await pool.execute(f"DROP OWNED BY {name}")
        pool = await self.pool("postgres")
        await pool.execute(f"DROP ROLE {name}")


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """PostgreSQL's SCRAM-SHA-256 verifier of a password (as password_encryption makes it)."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def _int_option(options: dict, key: str, default: int | None, low: int, high: int) -> int:
    value = options.get(key, default)
    try:
        number = int(value)
    except (TypeError, ValueError) as e:
        raise AdminError(f"{key} must be a number") from e
    if not low <= number <= high:
        raise AdminError(f"{key} must be between {low} and {high}")
    return number
