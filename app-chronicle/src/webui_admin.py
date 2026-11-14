"""The web routes of the database pages: Databases (tree, dashboard, tables), SQL, Activity,
Maintenance and Backups. Tasks that need the superuser go to the database helper
(src/helper.py) over its Unix socket; everything else runs as the web UI's own role."""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import aiohttp
import asyncpg
from aiohttp import web

from .admin import Admin, AdminError, Job
from .config import integration_password
from .sql import TIMEOUTS, SessionError, SqlSessions

logger = logging.getLogger(__name__)

HELPER_SOCKET = Path(os.environ.get("CHRONICLE_HELPER_SOCKET", "/run/chronicle/helper.sock"))
BACKUP_DIR = Path(os.environ.get("CHRONICLE_BACKUP_DIR", "/share/chronicle"))
# The helper's record of the dumps it made, with their SHA-256 (out of reach of other apps)
MANIFEST = Path(os.environ.get("CHRONICLE_HELPER_DIR", "/data/helper")) / "backups.json"
DUMP_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.dump$")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$ .-]{0,62}$")
# An upload stops when /share has less than this left
UPLOAD_MIN_FREE = 1024 * 1024 * 1024
MAX_SQL = 1_000_000


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, default=str), content_type="application/json", status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"error": message}, status)


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        raise AdminError("Expected a JSON object")
    return body


def _name(value: object, what: str) -> str:
    if not isinstance(value, str) or not NAME.match(value):
        raise AdminError(f"Invalid {what}")
    return value


def _dump_path(name: object) -> Path:
    if not isinstance(name, str) or not DUMP_FILE.match(name) or ".." in name:
        raise AdminError("Invalid file name")
    path = BACKUP_DIR / name
    if path.parent.resolve() != BACKUP_DIR.resolve():
        raise AdminError("Invalid file name")
    return path


class AdminRoutes:
    def __init__(self, admin: Admin, sessions: SqlSessions, on_change=None):
        self.admin = admin
        self.sessions = sessions
        # Called after something changed databases (the Chronicle pages cache their state)
        self.on_change = on_change or (lambda: None)
        self._following: set[asyncio.Task] = set()
        # The database the Chronicle integration records into (the pages' default)
        self.chronicle = admin.config.database

    def add(self, app: web.Application) -> None:
        get, post = app.router.add_get, app.router.add_post
        get("/api/databases", self.databases)
        get("/api/tree", self.tree)
        get("/api/relation", self.relation)
        get("/api/rows", self.rows)
        get("/api/dashboard", self.dashboard)
        get("/api/activity", self.activity)
        post("/api/activity/signal", self.signal)
        post("/api/sql/run", self.sql_run)
        post("/api/sql/end", self.sql_end)
        post("/api/sql/cancel", self.sql_cancel)
        post("/api/sql/close", self.sql_close)
        get("/api/tasks", self.tasks)
        post("/api/maintenance", self.maintenance)
        get("/api/readers", self.readers)
        post("/api/readers/create", self.reader_create)
        post("/api/readers/drop", self.reader_drop)
        get("/api/backups", self.backups)
        get("/api/backups/file", self.backup_download)
        post("/api/backups/upload", self.backup_upload)
        post("/api/backups/delete", self.backup_delete)
        post("/api/backups/dump", self.helper_dump)
        post("/api/backups/restore", self.helper_restore)
        post("/api/backups/inspect", self.copy_inspect)
        post("/api/backups/copy", self.helper_copy)
        post("/api/databases/use", self.helper_use)
        post("/api/databases/drop", self.helper_drop)
        get("/api/integration", self.integration)

    @staticmethod
    @web.middleware
    async def errors(request: web.Request, handler):
        try:
            return await handler(request)
        except (AdminError, SessionError) as e:
            return _error(str(e))
        except asyncpg.InvalidCatalogNameError:
            return _error("That database does not exist (any more)", 404)

    # ------------------------------------------------------------------ browse

    async def databases(self, _request):
        return _json({"databases": await self.admin.databases(), "chronicle": self.chronicle})

    async def tree(self, request):
        database = _name(request.query.get("database", self.chronicle), "database")
        return _json(await self.admin.tree(database, request.query.get("system") == "1"))

    async def relation(self, request):
        q = request.query
        return _json(
            await self.admin.relation(
                _name(q.get("database"), "database"), _name(q.get("schema"), "schema"), _name(q.get("name"), "name")
            )
        )

    async def rows(self, request):
        q = request.query
        try:
            offset, limit = int(q.get("offset", 0)), int(q.get("limit", 100))
        except ValueError:
            return _error("offset and limit are numbers")
        order = q.get("order") or None
        try:
            return _json(
                await self.admin.rows(
                    _name(q.get("database"), "database"),
                    _name(q.get("schema"), "schema"),
                    _name(q.get("name"), "name"),
                    offset,
                    limit,
                    order,
                    q.get("desc") == "1",
                )
            )
        except asyncpg.QueryCanceledError:
            return _error("The table took too long to read in this order; sort by another column", 504)

    async def dashboard(self, request):
        return _json(await self.admin.dashboard(_name(request.query.get("database", self.chronicle), "database")))

    async def activity(self, _request):
        return _json(await self.admin.activity())

    async def signal(self, request):
        body = await _body(request)
        try:
            pid = int(body.get("pid"))
        except (TypeError, ValueError):
            return _error("Invalid pid")
        return _json({"ok": await self.admin.signal(pid, bool(body.get("terminate")))})

    # ------------------------------------------------------------------ SQL

    async def sql_run(self, request):
        body = await _body(request)
        script = body.get("sql")
        if not isinstance(script, str) or not script.strip():
            return _error("Enter a statement")
        if len(script) > MAX_SQL:
            return _error("The script is too long")
        database = _name(body.get("database") or self.chronicle, "database")
        if database not in {d["name"] for d in await self.admin.databases()}:
            return _error(f"There is no database {database}")
        try:
            timeout = int(body.get("timeout") or TIMEOUTS[1])
        except (TypeError, ValueError):
            timeout = TIMEOUTS[1]
        result = await self.sessions.run(
            self.sessions.get(body.get("session")),
            database,
            script,
            autocommit=body.get("autocommit", True) is not False,
            timeout=timeout,
            confirmed=bool(body.get("confirmed")),
        )
        if result.get("results"):
            self.on_change()
        return _json(result)

    async def sql_end(self, request):
        body = await _body(request)
        session = self.sessions.get(body.get("session"))
        if session is None:
            return _error("The session has ended (closed after 15 minutes without use); nothing is open", 410)
        return _json(await self.sessions.end(session, bool(body.get("commit"))))

    async def sql_cancel(self, request):
        body = await _body(request)
        session = self.sessions.get(body.get("session"))
        if session is None:
            return _json({"ok": False})
        return _json({"ok": await self.sessions.cancel(session, await self.admin.pool("postgres"))})

    async def sql_close(self, request):
        body = await _body(request)
        if isinstance(body.get("session"), str):
            await self.sessions.close(body["session"])
        return _json({"ok": True})

    # ------------------------------------------------------------------ maintenance

    async def tasks(self, _request):
        helper = await self._helper("GET", "/tasks", quiet=True)
        jobs = [j.public() for j in reversed(self.admin.jobs)]
        tasks = sorted(jobs + (helper or {}).get("tasks", []), key=lambda t: t["started"], reverse=True)
        return _json({"tasks": tasks, "helper": helper is not None})

    async def maintenance(self, request):
        body = await _body(request)
        database = _name(body.get("database") or self.chronicle, "database")
        schema = _name(body["schema"], "schema") if body.get("schema") else None
        name = _name(body["name"], "name") if body.get("name") else None
        options = body.get("options") if isinstance(body.get("options"), dict) else {}
        job: Job = await self.admin.maintenance(str(body.get("action")), database, schema, name, options)
        return _json(job.public())

    async def readers(self, _request):
        return _json({"readers": await self.admin.readers()})

    async def reader_create(self, request):
        body = await _body(request)
        databases = body.get("databases")
        if not isinstance(databases, list):
            return _error("Pick at least one database")
        return _json(await self.admin.create_reader(str(body.get("name", "")), [str(d) for d in databases]))

    async def reader_drop(self, request):
        body = await _body(request)
        await self.admin.drop_reader(str(body.get("name", "")))
        return _json({"ok": True})

    # ------------------------------------------------------------------ backups

    async def backups(self, _request):
        if not BACKUP_DIR.is_dir():
            return _json({"available": False, "files": []})
        try:
            made = json.loads(MANIFEST.read_text())
        except (OSError, ValueError):
            made = {}
        files = []
        for path in sorted(BACKUP_DIR.glob("*.dump"), key=lambda p: p.lstat().st_mtime, reverse=True):
            stat = path.lstat()
            if not path.is_file() or path.is_symlink():
                continue
            info = made.get(path.name) if isinstance(made, dict) else None
            ours = isinstance(info, dict) and info.get("size") == stat.st_size
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    # Made by this app (its SHA-256 is checked again before a restore), or not
                    "ours": ours,
                    "database": info.get("database") if ours else None,
                    "timescaledb": info.get("timescaledb") if ours else None,
                }
            )
        usage = shutil.disk_usage(BACKUP_DIR)
        return _json({"available": True, "folder": str(BACKUP_DIR), "files": files, "free": usage.free})

    async def backup_download(self, request):
        path = _dump_path(request.query.get("name"))
        # Not a symlink another app put in the shared folder
        if path.is_symlink() or not path.is_file():
            return _error("There is no such backup file", 404)
        return web.FileResponse(
            path, headers={"Content-Disposition": f'attachment; filename="{path.name}"', "Cache-Control": "no-store"}
        )

    async def backup_upload(self, request):
        """A dump file from the computer, streamed into the backup folder."""
        if not BACKUP_DIR.is_dir():
            return _error("The backup folder (/share/chronicle) is not there")
        reader = await request.multipart()
        part = await reader.next()
        if part is None or getattr(part, "name", None) != "file":
            return _error("Expected a file")
        name = Path(part.filename or "").name
        if not name.endswith(".dump"):
            name = f"{name}.dump" if name else f"upload-{int(time.time())}.dump"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip("._-") or f"upload-{int(time.time())}.dump"
        path = _dump_path(name)
        if path.exists() or path.is_symlink():
            return _error(f"There is already a file {name}")
        # Written in a new private folder, then renamed into place: the shared folder may hold
        # a planted symlink, which a rename replaces instead of following
        folder = Path(tempfile.mkdtemp(prefix=".upload-", dir=BACKUP_DIR))
        partial = folder / name
        size = 0
        try:
            with partial.open("xb") as f:
                while chunk := await part.read_chunk(1024 * 1024):
                    size += len(chunk)
                    f.write(chunk)
                    if size % (64 * 1024 * 1024) < len(chunk) and shutil.disk_usage(BACKUP_DIR).free < UPLOAD_MIN_FREE:
                        raise AdminError("Not enough free space in /share for this file")
            partial.chmod(0o640)
            os.replace(partial, path)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return _json({"name": name, "size": size})

    async def backup_delete(self, request):
        body = await _body(request)
        _dump_path(body.get("name")).unlink(missing_ok=True)
        return _json({"ok": True})

    async def copy_inspect(self, request):
        """Looks at a database on another server before copying it: versions and tables."""
        body = await _body(request)
        try:
            conn = await asyncpg.connect(
                host=str(body.get("host", "")),
                port=int(body.get("port") or 5432),
                user=str(body.get("username", "")),
                password=str(body.get("password", "")),
                database=str(body.get("database", "")),
                timeout=10,
            )
        except (OSError, TimeoutError, ValueError, asyncpg.PostgresError, asyncpg.InterfaceError) as e:
            return _error(f"Cannot connect: {e}")
        try:
            info = await conn.fetchrow(
                """SELECT current_setting('server_version') AS postgres,
                          (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb') AS timescaledb,
                          pg_database_size(current_database()) AS size,
                          EXISTS (SELECT FROM pg_namespace WHERE nspname = 'chronicle') AS chronicle"""
            )
            tables = await conn.fetch(
                """SELECT n.nspname AS schema, c.relname AS name, pg_total_relation_size(c.oid) AS size,
                          greatest(c.reltuples, 0)::bigint AS rows
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
                     AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'chronicle')
                     AND n.nspname NOT LIKE '\\_timescaledb\\_%' AND n.nspname NOT LIKE 'timescaledb\\_%'
                     AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname NOT LIKE 'toolkit\\_%'
                   ORDER BY 3 DESC"""
            )
            extensions = [
                r["extname"]
                for r in await conn.fetch("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1")
            ]
        finally:
            await conn.close()
        local = await self.admin.pool("postgres")
        available = [
            r["version"]
            for r in await local.fetch("SELECT version FROM pg_available_extension_versions WHERE name = 'timescaledb'")
        ]
        # Ltss hypertables keep their chunks elsewhere; their size is the hypertable's
        return _json(
            {
                **dict(info),
                "tables": [dict(t) for t in tables],
                "extensions": extensions,
                "restorable": info["timescaledb"] is None or info["timescaledb"] in available,
                "available": available,
            }
        )

    # ------------------------------------------------------------------ the helper

    async def _helper(self, method: str, path: str, body: dict | None = None, quiet: bool = False):
        connector = aiohttp.UnixConnector(path=str(HELPER_SOCKET))
        try:
            async with (
                aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30)) as session,
                session.request(method, f"http://helper{path}", json=body) as response,
            ):
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise AdminError(data.get("error") or f"The helper refused ({response.status})")
                return data
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as e:
            if quiet:
                return None
            raise AdminError(f"The database helper is not running: {e}") from e

    async def _helper_task(self, request, path: str, keys: tuple[str, ...]) -> web.Response:
        body = await _body(request)
        task = await self._helper("POST", path, {k: body.get(k) for k in keys if k in body})
        # The Chronicle pages and pools follow the databases once the task is done
        follow = asyncio.create_task(self._after_helper(task.get("id")))
        self._following.add(follow)
        follow.add_done_callback(self._following.discard)
        return _json(task)

    async def _after_helper(self, task_id: str | None) -> None:
        for _ in range(3600):
            await asyncio.sleep(2)
            data = await self._helper("GET", "/tasks", quiet=True)
            task = next((t for t in (data or {}).get("tasks", []) if t["id"] == task_id), None)
            if task is None or task["state"] != "running":
                break
        for name in list(self.admin._pools):
            if name != "postgres":
                await self.admin.forget(name)
        self.on_change()

    async def helper_dump(self, request):
        return await self._helper_task(request, "/dump", ("database",))

    async def helper_restore(self, request):
        return await self._helper_task(request, "/restore", ("file", "database", "drop_tables", "trust"))

    async def helper_copy(self, request):
        return await self._helper_task(
            request, "/copy", ("host", "port", "username", "password", "database", "target", "drop_tables")
        )

    async def helper_use(self, request):
        return await self._helper_task(request, "/use", ("database",))

    async def helper_drop(self, request):
        return await self._helper_task(request, "/drop", ("database",))

    # ------------------------------------------------------------------ the integration's connection

    async def integration(self, request):
        """The connection for setting up the integration by hand, with the password."""
        config = self.admin.config
        return _json({**config.integration(), "password": integration_password()})
