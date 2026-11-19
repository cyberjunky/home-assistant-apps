"""Where an integration's files come from: a folder under custom_components, or a repository on GitHub.

A repository is read straight from its archive, in memory. Nothing is unpacked to disk and nothing is run,
so a hostile archive has no path to write to and no code that starts.
"""

import asyncio
import json
import logging
import re
import tarfile
import tempfile
from pathlib import Path

import aiohttp

from .analyzer import Files

logger = logging.getLogger(__name__)

REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,100}$")
# The same ceiling the Marketplace itself has for a download
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_FILES = 4000
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_TOTAL_READ = 80 * 1024 * 1024
# Read whole: Python and the manifest. Everything else is measured and its first bytes kept (to tell a binary).
READ_WHOLE = (".py",)
HEAD_BYTES = 16
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv"}


class SourceError(Exception):
    """The files could not be had."""


def _wanted(name: str) -> bool:
    return name.endswith(READ_WHOLE) or name == "manifest.json"


def read_folder(folder: Path) -> Files:
    """The files of an installed integration."""
    files: Files = {}
    total = 0
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if SKIP_DIRS & set(relative.parts) or path.is_symlink() or not path.is_file():
            continue
        if len(files) >= MAX_FILES:
            break
        try:
            size = path.stat().st_size
            whole = _wanted(str(relative)) and size <= MAX_READ_BYTES and total + size <= MAX_TOTAL_READ
            with path.open("rb") as handle:
                data = handle.read(size if whole else HEAD_BYTES)
        except OSError:
            continue
        total += len(data)
        files[relative.as_posix()] = (size, data)
    return files


def read_archive(archive) -> tuple[Files, str]:
    """The integration inside a repository archive, and the path it was found at.

    archive: a binary file opened for reading. Home Assistant installs custom_components/<domain>/ (the
    first one), or the repository's root when the manifest is there.
    """
    try:
        archive.seek(0)
        tar = tarfile.open(fileobj=archive, mode="r:gz")  # noqa: SIM115 (closed by the with below)
    except (tarfile.TarError, OSError, EOFError) as e:
        raise SourceError(f"not a readable archive: {e}") from e
    with tar:
        try:
            members = [m for m in tar if m.isfile()]
        except (tarfile.TarError, OSError, EOFError) as e:
            raise SourceError(f"not a readable archive: {e}") from e
        # GitHub puts everything under one folder named after the repository and commit
        names = {m.name.split("/", 1)[1]: m for m in members if "/" in m.name}
        manifests = sorted(n for n in names if re.fullmatch(r"custom_components/[^/]+/manifest\.json", n))
        if manifests:
            root = manifests[0].rsplit("/", 1)[0] + "/"
        elif "manifest.json" in names:
            root = ""
        else:
            raise SourceError("no integration found (no custom_components/<domain>/manifest.json)")
        files: Files = {}
        total = 0
        for name, member in sorted(names.items()):
            if not name.startswith(root) or len(files) >= MAX_FILES:
                continue
            relative = name[len(root) :]
            parts = relative.split("/")
            if SKIP_DIRS & set(parts) or (not root and parts[0] in ("tests", "test", "docs", ".github", "examples")):
                continue
            whole = _wanted(relative) and member.size <= MAX_READ_BYTES and total + member.size <= MAX_TOTAL_READ
            handle = tar.extractfile(member)
            data = handle.read(member.size if whole else HEAD_BYTES) if handle else b""
            total += len(data)
            files[relative] = (member.size, data)
    return files, root.rstrip("/")


async def fetch_repository(
    session: aiohttp.ClientSession, repository: str, ref: str | None = None
) -> tuple[Files, str]:
    """Download a public GitHub repository's archive and read the integration in it.

    ref is a tag or branch; without one, the default branch. Returns the files and the ref that was read.
    """
    if not REPOSITORY.match(repository) or ".." in repository:
        raise SourceError("a repository is written as owner/name")
    refs = [ref] if ref and REF.match(ref) and ".." not in ref else []
    last = "not found"
    for candidate in [*refs, "HEAD"]:
        url = f"https://github.com/{repository}/archive/{candidate}.tar.gz"
        # Kept in memory while small, on disk when not: a large repository must not fill a small machine's memory
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as archive:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=180)) as response:
                    if response.status != 200:
                        last = f"GitHub answered {response.status}"
                        continue
                    size = 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            raise SourceError("the repository is larger than 100 MB")
                        archive.write(chunk)
            except (aiohttp.ClientError, TimeoutError) as e:
                last = f"{type(e).__name__}: {e}"
                continue
            files, _ = await asyncio.to_thread(read_archive, archive)
            return files, candidate
    raise SourceError(f"could not download {repository}: {last}")


def installed(custom_components: Path) -> list[dict]:
    """The integrations in Home Assistant's custom_components folder: {domain, path, version, name}."""
    out = []
    if not custom_components.is_dir():
        return out
    for folder in sorted(custom_components.iterdir()):
        if not folder.is_dir() or folder.name.startswith((".", "__")):
            continue
        manifest: dict = {}
        try:
            loaded = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
            manifest = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            pass
        out.append(
            {
                "domain": str(manifest.get("domain") or folder.name),
                "folder": folder.name,
                "path": folder,
                "name": str(manifest.get("name") or folder.name),
                "version": str(manifest.get("version") or ""),
            }
        )
    return out
