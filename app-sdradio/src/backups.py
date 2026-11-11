"""Database backups in /data/backups: made nightly and on request, restored from the page."""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from .database import Database, ValidationError, check_backup

logger = logging.getLogger(__name__)

NAME = re.compile(r"^sdradio-\d{8}-\d{6}(-[a-z-]+)?\.db$")


class Backups:
    def __init__(self, database: Database, folder: Path):
        self.db = database
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        files = [p for p in self.folder.iterdir() if NAME.match(p.name)]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "size": p.stat().st_size, "ts": p.stat().st_mtime} for p in files]

    def path(self, name: str) -> Path:
        if not NAME.match(name):
            raise KeyError(name)
        path = self.folder / name
        if not path.is_file():
            raise KeyError(name)
        return path

    def create(self, label: str = "") -> dict:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"sdradio-{stamp}{'-' + label if label else ''}.db"
        target = self.folder / name
        self.db.backup_to(target)
        logger.info(f"Backup made: {name}")
        return {"name": name, "size": target.stat().st_size, "ts": target.stat().st_mtime}

    def prune(self, keep: int) -> None:
        """Keeps the newest `keep` backups (uploaded and before-restore ones count too)."""
        for item in self.list()[keep:]:
            (self.folder / item["name"]).unlink(missing_ok=True)

    def restore(self, name: str) -> None:
        source = self.path(name)
        check_backup(source)
        self.create("before-restore")
        self.db.restore_from(source)
        logger.info(f"Restored the database from {name}")

    def add_upload(self, data: bytes) -> dict:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.folder / f"sdradio-{stamp}-uploaded.db"
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        try:
            check_backup(tmp)
        except ValidationError:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(target)
        return {"name": target.name, "size": target.stat().st_size, "ts": target.stat().st_mtime}

    def last(self) -> dict | None:
        items = self.list()
        return items[0] if items else None


async def nightly(backups: Backups, database: Database) -> None:
    """Makes the nightly backup and removes old hits, at the time set on the Settings page."""
    while True:
        settings = database.settings()
        hour, minute = (int(p) for p in settings["backup_time"].split(":"))
        now = datetime.now()
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        # Wake up at least hourly, so a changed backup time takes effect
        await asyncio.sleep(min((due - now).total_seconds(), 3600))
        if datetime.now() < due:
            continue
        settings = database.settings()
        try:
            await asyncio.to_thread(backups.create)
            await asyncio.to_thread(backups.prune, int(settings["backups_keep"]))
            removed = await asyncio.to_thread(database.prune_hits, int(settings["hits_keep_days"]))
            if removed:
                logger.info(f"Removed {removed} hits older than {settings['hits_keep_days']} days")
        except Exception:
            logger.exception("The nightly backup failed")
        await asyncio.sleep(61)
