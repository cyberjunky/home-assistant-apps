"""KNX-Ray - main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .hub import Hub
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def run(config: Config) -> int:
    """Run the app until stopped."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    hub = Hub(config, DATA_DIR)
    webui = WebUI(WEB_PORT, hub)

    async def forever(service) -> None:
        """A service that ends on its own (the web server only starts listening) must not end the app."""
        await service
        await asyncio.Event().wait()

    tasks = [
        asyncio.create_task(forever(webui.start()), name="webui"),
        asyncio.create_task(forever(hub.run()), name="hub"),
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
    # Last, so the recording notes when the app stopped and the bus connection is given back
    await hub.close()
    logger.info("KNX-Ray stopped")
    return exit_code


async def _amain() -> int:
    logger.info("KNX-Ray %s starting...", APP_VERSION)
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error("%s - fix the app configuration and restart", e)
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())
    # xknx reports every reconnect attempt and undecodable frame; the app shows those itself
    logging.getLogger("xknx").setLevel(logging.DEBUG if config.log_level == "debug" else logging.ERROR)
    # A device that refuses a connection or does not answer is a result the app reports, not an error
    logging.getLogger("xknx.management").setLevel(logging.DEBUG if config.log_level == "debug" else logging.CRITICAL)
    return await run(config)


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
