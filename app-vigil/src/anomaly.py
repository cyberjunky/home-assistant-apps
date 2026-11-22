"""Anomaly bands for metric series: what a value usually is at this time, and what is far from it.

For each time bucket the band is the mean +/- 3 standard deviations of comparable earlier values:
  - with (about) a week of history: the values at the same hour of the day over the previous
    7 days (a router that is busy every evening is not anomalous every evening);
  - with less: the values of the preceding hour (a rolling window).
A point outside its band is an anomaly. Plain Python: the series are a few thousand points.
"""

import math
from datetime import datetime

SIGMAS = 3.0
WEEK = 7 * 86400
ROLLING_WINDOW = 3600
# Seasonal bands need most of a week of history; otherwise the hour-of-day groups are too thin
SEASONAL_MIN_SPAN = 6 * 86400
MIN_SAMPLES = 5


def mean_std(values: list[float]) -> tuple[float, float] | None:
    """Mean and (sample) standard deviation; None with fewer than MIN_SAMPLES values."""
    n = len(values)
    if n < MIN_SAMPLES:
        return None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance)


def _floor_std(mean: float, std: float) -> float:
    # A perfectly flat history would make any change infinitely anomalous; allow a little noise
    return max(std, abs(mean) * 0.001, 1e-9)


def hour_of_day(t: float) -> int:
    """The local hour (the TZ of Home Assistant): daily rhythms follow the clock on the wall."""
    return datetime.fromtimestamp(t).hour


def seasonal(history: list[tuple[float, float]], t0: float) -> bool:
    """Whether the history (time, value) reaches far enough back before t0 for hour-of-day bands."""
    return bool(history) and t0 - min(t for t, _ in history) >= SEASONAL_MIN_SPAN


def band(
    t: list[float], values: list[float | None], history: list[tuple[float, float]]
) -> tuple[list[float | None], list[float | None], list[int]]:
    """(lo, hi, anomalies) for the buckets t with values; history = (time, value) before t[0].

    lo/hi are None where there is too little to compare with; anomalies are indexes into t.
    """
    lo: list[float | None] = [None] * len(t)
    hi: list[float | None] = [None] * len(t)
    anomalies: list[int] = []
    if not t:
        return lo, hi, anomalies
    if seasonal(history, t[0]):
        by_hour: dict[int, list[tuple[float, float]]] = {}
        for ht, hv in history:
            by_hour.setdefault(hour_of_day(ht), []).append((ht, hv))
        for i, ti in enumerate(t):
            samples = [v for ht, v in by_hour.get(hour_of_day(ti), ()) if ti - WEEK <= ht < ti]
            _fill(i, samples, values, lo, hi, anomalies)
        return lo, hi, anomalies
    # Rolling: the hour before each bucket, from the history and the window itself
    points = sorted(history) + [(ti, v) for ti, v in zip(t, values, strict=True) if v is not None]
    start = 0
    for i, ti in enumerate(t):
        while start < len(points) and points[start][0] < ti - ROLLING_WINDOW:
            start += 1
        samples = []
        for pt, pv in points[start:]:
            if pt >= ti:
                break
            samples.append(pv)
        _fill(i, samples, values, lo, hi, anomalies)
    return lo, hi, anomalies


def _fill(i, samples, values, lo, hi, anomalies) -> None:
    stats = mean_std(samples)
    if stats is None:
        return
    mean, std = stats
    width = SIGMAS * _floor_std(mean, std)
    lo[i], hi[i] = mean - width, mean + width
    value = values[i]
    if value is not None and not lo[i] <= value <= hi[i]:
        anomalies.append(i)


def zscore(value: float, samples: list[float]) -> float | None:
    """How many standard deviations value is from the samples' mean (None: too few samples)."""
    stats = mean_std(samples)
    if stats is None:
        return None
    mean, std = stats
    return abs(value - mean) / _floor_std(mean, std)


def last_zscore(points: list[tuple[float, float]]) -> float | None:
    """|z| of the last point against the earlier ones, seasonal or rolling as for the bands."""
    if not points:
        return None
    points = sorted(points)
    t_last, v_last = points[-1]
    history = points[:-1]
    if seasonal(history, t_last):
        hour = hour_of_day(t_last)
        samples = [v for t, v in history if t >= t_last - WEEK and hour_of_day(t) == hour]
    else:
        samples = [v for t, v in history if t >= t_last - ROLLING_WINDOW]
    return zscore(v_last, samples)
