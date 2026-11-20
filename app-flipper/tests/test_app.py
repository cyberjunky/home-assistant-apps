"""Projects on this machine, and the API with a stand-in Flipper behind it."""

import asyncio
import contextlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import workspace as workspace_module
from src.config import Config
from src.link import Flipper
from src.webui import WebUI, _device_path
from src.workspace import Workspace, WorkspaceError
from tools.fake_flipper import FakeFlipper


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(workspace_module, "UFBT_HOME", tmp_path / "ufbt")
    return Workspace(Flipper("/dev/null-not-there"))


def test_templates_make_projects(workspace):
    app = workspace.create("my_app", "screen", 'My "App"')
    assert app["kind"] == "fap" and app["appid"] == "my_app" and app["category"] == "Tools"
    assert app["title"] == "My 'App'"  # a quote would end the string in the manifest and the C source
    assert [f["path"] for f in app["files"]] == ["application.fam", "my_app.c"]
    assert "int32_t my_app_app(void* p)" in workspace.read("my_app", "my_app.c")
    script = workspace.create("blink", "js")
    assert script["kind"] == "js" and [f["path"] for f in script["files"]] == ["blink.js"]
    assert [p["name"] for p in workspace.projects()] == ["blink", "my_app"]
    with pytest.raises(WorkspaceError, match="already"):
        workspace.create("blink", "js")
    for bad in ("", "A", "9lives", "../x", "has space", "x" * 40):
        with pytest.raises(WorkspaceError):
            workspace.create(bad, "js")
    with pytest.raises(WorkspaceError, match="template"):
        workspace.create("other", "nope")


def test_files_stay_inside_the_project(workspace, tmp_path):
    workspace.create("my_app", "minimal")
    (tmp_path / "secret.txt").write_text("secret")
    for bad in ("../secret.txt", "/etc/passwd", "a/../../secret.txt", "..", "", ".hidden", "a\\b", "x" * 80):
        with pytest.raises(WorkspaceError):
            workspace.read("my_app", bad)
        with pytest.raises(WorkspaceError):
            workspace.write("my_app", bad, "x")
    # A link planted in the project does not lead out of it either
    (workspace_module.PROJECTS_DIR / "my_app" / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(WorkspaceError):
        workspace.read("my_app", "link.txt")
    assert "link.txt" not in [f["path"] for f in workspace.describe("my_app")["files"]]

    workspace.write("my_app", "lib/util.h", "#pragma once\n")
    workspace.rename_file("my_app", "lib/util.h", "lib/tools.h")
    assert workspace.read("my_app", "lib/tools.h") == "#pragma once\n"
    with pytest.raises(WorkspaceError, match="taken"):
        workspace.rename_file("my_app", "my_app.c", "application.fam")
    workspace.delete_file("my_app", "lib/tools.h")
    workspace.write_bytes("my_app", "icon.png", b"\x89PNG\xff")
    with pytest.raises(WorkspaceError, match="not a text file"):
        workspace.read("my_app", "icon.png")


def test_export_and_import(workspace):
    workspace.create("my_app", "screen", "Mine")
    (workspace_module.PROJECTS_DIR / "my_app" / "dist").mkdir()
    (workspace_module.PROJECTS_DIR / "my_app" / "dist" / "my_app.fap").write_bytes(b"fap")
    data = workspace.export("my_app")
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["my_app/application.fam", "my_app/my_app.c"]
    copy = workspace.import_zip("copy_app", data)
    assert copy["kind"] == "fap" and copy["appid"] == "my_app" and len(copy["files"]) == 2

    hostile = io.BytesIO()
    with zipfile.ZipFile(hostile, "w") as archive:
        archive.writestr("../../escape.txt", "x")
        archive.writestr("/abs.txt", "x")
        archive.writestr("ok.js", "print(1);")
    made = workspace.import_zip("careful", hostile.getvalue())
    assert [f["path"] for f in made["files"]] == ["ok.js"]
    assert not (workspace_module.PROJECTS_DIR.parent / "escape.txt").exists()
    with pytest.raises(WorkspaceError, match="not a zip"):
        workspace.import_zip("nozip", b"hello")
    assert not (workspace_module.PROJECTS_DIR / "nozip").exists()


async def test_building_needs_the_sdk(workspace):
    workspace.create("my_app", "minimal")
    assert workspace.sdk()["installed"] is False
    with pytest.raises(WorkspaceError, match="install the SDK"):
        await workspace.build("my_app")
    workspace.create("blink", "js")
    with pytest.raises(WorkspaceError, match="needs no build"):
        await workspace.build("blink")


async def test_a_tools_output_reaches_the_log(workspace, tmp_path):
    lines = []
    workspace.listener = lambda kind, payload: lines.append(payload) if kind == "build" else None
    script = "import sys; sys.stdout.write('one\\n#  5.0%\\r## 12.5%\\r## 14.0%\\r### 100.0%\\ntwo\\n'); sys.exit(3)"
    assert await workspace._run("try", ["python3", "-c", script], tmp_path) is False
    assert lines[1:] == ["one", "  0%", "  10%", "two", "Failed (exit code 3)."]
    assert workspace.job == {"running": False, "what": "try", "ok": False}


def test_device_paths():
    assert _device_path("/ext/apps/Tools/x.fap") == "/ext/apps/Tools/x.fap"
    assert _device_path("/int") == "/int"
    for bad in ("", "/", "/any", "ext/x", "/ext/../int", "/ext//x", "/ext/x\n", "/ext/" + "a" * 300, None):
        with pytest.raises(WorkspaceError):
            _device_path(bad)


def test_options():
    assert Config.from_options({}).device == ""
    assert Config.from_options({"device": "/dev/ttyACM0", "log_level": "DEBUG"}) == Config("/dev/ttyACM0", "debug")
    with pytest.raises(ValueError, match="/dev"):
        Config.from_options({"device": "ttyACM0"})


class Rig:
    """The whole app on a test server, with a stand-in Flipper connected."""

    async def __aenter__(self):
        self.fake = FakeFlipper(Path(tempfile.mkdtemp(prefix="fake-flipper-")))
        self.fake.start()
        self.flipper = Flipper(self.fake.path)
        self.workspace = Workspace(self.flipper)
        self.client = TestClient(TestServer(WebUI(0, self.flipper, self.workspace).app))
        await self.client.start_server()
        self.task = asyncio.create_task(self.flipper.run())
        for _ in range(400):
            if self.flipper.state == "rpc":
                break
            await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc):
        await self.client.close()
        await self.flipper.close()
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        self.fake.stop()

    async def call(self, method: str, path: str, **kwargs) -> tuple[int, dict]:
        response = await self.client.request(method, path, **kwargs)
        return response.status, await response.json()


async def test_api_files_and_scripts(workspace):
    async with Rig() as rig:
        status, state = await rig.call("GET", "/api/state")
        assert status == 200 and state["flipper"]["state"] == "rpc" and "screen" in state["templates"]

        assert (await rig.call("POST", "/api/files/upload?path=/ext/note.txt", data=b"hello"))[0] == 200
        status, listing = await rig.call("GET", "/api/files?path=/ext")
        assert [e["name"] for e in listing["entries"]] == ["apps", "note.txt"]  # folders first
        download = await rig.client.get("/api/files/download?path=/ext/note.txt")
        assert await download.read() == b"hello"
        assert (await rig.call("POST", "/api/files/delete", json={"path": "/ext"}))[0] == 400
        assert (await rig.call("GET", "/api/files?path=/etc"))[0] == 400
        status, body = await rig.call("GET", "/api/files?path=/ext/nowhere")
        assert status == 502 and "no such file" in body["error"]

        # A JavaScript project: its files go to the Flipper and the open one is run
        assert (await rig.call("POST", "/api/projects", json={"name": "blink", "template": "js"}))[0] == 200
        assert (await rig.call("POST", "/api/projects/blink/run", json={"path": "blink.js"}))[0] == 200
        for _ in range(300):
            if rig.fake.app:
                break
            await asyncio.sleep(0.02)
        assert rig.fake.app == "/ext/apps/Scripts/blink/blink.js"
        assert (rig.fake.root / "ext/apps/Scripts/blink/blink.js").read_text() == rig.workspace.read(
            "blink", "blink.js"
        )
        # Running it again closes the one that runs first
        assert (await rig.call("POST", "/api/projects/blink/run", json={"path": "blink.js"}))[0] == 200
        for _ in range(400):
            if "Running on the Flipper" in "".join(rig.workspace.log) and not rig.flipper.busy:
                break
            await asyncio.sleep(0.02)
        assert rig.fake.app and rig.flipper.state == "rpc"
        assert (await rig.call("POST", "/api/projects/nothere/build"))[0] == 400


async def test_api_live_screen_and_buttons(workspace):
    async with Rig() as rig, rig.client.ws_connect("/api/ws") as ws:
        first = json.loads((await ws.receive()).data)
        assert first["t"] == "state" and first["flipper"]["info"]["hardware_name"] == "Standin"
        frame = (await ws.receive()).data
        assert isinstance(frame, bytes) and len(frame) == 1025 and frame[0] == 0
        for kind in ("press", "short", "release"):
            await ws.send_str(json.dumps({"t": "key", "key": "down", "kind": kind}))
        frame = await asyncio.wait_for(ws.receive(), 5)  # a short press redraws
        assert len(frame.data) == 1025
        assert [e[0] for e in rig.fake.events] == [1, 1, 1]
