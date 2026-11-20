"""Flipper - main entry point."""

import asyncio
import contextlib
import logging
import signal
import sys

from .config import APP_VERSION, DATA_DIR, WEB_PORT, Config
from .link import Flipper
from .webui import WebUI
from .workspace import Workspace

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


async def run(config: Config) -> int:
    """Run the app until stopped."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    flipper = Flipper(config.device)
    workspace = Workspace(flipper)
    webui = WebUI(WEB_PORT, flipper, workspace)

    async def forever(service) -> None:
        """A service that ends on its own (the web server only starts listening) must not end the app."""
        await service
        await asyncio.Event().wait()

    tasks = [
        asyncio.create_task(forever(webui.start()), name="webui"),
        asyncio.create_task(forever(flipper.run()), name="flipper"),
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
    # End the session first, so the Flipper is back at its command line and free to use
    workspace.cancel()
    await flipper.close()
    for task in [*tasks, stopper]:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Flipper app stopped")
    return exit_code


async def _amain() -> int:
    logger.info("Flipper app %s starting...", APP_VERSION)
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error("%s - fix the app configuration and restart", e)
        return 1
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())
    return await run(config)


def main() -> int:
    try:
        return asyncio.run(_amain())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
