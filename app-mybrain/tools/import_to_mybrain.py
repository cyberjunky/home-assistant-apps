#!/usr/bin/env python3
"""Put what you already have into MyBrain, once: Google Keep and Gmail from Google Takeout, browser
bookmarks, or a text file of links.

Every note, mail or bookmark is sent to Home Assistant as a share (the `mobile_app.share` event the
phone app sends), so MyBrain saves, reads and sorts it as if you had shared it. It becomes the item
of the user whose token you use. A link already in MyBrain is not saved twice.

Only Python 3 is needed, nothing to install:

    python3 import_to_mybrain.py takeout-20261128.zip --ha https://homeassistant.local:8123 --token <token>
    python3 import_to_mybrain.py takeout.zip --dry-run          # only count and show a few

The token: Home Assistant → your profile → Security → Long-lived access tokens (an administrator's,
since sending events needs one). Files it reads:

- Google Takeout's .zip as downloaded, with Keep (Takeout/Keep/*.json) and/or Mail (*.mbox) in it,
  or a loose .mbox or Keep .json
- a bookmarks .html exported from Chrome, Firefox or Edge
- a .txt or .md: each block of lines between empty lines is one note; a line with only a link is
  one link

The original date can't be passed on (MyBrain saves it as shared now), so it is put at the end of
the note, with the Keep labels or Gmail labels as #tags.
"""

from __future__ import annotations

import argparse
import email
import email.header
import email.policy
import json
import mailbox
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
MAX_TEXT = 6000
# The phone app a share says it came from: MyBrain marks Keep notes and mails by it
CALLERS = {"keep": "com.google.android.keep", "email": "com.google.android.gm", "bookmarks": "", "text": ""}
ROOT_FOLDERS = {"bookmarks", "bookmarks bar", "bookmarks toolbar", "bookmarks menu", "favorites bar", "other bookmarks"}
GMAIL_SYSTEM_LABELS = {
    "inbox",
    "sent",
    "opened",
    "unread",
    "important",
    "starred",
    "archived",
    "drafts",
    "spam",
    "trash",
}


@dataclass
class Entry:
    text: str
    kind: str
    title: str = ""
    created: float | None = None
    tags: list[str] = field(default_factory=list)
    archived: bool = False

    def share(self) -> dict:
        """The share event's data, as the phone app would send it."""
        parts = [p for p in (self.title, self.text if self.text != self.title else "") if p]
        footer = []
        if self.created:
            day = datetime.fromtimestamp(self.created)
            footer.append(f"{label(self.kind)}, {day.day} {day:%b %Y}")
        footer += [f"#{t}" for t in self.tags]
        text = "\n".join(parts)
        if footer:
            text += "\n\n" + " · ".join(footer)
        return {"text": text[:MAX_TEXT], "subject": self.title, "caller": CALLERS[self.kind]}


def label(kind: str) -> str:
    return {"keep": "From Google Keep", "email": "Mail", "bookmarks": "Bookmark", "text": "From a note"}[kind]


def tag(name: str) -> str:
    return re.sub(r"[^\w\-.+&]", "", re.sub(r"\s+", "-", name.strip().lower().lstrip("#")))[:32]


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def read(path: Path) -> list[Entry]:
    lower = path.name.lower()
    if zipfile.is_zipfile(path):
        return read_zip(path)
    if lower.endswith(".mbox"):
        return list(read_mbox(path))
    if lower.endswith(".json"):
        entry = keep_note(json.loads(path.read_text(encoding="utf-8", errors="replace")))
        return [entry] if entry else []
    head = path.read_bytes()[:4096].decode("utf-8", errors="replace")
    if lower.endswith((".html", ".htm")) or "NETSCAPE-Bookmark-file" in head:
        return read_bookmarks(path.read_text(encoding="utf-8", errors="replace"))
    if head.startswith("From "):
        return list(read_mbox(path))
    return list(read_text(path.read_text(encoding="utf-8", errors="replace")))


def read_zip(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
        for info in archive.infolist():
            lower = info.filename.lower()
            if info.is_dir():
                continue
            if lower.endswith(".json") and "/keep/" in f"/{lower}":
                try:
                    note = keep_note(json.loads(archive.read(info).decode("utf-8", errors="replace")))
                except ValueError:
                    continue
                if note:
                    entries.append(note)
            elif lower.endswith(".mbox"):
                # mailbox needs a real file, and Takeout's mbox can be big: unpack it to a temporary one
                target = Path(tmp) / "mail.mbox"
                with archive.open(info) as source, target.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                entries.extend(read_mbox(target))
            elif lower.endswith((".html", ".htm")) and "bookmark" in lower:
                entries.extend(read_bookmarks(archive.read(info).decode("utf-8", errors="replace")))
    return entries


def keep_note(note: object) -> Entry | None:
    """A Keep note from Takeout's JSON; None for one in the bin or an empty one."""
    if not isinstance(note, dict) or note.get("isTrashed"):
        return None
    lines = []
    if text := str(note.get("textContent") or "").strip():
        lines.append(text)
    for row in note.get("listContent") or []:
        if isinstance(row, dict) and str(row.get("text") or "").strip():
            lines.append(("☑ " if row.get("isChecked") else "☐ ") + str(row["text"]).strip())
    body = "\n".join(lines)
    for annotation in note.get("annotations") or []:
        link = str((annotation or {}).get("url") or "")
        if link.startswith("http") and link not in body:
            body = f"{body}\n{link}".strip()
    title = str(note.get("title") or "").strip()
    if not title and not body:
        return None
    created = note.get("createdTimestampUsec") or note.get("userEditedTimestampUsec")
    return Entry(
        text=body or title,
        kind="keep",
        title=title,
        created=int(created) / 1_000_000 if created else None,
        tags=[t for item in note.get("labels") or [] if (t := tag(str((item or {}).get("name") or "")))],
        archived=bool(note.get("isArchived")),
    )


def mail_text(message: email.message.Message) -> str:
    plain = html = ""
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() != "text" or part.get_filename():
            continue
        try:
            content = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
        except (LookupError, AssertionError):
            continue
        if part.get_content_subtype() == "plain" and not plain:
            plain = content
        elif part.get_content_subtype() == "html" and not html:
            html = content
    if plain.strip():
        return plain
    html = re.sub(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 \1", html, flags=re.I | re.S)
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<br\s*/?>|</p>|</div>", "\n", html, flags=re.I)
    return unescape(re.sub(r"<[^>]+>", " ", html))


def read_mbox(path: Path):
    box = mailbox.mbox(str(path), factory=lambda f: email.message_from_binary_file(f, policy=email.policy.compat32))
    try:
        for message in box:
            if message is None:
                continue
            subject = str(email.header.make_header(email.header.decode_header(message.get("Subject") or ""))).strip()
            body = re.sub(r"[ \t]+", " ", mail_text(message))
            body = re.sub(r"\n\s*\n+", "\n\n", body).strip()[:MAX_TEXT]
            if not subject and not body:
                continue
            try:
                created = parsedate_to_datetime(message["Date"]).timestamp() if message.get("Date") else None
            except (TypeError, ValueError):
                created = None
            labels = [
                tag(name)
                for name in str(message.get("X-Gmail-Labels") or "").split(",")
                if name.strip()
                and name.strip().lower() not in GMAIL_SYSTEM_LABELS
                and not name.strip().lower().startswith("category")
            ]
            yield Entry(
                text=body or subject, kind="email", title=subject, created=created, tags=[t for t in labels if t][:4]
            )
    finally:
        box.close()


class Bookmarks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[Entry] = []
        self.folders: list[str] = []
        self.pending: str | None = None
        self.in_folder = False
        self.link: dict | None = None
        self.text = ""

    def handle_starttag(self, name, attrs):
        a = {k.lower(): v or "" for k, v in attrs}
        if name == "h3":
            self.in_folder, self.text = True, ""
        elif name == "dl" and self.pending is not None:
            self.folders.append(self.pending)
            self.pending = None
        elif name == "a" and a.get("href", "").startswith("http"):
            self.link, self.text = a, ""

    def handle_endtag(self, name):
        if name == "h3":
            self.in_folder, self.pending = False, self.text.strip()
        elif name == "dl" and self.folders:
            self.folders.pop()
        elif name == "a" and self.link is not None:
            title, added = self.text.strip(), self.link.get("add_date", "")
            folder = next((f for f in reversed(self.folders) if f.lower() not in ROOT_FOLDERS), "")
            self.entries.append(
                Entry(
                    text=self.link["href"],
                    kind="bookmarks",
                    title=title,
                    created=float(added) if added.isdigit() else None,
                    tags=[t for t in [tag(folder)] if t],
                )
            )
            self.link = None

    def handle_data(self, data):
        if self.in_folder or self.link is not None:
            self.text += data


def read_bookmarks(html: str) -> list[Entry]:
    reader = Bookmarks()
    reader.feed(html)
    return reader.entries


def read_text(text: str):
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        if all(URL_RE.fullmatch(ln) for ln in lines):
            for line in lines:
                yield Entry(text=line, kind="text")
        else:
            yield Entry(text="\n".join(lines)[:MAX_TEXT], kind="text")


# ----------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------
def send(ha: str, token: str, data: dict, insecure: bool) -> None:
    request = urllib.request.Request(
        f"{ha.rstrip('/')}/api/events/mobile_app.share",
        data=json.dumps(data).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    # Only with --insecure: for a Home Assistant with a self-signed certificate
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Put Google Keep, Gmail, bookmarks or a text file into MyBrain, once.")
    parser.add_argument("files", nargs="+", type=Path, help="Takeout .zip, .mbox, Keep .json, bookmarks .html, .txt")
    parser.add_argument("--ha", help="Home Assistant's address, like http://homeassistant.local:8123")
    parser.add_argument("--token", help="a long-lived access token of an administrator")
    parser.add_argument("--dry-run", action="store_true", help="only count and show the first few")
    parser.add_argument("--skip-archived", action="store_true", help="leave out notes archived in Keep")
    parser.add_argument("--since", help="only what was saved on or after this day, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, help="send at most this many (to try it first)")
    parser.add_argument("--delay", type=float, default=0.3, help="seconds between two shares (default 0.3)")
    parser.add_argument("--insecure", action="store_true", help="accept Home Assistant's self-signed certificate")
    args = parser.parse_args()

    entries: list[Entry] = []
    for path in args.files:
        found = read(path)
        print(f"{path.name}: {len(found)} found")
        entries += found
    if args.skip_archived:
        entries = [e for e in entries if not e.archived]
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").timestamp()
        entries = [e for e in entries if (e.created or 0) >= since]
    entries.sort(key=lambda e: e.created or 0)  # oldest first, so the newest end up on top in MyBrain
    if args.limit:
        entries = entries[: args.limit]
    kinds = {k: sum(e.kind == k for e in entries) for k in CALLERS}
    links = sum(len(URL_RE.findall(e.share()["text"])) for e in entries)
    print(f"To send: {len(entries)} ({', '.join(f'{n} {k}' for k, n in kinds.items() if n)}), with {links} links.")

    if args.dry_run or not entries:
        for entry in entries[:5]:
            print("---\n" + entry.share()["text"][:400])
        return 0
    if not args.ha or not args.token:
        parser.error("--ha and --token are needed to send (or use --dry-run)")

    sent = 0
    for number, entry in enumerate(entries, start=1):
        try:
            send(args.ha, args.token, entry.share(), args.insecure)
            sent += 1
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print(f"Home Assistant refused the token ({e.code}): it must be an administrator's long-lived token")
                return 1
            print(f"#{number}: Home Assistant answered {e.code}; skipped")
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"Home Assistant can't be reached: {e}")
            return 1
        if number % 50 == 0 or number == len(entries):
            print(f"{number}/{len(entries)} sent")
        time.sleep(args.delay)
    print(f"Done: {sent} sent. MyBrain reads and sorts them in the background; its status bar shows how many are left.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
