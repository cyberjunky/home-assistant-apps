"""Statistics for the dashboard, worked out from the database.

Meters are counters, so what was used is the difference between two readings. A counter that drops
is a replaced or reset meter, and nothing is known about use across a long gap: both give no value,
never a lump. All of this runs in a worker thread with its own read-only connection, because a long
period takes a moment on small hardware and the connection to the Toon must not wait for it.
"""

import csv
import io
import sqlite3
import statistics
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .database import NO_ZERO_READINGS
from .history import ARCHIVES

DAY = 86400
HOUR = 3600
MAX_COUNTER_GAP = 3 * DAY
# Samples are stored on change and at least every 15 minutes; longer means nothing is known
MAX_STATE_GAP = 1800
DELTA_CACHE_SECONDS = 300
TODAY_CACHE_SECONDS = 20
HEATMAP_DAYS = 365
BUCKETS = ("day", "week", "month", "year")

# burnerInfo: 0 off, 1 heating, 2 hot water, 3 preheating, 4 error
BURNING = {1: "heat", 2: "dhw", 3: "preheat"}
HEATING = ("heat", "preheat")
SHORT_CYCLE_SECONDS = 600
# Below about this return temperature a condensing boiler can condense its flue gas
CONDENSING_RETURN = 55.0
# A rise of the day's lowest pressure this big, sustained for some days, is water added, not warmth
TOP_UP_RISE = 0.3
TOP_UP_DAYS = 3
LOSS_MIN_DAYS = 14
NIGHT_HOURS = (2, 3, 4)

COUNTERS = {
    "gas": "gas_total",
    "import_low": "energy_import_low",
    "import_normal": "energy_import_normal",
    "export_low": "energy_export_low",
    "export_normal": "energy_export_normal",
    "burner_hours": "burner_hours",
    "dhw_hours": "dhw_burner_hours",
    "burner_minutes": "burner_minutes",
    "failed_starts": "failed_burner_starts",
}
# Measured values with their lowest, average and highest. A later source, such as sensors on the
# pipes, only has to be added here and in the dashboard's chart definitions.
GAUGES = (
    "room_temperature",
    "setpoint",
    "boiler_temperature",
    "boiler_return_temperature",
    "boiler_setpoint",
    "dhw_setpoint",
    "max_heater_temperature",
    "modulation",
    "boiler_pressure",
    "power_import",
    "power_export",
    "gas_flow",
)
MODULATION_BINS = ((0, 0, "0%"), (1, 25, "1-25%"), (26, 50, "26-50%"), (51, 75, "51-75%"), (76, 100, "76-100%"))
Point = tuple[int, float]


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts)


def day_key(ts: float) -> str:
    return local(ts).date().isoformat()


def midnight(ts: float) -> int:
    return int(local(ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def bucket_key(day: str, bucket: str) -> str:
    """The bucket a day (YYYY-MM-DD) falls in. A week is named after its Monday."""
    if bucket == "year":
        return day[:4]
    if bucket == "month":
        return day[:7]
    if bucket == "week":
        d = date.fromisoformat(day)
        return (d - timedelta(days=d.weekday())).isoformat()
    return day


def year_before(day: str) -> str:
    d = date.fromisoformat(day)
    try:
        return d.replace(year=d.year - 1).isoformat()
    except ValueError:
        return d.replace(year=d.year - 1, day=28).isoformat()


def counter_deltas(points: list[Point], max_gap: int = MAX_COUNTER_GAP) -> list[tuple[int, float, int]]:
    """Use per interval between readings: (middle of the interval, amount, length of the interval).

    The middle puts a daily value on the day it covers: the Toon stamps a bin with its end.
    """
    deltas = []
    previous = None
    for ts, value in points:
        if previous is not None:
            span = ts - previous[0]
            amount = value - previous[1]
            if 0 < span <= max_gap and amount >= 0:
                deltas.append(((ts + previous[0]) // 2, amount, span))
        previous = (ts, value)
    return deltas


def sum_by_day(deltas: list[tuple[int, float, int]]) -> dict[str, float]:
    """Use per local day. An interval of several days is shared out over those days."""
    days: dict[str, float] = defaultdict(float)
    for middle, amount, span in deltas:
        parts = max(1, round(span / DAY))
        for part in range(parts):
            at = middle - span // 2 + span * (2 * part + 1) // (2 * parts)
            days[day_key(at)] += amount / parts
    return dict(days)


def usage_between(points: list[Point], start: int, end: int) -> float | None:
    """Use between two moments from readings around them; None without any reading in the period."""
    deltas = [(ts, value) for ts, value in points if ts <= end]
    total = None
    previous = None
    for ts, value in deltas:
        if previous is not None and ts > start and 0 < ts - previous[0] <= MAX_COUNTER_GAP:
            total = (total or 0.0) + max(0.0, value - previous[1])
        previous = (ts, value)
    return total


def burner_segments(codes: list[Point], end: int, max_gap: int = MAX_STATE_GAP) -> list[tuple[int, int, str]]:
    """Periods the burner was on: (start, end, "heat" or "dhw"), joined when they follow each other."""
    segments: list[tuple[int, int, str]] = []
    for i, (ts, code) in enumerate(codes):
        following = codes[i + 1][0] if i + 1 < len(codes) else end
        kind = BURNING.get(int(code))
        if not kind or following <= ts:
            continue
        stop = min(following, ts + max_gap)
        if segments and segments[-1][1] == ts and segments[-1][2] == kind:
            segments[-1] = (segments[-1][0], stop, kind)
        else:
            segments.append((ts, stop, kind))
    return segments


def burner_runs(segments: list[tuple[int, int, str]]) -> list[tuple[int, int]]:
    """Uninterrupted burning: from a burner start until it goes off, heating and hot water together."""
    runs: list[tuple[int, int]] = []
    for start, stop, _ in segments:
        if runs and runs[-1][1] == start:
            runs[-1] = (runs[-1][0], stop)
        else:
            runs.append((start, stop))
    return runs


def cycle_summary(runs: list[tuple[int, int]]) -> dict:
    lengths = [stop - start for start, stop in runs]
    return {
        "starts": len(runs),
        "average_minutes": round(sum(lengths) / len(lengths) / 60, 1) if lengths else None,
        "short": sum(1 for length in lengths if length < SHORT_CYCLE_SECONDS),
        "longest_minutes": round(max(lengths) / 60, 1) if lengths else None,
    }


def weighted_while(
    segments: list[tuple[int, int, str]],
    series: list[Point],
    kinds: tuple[str, ...] = HEATING,
    max_age: int = MAX_STATE_GAP,
) -> list[tuple[int, int, float]]:
    """A value held between its readings, during the burner's periods of a kind: (moment, seconds, value)."""
    pieces = []
    i = 0
    for start, stop, segment_kind in segments:
        if segment_kind not in kinds:
            continue
        while i + 1 < len(series) and series[i + 1][0] <= start:
            i += 1
        j = i
        at = start
        while at < stop and j < len(series):
            ts, value = series[j]
            following = series[j + 1][0] if j + 1 < len(series) else stop
            until = min(stop, following)
            if ts <= at and at - ts <= max_age and until > at:
                pieces.append((at, until - at, value))
            at = max(at, until)
            j += 1
    return pieces


def modulation_distribution(pieces: list[tuple[int, int, float]]) -> list[dict]:
    """How long the burner ran at which modulation."""
    rows = []
    for low, high, label in MODULATION_BINS:
        seconds = sum(piece[1] for piece in pieces if low <= piece[2] <= high)
        rows.append({"label": label, "minutes": round(seconds / 60, 1)})
    return rows


def pressure_summary(daily_min: dict[str, float], today: str | None = None) -> dict:
    """Water pressure over time: its change, the last top-up and the loss since then.

    The Toon reads the pressure in steps of about 0.1 bar and it moves with the temperature, so all
    of this looks at several days together.
    """
    days = sorted(daily_min)
    empty = {"change_30d": None, "top_ups": [], "last_top_up": None, "loss_per_week": None}
    if not days:
        return empty
    today = today or date.today().isoformat()
    lows = [daily_min[day] for day in days]
    top_ups = []
    best = None
    for i in range(TOP_UP_DAYS, len(days) - TOP_UP_DAYS + 1):
        rise = statistics.mean(lows[i : i + TOP_UP_DAYS]) - statistics.mean(lows[i - TOP_UP_DAYS : i])
        if rise >= TOP_UP_RISE:
            # Neighbouring days see the same top-up; it is where the rise is largest
            if best is None or rise > best[0]:
                best = (rise, days[i])
        elif best:
            top_ups.append(best[1])
            best = None
    if best:
        top_ups.append(best[1])

    month_ago = (date.fromisoformat(today) - timedelta(days=30)).isoformat()
    earlier = [daily_min[day] for day in days if day <= month_ago]
    # With less than 30 days of history this compares with the first days there are
    if not earlier and (date.fromisoformat(today) - date.fromisoformat(days[0])).days >= 14:
        earlier = lows[:3]
    change = None
    if earlier:
        change = round(statistics.mean(lows[-3:]) - statistics.mean(earlier[-3:]), 2) + 0.0

    since = top_ups[-1] if top_ups else days[0]
    rest = [day for day in days if day >= since]
    loss = None
    if (date.fromisoformat(rest[-1]) - date.fromisoformat(rest[0])).days >= LOSS_MIN_DAYS:
        ordinals = [date.fromisoformat(day).toordinal() for day in rest]
        slope = statistics.linear_regression(ordinals, [daily_min[day] for day in rest]).slope
        loss = round(-slope * 7, 2) + 0.0
    return {
        "change_30d": change,
        "top_ups": top_ups,
        "last_top_up": top_ups[-1] if top_ups else None,
        "loss_per_week": loss,
    }


def night_base_load(low: list, normal: list) -> dict[str, float]:
    """Average power in the small hours per day, in W: what the house uses when nobody uses anything."""
    hours: dict[str, float] = defaultdict(float)
    energy: dict[str, float] = defaultdict(float)
    for middle, amount, span in low:
        if span <= 2 * HOUR and local(middle).hour in NIGHT_HOURS:
            day = day_key(middle)
            hours[day] += span / HOUR
            energy[day] += amount
    for middle, amount, span in normal:
        if span <= 2 * HOUR and local(middle).hour in NIGHT_HOURS:
            energy[day_key(middle)] += amount
    return {day: round(energy[day] * 1000 / hours[day]) for day in hours if hours[day] >= 2}


def gas_heatmap(deltas: list[tuple[int, float, int]], start: int, end: int) -> list[list[float | None]]:
    """Average gas per hour (m³) for each weekday (Monday first) and hour of the day."""
    totals = [[0.0] * 24 for _ in range(7)]
    counts = [[0] * 24 for _ in range(7)]
    for middle, amount, span in deltas:
        if start <= middle < end and span <= 2 * HOUR:
            moment = local(middle)
            totals[moment.weekday()][moment.hour] += amount * HOUR / span
            counts[moment.weekday()][moment.hour] += 1
    return [[round(totals[d][h] / counts[d][h], 4) if counts[d][h] else None for h in range(24)] for d in range(7)]


@dataclass
class _Cached:
    at: float
    value: object


class Stats:
    """Answers the dashboard's questions about the past. Call from a worker thread."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._deltas: dict[str, _Cached] = {}
        self._today: _Cached | None = None

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5)

    @staticmethod
    def _rollup_start(db: sqlite3.Connection, metric: str) -> int:
        """Hourly values from this moment on are this app's own, the ones before it the Toon's."""
        for archive in ARCHIVES:
            if archive.metric == metric and archive.bin_seconds == HOUR:
                row = db.execute(
                    "SELECT next_ts FROM import_progress WHERE logger = ? AND rra = ?", (archive.logger, archive.rra)
                ).fetchone()
                return row[0] if row else 0
        return 0

    def _points(self, db: sqlite3.Connection, metric: str, start: int, end: int, fine: bool = False) -> list[Point]:
        """A counter's readings. Hourly values hold the reading at the end of their hour.

        fine uses every sample within the period, otherwise samples only fill in after the last hour.
        """
        cut = self._rollup_start(db, metric)
        rows = db.execute(
            "SELECT ts, last FROM hourly WHERE metric = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (metric, start - HOUR, end),
        ).fetchall()
        hourly = [(ts + HOUR if ts >= cut else ts, value) for ts, value in rows]
        hourly = [(ts, value) for ts, value in hourly if ts <= end]
        samples = db.execute(
            "SELECT ts, value FROM samples WHERE metric = ? AND ts >= ? AND ts <= ? ORDER BY ts", (metric, start, end)
        ).fetchall()
        if fine and samples:
            first = samples[0][0]
            return [p for p in hourly if p[0] < first] + samples
        last = hourly[-1][0] if hourly else 0
        return hourly + [p for p in samples if p[0] > last]

    def _all_deltas(self, db: sqlite3.Connection, metric: str, now: int) -> list[tuple[int, float, int]]:
        with self._lock:
            cached = self._deltas.get(metric)
            if cached and now - cached.at < DELTA_CACHE_SECONDS:
                return cached.value
        deltas = counter_deltas(self._points(db, metric, 0, now))
        with self._lock:
            self._deltas[metric] = _Cached(now, deltas)
        return deltas

    @staticmethod
    def _samples(db: sqlite3.Connection, metric: str, start: int, end: int) -> list[Point]:
        """A state's samples in a period, with the one before it."""
        before = db.execute(
            "SELECT ts, value FROM samples WHERE metric = ? AND ts < ? ORDER BY ts DESC LIMIT 1", (metric, start)
        ).fetchone()
        rows = db.execute(
            "SELECT ts, value FROM samples WHERE metric = ? AND ts >= ? AND ts <= ? ORDER BY ts", (metric, start, end)
        ).fetchall()
        return ([before] if before else []) + rows

    @staticmethod
    def _daily_gauge(db: sqlite3.Connection, metric: str, start: int, end: int) -> dict[str, dict]:
        """Lowest, highest and average per local day of a measured value, from the hourly values."""
        days: dict[str, dict] = {}
        rows = db.execute(
            "SELECT ts, avg, min, max, last FROM hourly WHERE metric = ? AND ts >= ? AND ts < ? ORDER BY ts",
            (metric, start, end),
        )
        for ts, avg, low, high, last in rows:
            # The boiler briefly reports a pressure of 0 now and then; that isn't a measurement
            if metric in NO_ZERO_READINGS and low <= 0:
                avg = low = last
                if last <= 0:
                    continue
            day = days.setdefault(day_key(ts), {"sum": 0.0, "n": 0, "min": low, "max": high})
            day["sum"] += avg
            day["n"] += 1
            day["min"] = min(day["min"], low)
            day["max"] = max(day["max"], high)
        return days

    def _burner(self, db: sqlite3.Connection, start: int, end: int) -> tuple[list, list, str | None]:
        codes = self._samples(db, "burner_code", start, end)
        first = db.execute("SELECT MIN(ts) FROM samples WHERE metric = 'burner_code'").fetchone()[0]
        segments = [(max(a, start), b, kind) for a, b, kind in burner_segments(codes, end) if b > start]
        return segments, burner_runs(segments), day_key(first) if first else None

    def _temperatures(self, db: sqlite3.Connection, segments: list, start: int, end: int) -> dict[str, list]:
        return {
            name: weighted_while(segments, self._samples(db, metric, start, end))
            for name, metric in (
                ("return", "boiler_return_temperature"),
                ("supply", "boiler_temperature"),
                ("setpoint", "boiler_setpoint"),
                ("modulation", "modulation"),
            )
        }

    def today(self, now: int | None = None) -> dict:
        """What the dashboard shows of today."""
        now = now or int(time.time())
        with self._lock:
            if self._today and now - self._today.at < TODAY_CACHE_SECONDS:
                return self._today.value
        result = self._work_out_today(now)
        with self._lock:
            self._today = _Cached(now, result)
        return result

    def _work_out_today(self, now: int) -> dict:
        db = self._connect()
        try:
            start = midnight(now)
            yesterday = midnight(start - HOUR)
            usage = {"today": {}, "yesterday": {}}
            points = {}
            for name in ("gas", "import_low", "import_normal", "export_low", "export_normal"):
                points[name] = self._points(db, COUNTERS[name], yesterday - 6 * HOUR, now, fine=True)
                usage["today"][name] = usage_between(points[name], start, now)
                usage["yesterday"][name] = usage_between(points[name], yesterday, start)

            hour_now = now // HOUR * HOUR
            gas_hours = [{"ts": hour_now - HOUR * i, "gas": 0.0} for i in range(23, -1, -1)]
            by_ts = {row["ts"]: row for row in gas_hours}
            known = False
            for ts, amount, span in counter_deltas(points["gas"]):
                if span <= 2 * HOUR:
                    row = by_ts.get(ts // HOUR * HOUR)
                    if row:
                        row["gas"] = round(row["gas"] + amount, 3)
                        known = True

            segments, runs, since = self._burner(db, start, now)
            seconds = {"heat": 0, "preheat": 0, "dhw": 0}
            for a, b, kind in segments:
                seconds[kind] += b - a
            temperatures = self._temperatures(db, segments, start, now)

            month_ago = midnight(now - 31 * DAY)
            pressure = self._daily_gauge(db, "boiler_pressure", month_ago, now)
            low = counter_deltas(self._points(db, COUNTERS["import_low"], now - 9 * DAY, now))
            normal = counter_deltas(self._points(db, COUNTERS["import_normal"], now - 9 * DAY, now))
            base = night_base_load(low, normal)
            return {
                "usage": usage,
                "gas_hours": gas_hours if known else [],
                "burner": {
                    "heat_minutes": round((seconds["heat"] + seconds["preheat"]) / 60),
                    "dhw_minutes": round(seconds["dhw"] / 60),
                    "collecting_since": since,
                    **cycle_summary(runs),
                },
                "return_average": _average(temperatures["return"]),
                "supply_average": _average(temperatures["supply"]),
                "pressure": pressure_summary({d: v["min"] for d, v in pressure.items()}, day_key(now)),
                "base_load": statistics.median(base.values()) if base else None,
            }
        finally:
            db.close()

    def raw_csv(self, metrics: list[str], first: str, last: str, hourly: bool = False) -> str:
        """The stored values of a period, one per line: every sample, or the hourly values."""
        start = int(datetime.fromisoformat(first).timestamp())
        end = int((datetime.fromisoformat(last) + timedelta(days=1)).timestamp())
        marks = ",".join("?" * len(metrics))
        query = (
            f"SELECT ts, metric, avg, min, max, last FROM hourly WHERE metric IN ({marks}) AND ts >= ? AND ts < ? ORDER BY ts, metric"
            if hourly
            else f"SELECT ts, metric, value FROM samples WHERE metric IN ({marks}) AND ts >= ? AND ts < ? ORDER BY ts, metric"
        )
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["time", "timestamp", "metric", *(("avg", "min", "max", "last") if hourly else ("value",))])
        db = self._connect()
        try:
            for ts, *rest in db.execute(query, (*metrics, start, end)):
                writer.writerow([local(ts).isoformat(), ts, *rest])
        finally:
            db.close()
        return out.getvalue()

    def report(self, first: str, last: str, bucket: str, now: int | None = None) -> dict:
        """Everything of the Stats page for the days first up to and including last (YYYY-MM-DD)."""
        now = now or int(time.time())
        db = self._connect()
        try:
            return self._report(db, first, last, bucket, now)
        finally:
            db.close()

    def _report(self, db: sqlite3.Connection, first: str, last: str, bucket: str, now: int) -> dict:
        today = day_key(now)
        last = min(last, today)
        deltas = {name: self._all_deltas(db, metric, now) for name, metric in COUNTERS.items()}
        daily = {name: sum_by_day(rows) for name, rows in deltas.items()}
        with_data = [day for rows in daily.values() for day in rows]
        data_from = min(with_data) if with_data else None
        first = max(first, data_from) if data_from else first
        start = int(datetime.fromisoformat(first).timestamp())
        end = min(now, int((datetime.fromisoformat(last) + timedelta(days=1)).timestamp()))

        days = []
        at = date.fromisoformat(first)
        while at.isoformat() <= last:
            days.append(at.isoformat())
            at += timedelta(days=1)

        room = self._daily_gauge(db, "room_temperature", start, end)
        setpoint = self._daily_gauge(db, "setpoint", start, end)
        pressure = self._daily_gauge(db, "boiler_pressure", start, end)
        power = self._daily_gauge(db, "power_import", start, end)
        base = night_base_load(deltas["import_low"], deltas["import_normal"])
        segments, runs, collecting = self._burner(db, start, end)
        temperatures = self._temperatures(db, segments, start, end)

        order: list[str] = []
        rows: dict[str, dict] = {}
        for day in days:
            key = bucket_key(day, bucket)
            if key not in rows:
                order.append(key)
                rows[key] = {"key": key, "days": []}
            rows[key]["days"].append(day)

        per_day_runs: dict[str, list] = defaultdict(list)
        for run in runs:
            per_day_runs[day_key(run[0])].append(run)
        per_day_temperature: dict[str, dict[str, list]] = defaultdict(
            lambda: {"return": [], "supply": [], "setpoint": []}
        )
        for name in ("return", "supply", "setpoint"):
            for piece in temperatures[name]:
                per_day_temperature[day_key(piece[0])][name].append(piece)
        per_day_seconds: dict[str, dict[str, int]] = defaultdict(lambda: {"heat": 0, "preheat": 0, "dhw": 0})
        for a, b, kind in segments:
            per_day_seconds[day_key(a)][kind] += b - a
        gauges = {metric: self._daily_gauge(db, metric, start, end) for metric in GAUGES}

        buckets = []
        for key in order:
            span = rows[key]["days"]
            row = {"key": key}
            for name in COUNTERS:
                values = [daily[name][d] for d in span if d in daily[name]]
                row[name] = round(sum(values), 3) if values else None
            previous = [daily["gas"][year_before(d)] for d in span if year_before(d) in daily["gas"]]
            row["gas_year_before"] = round(sum(previous), 3) if len(previous) >= len(span) * 0.8 else None
            row["room"] = _gauge_average(room, span)
            row["setpoint"] = _gauge_average(setpoint, span)
            lows = [pressure[d]["min"] for d in span if d in pressure]
            row["pressure_min"] = round(min(lows), 2) if lows else None
            loads = [base[d] for d in span if d in base]
            row["base_load"] = round(statistics.mean(loads)) if loads else None
            bucket_runs = [run for d in span for run in per_day_runs.get(d, [])]
            summary = cycle_summary(bucket_runs)
            row["starts"] = summary["starts"] if collecting and span[-1] >= collecting else None
            row["average_cycle"] = summary["average_minutes"]
            row["short_cycles"] = summary["short"] if row["starts"] is not None else None
            piece_of = lambda name, span=span: [p for d in span for p in per_day_temperature.get(d, {}).get(name, [])]  # noqa: E731
            row["return_while_burning"] = _average(piece_of("return"))
            row["supply_while_burning"] = _average(piece_of("supply"))
            row["setpoint_while_burning"] = _average(piece_of("setpoint"))
            on_time = {
                kind: sum(per_day_seconds[d][kind] for d in span if d in per_day_seconds)
                for kind in ("heat", "preheat", "dhw")
            }
            for kind, seconds in on_time.items():
                row[f"{kind}_minutes"] = round(seconds / 60, 1) if row["starts"] is not None else None
            row["gauges"] = {metric: _gauge_summary(days_of, span) for metric, days_of in gauges.items()}
            buckets.append(row)

        total = lambda names, span=days: _sum_daily(daily, names, span)  # noqa: E731
        year_days = [year_before(d) for d in days]
        have_year = sum(1 for d in year_days if d in daily["gas"]) >= len(days) * 0.8
        elapsed = sum(b - a for a, b, _ in segments)
        heat = [p for p in temperatures["return"]]
        below = sum(seconds for _, seconds, value in heat if value < CONDENSING_RETURN)
        heat_seconds = sum(seconds for _, seconds, _ in heat)
        heatmap_start = max(start, now - HEATMAP_DAYS * DAY)
        recent_base = [base[d] for d in sorted(base) if d <= last][-7:]
        return {
            "first": first,
            "last": last,
            "bucket": bucket,
            "buckets": buckets,
            "data_from": data_from,
            "totals": {
                "gas": total(["gas"]),
                "gas_year_before": total(["gas"], year_days) if have_year else None,
                "import": total(["import_low", "import_normal"]),
                "export": total(["export_low", "export_normal"]),
                "burner_hours": total(["burner_hours"]),
                "dhw_hours": total(["dhw_hours"]),
                "burner_minutes": total(["burner_minutes"]),
                "failed_starts": total(["failed_starts"]),
            },
            "gauges": {metric: _gauge_summary(days_of, days) for metric, days_of in gauges.items()},
            "base_load": statistics.median(recent_base) if recent_base else None,
            "heatmap": gas_heatmap(deltas["gas"], heatmap_start, end),
            "cycles": {
                "collecting_since": collecting,
                "burning_hours": round(elapsed / HOUR, 1),
                "return_average": _average(heat),
                "supply_average": _average(temperatures["supply"]),
                "setpoint_average": _average(temperatures["setpoint"]),
                "modulation": modulation_distribution(temperatures["modulation"]),
                "condensing_share": round(below / heat_seconds * 100) if heat_seconds else None,
                **cycle_summary(runs),
            },
            "pressure": pressure_summary(
                {d: v["min"] for d, v in self._daily_gauge(db, "boiler_pressure", now - 400 * DAY, now).items()}, today
            ),
            "records": _records(daily, days, room, pressure, power, runs),
        }


CSV_SKIP = ("key", "gauges")


def buckets_csv(report: dict) -> str:
    """The per-period table of a report as CSV; measured values get _min, _avg and _max columns."""
    out = io.StringIO()
    writer = csv.writer(out)
    rows = report["buckets"]
    columns = [name for name in (rows[0] if rows else {}) if name not in CSV_SKIP]
    writer.writerow(["period", *columns, *(f"{m}_{part}" for m in GAUGES for part in ("min", "avg", "max"))])
    for row in rows:
        gauges = [(row["gauges"].get(m) or {}).get(part) for m in GAUGES for part in ("min", "avg", "max")]
        writer.writerow([row["key"], *(row[name] for name in columns), *gauges])
    return out.getvalue()


def _average(pieces: list[tuple[int, int, float]]) -> float | None:
    seconds = sum(piece[1] for piece in pieces)
    return round(sum(piece[1] * piece[2] for piece in pieces) / seconds, 1) if seconds else None


def _gauge_average(days: dict[str, dict], span: list[str]) -> float | None:
    used = [days[d] for d in span if d in days]
    count = sum(day["n"] for day in used)
    return round(sum(day["sum"] for day in used) / count, 2) if count else None


def _gauge_summary(days: dict[str, dict], span: list[str]) -> dict | None:
    """Lowest, average and highest of a measured value over some days."""
    used = [days[d] for d in span if d in days]
    if not used:
        return None
    return {
        "min": round(min(day["min"] for day in used), 2),
        "avg": _gauge_average(days, span),
        "max": round(max(day["max"] for day in used), 2),
    }


def _sum_daily(daily: dict[str, dict[str, float]], names: list[str], span: list[str]) -> float | None:
    values = [daily[name][d] for name in names for d in span if d in daily[name]]
    return round(sum(values), 3) if values else None


def _records(daily: dict, days: list[str], room: dict, pressure: dict, power: dict, runs: list) -> list[dict]:
    """The extremes of the period."""
    records = []

    def highest(label: str, values: dict[str, float], unit: str) -> None:
        inside = {d: values[d] for d in days if d in values and values[d] > 0}
        if inside:
            day = max(inside, key=inside.get)
            records.append({"label": label, "value": round(inside[day], 2), "unit": unit, "day": day})

    highest("Most gas in a day", daily["gas"], "m³")
    combined_import = defaultdict(float)
    combined_export = defaultdict(float)
    for name in ("import_low", "import_normal"):
        for day, amount in daily[name].items():
            combined_import[day] += amount
    for name in ("export_low", "export_normal"):
        for day, amount in daily[name].items():
            combined_export[day] += amount
    highest("Most electricity from the grid", combined_import, "kWh")
    highest("Most electricity to the grid", combined_export, "kWh")
    if power:
        day = max(power, key=lambda d: power[d]["max"])
        records.append(
            {"label": "Highest power from the grid", "value": round(power[day]["max"]), "unit": "W", "day": day}
        )
    if room:
        day = min(room, key=lambda d: room[d]["min"])
        records.append({"label": "Coldest room", "value": round(room[day]["min"], 1), "unit": "°C", "day": day})
        day = max(room, key=lambda d: room[d]["max"])
        records.append({"label": "Warmest room", "value": round(room[day]["max"], 1), "unit": "°C", "day": day})
    if pressure:
        day = min(pressure, key=lambda d: pressure[d]["min"])
        records.append({"label": "Lowest pressure", "value": round(pressure[day]["min"], 2), "unit": "bar", "day": day})
    if runs:
        longest = max(runs, key=lambda run: run[1] - run[0])
        records.append(
            {
                "label": "Longest burner run",
                "value": round((longest[1] - longest[0]) / 60),
                "unit": "min",
                "day": day_key(longest[0]),
            }
        )
    return records
