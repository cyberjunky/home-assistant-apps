"""Documents kept with the devices: the manual, the data sheet, a photo of how it is wired, a link to the
maker's page. A PDF or a picture is stored in the app's data; a link is only its address.

A document belongs to one device, or to every device of the same product (the manual of an actuator is
the same for all eight of them).
"""

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_PDF = 60 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024
# Pictures by how their file starts: (file ending, type the browser is told). No SVG: that is a program
IMAGES = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF8", "gif", "image/gif"),
)
IMAGE_LINK = re.compile(r"\.(jpe?g|png|gif|webp)$", re.IGNORECASE)


def image_type(data: bytes) -> tuple[str, str] | None:
    """(file ending, content type) of a picture, or None when it is not one the app takes."""
    for start, ending, content_type in IMAGES:
        if data.startswith(start):
            return ending, content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


MAX_DOCUMENTS = 5000


class DocumentError(Exception):
    """Something asked of the documents could not be done; the text is for the person."""


def product_key(device: dict[str, Any] | None) -> str:
    """What makes two devices the same product: the maker and the order number (or the product's name)."""
    if not device:
        return ""
    name = str(device.get("order_number") or device.get("product") or "").strip().lower()
    return f"{str(device.get('maker') or '').strip().lower()}|{name}" if name else ""


def _title(value: Any, fallback: str) -> str:
    title = re.sub(r"[\x00-\x1f]", " ", str(value or "")).strip()[:200]
    return title or fallback


class Documents:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.index = self.folder / "index.json"
        self.items: list[dict[str, Any]] = []
        try:
            loaded = json.loads(self.index.read_text())
            self.items = [i for i in loaded if isinstance(i, dict) and i.get("id")] if isinstance(loaded, list) else []
        except (OSError, ValueError):
            self.items = []

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_name("index.json.tmp")
        tmp.write_text(json.dumps(self.items))
        os.replace(tmp, self.index)

    def _new(self, kind: str, title: str, address: str, product: str) -> dict[str, Any]:
        if len(self.items) >= MAX_DOCUMENTS:
            raise DocumentError("there are too many documents")
        if not re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{1,3}", address):
            raise DocumentError("a document belongs to a device")
        return {
            "id": secrets.token_hex(8),
            "kind": kind,
            "title": title,
            "address": address,
            # Set when the document is for every device of this product
            "product": product,
            "added": time.time(),
        }

    def add_pdf(self, address: str, title: str, data: bytes, product: str = "") -> dict[str, Any]:
        if not data.startswith(b"%PDF-"):
            raise DocumentError("that is not a PDF")
        if len(data) > MAX_PDF:
            raise DocumentError("a PDF can be 60 MB at most")
        item = self._new("pdf", _title(title, "Document"), address, product)
        item["size"] = len(data)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{item['id']}.pdf").write_bytes(data)
        self.items.append(item)
        self._save()
        return item

    def add_file(self, address: str, title: str, data: bytes, product: str = "") -> dict[str, Any]:
        """A PDF or a picture, told apart by how the file starts."""
        if data.startswith(b"%PDF-"):
            return self.add_pdf(address, title, data, product)
        kind = image_type(data)
        if kind is None:
            raise DocumentError("a PDF or a picture (JPEG, PNG, GIF or WebP)")
        if len(data) > MAX_IMAGE:
            raise DocumentError("a picture can be 25 MB at most")
        item = self._new("image", _title(title, "Picture"), address, product)
        item.update(size=len(data), ending=kind[0])
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{item['id']}.{kind[0]}").write_bytes(data)
        self.items.append(item)
        self._save()
        return item

    def add_link(self, address: str, title: str, url: str, product: str = "") -> dict[str, Any]:
        url = str(url or "").strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > 2000 or re.search(r"[\s<>\"]", url):
            raise DocumentError("a link starts with http:// or https://")
        # A link to a picture is shown as one
        kind = "image" if IMAGE_LINK.search(parts.path) else "link"
        item = self._new(kind, _title(title, parts.netloc), address, product)
        item["url"] = url
        self.items.append(item)
        self._save()
        return item

    def _find(self, document_id: str) -> dict[str, Any]:
        for item in self.items:
            if item["id"] == document_id:
                return item
        raise DocumentError("that document is not there any more")

    def rename(self, document_id: str, title: str) -> None:
        item = self._find(document_id)
        item["title"] = _title(title, item["title"])
        self._save()

    def delete(self, document_id: str) -> None:
        item = self._find(document_id)
        self.items.remove(item)
        self._save()
        if not item.get("url"):
            (self.folder / f"{item['id']}.{item.get('ending', 'pdf')}").unlink(missing_ok=True)

    def file(self, document_id: str) -> tuple[Path, str, str]:
        """The stored PDF or picture, a name to save it under, and its type."""
        item = self._find(document_id)
        ending = item.get("ending", "pdf")
        path = self.folder / f"{item['id']}.{ending}"
        if item.get("url") or not path.is_file():
            raise DocumentError("that document has no file")
        name = re.sub(r"[^A-Za-z0-9_. ()-]", "_", item["title"]).strip() or "document"
        types = {"pdf": "application/pdf", **{e: t for _s, e, t in IMAGES}, "webp": "image/webp"}
        return path, name if name.lower().endswith(f".{ending}") else f"{name}.{ending}", types[ending]

    def of(self, address: str, product: str = "") -> list[dict[str, Any]]:
        """The documents of one device: its own, and those of its product."""
        found = [i for i in self.items if i["address"] == address or (product and i.get("product") == product)]
        return sorted(found, key=lambda i: i["title"].lower())

    def counts(self, devices: dict[str, str]) -> dict[str, int]:
        """How many documents each device has; ``devices`` maps an address to its product key."""
        by_product: dict[str, int] = {}
        own: dict[str, int] = {}
        for item in self.items:
            if item.get("product"):
                by_product[item["product"]] = by_product.get(item["product"], 0) + 1
            else:
                own[item["address"]] = own.get(item["address"], 0) + 1
        out = dict(own)
        for address, product in devices.items():
            if product in by_product:
                out[address] = out.get(address, 0) + by_product[product]
        return out
