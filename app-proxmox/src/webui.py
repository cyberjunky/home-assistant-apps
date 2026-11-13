"""The dashboard, served through Home Assistant's ingress.

The browser only talks to this app; the API token stays here. Changes to the cluster (power,
firewall rules, a guest's VLAN) only come from Home Assistant's signed-in ingress proxy, and only
when read_only is off.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR, Config
from .donate import DonatePrompt
from .proxmox import POWER_ACTIONS, TIMEFRAMES, Cluster, ProxmoxError, lockout_risk

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
LAYOUT_FILE = DATA_DIR / "layout.json"
MAX_LAYOUT_BYTES = 256 * 1024


def public_config(config: Config) -> dict:
    """The options to show. The token's secret stays out."""
    return {
        "hosts": config.hosts,
        "token_id": config.token_id,
        "verify_ssl": config.verify_ssl,
        "poll_seconds": config.poll_seconds,
        "read_only": config.read_only,
        "log_level": config.log_level,
        "configured": config.configured,
    }


class WebUI:
    """The dashboard's page and its JSON API."""

    def __init__(self, port: int, config: Config, cluster: Cluster):
        self.port = port
        self.config = config
        self.cluster = cluster
        self.app = web.Application()
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/api/status", self._status)
        r.add_get("/api/config", self._conf)
        r.add_get("/api/rrd", self._rrd)
        r.add_get("/api/rrd/cluster", self._cluster_rrd)
        r.add_get("/api/guest/{vmid}", self._guest)
        r.add_get("/api/firewall/log", self._firewall_log)
        r.add_get("/api/backups", self._backups)
        r.add_get("/api/task", self._task)
        r.add_get("/api/layout", self._layout)
        r.add_post("/api/layout", self._save_layout)
        r.add_post("/api/guest/{vmid}/power", self._power)
        r.add_post("/api/guest/{vmid}/vlan", self._vlan)
        r.add_post("/api/firewall/rule", self._rule)
        r.add_post("/api/refresh", self._refresh)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_static("/static", WEB_DIR)

    async def start(self) -> None:
        """Serve until cancelled."""
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info(f"Dashboard on port {self.port}")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(
            text=page,
            content_type="text/html",
            # Never let the browser serve a stale cached page after an update
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"version": APP_VERSION, "now": time.time(), "config": public_config(self.config), **self.cluster.summary()}
        )

    async def _conf(self, request: web.Request) -> web.Response:
        return web.json_response({"conf_at": self.cluster.conf_at, **self.cluster.conf})

    @staticmethod
    def _timeframe(request: web.Request) -> str:
        timeframe = request.query.get("timeframe", "hour")
        if timeframe not in TIMEFRAMES:
            raise web.HTTPBadRequest(text="timeframe must be hour, day, week, month or year")
        return timeframe

    @staticmethod
    def _error(e: ProxmoxError) -> web.Response:
        return web.json_response({"error": str(e)}, status=403 if e.status == 403 else 502)

    async def _rrd(self, request: web.Request) -> web.Response:
        """History of a node, a guest (vmid) or a node's storage."""
        timeframe = self._timeframe(request)
        guest = None
        if request.query.get("vmid"):
            guest = self.cluster.guest(request.query["vmid"])
            if guest is None:
                raise web.HTTPNotFound(text="no such guest")
            node = guest["node"]
        else:
            node = request.query.get("node", "")
            if node not in self.cluster.live.get("node_status", {}):
                raise web.HTTPNotFound(text="no such node, or it is offline")
        try:
            rows = await self.cluster.rrd(node, timeframe, guest=guest, storage=request.query.get("storage", ""))
        except ProxmoxError as e:
            return self._error(e)
        return web.json_response({"rows": rows or []})

    async def _cluster_rrd(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(await self.cluster.cluster_rrd(self._timeframe(request)))
        except ProxmoxError as e:
            return self._error(e)

    async def _guest(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(await self.cluster.guest_details(request.match_info["vmid"]))
        except KeyError as e:
            raise web.HTTPNotFound(text="no such guest") from e
        except ProxmoxError as e:
            return self._error(e)

    async def _firewall_log(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(await self.cluster.firewall_log())
        except ProxmoxError as e:
            return self._error(e)

    async def _backups(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(await self.cluster.backups())
        except ProxmoxError as e:
            return self._error(e)

    async def _task(self, request: web.Request) -> web.Response:
        node, upid = request.query.get("node", ""), request.query.get("upid", "")
        if not node or not upid.startswith("UPID:"):
            raise web.HTTPBadRequest(text="node and upid are needed")
        try:
            return web.json_response(await self.cluster.task(node, upid))
        except ProxmoxError as e:
            return self._error(e)

    async def _layout(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(json.loads(LAYOUT_FILE.read_text()))
        except (OSError, ValueError):
            return web.json_response({})

    # ------------------------------------------------------------------
    # Changes: only from Home Assistant's signed-in ingress, as JSON
    # ------------------------------------------------------------------
    @staticmethod
    async def _change_body(request: web.Request) -> dict:
        # Under Home Assistant only its signed-in ingress proxy may do this, not other apps on the network
        if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            raise web.HTTPForbidden(text="Use the sidebar")
        # Another site's page can't send this content type without the browser asking first
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Send JSON")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Send a JSON object")
        return body

    def _writable(self) -> web.Response | None:
        if self.config.read_only:
            return web.json_response(
                {"error": "This app is set to read-only. Turn off read_only in its configuration to make changes."},
                status=403,
            )
        return None

    async def _save_layout(self, request: web.Request) -> web.Response:
        """Where the cards on the network map were dragged to. Only the layout; nothing on the cluster."""
        body = await self._change_body(request)
        text = json.dumps(body)
        if len(text) > MAX_LAYOUT_BYTES:
            return web.json_response({"error": "The layout is too large"}, status=413)
        tmp = LAYOUT_FILE.with_name(LAYOUT_FILE.name + ".tmp")
        await asyncio.to_thread(tmp.write_text, text)
        await asyncio.to_thread(os.replace, tmp, LAYOUT_FILE)
        return web.json_response({"ok": True})

    async def _refresh(self, request: web.Request) -> web.Response:
        await self._change_body(request)
        self.cluster.reread_config()
        return web.json_response({"ok": True})

    async def _power(self, request: web.Request) -> web.Response:
        if refused := self._writable():
            return refused
        body = await self._change_body(request)
        action = body.get("action")
        if action not in POWER_ACTIONS:
            return web.json_response({"error": "Unknown action"}, status=400)
        vmid = request.match_info["vmid"]
        try:
            upid = await self.cluster.power(vmid, action)
        except KeyError as e:
            raise web.HTTPNotFound(text="no such guest") from e
        except ProxmoxError as e:
            logger.warning(f"Guest {vmid}: {action} refused: {e}")
            return self._error(e)
        logger.info(f"Guest {vmid}: {action} from the dashboard")
        guest = self.cluster.guest(vmid) or {}
        return web.json_response({"ok": True, "upid": upid, "node": guest.get("node")})

    async def _vlan(self, request: web.Request) -> web.Response:
        if refused := self._writable():
            return refused
        body = await self._change_body(request)
        vmid = request.match_info["vmid"]
        tag = body.get("tag")
        try:
            tag = None if tag in (None, "", 0) else int(tag)
            result = await self.cluster.set_vlan(vmid, str(body.get("nic", "")), tag, str(body.get("digest") or ""))
        except KeyError as e:
            raise web.HTTPNotFound(text="no such guest") from e
        except (TypeError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)
        except ProxmoxError as e:
            logger.warning(f"Guest {vmid}: VLAN change refused: {e}")
            return web.json_response({"error": str(e)}, status=409 if e.status == 409 else 502)
        logger.info(f"Guest {vmid}: {body.get('nic')} {result['before']!r} -> {result['after']!r} from the dashboard")
        return web.json_response({"ok": True, **result})

    async def _rule(self, request: web.Request) -> web.Response:
        """Create, change, switch, move or delete one firewall rule.

        A rule that may lock the host out is refused once with 409 and its reason; the page asks, and
        sends it again with accept_risk."""
        if refused := self._writable():
            return refused
        body = await self._change_body(request)
        scope = body.get("scope") if isinstance(body.get("scope"), dict) else {}
        op = body.get("op")
        rule = body.get("rule") if isinstance(body.get("rule"), dict) else {}
        if op in ("create", "update", "toggle") and not body.get("accept_risk"):
            check = dict(rule)
            if op == "toggle":
                check = {**(body.get("current") or {}), "enable": "1" if rule.get("enable") else "0"}
            if reason := lockout_risk(scope, check):
                return web.json_response({"error": "lockout", "lockout": reason}, status=409)
        try:
            result = await self.cluster.change_rule(
                scope, op, rule=rule, pos=body.get("pos"), to=body.get("to"), digest=str(body.get("digest") or "")
            )
        except (TypeError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)
        except ProxmoxError as e:
            logger.warning(f"Firewall {op} on {scope} refused: {e}")
            return self._error(e)
        logger.info(f"Firewall: {op} rule on {scope} from the dashboard")
        return web.json_response({"ok": True, "result": result})
