"""Shared fixtures: a temporary database and an account signed in to the made-up Garmin account."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from fake_garmin import FakeGarmin

from src import collector as collector_module
from src.account import GarminAccount
from src.collector import Collector
from src.config import Config
from src.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "garmin.db")
    yield database
    database.close()


@pytest.fixture
def account(tmp_path):
    return GarminAccount(token_dir=tmp_path / "garmin", factory=FakeGarmin)


@pytest.fixture
async def signed_in(account):
    assert await account.login("someone@example.com", "secret") == "ok"
    return account


@pytest.fixture
def collector(db, signed_in, monkeypatch):
    monkeypatch.setattr(collector_module, "PAUSE_BETWEEN_REQUESTS", 0)
    # A short history keeps the tests quick: the last 60 days
    from datetime import date, timedelta

    config = Config(start_date=(date.today() - timedelta(days=59)).isoformat())
    return Collector(config, db, signed_in)
