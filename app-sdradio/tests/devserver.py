"""Runs the app with the simulated receiver, for working on the page without an SDR.

    SDRADIO_DATA_DIR=/tmp/sdradio python -m tests.devserver
then open http://localhost:8099/

Without Home Assistant there is no home location or map tile proxy: the home is set to where the
simulated aircraft fly, and map tiles come from OpenStreetMap directly (a few, cached here).
"""

import asyncio
import logging

import aiohttp

from src.config import DATA_DIR, WEB_PORT, Config
from src.database import Database
from src.webui import WebUI
from tests.simulator import ADSB_HOME, SimulatedDevice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = Database(DATA_DIR / "sdradio.db")
    webui = WebUI(WEB_PORT, Config(), db, SimulatedDevice, data_dir=DATA_DIR)
    webui.radio.location = ADSB_HOME
    tiles: dict = {}

    async def map_tile(z: int, x: int, y: int) -> bytes | None:
        if (z, x, y) not in tiles:
            headers = {"User-Agent": "SDRadio development server"}
            url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            async with aiohttp.ClientSession(headers=headers) as session, session.get(url) as response:
                tiles[(z, x, y)] = await response.read() if response.status == 200 else None
        return tiles[(z, x, y)]

    webui.ha.map_tile = map_tile
    try:
        await webui.start()
    finally:
        await webui.stop()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
