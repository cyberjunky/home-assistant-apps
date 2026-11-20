import time
from unittest.mock import AsyncMock

import pytest

from src.bot import BotClient, FreqtradeError
from src.config import BotConfig


@pytest.mark.parametrize(
    "state,error,accepted",
    [
        ("stopped", "count: trader is not running", True),
        ("running", "count: trader is not running", False),
        ("stopped", "count: authentication refused", False),
    ],
)
async def test_stopped_trader_is_not_an_offline_api(state, error, accepted):
    client = BotClient(BotConfig("Test", "test", "http://localhost:8080", "u", "p"), 30, None)
    client._slow_due = False
    client.slow_updated = time.time()
    values = {
        "show_config": {"state": state, "max_open_trades": 10},
        "balance": {"total_bot": 1234},
        "status": [{"trade_id": 1, "stake_amount": 100}],
        "locks": {},
    }

    async def get(endpoint):
        if endpoint == "count":
            raise FreqtradeError(error)
        return values[endpoint]

    client.get = get
    client.optional = AsyncMock(return_value={})
    client._profit_all = AsyncMock(return_value={"all": {"profit_all_coin": 50}})
    if not accepted:
        with pytest.raises(FreqtradeError, match=error):
            await client.refresh()
        assert not client.connected
        return
    await client.refresh()
    assert client.connected and not client.error
    assert client.data["config"]["state"] == "stopped"
    assert client.data["balance"]["total_bot"] == 1234
    assert client.data["count"] == {"current": 1, "max": 10, "total_stake": 100}
    assert len(client.data["status"]) == 1
