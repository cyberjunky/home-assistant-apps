"""Vigil - Main entry point: the receivers, the collectors, the writer, the rule engine and the
web UI, all in one asyncio process."""

import asyncio
import contextlib
import logging
import signal
import sys
import time

from .config import ADDON_VERSION, DATA_DIR, Config, discover_chronicle, published_ports
from .database import Database, Sources, Writer, describe
from .fritzbox import HOST as FRITZ_HOST
from .fritzbox import FritzBox
from .ingest import IngestServer, Intake
from .patterns import PatternMiner
from .rules import HomeAssistant, RuleEngine
from .scrape import Scraper
from .syslog import SyslogServer
from .webui import WebUI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

SELF_METRICS_INTERVAL = 30
HOUSEKEEPING_INTERVAL = 3600
# Time for the write queue to reach the database on SIGTERM (S6_SERVICES_GRACETIME is 15 s)
FLUSH_TIMEOUT = 10


async def maintenance(database: Database, writer: Writer) -> None:
    """Vigil's own metrics every 30 s, and the housekeeping every hour."""
    housekeeping_at = time.monotonic() - HOUSEKEEPING_INTERVAL + 300  # the first one 5 min after starting
    while True:
        await asyncio.sleep(SELF_METRICS_INTERVAL)
        writer.put_samples(writer.self_samples())
        if database.connected and time.monotonic() - housekeeping_at >= HOUSEKEEPING_INTERVAL:
            housekeeping_at = time.monotonic()
            try:
                await database.housekeeping()
            except Exception as e:
                logger.warning(f"Housekeeping failed: {describe(e)}")


async def _apply_port_mapping(config: Config) -> None:
    """The host ports devices send to, for the setup snippets (the user may have changed them)."""
    network = await published_ports()
    if network:
        config.public_syslog_port = network.get(f"{config.syslog_port}/udp") or network.get(f"{config.syslog_port}/tcp")
        config.public_ingest_port = network.get(f"{config.ingest_port}/tcp")


async def main() -> int:
    """Main application entry point. Returns the process exit code."""
    logger.info(f"Vigil {ADDON_VERSION} starting...")

    config = Config.load()
    if config.log_level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(config.log_level.upper())
    # aiohttp's access and client logs say nothing useful here and can carry tokens in URLs
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    # asyncpg repeats every failed reconnect of its pool; Vigil says it once, with the reason
    logging.getLogger("asyncpg").setLevel(logging.ERROR)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    await _apply_port_mapping(config)
    database = Database(config, resolve_host=None if config.db_host else discover_chronicle)
    public = config.public()
    logger.info(
        f"Using database {public['name']} as {public['user']} on {public['host'] or 'the Chronicle app'}:{public['port']}"
    )

    miner = PatternMiner()
    sources = Sources()
    writer = Writer(database, miner, sources)
    intake = Intake(writer, sources)
    home_assistant = HomeAssistant(config)
    engine = RuleEngine(database, home_assistant, log=writer.put_log)
    receivers: dict = {"syslog_udp": False, "syslog_tcp": False, "otlp": False}

    syslog = SyslogServer(config.syslog_port, intake.syslog) if config.syslog_port else None
    ingest = IngestServer(config.ingest_port, config.ingest_token, intake) if config.ingest_port else None
    scraper = Scraper(
        config.scrape_targets,
        writer.put_samples,
        lambda target, instance, count, error: sources.seen("scrape", target.name, instance, count, error),
    )
    fritzbox = FritzBox(
        config,
        DATA_DIR,
        writer.put_samples,
        writer.put_log,
        lambda count, error: sources.seen("fritzbox", FRITZ_HOST, config.fritzbox_host, count, error),
    )
    webui = WebUI(config.web_port, config, database, writer, intake, engine, home_assistant, receivers)

    tasks = [
        asyncio.create_task(database.run(), name="database"),
        asyncio.create_task(engine.run(), name="rules"),
        asyncio.create_task(maintenance(database, writer), name="maintenance"),
    ]
    writer_task = asyncio.create_task(writer.run(), name="writer")

    # The web server failing (e.g. port already in use) is fatal: exit non-zero so the
    # finish script stops the app instead of restarting it in a loop
    try:
        await webui.start()
    except OSError as e:
        logger.error(f"The web server cannot start: {e}")
        for task in tasks:
            task.cancel()
        await writer.stop(writer_task, 0)
        return 1

    if syslog is not None:
        await syslog.start()
        receivers["syslog_udp"] = config.syslog_port if syslog.udp else False
        receivers["syslog_tcp"] = config.syslog_port if syslog.tcp else False
    if ingest is not None:
        await ingest.start()
        receivers["otlp"] = config.ingest_port if ingest.listening else False
    scraper.start()
    fritzbox.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    exit_code = 0
    stopper = asyncio.create_task(stop.wait())
    watched = [stopper, writer_task, *tasks]
    done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            logger.error(f"{task.get_name()} failed", exc_info=task.exception())
            exit_code = 1

    logger.info("Shutting down...")
    stopper.cancel()
    # Receivers first, so nothing new arrives while the queue is written
    if syslog is not None:
        await syslog.stop()
    if ingest is not None:
        with contextlib.suppress(Exception):
            await ingest.stop()
    await scraper.stop()
    await fritzbox.stop()
    for task in tasks:
        if task.get_name() != "database":
            task.cancel()
    await writer.stop(writer_task, FLUSH_TIMEOUT)
    with contextlib.suppress(Exception):
        await webui.stop()
    for task in tasks:
        task.cancel()
    await database.close()
    logger.info(f"Vigil stopped ({writer.written_logs} log lines and {writer.written_points} points written)")
    return exit_code


if __name__ == "__main__":
    exit_code = 0
    with contextlib.suppress(KeyboardInterrupt):
        exit_code = asyncio.run(main())
    sys.exit(exit_code)
