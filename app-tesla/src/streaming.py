"""Tesla's streaming channel: live driving data over a WebSocket.

While the car is awake it pushes position, speed, power and battery level as
they change (about once a second while driving). Listening does not keep the
car awake, unlike reading its data over the API, so this is also how activity
is noticed while the car is being left alone to fall asleep.
"""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Callable

import aiohttp

from .config import KM_PER_MILE
from .tesla_auth import USER_AGENT, AuthError, TeslaAuth

logger = logging.getLogger(__name__)

STREAM_HOST = "wss://streaming.vn.teslamotors.com"
STREAM_HOST_CN = "wss://streaming.vn.cloud.tesla.cn"

# The columns asked for, in the order the car sends them back (after the timestamp)
COLUMNS = (
    "speed",
    "odometer",
    "soc",
    "elevation",
    "est_heading",
    "est_lat",
    "est_lng",
    "power",
    "shift_state",
    "range",
    "est_range",
    "heading",
)

# Without any message for this long the connection is assumed dead and reopened. A parked car can
# stay quiet for minutes (it only sends what changes), a driving one sends about every second.
SILENCE_TIMEOUT = 300
SILENCE_TIMEOUT_DRIVING = 60
# Gears a frame can report; anything else is a garbled frame
SHIFT_STATES = ("P", "D", "N", "R", "")
# This many unreadable frames in a row: the connection is reopened
MAX_BAD_FRAMES = 10
RETRY_DELAY = 15
RETRY_DELAY_MAX = 300
# A car that dropped off the stream is asked again after this long, doubling up to the maximum
RESUBSCRIBE_DELAY = 5
RESUBSCRIBE_DELAY_MAX = 60


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _km(miles: float | None) -> float | None:
    return None if miles is None else miles * KM_PER_MILE


def parse_update(value: str) -> dict | None:
    """One streaming line ("time,speed,odometer,...") as a metric position sample."""
    parts = value.split(",")
    if len(parts) != len(COLUMNS) + 1:
        return None
    raw = dict(zip(COLUMNS, parts[1:], strict=True))
    millis = _number(parts[0])
    lat, lon = _number(raw["est_lat"]), _number(raw["est_lng"])
    if millis is None or raw["shift_state"] not in SHIFT_STATES:
        return None
    # A garbled position, or "null island" (0, 0) which the car sends before it has a fix
    if (lat is None) != (lon is None):
        return None
    if lat is not None and (not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0)):
        return None
    soc = _number(raw["soc"])
    heading = _number(raw["est_heading"]) if raw["est_heading"] else _number(raw["heading"])
    speed = _km(_number(raw["speed"]))
    return {
        "ts": int(millis / 1000),
        "lat": lat,
        "lon": lon,
        "heading": None if heading is None else int(heading),
        "elevation_m": _number(raw["elevation"]),
        # No speed is sent while standing still
        "speed_kmh": speed if speed is not None else 0.0,
        "power_kw": _number(raw["power"]),
        "odometer_km": _km(_number(raw["odometer"])),
        "soc": None if soc is None else int(soc),
        "range_km": _km(_number(raw["range"])),
        "est_range_km": _km(_number(raw["est_range"])),
        "shift": raw["shift_state"] or None,
    }


class VehicleStream:
    """Keeps a streaming subscription for one vehicle while it is wanted."""

    def __init__(
        self,
        auth: TeslaAuth,
        vehicle_id: int,
        on_sample: Callable[[dict], None],
        driving: Callable[[], bool] | None = None,
    ):
        self.auth = auth
        self.vehicle_id = vehicle_id
        self.on_sample = on_sample
        # Whether a drive is going on: a silent stream is reopened sooner then
        self.driving = driving or (lambda: False)
        self._bad_frames = 0
        # Set by the monitor while the car is awake; cleared while it sleeps
        self.wanted = asyncio.Event()
        self.last_sample_at = 0.0
        self.connected = False
        self._resubscribe_delay = RESUBSCRIBE_DELAY

    @property
    def url(self) -> str:
        host = os.environ.get("TESLA_WSS_HOST") or (STREAM_HOST_CN if self.auth.region == "cn" else STREAM_HOST)
        return f"{host}/streaming/"

    @property
    def live(self) -> bool:
        """Whether samples are arriving right now (the car is sending and we're receiving)."""
        return self.connected and time.monotonic() - self.last_sample_at < 15

    async def run(self) -> None:
        """Run until cancelled: connect whenever the stream is wanted, back off on trouble."""
        delay = RETRY_DELAY
        while True:
            await self.wanted.wait()
            started = time.monotonic()
            try:
                await self._session()
            except AuthError:
                # Signed out: the monitor stops wanting the stream; don't hammer meanwhile
                delay = RETRY_DELAY_MAX
            except (aiohttp.ClientError, TimeoutError, OSError) as e:
                logger.debug(f"Stream {self.vehicle_id}: {type(e).__name__}: {e}")
            finally:
                self.connected = False
            # A session that held for a while resets the back-off
            if time.monotonic() - started > 60:
                delay = RETRY_DELAY
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_DELAY_MAX)

    async def _session(self) -> None:
        """One connection: subscribe and read updates until it ends or isn't wanted anymore."""
        token = await self.auth.access_token()
        async with (
            aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as http,
            http.ws_connect(self.url, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws,
        ):
            await self._subscribe(ws, token)
            self.connected = True
            logger.debug(f"Stream {self.vehicle_id}: connected")
            while self.wanted.is_set():
                try:
                    message = await ws.receive(timeout=self.silence_timeout)
                except TimeoutError:
                    logger.debug(f"Stream {self.vehicle_id}: silent, reconnecting")
                    return
                if message.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    if not await self._handle(ws, message.data):
                        return
                elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    return

    @property
    def silence_timeout(self) -> int:
        return SILENCE_TIMEOUT_DRIVING if self.driving() else SILENCE_TIMEOUT

    async def _subscribe(self, ws: aiohttp.ClientWebSocketResponse, token: str) -> None:
        await ws.send_json(
            {
                "msg_type": "data:subscribe_oauth",
                "token": token,
                "value": ",".join(COLUMNS),
                "tag": str(self.vehicle_id),
            }
        )

    async def _handle(self, ws: aiohttp.ClientWebSocketResponse, data: str | bytes) -> bool:
        """Process one message. Returns False when the connection should be reopened."""
        try:
            message = json.loads(data)
        except ValueError:
            return True
        kind = message.get("msg_type")
        if kind == "data:update":
            sample = parse_update(str(message.get("value", "")))
            if not sample:
                self._bad_frames += 1
                if self._bad_frames >= MAX_BAD_FRAMES:
                    logger.debug(f"Stream {self.vehicle_id}: {self._bad_frames} unreadable frames, reconnecting")
                    self._bad_frames = 0
                    return False
            else:
                self._bad_frames = 0
                self.last_sample_at = time.monotonic()
                self._resubscribe_delay = RESUBSCRIBE_DELAY
                try:
                    self.on_sample(sample)
                except Exception:
                    logger.exception(f"Stream {self.vehicle_id}: handling a sample failed")
        elif kind == "data:error":
            error = message.get("error_type")
            logger.debug(f"Stream {self.vehicle_id}: {error}: {message.get('value')}")
            if error == "vehicle_disconnected":
                # The car dropped off (parked, tunnel): ask again, it answers when it is back.
                # A parked car can stay off the stream for hours, so ask less and less often.
                await asyncio.sleep(self._resubscribe_delay)
                self._resubscribe_delay = min(self._resubscribe_delay * 2, RESUBSCRIBE_DELAY_MAX)
                if not self.wanted.is_set():
                    return False
                await self._subscribe(ws, await self.auth.access_token())
            elif error == "client_error":
                # Usually an expired token
                with contextlib.suppress(AuthError):
                    await self.auth.access_token(force_refresh=True)
                return False
            else:
                return False
        return True
