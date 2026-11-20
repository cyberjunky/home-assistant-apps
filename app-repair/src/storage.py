"""A look into Home Assistant's own storage, for what its API doesn't tell: which entities that no
longer exist still have recorded history, and what the registries still remember of removed ones.

This only runs when the deep_scan option is on, and it only reads. The database is opened
read-only, with short queries; the registry files are read as text. Deleting is never done here:
the history of a gone entity is purged by the recorder itself, asked through Home Assistant.
"""

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DATABASE = "home-assistant_v2.db"
# Counting rows is one indexed query per entity: enough for a real clean-up, not for a whole database
COUNTED_MAX = 300
LISTED_MAX = 60


def look_database(path: Path, known: set[str]) -> dict:
    """The recorded entities that Home Assistant no longer has, each with its number of stored states."""
    if not path.is_file():
        return {
            "found": False,
            "note": "The recorder doesn't use the built-in database file here, so it can't be looked into.",
        }
    out: dict = {"found": True, "size": sum(p.stat().st_size for p in path.parent.glob(path.name + "*") if p.is_file())}
    try:
        # Read-only: SQLite refuses every write on this connection
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as e:
        return {**out, "error": f"The database can't be opened: {e}"}
    try:
        rows = db.execute("SELECT metadata_id, entity_id FROM states_meta").fetchall()
        gone = [(meta, entity_id) for meta, entity_id in rows if entity_id not in known]
        orphans = []
        for meta, entity_id in gone[:COUNTED_MAX]:
            (count,) = db.execute("SELECT COUNT(*) FROM states WHERE metadata_id = ?", (meta,)).fetchone()
            orphans.append({"entity_id": entity_id, "rows": count})
        orphans += [{"entity_id": entity_id, "rows": None} for _, entity_id in gone[COUNTED_MAX:]]
    except sqlite3.Error as e:
        return {**out, "error": f"The database can't be read: {e}"}
    finally:
        db.close()
    orphans.sort(key=lambda o: -(o["rows"] or 0))
    return {**out, "entities": len(rows), "orphans": orphans}


def look_registry(storage: Path) -> dict:
    """What the entity and device registries still remember of what was removed."""
    out = {"deleted_entities": None, "deleted_devices": None, "sample": []}
    for name, key, field in (
        ("core.entity_registry", "deleted_entities", "entity_id"),
        ("core.device_registry", "deleted_devices", "id"),
    ):
        try:
            data = json.loads((storage / name).read_text()).get("data") or {}
        except (OSError, ValueError, AttributeError) as e:
            logger.debug(f"{name}: {e}")
            continue
        deleted = data.get(key) or []
        out[key] = len(deleted)
        if key == "deleted_entities":
            out["sample"] = sorted(str(d.get(field)) for d in deleted if d.get(field))[:LISTED_MAX]
    return out


def look(config_dir: Path, known: set[str]) -> dict:
    """Everything the deep scan reads. Never raises: what can't be read is said in the result."""
    config_dir = Path(config_dir)
    if not config_dir.is_dir():
        return {"available": False, "note": f"Home Assistant's configuration folder is not at {config_dir}."}
    return {
        "available": True,
        "database": look_database(config_dir / DATABASE, known),
        "registry": look_registry(config_dir / ".storage"),
    }
