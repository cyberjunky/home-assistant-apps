"""Network - Main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .ha_client import HAClient, HAWebSocket
from .history import History
from .inventory import Inventory
from .store import Store
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def keep_tidy(history: History) -> None:
    while True:
        await asyncio.to_thread(history.prune)
        await asyncio.sleep(6 * 3600)


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"Network {APP_VERSION} starting...")
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
    history = History(DATA_DIR / "history.sqlite3", config.keep_days)
    store = Store(DATA_DIR / "network.json")
    inventory: Inventory
    first_load = asyncio.Event()

    async def connected() -> None:
        await inventory.connected()
        first_load.set()

    ws = HAWebSocket(client, connected)
    inventory = Inventory(ws, config.include_disabled)
    inventory.on_change = history.record

    async def backfill() -> None:
        await first_load.wait()
        await history.backfill(client, sorted(inventory.states), config.history_days)
        # Done: no reason to stop the app
        await asyncio.Event().wait()

    webui = WebUI(WEB_PORT, config, ws, inventory, history, store)
    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(ws.run(), name="websocket"),
        asyncio.create_task(backfill(), name="backfill"),
        asyncio.create_task(keep_tidy(history), name="tidy"),
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
    await client.close()
    history.close()
    logger.info("Network stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
