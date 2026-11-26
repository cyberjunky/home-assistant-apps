"""Reading Garmin's answers: forgiving parsers and the polyline decoder."""

from datetime import date

from fake_garmin import _encode

from src import datasets


def test_polyline_round_trip():
    points = [(52.09071, 5.12142), (52.09101, 5.12201), (52.0899, 5.1199)]
    decoded = datasets.decode_polyline(_encode(points))
    assert decoded == [[52.09071, 5.12142], [52.09101, 5.12201], [52.0899, 5.1199]]


def test_polyline_cut_short_doesnt_crash():
    assert datasets.decode_polyline(_encode([(52.0, 5.0), (52.1, 5.1)])[:-2]) == [[52.0, 5.0]]


def test_summary_skips_days_not_worn(db):
    data = {
        "data": [
            {"calendarDate": "2026-01-01", "includesWellnessData": False, "movement": {"steps": {"value": 0}}},
            {
                "calendarDate": "2026-01-02",
                "includesWellnessData": True,
                "movement": {"steps": {"value": 5000, "goal": 8000}},
                "heartRate": {"maxValue": 120, "restingValue": 50},
                "stress": {"highStressDurationInMillis": 600000},
            },
        ]
    }
    datasets.parse_summaries(db, data, date(2026, 1, 1), date(2026, 1, 2))
    values = db.series(["steps", "hr_resting", "stress_high_s"], "2026-01-01", "2026-01-02")
    assert values["steps"] == {"2026-01-02": 5000}
    assert values["stress_high_s"] == {"2026-01-02": 600}


def test_missing_fields_are_left_out(db):
    assert (
        datasets.parse_hrv(db, {"hrvSummaries": [{"calendarDate": "2026-01-01"}]}, date(2026, 1, 1), date(2026, 1, 1))
        == 0
    )
    assert (
        datasets.parse_sleep(db, [{"calendarDate": "2026-01-01", "values": {}}], date(2026, 1, 1), date(2026, 1, 1))
        == 0
    )
    assert datasets.parse_weight(db, None, date(2026, 1, 1), date(2026, 1, 1)) == 0


def test_readiness_keeps_the_morning_value(db):
    data = [
        {"calendarDate": "2026-01-01", "score": 40, "inputContext": "UPDATE_REALTIME_VARIABLES"},
        {"calendarDate": "2026-01-01", "score": 80, "inputContext": "AFTER_WAKEUP_RESET"},
        {"calendarDate": "2026-01-01", "score": 30, "inputContext": "UPDATE_REALTIME_VARIABLES"},
    ]
    datasets.parse_readiness(db, data, date(2026, 1, 1), date(2026, 1, 1))
    assert db.series(["readiness"], "2026-01-01", "2026-01-01")["readiness"] == {"2026-01-01": 80}


def test_bedtime_after_midnight_counts_on(db):
    # 00:30 local, stamped as if it were UTC (Garmin's "local" milliseconds)
    data = [
        {
            "calendarDate": "2026-01-02",
            "values": {
                "totalSleepTimeInSeconds": 25000,
                "localSleepStartTimeInMillis": 1767313800000,
                "localSleepEndTimeInMillis": 1767340800000,
            },
        }
    ]
    datasets.parse_sleep(db, data, date(2026, 1, 2), date(2026, 1, 2))
    values = db.series(["bedtime_h", "waketime_h"], "2026-01-02", "2026-01-02")
    assert values["bedtime_h"]["2026-01-02"] == 24.5
    assert values["waketime_h"]["2026-01-02"] == 8.0


def test_day_curves_in_five_minute_steps(db):
    start_ms = 1767222000000  # 2025-12-31T23:00Z: midnight in Amsterdam
    data = {
        "heart_rate": {
            "startTimestampGMT": "2025-12-31T23:00:00.0",
            "heartRateValues": [[start_ms, 60], [start_ms + 120000, 64], [start_ms + 600000, None]],
        },
        "stress": {
            "startTimestampGMT": "2025-12-31T23:00:00.0",
            "stressValuesArray": [[start_ms, -1], [start_ms + 180000, 30]],
            "bodyBatteryValuesArray": [[start_ms, "MEASURED", 50, 3.0]],
        },
    }
    datasets.parse_day_curves(db, data, date(2026, 1, 1), date(2026, 1, 1))
    assert db.intraday("hr", "2026-01-01", "2026-01-01") == [("2026-01-01", [[0, 62.0]])]
    # A stress level below zero means "not measured"
    assert db.intraday("stress", "2026-01-01", "2026-01-01") == [("2026-01-01", [[0, 30.0]])]
    assert db.series(["hr_avg"], "2026-01-01", "2026-01-01")["hr_avg"] == {"2026-01-01": 62.0}


def test_generic_range_reads_date_keyed_maps(db):
    _fetch, parse = datasets._generic_range("get_cycling_ability_range", "cycling_ability")
    data = {"cyclingAbilitiesMap": {"2026-01-01": {"score": 61, "climbing": 40}, "2026-01-02": {"score": 62}}}
    assert parse(db, data, date(2026, 1, 1), date(2026, 1, 2)) == 3
    assert db.series(["cycling_ability.score"], "2026-01-01", "2026-01-02")["cycling_ability.score"] == {
        "2026-01-01": 61,
        "2026-01-02": 62,
    }


def test_sleep_detail_keeps_deep_sleep(db):
    start = 1767225600000  # 2026-01-01T00:00Z
    data = {
        "dailySleepDTO": {
            "calendarDate": "2026-01-01",
            "sleepStartTimestampGMT": start,
            "sleepStartTimestampLocal": start + 3600000,
        },
        "sleepLevels": [
            {"startGMT": "2026-01-01T00:00:00.0", "endGMT": "2026-01-01T00:30:00.0", "activityLevel": 0.0},
            {"startGMT": "2026-01-01T00:30:00.0", "endGMT": "2026-01-01T01:00:00.0", "activityLevel": 2.0},
        ],
        "wellnessEpochSPO2DataDTOList": [{"epochTimestamp": "2026-01-01T00:10:00.0", "spo2Reading": 93}],
        "sleepHeartRate": [{"value": 52, "startGMT": start + 600000}],
    }
    assert datasets.parse_sleep_detail(db, data, date(2026, 1, 1), date(2026, 1, 1)) >= 1
    night = db.intraday("sleep", "2026-01-01", "2026-01-01")[0][1]
    assert night["stages"] == [[0.0, 30.0, "deep"], [30.0, 60.0, "rem"]]
    assert night["spo2"] == [[10.0, 93.0]] and night["offset_min"] == 60
    assert db.series(["sleep_spo2_low"], "2026-01-01", "2026-01-01")["sleep_spo2_low"] == {"2026-01-01": 93}


def test_steps_per_quarter_hour(db):
    data = {
        "steps": [
            {"startGMT": "2025-12-31T23:00:00.0", "steps": 0},
            {"startGMT": "2026-01-01T07:15:00.0", "steps": 420},
        ],
        "respiration": {},
    }
    datasets.parse_day_more(db, data, date(2026, 1, 1), date(2026, 1, 1))
    assert db.intraday("steps", "2026-01-01", "2026-01-01") == [("2026-01-01", [[0, 0.0], [495, 420.0]])]


def test_activity_detail_parts():
    from fake_garmin import FakeGarmin

    fake = FakeGarmin()
    activity = fake.get_activities_by_date("2026-11-01", "2026-11-30")[0]
    curves = datasets.activity_curves(fake.get_activity_details(activity["activityId"]))
    assert {"hr", "time", "distance"} <= set(curves) and len(curves["hr"]) == 400
    assert datasets.activity_curves({}) == {} and datasets.activity_curves(None) == {}
    sets = datasets.activity_sets(fake.get_activity_exercise_sets(1))
    assert len(sets) == 9 and sets[0]["exercise"] == "bench press"
    assert datasets.activity_laps({"lapDTOs": [{"distance": 1000, "duration": 300}]})[0]["distance_m"] == 1000
