"""The history import: newest first, in pieces, resumable, and the updates."""

import dataclasses
from datetime import date

from src.datasets import DATASET_BY_KEY


async def run_history(collector, limit=10000):
    turns = 0
    while await collector.history_turn():
        turns += 1
        assert turns < limit
    return turns


async def test_full_import_and_done(collector, db):
    await collector.update()
    await run_history(collector)
    status = collector.status()
    for entry in status["datasets"]:
        if entry["enabled"] and entry["kind"] != "snapshot":
            assert entry["done"], entry["key"]
    counts = db.counts()
    assert counts["days"] >= 59
    assert counts["activities"] > 0 and counts["tracks"] == db.scalar(
        "SELECT COUNT(*) FROM activities WHERE has_track = 1"
    )
    assert not await collector.history_turn()


async def test_import_resumes_where_it_was(collector, db, signed_in):
    for _ in range(12):
        await collector.history_turn()
    before = {k: v["oldest"] for k, v in db.all_progress().items()}
    # A new collector on the same database (an app restart) goes on from there
    from src.collector import Collector

    again = Collector(collector.config, db, signed_in)
    await again.history_turn()
    after = db.all_progress()
    assert any(after[k]["oldest"] and before.get(k) and after[k]["oldest"] < before[k] for k in after) or len(
        after
    ) > len(before)


async def test_history_walks_back_from_today(collector, db):
    await collector._history_piece(DATASET_BY_KEY["summaries"])
    progress = db.progress("summaries")
    days = db.series(["steps"], "2000-01-01", "2100-01-01")["steps"]
    assert max(days) == date.today().isoformat() or max(days) >= progress["oldest"]
    assert progress["done"]  # 60 days fit in one piece of 120


async def test_refused_piece_is_skipped(collector, db):
    from garminconnect import GarminConnectConnectionError

    async def refuse(account, start, end):
        raise GarminConnectConnectionError("API call client error (400): too far back")

    await collector._history_piece(dataclasses.replace(DATASET_BY_KEY["sleep"], fetch=refuse))
    progress = db.progress("sleep")
    assert progress["oldest"] and "400" in progress["last_error"]


async def test_reparse_reads_kept_answers_again(collector, db):
    await collector.update()
    # The day curves keep only what was read out of them, not Garmin's answer
    kept = "SELECT COUNT(*) FROM daily WHERE metric NOT IN ('hr_avg', 'hr_worn_min')"
    before = db.scalar(kept)
    db._db.execute("DELETE FROM daily")
    assert collector.reparse() > 0
    assert db.scalar(kept) == before


async def test_update_fetches_snapshots(collector, db):
    await collector.update()
    gear, fetched = db.snapshot("gear")
    assert gear and fetched
    assert db.events("badge")


async def test_activity_details_backlog(collector, db):
    await collector.update()
    await run_history(collector)
    assert db.activities_without_details(1) == []
    some = db.scalar("SELECT activity_id FROM activity_details LIMIT 1")
    assert set(db.activity_details(some)) >= {"splits", "details", "weather"}


async def test_optional_new_sets(db, signed_in, monkeypatch):
    from datetime import timedelta

    from src import collector as collector_module
    from src.collector import Collector
    from src.config import Config

    monkeypatch.setattr(collector_module, "PAUSE_BETWEEN_REQUESTS", 0)
    config = Config(start_date=(date.today() - timedelta(days=6)).isoformat())
    config.datasets.update(sleep_detail=True, intraday_more=True, planning=True)
    col = Collector(config, db, signed_in)
    await col.update()
    await run_history(col)
    assert len(db.intraday("sleep", "2000-01-01", "2100-01-01")) >= 6
    assert len(db.intraday("steps", "2000-01-01", "2100-01-01")) >= 6
    assert db.snapshot("training_plans")[0]
