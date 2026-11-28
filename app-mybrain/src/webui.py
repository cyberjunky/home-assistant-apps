"""The pages, served through Home Assistant's ingress.

Under Home Assistant every request must come from its signed-in ingress proxy, which says who the
user is (X-Remote-User-Id); another app on the network could otherwise claim to be anyone and read
their private items. Changes are accepted as JSON only, so another site can't send them either.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

from aiohttp import web

from . import classify, urls
from .brain import Brain
from .config import APP_VERSION, DATA_DIR, Config
from .donate import DonatePrompt
from .store import SORTS, STATUSES, Store

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
THUMB_NAME = re.compile(r"^[\w-]+\.webp$")
# Development only (no Supervisor): who the page is, as 'id:name'
DEV_USER = os.environ.get("MYBRAIN_DEV_USER", "")


@web.middleware
async def ingress_only(request: web.Request, handler):
    if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
        raise web.HTTPForbidden(text="Use the sidebar")
    return await handler(request)


def viewer(request: web.Request) -> tuple[str, str]:
    """The Home Assistant user looking at the page: (id, name), or ('', '') when unknown."""
    user = request.headers.get("X-Remote-User-Id", "")
    name = request.headers.get("X-Remote-User-Display-Name") or request.headers.get("X-Remote-User-Name", "")
    if not user and DEV_USER and not os.environ.get("SUPERVISOR_TOKEN"):
        user, _, name = DEV_USER.partition(":")
    return user, name


class WebUI:
    """The pages and their JSON API."""

    def __init__(self, port: int, config: Config, brain: Brain, store: Store, thumb_dir: Path):
        self.port = port
        self.config = config
        self.brain = brain
        self.store = store
        self.thumb_dir = thumb_dir
        self.app = web.Application(client_max_size=2 * 1024 * 1024, middlewares=[ingress_only])
        add = self.app.router.add_get
        add("/", self._index)
        add("/api/status", self._status)
        add("/api/items", self._items)
        add("/api/item", self._item)
        add("/api/stats", self._stats)
        add("/api/history", self._history)
        add("/api/export", self._export)
        add("/thumbs/{name}", self._thumb)
        post = self.app.router.add_post
        post("/api/add", self._add)
        post("/api/item", self._edit)
        post("/api/item/open", self._open)
        post("/api/item/again", self._again)
        post("/api/item/delete", self._delete)
        post("/api/item/copy", self._copy)
        post("/api/bulk", self._bulk)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/static", WEB_DIR)

    async def start(self) -> None:
        """Serve until cancelled."""
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info(f"Pages on port {self.port}")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    def _who(self, request: web.Request) -> str:
        user, name = viewer(request)
        self.brain.met(user, name)
        return user

    async def _status(self, request: web.Request) -> web.Response:
        user = self._who(request)
        await self.brain.refresh_people()  # the names of the household, at most every ten minutes
        await self.brain.ai_entity()  # which AI sorts, looked up at most every ten minutes
        household = await asyncio.to_thread(self.store.household, user)
        agenda = self.brain.agenda
        return web.json_response(
            {
                "version": APP_VERSION,
                "now": time.time(),
                "config": {
                    "listen_shares": self.config.listen_shares,
                    "fetch_pages": self.config.fetch_pages,
                    "ai_classify": self.config.ai_classify,
                    "ai_task_entity": self.config.ai_task_entity,
                    "calendar_sync": self.config.calendar_sync,
                    "calendar_entity": self.config.calendar_entity,
                    "share_by_default": self.config.share_by_default,
                },
                "categories": self.config.categories,
                "purposes": classify.PURPOSES,
                "statuses": STATUSES,
                "sorts": list(SORTS),
                "source_names": urls.SOURCE_NAMES,
                "viewer": {"id": user, "name": self.brain.users.get(user, "")},
                "household": [{**h, "name": self.brain.name_of(h["owner"])} for h in household],
                "names": {u: n for u, n in self.brain.users.items()},
                "entities": agenda.for_user(user) if agenda else {},
                "counts": await asyncio.to_thread(self.store.counts, user),
                "database_size": await asyncio.to_thread(self.store.size),
                **self.brain.summary(),
            }
        )

    @staticmethod
    def _int(value: str | None, default: int | None = None) -> int | None:
        if value in (None, ""):
            return default
        try:
            return int(value)
        except ValueError as e:
            raise web.HTTPBadRequest(text="not a number") from e

    async def _items(self, request: web.Request) -> web.Response:
        q = request.query
        days = self._int(q.get("days"))
        whose = q.get("whose", "mine")
        if whose not in ("mine", "everyone") and not whose.startswith("user:"):
            raise web.HTTPBadRequest(text="whose must be mine, everyone or user:<id>")
        result = await asyncio.to_thread(
            self.store.search,
            viewer=self._who(request),
            whose=whose,
            q=q.get("q", "")[:300],
            status=q.get("status", ""),
            category=q.get("category", ""),
            source=q.get("source", ""),
            tag=q.get("tag", ""),
            purpose=q.get("purpose", ""),
            priority=self._int(q.get("priority")),
            dated=q.get("dated") == "1",
            since=time.time() - days * 86400 if days else None,
            sort=q.get("sort", "new"),
            limit=self._int(q.get("limit"), 60) or 60,
            offset=self._int(q.get("offset"), 0) or 0,
        )
        return web.json_response(result)

    async def _item(self, request: web.Request) -> web.Response:
        user = self._who(request)
        item = await asyncio.to_thread(self.store.get, self._int(request.query.get("id"), 0), True)
        if not item or not Store.visible(item, user):
            return web.json_response({"error": "No such item"}, status=404)
        item["mine"] = Store.owns(item, user)
        item["owner_name"] = self.brain.name_of(item["owner"]) if item["owner"] else ""
        if not item["mine"]:
            item["history"] = []  # what someone else did with it is theirs
        return web.json_response(item)

    async def _stats(self, request: web.Request) -> web.Response:
        days = min(max(7, self._int(request.query.get("days"), 90) or 90), 730)
        return web.json_response(await asyncio.to_thread(self.store.stats, days, self._who(request)))

    async def _history(self, request: web.Request) -> web.Response:
        before = request.query.get("before")
        try:
            before_at = float(before) if before else None
        except ValueError as e:
            raise web.HTTPBadRequest(text="before must be a time") from e
        limit = self._int(request.query.get("limit"), 200) or 200
        user = self._who(request)
        return web.json_response({"events": await asyncio.to_thread(self.store.history, user, limit, before_at)})

    async def _export(self, request: web.Request) -> web.Response:
        """Everything this user saved, as one JSON file to keep."""
        user = self._who(request)
        result = await asyncio.to_thread(self.store.search, viewer=user, limit=500)
        items, offset = list(result["items"]), 500
        while offset < result["total"]:
            more = await asyncio.to_thread(self.store.search, viewer=user, limit=500, offset=offset)
            items += more["items"]
            offset += 500
        stamp = time.strftime("%Y%m%d-%H%M")
        return web.Response(
            text=json.dumps({"app": "MyBrain", "version": APP_VERSION, "exported_at": time.time(), "items": items}),
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="mybrain-{stamp}.json"'},
        )

    async def _thumb(self, request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        if not THUMB_NAME.match(name):
            raise web.HTTPNotFound()
        path = self.thumb_dir / name
        if not path.is_file():
            raise web.HTTPNotFound()
        # The name changes when the picture does, so it may be cached for good
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # ------------------------------------------------------------------
    # Changes: only from Home Assistant's signed-in ingress, as JSON
    # ------------------------------------------------------------------
    @staticmethod
    async def _change_body(request: web.Request) -> dict:
        # Another site's page can't send this content type without the browser asking first
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Send JSON")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Send a JSON object")
        return body

    @staticmethod
    def _id(body: dict) -> int:
        try:
            return int(body.get("id"))
        except (TypeError, ValueError) as e:
            raise web.HTTPBadRequest(text="id must be a number") from e

    async def _add(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        text = str(body.get("text") or "")[:50000]
        if not text.strip():
            return web.json_response({"error": "Paste a link or type a note"}, status=400)
        ids = await self.brain.add(text=text, via="add", owner=self._who(request))
        return web.json_response({"ok": True, "ids": ids})

    async def _edit(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        changes = {k: v for k, v in body.items() if k != "id"}
        user = self._who(request)
        try:
            item = await self.brain.edit(self._id(body), changes, user)
            item["mine"] = True
            return web.json_response(item)
        except PermissionError as e:
            return web.json_response({"error": str(e)}, status=403)
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _open(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        await self.brain.opened(self._id(body), self._who(request))
        return web.json_response({"ok": True})

    async def _again(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        what = str(body.get("what") or "")
        if what not in ("refetch", "resort"):
            return web.json_response({"error": "what must be refetch or resort"}, status=400)
        try:
            await self.brain.again(self._id(body), what, self._who(request))
        except PermissionError as e:
            return web.json_response({"error": str(e)}, status=403)
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)
        return web.json_response({"ok": True})

    async def _delete(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        try:
            await self.brain.delete(self._id(body), self._who(request))
        except PermissionError as e:
            return web.json_response({"error": str(e)}, status=403)
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)
        return web.json_response({"ok": True})

    async def _copy(self, request: web.Request) -> web.Response:
        """Save someone else's public item to your own collection."""
        body = await self._change_body(request)
        try:
            ids = await self.brain.copy(self._id(body), self._who(request))
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)
        return web.json_response({"ok": True, "ids": ids})

    async def _bulk(self, request: web.Request) -> web.Response:
        """One status for many items at once (the inbox's 'archive these')."""
        body = await self._change_body(request)
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids) or len(ids) > 1000:
            return web.json_response({"error": "ids must be a list of item ids"}, status=400)
        status = str(body.get("status") or "")
        if status not in STATUSES:
            return web.json_response({"error": "Unknown status"}, status=400)
        changed = 0
        user = self._who(request)
        for item_id in ids:
            try:
                await self.brain.edit(item_id, {"status": status}, user)
                changed += 1
            except (LookupError, PermissionError):
                continue
        return web.json_response({"ok": True, "changed": changed})
