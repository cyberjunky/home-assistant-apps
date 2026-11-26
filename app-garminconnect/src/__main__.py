"""Garmin Connect - main entry point."""

import asyncio
import contextlib
import logging
import os
import signal
import sys

from .account import GarminAccount
from .collector import Collector
from .config import ADDON_VERSION, WEB_PORT, Config
from .database import Database
from .ha_client import HAClient
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
# python-garminconnect and its HTTP clients report every request; that would be a line per request
for noisy in ("garminconnect", "urllib3", "requests", "curl_cffi"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def _factory():
    """The Garmin client. GARMIN_FAKE=1 is for development only: a made-up account (tools/fake_garmin.py)."""
    if os.environ.get("GARMIN_FAKE") == "1":
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        from fake_garmin import FakeGarmin

        return FakeGarmin
    from garminconnect import Garmin

    return Garmin


async def main() -> int:
    logger.info(f"Garmin Connect {ADDON_VERSION} starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    database = Database()
    account = GarminAccount(factory=_factory())
    collector = Collector(config, database, account)
    ha_client = HAClient()
    webui = WebUI(WEB_PORT, config, database, account, collector, ha_client)

    enabled = [group for group, on in config.datasets.items() if on]
    logger.info(f"Data sets: {', '.join(enabled) or 'none'}; update every {config.update_interval_minutes} minutes")
    if not account.has_tokens:
        logger.info("Open the dashboard and sign in to your Garmin account to start")

    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(collector.run(), name="collector"),
    ]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    # A service failing (e.g. port already in use) is fatal: exit non-zero so
    # the finish script stops the app instead of restarting it in a loop
    exit_code = 0
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([stopper, *services], return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            logger.error(f"Service {task.get_name()} failed", exc_info=task.exception())
            exit_code = 1

    logger.info("Shutting down...")
    stopper.cancel()
    for task in services:
        task.cancel()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.gather(*services, return_exceptions=True), timeout=6.0)
    for closer in (webui.stop, ha_client.close):
        with contextlib.suppress(Exception):
            await closer()
    database.close()
    logger.info("Garmin Connect stopped")
    return exit_code


if __name__ == "__main__":
    exit_code = 0
    with contextlib.suppress(KeyboardInterrupt):
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
