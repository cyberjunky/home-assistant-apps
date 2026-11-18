"""Tests of the dashboard's parts that don't need a broker: the log, discovery, history and helpers."""

import json

from src import discovery
from src.clients import Clients, level_of, split_address
from src.config import Config
from src.ha import form_values
from src.history import History
from src.logpipe import is_subscription
from src.webui import matcher, tree, valid_filter

# Lines as Mosquitto 2.1.4 writes them (captured from the real broker)
LOG = """2026-11-18T09:07:38: mosquitto version 2.1.4 starting
2026-11-18T09:07:38: Config loaded from /etc/mosquitto/mosquitto.conf.
2026-11-18T09:07:38: Websockets support available.
2026-11-18T09:07:38: Opening ipv4 listen socket on port 1883.
2026-11-18T09:07:38: mosquitto version 2.1.4 running
2026-11-18T09:07:47: New connection from 172.30.32.1:46421 on port 1883.
2026-11-18T09:07:47: New client connected from 172.30.32.1:46421 as ha-core (p4, c1, k45, u'homeassistant').
2026-11-18T09:07:48: ha-core 0 homeassistant/+/+/config
2026-11-18T09:07:48: ha-core 1 zigbee2mqtt/+/set
2026-11-18T09:07:48: ha-core zigbee2mqtt/+/set
2026-11-18T09:07:48: New connection from 192.168.1.20:45225 on port 1884.
2026-11-18T09:07:48: New client connected from 192.168.1.20:45225 as z2m (p5, c0, k60).
2026-11-18T09:07:49: z2m 1 zigbee2mqtt/bridge/request/#
2026-11-18T09:07:50: Client z2m [192.168.1.20:45225] disconnected: keepalive timeout.
2026-11-18T09:07:51: Client web [127.0.0.1:34783] disconnected.
"""


def feed(text: str) -> Clients:
    c = Clients()
    for line in text.splitlines():
        c.feed(line + "\n")
    return c


def test_clients_from_the_log():
    c = feed(LOG)
    assert c.broker_version == "2.1.4"
    ha = c.clients["ha-core"]
    assert ha["connected"] and ha["user"] == "homeassistant" and ha["ip"] == "172.30.32.1"
    assert ha["listener"] == 1883 and ha["protocol"] == "3.1.1" and ha["clean"] and ha["keepalive"] == 45
    # Subscribed to two, unsubscribed from one
    assert ha["subs"] == {"homeassistant/+/+/config": 0}
    z = c.clients["z2m"]
    assert not z["connected"] and z["reason"] == "keepalive timeout" and z["drops"] == 1
    assert z["protocol"] == "5" and z["listener"] == 1884 and not z["clean"]
    # A persistent session keeps its subscriptions while away
    assert z["subs"] == {"zigbee2mqtt/bridge/request/#": 1}
    # A client the log never saw connect is ignored
    assert "web" not in c.clients
    assert c.take_counts()[:3] == (2, 1, 0)
    # The subscribe lines are not in the Log page's lines
    assert not any("zigbee2mqtt/+/set" in line["text"] for line in c.log)


def test_reconnect_and_broker_restart():
    c = feed(LOG)
    c.feed("2026-11-18T09:08:00: New client connected from 192.168.1.20:45300 as z2m (p5, c0, k60).\n")
    z = c.clients["z2m"]
    assert z["connected"] and z["connects"] == 2 and z["subs"] == {"zigbee2mqtt/bridge/request/#": 1}
    c.feed("2026-11-18T10:00:00: mosquitto version 2.1.4 starting\n")
    assert not c.clients["ha-core"]["connected"] and c.clients["ha-core"]["subs"] == {}
    assert c.clients["z2m"]["reason"] == "broker restarted"
    # A clean session that comes back starts without subscriptions
    c.feed("2026-11-18T10:00:05: New client connected from 172.30.32.1:1 as ha-core (p4, c1, k45, u'homeassistant').\n")
    assert c.clients["ha-core"]["subs"] == {}


def test_refused_login():
    c = feed(LOG)
    c.feed("2026-11-18T09:09:00: Client tasmota_1 [192.168.1.30:5000] disconnected, not authorised.\n")
    assert c.refused == 1
    assert c.events[-1]["kind"] == "refused"


def test_helpers():
    assert split_address("[::1]:1883") == ("::1", 1883)
    assert split_address("10.0.0.1:5") == ("10.0.0.1", 5)
    assert level_of("Warning: Unable to open file") == "error"
    assert level_of("Client x has exceeded timeout, disconnecting.") == "warning"
    assert is_subscription("2026-11-18T09:07:48: ha-core 0 homeassistant/#")
    assert is_subscription("2026-11-18T09:07:48: ha-core zigbee2mqtt/+/set")
    assert not is_subscription("2026-11-18T09:07:38: Websockets support available.")
    assert not is_subscription("2026-11-18T09:07:38: Opening ipv4 listen socket on port 1883.")


def test_filters():
    assert valid_filter("a/+/c") and valid_filter("#") and valid_filter("a/#")
    assert not valid_filter("a/#/c") and not valid_filter("a+/b") and not valid_filter("")
    test = matcher("zigbee2mqtt/#", "kitchen")
    assert test("zigbee2mqtt/kitchen_lamp", "{}")
    assert test("zigbee2mqtt/lamp", '{"room": "Kitchen"}')
    assert not test("tasmota/kitchen")


def test_tree():
    rows = [
        {"topic": t, "count": 1, "rate": 0.5, "last": 1, "retained": r}
        for t, r in [("a/b/c", True), ("a/b/d", False), ("a/e", True), ("x", False)]
    ]
    root = tree(rows, "")
    assert [c["name"] for c in root] == ["a", "x"]
    a = root[0]
    assert a["topics"] == 3 and a["retained"] == 2 and a["has_children"] and a["leaf"] is None
    level = tree(rows, "a")
    assert [(c["name"], c["has_children"], c["leaf"] is not None) for c in level] == [
        ("b", True, False),
        ("e", False, True),
    ]


def test_discovery():
    payloads = {
        # Short keys, a base topic, a device
        "homeassistant/sensor/0x1234/temperature/config": json.dumps(
            {
                "~": "zigbee2mqtt/kitchen",
                "name": "Temperature",
                "uniq_id": "0x1234_temperature",
                "stat_t": "~",
                "avty": [{"t": "zigbee2mqtt/bridge/state"}, {"t": "~/availability"}],
                "avty_mode": "all",
                "dev": {"ids": ["zigbee2mqtt_0x1234"], "name": "Kitchen sensor", "mf": "Aqara", "mdl": "WSDCGQ11LM"},
                "o": {"name": "Zigbee2MQTT", "sw": "2.6.0"},
                "unit_of_meas": "°C",
            }
        ).encode(),
        # The same unique_id again: Home Assistant ignores one
        "homeassistant/sensor/0x1234/temp2/config": json.dumps(
            {
                "name": "T2",
                "unique_id": "0x1234_temperature",
                "state_topic": "x",
                "device": {"identifiers": "zigbee2mqtt_0x1234"},
            }
        ).encode(),
        # Device discovery: one config, several entities
        "homeassistant/device/plug1/config": json.dumps(
            {
                "dev": {"ids": "plug1", "name": "Plug"},
                "o": {"name": "Tasmota"},
                "avty_t": "tele/plug1/LWT",
                "pl_avail": "Online",
                "pl_not_avail": "Offline",
                "cmps": {
                    "power": {"p": "sensor", "stat_t": "tele/plug1/SENSOR"},
                    "relay": {"p": "switch", "cmd_t": "x"},
                },
            }
        ).encode(),
        "homeassistant/light/bad/config": b"{not json",
        # Removed: an empty retained message
        "homeassistant/light/gone/config": b"",
    }
    items, problems = discovery.entities(payloads)
    assert [p["topic"] for p in problems] == ["homeassistant/light/bad/config"]
    assert len(items) == 4
    temp = next(e for e in items if e["object_id"] == "temperature")
    assert temp["topics"]["state_topic"] == "zigbee2mqtt/kitchen"
    assert [t for t, _, _ in temp["availability"]] == ["zigbee2mqtt/bridge/state", "zigbee2mqtt/kitchen/availability"]
    assert temp["origin"] == "Zigbee2MQTT" and temp["device"]["manufacturer"] == "Aqara" and temp["unit"] == "°C"
    plug = [e for e in items if e["device_key"] == "id:plug1"]
    assert sorted(e["component"] for e in plug) == ["sensor", "switch"]

    values = {
        "zigbee2mqtt/bridge/state": ("online", 100.0),
        "zigbee2mqtt/kitchen/availability": ("offline", 200.0),
        "zigbee2mqtt/kitchen": ('{"temperature": 21}', 300.0),
        "tele/plug1/LWT": ("Online", 50.0),
    }
    devices = discovery.devices(items, values, watching_since=0, now=1000)
    by_name = {d["name"]: d for d in devices}
    # availability_mode all: one offline makes it unavailable
    assert by_name["Kitchen sensor"]["available"] == "offline"
    assert by_name["Kitchen sensor"]["duplicates"] == 2
    assert by_name["Kitchen sensor"]["last_update"] == 300.0
    assert by_name["Plug"]["available"] == "online" and by_name["Plug"]["origin"] == "Tasmota"
    assert discovery.watched_topics(items) == {
        "zigbee2mqtt/bridge/state",
        "zigbee2mqtt/kitchen/availability",
        "tele/plug1/LWT",
    }


def test_availability_latest():
    cfg = {"availability": [{"topic": "a"}, {"topic": "b"}]}
    assert discovery.availability(cfg, {"a": ("online", 1), "b": ("offline", 2)}) == "offline"
    assert discovery.availability(cfg, {"a": ("online", 3), "b": ("offline", 2)}) == "online"
    assert discovery.availability(cfg, {}) == "unknown"
    assert (
        discovery.availability({**cfg, "availability_mode": "any"}, {"a": ("online", 1), "b": ("offline", 2)})
        == "online"
    )


def test_history(tmp_path):
    h = History(tmp_path / "h.sqlite3", 30)
    t0 = 1_800_000_000
    sys = {
        "$SYS/broker/messages/received": "100",
        "$SYS/broker/messages/sent": "300",
        "$SYS/broker/bytes/received": "1000",
        "$SYS/broker/bytes/sent": "3000",
        "$SYS/broker/clients/connected": "5",
        "$SYS/broker/subscriptions/count": "40",
        "$SYS/broker/retained messages/count": "200",
    }
    h.record(t0, sys, (2, 1, 0, {"z2m": 2}), {"zigbee2mqtt": [10, 500]})
    sys2 = dict(sys)
    sys2["$SYS/broker/messages/received"] = "160"
    h.record(t0 + 60, sys2, (0, 0, 1, {}), {"zigbee2mqtt": [5, 100], "tasmota": [1, 10]})
    # The broker restarted: the counter starts again
    sys3 = dict(sys2)
    sys3["$SYS/broker/messages/received"] = "30"
    h.record(t0 + 120, sys3, (1, 0, 0, {"z2m": 1}), {})
    s = h.stats(1, now=t0 + 180)
    assert s["totals"]["received"] == 60 + 30
    assert s["totals"]["connects"] == 3 and s["totals"]["refused"] == 1
    assert s["groups"][0] == {"group": "zigbee2mqtt", "messages": 15, "bytes": 600}
    assert s["flapping"] == [{"client": "z2m", "connects": 3}]
    assert max(v for v in s["clients"] if v is not None) == 5
    h.prune(now=t0 + 31 * 86400)
    assert h.stats(1, now=t0 + 31 * 86400)["totals"]["received"] == 0
    h.close()


def test_form_values():
    schema = [
        {"name": "broker", "type": "string", "description": {"suggested_value": "core-mosquitto"}},
        {"name": "port", "type": "integer", "default": 1883},
        {"name": "username", "type": "string"},
        {"name": "other_settings", "type": "expandable", "schema": [{"name": "keepalive", "default": 60}]},
    ]
    assert form_values(schema) == {"broker": "core-mosquitto", "port": 1883, "other_settings": {"keepalive": 60}}


def test_config():
    c = Config.from_options(
        {"logins": [{"username": "tasmota", "password": "x"}], "keep_days": 5000, "capture_payloads": False}
    )
    assert c.logins == ("tasmota",) and c.keep_days == 3650 and not c.capture_payloads
