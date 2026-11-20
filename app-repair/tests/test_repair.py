"""Tests for Repair: the checks on the stand-in home of tools/mock_ha.py, the repairs against it,
the parts that sort and count, the database and the pages' API."""

import json
import sqlite3
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src import checks, fixes, ha, storage
from src.config import Config
from src.database import Database
from src.engine import Engine
from src.snapshot import Snapshot, take
from src.webui import WebUI
from tools import mock_ha


@pytest.fixture
async def home(monkeypatch):
    """The stand-in Home Assistant on a free port, and a client pointed at it."""
    mock = mock_ha.MockHome()
    runner = web.AppRunner(mock_ha.build_app(mock))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    monkeypatch.setattr(ha, "SUPERVISOR_URL", url)
    monkeypatch.setattr(ha, "CORE_API_URL", f"{url}/core/api")
    monkeypatch.setattr(ha, "CORE_WS_URL", url.replace("http", "ws") + "/core/websocket")
    monkeypatch.setattr("src.engine.SETTLE_SECONDS", 0)
    client = ha.HA()
    yield mock, client
    await client.close()
    await runner.cleanup()


def by_kind(findings: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for finding in findings:
        out.setdefault(finding["kind"], []).append(finding)
    return out


# ---------------------------------------------------------------------- the checks
async def test_the_stand_in_home_is_read_and_judged(home):
    mock, client = home
    snap = await take(client)
    assert snap.version == mock_ha.VERSION and not snap.gaps
    assert len(snap.states) == len(mock.states) and len(snap.devices) == 10 and snap.resolution["issues"]
    assert snap.automations["automation.morning_lights"]["alias"] == "Morning lights"
    assert [d["url_path"] for d in snap.dashboards] == [None, "energy-wall"]
    assert snap.config_check == {"result": "valid", "errors": None, "warnings": None}

    findings = checks.run(snap)
    kinds = by_kind(findings)
    ids = [f["id"] for f in findings]
    assert len(ids) == len(set(ids)) and all(f["category"] in checks.CATEGORIES for f in findings)
    assert [f["severity"] for f in findings] == sorted((f["severity"] for f in findings), key=checks.SEVERITY_ORDER.get)

    # Integrations that failed, each sorted by what Home Assistant says about it
    tado = kinds["entry_setup_error"][0]
    assert tado["category"] == "login" and tado["severity"] == "high" and tado["affected"] == ["climate.thermostat"]
    assert tado["fixes"] == [{"fix": "reload_entry", "label": "Reload Tado", "args": {"entry_id": "e-tado"}}]
    assert kinds["entry_setup_retry"][0]["category"] == "offline"
    # What depends on a failed integration is not reported once more
    assert not any("washing_machine" in " ".join(f["affected"]) for f in findings if not f["kind"].startswith("entry_"))

    # A device that is off, one that half reports, and one entity without a registry entry
    hall = kinds["device_offline"][0]
    assert hall["subject"] == {"type": "device", "id": "d-hall", "name": "Hallway lamp"} and len(hall["affected"]) == 2
    assert [f["fix"] for f in hall["fixes"]] == ["reload_entry", "cleanup_device"] and hall[
        "since"
    ] < time.time() - 2 * 86400
    assert kinds["device_partly_unavailable"][0]["title"] == "Front door sensor: 1 of 3 entities are unavailable"
    assert kinds["entity_unavailable"][0]["subject"]["id"] == "sensor.yaml_template"

    # Left behind: a device its integration dropped, one whose integration is gone, an empty one, a stateless entity
    assert kinds["device_not_provided"][0]["subject"]["id"] == "d-motion" and "entity_not_provided" not in kinds
    assert (
        kinds["device_without_integration"][0]["subject"]["id"] == "d-weather"
        and "entity_without_integration" not in kinds
    )
    assert kinds["device_without_entities"][0]["subject"]["name"] == "IKEA remote"  # the bridge has lamps behind it
    assert kinds["entity_without_state"][0]["subject"]["id"] == "sensor.unused_helper"
    assert not any("disabled_thing" in json.dumps(f) for f in findings)

    # Faulty configuration and broken references
    assert kinds["invalid_automation"][0]["links"] == [
        {"label": "Open the automation", "path": "/config/automation/edit/1700000000002"}
    ]
    reference = kinds["automation_reference"][0]
    assert reference["evidence"] == [
        "light.kitchen_old",
        "sensor.kitchen_lux_gone",
        "device 0123456789abcdef0123456789abcdef",
    ]
    assert kinds["scene_reference"][0]["evidence"] == ["light.tv_backlight_gone"]
    assert kinds["dashboard_reference"][0]["evidence"] == ["sensor.removed_one"]

    # Statistics, Home Assistant's own repairs, the Supervisor
    assert kinds["statistic_units_changed"][0]["evidence"][1] == "Stored in ft³, now reports in m³"
    left = kinds["statistics_without_entity"][0]
    assert (
        left["fixes"][0]["args"] == {"statistic_ids": ["sensor.attic_humidity", "sensor.old_power_meter"]}
        and left["fixes"][0]["word"] == "DELETE"
    )
    repair = kinds["repair"][0]
    assert repair["title"] == "The MQTT YAML configuration is being removed" and "`mqtt`" in repair["text"]
    assert "Stops working in Home Assistant 2027.6.0" in repair["evidence"]
    assert kinds["supervisor_issue"][0]["fixes"][0]["args"] == {"uuid": "s1"}

    # The log, sorted by what each line is about
    bug = kinds["log_bug"][0]
    assert bug["severity"] == "high" and bug["integration"] == "solarfox" and "custom integration" in bug["text"]
    assert {"label": "Report it to its makers", "url": "https://github.com/example/solarfox/issues"} in bug["links"]
    assert bug["evidence"][-1] == "KeyError: 'pv'" and bug["fixes"] == []
    assert kinds["log_offline"][0]["fixes"][0]["args"] == {"entry_id": "e-shelly"}
    assert kinds["log_login"][0]["integration"] == "tado" and kinds["log_deprecated"][0]["severity"] == "low"
    assert kinds["log_config"][0]["title"] == "Home Assistant reports a configuration error"
    assert "log_slow" not in kinds  # a warning about a slow update is on the Log page, not a finding

    assert checks.counts(findings)["total"] == len(findings) == 22
    assert checks.score(findings) == 34 and checks.score([]) == 100


def test_a_healthy_home_has_no_findings():
    snap = Snapshot(
        states={"light.a": {"entity_id": "light.a", "state": "on", "attributes": {}}},
        entities=[
            {"entity_id": "light.a", "platform": "hue", "config_entry_id": "e", "device_id": "d", "disabled_by": None}
        ],
        devices=[{"id": "d", "name": "Lamp", "config_entries": ["e"]}],
        entries=[{"entry_id": "e", "domain": "hue", "title": "Hue", "state": "loaded"}],
        config_check={"result": "valid"},
    )
    assert checks.run(snap) == []
    snap.config_check = {"result": "invalid", "errors": "Invalid config for 'sensor' at configuration.yaml, line 12\n"}
    snap.resolution = {"unhealthy": ["docker"], "unsupported": ["os"]}
    kinds = by_kind(checks.run(snap))
    assert kinds["configuration_invalid"][0]["evidence"] == [
        "Invalid config for 'sensor' at configuration.yaml, line 12"
    ]
    assert (
        kinds["supervisor_unhealthy"][0]["severity"] == "high"
        and kinds["supervisor_unsupported"][0]["severity"] == "medium"
    )


def test_references_in_configurations():
    config = {
        "triggers": [{"entity_id": "binary_sensor.door, binary_sensor.window"}],
        "actions": [
            {
                "action": "light.turn_on",
                "target": {"entity_id": ["light.a"]},
                "data": {"message": "light.not_an_entity"},
            },
            {"device_id": "ab" * 16, "type": "toggle"},
            {"device_id": "{{ device_id('light.b') }}"},
            {"value_template": "{{ states.sensor.power.state | float > state_attr('climate.x', 'temperature') }}"},
        ],
        "cards": [{"entity": "sensor.temp", "camera_image": "camera.door", "name": "sensor.fake"}],
    }
    known = {"binary_sensor", "light", "sensor", "climate", "camera"}
    entities, devices = checks.references(config, known)
    assert entities == {
        "binary_sensor.door",
        "binary_sensor.window",
        "light.a",
        "light.b",
        "sensor.power",
        "climate.x",
        "sensor.temp",
        "camera.door",
    }
    assert devices == {"ab" * 16}


@pytest.mark.parametrize(
    "message, exception, kind",
    [
        ("Timeout connecting to 192.168.1.5", "", "offline"),
        ("Config entry 'Hub' for hue integration not ready yet: Cannot connect to host", "", "offline"),
        ("Authentication failed: invalid_auth", "", "login"),
        ("Invalid config for 'sensor' at configuration.yaml, line 3: required key not provided", "", "config"),
        ("TemplateError('UndefinedError: x is undefined') while processing template", "", "config"),
        ("The 'foo' option is deprecated and will be removed in 2027.1", "", "deprecated"),
        ("Detected blocking call to open inside the event loop", "", "slow"),
        ("Unexpected error fetching data", "Traceback...\nKeyError: 'x'", "bug"),
        ("Something odd happened", "", "other"),
    ],
)
def test_log_lines_are_sorted_by_what_they_are_about(message, exception, kind):
    assert checks.classify_log({"message": [message], "exception": exception}, custom=False) == kind


def test_small_helpers():
    assert checks.log_domain({"name": "custom_components.foo.sensor"}) == "foo"
    assert checks.log_domain({"name": "x", "source": ["/config/custom_components/bar/api.py", 3]}) == "bar"
    assert checks.log_domain({"name": "homeassistant.components.mqtt.client"}) == "mqtt"
    assert checks.log_domain({"name": "homeassistant.core"}) == ""
    assert (
        checks.fill_placeholders("Remove {domain}, see [the docs](https://x)", {"domain": "mqtt"})
        == "Remove mqtt, see the docs"
    )
    assert checks.parse_time("2026-11-20T10:00:00+00:00") == 1795168800 and checks.parse_time(12.5) == 12.5
    assert checks.parse_time("") is None and checks.parse_time("nonsense") is None
    assert checks.finding_id("a", "b") == checks.finding_id("a", "b") != checks.finding_id("a", "c")
    assert checks.score([{"severity": "high"}] * 100) < 1
    assert Config.from_options({}).scan_minutes == 30 and Config.from_options({"scan_minutes": 1}).scan_minutes == 5
    assert Config.from_options({"allow_fixes": False}).allow_fixes is False


# ---------------------------------------------------------------------- the repairs
async def test_repairs_change_home_assistant_and_say_what_they_did(home, tmp_path):
    mock, client = home
    database = Database(tmp_path / "app.db")
    engine = Engine(Config(), database, client)
    assert await engine.scan()
    assert engine.summary()["score"] == 34 and engine.summary()["by_integration"][0]["count"] == 3

    def finding(kind: str) -> dict:
        return next(f for f in engine.findings if f["kind"] == kind)

    # The browser never gets the arguments of a repair, and can't ask for one that was not offered
    listed = engine.listed()
    assert all("args" not in fix for f in listed for fix in f["fixes"])
    hall = finding("device_offline")
    with pytest.raises(LookupError):
        await engine.repair(hall["id"], 0, "cleanup_device")
    with pytest.raises(LookupError):
        await engine.repair("nothing", 0, "reload_entry")

    # A device its integration lets go: history, then the entities, then the device
    mock.calls.clear()
    result = await engine.repair(finding("device_not_provided")["id"], 0, "cleanup_device")
    assert result["ok"] and len(result["steps"]) == 5
    names = [
        call[0]
        for call in mock.calls
        if call[0]
        in ("recorder.purge_entities", "config/entity_registry/remove", "config/device_registry/remove_config_entry")
    ]
    assert names == ["recorder.purge_entities", "config/entity_registry/remove"] * 2 + [
        "config/device_registry/remove_config_entry"
    ]
    assert ("recorder.purge_entities", {"entity_id": ["binary_sensor.old_motion"], "keep_days": 0}) in mock.calls
    assert not any(d["id"] == "d-motion" for d in mock.devices) and "binary_sensor.old_motion" not in mock.states
    assert not any(f["kind"] == "device_not_provided" for f in engine.findings)  # the scan after the repair

    # A device whose integration does not let Home Assistant remove it: said, not forced
    result = await engine.repair(hall["id"], 1, "cleanup_device")
    assert not result["ok"] and [s["ok"] for s in result["steps"]] == [True, True, True, True, False]
    assert "doesn't let Home Assistant remove its devices" in result["steps"][-1]["detail"]
    assert any(d["id"] == "d-hall" for d in mock.devices) and "light.hallway" not in mock.states

    # A device no integration holds any more goes with its last entity
    result = await engine.repair(finding("device_without_integration")["id"], 0, "cleanup_device")
    assert result["ok"] and "Home Assistant drops it" in result["steps"][-1]["detail"]
    assert not any(d["id"] == "d-weather" for d in mock.devices)

    result = await engine.repair(finding("statistics_without_entity")["id"], 0, "clear_statistics")
    assert result["ok"] and [s["statistic_id"] for s in mock.statistic_ids] == [
        "sensor.solarfox_energy",
        "sensor.gas_meter",
        "energy:imported_from_grid",
    ]
    result = await engine.repair(finding("entry_setup_error")["id"], 0, "reload_entry")
    assert result["ok"] and ("reload", "e-tado") in mock.calls
    result = await engine.repair(finding("supervisor_issue")["id"], 0, "apply_suggestion")
    assert (
        result["ok"]
        and ("suggestion", "s1") in mock.calls
        and not any(f["kind"] == "supervisor_issue" for f in engine.findings)
    )

    # Cleaning up something chosen by hand: an entity with statistics, and one that only has a state
    result = await engine.cleanup("entity", "sensor.gas_meter")
    assert result["ok"] and [s["label"] for s in result["steps"]] == [
        "Delete the history of sensor.gas_meter",
        "Delete its long-term statistics",
        "Remove sensor.gas_meter from the registry",
    ]
    result = await engine.cleanup("entity", "sensor.yaml_template")
    assert result["ok"] and "comes back at the next restart" in result["steps"][-1]["detail"]
    with pytest.raises(LookupError):
        await engine.cleanup("entity", "sensor.never_existed")

    # Ignoring takes a finding out of the score, not out of the list
    before = engine.summary()["score"]
    broken = finding("invalid_automation")
    engine.ignore(broken["id"], True)
    assert engine.summary()["score"] > before and engine.summary()["ignored"] == 1
    assert next(f for f in engine.listed() if f["id"] == broken["id"])["ignored"] is True
    engine.ignore(broken["id"], False)
    assert engine.summary()["ignored"] == 0

    # Everything it did is kept, newest first, and the trend has a point per scan
    actions = database.actions()
    assert [a["fix"] for a in actions][-1] == "cleanup_device" and actions[0]["subject"] == "sensor.yaml_template"
    assert sum(1 for a in actions if not a["ok"]) == 1
    assert len(database.trend(0)["ts"]) >= 1
    database.close()


async def test_repairs_can_be_turned_off(home, tmp_path):
    _, client = home
    database = Database(tmp_path / "app.db")
    engine = Engine(Config(allow_fixes=False), database, client)
    await engine.scan()
    assert all(f["fixes"] == [] for f in engine.listed())
    finding = next(f for f in engine.findings if f["kind"] == "entry_setup_error")
    with pytest.raises(PermissionError):
        await engine.repair(finding["id"], 0, "reload_entry")
    database.close()


async def test_an_unknown_repair_is_refused(home):
    _, client = home
    steps = await fixes.apply(client, Snapshot(), "format_disk", {})
    assert not steps.ok and "no repair called format_disk" in steps.steps[0]["detail"]


async def test_home_assistant_that_does_not_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(ha, "CORE_WS_URL", "ws://127.0.0.1:9/core/websocket")
    database = Database(tmp_path / "app.db")
    engine = Engine(Config(), database, ha.HA())
    assert not await engine.scan()
    summary = engine.summary()
    assert not summary["ready"] and summary["score"] is None and "does not answer" in summary["error"]
    await engine.ha.close()
    database.close()


# ---------------------------------------------------------------------- inspecting and the API
async def test_inspect_search_and_the_pages_api(home, tmp_path):
    mock, client = home
    database = Database(tmp_path / "app.db")
    engine = Engine(Config(), database, client)
    await engine.scan()

    assert [r["id"] for r in engine.search("hallway")] == ["d-hall", "light.hallway", "sensor.hallway_lamp_power"]
    assert engine.search("") == [] and engine.search("no such thing") == []
    lamp = engine.inspect("device", "d-living")
    assert lamp["integrations"] == [{"title": "Philips Hue", "domain": "hue", "removable": False}]
    assert [e["entity_id"] for e in lamp["entities"]] == ["light.living_room", "sensor.living_room_lamp_power"]
    assert {u["type"] for u in lamp["used_in"]} == {"automation", "script", "scene", "dashboard"}
    energy = engine.inspect("entity", "sensor.solarfox_energy")
    assert energy["entities"][0]["statistics"] and energy["device"] == {"id": "d-fox", "name": "SolarFox inverter"}
    motion = engine.inspect("entity", "binary_sensor.old_motion")
    assert motion["entities"][0]["restored"] and len(motion["findings"]) == 1
    with pytest.raises(LookupError):
        engine.inspect("device", "nope")

    async with TestClient(TestServer(WebUI(0, engine.config, engine, database).app)) as page:
        status = await (await page.get("/api/status")).json()
        assert status["score"] == 34 and status["categories"]["orphan"] == "Left behind" and status["ready"]
        findings = (await (await page.get("/api/findings")).json())["findings"]
        assert len(findings) == 22 and "args" not in json.dumps(findings)
        assert len((await (await page.get("/api/log")).json())["entries"]) == 6
        assert (await (await page.get("/api/search?q=solarfox")).json())["results"]
        assert (await page.get("/api/inspect?type=entity&id=sensor.nope")).status == 404
        assert (await page.get("/api/trend?days=x")).status == 400

        remove = next(f for f in findings if f["kind"] == "device_not_provided")
        body = {"finding": remove["id"], "index": 0, "fix": "cleanup_device"}
        assert (await page.post("/api/repair", json=body)).status == 400  # its word is not typed
        assert (await page.post("/api/repair", data="x=1")).status == 415
        assert (await page.post("/api/repair", json={**body, "index": 3, "word": "REMOVE"})).status == 404
        done = await (await page.post("/api/repair", json={**body, "word": "REMOVE"})).json()
        assert done["ok"] and not any(d["id"] == "d-motion" for d in mock.devices)
        assert (await page.post("/api/cleanup", json={"type": "entity", "id": "sensor.unused_helper"})).status == 400
        gone = await (
            await page.post("/api/cleanup", json={"type": "entity", "id": "sensor.unused_helper", "word": "REMOVE"})
        ).json()
        assert gone["ok"]
        assert (await page.post("/api/ignore", json={"finding": "nothing"})).status == 404
        assert (await page.post("/api/scan", json={})).status == 200
        assert len((await (await page.get("/api/actions")).json())["actions"]) == 2
        backup = await page.get("/api/backup")
        assert backup.status == 200 and (await backup.read()).startswith(b"SQLite format 3")
        assert (await page.post("/api/data/delete", json={"confirm": "no"})).status == 400
        removed = await (await page.post("/api/data/delete", json={"confirm": "DELETE"})).json()
        assert removed["removed"]["actions"] == 2
    database.close()


def test_database(tmp_path):
    db = Database(tmp_path / "app.db")
    counts = {"total": 3, "severity": {"high": 1, "medium": 1, "low": 1}, "category": {"offline": 3}}
    now = time.time()
    for i in range(5):
        db.add_scan(now - (5 - i) * 3600, 60 + i, counts)
    db.add_scan(now - 400 * 86400, 10, counts)  # older than a year: dropped by the next scan
    db.add_scan(now, 70, counts)
    trend = db.trend(now - 86400)
    assert trend["score"] == [60, 61, 62, 63, 64, 70] and trend["high"] == [1] * 6
    db.set_ignored("x", "A finding", True)
    assert db.ignored()["x"]["title"] == "A finding"
    db.set_ignored("x", "", False)
    assert db.ignored() == {}
    db.add_action("reload_entry", "Tado", True, [{"label": "Reload", "ok": True, "detail": ""}])
    assert db.actions()[0]["steps"][0]["label"] == "Reload"
    db.backup_to(tmp_path / "copy.db")
    assert sqlite3.connect(tmp_path / "copy.db").execute("SELECT COUNT(*) FROM scans").fetchone() == (6,)
    assert db.counts()["scans"] == 6 and db.counts()["size"] > 0
    db.set_ignored("y", "Kept", True)
    assert db.wipe() == {"scans": 6, "actions": 1} and db.counts()["ignored"] == 1
    db.close()


# ---------------------------------------------------------------------- the deep scan
async def test_deep_scan_reads_storage_and_lets_the_recorder_delete(home, tmp_path, monkeypatch):
    mock, client = home
    folder = mock_ha.make_config_dir(tmp_path / "config", mock)
    before = (folder / "home-assistant_v2.db").read_bytes()

    # Off: nothing is read, nothing is found
    database = Database(tmp_path / "app.db")
    engine = Engine(Config(), database, client)
    await engine.scan()
    assert engine.snapshot.storage is None and engine.summary()["storage"] is None
    assert not any(f["kind"] == "history_without_entity" for f in engine.findings)

    monkeypatch.setattr("src.engine.HA_CONFIG_DIR", folder)
    engine = Engine(Config(deep_scan=True), database, client)
    await engine.scan()
    seen = engine.snapshot.storage
    assert seen["available"] and seen["database"]["entities"] == len(mock.states) + 4
    assert seen["database"]["orphans"] == [
        {"entity_id": "sensor.old_power_meter", "rows": 48210},
        {"entity_id": "sensor.attic_humidity", "rows": 9120},
        {"entity_id": "binary_sensor.garden_gate", "rows": 356},
        {"entity_id": "light.christmas_tree", "rows": 41},
    ]
    assert seen["registry"] == {
        "deleted_entities": 2,
        "deleted_devices": 1,
        "sample": ["sensor.replaced_plug_power", "switch.replaced_plug"],
    }
    assert (folder / "home-assistant_v2.db").read_bytes() == before  # looking changed nothing

    finding = next(f for f in engine.findings if f["kind"] == "history_without_entity")
    assert (
        finding["title"] == "Recorded history of 4 entities that no longer exist"
        and "57,727 stored states" in finding["text"]
    )
    assert finding["fixes"][0]["word"] == "DELETE" and finding["evidence"][0] == "sensor.old_power_meter: 48,210 states"

    # The recorder is asked, by pattern; the app itself deletes nothing
    result = await engine.repair(finding["id"], 0, "purge_history")
    assert result["ok"]
    assert (
        "recorder.purge_entities",
        {"entity_globs": [o["entity_id"] for o in seen["database"]["orphans"]], "keep_days": 0},
    ) in mock.calls
    assert engine.snapshot.storage["database"]["orphans"] == [] and not any(
        f["kind"] == "history_without_entity" for f in engine.findings
    )
    database.close()


def test_storage_that_is_not_there(tmp_path):
    assert storage.look(tmp_path / "nope", set()) == {
        "available": False,
        "note": f"Home Assistant's configuration folder is not at {tmp_path / 'nope'}.",
    }
    seen = storage.look(tmp_path, set())
    assert seen["available"] and seen["database"]["found"] is False  # another database than the built-in one
    assert seen["registry"] == {"deleted_entities": None, "deleted_devices": None, "sample": []}
    (tmp_path / "home-assistant_v2.db").write_text("not a database")
    assert "can't be" in storage.look(tmp_path, set())["database"]["error"]
