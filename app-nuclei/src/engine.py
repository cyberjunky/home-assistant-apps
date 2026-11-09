"""The scan engine: turns a registered object into a running scan and findings into tickets.

Guard rails enforced here, not only in the UI:
  - An object is scanned only when it is enabled and authorised (the ownership gate).
  - A public IP or network is scanned only once expanded to hosts the person approved; a host name,
    URL or ASN must be authorised explicitly. Private ranges are allowed as yours.
One scan runs at a time per object. Findings become tickets; a repeat scan reopens or auto-resolves.
"""

import asyncio
import ipaddress
import logging
import time

from . import targets
from .config import Config
from .database import Database
from .devices import guess, risky_findings
from .scanner import HostResult, Scanner, fingerprint, severity_at_least

logger = logging.getLogger(__name__)


class NotAuthorised(Exception):
    """An object may not be scanned: disabled, or the ownership gate is not satisfied."""


class Engine:
    def __init__(self, database: Database, config: Config, scanner: Scanner | None = None):
        self.database = database
        self.config = config
        self.scanner = scanner or Scanner(config)
        self._running: set[int] = set()
        self._lock = asyncio.Lock()
        self.templates_updated_at: float | None = None

    def _expand(self, obj: dict) -> list[str]:
        """The scan targets of an object. Enforces the ownership gate."""
        if not obj["enabled"]:
            raise NotAuthorised("the object is disabled")
        if not obj["authorised"]:
            raise NotAuthorised("the object is not marked as authorised to scan")
        target, kind = obj["target"], obj["kind"]
        if kind in ("host", "url"):
            return [target]
        if kind == "asn":
            raise NotAuthorised("an ASN must be expanded to approved hosts before scanning")
        if kind == "ip":
            return [target]
        # cidr: private ones are yours; a public block must have been approved host-by-host, which is
        # recorded by its authorised flag plus a bounded size
        hosts = targets.hosts_of(target)
        if not targets.is_private(target) and len(hosts) > targets.MAX_HOSTS_PER_OBJECT:
            raise NotAuthorised("the network is too large; split it")
        return hosts

    def check_can_scan(self, object_id: int) -> None:
        """Raise NotAuthorised unless this object may be scanned right now."""
        if object_id in self._running:
            raise NotAuthorised("a scan of this object is already running")
        obj = self.database.object(object_id)
        if not obj:
            raise NotAuthorised("no such object")
        self._expand(obj)

    async def scan_object(self, object_id: int, trigger: str = "manual") -> int:
        async with self._lock:
            if object_id in self._running:
                raise NotAuthorised("a scan of this object is already running")
            obj = self.database.object(object_id)
            if not obj:
                raise NotAuthorised("no such object")
            hosts = self._expand(obj)
            self._running.add(object_id)
        scan_id = self.database.start_scan(object_id, obj["target"], trigger)
        try:
            return await self._run(scan_id, obj, hosts)
        finally:
            self._running.discard(object_id)

    async def _run(self, scan_id: int, obj: dict, hosts: list[str]) -> int:
        seen: set[str] = set()
        try:
            if self.config.update_templates:
                await self._update_templates()
            self.scanner.require_templates()
            # URLs name host and port already; IPs and networks get a port sweep first
            if obj["kind"] == "url":
                targets_for_nuclei = hosts
                self.database.update_scan(scan_id, {"hosts_total": 1, "hosts_done": 1})
            else:
                self.database.update_scan(scan_id, {"hosts_total": len(hosts)})
                results = await self.scanner.discover_ports(hosts)
                # Every host with an open port becomes a device, labelled with a first guess
                for result in results:
                    detected = guess(result.open_ports, [])
                    self.database.record_device(
                        result.host, obj["id"], detected["type"], detected["signals"], result.open_ports
                    )
                    # Services that shouldn't be exposed (telnet, rdp, open databases, ...) are tickets too
                    for risky in risky_findings(result.host, result.open_ports):
                        risky["fingerprint"] = fingerprint(
                            risky["host"], risky["port"], risky["template_id"], risky["matched_at"]
                        )
                        risky["object_id"] = obj["id"]
                        seen.add(risky["fingerprint"])
                        self.database.record_finding(risky)
                open_ports = sum(len(r.open_ports) for r in results)
                self.database.update_scan(scan_id, {"hosts_done": len(hosts), "open_ports": open_ports})
                targets_for_nuclei = self._host_port_targets(results)
                if not targets_for_nuclei:
                    self.database.finish_scan(scan_id, "done")
                    self.database.auto_resolve(obj["id"], seen)
                    return 0

            def on_finding(finding: dict) -> None:
                finding["object_id"] = obj["id"]
                if finding["severity"] == "info":
                    # Detections (what runs on a host) refine the device's guess; they are not tickets
                    self._learn_from_detection(finding, obj["id"])
                    return
                if not severity_at_least(finding["severity"], self.config.min_severity):
                    return
                seen.add(finding["fingerprint"])
                self.database.record_finding(finding)
                self.database.update_scan(scan_id, {"findings": len(seen)})

            await self.scanner.scan_targets(targets_for_nuclei, on_finding)
            self.database.auto_resolve(obj["id"], seen)
            self.database.finish_scan(scan_id, "done")
            return len(seen)
        except Exception as e:
            logger.exception("Scan %s of %s failed", scan_id, obj["target"])
            self.database.finish_scan(scan_id, "failed", str(e)[:500])
            return 0

    def _learn_from_detection(self, finding: dict, object_id: int) -> None:
        """An info-level detection says what a host runs; fold it into the device's signals and guess."""
        host = finding["host"]
        if not host:
            return
        device = self.database.device_by_host(host)
        ports = device["open_ports"] if device else ([finding["port"]] if finding.get("port") else [])
        signals = [*(device["signals"] if device else []), finding["name"], finding.get("template_id") or ""]
        detected = guess(ports, signals)
        self.database.record_device(host, object_id, detected["type"], detected["signals"], ports)

    async def _update_templates(self) -> None:
        if await self.scanner.update_templates():
            self.templates_updated_at = time.time()

    @staticmethod
    def _host_port_targets(results: list[HostResult]) -> list[str]:
        out = []
        for result in results:
            for port in result.open_ports:
                host = result.host
                if ":" in host and not host.startswith("["):
                    try:
                        ipaddress.IPv6Address(host)
                        host = f"[{host}]"
                    except ipaddress.AddressValueError:
                        pass
                out.append(f"{host}:{port}")
        return out

    @property
    def running(self) -> list[int]:
        return sorted(self._running)
