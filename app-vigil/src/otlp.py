"""OpenTelemetry (OTLP/HTTP, JSON encoding) logs and metrics, and JSON-lines logs.

OTLP JSON follows the protobuf layout: resourceLogs[].scopeLogs[].logRecords[] and
resourceMetrics[].scopeMetrics[].metrics[]; 64-bit numbers (times, intValue, asInt, counts)
arrive as strings. Only the JSON encoding is supported: set `encoding: json` on the
OpenTelemetry Collector's otlphttp exporter (Vector's opentelemetry sink sends JSON).
"""

import json
import math
from datetime import UTC, datetime

from .records import MAX_MESSAGE, LogEntry, Sample, clip, otlp_severity, parse_time, severity_number
from .windows import is_windows_event, windows_fields

MAX_ATTRS = 64
MAX_ATTR_VALUE = 2048


def any_value(value):
    """An OTLP AnyValue as plain JSON (kvlists become objects)."""
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    if "doubleValue" in value:
        try:
            return float(value["doubleValue"])
        except (TypeError, ValueError):
            return None
    if "arrayValue" in value:
        return [any_value(v) for v in (value["arrayValue"] or {}).get("values") or []]
    if "kvlistValue" in value:
        return key_values((value["kvlistValue"] or {}).get("values"))
    if "bytesValue" in value:
        return value["bytesValue"]
    return None


def key_values(items) -> dict:
    """An OTLP KeyValue list ([{key, value}]) as a dict."""
    result = {}
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            result[item["key"]] = any_value(item.get("value"))
    return result


def _attrs(values: dict) -> dict:
    """Attributes as stored: at most MAX_ATTRS, long texts cut."""
    result = {}
    for key, value in list(values.items())[:MAX_ATTRS]:
        if isinstance(value, str) and len(value) > MAX_ATTR_VALUE:
            value = value[:MAX_ATTR_VALUE]
        result[str(key)[:128]] = value
    return result


def _nano_time(*values) -> datetime | None:
    for value in values:
        try:
            nanos = int(value)
        except (TypeError, ValueError):
            continue
        if nanos > 0:
            return datetime.fromtimestamp(nanos / 1e9, UTC)
    return None


def _resource(item: dict) -> dict:
    return key_values(((item.get("resource") or {}).get("attributes")) or [])


def parse_logs(body: dict, now: datetime | None = None) -> list[LogEntry]:
    now = now or datetime.now(UTC)
    entries = []
    for resource_logs in body.get("resourceLogs") or []:
        if not isinstance(resource_logs, dict):
            continue
        resource = _resource(resource_logs)
        host = resource.get("host.name") or resource.get("host.hostname")
        service = resource.get("service.name")
        for scope_logs in resource_logs.get("scopeLogs") or resource_logs.get("instrumentationLibraryLogs") or []:
            if not isinstance(scope_logs, dict):
                continue
            scope = (scope_logs.get("scope") or {}).get("name")
            for record in scope_logs.get("logRecords") or []:
                if not isinstance(record, dict):
                    continue
                entries.append(_log_record(record, host, service, scope, resource, now))
    return entries


def _log_record(record: dict, host, service, scope, resource: dict, now: datetime) -> LogEntry:
    attrs = key_values(record.get("attributes"))
    body = any_value(record.get("body"))
    if isinstance(body, dict):
        # A structured body: its message field is the line, the rest goes with the attributes
        message = body.pop("message", None) or body.pop("msg", None)
        attrs.update({f"body.{k}": v for k, v in body.items()})
        if message is None:
            message = json.dumps(body, default=str)
    elif body is None:
        message = ""
    else:
        message = body if isinstance(body, str) else json.dumps(body, default=str)
    for key in ("traceId", "spanId"):
        if record.get(key):
            attrs[key] = record[key]
    if scope:
        attrs.setdefault("scope", scope)
    for key, value in resource.items():
        if key not in ("host.name", "service.name"):
            attrs.setdefault(f"resource.{key}", value)
    app = service or attrs.get("app") or attrs.get("service")
    return LogEntry(
        ts=_nano_time(record.get("timeUnixNano"), record.get("observedTimeUnixNano")) or now,
        source="otlp",
        host=clip(host or attrs.get("host")),
        app=clip(app),
        severity=otlp_severity(record.get("severityNumber"), record.get("severityText")),
        facility=None,
        message=str(message)[:MAX_MESSAGE],
        attrs=_attrs(attrs),
    )


def _point_value(point: dict) -> float | None:
    for key in ("asDouble", "asInt"):
        if key in point:
            try:
                value = float(point[key])
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None
    return None


def _labels(point: dict, base: dict) -> dict[str, str]:
    labels = dict(base)
    for key, value in key_values(point.get("attributes")).items():
        labels[key] = value if isinstance(value, str) else json.dumps(value)
    return labels


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_metrics(body: dict) -> list[Sample]:
    samples = []
    for resource_metrics in body.get("resourceMetrics") or []:
        if not isinstance(resource_metrics, dict):
            continue
        resource = _resource(resource_metrics)
        base = {k: str(resource[k]) for k in ("host.name", "service.name") if resource.get(k) is not None}
        for scope_metrics in (
            resource_metrics.get("scopeMetrics") or resource_metrics.get("instrumentationLibraryMetrics") or []
        ):
            if not isinstance(scope_metrics, dict):
                continue
            for metric in scope_metrics.get("metrics") or []:
                if isinstance(metric, dict) and metric.get("name"):
                    samples.extend(_metric(metric, base))
    return samples


def _metric(metric: dict, base: dict) -> list[Sample]:
    name = str(metric["name"])[:200]
    unit = clip(metric.get("unit"), 32)
    out = []

    def add(sample_name, point, value, kind, extra=None):
        if value is None:
            return
        labels = _labels(point, base)
        if extra:
            labels.update(extra)
        out.append(
            Sample(
                name=sample_name,
                labels=labels,
                value=value,
                ts=_nano_time(point.get("timeUnixNano")),
                unit=unit,
                kind=kind,
            )
        )

    if "gauge" in metric:
        for point in (metric["gauge"] or {}).get("dataPoints") or []:
            add(name, point, _point_value(point), "gauge")
    elif "sum" in metric:
        data = metric["sum"] or {}
        kind = "counter" if data.get("isMonotonic") else "gauge"
        for point in data.get("dataPoints") or []:
            add(name, point, _point_value(point), kind)
    elif "histogram" in metric or "exponentialHistogram" in metric:
        data = metric.get("histogram") or metric.get("exponentialHistogram") or {}
        for point in data.get("dataPoints") or []:
            add(f"{name}_count", point, _number(point.get("count")), "counter")
            add(f"{name}_sum", point, _number(point.get("sum")), "counter")
    elif "summary" in metric:
        for point in (metric["summary"] or {}).get("dataPoints") or []:
            add(f"{name}_count", point, _number(point.get("count")), "counter")
            add(f"{name}_sum", point, _number(point.get("sum")), "counter")
            for quantile in point.get("quantileValues") or []:
                q = _number(quantile.get("quantile"))
                if q is not None:
                    add(name, point, _number(quantile.get("value")), "summary", {"quantile": f"{q:g}"})
    return out


# ---------------------------------------------------------------------- JSON lines

_TIME_KEYS = ("ts", "timestamp", "time", "@timestamp")
_SEVERITY_KEYS = ("severity", "level", "lvl")
_MESSAGE_KEYS = ("message", "msg", "log")


def _windows_entry(item: dict, now: datetime) -> LogEntry:
    fields = windows_fields(item)
    return LogEntry(
        ts=fields["ts"] or now,
        source="json",
        host=clip(fields["host"]),
        app=clip(fields["app"]),
        severity=fields["severity"],
        facility=None,
        message=fields["message"][:MAX_MESSAGE],
        attrs=_attrs(fields["attrs"]),
    )


def _json_entry(item: dict, now: datetime) -> LogEntry | None:
    if is_windows_event(item):
        return _windows_entry(item, now)
    item = dict(item)
    ts = None
    for key in _TIME_KEYS:
        if key in item:
            ts = parse_time(item.pop(key))
            break
    severity = 6
    for key in _SEVERITY_KEYS:
        if key in item:
            severity = severity_number(item.pop(key), 6)
            break
    message = None
    for key in _MESSAGE_KEYS:
        if key in item:
            message = item.pop(key)
            break
    if message is None:
        return None
    if not isinstance(message, str):
        message = json.dumps(message, default=str)
    host = item.pop("host", None) or item.pop("hostname", None)
    app = item.pop("app", None) or item.pop("service", None) or item.pop("appname", None)
    return LogEntry(
        ts=ts or now,
        source="json",
        host=clip(host if isinstance(host, str) else None),
        app=clip(app if isinstance(app, str) else None),
        severity=severity,
        facility=None,
        message=message[:MAX_MESSAGE],
        attrs=_attrs(item),
    )


def parse_json_lines(text: str, now: datetime | None = None) -> tuple[list[LogEntry], int]:
    """(entries, rejected) from one JSON object per line, or one JSON array of objects."""
    now = now or datetime.now(UTC)
    stripped = text.strip()
    items: list = []
    rejected = 0
    if stripped.startswith("["):
        try:
            loaded = json.loads(stripped)
        except ValueError:
            return [], 1
        items = loaded if isinstance(loaded, list) else []
    else:
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                rejected += 1
    entries = []
    for item in items:
        entry = _json_entry(item, now) if isinstance(item, dict) else None
        if entry is None:
            rejected += 1
        else:
            entries.append(entry)
    return entries, rejected
