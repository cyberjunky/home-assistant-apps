"""Lottery - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

import aiohttp

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .database import Database
from .firmware import Firmware
from .ha_client import HAClient
from .miner import Miner
from .mqtt import MqttPublisher
from .network import Network
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

PRUNE_SECONDS = 3600


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"Lottery {APP_VERSION} starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())
    if not config.miners:
        logger.warning("No miners configured: add them under miners in the app configuration, then restart")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = Database(DATA_DIR / "lottery.db")
    network = Network(config.network_data)
    miners = [Miner(miner, config.poll_seconds, database, network) for miner in config.miners]
    network.miners = miners
    firmware = Firmware(config.firmware_check)
    mqtt = MqttPublisher(config, HAClient(), miners, network, firmware)
    webui = WebUI(WEB_PORT, config, database, miners, network, mqtt)

    async def forever(service) -> None:
        """A service that ends on its own (MQTT without a broker, network data off) must not end the app."""
        await service
        await asyncio.Event().wait()

    async def prune() -> None:
        while True:
            await asyncio.to_thread(database.prune)
            await asyncio.sleep(PRUNE_SECONDS)

    # The miners answer on the local network, the rest is public; one session for all of it
    async with aiohttp.ClientSession(headers={"User-Agent": f"home-assistant-lottery/{APP_VERSION}"}) as session:
        services = [
            asyncio.create_task(webui.start(), name="webui"),
            asyncio.create_task(forever(mqtt.run(session)), name="mqtt"),
            asyncio.create_task(forever(network.run(session)), name="network"),
            asyncio.create_task(forever(firmware.run(session)), name="firmware"),
            asyncio.create_task(prune(), name="prune"),
            *(asyncio.create_task(miner.run(session), name=f"miner-{miner.slug}") for miner in miners),
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
    logger.info("Lottery stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
