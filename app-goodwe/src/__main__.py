"""GoodWe - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import ADDON_VERSION, DATA_DIR, WEB_PORT, Config
from .controller import BatteryController
from .database import Database
from .events import EventBus
from .forecast import Forecast
from .ha_client import HAClient
from .inverter import InverterLink
from .miners import Miners
from .mqtt import MqttPublisher
from .recorder import Recorder
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"GoodWe {ADDON_VERSION} starting...")

    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the add-on configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = Database(DATA_DIR / "goodwe.db")
    database.miner_names = [m.name for m in config.miners]
    events = EventBus(database)
    ha_client = HAClient()
    forecast = Forecast(config, ha_client, DATA_DIR)
    link = InverterLink(config, events)
    controller = BatteryController(link, forecast, events, DATA_DIR)
    # After the controller (its listener stamps the surplus signal), before the recorder (stores it)
    controller.miners = Miners(config, ha_client, link, events, DATA_DIR)
    controller.miners.controller = controller
    recorder = Recorder(database, link, forecast, events)
    mqtt = MqttPublisher(config, ha_client, link, controller, forecast, events)
    webui = WebUI(WEB_PORT, config, database, link, controller, forecast, mqtt, events)

    await forecast.resolve_location()
    logger.info(
        f"Inverter at {config.inverter_host}, read every {config.poll_seconds} s; forecast"
        f" {'on' if forecast.configured else 'off'} ({config.total_kwp} kWp, {len(config.planes)} panel group(s)); MQTT"
        f" {'on' if config.mqtt_enabled else 'off'}"
    )

    async def forever(service) -> None:
        """A service that ends on its own (MQTT without a broker) must not end the add-on."""
        await service
        await asyncio.Event().wait()

    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(link.run(), name="inverter"),
        asyncio.create_task(recorder.run(), name="recorder"),
        asyncio.create_task(controller.run(), name="controller"),
        asyncio.create_task(forever(controller.miners.run()), name="miners"),
        asyncio.create_task(forever(mqtt.run()), name="mqtt"),
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
    for closer in (webui.stop, forecast.close, ha_client.close):
        with contextlib.suppress(Exception):
            await closer()
    database.close()
    logger.info("GoodWe stopped")
    return exit_code


if __name__ == "__main__":
    exit_code = 0
    with contextlib.suppress(KeyboardInterrupt):
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
