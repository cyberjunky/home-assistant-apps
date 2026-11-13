"""Phones and other GPS trackers: where they were, and what that says about their days.

From the recorder's history with attributes (latitude, longitude, accuracy, battery): the track,
the time in each zone per day, the trips away from home, the usual times of leaving and coming
home, the distance, and, for a phone tied to its Wi-Fi tracker, when the two disagree.
"""

import math
from datetime import UTC, datetime

DAY = 86400
# Points less accurate than this are left out of the distance
MAX_ACCURACY_M = 150
MAX_POINTS = 4000


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two points."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_history(items: list[dict]) -> list[dict]:
    """The recorder's rows of one tracker as points: ts, state, and the position when it has one."""
    out = []
    for item in items:
        ts = _ts(item.get("last_updated") or item.get("last_changed"))
        if ts is None:
            continue
        attrs = item.get("attributes") or {}
        lat, lon = attrs.get("latitude"), attrs.get("longitude")
        point = {"ts": ts, "state": item.get("state", "")}
        if isinstance(lat, int | float) and isinstance(lon, int | float):
            point.update(lat=float(lat), lon=float(lon), acc=attrs.get("gps_accuracy"))
        if isinstance(attrs.get("battery_level"), int | float):
            point["battery"] = attrs["battery_level"]
        out.append(point)
    out.sort(key=lambda p: p["ts"])
    return out


def segments(points: list[dict], since: float, until: float) -> list[dict]:
    """The states as stretches of time: {from, to, state}."""
    out: list[dict] = []
    for p in points:
        if p["state"] in ("unavailable", "unknown", ""):
            state = None
        else:
            state = p["state"]
        at = max(p["ts"], since)
        if out and out[-1]["state"] == state:
            continue
        if out:
            out[-1]["to"] = at
        out.append({"from": at, "to": until, "state": state})
    return [s for s in out if s["state"] is not None and s["to"] > s["from"]]


def daily(segs: list[dict], since: float, until: float, tz_offset: float) -> list[dict]:
    """Seconds per state for each local day."""
    days: dict[str, dict[str, float]] = {}
    for s in segs:
        t = s["from"]
        while t < s["to"]:
            local = t + tz_offset
            day_end = (local // DAY + 1) * DAY - tz_offset
            step = min(s["to"], day_end) - t
            key = datetime.fromtimestamp(local, UTC).strftime("%Y-%m-%d")
            days.setdefault(key, {})
            days[key][s["state"]] = days[key].get(s["state"], 0) + step
            t += step
    return [{"day": k, "states": v} for k, v in sorted(days.items())]


def trips(segs: list[dict], tz_offset: float) -> list[dict]:
    """Each time away from home: left, back, how long, and the zones on the way."""
    out = []
    current = None
    for s in segs:
        if s["state"] != "home":
            if current is None:
                current = {"left": s["from"], "back": None, "zones": []}
            if s["state"] != "not_home" and s["state"] not in current["zones"]:
                current["zones"].append(s["state"])
        elif current is not None:
            current["back"] = s["from"]
            out.append(current)
            current = None
    if current is not None:
        out.append(current)
    for t in out:
        t["seconds"] = (t["back"] - t["left"]) if t["back"] else None
        t["weekday"] = datetime.fromtimestamp(t["left"] + tz_offset, UTC).weekday()
    return out


def _clock(ts: float, tz_offset: float) -> float:
    local = datetime.fromtimestamp(ts + tz_offset, UTC)
    return local.hour + local.minute / 60


def habits(trip_list: list[dict], tz_offset: float) -> dict:
    """The usual (median) time of the first departure and the last return, weekdays and weekends."""

    def median(values: list[float]) -> float | None:
        values = sorted(values)
        return values[len(values) // 2] if values else None

    first_out: dict[str, dict[str, float]] = {"weekdays": {}, "weekend": {}}
    last_in: dict[str, dict[str, float]] = {"weekdays": {}, "weekend": {}}
    for t in trip_list:
        part = "weekend" if t["weekday"] >= 5 else "weekdays"
        day = datetime.fromtimestamp(t["left"] + tz_offset, UTC).strftime("%Y-%m-%d")
        hour = _clock(t["left"], tz_offset)
        first_out[part][day] = min(first_out[part].get(day, 24.0), hour)
        if t["back"]:
            back_day = datetime.fromtimestamp(t["back"] + tz_offset, UTC).strftime("%Y-%m-%d")
            last_in[part][back_day] = max(last_in[part].get(back_day, 0.0), _clock(t["back"], tz_offset))
    return {
        part: {
            "leaves": median(list(first_out[part].values())),
            "returns": median(list(last_in[part].values())),
            "days": len(first_out[part]),
        }
        for part in ("weekdays", "weekend")
    }


def distance(points: list[dict]) -> float:
    total = 0.0
    last = None
    for p in points:
        if "lat" not in p or (p.get("acc") or 0) > MAX_ACCURACY_M:
            continue
        if last is not None:
            step = haversine(last["lat"], last["lon"], p["lat"], p["lon"])
            # A jump of more than 300 km between two points is a bad fix, not a trip
            if step < 300000:
                total += step
        last = p
    return total


def thin(points: list[dict], limit: int = MAX_POINTS) -> list[dict]:
    """Fewer points for the map: the same position twice in a row once, and at most `limit`."""
    out = []
    for p in points:
        if "lat" not in p:
            continue
        if out and abs(out[-1]["lat"] - p["lat"]) < 1e-5 and abs(out[-1]["lon"] - p["lon"]) < 1e-5:
            continue
        out.append({k: p[k] for k in ("ts", "lat", "lon", "acc", "state", "battery") if k in p})
    if len(out) > limit:
        step = len(out) / limit
        out = [out[int(i * step)] for i in range(limit)] + [out[-1]]
    return out


def mismatch(
    gps_home: list[tuple[float, float]], wifi_up: list[tuple[float, float]], since: float, until: float
) -> dict:
    """How long the phone was home by GPS but not on the network, and on the network while GPS said away."""

    def overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
        total, i, j = 0.0, 0, 0
        while i < len(a) and j < len(b):
            lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
            if hi > lo:
                total += hi - lo
            if a[i][1] < b[j][1]:
                i += 1
            else:
                j += 1
        return total

    home = sum(b - a for a, b in gps_home)
    up = sum(b - a for a, b in wifi_up)
    both = overlap(gps_home, wifi_up)
    return {
        "home_seconds": home,
        "wifi_seconds": up,
        "home_not_on_wifi": max(0.0, home - both),
        "on_wifi_not_home": max(0.0, up - both),
        "agreement": both / home if home else None,
    }


def heat(points: list[dict], until: float) -> list[list[float]]:
    """Where the tracker spent its time: [lat, lon, hours], on a grid of about 50 m."""
    cells: dict[tuple[float, float], float] = {}
    fixes = [p for p in points if "lat" in p and (p.get("acc") or 0) <= MAX_ACCURACY_M * 2]
    for i, p in enumerate(fixes):
        end = fixes[i + 1]["ts"] if i + 1 < len(fixes) else until
        # A long gap without a fix still counts, up to half a day
        hours = max(0.02, min(end - p["ts"], DAY / 2)) / 3600
        key = (round(p["lat"], 4), round(p["lon"], 4))
        cells[key] = cells.get(key, 0) + hours
    return [[lat, lon, round(h, 3)] for (lat, lon), h in cells.items()]


def summary(points: list[dict], since: float, until: float, home: tuple[float, float] | None, tz_offset: float) -> dict:
    segs = segments(points, since, until)
    trip_list = trips(segs, tz_offset)
    positions = [p for p in points if "lat" in p and (p.get("acc") or 0) <= MAX_ACCURACY_M * 4]
    furthest = None
    if home and positions:
        far = max(positions, key=lambda p: haversine(home[0], home[1], p["lat"], p["lon"]))
        furthest = {
            "metres": haversine(home[0], home[1], far["lat"], far["lon"]),
            "ts": far["ts"],
            "lat": far["lat"],
            "lon": far["lon"],
        }
    known = sum(s["to"] - s["from"] for s in segs)
    at_home = sum(s["to"] - s["from"] for s in segs if s["state"] == "home")
    batteries = [(p["ts"], p["battery"]) for p in points if "battery" in p]
    return {
        "since": since,
        "until": until,
        "segments": segs,
        "days": daily(segs, since, until, tz_offset),
        "trips": trip_list[-100:],
        "trip_count": len(trip_list),
        "habits": habits(trip_list, tz_offset),
        "home_share": at_home / known if known else None,
        "distance": distance(points),
        "furthest": furthest,
        "battery": batteries[-500:],
        "track": thin(points),
        "heat": heat(points, until),
    }
