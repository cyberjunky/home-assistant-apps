"""The repairs. Each one is a short list of steps through Home Assistant's own API, and reports per
step whether it worked.

Nothing here touches Home Assistant's database or its .storage files directly. What can't be done
through the API is said so in the step, not forced another way.
"""

import logging

from . import checks
from .ha import HA, HAError
from .snapshot import Snapshot

logger = logging.getLogger(__name__)

# Entities per request to the recorder
PURGE_CHUNK = 50


class Steps:
    """What a repair did, step by step."""

    def __init__(self):
        self.steps: list[dict] = []

    def add(self, label: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"label": label, "ok": ok, "detail": detail})

    async def run(self, label: str, action, done: str = "") -> bool:
        """Do one step; a refusal becomes a failed step, and the repair goes on with the next."""
        try:
            result = await action
        except HAError as e:
            self.add(label, False, str(e))
            return False
        self.add(label, True, done(result) if callable(done) else done)
        return True

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(step["ok"] for step in self.steps)


async def ws(ha: HA, kind: str, **fields):
    async with ha.connect() as conn:
        return await conn.call(kind, **fields)


async def reload_entry(ha: HA, snap: Snapshot, steps: Steps, entry_id: str) -> None:
    def told(result) -> str:
        return "Home Assistant must be restarted for it to take effect" if (result or {}).get("require_restart") else ""

    await steps.run("Reload the integration", ha.core("POST", f"/config/config_entries/entry/{entry_id}/reload"), told)


async def delete_entry(ha: HA, snap: Snapshot, steps: Steps, entry_id: str) -> None:
    await steps.run("Remove the integration", ha.core("DELETE", f"/config/config_entries/entry/{entry_id}"))


async def disable_entity(ha: HA, snap: Snapshot, steps: Steps, entity_id: str) -> None:
    await steps.run(
        "Disable the entity", ws(ha, "config/entity_registry/update", entity_id=entity_id, disabled_by="user")
    )


async def enable_entity(ha: HA, snap: Snapshot, steps: Steps, entity_id: str) -> None:
    await steps.run("Enable the entity", ws(ha, "config/entity_registry/update", entity_id=entity_id, disabled_by=None))


async def clear_statistics(ha: HA, snap: Snapshot, steps: Steps, statistic_ids: list[str]) -> None:
    label = (
        f"Delete the long-term statistics of {len(statistic_ids)}"
        if len(statistic_ids) > 1
        else "Delete its long-term statistics"
    )
    await steps.run(label, ws(ha, "recorder/clear_statistics", statistic_ids=list(statistic_ids)))


async def cleanup_entity(ha: HA, snap: Snapshot, steps: Steps, entity_id: str) -> None:
    """Remove an entity and what is stored about it: its history, its statistics, its registry entry.

    The history goes first: the recorder only purges what it is asked to by entity id, and that is
    simplest while Home Assistant still knows the entity.
    """
    registered = any(e["entity_id"] == entity_id for e in snap.entities)
    await steps.run(
        f"Delete the history of {entity_id}",
        ha.service("recorder", "purge_entities", {"entity_id": [entity_id], "keep_days": 0}),
        "The recorder does this in the background",
    )
    if any(s.get("statistic_id") == entity_id for s in snap.statistic_ids):
        await steps.run(
            "Delete its long-term statistics", ws(ha, "recorder/clear_statistics", statistic_ids=[entity_id])
        )
    if registered:
        await steps.run(
            f"Remove {entity_id} from the registry", ws(ha, "config/entity_registry/remove", entity_id=entity_id)
        )
    elif entity_id in snap.states:
        await steps.run(
            f"Remove the state of {entity_id}",
            ha.core("DELETE", f"/states/{entity_id}"),
            "It has no registry entry: if its configuration still makes it, it comes back at the next restart",
        )


async def cleanup_device(ha: HA, snap: Snapshot, steps: Steps, device_id: str) -> None:
    """Remove a device: each of its entities with what is stored about them, then the device itself.

    Whether the device can go is checked first. Removing only its entities would leave the device,
    and its integration would make them again: then nothing is done at all.
    """
    device = next((d for d in snap.devices if d["id"] == device_id), None)
    if device is None:
        steps.add("Find the device", False, "Home Assistant no longer has this device")
        return
    way, why_not = checks.removal(snap, device)
    if not way:
        steps.add(
            "Check that the device can be removed",
            False,
            f"{why_not}, so nothing was done: removing only its entities would leave the device, and its integration "
            "would make them again. Remove it on the integration's own page, or reset the device.",
        )
        return
    for entity in [e for e in snap.entities if e.get("device_id") == device_id]:
        await cleanup_entity(ha, snap, steps, entity["entity_id"])
    if way == "gone":
        # Home Assistant drops a device by itself once nothing refers to it: its entities are gone now
        steps.add(
            "Remove the device",
            True,
            "No integration holds it any more: Home Assistant drops it now that its entities are gone",
        )
    elif way == "zha":
        await steps.run(
            "Remove the device from the Zigbee network",
            ha.service("zha", "remove", {"ieee": checks.zigbee_address(device)}),
            "ZHA removes it from its network and from Home Assistant",
        )
    else:
        await remove_from_registry(ha, snap, steps, device)


async def remove_from_registry(ha: HA, snap: Snapshot, steps: Steps, device: dict) -> None:
    """Remove a device through the device registry, the way Home Assistant's own Delete button does."""
    try:
        await ws(ha, "config/device_registry/remove", device_id=device["id"])
    except HAError as e:
        if "unknown command" not in str(e).lower():
            steps.add("Remove the device", False, str(e))
            return
    else:
        steps.add("Remove the device", True)
        return
    # A Home Assistant from before 2026.11 knows only the older command, one config entry at a time
    known = {e["entry_id"]: e for e in snap.entries}
    for entry_id in [e for e in device.get("config_entries") or [] if e in known]:
        title = known[entry_id].get("title") or known[entry_id]["domain"]
        await steps.run(
            f"Remove the device from {title}",
            ws(ha, "config/device_registry/remove_config_entry", device_id=device["id"], config_entry_id=entry_id),
        )


async def purge_history(ha: HA, snap: Snapshot, steps: Steps, entity_ids: list[str]) -> None:
    """Delete the recorded history of entities that no longer exist.

    The recorder does the deleting. It is asked by pattern, not by entity: a pattern is matched
    against what is in the database, which is the only place these entities still are.
    """
    for start in range(0, len(entity_ids), PURGE_CHUNK):
        chunk = list(entity_ids[start : start + PURGE_CHUNK])
        last = min(start + PURGE_CHUNK, len(entity_ids))
        await steps.run(
            f"Ask the recorder to delete the history of {start + 1} to {last} of {len(entity_ids)}",
            ha.service("recorder", "purge_entities", {"entity_globs": chunk, "keep_days": 0}),
            "The recorder does this in the background",
        )


async def apply_suggestion(ha: HA, snap: Snapshot, steps: Steps, uuid: str) -> None:
    await steps.run("Apply the Supervisor's suggestion", ha.supervisor("POST", f"/resolution/suggestion/{uuid}"))


FIXES = {
    "reload_entry": reload_entry,
    "delete_entry": delete_entry,
    "disable_entity": disable_entity,
    "enable_entity": enable_entity,
    "clear_statistics": clear_statistics,
    "cleanup_entity": cleanup_entity,
    "cleanup_device": cleanup_device,
    "apply_suggestion": apply_suggestion,
    "purge_history": purge_history,
}


async def apply(ha: HA, snap: Snapshot, fix: str, args: dict) -> Steps:
    """Run one repair. Unknown names are refused: the browser can only ask for what is in this file."""
    steps = Steps()
    handler = FIXES.get(fix)
    if handler is None:
        steps.add("Find the repair", False, f"There is no repair called {fix}")
        return steps
    await handler(ha, snap, steps, **args)
    logger.info(f"Repair {fix} {args}: {'done' if steps.ok else 'not all of it worked'}")
    return steps
