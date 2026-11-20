"""Aircraft photos: one of an aircraft by its registration, from planespotters.net.

Only when switched on on the Settings page: the registration of the aircraft picked on the map
goes to planespotters.net. The page shows their thumbnail from their own server, with the
photographer's name and a link to the photo, as their API asks.
"""

import logging
import time

import aiohttp

from .config import ADDON_VERSION

logger = logging.getLogger(__name__)

PHOTO_API_URL = "https://api.planespotters.net/pub/photos/reg/{}"
# An aircraft's photo is asked for once a day at most, and no more than this many are remembered
KEEP_SECONDS = 24 * 60 * 60
KEEP_COUNT = 500
# After an answer that is no answer (planespotters.net out of reach, or asked too often)
RETRY_SECONDS = 5 * 60


def photo_of(data) -> dict | None:
    """The first photo in planespotters.net's answer as {url, link, photographer}, or None."""
    photos = data.get("photos") if isinstance(data, dict) else None
    if not isinstance(photos, list) or not photos or not isinstance(photos[0], dict):
        return None
    photo = photos[0]
    thumbnail = photo.get("thumbnail_large") or photo.get("thumbnail") or {}
    url = thumbnail.get("src") if isinstance(thumbnail, dict) else None
    link = photo.get("link")
    # Both go into the page: only web addresses
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    if not isinstance(link, str) or not link.startswith("https://"):
        link = None
    return {"url": url, "link": link, "photographer": str(photo.get("photographer") or "")[:80]}


class Photos:
    def __init__(self):
        self._session: aiohttp.ClientSession | None = None
        # registration → (when asked, the photo or None, how long that stands)
        self._known: dict[str, tuple[float, dict | None, float]] = {}

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": f"SDRadio/{ADDON_VERSION} (Home Assistant app)"},
                timeout=aiohttp.ClientTimeout(total=8),
            )
        return self._session

    async def get(self, registration: str) -> dict | None:
        now = time.monotonic()
        known = self._known.get(registration)
        if known and now - known[0] < known[2]:
            return known[1]
        photo, keep = await self._ask(registration)
        if len(self._known) >= KEEP_COUNT:
            del self._known[min(self._known, key=lambda r: self._known[r][0])]
        self._known[registration] = (now, photo, keep)
        return photo

    async def _ask(self, registration: str) -> tuple[dict | None, float]:
        try:
            async with self._client().get(PHOTO_API_URL.format(registration)) as response:
                if response.status != 200:
                    logger.debug(f"No photo of {registration}: planespotters.net answered {response.status}")
                    return None, RETRY_SECONDS
                return photo_of(await response.json(content_type=None)), KEEP_SECONDS
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            logger.debug(f"No photo of {registration}: {e}")
            return None, RETRY_SECONDS

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
