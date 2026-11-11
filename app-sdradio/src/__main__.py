"""SDRadio - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import ADDON_VERSION, DATA_DIR, WEB_PORT, Config
from .database import Database
from .device import SoapyDevice
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"SDRadio {ADDON_VERSION} starting...")

    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = Database(DATA_DIR / "sdradio.db")
    webui = WebUI(WEB_PORT, config, database, SoapyDevice)
    logger.info(f"Receiver: {config.receiver.name} at {config.receiver.sample_rate / 1e6:g} MS/s")

    server = asyncio.create_task(webui.start(), name="webui")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    # The web server failing (e.g. port already in use) is fatal: exit non-zero so the
    # finish script stops the app instead of restarting it in a loop
    exit_code = 0
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([stopper, server], return_when=asyncio.FIRST_COMPLETED)
    if server in done and not server.cancelled() and server.exception():
        logger.error("The web server failed", exc_info=server.exception())
        exit_code = 1

    logger.info("Shutting down...")
    stopper.cancel()
    server.cancel()
    with contextlib.suppress(Exception):
        await webui.stop()
    database.close()
    logger.info("SDRadio stopped")
    return exit_code


if __name__ == "__main__":
    exit_code = 0
    with contextlib.suppress(KeyboardInterrupt):
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
