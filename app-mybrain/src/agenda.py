"""MyBrain in Home Assistant: per user a sensor with the inbox and one with the next item that has a
date, a calendar per user with their own items that have a date, and a household calendar with
everyone's public ones.

The calendars are Local Calendars in Home Assistant: "MyBrain" (calendar.mybrain) for the household
and "MyBrain <name>" (calendar.mybrain_<name>) per person, each only when it exists. The app keeps
them in step: it reads a calendar's events, finds its own by the marker at the end of their
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
INBOX_SENSOR = "sensor.mybrain_inbox"


def slug(name: str) -> str:
    """A name as Home Assistant writes it in an entity id: 'Anne-Marie' → 'anne_marie'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "user"


MARKER_RE = re.compile(r"mybrain:(\d+):(due|happens_on)\s*$")


def wanted_events(items: list[dict], names: dict[str, str] | None = None) -> dict[str, dict]:
    """The events a calendar should have: marker → event fields. With names, whose it is follows the title."""
    events = {}
    for item in items:
        title = item.get("title") or item.get("url") or "Untitled"
        if names and names.get(item.get("owner") or ""):
            title = f"{title} ({names[item['owner']]})"
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
    """Keeps every user's sensors and calendars in step with the items."""

    def __init__(self, config: Config, store: Store, ha: HA, users: dict[str, str]):
        self.config = config
        self.store = store
        self.ha = ha
        self.users = users
        # entity id → {found, events, synced_at, error}
        self.calendars: dict[str, dict] = {}
        # Everyone seen since the start, so someone who deletes their last item gets their sensors zeroed
        self.seen: set[str] = set()

    def suffix(self, owner: str) -> str:
        """What follows mybrain_inbox, mybrain_next and calendar.mybrain for a user ('' for nobody's)."""
        if not owner:
            return ""
        return "_" + slug(self.users.get(owner) or owner[:8])

    def shared_calendar(self) -> str:
        return self.config.calendar_entity or DEFAULT_CALENDAR

    def calendar_of(self, owner: str) -> str:
        return f"{DEFAULT_CALENDAR}{self.suffix(owner)}" if owner else ""

    async def sync(self) -> None:
        self.seen.update(await asyncio.to_thread(self.store.owners) or [""])
        owners = sorted(self.seen)
        today = dt.date.today()
        for owner in owners:
            counts = await asyncio.to_thread(self.store.counts, owner)
            items = await asyncio.to_thread(self.store.dated_open, owner)
            name = self.users.get(owner, "")
            label = f" {name}" if name else ""
            nxt = next_up(items, today)
            try:
                await self.ha.set_state(
                    f"{INBOX_SENSOR}{self.suffix(owner)}",
                    counts["by_status"]["inbox"],
                    {
                        "friendly_name": f"MyBrain inbox{label}",
                        "icon": "mdi:brain",
                        "unit_of_measurement": "items",
                        "state_class": "measurement",
                        **{s: counts["by_status"][s] for s in ("later", "done", "archived")},
                        "total": counts["total"],
                        "waiting": counts["waiting"],
                    },
                )
                await self.ha.set_state(
                    f"{NEXT_SENSOR}{self.suffix(owner)}",
                    nxt["state"],
                    {"friendly_name": f"MyBrain next{label}", "icon": "mdi:calendar-star", **nxt["attributes"]},
                )
            except HAError as e:
                logger.debug(f"Sensors: {e}")
            if self.config.calendar_sync and owner:
                await self._calendar(self.calendar_of(owner), items)
        if self.config.calendar_sync:
            shared = await asyncio.to_thread(self.store.dated_open, None, True)
            several = len([o for o in owners if o]) > 1
            await self._calendar(self.shared_calendar(), shared, self.users if several else None)

    async def _calendar(self, entity: str, items: list[dict], names: dict[str, str] | None = None) -> None:
        state = self.calendars.setdefault(entity, {"found": False, "events": 0, "synced_at": 0.0, "error": ""})
        if not await self.ha.entity_exists(entity):
            state.update(found=False, error="")
            return
        state["found"] = True
        start = (dt.datetime.now() - dt.timedelta(days=800)).isoformat(timespec="seconds")
        end = (dt.datetime.now() + dt.timedelta(days=4 * 365)).isoformat(timespec="seconds")
        try:
            existing = await self.ha.calendar_events(entity, start, end)
            wanted = wanted_events(items, names)
            calls = plan(wanted, existing, entity)
            errors = await self.ha.commands(calls)
        except HAError as e:
            state["error"] = str(e)
            logger.warning(f"Calendar {entity}: {e}")
            return
        if calls:
            logger.info(f"Calendar {entity}: {len(calls) - len(errors)} of {len(calls)} changes made")
        for error in errors[:3]:
            logger.warning(f"Calendar {entity}: {error}")
        state.update(synced_at=time.time(), events=len(wanted), error=errors[0] if errors else "")

    def for_user(self, owner: str) -> dict:
        """What the Info page shows a user: their entities and both calendars."""
        mine = self.calendar_of(owner)
        shared = self.shared_calendar()
        return {
            "inbox_sensor": f"{INBOX_SENSOR}{self.suffix(owner)}",
            "next_sensor": f"{NEXT_SENSOR}{self.suffix(owner)}",
            "mine": {"entity": mine, **self.calendars.get(mine, {})} if mine else None,
            "shared": {"entity": shared, **self.calendars.get(shared, {})},
            "name_hint": f"MyBrain {self.users.get(owner, '')}".strip(),
        }
