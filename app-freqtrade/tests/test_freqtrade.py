"""The app against a stand-in Freqtrade bot (tools/mock_freqtrade.py): reading, events, changes and pages."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.bot import BotClient, FreqtradeError, NotFound, describe_event, plot_columns, trim_candles
from src.config import BotConfig, Config, parse_bots, slugify
from src.database import Database
from src.mqtt import discovery_messages, state_payload
from src.webui import WebUI
from tools import mock_freqtrade


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
def test_bots_are_parsed():
    bots = parse_bots(
        [
            {"name": "Leader Squeeze", "url": "http://10.0.0.5:8080/api/v1/", "username": "u", "password": "p"},
            {"name": "Spot", "url": "https://bot.example:8443", "username": "u", "password": "p", "ws_token": "t"},
        ]
    )
    assert [b.slug for b in bots] == ["leader_squeeze", "spot"]
    assert bots[0].url == "http://10.0.0.5:8080"
    assert bots[0].api == "http://10.0.0.5:8080/api/v1"
    assert bots[0].ws_url == "ws://10.0.0.5:8080/api/v1/message/ws"
    assert bots[1].ws_url == "wss://bot.example:8443/api/v1/message/ws"
    assert bots[1].host == "bot.example:8443"


@pytest.mark.parametrize(
    "bots, message",
    [
        ([{"name": "", "url": "http://a", "username": "u", "password": "p"}], "no name"),
        ([{"name": "A", "url": "ftp://a", "username": "u", "password": "p"}], "http://"),
        ([{"name": "A", "url": "http://a", "username": "u", "password": ""}], "password"),
        (
            [
                {"name": "My bot", "url": "http://a", "username": "u", "password": "p"},
                {"name": "my-bot", "url": "http://b", "username": "u", "password": "p"},
            ],
            "two bots",
        ),
    ],
)
def test_bad_bots_are_refused(bots, message):
    with pytest.raises(ValueError, match=message):
        parse_bots(bots)


def test_no_bots_is_allowed():
    config = Config.from_options({"bots": [], "poll_seconds": 5})
    assert config.bots == []
    assert config.poll_seconds == 10
    assert slugify("Ünïcode Bot 2!") == "n_code_bot_2"


# ---------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------
def test_events_are_described():
    text, level = describe_event(
        {
            "type": "exit_fill",
            "pair": "ETH/USDT",
            "profit_ratio": 0.0123,
            "profit_amount": 1.5,
            "stake_currency": "USDT",
            "exit_reason": "roi",
        }
    )
    assert text == "Exited ETH/USDT +1.23% (1.5 USDT), roi"
    assert level == "ok"
    assert describe_event({"type": "exit_fill", "pair": "X", "profit_ratio": -0.01})[1] == "bad"
    assert (
        describe_event({"type": "entry", "pair": "X", "direction": "Long", "order_rate": 0.000012345678})[0]
        == "Entering X Long at 1.23457e-05"
    )
    assert describe_event({"type": "warning", "status": "Low balance"}) == ("Low balance", "warn")
    assert (
        describe_event({"type": "protection_trigger_global", "reason": "x", "lock_end_time": "t"})[0]
        == "All pairs locked until t: x"
    )


# ---------------------------------------------------------------------
# Against the stand-in bot
# ---------------------------------------------------------------------
@pytest.fixture
async def mock_bot():
    bot = mock_freqtrade.MockBot("TestStrategy", seed=3, futures=True, dry_run=True)
    server = TestServer(mock_freqtrade.make_app(bot))
    await server.start_server()
    yield bot, server
    await server.close()


def client_for(server, tmp_path, **extra) -> BotClient:
    config = BotConfig(
        name="Test",
        slug="test",
        url=str(server.make_url("")).rstrip("/"),
        username=mock_freqtrade.USER,
        password=extra.pop("password", mock_freqtrade.PASSWORD),
        **extra,
    )
    return BotClient(config, 30, Database(tmp_path / "test.db"))


async def started(client: BotClient):
    """The session without the loops, so a test reads when it wants."""
    import aiohttp

    client._session = aiohttp.ClientSession()
    return client


async def test_refresh_reads_everything(mock_bot, tmp_path):
    bot, server = mock_bot
    client = await started(client_for(server, tmp_path))
    try:
        await client.refresh()
        assert client.connected and not client.error
        for key in (
            "config",
            "balance",
            "profit_all",
            "count",
            "status",
            "locks",
            "performance",
            "daily",
            "plot_config",
        ):
            assert client.data.get(key), key
        assert "long" in client.data["profit_all"]
        assert all("orders" not in t for t in client.data["status"])
        assert len(client.trades) == len(bot.trades) == client.trades_total
        assert [t["trade_id"] for t in client.trades] == sorted(t["trade_id"] for t in bot.trades)
        summary = client.summary()
        assert summary["config"]["strategy"] == "TestStrategy"
        assert len(summary["open_trades"]) == len(bot.open)
        assert summary["daily"][0][0] == client.data["daily"]["data"][0]["date"]
        # Only the new trades are read the next time
        bot.trades.append(bot._closed(bot.trades[-1]["close_timestamp"] / 1000 + 60))
        client._slow_due = True
        await client.refresh()
        assert len(client.trades) == len(bot.trades)
        assert client.database.counts()["snapshots"] == 1
    finally:
        await client._session.close()


async def test_wrong_password(mock_bot, tmp_path):
    _, server = mock_bot
    client = await started(client_for(server, tmp_path, password="wrong"))
    try:
        with pytest.raises(FreqtradeError, match="refused"):
            await client.refresh()
    finally:
        await client._session.close()


async def test_old_freqtrade_without_profit_all(mock_bot, tmp_path):
    _, server = mock_bot
    client = await started(client_for(server, tmp_path))
    real_get = client.get

    async def get(path, **params):
        if path == "profit_all":
            raise NotFound("profit_all not found (404)")
        return await real_get(path, **params)

    client.get = get
    try:
        await client.refresh()
        assert set(client.data["profit_all"]) == {"all"}
    finally:
        await client._session.close()


@pytest.mark.parametrize("ws_token", ["", mock_freqtrade.WS_TOKEN])
async def test_websocket_events(mock_bot, tmp_path, ws_token):
    bot, server = mock_bot
    client = client_for(server, tmp_path, ws_token=ws_token)
    heard = []
    client.listeners.append(lambda _bot, event: heard.append(event))
    task = asyncio.create_task(client.run())
    try:
        for _ in range(50):
            if client.ws_connected and bot.sockets and next(iter(bot.sockets)).topics:
                break
            await asyncio.sleep(0.1)
        assert client.ws_connected, client.ws_error
        await bot.broadcast(
            {
                "type": "exit_fill",
                "pair": "ETH/USDT:USDT",
                "profit_ratio": 0.02,
                "profit_amount": 1.0,
                "exit_reason": "roi",
            }
        )
        await bot.broadcast({"type": "new_candle", "data": ["ETH/USDT:USDT", "15m"]})  # not subscribed
        for _ in range(30):
            if heard:
                break
            await asyncio.sleep(0.1)
        assert [e["type"] for e in heard] == ["exit_fill"]
        assert client.events[0]["level"] == "ok"
        assert client.database.events("test")[0]["pair"] == "ETH/USDT:USDT"
        assert client._wake.is_set() or client._slow_due
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_events_survive_a_restart(tmp_path):
    database = Database(tmp_path / "x.db")
    config = BotConfig("A", "a", "http://a", "u", "p")
    first = BotClient(config, 30, database)
    first.on_event({"type": "warning", "status": "one"})
    first.on_event({"type": "warning", "status": "two"})
    again = BotClient(config, 30, database)
    assert [e["text"] for e in again.events] == ["two", "one"]


# ---------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------
def test_candles_keep_only_what_is_drawn():
    plot = {"main_plot": {"ema": {}, "bb_low": {"fill_to": "bb_up"}}, "subplots": {"RSI": {"rsi": {"color": "red"}}}}
    assert plot_columns(plot) == ["ema", "bb_low", "bb_up", "rsi"]
    answer = {
        "pair": "X/USDT",
        "timeframe": "5m",
        "columns": [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "ema",
            "bb_low",
            "bb_up",
            "rsi",
            "junk",
            "extra",
            "__date_ts",
        ],
        "all_columns": [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "ema",
            "bb_low",
            "bb_up",
            "rsi",
            "junk",
            "extra",
            "enter_long",
        ],
        "data": [["d", 1, 2, 0.5, 1.5, 10, 1.1, 0.9, 1.9, 55, "x", 7, 1000]],
        "enter_long_signals": 2,
    }
    trimmed = trim_candles(answer, plot, ["extra"])
    assert "junk" not in trimmed["columns"] and "date" not in trimmed["columns"]
    assert trimmed["columns"][-1] == "__date_ts"
    assert trimmed["data"] == [[1, 2, 0.5, 1.5, 10, 1.1, 0.9, 1.9, 55, 7, 1000]]
    assert (
        "junk" in trimmed["available"]
        and "open" not in trimmed["available"]
        and "enter_long" not in trimmed["available"]
    )
    assert trimmed["signals"]["enter_long"] == 2


# ---------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------
async def test_mqtt_payload_and_discovery(mock_bot, tmp_path):
    _, server = mock_bot
    client = await started(client_for(server, tmp_path))
    try:
        await client.refresh()
    finally:
        await client._session.close()
    payload = state_payload(client)
    assert payload["state"] == "running" and payload["mode"] == "dry-run"
    assert isinstance(payload["balance"], float) and 0 <= payload["winrate"] <= 100
    messages = dict(discovery_messages(client, "homeassistant", "freqtrade"))
    assert messages["homeassistant/sensor/freqtrade_test/balance/config"]["unit_of_measurement"] == "USDT"
    assert "exit_fill" in messages["homeassistant/event/freqtrade_test/events/config"]["event_types"]
    json.dumps(messages)


# ---------------------------------------------------------------------
# The dashboard's API
# ---------------------------------------------------------------------
@pytest.fixture
async def dashboard(mock_bot, tmp_path):
    _, server = mock_bot
    bot_client = await started(client_for(server, tmp_path))
    await bot_client.refresh()
    config = Config(bots=[bot_client.config])
    webui = WebUI(0, config, bot_client.database, [bot_client])
    client = TestClient(TestServer(webui.app))
    await client.start_server()
    yield client, config, bot_client
    await client.close()
    await bot_client._session.close()


async def test_pages(dashboard):
    client, _, _ = dashboard
    status = await (await client.get("/api/status")).json()
    assert status["bots"][0]["connected"]
    assert "password" not in json.dumps(status["config"])
    assert (await client.get("/api/bot/test")).status == 200
    assert (await client.get("/api/bot/nope")).status == 404
    trades = await (await client.get("/api/bot/test/trades")).json()
    assert trades["total"] == len(trades["trades"])
    candles = await (await client.get("/api/bot/test/candles", params={"pair": "ETH/USDT:USDT", "limit": "60"})).json()
    assert len(candles["data"]) == 60 and "rsi" in candles["columns"]
    page = await (await client.get("/")).text()
    assert "__VERSION__" not in page


async def test_changes(dashboard):
    client, config, bot_client = dashboard
    assert (await client.post("/api/bot/test/control", data="action=stop")).status == 415
    assert (await client.post("/api/bot/test/control", json={"action": "rm -rf"})).status == 400
    response = await client.post("/api/bot/test/control", json={"action": "pause"})
    assert response.status == 200
    assert (await client.post("/api/bot/test/forceexit", json={"trade_id": 99999})).status == 400
    trade_id = bot_client.data["status"][0]["trade_id"]
    assert (await client.post("/api/bot/test/forceexit", json={"trade_id": trade_id})).status == 200
    assert (await client.post("/api/data/delete", json={"confirm": "yes"})).status == 400
    assert (await client.post("/api/data/delete", json={"confirm": "DELETE"})).status == 200
    config.read_only = True
    assert (await client.post("/api/bot/test/control", json={"action": "start"})).status == 403
    assert (await client.post("/api/bot/test/forceexit", json={"trade_id": trade_id})).status == 403


def test_startup_messages_are_plain_text():
    text, _ = describe_event({"type": "startup", "status": "*Exchange:* `binance`\n*Strategy:* `OPT7_NFILite`"})
    assert text == "Exchange: binance · Strategy: OPT7_NFILite"
    text, _ = describe_event(
        {
            "type": "startup",
            "status": "Pairs from [{'VolumePairList': 'VolumePairList - top 200 volume pairs.'}, {'AgeFilter': 'AgeFilter - min 10 days'}]",
        }
    )
    assert text == "Pairs from VolumePairList - top 200 volume pairs.; AgeFilter - min 10 days"
