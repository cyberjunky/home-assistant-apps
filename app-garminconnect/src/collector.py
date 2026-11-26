"""Fetches from Garmin Connect: the latest data on every update, and the whole history in the background.

Like the Toon app's history import, the history goes in small pieces with a pause in between and
continues where it was after a restart. It walks back from today, so the recent past shows first,
and takes turns between the data sets, so every chart fills at the same time instead of one after
the other. Updates of the latest data go first: an import of years never holds up today's numbers.
"""

import asyncio
import contextlib
import logging
import time
from datetime import date, timedelta

from garminconnect import GarminConnectConnectionError

from .account import GarminAccount, NotSignedIn, RateLimited
from .config import Config
from .database import Database
from .datasets import DATASETS, Dataset, chunks_back, decode_polyline, iso, thin

logger = logging.getLogger(__name__)

PAUSE_BETWEEN_REQUESTS = 2.0
# Without a known start, the history goes back this far
DEFAULT_HISTORY_YEARS = 10
# Network trouble: wait this long before trying again
RETRY_AFTER = 5 * 60
# Tracks fetched per turn of the history import
TRACKS_PER_TURN = 5
# Activity details: three or four requests per activity, so fewer per turn
DETAILS_PER_TURN = 2
MAX_TRACK_POINTS = 800


def _is_refusal(error: Exception) -> bool:
    """A 4xx answer: Garmin has nothing for that request (no such data, out of range), not a network fault."""
    text = str(error)
    return "client error" in text or "(400)" in text or "(403)" in text


class Collector:
    def __init__(self, config: Config, database: Database, account: GarminAccount):
        self.config = config
        self.db = database
        self.account = account
        self.phase = "idle"
        self.current = ""
        self.next_update_at = 0.0
        self.last_update_at = 0.0
        self.last_error = ""
        self._wake = asyncio.Event()
        self._turn = 0

    # ------------------------------------------------------------------
    # Which data sets, and from when
    # ------------------------------------------------------------------
    def enabled(self) -> list[Dataset]:
        return [d for d in DATASETS if self.config.enabled(d.group)]

    def history_start(self) -> date:
        for text in (self.config.start_date, self.account.profile.get("created", "")):
            with contextlib.suppress(ValueError):
                if text:
                    return date.fromisoformat(text)
        today = date.today()
        return today.replace(year=today.year - DEFAULT_HISTORY_YEARS)

    def status(self) -> dict:
        progress = self.db.all_progress()
        start = self.history_start()
        today = date.today()
        total_days = max(1, (today - start).days + 1)
        sets = []
        for dataset in DATASETS:
            row = progress.get(dataset.key, {})
            oldest = row.get("oldest")
            covered = None
            if dataset.kind != "snapshot":
                covered = 1.0 if row.get("done") else 0.0
                if oldest and not row.get("done"):
                    with contextlib.suppress(ValueError):
                        covered = min(1.0, ((today - date.fromisoformat(oldest)).days + 1) / total_days)
            sets.append(
                {
                    "key": dataset.key,
                    "group": dataset.group,
                    "title": dataset.title,
                    "kind": dataset.kind,
                    "enabled": self.config.enabled(dataset.group),
                    "oldest": oldest,
                    "done": bool(row.get("done")),
                    "covered": covered,
                    "last_fetch": row.get("last_fetch"),
                    "last_error": row.get("last_error"),
                    "rows": row.get("rows") or 0,
                    "newest": row.get("newest"),
                }
            )
        tracks_missing = self.db.scalar(
            "SELECT COUNT(*) FROM activities a LEFT JOIN tracks t ON t.activity_id = a.id"
            " WHERE a.has_track = 1 AND t.activity_id IS NULL"
        )
        return {
            "phase": self.phase,
            "current": self.current,
            "history_start": iso(start),
            "import_history": self.config.import_history,
            "next_update_at": self.next_update_at or None,
            "last_update_at": self.last_update_at or None,
            "update_interval_minutes": self.config.update_interval_minutes,
            "error": self.last_error,
            "datasets": sets,
            "tracks": {"enabled": self.config.enabled("activity_tracks"), "missing": tracks_missing or 0},
            "details": {
                "enabled": self.config.enabled("activity_details"),
                "missing": len(self.db.activities_without_details(100000)),
            },
        }

    def wake(self) -> None:
        """Run an update now (the dashboard's refresh button)."""
        self.next_update_at = 0
        self._wake.set()

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        if self.account.has_tokens:
            await self.account.resume()
        while True:
            if not self.account.signed_in:
                self.phase = "signed out"
                await self._wait_for_sign_in()
                continue
            try:
                if time.time() >= self.next_update_at:
                    await self.update()
                elif not await self.history_turn():
                    self.phase = "idle"
                    self.current = ""
                    await self._sleep_until(self.next_update_at)
            except RateLimited as e:
                self.phase = "paused"
                self.last_error = "Garmin asked to slow down; continuing later"
                await self._sleep_until(e.until)
            except NotSignedIn as e:
                self.last_error = str(e)
            except (GarminConnectConnectionError, OSError, TimeoutError) as e:
                logger.warning(f"Garmin Connect can't be reached, trying again in {RETRY_AFTER // 60} minutes: {e}")
                self.phase = "waiting"
                self.last_error = f"Garmin Connect can't be reached: {e}"
                await self._sleep_until(time.time() + RETRY_AFTER)

    async def _wait_for_sign_in(self) -> None:
        """Wait for a sign-in from the dashboard; with saved tokens, keep trying them now and then."""
        self.account.changed.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.account.changed.wait(), timeout=RETRY_AFTER)
        if not self.account.signed_in and self.account.has_tokens and not self.account.waiting_for_mfa:
            await self.account.resume()
        if self.account.signed_in:
            self.next_update_at = 0

    async def _sleep_until(self, moment: float) -> None:
        self._wake.clear()
        self.account.changed.clear()
        delay = max(1.0, moment - time.time())
        waiters = [asyncio.create_task(self._wake.wait()), asyncio.create_task(self.account.changed.wait())]
        try:
            await asyncio.wait(waiters, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()

    # ------------------------------------------------------------------
    # Updates: the last days of every data set
    # ------------------------------------------------------------------
    async def update(self) -> None:
        self.phase = "updating"
        today = date.today()
        errors = 0
        for dataset in self.enabled():
            self.current = dataset.title
            try:
                if dataset.kind == "snapshot":
                    if self.db.snapshot_age(dataset.key) >= dataset.every_hours * 3600 or self.next_update_at == 0:
                        await self._fetch(dataset, today, today)
                elif dataset.kind == "day":
                    # Oldest first, so the days join on to what the set already has
                    for back in reversed(range(dataset.recent_days)):
                        await self._fetch(dataset, today - timedelta(days=back), today - timedelta(days=back))
                else:
                    # From where the data set stopped (the app was off, or Garmin couldn't be reached),
                    # including its last days then, which Garmin may have filled in since
                    start = today - timedelta(days=dataset.recent_days - 1)
                    newest = self.db.newest(dataset.key)
                    if newest:
                        start = min(start, date.fromisoformat(newest) - timedelta(days=dataset.recent_days - 1))
                    start = max(start, self.history_start())
                    while start <= today:
                        end = min(start + timedelta(days=dataset.chunk_days - 1), today)
                        try:
                            await self._fetch(dataset, start, end)
                        except GarminConnectConnectionError as e:
                            if not _is_refusal(e):
                                raise
                            # Garmin has nothing for this stretch: don't ask for it on every update
                            errors += 1
                            self.db.set_newest(dataset.key, max(self.db.newest(dataset.key) or "", iso(end)))
                            self.db.commit()
                        start = end + timedelta(days=1)
            except GarminConnectConnectionError as e:
                if not _is_refusal(e):
                    raise
                errors += 1
        if self.config.enabled("activity_tracks"):
            await self._fetch_tracks(TRACKS_PER_TURN)
        if self.config.enabled("activity_details"):
            await self._fetch_details(DETAILS_PER_TURN)
        self.last_update_at = time.time()
        self.next_update_at = self.last_update_at + self.config.update_interval_minutes * 60
        self.last_error = "" if not errors else f"{errors} data set(s) refused by Garmin on the last update"
        self.current = ""
        logger.debug(f"Update done, {self.account.requests} requests so far")

    async def _fetch(self, dataset: Dataset, start: date, end: date, history: bool = False) -> int:
        try:
            data = await dataset.fetch(self.account, start, end)
        except GarminConnectConnectionError as e:
            if _is_refusal(e):
                logger.debug(f"{dataset.title} {start}..{end}: Garmin refused: {e}")
                self.db.set_progress(dataset.key, last_error=str(e)[:300])
                self.db.commit()
            raise
        rows = 0
        if data is not None:
            key = "now" if dataset.kind == "snapshot" else f"{iso(start)}_{iso(end)}"
            if dataset.keep_raw:
                self.db.put_raw(dataset.key, key, data)
            rows = dataset.parse(self.db, data, start, end)
        progress = self.db.progress(dataset.key)
        newest = self.db.newest(dataset.key)
        if dataset.kind != "snapshot" and (newest is None or start <= date.fromisoformat(newest) + timedelta(days=1)):
            # Only a piece that joins on moves "complete up to" forward: a jump ahead leaves a gap to fill
            self.db.set_newest(dataset.key, max(newest or "", iso(end)))
        self.db.set_progress(
            dataset.key,
            last_fetch=int(time.time()),
            last_error=None,
            rows=(progress["rows"] or 0) + (rows if history else 0),
        )
        self.db.commit()
        return rows

    # ------------------------------------------------------------------
    # History: one piece of one data set per turn
    # ------------------------------------------------------------------
    def _history_pending(self) -> list[Dataset]:
        if not self.config.import_history:
            return []
        return [d for d in self.enabled() if d.kind != "snapshot" and not self.db.progress(d.key)["done"]]

    def _behind(self) -> list[Dataset]:
        """Day-by-day sets with a gap between what they have and the last days the update fetches."""
        today = date.today()
        behind = []
        for dataset in self.enabled():
            if dataset.kind != "day":
                continue
            newest = self.db.newest(dataset.key)
            if newest and date.fromisoformat(newest) < today - timedelta(days=dataset.recent_days):
                behind.append(dataset)
        return behind

    async def _catch_up(self, dataset: Dataset) -> None:
        """The day after the last one the set has in full."""
        day = date.fromisoformat(self.db.newest(dataset.key)) + timedelta(days=1)
        self.current = f"{dataset.title} (catching up)"
        try:
            await self._fetch(dataset, day, day)
        except GarminConnectConnectionError as e:
            if not _is_refusal(e):
                raise
            # Garmin has nothing for that day: count it as done
            self.db.set_newest(dataset.key, iso(day))
            self.db.commit()

    async def history_turn(self) -> bool:
        """Fetch the next piece of history. Returns False when there is nothing left to fetch."""
        behind = self._behind()
        if behind:
            # A gap in the recent past goes before the far past
            self.phase = "catching up"
            await self._catch_up(behind[self._turn % len(behind)])
            self._turn += 1
            await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)
            return True
        pending = self._history_pending()
        tracks_due = self.config.enabled("activity_tracks") and bool(self.db.activities_without_track(1))
        details_due = self.config.enabled("activity_details") and bool(self.db.activities_without_details(1))
        if not pending and not tracks_due and not details_due:
            return False
        self.phase = "importing history"
        self._turn += 1
        # Every few turns the activities get their tracks and details, newest first
        backlog_turn = not pending or self._turn % 4 in (0, 2)
        if backlog_turn and tracks_due and (self._turn % 4 == 0 or not details_due):
            self.current = "Activity tracks"
            await self._fetch_tracks(TRACKS_PER_TURN, pause=True)
            return True
        if backlog_turn and details_due:
            self.current = "Activity details"
            await self._fetch_details(DETAILS_PER_TURN, pause=True)
            return True
        # The day curves need a request per day: they get a turn only every other round
        quick = [d for d in pending if d.kind != "day"]
        candidates = quick if quick and self._turn % 2 else pending
        dataset = candidates[self._turn // 2 % len(candidates)]
        await self._history_piece(dataset)
        await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)
        return True

    async def _history_piece(self, dataset: Dataset) -> None:
        self.current = dataset.title
        first = self.history_start()
        progress = self.db.progress(dataset.key)
        newest = date.fromisoformat(progress["oldest"]) - timedelta(days=1) if progress["oldest"] else date.today()
        if newest < first:
            self.db.set_progress(dataset.key, done=1)
            self.db.commit()
            return
        size = 1 if dataset.kind == "day" else dataset.chunk_days
        start, end = chunks_back(newest, first, size)
        try:
            await self._fetch(dataset, start, end, history=True)
        except GarminConnectConnectionError as e:
            # Garmin has nothing for this stretch (or not this far back): go on with the one before it
            if not _is_refusal(e):
                raise
        self.db.set_progress(dataset.key, oldest=iso(start), done=1 if start <= first else 0)
        self.db.commit()
        if start <= first:
            logger.info(f"History of {dataset.title} complete, back to {iso(first)}")

    # ------------------------------------------------------------------
    # Activity tracks
    # ------------------------------------------------------------------
    async def _fetch_tracks(self, limit: int, pause: bool = False) -> None:
        for activity_id in self.db.activities_without_track(limit):
            try:
                answer = await self.account.call("get_activity_polyline", activity_id)
            except GarminConnectConnectionError as e:
                if not _is_refusal(e):
                    raise
                answer = None
            encoded = (
                ((answer or {}).get("gPolyline") or {}).get("encodedSamples") if isinstance(answer, dict) else None
            )
            points = decode_polyline(encoded) if encoded else []
            # Stored even when empty, so an activity without a usable track isn't asked for again
            self.db.put_track(activity_id, thin(points, MAX_TRACK_POINTS))
            self.db.commit()
            if pause:
                await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)

    async def _fetch_details(self, limit: int, pause: bool = False) -> None:
        """Laps, the samples through the activity, weather and (for strength training) the sets."""
        for activity_id in self.db.activities_without_details(limit):
            kind = self.db.scalar("SELECT type FROM activities WHERE id = ?", (activity_id,)) or ""
            calls = [
                ("splits", "get_activity_splits", (activity_id,)),
                ("details", "get_activity_details", (activity_id, 2000, 0)),
                ("weather", "get_activity_weather", (activity_id,)),
            ]
            if any(word in kind for word in ("strength", "fitness_equipment", "hiit", "training")):
                calls.append(("sets", "get_activity_exercise_sets", (activity_id,)))
            answers = {}
            for name, method, args in calls:
                try:
                    answers[name] = await self.account.call(method, *args)
                except GarminConnectConnectionError as e:
                    if not _is_refusal(e):
                        raise
                    answers[name] = None
                if pause:
                    await asyncio.sleep(PAUSE_BETWEEN_REQUESTS / 2)
            # Stored even when Garmin had nothing, so it isn't asked for again
            self.db.put_activity_details(activity_id, answers)
            self.db.commit()

    # ------------------------------------------------------------------
    # Starting over
    # ------------------------------------------------------------------
    def reparse(self) -> int:
        """Read every kept answer again (after an update of the app reads more out of them)."""
        rows = 0
        for dataset in DATASETS:
            for key, data in self.db.raw_items(dataset.key):
                try:
                    if key == "now":
                        start = end = date.today()
                    else:
                        start, end = (date.fromisoformat(part) for part in key.split("_"))
                    rows += dataset.parse(self.db, data, start, end)
                except (ValueError, TypeError, AttributeError) as e:
                    logger.debug(f"Reading {dataset.key}/{key} again failed: {e}")
        self.db.commit()
        return rows
