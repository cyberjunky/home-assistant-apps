"""The database helper: the few tasks that need PostgreSQL's superuser.

Restoring a TimescaleDB database needs a superuser: TimescaleDB keeps its catalog and the
chunks in schemas only the superuser may write to. The web UI runs as a role without those
powers, so it asks this helper, which runs as the postgres OS user (superuser over the local
socket), on its own Unix socket that only the web UI's group can reach. The helper only does
these tasks, each with checked arguments; it runs no SQL it is sent:

  dump      a database to a file in the backup folder (pg_dump, custom format)
  restore   a dump file from the backup folder into a new database
  copy      a database from another PostgreSQL server into a new database
  use       make a database the one the Chronicle integration records into
  drop      remove a database (never the one the integration records into)

How it stays safe as the superuser:

- Every session pins its own settings (search_path, read-only, timeouts) when it connects:
  they win over what a database's owner set, so an owner can't slip its own functions or
  operators into the superuser's queries.
- A restore keeps the owners from the dump. PostgreSQL runs index expressions, checks and
  materialized views as the table's owner while restoring, so a function someone put in a
  table never runs as the superuser. Owners that don't exist here get a role without login
  for the restore; what they owned goes to the integration's role afterwards. Event triggers
  (which would run on the superuser's own statements) are removed; subscriptions and
  publications are left out.
- The backup folder (/share/chronicle) is shared with other apps and Samba. Dumps are written
  in a private folder there and renamed into place (a rename doesn't follow a planted symlink),
  and their SHA-256 is recorded in the app's own folder, which others can't reach. A restore
  copies the file to a private place first (without following symlinks) and checks it: a
  file the app didn't make, or that changed, is only restored when the page says to trust it.

A restore or copy creates the TimescaleDB extension at the release of the source first
(TimescaleDB restores only into the same release) and updates it to this image's release
afterwards, and hands the chronicle schema to the integration's role.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import asyncpg
from aiohttp import web

logger = logging.getLogger("chronicle.helper")

SOCKET = Path(os.environ.get("CHRONICLE_HELPER_SOCKET", "/run/chronicle/helper.sock"))
BACKUP_DIR = Path(os.environ.get("CHRONICLE_BACKUP_DIR", "/share/chronicle"))
# The helper's own folder in /data (other apps can't reach it): the record of the dumps it
# made (readable by the web UI's group), and a private folder for work files
HELPER_DIR = Path(os.environ.get("CHRONICLE_HELPER_DIR", "/data/helper"))
MANIFEST = HELPER_DIR / "backups.json"
WORK_DIR = HELPER_DIR / "work"
PGSOCKET = os.environ.get("CHRONICLE_PGSOCKET", "/run/postgresql")
PGBIN = os.environ.get("CHRONICLE_PGBIN", "/usr/local/bin")
# The database the Chronicle integration records into, its role, and the web UI's role
CHRONICLE_DATABASE = "homeassistant"
CHRONICLE_ROLE = "chronicle"
APP_ROLES = {"postgres", CHRONICLE_ROLE, "chronicle_ui"}

# The superuser's session settings: sent when connecting, they win over ALTER DATABASE/ROLE
# settings of a database's owner (rootfs/usr/share/chronicle/common.sh has the same)
SESSION = {
    "search_path": "pg_catalog, pg_temp",
    "default_transaction_read_only": "off",
    "statement_timeout": "0",
    "lock_timeout": "0",
    "idle_in_transaction_session_timeout": "0",
    "pg_stat_statements.track_utility": "off",
}
PGOPTIONS = " ".join(f"-c {k}={v.replace(' ', '')}" for k, v in SESSION.items())

# What the arguments may look like. Database names are kept to plain identifiers so they
# never need quoting in a command line; file names to what the app writes and uploads.
DATABASE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
DUMP_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.dump$")
HOST_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^[0-9a-fA-F:]{2,39}$")
ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,62}$")
# Databases nobody may restore into, drop or rename
PROTECTED = {"postgres", "template0", "template1"}
TASKS_KEPT = 30
LOG_LINES = 200
CHUNK = 1024 * 1024


class TaskError(Exception):
    """A task can't run or failed; the message is for the page."""


@dataclass
class Task:
    """One task and what it has done so far."""

    kind: str
    title: str
    id: str = field(default_factory=lambda: secrets.token_hex(6))
    state: str = "running"
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    error: str | None = None

    def say(self, line: str) -> None:
        logger.info(f"[{self.kind}] {line}")
        self.log.append(line)
        del self.log[:-LOG_LINES]

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "state": self.state,
            "started": self.started,
            "finished": self.finished,
            "log": self.log,
            "result": self.result,
            "error": self.error,
        }


def check_database(name: object, *, new: bool = False) -> str:
    if not isinstance(name, str) or not DATABASE_NAME.match(name):
        raise TaskError("A database name has letters, digits and underscores, and starts with a letter")
    if name in PROTECTED or (new and name == CHRONICLE_DATABASE):
        raise TaskError(f"Not allowed for the database {name}")
    return name


def dump_path(name: object) -> Path:
    """A file in the backup folder; nothing outside it, no hidden files, no paths."""
    if not isinstance(name, str) or not DUMP_FILE.match(name) or ".." in name:
        raise TaskError("Invalid file name")
    path = BACKUP_DIR / name
    if path.parent.resolve() != BACKUP_DIR.resolve():
        raise TaskError("Invalid file name")
    return path


async def connect(database: str = "postgres") -> asyncpg.Connection:
    return await asyncpg.connect(
        host=PGSOCKET,
        user="postgres",
        database=database,
        timeout=10,
        statement_cache_size=0,
        server_settings={**SESSION, "application_name": "chronicle-helper"},
    )


async def run(task: Task, *args: str, env: dict | None = None) -> None:
    """Runs a PostgreSQL client program; its output goes to the task's log."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PGOPTIONS": PGOPTIONS, **(env or {})},
    )
    assert process.stdout is not None
    async for raw in process.stdout:
        line = raw.decode(errors="replace").rstrip()
        # pg_dump warns about TimescaleDB's own catalog on every dump; that is expected
        if line and "circular foreign-key" not in line and not line.startswith(("pg_dump: detail", "pg_dump: hint")):
            task.say(line)
    if await process.wait() != 0:
        raise TaskError(f"{Path(args[0]).name} failed (exit code {process.returncode}), see the log")


async def output(*args: str, env: dict | None = None) -> str:
    """Runs a client program and returns what it printed; fails with its error."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PGOPTIONS": PGOPTIONS, **(env or {})},
    )
    out, err = await process.communicate()
    if process.returncode != 0:
        raise TaskError(err.decode(errors="replace").strip() or f"{Path(args[0]).name} failed")
    return out.decode(errors="replace")


# ---------------------------------------------------------------------- the record of dumps


def read_manifest() -> dict:
    try:
        data = json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_manifest(data: dict) -> None:
    HELPER_DIR.mkdir(parents=True, exist_ok=True)
    partial = MANIFEST.with_name(MANIFEST.name + ".partial")
    partial.write_text(json.dumps(data, indent=1))
    partial.chmod(0o640)
    os.replace(partial, MANIFEST)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def private_copy(path: Path, folder: Path) -> tuple[Path, str]:
    """Copies a file from the shared backup folder into a private folder, without following a
    symlink (another app could put one there), and returns the copy and its SHA-256."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError as e:
        raise TaskError("There is no such backup file") from e
    except OSError as e:
        raise TaskError(f"Cannot read {path.name}: not a plain file") from e
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise TaskError(f"{path.name} is not a plain file")
    copy = folder / "restore.dump"
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as source, copy.open("wb") as target:
        while chunk := source.read(CHUNK):
            digest.update(chunk)
            target.write(chunk)
    return copy, digest.hexdigest()


class Helper:
    """The tasks, one at a time."""

    def __init__(self):
        self.tasks: list[Task] = []
        self._lock = asyncio.Lock()
        self._running: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ tasks

    def start(self, task: Task, work) -> Task:
        if self._lock.locked():
            raise TaskError("Another task is running; wait until it is done")
        self.tasks.append(task)
        del self.tasks[:-TASKS_KEPT]

        async def runner():
            async with self._lock:
                try:
                    task.result = await work(task) or {}
                    task.state = "done"
                except TaskError as e:
                    task.error = str(e)
                    task.state = "failed"
                except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
                    task.error = f"{type(e).__name__}: {e}"
                    task.state = "failed"
                except Exception as e:
                    logger.exception(f"Task {task.kind} failed")
                    task.error = f"Unexpected error: {e}"
                    task.state = "failed"
                finally:
                    task.finished = time.time()
                    if task.error:
                        task.say(f"Failed: {task.error}")

        running = asyncio.create_task(runner(), name=f"task-{task.kind}")
        self._running.add(running)
        running.add_done_callback(self._running.discard)
        return task

    def _work_folder(self):
        """A private folder for one task's files, removed afterwards."""
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(WORK_DIR, 0o700)
        return tempfile.TemporaryDirectory(dir=WORK_DIR)

    async def dump(self, task: Task, database: str) -> dict:
        if not BACKUP_DIR.is_dir():
            raise TaskError("The backup folder /share/chronicle is not there")
        conn = await connect(database)
        try:
            version = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        finally:
            await conn.close()
        name = f"{database}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.dump"
        task.say(f"Dumping {database} to {name}..")
        # Written in a new private folder in the backup folder (same file system), then
        # renamed into place: a rename replaces a planted symlink instead of following it
        folder = Path(tempfile.mkdtemp(prefix=".dump-", dir=BACKUP_DIR))
        try:
            partial = folder / name
            await run(
                task, f"{PGBIN}/pg_dump", "-h", PGSOCKET, "-U", "postgres", "-d", database, "-Fc", "-f", str(partial)
            )
            partial.chmod(0o640)
            digest = sha256_of(partial)
            size = partial.stat().st_size
            os.replace(partial, BACKUP_DIR / name)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        manifest = read_manifest()
        manifest[name] = {
            "sha256": digest,
            "size": size,
            "database": database,
            "timescaledb": version,
            "created": time.time(),
        }
        write_manifest(manifest)
        task.say(f"Done: {name} ({size} bytes)")
        return {"file": name, "size": size}

    async def restore(self, task: Task, path: Path, target: str, drop_tables: list[str], trust: bool) -> dict:
        await self._require_new_database(target)
        with self._work_folder() as folder:
            task.say(f"Checking {path.name}..")
            copy, digest = private_copy(path, Path(folder))
            known = read_manifest().get(path.name)
            if known and known.get("sha256") == digest:
                task.say("Made by this app, unchanged")
            elif not trust:
                why = "changed since this app made it" if known else "not made by this app"
                raise TaskError(f"{path.name} is {why}: restore it only when you trust it, and say so")
            else:
                task.say("Not made by this app (or changed since): restoring it because you trust it")
            version = await self._dump_timescaledb_version(task, copy)
            return await self._restore_into(task, copy, Path(folder), target, version, drop_tables)

    async def copy(self, task: Task, source: dict, target: str, drop_tables: list[str]) -> dict:
        await self._require_new_database(target)
        env = await self._password_env(source["password"])
        try:
            remote = [
                "-h",
                source["host"],
                "-p",
                str(source["port"]),
                "-U",
                source["username"],
                "-d",
                source["database"],
            ]
            task.say(
                f"Connecting to {source['database']} on {source['host']}:{source['port']} as {source['username']}.."
            )
            version = await self._remote_timescaledb_version(task, remote, env["PGPASSFILE"])
            with self._work_folder() as folder:
                path = Path(folder) / "copy.dump"
                task.say("Dumping the database on the other server..")
                # The other server's settings are its own: PGOPTIONS is for this server only
                await run(
                    task,
                    f"{PGBIN}/pg_dump",
                    *remote,
                    "-Fc",
                    "-f",
                    str(path),
                    env={"PGPASSFILE": env["PGPASSFILE"], "PGOPTIONS": ""},
                )
                task.say(f"Dump done ({path.stat().st_size} bytes)")
                return await self._restore_into(task, path, Path(folder), target, version, drop_tables)
        finally:
            Path(env["PGPASSFILE"]).unlink(missing_ok=True)

    async def use(self, task: Task, database: str) -> dict:
        """The database becomes the Chronicle integration's: the current one is renamed (kept)."""
        conn = await connect(database)
        try:
            if not await conn.fetchval("SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname = 'chronicle')"):
                raise TaskError(f"{database} has no Chronicle history (no chronicle schema)")
        finally:
            await conn.close()
        kept = f"{CHRONICLE_DATABASE}_before_{datetime.now().strftime('%Y%m%d_%H%M')}"
        conn = await connect()
        try:
            task.say(f"Disconnecting {CHRONICLE_DATABASE} and {database}..")
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = ANY($1::text[]) AND pid <> pg_backend_pid()",
                [CHRONICLE_DATABASE, database],
            )
            # Both renames at once: the integration never finds a half-done state
            async with conn.transaction():
                if await conn.fetchval(
                    "SELECT EXISTS (SELECT FROM pg_database WHERE datname = $1)", CHRONICLE_DATABASE
                ):
                    await conn.execute(f'ALTER DATABASE "{CHRONICLE_DATABASE}" RENAME TO "{kept}"')
                    task.say(f"The current {CHRONICLE_DATABASE} is kept as {kept}")
                await conn.execute(f'ALTER DATABASE "{database}" RENAME TO "{CHRONICLE_DATABASE}"')
                await conn.execute(f'ALTER DATABASE "{CHRONICLE_DATABASE}" OWNER TO {CHRONICLE_ROLE}')
                await conn.execute(f'GRANT CONNECT, TEMPORARY ON DATABASE "{CHRONICLE_DATABASE}" TO {CHRONICLE_ROLE}')
        except asyncpg.ObjectInUseError as e:
            raise TaskError("The database is still in use; try again") from e
        finally:
            await conn.close()
        conn = await connect(CHRONICLE_DATABASE)
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements SCHEMA public")
        finally:
            await conn.close()
        task.say(f"{database} is now {CHRONICLE_DATABASE}: the Chronicle integration records into it from now on.")
        return {"database": CHRONICLE_DATABASE, "kept": kept}

    async def drop(self, task: Task, database: str) -> dict:
        conn = await connect()
        try:
            if not await conn.fetchval("SELECT EXISTS (SELECT FROM pg_database WHERE datname = $1)", database):
                raise TaskError(f"There is no database {database}")
            await conn.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        finally:
            await conn.close()
        task.say(f"Removed {database}")
        return {"database": database}

    # ------------------------------------------------------------------ restore steps

    async def _require_new_database(self, name: str) -> None:
        conn = await connect()
        try:
            if await conn.fetchval("SELECT EXISTS (SELECT FROM pg_database WHERE datname = $1)", name):
                raise TaskError(f"There is already a database {name}; pick another name")
        finally:
            await conn.close()

    async def _password_env(self, password: str) -> dict:
        """A password file for the other server: never on a command line or in the environment."""
        handle, name = tempfile.mkstemp(prefix="pgpass-")
        with os.fdopen(handle, "w") as f:
            escaped = password.replace("\\", "\\\\").replace(":", "\\:")
            f.write(f"*:*:*:*:{escaped}\n")
        os.chmod(name, 0o600)
        return {"PGPASSFILE": name}

    async def _remote_timescaledb_version(self, task: Task, remote: list[str], passfile: str) -> str | None:
        try:
            out = await output(
                f"{PGBIN}/psql",
                "-X",
                "-tA",
                *remote,
                "-c",
                "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'",
                env={"PGPASSFILE": passfile, "PGCONNECT_TIMEOUT": "10", "PGOPTIONS": ""},
            )
        except TaskError as e:
            raise TaskError(f"Cannot connect to the other server: {e}") from e
        version = out.strip() or None
        task.say(f"TimescaleDB there: {version or 'not installed'}")
        return version

    async def _dump_timescaledb_version(self, task: Task, path: Path) -> str | None:
        """The TimescaleDB release a dump was made with, from TimescaleDB's own metadata in it."""
        try:
            out = await output(
                f"{PGBIN}/pg_restore", "-a", "-n", "_timescaledb_catalog", "-t", "metadata", "-f", "-", str(path)
            )
        except TaskError as e:
            raise TaskError(f"Not a dump this app can read: {e}") from e
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] == "timescaledb_version" and re.match(r"^\d+\.\d+\.\d+$", parts[1]):
                task.say(f"The dump has TimescaleDB {parts[1]}")
                return parts[1]
        task.say("The dump has no TimescaleDB")
        return None

    async def _restore_into(
        self, task: Task, path: Path, folder: Path, target: str, version: str | None, drop_tables: list[str]
    ) -> dict:
        # The dump's table of contents: its extensions are left out (they come from this app),
        # and its owners are known before anything runs
        toc = await output(f"{PGBIN}/pg_restore", "-l", str(path))
        entries = [line for line in toc.splitlines() if not re.search(r" (COMMENT - )?EXTENSION ", line)]
        owners = {line.rsplit(" ", 1)[-1] for line in entries if line and not line.startswith(";") and " " in line}
        owners = {o for o in owners if o and o != "-" and ROLE_NAME.match(o)}
        listing = folder / "restore.list"
        listing.write_text("\n".join(entries) + "\n")

        conn = await connect()
        stand_ins: list[str] = []
        try:
            if version:
                available = await conn.fetch(
                    "SELECT version FROM pg_available_extension_versions WHERE name = 'timescaledb'"
                )
                if version not in {r["version"] for r in available}:
                    raise TaskError(
                        f"The source has TimescaleDB {version}; this app can restore "
                        f"{', '.join(sorted(r['version'] for r in available))}. Update TimescaleDB on the source first."
                    )
            # Owners this server doesn't have get a stand-in role without login for the restore
            existing = {r["rolname"] for r in await conn.fetch("SELECT rolname FROM pg_roles")}
            for owner in sorted(owners - existing):
                await conn.execute(f"CREATE ROLE {await conn.fetchval('SELECT quote_ident($1)', owner)} NOLOGIN")
                stand_ins.append(owner)
            if stand_ins:
                task.say(
                    f"Owners from the dump that this server doesn't have (their objects go to {CHRONICLE_ROLE}): {', '.join(stand_ins)}"
                )
            task.say(f"Creating the database {target}..")
            await conn.execute(f'CREATE DATABASE "{target}" OWNER {CHRONICLE_ROLE}')
            await conn.execute(f'REVOKE CONNECT, TEMPORARY ON DATABASE "{target}" FROM PUBLIC')
            await conn.execute(f'GRANT CONNECT, TEMPORARY ON DATABASE "{target}" TO {CHRONICLE_ROLE}')
        finally:
            await conn.close()

        try:
            if version:
                conn = await connect(target)
                try:
                    await conn.execute(f"CREATE EXTENSION timescaledb SCHEMA public VERSION '{version}'")
                    await conn.execute("SELECT public.timescaledb_pre_restore()")
                finally:
                    await conn.close()

            task.say("Restoring (this takes a while for a large history)..")
            try:
                # Errors are logged and the restore goes on (objects of extensions this image
                # doesn't have, grants to roles it doesn't have, ..); what matters is checked below
                await run(
                    task,
                    f"{PGBIN}/pg_restore",
                    "-h",
                    PGSOCKET,
                    "-U",
                    "postgres",
                    "-d",
                    target,
                    "--no-subscriptions",
                    "--no-publications",
                    "--no-security-labels",
                    "--no-tablespaces",
                    "-L",
                    str(listing),
                    str(path),
                )
            except TaskError:
                task.say("The restore reported errors (above); checking what was restored..")

            conn = await connect(target)
            try:
                if version:
                    schema = await conn.fetchval(
                        "SELECT quote_ident(n.nspname) FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'timescaledb'"
                    )
                    await conn.execute(f"SELECT {schema}.timescaledb_post_restore()")
                await self._make_safe(task, conn, stand_ins)
                for table in drop_tables:
                    await self._drop_table(task, conn, table)
            finally:
                await conn.close()
            if version:
                # A session of its own: ALTER EXTENSION must be its first statement
                conn = await connect(target)
                try:
                    await conn.execute("ALTER EXTENSION timescaledb UPDATE")
                    now = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
                    if now != version:
                        task.say(f"TimescaleDB updated from {version} to {now}")
                finally:
                    await conn.close()
            conn = await connect(target)
            try:
                await self._hand_over(task, conn)
                result = await conn.fetchrow(
                    """SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname = 'chronicle') AS chronicle,
                              pg_database_size(current_database()) AS size"""
                )
                rows = None
                if result["chronicle"] and await conn.fetchval("SELECT to_regclass('chronicle.states') IS NOT NULL"):
                    rows = await conn.fetchval("SELECT count(*) FROM chronicle.states")
                    task.say(f"Chronicle history: {rows} rows")
            finally:
                await conn.close()
        except BaseException:
            task.say(f"Removing the incomplete database {target}..")
            with contextlib.suppress(Exception):
                conn = await connect()
                try:
                    await conn.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
                finally:
                    await conn.close()
            raise
        finally:
            await self._drop_stand_ins(stand_ins)
        task.say(f"Done: {target}")
        return {"database": target, "chronicle": result["chronicle"], "rows": rows, "size": result["size"]}

    async def _make_safe(self, task: Task, conn: asyncpg.Connection, stand_ins: list[str]) -> None:
        """No event triggers (they would run on the superuser's own statements), and what the
        stand-in roles own goes to the integration's role."""
        # Not those of an extension (TimescaleDB has its own): they come from this app
        triggers = await conn.fetch(
            """SELECT t.evtname FROM pg_event_trigger t
               WHERE NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_event_trigger'::regclass
                                 AND d.objid = t.oid AND d.deptype = 'e')"""
        )
        for name in [r["evtname"] for r in triggers]:
            await conn.execute(f"DROP EVENT TRIGGER {await conn.fetchval('SELECT quote_ident($1)', name)}")
            task.say(f"Removed the event trigger {name}")
        for owner in stand_ins:
            quoted = await conn.fetchval("SELECT quote_ident($1)", owner)
            await conn.execute(f"REASSIGN OWNED BY {quoted} TO {CHRONICLE_ROLE}")
            await conn.execute(f"DROP OWNED BY {quoted}")

    async def _drop_stand_ins(self, stand_ins: list[str]) -> None:
        if not stand_ins:
            return
        with contextlib.suppress(Exception):
            conn = await connect()
            try:
                for owner in stand_ins:
                    with contextlib.suppress(asyncpg.PostgresError):
                        await conn.execute(
                            f"DROP ROLE IF EXISTS {await conn.fetchval('SELECT quote_ident($1)', owner)}"
                        )
            finally:
                await conn.close()

    async def _drop_table(self, task: Task, conn: asyncpg.Connection, table: str) -> None:
        schema, _, name = table.partition(".")
        if not name or not ROLE_NAME.match(schema) or not ROLE_NAME.match(name) or schema == "chronicle":
            task.say(f"Skipped removing {table}: not a table name outside the chronicle schema")
            return
        oid = await conn.fetchval(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind IN ('r', 'p', 'v', 'm', 'f')",
            schema,
            name,
        )
        if oid is None:
            task.say(f"No table {table} to remove")
            return
        quoted = await conn.fetchval("SELECT $1::oid::regclass::text", oid)
        await conn.execute(f"DROP TABLE IF EXISTS {quoted} CASCADE")
        task.say(f"Removed {table}")

    async def _hand_over(self, task: Task, conn: asyncpg.Connection) -> None:
        """The chronicle schema and everything in it belongs to the integration's role.
        TimescaleDB passes a hypertable's owner on to its chunks and jobs, a continuous
        aggregate's to its materialized hypertable; identity sequences follow their table."""
        if not await conn.fetchval("SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname = 'chronicle')"):
            return
        timescale = await conn.fetchval("SELECT EXISTS (SELECT FROM pg_extension WHERE extname = 'timescaledb')")
        cagg = (
            """EXISTS (SELECT FROM timescaledb_information.continuous_aggregates a
                       WHERE a.view_schema = n.nspname AND a.view_name = c.relname)"""
            if timescale
            else "false"
        )
        await conn.execute(
            f"""
            DO $$
            DECLARE r record;
            BEGIN
              EXECUTE 'ALTER SCHEMA chronicle OWNER TO {CHRONICLE_ROLE}';
              FOR r IN
                SELECT c.oid::regclass AS obj, c.relkind, {cagg} AS cagg
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'chronicle' AND c.relkind IN ('r', 'p', 'v', 'm')
              LOOP
                IF r.cagg OR r.relkind = 'm' THEN
                  EXECUTE format('ALTER MATERIALIZED VIEW %s OWNER TO {CHRONICLE_ROLE}', r.obj);
                ELSIF r.relkind = 'v' THEN
                  EXECUTE format('ALTER VIEW %s OWNER TO {CHRONICLE_ROLE}', r.obj);
                ELSE
                  EXECUTE format('ALTER TABLE %s OWNER TO {CHRONICLE_ROLE}', r.obj);
                END IF;
              END LOOP;
              FOR r IN
                SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'chronicle' AND p.prokind IN ('f', 'p')
              LOOP
                EXECUTE format('ALTER ROUTINE %s OWNER TO {CHRONICLE_ROLE}', r.fn);
              END LOOP;
            END $$
            """
        )
        task.say(f"The chronicle schema belongs to the role {CHRONICLE_ROLE}")


# ---------------------------------------------------------------------- the socket


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, default=str))


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as e:
        raise TaskError("Expected a JSON object") from e
    if not isinstance(body, dict):
        raise TaskError("Expected a JSON object")
    return body


def _drop_tables(body: dict) -> list[str]:
    tables = body.get("drop_tables") or []
    if (
        not isinstance(tables, list)
        or not all(isinstance(t, str) and len(t) < 130 for t in tables)
        or len(tables) > 100
    ):
        raise TaskError("drop_tables must be a list of schema.table names")
    return tables


def make_app(helper: Helper) -> web.Application:
    @web.middleware
    async def errors(request: web.Request, handler):
        try:
            return await handler(request)
        except TaskError as e:
            return _json({"error": str(e)}, 400)

    async def tasks(_request):
        return _json({"tasks": [t.public() for t in reversed(helper.tasks)], "busy": helper._lock.locked()})

    async def dump(request):
        body = await _body(request)
        database = check_database(body.get("database"))
        task = Task("dump", f"Back up {database}")
        return _json(helper.start(task, lambda t: helper.dump(t, database)).public())

    async def restore(request):
        body = await _body(request)
        path = dump_path(body.get("file"))
        if not path.exists():
            raise TaskError("There is no such backup file")
        target = check_database(body.get("database"), new=True)
        drop_tables = _drop_tables(body)
        trust = body.get("trust") is True
        task = Task("restore", f"Restore {path.name} as {target}")
        return _json(helper.start(task, lambda t: helper.restore(t, path, target, drop_tables, trust)).public())

    async def copy(request):
        body = await _body(request)
        host = body.get("host")
        if not isinstance(host, str) or not HOST_NAME.match(host):
            raise TaskError("Invalid host name")
        try:
            port = int(body.get("port") or 5432)
        except (TypeError, ValueError) as e:
            raise TaskError("Invalid port") from e
        if not 1 <= port <= 65535:
            raise TaskError("Invalid port")
        username = body.get("username")
        if not isinstance(username, str) or not ROLE_NAME.match(username):
            raise TaskError("Invalid user name")
        password = body.get("password")
        if not isinstance(password, str) or len(password) > 1000 or "\n" in password:
            raise TaskError("Invalid password")
        database = body.get("database")
        if not isinstance(database, str) or not ROLE_NAME.match(database):
            raise TaskError("Invalid database name on the other server")
        target = check_database(body.get("target"), new=True)
        drop_tables = _drop_tables(body)
        source = {"host": host, "port": port, "username": username, "password": password, "database": database}
        task = Task("copy", f"Copy {database} from {host} as {target}")
        return _json(helper.start(task, lambda t: helper.copy(t, source, target, drop_tables)).public())

    async def use(request):
        body = await _body(request)
        database = check_database(body.get("database"), new=True)
        task = Task("use", f"Record into {database}")
        return _json(helper.start(task, lambda t: helper.use(t, database)).public())

    async def drop(request):
        body = await _body(request)
        database = check_database(body.get("database"), new=True)
        task = Task("drop", f"Remove {database}")
        return _json(helper.start(task, lambda t: helper.drop(t, database)).public())

    app = web.Application(middlewares=[errors], client_max_size=64 * 1024)
    app.router.add_get("/tasks", tasks)
    app.router.add_post("/dump", dump)
    app.router.add_post("/restore", restore)
    app.router.add_post("/copy", copy)
    app.router.add_post("/use", use)
    app.router.add_post("/drop", drop)
    return app


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    runner = web.AppRunner(make_app(Helper()), access_log=None)
    await runner.setup()
    SOCKET.unlink(missing_ok=True)
    site = web.UnixSite(runner, str(SOCKET))
    await site.start()
    # Only the web UI's group may connect (the folder is the postgres user's, group chronicle)
    SOCKET.chmod(0o660)
    logger.info(f"Database helper listening on {SOCKET}")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.cleanup()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
    sys.exit(0)
