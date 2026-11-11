"""A TimescaleDB with a small Chronicle history for the tests that need a database.

Start one with:
    docker run -d --name chronicle-test-db -p 55432:5432 -e POSTGRES_PASSWORD=test \\
        timescale/timescaledb:latest-pg18
Those tests are skipped when it isn't running.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.config import Config

HOST = os.environ.get("CHRONICLE_TEST_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHRONICLE_TEST_PORT", "55432"))
PASSWORD = os.environ.get("CHRONICLE_TEST_PASSWORD", "test")

# The tables of the Chronicle integration (layout version 1), without its policies
SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb",
    "CREATE SCHEMA chronicle",
    "CREATE TABLE chronicle.meta (key text PRIMARY KEY, value text NOT NULL)",
    """CREATE TABLE chronicle.entities (
        id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, entity_id text NOT NULL UNIQUE,
        domain text GENERATED ALWAYS AS (split_part(entity_id, '.', 1)) STORED,
        name text, unit text, device_class text, state_class text,
        first_seen timestamptz NOT NULL DEFAULT now(), updated timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE chronicle.attributes (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, hash bigint NOT NULL UNIQUE, data jsonb NOT NULL)""",
    """CREATE TABLE chronicle.states (
        time timestamptz NOT NULL, entity integer NOT NULL, state text, value double precision, attributes bigint)""",
    "SELECT create_hypertable('chronicle.states', by_range('time', INTERVAL '1 day'))",
    "CREATE INDEX ON chronicle.states (entity, time DESC)",
    """CREATE VIEW chronicle.history AS
        SELECT s.time, e.entity_id, COALESCE(s.state, s.value::text) AS state, s.value, a.data AS attributes
        FROM chronicle.states s JOIN chronicle.entities e ON e.id = s.entity
        LEFT JOIN chronicle.attributes a ON a.id = s.attributes""",
    "INSERT INTO chronicle.meta VALUES ('schema_version', '1')",
]


async def _admin():
    try:
        return await asyncpg.connect(
            host=HOST, port=PORT, user="postgres", password=PASSWORD, database="postgres", timeout=3
        )
    except (OSError, asyncpg.PostgresError) as err:
        pytest.skip(f"No test database: {err}")


@pytest.fixture
async def config():
    """An empty database with the Chronicle tables and two days of history; dropped afterwards."""
    admin = await _admin()
    name = f"chronicle_app_{uuid.uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(host=HOST, port=PORT, user="postgres", password=PASSWORD, database=name)
    try:
        for statement in SCHEMA:
            await conn.execute(statement)
        await conn.execute(
            """INSERT INTO chronicle.entities (entity_id, name, unit, state_class) VALUES
               ('sensor.temperature', 'Temperature', '°C', 'measurement'),
               ('light.desk', 'Desk', NULL, NULL)"""
        )
        await conn.execute("""INSERT INTO chronicle.attributes (hash, data) VALUES (1, '{"friendly_name": "Desk"}')""")
        now = datetime.now(UTC)
        rows = [(now - timedelta(minutes=10 * i), 1, None, 20.0 + (i % 7) / 2, None) for i in range(288)]
        rows += [(now - timedelta(hours=h), 2, "on" if h % 2 else "off", float(h % 2), 1) for h in range(48)]
        await conn.copy_records_to_table(
            "states", schema_name="chronicle", columns=["time", "entity", "state", "value", "attributes"], records=rows
        )
    finally:
        await conn.close()
    yield Config(host=HOST, port=PORT, username="postgres", password=PASSWORD, database=name)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()


@pytest.fixture
async def empty_config():
    """A database without the Chronicle tables."""
    admin = await _admin()
    name = f"chronicle_app_{uuid.uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    yield Config(host=HOST, port=PORT, username="postgres", password=PASSWORD, database=name)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()


@pytest.fixture
async def fresh_config():
    """A history of one hour, as right after installing the integration: a power sensor every
    30 s with an unavailable stretch, and a tracker whose GPS updates only change attributes."""
    admin = await _admin()
    name = f"chronicle_app_{uuid.uuid4().hex[:8]}"
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(host=HOST, port=PORT, user="postgres", password=PASSWORD, database=name)
    try:
        for statement in SCHEMA:
            await conn.execute(statement)
        await conn.execute(
            """INSERT INTO chronicle.entities (entity_id, name, unit, state_class) VALUES
               ('sensor.power', 'Power', 'W', 'measurement'),
               ('device_tracker.phone', 'Phone', NULL, NULL)"""
        )
        start = datetime.now(UTC) - timedelta(hours=1)
        rows = []
        for i in range(120):
            unavailable = 60 <= i < 65
            rows.append(
                (
                    start + timedelta(seconds=30 * i),
                    1,
                    "unavailable" if unavailable else None,
                    None if unavailable else 300.0 + i,
                    None,
                )
            )
        for i in range(12):
            rows.append((start + timedelta(minutes=5 * i), 2, "not_home" if i >= 8 else "home", None, None))
        await conn.copy_records_to_table(
            "states", schema_name="chronicle", columns=["time", "entity", "state", "value", "attributes"], records=rows
        )
    finally:
        await conn.close()
    yield Config(host=HOST, port=PORT, username="postgres", password=PASSWORD, database=name)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()
