"""The packages an integration installs from PyPI: most of what a cloud integration does on the network is
in its library, not in the integration. Each direct requirement is resolved on PyPI, downloaded, and read
with the same analyzer. Nothing is installed and nothing is run.

Packages that Home Assistant itself ships are skipped: every installation has them, whatever is installed
from the Marketplace. What a requirement itself requires is followed on PyPI to a name and the version that
would be installed today, so it can be looked up in the vulnerability database; its code is not read.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

import aiohttp
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .analyzer import Files, analyze, finish
from .config import APP_VERSION

logger = logging.getLogger(__name__)

PYPI = "https://pypi.org/pypi"
DOWNLOAD_HOST = "https://files.pythonhosted.org/"
CORE_PACKAGES = "https://raw.githubusercontent.com/home-assistant/core/master/homeassistant/package_constraints.txt"
CORE_EVERY = 7 * 86400
NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")
MAX_DOWNLOAD = 40 * 1024 * 1024
MAX_FILES = 5000
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_TOTAL_READ = 60 * 1024 * 1024
HEAD_BYTES = 16
SKIP_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "example", "benchmarks", "__pycache__"}
CONCURRENT = 4
MAX_INDIRECT = 150  # packages followed under one direct requirement


class PackageError(Exception):
    """The package could not be read; the message says why."""


def normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def choose(files: list[dict]) -> tuple[dict, bool]:
    """The file to read of one release, and whether it holds compiled code that cannot be read.

    A pure Python wheel is exactly what gets installed. Then the source; then a compiled wheel, of which only
    the Python part is read.
    """
    files = [f for f in files if isinstance(f, dict) and str(f.get("url", "")).startswith(DOWNLOAD_HOST)]
    # A withdrawn file is passed over, unless the whole version was withdrawn: then that is what gets installed
    files = [f for f in files if not f.get("yanked")] or files
    wheels = [f for f in files if str(f.get("filename", "")).endswith(".whl")]
    pure = [f for f in wheels if str(f["filename"]).endswith("-none-any.whl")]
    sources = [f for f in files if str(f.get("filename", "")).endswith((".tar.gz", ".zip"))]
    if pure:
        return pure[0], False
    if sources:
        return sorted(sources, key=lambda f: not str(f["filename"]).endswith(".tar.gz"))[0], bool(wheels)
    linux = [f for f in wheels if "manylinux" in f["filename"] and "x86_64" in f["filename"]]
    if linux or wheels:
        return (linux or wheels)[0], True
    raise PackageError("PyPI has no file for this version")


def latest(releases: dict, spec: str) -> str:
    """The newest version that fits the requirement, as pip would pick it."""
    try:
        wanted = SpecifierSet(spec)
    except InvalidSpecifier as e:
        raise PackageError(f"not a version requirement: {spec[:40]}") from e
    versions = []
    for text, files in releases.items():
        try:
            version = Version(text)
        except InvalidVersion:
            continue
        if files and not all(f.get("yanked") for f in files) and not version.is_prerelease and wanted.contains(version):
            versions.append((version, text))
    if not versions:
        raise PackageError("no release fits the requirement")
    return max(versions)[1]


def _keep(path: str) -> bool:
    parts = path.split("/")
    return not (SKIP_DIRS & set(parts[:-1])) and ".dist-info/" not in path and ".egg-info/" not in path


def read_wheel(archive) -> Files:
    """The files of a wheel or a zipped source, read in memory."""
    files: Files = {}
    total = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or not _keep(member.filename) or len(files) >= MAX_FILES:
                    continue
                whole = member.filename.endswith(".py") and member.file_size <= MAX_READ_BYTES
                whole = whole and total + member.file_size <= MAX_TOTAL_READ
                with bundle.open(member) as handle:
                    data = handle.read(member.file_size if whole else HEAD_BYTES)
                total += len(data)
                files[member.filename] = (member.file_size, data)
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as e:
        raise PackageError(f"not a readable archive: {e}") from e
    return files


def read_sdist(archive) -> Files:
    """The files of a source archive, read in memory, without the folder everything is in."""
    files: Files = {}
    total = 0
    try:
        with tarfile.open(fileobj=archive, mode="r:gz") as tar:
            for member in tar:
                name = member.name.split("/", 1)[1] if "/" in member.name else member.name
                if not member.isfile() or not _keep(name) or len(files) >= MAX_FILES:
                    continue
                whole = name.endswith(".py") and member.size <= MAX_READ_BYTES and total + member.size <= MAX_TOTAL_READ
                handle = tar.extractfile(member)
                data = handle.read(member.size if whole else HEAD_BYTES) if handle else b""
                total += len(data)
                files[name] = (member.size, data)
    except (tarfile.TarError, OSError, EOFError) as e:
        raise PackageError(f"not a readable archive: {e}") from e
    return files


def scan_package(files: Files, name: str, version: str, compiled: bool) -> dict:
    report = analyze(files, package=True)
    if compiled:
        report["_hits"].add("code.compiled", "", 0, "only the Python part was read")
    finish(report)
    report.update(
        key=f"package:{normal(name)}=={version}", group="package", domain=normal(name), name=name, version=version
    )
    return report


class Packages:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.reports: dict[str, dict] = {}  # "name==version": report; a version never changes, so neither does this
        self.requires: dict[str, list[str]] = {}  # "name==version": what it requires, as PyPI lists it
        self.core: set[str] = set()
        self._core_at = 0.0
        self._releases: dict[str, dict] = {}  # this scan's answers of PyPI, by package
        self._load()

    # -- storage ------------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            stored = json.loads((self.cache_dir / "reports.json").read_text())
            if stored.get("analyzer") == APP_VERSION:
                self.reports = stored["reports"]
                self.requires = stored.get("requires") or {}
        except (OSError, ValueError, KeyError, AttributeError):
            pass
        try:
            path = self.cache_dir / "core_packages.txt"
            self._index_core(path.read_text())
            self._core_at = path.stat().st_mtime
        except OSError:
            pass

    def _save(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_dir / "reports.json.tmp"
        tmp.write_text(json.dumps({"analyzer": APP_VERSION, "reports": self.reports, "requires": self.requires}))
        os.replace(tmp, self.cache_dir / "reports.json")

    def _index_core(self, text: str) -> None:
        names = {
            normal(m.group(1))
            for line in text.splitlines()
            if (m := re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*[=<>!~]", line))
        }
        if names:
            self.core = names

    async def _refresh_core(self, session: aiohttp.ClientSession) -> None:
        if time.time() - self._core_at < CORE_EVERY and self.core:
            return
        try:
            async with session.get(CORE_PACKAGES, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    return
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError):
            return
        self._index_core(text)
        self._core_at = time.time()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((self.cache_dir / "core_packages.txt").write_text, text)

    # -- one package --------------------------------------------------------------------------------
    async def _json(self, session: aiohttp.ClientSession, url: str) -> dict:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 404:
                raise PackageError("missing")
            if response.status != 200:
                raise PackageError(f"PyPI answered {response.status}")
            return await response.json()

    async def _versions(self, session: aiohttp.ClientSession, name: str) -> dict:
        """PyPI's releases of a package, asked once a scan."""
        if name not in self._releases:
            data = await self._json(session, f"{PYPI}/{name}/json")
            self._releases[name] = data.get("releases") or {}
            info = data.get("info") or {}
            if info.get("version"):
                self.requires.setdefault(f"{name}=={info['version']}", list(info.get("requires_dist") or []))
        return self._releases[name]

    async def _required(self, session: aiohttp.ClientSession, name: str, version: str) -> list[str]:
        key = f"{name}=={version}"
        if key not in self.requires:
            info = (await self._json(session, f"{PYPI}/{name}/{version}/json")).get("info") or {}
            self.requires[key] = list(info.get("requires_dist") or [])
        return self.requires[key]

    async def _indirect(self, session: aiohttp.ClientSession, name: str, version: str) -> list[dict]:
        """What a requirement installs in turn, all the way down: [{name, version}], each at the newest version
        that fits, as pip would pick it today. Packages Home Assistant ships are left out, with what they need."""
        found: dict[str, dict] = {}
        queue: list[tuple[str, str, set[str]]] = [(normal(name), version, set())]
        while queue and len(found) < MAX_INDIRECT:
            parent, parent_version, extras = queue.pop(0)
            try:
                lines = await self._required(session, parent, parent_version)
            except (PackageError, aiohttp.ClientError, TimeoutError, ValueError):
                continue
            for line in lines:
                try:
                    wanted = Requirement(line)
                except InvalidRequirement:
                    continue
                child = normal(wanted.name)
                # Only what is installed here: not for another system, and not for an extra nobody asked for
                if wanted.marker and not any(wanted.marker.evaluate({"extra": e}) for e in (extras or {""})):
                    continue
                if wanted.url or child in found or child in self.core or child == normal(name):
                    continue
                try:
                    chosen = latest(await self._versions(session, child), str(wanted.specifier))
                except (PackageError, aiohttp.ClientError, TimeoutError, ValueError):
                    continue
                found[child] = {"name": wanted.name, "version": chosen}
                queue.append((child, chosen, set(wanted.extras)))
        return sorted(found.values(), key=lambda p: p["name"].lower())

    async def _one(self, session: aiohttp.ClientSession, requirement: dict) -> dict:
        """{status: scanned|core|missing|error, version, key, reason, yanked, published, indirect}"""
        name = requirement["name"]
        if not NAME.match(name):
            return {"status": "error", "reason": "not a package name"}
        if normal(name) in self.core:
            return {"status": "core", "version": requirement["version"]}
        try:
            if requirement["version"]:
                version = requirement["version"]
                data = await self._json(session, f"{PYPI}/{normal(name)}/{version}/json")
                files = data.get("urls") or []
                self.requires.setdefault(
                    f"{normal(name)}=={version}", list((data.get("info") or {}).get("requires_dist") or [])
                )
            else:
                releases = await self._versions(session, normal(name))
                version = latest(releases, requirement["spec"])
                files = releases[version]
            key = f"{normal(name)}=={version}"
            more = {
                "yanked": bool(files) and all(isinstance(f, dict) and f.get("yanked") for f in files),
                "indirect": await self._indirect(session, name, version),
            }
            if key in self.reports:
                return {
                    "status": "scanned",
                    "version": version,
                    "key": key,
                    **more,
                    "published": self.reports[key].get("published"),
                }
            chosen, compiled = choose(files)
            published = None
            with contextlib.suppress(ValueError):
                uploaded = str(chosen.get("upload_time_iso_8601")).replace("Z", "+00:00")
                published = int(datetime.fromisoformat(uploaded).timestamp())
            if int(chosen.get("size") or 0) > MAX_DOWNLOAD:
                raise PackageError("larger than 40 MB")
            digest = hashlib.sha256()
            # Kept in memory while small, on disk when not
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as archive:
                async with session.get(chosen["url"], timeout=aiohttp.ClientTimeout(total=180)) as response:
                    if response.status != 200:
                        raise PackageError(f"the download answered {response.status}")
                    size = 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            raise PackageError("larger than 40 MB")
                        digest.update(chunk)
                        archive.write(chunk)
                expected = (chosen.get("digests") or {}).get("sha256")
                if expected and digest.hexdigest() != expected:
                    raise PackageError("the download does not match PyPI's checksum")
                archive.seek(0)
                reader = read_sdist if str(chosen["filename"]).endswith(".tar.gz") else read_wheel
                files_read = await asyncio.to_thread(reader, archive)
            self.reports[key] = await asyncio.to_thread(scan_package, files_read, name, version, compiled)
            self.reports[key]["published"] = published
            return {"status": "scanned", "version": version, "key": key, **more, "published": published}
        except PackageError as e:
            if str(e) == "missing":
                return {"status": "missing", "reason": "PyPI does not have it"}
            return {"status": "error", "reason": str(e)}
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as e:
            return {"status": "error", "reason": f"{type(e).__name__}: {e}"[:120]}

    async def scan(self, session: aiohttp.ClientSession, requirements: list[dict]) -> dict[tuple[str, str], dict]:
        """Every direct requirement resolved and read. {(name, spec): {status, version, key, reason}}"""
        await self._refresh_core(session)
        wanted = {(r["name"].lower(), r["spec"]): r for r in requirements if not r["url"]}
        gate = asyncio.Semaphore(CONCURRENT)

        async def one(requirement: dict) -> dict:
            async with gate:
                return await self._one(session, requirement)

        before = len(self.reports), len(self.requires)
        self._releases = {}
        results = await asyncio.gather(*(one(r) for r in wanted.values()))
        if (len(self.reports), len(self.requires)) != before:
            await asyncio.to_thread(self._save)
        return dict(zip(wanted, results, strict=True))
