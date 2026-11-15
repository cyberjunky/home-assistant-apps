"""The app's page in Home Assistant's sidebar: PBS's own web interface, inside Home Assistant.

PBS's interface expects to be at the root of its own address. Here it is passed through Home
Assistant's ingress instead, below /pbs/, so the browser sees it on Home Assistant's own address:
no certificate to accept, and PBS's login cookie is a first-party cookie, also when Home Assistant
runs on plain http. PBS itself is unchanged; what this module adjusts on the way:

- the pages PBS serves (its interface and its console) get a small script first (web/shim.js) that
  sends PBS's absolute paths (/api2/..., /images/..., the console's websocket) below the ingress path
- PBS's login ticket cookie (__Host-PBSAuthCookie, HttpOnly: https and the root path only) is kept
  in the browser as PBSAppAuth on the ingress path, and given back to PBS under its own name. The
  readable PBSAuthCookie its page sets itself (the shim moves that one to the ingress path) keeps its name
- "Show Connection Information" leaves out Home Assistant's port: Proxmox VE connects to port 8007
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import ssl
from pathlib import Path

import aiohttp
from aiohttp import web

from .config import APP_VERSION, PANEL_PORT, PBS_PORT

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy: the only one that may use this page under Home Assistant
INGRESS_ADDRESS = "172.30.32.2"
PBS_URL = f"https://127.0.0.1:{PBS_PORT}"
PREFIX = "/pbs"
# PBS's ticket cookie, and its name in the browser here
PBS_COOKIE = "__Host-PBSAuthCookie"
AUTH_COOKIE = "PBSAppAuth"
# Not passed on, either way
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
    "accept-encoding",
}
# src="/..." and href="/..." in PBS's pages, not "//host/..."
ROOT_LINK = re.compile(r"""\b(src|href)=(["'])/(?!/)""")
# The connection information adds the page's port when it isn't 8007; through ingress that would
# be Home Assistant's port, where Proxmox VE can't reach PBS
CONNECTION_PORT = "window.location.port.toString() !== '8007'"


def base_path(request: web.Request) -> str:
    """Where PBS is in the browser: below Home Assistant's ingress path."""
    return request.headers.get("X-Ingress-Path", "").rstrip("/") + PREFIX


def rewrite_page(html: str, base: str, shim: str) -> str:
    """PBS's page with its root links below `base`, and the shim before anything else runs."""
    html = ROOT_LINK.sub(lambda m: f"{m.group(1)}={m.group(2)}{base}/", html)
    head = f"<script>window.PBSAPP_BASE = {json.dumps(base)};\n{shim}</script>"
    match = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if match:
        return html[: match.end()] + head + html[match.end() :]
    return head + html


def rewrite_set_cookie(value: str, base: str) -> str:
    """A cookie from PBS as the browser keeps it here: on the ingress path, no Secure (Home Assistant
    may be on http; it only travels through Home Assistant), and PBS's ticket cookie under its own name."""
    parts = [p.strip() for p in value.split(";")]
    name, _, rest = parts[0].partition("=")
    if name == PBS_COOKIE:
        name = AUTH_COOKIE
    elif name.startswith("__Host-"):
        name = name[len("__Host-") :]
    attrs = [p for p in parts[1:] if p and p.split("=", 1)[0].strip().lower() not in ("path", "secure", "domain")]
    return "; ".join([f"{name}={rest}", *attrs, f"Path={base}/"])


def request_cookies(header: str) -> str:
    """The browser's cookies as PBS expects them: the ticket cookie under PBS's own name."""
    if not header:
        return header
    extra = []
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == AUTH_COOKIE:
            extra.append(f"{PBS_COOKIE}={value}")
    return "; ".join([header, *extra]) if extra else header


def location(value: str, base: str) -> str:
    """A redirect to one of PBS's own paths, below the ingress path."""
    if value.startswith("/") and not value.startswith("//") and not value.startswith(base + "/"):
        return base + value
    return value


async def pbs_answers(timeout: float = 5.0) -> bool:
    """Whether PBS's proxy answers on its port: a finished TLS handshake counts."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", PBS_PORT, ssl=context), timeout)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


class Panel:
    """The sidebar page and PBS behind it."""

    def __init__(self, pbs_url: str = PBS_URL):
        self.pbs_url = pbs_url
        self.session: aiohttp.ClientSession | None = None
        self.app = web.Application(middlewares=[self._only_ingress], client_max_size=1024**3)
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/api/status", self._status)
        r.add_static("/static", WEB_DIR)
        r.add_route("*", PREFIX + "/{tail:.*}", self._proxy)
        r.add_route("*", PREFIX, self._to_slash)
        self.app.on_cleanup.append(self._close)

    @web.middleware
    async def _only_ingress(self, request: web.Request, handler):
        # Under Home Assistant only its ingress proxy, so PBS isn't also offered on plain http on the network
        if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            raise web.HTTPForbidden(text="Use the sidebar")
        return await handler(request)

    def _client(self) -> aiohttp.ClientSession:
        if self.session is None:
            # No cookie jar: every browser's login stays its own
            self.session = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),
                connector=aiohttp.TCPConnector(ssl=False),
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
            )
        return self.session

    async def _close(self, app: web.Application) -> None:
        if self.session is not None:
            await self.session.close()

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"version": APP_VERSION, "port": PBS_PORT, "pbs": await pbs_answers()},
            headers={"Cache-Control": "no-store"},
        )

    async def _to_slash(self, request: web.Request) -> web.Response:
        raise web.HTTPFound(base_path(request) + "/")

    async def _proxy(self, request: web.Request) -> web.StreamResponse:
        base = base_path(request)
        tail = request.match_info["tail"]
        url = f"{self.pbs_url}/{tail}"
        if request.query_string:
            url += "?" + request.query_string
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS and k.lower() != "cookie"}
        if cookies := request_cookies(request.headers.get("Cookie", "")):
            headers["Cookie"] = cookies
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._websocket(request, url, headers)
        # PBS refuses application/octet-stream. aiohttp sends that for a request without a type (Home
        # Assistant's ingress too, on a GET without body), so a typeless or octet-stream empty request
        # goes on without a body, and with the form type PBS takes when it has none
        body = await request.read()
        kind = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
        if not body and kind in ("", "application/octet-stream"):
            headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            upstream = await self._client().request(
                request.method, url, headers=headers, data=body or None, allow_redirects=False
            )
        except aiohttp.ClientError as e:
            return web.Response(status=502, text=f"PBS is not answering: {e}")
        async with upstream:
            response_headers = []
            for name, value in upstream.headers.items():
                low = name.lower()
                if low in HOP_HEADERS:
                    continue
                if low == "set-cookie":
                    value = rewrite_set_cookie(value, base)
                elif low == "location":
                    value = location(value, base)
                response_headers.append((name, value))
            kind = upstream.headers.get("Content-Type", "")
            if kind.startswith("text/html"):
                html = rewrite_page(await upstream.text(), base, (WEB_DIR / "shim.js").read_text())
                return self._response(upstream.status, response_headers, html.encode())
            if tail.endswith("proxmox-backup-gui.js"):
                text = (await upstream.text()).replace(CONNECTION_PORT, "false")
                return self._response(upstream.status, response_headers, text.encode())
            response = web.StreamResponse(status=upstream.status)
            for name, value in response_headers:
                response.headers.add(name, value)
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response

    @staticmethod
    def _response(status: int, headers: list[tuple[str, str]], body: bytes) -> web.Response:
        response = web.Response(status=status, body=body)
        for name, value in headers:
            response.headers.add(name, value)
        return response

    async def _websocket(self, request: web.Request, url: str, headers: dict) -> web.StreamResponse:
        """PBS's console: a websocket, passed both ways."""
        protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
        headers = {k: v for k, v in headers.items() if not k.lower().startswith("sec-websocket")}
        try:
            upstream = await self._client().ws_connect(url, headers=headers, protocols=protocols, max_msg_size=0)
        except (aiohttp.ClientError, aiohttp.WSServerHandshakeError) as e:
            return web.Response(status=502, text=f"PBS's console is not answering: {e}")
        browser = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else (), max_msg_size=0)
        await browser.prepare(request)

        async def relay(source, target) -> None:
            async for msg in source:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    await target.send_bytes(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    await target.send_str(msg.data)
                else:
                    break

        tasks = [asyncio.create_task(relay(browser, upstream)), asyncio.create_task(relay(upstream, browser))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await upstream.close()
            await browser.close()
        return browser

    async def serve(self, port: int = PANEL_PORT) -> None:
        """Serve until cancelled."""
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
        logger.info(f"Sidebar page on port {port}")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
