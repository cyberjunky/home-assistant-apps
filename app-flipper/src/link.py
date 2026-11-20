"""The link to the Flipper over its USB serial port.

The Flipper has one serial port with one shell on it. The shell is a text command line; its command
`start_rpc_session` turns the port into a stream of protobuf messages (screen, buttons, files, apps) until the
session is stopped. Text and messages never run at the same time, so the link is in one of two modes:

- rpc: the normal one. The screen is streamed, buttons can be pressed, files read and written.
- console: the text command line, for the log and for commands. The screen and files wait meanwhile.
"""

import asyncio
import contextlib
import glob
import logging
import os
import termios
import time
import tty
from collections.abc import Callable
from pathlib import Path

from . import proto

logger = logging.getLogger(__name__)

PROMPT = b">: "
USB_IDS = ("0483", "5740")
CHUNK = 512  # the most file data one message may carry (rpc_storage.c MAX_DATA_SIZE)
POLL_SECONDS = 15


class FlipperError(Exception):
    """A request the Flipper refused, or one it never answered."""

    def __init__(self, message: str, status: str = ""):
        super().__init__(message)
        self.status = status


def find_port(configured: str = "") -> str | None:
    """The Flipper's serial port: the configured one, or the first Flipper found."""
    if configured:
        return configured if os.path.exists(configured) else None
    by_id = sorted(glob.glob("/dev/serial/by-id/usb-Flipper_Devices_Inc._Flipper_*"))
    if by_id:
        return by_id[0]
    # Without udev's by-id links: ask the kernel which ttyACM is a Flipper
    for tty_dir in sorted(glob.glob("/sys/class/tty/ttyACM*")):
        try:
            usb = (Path(tty_dir) / "device").resolve().parent
            ids = ((usb / "idVendor").read_text().strip(), (usb / "idProduct").read_text().strip())
        except OSError:
            continue
        if ids == USB_IDS and os.path.exists(f"/dev/{Path(tty_dir).name}"):
            return f"/dev/{Path(tty_dir).name}"
    return None


class Flipper:
    def __init__(self, device: str = ""):
        self.device = device
        self.state = "absent"  # absent, connecting, rpc, console, released
        self.error = ""
        self.port = ""
        self.info: dict[str, str] = {}
        self.power: dict[str, str] = {}
        self.storage: dict[str, int] = {}
        self.frame: tuple[bytes, int] | None = None
        self.busy = ""  # what a long job is doing, for the status line
        # Called with (kind, payload): "state" {}, "frame" (data, orientation), "console" text
        self.listener: Callable[[str, object], None] = lambda kind, payload: None
        self._fd: int | None = None
        self._binary = False
        self._text = bytearray()
        self._frames = bytearray()
        self._arrived = asyncio.Event()
        self._lost = asyncio.Event()
        self._wake = asyncio.Event()
        self._pending: dict[int, tuple[asyncio.Future, list[dict]]] = {}
        self._next_id = 0
        self._rpc_lock = asyncio.Lock()  # one request at a time: a file written in parts must not be interleaved
        self._mode_lock = asyncio.Lock()  # one change of mode at a time
        self._write_lock = asyncio.Lock()
        self._released = False
        self._console_wanted = False

    # ---- state ----

    def status(self) -> dict:
        return {
            "state": self.state,
            "error": self.error,
            "port": self.port,
            "busy": self.busy,
            "info": self.info,
            "power": self.power,
            "storage": self.storage,
        }

    def _set_state(self, state: str, error: str = "") -> None:
        if (state, error) != (self.state, self.error):
            self.state, self.error = state, error
            self.listener("state", {})

    def set_busy(self, text: str) -> None:
        self.busy = text
        self.listener("state", {})

    # ---- the port ----

    def _open(self, path: str) -> None:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            attrs[2] |= termios.CLOCAL | termios.CREAD
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except termios.error:
            pass  # not a terminal that takes these settings; the bytes still pass
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        self._binary = False
        self._text.clear()
        self._frames.clear()
        self._lost.clear()
        asyncio.get_running_loop().add_reader(fd, self._readable)

    def _close(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self._fd)
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
        for future, _ in self._pending.values():
            if not future.done():
                future.set_exception(FlipperError("the Flipper was disconnected"))
        self._pending.clear()
        self.frame = None

    def _readable(self) -> None:
        try:
            data = os.read(self._fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            # Unplugged, or rebooting
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self._fd)
            self._lost.set()
            self._arrived.set()
            return
        if self._binary:
            self._frames += data
            self._take_frames()
        else:
            self._text += data
            if self.state == "console":
                self.listener("console", data.decode("utf-8", "replace"))
        self._arrived.set()

    async def _write(self, data: bytes) -> None:
        async with self._write_lock:
            view = memoryview(data)
            while view:
                if self._fd is None:
                    raise FlipperError("the Flipper is not connected")
                try:
                    sent = os.write(self._fd, view)
                except BlockingIOError:
                    await asyncio.sleep(0.004)
                    continue
                except OSError as e:
                    self._lost.set()
                    raise FlipperError("the Flipper was disconnected") from e
                view = view[sent:]

    # ---- text mode ----

    async def _read_until(self, marker: bytes, timeout: float) -> bytes:
        """Wait for the marker in what the shell prints; what came up to and including it."""
        deadline = time.monotonic() + timeout
        while True:
            at = self._text.find(marker)
            if at >= 0:
                out = bytes(self._text[: at + len(marker)])
                del self._text[: at + len(marker)]
                return out
            if self._lost.is_set():
                raise FlipperError("the Flipper was disconnected")
            left = deadline - time.monotonic()
            if left <= 0:
                raise FlipperError("the Flipper's command line did not answer")
            self._arrived.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._arrived.wait(), left)

    async def _command(self, line: str, timeout: float = 5) -> str:
        """Run one command on the shell and return what it printed."""
        self._text.clear()
        await self._write(line.encode() + b"\r")
        out = (await self._read_until(PROMPT, timeout))[: -len(PROMPT)].decode("utf-8", "replace")
        # The shell echoes the command first
        return out.split("\n", 1)[1].strip() if "\n" in out else ""

    async def _find_prompt(self) -> None:
        """Get the shell to a prompt, whatever it was doing when the port was opened."""
        # A freshly opened port prints its banner and a prompt by itself
        with contextlib.suppress(FlipperError):
            await self._read_until(PROMPT, 1.2)
            return
        if self._lost.is_set():
            raise FlipperError("the Flipper was disconnected")
        # Something is running (a log, or a session an earlier run left open): stop it
        self._text.clear()
        await self._write(b"\x03\r")
        with contextlib.suppress(FlipperError):
            await self._read_until(PROMPT, 1.2)
            return
        self._text.clear()
        await self._write(proto.frame({"command_id": 1, "stop_session": {}}))
        await asyncio.sleep(0.3)
        await self._write(b"\r")
        await self._read_until(PROMPT, 2)

    # ---- rpc mode ----

    async def _start_session(self) -> None:
        await asyncio.sleep(0.1)
        self._text.clear()
        await self._write(b"start_rpc_session\r")
        await self._read_until(b"\n", 3)  # the echoed command; messages from here on
        self._frames += self._text
        self._text.clear()
        self._binary = True
        self._next_id = 0
        try:
            await self.request("system_ping_request", {"data": b"\x01\x02"}, timeout=3)
        except FlipperError as e:
            raise FlipperError(f"the Flipper did not start a session ({e})") from e

    async def _stop_session(self) -> None:
        """Back to the shell."""
        with contextlib.suppress(FlipperError):
            await self.request("stop_session", timeout=2)
        self._binary = False
        self._text.clear()
        self._frames.clear()
        self.frame = None
        await asyncio.sleep(0.1)
        await self._write(b"\r")
        await self._read_until(PROMPT, 2)

    def _take_frames(self) -> None:
        while True:
            try:
                message = proto.take_frame(self._frames)
            except proto.ProtoError as e:
                logger.warning("Unreadable message from the Flipper (%s): reconnecting", e)
                self._lost.set()
                return
            if message is None:
                return
            name, body = proto.content(message)
            if name == "gui_screen_frame":
                self.frame = (body.get("data", b""), body.get("orientation", 0))
                self.listener("frame", self.frame)
                continue
            entry = self._pending.get(message.get("command_id", 0))
            if entry is None:
                if proto.status_name(message) == "ERROR_DECODE":
                    logger.warning("The Flipper could not read a message and closed the session: reconnecting")
                    self._lost.set()
                continue
            future, parts = entry
            parts.append(message)
            if not message.get("has_next") and not future.done():
                future.set_result(parts)

    async def request(
        self, name: str, body: dict | None = None, *, timeout: float = 10, frames: list[dict] | None = None
    ) -> list[dict]:
        """Send one request and return the bodies of its answer (several when the Flipper sends it in parts).

        `frames` sends a request that itself comes in parts (a file being written): each is a body of `name`.
        """
        if not self._binary or self._fd is None:
            raise FlipperError(
                "the console is open: close it first" if self.state == "console" else "the Flipper is not connected"
            )
        async with self._rpc_lock:
            if not self._binary:
                raise FlipperError("the Flipper is not connected")
            self._next_id = self._next_id % 0x7FFFFFFF + 1
            command_id = self._next_id
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[command_id] = (future, [])
            try:
                bodies = frames if frames is not None else [body or {}]
                for n, part in enumerate(bodies):
                    message = {"command_id": command_id, "has_next": n < len(bodies) - 1, name: part}
                    await self._write(proto.frame(message))
                try:
                    parts = await asyncio.wait_for(future, timeout)
                except TimeoutError:
                    # No way to tell what state the session is in now: start over
                    self._lost.set()
                    raise FlipperError("the Flipper did not answer") from None
            finally:
                self._pending.pop(command_id, None)
        status = proto.status_name(parts[-1])
        if status != "OK":
            raise FlipperError(
                proto.STATUS_TEXT.get(status, status.removeprefix("ERROR_").replace("_", " ").lower()), status
            )
        return [proto.content(part)[1] for part in parts]

    # ---- the device ----

    async def refresh(self) -> None:
        """Read what the status line shows: the device, its battery and the SD card."""
        pairs = await self.request("system_device_info_request")
        self.info = {p["key"]: p.get("value", "") for p in pairs if p.get("key")}
        await self.refresh_power()

    async def refresh_power(self) -> None:
        pairs = await self.request("system_power_info_request")
        self.power = {p["key"]: p.get("value", "") for p in pairs if p.get("key")}
        try:
            space = (await self.request("storage_info_request", {"path": "/ext"}))[0]
            self.storage = {"total": space.get("total_space", 0), "free": space.get("free_space", 0)}
        except FlipperError:
            self.storage = {}  # no SD card
        self.listener("state", {})

    async def press(self, key: str, kind: str = "short") -> None:
        """One event of a button. A short press is press, short, release; a long one press, long, release."""
        types = {
            "press": proto.INPUT_PRESS,
            "release": proto.INPUT_RELEASE,
            "short": proto.INPUT_SHORT,
            "long": proto.INPUT_LONG,
            "repeat": proto.INPUT_REPEAT,
        }
        if key not in proto.KEYS or kind not in types:
            raise FlipperError("unknown button")
        await self.request("gui_send_input_event_request", {"key": proto.KEYS[key], "type": types[kind]}, timeout=4)

    async def set_clock(self) -> None:
        now = time.localtime()
        clock = {
            "hour": now.tm_hour,
            "minute": now.tm_min,
            "second": now.tm_sec,
            "day": now.tm_mday,
            "month": now.tm_mon,
            "year": now.tm_year,
            "weekday": now.tm_wday + 1,
        }
        await self.request("system_set_datetime_request", {"datetime": clock})

    async def alert(self) -> None:
        await self.request("system_play_audiovisual_alert_request")

    async def reboot(self) -> None:
        """Restart the Flipper. It does not answer: it restarts, and the link finds it again."""
        async with self._rpc_lock:
            if not self._binary:
                raise FlipperError("the Flipper is not connected")
            message = {"command_id": 0x7FFFFFF0, "system_reboot_request": {"mode": proto.REBOOT_OS}}
            await self._write(proto.frame(message))

    # ---- files ----

    async def list(self, path: str) -> list[dict]:
        parts = await self.request("storage_list_request", {"path": path}, timeout=30)
        files = [f for part in parts for f in part.get("file", [])]
        return [
            {"name": f.get("name", ""), "dir": f.get("type", 0) == proto.DIR, "size": f.get("size", 0)} for f in files
        ]

    async def stat(self, path: str) -> dict | None:
        try:
            file = (await self.request("storage_stat_request", {"path": path}))[0].get("file", {})
        except FlipperError as e:
            if e.status in ("ERROR_STORAGE_NOT_EXIST", "ERROR_STORAGE_INVALID_NAME"):
                return None
            raise
        return {"dir": file.get("type", 0) == proto.DIR, "size": file.get("size", 0)}

    async def read(self, path: str, limit: int = 0) -> bytes:
        if limit:
            found = await self.stat(path)
            if found and found["size"] > limit:
                raise FlipperError(f"the file is larger than {limit // 1024} KB")
        parts = await self.request("storage_read_request", {"path": path}, timeout=600)
        return b"".join(part.get("file", {}).get("data", b"") for part in parts)

    async def write(self, path: str, data: bytes) -> None:
        chunks = [data[n : n + CHUNK] for n in range(0, len(data), CHUNK)] or [b""]
        frames = [{"path": path, "file": {"data": chunk}} for chunk in chunks]
        await self.request("storage_write_request", frames=frames, timeout=20 + len(data) / 5000)

    async def md5(self, path: str) -> str:
        return (await self.request("storage_md5sum_request", {"path": path}, timeout=60))[0].get("md5sum", "")

    async def mkdir(self, path: str, parents: bool = False) -> None:
        if parents:
            # /ext/apps/Tools: make /ext/apps, then /ext/apps/Tools; one that is there already is fine
            pieces = path.strip("/").split("/")
            for depth in range(2, len(pieces) + 1):
                with contextlib.suppress(_Exists):
                    await self._mkdir("/" + "/".join(pieces[:depth]))
            return
        await self._mkdir(path)

    async def _mkdir(self, path: str) -> None:
        try:
            await self.request("storage_mkdir_request", {"path": path})
        except FlipperError as e:
            if e.status == "ERROR_STORAGE_EXIST":
                raise _Exists(str(e), e.status) from e
            raise

    async def delete(self, path: str) -> None:
        await self.request("storage_delete_request", {"path": path, "recursive": True}, timeout=120)

    async def rename(self, old: str, new: str) -> None:
        await self.request("storage_rename_request", {"old_path": old, "new_path": new})

    # ---- apps ----

    async def start_app(self, name: str, args: str = "") -> None:
        """Start an app: a built-in one by name, or a .fap by its path on the SD card."""
        await self.request("app_start_request", {"name": name, "args": args}, timeout=20)

    async def close_app(self) -> bool:
        """Close whatever app is running. The session has no request for that; the shell has `loader close`."""
        async with self._mode_lock:
            if self.state != "rpc":
                raise FlipperError("the Flipper is not connected")
            closed = False
            try:
                await self._stop_session()
                for _ in range(10):
                    answer = await self._command("loader close")
                    if "No application" in answer:
                        break
                    closed = True
                    await asyncio.sleep(0.2)
            finally:
                await self._resume()
            return closed

    async def _resume(self) -> None:
        """Into a session again, with the screen streaming."""
        try:
            await self._start_session()
            await self.request("gui_start_screen_stream_request")
        except FlipperError:
            self._lost.set()
            raise

    # ---- console ----

    async def open_console(self) -> None:
        async with self._mode_lock:
            if self.state == "console":
                return
            if self.state != "rpc":
                raise FlipperError("the Flipper is not connected")
            await self._stop_session()
            self._console_wanted = True
            self._set_state("console")
            self.listener("console", "\r\n>: ")

    async def close_console(self) -> None:
        async with self._mode_lock:
            if self.state != "console":
                return
            self._console_wanted = False
            self._set_state("connecting")
            try:
                # Stop a running log or script, and wait for the prompt
                self._text.clear()
                await self._write(b"\x03")
                await asyncio.sleep(0.2)
                self._text.clear()
                await self._write(b"\r")
                await self._read_until(PROMPT, 3)
                await self._resume()
            except FlipperError:
                self._lost.set()
                raise
            self._set_state("rpc")

    async def console_send(self, text: str) -> None:
        if self.state != "console":
            raise FlipperError("the console is not open")
        del self._text[:]  # nothing waits on it in this mode; don't let a long log pile up
        await self._write(text.encode())

    # ---- connecting ----

    async def release(self, released: bool) -> None:
        """Let go of the Flipper (it cannot change its USB mode while a session holds it), or take it again."""
        self._released = released
        self._lost.set()
        self._wake.set()

    async def run(self) -> None:
        """Find the Flipper, hold a session with it, and find it again when it goes away."""
        try:
            while True:
                if self._released:
                    self._set_state("released")
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                path = find_port(self.device)
                if not path:
                    self.info, self.power, self.storage, self.port = {}, {}, {}, ""
                    self._set_state("absent")
                    await self._nap(2)
                    continue
                self.port = path
                self._set_state("connecting")
                try:
                    self._open(path)
                    await self._find_prompt()
                    await self._start_session()
                    await self.refresh()
                    await self.request("gui_start_screen_stream_request")
                except (FlipperError, OSError) as e:
                    self._close()
                    logger.warning("Could not connect to the Flipper on %s: %s", path, e)
                    self._set_state("absent", str(e))
                    await self._nap(4)
                    continue
                logger.info(
                    "Connected to %s (firmware %s) on %s",
                    self.info.get("hardware_name", "a Flipper"),
                    self.info.get("firmware_version", "?"),
                    path,
                )
                self._console_wanted = False
                self._set_state("rpc")
                await self._hold()
                if self._binary and not self._released:
                    logger.info("The Flipper is gone; waiting for it")
                if self._binary and self._released:
                    # Hand the port back cleanly, so the Flipper is free at once
                    with contextlib.suppress(FlipperError, OSError):
                        await asyncio.wait_for(self._write(proto.frame({"command_id": 1, "stop_session": {}})), 1)
                self._close()
                self._set_state("released" if self._released else "absent")
                await self._nap(0 if self._released else 1.5)
        finally:
            self._close()

    async def _hold(self) -> None:
        """Stay until the Flipper is lost; meanwhile keep the battery and SD card figures fresh."""
        while not self._lost.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._lost.wait(), POLL_SECONDS)
                return
            if self.state == "rpc" and not self._mode_lock.locked() and not self._rpc_lock.locked():
                with contextlib.suppress(FlipperError):
                    await self.refresh_power()

    async def _nap(self, seconds: float) -> None:
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)

    async def close(self) -> None:
        if self._binary and self._fd is not None:
            with contextlib.suppress(FlipperError, OSError, TimeoutError):
                await asyncio.wait_for(self._write(proto.frame({"command_id": 1, "stop_session": {}})), 1)
        self._close()


class _Exists(FlipperError):
    """The folder to make is already there."""
