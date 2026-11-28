"""What happens to something that comes in: saved at once, then its page read, then sorted.

Saving never waits for a site or an AI: the item is in the database the moment it is shared, and
a worker fills in the rest. After a restart the worker picks up whatever was still waiting.
"""

import asyncio
import contextlib
import logging
import re
import time
from typing import TYPE_CHECKING

from . import classify, dates, urls
from .config import Config
from .fetch import Fetcher, FetchError
from .ha import HA, HAError
from .store import STATUSES, Store

if TYPE_CHECKING:
    from .agenda import Agenda

logger = logging.getLogger(__name__)

_DATE_NAMES = {"due": "due", "happens_on": "on"}

WORKERS = 3
SENSOR = "sensor.mybrain_inbox"
AI_LOOKUP_SECONDS = 600
# After an AI Task failure, rules only for a while (the integration may be down or out of credit)
AI_PAUSE_SECONDS = 300


class Brain:
    def __init__(self, config: Config, store: Store, ha: HA, fetcher: Fetcher):
        self.config = config
        self.store = store
        self.ha = ha
        self.fetcher = fetcher
        self.queue: asyncio.Queue[tuple[int, bool]] = asyncio.Queue()
        self._queued: set[int] = set()
        self._ai_lock = asyncio.Lock()
        self._sensor_dirty = asyncio.Event()
        self.ai = {"entities": [], "entity": "", "checked_at": 0.0, "error": "", "error_at": 0.0, "answers": 0}
        self.last_share: dict = {}
        self.agenda: Agenda | None = None

    # ------------------------------------------------------------------
    # Coming in
    # ------------------------------------------------------------------
    async def on_share(self, data: dict) -> None:
        """A mobile_app.share event: {url, text, subject, caller, device_id} on Android, {entered} on iOS."""
        url = str(data.get("url") or "").strip()
        text = str(data.get("text") or data.get("entered") or "").strip()
        subject = str(data.get("subject") or "").strip()
        caller = str(data.get("caller") or "").strip()
        device = self.ha.devices.get(str(data.get("device_id") or ""), "")
        ids = await self.add(url=url, text=text, subject=subject, via="share", caller=caller, device=device)
        self.last_share = {"at": time.time(), "caller": caller, "device": device, "items": ids}
        logger.info(f"Shared from {caller or 'the phone'}: saved as {', '.join(map(str, ids)) or 'nothing'}")

    async def add(
        self, url: str = "", text: str = "", subject: str = "", via: str = "add", caller: str = "", device: str = ""
    ) -> list[int]:
        """Save what was shared or typed. Every link becomes an item; text without a link becomes a note.

        Returns the item ids (an item saved before is not saved twice: it comes back to the inbox).
        """
        links = urls.find_urls(url) or []
        for found in urls.find_urls(text):
            if found not in links:
                links.append(found)
        note = text
        for link in links:
            note = note.replace(link, " ")
        note = re.sub(r"[ \t]+", " ", note)
        note = re.sub(r"\n\s*\n+", "\n\n", note).strip(" \n-\u2013\u2014|:")
        extra = {k: v for k, v in (("subject", subject), ("device", device)) if v}

        if not links:
            if not note and not subject:
                return []
            body = note or subject
            item_id = await asyncio.to_thread(
                self.store.add,
                {
                    "kind": "note",
                    "title": _title_of(body),
                    "note": body,
                    "source": urls.source_of(None, caller),
                    "via": via,
                    "caller": caller,
                    "fetch_state": "skipped",
                    "extra": extra,
                },
            )
            self._enqueue(item_id)
            return [item_id]

        ids = []
        for n, link in enumerate(links[:20]):
            key = urls.normalize(link)
            existing = await asyncio.to_thread(self.store.by_url_key, key)
            if existing:
                await self._shared_again(existing, note if n == 0 else "")
                ids.append(existing["id"])
                continue
            item_id = await asyncio.to_thread(
                self.store.add,
                {
                    "kind": "link",
                    "url": link,
                    "url_key": key,
                    "title": subject if n == 0 and subject and not urls.find_urls(subject) else "",
                    "note": note if n == 0 else "",
                    "source": urls.source_of(link, caller),
                    "site": urls.host_of(link),
                    "via": via,
                    "caller": caller,
                    "fetch_state": "waiting" if self.config.fetch_pages else "skipped",
                    "extra": extra,
                },
            )
            self._enqueue(item_id)
            ids.append(item_id)
        return ids

    async def _shared_again(self, item: dict, note: str) -> None:
        """Saving something again says it still matters: count it, bring it back, keep a new note."""
        fields: dict = {"shares": int(item.get("shares") or 1) + 1}
        if item["status"] in ("done", "archived"):
            fields["status"] = "inbox"
            fields["done_at"] = None
        if note and note not in (item.get("note") or ""):
            fields["note"] = f"{item['note']}\n\n{note}".strip() if item.get("note") else note
        await asyncio.to_thread(self.store.update, item["id"], fields)
        await asyncio.to_thread(self.store.log, item["id"], "shared again", f"{fields['shares']} times")
        self._sensor_dirty.set()

    def _enqueue(self, item_id: int, fetch: bool = True) -> None:
        self._sensor_dirty.set()
        if item_id not in self._queued:
            self._queued.add(item_id)
            self.queue.put_nowait((item_id, fetch))

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Work through the queue, keep the sensor current, and listen for shares."""
        for item_id in await asyncio.to_thread(self.store.waiting):
            self._enqueue(item_id)
        tasks = [asyncio.create_task(self._worker(n), name=f"worker-{n}") for n in range(WORKERS)]
        tasks.append(asyncio.create_task(self._sensor_loop(), name="sensor"))
        if self.config.listen_shares and self.ha.available:
            tasks.append(asyncio.create_task(self.ha.listen(self.on_share), name="shares"))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

    async def _worker(self, n: int) -> None:
        while True:
            item_id, fetch = await self.queue.get()
            try:
                await self.process(item_id, fetch)
            except Exception:
                logger.exception(f"Item {item_id} could not be processed")
            finally:
                self._queued.discard(item_id)
                self.queue.task_done()
                self._sensor_dirty.set()

    async def process(self, item_id: int, fetch: bool = True) -> None:
        item = await asyncio.to_thread(self.store.get, item_id)
        if not item:
            return
        if fetch and item["kind"] == "link" and item["url"] and item["fetch_state"] in ("waiting", "again"):
            item = await self._read_page(item)
            if item is None:
                return  # it turned out to be one already saved
        if item.get("sorted_by") in ("", "again"):
            await self._sort(item)

    async def _read_page(self, item: dict) -> dict | None:
        try:
            page = await self.fetcher.read(item["url"])
        except FetchError as e:
            state = "dead" if e.dead else "failed"
            await asyncio.to_thread(
                self.store.update, item["id"], {"fetch_state": state, "fetch_error": str(e), "fetched_at": time.time()}
            )
            logger.info(f"Item {item['id']}: {item['url']}: {e}")
            return await asyncio.to_thread(self.store.get, item["id"])

        final_key = urls.normalize(page.url)
        if final_key != item["url_key"]:
            other = await asyncio.to_thread(self.store.by_url_key, final_key, item["id"])
            if other:
                # A short link (t.co, reddit's /s/, bit.ly) to something already saved: one item, not two
                await asyncio.to_thread(self.store.delete, item["id"])
                await self._shared_again(other, item.get("note") or "")
                logger.info(f"Item {item['id']} is item {other['id']} again; merged")
                return None

        fields: dict = {
            "url_key": final_key,
            "fetch_state": "ok",
            "fetch_error": "",
            "fetched_at": time.time(),
            "description": page.description[:2000],
            "text": page.text,
            "author": page.author[:200],
            "detail": page.detail[:200],
            "page_kind": page.kind,
            "site": urls.host_of(page.url) or item["site"],
        }
        # A short link is replaced by where it leads; any other link stays as it was shared
        if page.url != item["url"] and urls.is_short_link(item["url"]):
            fields["url"] = page.url
        # The page's title on the first read; a title changed by hand survives reading it again
        if page.title and (not item["title"] or item["fetch_state"] == "waiting"):
            fields["title"] = page.title[:500]
        source = urls.source_of(page.url, item.get("caller") or "")
        if source != "web" or item["source"] in ("web", "note"):
            fields["source"] = source
        extra = dict(item.get("extra") or {})
        extra.update({k: v for k, v in page.extra.items() if v is not None})
        if page.site_name:
            extra["site_name"] = page.site_name[:100]
        if page.published:
            extra["published"] = page.published[:40]
        fields["extra"] = extra
        # A shared text that only repeats the page title is not a note
        note = (item.get("note") or "").strip()
        if note and page.title and _same_text(note, page.title):
            fields["note"] = ""
        if page.image:
            thumb = await self.fetcher.thumbnail(page.image, f"{item['id']}-{int(time.time())}")
            if thumb:
                fields["image"] = thumb
                self._remove_thumb(item.get("image"))
        await asyncio.to_thread(self.store.update, item["id"], fields)
        return await asyncio.to_thread(self.store.get, item["id"])

    def _remove_thumb(self, name: str | None) -> None:
        if name:
            (self.fetcher.thumb_dir / name).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    async def ai_entity(self) -> str | None:
        """The AI Task entity to ask: the configured one, else the only/first one. None: no AI."""
        if not self.config.ai_classify or not self.ha.available:
            return None
        now = time.time()
        if now - self.ai["checked_at"] > AI_LOOKUP_SECONDS:
            try:
                self.ai["entities"] = await self.ha.ai_task_entities()
            except HAError as e:
                logger.debug(f"AI Task entities: {e}")
            self.ai["checked_at"] = now
        names = [e["entity_id"] for e in self.ai["entities"]]
        if self.config.ai_task_entity:
            self.ai["entity"] = self.config.ai_task_entity if self.config.ai_task_entity in names else ""
        else:
            self.ai["entity"] = names[0] if names else ""
        if not self.ai["entity"]:
            return None
        if self.ai["error_at"] and now - self.ai["error_at"] < AI_PAUSE_SECONDS:
            return None
        return self.ai["entity"]

    async def _sort(self, item: dict) -> None:
        rules = classify.by_rules(item, self.config.categories)
        result = rules
        entity = await self.ai_entity()
        if entity:
            known = await asyncio.to_thread(self.store.known_tags)
            async with self._ai_lock:
                try:
                    answer = await self.ha.generate_data(
                        entity, classify.ai_instructions(item, known), classify.ai_structure(self.config.categories)
                    )
                    result = classify.from_ai(answer, self.config.categories, rules)
                    self.ai["answers"] += 1
                    self.ai["error"], self.ai["error_at"] = "", 0.0
                except HAError as e:
                    self.ai["error"], self.ai["error_at"] = str(e), time.time()
                    logger.warning(f"AI Task {entity}: {e}; sorting by the rules for now")
        fields = {
            "category": result.category,
            "tags": result.tags,
            "purpose": result.purpose,
            "summary": result.summary or item.get("summary") or "",
            "sorted_by": result.by,
        }
        # What was set by hand stays
        if item.get("sorted_by") != "you":
            fields["priority"] = result.priority
        # A date the AI found in the content, when the item has none yet
        for key in dates.KINDS:
            value = getattr(result, key)
            if value and not item.get(key):
                fields[key] = value
                await asyncio.to_thread(self.store.log, item["id"], "dated", f"{_DATE_NAMES[key]} {value} · found")
        await asyncio.to_thread(self.store.update, item["id"], fields, False)
        await asyncio.to_thread(self.store.log, item["id"], "sorted", f"{result.category} · by {result.by}")

    # ------------------------------------------------------------------
    # Changes from the page
    # ------------------------------------------------------------------
    async def edit(self, item_id: int, changes: dict) -> dict:
        item = await asyncio.to_thread(self.store.get, item_id)
        if not item:
            raise LookupError("No such item")
        fields: dict = {}
        if "status" in changes:
            status = str(changes["status"])
            if status not in STATUSES:
                raise ValueError(f"status must be one of {', '.join(STATUSES)}")
            if status != item["status"]:
                fields["status"] = status
                fields["done_at"] = time.time() if status in ("done", "archived") else None
        if "priority" in changes:
            try:
                priority = int(changes["priority"])
            except (TypeError, ValueError) as e:
                raise ValueError("priority must be 0, 1 or 2") from e
            if priority not in (0, 1, 2):
                raise ValueError("priority must be 0, 1 or 2")
            fields["priority"] = priority
        if "category" in changes:
            category = str(changes["category"])
            if category not in self.config.categories:
                raise ValueError("Unknown category")
            fields["category"] = category
        if "purpose" in changes:
            purpose = str(changes["purpose"])
            if purpose not in classify.PURPOSES:
                raise ValueError("Unknown purpose")
            fields["purpose"] = purpose
        if "tags" in changes:
            raw = changes["tags"]
            if isinstance(raw, str):
                raw = re.split(r"[,\s]+", raw)
            if not isinstance(raw, list):
                raise ValueError("tags must be a list")
            tags = []
            for tag in raw:
                tag = classify.clean_tag(str(tag))
                if tag and tag not in tags:
                    tags.append(tag)
            fields["tags"] = tags[:12]
        for key in dates.KINDS:
            if key in changes:
                fields[key] = dates.clean(changes[key])
        for key, limit in (("title", 500), ("note", 20000), ("summary", 2000)):
            if key in changes:
                fields[key] = str(changes[key] or "")[:limit]
        if any(k in fields for k in ("category", "tags", "purpose", "priority")):
            fields["sorted_by"] = "you"
        if not fields:
            return item
        await asyncio.to_thread(self.store.update, item_id, fields)
        if "status" in fields:
            await asyncio.to_thread(self.store.log, item_id, fields["status"])
        for key in dates.KINDS:
            if key in fields and fields[key] != item.get(key):
                await asyncio.to_thread(self.store.log, item_id, "dated", f"{_DATE_NAMES[key]} {fields[key] or 'none'}")
        other = sorted(k for k in fields if k not in ("status", "done_at", "sorted_by", *dates.KINDS))
        if other:
            await asyncio.to_thread(self.store.log, item_id, "edited", ", ".join(other))
        self._sensor_dirty.set()
        return await asyncio.to_thread(self.store.get, item_id, True)

    async def opened(self, item_id: int) -> None:
        item = await asyncio.to_thread(self.store.get, item_id)
        if item and not item.get("opened_at"):
            await asyncio.to_thread(self.store.update, item_id, {"opened_at": time.time()}, False)
            await asyncio.to_thread(self.store.log, item_id, "opened")

    async def again(self, item_id: int, what: str) -> None:
        """Read the page again (refetch) and/or sort it again (resort)."""
        item = await asyncio.to_thread(self.store.get, item_id)
        if not item:
            raise LookupError("No such item")
        fields = {"sorted_by": "again"}
        if what == "refetch" and item["kind"] == "link":
            fields["fetch_state"] = "again"
        await asyncio.to_thread(self.store.update, item_id, fields, False)
        self._enqueue(item_id, what == "refetch")

    async def delete(self, item_id: int) -> None:
        item = await asyncio.to_thread(self.store.delete, item_id)
        if not item:
            raise LookupError("No such item")
        self._remove_thumb(item.get("image"))
        self._sensor_dirty.set()

    # ------------------------------------------------------------------
    # The sensor
    # ------------------------------------------------------------------
    async def _sensor_loop(self) -> None:
        if not self.ha.available:
            return
        while True:
            # Home Assistant forgets the state on a restart: send it now and then anyway
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._sensor_dirty.wait(), timeout=600)
            self._sensor_dirty.clear()
            await asyncio.sleep(2)  # one update for a burst of changes
            counts = await asyncio.to_thread(self.store.counts)
            try:
                await self.ha.set_state(
                    SENSOR,
                    counts["by_status"]["inbox"],
                    {
                        "friendly_name": "MyBrain inbox",
                        "icon": "mdi:brain",
                        "unit_of_measurement": "items",
                        "state_class": "measurement",
                        "later": counts["by_status"]["later"],
                        "done": counts["by_status"]["done"],
                        "archived": counts["by_status"]["archived"],
                        "total": counts["total"],
                        "waiting": counts["waiting"],
                    },
                )
            except HAError as e:
                logger.debug(f"Sensor: {e}")
            if self.agenda:
                try:
                    await self.agenda.sync()
                except Exception:
                    logger.exception("Calendar and next-item sensor")

    def summary(self) -> dict:
        return {
            "queue": self.queue.qsize() + len(self._queued),
            "listening": self.ha.connected,
            "ha_version": self.ha.version,
            "last_share": self.last_share,
            "calendar": dict(self.agenda.state) if self.agenda else {},
            "ai": {
                "enabled": self.config.ai_classify,
                "entity": self.ai["entity"],
                "entities": self.ai["entities"],
                "error": self.ai["error"] if self.ai["error_at"] else "",
                "answers": self.ai["answers"],
            },
        }


def _title_of(text: str) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= 120 else line[:119].rstrip() + "…"


def _same_text(a: str, b: str) -> bool:
    def squash(s: str) -> str:
        return re.sub(r"\W+", "", s.lower())

    a, b = squash(a), squash(b)
    return bool(a) and (a == b or a in b)
