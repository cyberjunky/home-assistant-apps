"""Anomaly bands: rolling with little history, by hour of day with a week of it."""

import math

from src.anomaly import band, last_zscore, mean_std, zscore


def test_mean_std():
    assert mean_std([1, 2, 3]) is None
    mean, std = mean_std([2, 4, 4, 4, 5, 5, 7, 9])
    assert mean == 5 and math.isclose(std, 2.138, rel_tol=1e-3)


def test_rolling_band_flags_a_jump():
    t0 = 1_700_000_000
    t = [t0 + 60 * i for i in range(60)]
    values = [10.0 + (i % 3) * 0.1 for i in range(60)]
    values[50] = 50.0
    lo, hi, anomalies = band(t, values, [])
    # Too few earlier values for the first buckets
    assert lo[0] is None and lo[10] is not None
    assert anomalies == [50]
    assert lo[20] < 10 < hi[20]


def test_rolling_band_uses_the_history():
    t0 = 1_700_000_000
    history = [(t0 - 60 * i, 5.0 + (i % 2)) for i in range(1, 30)]
    lo, _hi, anomalies = band([t0], [5.5], history)
    assert lo[0] is not None and anomalies == []
    _, _, anomalies = band([t0], [20.0], history)
    assert anomalies == [0]


def test_seasonal_band_with_a_week_of_history():
    # Busy (100) every day from 18:00 to 19:00 local time, quiet (10) otherwise
    from datetime import datetime

    start = datetime(2026, 11, 1, 0, 0).timestamp()
    history = []
    for i in range(8 * 24 * 12):
        t = start + 300 * i
        hour = datetime.fromtimestamp(t).hour
        history.append((t, (100.0 if hour == 18 else 10.0) + (i % 3)))
    window_start = start + 8 * 86400
    evening = window_start + 18 * 3600
    t = [evening, evening + 300, window_start + 3 * 3600]
    lo, hi, anomalies = band(t, [101.0, 10.0, 100.0], history)
    # 101 at 18:00 is normal; 10 at 18:05 and 100 at 03:00 are not
    assert anomalies == [1, 2]
    assert lo[0] < 101 < hi[0]


def test_zscore():
    assert zscore(1.0, [1.0]) is None
    assert zscore(10.0, [10.0] * 10) == 0
    assert zscore(13.0, [9.0, 10.0, 11.0, 10.0, 10.0]) > 3
    points = [(1_700_000_000 + 60 * i, 10.0 + (i % 2)) for i in range(30)]
    assert last_zscore(points) < 3
    points.append((1_700_000_000 + 60 * 30, 40.0))
    assert last_zscore(points) > 3
    assert last_zscore([]) is None
