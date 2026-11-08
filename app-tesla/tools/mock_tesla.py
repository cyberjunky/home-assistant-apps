#!/usr/bin/env python3
"""A stand-in for Tesla's servers, for developing this app without a car.

Serves the sign-on token endpoint, the owner API and the streaming channel for
one simulated car that keeps repeating a short story: parked, a drive, parked,
an AC charge, idle until it falls asleep, waking up and driving back.

    python3 tools/mock_tesla.py            # listens on http://127.0.0.1:9911

Run the add-on against it with:

    TESLA_AUTH_HOST=http://127.0.0.1:9911 TESLA_API_HOST=http://127.0.0.1:9911 \\
    TESLA_WSS_HOST=ws://127.0.0.1:9911 TESLA_DATA_DIR=/tmp/tesla \\
    TESLA_PORT=8099 python3 -m src

and sign in on the dashboard: the simulated sign-in page ends in the same kind of app link as Tesla's.
"""

import asyncio
import itertools
import json
import math
import os
import time

from aiohttp import WSMsgType, web

PORT = int(os.environ.get("MOCK_PORT", "9911"))
TESLA_ID = 1492931365719407
VEHICLE_ID = 1234567890
VIN = "5YJRE7EB0PF000001"
KM_PER_MILE = 1.609344
FULL_RANGE_KM = 498.0
# MOCK_FAST_CHARGE=1 turns the charge of the story into a Supercharger session that Tesla bills
FAST_CHARGE = os.environ.get("MOCK_FAST_CHARGE", "") not in ("", "0")
# What the simulated Supercharger bills, and in which currency
PRICE_PER_KWH = 0.45
BILL_CURRENCY = os.environ.get("MOCK_BILL_CURRENCY", "EUR")
# The car falls asleep when its data hasn't been read for this long while idle
SLEEP_AFTER = float(os.environ.get("MOCK_SLEEP_AFTER", "45"))

# Utrecht -> Amersfoort, roughly along the A28
ROUTE = [
    (52.0907, 5.1214),
    (52.0935, 5.1390),
    (52.0990, 5.1720),
    (52.1080, 5.2100),
    (52.1190, 5.2600),
    (52.1330, 5.3100),
    (52.1470, 5.3560),
    (52.1561, 5.3878),
]
# (phase, seconds)
STORY = [
    ("parked", 20),
    ("drive_out", 100),
    ("parked", 15),
    ("charge", 70),
    ("idle", 150),
    ("drive_back", 100),
    ("idle", 150),
]


def haversine_km(a, b) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


LEGS = [haversine_km(a, b) for a, b in itertools.pairwise(ROUTE)]
ROUTE_KM = sum(LEGS)


def along(fraction: float) -> tuple[float, float, int]:
    """Position and heading at a fraction (0..1) of the route."""
    target = max(0.0, min(1.0, fraction)) * ROUTE_KM
    for (a, b), leg in zip(itertools.pairwise(ROUTE), LEGS, strict=True):
        if target <= leg:
            t = target / leg
            heading = math.degrees(math.atan2((b[1] - a[1]) * math.cos(math.radians(a[0])), b[0] - a[0])) % 360
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, int(heading)
        target -= leg
    return (*ROUTE[-1], 90)


class Car:
    """The simulated car. Advanced by tick() once a second."""

    def __init__(self):
        self.started = time.time()
        self.odometer_km = 48211.0
        self.range_km = 0.62 * FULL_RANGE_KM
        self.lat, self.lon, self.heading = (*ROUTE[0], 90)
        self.at_end = False
        self.shift = None
        self.speed_kmh = 0.0
        self.power_kw = 0
        self.charging = False
        self.charge_added_kwh = 0.0
        self.charge_start_range = 0.0
        self.asleep = False
        self.last_read = time.time()
        self.phase = "parked"
        self.phase_started = time.time()
        self.story_index = 0
        self.listeners: set[web.WebSocketResponse] = set()
        # Finished Supercharger sessions, as (start, stop, kWh)
        self.billed_sessions: list[tuple[float, float, float]] = []

    @property
    def soc(self) -> int:
        return round(self.range_km / FULL_RANGE_KM * 100)

    def _next_phase(self, now: float) -> None:
        if self.phase == "charge" and FAST_CHARGE:
            self.billed_sessions.append((self.phase_started, now, self.charge_added_kwh))
        self.story_index = (self.story_index + 1) % len(STORY)
        self.phase = STORY[self.story_index][0]
        self.phase_started = now
        print(f"[mock] phase: {self.phase}")
        if self.phase.startswith("drive"):
            self.asleep = False
        if self.phase == "charge":
            self.charge_added_kwh = 0.0
            self.charge_start_range = self.range_km

    def tick(self) -> None:
        now = time.time()
        elapsed = now - self.phase_started
        duration = STORY[self.story_index][1]
        if elapsed >= duration:
            if self.phase.startswith("drive"):
                self.at_end = self.phase == "drive_out"
            self._next_phase(now)
            elapsed, duration = 0.0, STORY[self.story_index][1]

        self.shift, self.speed_kmh, self.power_kw, self.charging = None, 0.0, 0, False
        if self.phase.startswith("drive"):
            fraction = elapsed / duration
            # Accelerate, cruise, brake; the real distance is compressed into the phase
            self.speed_kmh = 118.0 * min(1.0, fraction * 6, (1 - fraction) * 6) + 6 * math.sin(elapsed / 4)
            self.speed_kmh = max(0.0, self.speed_kmh)
            step_km = ROUTE_KM / duration
            position = fraction if self.phase == "drive_out" else 1 - fraction
            self.lat, self.lon, heading = along(position)
            self.heading = heading if self.phase == "drive_out" else (heading + 180) % 360
            self.odometer_km += step_km
            self.range_km -= step_km * 1.12
            self.power_kw = round(4 + self.speed_kmh / 5 + 8 * math.sin(elapsed / 3))
            self.shift = "D"
        elif self.phase == "charge":
            self.charging = True
            # 11 kW on three phases (or 120 kW at a Supercharger), sped up so a minute adds a few kWh
            self.charge_added_kwh += (120.0 if FAST_CHARGE else 11.0) / 3600 * 60 / (8 if FAST_CHARGE else 1)
            self.range_km = min(FULL_RANGE_KM * 0.9, self.charge_start_range + self.charge_added_kwh / 0.150)
        elif self.phase == "idle" and not self.asleep and now - self.last_read > SLEEP_AFTER:
            self.asleep = True
            print("[mock] car fell asleep")

    def summary(self) -> dict:
        return {
            "id": TESLA_ID,
            "vehicle_id": VEHICLE_ID,
            "vin": VIN,
            "display_name": "Mockingbird",
            "state": "asleep" if self.asleep else "online",
            "in_service": False,
        }

    def stream_line(self) -> str:
        elevation = round(8 + 6 * math.sin(self.odometer_km))
        fields = [
            int(time.time() * 1000),
            round(self.speed_kmh / KM_PER_MILE) if self.shift else "",
            round(self.odometer_km / KM_PER_MILE, 1),
            self.soc,
            elevation,
            self.heading,
            round(self.lat, 6),
            round(self.lon, 6),
            self.power_kw,
            self.shift or "",
            round(self.range_km / KM_PER_MILE),
            round(self.range_km / KM_PER_MILE * 0.93),
            self.heading,
        ]
        return ",".join(str(field) for field in fields)

    def data(self) -> dict:
        self.last_read = time.time()
        now_ms = int(time.time() * 1000)
        miles = self.range_km / KM_PER_MILE
        charging_state = (
            "Charging" if self.charging else ("Complete" if self.phase == "idle" and self.at_end else "Disconnected")
        )
        return {
            **self.summary(),
            "drive_state": {
                "shift_state": self.shift,
                "speed": round(self.speed_kmh / KM_PER_MILE) if self.shift else None,
                "power": self.power_kw,
                "latitude": round(self.lat, 6),
                "longitude": round(self.lon, 6),
                "heading": self.heading,
                "timestamp": now_ms,
                "active_route_destination": "Amersfoort" if self.phase == "drive_out" else None,
                "active_route_minutes_to_arrival": 12.5 if self.phase == "drive_out" else None,
                "active_route_miles_to_arrival": 9.8 if self.phase == "drive_out" else None,
                "active_route_energy_at_arrival": 51 if self.phase == "drive_out" else None,
            },
            "charge_state": {
                "battery_level": self.soc,
                "usable_battery_level": self.soc - 1,
                "battery_range": round(miles, 2),
                "est_battery_range": round(miles * 0.93, 2),
                "ideal_battery_range": round(miles, 2),
                "charging_state": charging_state,
                "charge_limit_soc": 90,
                "charger_power": (120 if FAST_CHARGE else 11) if self.charging else 0,
                "charger_voltage": (395 if FAST_CHARGE else 230) if self.charging else 2,
                "charger_actual_current": (0 if FAST_CHARGE else 16) if self.charging else 0,
                "charger_phases": (None if FAST_CHARGE else 2) if self.charging else None,
                "charge_energy_added": round(self.charge_added_kwh, 2),
                "charge_rate": 44.0 if self.charging else 0.0,
                "minutes_to_full_charge": 95 if self.charging else 0,
                "charge_port_door_open": self.charging,
                "fast_charger_present": FAST_CHARGE and self.charging,
                "fast_charger_brand": "Tesla" if FAST_CHARGE and self.charging else "",
                "scheduled_charging_start_time": None,
                "battery_heater_on": False,
                "timestamp": now_ms,
            },
            "climate_state": {
                "inside_temp": 19.5 if self.shift else 14.2,
                "outside_temp": 11.0,
                "is_climate_on": bool(self.shift),
                "is_preconditioning": False,
                "driver_temp_setting": 20.0,
            },
            "vehicle_state": {
                "odometer": self.odometer_km / KM_PER_MILE,
                "locked": not self.shift,
                "sentry_mode": False,
                "is_user_present": bool(self.shift),
                "df": 0,
                "dr": 0,
                "pf": 0,
                "pr": 0,
                "ft": 0,
                "rt": 0,
                "fd_window": 0,
                "fp_window": 0,
                "rd_window": 0,
                "rp_window": 0,
                "tpms_pressure_fl": 2.9,
                "tpms_pressure_fr": 2.9,
                "tpms_pressure_rl": 2.85,
                "tpms_pressure_rr": 2.875,
                "car_version": "2026.38.4 0a1b2c3d4e5f",
                "software_update": {"status": "", "version": " ", "install_perc": 1},
                "vehicle_name": "Mockingbird",
                "timestamp": now_ms,
            },
            "vehicle_config": {
                "car_type": "roadster",
                "trim_badging": "",
                "exterior_color": "RedMulticoat",
                "wheel_type": "",
                "roof_color": "Glass",
                "exterior_trim": "Carbon",
                "charge_port_type": "US",
                "has_air_suspension": False,
            },
            "gui_settings": {"gui_distance_units": "km/hr", "gui_temperature_units": "C"},
        }


car = Car()


def authorized(request: web.Request) -> bool:
    return request.headers.get("Authorization", "").startswith("Bearer mock-access-")


AUTHORIZE_PAGE = """<!doctype html><meta charset="utf-8"><title>Simulated Tesla sign-in</title>
<body style="font-family:sans-serif;max-width:32rem;margin:4rem auto">
<h2>Simulated Tesla sign-in</h2>
<p>Like the real page, this ends by sending the browser to an app link (<code>tesla://auth/callback?code=...</code>)
that a browser can't open.</p>
<button id="go" style="padding:.6rem 1.2rem">Sign in</button>
<script>
document.getElementById('go').onclick = () => {
  const state = new URLSearchParams(location.search).get('state') || '';
  location.href = 'tesla://auth/callback?code=MOCK_' + Date.now() + '&state=' + encodeURIComponent(state)
    + '&issuer=' + encodeURIComponent(location.origin + '/oauth2/v3');
};
</script>"""


async def authorize(request: web.Request) -> web.Response:
    """The sign-in page: one button that 'logs in' and redirects to the app link."""
    if request.query.get("redirect_uri") != "tesla://auth/callback":
        return web.Response(text="The 'redirect_uri' supplied is not registered for this 'client_id'.", status=400)
    return web.Response(text=AUTHORIZE_PAGE, content_type="text/html")


async def token(request: web.Request) -> web.Response:
    body = await request.json()
    grant = body.get("grant_type")
    if grant == "authorization_code" and (
        not body.get("code_verifier") or body.get("redirect_uri") != "tesla://auth/callback"
    ):
        return web.json_response({"error": "invalid_request"}, status=400)
    if grant == "refresh_token" and not str(body.get("refresh_token", "")).startswith("mock-refresh-"):
        return web.json_response({"error": "invalid_grant", "error_description": "unknown refresh token"}, status=401)
    stamp = int(time.time())
    return web.json_response(
        {
            "access_token": f"mock-access-{stamp}",
            "refresh_token": f"mock-refresh-{stamp}",
            "id_token": "x." + "eyJlbWFpbCI6ICJkcml2ZXJAZXhhbXBsZS5jb20ifQ" + ".x",
            "expires_in": int(os.environ.get("MOCK_TOKEN_SECONDS", "28800")),
            "token_type": "Bearer",
        }
    )


async def products(request: web.Request) -> web.Response:
    if not authorized(request):
        return web.json_response({"error": "invalid bearer token"}, status=401)
    powerwall = {"energy_site_id": 42, "resource_type": "battery", "site_name": "Home"}
    return web.json_response({"response": [car.summary(), powerwall], "count": 2})


async def vehicle(request: web.Request) -> web.Response:
    if not authorized(request):
        return web.json_response({"error": "invalid bearer token"}, status=401)
    return web.json_response({"response": car.summary()})


async def vehicle_data(request: web.Request) -> web.Response:
    if not authorized(request):
        return web.json_response({"error": "invalid bearer token"}, status=401)
    if car.asleep:
        return web.json_response(
            {"response": None, "error": "vehicle unavailable: vehicle is offline or asleep"}, status=408
        )
    return web.json_response({"response": car.data()})


def iso(ts: float) -> str:
    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:]
    )


async def charging_history(request: web.Request) -> web.Response:
    """The account's Supercharger sessions with their fees, newest first."""
    if not authorized(request):
        return web.json_response({"error": "invalid bearer token"}, status=401)
    # One old session that matches nothing, then the ones of this run
    sessions = [(time.time() - 40 * 86400, time.time() - 40 * 86400 + 1500, 41.3), *car.billed_sessions]
    data = [
        {
            "sessionId": 2000000000 + index,
            "vin": VIN,
            "siteLocationName": "Amersfoort, Netherlands",
            "chargeStartDateTime": iso(start),
            "chargeStopDateTime": iso(stop),
            "unlatchDateTime": iso(stop + 60),
            "countryCode": "NL",
            "billingType": "IMMEDIATE",
            "vehicleMakeType": "TSLA",
            "fees": [
                {
                    "sessionFeeId": 1,
                    "feeType": "CHARGING",
                    "currencyCode": BILL_CURRENCY,
                    "pricingType": "PAYMENT",
                    "rateBase": PRICE_PER_KWH,
                    "usageBase": round(kwh, 3),
                    "totalDue": round(kwh * PRICE_PER_KWH, 2),
                    "uom": "kwh",
                    "isPaid": True,
                    "status": "PAID",
                },
                {"sessionFeeId": 2, "feeType": "PARKING", "currencyCode": BILL_CURRENCY, "totalDue": 0, "uom": "min"},
            ],
        }
        for index, (start, stop, kwh) in enumerate(sessions)
    ]
    page = int(request.query.get("pageNo", 1))
    size = int(request.query.get("pageSize", 50))
    ordered = list(reversed(data))
    return web.json_response({"data": ordered[(page - 1) * size : page * size], "totalResults": len(data)})


async def wake_up(request: web.Request) -> web.Response:
    car.asleep = False
    car.last_read = time.time()
    return web.json_response({"response": car.summary()})


async def streaming(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_bytes(json.dumps({"msg_type": "control:hello", "connection_timeout": 0}).encode())
    try:
        async for message in ws:
            if message.type not in (WSMsgType.TEXT, WSMsgType.BINARY):
                continue
            hello = json.loads(message.data)
            if hello.get("msg_type") != "data:subscribe_oauth":
                continue
            if not str(hello.get("token", "")).startswith("mock-access-"):
                error = {
                    "msg_type": "data:error",
                    "tag": hello.get("tag"),
                    "error_type": "client_error",
                    "value": "Can't validate token.",
                }
                await ws.send_bytes(json.dumps(error).encode())
                continue
            car.listeners.add(ws)
    finally:
        car.listeners.discard(ws)
    return ws


async def simulate(app: web.Application):
    async def loop():
        last_line = ""
        while True:
            car.tick()
            line = car.stream_line()
            # Like the real car: only speak when something changed, and never while asleep
            if not car.asleep and line.split(",", 1)[1] != last_line:
                last_line = line.split(",", 1)[1]
                payload = json.dumps({"msg_type": "data:update", "tag": str(VEHICLE_ID), "value": line}).encode()
                for ws in list(car.listeners):
                    try:
                        await ws.send_bytes(payload)
                    except Exception:
                        car.listeners.discard(ws)
            await asyncio.sleep(1)

    task = asyncio.create_task(loop())
    yield
    task.cancel()


def main() -> None:
    app = web.Application()
    app.router.add_get("/oauth2/v3/authorize", authorize)
    app.router.add_post("/oauth2/v3/token", token)
    app.router.add_get("/api/1/products", products)
    app.router.add_get(r"/api/1/vehicles/{id:\d+}", vehicle)
    app.router.add_get(r"/api/1/vehicles/{id:\d+}/vehicle_data", vehicle_data)
    app.router.add_post(r"/api/1/vehicles/{id:\d+}/wake_up", wake_up)
    app.router.add_get("/api/1/dx/charging/history", charging_history)
    app.router.add_get("/mobile-app/charging/history", charging_history)
    app.router.add_get("/streaming/", streaming)
    app.cleanup_ctx.append(simulate)
    web.run_app(
        app, host="127.0.0.1", port=PORT, print=lambda *_: print(f"[mock] Tesla stand-in on http://127.0.0.1:{PORT}")
    )


if __name__ == "__main__":
    main()
