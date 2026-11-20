"""What the app does with a scanner: find them, read their options, preview, scan pages into the
tray and save the tray as a document. One thing at a time: a scanner can't do two."""

import asyncio
import contextlib
import logging
import os
import re
import shutil
import time
from pathlib import Path

import aiohttp

from . import sane, share
from .archive import Archive, clean_tags
from .config import DESTINATION_ROOTS, IPP_USB_PORTS, RUN_DIR, WORK_DIR, Config
from .pages import PREVIEW_SIZE, Tray, load_scan
from .settings import Settings
from .setup import write_airscan

logger = logging.getLogger(__name__)

# A feeder that never says it is empty must not scan forever
MAX_BATCH = 500
FEEDER = re.compile(r"adf|feeder|duplex", re.IGNORECASE)
_MODEL = re.compile(r"<(?:\w+:)?MakeAndModel>([^<]+)<")


class Busy(Exception):
    pass


def resolution_of(groups: list[dict]) -> int:
    """The resolution the scan will have, for the page's size on paper."""
    values = sane.current_values(groups)
    for name in ("resolution", "x-resolution", "y-resolution"):
        try:
            dpi = int(float(values.get(name, "")))
        except (TypeError, ValueError):
            continue
        if dpi > 0:
            return dpi
    return 300


def uses_feeder(groups: list[dict]) -> bool:
    source = sane.find_option(groups, "source")
    return bool(source and source["active"] and source["value"] and FEEDER.search(source["value"]))


class Station:
    def __init__(self, config: Config, settings: Settings, tray: Tray, archive: Archive, work: Path = WORK_DIR):
        self.config = config
        self.settings = settings
        self.tray = tray
        self.archive = archive
        self.work = Path(work)
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True, exist_ok=True)
        self.devices: list[dict] = []
        self.devices_at = 0.0
        self.devices_error = ""
        self.searching = False
        self.job: dict = {"kind": "", "state": "idle"}
        self.preview: dict | None = None
        self._task: asyncio.Task | None = None
        self._scan: sane.Scan | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._share_state: tuple[float, dict] = (0.0, {})

    # ------------------------------------------------------------------
    # Scanners
    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.job.get("state") == "running"

    async def _driverless(self) -> list[tuple[str, str]]:
        """Driverless USB scanners: ipp-usb serves each one as eSCL on a port of this machine."""
        if not self.config.driverless_usb or not (RUN_DIR / "driverless-usb").exists():
            return []
        found = []
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for port in IPP_USB_PORTS:
                url = f"http://127.0.0.1:{port}/eSCL"
                try:
                    async with session.get(url + "/ScannerCapabilities") as response:
                        if response.status != 200:
                            continue
                        match = _MODEL.search(await response.text())
                except (aiohttp.ClientError, TimeoutError, OSError):
                    continue
                found.append(((match.group(1).strip() if match else f"USB scanner {port - 60000 + 1}") + " (USB)", url))
        return found

    async def find_devices(self, restart_usb: bool = False) -> list[dict]:
        """Ask SANE for the scanners. Slow (seconds): the result is kept until asked again."""
        if self.busy:
            raise Busy("The scanner is busy")
        async with self._lock:
            self.searching = True
            try:
                if restart_usb and self.config.driverless_usb and shutil.which("s6-svc"):
                    # ipp-usb sees a scanner plugged in later only when it starts
                    with contextlib.suppress(OSError):
                        proc = await asyncio.create_subprocess_exec("s6-svc", "-r", "/run/service/ipp-usb")
                        await proc.wait()
                        await asyncio.sleep(4)
                usb = await self._driverless()
                write_airscan([*self.config.network_scanners, *usb])
                self.devices = await sane.list_devices()
                self.devices_error = ""
            except sane.ScanError as e:
                self.devices_error = str(e)
                self.devices = []
            finally:
                self.searching = False
                self.devices_at = time.time()
        logger.info(f"Scanners: {', '.join(d['id'] for d in self.devices) or 'none'}")
        return self.devices

    async def options(self, device: str, values: dict | None) -> dict:
        if self.busy:
            raise Busy("The scanner is busy")
        async with self._lock:
            result = await sane.read_options(device, values)
        result["bed"] = sane.bed_size(result["groups"])
        result["feeder"] = uses_feeder(result["groups"])
        result["resolution"] = resolution_of(result["groups"])
        return result

    # ------------------------------------------------------------------
    # Jobs: a preview, a scan, a save
    # ------------------------------------------------------------------
    def _start(self, kind: str, coroutine) -> dict:
        if self.busy:
            coroutine.close()
            raise Busy("The scanner is busy")
        self._stop = asyncio.Event()
        self.job = {
            "kind": kind,
            "state": "running",
            "page": 0,
            "percent": 0.0,
            "pages": 0,
            "message": "",
            "error": "",
            "started": time.time(),
            "finished": None,
            "cancelling": False,
        }
        self._task = asyncio.create_task(self._run(coroutine), name=kind)
        return self.job

    async def _run(self, coroutine) -> None:
        job = self.job
        try:
            await coroutine
            job["state"] = "done"
        except sane.ScanError as e:
            # Cancelling is what the user asked for, not a failure
            job["state"] = "done" if str(e) == "Cancelled" else "failed"
            job["error"] = "" if str(e) == "Cancelled" else str(e)
            job["cancelled"] = str(e) == "Cancelled"
        except (share.ShareError, ValueError, OSError) as e:
            job["state"] = "failed"
            job["error"] = str(e)
        except Exception as e:
            logger.exception(f"The {job['kind']} failed")
            job["state"] = "failed"
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["finished"] = time.time()
            job["message"] = ""
            self._scan = None
            if job["error"]:
                logger.warning(f"The {job['kind']} failed: {job['error']}")

    async def cancel(self) -> None:
        if not self.busy:
            return
        self.job["cancelling"] = True
        self._stop.set()
        if self._scan is not None:
            await self._scan.cancel()

    async def wait(self) -> None:
        """Until the running job is done (for the tests and for a clean stop)."""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _progress(self, page: int, percent: float) -> None:
        self.job["page"] = page
        self.job["percent"] = percent

    async def _prepare(self, device: str, values: dict) -> tuple[list[str], list[dict]]:
        async with self._lock:
            options = await sane.read_options(device, values)
        return sane.option_args(options["applied"], options["groups"]), options["groups"]

    def start_preview(self, device: str, values: dict) -> dict:
        return self._start("preview", self._do_preview(device, values))

    async def _do_preview(self, device: str, values: dict) -> None:
        self.job["message"] = "Asking the scanner"
        async with self._lock:
            options = await sane.read_options(device, values)
            preview = await sane.read_options(device, sane.preview_values(options["groups"], options["applied"]))
        folder = self.work / "preview"
        shutil.rmtree(folder, ignore_errors=True)
        target = self.work / "preview.jpg"
        size: list[int] = []

        async def on_page(path: Path) -> None:
            def convert() -> None:
                image = load_scan(path)
                image = image.convert("L" if image.mode in ("1", "L") else "RGB")
                image.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
                image.save(target, quality=85)
                size.extend(image.size)

            await asyncio.to_thread(convert)

        self.job["message"] = "Scanning a preview"
        self._scan = sane.Scan()
        try:
            await self._scan.run(
                device,
                sane.option_args(preview["applied"], preview["groups"]),
                folder,
                False,
                1,
                self._progress,
                on_page,
            )
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if not size:
            raise sane.ScanError("The scanner gave no picture")
        self.preview = {
            "device": device,
            "at": time.time(),
            "width": size[0],
            "height": size[1],
            "bed": sane.bed_size(preview["groups"]),
            "source": sane.current_values(preview["groups"]).get("source", ""),
        }

    def start_scan(self, device: str, values: dict, batch: dict) -> dict:
        return self._start("scan", self._do_scan(device, values, batch))

    async def _do_scan(self, device: str, values: dict, batch: dict) -> None:
        self.job["message"] = "Asking the scanner"
        args, groups = await self._prepare(device, values)
        dpi = resolution_of(groups)
        quality = int(self.settings["jpeg_quality"])
        mode = batch.get("mode", "single")
        count = int(batch.get("count") or 0)
        if mode == "feeder" and count == 0 and not uses_feeder(groups):
            raise sane.ScanError(
                "The source isn't a feeder: choose the feeder as the source, or give the number of pages"
            )
        self.settings.remember(device, values, batch)
        folder = self.work / f"scan-{int(time.time() * 1000)}"

        async def on_page(path: Path) -> None:
            await asyncio.to_thread(self.tray.add, path, dpi, quality)
            self.job["pages"] += 1

        try:
            if mode == "feeder":
                self.job["message"] = "Scanning"
                self._scan = sane.Scan()
                await self._scan.run(device, args, folder, True, count or MAX_BATCH, self._progress, on_page)
                return
            rounds = 1 if mode == "single" else (count or MAX_BATCH)
            interval = int(batch.get("interval") or 10)
            for number in range(1, rounds + 1):
                self.job["message"] = "Scanning"
                self._scan = sane.Scan()
                before = self.job["pages"]
                await self._scan.run(
                    device, args, folder, False, 1, lambda _page, percent, n=number: self._progress(n, percent), on_page
                )
                if self.job["pages"] == before:
                    raise sane.ScanError("The scanner gave no picture")
                if number == rounds:
                    break
                # Time to turn the page; Stop here ends the batch with what there is
                self._scan = None
                for left in range(interval, 0, -1):
                    self.job["message"] = f"Next page in {left} s"
                    self.job["countdown"] = left
                    try:
                        await asyncio.wait_for(self._stop.wait(), 1)
                        return
                    except TimeoutError:
                        pass
                self.job["countdown"] = 0
        finally:
            self.job["countdown"] = 0
            shutil.rmtree(folder, ignore_errors=True)

    def suggested_name(self) -> str:
        return share.fill(self.settings["name_pattern"], share.tokens(time.time(), pages=len(self.tray.pages)))

    def start_save(self, name: str, tags: list[str] | str, ocr: bool, ids: list[str] | None, device: str = "") -> dict:
        pages = [p for p in self.tray.pages if ids is None or p["id"] in ids]
        if not pages:
            raise ValueError("There are no pages to save")
        return self._start("save", self._do_save(pages, name, tags, ocr, device))

    async def _do_save(self, pages: list[dict], name: str, tags, ocr: bool, device: str) -> None:
        languages = list(self.settings["ocr_languages"]) if ocr else None
        self.job["message"] = "Recognising the text" if ocr else "Making the PDF"

        def on_page(number: int, total: int) -> None:
            self.job["page"] = number
            self.job["pages"] = total
            self.job["percent"] = (number - 1) / total * 100

        doc = await asyncio.to_thread(
            self.archive.add,
            pages,
            [self.tray.path(p) for p in pages],
            name or self.suggested_name(),
            clean_tags(tags),
            languages,
            device,
            on_page,
        )
        self.tray.clear([p["id"] for p in pages])
        self.job["percent"] = 100.0
        if self.settings["share_auto"] and self.settings["share_folder"]:
            self.job["message"] = "Copying to the share"
            try:
                doc = await self.copy(doc["id"])
            except share.ShareError as e:
                # The document is safe in the archive; the copy can be done again from there
                self.job["warning"] = f"Saved, but not copied: {e}"
                doc = self.archive.get(doc["id"]) or doc
        self.job["doc"] = doc
        logger.info(f"Saved document {doc['id']}: {doc['name']} ({doc['pages']} pages)")

    async def copy(self, doc_id: int) -> dict:
        """Copy a document to the chosen folder. Raises ShareError; the reason is kept with the document."""
        doc = self.archive.get(doc_id)
        if doc is None:
            raise share.ShareError("No such document")
        relative = share.relative_path(
            self.settings["share_pattern"], share.tokens(doc["created"], doc["name"], doc["id"], doc["pages"])
        )
        try:
            target = await asyncio.to_thread(
                share.copy_document,
                self.archive.pdf(doc_id),
                self.settings["share_folder"],
                relative,
                DESTINATION_ROOTS,
            )
        except share.ShareError as e:
            self.archive.set_copied(doc_id, "", str(e))
            raise
        self.archive.set_copied(doc_id, target)
        logger.info(f"Copied document {doc_id} to {target}")
        return self.archive.get(doc_id) or doc

    async def share_state(self) -> dict:
        """Whether the chosen folder is there and can be written to. Looked at twice a minute at most."""
        at, state = self._share_state
        folder = self.settings["share_folder"]
        if state.get("folder") == folder and time.time() - at < 30:
            return state

        def look() -> dict:
            if not folder:
                return {"folder": "", "ok": False, "kind": "", "problem": ""}
            path = share.inside(Path(folder), DESTINATION_ROOTS)
            if path is None:
                return {"folder": folder, "ok": False, "kind": "", "problem": "Not in the share or media folder"}
            if not path.is_dir():
                return {"folder": folder, "ok": False, "kind": "", "problem": "The folder isn't there"}
            ok = os.access(path, os.W_OK)
            return {
                "folder": folder,
                "ok": ok,
                "kind": share.kind_of(path),
                "problem": "" if ok else "Cannot write there",
            }

        try:
            state = await asyncio.wait_for(asyncio.to_thread(look), 10)
        except TimeoutError:
            state = {"folder": folder, "ok": False, "kind": "", "problem": "The share doesn't answer"}
        self._share_state = (time.time(), state)
        return state

    def forget_share_state(self) -> None:
        self._share_state = (0.0, {})
