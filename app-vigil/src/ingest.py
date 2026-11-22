"""The ingest port (4318): OTLP/HTTP JSON logs and metrics, and JSON-lines logs.

    POST /v1/logs       OTLP logs (JSON encoding)
    POST /v1/metrics    OTLP metrics (JSON encoding)
    POST /ingest/logs   one JSON object per line, or a JSON array
    GET  /health        for load balancers and docker health checks

With an ingest_token set, every request needs "Authorization: Bearer <token>". Without one only
senders on private networks (RFC 1918, IPv6 ULA, loopback) are accepted: the port is open on
the host, and logs written by strangers would be worse than none.

Also here: the Intake every receiver hands its records to, which counts them per source and caps
each sender at RATE_LIMIT records per second (the excess is dropped and counted), so one chatty
or broken device can't push everyone else out of the write queue.
"""

import hmac
import ipaddress
import json
import logging
import time

from aiohttp import web

from .database import Sources, Writer
from .otlp import parse_json_lines, parse_logs, parse_metrics
from .records import LogEntry, Sample

logger = logging.getLogger(__name__)

MAX_BODY = 5 * 1024 * 1024
RATE_LIMIT = 5_000


class RateLimiter:
    """At most `limit` records per second per key (a fixed one-second window)."""

    def __init__(self, limit: int = RATE_LIMIT):
        self.limit = limit
        self._windows: dict[str, list] = {}

    def allow(self, key: str, count: int = 1, now: float | None = None) -> int:
        """How many of `count` records may pass."""
        second = int(now if now is not None else time.monotonic())
        window = self._windows.get(key)
        if window is None or window[0] != second:
            if len(self._windows) > 10_000:
                self._windows = {k: w for k, w in self._windows.items() if w[0] == second}
            window = self._windows[key] = [second, 0]
        allowed = max(0, min(count, self.limit - window[1]))
        window[1] += allowed
        return allowed


class Intake:
    """Where every receiver puts what it got: rate cap per sender, source counts, write queue."""

    def __init__(self, writer: Writer, sources: Sources, limit: int = RATE_LIMIT):
        self.writer = writer
        self.sources = sources
        self.limiter = RateLimiter(limit)
        self.rate_limited = 0

    @property
    def dropped(self) -> int:
        return self.writer.dropped + self.rate_limited

    def logs(self, entries: list[LogEntry], kind: str, sender: str) -> int:
        allowed = self.limiter.allow(sender or "?", len(entries))
        if allowed < len(entries):
            self.rate_limited += len(entries) - allowed
        counts: dict[str, list] = {}
        for entry in entries[:allowed]:
            name = entry.host or entry.app or sender or "?"
            item = counts.setdefault(name, [0, sender or entry.host])
            item[0] += 1
            self.writer.put_log(entry)
        for name, (count, host) in counts.items():
            self.sources.seen(kind, name, host, count)
        return allowed

    def syslog(self, entry: LogEntry, sender: str) -> None:
        self.logs([entry], "syslog", sender)

    def samples(self, samples: list[Sample], kind: str, sender: str, name: str | None = None) -> int:
        allowed = self.limiter.allow(sender or "?", len(samples))
        if allowed < len(samples):
            self.rate_limited += len(samples) - allowed
        if name is not None:
            self.sources.seen(kind, name, sender or None, allowed)
        else:
            counts: dict[str, int] = {}
            for sample in samples[:allowed]:
                key = sample.labels.get("host.name") or sample.labels.get("service.name") or sender or "?"
                counts[key] = counts.get(key, 0) + 1
            for key, count in counts.items():
                self.sources.seen(kind, key, sender or None, count)
        self.writer.put_samples(samples[:allowed])
        return allowed


def is_private(address: str | None) -> bool:
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _PRIVATE_V4)
    return ip in _ULA


_PRIVATE_V4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
_ULA = ipaddress.ip_network("fc00::/7")


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


class IngestServer:
    def __init__(self, port: int, token: str, intake: Intake):
        self.port = port
        self.token = token
        self.intake = intake
        self.app = web.Application(middlewares=[self._auth], client_max_size=MAX_BODY)
        self.app.router.add_post("/v1/logs", self._handle_otlp_logs)
        self.app.router.add_post("/v1/metrics", self._handle_otlp_metrics)
        self.app.router.add_post("/ingest/logs", self._handle_json_lines)
        self.app.router.add_get("/health", self._handle_health)
        self._runner: web.AppRunner | None = None
        self.listening = False

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=2.0)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        except OSError as e:
            logger.error(f"Cannot listen for OpenTelemetry and JSON on port {self.port}: {e}")
            return
        self.listening = True
        mode = "with the ingest token" if self.token else "from private networks only (no ingest_token set)"
        logger.info(f"OpenTelemetry (OTLP/HTTP JSON) and JSON lines on port {self.port}, {mode}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _auth(self, request: web.Request, handler):
        if request.path == "/health":
            return await handler(request)
        if self.token:
            header = request.headers.get("Authorization", "")
            scheme, _, given = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode(), self.token.encode()):
                return _error("A valid Authorization: Bearer <ingest_token> header is needed", 401)
        elif not is_private(request.remote):
            return _error("Set an ingest_token in Vigil's options to accept data from outside the home network", 403)
        return await handler(request)

    async def _body_json(self, request: web.Request):
        content_type = request.headers.get("Content-Type", "")
        if "protobuf" in content_type:
            raise web.HTTPUnsupportedMediaType(
                text=json.dumps({"error": "Only the OTLP JSON encoding is supported (otlphttp: encoding: json)"}),
                content_type="application/json",
            )
        data = await request.read()
        try:
            return json.loads(data)
        except (ValueError, UnicodeDecodeError) as e:
            raise web.HTTPBadRequest(
                text=json.dumps({"error": f"Not valid JSON: {e}"}), content_type="application/json"
            ) from e

    async def _handle_otlp_logs(self, request: web.Request) -> web.Response:
        body = await self._body_json(request)
        if not isinstance(body, dict):
            return _error("Expected an OTLP ExportLogsServiceRequest object", 400)
        entries = parse_logs(body)
        accepted = self.intake.logs(entries, "otlp", request.remote or "")
        rejected = len(entries) - accepted
        result = (
            {"partialSuccess": {"rejectedLogRecords": str(rejected), "errorMessage": "rate limit"}} if rejected else {}
        )
        return web.json_response(result)

    async def _handle_otlp_metrics(self, request: web.Request) -> web.Response:
        body = await self._body_json(request)
        if not isinstance(body, dict):
            return _error("Expected an OTLP ExportMetricsServiceRequest object", 400)
        samples = parse_metrics(body)
        accepted = self.intake.samples(samples, "otlp", request.remote or "")
        rejected = len(samples) - accepted
        result = (
            {"partialSuccess": {"rejectedDataPoints": str(rejected), "errorMessage": "rate limit"}} if rejected else {}
        )
        return web.json_response(result)

    async def _handle_json_lines(self, request: web.Request) -> web.Response:
        text = (await request.read()).decode("utf-8", errors="replace")
        entries, rejected = parse_json_lines(text)
        accepted = self.intake.logs(entries, "json", request.remote or "")
        return web.json_response({"accepted": accepted, "rejected": rejected + len(entries) - accepted})

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True})
