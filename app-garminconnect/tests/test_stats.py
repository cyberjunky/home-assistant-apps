"""The statistics."""

from src import stats


def test_eddington():
    assert stats.eddington([]) == 0
    assert stats.eddington([1, 1, 1]) == 1
    assert stats.eddington([10, 9, 8, 3, 3, 3]) == 3
    assert stats.eddington([50] * 40) == 40


def test_streaks():
    days = [f"2026-01-0{i}" for i in range(1, 8)]
    steps = {d: 9000 for d in days}
    steps["2026-01-03"] = 100
    result = stats.streaks(steps, {}, days)
    # Without a goal the default 10,000 counts: nothing hit
    assert result["longest"] == 0
    result = stats.streaks(steps, {d: 8000 for d in days}, days)
    assert result == {"current": 4, "longest": 4, "longest_end": "2026-01-07", "hit": 6, "worn": 7}


def test_today_doesnt_break_the_streak():
    days = ["2026-01-01", "2026-01-02", "2026-01-03"]
    steps = {"2026-01-01": 9000, "2026-01-02": 9000, "2026-01-03": 300}
    assert stats.streaks(steps, {d: 8000 for d in days}, days)["current"] == 2


def test_family():
    assert stats.family("trail_running") == "running"
    assert stats.family("road_biking") == "cycling"
    assert stats.family("hiking") == "walking"
    assert stats.family("lap_swimming") == "swimming"
    assert stats.family("strength_training") == "strength"
    assert stats.family("golf") == "other"


def test_sleep_rhythm_social_jetlag():
    # Work nights 23:00-07:00 (middle 03:00), weekend nights 01:00-09:00 (middle 05:00)
    bed, wake = {}, {}
    for day in ("2026-01-05", "2026-01-06", "2026-01-07"):  # Mon-Wed
        bed[day], wake[day] = 23.0, 7.0
    for day in ("2026-01-10", "2026-01-11"):  # Sat, Sun
        bed[day], wake[day] = 25.0, 9.0
    result = stats.sleep_rhythm(bed, wake, {}, {})
    assert result["social_jetlag_h"] == 2.0
    assert result["nights"] == 5


def test_series_buckets_sum_and_mean(db):
    db.put_daily(
        [
            ("steps", "2026-01-05", 1000),
            ("steps", "2026-01-06", 3000),
            ("hr_resting", "2026-01-05", 50),
            ("hr_resting", "2026-01-06", 52),
        ]
    )
    result = stats.series(db, ["steps", "hr_resting"], "2026-01-05", "2026-01-11", "week")
    assert result["buckets"] == ["2026-01-05"]
    assert result["series"]["steps"] == [4000]
    assert result["means"]["steps"] == [2000]
    assert result["series"]["hr_resting"] == [51]


async def test_insights_on_imported_history(collector, db):
    from tests.test_collector import run_history

    await collector.update()
    await run_history(collector)
    from datetime import date, timedelta

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=59)).isoformat()
    result = stats.insights(db, start, end)
    assert result["streak"]["worn"] >= 59
    assert result["fun"]["km"] > 0 and result["fun"]["heartbeats"]
    assert result["rhythm"]["days"] >= 59
    assert len(result["bests"]) >= 8
    summary = stats.activity_summary(db, start, end)
    assert summary["count"] > 0
    cells = stats.heat_cells(db, start, end)
    assert cells and all(len(c) == 3 for c in cells)
    assert stats.heat_cells(db, start, end, "swimming") == []
    assert stats.day_view(db, end)["values"]["steps"]


def test_track_cells_fill_in_between_points():
    from src.database import track_cells

    # Two points 1 km apart east-west: the cells in between are there too
    cells = track_cells([[52.0, 5.0], [52.0, 5.0146]])
    assert (52.0, 5.0) in cells and (52.0, 5.015) in cells
    assert len(cells) >= 14
    # A jump of tens of km is a gap in the recording, not a road
    assert len(track_cells([[52.0, 5.0], [52.5, 5.5]])) == 2


async def test_cells_count_activities_per_cell(db):
    db.put_activities(
        [
            {"id": 1, "start_ts": 1, "start_local": "2026-01-01 10:00:00", "day": "2026-01-01", "type": "running"},
            {"id": 2, "start_ts": 2, "start_local": "2026-01-02 10:00:00", "day": "2026-01-02", "type": "road_biking"},
        ]
    )
    for activity_id in (1, 2):
        db.put_track(activity_id, [[52.0, 5.0], [52.0, 5.0004]])
    assert db.unmapped_tracks() == [2, 1]
    for activity_id in db.unmapped_tracks():
        db.map_track_cells(activity_id)
    assert db.unmapped_tracks() == []
    cells = stats.heat_cells(db, "2026-01-01", "2026-01-31")
    assert [52.0, 5.0, 2] in cells
    assert stats.heat_cells(db, "2026-01-01", "2026-01-31", "running") == [[52.0, 5.0, 1]]
    # A long period uses the other index, with the same answer
    assert stats.heat_cells(db, "2025-01-01", "2026-12-31", "running") == [[52.0, 5.0, 1]]


async def test_earlier_tracks_get_mapped(collector, db):
    db.put_activities(
        [{"id": 9, "start_ts": 1, "start_local": "2026-01-01 10:00:00", "day": "2026-01-01", "type": "hiking"}]
    )
    db.put_track(9, [[52.0, 5.0], [52.001, 5.0]])
    assert await collector.map_missing_cells() == 1
    assert db.query_one("SELECT cells FROM mapped_tracks WHERE activity_id = 9")["cells"] >= 2


async def test_heat_tracks_in_view(db):
    db.put_activities(
        [
            {"id": 1, "start_ts": 1, "start_local": "2026-01-01 10:00:00", "day": "2026-01-01", "type": "walking"},
            {"id": 2, "start_ts": 2, "start_local": "2026-01-02 10:00:00", "day": "2026-01-02", "type": "walking"},
        ]
    )
    # One track through the view and out of it again, one far away
    db.put_track(1, [[51.90, 4.40], [51.901, 4.401], [51.95, 4.50], [51.902, 4.402], [51.903, 4.403]])
    db.put_track(2, [[52.30, 4.90], [52.301, 4.901]])
    for activity_id in (1, 2):
        db.map_track_cells(activity_id)
    result = stats.heat_tracks(db, "2026-01-01", "2026-01-31", (51.899, 4.399, 51.904, 4.404))
    assert result == {"lines": [[[51.9, 4.4], [51.901, 4.401]], [[51.902, 4.402], [51.903, 4.403]]], "more": False}
    assert stats.heat_tracks(db, "2026-01-01", "2026-01-31", (51.899, 4.399, 51.904, 4.404), "running")["lines"] == []
