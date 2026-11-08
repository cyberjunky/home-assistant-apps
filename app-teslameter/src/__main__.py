"""TeslaMeter - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .billing import ChargingCosts
from .config import ADDON_VERSION, WEB_PORT, Config
from .database import Database, DatabaseLayoutError
from .ha_client import HAClient
from .monitor import Garage
from .mqtt import MqttPublisher
from .places import Places
from .tesla_api import TeslaApi
from .tesla_auth import TeslaAuth
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
# The HTTP client reports every request at info level; that would be a line per poll
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"TeslaMeter {ADDON_VERSION} starting...")

    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the add-on configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    try:
        database = Database()
    except DatabaseLayoutError as e:
        logger.error(e)
        return 1
    ha_client = HAClient()
    auth = TeslaAuth()
    api = TeslaApi(auth)
    places = Places(database, ha_client, config.geocoding)
    garage = Garage(config, database, auth, api, places)
    mqtt = MqttPublisher(config, ha_client, garage)
    costs = ChargingCosts(config, database, auth, api)
    webui = WebUI(WEB_PORT, config, database, auth, garage, mqtt, ha_client, costs)

    cars = database.list_cars()
    logger.info(
        f"{len(cars)} car(s) known, Tesla account {'signed in' if auth.signed_in else 'not signed in yet'},"
        f" streaming {'on' if config.streaming else 'off'}, address lookup {'on' if config.geocoding else 'off'}"
    )
    if not auth.signed_in:
        logger.info("Open the TeslaMeter dashboard and sign in to your Tesla account to start logging")

    async def name_places() -> None:
        # Drives and charges recorded while address lookups failed get their names now
        await asyncio.sleep(20)
        await places.fill_missing()

    async def map_cells() -> None:
        # After the update to 0.5.6 (and after an import) the drives still have to be mapped for the
        # Stats map; a few at a time, in a worker thread, so logging goes on undisturbed
        pending = database.unmapped_drives()
        if pending:
            logger.info(f"Mapping {pending} drives for the Stats map...")
            mapped = await asyncio.to_thread(database.map_missing_cells)
            logger.info(f"Mapped {mapped} drives for the Stats map")

    async def forever(service) -> None:
        """A service that ends on its own (MQTT without a broker) must not end the add-on."""
        await service
        await asyncio.Event().wait()

    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(garage.run(), name="garage"),
        asyncio.create_task(forever(mqtt.run()), name="mqtt"),
        asyncio.create_task(forever(costs.run()), name="costs"),
        asyncio.create_task(places.keep_zones_fresh(), name="zones"),
        asyncio.create_task(forever(name_places()), name="places"),
        asyncio.create_task(forever(map_cells()), name="cells"),
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    # A service failing (e.g. port already in use) is fatal: exit non-zero so
    # the finish script stops the add-on instead of restarting it in a loop
    exit_code = 0
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait([stopper, *services], return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            logger.error(f"Service {task.get_name()} failed", exc_info=task.exception())
            exit_code = 1

    logger.info("Shutting down...")
    webui.closing()
    stopper.cancel()
    for task in services:
        task.cancel()
    # Stay within S6_SERVICES_GRACETIME (see Dockerfile), or s6 kills us mid-shutdown
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(asyncio.gather(*services, return_exceptions=True), timeout=6.0)
    for closer in (webui.stop, api.close, auth.close, places.close, ha_client.close):
        with contextlib.suppress(Exception):
            await closer()
    database.close()
    logger.info("TeslaMeter stopped")
    return exit_code


if __name__ == "__main__":
    exit_code = 0
    with contextlib.suppress(KeyboardInterrupt):
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
