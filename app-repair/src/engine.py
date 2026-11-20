"""The engine: looks Home Assistant over at a set pace, keeps what it found, and runs the repairs.

A repair is always one the latest scan offered for a finding, or a cleanup of an entity or device
that scan knows. The browser names it; the arguments come from here.
"""

import asyncio
import contextlib
import logging
import time

from . import checks, fixes
from .config import HA_CONFIG_DIR, Config
from .database import Database
from .ha import HA, HAError
from .snapshot import Snapshot, take

logger = logging.getLogger(__name__)

# After a repair Home Assistant needs a moment before a new scan shows its effect
SETTLE_SECONDS = 3
SEARCH_MAX = 40


class Engine:
    def __init__(self, config: Config, database: Database, ha: HA):
        self.config = config
        self.database = database
        self.ha = ha
        self.snapshot: Snapshot | None = None
        self.findings: list[dict] = []
        self.log: list[dict] = []
        self.error = ""
        self.scanning = False
        self.scanned_at = 0.0
        self.scan_seconds = 0.0
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()

    # ------------------------------------------------------------------ scanning
    async def run(self) -> None:
        """Scan now, then every scan_minutes, or sooner when asked."""
        while True:
            await self.scan()
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.config.scan_minutes * 60)

    def scan_soon(self) -> None:
        self._wake.set()

    async def scan(self) -> bool:
        async with self._lock:
            self.scanning = True
            started = time.time()
            try:
                snapshot = await take(
                    self.ha, self.config.check_config, HA_CONFIG_DIR if self.config.deep_scan else None
                )
                findings = await asyncio.to_thread(checks.run, snapshot)
            except HAError as e:
                self.error = str(e)
                logger.warning(f"Scan failed: {e}")
                return False
            except Exception as e:
                self.error = repr(e)
                logger.exception("Scan failed")
                return False
            finally:
                self.scanning = False
            self.snapshot, self.findings, self.error = snapshot, findings, ""
            self.log = checks.log_entries(snapshot)
            self.scanned_at, self.scan_seconds = snapshot.taken, time.time() - started
            active = self.active()
            await asyncio.to_thread(self.database.add_scan, snapshot.taken, checks.score(active), checks.counts(active))
            logger.info(f"Scan: {len(active)} findings, health {checks.score(active)}, in {self.scan_seconds:.1f} s")
            return True

    def active(self) -> list[dict]:
        """The findings that count: those not ignored."""
        ignored = self.database.ignored()
        return [f for f in self.findings if f["id"] not in ignored]

    def listed(self) -> list[dict]:
        """Every finding, each marked ignored or not, without the arguments of its repairs."""
        ignored = self.database.ignored()
        out = []
        for finding in self.findings:
            repairs = (
                [{k: v for k, v in fix.items() if k != "args"} for fix in finding["fixes"]]
                if self.config.allow_fixes
                else []
            )
            out.append({**finding, "fixes": repairs, "ignored": finding["id"] in ignored})
        return out

    def summary(self) -> dict:
        active = self.active()
        snap = self.snapshot
        by_integration: dict[str, int] = {}
        for finding in active:
            if finding.get("integration"):
                by_integration[finding["integration"]] = by_integration.get(finding["integration"], 0) + 1
        names = {domain: checks.integration_name(snap, domain) for domain in by_integration} if snap else {}
        return {
            "scanned_at": self.scanned_at,
            "scan_seconds": round(self.scan_seconds, 1),
            "scanning": self.scanning,
            "error": self.error,
            "ready": snap is not None,
            "ha_version": snap.version if snap else "",
            "score": checks.score(active) if snap else None,
            "counts": checks.counts(active),
            "ignored": len(self.findings) - len(active),
            "gaps": snap.gaps if snap else [],
            "supervisor": bool(snap and snap.resolution is not None),
            "storage": snap.storage if snap else None,
            "by_integration": sorted(
                ({"domain": d, "name": names[d], "count": c} for d, c in by_integration.items()),
                key=lambda r: -r["count"],
            )[:12],
            "totals": {
                "entities": len(snap.states),
                "registered": len(snap.entities),
                "devices": len(snap.devices),
                "integrations": len(snap.entries),
                "automations": len(snap.automations),
                "dashboards": len(snap.dashboards),
                "log": len(self.log),
            }
            if snap
            else {},
        }

    # ------------------------------------------------------------------ repairs
    async def _apply(self, fix: str, args: dict, subject: str) -> dict:
        if not self.config.allow_fixes:
            raise PermissionError("Repairs are turned off in the app's configuration")
        if self.snapshot is None:
            raise LookupError("There is no scan yet")
        steps = await fixes.apply(self.ha, self.snapshot, fix, args)
        await asyncio.to_thread(self.database.add_action, fix, subject, steps.ok, steps.steps)
        # The next scan shows what the repair changed
        await asyncio.sleep(SETTLE_SECONDS)
        await self.scan()
        return {"ok": steps.ok, "steps": steps.steps}

    def offered(self, finding_id: str, index: int, fix: str) -> tuple[dict, dict]:
        """The finding and the repair meant: by its place in the finding's list, and it must be the one named."""
        finding = next((f for f in self.findings if f["id"] == finding_id), None)
        if finding is None:
            raise LookupError("That finding is no longer there: it may have been solved already")
        if not 0 <= index < len(finding["fixes"]) or finding["fixes"][index]["fix"] != fix:
            raise LookupError("That repair is not offered for this finding")
        return finding, finding["fixes"][index]

    async def repair(self, finding_id: str, index: int, fix: str) -> dict:
        """Run a repair the latest scan offered for this finding."""
        finding, offered = self.offered(finding_id, index, fix)
        return await self._apply(fix, offered["args"], finding["subject"]["name"])

    async def purge_selected(self, entity_ids: list[str]) -> dict:
        """Delete the recorded history of the gone entities picked on the Clean up page.

        Only entities the deep scan found as gone are accepted: the browser picks from that list, it
        can't name anything else.
        """
        orphans = ((self.snapshot.storage or {}).get("database") or {}).get("orphans") if self.snapshot else None
        if not orphans:
            raise LookupError("The deep scan found no history of entities that no longer exist")
        gone = {o["entity_id"] for o in orphans}
        picked = list(dict.fromkeys(e for e in entity_ids if e in gone))
        if not picked:
            raise LookupError("None of these is history the deep scan found")
        subject = picked[0] if len(picked) == 1 else f"{len(picked)} entities that no longer exist"
        return await self._apply("purge_history", {"entity_ids": picked}, subject)

    async def cleanup(self, kind: str, item_id: str) -> dict:
        """Remove an entity or a device the latest scan knows, with everything stored about it."""
        snap = self.snapshot
        if snap is None:
            raise LookupError("There is no scan yet")
        if kind == "entity" and (item_id in snap.states or any(e["entity_id"] == item_id for e in snap.entities)):
            return await self._apply("cleanup_entity", {"entity_id": item_id}, item_id)
        device = next((d for d in snap.devices if d["id"] == item_id), None) if kind == "device" else None
        if device:
            return await self._apply("cleanup_device", {"device_id": item_id}, checks.device_name(device))
        raise LookupError("Home Assistant has no such entity or device")

    def ignore(self, finding_id: str, ignored: bool) -> None:
        finding = next((f for f in self.findings if f["id"] == finding_id), None)
        if finding is None and ignored:
            raise LookupError("That finding is no longer there")
        self.database.set_ignored(finding_id, finding["title"] if finding else "", ignored)

    # ------------------------------------------------------------------ the cleanup page
    def search(self, text: str) -> list[dict]:
        """Entities and devices whose id or name holds the text."""
        snap = self.snapshot
        words = text.lower().split()
        if snap is None or not words:
            return []
        ix = checks.Index(snap)
        out = []
        for device in snap.devices:
            name = checks.device_name(device)
            hay = f"{name} {device.get('manufacturer') or ''} {device.get('model') or ''}".lower()
            if all(w in hay for w in words):
                out.append(
                    {
                        "type": "device",
                        "id": device["id"],
                        "name": name,
                        "detail": f"{len(ix.by_device.get(device['id'], []))} entities",
                    }
                )
        for entity_id in sorted(set(snap.states) | set(ix.entity)):
            name = checks.friendly(snap.states.get(entity_id), ix.entity.get(entity_id), entity_id)
            if all(w in f"{entity_id} {name}".lower() for w in words):
                out.append({"type": "entity", "id": entity_id, "name": name, "detail": entity_id})
        return out[:SEARCH_MAX]

    def _used_in(self, entity_ids: set[str], device_id: str | None) -> list[dict]:
        """The automations, scripts, scenes, groups and dashboards that name these entities or this device."""
        snap = self.snapshot
        known = {e.split(".")[0] for e in snap.states} | {e["entity_id"].split(".")[0] for e in snap.entities}
        used = []

        def mentions(config) -> bool:
            entities, devices = checks.references(config, known)
            return bool(entities & entity_ids) or (device_id is not None and device_id in devices)

        for kind, configs in (("automation", snap.automations), ("script", snap.scripts)):
            for entity_id, config in configs.items():
                if mentions(config):
                    name = checks.friendly(snap.states.get(entity_id), None, entity_id)
                    item = config.get("id") or entity_id.split(".", 1)[1]
                    used.append({"type": kind, "name": name, "path": f"/config/{kind}/edit/{item}"})
        for entity_id, state in snap.states.items():
            members = (state.get("attributes") or {}).get("entity_id")
            if (
                entity_id.split(".")[0] in ("scene", "group")
                and isinstance(members, list)
                and entity_ids & set(members)
            ):
                kind = entity_id.split(".")[0]
                used.append(
                    {
                        "type": kind,
                        "name": checks.friendly(state, None, entity_id),
                        "path": "/config/scene" if kind == "scene" else "/config/helpers",
                    }
                )
        for board in snap.dashboards:
            if mentions(board["config"]):
                used.append(
                    {
                        "type": "dashboard",
                        "name": board["title"] or "Overview",
                        "path": f"/{board['url_path'] or 'lovelace'}",
                    }
                )
        return used

    def inspect(self, kind: str, item_id: str) -> dict:
        """Everything Home Assistant holds about one entity or device, and where it is used."""
        snap = self.snapshot
        if snap is None:
            raise LookupError("There is no scan yet")
        ix = checks.Index(snap)
        statistics = {s["statistic_id"] for s in snap.statistic_ids}

        def entity_view(entity_id: str) -> dict:
            entity, state = ix.entity.get(entity_id), snap.states.get(entity_id)
            entry = ix.entry.get((entity or {}).get("config_entry_id") or "")
            return {
                "entity_id": entity_id,
                "name": checks.friendly(state, entity, entity_id),
                "state": (state or {}).get("state"),
                "restored": bool(((state or {}).get("attributes") or {}).get("restored")),
                "registered": entity is not None,
                "disabled": bool((entity or {}).get("disabled_by")),
                "platform": (entity or {}).get("platform") or "",
                "integration": entry.get("title") if entry else "",
                "statistics": entity_id in statistics,
                "since": checks.parse_time((state or {}).get("last_changed")),
            }

        if kind == "entity":
            if not ix.exists(item_id):
                raise LookupError("Home Assistant has no such entity")
            entity = ix.entity.get(item_id)
            device = ix.device.get((entity or {}).get("device_id") or "")
            view = entity_view(item_id)
            return {
                "type": "entity",
                "id": item_id,
                "name": view["name"],
                "entities": [view],
                "device": {"id": device["id"], "name": checks.device_name(device)} if device else None,
                "used_in": self._used_in({item_id}, None),
                "findings": [
                    f["id"] for f in self.findings if item_id in f["affected"] or f["subject"]["id"] == item_id
                ],
            }
        device = ix.device.get(item_id) if kind == "device" else None
        if device is None:
            raise LookupError("Home Assistant has no such device")
        entity_ids = [e["entity_id"] for e in ix.by_device.get(item_id, [])]
        entries = [ix.entry[e] for e in device.get("config_entries") or [] if e in ix.entry]
        return {
            "type": "device",
            "id": item_id,
            "name": checks.device_name(device),
            "maker": device.get("manufacturer") or "",
            "model": device.get("model") or "",
            "integrations": [
                {
                    "title": e.get("title") or e["domain"],
                    "domain": e["domain"],
                }
                for e in entries
            ],
            "removable": bool(checks.removal(snap, device)[0]),
            "not_removable": checks.removal(snap, device)[1],
            "entities": [entity_view(e) for e in entity_ids],
            "used_in": self._used_in(set(entity_ids), item_id),
            "findings": [
                f["id"] for f in self.findings if f["subject"]["id"] == item_id or set(entity_ids) & set(f["affected"])
            ],
        }
