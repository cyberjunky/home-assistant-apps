"""The checks: what is wrong in a snapshot of Home Assistant, as a list of findings.

A finding says what is wrong with one thing (an entity, a device, an integration, an automation),
why the app thinks so, and which repairs fit. The checks only read the snapshot; the repairs
themselves are in fixes.py.

Each finding:
    id        stable from scan to scan, so "ignore" and the history can follow it
    category  offline | login | config | bug | orphan | reference | statistics | ha | system
    severity  high | medium | low
    kind      the check that found it
    title, text
    subject   {type, id, name}
    integration, since (unix time or None), evidence [text], affected [entity ids]
    fixes     [{fix, label, args, danger?, word?}]   word: to be typed before it runs
    links     [{label, path}] into Home Assistant, or {label, url} elsewhere
"""

import hashlib
import math
import re
from collections.abc import Iterator
from datetime import datetime

from .snapshot import Snapshot

CATEGORIES = {
    "offline": "Not reachable",
    "login": "Login no longer valid",
    "config": "Faulty configuration",
    "bug": "Software fault",
    "orphan": "Left behind",
    "reference": "Broken reference",
    "statistics": "Statistics",
    "ha": "Reported by Home Assistant",
    "system": "System",
}
SEVERITY_WEIGHT = {"high": 8, "medium": 3, "low": 1}
ENTRY_OK = ("loaded", "setup_in_progress")
# Domains whose entities are made in Home Assistant itself: "unavailable" there means a faulty configuration
CONFIG_DOMAINS = ("automation", "script", "scene")
LOG_MAX = 120
ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
# Entity ids inside a template: states('x.y'), is_state("x.y", ...), state_attr('x.y', ...), states.x.y
IN_TEMPLATE = re.compile(
    r"""(?:states|is_state|state_attr|is_state_attr|has_value|expand|device_id|area_name)\(\s*['"]([a-z][a-z0-9_]*\.[a-z0-9_]+)['"]"""
    r"""|states\.([a-z][a-z0-9_]*)\.([a-z0-9_]+)"""
)
# Keys whose value is an entity id or a list of them
ENTITY_KEYS = ("entity_id", "entity", "entities", "camera_image", "entity_ids")

# What a line in the log is about, by the words in it. First match wins
LOG_RULES = (
    (
        "login",
        r"auth\w* (failed|error|invalid)|invalid[_ ]auth|unauthori[sz]ed|\b401\b|\b403\b|reauth|credentials|token (is |has )?(expired|invalid)|invalid (api )?key|login (failed|attempt)",
    ),
    ("deprecated", r"deprecat|will stop working|will be removed in|is scheduled for removal|no longer supported"),
    (
        "config",
        r"invalid config|platform error|is an invalid option|required key not provided|extra keys not allowed|not a valid value|error loading /config|templateerror|undefinederror|template variable|error (while )?rendering|referenced (entities|devices|areas)|unknown (entity|device|service|action)|does not generate unique ids|entity id already exists|not allowed to be|invalid (slug|entity|url)",
    ),
    (
        "offline",
        r"timed? ?out|timeout|connection (refused|reset|error|lost|closed)|cannot connect|can't connect|unable to connect|failed to connect|not reachable|unreachable|no route to host|name or service not known|name resolution|host is down|disconnected|not ready|retrying|is unavailable|device (is )?offline|no response|clientconnectorerror|serverdisconnected",
    ),
    ("slow", r"took longer than|is taking over|blocking call|detected blocking|is taking longer"),
)


def finding_id(kind: str, subject: str) -> str:
    """Short, stable and safe in an address."""
    return f"{kind}-{hashlib.sha1(subject.encode()).hexdigest()[:12]}"


def parse_time(value) -> float | None:
    if not value:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def friendly(state: dict | None, entity: dict | None, entity_id: str) -> str:
    name = ((state or {}).get("attributes") or {}).get("friendly_name")
    return name or (entity or {}).get("name") or (entity or {}).get("original_name") or entity_id


def device_name(device: dict) -> str:
    return device.get("name_by_user") or device.get("name") or device.get("model") or device.get("id", "")


def integration_name(snap: Snapshot, domain: str) -> str:
    return (snap.manifests.get(domain) or {}).get("name") or domain.replace("_", " ").capitalize()


def integration_links(snap: Snapshot, domain: str, tracker: bool = False) -> list[dict]:
    links = [{"label": f"Open {integration_name(snap, domain)}", "path": f"/config/integrations/integration/{domain}"}]
    manifest = snap.manifests.get(domain) or {}
    if tracker and manifest.get("issue_tracker"):
        links.append({"label": "Report it to its makers", "url": manifest["issue_tracker"]})
    if manifest.get("documentation"):
        links.append({"label": "Documentation", "url": manifest["documentation"]})
    return links


def is_custom(snap: Snapshot, domain: str) -> bool:
    manifest = snap.manifests.get(domain)
    return bool(manifest) and manifest.get("is_built_in") is False


class Index:
    """The snapshot's lists, by what the checks look things up with."""

    def __init__(self, snap: Snapshot):
        self.snap = snap
        self.entity = {e["entity_id"]: e for e in snap.entities}
        self.device = {d["id"]: d for d in snap.devices}
        self.entry = {e["entry_id"]: e for e in snap.entries}
        self.by_device: dict[str, list[dict]] = {}
        for entity in snap.entities:
            if entity.get("device_id"):
                self.by_device.setdefault(entity["device_id"], []).append(entity)
        self.by_domain: dict[str, list[dict]] = {}
        for entry in snap.entries:
            self.by_domain.setdefault(entry["domain"], []).append(entry)

    def exists(self, entity_id: str) -> bool:
        return entity_id in self.snap.states or entity_id in self.entity

    def entry_failed(self, entry_id: str | None) -> bool:
        entry = self.entry.get(entry_id or "")
        return bool(entry) and (entry.get("state") not in ENTRY_OK or bool(entry.get("disabled_by")))

    def reload_fix(self, entry_id: str | None) -> list[dict]:
        entry = self.entry.get(entry_id or "")
        if not entry:
            return []
        return [
            {
                "fix": "reload_entry",
                "label": f"Reload {entry.get('title') or entry['domain']}",
                "args": {"entry_id": entry["entry_id"]},
            }
        ]


def cleanup_entity_fix(entity_id: str) -> dict:
    return {
        "fix": "cleanup_entity",
        "label": "Remove it and everything stored about it",
        "args": {"entity_id": entity_id},
        "danger": True,
        "word": "REMOVE",
    }


def cleanup_device_fix(device_id: str) -> dict:
    return {
        "fix": "cleanup_device",
        "label": "Remove the device, its entities and what is stored about them",
        "args": {"device_id": device_id},
        "danger": True,
        "word": "REMOVE",
    }


# ---------------------------------------------------------------------- integrations
ENTRY_STATES = {
    "setup_error": (
        "high",
        "failed to start",
        "Home Assistant tried to start it and it gave an error. It stays off until it is reloaded.",
    ),
    "setup_retry": (
        "medium",
        "can't start yet and keeps trying",
        "Home Assistant keeps retrying. Usually the device or service it talks to can't be reached.",
    ),
    "migration_error": (
        "high",
        "could not be upgraded",
        "Its stored settings could not be brought to the current version. That is a fault in the integration.",
    ),
    "failed_unload": (
        "medium",
        "could not be stopped",
        "It did not stop cleanly. Only a restart of Home Assistant clears this.",
    ),
    "not_loaded": ("low", "is not started", "It is set up but not running, and it is not switched off."),
}


def check_entries(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    for entry in snap.entries:
        state, domain = entry.get("state"), entry["domain"]
        if entry.get("disabled_by") or state in ENTRY_OK or state not in ENTRY_STATES:
            continue
        severity, what, why = ENTRY_STATES[state]
        reason = entry.get("reason") or ""
        category = "offline" if state == "setup_retry" else "bug" if state == "migration_error" else "config"
        for name, pattern in LOG_RULES:
            if reason and name in ("login", "config", "offline") and re.search(pattern, reason, re.I):
                category = name
                break
        title = entry.get("title") or integration_name(snap, domain)
        affected = [
            e["entity_id"]
            for e in snap.entities
            if e.get("config_entry_id") == entry["entry_id"] and not e.get("disabled_by")
        ]
        fixes = ix.reload_fix(entry["entry_id"]) if state != "failed_unload" else []
        yield {
            "id": finding_id("entry", entry["entry_id"]),
            "category": category,
            "severity": severity,
            "kind": "entry_" + state,
            "title": f"{title} {what}",
            "text": why
            + (
                " Its login is no longer accepted: sign in again from the integration's page."
                if category == "login"
                else ""
            ),
            "subject": {"type": "integration", "id": entry["entry_id"], "name": title},
            "integration": domain,
            "since": None,
            "evidence": ([f"Home Assistant says: {reason}"] if reason else [])
            + ([f"{len(affected)} entities depend on it"] if affected else []),
            "affected": affected,
            "fixes": fixes,
            "links": integration_links(snap, domain, tracker=category == "bug"),
        }


# ---------------------------------------------------------------------- entities and devices
def check_entities(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    # device id (or "\0" + entity id for one without a device) -> entity ids
    offline: dict[str, list[str]] = {}
    dropped: dict[str, list[str]] = {}
    for entity in snap.entities:
        entity_id = entity["entity_id"]
        if entity.get("disabled_by"):
            continue
        state = snap.states.get(entity_id)
        domain = entity_id.split(".")[0]
        platform = entity.get("platform") or ""
        entry_id = entity.get("config_entry_id")
        name = friendly(state, entity, entity_id)
        subject = {"type": "entity", "id": entity_id, "name": name}
        common = {
            "subject": subject,
            "integration": platform,
            "affected": [entity_id],
            "since": parse_time((state or {}).get("last_changed")),
        }
        entity_link = [{"label": "Open the entity", "path": f"/config/entities?historyBack=1&filter={entity_id}"}]

        device = ix.device.get(entity.get("device_id") or "")
        if entry_id and entry_id not in ix.entry:
            if device and not any(e in ix.entry for e in device.get("config_entries") or []):
                continue  # the whole device is left behind: its own finding covers this
            yield {
                **common,
                "id": finding_id("entity_gone", entity_id),
                "category": "orphan",
                "severity": "medium",
                "kind": "entity_without_integration",
                "title": f"{name} belongs to an integration that is gone",
                "text": "The integration this entity came from has been removed, but the entity is still registered. Nothing will ever update it again.",
                "evidence": [f"Entity {entity_id}", f"Came from {integration_name(snap, platform)}"],
                "fixes": [cleanup_entity_fix(entity_id)],
                "links": entity_link,
            }
            continue
        if ix.entry_failed(entry_id):
            continue  # the integration's own finding covers it
        attributes = (state or {}).get("attributes") or {}
        if state and attributes.get("restored"):
            dropped.setdefault(entity.get("device_id") or "\0" + entity_id, []).append(entity_id)
        elif state and state.get("state") == "unavailable":
            if domain in CONFIG_DOMAINS:
                edit = f"/config/{domain}/edit/{attributes.get('id')}" if attributes.get("id") else f"/config/{domain}"
                yield {
                    **common,
                    "id": finding_id("config_entity", entity_id),
                    "category": "config",
                    "severity": "high",
                    "kind": "invalid_" + domain,
                    "title": f"{domain.capitalize()} {name} could not be loaded",
                    "text": f"Home Assistant could not load this {domain}: its configuration has an error, such as a trigger or action that no longer exists. It does nothing until that is fixed.",
                    "evidence": [f"Entity {entity_id}", "State: unavailable"],
                    "fixes": [],
                    "links": [{"label": f"Open the {domain}", "path": edit}],
                }
            else:
                offline.setdefault(entity.get("device_id") or "\0" + entity_id, []).append(entity_id)
        elif state is None and domain not in ("event",):
            yield {
                **common,
                "id": finding_id("entity_stateless", entity_id),
                "category": "orphan",
                "severity": "low",
                "kind": "entity_without_state",
                "title": f"{name} is registered but does not exist",
                "text": "The entity is in the registry and not disabled, but Home Assistant has no state for it. Its integration no longer creates it.",
                "evidence": [f"Entity {entity_id}", f"Came from {integration_name(snap, platform)}"],
                "fixes": [cleanup_entity_fix(entity_id)],
                "links": entity_link,
            }

    for key, entity_ids in dropped.items():
        device = ix.device.get(key)
        enabled = [e["entity_id"] for e in ix.by_device.get(key, []) if not e.get("disabled_by")]
        platform = (ix.entity.get(entity_ids[0]) or {}).get("platform") or ""
        times = [t for t in (parse_time(snap.states[e].get("last_changed")) for e in entity_ids) if t]
        why = (
            f"{integration_name(snap, platform)} is running but no longer offers this. It is only kept because it was"
            " registered once: it was removed or renamed there, or the integration dropped it."
        )
        if device and len(entity_ids) == len(enabled):
            name = device_name(device)
            yield {
                "id": finding_id("device_dropped", key),
                "category": "orphan",
                "severity": "medium",
                "kind": "device_not_provided",
                "title": f"{name} is no longer provided",
                "text": why,
                "subject": {"type": "device", "id": key, "name": name},
                "integration": platform,
                "since": min(times) if times else None,
                "evidence": [f"{e}: unavailable, restored from the registry" for e in entity_ids[:12]],
                "affected": entity_ids,
                "fixes": [cleanup_device_fix(key)],
                "links": [
                    {"label": "Open the device", "path": f"/config/devices/device/{key}"},
                    *integration_links(snap, platform),
                ],
            }
            continue
        for entity_id in entity_ids:
            entity = ix.entity.get(entity_id)
            name = friendly(snap.states.get(entity_id), entity, entity_id)
            yield {
                "id": finding_id("entity_restored", entity_id),
                "category": "orphan",
                "severity": "low" if entity_id.split(".")[0] in CONFIG_DOMAINS else "medium",
                "kind": "entity_not_provided",
                "title": f"{name} is no longer provided",
                "text": why,
                "subject": {"type": "entity", "id": entity_id, "name": name},
                "integration": platform,
                "since": parse_time(snap.states[entity_id].get("last_changed")),
                "evidence": [f"Entity {entity_id}", "State: unavailable, restored from the registry"],
                "affected": [entity_id],
                "fixes": [
                    cleanup_entity_fix(entity_id),
                    {"fix": "disable_entity", "label": "Disable it", "args": {"entity_id": entity_id}},
                ],
                "links": [
                    {"label": "Open the entity", "path": f"/config/entities?historyBack=1&filter={entity_id}"},
                    *integration_links(snap, platform),
                ],
            }

    # Entities without a registry entry (made in YAML without a unique id) can be unavailable too
    for entity_id, state in snap.states.items():
        if (
            entity_id not in ix.entity
            and state.get("state") == "unavailable"
            and entity_id.split(".")[0] not in CONFIG_DOMAINS
        ):
            offline.setdefault("\0" + entity_id, []).append(entity_id)

    for key, entity_ids in offline.items():
        times = [t for t in (parse_time(snap.states[e].get("last_changed")) for e in entity_ids) if t]
        since = min(times) if times else None
        if key.startswith("\0"):
            entity_id = entity_ids[0]
            entity = ix.entity.get(entity_id)
            platform = (entity or {}).get("platform") or ""
            name = friendly(snap.states.get(entity_id), entity, entity_id)
            yield {
                "id": finding_id("entity_offline", entity_id),
                "category": "offline",
                "severity": "low",
                "kind": "entity_unavailable",
                "title": f"{name} is unavailable",
                "text": "Its integration is running but has no value for this entity. What it reads from is switched off, out of range or not answering.",
                "subject": {"type": "entity", "id": entity_id, "name": name},
                "integration": platform,
                "since": since,
                "evidence": [f"Entity {entity_id}"],
                "affected": entity_ids,
                "fixes": ix.reload_fix((entity or {}).get("config_entry_id"))
                + (
                    [
                        {"fix": "disable_entity", "label": "Disable it", "args": {"entity_id": entity_id}},
                        cleanup_entity_fix(entity_id),
                    ]
                    if entity
                    else []
                ),
                "links": [{"label": "Open the entity", "path": f"/config/entities?historyBack=1&filter={entity_id}"}],
            }
            continue
        device = ix.device.get(key) or {"id": key}
        enabled = [e["entity_id"] for e in ix.by_device.get(key, []) if not e.get("disabled_by")]
        everything = len(entity_ids) == len(enabled)
        name = device_name(device)
        platform = (ix.entity.get(entity_ids[0]) or {}).get("platform") or ""
        entry_id = device.get("primary_config_entry") or next(iter(device.get("config_entries") or []), None)
        yield {
            "id": finding_id("device_offline", key),
            "category": "offline",
            "severity": "medium" if everything else "low",
            "kind": "device_offline" if everything else "device_partly_unavailable",
            "title": f"{name} is not reachable"
            if everything
            else f"{name}: {len(entity_ids)} of {len(enabled)} entities are unavailable",
            "text": "Every entity of this device is unavailable: it is switched off, out of range, has an empty battery or lost its network."
            if everything
            else "Part of this device does not report. That part may not be supported, be switched off on the device, or wait for a first reading.",
            "subject": {"type": "device", "id": key, "name": name},
            "integration": platform,
            "since": since,
            "evidence": [f"{len(entity_ids)} of its {len(enabled)} entities are unavailable"],
            "affected": entity_ids,
            "fixes": ix.reload_fix(entry_id) + ([cleanup_device_fix(key)] if everything else []),
            "links": [{"label": "Open the device", "path": f"/config/devices/device/{key}"}],
        }


def check_devices(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    for device in snap.devices:
        if device.get("disabled_by"):
            continue
        device_id, name = device["id"], device_name(device)
        entries = device.get("config_entries") or []
        subject = {"type": "device", "id": device_id, "name": name}
        link = [{"label": "Open the device", "path": f"/config/devices/device/{device_id}"}]
        entities = ix.by_device.get(device_id, [])
        if entries and not any(e in ix.entry for e in entries):
            yield {
                "id": finding_id("device_gone", device_id),
                "category": "orphan",
                "severity": "medium",
                "kind": "device_without_integration",
                "title": f"{name} belongs to an integration that is gone",
                "text": "The integration this device came from has been removed, but the device is still registered.",
                "subject": subject,
                "integration": "",
                "since": None,
                "evidence": [f"{len(entities)} entities still point at it"],
                "affected": [e["entity_id"] for e in entities],
                "fixes": [cleanup_device_fix(device_id)],
                "links": link,
            }
        elif (
            not entities
            and device.get("entry_type") != "service"
            and not any(d.get("via_device_id") == device_id for d in snap.devices)
        ):
            domain = (ix.entry.get(entries[0]) or {}).get("domain", "") if entries else ""
            yield {
                "id": finding_id("device_empty", device_id),
                "category": "orphan",
                "severity": "low",
                "kind": "device_without_entities",
                "title": f"{name} has no entities",
                "text": "The device is registered but nothing belongs to it. That is normal for a few devices, such as a remote that only sends events; otherwise it was left behind when its entities were removed.",
                "subject": subject,
                "integration": domain,
                "since": None,
                "evidence": [
                    f"Made by {device.get('manufacturer') or 'an unknown maker'}, model {device.get('model') or 'unknown'}"
                ],
                "affected": [],
                "fixes": [cleanup_device_fix(device_id)],
                "links": link,
            }


# ---------------------------------------------------------------------- references
def references(node, known_domains: set[str]) -> tuple[set[str], set[str]]:
    """The entity ids and device ids a configuration (an automation, a dashboard) refers to."""
    entities: set[str] = set()
    devices: set[str] = set()

    def walk(value, key: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, str(k))
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, str):
            if key == "device_id" and re.fullmatch(r"[0-9a-f]{32}", value):
                devices.add(value)
            elif key in ENTITY_KEYS:
                for part in value.split(","):
                    part = part.strip()
                    if ENTITY_ID.match(part) and part.split(".")[0] in known_domains:
                        entities.add(part)
            elif "{" in value:
                for match in IN_TEMPLATE.finditer(value):
                    entity_id = match.group(1) or f"{match.group(2)}.{match.group(3)}"
                    if entity_id.split(".")[0] in known_domains:
                        entities.add(entity_id)

    walk(node)
    return entities, devices


def check_references(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    known = {e.split(".")[0] for e in snap.states} | {e.split(".")[0] for e in ix.entity}

    def missing(config) -> list[str]:
        entities, devices = references(config, known)
        gone = sorted(e for e in entities if not ix.exists(e))
        gone += sorted(f"device {d}" for d in devices if d not in ix.device)
        return gone

    for domain, configs in (("automation", snap.automations), ("script", snap.scripts)):
        for entity_id, config in configs.items():
            gone = missing(config)
            if not gone:
                continue
            name = friendly(snap.states.get(entity_id), ix.entity.get(entity_id), entity_id)
            item_id = config.get("id") or ((snap.states.get(entity_id) or {}).get("attributes") or {}).get("id")
            path = f"/config/{domain}/edit/{item_id or entity_id.split('.', 1)[1]}"
            yield {
                "id": finding_id("reference", entity_id),
                "category": "reference",
                "severity": "medium",
                "kind": domain + "_reference",
                "title": f"{domain.capitalize()} {name} uses {len(gone)} thing{'s' if len(gone) > 1 else ''} that no longer exist{'' if len(gone) > 1 else 's'}",
                "text": f"This {domain} names entities or devices that Home Assistant doesn't have. The steps that use them fail or do nothing.",
                "subject": {"type": domain, "id": entity_id, "name": name},
                "integration": domain,
                "since": None,
                "evidence": gone[:20],
                "affected": [entity_id],
                "fixes": [],
                "links": [{"label": f"Open the {domain}", "path": path}],
            }

    for entity_id, state in snap.states.items():
        domain = entity_id.split(".")[0]
        members = (state.get("attributes") or {}).get("entity_id")
        if domain not in ("scene", "group") or not isinstance(members, list):
            continue
        gone = sorted(m for m in members if isinstance(m, str) and not ix.exists(m))
        if gone:
            name = friendly(state, ix.entity.get(entity_id), entity_id)
            yield {
                "id": finding_id("reference", entity_id),
                "category": "reference",
                "severity": "low",
                "kind": domain + "_reference",
                "title": f"{domain.capitalize()} {name} has {len(gone)} member{'s' if len(gone) > 1 else ''} that no longer exist{'' if len(gone) > 1 else 's'}",
                "text": f"This {domain} lists entities that Home Assistant doesn't have.",
                "subject": {"type": domain, "id": entity_id, "name": name},
                "integration": domain,
                "since": None,
                "evidence": gone[:20],
                "affected": [entity_id],
                "fixes": [],
                "links": [
                    {
                        "label": f"Open the {domain}s",
                        "path": "/config/scene" if domain == "scene" else "/config/helpers",
                    }
                ],
            }

    for board in snap.dashboards:
        gone = missing(board["config"])
        if gone:
            title = board["title"] or board["url_path"] or "Overview"
            yield {
                "id": finding_id("dashboard", board["url_path"] or "lovelace"),
                "category": "reference",
                "severity": "low",
                "kind": "dashboard_reference",
                "title": f"Dashboard {title} shows {len(gone)} thing{'s' if len(gone) > 1 else ''} that no longer exist{'' if len(gone) > 1 else 's'}",
                "text": 'Cards on this dashboard point at entities Home Assistant doesn\'t have. They show as "Entity not found".',
                "subject": {"type": "dashboard", "id": board["url_path"] or "lovelace", "name": title},
                "integration": "lovelace",
                "since": None,
                "evidence": gone[:30],
                "affected": [],
                "fixes": [],
                "links": [{"label": "Open the dashboard", "path": f"/{board['url_path'] or 'lovelace'}"}],
            }


# ---------------------------------------------------------------------- statistics
STATISTIC_ISSUES = {
    "entity_not_recorded": (
        "low",
        "is not recorded",
        "The recorder is set to leave this entity out, so its long-term statistics no longer grow.",
    ),
    "entity_no_longer_recorded": (
        "low",
        "is no longer recorded",
        "The recorder is set to leave this entity out, so its long-term statistics stopped.",
    ),
    "no_state": (
        "low",
        "has no entity any more",
        "There are long-term statistics for it, but the entity itself is gone.",
    ),
    "state_class_removed": (
        "low",
        "lost its state class",
        "The entity no longer says what kind of measurement it is, so no new statistics are made.",
    ),
    "units_changed": (
        "medium",
        "changed its unit",
        "The entity now reports in another unit than its stored statistics. New values are not added until that is settled.",
    ),
    "mean_type_changed": (
        "medium",
        "changed how it is averaged",
        "The entity's statistics were made with another kind of mean than it reports now.",
    ),
    "unsupported_state_class": (
        "medium",
        "has a state class that doesn't fit",
        "The entity's state class no longer supports statistics.",
    ),
}


def check_statistics(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    for statistic_id, issues in (snap.statistic_issues or {}).items():
        kinds = [i.get("type") for i in issues if i.get("type") in STATISTIC_ISSUES]
        if not kinds:
            continue
        kind = max(kinds, key=lambda k: SEVERITY_WEIGHT[STATISTIC_ISSUES[k][0]])
        severity, what, why = STATISTIC_ISSUES[kind]
        name = friendly(snap.states.get(statistic_id), ix.entity.get(statistic_id), statistic_id)
        data = next((i.get("data") or {} for i in issues if i.get("type") == kind), {})
        evidence = [f"Statistic {statistic_id}"]
        if kind == "units_changed":
            evidence.append(f"Stored in {data.get('metadata_unit')}, now reports in {data.get('state_unit')}")
        yield {
            "id": finding_id("statistic", statistic_id),
            "category": "statistics",
            "severity": severity,
            "kind": "statistic_" + kind,
            "title": f"{name} {what}",
            "text": why,
            "subject": {"type": "statistic", "id": statistic_id, "name": name},
            "integration": (ix.entity.get(statistic_id) or {}).get("platform") or "recorder",
            "since": None,
            "evidence": evidence,
            "affected": [statistic_id] if ix.exists(statistic_id) else [],
            "fixes": [
                {
                    "fix": "clear_statistics",
                    "label": "Delete its long-term statistics",
                    "args": {"statistic_ids": [statistic_id]},
                    "danger": True,
                    "word": "DELETE",
                }
            ],
            "links": [{"label": "Statistics in Home Assistant", "path": "/developer-tools/statistics"}],
        }
    reported = set(snap.statistic_issues or {})
    left = sorted(
        s["statistic_id"]
        for s in snap.statistic_ids
        if s.get("source") == "recorder" and s["statistic_id"] not in reported and not ix.exists(s["statistic_id"])
    )
    if left:
        yield {
            "id": finding_id("statistics_left", "all"),
            "category": "orphan",
            "severity": "low",
            "kind": "statistics_without_entity",
            "title": f"Statistics of {len(left)} entit{'ies' if len(left) > 1 else 'y'} that no longer exist{'' if len(left) > 1 else 's'}",
            "text": "Long-term statistics are kept for good, also after their entity is removed. These take room in the database and still show in the statistics pickers.",
            "subject": {"type": "statistic", "id": "left-behind", "name": "Statistics left behind"},
            "integration": "recorder",
            "since": None,
            "evidence": left[:40] + ([f"and {len(left) - 40} more"] if len(left) > 40 else []),
            "affected": [],
            "fixes": [
                {
                    "fix": "clear_statistics",
                    "label": f"Delete the statistics of all {len(left)}",
                    "args": {"statistic_ids": left},
                    "danger": True,
                    "word": "DELETE",
                }
            ],
            "links": [{"label": "Statistics in Home Assistant", "path": "/developer-tools/statistics"}],
        }


# ---------------------------------------------------------------------- what Home Assistant reports itself
def fill_placeholders(text: str, values: dict | None) -> str:
    for key, value in (values or {}).items():
        text = text.replace("{" + key + "}", str(value))
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # a Markdown link reads as its words


def check_repairs(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    for issue in snap.repairs:
        if issue.get("ignored") or issue.get("dismissed_version"):
            continue
        domain = issue.get("issue_domain") or issue.get("domain") or ""
        key = issue.get("translation_key") or issue.get("issue_id") or ""
        base = f"component.{issue.get('domain')}.issues.{key}"
        values = issue.get("translation_placeholders")
        title = fill_placeholders(snap.issue_texts.get(base + ".title", ""), values)
        text = fill_placeholders(snap.issue_texts.get(base + ".description", ""), values)
        severity = {"critical": "high", "error": "high", "warning": "medium"}.get(issue.get("severity"), "medium")
        links = [{"label": "Open Repairs in Home Assistant", "path": "/config/repairs"}]
        if issue.get("learn_more_url"):
            links.append({"label": "Learn more", "url": issue["learn_more_url"]})
        evidence = [f"Repair {issue.get('domain')}/{issue.get('issue_id')}"]
        if issue.get("breaks_in_ha_version"):
            evidence.append(f"Stops working in Home Assistant {issue['breaks_in_ha_version']}")
        yield {
            "id": finding_id("repair", f"{issue.get('domain')}/{issue.get('issue_id')}"),
            "category": "ha",
            "severity": severity,
            "kind": "repair",
            "title": title or f"{integration_name(snap, domain)}: {key.replace('_', ' ')}",
            "text": text
            or "Home Assistant raised this repair itself. Open Repairs for what it asks."
            + (" It offers to fix this for you there." if issue.get("is_fixable") else ""),
            "subject": {"type": "integration", "id": domain, "name": integration_name(snap, domain)},
            "integration": domain,
            "since": parse_time(issue.get("created")),
            "evidence": evidence,
            "affected": [],
            "fixes": [],
            "links": links,
        }


def check_system(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    check = snap.config_check
    if check and check.get("result") == "invalid":
        yield {
            "id": finding_id("config_check", "core"),
            "category": "config",
            "severity": "high",
            "kind": "configuration_invalid",
            "title": "The configuration has an error",
            "text": "Home Assistant's own check of configuration.yaml and the files it includes fails. It keeps running on what it loaded before, but will not restart cleanly until this is fixed.",
            "subject": {"type": "system", "id": "configuration", "name": "configuration.yaml"},
            "integration": "homeassistant",
            "since": None,
            "evidence": [line for line in str(check.get("errors") or "").splitlines() if line.strip()][:20],
            "affected": [],
            "fixes": [],
            "links": [{"label": "Check configuration in Home Assistant", "path": "/developer-tools/yaml"}],
        }
    resolution = snap.resolution or {}
    for group, severity, what in (
        ("unhealthy", "high", "not healthy"),
        ("unsupported", "medium", "not a supported installation"),
    ):
        for reason in resolution.get(group) or []:
            yield {
                "id": finding_id("supervisor_" + group, reason),
                "category": "system",
                "severity": severity,
                "kind": "supervisor_" + group,
                "title": f"The system is {what}: {str(reason).replace('_', ' ')}",
                "text": "The Supervisor reports this about the machine Home Assistant runs on. While it lasts, updates or backups may be held back.",
                "subject": {"type": "system", "id": str(reason), "name": "Supervisor"},
                "integration": "hassio",
                "since": None,
                "evidence": [f"Reason: {reason}"],
                "affected": [],
                "fixes": [],
                "links": [
                    {"label": "System in Home Assistant", "path": "/config/system"},
                    {"label": "What this means", "url": f"https://www.home-assistant.io/more-info/{group}/{reason}"},
                ],
            }
    suggestions = resolution.get("suggestions") or []
    for issue in resolution.get("issues") or []:
        fits = [
            s
            for s in suggestions
            if s.get("context") == issue.get("context") and s.get("reference") == issue.get("reference")
        ]
        reference = f" ({issue['reference']})" if issue.get("reference") else ""
        yield {
            "id": finding_id("supervisor_issue", str(issue.get("uuid") or issue.get("type"))),
            "category": "system",
            "severity": "medium",
            "kind": "supervisor_issue",
            "title": f"{str(issue.get('type', 'issue')).replace('_', ' ').capitalize()}{reference}",
            "text": f"The Supervisor found a problem with {str(issue.get('context', 'the system')).replace('_', ' ')}.",
            "subject": {"type": "system", "id": str(issue.get("uuid")), "name": "Supervisor"},
            "integration": "hassio",
            "since": None,
            "evidence": [f"Type {issue.get('type')}, context {issue.get('context')}"],
            "affected": [],
            "fixes": [
                {
                    "fix": "apply_suggestion",
                    "label": str(s.get("type", "fix")).replace("_", " ").capitalize(),
                    "args": {"uuid": s["uuid"]},
                    "danger": True,
                }
                for s in fits
                if s.get("uuid")
            ],
            "links": [{"label": "System in Home Assistant", "path": "/config/system"}],
        }


# ---------------------------------------------------------------------- the deep scan
def check_storage(ix: Index) -> Iterator[dict]:
    """Recorded history of entities Home Assistant no longer has: only known with the deep scan on."""
    database = ((ix.snap.storage or {}).get("database")) or {}
    orphans = database.get("orphans") or []
    if not orphans:
        return
    rows = sum(o["rows"] or 0 for o in orphans)
    shown = [
        f"{o['entity_id']}: {o['rows']:,} states" if o["rows"] is not None else o["entity_id"] for o in orphans[:40]
    ]
    yield {
        "id": finding_id("history_left", "all"),
        "category": "orphan",
        "severity": "low",
        "kind": "history_without_entity",
        "title": f"Recorded history of {len(orphans)} entit{'ies' if len(orphans) > 1 else 'y'} that no longer exist{'' if len(orphans) > 1 else 's'}",
        "text": "The database still holds states of entities that were removed. The recorder drops them by itself once they are older than it keeps history; until then they take room."
        f" Together they are {rows:,} stored states.",
        "subject": {"type": "database", "id": "history-left-behind", "name": "History left behind"},
        "integration": "recorder",
        "since": None,
        "evidence": shown + ([f"and {len(orphans) - 40} more"] if len(orphans) > 40 else []),
        "affected": [],
        "fixes": [
            {
                "fix": "purge_history",
                "label": f"Delete the history of all {len(orphans)}",
                "args": {"entity_ids": [o["entity_id"] for o in orphans]},
                "danger": True,
                "word": "DELETE",
            }
        ],
        "links": [],
    }


# ---------------------------------------------------------------------- the log
def log_domain(entry: dict) -> str:
    """The integration a log line comes from, by its logger name or its file."""
    name = str(entry.get("name") or "")
    source = entry.get("source") or []
    path = str(source[0]) if source else ""
    for text, pattern in (
        (path, r"custom_components/([a-z0-9_]+)"),
        (name, r"custom_components\.([a-z0-9_]+)"),
        (name, r"homeassistant\.components\.([a-z0-9_]+)"),
        (path, r"components/([a-z0-9_]+)"),
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def classify_log(entry: dict, custom: bool) -> str:
    """offline | login | config | deprecated | slow | bug | other"""
    message = " ".join(str(m) for m in entry.get("message") or [])
    text = f"{message} {entry.get('exception') or ''}"
    for name, pattern in LOG_RULES:
        if re.search(pattern, text, re.I):
            return name
    if entry.get("exception"):
        return "bug"
    return "other"


LOG_KINDS = {
    "offline": (
        "offline",
        "can't reach its device or service",
        "The integration reports that what it talks to doesn't answer. Check that it is on and on the network; then reload the integration.",
    ),
    "login": (
        "login",
        "is refused its login",
        "The service no longer accepts the stored login. Sign in again from the integration's page.",
    ),
    "config": (
        "config",
        "reports a configuration error",
        "Something in its configuration is not accepted: a wrong option, a template that fails, or an entity that doesn't exist.",
    ),
    "deprecated": (
        "config",
        "uses something that is going away",
        "It still works, but uses a setting or feature that a later Home Assistant removes. Change it before that update.",
    ),
    "slow": (
        "bug",
        "is slow or blocks Home Assistant",
        "The integration takes too long or holds up Home Assistant while it works. That is for its makers to fix.",
    ),
    "bug": (
        "bug",
        "raised an error in its code",
        "The integration ran into a situation its code doesn't handle. That is a fault in the software: only an update of the integration fixes it.",
    ),
    "other": (
        "bug",
        "logged a problem",
        "Home Assistant logged this as a problem. Read the message for what it is about.",
    ),
}


def log_entries(snap: Snapshot) -> list[dict]:
    """The log's problems, each with what it is about. Used for the findings and for the Log page."""
    out = []
    for entry in snap.log:
        level = str(entry.get("level") or "").upper()
        if level not in ("ERROR", "CRITICAL", "WARNING"):
            continue
        domain = log_domain(entry)
        kind = classify_log(entry, is_custom(snap, domain))
        message = [str(m) for m in entry.get("message") or []]
        source = entry.get("source") or []
        out.append(
            {
                "key": finding_id("log", f"{entry.get('name')}|{message[0] if message else ''}"[:300]),
                "level": level,
                "domain": domain,
                "custom": is_custom(snap, domain),
                "kind": kind,
                "message": message[0] if message else "",
                "messages": message[:5],
                "exception": str(entry.get("exception") or "")[-3000:],
                "logger": entry.get("name") or "",
                "source": f"{source[0]}:{source[1]}" if len(source) > 1 else "",
                "count": int(entry.get("count") or 1),
                "first": parse_time(entry.get("first_occurred")),
                "last": parse_time(entry.get("timestamp")),
            }
        )
    out.sort(key=lambda e: -(e["last"] or 0))
    return out


def check_log(ix: Index) -> Iterator[dict]:
    snap = ix.snap
    # Warnings that only repeat a deprecation or tell of a slow update are not each a finding of their own
    for entry in log_entries(snap)[:LOG_MAX]:
        kind, domain = entry["kind"], entry["domain"]
        if entry["level"] == "WARNING" and kind in ("other", "slow"):
            continue
        category, what, why = LOG_KINDS[kind]
        name = integration_name(snap, domain) if domain else "Home Assistant"
        severity = "low" if entry["level"] == "WARNING" or kind == "deprecated" else "medium"
        if kind == "bug" and entry["count"] >= 20:
            severity = "high"
        entries = ix.by_domain.get(domain, [])
        # Reloading only helps where the cause is outside the software, and only when it is clear which entry is meant
        fixes = ix.reload_fix(entries[0]["entry_id"]) if kind in ("offline", "login") and len(entries) == 1 else []
        evidence = entry["messages"] + ([f"Logged {entry['count']} times"] if entry["count"] > 1 else [])
        if entry["source"]:
            evidence.append(f"From {entry['source']}")
        if entry["exception"]:
            evidence.append(entry["exception"].strip().splitlines()[-1])
        yield {
            "id": entry["key"],
            "category": category,
            "severity": severity,
            "kind": "log_" + kind,
            "title": f"{name} {what}",
            "text": why
            + (
                " It is a custom integration: report it to its makers, not to Home Assistant."
                if entry["custom"] and category == "bug"
                else ""
            ),
            "subject": {"type": "integration" if domain else "log", "id": domain or entry["logger"], "name": name},
            "integration": domain,
            "since": entry["first"],
            "evidence": evidence,
            "affected": [],
            "fixes": fixes,
            "links": (integration_links(snap, domain, tracker=category == "bug") if domain else [])
            + [{"label": "Logs in Home Assistant", "path": "/config/logs"}],
            "count": entry["count"],
        }


# ---------------------------------------------------------------------- all of it
CHECKS = (
    check_entries,
    check_entities,
    check_devices,
    check_references,
    check_statistics,
    check_repairs,
    check_system,
    check_storage,
    check_log,
)
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def run(snap: Snapshot) -> list[dict]:
    """Every finding in the snapshot, worst first."""
    ix = Index(snap)
    found: dict[str, dict] = {}
    for check in CHECKS:
        for finding in check(ix):
            found.setdefault(finding["id"], finding)
    return sorted(found.values(), key=lambda f: (SEVERITY_ORDER[f["severity"]], f["category"], f["title"].lower()))


def score(findings: list[dict]) -> int:
    """Health from 0 to 100. Each finding takes a share of what is left, so a hundred small ones don't read as zero."""
    weight = sum(SEVERITY_WEIGHT[f["severity"]] for f in findings)
    return round(100 * math.exp(-weight / 60))


def counts(findings: list[dict]) -> dict:
    by_category = {key: 0 for key in CATEGORIES}
    by_severity = {"high": 0, "medium": 0, "low": 0}
    for finding in findings:
        by_category[finding["category"]] += 1
        by_severity[finding["severity"]] += 1
    return {"total": len(findings), "category": by_category, "severity": by_severity}
