"""Statistics and the dashboard's API, on a small database made up here."""

import json
import time
from datetime import datetime, timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.config import Config
from src.database import Database
from src.history import ARCHIVES, HistoryImporter, parse_rows
from src.metrics import METRIC_BY_KEY, PUBLISHED
from src.monitor import ToonMonitor
from src.mqtt import discovery_messages
from src.stats import (
    Stats,
    boiler_energy,
    bucket_key,
    buckets_csv,
    burner_runs,
    burner_segments,
    counter_deltas,
    cycle_summary,
    estimated_efficiency,
    gas_heatmap,
    gas_per_hour,
    modulation_distribution,
    night_base_load,
    pressure_summary,
    sum_by_day,
    usage_between,
    weighted_while,
    year_before,
)
from src.webui import WebUI, next_change

DAY = 86400
HOUR = 3600


def at(text):
    return int(datetime.fromisoformat(text).timestamp())


def test_counter_drop_and_long_gap_give_no_lump():
    points = [
        (0, 100.0),
        (HOUR, 101.0),
        (2 * HOUR, 0.5),
        (3 * HOUR, 1.5),
        (3 * HOUR + 10 * DAY, 90.0),
        (3 * HOUR + 10 * DAY + HOUR, 91.0),
    ]
    assert [round(d[1], 3) for d in counter_deltas(points)] == [1.0, 1.0, 1.0]


def test_daily_reading_counts_for_the_day_it_covers():
    # The Toon stamps a day with its end
    deltas = counter_deltas([(at("2026-03-10T00:00:00"), 10.0), (at("2026-03-11T00:00:00"), 15.0)])
    assert sum_by_day(deltas) == {"2026-03-10": 5.0}


def test_interval_of_days_is_shared_out():
    deltas = counter_deltas([(at("2026-03-10T00:00:00"), 10.0), (at("2026-03-12T00:00:00"), 16.0)])
    assert sum_by_day(deltas) == {"2026-03-10": 3.0, "2026-03-11": 3.0}


def test_usage_between_ignores_a_reset():
    points = [(0, 5.0), (10, 7.0), (20, 1.0), (30, 2.0)]
    assert usage_between(points, 5, 30) == 3.0
    assert usage_between([], 0, 10) is None


def test_buckets_and_year_before():
    assert bucket_key("2026-11-08", "week") == "2026-11-02"
    assert bucket_key("2026-11-08", "month") == "2026-11"
    assert bucket_key("2026-11-08", "year") == "2026"
    assert year_before("2024-02-29") == "2023-02-28"


def test_burner_runs_and_cycles():
    codes = [(0, 0), (100, 1), (400, 2), (650, 0), (1000, 1), (2000, 0)]
    segments = burner_segments(codes, 2100)
    assert segments == [(100, 400, "heat"), (400, 650, "dhw"), (1000, 2000, "heat")]
    assert burner_runs(segments) == [(100, 650), (1000, 2000)]
    summary = cycle_summary(burner_runs(segments))
    assert (summary["starts"], summary["short"]) == (2, 1)
    assert summary["longest_minutes"] == round(1000 / 60, 1)


def test_burner_state_isnt_assumed_across_a_gap():
    assert burner_segments([(0, 1), (10000, 0)], 10100) == [(0, 1800, "heat")]


def test_weighted_while_holds_values_only_while_heating():
    segments = [(100, 200, "heat"), (300, 400, "dhw")]
    pieces = weighted_while(segments, [(0, 30.0), (150, 40.0), (350, 90.0)])
    assert [(p[1], p[2]) for p in pieces] == [(50, 30.0), (50, 40.0)]


def test_modulation_distribution():
    rows = modulation_distribution([(0, 600, 0.0), (600, 600, 20.0), (1200, 1200, 100.0)])
    assert {row["label"]: row["minutes"] for row in rows}["76-100%"] == 20.0
    assert {row["label"]: row["minutes"] for row in rows}["1-25%"] == 10.0


def days(values, first="2026-10-01"):
    start = datetime.fromisoformat(first)
    return {(start + timedelta(days=i)).date().isoformat(): value for i, value in enumerate(values)}


def test_pressure_noise_is_no_top_up():
    summary = pressure_summary(days([0.79, 0.69, 0.79, 0.59, 0.89, 0.69, 0.79, 0.5, 0.79, 0.69]), "2026-10-10")
    assert summary["top_ups"] == []


def test_pressure_top_up_and_loss():
    lows = [1.0, 0.9, 0.8, 0.8, 1.6, 1.6, 1.5, *[1.5 - 0.02 * i for i in range(20)]]
    summary = pressure_summary(days(lows), "2026-10-27")
    assert summary["last_top_up"] == "2026-10-05"
    assert summary["loss_per_week"] > 0


def test_pressure_without_data():
    assert pressure_summary({})["change_30d"] is None


def test_night_base_load_only_uses_the_night():
    night = at("2026-03-10T03:00:00")
    low = [(night + HOUR * i, 0.3, HOUR) for i in range(-1, 2)] + [(at("2026-03-10T12:30:00"), 5.0, HOUR)]
    assert night_base_load(low, []) == {"2026-03-10": 300}


def test_gas_heatmap_takes_averages():
    monday = at("2026-03-09T08:30:00")
    rows = gas_heatmap(
        [(monday, 1.0, HOUR), (monday + 7 * DAY, 3.0, HOUR), (monday, 50.0, 2 * DAY)], monday - DAY, monday + 8 * DAY
    )
    assert rows[0][8] == 2.0
    assert rows[1][8] is None


@pytest.fixture
def filled(tmp_path):
    db = Database(tmp_path / "t.db")
    now = int(time.time())
    hour = now // HOUR * HOUR
    gas = 100.0
    rows = []
    for i in range(24 * 40, 0, -1):
        gas += 0.5
        rows.append(("gas_total", hour - i * HOUR, gas))
    # A replaced meter halfway
    rows = [(m, ts, v if ts < hour - 20 * DAY else v - 100) for m, ts, v in rows]
    rows += [("energy_import_low", ts, 0.1 * (ts - rows[0][1]) / HOUR) for _, ts, _ in rows]
    db.add_hourly(rows)
    db.add_hourly([("boiler_pressure", hour - i * HOUR, 0.0 if i == 5 else 1.5) for i in range(1, 48)])
    db.add_hourly([("room_temperature", hour - i * HOUR, 20.0) for i in range(1, 48)])
    db.flush()
    t = now - 3 * HOUR
    for ts, code in ((t, 0), (t + 100, 1), (t + 900, 0), (t + 1000, 2), (t + 1200, 0)):
        db.record("burner_code", ts, code)
    for ts, value in ((t, 30.0), (t + 500, 60.0)):
        db.record("boiler_return_temperature", ts, value)
    db.flush()
    yield db
    db.close()


def test_report_on_a_database(filled):
    now = int(time.time())
    first = (datetime.fromtimestamp(now) - timedelta(days=39)).date().isoformat()
    last = datetime.fromtimestamp(now).date().isoformat()
    report = Stats(filled.path).report(first, last, "day")
    gas_days = [row["gas"] for row in report["buckets"] if row["gas"] is not None]
    # 0.5 an hour: at most a day of 24 hours, and the drop of the replaced meter isn't in there
    assert max(gas_days) <= 12.5
    assert report["totals"]["gas"] > 400
    assert report["gauges"]["boiler_pressure"]["min"] == 1.5
    assert report["cycles"]["starts"] == 2
    assert report["cycles"]["collecting_since"]
    assert report["cycles"]["condensing_share"] is not None
    assert report["gauges"]["boiler_temperature"] is None
    assert "gas" in buckets_csv(report).splitlines()[0]


def test_today_on_a_database(filled):
    today = Stats(filled.path).today()
    assert today["usage"]["yesterday"]["gas"] == pytest.approx(12.0, abs=1.0)
    assert today["burner"]["starts"] == 2
    assert len(today["gas_hours"]) == 24


def test_raw_export(filled):
    now = datetime.now()
    text = Stats(filled.path).raw_csv(["burner_code"], now.date().isoformat(), now.date().isoformat())
    assert text.splitlines()[0] == "time,timestamp,metric,value"


def test_new_history_files_and_no_year_file():
    names = {(a.logger, a.rra) for a in ARCHIVES}
    assert not any(rra == "year" for _, rra in names)
    assert ("boiler_burner_minutes", "5yrhours") in names
    assert ("boiler_burner_minutes", "10yrdays") in names
    assert ("thermstat_boilerOpMode", "30days") in names
    assert all(a.metric in METRIC_BY_KEY for a in ARCHIVES)


def test_dashboard_only_values_get_no_entity():
    keys = {metric.key for metric in PUBLISHED}
    assert not keys & {"burner_code", "burner_minutes", "boiler_op_mode"}
    topics = dict(discovery_messages("00-00-000-000", "1", "homeassistant", "toon"))
    assert not any("burner_code" in topic for topic in topics)


class FakeBus:
    async def action(self, destination, service, name, arguments=None, timeout=5):
        return {
            "burnerInfo": "1",
            "currentModulationLevel": "40",
            "nextTime": "2000",
            "nextState": "2",
            "nextProgram": "1",
        }


async def test_poll_keeps_burner_code_and_schedule(tmp_path):
    db = Database(tmp_path / "t.db")
    monitor = ToonMonitor(Config(ssh_host="x", ssh_password="x"), db, None)
    await monitor._poll_fast(FakeBus())
    assert monitor.values["burner_code"] == 1
    assert monitor.values["burner_state"] == "heating"
    monitor.values["clock_offset"] = -100
    assert next_change(monitor) == {"time": 2100, "state": 2}
    monitor.values["clock_offset"] = 0
    assert next_change(monitor)["time"] == 2000
    db.flush()
    db.close()


class FakeMqtt:
    connected = False
    broker = "broker"


@pytest.fixture
async def client(filled):
    config = Config(ssh_host="toon.example", ssh_password="pw-secret", mqtt_password="mq-secret", mqtt_user="mq-user")
    monitor = ToonMonitor(config, filled, None)
    ui = WebUI(0, filled, monitor, FakeMqtt())
    async with TestClient(TestServer(ui.app)) as test_client:
        yield test_client


async def test_status_has_no_secrets(client):
    response = await client.get("/api/status")
    text = await response.text()
    assert response.status == 200
    assert "secret" not in text
    assert json.loads(text)["import"]["total"] == len(ARCHIVES)


async def test_pages_and_api(client):
    page = await (await client.get("/")).text()
    assert "__VERSION__" not in page
    assert 'src="/' not in page
    assert (await client.get("/api/today")).status == 200
    assert (await client.get("/api/stats?bucket=month")).status == 200
    assert (await client.get("/api/stats?bucket=decade")).status == 400
    assert (await client.get("/api/stats?from=nonsense")).status == 400
    assert (await client.get("/static/stats.js")).status == 200
    export = await client.get("/api/export/periods.csv?bucket=day")
    assert export.headers["Content-Type"].startswith("text/csv")
    assert (await client.get("/api/export/values.csv?source=hourly")).status == 200
    assert (await client.get("/api/history?start=0&end=10&metrics=setpoint")).status == 200


def test_boiler_energy_is_an_estimate_from_the_return_temperature():
    assert estimated_efficiency(25) == 0.97
    assert estimated_efficiency(70) == 0.88
    assert estimated_efficiency(42.5) == pytest.approx(0.925)
    energy = boiler_energy(100.0, 30.0, 9.77)
    assert energy["gas_kwh"] == 977.0
    assert energy["heat_kwh"] == pytest.approx(947.7)
    # Without a return temperature, only what's measured
    assert boiler_energy(100.0, None, 9.77)["heat_kwh"] is None
    assert boiler_energy(None, 40.0, 9.77)["gas_kwh"] is None


def test_gas_per_hour():
    base = 1_000_000 // HOUR * HOUR
    points = [(base, 10.0), (base + HOUR, 10.5), (base + 2 * HOUR, 11.25)]
    rows = gas_per_hour(points, [base, base + HOUR, base + 2 * HOUR])
    assert [row["gas"] for row in rows] == [0.5, 0.75, 0.0]
    # Daily readings say nothing about the hours
    assert gas_per_hour([(base, 10.0), (base + DAY, 20.0)], list(range(base, base + DAY, HOUR))) == []


async def test_gas_hours_api(client):
    assert (await client.get("/api/gas-hours?day=nonsense")).status == 400
    assert (await client.get("/api/gas-hours")).status == 400
    response = await client.get(f"/api/gas-hours?day={datetime.now().date().isoformat()}")
    assert response.status == 200
    assert isinstance(await response.json(), list)


def test_boiler_name_option():
    options = {"ssh": {"host": "toon", "password": "x"}}
    assert Config.from_options(options).boiler_name == ""
    assert Config.from_options({**options, "boiler_name": " Calenta Ace 28c "}).boiler_name == "Calenta Ace 28c"


def test_report_says_since_when_burner_hours_are_counted(filled):
    day = (datetime.now() - timedelta(days=3)).date().isoformat()
    filled.add_samples([("burner_hours", int(datetime.fromisoformat(f"{day}T12:00:00").timestamp()), 100.0)])
    filled.flush()
    today = datetime.now().date().isoformat()
    report = Stats(filled.path).report(day, today, "day")
    assert report["burner_hours_since"] <= day


def test_new_histories_are_imported_and_counted(tmp_path):
    archives = {(a.logger, a.rra): a for a in ARCHIVES}
    hours = archives[("thermstat_boilerBurnerHours", "30days")]
    assert hours.metric == "burner_hours"
    meter = archives[("gas_delivered", "5minweek")]
    assert meter.bin_seconds == 300
    assert METRIC_BY_KEY["gas_delivered"].key not in {m.key for m in PUBLISHED}

    db = Database(tmp_path / "t.db")
    importer = HistoryImporter(db)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    stamp = lambda days: str(int((today - timedelta(days=days)).timestamp()))  # noqa: E731
    answer = {stamp(5): 100, stamp(4): 103, stamp(3): 105, stamp(2): None, "error": "x"}
    assert importer._store(hours, parse_rows(hours, answer)) == 3
    first = (today - timedelta(days=5)).date().isoformat()
    report = Stats(db.path).report(first, today.date().isoformat(), "day")
    assert report["totals"]["burner_hours"] == pytest.approx(5)
    assert report["burner_hours_since"] == first

    # The meter's raw litres become m³, as samples
    ts = int(today.timestamp()) - 600
    assert importer._store(meter, parse_rows(meter, {str(ts): 811447})) == 1
    assert db.history(["gas_delivered"], ts - 60, ts + 60)["gas_delivered"][0][1] == pytest.approx(811.447)
    db.close()
