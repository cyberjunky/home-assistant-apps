"""Solar production forecast: Forecast.Solar (free, no account), Open-Meteo as a fallback.

Forecast.Solar: GET https://api.forecast.solar/estimate/{lat}/{lon}/{tilt}/{azimuth}/{kwp}
per plane, summed. Open-Meteo gives global irradiance; PV is estimated from it as
kWp x GHI x performance ratio, which ignores tilt and azimuth but keeps a forecast
coming when Forecast.Solar's backend errors (it often does). Results are cached for
30 minutes, on disk too, so a restart does not cost a request. A failed fetch is
never cached: the controller must not read an empty forecast as "no sun".
"""

import asyncio
import contextlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

from .config import ADDON_VERSION, Config
from .ha_client import HAClient

logger = logging.getLogger(__name__)

CACHE_TTL = 1800
PERFORMANCE_RATIO = 0.85


class Forecast:
    """The merged forecast for all planes, cached."""

    def __init__(self, config: Config, ha_client: HAClient, data_dir: Path):
        self.enabled = config.forecast_enabled
        self.lat = config.latitude
        self.lon = config.longitude
        self.planes = [p.as_dict() for p in config.planes]
        self.ha_client = ha_client
        self.cache_file = data_dir / "forecast_cache.json"
        self.location_source = "configuration" if (self.lat or self.lon) else "unset"
        self._data: dict = {}
        self._session: aiohttp.ClientSession | None = None
        self._fetching: asyncio.Lock = asyncio.Lock()
        self.error = ""
        try:
            disk = json.loads(self.cache_file.read_text())
            if disk.get("cache_key") == self.cache_key and disk.get("watt_hours_day"):
                self._data = disk
        except (OSError, ValueError):
            pass

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.planes) and (self.lat != 0 or self.lon != 0)

    @property
    def cache_key(self) -> str:
        return f"{self.lat}:{self.lon}:{json.dumps(self.planes, sort_keys=True)}"

    async def resolve_location(self) -> None:
        """Take the home location from Home Assistant when the options leave it at 0/0."""
        if self.lat or self.lon or not self.ha_client.available:
            return
        location = await self.ha_client.get_home_location()
        if location:
            self.lat, self.lon = round(location[0], 4), round(location[1], 4)
            self.location_source = "Home Assistant"
            logger.info(f"Forecast uses Home Assistant's home location {self.lat}, {self.lon}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": f"GoodWe/{ADDON_VERSION} (Home Assistant app)"},
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------
    @property
    def stale(self) -> bool:
        return not self._data or time.time() - self._data.get("fetched_at", 0) >= CACHE_TTL

    async def fetch(self, force: bool = False) -> dict:
        """The forecast, from the cache unless it is older than 30 minutes (or force)."""
        if not self.configured:
            return {}
        if not force and not self.stale:
            return self._data
        async with self._fetching:
            if not force and not self.stale:
                return self._data
            result = await self._fetch()
            if result.get("watt_hours_day"):
                self._data = result
                self.error = ""
                with contextlib.suppress(OSError):
                    self.cache_file.write_text(json.dumps(result))
            else:
                # Both sources failed: keep what we had, say so, and try again next time
                self.error = "; ".join(result.get("errors") or ["no data"])[:300]
                logger.warning(f"Forecast fetch failed: {self.error}")
                if self._data:
                    self._data["fetched_at"] = time.time() - CACHE_TTL + 300  # retry in 5 minutes
            return self._data

    async def _fetch(self) -> dict:
        watts: dict[str, float] = {}
        wh_day: dict[str, float] = {}
        errors: list[str] = []
        tz = None
        session = await self._get_session()
        for plane in self.planes:
            url = (
                f"https://api.forecast.solar/estimate/{self.lat}/{self.lon}"
                f"/{int(plane['tilt'])}/{int(plane['azimuth'])}/{plane['kwp']}"
            )
            try:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        text = (await resp.text())[:160]
                        errors.append(f"{plane['name']}: Forecast.Solar HTTP {resp.status}: {text}")
                        continue
                    body = await resp.json()
                tz = tz or (body.get("message") or {}).get("info", {}).get("timezone")
                result = body.get("result") or {}
                for ts, w in (result.get("watts") or {}).items():
                    watts[ts] = watts.get(ts, 0) + float(w)
                for day, wh in (result.get("watt_hours_day") or {}).items():
                    wh_day[day] = wh_day.get(day, 0) + float(wh)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                errors.append(f"{plane['name']}: {type(e).__name__}: {e}")
        source = "forecast.solar"
        if not wh_day:
            logger.info("Forecast.Solar gave nothing, trying Open-Meteo")
            watts, wh_day, tz2, error = await self._fetch_openmeteo(session)
            if error:
                errors.append(error)
            if wh_day:
                source = "open-meteo"
                tz = tz or tz2
        return {
            "watts": watts,
            "watt_hours_day": wh_day,
            "fetched_at": int(time.time()),
            "cache_key": self.cache_key,
            "errors": errors,
            "source": source,
            "timezone": tz,
        }

    async def _fetch_openmeteo(self, session: aiohttp.ClientSession) -> tuple[dict, dict, str | None, str]:
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={self.lat}&longitude={self.lon}"
            "&hourly=shortwave_radiation&forecast_days=3&timezone=auto"
        )
        watts: dict[str, float] = {}
        wh_day: dict[str, float] = {}
        try:
            async with session.get(url) as resp:
                resp.raise_for_status()
                body = await resp.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            return watts, wh_day, None, f"Open-Meteo: {type(e).__name__}: {e}"
        total_kwp = sum(float(p["kwp"]) for p in self.planes)
        hourly = body.get("hourly") or {}
        for ts, ghi in zip(hourly.get("time") or [], hourly.get("shortwave_radiation") or [], strict=False):
            if ghi is None:
                continue
            power = total_kwp * float(ghi) * PERFORMANCE_RATIO
            key = ts.replace("T", " ") + ":00"
            watts[key] = watts.get(key, 0) + power
            wh_day[ts[:10]] = wh_day.get(ts[:10], 0) + power
        return watts, wh_day, body.get("timezone"), ""

    # ------------------------------------------------------------------
    # Reading the cached forecast
    # ------------------------------------------------------------------
    def _now(self) -> datetime:
        tz = self._data.get("timezone")
        try:
            return datetime.now(ZoneInfo(tz)) if tz else datetime.now()
        except (KeyError, ValueError):
            return datetime.now()

    def hour_watts(self, when: datetime | None = None) -> float:
        """Forecast power (W) for the hour of `when` (now by default); 0 at night or without a forecast."""
        when = when or self._now()
        prefix = when.strftime("%Y-%m-%d %H")
        best = 0.0
        for ts, w in self._data.get("watts", {}).items():
            if ts.startswith(prefix):
                best = max(best, float(w))
        return best

    def hour_kwh(self) -> float:
        """Forecast production (kWh) for the current hour."""
        return round(self.hour_watts() / 1000, 3)

    def hourly(self, day: date | None = None) -> list[dict]:
        """[{ts, watts}] for one local day (today by default)."""
        day = day or self._now().date()
        prefix = day.isoformat()
        rows = []
        for ts, w in sorted(self._data.get("watts", {}).items()):
            if ts.startswith(prefix):
                try:
                    stamp = int(datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").timestamp())
                except ValueError:
                    continue
                rows.append({"ts": stamp, "watts": round(float(w))})
        return rows

    def daily(self) -> list[dict]:
        return [
            {"date": day, "kwh": round(wh / 1000, 2)}
            for day, wh in sorted(self._data.get("watt_hours_day", {}).items())
        ]

    def day_kwh(self, day: date) -> float | None:
        wh = self._data.get("watt_hours_day", {}).get(day.isoformat())
        return round(wh / 1000, 2) if wh is not None else None

    def summary(self) -> dict:
        """What the live stream carries about the forecast."""
        today = self._now().date()
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "today_kwh": self.day_kwh(today),
            "tomorrow_kwh": self.day_kwh(today + timedelta(days=1)),
            "hour_kwh": self.hour_kwh() if self._data else None,
            "source": self._data.get("source"),
            "fetched_at": self._data.get("fetched_at"),
            "error": self.error,
            "location": {"lat": self.lat, "lon": self.lon, "source": self.location_source},
            "planes": self.planes,
        }
