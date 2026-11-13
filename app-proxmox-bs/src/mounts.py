"""The disks PBS set up, mounted the way systemd would mount them on a real PBS.

PBS's Disks page (Directory → Create: Directory) partitions and formats a disk, writes a mount unit
to /etc/systemd/system and asks systemctl to start it. An app has no systemd, so the app's own
systemctl (src/systemctl.py) mounts it with these functions, and the app keeps every such disk
mounted: after a restart, and again when it is plugged back in. While a disk is away, the datastores
on it are offline ("Disk not connected"), so Proxmox VE sees why its backups can't run, and the
empty mount point is locked so nothing is written to Home Assistant's own storage instead.
"""

import configparser
import contextlib
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import DATASTORE_CFG, MOUNTINFO, UNIT_DIR

logger = logging.getLogger(__name__)

OFFLINE_MESSAGE = "Disk not connected"
MANAGER = "proxmox-backup-manager"
# File systems the mount units may hold (PBS's tooling makes ext4 and xfs)
FILESYSTEMS = ("ext4", "xfs", "btrfs")


@dataclass
class Unit:
    name: str
    what: str
    where: str
    type: str
    options: str

    @property
    def device(self) -> str:
        """The device node it points to now, or '' while it isn't connected."""
        path = os.path.realpath(self.what)
        return path if self.what and os.path.exists(path) else ""


def run(*cmd: str, timeout: float = 120) -> tuple[int, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, f"{cmd[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} took longer than {timeout:.0f} seconds"
    return done.returncode, (done.stdout + done.stderr).strip()


# ----------------------------------------------------------------------
# Reading: mount units, the mount table, the datastores
# ----------------------------------------------------------------------
def parse_unit(name: str, text: str) -> Unit | None:
    """A systemd .mount unit as PBS writes it ([Mount] What=, Where=, Type=, Options=)."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    if not parser.has_section("Mount"):
        return None
    mount = parser["Mount"]
    what, where = mount.get("What", "").strip(), mount.get("Where", "").strip()
    if not what or not where.startswith("/"):
        return None
    return Unit(
        name, what, where, mount.get("Type", "").strip() or "auto", mount.get("Options", "").strip() or "defaults"
    )


def units(unit_dir: Path | None = None) -> list[Unit]:
    out = []
    for path in sorted((unit_dir or UNIT_DIR).glob("*.mount")):
        try:
            unit = parse_unit(path.name, path.read_text())
        except OSError:
            continue
        if unit:
            out.append(unit)
    return out


def find_unit(name: str, unit_dir: Path | None = None) -> Unit | None:
    if "/" in name:
        return None
    path = (unit_dir or UNIT_DIR) / name
    try:
        return parse_unit(name, path.read_text())
    except OSError:
        return None


def mount_table(text: str | None = None) -> dict[str, dict]:
    """What is mounted where: {'/mnt/datastore/x': {'device': '8:17', 'fstype': 'ext4', 'source': '/dev/sdb1'}}."""
    if text is None:
        try:
            text = MOUNTINFO.read_text()
        except OSError:
            return {}
    table: dict[str, dict] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 10 or "-" not in parts:
            continue
        sep = parts.index("-")
        target = parts[4].replace("\\040", " ")
        table[target] = {
            "device": parts[2],
            "fstype": parts[sep + 1] if len(parts) > sep + 1 else "",
            "source": parts[sep + 2] if len(parts) > sep + 2 else "",
        }
    return table


def datastores(text: str | None = None) -> list[dict]:
    """The datastores in PBS's datastore.cfg: [{'name', 'path', 'maintenance'}]."""
    if text is None:
        try:
            text = DATASTORE_CFG.read_text()
        except OSError:
            return []
    stores: list[dict] = []
    for line in text.splitlines():
        if line.startswith("datastore:"):
            stores.append({"name": line.split(":", 1)[1].strip(), "path": "", "maintenance": ""})
        elif stores and line[:1] in ("\t", " ") and line.strip():
            key, _, value = line.strip().partition(" ")
            if key == "path":
                stores[-1]["path"] = value.strip()
            elif key == "maintenance-mode":
                stores[-1]["maintenance"] = value.strip()
    return stores


def stores_on(where: str, stores: list[dict]) -> list[dict]:
    """The datastores that live on the disk mounted at `where`."""
    base = where.rstrip("/")
    return [s for s in stores if s["path"] == base or s["path"].startswith(base + "/")]


def ours(maintenance: str) -> bool:
    """Whether a maintenance mode is the one this app sets (one set by hand is left alone)."""
    return OFFLINE_MESSAGE in maintenance.replace("\\", "").replace('"', "")


# ----------------------------------------------------------------------
# Doing: mount, unmount, lock, offline
# ----------------------------------------------------------------------
def lock(where: str) -> None:
    """An empty mount point nobody but root may write into, for while its disk is away."""
    path = Path(where)
    with contextlib.suppress(OSError):
        path.mkdir(parents=True, exist_ok=True)
        if where not in mount_table():
            os.chown(path, 0, 0)
            os.chmod(path, 0o000)


def mount(unit: Unit) -> tuple[bool, str]:
    """Mount a unit's disk at its mount point, after checking an ext4 file system."""
    if unit.where in mount_table():
        return True, "already mounted"
    device = unit.device
    if not device:
        return False, f"{unit.what} is not connected"
    if unit.type not in FILESYSTEMS and unit.type != "auto":
        return False, f"file system {unit.type} is not supported"
    if unit.type == "ext4":
        # -p repairs what is safe to repair; 1 means something was fixed, 4 and up that it needs a hand
        code, out = run("e2fsck", "-p", device, timeout=3600)
        if code >= 4:
            last = out.splitlines()[-1] if out else ""
            return False, f"{device} has file system errors e2fsck -p can't fix ({last}); run e2fsck -f on it"
        if code:
            logger.warning(f"{unit.where}: e2fsck repaired the file system")
    Path(unit.where).mkdir(parents=True, exist_ok=True)
    code, out = run("mount", "-t", unit.type, "-o", unit.options, unit.what, unit.where, timeout=120)
    if code != 0:
        return False, out or f"mount failed ({code})"
    with contextlib.suppress(OSError):
        os.chmod(unit.where, 0o755)
    return True, "mounted"


def unmount(where: str) -> tuple[bool, str]:
    if where not in mount_table():
        lock(where)
        return True, "not mounted"
    run("sync", timeout=120)
    code, out = run("umount", where, timeout=120)
    if code != 0:
        # Something still has a file open: let go now, finish when it closes
        code, out = run("umount", "-l", where, timeout=60)
    lock(where)
    return code == 0, out or "unmounted"


def set_offline(store: str) -> bool:
    code, out = run(
        MANAGER, "datastore", "update", store, "--maintenance-mode", f"type=offline,message={OFFLINE_MESSAGE}"
    )
    if code != 0:
        logger.warning(f"Datastore {store}: can't set it offline: {out}")
    return code == 0


def set_online(store: str) -> bool:
    code, out = run(MANAGER, "datastore", "update", store, "--delete", "maintenance-mode")
    if code != 0:
        logger.warning(f"Datastore {store}: can't bring it online: {out}")
    return code == 0


# ----------------------------------------------------------------------
# The watcher
# ----------------------------------------------------------------------
class Watcher:
    """Keeps the disks of every mount unit mounted, and their datastores offline while they are away."""

    def __init__(self):
        self.reported: dict[str, str] = {}

    def _report(self, unit: Unit, state: str, message: str, level: int = logging.INFO) -> None:
        # Only when something changed, so the log isn't repeated every few seconds
        if self.reported.get(unit.name) != state:
            self.reported[unit.name] = state
            logger.log(level, f"{unit.where}: {message}")

    def check(self, unit_dir: Path | None = None) -> None:
        stores = datastores()
        for unit in units(unit_dir):
            mounted = unit.where in mount_table()
            on_disk = stores_on(unit.where, stores)
            if unit.device:
                if not mounted:
                    ok, message = mount(unit)
                    if not ok:
                        self._report(unit, "error", f"can't mount {unit.what}: {message}", logging.ERROR)
                        continue
                    self._report(unit, "mounted", f"mounted {unit.what}")
                else:
                    self._report(unit, "mounted", f"{unit.what} is mounted")
                for store in on_disk:
                    if ours(store["maintenance"]) and set_online(store["name"]):
                        logger.info(f"Datastore {store['name']}: online again")
            else:
                # Offline first: PBS lets go of the datastore, so the unmount is clean
                for store in on_disk:
                    if not store["maintenance"] and set_offline(store["name"]):
                        logger.warning(f"Datastore {store['name']}: offline until its disk is back")
                if mounted:
                    unmount(unit.where)
                    self._report(unit, "away", f"{unit.what} was disconnected; unmounted", logging.WARNING)
                else:
                    lock(unit.where)
                    self._report(unit, "away", f"{unit.what} is not connected", logging.WARNING)
