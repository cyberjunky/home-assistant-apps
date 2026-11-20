"""Copying documents to a folder: Home Assistant's share or media folder, and with them the
network storage (an SMB or NFS share) that Home Assistant mounts there.

The app holds no login to a share: Home Assistant connects to it (Settings, System, Storage,
Add network storage) and the app writes into the folder that gives.
"""

import contextlib
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from .archive import file_name

logger = logging.getLogger(__name__)

TOKENS = ("name", "date", "time", "year", "month", "day", "id", "n")


class ShareError(Exception):
    pass


def tokens(when: float, name: str = "", doc_id: int | str = "", pages: int | str = "") -> dict[str, str]:
    t = datetime.fromtimestamp(when)
    return {
        "name": name,
        "date": t.strftime("%Y-%m-%d"),
        "time": t.strftime("%H.%M"),
        "year": t.strftime("%Y"),
        "month": t.strftime("%m"),
        "day": t.strftime("%d"),
        "id": str(doc_id),
        "n": str(pages),
    }


def fill(pattern: str, values: dict[str, str]) -> str:
    """Fill {date} and the like in a pattern; anything else in braces stays as it is."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), pattern)


def relative_path(pattern: str, values: dict[str, str]) -> Path:
    """Where a document goes below the folder: the pattern filled, each part a safe name, ending in .pdf."""
    filled = fill(pattern, {**values, "name": values.get("name", "").replace("/", " ")})
    parts = [file_name(p) for p in filled.replace("\\", "/").split("/") if p.strip() and p.strip() not in (".", "..")]
    if not parts:
        parts = [file_name(values.get("name", ""))]
    return Path(*parts[:-1], parts[-1] + ".pdf")


def inside(path: Path, roots: tuple[Path, ...]) -> Path | None:
    """The path, resolved, when it is one of the roots or below one; None otherwise."""
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        return None
    for root in roots:
        with contextlib.suppress(OSError, RuntimeError):
            root = root.resolve()
        if resolved == root or root in resolved.parents:
            return resolved
    return None


def _mounts() -> dict[str, str]:
    """Mount points and their file system type (cifs for SMB, nfs)."""
    out: dict[str, str] = {}
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                fields = left.split()
                if len(fields) > 4 and right:
                    out[fields[4].replace("\\040", " ")] = right.split()[0]
    except OSError:
        pass
    return out


def kind_of(path: Path, mounts: dict[str, str] | None = None) -> str:
    """ "smb", "nfs" or "" for a folder: what it is mounted from, itself or a folder above it."""
    mounts = _mounts() if mounts is None else mounts
    for p in (path, *path.parents):
        fs = mounts.get(str(p))
        if fs:
            return {"cifs": "smb", "smb3": "smb", "nfs": "nfs", "nfs4": "nfs"}.get(fs, "")
    return ""


def list_folder(path: str, roots: tuple[Path, ...]) -> dict:
    """The folders in a folder, for choosing one. Without a path: the roots."""
    mounts = _mounts()
    if not path:
        return {
            "path": "",
            "parent": None,
            "folders": [{"name": str(r), "path": str(r), "kind": kind_of(r, mounts)} for r in roots if r.is_dir()],
        }
    folder = inside(Path(path), roots)
    if folder is None or not folder.is_dir():
        raise ShareError("That folder isn't in Home Assistant's share or media folder")
    folders = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        raise ShareError(f"Cannot read {folder}: {e.strerror or e}") from e
    for entry in entries:
        if entry.name.startswith("."):
            continue
        with contextlib.suppress(OSError):
            if entry.is_dir():
                folders.append({"name": entry.name, "path": str(entry), "kind": kind_of(entry, mounts)})
    is_root = any(folder == r.resolve() for r in roots)
    return {
        "path": str(folder),
        "parent": "" if is_root else str(folder.parent),
        "kind": kind_of(folder, mounts),
        "writable": os.access(folder, os.W_OK),
        "folders": folders[:500],
    }


def make_folder(parent: str, name: str, roots: tuple[Path, ...]) -> str:
    folder = inside(Path(parent), roots)
    name = file_name(name)
    if folder is None or not folder.is_dir():
        raise ShareError("That folder isn't in Home Assistant's share or media folder")
    target = folder / name
    try:
        target.mkdir(exist_ok=True)
    except OSError as e:
        raise ShareError(f"Cannot make {target}: {e.strerror or e}") from e
    return str(target)


def copy_document(source: Path, folder: str, relative: Path, roots: tuple[Path, ...]) -> str:
    """Copy a PDF into the folder. An existing file is never replaced: the copy gets (2), (3)…"""
    if not folder:
        raise ShareError("No folder is chosen: choose one under Settings")
    base = inside(Path(folder), roots)
    if base is None:
        raise ShareError("The chosen folder isn't in Home Assistant's share or media folder")
    if not base.is_dir():
        raise ShareError(f"{folder} isn't there: is the network storage connected in Home Assistant?")
    target = inside(base / relative, (base,))
    if target is None:
        raise ShareError("The file name pattern leads outside the chosen folder")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        stem, number = target.stem, 1
        while target.exists():
            number += 1
            target = target.with_name(f"{stem} ({number}){target.suffix}")
        tmp = target.with_name(f".{target.name}.{int(time.time())}.part")
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, target)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
    except OSError as e:
        raise ShareError(f"Cannot write to {target.parent}: {e.strerror or e}") from e
    return str(target)
