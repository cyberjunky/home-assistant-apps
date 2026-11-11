"""Thumbnails: a picture of each game, taken by the player while you play it.

The player takes a MAME snapshot of the game screen some time after you pressed Start and
sends it here; the library shows it on the game's card. One image per game, in /data/thumbs.
"""

import re
from pathlib import Path

SAFE_NAME = re.compile(r"^[a-z0-9_]{1,32}$")
MAX_THUMB = 512 * 1024
TYPES = {".webp": "image/webp", ".png": "image/png"}


def image_type(data: bytes) -> str | None:
    """The extension (.webp or .png) from the first bytes, None for anything else."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None


class Thumbs:
    def __init__(self, root: Path):
        self.root = root

    def path(self, game: str) -> Path | None:
        if not SAFE_NAME.match(game):
            return None
        for ext in TYPES:
            p = self.root / f"{game}{ext}"
            if p.is_file():
                return p
        return None

    def version(self, game: str) -> int | None:
        """A number that changes with the image (for the URL), None without one."""
        p = self.path(game)
        return p.stat().st_mtime_ns // 1_000_000 if p else None

    def write(self, game: str, data: bytes) -> None:
        if not SAFE_NAME.match(game):
            raise ValueError("invalid game")
        if len(data) > MAX_THUMB:
            raise ValueError("image too large")
        ext = image_type(data)
        if not ext:
            raise ValueError("not a WebP or PNG image")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{game}{ext}"
        tmp = target.with_suffix(ext + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        for other in TYPES:
            if other != ext:
                (self.root / f"{game}{other}").unlink(missing_ok=True)

    def all(self) -> dict[str, int]:
        """game -> version of every thumbnail."""
        out: dict[str, int] = {}
        if self.root.is_dir():
            for p in self.root.iterdir():
                if p.suffix in TYPES and SAFE_NAME.match(p.stem) and p.is_file():
                    out[p.stem] = p.stat().st_mtime_ns // 1_000_000
        return out
