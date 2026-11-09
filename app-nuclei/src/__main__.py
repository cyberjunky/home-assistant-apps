"""Nuclei Scanner - main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .database import Database
from .engine import Engine
from .presence import PresenceMonitor
from .scanner import Scanner
from .scheduler import Scheduler
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def _amain() -> int:
    logger.info("Nuclei Scanner %s starting...", APP_VERSION)
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error("%s - fix the app configuration and restart", e)
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())

    available = Scanner.available()
    if not all(available.values()):
        logger.warning("Scanner binaries missing: %s", {k: v for k, v in available.items() if not v})

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = Database(DATA_DIR / "nuclei.db")
    engine = Engine(database, config)
    scheduler = Scheduler(engine)
    webui = WebUI(WEB_PORT, database, engine)

    database.prune(config.retention_days)

    async def forever(service) -> None:
        """A service that ends on its own (the web server only starts listening) must not end the app."""
        await service
        await asyncio.Event().wait()

    tasks = [
        asyncio.create_task(forever(webui.start()), name="webui"),
        asyncio.create_task(forever(scheduler.run()), name="scheduler"),
        asyncio.create_task(
            forever(PresenceMonitor(database, config.presence_interval_minutes, config.max_concurrent_hosts).run()),
            name="presence",
        ),
    ]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    stopper = asyncio.create_task(stop.wait(), name="stop")

    # A service that fails (the web server can't bind its port, say) must end the app with an error, not
    # leave it running without a dashboard
    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
    exit_code = 0
    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            logger.error("Service %s failed: %r", task.get_name(), task.exception())
            exit_code = 2
    for task in [*tasks, stopper]:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks, return_exceptions=True)
    database.close()
    logger.info("Nuclei Scanner stopped")
    return exit_code


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
