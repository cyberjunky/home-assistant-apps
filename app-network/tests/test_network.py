"""The app against a stand-in Home Assistant (tools/mock_ha.py): kinds, hosts, statistics, the
document and the dashboard's API, renaming included."""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import ha_client
from src.config import Config
from src.history import History, host_events, host_stats, merge, online_series, up_intervals
from src.inventory import Inventory, build_hosts, is_up
from src.kinds import KINDS, guess_kind, is_random_mac, mac_vendor, normalize_mac
from src.mobile import distance, habits, haversine, mismatch, segments, summary, trips
from src.store import Conflict, Store, clean, empty_doc
from src.webui import WebUI
from tools import mock_ha

HOUR = 3600
DAY = 86400


# ---------------------------------------------------------------------
# Kinds and MAC addresses
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("texts", "maker", "kind"),
    [
        (["iPhone Ron"], "Apple", "phone"),
        (["HP7C3B8A"], "HP", "printer"),
        (["USW-24-PoE"], "Ubiquiti", "switch"),
        (["U6-Pro Living room"], "Ubiquiti", "ap"),
        (["Dream Machine Pro"], "Ubiquiti", "router"),
        (["garage-door-esp32"], "Espressif", "iot"),
        (["DS920plus"], "Synology", "nas"),
        (["LG-webOS-TV"], "LG Electronics", "tv"),
        (["Sonos-Kitchen"], "Sonos", "speaker"),
        (["PS5"], "Sony Interactive Entertainment", "playstation"),
        (["Reolink-Doorbell"], "Reolink", "doorbell"),
        (["bitaxe-gamma-1"], "Espressif", "miner"),
        (["LT-WORK-4471"], "Lenovo", "laptop"),
        (["Internet (ping 1.1.1.1)"], "", "internet"),
        (["something"], "Raspberry Pi Trading Ltd", "sbc"),
        (["Automower-430X"], "Husqvarna", "mower"),
        (["roborock-s8"], "Roborock", "vacuum"),
        (["BRWADS-1700W"], "Brother", "scanner"),
        (["Denon-AVR-X2800H"], "Denon", "audio"),
        (["homeassistant"], "", "ha"),
        (["Home Assistant Green"], "", "ha_green"),
        (["pve1"], "ASRock", "proxmox"),
        (["something"], "Bitmain", "miner"),
        (["SLZB ZBT-2"], "", "zbt"),
        (["bosch-dishwasher-0"], "", "dishwasher"),
        (["Miele-WashingMachine"], "Miele", "washer"),
        (["shellyplug-s-washer"], "Shelly", "plug"),
        (["Xbox Series X"], "", "xbox"),
        (["PS5"], "", "playstation"),
        (["Nintendo-Switch"], "Nintendo", "nintendo"),
        (["Daikin Perfera"], "", "airco"),
        (["Nespresso Vertuo"], "", "coffee"),
        (["Sonos Arc"], "Sonos", "soundbar"),
        (["Samsung HW-Q990 Soundbar"], "", "soundbar"),
        (["Sonos-Kitchen"], "Sonos", "speaker"),
        (["Wahoo KICKR"], "", "fitness"),
        (["Peloton Bike"], "", "fitness"),
        (["something"], "", "generic"),
    ],
)
def test_guess_kind(texts, maker, kind):
    assert guess_kind(*texts, manufacturer=maker) == kind


def test_every_kind_has_an_icon():
    assert all(mdi.startswith("mdi:") for _label, mdi, _infra in KINDS.values())


def test_maker_from_the_mac():
    assert mac_vendor("24:0A:C4:00:30:10") == "Espressif"
    assert mac_vendor("dc:a6:32:00:10:50").startswith("Raspberry Pi")
    # A private (random) address belongs to no maker
    assert mac_vendor("6a:3f:21:9c:00:01") == ""
    assert mac_vendor("not a mac") == ""


def test_macs():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("00:00:00:00:00:00") == ""
    assert normalize_mac("not a mac") == ""
    # The second bit of the first byte: locally administered, a phone's private address
    assert is_random_mac("6a:3f:21:9c:00:01")
    assert is_random_mac("4A:1C:00:22:50:21")
    assert not is_random_mac("f4:e2:c6:10:00:01")


def test_states():
    assert is_up("home") is True
    assert is_up("not_home") is False
    assert is_up("Work") is False
    assert is_up("unavailable") is None


# ---------------------------------------------------------------------
# Hosts: trackers with the same MAC are one
# ---------------------------------------------------------------------
def test_trackers_with_one_mac_are_one_host():
    mac = "aa:bb:cc:00:00:01"
    entities = {
        "device_tracker.phone": {
            "entity_id": "device_tracker.phone",
            "platform": "unifi",
            "device_id": "d1",
            "has_entity_name": True,
        },
        "device_tracker.phone_nmap": {
            "entity_id": "device_tracker.phone_nmap",
            "platform": "nmap_tracker",
            "original_name": "phone (nmap)",
        },
    }
    devices = {
        "d1": {
            "id": "d1",
            "name": "Phone",
            "manufacturer": "Apple",
            "connections": [["mac", mac.upper()]],
            "area_id": "a1",
            "via_device_id": "d2",
        },
        "d2": {"id": "d2", "name": "AP", "connections": [["mac", "aa:bb:cc:00:00:09"]]},
    }
    states = {
        "device_tracker.phone": {
            "state": "not_home",
            "attributes": {"friendly_name": "Phone", "source_type": "router"},
            "last_changed": "2026-11-13T10:00:00+00:00",
        },
        "device_tracker.phone_nmap": {
            "state": "home",
            "attributes": {"friendly_name": "phone (nmap)", "mac": mac, "ip": "192.168.1.5"},
            "last_changed": "2026-11-13T11:00:00+00:00",
        },
        "device_tracker.ap": {"state": "home", "attributes": {"friendly_name": "AP", "mac": "aa:bb:cc:00:00:09"}},
        "device_tracker.gps": {"state": "Work", "attributes": {"friendly_name": "Phone (app)", "source_type": "gps"}},
        "sensor.other": {"state": "1", "attributes": {}},
    }
    entities["device_tracker.ap"] = {"entity_id": "device_tracker.ap", "platform": "unifi", "device_id": "d2"}
    hosts = {h["key"]: h for h in build_hosts(entities, devices, {"a1": {"name": "Hall"}}, states)}
    phone = hosts[f"mac:{mac}"]
    assert [t["entity_id"] for t in phone["trackers"]] == ["device_tracker.phone", "device_tracker.phone_nmap"]
    assert phone["name"] == "Phone"
    # Online when any tracker says so
    assert phone["up"] is True
    assert phone["ip"] == "192.168.1.5"
    assert phone["area"] == "Hall"
    assert phone["guess"] == "phone"
    assert phone["platforms"] == ["nmap_tracker", "unifi"]
    assert phone["via"] == "mac:aa:bb:cc:00:00:09"
    gps = hosts["ent:device_tracker.gps"]
    assert gps["gps_only"] and gps["up"] is False
    assert not any(k.startswith("ent:sensor") for k in hosts)


# ---------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------
def test_intervals():
    since, until = 0.0, 10 * HOUR
    iv, known = up_intervals(True, [(2 * HOUR, False), (3 * HOUR, True), (8 * HOUR, None)], since, until)
    assert iv == [(0.0, 2 * HOUR), (3 * HOUR, 8 * HOUR)]
    # Unknown from 8h on
    assert known == 8 * HOUR
    iv, known = up_intervals(None, [], since, until)
    assert iv == [] and known == 0
    assert merge([(0, 5), (5, 8), (10, 12)]) == [(0, 8), (10, 12)]


def test_host_stats():
    since, until = 0.0, 2 * DAY
    iv = [(0.0, 10 * HOUR), (11 * HOUR, 2 * DAY)]
    s = host_stats(iv, 2 * DAY, since, until)
    assert s["drops"] == 1
    assert s["drops_per_day"] == 0.5
    assert s["longest_down"] == HOUR
    assert s["availability"] == pytest.approx(47 / 48)
    assert len(s["hours"]) == 24 and len(s["week"]) == 7
    # 1970-01-01 was a Thursday; hour 10 was off on the first day only
    assert s["hours"][10] == pytest.approx(0.5)
    events = host_events({"a": iv}, since, until)
    assert [(e["ts"], e["up"]) for e in events] == [(11 * HOUR, True), (10 * HOUR, False)]
    series = online_series({"a": iv, "b": []}, since, until, 5 * HOUR)
    assert series["y"][:3] == [1, 1, 0]


def test_history_store(tmp_path):
    history = History(tmp_path / "h.sqlite3")
    history.add_many(
        [("device_tracker.a", 100.0, True), ("device_tracker.a", 200.0, False), ("device_tracker.a", 300.0, True)]
    )
    # The same change twice is kept once
    assert history.add_many([("device_tracker.a", 200.0, False)]) == 0
    start, after = history.changes(150.0)
    assert start == {"device_tracker.a": True}
    assert after == {"device_tracker.a": [(200.0, False), (300.0, True)]}
    assert history.first_seen() == {"device_tracker.a": 100.0}
    history.keep_days = 7
    history.prune(now=100.0 + 7 * DAY + 150)
    assert history.changes(0.0)[1]["device_tracker.a"] == [(300.0, True)]
    history.close()


# ---------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------
def test_store_revisions(tmp_path):
    store = Store(tmp_path / "network.json")
    doc = empty_doc()
    doc["devices"] = {"mac:aa": {"kind": "printer", "notes": "", "bogus": 1}}
    saved = store.save(doc, 0)
    assert saved["rev"] == 1
    assert saved["devices"] == {"mac:aa": {"kind": "printer"}}
    # A save from an older revision is refused
    with pytest.raises(Conflict):
        store.save(doc, 0)
    assert Store(tmp_path / "network.json").doc["rev"] == 1


def test_clean_drops_dangling_links():
    doc = empty_doc()
    page = doc["pages"][0]
    page["nodes"] = [{"id": "a", "ref": "mac:x", "x": 0, "y": 0}]
    page["links"] = [{"id": "l", "from": "a", "to": "gone"}]
    page["parent"] = "p0"
    out = clean(doc)
    assert out["pages"][0]["links"] == []
    assert out["pages"][0]["parent"] == "p0"
    with pytest.raises(ValueError):
        clean({"pages": []})
    with pytest.raises(ValueError):
        clean({"pages": [{"id": "a"}, {"id": "a"}]})


# ---------------------------------------------------------------------
# Against the stand-in Home Assistant
# ---------------------------------------------------------------------
@pytest.fixture
async def mock():
    server = mock_ha.MockHA(18124)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def app(mock, tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", mock_ha.TOKEN)
    monkeypatch.setenv("NETWORK_SUPERVISOR_URL", "http://127.0.0.1:18124")
    monkeypatch.setattr(ha_client, "CORE_API_URL", "http://127.0.0.1:18124/core/api")
    monkeypatch.setattr(ha_client, "CORE_WS_URL", "ws://127.0.0.1:18124/core/websocket")
    client = ha_client.HAClient()
    loaded = asyncio.Event()
    inventory: Inventory

    async def connected():
        await inventory.connected()
        loaded.set()

    ws = ha_client.HAWebSocket(client, connected)
    inventory = Inventory(ws)
    history = History(tmp_path / "history.sqlite3")
    inventory.on_change = history.record
    task = asyncio.create_task(ws.run())
    await asyncio.wait_for(loaded.wait(), 10)
    await history.backfill(client, sorted(inventory.states), 10)
    webui = WebUI(0, Config(), ws, inventory, history, Store(tmp_path / "network.json"))
    test = TestClient(TestServer(webui.app))
    await test.start_server()
    yield test, inventory, history
    await test.close()
    task.cancel()
    await client.close()
    history.close()


async def test_hosts_from_home_assistant(app):
    test, _inventory, history = app
    data = await (await test.get("/api/hosts")).json()
    hosts = {h["name"]: h for h in data["hosts"]}
    assert len(data["hosts"]) == len(mock_ha.DEVICES) + 2  # the two phones' GPS trackers are hosts of their own
    assert hosts["iPhone Ron"]["platforms"] == ["nmap_tracker", "unifi"]
    assert hosts["USW-24-PoE"]["guess"] == "switch"
    assert hosts["Galaxy-S23"]["random_mac"]
    # Ping comes from known_devices.yaml: not in the registry
    assert not hosts["Internet (ping 1.1.1.1)"]["trackers"][0]["in_registry"]
    assert history.backfill_state["rows"] > 100


async def test_stats(app):
    test, *_ = app
    data = await (await test.get("/api/stats?days=7")).json()
    flappy = next(k for k in data["hosts"] if k.endswith("24:0a:c4:00:30:10"))
    steady = next(k for k in data["hosts"] if k.endswith("f4:e2:c6:10:00:03"))
    assert data["hosts"][flappy]["drops"] > data["hosts"][steady]["drops"]
    assert data["events"] and data["online"]["x"]
    assert (await test.get("/api/stats?days=3")).status == 400


async def test_rename_in_home_assistant(app, mock):
    test, inventory, _ = app
    entity_id = "device_tracker.hp7c3b8a"
    answer = await test.post("/api/rename", json={"entity_id": entity_id, "name": "Office printer", "device": True})
    assert answer.status == 200, await answer.text()
    assert mock.entities[entity_id]["name"] == "Office printer"
    assert mock.devices[mock.entities[entity_id]["device_id"]]["name_by_user"] == "Office printer"
    hosts = {h["entity_id"]: h for h in inventory.hosts()}
    assert hosts[entity_id]["name"] == "Office printer"
    # Empty gives the integration's own name back
    await test.post("/api/rename", json={"entity_id": entity_id, "name": ""})
    assert mock.entities[entity_id]["name"] is None
    # Not in the registry
    missing = await test.post(
        "/api/rename", json={"entity_id": "device_tracker.internet_ping_1_1_1_1_ping", "name": "x"}
    )
    assert missing.status == 404


async def test_icon_and_doc(app, mock):
    test, *_ = app
    answer = await test.post("/api/icon", json={"entity_id": "device_tracker.hp7c3b8a", "icon": "mdi:printer"})
    assert answer.status == 200
    assert mock.entities["device_tracker.hp7c3b8a"]["icon"] == "mdi:printer"
    assert (
        await test.post("/api/icon", json={"entity_id": "device_tracker.hp7c3b8a", "icon": "printer"})
    ).status == 400
    doc = await (await test.get("/api/doc")).json()
    saved = await test.post("/api/doc", json={"rev": doc["rev"], "doc": doc})
    assert (await saved.json())["rev"] == doc["rev"] + 1
    stale = await test.post("/api/doc", json={"rev": doc["rev"], "doc": doc})
    assert stale.status == 409
    assert (await test.post("/api/doc", data="x", headers={"Content-Type": "text/plain"})).status == 415


async def test_changes_only_from_ingress(app, monkeypatch):
    test, *_ = app
    monkeypatch.delenv("NETWORK_SUPERVISOR_URL")
    answer = await test.post("/api/rename", json={"entity_id": "device_tracker.hp7c3b8a", "name": "x"})
    assert answer.status == 403


async def test_live_changes_are_recorded(app, mock):
    _test, inventory, history = app
    entity_id = "device_tracker.ps5"
    before = mock.states[entity_id]["state"]
    await mock.set_state(entity_id, "not_home" if before == "home" else "home")
    for _ in range(50):
        await asyncio.sleep(0.05)
        if inventory.states[entity_id]["state"] != before:
            break
    _, after = history.changes(0)
    assert after[entity_id][-1][1] is (before != "home")


# ---------------------------------------------------------------------
# Phones
# ---------------------------------------------------------------------
def _p(ts, state, lat=52.0, lon=5.0, acc=10):
    return {"ts": ts, "state": state, "lat": lat, "lon": lon, "acc": acc}


def test_haversine():
    # Utrecht to Amsterdam, about 35 km
    assert 34000 < haversine(52.0907, 5.1214, 52.3702, 4.8952) < 36000


def test_trips_and_habits():
    # Monday 1970-01-05 in UTC: left at 8, at work, home at 18
    day = 4 * DAY
    points = [
        _p(day, "home"),
        _p(day + 8 * HOUR, "not_home", 52.1, 5.0),
        _p(day + 9 * HOUR, "Work", 52.3, 4.9),
        _p(day + 17 * HOUR, "not_home", 52.1, 5.0),
        _p(day + 18 * HOUR, "home"),
    ]
    segs = segments(points, day, day + DAY)
    assert [s["state"] for s in segs] == ["home", "not_home", "Work", "not_home", "home"]
    found = trips(segs, 0)
    assert len(found) == 1 and found[0]["zones"] == ["Work"] and found[0]["seconds"] == 10 * HOUR
    h = habits(found, 0)
    assert h["weekdays"] == {"leaves": 8.0, "returns": 18.0, "days": 1}
    assert h["weekend"]["leaves"] is None
    s = summary(points, day, day + DAY, (52.0, 5.0), 0)
    assert s["home_share"] == pytest.approx(14 / 24)
    assert s["furthest"]["metres"] > 30000
    # The night at home is the warmest spot
    hottest = max(s["heat"], key=lambda c: c[2])
    assert hottest[:2] == [52.0, 5.0]


def test_inaccurate_fixes_are_no_distance():
    points = [_p(0, "x", 52.0, 5.0), _p(60, "x", 52.5, 5.0, acc=2000), _p(120, "x", 52.0, 5.001)]
    assert distance(points) < 100


def test_wifi_mismatch():
    m = mismatch([(0.0, 10.0)], [(2.0, 12.0)], 0, 12)
    assert m["home_not_on_wifi"] == 2 and m["on_wifi_not_home"] == 2
    assert m["agreement"] == pytest.approx(0.8)


async def test_mobile_api(app):
    test, *_ = app
    data = await (
        await test.get("/api/mobile?entity_id=device_tracker.iphone_ron_app&days=7&wifi=mac:6a:3f:21:9c:00:01")
    ).json()
    assert data["track"] and data["heat"] and data["trip_count"] > 3
    assert data["habits"]["weekdays"]["leaves"] is not None
    assert data["wifi"]["agreement"] > 0.5
    zones = (await (await test.get("/api/hosts")).json())["zones"]
    assert {z["state"] for z in zones} >= {"home", "work"}
    assert (await test.get("/api/mobile?entity_id=device_tracker.nope&days=7")).status == 404


def test_a_device_that_just_left_counts():
    # Left half a second before the statistics were made: still a drop, and an event
    until = 10 * HOUR
    iv, known = up_intervals(True, [(until - 0.5, False)], 0.0, until)
    assert host_stats(iv, known, 0.0, until)["drops"] == 1
    assert [e["up"] for e in host_events({"a": iv}, 0.0, until)] == [False]
    # Still online: no drop
    iv, known = up_intervals(True, [], 0.0, until)
    assert host_stats(iv, known, 0.0, until)["drops"] == 0
    assert host_events({"a": iv}, 0.0, until) == []
