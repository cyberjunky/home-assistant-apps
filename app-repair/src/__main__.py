"""Repair - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .database import Database
from .engine import Engine
from .ha import HA
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"Repair {APP_VERSION} starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = Database(DATA_DIR / "repair.db")
    ha = HA()
    if not ha.available:
        logger.warning("No Supervisor token: this app only works as a Home Assistant app")
    engine = Engine(config, database, ha)
    webui = WebUI(WEB_PORT, config, engine, database)

    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(engine.run(), name="engine"),
    ]

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
    for task in [*services, stopper]:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*services, return_exceptions=True)
    await ha.close()
    database.close()
    logger.info("Repair stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
