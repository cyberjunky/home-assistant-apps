"""Configuration for Arcade, read from the app options."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# /data in the app container; ARCADE_DATA_DIR for development (see DOCS.md)
DATA_DIR = Path(os.environ.get("ARCADE_DATA_DIR", "/data"))
OPTIONS_FILE = DATA_DIR / "options.json"
# The MAME build (mame.js, mame.wasm, games.json, plugins/) - copied into the image
MAME_DIR = Path(os.environ.get("ARCADE_MAME_DIR", "/app/mame"))
DEFAULT_ROM_FOLDER = "/share/arcade/roms"


@dataclass
class Config:
    rom_folder: Path = Path(DEFAULT_ROM_FOLDER)
    log_level: str = "info"

    @classmethod
    def from_options_file(cls) -> "Config":
        options: dict = {}
        if OPTIONS_FILE.exists():
            try:
                options = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise ValueError(f"Cannot read {OPTIONS_FILE}: {e}") from e
        folder = str(options.get("rom_folder") or os.environ.get("ARCADE_ROM_FOLDER") or DEFAULT_ROM_FOLDER).strip()
        if not folder.startswith(("/share/", "/media/")) and not os.environ.get("ARCADE_ROM_FOLDER"):
            raise ValueError(f"rom_folder must be under /share or /media, not {folder!r}")
        return cls(rom_folder=Path(folder), log_level=str(options.get("log_level") or "info"))
