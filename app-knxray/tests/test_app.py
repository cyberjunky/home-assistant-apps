"""The whole app against the stand-in Home Assistant: what the pages ask of it."""

import asyncio
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src import ha_client
from src.bus import Bus, BusError
from src.config import Config
from src.hub import Hub, HubError
from src.webui import INGRESS_ADDRESS, WebUI
from tools import installation as house
from tools.mock_ha import MockHA


@pytest.fixture
async def app(tmp_path, monkeypatch):
    """The app, its stand-in Home Assistant and a client; removing switched on."""
    mock = MockHA(simulate=False)
    server = TestServer(mock.app)
    await server.start_server()
    base = f"http://127.0.0.1:{server.port}"
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test")
    monkeypatch.setenv("KNXRAY_SUPERVISOR_URL", base)
    monkeypatch.setattr(ha_client, "CORE_API_URL", f"{base}/core/api")
    monkeypatch.setattr(ha_client, "CORE_WS_URL", f"ws://127.0.0.1:{server.port}/core/websocket")
    hub = Hub(Config(allow_remove=True), tmp_path)
    webui = WebUI(0, hub)
    runner = asyncio.create_task(hub.run())
    client = TestClient(TestServer(webui.app))
    await client.start_server()
    for _ in range(100):
        await asyncio.sleep(0.1)
        if hub.ha.loaded and hub.project.loaded and hub.ha.entities:
            break
    else:
        pytest.fail("the app never read Home Assistant")
    yield client, hub, mock
    runner.cancel()
    await client.close()
    await hub.close()
    await server.close()


async def test_state_and_model(app):
    client, _hub, _mock = app
    state = await (await client.get("/api/state")).json()
    assert state["source"] == "ha" and state["ha"]["connected"] and state["ha"]["address"] == house.HA_ADDRESS
    assert state["project"] == {"loaded": True, "name": "Klinkerstraat 12", "modified": "2026-10-02T19:41:08.000Z",
                                "tool": "6.3.1", "group_addresses": 48, "devices": 15}  # fmt: skip
    assert state["bus"]["mode"] == "ha" and state["allow_remove"] is True
    model = await (await client.get("/api/model")).json()
    assert len(model["entities"]) == 20 and len(model["stray_entities"]) == 2 and len(model["stray_devices"]) == 1
    assert model["exposes"] == {"sensor.living_room_temperature": ["3/0/8"]}
    by_address = {a["address"]: a for a in model["addresses"]}
    assert by_address["1/0/1"]["users"][0]["entity_id"] == "light.living_room_ceiling"
    # Used by Home Assistant, in no project and never seen: still listed
    assert by_address["1/0/19"]["known"] is False
    assert {f["code"] for f in model["findings"]} >= {"ha_stray_entity", "ha_dpt_mismatch", "dpt_conflict"}
    assert model["catalog"]["ha_stray_entity"]["fix"]
    page = await (await client.get("/")).text()
    assert "KNX-RAY" in page and "__VERSION__" not in page


async def test_telegrams_arrive_are_recorded_and_read(app):
    client, hub, mock = app
    await mock.emit(mock.simulation.startup())
    await asyncio.sleep(0.3)
    assert hub.monitor.total == 22
    items = (await (await client.get("/api/telegrams")).json())["items"]
    assert len(items) == 22 and items[0]["type"] == "read" and items[1]["text"] is not None
    detail = await (await client.get("/api/detail", params={"address": "1/1/6"})).json()
    assert [o["device"] for o in detail["objects"]] == ["1.1.1", "1.1.21"] and len(detail["telegrams"]) == 3
    device = await (await client.get("/api/detail", params={"address": "1.1.1"})).json()
    assert len(device["objects"]) == 10 and device["telegrams"]
    archive = await (await client.get("/api/archive", params={"q": "hall"})).json()
    assert archive["total"] == 3
    csv = await (await client.get("/api/archive.csv", params={"ga": "1/1/6"})).text()
    assert csv.count("\n") == 4 and "Hall status" in csv
    stats = await (await client.get("/api/stats")).json()
    assert stats["total"] == 22 and stats["availability"]["segments"] and stats["recorder"]["rows"] == 22
    series = await (await client.get("/api/series", params={"ga": "3/0/1"})).json()
    assert series["count"] == 1 and series["unit"] == "°C"
    assert (await client.get("/api/series", params={"ga": "nope"})).status == 400
    recorded = (await (await client.get("/api/recorded")).json())["addresses"]
    assert {r["ga"] for r in recorded} >= {"1/1/6", "3/0/1"}

    # Read now: through Home Assistant, as the app has no interface of its own
    answer = await (await client.post("/api/read", json={"address": "1/1/6"})).json()
    assert sorted(t["src"] for t in answer["responses"]) == ["1.1.1", "1.1.21"]
    silent = await (await client.post("/api/read", json={"address": "1/1/7"})).json()
    assert silent["responses"] == []
    assert (await client.post("/api/read", json={"address": "1.1.1"})).status == 400


async def test_hiding_a_finding(app):
    client, hub, _mock = app
    model = await (await client.get("/api/model")).json()
    finding = next(f for f in model["findings"] if f["code"] == "ha_dpt_mismatch")
    before = (await (await client.get("/api/state")).json())["findings"]
    assert (await client.post("/api/findings/hide", json={"id": finding["id"]})).status == 200
    after = (await (await client.get("/api/state")).json())["findings"]
    assert after["hidden"] == 1 and after["warning"] == before["warning"] - 1
    # Kept across a restart
    assert finding["id"] in Hub(Config(), hub.data_dir).hidden
    await client.post("/api/findings/hide", json={"id": finding["id"], "hidden": False})
    assert (await (await client.get("/api/state")).json())["findings"]["hidden"] == 0


async def test_removing_stray_data(app):
    client, hub, mock = app
    refused = await client.post("/api/remove", json={"kind": "stray_entity", "id": "light.hall"})
    assert refused.status == 400 and "not a stray" in (await refused.json())["error"]
    yaml = await client.post("/api/remove", json={"kind": "entity", "id": "cover.living_room_blind"})
    assert yaml.status == 400 and "YAML" in (await yaml.json())["error"]
    for kind, target in (("stray_entity", "light.attic"), ("stray_device", "dev_old"), ("entity", "switch.garage")):
        assert (await client.post("/api/remove", json={"kind": kind, "id": target})).status == 200
    assert mock.removed == {"light.attic", "dev_old", "switch.garage"}
    await asyncio.sleep(3)
    assert [e["entity_id"] for e in hub.ha.stray_entities] == ["switch.old_doorbell"] and hub.ha.stray_devices == []

    hub.config.allow_remove = False
    off = await client.post("/api/remove", json={"kind": "stray_entity", "id": "switch.old_doorbell"})
    assert off.status == 400 and "switched off" in (await off.json())["error"]
    assert "switch.old_doorbell" not in mock.removed


async def test_only_ingress_gets_an_answer(app, monkeypatch):
    client, _hub, _mock = app
    assert INGRESS_ADDRESS == "172.30.32.2"
    # Under Home Assistant (a token, no development URL) a caller from the LAN is turned away
    monkeypatch.delenv("KNXRAY_SUPERVISOR_URL")
    for path in ("/", "/api/state", "/api/model", "/static/core.js"):
        assert (await client.get(path)).status == 403
    assert (await client.post("/api/recorder/clear")).status == 403


async def test_device_checks_need_an_interface_of_the_apps_own(app):
    client, hub, _mock = app
    answer = await client.post("/api/job", json={"kind": "project"})
    assert answer.status == 400 and "Interface page" in (await answer.json())["error"]
    with pytest.raises(HubError):
        hub.start_job("line", "1.1")


async def test_choosing_an_interface(tmp_path):
    bus = Bus(tmp_path / "interface.json")
    assert bus.choice == {"mode": "ha"} and bus.status()["state"] == "off"
    for bad in ({"mode": "usb"}, {"mode": "tunneling"}, {"mode": "tunneling", "host": "a b"},
                {"mode": "tunneling_tcp", "host": "10.0.0.5", "port": 99999}, {"mode": "routing", "multicast": "nope"}):  # fmt: skip
        with pytest.raises(BusError):
            await bus.select(bad)
    assert bus.choice == {"mode": "ha"}
    with pytest.raises(BusError):
        await bus.ping("1.1.1")
    # Nothing answers on a port nobody listens on: the choice is kept and the state says so
    await bus.select({"mode": "tunneling_tcp", "host": "127.0.0.1", "port": 1, "name": "Test"})
    await asyncio.sleep(0.5)
    assert bus.status()["state"] in ("connecting", "failed") and not bus.connected
    assert Bus(tmp_path / "interface.json").choice == {
        "mode": "tunneling_tcp",
        "host": "127.0.0.1",
        "port": 1,
        "name": "Test",
    }
    await bus.select({"mode": "ha"})
    assert bus.status()["state"] == "off" and os.path.exists(tmp_path / "interface.json")


def test_options():
    assert Config.from_options({}) == Config()
    config = Config.from_options(
        {"retain_days": 0, "chatty_per_minute": 100, "allow_remove": True, "log_level": "DEBUG"}
    )
    assert (config.retain_days, config.chatty_per_minute, config.allow_remove, config.log_level) == (
        0,
        100,
        True,
        "debug",
    )
    for bad in ({"retain_days": -1}, {"monitor_size": 10}, {"chatty_per_minute": "many"}):
        with pytest.raises(ValueError):
            Config.from_options(bad)


def test_web_application_has_no_writing_routes_for_the_bus():
    """The app only reads the bus: no route sends a value."""
    hub = Hub(Config(), __import__("pathlib").Path(os.environ["KNXRAY_DATA_DIR"]))
    routes = {r.resource.canonical for r in WebUI(0, hub).app.router.routes() if isinstance(r.resource, web.Resource)}
    assert not any(word in route for route in routes for word in ("write", "send", "restart", "program"))


async def test_report_names_every_finding(app):
    client, _hub, _mock = app
    answer = await client.get("/api/report.md")
    text = await answer.text()
    assert "attachment" in answer.headers["Content-Disposition"] and text.startswith("# KNX-Ray report")
    assert "- Project: Klinkerstraat 12, changed 2026-10-02" in text
    assert "## Warning: Entity expects another datapoint type than the project gives (1)" in text
    assert "- **sensor.wind_speed on 4/0/2**: " in text and "- **3/0/6** Bedroom temperature: " in text


async def test_catching_up_from_home_assistants_store(app):
    """What Home Assistant kept while the app was away goes into the recording, once."""
    client, hub, mock = app
    import time
    from datetime import UTC, datetime

    from tools.mock_ha import telegram_dict

    assert hub.recorder.summary()["rows"] == 0
    for minutes, value in ((30, 20.0), (20, 20.5), (10, 21.0)):
        item = telegram_dict("1.1.20", "3/0/1", "write", house.float16(value), mock.project)
        item["timestamp"] = datetime.fromtimestamp(time.time() - minutes * 60, UTC).isoformat()
        mock.stored.append(item)
    await hub._catch_up()
    assert hub.recorder.summary()["rows"] == 3
    series = await (await client.get("/api/series", params={"ga": "3/0/1"})).json()
    assert [round(p[1], 1) for p in series["points"]] == [20.0, 20.5, 21.0]
    # Shown in the archive, not counted as seen since the app started
    assert hub.monitor.total == 0
    await hub._catch_up()
    assert hub.recorder.summary()["rows"] == 3


async def test_tables_as_csv(app):
    client, _hub, _mock = app
    addresses = (await (await client.get("/api/export/addresses.csv")).text()).splitlines()
    assert addresses[0].startswith("address,name,range,type") and len(addresses) == 51
    assert any(line.startswith("1/0/19,,,,no,") for line in addresses)
    devices = (await (await client.get("/api/export/devices.csv")).text()).splitlines()
    assert len(devices) == 16 and devices[1].startswith("1.0.1,IP router,Weinzierl")
    entities = await (await client.get("/api/export/entities.csv")).text()
    assert (
        "light.living_room_ceiling,Living room ceiling,light,ui,on,1/0/3,ga_brightness,write,Living room ceiling brightness"
        in entities
    )
    assert (await client.get("/api/export/secrets.csv")).status == 404


async def test_another_interface_starts_the_pages_afresh(app):
    client, hub, mock = app
    await mock.emit(mock.simulation.startup())
    await asyncio.sleep(0.3)
    hub.reach["1.1.12"] = {"reachable": False}
    assert hub.monitor.total == 22 and hub.epoch == 0
    # The same choice again changes nothing
    assert (await client.post("/api/interface/select", json={"mode": "ha"})).status == 200
    assert hub.monitor.total == 22 and hub.epoch == 0
    assert (
        await client.post("/api/interface/select", json={"mode": "tunneling_tcp", "host": "127.0.0.1", "port": 1})
    ).status == 200
    state = await (await client.get("/api/state")).json()
    assert state["epoch"] == 1 and state["telegrams"]["total"] == 0 and hub.reach == {}
    # What was recorded stays
    assert hub.recorder.summary()["rows"] == 22
    await hub.select_interface({"mode": "ha"})
    assert hub.epoch == 2


async def test_documents_kept_with_a_device(app):
    client, hub, _mock = app
    pdf = b"%PDF-1.7\n" + bytes(2000)
    refused = await client.post("/api/documents/upload", params={"address": "1.1.1", "title": "x"}, data=b"<html>")
    assert refused.status == 400 and "a PDF or a picture" in (await refused.json())["error"]
    manual = await (
        await client.post(
            "/api/documents/upload", params={"address": "1.1.1", "title": "Manual AKS", "product": "1"}, data=pdf
        )
    ).json()
    assert manual["kind"] == "pdf" and manual["size"] == len(pdf) and manual["product"] == "mdt|aks-0816.03"
    note = await (
        await client.post("/api/documents/upload", params={"address": "1.1.1", "title": "Wiring"}, data=pdf)
    ).json()
    link = await (
        await client.post(
            "/api/documents/link", json={"address": "1.1.1", "url": "https://www.mdt.de/aks", "title": ""}
        )
    ).json()
    assert link["title"] == "www.mdt.de" and note["product"] == ""
    for bad in ("javascript:alert(1)", "ftp://x/y", "https://", "not a link"):
        assert (await client.post("/api/documents/link", json={"address": "1.1.1", "url": bad})).status == 400
    assert (await client.post("/api/documents/link", json={"address": "1/1/1", "url": "https://x.nl"})).status == 400

    detail = await (await client.get("/api/detail", params={"address": "1.1.1"})).json()
    assert [d["title"] for d in detail["documents"]] == ["Manual AKS", "Wiring", "www.mdt.de"]
    answer = await client.get(f"/api/documents/file/{manual['id']}")
    assert answer.status == 200 and await answer.read() == pdf
    assert (
        answer.headers["Content-Type"] == "application/pdf" and answer.headers["Content-Security-Policy"] == "sandbox"
    )
    assert 'filename="Manual AKS.pdf"' in answer.headers["Content-Disposition"]
    assert (await client.get(f"/api/documents/file/{link['id']}")).status == 400
    model = await (await client.get("/api/model")).json()
    assert {d["address"]: d["documents"] for d in model["devices"]}["1.1.1"] == 3

    assert (
        await client.post("/api/documents/rename", json={"id": note["id"], "title": "Wiring diagram"})
    ).status == 200
    assert (await client.post("/api/documents/delete", json={"id": link["id"]})).status == 200
    assert (await client.post("/api/documents/delete", json={"id": link["id"]})).status == 400
    # Pictures: uploaded, or a link that ends like one
    png = b"\x89PNG\r\n\x1a\n" + bytes(500)
    photo = await (
        await client.post("/api/documents/upload", params={"address": "1.1.1", "title": "Wiring photo"}, data=png)
    ).json()
    assert photo["kind"] == "image" and photo["ending"] == "png"
    shown = await client.get(f"/api/documents/file/{photo['id']}")
    assert (
        shown.headers["Content-Type"] == "image/png"
        and await shown.read() == png
        and "Wiring photo.png" in shown.headers["Content-Disposition"]
    )
    remote = await (
        await client.post(
            "/api/documents/link", json={"address": "1.1.1", "url": "https://example.com/a/actuator.JPG?x=1"}
        )
    ).json()
    assert remote["kind"] == "image" and remote["url"].endswith("?x=1")
    assert (
        await client.post(
            "/api/documents/upload", params={"address": "1.1.1"}, data=b"<svg xmlns='http://www.w3.org/2000/svg'/>"
        )
    ).status == 400
    for item in (photo, remote):
        assert (await client.post("/api/documents/delete", json={"id": item["id"]})).status == 200
    assert not (hub.data_dir / "documents" / f"{photo['id']}.png").exists()

    # Kept across a restart, and the file goes with its document
    from src.documents import Documents

    again = Documents(hub.data_dir / "documents")
    assert sorted(d["title"] for d in again.items) == ["Manual AKS", "Wiring diagram"]
    again.delete(manual["id"])
    assert not (hub.data_dir / "documents" / f"{manual['id']}.pdf").exists()


async def test_types_from_home_assistant_without_a_project(app):
    """No ETS project: the types Home Assistant uses decode the values, and its entities name the addresses."""
    client, hub, mock = app
    mock.no_project = True
    await hub.refresh()
    assert not hub.project.loaded
    # As at start: a telegram before Home Assistant's types are known
    hub.ha_dpts.clear()
    from xknx.dpt import DPTArray
    from xknx.telegram import GroupAddress, IndividualAddress, Telegram, apci

    hub._from_bus(Telegram(destination_address=GroupAddress("5/0/1"), source_address=IndividualAddress("1.2.12"),
                           payload=apci.GroupValueWrite(DPTArray(bytes.fromhex("43c24064")))))  # fmt: skip
    first = hub.monitor.telegrams[-1]
    assert first["text"] is None and first["raw"] == "43c24064"
    # What Home Assistant's integration last decoded on 4/0/2, and what its entities expect elsewhere
    mock.last_telegrams = {"4/0/2": {"destination": "4/0/2", "dpt_main": 9, "dpt_sub": 5}}
    await hub.refresh()
    assert hub.ha_dpts["5/0/1"] == "14.056" and hub.ha_dpts["4/0/2"] == "9.005" and hub.ha_dpts["4/0/5"] == "1"
    # What was already shown gets its value
    assert first["text"] == "388.5" and first["unit"] == "W"
    model = await (await client.get("/api/model")).json()
    row = next(a for a in model["addresses"] if a["address"] == "5/0/1")
    assert (
        row["dpt_ha"] == "14.056" and row["name_ha"] == "Total power" and row["stats"]["last_value"]["text"] == "388.5"
    )
    # A telegram reported by Home Assistant teaches its type too
    hub._from_ha({"destination": "6/0/1", "telegramtype": "GroupValueWrite", "payload": [3], "dpt_main": 17, "dpt_sub": 1,
                  "direction": "Incoming", "source": "1.1.10", "timestamp": "2026-11-21T10:00:00+01:00"})  # fmt: skip
    assert hub.ha_dpts["6/0/1"] == "17.001"


async def test_last_values_from_before_this_run(app):
    """An address nothing came in on since the app started shows its last value from the recording or from
    Home Assistant; an address of which no type is known shows a guess."""
    client, hub, mock = app
    from tools import installation

    now = __import__("time").time()
    hub.recorder.add({"ts": now - 3600, "dir": "in", "src": "1.2.10", "dst": "4/0/1", "type": "write", "raw": "0343",
                      "text": "8.35", "num": 8.35, "unit": "°C", "dpt": "9.001"})  # fmt: skip
    hub.recorder.add({"ts": now - 60, "dir": "in", "src": "1.1.77", "dst": "1/0/16", "type": "write", "raw": "44554031",
                      "text": None, "num": None, "unit": None, "dpt": None})  # fmt: skip
    hub.recorder.flush()
    for address, t in hub.recorder.last_values().items():
        hub._known(address, {**t, "bits": None, "from": "recording"})
    mock.last_telegrams = {"5/0/1": __import__("tools.mock_ha", fromlist=["telegram_dict"]).telegram_dict(
        "1.2.12", "5/0/1", "write", installation.struct.pack(">f", 412.5), mock.project)}  # fmt: skip
    await hub.refresh()
    rows = {a["address"]: a for a in (await (await client.get("/api/model")).json())["addresses"]}
    assert rows["4/0/1"]["last_known"]["text"] == "8.35" and rows["4/0/1"]["last_known"]["from"] == "recording"
    assert rows["1/0/16"]["last_known"]["guess"] == "853" and rows["1/0/16"]["known"] is False
    assert rows["5/0/1"]["last_known"]["text"] == "412.5" and rows["5/0/1"]["last_known"]["from"] == "Home Assistant"
