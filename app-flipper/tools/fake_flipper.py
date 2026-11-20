"""A stand-in Flipper on a pseudo terminal, for development and the tests.

It behaves like the real one as far as the app goes: a command line with a prompt, `start_rpc_session`, and
then the protobuf messages for the screen, the buttons, files and apps. Files live in a folder on this machine.
It was written from the firmware's source, not measured on a Flipper: passing against it shows the app is
consistent with itself and with that reading, nothing more.
"""

import asyncio
import contextlib
import hashlib
import os
import shutil
import time
import tty
from pathlib import Path

from src import proto

PROMPT = b"\r\n>: "
WIDTH, HEIGHT = 128, 64


def pack(pixels: list[list[int]]) -> bytes:
    """Rows of 0/1 to the Flipper's frame: a byte is eight pixels below each other, the top one in bit 0."""
    data = bytearray(WIDTH * HEIGHT // 8)
    for y, row in enumerate(pixels):
        for x, on in enumerate(row):
            if on:
                data[(y >> 3) * WIDTH + x] |= 1 << (y & 7)
    return bytes(data)


def draw(lines: list[str]) -> bytes:
    """A screen with a frame and some lines of text (text only when Pillow is there)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pixels = [[int(x in (0, WIDTH - 1) or y in (0, HEIGHT - 1)) for x in range(WIDTH)] for y in range(HEIGHT)]
        for n, line in enumerate(lines[:5]):
            for x in range(4, min(4 + len(line) * 4, WIDTH - 4)):
                pixels[8 + n * 11][x] = 1
        return pack(pixels)
    image = Image.new("1", (WIDTH, HEIGHT), 0)
    canvas = ImageDraw.Draw(image)
    canvas.rounded_rectangle((0, 0, WIDTH - 1, HEIGHT - 1), radius=4, outline=1)
    font = ImageFont.load_default()
    for n, line in enumerate(lines[:5]):
        canvas.text((5, 3 + n * 11), line, fill=1, font=font)
    raw = image.load()
    return pack([[int(bool(raw[x, y])) for x in range(WIDTH)] for y in range(HEIGHT)])


class FakeFlipper:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "ext" / "apps" / "Tools").mkdir(parents=True, exist_ok=True)
        (self.root / "int").mkdir(parents=True, exist_ok=True)
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)  # no echo, no line editing: bytes pass as they are
        os.set_blocking(self.master, False)
        self.path = os.ttyname(self.slave)
        self.binary = False
        self.buffer = bytearray()
        self.out = bytearray()
        self.streaming = False
        self.logging = False
        self.app = ""
        self.count = 0
        self.events: list[tuple[int, int]] = []
        self.clock: dict = {}
        self.sessions = 0
        self._log_task: asyncio.Task | None = None

    def start(self) -> None:
        asyncio.get_running_loop().add_reader(self.master, self._readable)
        self._send(b"\r\nWelcome to Flipper Zero Command Line Interface!\r\n" + PROMPT)

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            asyncio.get_running_loop().remove_reader(self.master)
            asyncio.get_running_loop().remove_writer(self.master)
        if self._log_task:
            self._log_task.cancel()
        os.close(self.master)
        os.close(self.slave)

    def _send(self, data: bytes) -> None:
        self.out += data
        self._flush()

    def _flush(self) -> None:
        """Write what fits; the rest when the reader has made room."""
        loop = asyncio.get_running_loop()
        while self.out:
            try:
                sent = os.write(self.master, self.out[:4096])
            except BlockingIOError:
                loop.add_writer(self.master, self._flush)
                return
            del self.out[:sent]
        loop.remove_writer(self.master)

    def _readable(self) -> None:
        try:
            self.buffer += os.read(self.master, 65536)
        except (BlockingIOError, OSError):
            return
        if self.binary:
            self._messages()
        else:
            self._text()

    # ---- the command line ----

    def _text(self) -> None:
        while not self.binary:
            if self.logging:
                if b"\x03" not in self.buffer:
                    self.buffer.clear()
                    return
                del self.buffer[: self.buffer.index(b"\x03") + 1]
                self.logging = False
                self._send(PROMPT)
                continue
            if self.buffer[:1] == b"\x03":
                del self.buffer[:1]
                self._send(b"^C" + PROMPT)
                continue
            if b"\r" not in self.buffer:
                return
            line, _, rest = bytes(self.buffer).partition(b"\r")
            self.buffer = bytearray(rest)
            command = line.decode("utf-8", "replace").strip()
            self._send(line + b"\r\n")
            if command == "start_rpc_session":
                self.binary = True
                self.sessions += 1
                self._messages()
                return
            self._send(self._run(command).replace("\n", "\r\n").encode() + PROMPT)

    def _run(self, command: str) -> str:
        if not command:
            return ""
        if command == "loader close":
            if not self.app:
                return "No application is running"
            self.app = ""
            return "Application closed"
        if command.startswith("log"):
            self.logging = True
            self._log_task = asyncio.create_task(self._log())
            return "Current log level: info\nUse <log ?> to list available log levels\nPress CTRL+C to stop..."
        if command == "device_info":
            return "\n".join(f"{k:<30}: {v}" for k, v in self.device_info().items())
        if command in ("help", "?"):
            return "Available commands:\ndevice_info  help  loader  log  start_rpc_session"
        return f"`{command.split()[0]}` command not found, use `help` or `?` to list all available commands"

    async def _log(self) -> None:
        n = 0
        while self.logging:
            n += 1
            self._send(f"{int(time.monotonic() * 1000)} [I][Fake] log line {n}\r\n".encode())
            await asyncio.sleep(1)

    # ---- the session ----

    @staticmethod
    def device_info() -> dict:
        return {
            "hardware_model": "Flipper Zero",
            "hardware_name": "Standin",
            "hardware_uid": "0123456789ABCDEF",
            "hardware_region": "EuRu",
            "hardware_target": "7",
            "firmware_version": "1.5.1",
            "firmware_branch": "release",
            "firmware_commit": "0000fake",
            "firmware_api_major": "87",
            "firmware_api_minor": "1",
            "radio_stack_major": "1",
        }

    def _local(self, path: str) -> Path:
        target = (self.root / path.lstrip("/")).resolve()
        if not target.is_relative_to(self.root.resolve()) or path.split("/")[1:2] not in (["ext"], ["int"]):
            raise KeyError("ERROR_STORAGE_INVALID_NAME")
        return target

    def _answer(self, command_id: int, name: str = "empty", body: dict | None = None, **head) -> None:
        self._send(proto.frame({"command_id": command_id, **head, name: body or {}}))

    def _fail(self, command_id: int, status: str) -> None:
        self._answer(command_id, command_status=proto.STATUS_CODE[status])

    def _screen(self) -> None:
        if not self.streaming:
            return
        if self.app:
            lines = [self.app.rsplit("/", 1)[-1], f"Pressed: {self.count}", "", "OK: count  Back: exit"]
        else:
            lines = ["Stand-in Flipper", time.strftime("%H:%M"), f"Buttons: {len(self.events)}", "", "No app running"]
        self._send(proto.frame({"gui_screen_frame": {"data": draw(lines), "orientation": 0}}))

    def _messages(self) -> None:
        while self.binary:
            try:
                message = proto.take_frame(self.buffer)
            except proto.ProtoError:
                self._fail(0, "ERROR_DECODE")
                self.binary = False
                self._send(PROMPT)
                return
            if message is None:
                return
            command_id = message.get("command_id", 0)
            name, body = proto.content(message)
            try:
                self._handle(command_id, name, body, message.get("has_next", False))
            except KeyError as e:
                self._fail(command_id, str(e.args[0]))
            except OSError:
                self._fail(command_id, "ERROR_STORAGE_INTERNAL")

    def _handle(self, command_id: int, name: str, body: dict, has_next: bool) -> None:
        if name == "stop_session":
            self._answer(command_id)
            self.binary = self.streaming = False
            self._send(PROMPT)
            self._text()
        elif name == "system_ping_request":
            self._answer(command_id, "system_ping_response", {"data": body.get("data", b"")})
        elif name in ("system_device_info_request", "system_power_info_request"):
            pairs = self.device_info() if "device" in name else {"charge_level": "87", "charge_state": "discharging"}
            answer = name.replace("request", "response")
            for n, (key, value) in enumerate(pairs.items()):
                self._answer(command_id, answer, {"key": key, "value": value}, has_next=n < len(pairs) - 1)
        elif name == "system_set_datetime_request":
            self.clock = body.get("datetime", {})
            self._answer(command_id)
        elif name == "system_play_audiovisual_alert_request":
            self._answer(command_id)
        elif name == "gui_start_screen_stream_request":
            if self.streaming:
                raise KeyError("ERROR_VIRTUAL_DISPLAY_ALREADY_STARTED")
            self.streaming = True
            self._answer(command_id)
            self._screen()
        elif name == "gui_send_input_event_request":
            key, kind = body.get("key", 0), body.get("type", 0)
            self.events.append((key, kind))
            if kind == proto.INPUT_SHORT and self.app:
                if key == proto.KEYS["back"]:
                    self.app = ""
                elif key == proto.KEYS["ok"]:
                    self.count += 1
            self._answer(command_id)
            if kind == proto.INPUT_SHORT:
                self._screen()
        elif name == "app_lock_status_request":
            self._answer(command_id, "app_lock_status_response", {"locked": bool(self.app)})
        elif name == "app_start_request":
            if self.app:
                raise KeyError("ERROR_APP_SYSTEM_LOCKED")
            target = body.get("name", "")
            if target.startswith("/") and not self._local(target).is_file():
                raise KeyError("ERROR_INVALID_PARAMETERS")
            self.app, self.count = body.get("args") or target, 0
            self._answer(command_id)
            self._screen()
        elif name.startswith("storage_"):
            self._storage(command_id, name, body, has_next)
        else:
            self._fail(command_id, "ERROR_NOT_IMPLEMENTED")

    def _storage(self, command_id: int, name: str, body: dict, has_next: bool) -> None:
        if name == "storage_rename_request":
            source, target = self._local(body.get("old_path", "")), self._local(body.get("new_path", ""))
            if not source.exists():
                raise KeyError("ERROR_STORAGE_NOT_EXIST")
            if target.exists():
                raise KeyError("ERROR_STORAGE_EXIST")
            source.rename(target)
            return self._answer(command_id)
        path = body.get("path", "")
        local = self._local(path)
        if name == "storage_info_request":
            self._answer(
                command_id, "storage_info_response", {"total_space": 31_900_000_000, "free_space": 27_000_000_000}
            )
        elif name == "storage_mkdir_request":
            if local.exists():
                raise KeyError("ERROR_STORAGE_EXIST")
            local.mkdir()
            self._answer(command_id)
        elif name == "storage_write_request":
            # The first part of a file starts it over; the parts that follow are added
            parts = getattr(self, "_writing", None)
            if parts is None or parts[0] != command_id:
                if not local.parent.is_dir():
                    raise KeyError("ERROR_STORAGE_NOT_EXIST")
                local.write_bytes(b"")
            with local.open("ab") as file:
                file.write(body.get("file", {}).get("data", b""))
            self._writing = (command_id,) if has_next else None
            if not has_next:
                self._answer(command_id)
        elif not local.exists():
            raise KeyError("ERROR_STORAGE_NOT_EXIST")
        elif name == "storage_stat_request":
            file = {"type": proto.DIR} if local.is_dir() else {"type": proto.FILE, "size": local.stat().st_size}
            self._answer(command_id, "storage_stat_response", {"file": file})
        elif name == "storage_list_request":
            entries = [
                {"type": proto.DIR, "name": p.name} if p.is_dir() else {"name": p.name, "size": p.stat().st_size}
                for p in sorted(local.iterdir())
            ]
            groups = [entries[n : n + 8] for n in range(0, len(entries), 8)] or [[]]
            for n, group in enumerate(groups):
                self._answer(command_id, "storage_list_response", {"file": group}, has_next=n < len(groups) - 1)
        elif name == "storage_read_request":
            data = local.read_bytes()
            chunks = [data[n : n + 512] for n in range(0, len(data), 512)] or [b""]
            for n, chunk in enumerate(chunks):
                self._answer(
                    command_id, "storage_read_response", {"file": {"data": chunk}}, has_next=n < len(chunks) - 1
                )
        elif name == "storage_md5sum_request":
            digest = hashlib.md5(local.read_bytes(), usedforsecurity=False).hexdigest()
            self._answer(command_id, "storage_md5sum_response", {"md5sum": digest})
        elif name == "storage_delete_request":
            if local.is_dir():
                if any(local.iterdir()) and not body.get("recursive"):
                    raise KeyError("ERROR_STORAGE_DIR_NOT_EMPTY")
                shutil.rmtree(local)
            else:
                local.unlink()
            self._answer(command_id)
        else:
            self._fail(command_id, "ERROR_NOT_IMPLEMENTED")
