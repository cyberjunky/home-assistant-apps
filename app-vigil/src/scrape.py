"""The Prometheus scraper: every scrape target's /metrics, in the text exposition format 0.0.4.

Counters, gauges and untyped metrics become points as they are. Of a summary or histogram only
the _sum and _count series (and a summary's quantiles) are kept: the buckets would be a series
per bucket boundary, many times the rest together, for charts nobody draws here.
Every series gets the labels job (the target's name) and instance (host:port of its URL), and
each scrape an `up` point (1 when it worked, 0 when not), as Prometheus does.
"""

import asyncio
import contextlib
import logging
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

import aiohttp

from .config import ScrapeTarget
from .records import Sample

logger = logging.getLogger(__name__)

MAX_BODY = 20 * 1024 * 1024
MAX_SAMPLES = 20_000
TIMEOUT = 10

_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_SUFFIXES = ("_total", "_count", "_sum", "_bucket", "_created", "_info")


class ParseError(ValueError):
    pass


def _parse_labels(text: str, pos: int) -> tuple[dict[str, str], int]:
    """The labels from text[pos] == '{' to its '}', with \\\\, \\" and \\n escapes."""
    labels: dict[str, str] = {}
    pos += 1
    n = len(text)
    while True:
        while pos < n and text[pos] in " ,":
            pos += 1
        if pos < n and text[pos] == "}":
            return labels, pos + 1
        match = _LABEL_NAME.match(text, pos)
        if not match:
            raise ParseError("bad label name")
        name = match.group(0)
        pos = match.end()
        while pos < n and text[pos] == " ":
            pos += 1
        if text[pos : pos + 2] != '="':
            raise ParseError("bad label")
        pos += 2
        value = []
        while pos < n and text[pos] != '"':
            if text[pos] == "\\" and pos + 1 < n:
                nxt = text[pos + 1]
                value.append("\n" if nxt == "n" else nxt)
                pos += 2
            else:
                value.append(text[pos])
                pos += 1
        if pos >= n:
            raise ParseError("unterminated label value")
        labels[name] = "".join(value)
        pos += 1


def _value(text: str) -> float | None:
    """A sample value; None for NaN and the infinities (they can't be charted or averaged)."""
    try:
        value = float(text)
    except ValueError as e:
        raise ParseError(f"bad value {text!r}") from e
    return value if math.isfinite(value) else None


def _kind_of(name: str, types: dict[str, str]) -> tuple[str | None, str]:
    """(type, base name) of a sample: its own # TYPE, or that of the metric it is a part of."""
    if name in types:
        return types[name], name
    for suffix in _SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in types:
            return types[name[: -len(suffix)]], name[: -len(suffix)]
    return None, name


def parse(text: str, limit: int = MAX_SAMPLES) -> tuple[list[Sample], int]:
    """(samples, lines that could not be read) from a text exposition."""
    types: dict[str, str] = {}
    samples: list[Sample] = []
    errors = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line.split(None, 3)
            if len(parts) >= 4 and parts[1] == "TYPE":
                types[parts[2]] = parts[3].strip().lower()
            continue
        try:
            match = _NAME.match(line)
            if not match:
                raise ParseError("bad metric name")
            name = match.group(0)
            pos = match.end()
            labels: dict[str, str] = {}
            if pos < len(line) and line[pos] == "{":
                labels, pos = _parse_labels(line, pos)
            rest = line[pos:].split()
            if not rest:
                raise ParseError("no value")
            value = _value(rest[0])
            ts = None
            if len(rest) > 1:
                with contextlib.suppress(ValueError, OverflowError, OSError):
                    ts = datetime.fromtimestamp(int(rest[1]) / 1000, UTC)
        except (ParseError, IndexError):
            errors += 1
            continue
        if value is None:
            continue
        kind, base = _kind_of(name, types)
        if kind in ("histogram", "gaugehistogram"):
            if name.endswith("_bucket") or name == base:
                continue
            kind = "counter"
        elif kind == "summary":
            kind = "counter" if name != base else "summary"
        elif kind not in ("counter", "gauge"):
            kind = "counter" if name.endswith("_total") else "untyped"
        samples.append(Sample(name=name, labels=labels, value=value, ts=ts, kind=kind))
        if len(samples) >= limit:
            break
    return samples, errors


def instance_of(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.hostname}:{port}"


class Scraper:
    """One task per target; samples go to sink(samples), the outcome to report(target, error)."""

    def __init__(
        self,
        targets: list[ScrapeTarget],
        sink: Callable[[list[Sample]], None],
        report: Callable[[ScrapeTarget, str, int, str | None], None],
    ):
        self.targets = targets
        self.sink = sink
        self.report = report  # (target, instance, samples, error)
        self._tasks: list[asyncio.Task] = []
        self._session: aiohttp.ClientSession | None = None

    def start(self) -> None:
        if not self.targets:
            return
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            headers={"Accept": "text/plain;version=0.0.4;q=1,*/*;q=0.1", "User-Agent": "Vigil"},
        )
        for i, target in enumerate(self.targets):
            self._tasks.append(asyncio.create_task(self._loop(target, i), name=f"scrape-{target.name}"))
        logger.info(f"Scraping {len(self.targets)} Prometheus target(s)")

    async def _loop(self, target: ScrapeTarget, index: int) -> None:
        # Spread the targets over the first interval instead of scraping them all at once
        await asyncio.sleep(min(target.interval, 10) * index / max(1, len(self.targets)))
        while True:
            started = time.monotonic()
            await self.scrape_once(target)
            await asyncio.sleep(max(1.0, target.interval - (time.monotonic() - started)))

    async def scrape_once(self, target: ScrapeTarget) -> None:
        instance = instance_of(target.url)
        base = {"job": target.name, "instance": instance}
        now = datetime.now(UTC)
        error = None
        samples: list[Sample] = []
        started = time.monotonic()
        try:
            if self._session is None:
                raise RuntimeError("the scraper is not started")
            async with self._session.get(target.url) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                body = await response.content.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise RuntimeError("the answer is larger than 20 MB")
            samples, bad = parse(body.decode("utf-8", errors="replace"))
            if bad and not samples:
                raise RuntimeError(f"{bad} lines could not be read; is this a Prometheus /metrics page?")
        except (aiohttp.ClientError, TimeoutError, RuntimeError, UnicodeError) as e:
            error = str(e) or type(e).__name__
            logger.debug(f"Scraping {target.name} failed: {error}")
        for sample in samples:
            sample.labels = {**sample.labels, **base}
            sample.ts = sample.ts or now
        samples.append(Sample("up", dict(base), 0.0 if error else 1.0, now, kind="gauge"))
        samples.append(
            Sample("scrape_duration_seconds", dict(base), round(time.monotonic() - started, 4), now, "s", "gauge")
        )
        self.sink(samples)
        self.report(target, instance, len(samples) - 2, error)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._session is not None:
            await self._session.close()
