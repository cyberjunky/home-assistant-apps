"""The database pages: the tree, a table's details and rows, the dashboard, activity and
maintenance (against the test database)."""

import asyncio

import asyncpg
import pytest

from src.admin import Admin, AdminError


async def _wait(admin, job):
    for _ in range(200):
        if job.state != "running":
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("the task did not finish")


async def test_tree_lists_the_chronicle_tables(config):
    admin = Admin(config)
    try:
        tree = await admin.tree(config.database, system=False)
        schemas = {s["name"]: s for s in tree["schemas"]}
        assert "chronicle" in schemas and "pg_catalog" not in schemas and "_timescaledb_internal" not in schemas
        kinds = {o["name"]: o["kind"] for o in schemas["chronicle"]["objects"]}
        assert kinds["states"] == "hypertable"
        assert kinds["history"] == "view"
        assert kinds["entities"] == "table"
        everything = await admin.tree(config.database, system=True)
        assert "pg_catalog" in {s["name"] for s in everything["schemas"]}
    finally:
        await admin.close()


async def test_relation_and_rows(config):
    admin = Admin(config)
    try:
        r = await admin.relation(config.database, "chronicle", "states")
        assert r["kind"] == "hypertable"
        assert [c["name"] for c in r["columns"]] == ["time", "entity", "state", "value", "attributes"]
        assert r["timescale"]["chunks"] >= 1
        assert any("entity" in i["definition"] for i in r["indexes"])
        view = await admin.relation(config.database, "chronicle", "history")
        assert "JOIN" in view["definition"].upper()

        page = await admin.rows(config.database, "chronicle", "entities", 0, 1, "entity_id", desc=True)
        assert page["rows"][0][1] == "sensor.temperature" and page["more"] is True
        page2 = await admin.rows(config.database, "chronicle", "entities", 1, 1, "entity_id", desc=True)
        assert page2["rows"][0][1] == "light.desk" and page2["more"] is False
        with pytest.raises(AdminError):
            await admin.rows(config.database, "chronicle", "entities", 0, 10, "entity_id; DROP TABLE x", desc=False)
        with pytest.raises(AdminError):
            await admin.relation(config.database, "chronicle", "nope")
        with pytest.raises(AdminError):
            await admin.pool("no_such_database")
    finally:
        await admin.close()


async def test_odd_names_are_quoted(config):
    """Names are looked up in the catalog and quoted by PostgreSQL: any character works."""
    conn = await asyncpg.connect(
        host=config.host, port=config.port, user=config.username, password=config.password, database=config.database
    )
    try:
        await conn.execute('CREATE TABLE public."we""ird; name" ("o\'dd col" int)')
        await conn.execute('INSERT INTO public."we""ird; name" VALUES (1), (2)')
    finally:
        await conn.close()
    admin = Admin(config)
    try:
        page = await admin.rows(config.database, "public", 'we"ird; name', 0, 10, "o'dd col", desc=True)
        assert page["rows"] == [[2], [1]]
        job = await admin.maintenance("analyze", config.database, "public", 'we"ird; name', {})
        assert (await _wait(admin, job)).state == "done", job.error
    finally:
        await admin.close()


async def test_dashboard_and_activity(config):
    admin = Admin(config)
    try:
        await admin.sample()
        await admin.sample()
        d = await admin.dashboard(config.database)
        assert d["stat"]["size"] > 0
        assert any(t["name"] == "states" and t["kind"] == "hypertable" for t in d["tables"])
        assert len(d["series"]["t"]) == 1
        a = await admin.activity()
        assert a["server"]["version"]
        assert any(s["own"] for s in a["sessions"])
        databases = await admin.databases()
        assert any(x["name"] == config.database and x["chronicle"] for x in databases)
    finally:
        await admin.close()


async def test_maintenance_tasks(config):
    admin = Admin(config)
    try:
        job = await admin.maintenance("vacuum", config.database, "chronicle", "entities", {})
        assert (await _wait(admin, job)).state == "done", job.error
        assert job.log[0].startswith("VACUUM")
        job = await admin.maintenance("compress", config.database, "chronicle", "states", {"older_than_days": 0})
        # The test table has no compression set up: TimescaleDB says so, and the task fails cleanly
        await _wait(admin, job)
        assert job.state in ("done", "failed")
        with pytest.raises(AdminError, match="confirm"):
            await admin.maintenance("drop_chunks", config.database, "chronicle", "states", {"older_than_days": 30})
        with pytest.raises(AdminError):
            await admin.maintenance("rm -rf", config.database, None, None, {})
        with pytest.raises(AdminError):
            await admin.maintenance("compress", config.database, "chronicle", "states", {"older_than_days": "1; DROP"})
    finally:
        await admin.close()


async def test_reader_names_are_checked(config):
    admin = Admin(config)
    try:
        for bad in ("postgres", "chronicle_ui", "Robert'); DROP", "pg_x", "ab"):
            with pytest.raises(AdminError):
                await admin.create_reader(bad, [config.database])
    finally:
        await admin.close()


def test_scram_verifier_is_what_postgresql_accepts():
    """The verifier has PostgreSQL's format; the login itself is checked against the server
    in tests/test_admin.py::test_reader_logs_in."""
    from src.admin import scram_verifier

    verifier = scram_verifier("secret")
    method, rest = verifier.split("$", 1)
    iterations_salt, keys = rest.split("$")
    assert method == "SCRAM-SHA-256"
    assert iterations_salt.startswith("4096:")
    assert len(keys.split(":")) == 2
    assert "secret" not in verifier


async def test_reader_logs_in(config):
    """A read-only login can log in with the password it was given, and only reads."""
    admin = Admin(config)
    try:
        reader = await admin.create_reader("test_reader_x", [config.database])
        conn = await asyncpg.connect(
            host=config.host,
            port=config.port,
            user="test_reader_x",
            password=reader["password"],
            database=config.database,
        )
        try:
            assert await conn.fetchval("SELECT count(*) FROM chronicle.entities") == 2
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("DELETE FROM chronicle.entities")
        finally:
            await conn.close()
    finally:
        pool = await admin.pool(config.database)
        await pool.execute("DROP OWNED BY test_reader_x")
        await (await admin.pool("postgres")).execute("DROP ROLE IF EXISTS test_reader_x")
        await admin.close()
