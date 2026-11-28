"""Dates in Home Assistant: a calendar with every open item that has a date, and a sensor with the
next one.

The calendar is a Local Calendar in Home Assistant (named MyBrain, so calendar.mybrain). The app
keeps it in step: it reads the calendar's events, finds its own by the marker at the end of their
description, and adds, changes or removes them until they match the items. Events without the
marker are someone else's and are never touched.
"""

import asyncio
import datetime as dt
import logging
import re
import time

from . import dates
from .config import Config
from .ha import HA, HAError
from .store import Store

logger = logging.getLogger(__name__)

DEFAULT_CALENDAR = "calendar.mybrain"
NEXT_SENSOR = "sensor.mybrain_next"
MARKER_RE = re.compile(r"mybrain:(\d+):(due|happens_on)\s*$")


def wanted_events(items: list[dict]) -> dict[str, dict]:
    """The events the calendar should have: marker → event fields."""
    events = {}
    for item in items:
        title = item.get("title") or item.get("url") or "Untitled"
        for kind in dates.KINDS:
            value = item.get(kind) or ""
            if not value:
                continue
            start, end = dates.event_span(value, kind)
            lines = [item["url"]] if item.get("url") else []
            text = item.get("note") or item.get("summary") or ""
            if text:
                lines.append(text[:1000])
            marker = f"mybrain:{item['id']}:{kind}"
            lines.append(marker)
            events[marker] = {
                "summary": f"⏰ {title}" if kind == "due" else title,
                "dtstart": start,
                "dtend": end,
                "description": "\n\n".join(lines),
            }
    return events


def _when(field: dict) -> str:
    """An event's start or end from the REST API, written like ours."""
    if not isinstance(field, dict):
        return ""
    if field.get("date"):
        return str(field["date"])
    moment = dt.datetime.fromisoformat(str(field.get("dateTime")))
    return moment.replace(tzinfo=None).isoformat()


def plan(wanted: dict[str, dict], existing: list[dict], entity_id: str) -> list[dict]:
    """The websocket commands that make the calendar match: create, update, delete."""
    calls: list[dict] = []
    seen: set[str] = set()
    for event in existing:
        match = MARKER_RE.search(str(event.get("description") or ""))
        if not match or not event.get("uid"):
            continue  # not ours
        marker = f"mybrain:{match.group(1)}:{match.group(2)}"
        target = wanted.get(marker)
        if target is None or marker in seen:
            calls.append({"type": "calendar/event/delete", "entity_id": entity_id, "uid": event["uid"]})
            continue
        seen.add(marker)
        now = {
            "summary": event.get("summary") or "",
            "dtstart": _when(event.get("start")),
            "dtend": _when(event.get("end")),
            "description": str(event.get("description") or "").strip(),
        }
        if now != target:
            calls.append(
                {"type": "calendar/event/update", "entity_id": entity_id, "uid": event["uid"], "event": target}
            )
    for marker, target in wanted.items():
        if marker not in seen:
            calls.append({"type": "calendar/event/create", "entity_id": entity_id, "event": target})
    return calls


def next_up(items: list[dict], today: dt.date) -> dict:
    """The sensor: the first date still to come (or overdue), and the few after it."""
    entries = []
    for item in items:
        for kind in dates.KINDS:
            value = item.get(kind) or ""
            if value:
                entries.append((value, kind, item))
    entries.sort(key=lambda e: e[0])
    overdue = [e for e in entries if e[1] == "due" and dates.day_of(e[0]) < today]
    coming = [e for e in entries if dates.day_of(e[0]) >= today]
    first = overdue[0] if overdue else coming[0] if coming else None
    upcoming = [
        {"title": i.get("title") or i.get("url"), "when": v, "kind": k, "item_id": i["id"]} for v, k, i in coming[:5]
    ]
    if not first:
        return {"state": "nothing planned", "attributes": {"overdue": 0, "upcoming": []}}
    value, kind, item = first
    return {
        "state": (item.get("title") or item.get("url") or "Untitled")[:250],
        "attributes": {
            "when": value,
            "kind": kind,
            "days": (dates.day_of(value) - today).days,
            "item_id": item["id"],
            "url": item.get("url") or "",
            "category": item.get("category"),
            "overdue": len(overdue),
            "upcoming": upcoming,
        },
    }


class Agenda:
    """Keeps the calendar and the next-item sensor in step with the items."""

    def __init__(self, config: Config, store: Store, ha: HA):
        self.config = config
        self.store = store
        self.ha = ha
        self.state = {"entity": "", "found": False, "synced_at": 0.0, "events": 0, "error": "", "changes": 0}

    async def sync(self) -> None:
        items = await asyncio.to_thread(self.store.dated_open)
        today = dt.date.today()
        nxt = next_up(items, today)
        try:
            await self.ha.set_state(
                NEXT_SENSOR,
                nxt["state"],
                {"friendly_name": "MyBrain next", "icon": "mdi:calendar-star", **nxt["attributes"]},
            )
        except HAError as e:
            logger.debug(f"Sensor: {e}")
        if self.config.calendar_sync:
            await self._calendar(items)

    async def _calendar(self, items: list[dict]) -> None:
        entity = self.config.calendar_entity or DEFAULT_CALENDAR
        self.state["entity"] = entity
        if not await self.ha.entity_exists(entity):
            self.state.update(found=False, error="")
            return
        self.state["found"] = True
        start = (dt.datetime.now() - dt.timedelta(days=800)).isoformat(timespec="seconds")
        end = (dt.datetime.now() + dt.timedelta(days=4 * 365)).isoformat(timespec="seconds")
        try:
            existing = await self.ha.calendar_events(entity, start, end)
            wanted = wanted_events(items)
            calls = plan(wanted, existing, entity)
            errors = await self.ha.commands(calls)
        except HAError as e:
            self.state["error"] = str(e)
            logger.warning(f"Calendar {entity}: {e}")
            return
        if calls:
            logger.info(f"Calendar {entity}: {len(calls) - len(errors)} of {len(calls)} changes made")
        for error in errors[:3]:
            logger.warning(f"Calendar {entity}: {error}")
        self.state.update(
            synced_at=time.time(),
            events=len(wanted),
            error=errors[0] if errors else "",
            changes=self.state["changes"] + len(calls) - len(errors),
        )
