"""Web UI for Arcade, served through Home Assistant ingress."""

import contextlib
import json
import logging
import tempfile
import time
from pathlib import Path

from aiohttp import web

from .config import DATA_DIR, MAME_DIR
from .donate import DonatePrompt
from .hiscores import HiScores
from .library import Library
from .saves import MAX_FILE, Saves
from .stats import Stats
from .thumbs import MAX_THUMB, TYPES, Thumbs

logger = logging.getLogger(__name__)


def _app_version() -> str:
    """The app version from config.yaml (copied next to src/ in the image), so it can't drift."""
    path = Path(__file__).resolve().parent.parent / "config.yaml"
    with contextlib.suppress(OSError):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    return "dev"


APP_VERSION = _app_version()
SRC_DIR = Path(__file__).resolve().parent
STATIC_DIR = SRC_DIR / "static"
INDEX_HTML = (SRC_DIR / "web" / "index.html").read_text(encoding="utf-8")
PLAY_HTML = (SRC_DIR / "web" / "play.html").read_text(encoding="utf-8")
MAX_UPLOAD = 128 * 1024 * 1024
LEGAL_FILE = DATA_DIR / "legal.json"
# The MAME build is fixed per app version: let browsers keep it
MAME_MAX_AGE = 30 * 24 * 3600


def _mame_info() -> dict:
    with contextlib.suppress(OSError, ValueError):
        return json.loads((MAME_DIR / "build.json").read_text(encoding="utf-8"))
    return {}


class WebUI:
    def __init__(self, port: int, library: Library, saves: Saves, stats: Stats, thumbs: Thumbs, hiscores: HiScores):
        self.port = port
        self.library = library
        self.saves = saves
        self.stats = stats
        self.thumbs = thumbs
        self.hiscores = hiscores
        self.mame = _mame_info()
        self.app = web.Application(client_max_size=MAX_UPLOAD)
        self._runner: web.AppRunner | None = None
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/play/{game}", self._play)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_static("/static/", STATIC_DIR, name="static")
        r.add_get("/mame/{file}", self._mame_file)
        r.add_get("/mame/plugins/{path:.+}", self._mame_plugin)
        r.add_get("/api/status", self._status)
        r.add_post("/api/legal", self._legal)
        r.add_get("/api/games", self._games)
        r.add_post("/api/roms", self._upload)
        r.add_delete("/api/roms/{file}", self._delete_rom)
        r.add_post("/api/roms/{file}/rename", self._rename_rom)
        r.add_delete("/api/unrecognised", self._forget_unrecognised)
        r.add_get("/roms/{file}", self._rom)
        r.add_get("/api/saves/{game}", self._saves_list)
        r.add_get("/api/saves/{game}/{path:.+}", self._save_get)
        r.add_put("/api/saves/{game}/{path:.+}", self._save_put)
        r.add_delete("/api/saves/{game}", self._saves_delete)
        r.add_get("/thumbs/{game}", self._thumb_get)
        r.add_put("/api/thumbs/{game}", self._thumb_put)
        r.add_get("/api/stats", self._stats)
        r.add_get("/api/scores", self._scores)
        r.add_post("/api/stats/{game}/session", self._session_start)
        r.add_post("/api/stats/session/{session}", self._session_beat)

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI on port {self.port}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    # ------------------------------------------------------------------ pages
    async def _index(self, request: web.Request) -> web.Response:
        base = request.headers.get("X-Ingress-Path", "")
        html = INDEX_HTML.replace("__BASE__", base).replace("__APP_VERSION__", APP_VERSION)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def _play(self, request: web.Request) -> web.Response:
        game = request.match_info["game"]
        if game not in self.library.games:
            raise web.HTTPNotFound(text=f"{game} is not a game this app's MAME build can run")
        base = request.headers.get("X-Ingress-Path", "")
        html = PLAY_HTML.replace("__BASE__", base).replace("__APP_VERSION__", APP_VERSION).replace("__GAME__", game)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def _mame_file(self, request: web.Request) -> web.StreamResponse:
        name = request.match_info["file"]
        if name not in ("mame.js", "mame.wasm"):
            raise web.HTTPNotFound()
        path = MAME_DIR / name
        if not path.is_file():
            raise web.HTTPNotFound(text="The MAME build is missing from this image")
        # aiohttp serves mame.wasm.gz / .br instead when the browser accepts it
        resp = web.FileResponse(path, headers={"Cache-Control": f"public, max-age={MAME_MAX_AGE}"})
        if name.endswith(".wasm"):
            resp.content_type = "application/wasm"
        return resp

    async def _mame_plugin(self, request: web.Request) -> web.StreamResponse:
        rel = request.match_info["path"]
        root = (MAME_DIR / "plugins").resolve()
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": f"public, max-age={MAME_MAX_AGE}"})

    # ------------------------------------------------------------------- api
    def _legal_state(self) -> dict:
        with contextlib.suppress(OSError, ValueError):
            return json.loads(LEGAL_FILE.read_text(encoding="utf-8"))
        return {}

    async def _status(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "version": APP_VERSION,
                "mame": self.mame,
                "rom_folder": str(self.library.rom_folder),
                "known_games": len(self.library.games),
                "legal_accepted": bool(self._legal_state().get("accepted")),
                "mame_present": (MAME_DIR / "mame.wasm").is_file(),
            }
        )

    async def _legal(self, _request: web.Request) -> web.Response:
        LEGAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        LEGAL_FILE.write_text(json.dumps({"accepted": True, "at": time.time()}), encoding="utf-8")
        return web.json_response({"ok": True})

    async def _games(self, _request: web.Request) -> web.Response:
        data = self.library.status()
        hi = self.saves.high_score_games()
        tables = self.hiscores.all()
        thumbs = self.thumbs.all()
        played = self.stats.played()
        for g in data["games"]:
            # Games that keep their scores in NVRAM have no .hi, but can have a table
            g["has_scores"] = g["name"] in hi or g["name"] in tables
            g["score_table"] = g["name"] in tables
            g["score_definition"] = self.hiscores.definition_name(g["name"]) is not None
            g["thumb"] = thumbs.get(g["name"])
            g["played"] = played.get(g["name"])
        return web.json_response(data)

    async def _upload(self, request: web.Request) -> web.Response:
        if not self._legal_state().get("accepted"):
            return web.json_response({"error": "Accept the notice about ROM files first"}, status=403)
        reader = await request.multipart()
        results = []
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "file" or not part.filename:
                continue
            name = Path(part.filename).name
            fd, tmp_name = tempfile.mkstemp(suffix=".upload", dir=DATA_DIR)
            tmp = Path(tmp_name)
            size = 0
            try:
                with open(fd, "wb") as f:
                    while chunk := await part.read_chunk(256 * 1024):
                        size += len(chunk)
                        if size > MAX_UPLOAD:
                            raise ValueError(f"{name} is larger than {MAX_UPLOAD // 1024 // 1024} MB")
                        f.write(chunk)
                logger.info(f"Received {name}: {size} bytes")
                info = self.library.add(tmp, name)
                logger.info(
                    f"Added {name} as {info['file']} ({info['description']})"
                    + (f", replacing {', '.join(info['replaced'])}" if info["replaced"] else "")
                )
                results.append({"file": name, "ok": True, **info})
            except (ValueError, OSError) as e:
                results.append({"file": name, "ok": False, "error": str(e)})
            finally:
                tmp.unlink(missing_ok=True)
        return web.json_response({"results": results})

    async def _delete_rom(self, request: web.Request) -> web.Response:
        if not self.library.remove(request.match_info["file"]):
            raise web.HTTPNotFound()
        logger.info(f"Removed {request.match_info['file']}")
        return web.json_response({"ok": True})

    async def _rename_rom(self, request: web.Request) -> web.Response:
        body = await request.json()
        try:
            self.library.rename(request.match_info["file"], str(body.get("to", "")))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    async def _forget_unrecognised(self, _request: web.Request) -> web.Response:
        """Clear the remembered uploads that weren't recognised (files in the folder stay listed)."""
        return web.json_response({"ok": True, "removed": self.library.forget_rejected()})

    async def _rom(self, request: web.Request) -> web.StreamResponse:
        path = self.library.path(request.match_info["file"])
        if not path:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})

    def _game(self, request: web.Request) -> str:
        game = request.match_info["game"]
        if game not in self.library.games:
            raise web.HTTPNotFound()
        return game

    async def _saves_list(self, request: web.Request) -> web.Response:
        return web.json_response({"files": self.saves.list(self._game(request))})

    async def _save_get(self, request: web.Request) -> web.StreamResponse:
        path = self.saves.path(self._game(request), request.match_info["path"])
        if not path or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-store"})

    async def _save_put(self, request: web.Request) -> web.Response:
        game = self._game(request)
        data = await request.read()
        if len(data) > MAX_FILE:
            return web.json_response({"error": "file too large"}, status=413)
        try:
            self.saves.write(game, request.match_info["path"], data)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    async def _saves_delete(self, request: web.Request) -> web.Response:
        n = self.saves.delete_game(self._game(request))
        logger.info(f"Cleared {n} saved file(s) of {request.match_info['game']}")
        return web.json_response({"ok": True, "removed": n})

    # ---------------------------------------------------------- thumbnails
    async def _thumb_get(self, request: web.Request) -> web.StreamResponse:
        path = self.thumbs.path(self._game(request))
        if not path:
            raise web.HTTPNotFound()
        # The library asks for ?v=<version>, so a new picture has a new URL
        resp = web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
        resp.content_type = TYPES[path.suffix]
        return resp

    async def _thumb_put(self, request: web.Request) -> web.Response:
        game = self._game(request)
        if request.content_length and request.content_length > MAX_THUMB:
            return web.json_response({"error": "image too large"}, status=413)
        data = await request.read()
        try:
            self.thumbs.write(game, data)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        logger.debug(f"New thumbnail for {game} ({len(data)} bytes)")
        return web.json_response({"ok": True, "thumb": self.thumbs.version(game)})

    # --------------------------------------------------------------- stats
    async def _stats(self, request: web.Request) -> web.Response:
        try:
            offset = int(request.query.get("offset", "0"))
        except ValueError:
            offset = 0
        data = self.stats.summary(request.query.get("tz", "")[:64], max(-900, min(900, offset)))
        hi = self.saves.high_score_games() | set(self.hiscores.all())
        for g in data["games"]:
            info = self.library.games.get(g["name"], {})
            g["description"] = info.get("description", g["name"])
            g["year"] = info.get("year", "")
            g["company"] = info.get("company", "")
            g["has_scores"] = g["name"] in hi
        if data["longest"]:
            data["longest"]["description"] = self.library.games.get(data["longest"]["game"], {}).get(
                "description", data["longest"]["game"]
            )
        return web.json_response(data)

    async def _session_start(self, request: web.Request) -> web.Response:
        return web.json_response({"session": self.stats.start(self._game(request))})

    async def _session_beat(self, request: web.Request) -> web.Response:
        # Also sent with navigator.sendBeacon when the page closes, which posts the JSON as text
        try:
            session = int(request.match_info["session"])
            seconds = float(json.loads(await request.text() or "{}").get("seconds", 0))
        except (ValueError, TypeError, AttributeError):
            return web.json_response({"error": "invalid request"}, status=400)
        if not 0 <= seconds < 7 * 24 * 3600:
            return web.json_response({"error": "invalid play time"}, status=400)
        if not self.stats.beat(session, seconds):
            raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    # ---------------------------------------------------------- high scores
    async def _scores(self, _request: web.Request) -> web.Response:
        """Every decoded high score table: {game: {tables: [{columns, rows}], fields, updated, definition}}."""
        return web.json_response({"games": self.hiscores.all()})
