"""OTLP JSON logs and metrics, and JSON lines."""

from datetime import UTC, datetime

from src.otlp import parse_json_lines, parse_logs, parse_metrics

RESOURCE = {
    "attributes": [
        {"key": "host.name", "value": {"stringValue": "nas"}},
        {"key": "service.name", "value": {"stringValue": "backup"}},
        {"key": "os.type", "value": {"stringValue": "linux"}},
    ]
}


def test_logs():
    body = {
        "resourceLogs": [
            {
                "resource": RESOURCE,
                "scopeLogs": [
                    {
                        "scope": {"name": "my.lib"},
                        "logRecords": [
                            {
                                "timeUnixNano": "1763805600000000000",
                                "severityNumber": 17,
                                "severityText": "ERROR",
                                "body": {"stringValue": "Backup failed"},
                                "attributes": [
                                    {"key": "job", "value": {"stringValue": "nightly"}},
                                    {"key": "tries", "value": {"intValue": "3"}},
                                ],
                                "traceId": "5b8efff798038103d269b633813fc60c",
                            },
                            {
                                "severityNumber": 9,
                                "body": {
                                    "kvlistValue": {
                                        "values": [
                                            {"key": "message", "value": {"stringValue": "structured"}},
                                            {"key": "size", "value": {"doubleValue": 1.5}},
                                        ]
                                    }
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }
    now = datetime(2026, 11, 22, tzinfo=UTC)
    first, second = parse_logs(body, now)
    assert (first.host, first.app, first.severity, first.message) == ("nas", "backup", 3, "Backup failed")
    assert first.ts == datetime.fromtimestamp(1763805600, UTC)
    assert first.attrs["job"] == "nightly" and first.attrs["tries"] == 3
    assert first.attrs["scope"] == "my.lib" and first.attrs["resource.os.type"] == "linux"
    assert first.attrs["traceId"].startswith("5b8e")
    assert (second.severity, second.message, second.attrs["body.size"]) == (6, "structured", 1.5)
    assert second.ts == now


def test_severity_mapping():
    def severity(number):
        body = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"severityNumber": number, "body": {}}]}]}]}
        return parse_logs(body)[0].severity

    assert [severity(n) for n in (1, 5, 9, 13, 17, 21)] == [7, 7, 6, 4, 3, 2]


def test_metrics():
    body = {
        "resourceMetrics": [
            {
                "resource": RESOURCE,
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "cpu.load",
                                "unit": "1",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "asDouble": 0.5,
                                            "timeUnixNano": "1763805600000000000",
                                            "attributes": [{"key": "cpu", "value": {"stringValue": "0"}}],
                                        }
                                    ]
                                },
                            },
                            {
                                "name": "requests",
                                "sum": {"isMonotonic": True, "dataPoints": [{"asInt": "42"}]},
                            },
                            {
                                "name": "latency",
                                "histogram": {"dataPoints": [{"count": "10", "sum": 2.5, "bucketCounts": ["1", "9"]}]},
                            },
                            {
                                "name": "rpc",
                                "summary": {
                                    "dataPoints": [
                                        {
                                            "count": "4",
                                            "sum": 8,
                                            "quantileValues": [{"quantile": 0.99, "value": 3}],
                                        }
                                    ]
                                },
                            },
                        ]
                    }
                ],
            }
        ]
    }
    samples = {(s.name, tuple(sorted(s.labels.items()))): s for s in parse_metrics(body)}
    base = (("host.name", "nas"), ("service.name", "backup"))
    cpu = samples[("cpu.load", (("cpu", "0"), *base))]
    assert (cpu.value, cpu.kind, cpu.unit) == (0.5, "gauge", "1")
    assert samples[("requests", base)].value == 42 and samples[("requests", base)].kind == "counter"
    assert samples[("latency_count", base)].value == 10
    assert samples[("latency_sum", base)].value == 2.5
    assert samples[("rpc", tuple(sorted((*base, ("quantile", "0.99")))))].value == 3
    assert ("latency_bucket", base) not in samples


def test_json_lines():
    text = "\n".join(
        [
            '{"ts": 1763805600, "host": "nas", "app": "cron", "level": "warn", "msg": "slow", "job": 1}',
            "not json",
            '{"timestamp": "2026-11-22T10:00:00Z", "message": "iso time", "severity": 3}',
            '{"no": "message"}',
            "",
        ]
    )
    entries, rejected = parse_json_lines(text)
    assert rejected == 2
    first, second = entries
    assert (first.host, first.app, first.severity, first.message, first.attrs) == ("nas", "cron", 4, "slow", {"job": 1})
    assert first.ts == datetime.fromtimestamp(1763805600, UTC)
    assert second.ts == datetime(2026, 11, 22, 10, tzinfo=UTC) and second.severity == 3


def test_json_array_and_millisecond_times():
    entries, rejected = parse_json_lines('[{"ts": 1763805600123, "message": "a"}, {"message": "b"}, 5]')
    assert rejected == 1 and [e.message for e in entries] == ["a", "b"]
    assert entries[0].ts.timestamp() == 1763805600.123
