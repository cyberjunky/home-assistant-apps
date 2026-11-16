"""The app against a stand-in Home Assistant (tools/mock_ha.py): kinds, hosts, statistics, the
document and the dashboard's API, renaming included."""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import ha_client
from src.config import Config
from src.history import History, host_events, host_stats, merge, online_series, up_intervals
from src.inventory import Inventory, behind_bridges, build_hosts, fritz_nodes, is_up, on_fritz_wifi, port_entity
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
        (["sw-garage"], "MikroTik", "switch"),
        (["CRS326-24G-2S+"], "MikroTik", "switch"),
        (["cAP ax Garden"], "MikroTik", "ap"),
        (["wAP ac"], "MikroTik", "ap"),
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
        (["Starlink"], "", "starlink"),
        (["something"], "Space Exploration Technologies", "starlink"),
        (["GoodWe-ES"], "GoodWe", "inverter"),
        (["SolarEdge SE5000H"], "", "inverter"),
        (["HomeWizard P1"], "HomeWizard", "energy"),
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


def test_what_the_fritz_box_integration_says():
    # Its trackers name the FRITZ! device a device is on, and LAN or WLAN; nothing else says wired or not
    states = {
        "device_tracker.tv": {
            "state": "home",
            "attributes": {
                "mac": "aa:bb:cc:00:00:09",
                "ip": "192.168.178.30",
                "connected_to": "FRITZ!Repeater 2400",
                "connection_type": "LAN",
            },
        },
        "device_tracker.tablet": {
            "state": "home",
            "attributes": {
                "mac": "aa:bb:cc:00:00:0a",
                "connected_to": "FRITZ!Box 7590",
                "connection_type": "WLAN",
                "ssid": "Thuis",
            },
        },
    }
    hosts = {h["mac"]: h for h in build_hosts({}, {}, {}, states)}
    tv, tablet = hosts["aa:bb:cc:00:00:09"], hosts["aa:bb:cc:00:00:0a"]
    assert tv["connected_to"] == "FRITZ!Repeater 2400" and tv["connection_type"] == "LAN" and tv["wired"] is True
    assert tablet["connected_to"] == "FRITZ!Box 7590" and tablet["wired"] is False


def test_fritz_devices_found_by_their_device():
    # The repeater is tracked by arp-scan under its MAC; the FRITZ!Box integration names it in
    # connected_to, and has a device with that name and MAC
    hosts = [
        {
            "key": "rep",
            "name": "192.168.2.20",
            "host_name": "",
            "mac": "dc:39:6f:00:00:01",
            "ip": "192.168.2.20",
            "trackers": [],
        },
        {
            "key": "box",
            "name": "fritz.box",
            "host_name": "",
            "mac": "00:7a:a4:4b:f4:4a",
            "ip": "192.168.2.254",
            "trackers": [],
        },
        {
            "key": "tv",
            "name": "tv",
            "mac": "aa:00:00:00:00:01",
            "connected_to": "FRITZ!WLAN Repeater 1750E",
            "trackers": [],
        },
        {"key": "pc", "name": "pc", "mac": "aa:00:00:00:00:02", "connected_to": "FRITZ!Box 4630", "trackers": []},
        {"key": "x", "name": "x", "mac": "aa:00:00:00:00:03", "connected_to": "Unknown Repeater", "trackers": []},
    ]
    devices = {
        "d1": {"id": "d1", "name": "FRITZ!WLAN Repeater 1750E", "connections": [["mac", "DC:39:6F:00:00:01"]]},
        "d2": {
            "id": "d2",
            "name": "FRITZ!Box 4630",
            "name_by_user": None,
            "connections": [["mac", "00:7A:A4:4B:F4:4A"]],
        },
    }
    found = fritz_nodes(hosts, devices, {})
    assert found["fritz!wlan repeater 1750e"] == "rep" and found["fritz!box 4630"] == "box"
    assert "unknown repeater" not in found
    # No device: the router itself by its address
    router = {"found": True, "address": "192.168.2.254", "info": {"name": "FRITZ!Box 4630"}}
    assert fritz_nodes(hosts, {}, router)["fritz!box 4630"] == "box"
    # connected_to holds a host name: found by its address in DNS
    hosts += [
        {"key": "pixel", "name": "Pixel", "mac": "aa:00:00:00:00:04", "connected_to": "fritz.box", "trackers": []},
        {"key": "plug", "name": "plug", "mac": "aa:00:00:00:00:05", "connected_to": "fritz.repeater", "trackers": []},
        {"key": "rep2", "name": "rep2", "mac": "aa:00:00:00:00:06", "ip": "192.168.2.253", "trackers": []},
    ]
    found = fritz_nodes(hosts, {}, router, {"fritz.box": "192.168.2.254", "fritz.repeater": "192.168.2.253"})
    assert found["fritz.box"] == "box" and found["fritz.repeater"] == "rep2"
    # Not in DNS: not found
    assert "fritz.repeater" not in fritz_nodes(hosts, {}, router, {})


def _fritz_setup(box_url="http://192.168.178.1", rep_url="http://192.168.178.30"):
    """A box and a repeater added to the FRITZ!Box integration, both tracked by arp-scan without a
    name, with the names the user gave them in the box's mesh in connected_to."""
    hosts = [
        {"key": "box", "name": "3c:a6:2f:00:00:01", "mac": "3c:a6:2f:00:00:01", "ip": "192.168.178.1", "trackers": []},
        {"key": "rep", "name": "192.168.178.30", "mac": "3c:a6:2f:00:00:02", "ip": "192.168.178.30", "trackers": []},
        {
            "key": "phone",
            "name": "phone",
            "mac": "aa:00:00:00:00:01",
            "connected_to": "Woonkamer",
            "trackers": [{"platform": "fritz", "via_device_id": "dbox"}],
        },
        {
            "key": "tv",
            "name": "tv",
            "mac": "aa:00:00:00:00:02",
            "connected_to": "Zolder",
            "trackers": [{"platform": "fritz", "via_device_id": "dbox"}],
        },
    ]
    devices = {
        "dbox": {
            "id": "dbox",
            "name": "FRITZ!Box 7530",
            "identifiers": [["fritz", "3C:A6:2F:00:00:01"]],
            "connections": [["mac", "3C:A6:2F:00:00:01"]],
            "configuration_url": box_url,
        },
        "drep": {
            "id": "drep",
            "name": "FRITZ!Repeater 600",
            "identifiers": [["fritz", "3C:A6:2F:00:00:02"]],
            "connections": [["mac", "3C:A6:2F:00:00:02"]],
            "configuration_url": rep_url,
        },
        # The stand-in device of a tracked client: no FRITZ! device of the mesh
        "dphone": {
            "id": "dphone",
            "name": "phone",
            "connections": [["mac", "AA:00:00:00:00:01"]],
            "via_device_id": "dbox",
        },
    }
    return hosts, devices


def test_fritz_nodes_with_any_name_and_address():
    hosts, devices = _fritz_setup()
    # Mesh names that match nothing in Home Assistant: their address in DNS
    found = fritz_nodes(hosts, devices, {}, {"woonkamer": "192.168.178.1", "zolder": "192.168.178.30"})
    assert found == {"woonkamer": "box", "zolder": "rep"}
    # DNS gives the address the FRITZ! device was added by, and no host on the map has it
    hosts_no_ip = [{**h, "ip": "", "mac": h["mac"] if h["key"] not in ("box", "rep") else ""} for h in hosts]
    hosts_no_ip = [h for h in hosts_no_ip if h["key"] in ("phone", "tv")] + [
        {"key": "box", "name": "b", "mac": "3c:a6:2f:00:00:01", "trackers": []},
        {"key": "rep", "name": "r", "mac": "3c:a6:2f:00:00:02", "trackers": []},
    ]
    found = fritz_nodes(hosts_no_ip, devices, {}, {"woonkamer": "192.168.178.1", "zolder": "192.168.178.30"})
    assert found == {"woonkamer": "box", "zolder": "rep"}
    # No DNS: nothing to go by for two unknown names
    assert fritz_nodes(hosts, devices, {}, {}) == {}
    # The repeater's entry is named as in the mesh (its model, the default for both): the one name
    # left is the box's
    devices["drep"]["name_by_user"] = "Zolder"
    assert fritz_nodes(hosts, devices, {}, {}) == {"woonkamer": "box", "zolder": "rep"}


def test_fritz_box_added_by_its_host_name():
    # Added as http://fritz.box, and the mesh calls it fritz.box (Ron's)
    hosts, devices = _fritz_setup(box_url="http://fritz.box")
    for h in hosts:
        if h.get("connected_to") == "Woonkamer":
            h["connected_to"] = "fritz.box"
    found = fritz_nodes(hosts, devices, {}, {"fritz.box": "192.168.178.1"})
    assert found["fritz.box"] == "box"
    # Without DNS too: the address it was added by is the name
    assert fritz_nodes(hosts, devices, {}, {})["fritz.box"] == "box"


def test_fritz_box_is_what_its_trackers_hang_from():
    # One name left, and the repeater isn't on the map: the box is the device the trackers hang from
    hosts, devices = _fritz_setup()
    hosts = [h for h in hosts if h["key"] != "rep" and h.get("connected_to") != "Zolder"]
    devices["dbox2"] = {**devices["dbox"], "id": "dbox2", "connections": [["mac", "3C:A6:2F:00:00:09"]]}
    hosts.append({"key": "box2", "name": "box2", "mac": "3c:a6:2f:00:00:09", "trackers": []})
    assert fritz_nodes(hosts, devices, {}, {}) == {"woonkamer": "box"}


def test_a_real_name_wins_over_a_mac_address():
    # A router's tracker of the FRITZ!Box has no host name, so it is named after the MAC, and its
    # entity ID sorts first: the box's own tracker still names the device
    mac = "00:7a:a4:4b:f4:4a"
    entities = {
        "device_tracker.00_7a_a4_4b_f4_4a": {
            "entity_id": "device_tracker.00_7a_a4_4b_f4_4a",
            "platform": "mikrotik_network",
        },
        "device_tracker.fritz_box": {"entity_id": "device_tracker.fritz_box", "platform": "arpscan_tracker"},
    }
    states = {
        "device_tracker.00_7a_a4_4b_f4_4a": {"state": "home", "attributes": {"friendly_name": mac, "mac": mac}},
        "device_tracker.fritz_box": {"state": "home", "attributes": {"friendly_name": "FritzBox", "mac": mac}},
    }
    (host,) = build_hosts(entities, {}, {}, states)
    assert host["name"] == "FritzBox" and len(host["trackers"]) == 2
    # Only bare names: still one of them
    states["device_tracker.fritz_box"]["attributes"]["friendly_name"] = "192.168.2.254"
    (host,) = build_hosts(entities, {}, {}, states)
    assert host["name"] in (mac, "192.168.2.254")


def test_a_port_that_lists_its_devices():
    port = port_entity(
        {
            "entity_id": "binary_sensor.sw_port_ether5",
            "state": "on",
            "attributes": {
                "switch_mac": "48:A9:8A:00:00:20",
                "port": "ether5",
                "connected": ["DC:A6:32:00:01:61"],
                "speed_mbps": 1000,
            },
        }
    )
    assert port["owner"] == "48:a9:8a:00:00:20" and port["port"] == "ether5" and port["kind"] == "ethernet"
    assert port["connected"] == ["dc:a6:32:00:01:61"] and port["up"] and port["speed"] == 1_000_000_000
    ap = port_entity(
        {
            "entity_id": "sensor.ap",
            "state": "2",
            "attributes": {"ap_mac": "48:a9:8a:00:00:21", "interface": "wifi1", "connected": []},
        }
    )
    assert ap["kind"] == "wifi" and ap["owner"] == "48:a9:8a:00:00:21"
    # Not a port: no list, or no switch
    assert port_entity({"entity_id": "sensor.x", "state": "1", "attributes": {"connected": True}}) is None
    assert (
        port_entity({"entity_id": "sensor.x", "state": "1", "attributes": {"connected": [], "port": "ether1"}}) is None
    )


def test_devices_get_their_port_from_the_switch():
    # arp-scan tracks a Pi without knowing its port; the switch's port lists it. A tracker that
    # names its own switch port (UniFi) keeps that.
    states = {
        "device_tracker.pi": {"state": "home", "attributes": {"friendly_name": "pi", "mac": "dc:a6:32:00:01:61"}},
        "device_tracker.tv": {
            "state": "home",
            "attributes": {
                "friendly_name": "tv",
                "mac": "aa:00:00:00:00:01",
                "sw_mac": "f4:e2:c6:10:00:02",
                "sw_port": 9,
            },
        },
        "device_tracker.phone": {"state": "home", "attributes": {"friendly_name": "phone", "mac": "aa:00:00:00:00:02"}},
    }
    ports = [
        {
            "owner": "48:a9:8a:00:00:20",
            "port": "ether3",
            "kind": "ethernet",
            "connected": ["dc:a6:32:00:01:61", "aa:00:00:00:00:01"],
            "ssid": "",
        },
        {
            "owner": "48:a9:8a:00:00:21",
            "port": "wifi1",
            "kind": "wifi",
            "connected": ["aa:00:00:00:00:02"],
            "ssid": "Home",
        },
    ]
    hosts = {h["mac"]: h for h in build_hosts({}, {}, {}, states, ports)}
    pi, tv, phone = hosts["dc:a6:32:00:01:61"], hosts["aa:00:00:00:00:01"], hosts["aa:00:00:00:00:02"]
    assert (pi["sw_mac"], pi["sw_port"], pi["wired"]) == ("48:a9:8a:00:00:20", "ether3", True)
    assert (tv["sw_mac"], tv["sw_port"]) == ("f4:e2:c6:10:00:02", "9")
    assert phone["ap_mac"] == "48:a9:8a:00:00:21" and phone["ssid"] == "Home" and phone["wired"] is False


def test_a_device_on_several_addresses():
    # A FRITZ!Box answers on more addresses; arp-scan saw one, MikroTik Network lists them all
    mac = "00:7a:a4:4b:f4:4a"
    states = {
        "device_tracker.fritz_box": {
            "state": "home",
            "attributes": {"friendly_name": "fritz", "mac": mac, "ip": "192.168.2.187"},
        },
        "device_tracker.00_7a": {
            "state": "home",
            "attributes": {
                "friendly_name": mac,
                "mac": mac,
                "ip": "192.168.2.254",
                "ip_addresses": ["192.168.2.187", "192.168.2.181", "192.168.2.254"],
            },
        },
    }
    (host,) = build_hosts({}, {}, {}, states)
    assert host["ips"] == ["192.168.2.181", "192.168.2.187", "192.168.2.254"]
    # The address the integration that knows them all chose, not the first tracker's
    assert host["ip"] == "192.168.2.254" and host["name"] == "fritz"


def test_a_router_knows_the_address_better_than_a_scan():
    # arp-scan got the FRITZ!Box's answer on a VPN address; the MikroTik saw it on .254
    mac = "00:7a:a4:4b:f4:4a"
    entities = {
        "device_tracker.fritz_box_4630_ll": {
            "entity_id": "device_tracker.fritz_box_4630_ll",
            "platform": "arpscan_tracker",
        },
        "device_tracker.00_7a_a4_4b_f4_4a": {
            "entity_id": "device_tracker.00_7a_a4_4b_f4_4a",
            "platform": "mikrotik_network",
        },
    }
    states = {
        "device_tracker.fritz_box_4630_ll": {
            "state": "home",
            "attributes": {"friendly_name": "fritz", "mac": mac, "ip": "192.168.2.187"},
        },
        "device_tracker.00_7a_a4_4b_f4_4a": {
            "state": "home",
            "attributes": {"friendly_name": mac, "mac": mac, "ip": "192.168.2.254"},
        },
    }
    (host,) = build_hosts(entities, {}, {}, states)
    assert host["name"] == "fritz" and host["ip"] == "192.168.2.254"
    assert host["ips"] == ["192.168.2.187", "192.168.2.254"]


def test_devices_behind_a_repeater_on_a_switch_port():
    # A FRITZ!Repeater on a MikroTik port: its Wi-Fi clients are seen on that port too
    hosts = [
        {"key": "rep", "mac": "dc:39:6f:00:00:01", "guess": "ap"},
        {"key": "phone", "mac": "aa:00:00:00:00:01", "guess": "phone"},
        {"key": "tv", "mac": "aa:00:00:00:00:02", "guess": "tv", "ap_mac": "48:a9:8a:00:00:21"},
        {"key": "pi", "mac": "aa:00:00:00:00:03", "guess": "sbc"},
        {"key": "sw2", "mac": "aa:00:00:00:00:04", "guess": "generic"},
        {"key": "nas", "mac": "aa:00:00:00:00:05", "guess": "nas"},
    ]
    ports = {
        "48:a9:8a:00:00:20": [
            {"port": "ether5", "connected": ["dc:39:6f:00:00:01", "aa:00:00:00:00:01", "aa:00:00:00:00:02"]},
            {"port": "ether6", "connected": ["aa:00:00:00:00:03"]},
            {"port": "ether7", "connected": ["aa:00:00:00:00:04", "aa:00:00:00:00:05"]},
        ]
    }
    kinds = {"sw2": "unmanaged_switch"}
    behind = behind_bridges(hosts, ports, lambda h: kinds.get(h["key"]) or h["guess"])
    # The phone hangs on the repeater's Wi-Fi; a device another access point names stays there
    assert behind["phone"] == ("rep", "wifi") and "tv" not in behind and "rep" not in behind
    # Alone on its port: on the port
    assert "pi" not in behind
    # Behind a switch set on the map (unmanaged, no tracker of its own would say so)
    assert behind["nas"] == ("sw2", "ethernet")


def test_the_fritz_box_placeholder_is_not_the_device():
    # The FRITZ!Box integration registers every device it tracks as "FRITZ!" / "FRITZ!Box Tracked
    # device": not a router, and not its maker
    entities = {"device_tracker.phone": {"entity_id": "device_tracker.phone", "platform": "fritz", "device_id": "d1"}}
    devices = {
        "d1": {"id": "d1", "name": "Pixel-10-Pro-XL", "manufacturer": "FRITZ!", "model": "FRITZ!Box Tracked device"}
    }
    states = {
        "device_tracker.phone": {
            "state": "home",
            "attributes": {
                "friendly_name": "Pixel-10-Pro-XL",
                "mac": "aa:00:00:00:00:10",
                "connected_to": "FRITZ!Box 4630",
                "connection_type": "WLAN",
                "ssid": "Herschelan",
            },
        }
    }
    (host,) = build_hosts(entities, devices, {}, states)
    assert host["guess"] != "router" and host["model"] == "" and host["manufacturer"] == ""
    assert host["ssid"] == "Herschelan" and host["wired"] is False


def test_vms_behind_their_proxmox_host():
    hosts = [
        {"key": "pve", "mac": "aa:00:00:00:01:00", "guess": "proxmox"},
        {"key": "vm", "mac": "aa:00:00:00:01:01", "guess": "generic"},
    ]
    ports = {"sw": [{"port": "ether2", "connected": ["aa:00:00:00:01:00", "aa:00:00:00:01:01"]}]}
    assert behind_bridges(hosts, ports, lambda h: h["guess"]) == {"vm": ("pve", "virtual")}


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
    # The device gets the name; the tracker an empty own name, so Home Assistant shows only the device's
    assert mock.entities[entity_id]["name"] == ""
    assert mock.devices[mock.entities[entity_id]["device_id"]]["name_by_user"] == "Office printer"
    assert mock.states[entity_id]["attributes"]["friendly_name"] == "Office printer"
    hosts = {h["entity_id"]: h for h in inventory.hosts()}
    assert hosts[entity_id]["name"] == "Office printer"
    # Only the tracker: Home Assistant puts the device's name in front of it
    await test.post("/api/rename", json={"entity_id": entity_id, "name": "LAN", "device": False})
    assert mock.states[entity_id]["attributes"]["friendly_name"] == "Office printer LAN"
    # Typed with the device's name in front: only the rest is its own name
    await test.post("/api/rename", json={"entity_id": entity_id, "name": "Office printer Wi-Fi", "device": False})
    assert mock.entities[entity_id]["name"] == "Wi-Fi"
    assert mock.states[entity_id]["attributes"]["friendly_name"] == "Office printer Wi-Fi"
    # Empty gives the integration's own name back
    await test.post("/api/rename", json={"entity_id": entity_id, "name": ""})
    assert mock.entities[entity_id]["name"] is None
    # Not in the registry
    missing = await test.post(
        "/api/rename", json={"entity_id": "device_tracker.internet_ping_1_1_1_1_ping", "name": "x"}
    )
    assert missing.status == 404


async def test_rename_clears_every_tracker_of_the_device(app, mock):
    test, inventory, _ = app
    named = next(e for e, x in mock.entities.items() if x.get("has_entity_name") and x.get("device_id"))
    device_id = mock.entities[named]["device_id"]
    # Ron's FRITZ!Box: a second tracker on the device, no has_entity_name, with the full name as its
    # own (from before 2026.11): Home Assistant shows "FRITZ!Box 4630 FRITZ!Box 4630"
    other = next(e for e, x in mock.entities.items() if e != named and x.get("device_id") != device_id)
    mock.entities[other].update(
        device_id=device_id, has_entity_name=False, name="FRITZ!Box 4630", original_name="fritz"
    )
    mock.devices[device_id]["name_by_user"] = "FRITZ!Box 4630"
    await mock._refresh_name(other)
    assert mock.states[other]["attributes"]["friendly_name"] == "FRITZ!Box 4630 FRITZ!Box 4630"
    await inventory.load()
    answer = await test.post("/api/rename", json={"entity_id": named, "name": "FRITZ!Box 4630", "device": True})
    assert answer.status == 200, await answer.text()
    for e in (named, other):
        assert mock.entities[e]["name"] == ""
        assert mock.states[e]["attributes"]["friendly_name"] == "FRITZ!Box 4630"


async def test_rename_clears_an_old_kept_name(app, mock):
    # Names from before 2026.11 that Home Assistant kept as they were (compat_name) win over
    # everything until the entity's own name changes
    test, inventory, _ = app
    named = next(e for e, x in mock.entities.items() if x.get("has_entity_name") and x.get("device_id"))
    mock.entities[named].update(name="", compat_name="Old name Old name")
    await mock._refresh_name(named)
    assert mock.states[named]["attributes"]["friendly_name"] == "Old name Old name"
    await inventory.load()
    await test.post("/api/rename", json={"entity_id": named, "name": "New name", "device": True})
    assert mock.states[named]["attributes"]["friendly_name"] == "New name"


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


async def test_switch_ports_from_home_assistant(app, mock):
    test, inventory, _ = app
    data = await (await test.get("/api/hosts")).json()
    pi = next(h for h in data["hosts"] if h["mac"] == "dc:a6:32:00:01:61")
    # Only Nmap tracks it; the switch's port says where it is
    assert pi["sw_mac"] == "48:a9:8a:00:00:20" and pi["sw_port"] == "ether3"
    ports = data["ports"]["48:a9:8a:00:00:20"]
    assert [p["port"] for p in ports][:3] == ["ether1-uplink", "ether2", "ether3"] and len(ports) == 8
    assert ports[5]["neighbor"] == "cAP ax Garden" and ports[7]["disabled"]
    # Plugged into another port: the app follows, live
    version = inventory.version
    await mock.set_port("binary_sensor.sw_garage_port_ether3", "off", connected=[])
    await mock.set_port("binary_sensor.sw_garage_port_ether7", "on", connected=["DC:A6:32:00:01:61"])
    for _ in range(50):
        if inventory.version >= version + 2:
            break
        await asyncio.sleep(0.05)
    data = await (await test.get("/api/hosts")).json()
    assert next(h for h in data["hosts"] if h["mac"] == "dc:a6:32:00:01:61")["sw_port"] == "ether7"


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


def test_an_access_point_registration_beats_fritz_lan():
    # A client of a MikroTik access point: the FRITZ!Box sees it over the cable and says LAN
    states = {
        "device_tracker.plug_fritz": {
            "entity_id": "device_tracker.plug_fritz",
            "state": "home",
            "attributes": {"mac": "AA:BB:CC:00:00:31", "connected_to": "fritz.box", "connection_type": "LAN"},
        },
        "device_tracker.plug_mikrotik": {
            "entity_id": "device_tracker.plug_mikrotik",
            "state": "home",
            "attributes": {"mac": "AA:BB:CC:00:00:31", "ap_mac": "48:A9:8A:00:00:25", "ssid": "Herscheliot"},
        },
    }
    (host,) = build_hosts({}, {}, {}, states)
    assert host["wired"] is False and host["ssid"] == "Herscheliot"
    # Through the port entities of the access point only, too
    ports = [
        {
            "kind": "wifi",
            "owner": "48:a9:8a:00:00:25",
            "port": "wlan1",
            "ssid": "Herscheliot",
            "connected": ["aa:bb:cc:00:00:31"],
        }
    ]
    (host,) = build_hosts({}, {}, {}, {"device_tracker.plug_fritz": states["device_tracker.plug_fritz"]}, ports)
    assert host["wired"] is False and host["ap_mac"] == "48:a9:8a:00:00:25" and host["ssid"] == "Herscheliot"


def test_fritz_wifi_beats_the_switch_port():
    # A FRITZ!Box Wi-Fi client that a MikroTik sees on the port towards the FRITZ!Box
    host = {
        "mac": "aa",
        "connection_type": "WLAN",
        "ssid": "Herschelan",
        "sw_mac": "sw",
        "sw_port": "ether1",
        "ap_mac": "",
    }
    entry = on_fritz_wifi(host)
    assert (
        entry["sw_mac"] == "" and entry["sw_port"] == "" and entry["wired"] is False and entry["ssid"] == "Herschelan"
    )
    assert host["sw_mac"] == "sw"
    # Wired to the FRITZ!Box, or registered to a MikroTik access point: unchanged
    assert on_fritz_wifi({**host, "connection_type": "LAN"})["sw_mac"] == "sw"
    assert (
        on_fritz_wifi({**host, "ap_mac": "ap"}) is not None
        and on_fritz_wifi({**host, "ap_mac": "ap"})["sw_mac"] == "sw"
    )


def test_battery_from_the_devices_sensor():
    """The Companion app has the battery as a sensor of the phone, not on its tracker."""
    entities = {"device_tracker.pixel": {"entity_id": "device_tracker.pixel", "device_id": "d1"}}
    states = {
        "device_tracker.pixel": {
            "entity_id": "device_tracker.pixel",
            "state": "home",
            "attributes": {"source_type": "gps"},
        }
    }
    (host,) = build_hosts(entities, {}, {}, states, None, {"d1": 30.0})
    assert host["trackers"][0]["battery"] == 30.0
    # Its own battery_level comes first
    states["device_tracker.pixel"]["attributes"]["battery_level"] = 55
    (host,) = build_hosts(entities, {}, {}, states, None, {"d1": 30.0})
    assert host["trackers"][0]["battery"] == 55


def test_battery_history():
    from src.mobile import battery_history

    rows = [
        {"state": "31", "last_changed": "2026-11-16T10:00:00+00:00"},
        {"state": "unavailable", "last_changed": "2026-11-16T10:05:00+00:00"},
        {"state": "30", "last_changed": "2026-11-16T10:10:00+00:00"},
    ]
    assert [v for _, v in battery_history(rows)] == [31.0, 30.0]
