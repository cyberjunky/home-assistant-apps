"""What is fetched from Garmin Connect, in which pieces, and how it's read into the database.

Each data set belongs to a group that the app options switch on and off. Three kinds:

- range: one request covers a span of days (up to `chunk_days`, Garmin's own limit or less);
- day: one request (or a few) per day, such as the heart rate curve of a day;
- snapshot: only exists as "now" (gear, devices, records), fetched again every `every_hours`.

Parsers are forgiving: a field Garmin leaves out is simply not stored. The full answer is kept in
the database anyway (see database.py), so nothing is lost when a field isn't read out yet.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .account import GarminAccount, NotSignedIn, RateLimited
from .database import Database

logger = logging.getLogger(__name__)

Fetch = Callable[[GarminAccount, date, date], Awaitable[Any]]
Parse = Callable[[Database, Any, date, date], int]


@dataclass(frozen=True)
class Dataset:
    key: str
    group: str
    title: str
    kind: str  # "range", "day" or "snapshot"
    fetch: Fetch
    parse: Parse
    chunk_days: int = 1
    # The last days are fetched again on every update: Garmin still changes them
    recent_days: int = 3
    every_hours: float = 6.0
    # Keep the whole answer too (the day curves are big and fully read out, so they don't)
    keep_raw: bool = True


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def iso(day: date) -> str:
    return day.isoformat()


def dig(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def as_list(data: Any, *keys: str) -> list:
    """A list from an answer that is either the list itself or holds it under one of `keys`."""
    if isinstance(data, list):
        return data
    for key in keys:
        value = dig(data, key)
        if isinstance(value, list):
            return value
    return []


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def day_of(value: Any) -> str | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def numeric_fields(entry: dict, prefix: str, skip: tuple[str, ...] = ()) -> dict[str, float]:
    """Every number in an entry (one level of nesting too), as metric names: for data the
    dashboard shows through the Explore chart rather than a chart of its own."""
    found: dict[str, float] = {}
    for key, value in (entry or {}).items():
        if key in skip or key.lower().endswith(("id", "pk", "timestamp", "date", "millis", "version")):
            continue
        if isinstance(value, dict):
            for inner, inner_value in value.items():
                n = number(inner_value)
                if n is not None and not inner.lower().endswith(("id", "pk", "timestamp", "millis")):
                    found[f"{prefix}.{key}.{inner}"] = n
        else:
            n = number(value)
            if n is not None:
                found[f"{prefix}.{key}"] = n
    return found


def gmt_ts(value: Any) -> int | None:
    """Epoch seconds from Garmin's '2026-11-24T05:45:20.0', '2026-11-24 05:45:20' (GMT) or milliseconds."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value / 1000) if value > 1e11 else int(value)
    text = str(value or "").replace(" ", "T")
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text[:19]).replace(tzinfo=UTC).timestamp())
    except ValueError:
        return None


def local_hours(millis: Any) -> float | None:
    """Wall-clock hours from Garmin's 'local' milliseconds (local time stamped as if it were UTC)."""
    n = number(millis)
    if n is None:
        return None
    moment = datetime.fromtimestamp(n / 1000, UTC)
    return moment.hour + moment.minute / 60


def store(db: Database, rows: dict[str, dict[str, float | None]]) -> int:
    """{day: {metric: value}} into the daily table."""
    return db.put_daily([(metric, day, value) for day, values in rows.items() for metric, value in values.items()])


def decode_polyline(encoded: str) -> list[list[float]]:
    """Google's encoded polyline format, as Garmin sends tracks."""
    points, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        for axis in (0, 1):
            shift = result = 0
            while True:
                if index >= len(encoded):
                    return points
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append([round(lat / 1e5, 5), round(lon / 1e5, 5)])
    return points


def thin(points: list, limit: int) -> list:
    """At most `limit` points, evenly picked, keeping the last one."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    picked = [points[int(i * step)] for i in range(limit - 1)]
    return [*picked, points[-1]]


# ----------------------------------------------------------------------
# Daily summaries: steps, calories, heart rate, stress, Body Battery, ...
# ----------------------------------------------------------------------
SUMMARY_FIELDS = {
    "steps": ("movement", "steps", "value"),
    "steps_goal": ("movement", "steps", "goal"),
    "distance_m": ("movement", "steps", "distanceInMeters"),
    "floors_up": ("movement", "floorsAscended", "value"),
    "floors_down": ("movement", "floorsDescended", "value"),
    "floors_goal": ("movement", "floorsAscended", "goal"),
    "kcal_total": ("calories", "burnedTotal"),
    "kcal_active": ("calories", "burnedActive"),
    "kcal_resting": ("calories", "burnedResting"),
    "hr_min": ("heartRate", "minValue"),
    "hr_max": ("heartRate", "maxValue"),
    "hr_resting": ("heartRate", "restingValue"),
    "im_moderate": ("intensityMinutes", "moderate"),
    "im_vigorous": ("intensityMinutes", "vigorous"),
    "im_goal": ("intensityMinutes", "goal"),
    "stress_avg": ("stress", "avgLevel"),
    "stress_max": ("stress", "maxLevel"),
    "bb_min": ("bodyBattery", "minValue"),
    "bb_max": ("bodyBattery", "maxValue"),
    "bb_charged": ("bodyBattery", "chargedValue"),
    "bb_drained": ("bodyBattery", "drainedValue"),
    "resp_avg": ("respiration", "avgValue"),
    "resp_min": ("respiration", "minValue"),
    "resp_max": ("respiration", "maxValue"),
    "spo2_avg": ("pulseOx", "avgValue"),
    "spo2_min": ("pulseOx", "minValue"),
    "hydration_ml": ("hydration", "valueInML"),
    "hydration_goal_ml": ("hydration", "goalInML"),
    "sweat_ml": ("hydration", "sweatLossInML"),
}
STRESS_DURATIONS = {
    "stress_rest_s": "restDurationInMillis",
    "stress_low_s": "lowStressDurationInMillis",
    "stress_medium_s": "mediumStressDurationInMillis",
    "stress_high_s": "highStressDurationInMillis",
    "stress_activity_s": "activityDurationInMillis",
}


async def fetch_summaries(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_daily_summaries", iso(start), iso(end))


def parse_summaries(db: Database, data: Any, start: date, end: date) -> int:
    rows: dict[str, dict] = {}
    for entry in as_list(data, "data"):
        day = day_of(entry.get("calendarDate"))
        # A day the watch wasn't worn has a summary full of zeros: leave it out
        if not day or entry.get("includesWellnessData") is False:
            continue
        values = {metric: number(dig(entry, *path)) for metric, path in SUMMARY_FIELDS.items()}
        stress = entry.get("stress") or {}
        for metric, key in STRESS_DURATIONS.items():
            millis = number(stress.get(key))
            values[metric] = millis / 1000 if millis is not None else None
        # A day without heart rate data never had a watch on
        if values["hr_max"] is None and not values["steps"]:
            continue
        rows[day] = values
    return store(db, rows)


# ----------------------------------------------------------------------
# Sleep and HRV
# ----------------------------------------------------------------------
SLEEP_FIELDS = {
    "sleep_score": "sleepScore",
    "sleep_s": "totalSleepTimeInSeconds",
    "sleep_deep_s": "deepTime",
    "sleep_light_s": "lightTime",
    "sleep_rem_s": "remTime",
    "sleep_awake_s": "awakeTime",
    "sleep_need_min": "sleepNeed",
    "sleep_hr": "avgHeartRate",
    "sleep_resp": "respiration",
    "sleep_hrv": "avgOvernightHrv",
    "sleep_spo2": "spO2",
    "skin_temp_c": "skinTempC",
    "sleep_bb": "bodyBatteryChange",
    "sleep_rhr": "restingHeartRate",
}
HRV_STATUS = {"POOR": 1, "LOW": 2, "UNBALANCED": 3, "BALANCED": 4}


async def fetch_sleep(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_sleep_daily", iso(start), iso(end))


def parse_sleep(db: Database, data: Any, start: date, end: date) -> int:
    rows: dict[str, dict] = {}
    for entry in as_list(data):
        day = day_of(entry.get("calendarDate"))
        values = entry.get("values") or {}
        if not day or not number(values.get("totalSleepTimeInSeconds")):
            continue
        row = {metric: number(values.get(key)) for metric, key in SLEEP_FIELDS.items()}
        bedtime = local_hours(values.get("localSleepStartTimeInMillis"))
        # Bedtime after midnight counts on from 24, so 00:30 sorts after 23:30
        row["bedtime_h"] = None if bedtime is None else bedtime + 24 if bedtime < 15 else bedtime
        row["waketime_h"] = local_hours(values.get("localSleepEndTimeInMillis"))
        rows[day] = row
    return store(db, rows)


async def fetch_hrv(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_hrv_data_range", iso(start), iso(end))


def parse_hrv(db: Database, data: Any, start: date, end: date) -> int:
    rows = {}
    for entry in as_list(data, "hrvSummaries"):
        day = day_of(entry.get("calendarDate"))
        if not day:
            continue
        rows[day] = {
            "hrv_night": number(entry.get("lastNightAvg")),
            "hrv_weekly": number(entry.get("weeklyAvg")),
            "hrv_5min_high": number(entry.get("lastNight5MinHigh")),
            "hrv_low": number(dig(entry, "baseline", "balancedLow")),
            "hrv_high": number(dig(entry, "baseline", "balancedUpper")),
            "hrv_status": HRV_STATUS.get(str(entry.get("status") or "")),
        }
    return store(db, rows)


# ----------------------------------------------------------------------
# Activities (the list; tracks are fetched per activity by the collector)
# ----------------------------------------------------------------------
async def fetch_activities(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_activities_by_date", iso(start), iso(end))


def activity_row(entry: dict) -> dict | None:
    activity_id = entry.get("activityId")
    start_ts = gmt_ts(entry.get("startTimeGMT"))
    start_local = str(entry.get("startTimeLocal") or "")
    if not isinstance(activity_id, int) or start_ts is None or not day_of(start_local):
        return None
    kind = entry.get("activityType") or {}
    return {
        "id": activity_id,
        "start_ts": start_ts,
        "start_local": start_local,
        "day": day_of(start_local),
        "type": kind.get("typeKey") or "other",
        "parent_type": str(kind.get("parentTypeId") or ""),
        "name": entry.get("activityName") or "",
        "distance_m": number(entry.get("distance")),
        "duration_s": number(entry.get("duration")),
        "moving_s": number(entry.get("movingDuration")),
        "elevation_gain_m": number(entry.get("elevationGain")),
        "avg_hr": number(entry.get("averageHR")),
        "max_hr": number(entry.get("maxHR")),
        "calories": number(entry.get("calories")),
        "avg_speed": number(entry.get("averageSpeed")),
        "max_speed": number(entry.get("maxSpeed")),
        "steps": int(entry["steps"]) if isinstance(entry.get("steps"), int) else None,
        "training_load": number(entry.get("activityTrainingLoad")),
        "aerobic_te": number(entry.get("aerobicTrainingEffect")),
        "anaerobic_te": number(entry.get("anaerobicTrainingEffect")),
        "vo2max": number(entry.get("vO2MaxValue")),
        "start_lat": number(entry.get("startLatitude")),
        "start_lon": number(entry.get("startLongitude")),
        "location": entry.get("locationName") or "",
        "has_track": 1 if entry.get("hasPolyline") else 0,
        "body_battery": number(entry.get("differenceBodyBattery")),
        "body": entry,
    }


def parse_activities(db: Database, data: Any, start: date, end: date) -> int:
    rows = [row for row in (activity_row(entry) for entry in as_list(data)) if row]
    return db.put_activities(rows)


# ----------------------------------------------------------------------
# Training: readiness, status and load, VO2 max, race predictions, scores, fitness age
# ----------------------------------------------------------------------
async def fetch_readiness(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_training_readiness_range", iso(start), iso(end))


def parse_readiness(db: Database, data: Any, start: date, end: date) -> int:
    # Garmin recalculates readiness during the day; the morning value (after waking up) is the one to keep
    chosen: dict[str, dict] = {}
    for entry in as_list(data):
        day = day_of(entry.get("calendarDate"))
        if not day or number(entry.get("score")) is None:
            continue
        morning = entry.get("inputContext") == "AFTER_WAKEUP_RESET"
        current = chosen.get(day)
        if current is None or (morning and current.get("inputContext") != "AFTER_WAKEUP_RESET"):
            chosen[day] = entry
    rows = {
        day: {
            "readiness": number(entry.get("score")),
            "readiness_acute_load": number(entry.get("acuteLoad")),
            "readiness_recovery_min": number(entry.get("recoveryTime")),
            "readiness_sleep_pct": number(entry.get("sleepScoreFactorPercent")),
            "readiness_hrv_pct": number(entry.get("hrvFactorPercent")),
            "readiness_stress_pct": number(entry.get("stressHistoryFactorPercent")),
        }
        for day, entry in chosen.items()
    }
    return store(db, rows)


async def fetch_vo2max(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_max_metrics_range", iso(start), iso(end))


def parse_vo2max(db: Database, data: Any, start: date, end: date) -> int:
    rows: dict[str, dict] = {}
    for entry in as_list(data):
        generic = entry.get("generic") or {}
        cycling = entry.get("cycling") or {}
        for part, metric in ((generic, "vo2max"), (cycling, "vo2max_cycling")):
            day = day_of(part.get("calendarDate"))
            value = number(part.get("vo2MaxPreciseValue")) or number(part.get("vo2MaxValue"))
            if day and value:
                rows.setdefault(day, {})[metric] = value
    return store(db, rows)


async def fetch_race(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_race_predictions", iso(start), iso(end), "daily")


def parse_race(db: Database, data: Any, start: date, end: date) -> int:
    rows = {}
    for entry in as_list(data):
        day = day_of(entry.get("calendarDate"))
        if day:
            rows[day] = {
                "race_5k_s": number(entry.get("time5K")),
                "race_10k_s": number(entry.get("time10K")),
                "race_half_s": number(entry.get("timeHalfMarathon")),
                "race_marathon_s": number(entry.get("timeMarathon")),
            }
    return store(db, rows)


async def fetch_training_status(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_weekly_training_status", iso(start), iso(end))


def parse_training_status(db: Database, data: Any, start: date, end: date) -> int:
    rows: dict[str, dict] = {}
    report = dig(data, "reportData") or {}
    for entries in report.values() if isinstance(report, dict) else []:
        for entry in entries or []:
            day = day_of(entry.get("calendarDate"))
            if not day:
                continue
            load = entry.get("acuteTrainingLoadDTO") or {}
            row = rows.setdefault(day, {})
            for metric, value in (
                ("training_status", entry.get("trainingStatus")),
                ("load_weekly", entry.get("weeklyTrainingLoad")),
                ("load_acute", load.get("dailyTrainingLoadAcute")),
                ("load_chronic", load.get("dailyTrainingLoadChronic")),
                ("load_acwr", load.get("dailyAcuteChronicWorkloadRatio")),
                ("load_acwr_pct", load.get("acwrPercent")),
            ):
                if number(value) is not None:
                    row[metric] = number(value)
    return store(db, rows)


async def fetch_endurance(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_endurance_score", iso(start), iso(end))


def parse_endurance(db: Database, data: Any, start: date, end: date) -> int:
    rows = {}
    for day, group in (dig(data, "groupMap") or {}).items():
        if day_of(day) and isinstance(group, dict):
            rows[day_of(day)] = {
                "endurance": number(group.get("groupAverage")),
                "endurance_max": number(group.get("groupMax")),
            }
    return store(db, rows)


async def fetch_hill(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_hill_score", iso(start), iso(end))


def parse_hill(db: Database, data: Any, start: date, end: date) -> int:
    rows = {}
    for entry in as_list(data, "hillScoreDTOList"):
        day = day_of(entry.get("calendarDate"))
        if day:
            rows[day] = {
                "hill": number(entry.get("overallScore")),
                "hill_strength": number(entry.get("strengthScore")),
                "hill_endurance": number(entry.get("enduranceScore")),
            }
    return store(db, rows)


async def fetch_fitness_age(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_fitness_age_daily", iso(start), iso(end))


def parse_fitness_age(db: Database, data: Any, start: date, end: date) -> int:
    rows = {}
    for entry in as_list(data):
        day = day_of(entry.get("calendarDate"))
        values = entry.get("values") or {}
        if day:
            rows[day] = {
                "fitness_age": number(values.get("fitnessAge")),
                "fitness_age_achievable": number(values.get("achievableFitnessAge")),
            }
    return store(db, rows)


async def fetch_acclimation(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_heat_altitude_acclimation_range", iso(start), iso(end))


def parse_acclimation(db: Database, data: Any, start: date, end: date) -> int:
    rows = {}
    for entry in as_list(data):
        day = day_of(entry.get("calendarDate"))
        if day:
            rows[day] = {
                "heat_acclimation": number(entry.get("heatAcclimationPercentage")),
                "altitude_acclimation": number(entry.get("altitudeAcclimation")),
            }
    return store(db, rows)


# ----------------------------------------------------------------------
# Body: weight and body composition, blood pressure
# ----------------------------------------------------------------------
async def fetch_weight(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_weigh_ins", iso(start), iso(end))


def _grams(value: Any) -> float | None:
    n = number(value)
    return round(n / 1000, 2) if n is not None else None


def parse_weight(db: Database, data: Any, start: date, end: date) -> int:
    rows, events = {}, []
    for summary in as_list(data, "dailyWeightSummaries"):
        day = day_of(summary.get("summaryDate"))
        latest = summary.get("latestWeight") or {}
        if not day or number(latest.get("weight")) is None:
            continue
        rows[day] = {
            "weight_kg": _grams(latest.get("weight")),
            "bmi": number(latest.get("bmi")),
            "body_fat_pct": number(latest.get("bodyFat")),
            "body_water_pct": number(latest.get("bodyWater")),
            "muscle_kg": _grams(latest.get("muscleMass")),
            "bone_kg": _grams(latest.get("boneMass")),
            "visceral_fat": number(latest.get("visceralFat")),
            "metabolic_age": number(latest.get("metabolicAge")),
        }
        for weigh_in in summary.get("allWeightMetrics") or [latest]:
            key = weigh_in.get("samplePk") or weigh_in.get("date")
            if key:
                events.append((key, gmt_ts(weigh_in.get("timestampGMT") or weigh_in.get("date")), day, weigh_in))
    db.put_events("weight", events)
    return store(db, rows) + len(events)


async def fetch_blood_pressure(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_blood_pressure", iso(start), iso(end))


def parse_blood_pressure(db: Database, data: Any, start: date, end: date) -> int:
    per_day: dict[str, list[dict]] = {}
    events = []
    for summary in as_list(data, "measurementSummaries"):
        for reading in summary.get("measurements") or []:
            day = day_of(reading.get("measurementTimestampLocal"))
            if not day or number(reading.get("systolic")) is None:
                continue
            per_day.setdefault(day, []).append(reading)
            events.append(
                (
                    reading.get("version") or reading.get("measurementTimestampGMT"),
                    gmt_ts(reading.get("measurementTimestampGMT")),
                    day,
                    reading,
                )
            )
    rows = {}
    for day, readings in per_day.items():

        def avg(key: str, readings=readings) -> float | None:
            values = [number(r.get(key)) for r in readings if number(r.get(key)) is not None]
            return round(sum(values) / len(values), 1) if values else None

        rows[day] = {"bp_systolic": avg("systolic"), "bp_diastolic": avg("diastolic"), "bp_pulse": avg("pulse")}
    db.put_events("blood_pressure", events)
    return store(db, rows) + len(events)


# ----------------------------------------------------------------------
# The day curves: heart rate, stress and Body Battery through the day, in 5-minute steps
# ----------------------------------------------------------------------
STEP_MIN = 5


async def fetch_day_curves(account: GarminAccount, start: date, end: date) -> Any:
    return {
        "heart_rate": await account.call("get_heart_rates", iso(start)),
        "stress": await account.call("get_stress_data", iso(start)),
    }


def _buckets(values: list, start_ms: float, index: int) -> list[list[float]]:
    """[[minute of the day, average], ...] in STEP_MIN steps from [[epoch ms, ..., value at index], ...]."""
    sums: dict[int, list[float]] = {}
    for sample in values or []:
        if not isinstance(sample, list | tuple) or len(sample) <= index:
            continue
        value = number(sample[index])
        stamp = number(sample[0])
        if value is None or value < 0 or stamp is None:
            continue
        minute = int((stamp - start_ms) / 60000)
        if 0 <= minute < 1440:
            bucket = sums.setdefault(minute // STEP_MIN * STEP_MIN, [0.0, 0])
            bucket[0] += value
            bucket[1] += 1
    return [[minute, round(total / count, 1)] for minute, (total, count) in sorted(sums.items())]


def parse_day_curves(db: Database, data: Any, start: date, end: date) -> int:
    day = iso(start)
    stored = 0
    heart = data.get("heart_rate") or {}
    stress = data.get("stress") or {}
    start_ms = (gmt_ts(heart.get("startTimestampGMT") or stress.get("startTimestampGMT")) or 0) * 1000
    if not start_ms:
        return 0
    curves = {
        "hr": _buckets(heart.get("heartRateValues"), start_ms, 1),
        "stress": _buckets(stress.get("stressValuesArray"), start_ms, 1),
        "bb": _buckets(stress.get("bodyBatteryValuesArray"), start_ms, 2),
    }
    for kind, points in curves.items():
        if points:
            db.put_intraday(kind, day, points)
            stored += 1
    # Average heart rate while worn, and for how long: for the heartbeat count on Insights
    measured = [number(s[1]) for s in heart.get("heartRateValues") or [] if isinstance(s, list) and len(s) > 1]
    measured = [v for v in measured if v and v > 0]
    if measured:
        # Garmin's day curve has a value every 2 minutes
        stored += store(db, {day: {"hr_avg": sum(measured) / len(measured), "hr_worn_min": len(measured) * 2}})
    return stored


# ----------------------------------------------------------------------
# Records, badges, gear, devices: what only exists as "now"
# ----------------------------------------------------------------------
async def fetch_records(account: GarminAccount, start: date, end: date) -> Any:
    result = {}
    for name, method, args in (
        ("personal_records", "get_personal_record", ()),
        ("badges", "get_earned_badges", ()),
        ("gear", "get_gear_list", ()),
        ("devices", "get_devices", ()),
        ("lifetime", "get_lifetime_totals", ()),
        ("statistics", "get_user_statistics", ()),
        ("goals_active", "get_goals", ("active",)),
        ("goals_past", "get_goals", ("past",)),
        ("lactate_threshold", "get_lactate_threshold", ()),
    ):
        try:
            result[name] = await account.call(method, *args)
        except (RateLimited, NotSignedIn):
            raise
        except Exception as e:
            logger.debug(f"Records: {method} failed: {e}")
    return result


def parse_records(db: Database, data: Any, start: date, end: date) -> int:
    stored = 0
    for name, value in (data or {}).items():
        if value is not None:
            db.put_snapshot(name, value)
            stored += 1
    badges = []
    for badge in as_list(data.get("badges")):
        earned = badge.get("badgeEarnedDate")
        if badge.get("badgeId") is not None and earned:
            badges.append(
                (f"{badge['badgeId']}-{badge.get('badgeEarnedNumber') or 1}", gmt_ts(earned), day_of(earned), badge)
            )
    return stored + db.put_events("badge", badges)


# ----------------------------------------------------------------------
# Health monitoring: ECG, Health Snapshot, glucose, Jet Lag Adviser, lifestyle logging
# ----------------------------------------------------------------------
async def fetch_ecg(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_ecg_daily", iso(start), iso(end))


def parse_ecg(db: Database, data: Any, start: date, end: date) -> int:
    events = []
    for entry in as_list(data, "content"):
        key = entry.get("detailId")
        if key:
            events.append((key, gmt_ts(entry.get("startTime")), day_of(entry.get("startTimeLocal")), entry))
    return db.put_events("ecg", events)


async def fetch_health_snapshots(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_health_snapshots_range", iso(start), iso(end))


def parse_health_snapshots(db: Database, data: Any, start: date, end: date) -> int:
    events = []
    for entry in as_list(data):
        key = dig(entry, "activityUuid", "uuid") or entry.get("startTimestampGMT")
        if key:
            events.append((key, gmt_ts(entry.get("startTimestampGMT")), day_of(entry.get("calendarDate")), entry))
    return db.put_events("health_snapshot", events)


async def fetch_glucose(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_glucose_data_range", iso(start), iso(end))


def parse_glucose(db: Database, data: Any, start: date, end: date) -> int:
    events = []
    for entry in as_list(data, "glucoseList"):
        stamp = entry.get("timestampGMT") or entry.get("measurementTimestampGMT") or entry.get("timestamp")
        key = entry.get("id") or entry.get("glucosePk") or stamp
        if key:
            events.append(
                (key, gmt_ts(stamp), day_of(entry.get("calendarDate") or entry.get("timestampLocal") or stamp), entry)
            )
    return db.put_events("glucose", events)


async def fetch_jetlag(account: GarminAccount, start: date, end: date) -> Any:
    return {"jetlag_trips": await account.call("get_jetlag_trips")}


async def fetch_lifestyle(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_lifestyle_logging_data", iso(start))


def parse_lifestyle(db: Database, data: Any, start: date, end: date) -> int:
    """What was logged that day. Kept as is; the Explore chart shows the numbers in it."""
    logged = []
    for item in as_list(data, "dailyLogsReport"):
        logs = item.get("logs") or item.get("dailyLogs") or item.get("logDetails")
        if logs:
            logged.append({"name": item.get("name"), "category": item.get("category"), "logs": logs})
    if logged:
        db.put_events("lifestyle", [(iso(start), None, iso(start), {"logged": logged})])
    return len(logged)


# ----------------------------------------------------------------------
# Performance: the newer metrics (running economy, cycling ability, critical swim speed, ...)
# ----------------------------------------------------------------------
def _generic_range(method: str, prefix: str, *extra: Any) -> tuple[Fetch, Parse]:
    async def fetch(account: GarminAccount, start: date, end: date) -> Any:
        return await account.call(method, iso(start), iso(end), *extra)

    def parse(db: Database, data: Any, start: date, end: date) -> int:
        rows: dict[str, dict] = {}
        entries = as_list(data)
        if not entries and isinstance(data, dict):
            # Some answers are keyed by date ({"2026-11-01": {...}, ...}) inside a map
            for value in data.values():
                if isinstance(value, dict) and all(day_of(k) for k in list(value)[:3]):
                    entries = [{"calendarDate": k, **v} for k, v in value.items() if isinstance(v, dict)]
                    break
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            day = day_of(entry.get("calendarDate") or entry.get("date"))
            if day:
                rows.setdefault(day, {}).update(numeric_fields(entry, prefix))
        return store(db, rows)

    return fetch, parse


async def fetch_thresholds(account: GarminAccount, start: date, end: date) -> Any:
    return {
        "cycling_ftp": await account.call("get_cycling_ftp"),
        "power_zones": await account.call("get_power_zones"),
        "heart_rate_zones": await account.call("get_heart_rate_zones"),
    }


# ----------------------------------------------------------------------
# Nutrition (Garmin Connect+ food logging)
# ----------------------------------------------------------------------
async def fetch_nutrition(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_calorie_summary", iso(start), iso(end))


def parse_nutrition(db: Database, data: Any, start: date, end: date) -> int:
    rows: dict[str, dict] = {}
    for entry in as_list(data, "dailyNutritionContents"):
        day = day_of(entry.get("mealDate") or entry.get("calendarDate"))
        if not day:
            continue
        row = rows.setdefault(day, {})
        goals = entry.get("nutritionGoals") or {}
        row["food_kcal_goal"] = number(goals.get("adjustedCalories") or goals.get("calories"))
        for key in ("nutritionContent", "consumedNutritionContent", "consumed", "totals"):
            content = entry.get(key)
            if isinstance(content, dict) and content:
                row["food_kcal"] = number(content.get("calories"))
                row["food_protein_g"] = number(content.get("protein"))
                row["food_carbs_g"] = number(content.get("carbs"))
                row["food_fat_g"] = number(content.get("fat"))
                row.update(numeric_fields(content, "food"))
                break
    return store(db, rows)


# ----------------------------------------------------------------------
# Menstrual cycle
# ----------------------------------------------------------------------
async def fetch_menstrual(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_menstrual_calendar_data", iso(start), iso(end))


def parse_menstrual(db: Database, data: Any, start: date, end: date) -> int:
    events, rows = [], {}
    for cycle in as_list(data, "cycleSummaries"):
        day = day_of(cycle.get("startDate") or cycle.get("cycleStartDate"))
        if not day:
            continue
        events.append((day, None, day, cycle))
        rows[day] = {
            "cycle_length": number(cycle.get("cycleLength") or cycle.get("actualCycleLength")),
            "period_length": number(cycle.get("periodLength") or cycle.get("actualPeriodLength")),
            **numeric_fields(cycle, "cycle"),
        }
    for name in ("loggedSymptomDays", "loggedOvulationDays", "loggedNoteDays"):
        for item in as_list(data, name):
            day = day_of(item if isinstance(item, str) else (item or {}).get("calendarDate"))
            if day:
                events.append((f"{name}-{day}", None, day, {"type": name, "detail": item}))
    db.put_events("menstrual", events)
    return store(db, rows) + len(events)


# ----------------------------------------------------------------------
# Golf
# ----------------------------------------------------------------------
async def fetch_golf(account: GarminAccount, start: date, end: date) -> Any:
    cards, offset = [], 0
    while True:
        page = await account.call("get_golf_summary", offset, 100)
        found = as_list(page, "scorecardSummaries", "scorecards")
        cards.extend(found)
        if len(found) < 100:
            break
        offset += 100
    return {"golf": cards, "golf_clubs": await account.call("get_golf_club_stats")}


def parse_golf(db: Database, data: Any, start: date, end: date) -> int:
    events = []
    for card in as_list(data.get("golf")):
        key = card.get("id") or card.get("scorecardId")
        stamp = card.get("startTime") or card.get("startTimeLocal")
        if key:
            events.append((key, gmt_ts(stamp), day_of(card.get("startTimeLocal") or stamp), card))
    for name, value in data.items():
        if value is not None:
            db.put_snapshot(name, value)
    return db.put_events("golf", events)


def _snapshot_parse(db: Database, data: Any, start: date, end: date) -> int:
    stored = 0
    for name, value in (data or {}).items():
        if value is not None:
            db.put_snapshot(name, value)
            stored += 1
    return stored


# ----------------------------------------------------------------------
# Sleep in detail: stages through the night, and heart rate, HRV, SpO2 and breathing while asleep
# ----------------------------------------------------------------------
SLEEP_STAGES = {0: "deep", 1: "light", 2: "rem", 3: "awake"}


async def fetch_sleep_detail(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_sleep_data", iso(start))


def _night_points(samples: Any, start_ts: int, stamp_keys: tuple[str, ...], value_keys: tuple[str, ...]) -> list:
    """[[minutes after falling asleep, value], ...] from Garmin's per-sample lists, whatever they call the fields."""
    points = []
    for sample in samples or []:
        if not isinstance(sample, dict):
            continue
        stamp = next((gmt_ts(sample[k]) for k in stamp_keys if sample.get(k) is not None), None)
        value = next((number(sample[k]) for k in value_keys if number(sample.get(k)) is not None), None)
        if stamp is not None and value is not None and value >= 0:
            points.append([round((stamp - start_ts) / 60, 1), value])
    points.sort()
    return points


def parse_sleep_detail(db: Database, data: Any, start: date, end: date) -> int:
    summary = dig(data, "dailySleepDTO") or {}
    day = day_of(summary.get("calendarDate")) or iso(start)
    start_ts = gmt_ts(summary.get("sleepStartTimestampGMT"))
    if not start_ts:
        return 0
    stages = []
    for level in dig(data, "sleepLevels") or []:
        a, b = gmt_ts(level.get("startGMT")), gmt_ts(level.get("endGMT"))
        level_number = number(level.get("activityLevel"))
        stage = SLEEP_STAGES.get(int(level_number)) if level_number is not None else None
        if a is not None and b is not None and stage:
            stages.append([round((a - start_ts) / 60, 1), round((b - start_ts) / 60, 1), stage])
    night = {
        "start": start_ts,
        "end": gmt_ts(summary.get("sleepEndTimestampGMT")),
        # Garmin's "local" stamp is local time written as if it were UTC: the difference is the offset
        "offset_min": round(((gmt_ts(summary.get("sleepStartTimestampLocal")) or start_ts) - start_ts) / 60),
        "stages": stages,
        "hr": _night_points(data.get("sleepHeartRate"), start_ts, ("startGMT",), ("value",)),
        "hrv": _night_points(data.get("hrvData"), start_ts, ("startGMT",), ("value",)),
        "spo2": _night_points(
            data.get("wellnessEpochSPO2DataDTOList"), start_ts, ("epochTimestamp", "startGMT"), ("spo2Reading", "value")
        ),
        "resp": _night_points(
            data.get("wellnessEpochRespirationDataDTOList"),
            start_ts,
            ("startTimeGMT", "startGMT"),
            ("respirationValue", "value"),
        ),
        "stress": _night_points(data.get("sleepStress"), start_ts, ("startGMT",), ("value",)),
        "bb": _night_points(data.get("sleepBodyBattery"), start_ts, ("startGMT",), ("value",)),
        "restless": number(data.get("restlessMomentsCount")),
    }
    if not stages and not night["hr"]:
        return 0
    db.put_intraday("sleep", day, night)
    rows = {"restless_moments": night["restless"], "sleep_spo2_low": min((v for _, v in night["spo2"]), default=None)}
    return 1 + store(db, {day: rows})


# ----------------------------------------------------------------------
# More through the day: steps per 15 minutes and breathing
# ----------------------------------------------------------------------
async def fetch_day_more(account: GarminAccount, start: date, end: date) -> Any:
    return {
        "steps": await account.call("get_steps_data", iso(start)),
        "respiration": await account.call("get_respiration_data", iso(start)),
    }


def parse_day_more(db: Database, data: Any, start: date, end: date) -> int:
    day = iso(start)
    stored = 0
    blocks = [b for b in as_list(data.get("steps")) if isinstance(b, dict)]
    first = min((gmt_ts(b.get("startGMT")) for b in blocks if gmt_ts(b.get("startGMT"))), default=None)
    steps = []
    for block in blocks:
        stamp = gmt_ts(block.get("startGMT"))
        value = number(block.get("steps"))
        if stamp is not None and first is not None and value is not None:
            minute = int((stamp - first) / 60)
            if 0 <= minute < 1440:
                steps.append([minute, value])
    if any(v for _, v in steps):
        db.put_intraday("steps", day, sorted(steps))
        stored += 1
    breathing = data.get("respiration") or {}
    start_ms = (gmt_ts(breathing.get("startTimestampGMT")) or 0) * 1000
    if start_ms:
        points = _buckets(breathing.get("respirationValuesArray"), start_ms, 1)
        if points:
            db.put_intraday("resp", day, points)
            stored += 1
    return stored


# ----------------------------------------------------------------------
# Planning: workouts, training plans, the calendar and races
# ----------------------------------------------------------------------
async def fetch_planning(account: GarminAccount, start: date, end: date) -> Any:
    today = date.today()
    result = {}
    for name, method, args in (
        ("workouts", "get_workouts", (0, 100)),
        ("training_plans", "get_training_plans", ()),
        ("upcoming_events", "get_upcoming_events", (365, 20)),
        (
            "scheduled_workouts",
            "get_scheduled_workouts_by_period",
            (iso(today - timedelta(days=7)), iso(today + timedelta(days=60))),
        ),
    ):
        try:
            result[name] = await account.call(method, *args)
        except (RateLimited, NotSignedIn):
            raise
        except Exception as e:
            logger.debug(f"Planning: {method} failed: {e}")
    return result


async def fetch_races(account: GarminAccount, start: date, end: date) -> Any:
    return await account.call("get_calendar_events", iso(start), iso(end))


def parse_races(db: Database, data: Any, start: date, end: date) -> int:
    events = []
    for event in as_list(data, "events", "calendarEvents"):
        if not isinstance(event, dict):
            continue
        day = day_of(event.get("date") or event.get("eventDate") or event.get("startDate"))
        key = (
            event.get("id")
            or event.get("eventUuid")
            or event.get("uuid")
            or f"{day}-{event.get('title') or event.get('name')}"
        )
        if day:
            events.append((key, None, day, event))
    return db.put_events("race", events)


# ----------------------------------------------------------------------
# Activity details: laps, the curves through an activity, weather and strength sets
# (fetched per activity by the collector, like the tracks)
# ----------------------------------------------------------------------
DETAIL_CHANNELS = {
    "hr": ("directHeartRate",),
    "speed": ("directSpeed", "directEnhancedSpeed"),
    "elevation": ("directElevation", "directEnhancedElevation"),
    "power": ("directPower",),
    "cadence": ("directRunCadence", "directBikeCadence", "directDoubleCadence"),
    "distance": ("sumDistance",),
    "time": ("sumDuration", "sumElapsedDuration"),
}
MAX_DETAIL_POINTS = 600


def activity_curves(details: Any) -> dict:
    """The samples of an activity as rows per channel, thinned to MAX_DETAIL_POINTS."""
    if not isinstance(details, dict):
        return {}
    try:
        from garminconnect import parse_activity_detail_metrics

        samples = parse_activity_detail_metrics(details)
    except (ImportError, AttributeError, TypeError):
        return {}
    samples = thin(samples, MAX_DETAIL_POINTS)
    curves: dict[str, list] = {}
    for name, keys in DETAIL_CHANNELS.items():
        key = next((k for k in keys if any(number(s.get(k)) is not None for s in samples)), None)
        if key:
            curves[name] = [number(s.get(key)) for s in samples]
    return curves if "time" in curves or "distance" in curves else {}


def activity_laps(splits: Any) -> list[dict]:
    laps = []
    for lap in as_list(splits, "lapDTOs", "laps"):
        if isinstance(lap, dict):
            laps.append(
                {
                    "distance_m": number(lap.get("distance")),
                    "duration_s": number(lap.get("duration") or lap.get("elapsedDuration")),
                    "moving_s": number(lap.get("movingDuration")),
                    "avg_hr": number(lap.get("averageHR")),
                    "max_hr": number(lap.get("maxHR")),
                    "avg_speed": number(lap.get("averageSpeed")),
                    "climb_m": number(lap.get("elevationGain")),
                    "avg_power": number(lap.get("averagePower")),
                    "cadence": number(lap.get("averageRunCadence") or lap.get("averageBikeCadence")),
                }
            )
    return laps


def activity_sets(answer: Any) -> list[dict]:
    sets = []
    for entry in as_list(answer, "exerciseSets"):
        if not isinstance(entry, dict) or str(entry.get("setType") or "").upper() == "REST":
            continue
        exercises = entry.get("exercises") or []
        names = [
            str(e.get("name") or e.get("category") or "").replace("_", " ").lower()
            for e in exercises
            if isinstance(e, dict)
        ]
        sets.append(
            {
                "exercise": next((n for n in names if n), ""),
                "reps": number(entry.get("repetitionCount")),
                "weight": number(entry.get("weight")),
                "duration_s": number(entry.get("duration")),
            }
        )
    return sets


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------
_running_economy = _generic_range("get_running_economy_range", "running_economy")
_cycling_ability = _generic_range("get_cycling_ability_range", "cycling_ability")
_swim_speed = _generic_range("get_critical_swim_speed_range", "critical_swim_speed")

DATASETS: tuple[Dataset, ...] = (
    Dataset("summaries", "daily", "Daily summaries", "range", fetch_summaries, parse_summaries, chunk_days=120),
    Dataset("sleep", "sleep", "Sleep", "range", fetch_sleep, parse_sleep, chunk_days=28),
    Dataset("hrv", "sleep", "HRV", "range", fetch_hrv, parse_hrv, chunk_days=180),
    Dataset(
        "activities",
        "activities",
        "Activities",
        "range",
        fetch_activities,
        parse_activities,
        chunk_days=90,
        recent_days=7,
    ),
    Dataset("readiness", "training", "Training readiness", "range", fetch_readiness, parse_readiness, chunk_days=90),
    Dataset(
        "training_status",
        "training",
        "Training status and load",
        "range",
        fetch_training_status,
        parse_training_status,
        chunk_days=180,
    ),
    Dataset("vo2max", "training", "VO2 max", "range", fetch_vo2max, parse_vo2max, chunk_days=365, recent_days=7),
    Dataset("race", "training", "Race predictions", "range", fetch_race, parse_race, chunk_days=180),
    Dataset(
        "endurance",
        "training",
        "Endurance score",
        "range",
        fetch_endurance,
        parse_endurance,
        chunk_days=365,
        recent_days=14,
    ),
    Dataset("hill", "training", "Hill score", "range", fetch_hill, parse_hill, chunk_days=365, recent_days=7),
    Dataset("fitness_age", "training", "Fitness age", "range", fetch_fitness_age, parse_fitness_age, chunk_days=28),
    Dataset(
        "acclimation",
        "training",
        "Heat and altitude acclimation",
        "range",
        fetch_acclimation,
        parse_acclimation,
        chunk_days=365,
    ),
    Dataset(
        "weight",
        "body",
        "Weight and body composition",
        "range",
        fetch_weight,
        parse_weight,
        chunk_days=730,
        recent_days=7,
    ),
    Dataset(
        "blood_pressure",
        "body",
        "Blood pressure",
        "range",
        fetch_blood_pressure,
        parse_blood_pressure,
        chunk_days=730,
        recent_days=7,
    ),
    Dataset(
        "records", "records", "Records, badges, gear, devices", "snapshot", fetch_records, parse_records, every_hours=6
    ),
    Dataset("ecg", "health_monitor", "ECG", "range", fetch_ecg, parse_ecg, chunk_days=180, recent_days=7),
    Dataset(
        "health_snapshots",
        "health_monitor",
        "Health Snapshot",
        "range",
        fetch_health_snapshots,
        parse_health_snapshots,
        chunk_days=180,
        recent_days=7,
    ),
    Dataset(
        "glucose", "health_monitor", "Glucose", "range", fetch_glucose, parse_glucose, chunk_days=90, recent_days=7
    ),
    Dataset(
        "jetlag", "health_monitor", "Jet Lag Adviser trips", "snapshot", fetch_jetlag, _snapshot_parse, every_hours=12
    ),
    Dataset("lifestyle", "health_monitor", "Lifestyle logging", "day", fetch_lifestyle, parse_lifestyle, recent_days=2),
    Dataset(
        "running_economy", "performance", "Running economy", "range", *_running_economy, chunk_days=30, recent_days=7
    ),
    Dataset(
        "cycling_ability", "performance", "Cycling ability", "range", *_cycling_ability, chunk_days=30, recent_days=7
    ),
    Dataset("swim_speed", "performance", "Critical swim speed", "range", *_swim_speed, chunk_days=30, recent_days=7),
    Dataset(
        "thresholds",
        "performance",
        "FTP, power and heart rate zones",
        "snapshot",
        fetch_thresholds,
        _snapshot_parse,
        every_hours=24,
    ),
    Dataset("nutrition", "nutrition", "Nutrition (Connect+)", "range", fetch_nutrition, parse_nutrition, chunk_days=31),
    Dataset(
        "menstrual",
        "menstrual",
        "Menstrual cycle",
        "range",
        fetch_menstrual,
        parse_menstrual,
        chunk_days=90,
        recent_days=14,
    ),
    Dataset("golf", "golf", "Golf scorecards", "snapshot", fetch_golf, parse_golf, every_hours=12),
    Dataset(
        "sleep_detail",
        "sleep_detail",
        "Sleep stages and readings through the night",
        "day",
        fetch_sleep_detail,
        parse_sleep_detail,
        recent_days=2,
    ),
    Dataset(
        "planning",
        "planning",
        "Workouts, training plans and calendar",
        "snapshot",
        fetch_planning,
        _snapshot_parse,
        every_hours=6,
    ),
    Dataset("races", "planning", "Races and events", "range", fetch_races, parse_races, chunk_days=365, recent_days=1),
    Dataset(
        "day_more",
        "intraday_more",
        "Steps per 15 minutes and breathing through the day",
        "day",
        fetch_day_more,
        parse_day_more,
        recent_days=2,
        keep_raw=False,
    ),
    # Last: two requests for every day of the history
    Dataset(
        "day_curves",
        "intraday",
        "Heart rate, stress and Body Battery through the day",
        "day",
        fetch_day_curves,
        parse_day_curves,
        recent_days=2,
        keep_raw=False,
    ),
)
DATASET_BY_KEY = {d.key: d for d in DATASETS}
GROUP_TITLES = {
    "daily": "Daily summaries",
    "sleep": "Sleep and HRV",
    "activities": "Activities",
    "activity_tracks": "Activity tracks (GPS)",
    "training": "Training",
    "body": "Body composition and blood pressure",
    "intraday": "Day curves",
    "records": "Records, badges, gear",
    "health_monitor": "Health monitoring (ECG, Health Snapshot, glucose, jet lag, lifestyle)",
    "performance": "Performance metrics",
    "nutrition": "Nutrition (Connect+)",
    "menstrual": "Menstrual cycle",
    "golf": "Golf",
    "activity_details": "Activity details (laps, curves, weather, strength sets)",
    "sleep_detail": "Sleep in detail",
    "intraday_more": "Steps and breathing through the day",
    "planning": "Workouts, training plans and races",
}


def chunks_back(newest: date, oldest: date, chunk_days: int) -> tuple[date, date]:
    """The next piece of history, walking back from `newest` but not past `oldest`."""
    start = max(oldest, newest - timedelta(days=chunk_days - 1))
    return start, newest
