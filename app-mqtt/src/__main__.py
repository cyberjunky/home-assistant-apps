"""MQTT - the dashboard of the broker. Mosquitto itself runs as its own service (see rootfs)."""

import asyncio
import contextlib
import logging
import signal
import sys
import time

from .clients import Clients
from .config import APP_VERSION, BROKER_HOST, BROKER_LOG, BROKER_PORT, DATA_DIR, WEB_PORT, Config, system_user
from .ha import HomeAssistant
from .history import History
from .monitor import Monitor
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

TICK = 5


async def tick(monitor: Monitor) -> None:
    while True:
        await asyncio.sleep(TICK)
        await asyncio.to_thread(monitor.tick, TICK)


async def record(monitor: Monitor, clients: Clients, history: History, webui: WebUI) -> None:
    """Every minute a sample for the history; every 6 hours the old ones go."""
    pruned = 0.0
    while True:
        await asyncio.sleep(60 - time.time() % 60 + 1)
        now = time.time()
        await asyncio.to_thread(
            history.record, now - 30, monitor.sys_values(), clients.take_counts(), monitor.take_groups()
        )
        # Also keeps the availability topics' values when the dashboard doesn't keep payloads
        webui.discovery_state()
        if now - pruned > 6 * 3600:
            pruned = now
            await asyncio.to_thread(history.prune, now)


async def main() -> int:
    logger.info(f"MQTT dashboard {APP_VERSION} starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        username, password = system_user("dashboard")
    except (OSError, KeyError, ValueError) as e:
        logger.error(f"The dashboard's login to the broker is missing ({e}): restart the app")
        return 1

    clients = Clients()
    monitor = Monitor(BROKER_HOST, BROKER_PORT, username, password, config.capture_payloads)
    history = History(DATA_DIR / "history.sqlite3", config.keep_days)
    ha = HomeAssistant()
    webui = WebUI(WEB_PORT, config, monitor, clients, history, ha)
    monitor.start()
    services = [
        asyncio.create_task(webui.start(), name="webui"),
        asyncio.create_task(clients.follow(BROKER_LOG), name="log"),
        asyncio.create_task(tick(monitor), name="tick"),
        asyncio.create_task(record(monitor, clients, history, webui), name="history"),
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
    monitor.stop()
    await ha.close()
    history.close()
    logger.info("MQTT dashboard stopped")
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
