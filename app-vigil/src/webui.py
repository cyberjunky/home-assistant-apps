"""Web dashboard: serves the single-page UI and its JSON API (behind Home Assistant ingress)."""

import json
import logging
import os
import re
import time
from pathlib import Path

import asyncpg
from aiohttp import web

from . import changes
from .config import ADDON_VERSION, DATA_DIR, Config
from .database import METRIC_AGGS, Database, NotConnectedError, Writer
from .donate import DonatePrompt
from .ingest import Intake
from .query import QueryError
from .rules import TEMPLATES, HomeAssistant, RuleEngine, validate

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"

# Home Assistant's ingress proxy: the only client allowed when running as an app
INGRESS_ADDRESS = "172.30.32.2"
OVERVIEW_HOURS = (1, 6, 24, 168)
MAX_SPAN = 400 * 86400
LABEL_KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
PANEL_TYPES = ("timeseries", "stat", "logs", "bar", "table")
PATTERN_SORTS = ("count", "new", "spike")
HOST_PLACEHOLDER = "HOME_ASSISTANT_IP"


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, default=str), content_type="application/json", status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"error": message}, status)


class BadRequest(Exception):
    """A parameter that can't be used (the message says which)."""


def _float(request: web.Request, name: str, default: float | None = None) -> float | None:
    value = request.query.get(name, "")
    if value == "":
        return default
    try:
        number = float(value)
    except ValueError as e:
        raise BadRequest(f"{name} must be a number") from e
    if number != number or number in (float("inf"), float("-inf")):
        raise BadRequest(f"{name} must be a number")
    return number


def _int(request: web.Request, name: str, default: int, low: int, high: int) -> int:
    value = _float(request, name, default)
    return int(min(high, max(low, value)))


def _window(request: web.Request, default_span: float = 3600) -> tuple[float, float]:
    """from and to in epoch seconds (default: the last default_span seconds)."""
    end = _float(request, "to") or time.time()
    start = _float(request, "from") or end - default_span
    if end <= start:
        raise BadRequest("to must be after from")
    if end - start > MAX_SPAN:
        raise BadRequest("The window is too long (at most 400 days)")
    if start < 0:
        raise BadRequest("from is before 1970")
    return start, end


def _id(body: dict, name: str = "id") -> int:
    try:
        value = int(body.get(name))
    except (TypeError, ValueError) as e:
        raise BadRequest(f"{name} must be a number") from e
    if value < 1:
        raise BadRequest(f"{name} must be a number")
    return value


def setup_examples(syslog_port: int | None, otlp_port: int | None, token_set: bool) -> dict:
    """Copy-paste configuration for the Sources page (HOME_ASSISTANT_IP is for the page to fill in)."""
    host = HOST_PLACEHOLDER
    syslog = syslog_port or 514
    otlp = otlp_port or 4318
    auth_header = "Authorization: Bearer <ingest_token>"
    return {
        "rsyslog": (
            "# /etc/rsyslog.d/90-vigil.conf (then: systemctl restart rsyslog)\n"
            f"*.* @@{host}:{syslog}    # TCP; use a single @ for UDP"
        ),
        "mikrotik": (
            # Only parameters every RouterOS knows: the format setting differs per version
            # (bsd-syslog=yes, remote-log-format=..), and Vigil reads all of them
            f"/system logging action add name=vigil target=remote remote={host} remote-port={syslog}\n"
            "/system logging add action=vigil topics=info\n"
            "/system logging add action=vigil topics=warning\n"
            "/system logging add action=vigil topics=error\n"
            "/system logging add action=vigil topics=critical"
        ),
        "netgear": (
            "Netgear switches and routers: Management (or Advanced > Administration) > Logs / Syslog: "
            f"enable remote logging, server {host}, port {syslog} (UDP), severity Informational."
        ),
        "vector": (
            "[sinks.vigil]\n"
            'type = "http"\n'
            'inputs = ["my_source"]\n'
            f'uri = "http://{host}:{otlp}/ingest/logs"\n'
            'encoding.codec = "json"\n'
            'framing.method = "newline_delimited"\n'
            + ('request.headers.Authorization = "Bearer <ingest_token>"\n' if token_set else "")
        ),
        "otelcol": (
            "exporters:\n"
            "  otlphttp/vigil:\n"
            f"    endpoint: http://{host}:{otlp}\n"
            "    encoding: json\n"
            + ('    headers:\n      Authorization: "Bearer <ingest_token>"\n' if token_set else "")
            + "service:\n  pipelines:\n    logs:\n      exporters: [otlphttp/vigil]\n"
            "    metrics:\n      exporters: [otlphttp/vigil]\n"
        ),
        "windows": (
            "# C:\\Program Files\\fluent-bit\\conf\\fluent-bit.yaml\n"
            "service:\n"
            "  flush: 5\n"
            "pipeline:\n"
            "  inputs:\n"
            "    - name: winevtlog\n"
            "      channels: Security,System,Application,Microsoft-Windows-Windows Defender/Operational"
            ",Microsoft-Windows-Sysmon/Operational\n"
            "      # with PowerShell script block logging on, add: ,Microsoft-Windows-PowerShell/Operational\n"
            "      ignore_missing_channels: true\n"
            "      interval_sec: 5\n"
            "      db: C:\\ProgramData\\fluent-bit\\winevtlog.db\n"
            "      event_data_as_map: true\n"
            "      string_inserts: false\n"
            "  outputs:\n"
            "    - name: http\n"
            "      match: '*'\n"
            f"      host: {host}\n"
            f"      port: {otlp}\n"
            "      uri: /ingest/logs\n"
            "      format: json_lines\n"
            "      json_date_key: false\n"
            + ("      header:\n        - Authorization Bearer <ingest_token>\n" if token_set else "")
            + "\n# As a service, if the installer made none (Command Prompt as administrator):\n"
            "#   mkdir C:\\ProgramData\\fluent-bit\n"
            '#   sc.exe create fluent-bit binpath= "\\"C:\\Program Files\\fluent-bit\\bin\\fluent-bit.exe\\"'
            ' -c \\"C:\\Program Files\\fluent-bit\\conf\\fluent-bit.yaml\\"" start= auto\n'
            "#   sc.exe start fluent-bit\n"
            "# Failed logons are only logged when logon auditing is on:\n"
            '#   auditpol /set /subcategory:"Logon" /success:enable /failure:enable'
        ),
        "node_exporter": (
            "# In Vigil's options (Configuration tab):\n"
            "scrape_targets:\n"
            "  - name: my-server\n"
            "    url: http://my-server:9100/metrics\n"
            "    interval: 60"
        ),
        "json_lines": (
            f"curl -X POST http://{host}:{otlp}/ingest/logs"
            + (f" -H '{auth_header}'" if token_set else "")
            + ' --data-binary \'{"host": "nas", "app": "backup", "level": "error", "message": "Backup failed"}\''
        ),
    }


class WebUI:
    """The app's web server."""

    def __init__(
        self,
        port: int,
        config: Config,
        database: Database,
        writer: Writer,
        intake: Intake,
        engine: RuleEngine,
        home_assistant: HomeAssistant,
        receivers: dict,
    ):
        self.port = port
        self.config = config
        self.database = database
        self.writer = writer
        self.intake = intake
        self.engine = engine
        self.home_assistant = home_assistant
        self.receivers = receivers  # {syslog_udp, syslog_tcp, otlp}: port number or False, kept up to date
        self.ingress_only = bool(os.environ.get("SUPERVISOR_TOKEN"))
        self.app = web.Application(
            middlewares=[self._ingress_only, self._same_page, self._errors], client_max_size=1024 * 1024
        )
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        get, post = self.app.router.add_get, self.app.router.add_post
        get("/", self._handle_index)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/web/", WEB_DIR)
        get("/api/status", self._handle_status)
        get("/api/overview", self._handle_overview)
        get("/api/logs", self._handle_logs)
        get("/api/logs/histogram", self._handle_histogram)
        get("/api/logs/tail", self._handle_tail)
        get("/api/logs/fields", self._handle_fields)
        get("/api/patterns", self._handle_patterns)
        get("/api/metrics/names", self._handle_metric_names)
        get("/api/metrics/labels", self._handle_metric_labels)
        get("/api/metrics/query", self._handle_metric_query)
        get("/api/changes", self._handle_changes)
        get("/api/sources", self._handle_sources)
        get("/api/rules", self._handle_rules)
        post("/api/rules/save", self._handle_rule_save)
        post("/api/rules/delete", self._handle_rule_delete)
        post("/api/rules/test", self._handle_rule_test)
        get("/api/alerts", self._handle_alerts)
        get("/api/dashboards", self._handle_dashboards)
        post("/api/dashboards/save", self._handle_dashboard_save)
        post("/api/dashboards/delete", self._handle_dashboard_delete)
        get("/api/searches", self._handle_searches)
        post("/api/searches/save", self._handle_search_save)
        post("/api/searches/delete", self._handle_search_delete)

    async def start(self) -> None:
        # handler_cancellation: a query the page cancels (or a closed page) stops in the database too
        self._runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=1.0, handler_cancellation=True)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI listening on port {self.port}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _ingress_only(self, request: web.Request, handler):
        # The logs are private: under Home Assistant only the (authenticated) ingress proxy may ask
        if self.ingress_only and request.remote != INGRESS_ADDRESS:
            return web.Response(status=403, text="Open Vigil from the Home Assistant sidebar")
        return await handler(request)

    @web.middleware
    async def _same_page(self, request: web.Request, handler):
        # Everything that changes something comes from this page's own scripts: they send this
        # header, which a link, a form or another site can't (a cross-site request with it
        # needs a CORS preflight, which this server never allows)
        # (The donate card only records that it was closed; its script is shared by all apps.)
        if (
            request.method not in ("GET", "HEAD")
            and request.headers.get("X-Vigil") != "1"
            and not request.path.startswith("/api/donate")
        ):
            return web.Response(status=403, text="Missing the X-Vigil header")
        return await handler(request)

    @web.middleware
    async def _errors(self, request: web.Request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except (BadRequest, QueryError) as e:
            return _error(str(e), 400)
        except NotConnectedError as e:
            return _error(f"Cannot use the database: {e}", 503)
        except asyncpg.QueryCanceledError:
            return _error("The database took too long to answer; try a shorter window", 504)
        except asyncpg.UndefinedTableError:
            return _error("Vigil's tables are not in the database yet", 503)
        except asyncpg.UniqueViolationError:
            return _error("That name is already taken", 409)
        except asyncpg.PostgresError as e:
            logger.warning(f"Query failed: {e}")
            return _error(f"The database refused the query: {e}", 500)
        except Exception:
            logger.exception(f"{request.method} {request.path} failed")
            return _error("Something went wrong in Vigil; see the app's log", 500)

    async def _body(self, request: web.Request) -> dict:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            raise BadRequest("Expected a JSON object")
        return body

    # ------------------------------------------------------------------ page and status

    async def _handle_index(self, request: web.Request) -> web.Response:
        # Behind Home Assistant ingress the dashboard lives under X-Ingress-Path; the
        # <link>/<script> tags in the head can't compute that like the JS does
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        try:
            html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            return web.Response(status=503, text="The Vigil page is missing from this build")
        html = html.replace("__ADDON_VERSION__", ADDON_VERSION).replace("__BASE__", base)
        return web.Response(
            text=html,
            content_type="text/html",
            # Never let the browser serve a stale cached page after an app update
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    async def _handle_status(self, _request: web.Request) -> web.Response:
        info = self.database.info
        result = {
            "version": ADDON_VERSION,
            "connected": self.database.connected,
            "database": {
                "host": info.host,
                "port": self.config.db_port,
                "name": self.config.db_name,
                "user": self.config.db_user,
                "timescaledb": info.timescaledb,
                "version": info.version,
            },
            "counts": {"logs_24h": None, "series": None, "rules": None, "firing": None},
            "receivers": dict(self.receivers),
            "queue": {
                "size": self.writer.queue.qsize(),
                "max": self.writer.queue.maxsize,
                "dropped": self.intake.dropped,
                "rate_limited": self.intake.rate_limited,
            },
            "writer": {
                "logs": self.writer.written_logs,
                "points": self.writer.written_points,
                "last_write": self.writer.last_write,
                "last_write_seconds": self.writer.last_write_seconds,
                "error": self.writer.last_error,
            },
        }
        if self.database.connected:
            try:
                result["counts"] = await self.database.counts()
            except (NotConnectedError, asyncpg.PostgresError) as e:
                result["error"] = str(e)
        if not self.database.connected:
            result["connected"] = False
            result["error"] = self.database.last_error or "not connected"
        return _json(result)

    async def _handle_overview(self, request: web.Request) -> web.Response:
        hours = _int(request, "hours", 24, 1, 168)
        return _json(await self.database.overview(hours if hours in OVERVIEW_HOURS else 24))

    # ------------------------------------------------------------------ logs

    async def _handle_logs(self, request: web.Request) -> web.Response:
        start, end = _window(request)
        limit = _int(request, "limit", 200, 1, 1000)
        before = _float(request, "before")
        return _json(await self.database.logs(request.query.get("q", ""), start, end, limit, before))

    async def _handle_histogram(self, request: web.Request) -> web.Response:
        start, end = _window(request)
        buckets = _int(request, "buckets", 60, 1, 500)
        group = request.query.get("group") or None
        if group not in (None, "host", "app", "source"):
            raise BadRequest("group must be host, app or source")
        return _json(await self.database.histogram(request.query.get("q", ""), start, end, buckets, group))

    async def _handle_tail(self, request: web.Request) -> web.Response:
        after = _float(request, "after") or time.time() - 60
        return _json(await self.database.tail(request.query.get("q", ""), after))

    async def _handle_fields(self, request: web.Request) -> web.Response:
        start, end = _window(request)
        result = await self.database.fields(start, end, request.query.get("q", ""))
        field = request.query.get("field")
        if field:
            if field not in result:
                raise BadRequest("field must be hosts, apps, sources or severities")
            result = {field: result[field]}
        return _json(result)

    async def _handle_patterns(self, request: web.Request) -> web.Response:
        start, end = _window(request, 86400)
        sort = request.query.get("sort", "count")
        if sort not in PATTERN_SORTS:
            raise BadRequest("sort must be count, new or spike")
        return _json(await self.database.patterns(start, end, sort, request.query.get("q", "")))

    # ------------------------------------------------------------------ metrics

    async def _handle_metric_names(self, request: web.Request) -> web.Response:
        return _json({"names": await self.database.metric_names(request.query.get("q", "")[:200])})

    async def _handle_metric_labels(self, request: web.Request) -> web.Response:
        name = request.query.get("name", "")
        if not name:
            raise BadRequest("name is needed")
        return _json({"labels": await self.database.metric_labels(name)})

    async def _handle_metric_query(self, request: web.Request) -> web.Response:
        name = request.query.get("name", "")
        if not name:
            raise BadRequest("name is needed")
        filters = {}
        if request.query.get("filter"):
            try:
                filters = json.loads(request.query["filter"])
            except ValueError as e:
                raise BadRequest("filter must be a JSON object") from e
            if not isinstance(filters, dict) or not all(isinstance(v, str) for v in filters.values()):
                raise BadRequest("filter must be a JSON object of label: value texts")
        agg = request.query.get("agg", "avg")
        if agg not in METRIC_AGGS:
            raise BadRequest(f"agg must be one of {', '.join(METRIC_AGGS)}")
        group = request.query.get("group") or None
        if group and not LABEL_KEY.match(group):
            raise BadRequest("Invalid group label")
        start, end = _window(request)
        step = _int(request, "step", 0, 0, 86400) or None
        anomaly = request.query.get("anomaly") in ("1", "true")
        return _json(await self.database.metric_query(name, filters, agg, group, start, end, step, anomaly))

    async def _handle_changes(self, request: web.Request) -> web.Response:
        start, end = _window(request)
        if end - start > 7 * 86400:
            raise BadRequest("Compare at most 7 days")
        data = await self.database.change_data(start, end)
        return _json({"items": changes.items(data, start, end)})

    # ------------------------------------------------------------------ sources

    async def _handle_sources(self, _request: web.Request) -> web.Response:
        rows = await self.database.sources()
        for row in rows:
            row["rate_per_min"] = self.intake.sources.rate_per_min(row["kind"], row["name"])
        return _json(
            {
                "sources": rows,
                "setup": {
                    "syslog_port": self.config.public_syslog_port,
                    "otlp_port": self.config.public_ingest_port,
                    "token_set": bool(self.config.ingest_token),
                    "host_placeholder": HOST_PLACEHOLDER,
                    "examples": setup_examples(
                        self.config.public_syslog_port, self.config.public_ingest_port, bool(self.config.ingest_token)
                    ),
                },
            }
        )

    # ------------------------------------------------------------------ rules and alerts

    async def _handle_rules(self, _request: web.Request) -> web.Response:
        rules = await self.database.rules()
        for rule in rules:
            rule["error"] = self.engine.errors.get(rule["id"])
        return _json({"rules": rules, "templates": TEMPLATES})

    async def _handle_rule_save(self, request: web.Request) -> web.Response:
        try:
            rule = validate(await self._body(request))
        except ValueError as e:
            raise BadRequest(str(e)) from e
        before = await self.database.rule(rule["id"]) if rule["id"] else None
        if rule["id"] and before is None:
            return _error("Unknown rule", 404)
        rule_id = await self.database.save_rule(rule)
        after = await self.database.rule(rule_id)
        if before and before["state"] == "firing" and after and after["state"] != "firing":
            await self.home_assistant.dismiss(rule_id)
        return _json({"ok": True, "id": rule_id, "rule": after})

    async def _handle_rule_delete(self, request: web.Request) -> web.Response:
        rule_id = _id(await self._body(request))
        if not await self.database.delete_rule(rule_id):
            return _error("Unknown rule", 404)
        self.engine.errors.pop(rule_id, None)
        await self.home_assistant.dismiss(rule_id)
        return _json({"ok": True})

    async def _handle_rule_test(self, request: web.Request) -> web.Response:
        try:
            rule = validate(await self._body(request))
        except ValueError as e:
            raise BadRequest(str(e)) from e
        return _json(await self.engine.test(rule))

    async def _handle_alerts(self, request: web.Request) -> web.Response:
        start, end = _window(request, 7 * 86400)
        return _json({"alerts": await self.database.alerts(start, end)})

    # ------------------------------------------------------------------ dashboards and searches

    async def _handle_dashboards(self, _request: web.Request) -> web.Response:
        return _json({"dashboards": await self.database.dashboards()})

    @staticmethod
    def _panels(value) -> list:
        if not isinstance(value, list) or len(value) > 50:
            raise BadRequest("panels must be a list (at most 50)")
        panels = []
        for i, panel in enumerate(value):
            if not isinstance(panel, dict):
                raise BadRequest(f"Panel {i + 1} is not an object")
            if panel.get("type") not in PANEL_TYPES:
                raise BadRequest(f"Panel {i + 1}: type must be one of {', '.join(PANEL_TYPES)}")
            if not isinstance(panel.get("query", {}), dict):
                raise BadRequest(f"Panel {i + 1}: query must be an object")
            try:
                w, h = int(panel.get("w", 2)), int(panel.get("h", 1))
            except (TypeError, ValueError) as e:
                raise BadRequest(f"Panel {i + 1}: w and h must be numbers") from e
            panels.append(
                {
                    **panel,
                    "id": str(panel.get("id") or f"p{i + 1}")[:64],
                    "title": str(panel.get("title") or "")[:200],
                    "w": min(4, max(1, w)),
                    "h": min(3, max(1, h)),
                    "query": panel.get("query") or {},
                }
            )
        if len(json.dumps(panels)) > 200_000:
            raise BadRequest("The dashboard is too large")
        return panels

    async def _handle_dashboard_save(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 100:
            raise BadRequest("A dashboard needs a name (at most 100 characters)")
        dashboard_id = _id(body) if body.get("id") else None
        try:
            saved = await self.database.save_dashboard(dashboard_id, name, self._panels(body.get("panels", [])))
        except KeyError:
            return _error("Unknown dashboard", 404)
        return _json({"ok": True, "id": saved})

    async def _handle_dashboard_delete(self, request: web.Request) -> web.Response:
        if not await self.database.delete_dashboard(_id(await self._body(request))):
            return _error("Unknown dashboard", 404)
        return _json({"ok": True})

    async def _handle_searches(self, _request: web.Request) -> web.Response:
        return _json({"searches": await self.database.searches()})

    async def _handle_search_save(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        name = str(body.get("name") or "").strip()
        query = str(body.get("query") or "")
        if not name or len(name) > 100:
            raise BadRequest("A search needs a name (at most 100 characters)")
        if len(query) > 2000:
            raise BadRequest("The search is too long")
        search_id = _id(body) if body.get("id") else None
        try:
            saved = await self.database.save_search(search_id, name, query)
        except KeyError:
            return _error("Unknown search", 404)
        return _json({"ok": True, "id": saved})

    async def _handle_search_delete(self, request: web.Request) -> web.Response:
        if not await self.database.delete_search(_id(await self._body(request))):
            return _error("Unknown search", 404)
        return _json({"ok": True})
