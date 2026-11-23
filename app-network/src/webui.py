"""The dashboard, served through Home Assistant's ingress.

The browser only talks to this app. Renames go to Home Assistant's registries, and only when they
come from Home Assistant's signed-in ingress proxy.
"""

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR, Config
from .donate import DonatePrompt
from .ha_client import HAClientError, HAWebSocket
from .history import DAY, History, host_events, host_stats, merge, online_series, up_intervals
from .inventory import Inventory, behind_bridges, config_host, fritz_devices, fritz_nodes, on_fritz_wifi
from .kinds import KINDS
from .mobile import battery_history, mismatch, parse_history
from .mobile import summary as mobile_summary
from .router import Router
from .store import Conflict, Store

logger = logging.getLogger(__name__)


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
STATS_DAYS = (1, 7, 30, 90, 365)
MOBILE_DAYS = (1, 7, 30)
MAP_MAX_ZOOM = 19
MAP_TILE_MAX_AGE = 7 * 24 * 60 * 60
# The stream's heartbeat, so ingress and the browser keep it open
LIVE_HEARTBEAT = 20


class WebUI:
    def __init__(
        self,
        port: int,
        config: Config,
        ws: HAWebSocket,
        inventory: Inventory,
        history: History,
        store: Store,
        router: Router | None = None,
    ):
        self.port = port
        self.config = config
        self.ws = ws
        self.inventory = inventory
        self.history = history
        self.store = store
        self.router = router
        self._stats_cache: dict[int, tuple[float, dict, int]] = {}
        self._mobile_cache: dict[tuple, tuple[float, dict]] = {}
        # Names of FRITZ! devices in DNS (fritz.box): name -> (IPv4 address or "", when looked up)
        self._dns: dict[str, tuple[str, float]] = {}
        # The REST side of the connection to Home Assistant: history and map tiles
        self.ws_client = ws.client
        self.app = web.Application(client_max_size=8 * 1024 * 1024)
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/api/status", self._status)
        r.add_get("/api/hosts", self._hosts)
        r.add_get("/api/doc", self._doc)
        r.add_post("/api/doc", self._save_doc)
        r.add_get("/api/stats", self._stats)
        r.add_get("/api/mobile", self._mobile)
        r.add_get("/api/live", self._live)
        r.add_get("/api/router", self._router)
        r.add_get("/api/traffic", self._traffic)
        r.add_get("/api/map-tiles/{z:[0-9]+}/{x:[0-9]+}/{y:[0-9]+}.png", self._map_tile)
        r.add_post("/api/rename", self._rename)
        r.add_post("/api/icon", self._icon)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_static("/static", WEB_DIR)

    async def start(self) -> None:
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
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    async def _status(self, request: web.Request) -> web.Response:
        hosts = self.inventory.hosts()
        return web.json_response(
            {
                "version": APP_VERSION,
                "now": time.time(),
                "connected": self.ws.connected,
                "error": self.ws.last_error or self.inventory.error,
                "loaded_at": self.inventory.loaded_at,
                "hosts": len(hosts),
                "up": sum(1 for h in hosts if h["up"]),
                "rev": self.store.doc["rev"],
                "backfill": self.history.backfill_state,
                "config": {
                    "history_days": self.config.history_days,
                    "keep_days": self.config.keep_days,
                    "include_disabled": self.config.include_disabled,
                    "router": self.config.router,
                    "log_level": self.config.log_level,
                },
            }
        )

    async def _hosts(self, request: web.Request) -> web.Response:
        hosts = self.inventory.hosts()
        fritz_nodes = await self._fritz_nodes(hosts)
        devices = self.store.doc.get("devices", {})
        behind = behind_bridges(
            hosts, self.inventory.port_list(), lambda h: (devices.get(h["key"]) or {}).get("kind") or h.get("guess")
        )
        first, last = await asyncio.to_thread(lambda: (self.history.first_seen(), self.history.last_seen()))
        out = []
        for host in hosts:
            ids = [t["entity_id"] for t in host["trackers"]]
            seen = [first[i] for i in ids if i in first]
            gone = [last[i] for i in ids if i in last]
            entry = on_fritz_wifi(host)
            fritz_wifi = entry is not host
            via = fritz_nodes.get(host.get("connected_to", "").lower(), "")
            out.append(
                {
                    **entry,
                    "first_seen": min(seen) if seen else None,
                    "last_seen": max(gone) if gone else None,
                    "connected_via": via,
                    # Behind an access point or switch that is on the same switch port
                    **(
                        {"behind": behind[host["key"]][0], "behind_by": behind[host["key"]][1]}
                        | ({"wired": False} if behind[host["key"]][1] == "wifi" else {})
                        if host["key"] in behind and not (fritz_wifi and via)
                        else {}
                    ),
                }
            )
        return web.json_response(
            {
                "hosts": out,
                "kinds": {k: {"label": v[0], "mdi": v[1], "infra": v[2]} for k, v in KINDS.items()},
                "zones": self.inventory.zone_list(),
                # Port forwards Home Assistant has a switch for (the FRITZ!Box integration)
                "forwards": self.inventory.port_forwards(),
                # The ports of every switch and access point, by its MAC address
                "ports": self.inventory.port_list(),
                # What goes through the ports that have a traffic sensor (see /api/traffic)
                "traffic": self.inventory.traffic(),
            }
        )

    async def _doc(self, request: web.Request) -> web.Response:
        return web.json_response(self.store.doc)

    def stats(self, days: int, now: float | None = None, with_intervals: bool | None = None) -> dict:
        now = time.time() if now is None else now
        since = now - days * DAY
        start, after = self.history.changes(since)
        per_host: dict[str, list[tuple[float, float]]] = {}
        known: dict[str, float] = {}
        for host in self.inventory.hosts():
            intervals: list[tuple[float, float]] = []
            host_known = 0.0
            for tracker in host["trackers"]:
                entity_id = tracker["entity_id"]
                first, changes = start.get(entity_id), after.get(entity_id, [])
                if entity_id not in start and not changes:
                    # Nothing recorded: what Home Assistant says now, since its last change
                    if tracker["last_changed"] and tracker["last_changed"] > since:
                        changes = [(tracker["last_changed"], tracker["up"])]
                    else:
                        first = tracker["up"]
                iv, k = up_intervals(first, changes, since, now)
                intervals += iv
                host_known = max(host_known, k)
            per_host[host["key"]] = merge(intervals)
            known[host["key"]] = host_known
        offset = time.localtime(now).tm_gmtoff
        step = 600 if days <= 1 else 3600 if days <= 30 else 6 * 3600 if days <= 90 else DAY
        return {
            "days": days,
            "since": since,
            "until": now,
            "hosts": {k: host_stats(iv, known[k], since, now, offset) for k, iv in per_host.items()},
            "online": online_series(per_host, since, now, step),
            "events": host_events(per_host, since, now),
            # Every host's times online: for the timelines, only for short periods (they add up)
            "intervals": dict(per_host) if (days <= 7 if with_intervals is None else with_intervals) else {},
        }

    async def _stats(self, request: web.Request) -> web.Response:
        try:
            days = int(request.query.get("days", "7"))
        except ValueError:
            days = 7
        if days not in STATS_DAYS:
            raise web.HTTPBadRequest(text=f"days must be one of {STATS_DAYS}")
        cached = self._stats_cache.get(days)
        # Again after any change of a tracker, else at most every minute
        if cached and cached[2] == self.inventory.version and time.time() - cached[0] < 60:
            return web.json_response(cached[1])
        version = self.inventory.version
        result = await asyncio.to_thread(self.stats, days)
        self._stats_cache[days] = (time.time(), result, version)
        return web.json_response(result)

    async def _mobile(self, request: web.Request) -> web.Response:
        """A GPS tracker's track, heatmap and habits from the recorder; with wifi=<host key> also
        how its Wi-Fi tracker agrees."""
        entity_id = request.query.get("entity_id", "")
        if not entity_id.startswith("device_tracker.") or entity_id not in self.inventory.states:
            raise web.HTTPNotFound(text="no such tracker")
        try:
            days = int(request.query.get("days", "1"))
        except ValueError:
            days = 1
        if days not in MOBILE_DAYS:
            raise web.HTTPBadRequest(text=f"days must be one of {MOBILE_DAYS}")
        key = (entity_id, days, request.query.get("wifi", ""))
        cached = self._mobile_cache.get(key)
        if cached and time.time() - cached[0] < 20:
            return web.json_response(cached[1])
        now = time.time()
        since = now - days * DAY
        try:
            series = await self.ws_client.get_history(
                [entity_id], datetime.fromtimestamp(since, UTC), datetime.fromtimestamp(now, UTC), attributes=True
            )
        except HAClientError as e:
            return web.json_response({"error": f"Could not read the recorder: {e}"}, status=502)
        points = parse_history(series[0] if series else [])
        offset = time.localtime(now).tm_gmtoff
        result = mobile_summary(points, since, now, self.inventory.home(), offset)
        # The Companion app reports the battery as a sensor of the phone, not on its tracker
        sensor = "" if result["battery"] else self.inventory.battery_sensor(entity_id)
        if sensor:
            with contextlib.suppress(HAClientError):
                rows = await self.ws_client.get_history(
                    [sensor], datetime.fromtimestamp(since, UTC), datetime.fromtimestamp(now, UTC)
                )
                result["battery"] = battery_history(rows[0] if rows else [])
        wifi_key = request.query.get("wifi", "")
        if wifi_key:
            stats = await asyncio.to_thread(self.stats, days, now, True)
            # Only the time the Wi-Fi tracker's state is known: its history may be shorter than the GPS's
            known = (stats["hosts"].get(wifi_key) or {}).get("known_seconds", 0)
            start = max(since, now - known)
            home = [
                (max(seg["from"], start), seg["to"])
                for seg in result["segments"]
                if seg["state"] == "home" and seg["to"] > start
            ]
            result["wifi"] = mismatch(home, stats["intervals"].get(wifi_key, []), start, now)
            result["wifi"]["since"] = start
        self._mobile_cache[key] = (time.time(), result)
        return web.json_response(result)

    async def _live(self, request: web.Request) -> web.StreamResponse:
        """Server-sent events: which trackers changed, the moment Home Assistant says so."""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        await response.prepare(request)
        seen = self.inventory.version
        router_seen = self.router.version if self.router else 0
        last_write = time.monotonic()
        try:
            await response.write(f"event: hello\ndata: {json.dumps({'version': seen})}\n\n".encode())
            while True:
                await asyncio.sleep(0.5)
                if self.inventory.version != seen:
                    changed = self.inventory.changed_since(seen)
                    seen = self.inventory.version
                    await response.write(f"data: {json.dumps({'version': seen, 'changed': changed})}\n\n".encode())
                    last_write = time.monotonic()
                if self.router and self.router.version != router_seen:
                    router_seen = self.router.version
                    await response.write(f"event: router\ndata: {json.dumps(self.router.state())}\n\n".encode())
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= LIVE_HEARTBEAT:
                    await response.write(b"event: ping\ndata: {}\n\n")
                    last_write = time.monotonic()
        except (ConnectionResetError, TimeoutError, asyncio.CancelledError):
            pass
        return response

    async def _fritz_nodes(self, hosts: list[dict]) -> dict[str, str]:
        """The FRITZ! devices the FRITZ!Box integration names in connected_to, as hosts on the map.
        Names that look like a host name (fritz.box, fritz.repeater) are looked up in DNS too, which
        the FRITZ!Box answers for its mesh."""
        names = {h["connected_to"].lower() for h in hosts if h.get("connected_to")}
        # And the host names the FRITZ! devices were added by (http://fritz.box)
        names |= {config_host(d) for d in fritz_devices(self.inventory.devices)}
        await asyncio.gather(*(self._resolve(n) for n in names if n and " " not in n and not _is_ip(n)))
        addresses = {n: ip for n, (ip, _) in self._dns.items() if ip}
        return fritz_nodes(hosts, self.inventory.devices, self.router.state() if self.router else {}, addresses)

    async def _resolve(self, name: str) -> None:
        """A name's IPv4 address, kept for ten minutes (also when it has none)."""
        ip, at = self._dns.get(name, ("", 0.0))
        if time.monotonic() - at < 600 and name in self._dns:
            return
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(name, None, family=socket.AF_INET), timeout=3
            )
            ip = infos[0][4][0] if infos else ""
        except (OSError, TimeoutError):
            ip = ""
        self._dns[name] = (ip, time.monotonic())

    async def _router(self, request: web.Request) -> web.Response:
        """The internet router: its state now, its traffic over a period, and the bytes per hour."""
        if not self.router:
            return web.json_response({"found": False, "error": "Not read"})
        try:
            hours = min(24 * 31, max(1, int(request.query.get("hours", "24"))))
        except ValueError:
            hours = 24
        now = time.time()
        # Per minute for a day, per 10 minutes for a week, per hour beyond that
        step = 60 if hours <= 24 else 600 if hours <= 7 * 24 else 3600
        minutes, per_hour = await asyncio.to_thread(
            lambda: (self.history.router_minutes(now - hours * 3600, step), self.history.router_hours(now - 32 * DAY))
        )
        return web.json_response(
            {**self.router.state(), "minutes": minutes, "hours": per_hour, "recent": self.router.recent_series()}
        )

    async def _traffic(self, request: web.Request) -> web.Response:
        """What flows now: through the switch ports that have a traffic sensor in Home Assistant, and
        over the internet connection as the router tells it. Bytes per second."""
        state = self.router.state() if self.router else {}
        now = state.get("now") or {}
        internet = None
        if state.get("found") and now.get("ts") and time.time() - now["ts"] < 120:
            internet = {"address": state.get("address") or "", "down": now.get("down"), "up": now.get("up")}
        return web.json_response({"ports": self.inventory.traffic(), "internet": internet})

    async def _map_tile(self, request: web.Request) -> web.Response:
        """An OpenStreetMap raster tile through Home Assistant's map tile proxy."""
        z, x, y = (int(request.match_info[k]) for k in ("z", "x", "y"))
        if z > MAP_MAX_ZOOM or x >= 2**z or y >= 2**z:
            return web.Response(status=404)
        try:
            body = await self.ws_client.get_map_tile(z, x, y)
        except HAClientError as e:
            logger.debug(f"Map tile {z}/{x}/{y} failed: {e}")
            return web.Response(status=502)
        return web.Response(
            body=body, content_type="image/png", headers={"Cache-Control": f"private, max-age={MAP_TILE_MAX_AGE}"}
        )

    # ------------------------------------------------------------------
    # Changes: only from Home Assistant's signed-in ingress, as JSON
    # ------------------------------------------------------------------
    @staticmethod
    async def _change_body(request: web.Request) -> dict:
        # NETWORK_SUPERVISOR_URL is only set for development against tools/mock_ha.py
        dev = bool(os.environ.get("NETWORK_SUPERVISOR_URL"))
        if os.environ.get("SUPERVISOR_TOKEN") and not dev and request.remote != INGRESS_ADDRESS:
            raise web.HTTPForbidden(text="Use the sidebar")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Send JSON")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Send a JSON object")
        return body

    async def _save_doc(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        try:
            doc = await asyncio.to_thread(self.store.save, body.get("doc"), int(body.get("rev", -1)))
        except Conflict:
            return web.json_response({"error": "conflict", "doc": self.store.doc}, status=409)
        except (TypeError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True, "rev": doc["rev"]})

    async def _rename(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        entity_id = str(body.get("entity_id") or "")
        name = str(body.get("name") or "")
        if len(name) > 100:
            return web.json_response({"error": "That name is too long"}, status=400)
        try:
            result = await self.inventory.rename(entity_id, name, bool(body.get("device")))
        except KeyError:
            return web.json_response(
                {
                    "error": "This tracker is not in Home Assistant's entity registry (known_devices.yaml): rename it there"
                },
                status=404,
            )
        except HAClientError as e:
            return web.json_response({"error": f"Home Assistant refused: {e}"}, status=502)
        logger.info(f"Renamed {entity_id} to {name!r}{' and its device' if result['device'] else ''}")
        return web.json_response({"ok": True, **result})

    async def _icon(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        entity_id = str(body.get("entity_id") or "")
        icon = str(body.get("icon") or "")
        if icon and not icon.startswith("mdi:"):
            return web.json_response({"error": "An icon looks like mdi:printer"}, status=400)
        try:
            await self.inventory.set_icon(entity_id, icon)
        except KeyError:
            return web.json_response({"error": "This tracker is not in Home Assistant's entity registry"}, status=404)
        except HAClientError as e:
            return web.json_response({"error": f"Home Assistant refused: {e}"}, status=502)
        return web.json_response({"ok": True})
