"""The rule state machine, conditions and validation."""

import pytest

from src.rules import TEMPLATES, alert_message, compare, step, validate


def test_compare():
    assert compare(">", 21, 20)
    assert not compare(">", 20, 20)
    assert compare(">=", 20, 20)
    assert compare("<", 1, 2) and compare("<=", 2, 2) and compare("==", 3, 3)
    assert not compare(">", None, 0)
    assert not compare(">", float("nan"), 0)


def test_fires_at_once_without_a_wait():
    assert step("ok", 0, True, 0, 100) == ("firing", 100, "firing")
    assert step("firing", 100, True, 0, 130) == ("firing", 100, None)
    assert step("firing", 100, False, 0, 160) == ("ok", 160, "resolved")
    assert step("ok", 160, False, 0, 190) == ("ok", 160, None)


def test_pending_waits_for_seconds():
    assert step("ok", None, True, 60, 100) == ("pending", 100, None)
    assert step("pending", 100, True, 60, 130) == ("pending", 100, None)
    assert step("pending", 100, True, 60, 160) == ("firing", 160, "firing")


def test_pending_that_clears_never_fires():
    assert step("pending", 100, False, 60, 130) == ("ok", 130, None)


def test_cleared_stays_quiet_until_false_once():
    # Cleared by hand while the condition still holds: no new firing, no resolved message
    assert step("cleared", 100, True, 0, 130) == ("cleared", 100, None)
    assert step("cleared", 100, False, 0, 160) == ("ok", 160, None)
    # .. and after that it can fire again
    assert step("ok", 160, True, 0, 190) == ("firing", 190, "firing")


def test_validate():
    rule = validate(
        {
            "name": " Errors ",
            "kind": "log_count",
            "query": {"q": "severity:<=err", "window": "300"},
            "condition": {"op": ">", "threshold": "20"},
        }
    )
    assert rule == {
        "id": None,
        "name": "Errors",
        "kind": "log_count",
        "query": {"q": "severity:<=err", "window": 300},
        "condition": {"op": ">", "threshold": 20.0},
        "for_seconds": 0,
        "severity": "warning",
        "enabled": True,
        "notify": True,
    }
    metric = validate(
        {
            "name": "cpu",
            "kind": "absence",
            "query": {"name": "up", "filter": {"job": "nas"}},
            "condition": {"op": ">", "threshold": 600},
        }
    )
    assert metric["query"] == {"name": "up", "filter": {"job": "nas"}}
    for bad in (
        {"name": "", "kind": "log_count", "condition": {"op": ">", "threshold": 1}},
        {"name": "x", "kind": "nope", "condition": {"op": ">", "threshold": 1}},
        {"name": "x", "kind": "log_count", "condition": {"op": "!", "threshold": 1}},
        {"name": "x", "kind": "log_count", "condition": {"op": ">", "threshold": "a lot"}},
        {"name": "x", "kind": "log_count", "query": {"q": "severity:loud"}, "condition": {"op": ">", "threshold": 1}},
        {"name": "x", "kind": "metric", "query": {}, "condition": {"op": ">", "threshold": 1}},
        {
            "name": "x",
            "kind": "metric",
            "query": {"name": "m", "agg": "median"},
            "condition": {"op": ">", "threshold": 1},
        },
        {
            "name": "x",
            "kind": "metric",
            "query": {"name": "m", "filter": {"a": 1}},
            "condition": {"op": ">", "threshold": 1},
        },
    ):
        with pytest.raises(ValueError):
            validate(bad)


def test_templates_are_valid_rules():
    for template in TEMPLATES:
        validate(template)


def test_alert_message():
    rule = {"name": "Errors", "condition": {"op": ">", "threshold": 20}}
    assert alert_message(rule, 34.0, "firing") == "Errors: 34 (> 20)"
    assert alert_message(rule, 3.0, "resolved") == "Errors: resolved (3)"
