"""The danger zone: delete everything recorded, nothing else."""

import json
import time
from pathlib import Path

from aiohttp.test_utils import make_mocked_request

from src.database import Database
from src.webui import WebUI


def add_snapshots(db: Database, count: int, padding: int = 0) -> None:
    """Snapshots one minute apart: insert_snapshot stamps the current time, so a loop would overwrite itself."""
    now = int(time.time())
    db.con.executemany(
        "INSERT INTO snapshots (ts, data) VALUES (?, ?)",
        [(now - i * 60, json.dumps({"ppv": i, "pad": "x" * padding})) for i in range(count)],
    )
    db.con.commit()


def filled(tmp_path: Path) -> Database:
    db = Database(tmp_path / "goodwe.db")
    add_snapshots(db, 5)
    db.insert_snapshot({"ppv": 1000, "e_day": 3.5, "e_day_imp": 1, "e_day_exp": 2, "e_load_day": 4})
    db.record_forecast(time.strftime("%Y-%m-%d"), 12.5)
    db.insert_event(
        {"ts": int(time.time()), "type": "inverter_offline", "level": "warning", "message": "x", "data": {}}
    )
    return db


def test_wipe_empties_every_table_and_reports_what_was_there(tmp_path):
    db = filled(tmp_path)
    before = db.counts()
    assert before["snapshots"] >= 5 and before["days"] >= 1 and before["events"] == 1
    removed = db.wipe()
    assert removed == before
    assert db.counts() == {"snapshots": 0, "days": 0, "events": 0}
    assert db.con.execute("SELECT COUNT(*) FROM forecast_log").fetchone()[0] == 0
    # It keeps working: a new snapshot is recorded again
    db.insert_snapshot({"ts": int(time.time()), "ppv": 1, "e_day": 0})
    assert db.counts()["snapshots"] == 1
    db.close()


def test_the_file_shrinks(tmp_path):
    db = Database(tmp_path / "goodwe.db")
    add_snapshots(db, 5000, padding=300)
    big = db.size_bytes()
    assert big > 1_000_000
    db.wipe()
    assert db.size_bytes() < big / 20
    db.close()


async def call(ui: WebUI, body):
    request = make_mocked_request("POST", "/api/data/delete", headers={"Content-Type": "application/json"})

    async def read():
        return body

    request.json = read
    return await ui._handle_delete_data(request)


async def test_the_endpoint_needs_the_word_delete(tmp_path):
    ui = WebUI.__new__(WebUI)
    ui.database = filled(tmp_path)
    for body in ({}, {"confirm": "delete"}, {"confirm": True}, {"confirm": "no"}):
        response = await call(ui, body)
        assert response.status == 400
        assert ui.database.counts()["snapshots"] >= 5
    response = await call(ui, {"confirm": "DELETE"})
    assert response.status == 200
    assert json.loads(response.text)["removed"]["snapshots"] >= 5
    assert ui.database.counts()["snapshots"] == 0
    ui.database.close()
