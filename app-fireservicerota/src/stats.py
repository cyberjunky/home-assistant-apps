"""Statistics for the Stats tab, from the stored incidents, responses and duty changes."""

import statistics
from collections import Counter
from datetime import datetime, timedelta

from .database import Database
from .timeutil import norm_ts, parse_ts, utc_now

PERIODS = {"7": 7, "30": 30, "90": 90, "365": 365, "all": None}


def _local(dt: datetime) -> datetime:
    return dt.astimezone()


def _bucket(dt: datetime, unit: str) -> str:
    local = _local(dt)
    if unit == "month":
        return local.strftime("%Y-%m")
    if unit == "week":
        year, week, _ = local.isocalendar()
        return f"{year}-W{week:02d}"
    return local.strftime("%Y-%m-%d")


def _buckets(start: datetime, end: datetime, unit: str) -> list[str]:
    """Every bucket between start and end, so empty days show as zero."""
    keys: list[str] = []
    cursor = _local(start)
    while cursor <= _local(end) + timedelta(days=1):
        key = _bucket(cursor, unit)
        if not keys or keys[-1] != key:
            keys.append(key)
        cursor += timedelta(days=1)
    last = _bucket(end, unit)
    return keys[: keys.index(last) + 1] if last in keys else keys


def duty_seconds(rows: list[dict], start: datetime, end: datetime) -> float:
    """Seconds on duty between start and end, from the duty changes (the first may be before start)."""
    total = 0.0
    on_since: datetime | None = None
    for row in rows:
        ts = parse_ts(row["ts"])
        if ts is None:
            continue
        ts = max(ts, start)
        if row["state"] == "on":
            if on_since is None:
                on_since = ts
        elif on_since is not None:
            total += max(0.0, (min(ts, end) - on_since).total_seconds())
            on_since = None
    if on_since is not None:
        total += max(0.0, (end - on_since).total_seconds())
    return total


def duty_periods(rows: list[dict], limit: int = 50) -> list[dict]:
    """The duty periods, newest first, from the on/off changes."""
    periods: list[dict] = []
    current: dict | None = None
    for row in rows:
        if row["state"] == "on" and current is None:
            current = {
                "start": row["ts"],
                "end": None,
                "shift_start": row.get("start_time"),
                "shift_end": row.get("end_time"),
                "assigned_function": row.get("assigned_function"),
            }
        elif row["state"] != "on" and current is not None:
            current["end"] = row["ts"]
            periods.append(current)
            current = None
    if current is not None:
        periods.append(current)
    return list(reversed(periods))[:limit]


def compute(db: Database, period: str = "30") -> dict:
    days = PERIODS.get(period, 30)
    end = utc_now()
    incidents_all = db.all_incidents()
    if days is None:
        first = parse_ts(incidents_all[0]["first_seen"]) if incidents_all else end - timedelta(days=30)
        start = min(first, end - timedelta(days=1))
    else:
        start = end - timedelta(days=days)
    incidents = [i for i in incidents_all if (parse_ts(i["first_seen"]) or end) >= start]
    span_days = (end - start).days or 1
    unit = "day" if span_days <= 92 else ("week" if span_days <= 400 else "month")

    per_bucket = Counter()
    hours = Counter()
    weekdays = Counter()
    by_type = Counter()
    by_prio = Counter()
    outcomes = Counter()
    answer_minutes: list[float] = []
    ack_minutes: list[float] = []
    for i in incidents:
        when = parse_ts(i.get("created_at")) or parse_ts(i["first_seen"])
        per_bucket[_bucket(when, unit)] += 1
        hours[_local(when).hour] += 1
        weekdays[_local(when).weekday()] += 1
        by_type[i.get("type") or "Unknown"] += 1
        by_prio[(i.get("prio") or "—").upper()] += 1
        outcome = i["response"]
        outcomes[outcome["status"]] += 1
        answered = parse_ts(outcome.get("responded_at"))
        if answered and outcome["status"] != "none":
            minutes = max(0.0, (answered - when).total_seconds() / 60)
            answer_minutes.append(minutes)
            if outcome["status"] == "acknowledged":
                ack_minutes.append(minutes)

    duty = db.duty_rows(norm_ts(start.isoformat()))
    on_duty_s = duty_seconds(duty, start, end)
    total = len(incidents)
    return {
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "unit": unit,
        "total": total,
        "series": [{"key": k, "count": per_bucket.get(k, 0)} for k in _buckets(start, end, unit)],
        "hours": [hours.get(h, 0) for h in range(24)],
        "weekdays": [weekdays.get(d, 0) for d in range(7)],
        "by_type": dict(by_type.most_common()),
        "by_prio": dict(by_prio.most_common()),
        "responses": {
            "acknowledged": outcomes.get("acknowledged", 0),
            "rejected": outcomes.get("rejected", 0),
            "none": outcomes.get("none", 0),
        },
        "median_answer_minutes": round(statistics.median(answer_minutes), 1) if answer_minutes else None,
        "median_ack_minutes": round(statistics.median(ack_minutes), 1) if ack_minutes else None,
        "duty_hours": round(on_duty_s / 3600, 1),
        "duty_share": round(on_duty_s / max(1.0, (end - start).total_seconds()) * 100, 1),
        "duty_periods": duty_periods(duty, limit=20),
    }


def timeline(db: Database, hours: int = 48) -> list[dict]:
    """The incident sensor over the last hours, for the Wallboard's history strip: one bar per incident update."""
    since = norm_ts((utc_now() - timedelta(hours=hours)).isoformat())
    rows = db.conn.execute(
        "SELECT u.ts, u.incident_id, u.trigger, i.prio, i.type, u.body FROM incident_updates u "
        "LEFT JOIN incidents i ON i.id = u.incident_id WHERE u.ts >= ? ORDER BY u.ts",
        (since,),
    ).fetchall()
    return [dict(r) for r in rows]
