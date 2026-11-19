"""Scans what is installed, keeps the results, and checks a repository before you install it."""

import asyncio
import contextlib
import json
import logging
import os
import time

import aiohttp

from . import osv, payload, sources
from .analyzer import add_context, analyze, finish
from .catalog import Catalog
from .config import DATA_DIR, HA_CONFIG_DIR, USER_AGENT, Config
from .homeassistant import HomeAssistant, HomeAssistantError
from .packages import Packages

logger = logging.getLogger(__name__)

RESULTS_FILE = DATA_DIR / "results.json"
LOOK_EVERY = 300  # how often the installed integrations are compared with the last scan
HISTORY_DAYS = 90
MAX_CHECKED = 50
GROUPS = [{"id": "installed", "label": "Installed"}, {"id": "checked", "label": "Checked before install"}]


def scan_files(files: sources.Files, **context) -> dict:
    """One integration from its files to a finished report. Runs in a worker thread: parsing is CPU work."""
    report = analyze(files)
    add_context(report, **context)
    return finish(report)


class Engine:
    def __init__(self, config: Config, home_assistant: HomeAssistant, catalog: Catalog | None = None):
        self.config = config
        self.home_assistant = home_assistant
        self.catalog = catalog or Catalog(DATA_DIR / "catalog")
        self.packages = Packages(DATA_DIR / "packages")
        self.custom_components = HA_CONFIG_DIR / "custom_components"
        self.results: dict = {
            "installed": {},
            "checked": {},
            "packages": {},
            "history": [],
            "last_scan": None,
            "fingerprint": "",
        }
        self.scanning = False
        self.notes: dict[str, str] = {}  # what could not be asked this scan, by source
        self._osv_details: dict[str, dict] = {}
        self._failed_at = 0.0
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._load()

    # -- storage ------------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            stored = json.loads(RESULTS_FILE.read_text())
        except (OSError, ValueError):
            return
        if isinstance(stored, dict):
            self.results.update({k: stored[k] for k in self.results if k in stored})

    def _save(self) -> None:
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RESULTS_FILE.with_name(RESULTS_FILE.name + ".tmp")
        tmp.write_text(json.dumps(self.results))
        os.replace(tmp, RESULTS_FILE)

    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # -- installed ----------------------------------------------------------------------------------
    def _fingerprint(self) -> str:
        """Changes when an integration is added, removed or updated."""
        parts = []
        for item in sources.installed(self.custom_components):
            with contextlib.suppress(OSError):
                parts.append(
                    f"{item['folder']}={item['version']}@{int((item['path'] / 'manifest.json').stat().st_mtime)}"
                )
        return ";".join(parts)

    async def _requirements(self, reports: list[dict]) -> tuple[dict, dict]:
        """What PyPI and the vulnerability database say of the requirements: (packages, vulnerabilities).
        The packages are read first: that gives a version to requirements that pin none."""
        requirements = [r for report in reports for r in report["requirements"]]
        found: dict = {}
        if requirements and self.config.dependency_scans:
            found = await self.packages.scan(self.session(), requirements)
            unread = sum(1 for p in found.values() if p["status"] == "error")
            if unread:
                self.notes["packages"] = f"{unread} of {len(found)} packages could not be read from PyPI"
        versions = {
            (r["name"].lower(), (found.get((r["name"].lower(), r["spec"])) or {}).get("version") or r["version"])
            for r in requirements
        }
        versions = {(name, version) for name, version in versions if version}
        if not versions or not self.config.vulnerability_lookups:
            return found, {}
        try:
            return found, await osv.lookup(self.session(), versions, self._osv_details)
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            self.notes["vulnerabilities"] = f"The vulnerability database did not answer: {type(e).__name__} {e}".strip()
            return found, {}

    async def scan_installed(self) -> None:
        async with self._lock:
            self.scanning = True
            self.notes = {}
            try:
                await self._scan_installed()
            finally:
                self.scanning = False

    async def _scan_installed(self) -> None:
        started = time.time()
        fingerprint = await asyncio.to_thread(self._fingerprint)
        items = await asyncio.to_thread(sources.installed, self.custom_components)
        if self.config.catalog_lookups:
            await self.catalog.refresh(self.session())
            if self.catalog.error:
                self.notes["catalog"] = f"The catalog could not be refreshed: {self.catalog.error}"
        state: dict = {"setup": {}, "log": {}, "marketplace": None}
        if self.config.runtime_checks:
            try:
                state = await self.home_assistant.snapshot(self.session())
            except HomeAssistantError as e:
                self.notes["home_assistant"] = f"Home Assistant could not be asked: {e}"
        # First pass for the requirements, so every package is looked up in one request
        files = {item["folder"]: await asyncio.to_thread(sources.read_folder, item["path"]) for item in items}
        firsts = [await asyncio.to_thread(analyze, f) for f in files.values()]
        found, vulnerable = await self._requirements(firsts)

        installed = {}
        for item in items:
            domain = item["domain"]
            market = (state["marketplace"] or {}).get(domain)
            repository = (market or {}).get("full_name")
            entry = self.catalog.entry(repository, domain) if self.config.catalog_lookups else None
            repository = repository or (entry or {}).get("full_name")
            runtime = None
            if self.config.runtime_checks and "home_assistant" not in self.notes:
                log = state["log"].get(domain) or {}
                runtime = {
                    "setup_seconds": state["setup"].get(domain),
                    "errors": log.get("errors", 0),
                    "warnings": log.get("warnings", 0),
                }
            report = await asyncio.to_thread(
                scan_files,
                files[item["folder"]],
                catalog=entry,
                removed=self.catalog.removed(repository) if self.config.catalog_lookups else None,
                marketplace=market,
                installed=True,
                vulnerabilities=vulnerable,
                packages=found,
                package_reports=self.packages.reports,
                runtime=runtime,
            )
            report.update(
                key=f"installed:{item['folder']}",
                group="installed",
                domain=report["domain"] or domain,
                name=report["name"] or item["name"],
                installs=self.catalog.installs(domain),
            )
            installed[item["folder"]] = report
        self.results["installed"] = installed
        self.results["last_scan"] = int(time.time())
        self.results["fingerprint"] = fingerprint
        self._history(list(installed.values()))
        await asyncio.to_thread(self._save)
        logger.info("Scanned %d integrations in %.1f s", len(installed), time.time() - started)

    def _history(self, reports: list[dict]) -> None:
        if not reports:
            return
        day = time.strftime("%Y-%m-%d")
        point = {
            "day": day,
            "score": round(sum(r["score"] for r in reports) / len(reports)),
            "integrations": len(reports),
            **{s: sum(r["counts"][s] for r in reports) for s in ("critical", "high", "medium", "low")},
        }
        history = [h for h in self.results["history"] if h.get("day") != day]
        self.results["history"] = [*history, point][-HISTORY_DAYS:]

    # -- before you install -------------------------------------------------------------------------
    async def check(self, repository: str) -> dict:
        """Read a repository from GitHub and scan it. Raises sources.SourceError."""
        repository = repository.strip().removeprefix("https://github.com/").strip("/")
        if self.config.catalog_lookups:
            await self.catalog.refresh(self.session())
        entry = self.catalog.entry(repository)
        files, ref = await sources.fetch_repository(self.session(), repository, (entry or {}).get("last_version"))
        first = await asyncio.to_thread(analyze, files)
        found, vulnerable = await self._requirements([first])
        report = await asyncio.to_thread(
            scan_files,
            files,
            catalog=entry,
            removed=self.catalog.removed(repository),
            vulnerabilities=vulnerable,
            packages=found,
            package_reports=self.packages.reports,
        )
        key = repository.lower()
        report.update(
            key=f"checked:{key}",
            group="checked",
            repository=(entry or {}).get("full_name") or repository,
            name=report["name"] or repository.split("/")[1],
            ref=ref,
            installs=self.catalog.installs(report["domain"]) if report["domain"] else None,
        )
        checked = {k: v for k, v in self.results["checked"].items() if k != key}
        checked[key] = report
        self.results["checked"] = dict(list(checked.items())[-MAX_CHECKED:])
        await asyncio.to_thread(self._save)
        return report

    async def forget(self, repository: str) -> bool:
        if self.results["checked"].pop(repository.lower(), None) is None:
            return False
        await asyncio.to_thread(self._save)
        return True

    # -- the dashboard's document -------------------------------------------------------------------
    def payload(self) -> dict:
        integrations = [*self.results["installed"].values(), *self.results["checked"].values()]
        wanted = {r.get("package") for i in integrations for r in i["requirements"]}
        return payload.build(
            integrations,
            GROUPS,
            packages={k: v for k, v in self.packages.reports.items() if v["key"] in wanted},
            history=self.results["history"],
            status={
                "scanning": self.scanning,
                "last_scan": self.results["last_scan"],
                "notes": list(self.notes.values()),
                "catalog_at": int(self.catalog.fetched_at) or None,
                "custom_components": self.custom_components.is_dir(),
            },
            config=self.config.public(),
        )

    # -- when to scan -------------------------------------------------------------------------------
    def wake(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        """Scan at start, when something was installed, updated or removed, when asked, and every rescan_hours."""
        while True:
            # A scan that failed is tried again after an hour, not every few minutes
            waited = time.time() - self._failed_at >= 3600
            changed = await asyncio.to_thread(self._fingerprint) != self.results["fingerprint"]
            due = time.time() - (self.results["last_scan"] or 0) >= self.config.rescan_hours * 3600
            if self._wake.is_set() or ((changed or due) and waited):
                self._wake.clear()
                try:
                    await self.scan_installed()
                except Exception:
                    logger.exception("The scan failed")
                    self._failed_at = time.time()
                    self.notes["scan"] = "The last scan failed; see the app's log"
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), LOOK_EVERY)
