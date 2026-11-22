"""App databases: a database with a login of its own for another Home Assistant app, made as
the web UI's role, with TimescaleDB added by the helper (against the test database)."""

import asyncio
import contextlib
import shutil
import tempfile
from pathlib import Path

import asyncpg
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src import helper, webui_admin
from src.admin import APP_MARK, Admin, AdminError
from src.config import Config
from src.database import Database
from src.helper import Helper, Task, TaskError
from src.webui import WebUI

from .conftest import HOST, PASSWORD, PORT

SAME_PAGE = {"X-Chronicle": "1"}
UI_ROLE = "chronicle_ui"


async def _superuser(database: str = "postgres") -> asyncpg.Connection:
    return await asyncpg.connect(host=HOST, port=PORT, user="postgres", password=PASSWORD, database=database)


async def _drop_test_objects(conn: asyncpg.Connection) -> None:
    for name in await conn.fetch("SELECT datname FROM pg_database WHERE datname LIKE 'test\\_app\\_%'"):
        await conn.execute(f'DROP DATABASE IF EXISTS "{name["datname"]}" WITH (FORCE)')
    for name in await conn.fetch("SELECT rolname FROM pg_roles WHERE rolname LIKE 'test\\_app\\_%'"):
        await conn.execute(f'DROP ROLE IF EXISTS "{name["rolname"]}"')


@pytest.fixture
async def ui_config(config):
    """The web UI's role as the app sets it up (init-postgres): CREATEDB and CREATEROLE, no
    superuser, the roles it makes its own to manage again."""
    admin = await _superuser()
    await _drop_test_objects(admin)
    await admin.execute(f"DROP ROLE IF EXISTS {UI_ROLE}")
    await admin.execute(
        f"CREATE ROLE {UI_ROLE} LOGIN NOSUPERUSER CREATEDB CREATEROLE PASSWORD '{PASSWORD}'"
        f" IN ROLE pg_monitor, pg_signal_backend, pg_maintain"
    )
    await admin.execute(f"ALTER ROLE {UI_ROLE} SET createrole_self_grant = 'set, inherit'")
    yield Config(host=HOST, port=PORT, username=UI_ROLE, password=PASSWORD, database=config.database)
    await _drop_test_objects(admin)
    for row in await admin.fetch("SELECT datname FROM pg_database WHERE datallowconn"):
        with contextlib.suppress(asyncpg.PostgresError):
            conn = await _superuser(row["datname"])
            try:
                await conn.execute(f"DROP OWNED BY {UI_ROLE}")
            finally:
                await conn.close()
    await admin.execute(f"DROP ROLE IF EXISTS {UI_ROLE}")
    await admin.close()


async def _tcp_connect(database: str = "postgres") -> asyncpg.Connection:
    """The helper's own connection, over TCP instead of the app's local socket."""
    return await asyncpg.connect(
        host=HOST,
        port=PORT,
        user="postgres",
        password=PASSWORD,
        database=database,
        statement_cache_size=0,
        server_settings={**helper.SESSION, "application_name": "chronicle-helper"},
    )


@pytest.fixture
async def helper_socket(monkeypatch):
    """The helper on a Unix socket of its own, as the web UI reaches it in the app."""
    monkeypatch.setattr(helper, "connect", _tcp_connect)
    folder = Path(tempfile.mkdtemp(prefix="chs-"))
    socket = folder / "helper.sock"
    runner = web.AppRunner(helper.make_app(Helper()))
    await runner.setup()
    await web.UnixSite(runner, str(socket)).start()
    monkeypatch.setattr(webui_admin, "HELPER_SOCKET", socket)
    yield socket
    await runner.cleanup()
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
async def client(ui_config, helper_socket, tmp_path, monkeypatch):
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    db = Database(ui_config)
    ui = WebUI(0, ui_config, db)
    async with TestClient(TestServer(ui.app)) as c:
        yield c
    await ui.admin.close()
    await db.close()


async def _login(name: str, password: str, database: str | None = None) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=HOST, port=PORT, user=name, password=password, database=database or name, timeout=5
    )


async def test_create_list_rotate_remove(client):
    response = await client.post("/api/apps/create", json={"name": "test_app_vigil"}, headers=SAME_PAGE)
    created = await response.json()
    assert response.status == 200, created
    assert created["database"] == created["username"] == "test_app_vigil"
    assert created["port"] == 5432 and created["host"]
    password = created["password"]

    # The login connects, owns its database, has TimescaleDB and can make hypertables
    conn = await _login("test_app_vigil", password)
    try:
        info = await conn.fetchrow(
            """SELECT pg_get_userbyid(d.datdba) AS owner, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
                      r.rolreplication, r.rolbypassrls
               FROM pg_database d, pg_roles r WHERE d.datname = current_database() AND r.rolname = current_user"""
        )
        assert info["owner"] == "test_app_vigil"
        assert not any(info[k] for k in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls"))
        assert await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        await conn.execute("CREATE SCHEMA vigil")
        await conn.execute("CREATE TABLE vigil.logs (ts timestamptz NOT NULL, message text)")
        await conn.execute("SELECT create_hypertable('vigil.logs', by_range('ts'))")
    finally:
        await conn.close()

    # Nobody else may connect (CONNECT is not PUBLIC's)
    admin = await _superuser()
    try:
        await admin.execute("CREATE ROLE test_app_other LOGIN PASSWORD 'other'")
        assert (
            await admin.fetchval(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = 'test_app_vigil'"
            )
            == APP_MARK
        )
    finally:
        await admin.close()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _login("test_app_other", "other", "test_app_vigil")

    listed = (await (await client.get("/api/apps")).json())["databases"]
    app = next(d for d in listed if d["name"] == "test_app_vigil")
    assert app["owner"] == "test_app_vigil" and app["size"] > 0 and app["timescaledb"]
    assert "password" not in app
    # Not a read-only login, and the helper's task is in the log
    readers = (await (await client.get("/api/readers")).json())["readers"]
    assert "test_app_vigil" not in {r["name"] for r in readers}
    tasks = (await (await client.get("/api/tasks")).json())["tasks"]
    assert any(t["kind"] == "timescaledb" and t["state"] == "done" for t in tasks)
    assert not any(password in line for t in tasks for line in t["log"])

    # A new password: the old one stops working
    rotated = await (await client.post("/api/apps/password", json={"name": "test_app_vigil"}, headers=SAME_PAGE)).json()
    assert rotated["password"] != password and rotated["database"] == "test_app_vigil"
    with pytest.raises(asyncpg.InvalidPasswordError):
        await _login("test_app_vigil", password)
    await (await _login("test_app_vigil", rotated["password"])).close()

    # The Databases page's Remove doesn't take an app database (its login would stay behind)
    response = await client.post("/api/databases/drop", json={"database": "test_app_vigil"}, headers=SAME_PAGE)
    assert response.status == 400 and "app database" in (await response.json())["error"]

    # Remove: only with the name typed
    response = await client.post("/api/apps/drop", json={"name": "test_app_vigil", "confirm": "x"}, headers=SAME_PAGE)
    assert response.status == 400
    conn = await _login("test_app_vigil", rotated["password"])  # an open session doesn't stop it
    response = await client.post(
        "/api/apps/drop", json={"name": "test_app_vigil", "confirm": "test_app_vigil"}, headers=SAME_PAGE
    )
    assert response.status == 200, await response.text()
    with contextlib.suppress(Exception):
        await conn.close()
    admin = await _superuser()
    try:
        assert not await admin.fetchval("SELECT EXISTS (SELECT FROM pg_database WHERE datname = 'test_app_vigil')")
        assert not await admin.fetchval("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'test_app_vigil')")
    finally:
        await admin.close()


async def test_requests_need_the_page_header(client):
    for path in ("/api/apps/create", "/api/apps/password", "/api/apps/drop"):
        assert (await client.post(path, json={"name": "test_app_x"})).status == 403


@pytest.mark.parametrize(
    "name",
    ["ab", "Vigil", "1vigil", "pg_vigil", "postgres", "chronicle", "chronicle_ui", "template1", 'x"; DROP', "a" * 32],
)
async def test_bad_names_are_refused(ui_config, name):
    admin = Admin(ui_config)
    try:
        with pytest.raises(AdminError):
            await admin.create_app_database(name)
    finally:
        await admin.close()


async def test_existing_roles_and_databases_are_refused(ui_config):
    su = await _superuser()
    try:
        await su.execute("CREATE ROLE test_app_role NOLOGIN")
        await su.execute("CREATE DATABASE test_app_db")
    finally:
        await su.close()
    admin = Admin(ui_config)
    try:
        with pytest.raises(AdminError, match="already a role"):
            await admin.create_app_database("test_app_role")
        with pytest.raises(AdminError, match="already a database"):
            await admin.create_app_database("test_app_db")
        # The Chronicle database itself
        with pytest.raises(AdminError):
            await admin.create_app_database(ui_config.database)
    finally:
        await admin.close()


async def test_only_marked_app_databases_are_managed(ui_config):
    """A comment alone doesn't make an app database (the owner of any database can set it), and
    a database with a same-name owner but without the mark isn't one either."""
    su = await _superuser()
    try:
        await su.execute(f"COMMENT ON DATABASE \"{ui_config.database}\" IS '{APP_MARK}'")
        await su.execute(f"COMMENT ON DATABASE postgres IS '{APP_MARK}'")
        await su.execute("CREATE ROLE test_app_plain LOGIN PASSWORD 'x'")
        await su.execute("CREATE DATABASE test_app_plain OWNER test_app_plain")
        await su.execute("CREATE DATABASE test_app_super OWNER postgres")
        await su.execute(f"COMMENT ON DATABASE test_app_super IS '{APP_MARK}'")
    finally:
        await su.close()
    admin = Admin(ui_config)
    try:
        names = {d["name"] for d in await admin.app_databases()}
        assert not names & {ui_config.database, "postgres", "test_app_plain", "test_app_super"}
        for name in (ui_config.database, "postgres", "template1", "test_app_plain", "test_app_super"):
            with pytest.raises(AdminError, match="Not an app database"):
                await admin.drop_app_database(name, name)
            with pytest.raises(AdminError, match="Not an app database"):
                await admin.rotate_app_password(name)
    finally:
        await admin.close()
        su = await _superuser()
        try:
            await su.execute(f'COMMENT ON DATABASE "{ui_config.database}" IS NULL')
            await su.execute("COMMENT ON DATABASE postgres IS NULL")
        finally:
            await su.close()


async def test_nothing_is_left_when_timescaledb_cannot_be_added(client, monkeypatch):
    monkeypatch.setattr(webui_admin, "HELPER_SOCKET", Path("/nonexistent/helper.sock"))
    response = await client.post("/api/apps/create", json={"name": "test_app_nohelper"}, headers=SAME_PAGE)
    data = await response.json()
    assert response.status == 400 and "Nothing was created" in data["error"]
    assert "password" not in data
    su = await _superuser()
    try:
        assert not await su.fetchval("SELECT EXISTS (SELECT FROM pg_database WHERE datname = 'test_app_nohelper')")
        assert not await su.fetchval("SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'test_app_nohelper')")
    finally:
        await su.close()


async def test_helper_adds_timescaledb_only_to_a_new_app_database(ui_config, monkeypatch):
    monkeypatch.setattr(helper, "connect", _tcp_connect)
    admin = Admin(ui_config)
    try:
        created = await admin.create_app_database("test_app_tsdb")
        # Not marked, or Chronicle's own: refused
        for database in (ui_config.database, "test_app_unmarked"):
            if database == "test_app_unmarked":
                su = await _superuser()
                try:
                    await su.execute("CREATE ROLE test_app_unmarked LOGIN")
                    await su.execute("CREATE DATABASE test_app_unmarked OWNER test_app_unmarked")
                finally:
                    await su.close()
            with pytest.raises(TaskError, match="not an app database"):
                await Helper().timescaledb(Task("timescaledb", "t"), database)
        # Something of the owner's own in it: refused (the extension's scripts would run as the
        # superuser among the owner's objects)
        conn = await _login("test_app_tsdb", created["password"])
        try:
            await conn.execute("CREATE FUNCTION public.f() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        finally:
            await conn.close()
        with pytest.raises(TaskError, match="not empty"):
            await Helper().timescaledb(Task("timescaledb", "t"), "test_app_tsdb")
        await admin.drop_app_database("test_app_tsdb", "test_app_tsdb")

        # A new one gets it
        await admin.create_app_database("test_app_tsdb")
        task = Task("timescaledb", "t")
        result = await Helper().timescaledb(task, "test_app_tsdb")
        assert result["timescaledb"]
        assert (await admin.app_databases())[0]["timescaledb"] == result["timescaledb"]
        # Twice is fine
        await Helper().timescaledb(task, "test_app_tsdb")
        assert "already" in task.log[-1]
    finally:
        await admin.close()


async def test_helper_route_checks_the_name():
    async with TestClient(TestServer(helper.make_app(Helper()))) as c:
        for database in ("postgres", "homeassistant", "x; DROP", None):
            response = await c.post("/timescaledb", json={"database": database})
            assert response.status == 400
        await asyncio.sleep(0)
