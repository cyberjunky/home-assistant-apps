"""The MikroTik poller: RouterOS API answers to MKTXP-named metrics."""

from datetime import UTC, datetime

from src.mikrotik import collect, parse_uptime

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


class FakeApi:
    """librouteros' Api: path(*parts) gives the rows of that menu (values already typed)."""

    def __init__(self, menus: dict):
        self.menus = menus

    def path(self, *parts):
        menu = "/".join(parts)
        if menu not in self.menus:
            raise RuntimeError(f"no such command prefix: {menu}")
        return iter(self.menus[menu])


ROUTEROS_7 = {
    "system/identity": [{"name": "core-router"}],
    "system/resource": [
        {
            "cpu-load": 7,
            "cpu-count": 4,
            "cpu-frequency": 1400,
            "free-memory": 800_000_000,
            "total-memory": 1_073_741_824,
            "free-hdd-space": 90_000_000,
            "total-hdd-space": 128_000_000,
            "uptime": "2w3d04:05:06",
            "version": "7.16.1",
        }
    ],
    "system/health": [
        {"name": "cpu-temperature", "value": 51, "type": "C"},
        {"name": "voltage", "value": 24.1, "type": "V"},
    ],
    "interface": [
        {
            "name": "ether1",
            "type": "ether",
            "running": True,
            "disabled": False,
            "rx-byte": 1000,
            "tx-byte": 2000,
            "rx-error": 0,
            "tx-error": 0,
            "rx-drop": 3,
            "tx-drop": 0,
            "rx-packet": 10,
            "tx-packet": 20,
        },
        {"name": "ether5", "type": "ether", "running": False, "disabled": False, "rx-byte": 0, "tx-byte": 0},
        {"name": "old-vpn", "type": "wg", "running": False, "disabled": True, "rx-byte": 5, "tx-byte": 5},
    ],
    "ip/dhcp-server/lease": [{"status": "bound"}, {"status": "bound"}, {"status": "waiting"}],
    "interface/wifi/registration-table": [{"interface": "wifi1"}, {"interface": "wifi1"}, {"interface": "wifi2"}],
    "ip/firewall/connection/tracking": [{"total-entries": 1234}],
}


def by_name(samples):
    out = {}
    for s in samples:
        out.setdefault(s.name, []).append(s)
    return out


def test_routeros_7_answers_become_mktxp_metrics():
    m = by_name(collect(FakeApi(ROUTEROS_7), NOW))
    assert m["mktxp_system_cpu_load"][0].value == 7
    assert m["mktxp_system_cpu_load"][0].labels == {"host": "core-router"}
    assert m["mktxp_system_uptime"][0].value == 2 * 604800 + 3 * 86400 + 4 * 3600 + 5 * 60 + 6
    assert m["mktxp_system_cpu_temperature"][0].value == 51
    assert m["mktxp_system_voltage"][0].value == 24.1
    rx = {s.labels["name"]: s for s in m["mktxp_interface_rx_byte_total"]}
    assert set(rx) == {"ether1", "ether5"}  # the disabled interface is left out
    assert rx["ether1"].value == 1000 and rx["ether1"].kind == "counter"
    running = {s.labels["name"]: s.value for s in m["mktxp_interface_running"]}
    assert running == {"ether1": 1.0, "ether5": 0.0}
    assert m["mktxp_dhcp_lease_active_count"][0].value == 2
    wifi = {s.labels.get("name"): s.value for s in m["mktxp_wlan_clients_count"]}
    assert wifi == {"wifi1": 2.0, "wifi2": 1.0}
    assert m["mktxp_firewall_connections_total"][0].value == 1234


def test_routeros_6_health_and_menus_it_lacks():
    menus = {
        "system/resource": [{"cpu-load": "12", "free-memory": "1000", "total-memory": "2000", "uptime": "1w2d3h4m5s"}],
        "system/health": [{"temperature": "45", "voltage": "23.9"}],
        "interface": [],
        "ip/dhcp-server/lease": [],
    }
    m = by_name(collect(FakeApi(menus), NOW))
    assert m["mktxp_system_cpu_load"][0].labels == {"host": "mikrotik"}  # no identity menu: a default
    assert m["mktxp_system_uptime"][0].value == 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert m["mktxp_system_temperature"][0].value == 45
    assert m["mktxp_wlan_clients_count"][0].value == 0  # neither Wi-Fi package: 0, not an error
    assert "mktxp_firewall_connections_total" not in m


def test_parse_uptime():
    assert parse_uptime("05:06:07") == 5 * 3600 + 6 * 60 + 7
    assert parse_uptime("3d00:00:01") == 3 * 86400 + 1
    assert parse_uptime("45s") == 45
    assert parse_uptime("") is None and parse_uptime("soon") is None
