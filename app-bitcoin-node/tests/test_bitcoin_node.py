"""Tests for Bitcoin Node: the options, the arithmetic, the parsers, the database, and the three
clients read once against the mock node of tools/mock_node.py."""

import base64
import json
import sqlite3
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src import bitcoin, lightning, mqtt, tor
from src.config import Config, certificate_pem, macaroon_hex
from src.database import SAMPLE_SECONDS, SERIES, Database
from src.node import Node
from src.webui import WebUI
from tools import mock_node

OPTIONS = {
    "bitcoin": {"url": "http://192.168.1.50:8332/", "username": "homeassistant", "password": "secret"},
    "lightning": {"url": "https://192.168.1.50:8080", "macaroon": "0201 036c6e64", "tls_cert": "", "read_only": True},
    "tor": {"control": "192.168.1.50:9051", "password": "torpass"},
}


# ---------------------------------------------------------------------- options
def test_options_defaults_and_cleaning():
    empty = Config.from_options({})
    assert (empty.bitcoin_url, empty.lnd_url, empty.tor_control) == ("", "", "")
    assert empty.poll_seconds == 15 and empty.history_days == 90 and empty.lnd_read_only is True
    config = Config.from_options(OPTIONS)
    assert config.bitcoin_url == "http://192.168.1.50:8332"
    assert config.lnd_macaroon == "0201036C6E64"
    assert Config.from_options({"poll_seconds": 1}).poll_seconds == 5


@pytest.mark.parametrize(
    "options, message",
    [
        ({"bitcoin": {"url": "192.168.1.50:8332"}}, "must look like"),
        ({"bitcoin": {"url": "http://192.168.1.50:8332/wallet/x"}}, "without a path"),
        ({"bitcoin": {"url": "http://192.168.1.50:8332", "username": "u"}}, "username and password"),
        ({"lightning": {"url": "https://192.168.1.50:8080"}}, "macaroon"),
        ({"lightning": {"url": "https://192.168.1.50:8080", "macaroon": "not hex!"}}, "hex"),
        ({"tor": {"control": "192.168.1.50"}}, "host:port"),
    ],
)
def test_options_that_cannot_be_used(options, message):
    with pytest.raises(ValueError, match=message):
        Config.from_options(options)


def test_macaroon_and_certificate_forms():
    raw = bytes(range(40))
    assert macaroon_hex(base64.b64encode(raw).decode()) == raw.hex().upper()
    assert macaroon_hex(raw.hex()) == raw.hex().upper()
    body = base64.b64encode(bytes(200)).decode()
    one_line = f"-----BEGIN CERTIFICATE----- {body} -----END CERTIFICATE-----"
    pem = certificate_pem(one_line)
    assert pem.startswith("-----BEGIN CERTIFICATE-----\n") and pem.endswith("-----END CERTIFICATE-----\n")
    assert all(len(line) <= 64 for line in pem.splitlines())
    assert certificate_pem(pem) == pem
    with pytest.raises(ValueError):
        certificate_pem("not a certificate")


# ---------------------------------------------------------------------- arithmetic
def test_subsidy_and_supply():
    assert bitcoin.subsidy_at(0) == 50
    assert bitcoin.subsidy_at(209_999) == 50 and bitcoin.subsidy_at(210_000) == 25
    assert bitcoin.subsidy_at(840_000) == 3.125
    assert bitcoin.subsidy_at(64 * 210_000) == 0
    assert bitcoin.issued_at(0) == 50
    assert bitcoin.issued_at(209_999) == 10_500_000
    assert bitcoin.issued_at(840_000) == 19_687_503.125
    assert bitcoin.issued_at(10_000_000) < 21_000_000


def test_halving_and_retarget():
    halving = bitcoin.halving_info(975_000, now=1000)
    assert halving["height"] == 1_050_000 and halving["blocks_left"] == 75_000
    assert (halving["subsidy"], halving["next_subsidy"]) == (3.125, 1.5625)
    # 1,000 blocks into the period, each 9 minutes: the difficulty goes up by a ninth
    height = 483 * 2016 + 1000
    retarget = bitcoin.retarget_info(height, tip_time=540_000, epoch_time=0, now=540_000)
    assert retarget["blocks_done"] == 1000 and retarget["blocks_left"] == 1016
    assert retarget["avg_interval"] == 540 and retarget["change"] == pytest.approx(1 / 9)
    assert retarget["at"] == 540_000 + 1016 * 540
    # Too early in the period, or no times known: no change is claimed
    assert bitcoin.retarget_info(483 * 2016 + 2, 100, 0, 0)["change"] == 0
    assert bitcoin.retarget_info(height, None, None, 0)["change"] == 0
    assert bitcoin.retarget_info(height, 10_000_000, 0, 0)["change"] == -0.75


def test_version_block_and_histogram():
    assert bitcoin.version_text(320100) == "32.1.0" and bitcoin.version_text(None) == ""
    block = bitcoin.block_from_stats(
        {"height": 5, "blockhash": "ab", "time": 9, "txs": 3, "total_size": 10, "total_weight": 40}
        | {"totalfee": 12_345_678, "subsidy": 312_500_000, "feerate_percentiles": [1, 2, 3, 4, 5]}
        | {"minfeerate": 1, "maxfeerate": 99}
    )
    assert (block["fee_total"], block["subsidy"], block["fee_median"]) == (0.12345678, 3.125, 3)
    entries = {
        "a": {"vsize": 1_000_000, "fees": {"base": 0.015}},  # 1.5 sat/vB
        "b": {"vsize": 500_000, "fees": {"base": 0.06}},  # 12 sat/vB
        "c": {"vsize": 250_000, "fees": {"base": 5.0}},  # 2,000 sat/vB: the last band
        "d": {"vsize": 0, "fees": {"base": 1}},
    }
    bands = {(b["from"], b["to"]): b["vsize"] for b in bitcoin.fee_histogram(entries)}
    assert bands[(1, 2)] == 1.0 and bands[(10, 15)] == 0.5 and bands[(100, 500)] == 0.25
    assert sum(bands.values()) == 1.75


# ---------------------------------------------------------------------- parsers
def test_tor_replies():
    lines = [
        "250-version=0.4.8.21 (git-abc)",
        '250-status/bootstrap-phase=NOTICE BOOTSTRAP PROGRESS=85 TAG=x SUMMARY="Loading"',
    ]
    lines += [
        "250+circuit-status=",
        "5 BUILT $AA~one,$BB~two PURPOSE=GENERAL TIME_CREATED=2026-11-16T20:00:00.5",
        "6 LAUNCHED",
        ".",
        "250 OK",
    ]
    reply = tor.parse_reply(lines)
    assert reply["version"].startswith("0.4.8.21")
    assert tor.parse_bootstrap(reply["status/bootstrap-phase"]) == (85, "Loading")
    first, second = (tor.parse_circuit(line) for line in reply["circuit-status"].splitlines())
    assert (first["id"], first["status"], first["purpose"], first["path"]) == (5, "BUILT", "GENERAL", ["one", "two"])
    assert first["created"] == 1794859200
    assert second == {"id": 6, "status": "LAUNCHED", "purpose": "", "path": [], "created": None}
    assert tor.parse_circuit("") is None
    assert tor.quoted('a"b\\c') == '"a\\"b\\\\c"'


def test_lightning_helpers():
    assert lightning.sat("123") == 123 and lightning.sat(None) == 0 and lightning.sat("x") == 0
    assert lightning.opened_height(str((900_000 << 40) | (12 << 16) | 1)) == 900_000
    assert lightning.opened_height("0") is None
    now = time.time()
    forwards = [{"ts": now - 60, "amount": 1000, "fee": 0.4}, {"ts": now - 120, "amount": 500, "fee": 0.25}]
    forwards.append({"ts": now - 3 * 86400, "amount": 9, "fee": 1})
    days = lightning.forwards_per_day(forwards, now, days=7)
    assert len(days) == 7 and [d["day"] for d in days] == sorted(d["day"] for d in days)
    assert sum(d["count"] for d in days) == 3 and sum(d["volume"] for d in days) == 1509


# ---------------------------------------------------------------------- database
def test_database_keeps_history(tmp_path):
    db = Database(tmp_path / "app.db", history_days=30)
    start = 1_800_000_000
    for i in range(10):
        values = {"mempool_tx": 100 + i, "fee_next": 2.5, "size_on_disk": 1e9 + i * 1e8}
        assert db.sample(values, now=start + i * 3600)
    assert not db.sample({"mempool_tx": 1}, now=start + 9 * 3600 + SAMPLE_SECONDS - 1)
    history = db.history(start, start + 10 * 3600, points=5)
    assert set(SERIES) <= set(history) and len(history["ts"]) == 5
    assert history["mempool_tx"] == [100.5, 102.5, 104.5, 106.5, 108.5]
    assert history["ln_local"] == [None] * 5
    assert db.growth(now=start + 9 * 3600) == {"bytes": 900_000_000, "seconds": 9 * 3600}
    db.add_event({"ts": time.time() - 5, "source": "bitcoin", "type": "block", "level": "", "text": "Block 1"})
    db.add_event({"ts": time.time(), "source": "lightning", "type": "forward", "level": "ok", "text": "Forwarded"})
    assert [e["type"] for e in db.events()] == ["forward", "block"]
    for height in range(1, 200):
        db.save_block({"height": height, "time": height * 600})
    assert db.blocks()[0]["height"] == 199 and len(db.blocks()) <= 155
    db.forget_blocks_from(198)
    assert db.blocks()[0]["height"] == 197

    db.backup_to(tmp_path / "copy.db")
    assert sqlite3.connect(tmp_path / "copy.db").execute("SELECT COUNT(*) FROM samples").fetchone() == (10,)
    counts = db.counts()
    assert counts["samples"] == 10 and counts["events"] == 2 and counts["size"] > 0
    assert db.wipe() == {"samples": 10, "events": 2}
    assert db.counts()["samples"] == 0 and db.blocks()  # the blocks stay
    db.close()


def test_database_drops_what_is_too_old(tmp_path):
    db = Database(tmp_path / "app.db", history_days=2)
    start = 1_800_000_000
    db.sample({"mempool_tx": 1}, now=start)
    db.sample({"mempool_tx": 2}, now=start + 3 * 86400)
    assert db.counts()["samples"] == 1
    db.close()


# ---------------------------------------------------------------------- against the mock node
@pytest.fixture
async def mock():
    """The mock node's three services on free ports, and the options that point at them."""
    node = mock_node.MockNode(seed=7, fast=False)
    runners, ports = [], []
    for app in (mock_node.rpc_app(node), mock_node.lnd_app(node)):
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        ports.append(site._server.sockets[0].getsockname()[1])
    control = await mock_node.tor_server(node, "127.0.0.1", 0)
    options = {
        "bitcoin": {"url": f"http://127.0.0.1:{ports[0]}", "username": "homeassistant", "password": "secret"},
        "lightning": {"url": f"http://127.0.0.1:{ports[1]}", "macaroon": "0201"},
        "tor": {"control": f"127.0.0.1:{control.sockets[0].getsockname()[1]}", "password": "torpass"},
        "mqtt": {"enabled": False},
    }
    yield node, options
    control.close()
    for runner in runners:
        await runner.cleanup()


async def read_once(node: Node) -> None:
    """What each client's run() does on its first round, without the loop."""
    import aiohttp

    config = node.config
    headers = {"Grpc-Metadata-macaroon": config.lnd_macaroon}
    async with (
        aiohttp.ClientSession(headers=node.bitcoin.auth_headers()) as rpc,
        aiohttp.ClientSession(headers=headers) as rest,
    ):
        node.bitcoin._session, node.lightning._session = rpc, rest
        await node.bitcoin.poll()
        node.bitcoin._set_link(True, "")
        if node.bitcoin._backfill:
            await node.bitcoin._backfill
        await node.lightning.poll()
        node.lightning._set_link(True, "")
    await node.tor.poll()
    node.tor._set_link(True, "")


async def test_the_node_is_read_and_served(mock, tmp_path):
    served, options = mock
    database = Database(tmp_path / "app.db")
    node = Node(Config.from_options(options), database, tmp_path)
    events = []
    node.listeners.append(lambda event, extra: events.append((event, extra)))
    await read_once(node)

    status = node.status()
    assert status["node"]["blocks"] == served.tip and status["node"]["synced"] and status["node"]["version"] == "32.1.0"
    assert status["node"]["retarget"]["blocks_done"] == served.tip % 2016
    assert status["mempool"]["tx"] > 0 and status["mempool"]["fees"]["next"] >= status["mempool"]["fees"]["day"]
    assert len(status["mempool"]["histogram"]) == len(bitcoin.FEE_BANDS) - 1
    assert sum(b["vsize"] for b in status["mempool"]["histogram"]) == pytest.approx(status["mempool"]["vsize"], abs=0.2)
    assert status["net"]["peers"] == len(served.peers) and status["net"]["kinds"]["outbound-full-relay"] == 8
    blocks = node.bitcoin.blocks_view()
    assert len(blocks) == 144 and blocks[0]["height"] == served.tip and blocks[0]["interval"] > 0
    assert len(database.blocks()) >= 144

    ln = status["lightning"]
    assert ln["connected"] and ln["alias"] == "polder-node" and ln["version"] == "0.21.8-beta"
    assert ln["channels"]["active"] == 6 and ln["channels"]["inactive"] == 1 and ln["channels"]["pending"] == 1
    assert ln["forwards_30d"] == len(served.forwards) and ln["backup"]["available"] and ln["backup"]["complete"]
    assert "points" not in ln["backup"] and len(list((tmp_path / "channel-backups").glob("*.backup"))) == 1
    details = node.lightning.details(time.time(), served.tip)
    assert len(details["channels"]) == 7 and details["channels"][0]["alias"] == "northwind-routing"
    assert details["channels"][0]["opened"] < time.time() and details["pending"][0]["alias"] == "harbour-sats"
    assert len(details["forwards_daily"]) == 30 and {r["kind"] for r in details["recent"]} == {
        "forward",
        "payment",
        "invoice",
    }

    assert status["tor"]["bootstrap"] == 100 and status["tor"]["circuits"] == len(served.circuits)
    assert [s["port"] for s in status["tor"]["onion_services"]] == [8333, 9735]
    assert {h["name"]: h["state"] for h in status["health"]}["Channels"] == "warn"
    assert not events  # the first reading is the state as found: nothing happened yet

    # A new block, a forwarded payment and a channel going offline are told once each
    served.next_block_at = time.time() - 1
    served.forwards.clear()
    served._forward(time.time())
    served.channels[0]["active"] = False
    node.lightning._slow_at = 0
    await read_once(node)
    kinds = [event["type"] for event, _ in events]
    assert kinds.count("block") == 1 and kinds.count("forward") == 1 and kinds.count("channel_offline") == 1
    block_extra = next(extra for event, extra in events if event["type"] == "block")
    assert block_extra["height"] == served.tip
    assert database.events()[0]["source"] in ("bitcoin", "lightning")

    # MQTT: every device with its state, and entity ids that don't clash
    now = time.time()
    payload = mqtt.bitcoin_payload(node, now)
    assert payload["block_height"] == served.tip and payload["synced"] is True and payload["onion"] is True
    assert mqtt.lightning_payload(node, now)["channels_problem"] is True
    assert mqtt.tor_payload(node, now)["bootstrap"] == 100
    ids = []
    for key in mqtt.DEVICES:
        for topic, message in mqtt.discovery_messages(node, key, "homeassistant", "bitcoin_node"):
            assert topic.startswith("homeassistant/") and json.dumps(message)
            ids.append(message["unique_id"])
    assert len(ids) == len(set(ids)) > 40
    event = {"ts": 12.7, "source": "bitcoin", "type": "block", "level": "", "text": "Block 1"}
    assert mqtt.event_payload(event, {"height": 1, "empty": ""}) == {
        "event_type": "block",
        "text": "Block 1",
        "height": 1,
        "time": 12,
    }

    # The pages' API
    async with TestClient(TestServer(WebUI(0, node, database).app)) as client:
        answer = await (await client.get("/api/status")).json()
        assert answer["node"]["blocks"] == served.tip and "password" not in json.dumps(answer["config"])
        assert "0201" not in json.dumps(answer)
        database.sample(node.sample_values())
        history = await (await client.get("/api/history?range=24h")).json()
        assert history["range"] == "24h" and history["mempool_tx"][-1] > 0 and history["ln_local"][-1] > 0
        assert (await client.get("/api/history?range=1y")).status == 400
        assert len((await (await client.get("/api/blocks")).json())["blocks"]) == 144
        assert len((await (await client.get("/api/peers")).json())["peers"]) == len(served.peers)
        assert (await (await client.get("/api/lightning")).json())["channels"]
        assert (await (await client.get("/api/tor")).json())["circuits"]
        assert (await (await client.get("/api/events?limit=2")).json())["events"]
        backup = await client.get("/api/backup")
        assert backup.status == 200 and (await backup.read()).startswith(b"SQLite format 3")
        channel = await client.get("/api/channel-backup")
        assert channel.status == 200 and len(await channel.read()) > 100
        assert (await client.post("/api/data/delete", json={"confirm": "no"})).status == 400
        assert (await client.post("/api/data/delete", data="confirm=DELETE")).status == 415
        deleted = await (await client.post("/api/data/delete", json={"confirm": "DELETE"})).json()
        assert deleted["removed"]["samples"] == 1
    database.close()


async def test_only_bitcoin_core_set_up(mock, tmp_path):
    _, options = mock
    database = Database(tmp_path / "app.db")
    node = Node(Config.from_options({"bitcoin": options["bitcoin"]}), database, tmp_path)
    assert not node.lightning.configured and not node.tor.configured
    await node.lightning.run()  # ends at once: nothing to read
    await node.tor.run()
    status = node.status()
    assert status["node"] == {"configured": True, "connected": False, "error": ""}
    assert status["lightning"] == {"configured": False, "connected": False}
    assert status["health"][0]["state"] == "bad"
    assert mqtt.bitcoin_payload(node, time.time()) == {} and mqtt.lightning_payload(node, time.time()) == {}
    database.close()


async def test_a_wrong_password_is_told(mock, tmp_path):
    import aiohttp

    _, options = mock
    database = Database(tmp_path / "app.db")
    wrong = {"bitcoin": {**options["bitcoin"], "password": "wrong"}, "tor": {**options["tor"], "password": "wrong"}}
    node = Node(Config.from_options(wrong), database, tmp_path)
    async with aiohttp.ClientSession(headers=node.bitcoin.auth_headers()) as session:
        node.bitcoin._session = session
        with pytest.raises(bitcoin.RpcError, match="username or password"):
            await node.bitcoin.poll()
    with pytest.raises(tor.TorError, match="refused the password"):
        await node.tor.poll()
    database.close()


async def test_changes_are_refused_while_read_only(tmp_path):
    client = lightning.LndClient(Config.from_options(OPTIONS), tmp_path, lambda *a, **k: None)
    with pytest.raises(lightning.ReadOnly):
        await client.change("POST", "/v1/channels/transactions", {"payment_request": "lnbc1"})
