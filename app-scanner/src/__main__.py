"""Scanner - scan with any scanner SANE knows, into an archive of searchable PDFs."""

import asyncio
import contextlib
import logging
import signal
import sys

from .archive import Archive
from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .pages import Tray
from .settings import Settings
from .station import Station
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def first_search(station: Station) -> None:
    """Look for scanners at start: driverless USB scanners need a moment before they answer."""
    await asyncio.sleep(3)
    with contextlib.suppress(Exception):
        await station.find_devices()


async def main() -> int:
    logger.info(f"Scanner {APP_VERSION} starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    settings = Settings(DATA_DIR / "settings.json")
    tray = Tray(DATA_DIR / "tray")
    archive = Archive(DATA_DIR / "archive")
    station = Station(config, settings, tray, archive)
    webui = WebUI(WEB_PORT, config, settings, tray, archive, station)
    services = [
        asyncio.create_task(webui.start(), name="webui"),
    ]
    search = asyncio.create_task(first_search(station), name="search")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    stopper = asyncio.create_task(stop.wait(), name="stop")

    done, _ = await asyncio.wait([*services, stopper], return_when=asyncio.FIRST_COMPLETED)
    exit_code = 0
    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            logger.error(f"Service {task.get_name()} failed: {task.exception()!r}")
            exit_code = 2
    # A scan in progress is told to stop, so the scanner isn't left with its lamp on
    await station.cancel()
    await station.wait()
    for task in [*services, stopper, search]:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*services, search, return_exceptions=True)
    archive.close()
    logger.info("Scanner stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
