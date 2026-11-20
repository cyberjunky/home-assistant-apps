"""The archive: every saved document as a PDF, with its name, tags and text, in a searchable index.

A document is made from the tray's pages. With text recognition (Tesseract) the PDF gets an
invisible text layer, so it can be searched and copied from, here and in any PDF reader.
"""

import contextlib
import io
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import img2pdf
from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation

from .pages import THUMB_SIZE, save_thumb

logger = logging.getLogger(__name__)

TESSERACT = "tesseract"
OCR_TIMEOUT = 300
PAGE_SIZE = 60
SORTS = {
    "newest": "created DESC",
    "oldest": "created ASC",
    "name": "name COLLATE NOCASE ASC",
    "largest": "size DESC",
}


def clean_tags(tags: list[str] | str | None) -> list[str]:
    """Tags as a list: lower case, no doubles, no commas."""
    if isinstance(tags, str):
        tags = tags.split(",")
    out: list[str] = []
    for tag in tags or []:
        tag = re.sub(r"\s+", " ", str(tag)).strip().lower()[:40]
        if tag and tag not in out:
            out.append(tag)
    return out[:20]


def file_name(name: str) -> str:
    """A document's name as a file name that every system takes."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or "Scan"


def fts_query(text: str) -> str:
    """Words to an FTS5 query: every word must be there, the last one may be its beginning."""
    words = re.findall(r"\w+", text.lower())
    return " ".join(f'"{w}"*' for w in words)


def ocr_page(image: Path, base: Path, languages: list[str], dpi: int) -> tuple[bytes, str] | None:
    """A page's text as a PDF with only that text, invisible and in place, and as plain text.

    None when Tesseract can't do it. The picture is not Tesseract's to store: it would compress
    it again. The text goes over the page that image_pdf makes (see build_pdf).
    """
    command = [TESSERACT, str(image), str(base), "-l", "+".join(languages) or "eng", "--dpi", str(dpi)]
    command += ["-c", "textonly_pdf=1", "pdf", "txt"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=OCR_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Text recognition failed: {e}")
        return None
    pdf, txt = base.with_suffix(".pdf"), base.with_suffix(".txt")
    try:
        if result.returncode != 0 or not pdf.is_file():
            logger.warning(f"Text recognition failed: {result.stderr.decode(errors='replace').strip()[-300:]}")
            return None
        text = txt.read_text(errors="replace") if txt.is_file() else ""
        return pdf.read_bytes(), text
    finally:
        for path in (pdf, txt):
            with contextlib.suppress(OSError):
                path.unlink()


def image_pdf(image: Path) -> bytes:
    """A page as a PDF of its own size, the image as it is (not compressed again)."""
    return img2pdf.convert(str(image))


def build_pdf(
    pages: list[tuple[Path, int]],
    target: Path,
    title: str,
    languages: list[str] | None,
    on_page: Callable[[int, int], None] | None = None,
) -> tuple[str, bool]:
    """Make one PDF of the pages (path, dpi). Returns the text found and whether every page was read."""
    writer = PdfWriter()
    texts: list[str] = []
    complete = bool(languages)
    with tempfile.TemporaryDirectory(prefix="scanner-pdf-") as tmp:
        for number, (path, dpi) in enumerate(pages, 1):
            if on_page:
                on_page(number, len(pages))
            # In the writer first: pypdf only changes pages that are in one
            page = writer.add_page(PdfReader(io.BytesIO(image_pdf(path))).pages[0])
            done = ocr_page(path, Path(tmp) / f"page-{number}", languages, dpi) if languages else None
            if done is None:
                complete = False
            else:
                try:
                    layer = PdfReader(io.BytesIO(done[0])).pages[0]
                    # Both are the picture's size on paper; a rounding difference is scaled away
                    sx = float(page.mediabox.width) / float(layer.mediabox.width)
                    sy = float(page.mediabox.height) / float(layer.mediabox.height)
                    page.merge_transformed_page(layer, Transformation().scale(sx, sy))
                    texts.append(done[1].strip())
                except Exception as e:
                    logger.warning(f"The text of page {number} could not be added: {e}")
                    complete = False
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    writer.add_metadata(
        {
            "/Title": title,
            "/Creator": "Scanner app for Home Assistant",
            "/CreationDate": now.strftime("D:%Y%m%d%H%M%S") + f"{offset[:3]}'{offset[3:]}'",
        }
    )
    tmp_target = target.with_name(target.name + ".tmp")
    with open(tmp_target, "wb") as f:
        writer.write(f)
    os.replace(tmp_target, target)
    return "\n\n".join(t for t in texts if t), complete


class Archive:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.folder / "archive.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock, self.db:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created REAL NOT NULL,
                    pages INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    ocr INTEGER NOT NULL DEFAULT 0,
                    languages TEXT NOT NULL DEFAULT '',
                    device TEXT NOT NULL DEFAULT '',
                    copied_at REAL,
                    copied_to TEXT NOT NULL DEFAULT '',
                    copy_error TEXT NOT NULL DEFAULT ''
                )"""
            )
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(name, tags, text)")

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def pdf(self, doc_id: int) -> Path:
        return self.folder / f"{doc_id}.pdf"

    def thumb(self, doc_id: int) -> Path:
        return self.folder / f"{doc_id}.jpg"

    @staticmethod
    def _row(row: sqlite3.Row, with_text: bool = False) -> dict:
        doc = dict(row)
        doc["tags"] = [t for t in doc["tags"].split(",") if t]
        doc["ocr"] = bool(doc["ocr"])
        doc["has_text"] = bool(doc["text"].strip())
        if not with_text:
            del doc["text"]
        doc["file_name"] = file_name(doc["name"]) + ".pdf"
        return doc

    def _index(self, doc_id: int, name: str, tags: list[str], text: str) -> None:
        self.db.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
        self.db.execute(
            "INSERT INTO documents_fts (rowid, name, tags, text) VALUES (?, ?, ?, ?)",
            (doc_id, name, " ".join(tags), text),
        )

    def add(
        self,
        pages: list[dict],
        paths: list[Path],
        name: str,
        tags: list[str],
        languages: list[str] | None,
        device: str = "",
        on_page: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Make a document of the pages. Slow (text recognition): run it in a thread."""
        if not pages:
            raise ValueError("There are no pages to save")
        name = re.sub(r"\s+", " ", name).strip()[:200] or "Scan"
        tags = clean_tags(tags)
        with self._lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO documents (name, created, pages, size, tags, device) VALUES (?, ?, ?, 0, ?, ?)",
                (name, time.time(), len(pages), ",".join(tags), device),
            )
            doc_id = int(cursor.lastrowid or 0)
        try:
            text, complete = build_pdf(
                [(path, int(page.get("dpi") or 300)) for page, path in zip(pages, paths, strict=True)],
                self.pdf(doc_id),
                name,
                languages,
                on_page,
            )
            with Image.open(paths[0]) as first:
                save_thumb(first, self.thumb(doc_id), THUMB_SIZE)
        except Exception:
            with self._lock, self.db:
                self.db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            for path in (self.pdf(doc_id), self.thumb(doc_id)):
                with contextlib.suppress(OSError):
                    path.unlink()
            raise
        with self._lock, self.db:
            self.db.execute(
                "UPDATE documents SET size = ?, text = ?, ocr = ?, languages = ? WHERE id = ?",
                (self.pdf(doc_id).stat().st_size, text, int(complete), "+".join(languages or []), doc_id),
            )
            self._index(doc_id, name, tags, text)
        doc = self.get(doc_id)
        assert doc is not None
        return doc

    def get(self, doc_id: int, with_text: bool = False) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return self._row(row, with_text) if row else None

    def search(
        self, query: str = "", tag: str = "", sort: str = "newest", offset: int = 0, limit: int = PAGE_SIZE
    ) -> dict:
        """Documents by words in the name, tags or text, and by tag."""
        where, args = [], []
        snippets: dict[int, str] = {}
        match = fts_query(query)
        with self._lock:
            if match:
                try:
                    rows = self.db.execute(
                        "SELECT rowid, snippet(documents_fts, 2, '\x02', '\x03', ' … ', 14) AS snip "
                        "FROM documents_fts WHERE documents_fts MATCH ? LIMIT 2000",
                        (match,),
                    ).fetchall()
                except sqlite3.Error:
                    rows = []
                snippets = {r["rowid"]: r["snip"] for r in rows}
                if not snippets:
                    return {"documents": [], "total": 0}
                where.append(f"id IN ({','.join(str(int(i)) for i in snippets)})")
            if tag:
                where.append("(',' || tags || ',') LIKE ?")
                args.append(f"%,{tag.lower()},%")
            clause = f"WHERE {' AND '.join(where)}" if where else ""
            total = self.db.execute(f"SELECT COUNT(*) FROM documents {clause}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT * FROM documents {clause} ORDER BY {SORTS.get(sort, SORTS['newest'])} LIMIT ? OFFSET ?",
                (*args, max(1, min(500, limit)), max(0, offset)),
            ).fetchall()
        documents = []
        for row in rows:
            doc = self._row(row)
            snip = snippets.get(doc["id"], "")
            # Only a hit in the text needs showing: the name and tags are on the card already
            doc["snippet"] = snip if "\x02" in snip else ""
            documents.append(doc)
        return {"documents": documents, "total": total}

    def tags(self) -> list[dict]:
        counts: dict[str, int] = {}
        with self._lock:
            rows = self.db.execute("SELECT tags FROM documents WHERE tags != ''").fetchall()
        for row in rows:
            for tag in row["tags"].split(","):
                if tag:
                    counts[tag] = counts.get(tag, 0) + 1
        return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]

    def update(self, doc_id: int, name: str | None = None, tags: list[str] | str | None = None) -> dict | None:
        doc = self.get(doc_id, with_text=True)
        if doc is None:
            return None
        new_name = doc["name"] if name is None else (re.sub(r"\s+", " ", name).strip()[:200] or doc["name"])
        new_tags = doc["tags"] if tags is None else clean_tags(tags)
        with self._lock, self.db:
            self.db.execute(
                "UPDATE documents SET name = ?, tags = ? WHERE id = ?", (new_name, ",".join(new_tags), doc_id)
            )
            self._index(doc_id, new_name, new_tags, doc["text"])
        return self.get(doc_id)

    def set_copied(self, doc_id: int, target: str, error: str = "") -> None:
        with self._lock, self.db:
            if error:
                self.db.execute("UPDATE documents SET copy_error = ? WHERE id = ?", (error, doc_id))
            else:
                self.db.execute(
                    "UPDATE documents SET copied_at = ?, copied_to = ?, copy_error = '' WHERE id = ?",
                    (time.time(), target, doc_id),
                )

    def delete(self, doc_id: int) -> bool:
        with self._lock, self.db:
            found = self.db.execute("DELETE FROM documents WHERE id = ?", (doc_id,)).rowcount > 0
            self.db.execute("DELETE FROM documents_fts WHERE rowid = ?", (doc_id,))
        for path in (self.pdf(doc_id), self.thumb(doc_id)):
            with contextlib.suppress(OSError):
                path.unlink()
        return found

    def summary(self) -> dict:
        with self._lock:
            row = self.db.execute(
                "SELECT COUNT(*) AS documents, COALESCE(SUM(pages), 0) AS pages, COALESCE(SUM(size), 0) AS size, "
                "MAX(created) AS last FROM documents"
            ).fetchone()
        out = dict(row)
        try:
            out["free"] = shutil.disk_usage(self.folder).free
        except OSError:
            out["free"] = None
        return out
