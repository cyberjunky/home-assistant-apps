"""Runs the app with the simulated receiver, for working on the page without an SDR.

    SDRADIO_DATA_DIR=/tmp/sdradio python -m tests.devserver
then open http://localhost:8099/
"""

import asyncio
import logging

from src.config import DATA_DIR, WEB_PORT, Config
from src.database import Database
from src.webui import WebUI
from tests.simulator import SimulatedDevice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = Database(DATA_DIR / "sdradio.db")
    webui = WebUI(WEB_PORT, Config(), db, SimulatedDevice, data_dir=DATA_DIR)
    try:
        await webui.start()
    finally:
        await webui.stop()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
