import time
from unittest.mock import AsyncMock

import pytest

from src.bot import BotClient, FreqtradeError
from src.config import BotConfig


def client():
    bot = BotClient(BotConfig("Test", "test", "http://localhost:8080", "u", "p"), 30)
    bot._slow_due = False
    bot.slow_updated = time.time()
    values = {
        "show_config": {"state": "stopped", "max_open_trades": 10},
        "balance": {},
        "status": [{"trade_id": 1, "stake_amount": 5}],
        "count": {"current": 1},
        "locks": {},
    }
    bot.get = AsyncMock(side_effect=lambda path: values[path])
    bot.optional = AsyncMock(return_value=None)
    bot._profit_all = AsyncMock(return_value={"all": {}})
    return bot, values


async def test_stopped_api_has_fresh_independent_state_and_data():
    bot, _ = client()
    await bot.refresh()
    h = bot.summary()["diagnostics"]
    assert bot.connected and h["api_status"] == "reachable"
    assert h["state_updated"] and bot.updated and h["last_attempt"]
    assert h["refresh_duration_ms"] >= 0 and h["stale_after"] == 90
    assert bot.summary()["config"]["state"] == "stopped"


async def test_partial_failure_preserves_data_timestamp_and_confirms_state():
    bot, values = client()
    await bot.refresh()
    before = bot.updated

    async def get(path):
        if path == "balance":
            raise FreqtradeError("balance: HTTP 500")
        return values[path]

    bot.get = get
    with pytest.raises(FreqtradeError):
        await bot.refresh()
    assert bot.api_status == "reachable" and not bot.connected
    assert bot.updated == before and bot.state_updated >= before
    assert "500" in bot.error


@pytest.mark.parametrize("network,expected", [(True, "unreachable"), (False, "error")])
async def test_transport_failure_is_distinct_from_auth_failure(network, expected):
    bot, _ = client()
    await bot.refresh()
    state_at = bot.state_updated

    async def fail(*args):
        if network:
            raise FreqtradeError("timeout") from TimeoutError()
        raise FreqtradeError("credentials refused (401)")

    bot.get = fail
    bot._profit_all = fail
    with pytest.raises(FreqtradeError):
        await bot.refresh()
    assert bot.api_status == expected and not bot.connected
    assert bot.state_updated == state_at


async def test_slow_failure_keeps_current_data_and_recovers():
    bot, _ = client()
    previous = bot.slow_updated
    bot._slow_due = True
    bot._refresh_slow = AsyncMock(side_effect=FreqtradeError("history failed"))
    await bot.refresh()
    assert bot.connected and not bot.error and bot.details_error
    assert bot.slow_updated == previous and bot._slow_due
    bot._refresh_slow = AsyncMock()
    await bot.refresh()
    assert not bot.details_error and not bot._slow_due
    assert bot.slow_updated >= previous


async def test_stopped_count_fallback_does_not_require_other_contribution():
    bot, values = client()

    async def get(path):
        if path == "count":
            raise FreqtradeError("count: trader is not running")
        return values[path]

    bot.get = get
    await bot.refresh()
    assert bot.connected and bot.data["count"]["current"] == 1
