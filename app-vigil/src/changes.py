"""What changed? A window compared with the window of the same length just before it.

Each difference gets a score on one scale, so hosts, severities, patterns and metrics can be
ranked in one list: for counts the log2 of the ratio (with +1 so silence is not infinite),
weighted by how many lines it is about; for metrics how many standard deviations the mean moved,
divided by 3 (3 sigma counts like a doubling). Small changes are left out.
"""

import json
import math
from urllib.parse import quote

from .records import SEVERITIES

MIN_SCORE = 1.0
MAX_ITEMS = 40
MIN_SIGMA = 3.0


def count_score(cur: int, prev: int) -> float:
    """How much a count changed: 1 is a doubling (or halving) of a count of a few dozen."""
    ratio = abs(math.log2((cur + 1) / (prev + 1)))
    weight = min(1.0, math.log10(max(cur, prev) + 1) / 2)
    return round(ratio * weight, 3)


def _times(n: float) -> str:
    return f"{n:.0f}x" if n >= 10 else f"{n:.1f}x"


def describe_count(cur: int, prev: int, what: str) -> str:
    if prev == 0:
        return f"{cur} {what}, none in the window before"
    if cur == 0:
        return f"no {what}, {prev} in the window before"
    if cur >= prev:
        return f"{cur} {what}, {_times(cur / prev)} the {prev} before"
    return f"{cur} {what}, down from {prev}"


def _link(page: str, q: str, start: float, end: float) -> str:
    return f"#{page}/{quote(q, safe='')}/{int(start)}/{int(end)}"


def _quote_value(value: str) -> str:
    return f'"{value}"' if any(c.isspace() or c == '"' for c in value) else value


def items(data: dict, start: float, end: float) -> list[dict]:
    """The ranked list for /api/changes from Database.change_data()."""
    out = []
    for row in data.get("hosts", []):
        cur, prev = row["cur"], row["prev"]
        score = count_score(cur, prev)
        if score < MIN_SCORE:
            continue
        host = row["host"]
        if cur == 0:
            title = f"{host} went silent"
        elif prev == 0:
            title = f"{host} started sending"
        else:
            title = f"{host} sends {'more' if cur > prev else 'less'}"
        out.append(
            {
                "kind": "host",
                "title": title,
                "detail": describe_count(cur, prev, "log lines"),
                "score": score,
                "link": _link("logs", f"host:{_quote_value(host)}", start, end),
            }
        )
    for row in data.get("severities", []):
        severity = row["severity"]
        if not 0 <= severity <= 4:
            continue  # notice, info and debug volumes are covered by the hosts
        cur, prev = row["cur"], row["prev"]
        score = count_score(cur, prev) * (1.5 if severity <= 3 else 1.0)
        if score < MIN_SCORE or (cur <= prev and severity <= 3 and cur > 0):
            continue
        name = SEVERITIES[severity]
        out.append(
            {
                "kind": "severity",
                "title": f"{'More' if cur > prev else 'Fewer'} {name} messages",
                "detail": describe_count(cur, prev, f"{name} lines"),
                "score": round(score, 3),
                "link": _link("logs", f"severity:{name}", start, end),
            }
        )
    for row in data.get("patterns", []):
        cur, prev = row["cur"], row["prev"]
        new = row.get("first_seen") is not None and row["first_seen"] >= start
        score = count_score(cur, prev) + (0.5 if new else 0)
        if score < MIN_SCORE or cur < prev:
            continue
        template = row["template"]
        short = template if len(template) <= 80 else template[:77] + "..."
        out.append(
            {
                "kind": "pattern",
                "title": f"{'New pattern' if new else 'Pattern grew'}: {short}",
                "detail": describe_count(cur, prev, "lines"),
                "score": round(score, 3),
                "link": _link("logs", f"pattern:{row['id']}", start, end),
            }
        )
    for row in data.get("metrics", []):
        cur, mean, std, n = row["cur"], row["prev_mean"], row["prev_std"], row["prev_n"]
        if cur is None or mean is None or not n or n < 5:
            continue
        sigma = max(std or 0.0, abs(mean) * 0.01, 1e-9)
        z = abs(cur - mean) / sigma
        if z < MIN_SIGMA:
            continue
        labels = row.get("labels") or {}
        label_text = ", ".join(f"{k}={v}" for k, v in sorted(labels.items())[:4])
        name = row["name"]
        out.append(
            {
                "kind": "metric",
                "title": f"{name}{' {' + label_text + '}' if label_text else ''} {'rose' if cur > mean else 'fell'}",
                "detail": f"mean {cur:.4g}, was {mean:.4g} +/- {std or 0:.2g} ({z:.1f} sigma)",
                "score": round(min(z / 3, 10.0), 3),
                "link": _metric_link(name, labels, start, end),
            }
        )
    out.sort(key=lambda item: item["score"], reverse=True)
    return out[:MAX_ITEMS]


def _metric_link(name: str, labels: dict, start: float, end: float) -> str:
    """#metrics/<the Metrics page's queries as JSON>/<from>/<to>, as the page puts it in its hash."""
    query: dict = {"name": name}
    if labels:
        query["filter"] = labels
    return _link("metrics", json.dumps([query], separators=(",", ":"), sort_keys=True), start, end)
