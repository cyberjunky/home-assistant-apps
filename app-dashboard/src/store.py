"""The dashboard itself: the pages and what is on them, kept in one JSON file."""

import json
import logging
import os
import re
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

COLS, ROWS = 24, 14
MAX_PAGES, MAX_WIDGETS = 24, 80
# The look: fonts for text and labels, the weight of large numbers, both backgrounds and the accent
STYLE_KEYS = ("text", "label", "weight", "paper", "night", "tint")
PHASES = ("morning", "day", "evening", "night")
CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_OPTS_BYTES = 4096


class Conflict(Exception):
    """Someone else saved the dashboard in the meantime."""


def new_id() -> str:
    return secrets.token_hex(4)


def _widget(block: str, x: int, y: int, w: int, h: int, entities=(), label: str = "", **opts) -> dict:
    return {
        "id": new_id(),
        "type": block,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "label": label,
        "entities": list(entities),
        "opts": opts,
    }


def starter(states: dict[str, dict], areas: dict[str, str]) -> dict:
    """A first dashboard from what Home Assistant has, so the first screen isn't empty."""

    def of(*domains: str, device_class: str | None = None, unit: str | None = None) -> list[str]:
        found = []
        for entity_id, state in sorted(states.items()):
            if entity_id.split(".", 1)[0] not in domains or state["s"] in ("unavailable", "unknown"):
                continue
            attributes = state["a"]
            if device_class and attributes.get("device_class") != device_class:
                continue
            if unit and attributes.get("unit_of_measurement") != unit:
                continue
            found.append(entity_id)
        return found

    temperatures = of("sensor", device_class="temperature")
    power = of("sensor", device_class="power")
    energy = of("sensor", device_class="energy")
    now = [_widget("clock", 0, 0, 10, 5)]
    weather = of("weather")
    if weather:
        now.append(_widget("weather", 0, 5, 10, 2, weather[:1], rule=False))
    actions = of("scene", "script")[:3]
    if actions:
        now.append(_widget("actions", 18, 0, 6, 7, actions, "Scenes"))
    climate, calendars, cameras = of("climate"), of("calendar"), of("camera")
    if calendars:
        now.append(_widget("agenda", 10, 0, 8, 7, calendars[:4], days=2))
    else:
        if climate:
            now.append(_widget("climate", 10, 0, 8, 3, climate[:1]))
        if temperatures:
            now.append(_widget("chart", 10, 3, 8, 4, temperatures[:1], hours=24))
    media = of("media_player")
    if media:
        now.append(_widget("media", 0, 7, 9, 4, media[:1]))
    lights = of("light")[:4] or of("switch")[:4]
    if lights:
        now.append(_widget("tiles", 9, 7, 11, 4, lights, "Lights"))
    covers = of("cover")
    if covers:
        now.append(_widget("covers", 20, 7, 4, 4, covers[:2], "Blinds", compact=True))
    if power:
        now.append(_widget("value", 0, 11, 5, 3, power[:1]))
    batteries = of("sensor", device_class="battery")[:3]
    if batteries:
        now.append(_widget("bars", 5, 11, 7, 3, batteries, "Batteries"))
    if len(power) > 1:
        now.append(_widget("split", 12, 11, 12, 3, power[1:4], "Where from"))
    pages = [{"id": new_id(), "name": "Now", "widgets": now}]

    # With the agenda on the first page, the thermostat and its chart get a page with the cameras
    if calendars or cameras:
        home = []
        if climate:
            home.append(_widget("climate", 0, 0, 8, 4, climate[:1]))
        if temperatures:
            home.append(_widget("chart", 0, 4, 8, 5, temperatures[:2], hours=24))
        if weather:
            home.append(_widget("weather", 0, 9, 12, 5, weather[:1], forecast="daily"))
        if cameras:
            home.append(_widget("camera", 9, 0, 15, 9, cameras[:4]))
        doors = of("binary_sensor", device_class="door") + of("binary_sensor", device_class="occupancy")
        if doors or temperatures:
            home.append(_widget("list", 13, 9, 11, 5, (doors + temperatures)[:5], "Around the house"))
        pages.append({"id": new_id(), "name": "Home", "widgets": home})

    if energy or power:
        widgets = []
        if energy:
            widgets.append(_widget("chart", 0, 0, 16, 9, energy[:1], hours=168, kind="bars"))
        for i, entity_id in enumerate(power[:3]):
            widgets.append(_widget("value", 17, i * 3, 7, 3, [entity_id]))
        if power:
            widgets.append(_widget("chart", 0, 9, 16, 5, power[:1], hours=24))
        pages.append({"id": new_id(), "name": "Energy", "widgets": widgets})

    # Short labels on the top bar: "Bedroom 18.8 °C" says enough
    chips = [{"entity": e, "label": areas.get(e, "")} for e in temperatures[:2]]
    doors = of("binary_sensor", device_class="door") or of("lock")
    chips += [{"entity": e, "label": ""} for e in doors[:1]]
    # A doorbell and a camera that look like a pair: someone at the door shows the camera
    bells = [e for e in of("binary_sensor", "event") if "doorbell" in e or "deurbel" in e]
    bell = {"trigger": bells[0], "camera": cameras[0]} if bells and cameras else {}
    tablet = _tablet({"doorbell": bell})
    return {"rev": 0, "cols": COLS, "rows": ROWS, "style": {}, "tablet": tablet, "chips": chips, "pages": pages}


def _opts(opts) -> dict:
    """A block's own settings: whatever the designer keeps there, as long as it stays small."""
    if not isinstance(opts, dict) or len(json.dumps(opts)) > MAX_OPTS_BYTES:
        return {}
    return opts


def _tablet(tablet) -> dict:
    """What the tablet does by itself: the parts of the day, the night, going back, the doorbell."""
    if not isinstance(tablet, dict):
        tablet = {}

    def group(name: str) -> dict:
        return tablet[name] if isinstance(tablet.get(name), dict) else {}

    def number(value, low: int, high: int) -> int:
        try:
            return min(high, max(low, int(value)))
        except (TypeError, ValueError):
            return low

    phases = {k: v for k, v in group("phases").items() if k in PHASES and isinstance(v, str) and CLOCK.match(v)}
    night, idle, bell = group("night"), group("idle"), group("doorbell")
    return {
        "phases": phases,
        "night": {
            "dark": night.get("dark") if night.get("dark") in ("night", "sun") else "",
            "dim": number(night.get("dim"), 0, 90),
            "page": str(night.get("page") or "")[:16],
        },
        "idle": {"minutes": number(idle.get("minutes"), 0, 240), "page": str(idle.get("page") or "")[:16]},
        "doorbell": {
            "trigger": str(bell.get("trigger") or "")[:255],
            "camera": str(bell.get("camera") or "")[:255],
            "seconds": number(bell.get("seconds") or 60, 5, 600),
        },
    }


def clean(doc: dict) -> dict:
    """What a browser sent, reduced to what a dashboard is. Raises ValueError when it isn't one."""
    if not isinstance(doc, dict) or not isinstance(doc.get("pages"), list):
        raise ValueError("Not a dashboard")
    cols = min(48, max(6, int(doc.get("cols") or COLS)))
    rows = min(48, max(4, int(doc.get("rows") or ROWS)))
    pages = []
    for page in doc["pages"][:MAX_PAGES]:
        widgets = []
        for w in (page.get("widgets") or [])[:MAX_WIDGETS]:
            width = min(cols, max(1, int(w.get("w") or 1)))
            height = min(rows, max(1, int(w.get("h") or 1)))
            widgets.append(
                {
                    "id": str(w.get("id") or new_id())[:16],
                    "type": str(w.get("type") or "value")[:24],
                    "x": min(cols - width, max(0, int(w.get("x") or 0))),
                    "y": min(rows - height, max(0, int(w.get("y") or 0))),
                    "w": width,
                    "h": height,
                    "label": str(w.get("label") or "")[:80],
                    "entities": [str(e)[:255] for e in (w.get("entities") or [])[:24] if isinstance(e, str)],
                    "opts": _opts(w.get("opts")),
                }
            )
        pages.append(
            {
                "id": str(page.get("id") or new_id())[:16],
                "name": str(page.get("name") or "Page")[:40],
                "widgets": widgets,
            }
        )
    if not pages:
        pages = [{"id": new_id(), "name": "Now", "widgets": []}]
    chips = [
        {"entity": str(c["entity"])[:255], "label": str(c.get("label") or "")[:40]}
        for c in (doc.get("chips") or [])[:8]
        if isinstance(c, dict) and isinstance(c.get("entity"), str)
    ]
    style = {
        key: str(value)[:40]
        for key, value in (doc.get("style") if isinstance(doc.get("style"), dict) else {}).items()
        if key in STYLE_KEYS and value
    }
    tablet = _tablet(doc.get("tablet"))
    return {"cols": cols, "rows": rows, "style": style, "tablet": tablet, "chips": chips, "pages": pages}


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.doc: dict | None = None
        try:
            self.doc = json.loads(path.read_text())
        except FileNotFoundError:
            pass
        except (OSError, json.JSONDecodeError) as e:
            # Keep the unreadable file for the user, and start again
            logger.error(f"Cannot read {path}: {e} - kept as {path.name}.broken")
            path.replace(path.with_suffix(".broken"))

    def ensure(self, states: dict[str, dict], areas: dict[str, str]) -> dict:
        """The dashboard, made from Home Assistant's entities the first time."""
        if self.doc is None:
            self.doc = starter(states, areas)
            self._write()
        return self.doc

    def save(self, doc: dict, rev: int) -> dict:
        if self.doc is not None and rev != self.doc.get("rev", 0):
            raise Conflict
        self.doc = {**clean(doc), "rev": rev + 1}
        self._write()
        return self.doc

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.doc, indent=1))
        os.replace(tmp, self.path)
