"""The internet router, against the stand-in router (tools/mock_router.py): a FRITZ!Box and a plain
UPnP router found over SSDP."""

import asyncio
import socket

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import router as router_module
from src.config import Config
from src.history import History
from src.router import Router, counter_delta, parse_description, parse_soap
from src.webui import WebUI
from tools import mock_router


class Inventory:
    loaded_at = 1.0
    version = 0

    def __init__(self, hosts=None):
        self._hosts = hosts or []

    def hosts(self):
        return self._hosts


class Store:
    def __init__(self, devices=None):
        self.doc = {"devices": devices or {}, "rev": 1}


def free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def session():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
        yield s


async def serve(app) -> tuple[TestServer, int]:
    port = free_port()
    server = TestServer(app, host="127.0.0.1", port=port)
    await server.start_server()
    return server, port


def test_description_and_soap():
    info, services, _ = parse_description(mock_router.description(False), "http://10.0.0.1:49000/igddesc.xml")
    assert info["name"].startswith("FRITZ!Box")
    assert services["WANCommonInterfaceConfig"].control_url == "http://10.0.0.1:49000/igdupnp/control/CommonIfCfg"
    assert "WANIPConnection" in services
    _, services, _ = parse_description(mock_router.description(True), "http://10.0.0.2:5000/rootDesc.xml")
    assert services["WANPPPConnection"].control_url == "http://10.0.0.2:5000/ctl/WANConn"
    answer = mock_router.soap("GetStatusInfo", "urn:x", {"NewUptime": 12, "NewConnectionStatus": "Connected"}).text
    assert parse_soap(answer, "GetStatusInfo") == {"NewUptime": "12", "NewConnectionStatus": "Connected"}


def test_counters():
    assert counter_delta(150, 100, wide=False) == 50
    # A 32-bit counter wraps around at 4 GiB
    assert counter_delta(10, 2**32 - 90, wide=False) == 100
    # A counter that went back otherwise was reset: no number this time
    assert counter_delta(10, 5_000, wide=False) is None
    assert counter_delta(10, 2**32 - 90, wide=True) is None
    assert counter_delta(None, 5, wide=True) is None


def test_the_router_among_the_trackers():
    hosts = [
        {"key": "a", "ip": "192.168.1.2", "guess": "modem"},
        {"key": "b", "ip": "192.168.1.1", "guess": "router"},
        {"key": "c", "ip": "192.168.1.3", "guess": "switch"},
        {"key": "d", "ip": "192.168.1.4", "guess": "generic"},
        {"key": "e", "ip": None, "guess": "router"},
    ]
    r = Router(Inventory(hosts), Store({"d": {"kind": "firewall"}}), None)
    # The kind set on the map first, then a router before a modem; no switches, no host without an IP
    assert r.candidates() == [("192.168.1.4", None), ("192.168.1.1", None), ("192.168.1.2", None)]
    assert Router(Inventory(hosts), Store(), None, "10.0.0.1:8126").candidates() == [("10.0.0.1", 8126)]


async def test_a_fritz_box(tmp_path, session):
    server, port = await serve(mock_router.make_app(generic=False))
    history = History(tmp_path / "h.sqlite3")
    r = Router(Inventory(), Store(), history, f"127.0.0.1:{port}")
    r.session = session
    try:
        assert await r.discover(r.candidates())
        await asyncio.sleep(0.3)
        await r.poll()
        state = r.state()
        assert state["found"] and state["info"]["name"].startswith("FRITZ!Box")
        c = state["connection"]
        assert c["status"] == "Connected" and c["uptime"] > 3 * 86400
        assert c["external_ip"] == mock_router.WAN_IP
        assert c["external_ipv6"] == "2001:db8:1::1" and c["ipv6_prefix"] == "2001:db8:42::/56"
        assert c["dns"] == ["192.0.2.53", "192.0.2.54"]
        assert c["max_down"] == 1_000_000_000
        # Its own rates, and its 64-bit counters for the bytes per hour
        assert state["now"]["down"] > 0 and state["now"]["up"] > 0
        assert sum(history.router_hours(0)["down"]) > 0
        assert not r.unsupported
        # The connection drops: it says so
        server.app[mock_router.STATE]["connected"] = False
        await r.poll(force=True)
        assert r.state()["connection"]["status"] == "Disconnected"
    finally:
        await server.close()
        history.close()


async def test_a_plain_router_over_ssdp(tmp_path, session, monkeypatch):
    server, port = await serve(mock_router.make_app(generic=True))
    ssdp_port = free_port(socket.SOCK_DGRAM)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: mock_router.SsdpResponder(f"http://127.0.0.1:{port}/rootDesc.xml"), local_addr=("127.0.0.1", ssdp_port)
    )
    # Nothing on the FRITZ!Box port: it has to ask over SSDP
    monkeypatch.setattr(router_module, "DESC_PORT", free_port())
    monkeypatch.setattr(router_module, "SSDP_PORT", ssdp_port)
    monkeypatch.setattr(router_module, "SSDP_WAIT", 0.3)
    history = History(tmp_path / "h.sqlite3")
    r = Router(Inventory([{"key": "gw", "ip": "127.0.0.1", "guess": "router"}]), Store(), history)
    r.session = session
    try:
        assert await r.discover(r.candidates())
        assert set(r.services) == {"WANCommonInterfaceConfig", "WANPPPConnection"}
        await asyncio.sleep(0.3)
        await r.poll()
        state = r.state()
        # No rates of its own: worked out from its counters
        assert state["now"]["down"] > 0
        assert state["connection"]["access_type"] == "DSL" and state["connection"]["max_up"] == 40_000_000
        # It has no GetAddonInfos: asked once, then not again
        assert "WANCommonInterfaceConfig#GetAddonInfos" in r.unsupported
    finally:
        transport.close()
        await server.close()
        history.close()


async def test_no_router(tmp_path, session):
    r = Router(Inventory([{"key": "x", "ip": "127.0.0.1", "guess": "phone"}]), Store(), None)
    r.session = session
    assert not await r.discover(r.candidates())
    assert "No tracker is a router" in r.state()["error"]


def test_traffic_kept_per_minute_and_hour(tmp_path):
    history = History(tmp_path / "h.sqlite3")
    base = 1_800_000_000 // 3600 * 3600
    for m in range(30):
        history.add_router_minute(base + m * 60, 1000.0 + m, 100.0, 2000.0 + m, 300.0)
    history.add_router_bytes(base + 10, 500, 50)
    history.add_router_bytes(base + 3000, 700, 70)
    history.add_router_bytes(base + 3700, 1, 1)
    minutes = history.router_minutes(base, 600)
    assert minutes["x"] == [base, base + 600, base + 1200]
    assert minutes["down_peak"][0] == 2009.0 and minutes["down"][0] == pytest.approx(1004.5)
    assert history.router_hours(base) == {"x": [base, base + 3600], "down": [1200, 1], "up": [120, 1]}
    history.prune(now=base + 400 * 86400)
    assert history.router_minutes(0)["x"] == [] and history.router_hours(0)["x"] == []
    history.close()


async def test_the_router_api(tmp_path, session):
    server, port = await serve(mock_router.make_app(generic=False))
    history = History(tmp_path / "h.sqlite3")
    r = Router(Inventory(), Store(), history, f"127.0.0.1:{port}")
    r.session = session
    await r.discover(r.candidates())

    class WS:
        connected = True
        last_error = ""
        client = None

    webui = WebUI(0, Config(), WS(), Inventory(), history, Store(), r)
    test = TestClient(TestServer(webui.app))
    await test.start_server()
    try:
        data = await (await test.get("/api/router?hours=168")).json()
        assert data["found"] and data["minutes"]["step"] == 600
        assert data["recent"]["x"] and "hours" in data
    finally:
        await test.close()
        await server.close()
        history.close()
