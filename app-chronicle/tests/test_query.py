"""The SQL page: scripts in a session, changes after a confirmation, transactions, and the
examples (which must run against a Chronicle database)."""

import json
import re
from pathlib import Path

import asyncpg
import pytest

from src.sql import RESULT_ROWS, SqlSessions


async def _count(config, table):
    conn = await asyncpg.connect(
        host=config.host, port=config.port, user=config.username, password=config.password, database=config.database
    )
    try:
        return await conn.fetchval(f"SELECT count(*) FROM {table}")
    finally:
        await conn.close()


async def _run(sessions, config, script, session=None, **kw):
    kw = {"autocommit": True, "timeout": 30, "confirmed": True, **kw}
    return await sessions.run(session, config.database, script, **kw)


async def test_changes_wait_for_a_confirmation(config):
    sessions = SqlSessions(config)
    try:
        asked = await _run(
            sessions, config, "SELECT 1; DELETE FROM chronicle.states; DROP TABLE chronicle.meta", confirmed=False
        )
        assert asked == {"confirm": [{"kind": "DELETE", "count": 1}, {"kind": "DROP TABLE", "count": 1}]}
        # Nothing ran
        assert await _count(config, "chronicle.states") > 0
        # Reading needs no confirmation
        result = await _run(sessions, config, "SELECT count(*) FROM chronicle.entities", confirmed=False)
        assert result["results"][0]["rows"] == [[2]]
    finally:
        await sessions.close_all()


async def test_a_script_runs_statement_by_statement(config):
    sessions = SqlSessions(config)
    try:
        result = await _run(
            sessions,
            config,
            "CREATE TEMP TABLE t (a int); INSERT INTO t VALUES (1), (2); SELECT a FROM t ORDER BY a; SELECT 'x;y' AS s",
        )
        statuses = [r["status"] for r in result["results"]]
        assert statuses == ["CREATE TABLE", "INSERT 0 2", "2 rows", "1 rows"]
        assert result["results"][2]["rows"] == [[1], [2]]
        assert result["results"][3]["rows"] == [["x;y"]]
        assert result["error"] is None
    finally:
        await sessions.close_all()


async def test_an_error_stops_the_script_with_its_position(config):
    sessions = SqlSessions(config)
    try:
        script = "SELECT 1;\nSELECT nope FROM chronicle.entities;\nSELECT 3"
        result = await _run(sessions, config, script)
        assert len(result["results"]) == 1
        error = result["error"]
        assert error["statement"] == 1 and error["sqlstate"] == "42703"
        # The position is in the whole script, at "nope"
        assert script[error["position"] - 1 :].startswith("nope")
    finally:
        await sessions.close_all()


async def test_auto_commit_off_keeps_a_transaction_open(config):
    sessions = SqlSessions(config)
    before = await _count(config, "chronicle.states")
    try:
        result = await _run(sessions, config, "DELETE FROM chronicle.states", autocommit=False)
        assert result["in_transaction"] is True
        session = sessions.get(result["session"])
        # Another connection doesn't see it yet
        assert await _count(config, "chronicle.states") == before
        # The same session does, in a next run
        again = await _run(sessions, config, "SELECT count(*) FROM chronicle.states", session=session)
        assert again["results"][0]["rows"] == [[0]]
        state = await sessions.end(session, commit=False)
        assert state["in_transaction"] is False
        assert await _count(config, "chronicle.states") == before
        # BEGIN in the script itself works the same, and Commit saves it
        result = await _run(sessions, config, "BEGIN; DELETE FROM chronicle.meta", session=session)
        assert result["in_transaction"] is True
        await sessions.end(session, commit=True)
        assert await _count(config, "chronicle.meta") == 0
    finally:
        await sessions.close_all()


async def test_a_failed_transaction_rolls_back(config):
    sessions = SqlSessions(config)
    try:
        result = await _run(sessions, config, "BEGIN; SELECT 1/0")
        assert result["error"]["sqlstate"] == "22012"
        session = sessions.get(result["session"])
        # Until a rollback, PostgreSQL ignores the rest; ROLLBACK in the script works too
        result = await _run(sessions, config, "ROLLBACK; SELECT 2", session=session)
        assert result["error"] is None and result["in_transaction"] is False
        assert result["results"][1]["rows"] == [[2]]
    finally:
        await sessions.close_all()


async def test_another_database_needs_the_transaction_ended(config, empty_config):
    sessions = SqlSessions(config)
    try:
        result = await _run(sessions, config, "BEGIN")
        session = sessions.get(result["session"])
        with pytest.raises(Exception, match="commit or roll it back"):
            await sessions.run(session, empty_config.database, "SELECT 1", autocommit=True, timeout=30, confirmed=True)
        await sessions.end(session, commit=False)
        moved = await sessions.run(
            session, empty_config.database, "SELECT current_database()", autocommit=True, timeout=30, confirmed=True
        )
        assert moved["results"][0]["rows"] == [[empty_config.database]]
    finally:
        await sessions.close_all()


async def test_rows_are_capped_and_become_json(config):
    sessions = SqlSessions(config)
    try:
        result = await _run(sessions, config, f"SELECT g FROM generate_series(1, {RESULT_ROWS + 500}) g")
        assert len(result["results"][0]["rows"]) == RESULT_ROWS
        assert result["results"][0]["truncated"] is True
        values = await _run(
            sessions,
            config,
            "SELECT now() AS t, 1.5::numeric AS n, '{\"a\": 1}'::jsonb AS j, interval '2 hours' AS i, NULL AS x",
        )
        t, n, j, i, x = values["results"][0]["rows"][0]
        assert isinstance(t, str) and "T" in t
        assert (n, j, i, x) == (1.5, '{"a": 1}', "2:00:00", None)
    finally:
        await sessions.close_all()


async def test_notices_are_shown(config):
    sessions = SqlSessions(config)
    try:
        result = await _run(sessions, config, "DO $$ BEGIN RAISE NOTICE 'hello %', 42; END $$")
        assert result["notices"] == ["NOTICE: hello 42"]
    finally:
        await sessions.close_all()


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


async def test_examples_run(config):
    conn = await asyncpg.connect(
        host=config.host, port=config.port, user=config.username, password=config.password, database=config.database
    )
    try:
        for statement in AGGREGATES:
            await conn.execute(statement)
    finally:
        await conn.close()
    sessions = SqlSessions(config)
    try:
        for example in EXAMPLES:
            entity = "light.desk" if "value = 1" in example["sql"] else "sensor.temperature"
            text = example["sql"].replace("<entity_id>", entity).replace("<attribute>", "friendly_name")
            # The examples only read: they run without a confirmation
            result = await _run(sessions, config, text, confirmed=False)
            assert "confirm" not in result, example["title"]
            assert result["error"] is None, (example["title"], result["error"])
            assert result["results"][-1]["columns"], example["title"]
        silent = await _run(
            sessions, config, next(e["sql"] for e in EXAMPLES if e["title"] == "Entities that went silent")
        )
        hours = await _run(
            sessions,
            config,
            next(e["sql"] for e in EXAMPLES if e["title"] == "Hours on per day").replace("<entity_id>", "light.desk"),
        )
    finally:
        await sessions.close_all()
    # The light changed every hour for two days: on half of the time
    assert silent["results"][0]["rows"] == []
    assert sum(r[1] for r in hours["results"][0]["rows"]) == pytest.approx(24, abs=1.5)
