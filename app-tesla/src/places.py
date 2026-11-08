"""Names for the places drives and charges start and end.

A position inside one of Home Assistant's zones gets the zone's name ("Home",
"Work"). Anything else is looked up once at OpenStreetMap's Nominatim and
remembered, so the same place is never asked for twice.
"""

import asyncio
import logging
import math
import time

import aiohttp

from .config import ADDON_VERSION
from .database import Database, place_key
from .ha_client import HAClient, HAClientError

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim's usage policy: identify the application, at most one request per second
USER_AGENT = f"ha-tesla-app/{ADDON_VERSION} (Home Assistant add-on; https://github.com/cyberjunky/home-assistant-apps)"
REQUEST_SPACING = 1.5
# After a failed lookup (no internet, service down) nothing is asked for this long
FAILURE_PAUSE = 600
ZONE_REFRESH = 3600


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


def address_label(result: dict) -> tuple[str | None, str | None, str | None]:
    """(label, city, country) from a Nominatim answer: "Kerkstraat 12, Utrecht" or "Supercharger, Breda"."""
    address = result.get("address") or {}
    city = next((address[k] for k in ("city", "town", "village", "municipality", "hamlet") if address.get(k)), None)
    road = address.get("road") or address.get("pedestrian")
    street = " ".join(part for part in (road, address.get("house_number")) if part) if road else None
    # A named place (charging station, shop, car park) says more than its street
    name = result.get("name") if result.get("name") and result.get("name") != road else None
    parts = [part for part in (name or street, city) if part]
    label = ", ".join(parts) if parts else result.get("display_name")
    return label, city, address.get("country")


class Places:
    """Turns coordinates into a place name: a Home Assistant zone or a street address."""

    def __init__(self, database: Database, ha_client: HAClient, geocoding: bool):
        self.database = database
        self.ha_client = ha_client
        self.geocoding = geocoding
        self.zones: list[dict] = []
        self.zones_loaded_at = 0
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._paused_until = 0.0
        self._filling = False
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def keep_zones_fresh(self) -> None:
        """Load Home Assistant's zones now and every hour (they can be edited at any time)."""
        while True:
            if self.ha_client.available:
                try:
                    zones = await self.ha_client.get_zones()
                    if [(z["name"], z["radius"]) for z in zones] != [(z["name"], z["radius"]) for z in self.zones]:
                        names = ", ".join(f"{z['name']} ({z['radius']:.0f} m)" for z in zones) or "none"
                        logger.info(f"Home Assistant zones: {names}")
                    self.zones = zones
                    self.zones_loaded_at = int(time.time())
                except HAClientError as e:
                    logger.warning(f"Could not load Home Assistant zones: {e}")
            await asyncio.sleep(ZONE_REFRESH)

    def zone_at(self, lat: float, lon: float) -> str | None:
        """The smallest zone around a position, None when it is in none."""
        inside = [z for z in self.zones if distance_m(lat, lon, z["lat"], z["lon"]) <= z["radius"]]
        return min(inside, key=lambda z: z["radius"])["name"] if inside else None

    async def name(self, lat: float | None, lon: float | None) -> str | None:
        """A name for a position, None when unknown (no position, lookups disabled or failed)."""
        if lat is None or lon is None:
            return None
        zone = self.zone_at(lat, lon)
        if zone:
            return zone
        if not self.geocoding:
            return None
        # About 11 m: the same parking spot gives the same key
        key = f"{lat:.4f},{lon:.4f}"
        cached = self.database.get_address(key)
        if cached:
            return cached["label"]
        async with self._lock:
            if time.monotonic() < self._paused_until:
                return None
            wait = self._last_request + REQUEST_SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                result = await self._reverse(lat, lon)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                logger.warning(f"Address lookup failed, trying again later: {type(e).__name__}: {e}")
                self._paused_until = time.monotonic() + FAILURE_PAUSE
                return None
            finally:
                self._last_request = time.monotonic()
        label, city, country = address_label(result)
        self.database.put_address(key, label, city, country)
        return label

    async def _reverse(self, lat: float, lon: float) -> dict:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": USER_AGENT}, timeout=aiohttp.ClientTimeout(total=20)
            )
        params = {"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "zoom": "18", "addressdetails": "1"}
        async with self._session.get(NOMINATIM_URL, params=params) as resp:
            resp.raise_for_status()
            result = await resp.json()
        if not isinstance(result, dict) or result.get("error"):
            # "Unable to geocode": open sea, no data. Remembered as unnamed.
            return {}
        return result

    def apply_zones(self) -> dict:
        """Name every recorded place that lies inside a Home Assistant zone after the zone.

        For zones created after the trips were recorded, and for places Nominatim named
        wrongly where a zone now says what they are. Returns how many were changed.
        """
        drives = charges = 0
        for drive in self.database.query(
            "SELECT id, start_lat, start_lon, end_lat, end_lon, start_place, end_place FROM drives"
            " WHERE end_ts IS NOT NULL AND (start_lat IS NOT NULL OR end_lat IS NOT NULL)"
        ):
            start = drive["start_place"]
            end = drive["end_place"]
            if drive["start_lat"] is not None:
                start = self.zone_at(drive["start_lat"], drive["start_lon"]) or start
            if drive["end_lat"] is not None:
                end = self.zone_at(drive["end_lat"], drive["end_lon"]) or end
            if (start, end) != (drive["start_place"], drive["end_place"]):
                self.database.execute(
                    "UPDATE drives SET start_place = ?, end_place = ? WHERE id = ?", (start, end, drive["id"])
                )
                drives += 1
        for charge in self.database.query("SELECT id, lat, lon, place FROM charges WHERE lat IS NOT NULL"):
            zone = self.zone_at(charge["lat"], charge["lon"])
            if zone and zone != charge["place"]:
                self._rename_charge(charge, zone)
                charges += 1
        return {"zones": len(self.zones), "drives": drives, "charges": charges}

    def rename_place(self, old: str, new: str) -> dict:
        """Give every trip, charging session and cached address with one name another name."""
        drives = self.database.execute("UPDATE drives SET start_place = ? WHERE start_place = ?", (new, old))
        drives += self.database.execute("UPDATE drives SET end_place = ? WHERE end_place = ?", (new, old))
        charges = 0
        for charge in self.database.query("SELECT id, lat, lon, place FROM charges WHERE place = ?", (old,)):
            self._rename_charge(charge, new)
            charges += 1
        addresses = self.database.execute("UPDATE addresses SET label = ? WHERE label = ?", (new, old))
        return {"drives": drives, "charges": charges, "addresses": addresses}

    def _rename_charge(self, charge: dict, new: str) -> None:
        """Rename a charging session's place and carry its location's price tag along."""
        old_key = place_key(charge["place"], charge["lat"], charge["lon"])
        new_key = place_key(new, charge["lat"], charge["lon"])
        tag = self.database.location_tag(old_key) if old_key else None
        self.database.execute("UPDATE charges SET place = ? WHERE id = ?", (new, charge["id"]))
        if tag and new_key and new_key != old_key and not self.database.location_tag(new_key):
            self.database.set_location_tag(new_key, tag)

    async def fill_missing(self) -> None:
        """Name the drives and charges that have a position but no place name yet."""
        if self._filling:
            return
        self._filling = True
        try:
            await self._fill_missing()
        finally:
            self._filling = False

    async def _fill_missing(self) -> None:
        for drive in self.database.query(
            "SELECT id, start_lat, start_lon, end_lat, end_lon, start_place, end_place FROM drives"
            " WHERE end_ts IS NOT NULL AND (start_place IS NULL OR end_place IS NULL)"
            " ORDER BY start_ts DESC LIMIT 500"
        ):
            start = drive["start_place"] or await self.name(drive["start_lat"], drive["start_lon"])
            end = drive["end_place"] or await self.name(drive["end_lat"], drive["end_lon"])
            if start or end:
                self.database.execute(
                    "UPDATE drives SET start_place = ?, end_place = ? WHERE id = ?", (start, end, drive["id"])
                )
        for charge in self.database.query(
            "SELECT id, lat, lon FROM charges WHERE place IS NULL AND lat IS NOT NULL ORDER BY start_ts DESC LIMIT 500"
        ):
            place = await self.name(charge["lat"], charge["lon"])
            if place:
                self.database.execute("UPDATE charges SET place = ? WHERE id = ?", (place, charge["id"]))
