"""Arcade - main entry point."""

import asyncio
import contextlib
import logging
import os
import signal
import sys

from .config import DATA_DIR, MAME_DIR, Config
from .library import Library
from .saves import Saves
from .stats import Stats
from .thumbs import Thumbs
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

WEB_PORT = int(os.environ.get("ARCADE_WEB_PORT", "8099"))


async def main() -> int:
    logger.info("Arcade starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level == "debug":
        logging.getLogger().setLevel(logging.DEBUG)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        config.rom_folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Cannot create the ROM folder {config.rom_folder}: {e}")
        return 1
    library = Library(MAME_DIR / "games.json", config.rom_folder)
    saves = Saves(DATA_DIR / "saves")
    stats = Stats(DATA_DIR / "stats.sqlite3")
    webui = WebUI(WEB_PORT, library, saves, stats, Thumbs(DATA_DIR / "thumbs"))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await webui.start()
        await stop.wait()
    finally:
        await webui.stop()
        stats.close()
    logger.info("Arcade stopped")
    return 0


if __name__ == "__main__":
    exit_code = 0
    with contextlib.suppress(KeyboardInterrupt):
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
