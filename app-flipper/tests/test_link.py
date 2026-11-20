"""The link, against the stand-in Flipper on a pseudo terminal."""

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path

import pytest

from src import proto
from src.link import Flipper, FlipperError
from tools.fake_flipper import FakeFlipper, pack


class Bench:
    """A stand-in Flipper with the app's link connected to it."""

    def __init__(self):
        self.fake = FakeFlipper(Path(tempfile.mkdtemp(prefix="fake-flipper-")))
        self.flipper = Flipper(self.fake.path)
        self.events: list[tuple[str, object]] = []
        self.flipper.listener = lambda kind, payload: self.events.append((kind, payload))

    async def __aenter__(self):
        self.fake.start()
        self.task = asyncio.create_task(self.flipper.run())
        await self.until(lambda: self.flipper.state == "rpc")
        return self

    async def __aexit__(self, *exc):
        await self.flipper.close()
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        self.fake.stop()

    @staticmethod
    async def until(done, seconds: float = 8):
        for _ in range(int(seconds * 50)):
            if done():
                return
            await asyncio.sleep(0.02)
        raise AssertionError("timed out")


async def test_connects_and_reads_the_device():
    async with Bench() as bench:
        assert bench.flipper.info["hardware_name"] == "Standin"
        assert bench.flipper.power["charge_level"] == "87"
        assert bench.flipper.storage["total"] == 31_900_000_000
        # The screen streams from the start
        await bench.until(lambda: bench.flipper.frame is not None)
        data, orientation = bench.flipper.frame
        assert len(data) == 1024 and orientation == 0


async def test_files_round_trip():
    async with Bench() as bench:
        flipper = bench.flipper
        data = os.urandom(70_000)  # many parts, and more than the terminal's buffer holds
        await flipper.mkdir("/ext/apps/Deep/Er", parents=True)
        await flipper.write("/ext/apps/Deep/Er/blob.bin", data)
        assert (bench.fake.root / "ext/apps/Deep/Er/blob.bin").read_bytes() == data
        assert await flipper.read("/ext/apps/Deep/Er/blob.bin") == data
        await flipper.write("/ext/empty.txt", b"")
        assert await flipper.read("/ext/empty.txt") == b""
        names = {e["name"]: e for e in await flipper.list("/ext")}
        assert names["apps"]["dir"] and names["empty.txt"]["size"] == 0
        await flipper.rename("/ext/empty.txt", "/ext/renamed.txt")
        assert await flipper.stat("/ext/empty.txt") is None
        assert await flipper.stat("/ext/renamed.txt") == {"dir": False, "size": 0}
        await flipper.delete("/ext/apps/Deep")
        assert await flipper.stat("/ext/apps/Deep") is None
        with pytest.raises(FlipperError, match="no such file"):
            await flipper.read("/ext/nothing.txt")
        with pytest.raises(FlipperError, match="larger"):
            await flipper.write("/ext/big.bin", bytes(5000))
            await flipper.read("/ext/big.bin", limit=1024)


async def test_many_files_listed_in_parts():
    async with Bench() as bench:
        for n in range(20):
            (bench.fake.root / "ext" / f"f{n:02}.txt").write_text("x" * n)
        listed = await bench.flipper.list("/ext")
        assert len([e for e in listed if not e["dir"]]) == 20


async def test_a_short_press_is_press_short_release():
    async with Bench() as bench:
        for kind in ("press", "short", "release"):
            await bench.flipper.press("ok", kind)
        assert bench.fake.events == [(4, proto.INPUT_PRESS), (4, proto.INPUT_SHORT), (4, proto.INPUT_RELEASE)]
        with pytest.raises(FlipperError):
            await bench.flipper.press("power", "short")


async def test_apps_start_and_are_closed_through_the_shell():
    async with Bench() as bench:
        flipper, fake = bench.flipper, bench.fake
        await flipper.write("/ext/apps/Tools/x.fap", b"fap")
        await flipper.start_app("/ext/apps/Tools/x.fap")
        assert fake.app == "/ext/apps/Tools/x.fap"
        with pytest.raises(FlipperError, match="another app is running") as error:
            await flipper.start_app("/ext/apps/Tools/x.fap")
        assert error.value.status == "ERROR_APP_SYSTEM_LOCKED"
        sessions = fake.sessions
        assert await flipper.close_app() is True
        assert fake.app == "" and fake.sessions == sessions + 1
        # In a session again, and the screen streams again
        assert fake.streaming
        assert await flipper.list("/int") == []
        assert await flipper.close_app() is False


async def test_console_and_back():
    async with Bench() as bench:
        flipper = bench.flipper
        await flipper.open_console()
        assert flipper.state == "console"
        with pytest.raises(FlipperError, match="console is open"):
            await flipper.list("/ext")
        bench.events.clear()
        await flipper.console_send("log\r")
        text = lambda: "".join(p for k, p in bench.events if k == "console")  # noqa: E731
        await bench.until(lambda: "log line 1" in text())
        # Closing stops the log and goes back to a session
        await flipper.close_console()
        assert flipper.state == "rpc" and not bench.fake.logging
        assert (await flipper.stat("/ext"))["dir"]


async def test_takes_over_a_session_left_open():
    """The app was stopped without ending its session: the port still speaks messages when it is opened again."""
    fake = FakeFlipper(Path(tempfile.mkdtemp(prefix="fake-flipper-")))
    fake.start()
    fake.binary = True
    flipper = Flipper(fake.path)
    task = asyncio.create_task(flipper.run())
    try:
        await Bench.until(lambda: flipper.state == "rpc")
        assert flipper.info["firmware_version"] == "1.5.1"
    finally:
        await flipper.close()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        fake.stop()


async def test_release_and_take_again():
    async with Bench() as bench:
        await bench.flipper.release(True)
        await bench.until(lambda: bench.flipper.state == "released")
        await bench.until(lambda: not bench.fake.binary)
        await bench.flipper.release(False)
        await bench.until(lambda: bench.flipper.state == "rpc")


def test_frame_packing_matches_the_firmware():
    # pixel(x, y) = (data[(y >> 3) * 128 + x] >> (y & 7)) & 1
    pixels = [[0] * 128 for _ in range(64)]
    pixels[0][0] = pixels[9][5] = pixels[63][127] = 1
    data = pack(pixels)
    assert data[0] == 1 and data[128 + 5] == 2 and data[7 * 128 + 127] == 0x80


def test_messages_match_the_official_encoder():
    # Bytes produced by protoc's own Python classes for the same messages
    assert proto.frame({"command_id": 4, "stop_session": {}}).hex() == "0508049a0100"
    message = {"command_id": 7, "has_next": True, "storage_write_request": {"path": "/ext/a", "file": {"data": b"hi"}}}
    decoded = proto.decode("Main", proto.encode("Main", message))
    assert decoded == message
    buffer = bytearray(proto.frame(message)[:-1])
    assert proto.take_frame(buffer) is None  # not all there yet
