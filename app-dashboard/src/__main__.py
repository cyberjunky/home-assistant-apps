"""Dashboard - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .ha_client import HAClient, HAWebSocket
from .hub import Hub
from .store import Store
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"Dashboard {APP_VERSION} starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = HAClient()
    if not client.available:
        logger.error("No Supervisor token: this app runs under Home Assistant (homeassistant_api)")
        return 1
    hub: Hub

    async def connected() -> None:
        await hub.connected()

    ws = HAWebSocket(client, connected)
    hub = Hub(ws)
    store = Store(DATA_DIR / "dashboard.json")
    webui = WebUI(WEB_PORT, config, client, ws, hub, store)
    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(ws.run(), name="websocket"),
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
            logger.error(f"{task.get_name()} failed: {task.exception()!r}")
            exit_code = 1
    for task in [*services, stopper]:
        task.cancel()
    for task in [*services, stopper]:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    await client.close()
    logger.info("Dashboard stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
