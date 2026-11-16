"""Which of Home Assistant's trackers become devices, and which may be scanned."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.trackers import HAError, Trackers, parse_states, scannable, tracker_ip


def state(entity_id, state="home", **attributes):
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


def test_only_device_trackers_with_their_address():
    devices = parse_states(
        [
            state("device_tracker.nas", friendly_name="NAS", ip="192.168.1.20", mac="00:11:32:AA:BB:20"),
            state("device_tracker.phone", "not_home", ip_address="192.168.1.100"),
            state("device_tracker.tv", host="192.168.1.50"),
            state("sensor.temperature", ip="192.168.1.99"),
            "garbage",
        ]
    )
    assert [d["entity_id"] for d in devices] == ["device_tracker.nas", "device_tracker.phone", "device_tracker.tv"]
    nas, phone, tv = devices
    assert nas == {"entity_id": "device_tracker.nas", "name": "NAS", "ip": "192.168.1.20",
                   "mac": "00:11:32:aa:bb:20", "online": True}  # fmt: skip
    assert phone["online"] is False and phone["ip"] == "192.168.1.100" and phone["name"] == "phone"
    assert tv["ip"] == "192.168.1.50"


def test_an_attribute_that_is_not_an_address_is_skipped():
    assert tracker_ip({"ip": "unknown", "host": "10.0.0.7"}) == "10.0.0.7"
    assert tracker_ip({"ip": ""}) is None


@pytest.mark.parametrize(
    ("ip", "ok"),
    [("192.168.1.20", True), ("10.0.0.1", True), ("172.16.5.5", True), ("127.0.0.1", True), ("169.254.1.1", True),
     ("fd00::1", True), ("8.8.8.8", False), ("185.199.108.25", False), (None, False), ("", False), ("nas", False)],
)  # fmt: skip
def test_only_your_own_network_is_scannable(ip, ok):
    assert scannable(ip) is ok


@pytest.fixture
async def home_assistant():
    async def states(request):
        if request.headers.get("Authorization") != "Bearer tok":
            return web.json_response({"message": "401: Unauthorized"}, status=401)
        return web.json_response([state("device_tracker.nas", ip="192.168.1.20")])

    app = web.Application()
    app.router.add_get("/core/api/states", states)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("")).rstrip("/")
    await server.close()


async def test_reads_home_assistant(home_assistant):
    trackers = Trackers("tok", url=home_assistant)
    assert [d["ip"] for d in await trackers.fetch()] == ["192.168.1.20"]
    await trackers.close()


async def test_home_assistant_refusing_is_an_error(home_assistant):
    with pytest.raises(HAError, match="no Supervisor token"):
        await Trackers("", url=home_assistant).fetch()
    trackers = Trackers("wrong", url=home_assistant)
    with pytest.raises(HAError, match="401"):
        await trackers.fetch()
    await trackers.close()
