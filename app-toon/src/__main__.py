"""Toon - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .database import Database
from .external import ExternalSensors
from .ha_client import HAClient
from .monitor import ToonMonitor
from .mqtt import MqttPublisher
from .toon_link import ToonLink
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
# asyncssh reports every channel at info level; that would be lines per poll
logging.getLogger("asyncssh").setLevel(logging.WARNING)


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"Toon {APP_VERSION} starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = Database()
    monitor = ToonMonitor(config, database, ToonLink(config))
    client = HAClient()
    mqtt = MqttPublisher(config, client, monitor)
    external = ExternalSensors(config, database, client)
    webui = WebUI(WEB_PORT, database, monitor, mqtt, external)

    async def forever(service) -> None:
        """A service that ends on its own (MQTT without a broker) must not end the app."""
        await service
        await asyncio.Event().wait()

    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(monitor.run(), name="monitor"),
        asyncio.create_task(monitor.upkeep(), name="upkeep"),
        asyncio.create_task(forever(mqtt.run()), name="mqtt"),
        asyncio.create_task(forever(external.run()), name="external"),
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
    database.close()
    logger.info("Toon stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
