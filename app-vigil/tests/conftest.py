"""Databases for the tests that need one (skipped unless VIGIL_TEST_DB is set).

Start a TimescaleDB with:
    docker run -d --rm --name vigil-test-db -p 127.0.0.1:55433:5432 -e POSTGRES_PASSWORD=test \\
        timescale/timescaledb:2.32.0-pg18
    export VIGIL_TEST_DB=postgresql://postgres:test@127.0.0.1:55433/postgres
Every test gets a database of its own, dropped afterwards; with_timescaledb decides whether the
extension is created in it (a fresh database never has it).
"""

import asyncio
import os
import uuid
from urllib.parse import urlsplit

import asyncpg
import pytest

from src.config import Config
from src.database import Database

TEST_DB = os.environ.get("VIGIL_TEST_DB", "")


def _server() -> dict:
    parts = urlsplit(TEST_DB)
    return {
        "host": parts.hostname or "127.0.0.1",
        "port": parts.port or 5432,
        "user": parts.username or "postgres",
        "password": parts.password or "",
    }


async def _admin() -> asyncpg.Connection:
    if not TEST_DB:
        pytest.skip("VIGIL_TEST_DB is not set")
    try:
        return await asyncpg.connect(TEST_DB, timeout=5)
    except (OSError, asyncpg.PostgresError) as err:
        pytest.skip(f"No test database: {err}")


async def _make_database(timescaledb: bool):
    admin = await _admin()
    name = f"vigil_test_{uuid.uuid4().hex[:8]}"
    # template0: the TimescaleDB image has the extension in template1 already
    await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
    server = _server()
    if timescaledb:
        conn = await asyncpg.connect(database=name, **server)
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        finally:
            await conn.close()
    config = Config(
        db_host=server["host"],
        db_port=server["port"],
        db_user=server["user"],
        db_password=server["password"],
        db_name=name,
    )
    return admin, name, config


async def _connected(config: Config) -> Database:
    database = Database(config)
    task = asyncio.create_task(database.run())
    try:
        await asyncio.wait_for(database.ready.wait(), 30)
    except TimeoutError:
        task.cancel()
        pytest.fail(f"Cannot connect to the test database: {database.last_error}")
    database._test_task = task
    return database


async def _drop(admin: asyncpg.Connection, name: str) -> None:
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()


@pytest.fixture(params=[True, False], ids=["timescaledb", "plain"])
async def database(request):
    """A connected Database with Vigil's schema, with and without TimescaleDB."""
    admin, name, config = await _make_database(request.param)
    db = await _connected(config)
    yield db
    db._test_task.cancel()
    await db.close()
    await _drop(admin, name)


@pytest.fixture
async def empty_server():
    """A database without Vigil's tables (with TimescaleDB), and its config."""
    admin, name, config = await _make_database(True)
    yield config
    await _drop(admin, name)
