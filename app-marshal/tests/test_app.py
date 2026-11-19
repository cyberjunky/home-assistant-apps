"""Reading folders and archives, a whole scan, and the dashboard's API."""

import io
import json
import tarfile

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import payload, sources
from src.catalog import Catalog
from src.config import Config
from src.engine import Engine
from src.homeassistant import HomeAssistantError
from src.webui import WebUI

from .conftest import HA_CONFIG


def integration(domain: str, code: str, **manifest) -> None:
    folder = HA_CONFIG / "custom_components" / domain
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(
        json.dumps({"domain": domain, "name": domain.title(), "version": "1.0", **manifest})
    )
    (folder / "__init__.py").write_text(code)
    (folder / "__pycache__").mkdir(exist_ok=True)
    (folder / "__pycache__" / "x.pyc").write_bytes(b"\0")


class FakeHomeAssistant:
    def __init__(self, state=None):
        self.state = state

    async def snapshot(self, session):
        if self.state is None:
            raise HomeAssistantError("not there")
        return self.state


def archive(members: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(f"repo-abc123/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer


def offline() -> Config:
    return Config(catalog_lookups=False, vulnerability_lookups=False)


def test_read_archive_takes_only_the_integration():
    files, root = sources.read_archive(
        archive(
            {
                "README.md": b"hello",
                "tests/test_x.py": b"eval(x)",
                "custom_components/demo/manifest.json": b'{"domain": "demo"}',
                "custom_components/demo/__init__.py": b"x = 1\n",
                "custom_components/demo/www/panel.js": b"a" * 5000,
            }
        )
    )
    assert root == "custom_components/demo"
    assert set(files) == {"manifest.json", "__init__.py", "www/panel.js"}
    assert files["www/panel.js"] == (5000, b"a" * 16)  # measured, not read


def test_read_archive_root_and_garbage():
    files, root = sources.read_archive(archive({"manifest.json": b"{}", "sensor.py": b"", "tests/t.py": b""}))
    assert root == "" and set(files) == {"manifest.json", "sensor.py"}
    with pytest.raises(sources.SourceError):
        sources.read_archive(archive({"README.md": b"nothing here"}))
    with pytest.raises(sources.SourceError):
        sources.read_archive(io.BytesIO(b"not an archive"))


@pytest.mark.parametrize("name", ["../etc/passwd", "owner", "owner/name/extra", "owner/na me", "-x/y", "o/.."])
async def test_bad_repository_names_never_reach_the_network(name):
    with pytest.raises(sources.SourceError):
        await sources.fetch_repository(None, name)


async def test_scan_installed_and_api(tmp_path):
    integration(
        "good",
        "async def async_setup_entry(hass, entry):\n    return True\n",
        codeowners=["@a"],
        issue_tracker="x",
        iot_class="local_push",
        config_flow=True,
    )
    integration("bad", "import base64\nexec(base64.b64decode(BLOB))\n")
    state = {
        "setup": {"good": 0.4, "bad": 31.0},
        "log": {"bad": {"errors": 50, "warnings": 2}},
        "marketplace": {"good": {"full_name": "someone/good", "custom": False, "pending_upgrade": False}},
    }
    engine = Engine(offline(), FakeHomeAssistant(state), Catalog(tmp_path / "catalog"))
    await engine.scan_installed()
    document = engine.payload()
    by_domain = {i["domain"]: i for i in document["integrations"]}
    assert by_domain["good"]["grade"] == "A" and by_domain["good"]["repository"] == "someone/good"
    assert by_domain["bad"]["grade"] == "F" and by_domain["bad"]["runtime"]["setup_seconds"] == 31.0
    assert {"code.obfuscated_exec", "performance.slow_setup", "performance.log_errors", "upkeep.manual"} <= {
        f["rule"] for f in by_domain["bad"]["findings"]
    }
    assert document["history"][-1]["integrations"] == 2 and not document["status"]["notes"]
    json.dumps(document)

    # Stored, and read back by the next start
    again = Engine(offline(), FakeHomeAssistant(state), Catalog(tmp_path / "catalog"))
    assert set(again.results["installed"]) == {"good", "bad"}
    assert again._fingerprint() == again.results["fingerprint"]
    integration("new", "x = 1\n")
    assert again._fingerprint() != again.results["fingerprint"]

    async with TestClient(TestServer(WebUI(0, engine).app)) as client:
        assert "MARSHAL" in await (await client.get("/")).text()
        assert len((await (await client.get("/api/report")).json())["integrations"]) == 2
        assert (await client.post("/api/scan")).status == 200
        assert (await client.post("/api/check", json={"repository": "../../etc"})).status == 400
        assert (await client.delete("/api/check/nobody/nothing")).status == 404
        export = await client.get("/api/export")
        page = await export.text()
        assert "attachment" in export.headers["Content-Disposition"]
        assert "window.MARSHAL_STATIC = {" in page and "static/app.js" not in page and "donate.js" not in page
    await engine.close()


async def test_scan_without_home_assistant_says_so(tmp_path):
    integration("good", "x = 1\n")
    engine = Engine(offline(), FakeHomeAssistant(None), Catalog(tmp_path / "catalog"))
    await engine.scan_installed()
    assert "Home Assistant could not be asked" in engine.payload()["status"]["notes"][0]
    assert all("runtime" not in i for i in engine.payload()["integrations"])
    await engine.close()


def test_static_page_cannot_be_broken_out_of():
    report = {"key": "k", "group": "g", "name": "</script><script>alert(1)</script>", "findings": []}
    page = payload.render_static(payload.build([report], [{"id": "g", "label": "G"}]))
    assert "</script><script>alert(1)" not in page and "<\\/script>" in page


def test_catalog_reads_its_cache(tmp_path):
    (tmp_path / "integration.json").write_text(
        json.dumps({"1": {"full_name": "A/One", "domain": "one"}, "2": {"full_name": "b/two", "domain": "two"}})
    )
    (tmp_path / "removed.json").write_text(
        json.dumps([{"repository": "x/gone", "removal_type": "critical", "reason": "r"}])
    )
    (tmp_path / "installs.json").write_text(
        json.dumps({"one": {"total": 5}, "two": {"total": 50}, "other": {"total": 99}})
    )
    catalog = Catalog(tmp_path)
    assert catalog.entry("a/one")["domain"] == "one" and catalog.entry(domain="two")["full_name"] == "b/two"
    assert catalog.removed("X/Gone")["reason"] == "r" and catalog.removed(None) is None
    assert [e["domain"] for e in catalog.top(5)] == ["two", "one"]
