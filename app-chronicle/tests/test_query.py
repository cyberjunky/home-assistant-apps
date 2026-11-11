"""The Query page: read only, one statement, at most QUERY_ROWS rows."""

import asyncpg
import pytest

from src.database import QUERY_ROWS, Database, QueryRefusedError, check_query


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "  select * from chronicle.history",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "-- comment\nSELECT 1",
        "/* comment */ TABLE chronicle.entities",
        "EXPLAIN SELECT 1",
        "SHOW timezone",
        "(SELECT 1)",
        "VALUES (1)",
    ],
)
def test_reading_statements_pass(sql):
    check_query(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM chronicle.states",
        "DROP TABLE chronicle.states",
        "UPDATE chronicle.entities SET name = 'x'",
        "INSERT INTO chronicle.meta VALUES ('a', 'b')",
        "TRUNCATE chronicle.states",
        "SET ROLE postgres",
        "COPY chronicle.states TO PROGRAM 'id'",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('role', 'postgres', true)",
        "SELECT pg_terminate_backend(1)",
        "SELECT lo_export(1, '/tmp/x')",
        "SELECT U&\"\\0070g_read_file\"('/etc/passwd')",
        "/* SELECT */ DELETE FROM chronicle.states",
        "",
    ],
)
def test_other_statements_are_refused(sql):
    with pytest.raises(QueryRefusedError):
        check_query(sql)


async def _count(config, table):
    conn = await asyncpg.connect(
        host=config.host, port=config.port, user=config.username, password=config.password, database=config.database
    )
    try:
        return await conn.fetchval(f"SELECT count(*) FROM {table}")
    finally:
        await conn.close()


async def test_writes_fail_and_change_nothing(config):
    db = Database(config)
    before = await _count(config, "chronicle.states")
    try:
        # A writing CTE passes the first-word rule: PostgreSQL's read-only transaction stops it
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await db.query("WITH d AS (DELETE FROM chronicle.states RETURNING 1) SELECT count(*) FROM d")
        with pytest.raises(QueryRefusedError):
            await db.query("DROP TABLE chronicle.states")
        with pytest.raises(asyncpg.PostgresSyntaxError):
            await db.query("SELECT 1; DROP TABLE chronicle.states")
        # The settings of one query don't leak into the next
        with pytest.raises(QueryRefusedError):
            await db.query("SELECT set_config('default_transaction_read_only', 'off', false)")
    finally:
        await db.close()
    assert await _count(config, "chronicle.states") == before
    assert await _count(config, "chronicle.entities") == 2


async def test_superuser_queries_as_reader(config):
    db = Database(config)
    try:
        result = await db.query("SELECT current_user")
    finally:
        await db.close()
    assert result["rows"] == [["pg_read_all_data"]]


async def test_rows_are_capped(config):
    db = Database(config)
    try:
        result = await db.query(f"SELECT g FROM generate_series(1, {QUERY_ROWS + 500}) g")
        small = await db.query("SELECT entity_id, name FROM chronicle.entities ORDER BY entity_id")
    finally:
        await db.close()
    assert len(result["rows"]) == QUERY_ROWS
    assert result["truncated"] is True
    assert small["truncated"] is False
    assert small["columns"] == [{"name": "entity_id", "type": "text"}, {"name": "name", "type": "text"}]
    assert small["rows"] == [["light.desk", "Desk"], ["sensor.temperature", "Temperature"]]


async def test_values_become_json(config):
    db = Database(config)
    try:
        result = await db.query(
            "SELECT now() AS t, 1.5::numeric AS n, '{\"a\": 1}'::jsonb AS j, interval '2 hours' AS i, NULL AS x"
        )
    finally:
        await db.close()
    t, n, j, i, x = result["rows"][0]
    assert isinstance(t, str) and "T" in t
    assert n == 1.5
    assert j == '{"a": 1}'
    assert i == "2:00:00"
    assert x is None
