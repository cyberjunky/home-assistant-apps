"""Everything Home Assistant says about itself at one moment, read in one go.

The checks work on this and nothing else, so they can be tested without a Home Assistant.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import storage
from .ha import HA, HAError

logger = logging.getLogger(__name__)

# Automations and scripts are asked for their configuration one by one: stop at this many
CONFIGS_MAX = 600
DASHBOARDS_MAX = 40


@dataclass
class Snapshot:
    taken: float = 0.0
    version: str = ""
    # entity_id -> state object
    states: dict[str, dict] = field(default_factory=dict)
    entities: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    repairs: list[dict] = field(default_factory=list)
    # "component.<domain>.issues.<key>.title" -> text, for the repairs
    issue_texts: dict[str, str] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    # statistic_id -> [{type, data}]
    statistic_issues: dict[str, list] = field(default_factory=dict)
    statistic_ids: list[dict] = field(default_factory=list)
    # domain -> manifest
    manifests: dict[str, dict] = field(default_factory=dict)
    # entity_id -> configuration
    automations: dict[str, dict] = field(default_factory=dict)
    scripts: dict[str, dict] = field(default_factory=dict)
    # [{url_path, title, config}]
    dashboards: list[dict] = field(default_factory=list)
    # {result, errors, warnings} of Home Assistant's own configuration check, or None when not run
    config_check: dict | None = None
    # The Supervisor's resolution centre, or None when it can't be read
    resolution: dict | None = None
    # What the deep scan read from Home Assistant's own storage, or None when it is off
    storage: dict | None = None
    # What could not be read, to say so instead of calling it healthy
    gaps: list[str] = field(default_factory=list)


async def take(ha: HA, check_config: bool = True, deep_dir: Path | None = None) -> Snapshot:
    """Read everything. Raises HAError when Home Assistant itself can't be reached."""
    snap = Snapshot(taken=time.time())
    async with ha.connect() as conn:
        snap.version = conn.version
        snap.states = {s["entity_id"]: s for s in await conn.call("get_states")}
        snap.entities = await conn.call("config/entity_registry/list")
        snap.devices = await conn.call("config/device_registry/list")
        snap.entries = await conn.call("config_entries/get")

        async def optional(name: str, kind: str, default, **fields):
            result = await conn.ask(kind, None, **fields)
            if result is None:
                snap.gaps.append(name)
                return default
            return result

        snap.repairs = (await optional("repairs", "repairs/list_issues", {})).get("issues") or []
        snap.log = await optional("the log", "system_log/list", [])
        snap.statistic_issues = await optional("statistics", "recorder/validate_statistics", {})
        snap.statistic_ids = await conn.ask("recorder/list_statistic_ids", [])
        manifests = await conn.ask("manifest/list", [])
        snap.manifests = {m["domain"]: m for m in manifests if m.get("domain")}

        domains = sorted({i.get("issue_domain") or i.get("domain") for i in snap.repairs if i.get("domain")})
        if domains:
            texts = await conn.ask(
                "frontend/get_translations", {}, language="en", category="issues", integration=domains
            )
            snap.issue_texts = (texts or {}).get("resources") or {}

        for domain, target in (("automation", snap.automations), ("script", snap.scripts)):
            ids = [e for e in snap.states if e.startswith(domain + ".")][:CONFIGS_MAX]
            for entity_id in ids:
                answer = await conn.ask(f"{domain}/config", None, entity_id=entity_id)
                if answer and answer.get("config"):
                    target[entity_id] = answer["config"]

        boards = [{"url_path": None, "title": "Overview"}]
        boards += [b for b in await conn.ask("lovelace/dashboards/list", []) if b.get("url_path")]
        for board in boards[:DASHBOARDS_MAX]:
            fields = {"url_path": board["url_path"]} if board["url_path"] else {}
            config = await conn.ask("lovelace/config", None, **fields)
            if isinstance(config, dict):
                snap.dashboards.append(
                    {"url_path": board["url_path"], "title": board.get("title") or "", "config": config}
                )

    if check_config:
        try:
            snap.config_check = await ha.core("POST", "/config/core/check_config")
        except HAError as e:
            logger.debug(f"Configuration check: {e}")
            snap.gaps.append("the configuration check")
    if deep_dir is not None:
        known = set(snap.states) | {e["entity_id"] for e in snap.entities}
        snap.storage = await asyncio.to_thread(storage.look, deep_dir, known)
    try:
        snap.resolution = await ha.supervisor("GET", "/resolution/info")
    except HAError as e:
        logger.debug(f"Resolution centre: {e}")
    return snap
