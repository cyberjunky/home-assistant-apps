import json
import math
import time

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import odds
from src.config import Config, MinerConfig, parse_miners
from src.database import Database
from src.firmware import REPO_ESP_MINER, REPO_NERDQAXE, Firmware, repo_for
from src.miner import Miner, MinerError, chips, normalize, pause_mode
from src.mqtt import MqttPublisher, discovery_messages, miner_payload, totals_payload
from src.network import Network, summarize
from src.stats import miner_odds, totals
from src.webui import WebUI
from tools.mock_miner import Fake, make_app

# --- the odds -----------------------------------------------------------


def test_difficulty_parsing_and_formatting():
    assert odds.parse_difficulty("4.29G") == pytest.approx(4.29e9)
    assert odds.parse_difficulty("12.5k") == pytest.approx(12500)
    assert odds.parse_difficulty(123456) == 123456
    assert odds.parse_difficulty(True) is None
    assert odds.parse_difficulty("nonsense") is None
    assert odds.parse_difficulty(float("nan")) is None
    assert odds.format_difficulty(4.21e9) == "4.21G"
    assert odds.format_difficulty(149.3e12) == "149.3T"
    assert odds.format_difficulty(None) is None


def test_subsidy_halves():
    assert odds.subsidy(0) == 50
    assert odds.subsidy(840_000) == 3.125
    assert odds.subsidy(1_050_000) == 1.5625
    assert odds.subsidy(None) is None


def test_block_chance():
    difficulty = 150e12
    # A miner with the whole network's hash rate finds every block
    network = odds.network_hashrate(difficulty)
    assert odds.block_chance(network, difficulty) == pytest.approx(1)
    hashrate = 6e12
    per_block = odds.block_chance(hashrate, difficulty)
    assert per_block == pytest.approx(hashrate * 600 / (difficulty * 2**32))
    # Small chances add up over the draws
    assert odds.chance_within(per_block, 144) == pytest.approx(144 * per_block, rel=1e-4)
    assert odds.expected_years(hashrate, difficulty) == pytest.approx(1 / (per_block * 52_560))
    assert odds.block_chance(0, difficulty) is None
    assert odds.block_chance(hashrate, None) is None
    assert odds.one_in(0.25) == 4


def test_share_odds_and_luck():
    hashrate = 1e12
    # A share of difficulty 1 takes 2^32 hashes
    assert odds.seconds_to_share(hashrate, 1e9) == pytest.approx(1e9 * 2**32 / hashrate)
    assert odds.share_chance(hashrate, 1e9, 0) == 0
    assert odds.share_chance(hashrate, 1e9, 1e9) == pytest.approx(1 - math.exp(-1e9 * hashrate / (1e9 * 2**32)))
    hashes = 2**32 * 1e9
    assert odds.typical_best(hashes) == pytest.approx(1e9 / math.log(2))
    assert odds.luck(1e9 / math.log(2), hashes) == pytest.approx(1)
    assert odds.luck(None, hashes) is None


# --- the configuration ----------------------------------------------------


def test_miners_from_the_options():
    miners = parse_miners(
        [{"name": "NerdQAxe+", "host": "http://192.168.1.62/"}, {"name": "Lucky LV08", "host": "lv08.local:80"}]
    )
    assert [(m.name, m.slug, m.host) for m in miners] == [
        ("NerdQAxe+", "nerdqaxe", "192.168.1.62"),
        ("Lucky LV08", "lucky_lv08", "lv08.local:80"),
    ]
    assert miners[0].url == "http://192.168.1.62"
    with pytest.raises(ValueError, match="two miners"):
        parse_miners([{"name": "A b", "host": "1.2.3.4"}, {"name": "a-b", "host": "1.2.3.5"}])
    with pytest.raises(ValueError, match="no name"):
        parse_miners([{"name": " ", "host": "1.2.3.4"}])
    with pytest.raises(ValueError, match="not an IP address"):
        parse_miners([{"name": "x", "host": "1.2.3.4 5"}])
    config = Config.from_options({"miners": [], "poll_seconds": 1})
    assert config.poll_seconds == 5 and config.mqtt_base_topic == "lottery"


# --- what a miner reports ---------------------------------------------------


def test_axeos_values():
    info = Fake("lv08").info()
    v = normalize(info)
    assert v["mining"] is True and pause_mode(info) == "pause"
    assert v["pool"] == "solo.ckpool.org:3333" and v["using_fallback"] is False
    assert v["current"] == pytest.approx(info["current"] / 1000, abs=0.01)
    assert v["efficiency"] == pytest.approx(info["power"] / (info["hashRate"] / 1000), abs=0.01)
    assert v["board"] == "LV08" and v["asic_model"] == "BM1366"
    asics = chips(info)
    assert len(asics) == 4 and {"hashrate", "errors", "temp"} <= asics[0].keys()


def test_nerdqaxe_values():
    info = Fake("nerdqaxe").info()
    info["chips"] = [{"hashRate": 600.0, "errorHashRate": 2.5}, {"hashRate": 610.0, "errorHashRate": 0}]
    v = normalize(info)
    assert pause_mode(info) == "shutdown" and v["mining"] is True
    assert v["model"] == "NerdQAxe+" and v["pool_connected"] is True
    assert v["blocks_found"] == 0 and v["reset_reason"] == "POWERON"
    asics = chips(info)
    assert asics[0]["hashrate"] == 602.5 and asics[0]["error_hashrate"] == 2.5
    # Four temperatures, two chips in the dashboard: the temperatures still get a card each
    assert len(asics) == 4 and asics[3] == {"temp": info["asicTemps"][3]}


def test_older_firmware_values():
    v = normalize({"bestDiff": "4.29G", "foundBlocks": 1, "recentpingloss": 0.25, "fanCount": 2, "fanrpm2": 3000})
    assert v["best_diff"] == pytest.approx(4.29e9)
    assert v["blocks_found"] == 1 and v["ping_loss"] == 25.0 and v["fan2_rpm"] == 3000
    assert pause_mode({}) is None and "mining" not in v


# --- a miner over its API -----------------------------------------------------


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "lottery.db")
    yield db
    db.close()


async def served(fake: Fake):
    server = TestServer(make_app(fake))
    await server.start_server()
    return server


async def test_polling_counts_and_notices(database):
    fake = Fake("lv08")
    server = await served(fake)
    miner = Miner(MinerConfig("Lucky LV08", "lucky_lv08", f"127.0.0.1:{server.port}"), 15, database)
    try:
        async with aiohttp.ClientSession() as session:
            await miner.poll(session)
            assert miner.connected and miner.values["hashrate"] > 0
            assert database.bests()[0]["diff"] == fake.best_session
            # The next reading: work and energy are counted over the time between
            miner._last_poll -= 15
            fake.best_session *= 10
            await miner.poll(session)
            assert miner.total_hashes == pytest.approx(miner.values["hashrate"] * 1e9 * 15, rel=0.1)
            assert miner.energy_kwh > 0
            assert len(database.bests()) == 2
            # Paused, then restarted
            await miner.set_mining(session, False)
            await miner.poll(session)
            assert miner.values["mining"] is False
            await miner.set_mining(session, True)
            fake.down_until = 0
            fake.started = time.time()
            await miner.poll(session)
            # The new session starts from the last reading
            assert 0 < miner.session_hashes < miner.total_hashes / 2
        kinds = [e["kind"] for e in database.events()]
        assert {"paused", "resumed", "restart"} <= set(kinds)
        day = database.days("2000-01-01")[0]
        assert day["hashes"] > 0 and day["best"] == max(b["diff"] for b in database.bests())
    finally:
        await server.close()


async def test_nerdqaxe_pauses_by_shutting_down(database):
    fake = Fake("nerdqaxe")
    server = await served(fake)
    miner = Miner(MinerConfig("NerdQAxe+", "nerdqaxe", f"127.0.0.1:{server.port}"), 15, database)
    try:
        async with aiohttp.ClientSession() as session:
            await miner.poll(session)
            assert len(miner.info["chips"]) == 4
            await miner.set_mining(session, False)
            assert fake.paused
            # Resuming restarts it
            await miner.set_mining(session, True)
            assert not fake.paused and fake.down_until > 0
            await miner.poll(session)
            assert not miner.connected and "503" in miner.error
    finally:
        await server.close()


async def test_unreachable_miner(database):
    miner = Miner(MinerConfig("Gone", "gone", "127.0.0.1:9"), 15, database)
    async with aiohttp.ClientSession() as session:
        await miner.poll(session)
        with pytest.raises(MinerError):
            await miner.restart(session)
    assert not miner.connected and miner.error
    assert database.events()[0]["kind"] == "offline"


async def test_counters_survive_a_restart_of_the_app(database):
    miner = Miner(MinerConfig("A", "a", "127.0.0.1:9"), 15, database)
    miner.total_hashes, miner.energy_kwh, miner.snapshot = 1e15, 2.5, {"hashRate": 1000, "miningPaused": False}
    miner.save()
    again = Miner(MinerConfig("A", "a", "127.0.0.1:9"), 15, database)
    assert again.total_hashes == 1e15 and again.energy_kwh == 2.5
    # The sensors can be set up from the last answer while the miner is off
    assert again.values["hashrate"] == 1000 and again.pause_mode == "pause"


# --- the network ----------------------------------------------------------------


def test_mempool_blocks():
    blocks = [
        {
            "height": 900_001,
            "difficulty": 150e12,
            "timestamp": 2,
            "extras": {"totalFees": 2_000_000, "pool": {"name": "Foundry USA"}},
        },
        {
            "height": 900_000,
            "difficulty": 150e12,
            "timestamp": 1,
            "extras": {"totalFees": 4_000_000, "pool": {"name": "Solo CK"}},
        },
    ]
    s = summarize(blocks)
    assert s["height"] == 900_001 and s["fees"] == 0.03 and s["solo_blocks"][0]["height"] == 900_000
    with pytest.raises(ValueError):
        summarize([])


# --- MQTT -----------------------------------------------------------------------------


def make_miner(database, kind):
    fake = Fake(kind)
    slug = "lucky_lv08" if kind == "lv08" else "nerdqaxe"
    miner = Miner(MinerConfig(slug, slug, "127.0.0.1:9"), 15, database)
    miner.info = miner.snapshot = fake.info()
    if kind == "nerdqaxe":
        miner.info["chips"] = [{"hashRate": 620.0, "errorHashRate": 1.0} for _ in range(4)]
    miner.values = normalize(miner.info)
    miner.connected = True
    miner.last_seen = time.time()
    miner.best_ever = miner.values["best_diff"]
    return miner


def test_discovery(database):
    lv08, nerd = make_miner(database, "lv08"), make_miner(database, "nerdqaxe")
    firmware = Firmware(True)
    messages = discovery_messages(nerd, "homeassistant", "lottery", firmware)
    names = {payload["name"] for payload in messages.values()}
    # The Bitaxe integration's names, so the entity ids stay the same
    assert {
        "Hash rate",
        "Best difficulty (all time)",
        "Shares accepted",
        "ASIC temperature",
        "Mining",
        "Restart",
        "Firmware",
        "Energy",
    } <= names
    assert {"ASIC 1 hash rate", "ASIC 4 temperature", "Block chance today", "Luck"} <= names
    assert all("object_id" not in payload for payload in messages.values())
    switch = messages["homeassistant/switch/lottery_nerdqaxe/mining/config"]
    assert switch["command_topic"] == "lottery/nerdqaxe/mining/set"
    assert messages["homeassistant/sensor/lottery_nerdqaxe/hashrate/config"]["device"]["manufacturer"] == "NerdQAxe"
    # Lucky Miner's firmware has its own releases: no firmware entity rather than a wrong one
    lv08_messages = discovery_messages(lv08, "homeassistant", "lottery", firmware)
    assert not any("/update/" in topic for topic in lv08_messages)
    assert (
        repo_for(lv08.info) is None
        and repo_for(nerd.info) == REPO_NERDQAXE
        and repo_for({"boardVersion": "601"}) == REPO_ESP_MINER
    )
    # Without a way to pause there is no Mining switch
    lv08.info = lv08.snapshot = {k: v for k, v in lv08.info.items() if k != "miningPaused"}
    assert not any("/switch/" in topic for topic in discovery_messages(lv08, "homeassistant", "lottery", firmware))


def test_payloads(database):
    miners = [make_miner(database, "lv08"), make_miner(database, "nerdqaxe")]
    network = Network(False, miners)
    payload = miner_payload(miners[1], network.difficulty())
    assert payload["mining"] == "ON" and payload["pool_connected"] == "ON"
    assert payload["best_diff_formatted"] == "4.21G"
    assert "pool_user" not in payload and "mac" not in payload
    assert payload["day_chance"] > 0 and payload["years"] > 0
    total = totals_payload(miners, network)
    assert total["mining"] == 2 and total["best"] == 4.21e9 and total["prize"] == 3.125


async def test_commands_from_home_assistant(database):
    fake = Fake("lv08")
    server = await served(fake)
    miner = Miner(MinerConfig("Lucky LV08", "lucky_lv08", f"127.0.0.1:{server.port}"), 15, database)
    mqtt = MqttPublisher(Config(), None, [miner], Network(False, [miner]))
    try:
        async with aiohttp.ClientSession() as session:
            await miner.poll(session)
            mqtt.session = session
            await mqtt.command("lucky_lv08", "mining", "OFF")
            assert fake.paused
            await mqtt.command("lucky_lv08", "mining", "ON")
            assert not fake.paused
            await mqtt.command("lucky_lv08", "mining", "maybe")
            await mqtt.command("nobody", "mining", "OFF")
            assert not fake.paused
            await mqtt.command("lucky_lv08", "restart", "PRESS")
            assert fake.down_until > 0
    finally:
        await server.close()


# --- the odds over miners -----------------------------------------------------------------


def test_paused_miner_has_no_tickets(database):
    lv08, nerd = make_miner(database, "lv08"), make_miner(database, "nerdqaxe")
    difficulty = 150e12
    nerd.values["mining"] = False
    assert miner_odds(nerd, difficulty)["block_chance"] is None
    t = totals([lv08, nerd], difficulty)
    assert t["mining"] == 1 and t["shares"] == {"lucky_lv08": 1.0, "nerdqaxe": 0}
    assert t["day_chance"] == pytest.approx(miner_odds(lv08, difficulty)["day_chance"])


# --- the web server ---------------------------------------------------------------------------


async def test_web_api(database, monkeypatch, tmp_path):
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    miners = [make_miner(database, "lv08")]
    network = Network(False, miners)
    web = WebUI(0, Config(), database, miners, network)
    async with TestClient(TestServer(web.app)) as client:
        status = await (await client.get("/api/status")).json()
        assert status["miners"][0]["odds"]["day_chance"] > 0
        assert status["totals"]["miners"] == 1
        assert (await client.get("/api/history?miner=nobody")).status == 404
        assert (await client.get("/api/history?hours=x")).status == 400
        entities = await (await client.get("/api/entities")).json()
        assert any(e["name"] == "Mining" for e in entities["lucky_lv08"])
        refused = await client.post(
            "/api/data/delete", data=json.dumps({"confirm": "no"}), headers={"Content-Type": "application/json"}
        )
        assert refused.status == 400
        database.add_event(1, "lucky_lv08", "best", "ok", "x")
        done = await client.post("/api/data/delete", json={"confirm": "DELETE"})
        assert done.status == 200 and database.events() == []
        page = await (await client.get("/")).text()
        assert "__VERSION__" not in page


# --- the charts up to the last reading ---------------------------------------------------------


def test_short_history_gets_fine_buckets(database):
    now = int(time.time())
    for ts in range(now - 1200, now, 60):
        database.add_sample(ts, "a", {"hashrate": 1000})
    history = database.history(now - 86400, now)
    # 20 minutes of samples: minute buckets, not the 4 minutes a full day gets
    assert history["bucket"] == 60 and len(history["rows"]) >= 19


async def test_history_ends_with_the_latest_readings(database, monkeypatch, tmp_path):
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    fake = Fake("lv08")
    server = await served(fake)
    miner = Miner(MinerConfig("Lucky LV08", "lucky_lv08", f"127.0.0.1:{server.port}"), 5, database)
    try:
        async with aiohttp.ClientSession() as session:
            for _ in range(3):
                await miner.poll(session)
                miner._last_poll -= 5
        assert len(miner.recent) == 3
        web = WebUI(0, Config(), database, [miner], Network(False, [miner]))
        async with TestClient(TestServer(web.app)) as client:
            history = await (await client.get("/api/history?hours=24")).json()
        # The minute sample in the database, then the readings after it
        assert len(history["rows"]) >= 2
        assert history["rows"][-1]["hashrate"] == miner.values["hashrate"]
    finally:
        await server.close()
