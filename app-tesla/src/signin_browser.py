"""A browser inside the app, for signing in to Tesla without leaving the dashboard.

Tesla's sign-in ends by sending the browser to an app link
("tesla://auth/callback?code=...") that an ordinary browser can't open and
barely shows. So for the duration of a sign-in the app runs a normal
Chromium on a virtual display, opens Tesla's own sign-in page in it, shows its
screen in the dashboard and passes on the user's mouse and keyboard. The user
signs in as usual; the app sees the app link the moment Tesla issues it.

Nothing is automated or filled in: it is the user's own sign-in, in a remote
window. The browser starts with an empty profile and is gone afterwards.
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
import shutil
import signal
import socket
import tempfile
from pathlib import Path

import aiohttp

from .tesla_auth import REDIRECT_URI

logger = logging.getLogger(__name__)

BROWSERS = ("chromium", "chromium-browser", "google-chrome")
# The page as the user sees it in the dashboard (CSS pixels)
VIEW_WIDTH = 480
VIEW_HEIGHT = 640
# Seconds between looks at the page; an unchanged screen isn't sent again
FRAME_INTERVAL = 0.2
JPEG_QUALITY = 75
# A sign-in that isn't finished by then is abandoned and the browser stopped
SESSION_TIMEOUT = 600
STARTUP_TIMEOUT = 30
# Development only: without a virtual display, run the browser headless (Tesla's page refuses that)
HEADLESS = os.environ.get("TESLA_BROWSER_HEADLESS", "") not in ("", "0")

# Where a navigation's address can be found in the browser's events
_URL_FIELDS = ("url", "documentURL")


def find_browser() -> str | None:
    return next((path for path in map(shutil.which, BROWSERS) if path), None)


def available() -> bool:
    """Whether the app can run its sign-in browser (the image has Chromium and a virtual display)."""
    return bool(find_browser()) and (HEADLESS or bool(shutil.which("Xvfb")))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SignInBrowser:
    """One sign-in session: a browser showing Tesla's page until the app link comes by."""

    def __init__(self):
        self._processes: list[asyncio.subprocess.Process] = []
        self._profile: Path | None = None
        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._counter = 0
        # Resolves to the app link ("tesla://auth/callback?code=...") when Tesla sends the browser there
        self.callback: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def start(self, url: str) -> None:
        """Start the browser and open the sign-in page."""
        browser = find_browser()
        if not browser:
            raise RuntimeError("No browser installed in the app")
        self._profile = Path(tempfile.mkdtemp(prefix="tesla-signin-"))
        environment = {**os.environ, "HOME": str(self._profile)}
        arguments = [
            browser,
            # Home Assistant runs apps as root in a container, where Chromium's sandbox can't work
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            f"--user-data-dir={self._profile}",
            f"--window-size={VIEW_WIDTH},{VIEW_HEIGHT}",
            "--remote-debugging-address=127.0.0.1",
        ]
        if HEADLESS:
            arguments.append("--headless=new")
        else:
            environment["DISPLAY"] = await self._start_display()
        port = _free_port()
        arguments += [f"--remote-debugging-port={port}", "about:blank"]
        self._processes.append(
            await asyncio.create_subprocess_exec(
                *arguments,
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                # Its own process group, so its helper processes can be stopped along with it
                start_new_session=True,
            )
        )

        self._http = aiohttp.ClientSession()
        target = await self._find_page(port)
        self._ws = await self._http.ws_connect(target, max_msg_size=0)
        self._reader = asyncio.create_task(self._read())
        await self.call("Page.enable")
        await self.call("Network.enable")
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            width=VIEW_WIDTH,
            height=VIEW_HEIGHT,
            deviceScaleFactor=1,
            mobile=False,
        )
        await self.call("Page.navigate", url=url)

    async def _start_display(self) -> str:
        """Start a virtual display and return its name (":99"). Xvfb picks a free number itself."""
        xvfb = await asyncio.create_subprocess_exec(
            "Xvfb",
            "-displayfd",
            "1",
            "-screen",
            "0",
            f"{VIEW_WIDTH}x{VIEW_HEIGHT + 120}x24",
            "-nolisten",
            "tcp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._processes.append(xvfb)
        line = await asyncio.wait_for(xvfb.stdout.readline(), timeout=STARTUP_TIMEOUT)
        number = line.decode().strip()
        if not number.isdigit():
            raise RuntimeError("The virtual display did not start")
        return f":{number}"

    async def _find_page(self, port: int) -> str:
        """The browser's debugging address for its (only) page, once it is up."""
        deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with self._http.get(f"http://127.0.0.1:{port}/json") as resp:
                    pages = [target for target in await resp.json() if target.get("type") == "page"]
                if pages:
                    return pages[0]["webSocketDebuggerUrl"]
            except (aiohttp.ClientError, ValueError):
                pass
            await asyncio.sleep(0.25)
        raise RuntimeError("The sign-in browser did not start")

    async def _read(self) -> None:
        """Take in the browser's answers and events; watch the events for the app link."""
        async for message in self._ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(message.data)
            pending = self._pending.pop(data.get("id"), None)
            if pending and not pending.done():
                pending.set_result(data.get("result") or {})
                continue
            params = data.get("params") or {}
            # A navigation to the app link shows up in several events, with its full address
            candidates = [params.get(field) for field in _URL_FIELDS]
            candidates.append((params.get("request") or {}).get("url"))
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.startswith(REDIRECT_URI) and not self.callback.done():
                    self.callback.set_result(candidate)
        if not self.callback.done():
            self.callback.set_exception(RuntimeError("The sign-in browser stopped"))

    async def call(self, method: str, timeout: float = 15, **params) -> dict:
        """Send a command to the browser and wait for its answer."""
        if not self._ws or self._ws.closed:
            raise RuntimeError("The sign-in browser is not running")
        self._counter += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[self._counter] = future
        await self._ws.send_json({"id": self._counter, "method": method, "params": params})
        return await asyncio.wait_for(future, timeout=timeout)

    async def wait_for_page(self, origin: str, timeout: float = 30) -> None:
        """Wait until a page of the given origin ("https://auth.tesla.com") has finished loading."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            result = await self.call(
                "Runtime.evaluate", expression="document.readyState + ' ' + location.origin", returnByValue=True
            )
            if (result.get("result") or {}).get("value") == f"complete {origin}":
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(f"The app's browser did not reach a page on {origin}")

    async def post_json(self, url: str, payload: dict) -> tuple[int, dict]:
        """POST JSON to a URL of the current page's own site, from the page, and return (status, answer).

        This is how the Tesla tokens are requested: by the real browser that is on
        Tesla's sign-on site, so the request is a genuine browser request. Tesla's
        sign-on service hands such a client tokens its owner API accepts, where a
        plain Python client gets tokens it refuses.
        """
        # url and the JSON body go in as JS string literals (json.dumps gives valid, escaped JS strings)
        url_js = json.dumps(url)
        body_js = json.dumps(json.dumps(payload))
        script = (
            "(async () => { try {"
            " const r = await fetch(" + url_js + ", {method:'POST',"
            " headers:{'Content-Type':'application/json'}, body:" + body_js + "});"
            " return JSON.stringify({ok:true, status:r.status, text:await r.text()});"
            " } catch (e) { return JSON.stringify({ok:false, error:String(e)}); } })()"
        )
        result = await self.call(
            "Runtime.evaluate", timeout=40, expression=script, awaitPromise=True, returnByValue=True
        )
        value = (result.get("result") or {}).get("value")
        if result.get("exceptionDetails") or not isinstance(value, str):
            raise RuntimeError("The app's browser could not run the token request")
        answer = json.loads(value)
        if not answer.get("ok"):
            raise RuntimeError(f"The app's browser could not reach Tesla's sign-on service: {answer.get('error')}")
        try:
            body = json.loads(answer.get("text") or "")
        except ValueError:
            body = {}
        return int(answer.get("status", 0)), body if isinstance(body, dict) else {}

    async def frame(self) -> bytes:
        """What the page looks like right now, as a JPEG."""
        shot = await self.call("Page.captureScreenshot", format="jpeg", quality=JPEG_QUALITY)
        return base64.b64decode(shot.get("data", ""))

    async def handle_input(self, event: dict) -> None:
        """Pass on something the user did in the dashboard: mouse, wheel, key or pasted text."""
        kind = event.get("t")
        if kind == "mouse" and event.get("type") in ("mousePressed", "mouseReleased", "mouseMoved"):
            await self.call(
                "Input.dispatchMouseEvent",
                type=event["type"],
                x=float(event.get("x", 0)),
                y=float(event.get("y", 0)),
                button=event.get("button") if event.get("button") in ("left", "middle", "right") else "none",
                buttons=int(event.get("buttons", 0)),
                clickCount=int(event.get("clickCount", 0)),
                modifiers=int(event.get("modifiers", 0)),
            )
        elif kind == "wheel":
            await self.call(
                "Input.dispatchMouseEvent",
                type="mouseWheel",
                x=float(event.get("x", 0)),
                y=float(event.get("y", 0)),
                deltaX=float(event.get("dx", 0)),
                deltaY=float(event.get("dy", 0)),
            )
        elif kind == "key" and event.get("type") in ("keyDown", "rawKeyDown", "keyUp"):
            params = {
                "type": event["type"],
                "key": str(event.get("key", "")),
                "code": str(event.get("code", "")),
                "windowsVirtualKeyCode": int(event.get("keyCode", 0)),
                "modifiers": int(event.get("modifiers", 0)),
            }
            if event["type"] == "keyDown" and event.get("text"):
                params["text"] = str(event["text"])
            await self.call("Input.dispatchKeyEvent", **params)
        elif kind == "text" and event.get("text"):
            # Pasted text, e.g. from a password manager
            await self.call("Input.insertText", text=str(event["text"])[:2000])

    async def stop(self) -> None:
        """Stop the browser and remove everything it kept (cookies, cache)."""
        if self._reader:
            self._reader.cancel()
        if self._ws and not self._ws.closed:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._http and not self._http.closed:
            await self._http.close()
        for process in reversed(self._processes):
            if process.returncode is None:
                # The browser runs helper processes; signal its whole group
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGTERM)
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
        for process in self._processes:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
        self._processes.clear()
        if not self.callback.done():
            self.callback.cancel()
        if self._profile:
            # The profile holds Tesla's session cookies: make sure it is gone, also when a
            # helper process of the browser was still writing to it a moment ago
            for _ in range(5):
                shutil.rmtree(self._profile, ignore_errors=True)
                if not self._profile.exists():
                    break
                await asyncio.sleep(0.5)
            self._profile = None


async def post_from_browser(page_url: str, url: str, payload: dict) -> tuple[int, dict]:
    """Open a page of Tesla's sign-on site in a fresh short-lived browser, POST from it, close it.

    Used to renew tokens: the request then comes from a real browser, like the
    sign-in does. page_url is any page on the same site as url (its origin loads
    so a same-site fetch is allowed); the browser is always torn down afterwards.
    """
    browser = SignInBrowser()
    origin = "/".join(url.split("/", 3)[:3])
    try:
        await browser.start(page_url)
        await browser.wait_for_page(origin, timeout=STARTUP_TIMEOUT)
        return await browser.post_json(url, payload)
    finally:
        await browser.stop()
