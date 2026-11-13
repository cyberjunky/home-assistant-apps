"""The app's own document: the map pages, what you said about each host, and the networks.

One JSON file with a revision number, so two open browsers can't silently overwrite each other's
work: a save from an older revision is refused and the page reads the newer one.
"""

import json
import os
import uuid
from pathlib import Path

MAX_BYTES = 4 * 1024 * 1024
DEVICE_FIELDS = {"kind", "label", "notes", "network", "connection", "port", "uplink", "icon", "same_as"}


class Conflict(Exception):
    """The document was saved by someone else since this revision."""


def empty_doc() -> dict:
    return {
        "rev": 0,
        "pages": [{"id": uuid.uuid4().hex[:8], "name": "Network", "nodes": [], "groups": [], "links": [], "texts": []}],
        "devices": {},
        "networks": [],
    }


def clean(doc: dict) -> dict:
    """Only the parts the app knows, in the right shape. Raises ValueError for what can't be one."""
    if not isinstance(doc, dict):
        raise ValueError("The document must be an object")
    pages = doc.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("The document needs at least one page")
    out_pages = []
    seen = set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("id"), str) or page["id"] in seen:
            raise ValueError("Every page needs its own id")
        seen.add(page["id"])
        clean_page = {"id": page["id"], "name": str(page.get("name") or "Page")[:80]}
        for part in ("nodes", "groups", "links", "texts"):
            items = page.get(part) or []
            if not isinstance(items, list) or not all(
                isinstance(i, dict) and isinstance(i.get("id"), str) for i in items
            ):
                raise ValueError(f"{part} on page {clean_page['name']!r} must be a list of objects with an id")
            clean_page[part] = items
        ids = {n["id"] for n in clean_page["nodes"]}
        clean_page["links"] = [
            link for link in clean_page["links"] if link.get("from") in ids and link.get("to") in ids
        ]
        if isinstance(page.get("parent"), str):
            clean_page["parent"] = page["parent"]
        if isinstance(page.get("view"), dict):
            clean_page["view"] = page["view"]
        out_pages.append(clean_page)
    devices = doc.get("devices") or {}
    if not isinstance(devices, dict):
        raise ValueError("devices must be an object")
    out_devices = {}
    for key, meta in devices.items():
        if isinstance(meta, dict):
            kept = {k: v for k, v in meta.items() if k in DEVICE_FIELDS and v not in (None, "")}
            if kept:
                out_devices[str(key)] = kept
    networks = doc.get("networks") or []
    if not isinstance(networks, list) or not all(
        isinstance(n, dict) and isinstance(n.get("id"), str) for n in networks
    ):
        raise ValueError("networks must be a list of objects with an id")
    return {"rev": int(doc.get("rev") or 0), "pages": out_pages, "devices": out_devices, "networks": networks}


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.doc = clean(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            self.doc = empty_doc()

    def save(self, doc: dict, rev: int) -> dict:
        """Save a new document made from revision `rev`. Raises Conflict or ValueError."""
        if rev != self.doc["rev"]:
            raise Conflict()
        new = clean(doc)
        new["rev"] = self.doc["rev"] + 1
        text = json.dumps(new, separators=(",", ":"))
        if len(text) > MAX_BYTES:
            raise ValueError("The document is too large")
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, self.path)
        self.doc = new
        return new
