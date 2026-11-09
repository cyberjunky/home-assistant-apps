"""The app as a whole: it must stay up until told to stop, and fail loudly when a service fails."""

import asyncio
import os
import signal

import pytest

from src import __main__ as main
from src import custom_templates
from src.config import Config


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "WEB_PORT", 0)
    monkeypatch.setattr(custom_templates, "CUSTOM_DIR", tmp_path / "custom")
    monkeypatch.setattr(main.Config, "from_options_file", classmethod(lambda cls: Config(update_templates=False)))
    return tmp_path


async def test_stays_up_until_stopped(app_env):
    task = asyncio.create_task(main._amain())
    await asyncio.sleep(0.5)
    assert not task.done(), "the app must not stop on its own"
    assert (app_env / "nuclei.db").exists()
    os.kill(os.getpid(), signal.SIGTERM)
    assert await asyncio.wait_for(task, 5) == 0


async def test_a_failing_service_ends_the_app_with_an_error(app_env, monkeypatch):
    async def broken(self):
        raise OSError("address in use")

    monkeypatch.setattr(main.WebUI, "start", broken)
    assert await asyncio.wait_for(main._amain(), 5) == 2


async def test_bad_options_end_the_app(monkeypatch):
    def fail(cls):
        raise ValueError("bad options")

    monkeypatch.setattr(main.Config, "from_options_file", classmethod(fail))
    assert await main._amain() == 1
