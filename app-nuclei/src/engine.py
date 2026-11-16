"""The scan engine: scans one device and turns what it finds into issues.

A scan of a device: naabu finds its open ports, services that should not be exposed become issues, and
nuclei checks every open port. Only a device on your own network is scanned (see trackers.scannable),
and one scan runs at a time. After a complete scan, the device's issues that scan did not find again are
resolved; a scan that fails resolves nothing.
"""

import asyncio
import ipaddress
import logging
import time

from .config import Config
from .database import Database
from .devices import risky_findings
from .scanner import Scanner, fingerprint, severity_at_least
from .trackers import scannable

logger = logging.getLogger(__name__)

# The community templates are brought up to date at most this often
TEMPLATE_UPDATE_EVERY = 86400


class NotScannable(Exception):
    """A device may not be scanned: not on your own network, unknown, or a scan is already running."""


def _target(ip: str, port: int) -> str:
    try:
        if isinstance(ipaddress.ip_address(ip), ipaddress.IPv6Address):
            return f"[{ip}]:{port}"
    except ValueError:
        pass
    return f"{ip}:{port}"


class Engine:
    def __init__(self, database: Database, config: Config, scanner: Scanner | None = None):
        self.database = database
        self.config = config
        self.scanner = scanner or Scanner(config)
        self._lock = asyncio.Lock()
        self.updating_templates = False

    def check(self, device: dict | None) -> None:
        if not device:
            raise NotScannable("no such device")
        if not scannable(device["ip"]):
            raise NotScannable(f"{device['ip'] or 'no address'} is not an address on your own network")

    async def update_templates(self, force: bool = False) -> bool:
        """Bring the community templates up to date when they are older than a day (or now, when forced)."""
        last = self.scanner.templates_updated_at()
        if not force and last and time.time() - last < TEMPLATE_UPDATE_EVERY and self.scanner.template_count():
            return False
        self.updating_templates = True
        try:
            return await self.scanner.update_templates()
        finally:
            self.updating_templates = False

    async def scan_device(self, device: dict, reason: str) -> int:
        """Scan one device. Returns how many issues it has after the scan."""
        self.check(device)
        async with self._lock:
            scan_id = self.database.start_scan(device, reason)
            try:
                count = await self._run(scan_id, device)
                self.database.finish_scan(scan_id, "done")
                return count
            except Exception as e:
                logger.exception("Scan of %s (%s) failed", device["name"], device["ip"])
                self.database.finish_scan(scan_id, "failed", str(e)[:500])
                return 0

    async def _run(self, scan_id: int, device: dict) -> int:
        ip, seen, new = device["ip"], set(), 0

        def record(finding: dict) -> None:
            nonlocal new
            finding["device_id"] = device["id"]
            finding["fingerprint"] = fingerprint(
                device["entity_id"], ip, finding["port"], finding["template_id"], finding["matched_at"]
            )
            seen.add(finding["fingerprint"])
            if self.database.record_issue(finding):
                new += 1
            self.database.update_scan(scan_id, {"findings": len(seen), "new_issues": new})

        if self.config.update_templates:
            self.database.update_scan(scan_id, {"phase": "templates"})
            await self.update_templates()
        self.scanner.require_templates()

        self.database.update_scan(scan_id, {"phase": "ports"})
        results = await self.scanner.discover_ports([ip])
        ports = sorted({p for r in results for p in r.open_ports})
        self.database.set_ports(device["id"], ports)
        self.database.update_scan(scan_id, {"open_ports": len(ports), "phase": "vulnerabilities" if ports else None})
        for risky in risky_findings(ip, ports):
            record(risky)

        if ports:

            def on_finding(finding: dict) -> None:
                if severity_at_least(finding["severity"], self.config.min_severity):
                    record(finding)

            await self.scanner.scan_targets([_target(ip, p) for p in ports], on_finding)

        resolved = self.database.resolve_missing(device["id"], seen)
        self.database.update_scan(scan_id, {"resolved_issues": resolved})
        logger.info(
            "Scanned %s (%s): %d open ports, %d issues (%d new, %d resolved)",
            device["name"], ip, len(ports), len(seen), new, resolved,
        )  # fmt: skip
        return len(seen)
