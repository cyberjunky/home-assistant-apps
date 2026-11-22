"""What the receivers hand to the writer: log entries and metric samples, and severity names.

The parsers (syslog, OTLP, JSON lines, Prometheus) only build these; the writer in database.py
turns them into rows. Keeping them here lets the parsers and their tests run without a database.
"""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Syslog severities, 0 (most severe) .. 7; the names are the ones syslog itself uses
SEVERITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")
# What other senders call them (JSON lines "level", OTLP severityText, query terms)
SEVERITY_ALIASES = {
    "emergency": 0,
    "panic": 0,
    "fatal": 2,
    "critical": 2,
    "error": 3,
    "warn": 4,
    "information": 6,
    "informational": 6,
    "trace": 7,
    "verbose": 7,
}
# The four groups the charts stack (err = 0..3, warning = 4, info = 5..6, debug = 7)
SEVERITY_GROUPS = ("err", "warning", "info", "debug")

# Longest message kept; longer ones are cut (the search index and the pattern miner stay fast)
MAX_MESSAGE = 8192
MAX_FIELD = 255


def severity_group(severity: int) -> str:
    if severity <= 3:
        return "err"
    if severity == 4:
        return "warning"
    if severity <= 6:
        return "info"
    return "debug"


def severity_number(value, default: int | None = None) -> int | None:
    """A severity from a name ("warning", "WARN", "err") or a number ("4", 4); default if unknown."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        number = int(value)
        return number if 0 <= number <= 7 else default
    text = str(value).strip().lower()
    if text.isdigit():
        number = int(text)
        return number if 0 <= number <= 7 else default
    if text in SEVERITIES:
        return SEVERITIES.index(text)
    return SEVERITY_ALIASES.get(text, default)


def otlp_severity(number, text=None) -> int:
    """OTLP SeverityNumber (1..24) to syslog: trace/debug 7, info 6, warn 4, error 3, fatal 2."""
    try:
        n = int(number or 0)
    except (TypeError, ValueError):
        n = 0
    if 1 <= n <= 8:
        return 7
    if 9 <= n <= 12:
        return 6
    if 13 <= n <= 16:
        return 4
    if 17 <= n <= 20:
        return 3
    if 21 <= n <= 24:
        return 2
    return severity_number(text, 6)


def clip(value, limit: int = MAX_FIELD) -> str | None:
    """A short text field (host, app, source names) or None when empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


@dataclass(slots=True)
class LogEntry:
    ts: datetime
    source: str  # 'syslog' | 'otlp' | 'json' | 'fritzbox' | 'vigil'
    host: str | None
    app: str | None
    severity: int
    facility: int | None
    message: str
    attrs: dict = field(default_factory=dict)
    # The text to find the log pattern in, when not the whole message (a Windows event: its
    # first line, which says what happened; the lines below differ per event)
    pattern_text: str | None = None


@dataclass(slots=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float
    ts: datetime | None = None  # None: the time it is written
    unit: str | None = None
    kind: str | None = None  # 'gauge' | 'counter' | 'summary' | 'untyped'


def from_epoch(value, default: datetime | None = None) -> datetime | None:
    """A time from epoch seconds, milliseconds, microseconds or nanoseconds (guessed from the size)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number <= 0:
        return default
    # Seconds until the year 5138; beyond that the number is in a smaller unit
    while number > 1e11:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, UTC)
    except (OverflowError, OSError, ValueError):
        return default


def parse_time(value, default: datetime | None = None) -> datetime | None:
    """A time from an epoch number or an ISO 8601 text (a time without a zone is local time)."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return from_epoch(value, default)
    text = str(value).strip()
    if not text:
        return default
    try:
        return from_epoch(float(text), default)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return default
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(UTC)
