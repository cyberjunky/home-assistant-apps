"""Dates on items: a due date (by when) and an on date (when it happens).

Either is a day ("2026-12-01") or a day with a time ("2026-12-01T19:30"), in Home Assistant's time
zone, written as text so the database, the page and the calendar all read it the same way.
"""

import datetime as dt
import re

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$")
KINDS = ("due", "happens_on")
# How long a calendar event with a time lasts
DURATION = {"due": dt.timedelta(minutes=30), "happens_on": dt.timedelta(hours=1)}


def clean(value: object) -> str:
    """A date as stored: 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM', or '' for none. Raises ValueError."""
    text = str(value or "").strip()
    if not text:
        return ""
    if DAY_RE.match(text):
        dt.date.fromisoformat(text)
        return text
    if TIME_RE.match(text):
        moment = dt.datetime.fromisoformat(text.replace(" ", "T"))
        return moment.strftime("%Y-%m-%dT%H:%M")
    raise ValueError(f"not a date: {text!r} (use YYYY-MM-DD or YYYY-MM-DDTHH:MM)")


def parse(value: str) -> dt.date | dt.datetime | None:
    if not value:
        return None
    return dt.date.fromisoformat(value) if DAY_RE.match(value) else dt.datetime.fromisoformat(value)


def day_of(value: str) -> dt.date | None:
    moment = parse(value)
    return moment.date() if isinstance(moment, dt.datetime) else moment


def event_span(value: str, kind: str) -> tuple[str, str]:
    """Start and end of the calendar event for a date: a whole day, or a short slot at the time."""
    moment = parse(value)
    if isinstance(moment, dt.datetime):
        return moment.isoformat(), (moment + DURATION[kind]).isoformat()
    assert moment is not None
    return moment.isoformat(), (moment + dt.timedelta(days=1)).isoformat()


def plausible(value: str, today: dt.date) -> bool:
    """A date an AI found is only used when it is near enough to mean something: not past, within 3 years."""
    day = day_of(value)
    return day is not None and today <= day <= today + dt.timedelta(days=3 * 365)
