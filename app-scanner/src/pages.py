"""The pages scanned so far, before they become a document: add, turn, reorder, remove.

They are kept on disk (not in memory), so a restart halfway a pile of paper loses nothing.
"""

import contextlib
import json
import logging
import os
import time
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# A page at 1200 dpi is far over Pillow's limit against decompression bombs; these come from the scanner
Image.MAX_IMAGE_PIXELS = None
THUMB_SIZE = 420
PREVIEW_SIZE = 1400


def load_scan(path: Path) -> Image.Image:
    """A scanned page as black-and-white (1), grey (L) or colour (RGB)."""
    image = Image.open(path)
    image.load()
    if image.mode in ("1", "L", "RGB"):
        return image
    if image.mode.startswith("I"):
        # 16 bits per sample: the page keeps its 8 highest
        return image.point(lambda v: v / 256).convert("L")
    return image.convert("RGB")


def save_page(image: Image.Image, base: Path, dpi: int, quality: int) -> Path:
    """Black-and-white as PNG (exact and small), grey and colour as JPEG."""
    if image.mode == "1":
        path = base.with_suffix(".png")
        image.save(path, optimize=True, dpi=(dpi, dpi))
    else:
        path = base.with_suffix(".jpg")
        image.save(path, quality=quality, dpi=(dpi, dpi))
    return path


def save_thumb(image: Image.Image, path: Path, size: int = THUMB_SIZE) -> None:
    thumb = image.convert("L" if image.mode in ("1", "L") else "RGB")
    thumb.thumbnail((size, size))
    thumb.save(path, quality=80)


class Tray:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.index = self.folder / "pages.json"
        self.pages: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            pages = json.loads(self.index.read_text())
        except (OSError, ValueError):
            pages = []
        self.pages = [p for p in pages if isinstance(p, dict) and (self.folder / str(p.get("file"))).is_file()]

    def _save(self) -> None:
        tmp = self.index.with_name(self.index.name + ".tmp")
        tmp.write_text(json.dumps(self.pages))
        os.replace(tmp, self.index)

    def _new_id(self) -> str:
        used = {p["id"] for p in self.pages}
        n = int(time.time() * 1000)
        while str(n) in used:
            n += 1
        return str(n)

    def add(self, source: Path, dpi: int, quality: int, remove: bool = True) -> dict:
        """Take a scanned file (PNM) into the tray. Slow for a large page: run it in a thread."""
        image = load_scan(source)
        page_id = self._new_id()
        path = save_page(image, self.folder / page_id, dpi, quality)
        save_thumb(image, self.folder / f"{page_id}.thumb.jpg")
        page = {
            "id": page_id,
            "file": path.name,
            "width": image.width,
            "height": image.height,
            "dpi": dpi,
            "mode": {"1": "Black and white", "L": "Grey"}.get(image.mode, "Colour"),
            "size": path.stat().st_size,
            "added": time.time(),
            "turned": 0,
        }
        self.pages.append(page)
        self._save()
        if remove:
            with contextlib.suppress(OSError):
                source.unlink()
        return page

    def get(self, page_id: str) -> dict | None:
        return next((p for p in self.pages if p["id"] == page_id), None)

    def path(self, page: dict) -> Path:
        return self.folder / page["file"]

    def thumb(self, page: dict) -> Path:
        return self.folder / f"{page['id']}.thumb.jpg"

    def rotate(self, page_id: str, degrees: int, quality: int) -> dict | None:
        """Turn a page a quarter or half turn, clockwise."""
        page = self.get(page_id)
        if page is None or degrees % 360 not in (90, 180, 270):
            return page
        path = self.path(page)
        with Image.open(path) as opened:
            opened.load()
            turned = opened.transpose(
                {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}[
                    degrees % 360
                ]
            )
        if path.suffix == ".png":
            turned.save(path, optimize=True, dpi=(page["dpi"], page["dpi"]))
        else:
            turned.save(path, quality=quality, dpi=(page["dpi"], page["dpi"]))
        save_thumb(turned, self.thumb(page))
        page.update(width=turned.width, height=turned.height, size=path.stat().st_size)
        page["turned"] = int(page.get("turned", 0)) + 1
        self._save()
        return page

    def delete(self, page_id: str) -> bool:
        page = self.get(page_id)
        if page is None:
            return False
        self.pages.remove(page)
        self._save()
        self._unlink(page)
        return True

    def _unlink(self, page: dict) -> None:
        for path in (self.path(page), self.thumb(page)):
            with contextlib.suppress(OSError):
                path.unlink()

    def order(self, ids: list[str]) -> None:
        """Put the pages in this order; pages not named keep their place after them."""
        by_id = {p["id"]: p for p in self.pages}
        first = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
        self.pages = first + [p for p in self.pages if p not in first]
        self._save()

    def reverse(self) -> None:
        self.pages.reverse()
        self._save()

    def interleave(self) -> bool:
        """Two-sided paper on a one-sided feeder: first all fronts, then the pile turned over.

        The second half are the backs, last sheet first: 1 3 5 6 4 2 becomes 1 2 3 4 5 6.
        """
        count = len(self.pages)
        if count < 2 or count % 2:
            return False
        fronts, backs = self.pages[: count // 2], self.pages[count // 2 :][::-1]
        self.pages = [page for pair in zip(fronts, backs, strict=True) for page in pair]
        self._save()
        return True

    def clear(self, ids: list[str] | None = None) -> None:
        gone = [p for p in self.pages if ids is None or p["id"] in ids]
        self.pages = [p for p in self.pages if p not in gone]
        self._save()
        for page in gone:
            self._unlink(page)

    def summary(self) -> dict:
        return {"pages": len(self.pages), "size": sum(p.get("size", 0) for p in self.pages)}
