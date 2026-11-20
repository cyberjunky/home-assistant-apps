"""The JSON API and the WebSocket, with the simulated receiver."""

import asyncio
import json
import struct

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.config import Config
from src.database import Database
from src.webui import WebUI
from tests.simulator import SimulatedDevice


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    db = Database(tmp_path / "sdradio.db")
    webui = WebUI(0, Config(), db, SimulatedDevice, data_dir=tmp_path)
    async with TestClient(TestServer(webui.app)) as c:
        c.webui = webui
        yield c
    db.close()


async def test_index_and_status(client):
    html = await (await client.get("/")).text()
    assert "SDRADIO" in html and "__BASE__" not in html
    status = await (await client.get("/api/status")).json()
    assert status["options"]["tune"]["frequency"] == 145.5
    assert "nfm" in status["modes"]


async def test_channels_api(client):
    response = await client.post("/api/channels", json={"name": "Calling", "frequency": 145_500_000, "mode": "nfm"})
    assert response.status == 201
    channel = await response.json()
    response = await client.put(f"/api/channels/{channel['id']}", json={"favourite": True})
    assert (await response.json())["favourite"] == 1
    bad = await client.post("/api/channels", json={"frequency": "x", "mode": "nfm"})
    assert bad.status == 400 and "number" in (await bad.json())["error"]
    assert (await client.put("/api/channels/999", json={})).status == 404
    csv = await (await client.get("/api/channels/export")).text()
    assert "Calling" in csv
    result = await (await client.post("/api/channels/import", data=csv.encode())).json()
    assert result == {"added": 0, "updated": 1}
    channels = (await (await client.get("/api/channels")).json())["channels"]
    assert len(channels) == 1
    assert (await client.delete(f"/api/channels/{channel['id']}")).status == 200


async def test_settings_bands_backups(client):
    data = await (await client.get("/api/settings")).json()
    assert data["settings"]["fft_size"] == 2048
    assert (await client.put("/api/settings", json={"fft_size": 7})).status == 400
    band = await (
        await client.post("/api/bands", json={"name": "6 m", "start": 50e6, "stop": 52e6, "mode": "usb"})
    ).json()
    assert (await client.delete(f"/api/bands/{band['id']}")).status == 200
    made = await (await client.post("/api/backups")).json()
    listing = await (await client.get("/api/backups")).json()
    assert listing["backups"][0]["name"] == made["name"]
    download = await client.get(f"/api/backups/{made['name']}")
    assert download.status == 200 and (await download.read()).startswith(b"SQLite format 3")
    assert (await client.post(f"/api/backups/{made['name']}/restore")).status == 200
    assert (await client.get("/api/backups/..%2Fsdradio.db")).status == 404
    stats = await (await client.get("/api/stats")).json()
    assert len(stats["hours"]) == 24 and stats["backups"] >= 1


async def test_websocket_streams_and_commands(client):
    ws = await client.ws_connect("/api/ws")
    first = json.loads((await ws.receive()).data)
    assert first["type"] == "state"
    await ws.send_json({"cmd": "spectrum", "on": True})
    await ws.send_json({"cmd": "listen", "on": True})
    await ws.send_json({"cmd": "tune", "frequency": 145_600_000})
    kinds, frequency = set(), None
    for _ in range(200):
        message = await asyncio.wait_for(ws.receive(), 5)
        if message.type.name == "BINARY":
            kinds.add(message.data[0])
            if message.data[0] == 1:
                header = struct.unpack("<BddffI", message.data[:29])
                assert len(message.data) - 29 == 2048 and header[2] == 2_400_000
        else:
            data = json.loads(message.data)
            if data["type"] == "state":
                frequency = data["state"]["frequency"]
        if kinds >= {1, 2} and frequency == 145_600_000:
            break
    assert kinds >= {1, 2}
    assert frequency == 145_600_000
    await ws.close()


async def test_the_page_escapes_quotes(client):
    """Values go into attributes and inline handlers (a sensor's key, an APRS call): escapeHtml must
    escape quotes too, or the handler is cut off (a Home Assistant tick that never saved)."""
    js = await (await client.get("/web/core.js")).text()
    body = js.split("function escapeHtml(text) {", 1)[1].split("\n}", 1)[0]
    assert "&quot;" in body and "&#39;" in body


async def test_status_says_how_long_the_app_runs(client):
    status = await (await client.get("/api/status")).json()
    assert 0 <= status["uptime"] < 60 and status["stream_port"] == 8098


async def test_aircraft_photo_only_when_switched_on(client, monkeypatch):
    from src.photos import Photos, photo_of

    asked = []

    async def ask(self, registration):
        asked.append(registration)
        return {"url": "https://t.plnspttrs.net/1.jpg", "link": "https://www.planespotters.net/photo/1",
                "photographer": "A. Spotter"}, 3600  # fmt: skip

    monkeypatch.setattr(Photos, "_ask", ask)
    assert (await client.get("/api/aircraft/photo/PH-BXA")).status == 403 and asked == []
    await client.put("/api/settings", json={"aircraft_photos": True})
    for _ in range(2):  # asked once: the second answer is the remembered one
        photo = (await (await client.get("/api/aircraft/photo/ph-bxa")).json())["photo"]
        assert photo["photographer"] == "A. Spotter"
    assert asked == ["PH-BXA"]
    assert (await client.get("/api/aircraft/photo/PH%20BXA%22")).status == 404
    # Only web addresses reach the page
    answer = {"photos": [{"thumbnail_large": {"src": "javascript:alert(1)"}, "link": "https://x.example/1"}]}
    assert photo_of(answer) is None and photo_of({"photos": []}) is None and photo_of("nonsense") is None
    answer["photos"][0]["thumbnail_large"]["src"] = "https://t.plnspttrs.net/2.jpg"
    answer["photos"][0]["link"] = "javascript:alert(1)"
    assert photo_of(answer) == {"url": "https://t.plnspttrs.net/2.jpg", "link": None, "photographer": ""}


async def test_mp3_stream(client, monkeypatch):
    # cat for lame: what goes in comes out, so the stream is the sound itself (silence when nothing plays)
    monkeypatch.setattr("src.webui.mp3_encoder", lambda rate: ["cat"])
    response = await client.get("/api/radio.mp3")
    assert response.status == 200 and response.headers["Content-Type"] == "audio/mpeg"
    assert len(await asyncio.wait_for(response.content.readexactly(8000), 5)) == 8000
    # A player of the stream counts as someone listening: the radio makes sound for it
    assert client.webui.radio.listeners == 1
    response.close()
    for _ in range(40):
        if client.webui.radio.listeners == 0:
            break
        await asyncio.sleep(0.1)
    assert client.webui.radio.listeners == 0 and not client.webui.streams


async def test_mp3_stream_on_its_own_port_only_when_switched_on(client, monkeypatch):
    monkeypatch.setattr("src.webui.mp3_encoder", lambda rate: ["cat"])
    async with TestClient(TestServer(client.webui.stream_app)) as port:
        assert (await port.get("/radio.mp3")).status == 403
        assert (await port.get("/api/status")).status == 404  # nothing but the stream
        await client.put("/api/settings", json={"mp3_stream": True})
        response = await port.get("/radio.mp3")
        assert response.status == 200
        assert len(await asyncio.wait_for(response.content.readexactly(4000), 5)) == 4000
        response.close()


def test_mp3_encoder_takes_the_rate_of_the_sound():
    from src.webui import mp3_encoder

    command = mp3_encoder(8_000)
    assert command[0] == "lame" and command[command.index("-s") + 1] == "8" and command[-2:] == ["-", "-"]
    assert mp3_encoder(48_780.5)[mp3_encoder(48_780.5).index("-s") + 1] == "48.7805"


def test_mp3_frame_start():
    from src.webui import mp3_frame_start

    frame = b"\xff\xfb\x94\x44" + b"\x00" * 10
    assert mp3_frame_start(frame) == 0
    assert mp3_frame_start(b"gwu\xff\x12DB2" + frame) == 8  # a stray 0xFF is not a frame
    assert mp3_frame_start(b"no sync here") is None


def test_no_function_is_defined_twice_in_the_page():
    """The page's scripts share one global scope: a second `function x` silently replaces the first
    (a band check once replaced the channel one, so no channel was ever faded)."""
    import re
    from collections import Counter
    from pathlib import Path

    names = Counter()
    for path in (Path(__file__).parent.parent / "src" / "web").glob("*.js"):
        names.update(re.findall(r"^(?:async )?function (\w+)\s*\(", path.read_text(), re.M))
    assert [n for n, count in names.items() if count > 1] == []
