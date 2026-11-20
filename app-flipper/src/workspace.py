"""The projects on this machine, the Flipper SDK that builds them, and getting a build onto the Flipper.

A project is a folder under the app's data folder. One with an application.fam is a C app, built to a .fap by
ufbt (Flipper's own build tool, which downloads the SDK and compiler on first use). One without is a set of
JavaScript files, which the Flipper runs as they are.
"""

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import zipfile
from collections import deque
from collections.abc import Callable
from pathlib import Path

from . import templates
from .config import DATA_DIR
from .link import Flipper, FlipperError

logger = logging.getLogger(__name__)

PROJECTS_DIR = DATA_DIR / "projects"
UFBT_HOME = DATA_DIR / "ufbt"
PROJECT_NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. -]{0,63}$")
# Not shown in a project's file list: build output and editor leftovers
HIDDEN = {"dist", ".ufbt", ".git", ".vscode", "__pycache__"}
MAX_FILE = 1024 * 1024
MAX_PROJECT = 32 * 1024 * 1024
CHANNELS = ("release", "rc", "dev")
# The download and what it unpacks to, to warn before a disk fills up
SDK_NEEDS = 2 * 1024**3


class WorkspaceError(Exception):
    """Something the person asked for that cannot be done; the message is for them."""


def _manifest_field(text: str, field: str) -> str:
    found = re.search(rf"^\s*{field}\s*=\s*([\"'])(.*?)\1", text, re.MULTILINE)
    return found.group(2) if found else ""


class Workspace:
    def __init__(self, flipper: Flipper):
        self.flipper = flipper
        self.log: deque[str] = deque(maxlen=3000)
        self.job = {"running": False, "what": "", "ok": None}
        # Called with (kind, payload): "build" line, "job" {}
        self.listener: Callable[[str, object], None] = lambda kind, payload: None
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    # ---- projects ----

    def _project(self, name: str) -> Path:
        if not PROJECT_NAME.match(name or ""):
            raise WorkspaceError("a project name is 2 to 31 lower-case letters, digits and _, starting with a letter")
        return PROJECTS_DIR / name

    def _existing(self, name: str) -> Path:
        folder = self._project(name)
        if not folder.is_dir():
            raise WorkspaceError(f"there is no project {name}")
        return folder

    def _file(self, name: str, path: str) -> Path:
        """A path inside a project; never one that leads out of it."""
        folder = self._existing(name)
        pieces = str(path or "").split("/")
        if not all(pieces) or len(pieces) > 6 or any(not FILE_NAME.match(p) or p in (".", "..") for p in pieces):
            raise WorkspaceError("not a valid file name")
        target = folder.joinpath(*pieces)
        if not target.resolve().is_relative_to(folder.resolve()):
            raise WorkspaceError("not a valid file name")
        return target

    def describe(self, name: str) -> dict:
        folder = self._existing(name)
        manifest = folder / "application.fam"
        info = {"name": name, "kind": "js", "title": name, "category": "", "appid": name}
        if manifest.is_file():
            text = manifest.read_text(errors="replace")
            appid = _manifest_field(text, "appid") or name
            info.update(
                kind="fap",
                appid=appid,
                title=_manifest_field(text, "name") or name,
                category=_manifest_field(text, "fap_category"),
            )
            built = folder / "dist" / f"{appid}.fap"
            if built.is_file():
                info["built"] = {"size": built.stat().st_size, "at": int(built.stat().st_mtime)}
        files = []
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if any(part in HIDDEN for part in relative.parts) or path.is_symlink() or not path.is_file():
                continue
            files.append({"path": relative.as_posix(), "size": path.stat().st_size})
        info["files"] = files
        return info

    def projects(self) -> list[dict]:
        out = []
        for folder in sorted(PROJECTS_DIR.iterdir()) if PROJECTS_DIR.is_dir() else []:
            if folder.is_dir() and PROJECT_NAME.match(folder.name):
                info = self.describe(folder.name)
                info["files"] = len(info["files"])
                out.append(info)
        return out

    def create(self, name: str, template: str, title: str = "") -> dict:
        folder = self._project(name)
        if folder.exists():
            raise WorkspaceError(f"there is already a project {name}")
        files = templates.render(template, name, title.strip() or name.replace("_", " ").title())
        if files is None:
            raise WorkspaceError("unknown template")
        folder.mkdir(parents=True)
        for path, text in files.items():
            (folder / path).write_text(text)
        return self.describe(name)

    def remove(self, name: str) -> None:
        shutil.rmtree(self._existing(name))

    def read(self, name: str, path: str) -> str:
        target = self._file(name, path)
        if not target.is_file():
            raise WorkspaceError("no such file")
        if target.stat().st_size > MAX_FILE:
            raise WorkspaceError("the file is too large to edit here")
        data = target.read_bytes()
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise WorkspaceError("not a text file") from None

    def write(self, name: str, path: str, text: str) -> None:
        target = self._file(name, path)
        if len(text.encode()) > MAX_FILE:
            raise WorkspaceError("the file is too large")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def write_bytes(self, name: str, path: str, data: bytes) -> None:
        target = self._file(name, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def delete_file(self, name: str, path: str) -> None:
        target = self._file(name, path)
        if not target.is_file():
            raise WorkspaceError("no such file")
        target.unlink()

    def rename_file(self, name: str, path: str, new_path: str) -> None:
        source, target = self._file(name, path), self._file(name, new_path)
        if not source.is_file():
            raise WorkspaceError("no such file")
        if target.exists():
            raise WorkspaceError("that name is taken")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)

    def export(self, name: str) -> bytes:
        folder = self._existing(name)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in self.describe(name)["files"]:
                archive.write(folder / file["path"], f"{name}/{file['path']}")
        return buffer.getvalue()

    def import_zip(self, name: str, data: bytes) -> dict:
        """A project from a zip: the files, with one enclosing folder taken off when there is one."""
        folder = self._project(name)
        if folder.exists():
            raise WorkspaceError(f"there is already a project {name}")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise WorkspaceError("not a zip file") from None
        members = [m for m in archive.infolist() if not m.is_dir()]
        if sum(m.file_size for m in members) > MAX_PROJECT or len(members) > 2000:
            raise WorkspaceError("the zip is too large")
        tops = {m.filename.split("/", 1)[0] for m in members if "/" in m.filename}
        strip = len(tops) == 1 and all("/" in m.filename for m in members)
        folder.mkdir(parents=True)
        try:
            count = 0
            for member in members:
                path = member.filename.split("/", 1)[1] if strip else member.filename
                if any(part in HIDDEN for part in path.split("/")):
                    continue
                try:
                    target = self._file(name, path)
                except WorkspaceError:
                    continue  # a name this app would not let you make: left out
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
                count += 1
            if not count:
                raise WorkspaceError("the zip holds no files this app can use")
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return self.describe(name)

    # ---- the SDK ----

    def sdk(self) -> dict:
        state = {}
        with contextlib.suppress(OSError, ValueError):
            state = json.loads((UFBT_HOME / "current" / "ufbt_state.json").read_text())
        toolchain = UFBT_HOME / "toolchain"
        usage = shutil.disk_usage(DATA_DIR)
        return {
            "installed": bool(state),
            # ufbt fetches the compiler the first time something is built, not with the SDK
            "compiler": toolchain.is_dir() and any(toolchain.iterdir()),
            "version": str(state.get("version") or ""),
            "channel": str(state.get("channel") or ""),
            "target": str(state.get("hw_target") or ""),
            "disk_free": usage.free,
            "needs": SDK_NEEDS,
            "available": shutil.which("ufbt") is not None,
        }

    async def install_sdk(self, channel: str) -> None:
        if channel not in CHANNELS:
            raise WorkspaceError("unknown channel")
        if not self.sdk()["installed"] and shutil.disk_usage(DATA_DIR).free < SDK_NEEDS:
            raise WorkspaceError("not enough free disk space for the SDK (about 2 GB)")
        await self._run(f"SDK ({channel})", ["ufbt", "update", f"--channel={channel}"], DATA_DIR)

    def remove_sdk(self) -> None:
        if self.job["running"]:
            raise WorkspaceError("wait for the running job")
        shutil.rmtree(UFBT_HOME, ignore_errors=True)
        self.listener("job", {})

    # ---- building ----

    def _say(self, line: str) -> None:
        self.log.append(line)
        self.listener("build", line)

    async def _run(self, what: str, command: list[str], cwd: Path) -> bool:
        """Run a tool, its output line by line to the build log. One at a time."""
        if self._lock.locked():
            raise WorkspaceError(f"wait for the running job ({self.job['what']})")
        async with self._lock:
            if shutil.which(command[0]) is None:
                raise WorkspaceError(f"{command[0]} is not installed here")
            self.log.clear()
            self.job = {"running": True, "what": what, "ok": None}
            self.listener("job", {})
            self._say(f"$ {' '.join(command)}")
            env = {**os.environ, "UFBT_HOME": str(UFBT_HOME), "PYTHONUNBUFFERED": "1", "TERM": "dumb"}
            ok = False
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=cwd,
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                # A download's progress bar redraws one line with \r, hundreds of times: of those only every
                # tenth percent is kept
                buffer, shown = b"", -1
                while chunk := await self._process.stdout.read(4096):
                    buffer += chunk
                    *pieces, buffer = re.split(rb"(\r\n|\n|\r)", buffer)
                    for line, end in zip(pieces[::2], pieces[1::2], strict=False):
                        text = line.decode("utf-8", "replace")
                        bar = re.search(r"#*\s*(\d+)\.\d%\s*$", text)
                        if end == b"\r" and bar:
                            tenth = int(bar.group(1)) // 10
                            if tenth != shown:
                                shown = tenth
                                self._say(f"  {tenth * 10}%")
                            continue
                        if bar and not text.strip("# 0123456789.%"):
                            continue  # the bar's last redraw
                        self._say(text)
                if buffer:
                    self._say(buffer.decode("utf-8", "replace"))
                code = await self._process.wait()
                ok = code == 0
                self._say("Done." if ok else f"Failed (exit code {code}).")
            except OSError as e:
                self._say(f"Could not run {command[0]}: {e}")
            finally:
                self._process = None
                self.job = {"running": False, "what": what, "ok": ok}
                self.listener("job", {})
            return ok

    def cancel(self) -> None:
        if self._process is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._process.pid, 15)

    async def build(self, name: str) -> Path | None:
        """Build a C app; the .fap when it worked."""
        info = self.describe(name)
        if info["kind"] != "fap":
            raise WorkspaceError("JavaScript needs no build: use Run")
        if not self.sdk()["installed"]:
            raise WorkspaceError("install the SDK first (on this page, under SDK)")
        folder = self._existing(name)
        if not await self._run(f"build {name}", ["ufbt"], folder):
            return None
        built = folder / "dist" / f"{info['appid']}.fap"
        if not built.is_file():
            self._say(f"The build finished, but there is no dist/{info['appid']}.fap.")
            return None
        return built

    # ---- onto the Flipper ----

    async def _free(self) -> None:
        """Close the app that is running on the Flipper, if one is: its file is in use, and a second cannot start."""
        locked = (await self.flipper.request("app_lock_status_request"))[0].get("locked", False)
        if locked:
            self._say("Closing the app that is running on the Flipper...")
            await self.flipper.close_app()

    async def _upload(self, data: bytes, target: str) -> None:
        self._say(f"Uploading {len(data):,} bytes to {target} ...")
        await self.flipper.mkdir(target.rsplit("/", 1)[0], parents=True)
        await self.flipper.write(target, data)
        if (await self.flipper.md5(target)) != hashlib.md5(data, usedforsecurity=False).hexdigest():
            raise FlipperError("the file on the Flipper differs from what was sent")

    async def launch(self, name: str, build: bool = True) -> str:
        """Build a C app (unless told not to), put it on the Flipper and start it. Where it went."""
        info = self.describe(name)
        folder = self._existing(name)
        built = folder / "dist" / f"{info['appid']}.fap"
        if build or not built.is_file():
            built = await self.build(name)
            if built is None:
                raise WorkspaceError("the build failed: see the output")
        target = f"/ext/apps/{info['category'] or 'Misc'}/{info['appid']}.fap"
        self.flipper.set_busy(f"starting {info['appid']}")
        try:
            await self._free()
            await self._upload(built.read_bytes(), target)
            self._say(f"Starting {target} ...")
            await self.flipper.start_app(target)
            self._say("Running on the Flipper.")
        except FlipperError as e:
            self._say(f"Flipper: {e}")
            raise
        finally:
            self.flipper.set_busy("")
        return target

    async def run_script(self, name: str, path: str) -> str:
        """Put a project's JavaScript files on the Flipper and run one of them."""
        folder = self._existing(name)
        script = self._file(name, path)
        if script.suffix != ".js" or not script.is_file():
            raise WorkspaceError("choose a .js file to run")
        scripts = [f["path"] for f in self.describe(name)["files"] if f["path"].endswith(".js")]
        base = f"/ext/apps/Scripts/{name}"
        self.log.clear()
        self.flipper.set_busy(f"running {script.name}")
        try:
            await self._free()
            for relative in scripts:
                await self._upload((folder / relative).read_bytes(), f"{base}/{relative}")
            target = f"{base}/{script.relative_to(folder).as_posix()}"
            self._say(f"Running {target} ...")
            await self.flipper.start_app("JS Runner", target)
            self._say("Running on the Flipper. What the script prints shows on the Flipper's screen.")
        except FlipperError as e:
            self._say(f"Flipper: {e}")
            raise
        finally:
            self.flipper.set_busy("")
        return target
