"""FireServiceRota - main entry point."""

import asyncio
import contextlib
import importlib
import logging
import os
import signal
import sys

from .config import DATA_DIR, Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

WEB_PORT = int(os.environ.get("FSR_WEB_PORT", "8099"))
# Development without Home Assistant: FSR_MOCK=1 starts tools/mock_ha.py (a stand-in
# Supervisor + Home Assistant with the integration's three entities) in this process
MOCK = os.environ.get("FSR_MOCK", "") not in ("", "0")
MOCK_PORT = int(os.environ.get("FSR_MOCK_PORT", "8124"))


async def main() -> int:
    logger.info("FireServiceRota starting...")
    try:
        config = Config.from_options_file()
    except ValueError as e:
        logger.error(f"{e} - fix the app configuration and restart")
        return 1
    if config.log_level == "debug":
        logging.getLogger().setLevel(logging.DEBUG)
    for name in ("aiohttp.access",):
        logging.getLogger(name).setLevel(logging.WARNING)

    mock = None
    if MOCK:
        # Must be set before ha_client is imported: it reads the URL at import
        os.environ["FSR_SUPERVISOR_URL"] = f"http://127.0.0.1:{MOCK_PORT}"
        os.environ.setdefault("SUPERVISOR_TOKEN", "mock-token")
        mock_module = importlib.import_module("tools.mock_ha")
        mock = mock_module.MockHomeAssistant(MOCK_PORT)
        await mock.start()
        logger.warning(f"Mock mode: talking to the stand-in Home Assistant on port {MOCK_PORT}")

    from .database import Database
    from .ha_client import HAClient
    from .tracker import Tracker
    from .webui import WebUI

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = Database(str(DATA_DIR / "fireservicerota.sqlite3"))
    counts = database.counts()
    logger.info(
        f"Database: {counts['incidents']} incidents, {counts['responses']} responses, {counts['duty']} duty changes"
    )

    ha = HAClient()
    tracker = Tracker(config, database, ha)
    webui = WebUI(WEB_PORT, database, tracker)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    exit_code = 0
    try:
        await webui.start()
        await tracker.start()
        await stop.wait()
    except Exception:
        logger.exception("Service error")
        exit_code = 1
    finally:
        logger.info("Shutting down...")

        async def shutdown():
            await tracker.stop()
            await webui.stop()
            await ha.close()
            if mock:
                await mock.stop()
            database.close()

        try:
            await asyncio.wait_for(shutdown(), timeout=7.0)
        except TimeoutError:
            logger.warning("Shutdown took too long, stopping anyway")
    logger.info("FireServiceRota stopped")
    return exit_code


if __name__ == "__main__":
    exit_code = 0
    with contextlib.suppress(KeyboardInterrupt):
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
