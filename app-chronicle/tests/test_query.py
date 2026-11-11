"""The Query page: read only, one statement, at most QUERY_ROWS rows; the examples run."""

import json
import re
from pathlib import Path

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


EXAMPLES = json.loads((Path(__file__).resolve().parent.parent / "src" / "web" / "query-examples.json").read_text())
AGGREGATES = [
    """CREATE MATERIALIZED VIEW chronicle.states_hourly WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
       SELECT time_bucket(INTERVAL '1 hour', time) AS bucket, entity, avg(value) AS mean, min(value) AS min,
              max(value) AS max, last(value, time) AS last, count(*) AS samples
       FROM chronicle.states WHERE value IS NOT NULL GROUP BY bucket, entity WITH NO DATA""",
    """CREATE MATERIALIZED VIEW chronicle.states_daily WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
       SELECT time_bucket(INTERVAL '1 day', bucket, 'Europe/Amsterdam') AS bucket, entity,
              sum(mean * samples) / sum(samples) AS mean, min(min) AS min, max(max) AS max,
              last(last, bucket) AS last, sum(samples)::bigint AS samples
       FROM chronicle.states_hourly GROUP BY 1, entity WITH NO DATA""",
    "INSERT INTO chronicle.meta VALUES ('aggregate_time_zone', 'Europe/Amsterdam')",
]


def test_examples_are_described():
    assert len(EXAMPLES) >= 12
    for example in EXAMPLES:
        assert example["title"] and example["sql"]
        assert 20 < len(example["description"]) < 400
        # Every placeholder is explained in the description
        for placeholder in re.findall(r"<[a-z_]+>", example["sql"]):
            assert placeholder in example["description"], (example["title"], placeholder)
        check_query(example["sql"])


async def test_examples_run(config):
    conn = await asyncpg.connect(
        host=config.host, port=config.port, user=config.username, password=config.password, database=config.database
    )
    try:
        for statement in AGGREGATES:
            await conn.execute(statement)
    finally:
        await conn.close()
    db = Database(config)
    try:
        for example in EXAMPLES:
            entity = "light.desk" if "on per day" in example["title"] else "sensor.temperature"
            sql = example["sql"].replace("<entity_id>", entity).replace("<attribute>", "friendly_name")
            result = await db.query(sql)
            assert result["columns"], example["title"]
        silent = await db.query(next(e["sql"] for e in EXAMPLES if e["title"] == "Entities that went silent"))
        hours = await db.query(
            next(e["sql"] for e in EXAMPLES if e["title"] == "Hours on per day").replace("<entity_id>", "light.desk")
        )
    finally:
        await db.close()
    # The light changed every hour for two days: on half of the time
    assert silent["rows"] == []
    assert sum(r[1] for r in hours["rows"]) == pytest.approx(24, abs=1.5)
