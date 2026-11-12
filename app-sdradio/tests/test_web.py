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


def test_mp3_frame_start():
    from src.webui import mp3_frame_start

    frame = b"\xff\xfb\x94\x44" + b"\x00" * 10
    assert mp3_frame_start(frame) == 0
    assert mp3_frame_start(b"gwu\xff\x12DB2" + frame) == 8  # a stray 0xFF is not a frame
    assert mp3_frame_start(b"no sync here") is None
