"""What you choose in the app itself: the scanner and its settings, profiles, how documents are
made and where they are copied to. Kept in /data/settings.json."""

import contextlib
import json
import logging
import os
import re
from pathlib import Path

from .config import OCR_LANGUAGES

logger = logging.getLogger(__name__)

BATCH_MODES = ("single", "feeder", "timed", "step")
DEFAULTS: dict = {
    # The scanner last used and, per scanner, the option values chosen for it
    "device": "",
    "values": {},
    "batch": {"mode": "single", "count": 0, "interval": 10},
    "profiles": [],
    # {date} {time} {year} {month} {day} {n} (pages)
    "name_pattern": "Scan {date} {time}",
    "jpeg_quality": 85,
    "ocr": True,
    "ocr_languages": ["eng"],
    # A folder in /share or /media (Home Assistant's network storage) that documents are copied to
    "share_folder": "",
    # {name} {date} {time} {year} {month} {day} {id}; a / makes folders
    "share_pattern": "{name}",
    "share_auto": False,
}


def _batch(value: object) -> dict:
    batch = dict(DEFAULTS["batch"])
    if isinstance(value, dict):
        if value.get("mode") in BATCH_MODES:
            batch["mode"] = value["mode"]
        for key, low, high in (("count", 0, 500), ("interval", 2, 600)):
            with contextlib.suppress(TypeError, ValueError):
                batch[key] = min(high, max(low, int(value.get(key, batch[key]))))
    return batch


def _values(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k)[:80]: str(v)[:400] for k, v in list(value.items())[:200] if re.match(r"^[A-Za-z0-9][\w-]*$", str(k))}


def clean(data: dict, base: dict | None = None) -> dict:
    """Settings that can be used, from what a file or the browser gave; what is missing comes from base."""
    out = json.loads(json.dumps(base or DEFAULTS))
    if not isinstance(data, dict):
        return out
    if isinstance(data.get("device"), str):
        out["device"] = data["device"][:300]
    if isinstance(data.get("values"), dict):
        out["values"] = {str(d)[:300]: _values(v) for d, v in list(data["values"].items())[:40]}
    if "batch" in data:
        out["batch"] = _batch(data["batch"])
    if isinstance(data.get("profiles"), list):
        profiles = []
        for p in data["profiles"][:50]:
            name = re.sub(r"\s+", " ", str(p.get("name") or "")).strip()[:60] if isinstance(p, dict) else ""
            if name and name not in [x["name"] for x in profiles]:
                profiles.append(
                    {
                        "name": name,
                        "device": str(p.get("device") or "")[:300],
                        "values": _values(p.get("values")),
                        "batch": _batch(p.get("batch")),
                    }
                )
        out["profiles"] = profiles
    for key in ("name_pattern", "share_pattern"):
        if isinstance(data.get(key), str) and data[key].strip():
            out[key] = data[key].strip()[:200]
    if isinstance(data.get("share_folder"), str):
        out["share_folder"] = data["share_folder"].strip()[:500]
    for key in ("ocr", "share_auto"):
        if key in data:
            out[key] = bool(data[key])
    if "jpeg_quality" in data:
        with contextlib.suppress(TypeError, ValueError):
            out["jpeg_quality"] = min(100, max(30, int(data["jpeg_quality"])))
    if isinstance(data.get("ocr_languages"), list):
        out["ocr_languages"] = [x for x in data["ocr_languages"] if x in OCR_LANGUAGES] or ["eng"]
    return out


class Settings:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.data = clean(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            self.data = clean({})

    def __getitem__(self, key: str):
        return self.data[key]

    def update(self, changes: dict) -> dict:
        """Change the given settings and keep the rest."""
        self.data = clean(changes, self.data)
        self.save()
        return self.data

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(self.data, indent=2))
            os.replace(tmp, self.path)
        except OSError as e:
            logger.error(f"Cannot save the settings: {e}")

    def remember(self, device: str, values: dict, batch: dict | None = None) -> None:
        """Keep what was used for a scan, to start with it next time."""
        self.data["device"] = device
        self.data["values"][device] = _values(values)
        if batch is not None:
            self.data["batch"] = _batch(batch)
        self.save()

    def save_profile(self, name: str, device: str, values: dict, batch: dict) -> dict:
        profiles = [p for p in self.data["profiles"] if p["name"] != name]
        profiles.append({"name": name, "device": device, "values": values, "batch": batch})
        profiles.sort(key=lambda p: p["name"].lower())
        return self.update({"profiles": profiles})

    def delete_profile(self, name: str) -> dict:
        return self.update({"profiles": [p for p in self.data["profiles"] if p["name"] != name]})
