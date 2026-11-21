"""Where everything meets: Home Assistant's KNX integration, the app's own bus connection, the monitor,
the recorder and the checks.

Telegrams come from the app's own connection while it is up (it sees the whole bus), and otherwise from
Home Assistant's. The ETS project and the entities always come from Home Assistant.
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xknx.telegram import Telegram

from .bus import Bus, BusError
from .busmon import BusMonitor
from .checks import CATALOG, run_checks
from .config import Config
from .documents import Documents, product_key
from .ha_client import HAClient, HAClientError, HAWebSocket
from .model import HAConfig, Project, _yaml_addresses, build_entities, line_of
from .monitor import Monitor
from .recorder import Recorder
from .telegrams import decode, dpt_text, from_ha, from_xknx, guess

logger = logging.getLogger(__name__)

REFRESH_EVERY = 300
UI_DEVICE_PREFIX = "knx_vdev_"
SEED_TELEGRAMS = 2000
# What is taken from Home Assistant's telegram store after the app was away
CATCH_UP_DAYS = 14
CATCH_UP_LIMIT = 100000


class HubError(Exception):
    """Something asked of the app could not be done; the text is for the person."""


def _read_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(value))
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not save %s: %s", path, e)


class Hub:
    def __init__(self, config: Config, data_dir: Path):
        self.config = config
        self.data_dir = data_dir
        self.client = HAClient()
        self.ws = HAWebSocket(self.client, self._ha_connected)
        self.bus = Bus(data_dir / "interface.json")
        self.monitor = Monitor(config.monitor_size)
        self.documents = Documents(data_dir / "documents")
        self.busmon = BusMonitor()
        self.busmon.decode = self._decode_wire
        self.busmon.on_change = self._changed
        self.recorder = Recorder(data_dir / "telegrams.db", config.retain_days, config.retain_rows)
        self.project = Project()
        self.ha = HAConfig()
        # individual address -> what the device answered when it was last asked directly
        self.reach: dict[str, dict[str, Any]] = _read_json(data_dir / "devices.json", {})
        self.hidden: set[str] = set(_read_json(data_dir / "hidden.json", []))
        self.job: dict[str, Any] = {}
        # Set by the web side: a telegram came in, something else changed
        self.on_telegram: Callable[[dict[str, Any]], None] = lambda t: None
        self.on_change: Callable[[], None] = lambda: None
        self._job_task: asyncio.Task | None = None
        self._refresh_wanted = asyncio.Event()
        self._findings: list[dict[str, Any]] = []
        self._findings_at = 0.0
        self._project_stamp: Any = None
        # Datapoint types Home Assistant uses for addresses the project gives none (or there is no
        # project): from its telegrams, and from what its entities are configured with
        self.ha_dpts: dict[str, str] = {}
        self._backfill_task: asyncio.Task | None = None
        self._pending_dpts: dict[str, str] = {}
        # The newest value of each address from before this run: from the recording and from Home Assistant
        self.last_known: dict[str, dict[str, Any]] = {}
        # Frames seen with their flags, and how many of those were marked as sent again
        self._flagged = [0, 0]
        self._repeats_mean_nothing = False
        self.revision = 0
        # Goes up when what was heard is forgotten, so the open pages drop their own lists too
        self.epoch = 0
        self.bus.on_telegram = self._from_bus
        self.bus.on_change = self._changed
        self.bus.on_link = self._bus_link

    # ---- running ----

    async def run(self) -> None:
        self.recorder.start()
        for t in self.recorder.newest(SEED_TELEGRAMS):
            self.monitor.seed({**t, "via": "", "secure": None, "bits": None})
        for address, t in (await asyncio.to_thread(self.recorder.last_values)).items():
            self._known(address, {**t, "bits": None, "from": "recording"})
        await self.bus.restart()
        tasks = [asyncio.create_task(self._ticker(), name="ticker")]
        if self.client.available or os.environ.get("KNXRAY_SUPERVISOR_URL"):
            tasks += [
                asyncio.create_task(self.ws.run(), name="ha-websocket"),
                asyncio.create_task(self._refresher(), name="ha-refresh"),
            ]
        else:
            self.ha.error = "Not running under Home Assistant."
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

    async def close(self) -> None:
        if self._job_task is not None:
            self._job_task.cancel()
        await self.busmon.stop()
        await self.bus.stop()
        await self.client.close()
        await self.recorder.stop()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(1)
            self.monitor.tick()

    async def select_interface(self, choice: dict[str, Any]) -> None:
        """Choose another way to the bus. What the pages show about the bus starts afresh then: another
        interface may sit on another line, or on another installation altogether."""
        before = dict(self.bus.choice)
        await self.bus.select(choice)
        if self.bus.choice != before:
            await self.reset()

    async def reset(self) -> None:
        """Forget what was heard and what devices answered; the recording is kept."""
        self.cancel_job()
        await self.busmon.stop()
        self.busmon.frames.clear()
        self.busmon.counts = self.busmon._zero()
        self.monitor.clear()
        self.reach.clear()
        _write_json(self.data_dir / "devices.json", self.reach)
        self.job = {}
        self._flagged, self._repeats_mean_nothing = [0, 0], False
        self.epoch += 1
        self._changed()

    def _changed(self) -> None:
        self.revision += 1
        self._findings_at = 0.0
        self.on_change()

    # ---- telegrams ----

    @property
    def source(self) -> str:
        """Where telegrams come from right now: the app's own connection, Home Assistant's, or nowhere."""
        if self.bus.connected:
            return "bus"
        return "ha" if self.ws.connected and self.ha.loaded else ""

    def _bus_link(self, up: bool) -> None:
        self._link_event()

    def _link_event(self) -> None:
        """Note in the recording when telegrams started or stopped coming in."""
        listening = bool(self.source) and (self.bus.connected or bool(self.ha.connection.get("connected")))
        if listening != getattr(self, "_listening", False):
            self._listening = listening
            self.recorder.event("connected" if listening else "disconnected")

    def dpt_of(self, address: str) -> str | None:
        """The type a value on this address is read as: the project's, or else the one Home Assistant uses."""
        return self.project.dpt_of(address) or self.ha_dpts.get(address)

    def _learn_dpts(self, found: dict[str, str]) -> None:
        """Take in types Home Assistant uses. What was shown or recorded without a value gets one."""
        new = {a: d for a, d in found.items() if d and not self.project.dpt_of(a) and self.ha_dpts.get(a) != d}
        if not new:
            return
        self.ha_dpts.update(new)
        for t in self.monitor.telegrams:
            if t["dst"] in new and t.get("text") is None and t.get("raw") and t["type"] != "read":
                self._redecode(t, new[t["dst"]])
        for address in new:
            for last in ((self.monitor.addresses.get(address) or {}).get("last_value"), self.last_known.get(address)):
                if last and last.get("text") is None and last.get("raw"):
                    self._redecode(last, new[address])
                    if last.get("text") is not None:
                        last.pop("guess", None)
        self._backfill()
        self._changed()

    @staticmethod
    def _redecode(t: dict[str, Any], dpt: str) -> None:
        decoded = decode(dpt, t["raw"], t.get("bits"))
        if decoded:
            t["text"], t["num"], t["unit"] = decoded
            t["dpt"] = dpt

    def _backfill(self) -> None:
        """Give recorded telegrams without a value one, now that their type is known."""
        if self._backfill_task is not None and not self._backfill_task.done():
            return
        dpts = {a: d for a in {*self.project.group_addresses, *self.ha_dpts} if (d := self.dpt_of(a))}

        def decoder(dst: str, raw: str) -> tuple | None:
            dpt = dpts.get(dst)
            if dpt is None:
                return None
            if not raw:
                return (None, None, None, dpt)
            decoded = decode(dpt, raw)
            return (*decoded, dpt) if decoded else None

        self._backfill_task = asyncio.create_task(asyncio.to_thread(self.recorder.backfill, decoder))
        self._backfill_task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    def _decode_wire(self, frame: dict[str, Any]) -> None:
        """Give a group frame from the bus monitor its value, by the project's datapoint type."""
        dpt = self.dpt_of(frame["dst"])
        frame["dpt"] = dpt
        decoded = decode(dpt, frame["payload"], frame["bits"]) if frame["payload"] else None
        frame["text"], frame["unit"] = (decoded[0], decoded[2]) if decoded else (None, None)

    def wire(self) -> dict[str, Any]:
        """What bus monitor mode counted, and where the frames went that nobody acknowledged."""
        unanswered: dict[str, int] = {}
        for frame in self.busmon.frames:
            if frame.get("ack") == "none":
                unanswered[frame["dst"]] = unanswered.get(frame["dst"], 0) + 1
        top = sorted(unanswered, key=lambda d: -unanswered[d])[:5]
        return {**self.busmon.counts, "unanswered_to": [f"{d} ({unanswered[d]})" for d in top]}

    def _from_bus(self, telegram: Telegram, flags: Any = None) -> None:
        t = from_xknx(telegram, self.dpt_of(str(telegram.destination_address)), flags)
        if t is None:
            return
        if t["src"] == "0.0.0":
            # What the app sends itself (a read) leaves without a sender; the interface fills in its address
            t["src"] = self.bus.address or t["src"]
        if "rep" in t:
            # Some interfaces mark every frame as sent again (the bit means something else on the way to the
            # bus): when most frames carry it, it says nothing and is left out
            self._flagged[0] += 1
            self._flagged[1] += 1 if t["rep"] else 0
            if self._flagged[0] >= 20 and self._flagged[1] > self._flagged[0] / 2:
                self._repeats_mean_nothing = True
            if self._repeats_mean_nothing or self._flagged[0] < 20:
                t["rep"] = False
        self._ingest(t)

    def _from_ha(self, event: dict[str, Any]) -> None:
        t = from_ha(event)
        if t is not None and t["dpt"] and not self.project.dpt_of(t["dst"]) and self.ha_dpts.get(t["dst"]) != t["dpt"]:
            self._learn_dpts({t["dst"]: t["dpt"]})
        if self.bus.connected:
            return  # the app's own connection sees the same telegram
        if t is not None:
            self._ingest(t)

    def _known(self, address: str, t: dict[str, Any]) -> None:
        """Keep the newest value of an address heard of before this run: from the recording or Home Assistant."""
        if t.get("type") == "read" or not (t.get("raw") or t.get("text") is not None):
            return
        held = self.last_known.get(address)
        if held is None or t["ts"] > held["ts"]:
            value = {k: t.get(k) for k in ("ts", "src", "raw", "text", "unit", "type", "bits", "dpt", "from")}
            if value["text"] is None and (dpt := self.dpt_of(address)) and value["raw"]:
                self._redecode(value, dpt)
            if value["text"] is None and not value.get("dpt"):
                value["guess"] = guess(value["raw"], value["bits"])
            self.last_known[address] = value

    def _ingest(self, t: dict[str, Any]) -> None:
        if t.get("text") is None and t.get("raw") and not t.get("dpt") and t["type"] != "read":
            t["guess"] = guess(t["raw"], t.get("bits"))
        self.monitor.add(t)
        self.recorder.add(t)
        self._findings_at = min(self._findings_at, time.time() - 3)
        self.on_telegram(t)

    # ---- Home Assistant ----

    async def _ha_connected(self) -> None:
        await self.ws.subscribe_events("entity_registry_updated", lambda event: self._refresh_wanted.set())
        await self.ws.subscribe_events("device_registry_updated", lambda event: self._refresh_wanted.set())
        with contextlib.suppress(HAClientError):
            await self.ws.subscribe({"type": "knx/subscribe_telegrams"}, self._from_ha)
        self._refresh_wanted.set()
        task = asyncio.create_task(self._catch_up(), name="catch-up")
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    async def _catch_up(self) -> None:
        """Fill the recording with what Home Assistant kept while this app was not listening: the KNX
        integration has a telegram store of its own. Only what is newer than the last recorded telegram is
        taken, so nothing is recorded twice."""
        summary = self.recorder.summary()
        newest = summary["newest"] or 0.0
        start = max(newest + 0.001, time.time() - CATCH_UP_DAYS * 86400)
        if time.time() - start < 20:
            return
        answer = await self._try(
            {
                "type": "knx/query_telegrams",
                "start_time": datetime.fromtimestamp(start, UTC).isoformat(),
                "limit": CATCH_UP_LIMIT,
                "order_descending": False,
            },
            None,
            timeout=120,
        )
        if not isinstance(answer, dict):
            return
        # Whatever came in live meanwhile is already recorded: stop where the live telegrams begin
        live_from = min((t["ts"] for t in self.monitor.telegrams if t.get("via")), default=time.time())
        count = 0
        for item in answer.get("telegrams") or []:
            t = from_ha(item) if isinstance(item, dict) else None
            if t is not None and start <= t["ts"] < live_from:
                self.recorder.add(t)
                count += 1
        if count:
            logger.info("Took %d telegrams from Home Assistant's own store into the recording", count)

    async def _refresher(self) -> None:
        """Read Home Assistant's side at connect, when its registries change and every few minutes."""
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._refresh_wanted.wait(), REFRESH_EVERY)
            # Several changes in a row (a reload of the integration) are one refresh
            await asyncio.sleep(2)
            self._refresh_wanted.clear()
            if not self.ws.connected:
                self.ha.loaded = False
                self.ha.error = self.ws.last_error or "No connection to Home Assistant."
                self._link_event()
                self._changed()
                continue
            try:
                await self.refresh()
            except HAClientError as e:
                logger.warning("Reading Home Assistant failed: %s", e)
            except Exception:
                logger.exception("Reading Home Assistant failed")

    async def _try(self, message: dict[str, Any], default: Any = None, timeout: float = 30) -> Any:
        try:
            return await self.ws.call(message, timeout)
        except HAClientError as e:
            logger.debug("%s: %s", message.get("type"), e)
            return default

    async def refresh(self) -> None:
        ha = HAConfig()
        entries = await self._try({"type": "config_entries/get", "domain": "knx"}, [])
        entry = next((e for e in entries or [] if e.get("domain", "knx") == "knx"), None)
        if entry is None:
            ha.error = "The KNX integration is not set up in Home Assistant."
        else:
            ha.present = True
            ha.entry_id = str(entry.get("entry_id") or "")
            try:
                base = await self.ws.call({"type": "knx/get_base_data"})
            except HAClientError as e:
                base, ha.error = None, f"The KNX integration does not answer ({e})."
            if base:
                ha.loaded = True
                ha.connection = dict(base.get("connection_info") or {})
                ha.project_info = base.get("project_info")
                await self._read_project(ha)
                await self._read_entities(ha)
        self.ha = ha
        self._learn_dpts(self._pending_dpts)
        self._pending_dpts = {}
        self._link_event()
        self._changed()

    async def _read_project(self, ha: HAConfig) -> None:
        stamp = json.dumps(ha.project_info, sort_keys=True) if ha.project_info else None
        if stamp == self._project_stamp and (self.project.loaded or stamp is None):
            return
        data = await self._try({"type": "knx/get_knx_project"}, None, timeout=120) if stamp else None
        self.project = await asyncio.to_thread(Project.from_knxproject, data)
        self._project_stamp = stamp
        logger.info(
            "ETS project: %d group addresses, %d devices",
            len(self.project.group_addresses),
            len(self.project.devices),
        )
        if self.project.loaded:
            # Telegrams recorded before the project was known get their values now
            for address, last in self.last_known.items():
                if last.get("text") is None and last.get("raw") and (dpt := self.project.dpt_of(address)):
                    self._redecode(last, dpt)
                    if last.get("text") is not None:
                        last.pop("guess", None)
            self._backfill()

    async def _read_entities(self, ha: HAConfig) -> None:
        registry = await self._try({"type": "config/entity_registry/list"}, [])
        knx_entries = [e for e in registry or [] if e.get("platform") == "knx"]
        wanted = {e["entity_id"] for e in knx_entries}
        states = {
            s["entity_id"]: s for s in await self._try({"type": "get_states"}, []) or [] if s.get("entity_id") in wanted
        }
        by_group = await self._try({"type": "knx/get_entities_by_group"}, {})
        diagnostics: dict[str, Any] = {}
        if ha.entry_id:
            try:
                diagnostics = (await self.client.get_diagnostics(ha.entry_id)).get("data") or {}
            except HAClientError as e:
                logger.debug("No diagnostics of the KNX integration: %s", e)
        yaml_config = diagnostics.get("configuration_yaml") if isinstance(diagnostics, dict) else None
        ha.yaml_error = str(diagnostics.get("yaml_configuration_error") or "")
        ha.entities, ha.stray_entities = build_entities(
            knx_entries,
            states,
            by_group if isinstance(by_group, dict) else {},
            diagnostics.get("config_store"),
            yaml_config if isinstance(yaml_config, dict) else None,
        )
        # The types Home Assistant uses: what its integration last decoded per address, then what its entities
        # are configured with
        found: dict[str, str] = {}
        for entity in ha.entities:
            for address in entity["addresses"]:
                if address.get("expects") and address["ga"] not in found:
                    found[address["ga"]] = address["expects"]
        last = await self._try({"type": "knx/group_telegrams"}, {})
        for address, telegram in (last or {}).items():
            if isinstance(telegram, dict) and telegram.get("dpt_main") is not None:
                found[str(address)] = dpt_text(telegram["dpt_main"], telegram.get("dpt_sub"))
            # The last value Home Assistant saw, for an address nothing came in on since the app started
            if isinstance(telegram, dict) and (t := from_ha({"destination": address, **telegram})) is not None:
                self._known(str(address), {**t, "from": "Home Assistant"})
        self._pending_dpts = found
        exposes = await self._try({"type": "knx/get_expose_groups"}, {})
        ha.exposes = {str(k): [str(a) for a in v or []] for k, v in (exposes or {}).items()}
        for item in (yaml_config or {}).get("expose") or [] if isinstance(yaml_config, dict) else []:
            if isinstance(item, dict):
                name = str(item.get("entity_id") or item.get("type") or "expose")
                for address in _yaml_addresses(item):
                    if address["ga"] not in ha.exposes.setdefault(name, []):
                        ha.exposes[name].append(address["ga"])

        devices = await self._try({"type": "config/device_registry/list"}, [])
        with_entities = {e.get("device_id") for e in registry or []}
        for device in devices or []:
            made_here = any(
                isinstance(i, list | tuple) and len(i) == 2 and i[0] == "knx" and str(i[1]).startswith(UI_DEVICE_PREFIX)
                for i in device.get("identifiers") or []
            )
            if made_here and device.get("id") not in with_entities:
                ha.stray_devices.append(
                    {"id": device["id"], "name": device.get("name_by_user") or device.get("name") or device["id"]}
                )

    # ---- findings and the model the pages show ----

    def findings(self) -> list[dict[str, Any]]:
        now = time.time()
        if now - self._findings_at > 5:
            found = run_checks(
                self.project,
                self.ha,
                self.monitor,
                self.reach,
                chatty_per_minute=self.config.chatty_per_minute,
                own_addresses=(self.bus.address,),
                observed=now - self.monitor.since,
                bus_issues=self.bus.secure_issues,
                wire=self.wire(),
            )
            for finding in found:
                finding["hidden"] = finding["id"] in self.hidden
            self._findings, self._findings_at = found, now
        return self._findings

    def hide(self, finding_id: str, hidden: bool) -> None:
        (self.hidden.add if hidden else self.hidden.discard)(finding_id)
        _write_json(self.data_dir / "hidden.json", sorted(self.hidden))
        self._changed()

    def export(self, what: str) -> list[list[Any]] | None:
        """A table as rows for a spreadsheet: the group addresses, the devices or Home Assistant's entities,
        each with what was seen and how many findings point at it."""
        model = self.model()
        issues: dict[str, int] = {}
        for finding in model["findings"]:
            for key in finding["subject"].split("|"):
                issues[key] = issues.get(key, 0) + (0 if finding["hidden"] else 1)

        def when(ts: float | None) -> str:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""

        if what == "addresses":
            rows: list[list[Any]] = [
                [
                    "address",
                    "name",
                    "range",
                    "type",
                    "in project",
                    "last value",
                    "unit",
                    "last seen",
                    "telegrams",
                    "senders",
                    "linked objects",
                    "home assistant",
                    "findings",
                ]
            ]
            for a in sorted(model["addresses"], key=lambda a: _ga_sort(a["address"])):
                stats, last = a.get("stats") or {}, (a.get("stats") or {}).get("last_value") or {}
                rows.append(
                    [
                        a["address"],
                        a.get("name", ""),
                        a.get("range", ""),
                        a.get("dpt") or "",
                        "yes" if a["known"] else "no",
                        last.get("text") if last.get("text") is not None else last.get("raw", ""),
                        last.get("unit") or "",
                        when(stats.get("last")),
                        stats.get("count", 0),
                        " ".join(stats.get("sources", {})),
                        len(a.get("objects", [])),
                        " ".join(u["entity_id"] or u["name"] for u in a.get("users", [])),
                        issues.get(a["address"], 0),
                    ]
                )
            return rows
        if what == "devices":
            rows = [
                [
                    "address",
                    "name",
                    "maker",
                    "product",
                    "order number",
                    "line",
                    "room",
                    "in project",
                    "last heard",
                    "telegrams",
                    "answers",
                    "mask",
                    "serial",
                    "application",
                    "programming mode",
                    "findings",
                ]
            ]
            for d in sorted(model["devices"], key=lambda d: _address_key(d["address"])):
                stats, reach = d.get("stats") or {}, d.get("reach") or {}
                rows.append(
                    [
                        d["address"],
                        d.get("name", ""),
                        d.get("maker", ""),
                        d.get("product", ""),
                        d.get("order_number", ""),
                        d.get("line", ""),
                        d.get("room", ""),
                        "yes" if d["known"] else "no",
                        when(stats.get("last")),
                        stats.get("count", 0),
                        {True: "yes", False: "no"}.get(reach.get("reachable"), ""),
                        reach.get("mask", ""),
                        reach.get("serial", ""),
                        reach.get("load_state", ""),
                        {True: "on", False: "off"}.get(reach.get("progmode"), ""),
                        issues.get(d["address"], 0),
                    ]
                )
            return rows
        if what == "entities":
            rows = [
                ["entity", "name", "platform", "from", "state", "address", "role", "use", "address name", "findings"]
            ]
            for e in model["entities"]:
                for a in e["addresses"] or [{"ga": "", "role": "", "use": ""}]:
                    label = e["entity_id"] or e["name"]
                    rows.append(
                        [
                            e["entity_id"],
                            e["name"],
                            e["platform"],
                            e["source"],
                            e["state"] or "",
                            a["ga"],
                            a["role"],
                            a["use"],
                            self.project.name_of(a["ga"]) if a["ga"] else "",
                            issues.get(label, 0),
                        ]
                    )
            return rows
        return None

    def report(self) -> str:
        """Everything found, with what to do about it, as Markdown: to keep, or to hand to whoever fixes it."""
        project, monitor = self.project, self.monitor
        sources = {"bus": "the app's own interface", "ha": "Home Assistant", "": "nowhere"}
        lines = [
            "# KNX-Ray report",
            "",
            f"- Made: {time.strftime('%Y-%m-%d %H:%M')}",
            f"- Project: {project.info.get('name') or 'none'}"
            + (f", changed {str(project.info.get('last_modified'))[:10]}" if project.loaded else ""),
            f"- In the project: {len(project.group_addresses)} group addresses, {len(project.devices)} devices",
            f"- Telegrams since {time.strftime('%Y-%m-%d %H:%M', time.localtime(monitor.since))}: {monitor.total},"
            f" on {len(monitor.addresses)} group addresses from {len(monitor.sources)} devices",
            f"- Telegrams from: {sources[self.source]}",
            f"- Home Assistant: {len(self.ha.entities)} KNX entities, {len(self.ha.stray_entities)} stray",
            "",
        ]
        shown = [f for f in self.findings() if not f["hidden"]]
        if not shown:
            lines.append("Nothing found.")
        last_code = None
        for finding in shown:
            if finding["code"] != last_code:
                last_code = finding["code"]
                severity, category, title, fix = CATALOG[finding["code"]]
                count = sum(1 for f in shown if f["code"] == last_code)
                label = {"error": "Error", "warning": "Warning", "info": "Note"}[severity]
                lines += ["", f"## {label}: {title} ({count})", "", f"{category}. {fix}", ""]
            subject = finding["subject"].replace("|", " on ")
            name = project.name_of(finding["subject"]) if finding["kind"] in ("ga", "device") else ""
            lines.append(f"- **{subject}**{' ' + name if name else ''}: {finding['detail']}")
        hidden = sum(1 for f in self.findings() if f["hidden"])
        if hidden:
            lines += ["", f"{hidden} findings are hidden in the app and left out here."]
        return "\n".join(lines) + "\n"

    def summary(self) -> dict[str, Any]:
        counts = {"error": 0, "warning": 0, "info": 0, "hidden": 0}
        for finding in self.findings():
            counts["hidden" if finding["hidden"] else finding["severity"]] += 1
        return {
            "t": "state",
            "revision": self.revision,
            "epoch": self.epoch,
            "source": self.source,
            "bus": self.bus.status(),
            "ha": {
                "websocket": self.ws.connected,
                "present": self.ha.present,
                "loaded": self.ha.loaded,
                "error": self.ha.error,
                "connected": bool(self.ha.connection.get("connected")),
                "address": self.ha.address,
                "xknx": self.ha.connection.get("version"),
                "entities": len(self.ha.entities),
                "stray": len(self.ha.stray_entities) + len(self.ha.stray_devices),
            },
            "project": {
                "loaded": self.project.loaded,
                "name": self.project.info.get("name"),
                "modified": self.project.info.get("last_modified"),
                "tool": self.project.info.get("tool_version"),
                "group_addresses": len(self.project.group_addresses),
                "devices": len(self.project.devices),
            },
            "telegrams": {
                "total": self.monitor.total,
                "since": self.monitor.since,
                "rate": round(self.monitor.rate(), 1),
                "addresses": len(self.monitor.addresses),
                "sources": len(self.monitor.sources),
                "last_id": self.monitor.telegrams[-1]["id"] if self.monitor.telegrams else 0,
            },
            "findings": counts,
            "job": self.job,
            "busmon": self.busmon.status(),
            "allow_remove": self.config.allow_remove,
        }

    def model(self) -> dict[str, Any]:
        """Everything the tables show, in one answer."""
        users = self.ha.users()
        public = self.monitor.public
        addresses = []
        for address, ga in self.project.group_addresses.items():
            addresses.append(
                {
                    **ga,
                    "known": True,
                    "stats": public(self.monitor.addresses.get(address) or {}),
                    "users": users.get(address, []),
                }
            )
        for address, stats in self.monitor.addresses.items():
            if address not in self.project.group_addresses:
                addresses.append(
                    {
                        "address": address,
                        "name": "",
                        "known": False,
                        "stats": public(stats),
                        "users": users.get(address, []),
                    }
                )
        listed = {row["address"] for row in addresses}
        for address in [*users, *self.last_known]:
            if address not in listed:
                listed.add(address)
                addresses.append(
                    {"address": address, "name": "", "known": False, "stats": {}, "users": users.get(address, [])}
                )
        for row in addresses:
            # Nothing came in yet since the app started: the last value from before
            if not (row.get("stats") or {}).get("last_value") and row["address"] in self.last_known:
                row["last_known"] = self.last_known[row["address"]]
            # Where the project says nothing: the type Home Assistant uses, and the name of its entity
            if not row.get("dpt") and not row.get("dpt_objects") and row["address"] in self.ha_dpts:
                row["dpt_ha"] = self.ha_dpts[row["address"]]
            if not row["name"] and row["users"]:
                row["name_ha"] = next(
                    (u["name"] for u in row["users"] if u.get("name") and u["platform"] != "expose"), ""
                )

        own = {self.bus.address: "This app", self.ha.address: "Home Assistant"}
        documents = self.documents.counts({a: product_key(d) for a, d in self.project.devices.items()})
        devices = []
        for address, device in self.project.devices.items():
            devices.append(
                {
                    **{k: v for k, v in device.items() if k != "objects"},
                    "known": True,
                    "objects": len(device["objects"]),
                    "stats": public(self.monitor.sources.get(address) or {}),
                    "reach": self.reach.get(address),
                    "documents": documents.get(address, 0),
                }
            )
        for address in {*self.monitor.sources, *self.reach} - set(self.project.devices):
            if not address or address == "0.0.0":
                continue
            devices.append(
                {
                    "address": address,
                    "name": own.get(address, ""),
                    "known": False,
                    "own": address in own,
                    "line": line_of(address),
                    "stats": public(self.monitor.sources.get(address) or {}),
                    "reach": self.reach.get(address),
                    "documents": documents.get(address, 0),
                }
            )
        return {
            "revision": self.revision,
            "project": {**self.project.info, "loaded": self.project.loaded, "lines": self.project.lines},
            "addresses": addresses,
            "devices": devices,
            "entities": self.ha.entities,
            "stray_entities": self.ha.stray_entities,
            "stray_devices": self.ha.stray_devices,
            "exposes": self.ha.exposes,
            "findings": self.findings(),
            "catalog": {
                code: {"title": c[2], "fix": c[3], "severity": c[0], "category": c[1]} for code, c in CATALOG.items()
            },
            "load": self.monitor.load(60),
        }

    def detail(self, address: str) -> dict[str, Any]:
        """One group address or one device, with what is linked to it and its last telegrams."""
        objects = []
        if "/" in address:
            ga = self.project.group_addresses.get(address)
            ids = ga["objects"] if ga else []
        else:
            device = self.project.devices.get(address)
            ids = device["objects"] if device else []
        for object_id in ids:
            co = self.project.objects[object_id]
            objects.append({**co, "device_name": self.project.name_of(co["device"])})
        objects.sort(key=lambda o: (o["device"], o["number"] if isinstance(o["number"], int) else 0))
        out = {"address": address, "objects": objects, "telegrams": self.monitor.of(address, 200)}
        if "/" not in address:
            product = product_key(self.project.devices.get(address))
            out["documents"] = self.documents.of(address, product)
            out["product_key"] = product
        return out

    def names(self) -> dict[str, str]:
        """Group address or individual address -> name, for the CSV export."""
        names = {a: ga["name"] for a, ga in self.project.group_addresses.items()}
        names.update({a: self.project.name_of(a) for a in self.project.devices})
        return names

    # ---- asking the bus ----

    async def read_group(self, address: str) -> list[dict[str, Any]]:
        """Ask a group address for its value and collect who answers."""
        waiter = self.monitor.wait_for(address)
        try:
            if self.bus.connected:
                await self.bus.read_group(address)
            elif self.source == "ha":
                try:
                    await self.client.call_service("knx", "read", {"address": address})
                except HAClientError as e:
                    raise HubError(f"Home Assistant could not send the read: {e}") from e
            else:
                raise HubError("no connection to the bus")
            deadline = time.time() + 3
            while time.time() < deadline:
                await asyncio.sleep(0.1)
                # A little longer after the first answer, for a second device that answers too
                if waiter and time.time() - waiter[0]["ts"] > 0.6:
                    break
            return list(waiter)
        finally:
            self.monitor.done(address, waiter)

    def start_job(self, kind: str, target: str = "") -> None:
        """Start asking devices, one after the other: a single device, the project's devices or a line."""
        if self.job.get("running"):
            raise HubError("another check is still running")
        if not self.bus.connected:
            raise HubError(
                "asking devices needs the app's own connection: choose a KNX interface on the Interface page"
            )
        if kind in ("ping", "info", "deep"):
            if not _is_individual(target):
                raise HubError("not an individual address")
            verb = {"ping": "Checking", "info": "Reading", "deep": "Reading everything of"}[kind]
            targets, title = [target], f"{verb} {target}"
        elif kind == "project":
            targets = sorted(self.project.devices, key=_address_key)
            if not targets:
                raise HubError("there is no project with devices")
            title = "Checking the project's devices"
        elif kind == "line":
            parts = target.split(".")
            if len(parts) != 2 or not _is_individual(f"{target}.1"):
                raise HubError("a line is written as area.line, for example 1.1")
            targets, title = [f"{target}.{n}" for n in range(0, 256)], f"Scanning line {target}"
        elif kind == "progmode":
            targets, title = [], "Looking for devices in programming mode"
        else:
            raise HubError("unknown check")
        self.job = {
            "kind": kind,
            "title": title,
            "total": len(targets) or 1,
            "done": 0,
            "found": 0,
            "running": True,
            "error": "",
            "started": time.time(),
            "results": [],
        }
        self._job_task = asyncio.create_task(self._run_job(kind, targets), name="job")
        self._changed()

    def cancel_job(self) -> None:
        if self._job_task is not None and not self._job_task.done():
            self._job_task.cancel()

    async def _run_job(self, kind: str, targets: list[str]) -> None:
        job = self.job
        own = {self.bus.address, self.ha.address}
        try:
            if kind == "progmode":
                found = await self.bus.programming_mode()
                for address, result in self.reach.items():
                    if result.get("progmode") and address not in found:
                        result["progmode"] = False
                for address in found:
                    self.reach[address] = {
                        **self.reach.get(address, {}),
                        "address": address,
                        "checked": time.time(),
                        "reachable": True,
                        "progmode": True,
                    }
                    job["results"].append(self.reach[address])
                job["done"], job["found"] = 1, len(found)
            pending = [a for a in targets if a not in own]
            job["done"] += len(targets) - len(pending)
            # A line scan asks four addresses at a time: most are empty, and each empty one is a wait
            width = 4 if kind == "line" else 1
            for start in range(0, len(pending), width):
                batch = pending[start : start + width]
                if kind in ("info", "deep"):
                    results = [await (self.bus.deep if kind == "deep" else self.bus.info)(batch[0])]
                else:
                    # Thorough where a device is expected (the project has it, or it was heard sending); an
                    # empty address is only asked the quick way
                    expected = {*self.project.devices, *self.monitor.sources}
                    results = await asyncio.gather(
                        *(self.bus.ping(a, thorough=kind != "line" or a in expected) for a in batch)
                    )
                for address, result in zip(batch, results, strict=True):
                    job["done"] += 1
                    # A line scan keeps only what answered, and what the project expects there
                    if result["reachable"] or address in expected or kind != "line":
                        previous = self.reach.get(address, {})
                        # A plain check does not wipe what an earlier full read learned
                        self.reach[address] = {**previous, **result} if result["reachable"] else result
                        job["results"].append(self.reach[address])
                        job["found"] += 1 if result["reachable"] else 0
                    elif address in self.reach:
                        del self.reach[address]
                self._changed()
        except asyncio.CancelledError:
            job["error"] = "stopped"
        except BusError as e:
            job["error"] = str(e)
        except Exception as e:
            logger.exception("A device check failed")
            job["error"] = f"unexpected error: {e}"
        job["running"] = False
        job["results"] = job["results"][-300:]
        _write_json(self.data_dir / "devices.json", self.reach)
        self._changed()

    def forget_device(self, address: str) -> None:
        """Drop what a device answered earlier (it was removed, or the answer is out of date)."""
        if self.reach.pop(address, None) is not None:
            _write_json(self.data_dir / "devices.json", self.reach)
            self._changed()

    # ---- removing stray data from Home Assistant (only when switched on in the options) ----

    async def remove(self, kind: str, target: str) -> None:
        if not self.config.allow_remove:
            raise HubError("removing is switched off in the app's options")
        try:
            if kind == "stray_entity":
                if not any(e["entity_id"] == target for e in self.ha.stray_entities):
                    raise HubError("that is not a stray entity")
                await self.ws.call({"type": "config/entity_registry/remove", "entity_id": target})
            elif kind == "entity":
                entity = next((e for e in self.ha.entities if e["entity_id"] == target), None)
                if entity is None or entity["source"] != "ui":
                    raise HubError(
                        "only an entity made in Home Assistant's KNX panel can be removed here; one from YAML is removed in configuration.yaml"
                    )
                await self.ws.call({"type": "knx/delete_entity", "entity_id": target})
            elif kind == "stray_device":
                if not any(d["id"] == target for d in self.ha.stray_devices):
                    raise HubError("that is not a stray device")
                await self.ws.call({"type": "config/device_registry/remove", "device_id": target})
            else:
                raise HubError("unknown kind")
        except HAClientError as e:
            raise HubError(f"Home Assistant refused: {e}") from e
        logger.info("Removed %s %s from Home Assistant", kind, target)
        self._refresh_wanted.set()


def _is_individual(address: str) -> bool:
    parts = address.split(".")
    try:
        area, line, device = (int(p) for p in parts)
    except ValueError:
        return False
    return len(parts) == 3 and 0 <= area <= 15 and 0 <= line <= 15 and 0 <= device <= 255


def _ga_sort(address: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in address.split("/"))


def _address_key(address: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in address.split("."))
