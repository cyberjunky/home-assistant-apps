"""The example searches and charts the Logs and Metrics pages offer (src/web/query-examples.json)
must stay valid: every search parses, every chart uses a known aggregation."""

import json
from pathlib import Path

import pytest

from src.database import METRIC_AGGS
from src.query import QueryError, to_where

EXAMPLES = json.loads((Path(__file__).resolve().parent.parent / "src" / "web" / "query-examples.json").read_text())


@pytest.mark.parametrize("example", EXAMPLES["logs"], ids=lambda e: e["title"])
def test_log_example_parses(example):
    try:
        where = to_where(example["q"])
    except QueryError as e:
        pytest.fail(f"{example['title']}: {e}")
    assert where.sql
    assert example["description"] and example["needs"]


@pytest.mark.parametrize("example", EXAMPLES["metrics"], ids=lambda e: e["title"])
def test_metric_example_is_valid(example):
    assert example["queries"] and example["description"] and example["needs"]
    for query in example["queries"]:
        assert query["name"]
        assert query.get("agg", "avg") in METRIC_AGGS
        assert set(query) <= {"name", "agg", "group", "filter", "anomaly"}
