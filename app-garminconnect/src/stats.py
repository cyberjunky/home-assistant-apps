"""Statistics over the stored data, for the dashboard.

Everything works on days ('YYYY-MM-DD', both ends included) and returns plain JSON-ready structures.
Most of this is what the Garmin site doesn't show: streaks, what moves your sleep, how regular your
nights are, days your body looked off, Eddington numbers and how far all those steps took you.
"""

import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from .database import Database, unpack
from .sports import FAMILIES, family

# Metrics that add up over a week or month; everything else is averaged
SUM_METRICS = {
    "steps",
    "distance_m",
    "floors_up",
    "floors_down",
    "kcal_total",
    "kcal_active",
    "kcal_resting",
    "im_moderate",
    "im_vigorous",
    "stress_rest_s",
    "stress_low_s",
    "stress_medium_s",
    "stress_high_s",
    "stress_activity_s",
    "hydration_ml",
    "sweat_ml",
    "food_kcal",
    "food_protein_g",
    "food_carbs_g",
    "food_fat_g",
    "hr_worn_min",
}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

EARTH_KM = 40075.0
MOON_KM = 384400.0
MARATHON_KM = 42.195
EVEREST_M = 8849.0
EIFFEL_M = 330.0
FLOOR_M = 3.0
PIZZA_KCAL = 2000.0
FAT_KCAL_PER_KG = 7700.0
BEER_KCAL = 150.0


def days_between(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def bucket_of(day: str, bucket: str) -> str:
    d = date.fromisoformat(day)
    if bucket == "week":
        return (d - timedelta(days=d.weekday())).isoformat()
    if bucket == "month":
        return d.replace(day=1).isoformat()
    if bucket == "year":
        return d.replace(month=1, day=1).isoformat()
    return day


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def series(db: Database, metrics: list[str], start: str, end: str, bucket: str = "day") -> dict:
    """Each metric per day, week, month or year: summed or averaged, with every bucket present."""
    raw = db.series(metrics, start, end)
    buckets = sorted({bucket_of(day, bucket) for day in days_between(start, end)})
    result: dict[str, list] = {}
    means: dict[str, list] = {}
    for metric in metrics:
        grouped: dict[str, list[float]] = defaultdict(list)
        for day, value in raw[metric].items():
            grouped[bucket_of(day, bucket)].append(value)
        add = metric in SUM_METRICS and bucket != "day"
        means[metric] = [None if not grouped.get(b) else _r(_mean(grouped[b]), 2) for b in buckets]
        result[metric] = (
            [None if not grouped.get(b) else _r(sum(grouped[b]), 2) for b in buckets] if add else means[metric]
        )
    # "means": the average day of each week or month (only the days with data count), for totals
    # such as steps, where a part-week at the edge of the period would otherwise look like a bad week
    return {"buckets": buckets, "bucket": bucket, "series": result, "means": means}


def metric_catalog(db: Database) -> list[dict]:
    """Every metric with data, for the Explore chart."""
    return db.query(
        "SELECT metric, COUNT(*) AS days, MIN(day) AS first, MAX(day) AS last FROM daily GROUP BY metric ORDER BY metric"
    )


# ----------------------------------------------------------------------
# One day
# ----------------------------------------------------------------------
DAY_METRICS = (
    "steps",
    "steps_goal",
    "distance_m",
    "floors_up",
    "floors_goal",
    "kcal_total",
    "kcal_active",
    "hr_resting",
    "hr_min",
    "hr_max",
    "stress_avg",
    "bb_min",
    "bb_max",
    "bb_charged",
    "bb_drained",
    "im_moderate",
    "im_vigorous",
    "im_goal",
    "sleep_score",
    "sleep_s",
    "sleep_deep_s",
    "sleep_light_s",
    "sleep_rem_s",
    "sleep_awake_s",
    "sleep_need_min",
    "bedtime_h",
    "waketime_h",
    "hrv_night",
    "hrv_low",
    "hrv_high",
    "hrv_status",
    "readiness",
    "vo2max",
    "weight_kg",
    "body_fat_pct",
    "resp_avg",
    "spo2_avg",
    "fitness_age",
    "training_status",
    "load_acute",
    "hydration_ml",
)


def day_view(db: Database, day: str) -> dict:
    """A day's numbers, how they compare with the 30 days before, and its curves and activities."""
    before = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    month = db.series(list(DAY_METRICS), before, yesterday)
    today = db.series(list(DAY_METRICS), day, day)
    values, typical = {}, {}
    for metric in DAY_METRICS:
        values[metric] = today[metric].get(day)
        typical[metric] = _r(_mean(list(month[metric].values())), 1)
    # Slow-changing values: show the last known one
    for metric in ("vo2max", "weight_kg", "body_fat_pct", "fitness_age", "training_status"):
        if values[metric] is None:
            latest = db.latest(metric, day)
            if latest:
                values[metric] = latest[1]
    curves = {
        kind: (db.intraday(kind, day, day) or [(day, [])])[0][1] for kind in ("hr", "stress", "bb", "steps", "resp")
    }
    night = (db.intraday("sleep", day, day) or [(day, None)])[0][1]
    activities = db.query(
        "SELECT id, start_local, type, name, distance_m, duration_s, calories, avg_hr, training_load"
        " FROM activities WHERE day = ? ORDER BY start_ts",
        (day,),
    )
    return {
        "day": day,
        "values": values,
        "typical": typical,
        "curves": curves,
        "night": night,
        "activities": activities,
    }


# ----------------------------------------------------------------------
# Insights
# ----------------------------------------------------------------------
def streaks(steps: dict[str, float], goals: dict[str, float], days: list[str]) -> dict:
    """Current and longest run of days hitting the step goal (a day without data breaks it)."""
    longest = current = run = 0
    longest_end = None
    for day in days:
        goal = goals.get(day) or 10000
        if steps.get(day) is not None and steps[day] >= goal:
            run += 1
            if run > longest:
                longest, longest_end = run, day
        else:
            run = 0
    # The current streak may still go on today, which isn't over yet
    for day in reversed(days):
        goal = goals.get(day) or 10000
        if steps.get(day) is not None and steps[day] >= goal:
            current += 1
        elif day == days[-1]:
            continue
        else:
            break
    hit = sum(1 for d in days if steps.get(d) is not None and steps[d] >= (goals.get(d) or 10000))
    worn = sum(1 for d in days if steps.get(d) is not None)
    return {"current": current, "longest": longest, "longest_end": longest_end, "hit": hit, "worn": worn}


def best_days(db: Database, start: str, end: str) -> list[dict]:
    """Personal bests among the daily values (records the Garmin site doesn't keep)."""
    picks = (
        ("steps", "MAX", "Most steps"),
        ("floors_up", "MAX", "Most floors"),
        ("kcal_active", "MAX", "Most active calories"),
        ("sleep_s", "MAX", "Longest sleep"),
        ("sleep_score", "MAX", "Best sleep score"),
        ("hr_resting", "MIN", "Lowest resting heart rate"),
        ("hrv_night", "MAX", "Highest overnight HRV"),
        ("bb_charged", "MAX", "Best Body Battery recharge"),
        ("stress_avg", "MIN", "Calmest day"),
        ("stress_avg", "MAX", "Most stressful day"),
        ("vo2max", "MAX", "Highest VO2 max"),
        ("readiness", "MAX", "Highest training readiness"),
    )
    found = []
    for metric, how, label in picks:
        order = "DESC" if how == "MAX" else "ASC"
        row = db.query_one(
            f"SELECT day, value FROM daily WHERE metric = ? AND day >= ? AND day <= ? AND value > 0 ORDER BY value {order}, day DESC LIMIT 1",
            (metric, start, end),
        )
        if row:
            found.append({"metric": metric, "label": label, "day": row["day"], "value": row["value"]})
    return found


def weekday_pattern(values: dict[str, dict[str, float]]) -> dict[str, list]:
    result = {}
    for metric, by_day in values.items():
        groups: dict[int, list[float]] = defaultdict(list)
        for day, value in by_day.items():
            groups[date.fromisoformat(day).weekday()].append(value)
        result[metric] = [_r(_mean(groups.get(i, [])), 1) for i in range(7)]
    return result


def sleep_rhythm(
    bed: dict[str, float], wake: dict[str, float], sleep_s: dict[str, float], need: dict[str, float]
) -> dict:
    """How regular the nights are. Sleep is filed under the day you wake up.

    Social jetlag: how much later the middle of your sleep falls on free days (nights to Saturday and
    Sunday) than on work days. Debt: the sleep you needed according to Garmin minus what you got.
    """
    work, free = [], []
    for day, bedtime in bed.items():
        if day not in wake:
            continue
        waketime = wake[day] + 24
        middle = (bedtime + waketime) / 2
        (free if date.fromisoformat(day).weekday() >= 5 else work).append((bedtime, wake[day], middle))
    nights = work + free

    def avg(rows: list, i: int) -> float | None:
        return _r(_mean([row[i] for row in rows]), 2)

    debt = 0.0
    for day, slept in sleep_s.items():
        if need.get(day):
            debt += need[day] * 60 - slept
    consistency = _r(statistics.pstdev([n[0] for n in nights]) * 60, 0) if len(nights) > 2 else None
    social = None
    if work and free:
        social = _r(avg(free, 2) - avg(work, 2), 2)
    return {
        "nights": len(nights),
        "bedtime_work": avg(work, 0),
        "bedtime_free": avg(free, 0),
        "wake_work": avg(work, 1),
        "wake_free": avg(free, 1),
        "social_jetlag_h": social,
        "bedtime_spread_min": consistency,
        "debt_h": _r(debt / 3600, 1) if need else None,
        "chronotype": None if not nights else _chronotype(avg(nights, 2)),
    }


def _chronotype(middle: float | None) -> str | None:
    """From the average middle of the sleep (hours after the previous midnight, 27 = 03:00)."""
    if middle is None:
        return None
    if middle < 26.5:
        return "early bird"
    if middle < 28.0:
        return "in between"
    return "night owl"


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def effects(db: Database, start: str, end: str, activity_days: set[str], late_days: set[str]) -> list[dict]:
    """What goes together with a better or worse night: the next night's sleep score, overnight HRV and
    resting heart rate after days high or low in something, compared by halves around the median.
    Plain differences, not proof: they only show what tends to go together for you."""
    factors = ("steps", "stress_avg", "kcal_active", "im_vigorous", "bb_drained", "food_kcal", "hydration_ml")
    outcomes = ("sleep_score", "hrv_night", "hr_resting", "sleep_s", "readiness")
    span_end = _shift(end, 1)
    data = db.series([*factors, *outcomes], start, span_end)
    found = []

    def compare(
        name: str, label_high: str, label_low: str, high_days: list[str], low_days: list[str], kind: str
    ) -> None:
        for outcome in outcomes:
            high = [data[outcome][_shift(d, 1)] for d in high_days if _shift(d, 1) in data[outcome]]
            low = [data[outcome][_shift(d, 1)] for d in low_days if _shift(d, 1) in data[outcome]]
            if len(high) < 8 or len(low) < 8:
                continue
            a, b = _mean(high), _mean(low)
            spread = statistics.pstdev(high + low) or 1.0
            found.append(
                {
                    "factor": name,
                    "kind": kind,
                    "high": label_high,
                    "low": label_low,
                    "outcome": outcome,
                    "with": _r(a, 1),
                    "without": _r(b, 1),
                    "difference": _r(a - b, 1),
                    # How big the difference is next to the normal ups and downs
                    "size": _r((a - b) / spread, 2),
                    "n": [len(high), len(low)],
                }
            )

    for factor in factors:
        values = data[factor]
        if len(values) < 20:
            continue
        median = statistics.median(values.values())
        if median <= 0 and factor != "im_vigorous":
            continue
        high = [d for d, v in values.items() if v > median]
        low = [d for d, v in values.items() if v <= median]
        if factor == "im_vigorous":
            compare(
                factor,
                "with vigorous exercise",
                "without",
                [d for d, v in values.items() if v > 0],
                [d for d, v in values.items() if v == 0],
                "yesno",
            )
            continue
        compare(factor, f"above {round(median):,}", f"at most {round(median):,}", high, low, "split")
    worn = [d for d in data["steps"]]
    if worn:
        compare(
            "workout",
            "with a workout",
            "without",
            [d for d in worn if d in activity_days],
            [d for d in worn if d not in activity_days],
            "yesno",
        )
        evening = [d for d in worn if d in activity_days]
        compare(
            "late_workout",
            "workout after 19:00",
            "earlier workout",
            [d for d in evening if d in late_days],
            [d for d in evening if d not in late_days],
            "yesno",
        )
    found.sort(key=lambda e: abs(e["size"] or 0), reverse=True)
    return found[:24]


def body_alerts(db: Database, start: str, end: str) -> list[dict]:
    """Days the body looked off: resting heart rate well above its own recent normal, together with
    a low HRV or a poor recharge. Often the first sign of a cold, a hard week or a late night out."""
    lead = (date.fromisoformat(start) - timedelta(days=35)).isoformat()
    data = db.series(["hr_resting", "hrv_night", "hrv_low", "bb_charged", "stress_avg"], lead, end)
    rhr = data["hr_resting"]
    alerts = []
    days = sorted(rhr)
    for i, day in enumerate(days):
        if day < start:
            continue
        window = [rhr[d] for d in days[max(0, i - 30) : i]]
        if len(window) < 14:
            continue
        mean, sd = statistics.mean(window), statistics.pstdev(window) or 1.0
        rise = rhr[day] - mean
        if rise < max(3.0, 2 * sd):
            continue
        signs = [f"resting heart rate {rhr[day]:.0f}, normally {mean:.0f}"]
        hrv, low = data["hrv_night"].get(day), data["hrv_low"].get(day)
        if hrv is not None and low is not None and hrv < low:
            signs.append(f"HRV {hrv:.0f} below its baseline")
        charged = data["bb_charged"].get(day)
        if charged is not None and charged < 30:
            signs.append(f"Body Battery recharged only {charged:.0f}")
        alerts.append({"day": day, "rise": _r(rise, 1), "signs": signs, "score": _r(rise / sd, 1)})
    alerts.sort(key=lambda a: a["day"], reverse=True)
    return alerts[:30]


def year_over_year(db: Database, metric: str = "steps") -> dict:
    rows = db.query(
        "SELECT substr(day, 1, 4) AS year, CAST(substr(day, 6, 2) AS INTEGER) AS month, AVG(value) AS avg"
        " FROM daily WHERE metric = ? GROUP BY year, month ORDER BY year, month",
        (metric,),
    )
    years: dict[str, list] = {}
    for row in rows:
        years.setdefault(row["year"], [None] * 12)[row["month"] - 1] = round(row["avg"], 1)
    return {"metric": metric, "years": years}


def day_rhythm(db: Database, start: str, end: str, max_days: int = 400) -> dict:
    """The average day: heart rate, stress and Body Battery per hour, and stress per weekday and hour."""
    first = max(start, (date.fromisoformat(end) - timedelta(days=max_days)).isoformat())
    result: dict[str, Any] = {"days": 0}
    # When you move: steps per weekday and hour (from the steps per 15 minutes)
    moving: dict[tuple[int, int], list[float]] = defaultdict(list)
    for day, points in db.intraday("steps", first, end):
        weekday = date.fromisoformat(day).weekday()
        per_hour: dict[int, float] = defaultdict(float)
        for minute, value in points:
            per_hour[int(minute) // 60] += value
        for hour in range(24):
            moving[(weekday, hour)].append(per_hour.get(hour, 0.0))
    result["steps_grid"] = (
        [[_r(_mean(moving.get((w, h), [])), 0) for h in range(24)] for w in range(7)] if moving else None
    )
    for kind in ("hr", "stress", "bb"):
        hours: dict[int, list[float]] = defaultdict(list)
        grid: dict[tuple[int, int], list[float]] = defaultdict(list)
        curves = db.intraday(kind, first, end)
        for day, points in curves:
            weekday = date.fromisoformat(day).weekday()
            for minute, value in points:
                hours[int(minute) // 60].append(value)
                if kind == "stress":
                    grid[(weekday, int(minute) // 60)].append(value)
        result["days"] = max(result["days"], len(curves))
        result[kind] = [_r(_mean(hours.get(h, [])), 1) for h in range(24)]
        if kind == "stress":
            result["stress_grid"] = [[_r(_mean(grid.get((w, h), [])), 0) for h in range(24)] for w in range(7)]
    return result


def calendar(db: Database, metric: str, start: str, end: str) -> dict:
    return {"metric": metric, "values": db.series([metric], start, end)[metric]}


def fun_facts(db: Database, start: str, end: str) -> dict:
    """Totals and how they compare with the world. The numbers behind the jokes are plain sums."""
    totals = {
        row["metric"]: row
        for row in db.query(
            "SELECT metric, SUM(value) AS total, COUNT(*) AS days, AVG(value) AS avg FROM daily"
            " WHERE day >= ? AND day <= ? AND metric IN ('steps', 'distance_m', 'floors_up', 'kcal_active', 'kcal_total',"
            " 'sleep_s', 'stress_high_s', 'stress_rest_s', 'hr_resting', 'hr_avg', 'hr_worn_min', 'im_vigorous', 'im_moderate')"
            " GROUP BY metric",
            (start, end),
        )
    }

    def total(metric: str) -> float:
        return float((totals.get(metric) or {}).get("total") or 0)

    km = total("distance_m") / 1000
    floors = total("floors_up")
    active_kcal = total("kcal_active")
    sleep_h = total("sleep_s") / 3600
    worn_min = total("hr_worn_min")
    beats = None
    if worn_min:
        rows = db.query(
            "SELECT a.value * b.value AS beats FROM daily a JOIN daily b ON a.day = b.day AND b.metric = 'hr_worn_min'"
            " WHERE a.metric = 'hr_avg' AND a.day >= ? AND a.day <= ?",
            (start, end),
        )
        beats = sum(r["beats"] for r in rows)
    activity = db.query_one(
        "SELECT COUNT(*) AS count, SUM(distance_m) AS distance_m, SUM(duration_s) AS duration_s,"
        " SUM(elevation_gain_m) AS climb_m, SUM(calories) AS calories FROM activities WHERE day >= ? AND day <= ?",
        (start, end),
    )
    climb = (activity.get("climb_m") or 0) + floors * FLOOR_M
    days = (totals.get("steps") or {}).get("days") or 0
    return {
        "days": days,
        "steps": total("steps"),
        "km": round(km, 1),
        "earth_laps": round(km / EARTH_KM, 4),
        "moon_share": round(km / MOON_KM, 5),
        "marathons": round(km / MARATHON_KM, 1),
        "floors": floors,
        "everests": round(floors * FLOOR_M / EVEREST_M, 2),
        "eiffel_towers": round(climb / EIFFEL_M, 1),
        "climb_m": round(climb),
        "active_kcal": active_kcal,
        "pizzas": round(active_kcal / PIZZA_KCAL, 1),
        "beers": round(active_kcal / BEER_KCAL),
        "fat_kg": round(active_kcal / FAT_KCAL_PER_KG, 1),
        "sleep_h": round(sleep_h),
        "sleep_years": round(sleep_h / 24 / 365.25, 2),
        "sleep_nights": (totals.get("sleep_s") or {}).get("days") or 0,
        "high_stress_h": round(total("stress_high_s") / 3600),
        "rest_h": round(total("stress_rest_s") / 3600),
        "heartbeats": round(beats) if beats else None,
        "vigorous_min": total("im_vigorous"),
        "moderate_min": total("im_moderate"),
        "activities": activity,
    }


def insights(db: Database, start: str, end: str) -> dict:
    days = days_between(start, end)
    data = db.series(
        [
            "steps",
            "steps_goal",
            "sleep_score",
            "stress_avg",
            "hr_resting",
            "hrv_night",
            "bedtime_h",
            "waketime_h",
            "sleep_s",
            "sleep_need_min",
            "bb_charged",
        ],
        start,
        end,
    )
    activity_rows = db.query("SELECT day, start_local FROM activities WHERE day >= ? AND day <= ?", (start, end))
    activity_days = {r["day"] for r in activity_rows}
    late_days = {r["day"] for r in activity_rows if r["start_local"][11:13] >= "19"}
    return {
        "start": start,
        "end": end,
        "streak": streaks(data["steps"], data["steps_goal"], days),
        "bests": best_days(db, start, end),
        "weekdays": weekday_pattern(
            {m: data[m] for m in ("steps", "sleep_score", "stress_avg", "hr_resting", "hrv_night", "bb_charged")}
        ),
        "sleep_rhythm": sleep_rhythm(data["bedtime_h"], data["waketime_h"], data["sleep_s"], data["sleep_need_min"]),
        "effects": effects(db, start, end, activity_days, late_days),
        "alerts": body_alerts(db, start, end),
        "fun": fun_facts(db, start, end),
        "rhythm": day_rhythm(db, start, end),
    }


# ----------------------------------------------------------------------
# Activities
# ----------------------------------------------------------------------
def eddington(daily_km: list[float]) -> int:
    """The largest E with E days of at least E km."""
    ranked = sorted(daily_km, reverse=True)
    e = 0
    for i, km in enumerate(ranked, start=1):
        if km >= i:
            e = i
        else:
            break
    return e


def activity_summary(db: Database, start: str, end: str, bucket: str = "month") -> dict:
    rows = db.query(
        "SELECT id, day, start_local, type, distance_m, duration_s, elevation_gain_m, calories, training_load,"
        " avg_hr, aerobic_te, anaerobic_te FROM activities WHERE day >= ? AND day <= ? ORDER BY start_ts",
        (start, end),
    )
    per_type: dict[str, dict] = {}
    per_family_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    buckets = sorted({bucket_of(d, bucket) for d in days_between(start, end)})
    by_bucket: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    grid = [[0] * 24 for _ in range(7)]
    for row in rows:
        kind = row["type"]
        fam = family(kind)
        entry = per_type.setdefault(
            kind,
            {
                "type": kind,
                "family": fam,
                "count": 0,
                "distance_m": 0.0,
                "duration_s": 0.0,
                "climb_m": 0.0,
                "calories": 0.0,
            },
        )
        entry["count"] += 1
        entry["distance_m"] += row["distance_m"] or 0
        entry["duration_s"] += row["duration_s"] or 0
        entry["climb_m"] += row["elevation_gain_m"] or 0
        entry["calories"] += row["calories"] or 0
        per_family_day[fam][row["day"]] += (row["distance_m"] or 0) / 1000
        by_bucket[bucket_of(row["day"], bucket)][fam] += (row["duration_s"] or 0) / 3600
        with_time = row["start_local"]
        if len(with_time) >= 13:
            grid[date.fromisoformat(row["day"]).weekday()][int(with_time[11:13])] += 1
    families = [name for name, _ in FAMILIES] + ["other"]
    zones = [0.0] * 5
    for body in db.query("SELECT body FROM activities WHERE day >= ? AND day <= ?", (start, end)):
        entry = unpack(body["body"]) or {}
        for i in range(5):
            zones[i] += float(entry.get(f"hrTimeInZone_{i + 1}") or 0)
    longest = db.query_one(
        "SELECT id, name, type, day, distance_m FROM activities WHERE day >= ? AND day <= ? ORDER BY distance_m DESC LIMIT 1",
        (start, end),
    )
    climb = db.query_one(
        "SELECT id, name, type, day, elevation_gain_m FROM activities WHERE day >= ? AND day <= ? ORDER BY elevation_gain_m DESC LIMIT 1",
        (start, end),
    )
    hardest = db.query_one(
        "SELECT id, name, type, day, training_load FROM activities WHERE day >= ? AND day <= ? ORDER BY training_load DESC LIMIT 1",
        (start, end),
    )
    return {
        "count": len(rows),
        "types": sorted(per_type.values(), key=lambda t: t["duration_s"], reverse=True),
        "eddington": {
            fam: eddington(list(per_family_day[fam].values()))
            for fam in ("running", "cycling", "walking")
            if per_family_day[fam]
        },
        "buckets": buckets,
        "hours": {
            fam: [round(by_bucket[b].get(fam, 0), 2) for b in buckets]
            for fam in families
            if any(by_bucket[b].get(fam) for b in buckets)
        },
        "start_grid": grid,
        "hr_zones_s": [round(z) for z in zones],
        "records": {"longest": longest or None, "climb": climb or None, "hardest": hardest or None},
    }


# Over a long period nearly every cell is in play: walking the cells in order and counting beats
# collecting the period's rows by day and sorting them (as in the Tesla app)
LONG_PERIOD_DAYS = 120


def heat_cells(db: Database, start: str, end: str, activity_family: str = "", limit: int = 40000) -> list[list[float]]:
    """Where you've been, as [lat, lon, activities through it] per ~100 m cell, busiest first."""
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    index = "visited_cells_cell" if days > LONG_PERIOD_DAYS else "visited_cells_day"
    sport = " AND family = ?" if activity_family else ""
    rows = db.query(
        f"SELECT lat, lon, COUNT(*) AS weight FROM visited_cells INDEXED BY {index}"
        f" WHERE day >= ? AND day <= ?{sport} GROUP BY lat, lon ORDER BY weight DESC LIMIT ?",
        (start, end, *([activity_family] if activity_family else []), limit),
    )
    return [[row["lat"], row["lon"], row["weight"]] for row in rows]


def heat_tracks(
    db: Database,
    start: str,
    end: str,
    bounds: tuple[float, float, float, float],
    activity_family: str = "",
    limit: int = 400,
) -> dict:
    """The tracks through a part of the map, for the heat map up close, where ~100 m cells would show
    as dots. Only the parts inside the area (with a margin) are returned. The cells find the activities,
    so no track outside the area is read."""
    south, west, north, east = bounds
    margin_lat, margin_lon = (north - south) * 0.2, (east - west) * 0.2
    south, north, west, east = south - margin_lat, north + margin_lat, west - margin_lon, east + margin_lon
    sport = " AND family = ?" if activity_family else ""
    ids = [
        row["activity_id"]
        for row in db.query(
            "SELECT DISTINCT activity_id FROM visited_cells INDEXED BY visited_cells_cell"
            f" WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND day >= ? AND day <= ?{sport} LIMIT ?",
            (south, north, west, east, start, end, *([activity_family] if activity_family else []), limit + 1),
        )
    ]
    lines = []
    for activity_id in ids[:limit]:
        run: list = []
        for lat, lon in db.track(activity_id) or []:
            if south <= lat <= north and west <= lon <= east:
                run.append([lat, lon])
            elif run:
                lines.append(run)
                run = []
        if len(run) > 1:
            lines.append(run)
    return {"lines": [line for line in lines if len(line) > 1], "more": len(ids) > limit}
