"""Energy prices and what was used costs, per page."""

import time
from datetime import datetime, timedelta

import pytest

from src.config import Config, parse_prices
from src.database import Database
from src.stats import Stats, add_heatmaps, day_costs, price_on

HOUR = 3600

PRICES = [
    {
        "start": "2026-01-01",
        "electricity_low": 0.25,
        "electricity_normal": 0.30,
        "gas": 1.20,
        "electricity_fixed_per_day": -0.50,
        "gas_fixed_per_day": 0.60,
    },
    {"start": "2025-01-01", "electricity_low": 0.20, "electricity_normal": 0.25, "return_low": 0.05, "gas": 1.00},
]


def test_prices_are_parsed_and_sorted():
    prices = parse_prices(PRICES)
    assert [p["start"] for p in prices] == ["2025-01-01", "2026-01-01"]
    # Returned costs what used costs unless set (net metering); fixed costs default to 0
    assert prices[1]["return_normal"] == 0.30
    assert prices[0]["return_low"] == 0.05 and prices[0]["return_normal"] == 0.25
    assert prices[0]["gas_fixed_per_day"] == 0.0
    with pytest.raises(ValueError):
        parse_prices([{"start": "1 jan", "electricity_low": 1, "electricity_normal": 1, "gas": 1}])
    config = Config.from_options({"ssh": {"host": "t", "password": "x"}, "prices": PRICES})
    assert len(config.prices) == 2


def test_the_contract_of_a_day():
    prices = parse_prices(PRICES)
    assert price_on(prices, "2024-12-31") is None
    assert price_on(prices, "2025-06-01")["gas"] == 1.00
    assert price_on(prices, "2026-01-01")["gas"] == 1.20


def test_day_costs():
    prices = parse_prices(PRICES)
    used = {"import_low": 4.0, "import_normal": 6.0, "export_low": 1.0, "export_normal": 2.0, "gas": 3.0}
    costs = day_costs(prices, "2026-03-01", used)
    # 4 * 0.25 + 6 * 0.30 - 1 * 0.25 - 2 * 0.30 - 0.50 = 1.45; gas 3 * 1.20 + 0.60 = 4.20
    assert costs["electricity"] == pytest.approx(1.45)
    assert costs["gas"] == pytest.approx(4.20)
    assert day_costs(prices, "2024-06-01", used) == {"electricity": None, "gas": None}
    assert day_costs(prices, "2026-03-01", {"gas": None})["electricity"] is None


def test_heatmaps_add_up():
    a = [[None, 1.0], [0.5, None]]
    b = [[None, 2.0], [None, 0.25]]
    assert add_heatmaps(a, b) == [[None, 3.0], [0.5, 0.25]]


def test_report_has_costs_and_pages(tmp_path):
    database = Database(tmp_path / "t.db")
    now = int(time.time())
    hour = now // HOUR * HOUR
    rows = []
    for i in range(24 * 10, 0, -1):
        ts = hour - i * HOUR
        rows += [("gas_total", ts, 100 + 0.1 * (24 * 10 - i)), ("energy_import_normal", ts, 50 + 0.5 * (24 * 10 - i))]
    database.add_hourly(rows)
    database.flush()
    first = (datetime.fromtimestamp(now) - timedelta(days=6)).date().isoformat()
    last = datetime.fromtimestamp(now).date().isoformat()
    prices = parse_prices(
        [
            {
                "start": "2000-01-01",
                "electricity_low": 0.2,
                "electricity_normal": 0.3,
                "gas": 1.5,
                "gas_fixed_per_day": 1.0,
            }
        ]
    )
    report = Stats(database.path, prices=prices).report(first, last, "day")
    day = report["buckets"][1]
    # A full day: 24 * 0.1 m³ of gas and 24 * 0.5 kWh
    assert day["cost_gas"] == pytest.approx(24 * 0.1 * 1.5 + 1.0, abs=0.02)
    assert day["cost_electricity"] == pytest.approx(24 * 0.5 * 0.3, abs=0.02)
    assert report["costs"]["total"] == pytest.approx(report["costs"]["gas"] + report["costs"]["electricity"])
    assert len(report["heatmap_electricity"]) == 7
    assert {r["page"] for r in report["records"]} <= {"electricity", "gas"}
    # Without prices there are no costs at all
    assert Stats(database.path).report(first, last, "day")["costs"] is None
    database.close()
