"""Timestamps: Home Assistant and FireServiceRota send ISO 8601 with an offset."""

from datetime import UTC, datetime


def parse_ts(value) -> datetime | None:
    """An aware datetime from an ISO string (or None)."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def norm_ts(value) -> str | None:
    """The same moment as a fixed-format UTC string, so stored timestamps compare as text."""
    parsed = parse_ts(value)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00") if parsed else None


def utc_now() -> datetime:
    return datetime.now(UTC)


def now_ts() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
