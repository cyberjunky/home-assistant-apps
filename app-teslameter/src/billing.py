"""What Tesla billed for charging at its own chargers.

The car doesn't know what a session cost, but the Tesla account keeps a
charging history with the amounts (the one the Tesla app shows). After a
fast-charge session its entry is looked up there, by car and time, and the
billed amount becomes the session's cost. Sessions at other networks and at
home aren't in that history; they keep the cost calculated from the price per
kWh.
"""

import asyncio
import logging
import os
import time
from datetime import datetime

from .config import DATA_DIR, Config
from .database import Database
from .tesla_api import RateLimited, TeslaApi, TeslaApiError
from .tesla_auth import AuthError, TeslaAuth

logger = logging.getLogger(__name__)

# How often to look whether a session is waiting for its bill (no request is made when none is)
CHECK_INTERVAL = int(os.environ.get("TESLAMETER_BILLING_INTERVAL", "300"))
# Tesla needs a while to bill a session: ask every half hour at first, then a few times a day
RETRY_FRESH = 30 * 60
RETRY_OLDER = 6 * 3600
FRESH_FOR = 6 * 3600
# After this long a session without a bill was not at a Tesla charger (or was free)
GIVE_UP_AFTER = 3 * 86400
# When the history can't be read at all, try again this much later
UNAVAILABLE_RETRY = 12 * 3600
# A history entry belongs to a session when they overlap, or start this close to each other
START_TOLERANCE = 15 * 60


def find_sessions(answer) -> list[dict] | None:
    """The list of sessions inside Tesla's answer, None when there is no such list.

    The list sits under "data", sometimes wrapped in "response".
    """
    for _ in range(4):
        if isinstance(answer, list):
            return [entry for entry in answer if isinstance(entry, dict)]
        if not isinstance(answer, dict):
            return None
        answer = next((answer[key] for key in ("response", "data", "sessions") if key in answer), None)
    return None


def _timestamp(value) -> int | None:
    """An ISO 8601 time ("2026-07-27T11:43:45+02:00") as unix seconds."""
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def billed(entry: dict) -> tuple[float, str] | None:
    """(amount, currency) billed for a history entry: charging plus any idle or parking fee."""
    total = 0.0
    currencies = set()
    fees = [fee for fee in entry.get("fees") or [] if isinstance(fee, dict)]
    for fee in fees:
        try:
            total += float(fee.get("totalDue") or 0)
        except (TypeError, ValueError):
            return None
        if fee.get("currencyCode"):
            currencies.add(str(fee["currencyCode"]).upper())
    # No fees at all, or fees in two currencies: not something to put a number on
    if not fees or len(currencies) != 1:
        return None
    return round(total, 2), currencies.pop()


def _coord(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if -90.0 <= number <= 180.0 and number != 0 else None


def session_location(entry: dict) -> tuple[float | None, float | None]:
    """(lat, lon) of a history entry's charging site, where Tesla gives it; (None, None) otherwise."""
    for key in ("siteLatLng", "siteAddress", "siteEntryLocation", "location"):
        place = entry.get(key)
        if isinstance(place, dict):
            lat = _coord(place.get("latitude") or place.get("lat"))
            lon = _coord(place.get("longitude") or place.get("lng") or place.get("lon"))
            if lat is not None and lon is not None:
                return lat, lon
    return None, None


def session_energy(entry: dict) -> float | None:
    """The kWh delivered in a history entry, from its charging fee."""
    for fee in entry.get("fees") or []:
        if isinstance(fee, dict) and str(fee.get("uom", "")).lower() == "kwh" and fee.get("usageBase") is not None:
            try:
                return round(float(fee["usageBase"]), 3)
            except (TypeError, ValueError):
                return None
    return None


def sessions_of_car(sessions: list[dict], vin: str) -> list[dict]:
    """The sessions that belong to this car. Tesla can answer with the whole account's history, whichever
    VIN was asked for, so each session's own VIN decides. Without one there is nothing to tell them apart."""
    return [entry for entry in sessions if not entry.get("vin") or entry["vin"] == vin]


def session_to_charge(entry: dict, currency: str) -> dict | None:
    """A history entry as an importable charge record, or None when it has no usable start/energy."""
    start = _timestamp(entry.get("chargeStartDateTime"))
    energy = session_energy(entry)
    if start is None or energy is None:
        return None
    stop = _timestamp(entry.get("chargeStopDateTime")) or _timestamp(entry.get("unlatchDateTime")) or start
    bill = billed(entry)
    lat, lon = session_location(entry)
    dc = str(entry.get("chargerType", "")).upper() != "AC"
    return {
        "start_ts": start,
        "end_ts": max(stop, start),
        "duration_s": max(0, max(stop, start) - start),
        "lat": lat,
        "lon": lon,
        "place": entry.get("siteLocalizedName") or entry.get("siteLocationName"),
        "kind": "DC" if dc else "AC",
        "charger_brand": entry.get("brandName") or "Tesla",
        "energy_added_kwh": energy,
        "energy_used_kwh": energy,
        "cost": bill[0] if bill and bill[1] == currency.upper() else None,
        "billed_amount": bill[0] if bill else None,
        "billed_currency": bill[1] if bill else None,
    }


def match(charge: dict, sessions: list[dict]) -> dict | None:
    """The history entry of a recorded charge: the one that overlaps it most, or starts nearest."""
    best, best_score = None, None
    for entry in sessions:
        start = _timestamp(entry.get("chargeStartDateTime"))
        if start is None:
            continue
        stop = _timestamp(entry.get("chargeStopDateTime")) or start
        overlap = min(stop, charge["end_ts"]) - max(start, charge["start_ts"])
        apart = abs(start - charge["start_ts"])
        if overlap <= 0 and apart > START_TOLERANCE:
            continue
        score = (max(overlap, 0), -apart)
        if best_score is None or score > best_score:
            best, best_score = entry, score
    return best


def describe(answer) -> str:
    """The shape of an answer, for the log: enough to adapt to it, without ids or the VIN."""
    sessions = find_sessions(answer)
    if sessions is None:
        keys = sorted(answer) if isinstance(answer, dict) else type(answer).__name__
        return f"no session list found; got {keys}"
    if not sessions:
        return "0 sessions"
    newest = sessions[0]
    fees = [
        {key: fee.get(key) for key in ("feeType", "totalDue", "currencyCode", "uom", "usageBase", "pricingType")}
        for fee in newest.get("fees") or []
        if isinstance(fee, dict)
    ]
    return (
        f"{len(sessions)} sessions; newest: start {newest.get('chargeStartDateTime')},"
        f" stop {newest.get('chargeStopDateTime')}, at {newest.get('siteLocationName')!r}, fees {fees};"
        f" fields {sorted(newest)}"
    )


class ChargingCosts:
    """Fills in the billed cost of fast-charge sessions from the Tesla account's charging history."""

    def __init__(self, config: Config, database: Database, auth: TeslaAuth, api: TeslaApi):
        self.config = config
        self.database = database
        self.auth = auth
        self.api = api
        # "off", "waiting" (not signed in or not tried yet), "ok" or "unavailable"
        self.state = "waiting" if config.supercharger_costs else "off"
        self.detail = ""
        self._last_try: dict[int, float] = {}
        self._described = False
        self._retry_at = 0.0
        # Written once the past sessions have been imported, so it only happens on the first run
        self._import_marker = DATA_DIR / "charging_history_imported"
        self._import_lock = asyncio.Lock()

    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail}

    async def run(self) -> None:
        """Run until cancelled."""
        if not self.config.supercharger_costs:
            return
        # Let the vehicle monitors find the cars first
        await asyncio.sleep(min(30, CHECK_INTERVAL))
        # First run: bring in the charging sessions Tesla already has on record
        if not self._import_marker.exists() and self.auth.signed_in:
            try:
                count = await self.import_history()
                self._import_marker.write_text(str(int(time.time())))
                logger.info(f"Imported {count} past charging session(s) from Tesla's history")
            except AuthError:
                pass
            except (TeslaApiError, OSError) as e:
                logger.warning(f"Could not import past charging sessions: {e}")
        while True:
            try:
                await self._check()
            except AuthError:
                self.state, self.detail = "waiting", "not signed in"
            except RateLimited as e:
                self._retry_at = time.monotonic() + e.retry_after
            except TeslaApiError as e:
                # Most likely the account can't read this history; don't keep asking
                if self.state != "unavailable":
                    logger.warning(f"Tesla charging history can't be read, using the price per kWh instead: {e}")
                self.state, self.detail = "unavailable", str(e)
                self._retry_at = time.monotonic() + UNAVAILABLE_RETRY
            except Exception:
                logger.exception("Charging cost lookup failed")
                self._retry_at = time.monotonic() + UNAVAILABLE_RETRY
            await asyncio.sleep(CHECK_INTERVAL)

    async def _check(self) -> None:
        if not self.auth.signed_in:
            self.state, self.detail = "waiting", "not signed in"
            return
        if time.monotonic() < self._retry_at:
            return
        now = time.time()
        waiting = [
            charge for charge in self.database.unbilled_fast_charges(int(now - GIVE_UP_AFTER)) if self._due(charge, now)
        ]
        by_vin: dict[str, list[dict]] = {}
        for charge in waiting:
            by_vin.setdefault(charge["vin"], []).append(charge)
        if not by_vin and self.state != "waiting":
            return
        # Nothing waiting but never tried: ask once, so Settings (and the log) can tell whether this works
        if not by_vin:
            cars = [car["vin"] for car in self.database.list_cars() if car.get("vin")]
            if not cars:
                return
            by_vin = {cars[0]: []}

        for vin, charges in by_vin.items():
            answer = await self.api.charging_history(vin)
            sessions = find_sessions(answer)
            if not self._described:
                # Once per start: what Tesla sent, so a change on their side shows in the log
                logger.info(f"Tesla charging history: {describe(answer)}")
                self._described = True
            if sessions is None:
                raise TeslaApiError("Tesla's charging history has an unexpected layout (see the add-on log)")
            self.state, self.detail = "ok", f"{len(sessions)} sessions in your Tesla charging history"
            for charge in charges:
                self._last_try[charge["id"]] = now
                self._apply(charge, match(charge, sessions_of_car(sessions, vin)))

    async def import_history(self) -> int:
        """Add the account's charging sessions from Tesla's history as charges. Returns how many were added.

        Sessions already recorded (matched by start time) are skipped; a recorded
        session that matches one here gets its billed cost filled in instead.
        Safe to run again: it never duplicates.
        """
        async with self._import_lock:
            imported = 0
            currency = self.config.currency
            # A session without a VIN of its own goes to the first car that gets it, not to all of them
            claimed: set = set()
            for car in self.database.list_cars():
                vin = car.get("vin")
                if not vin:
                    continue
                answer = await self.api.charging_history(vin)
                sessions = find_sessions(answer)
                if not self._described:
                    logger.info(f"Tesla charging history: {describe(answer)}")
                    self._described = True
                if sessions is None:
                    raise TeslaApiError("Tesla's charging history has an unexpected layout (see the add-on log)")
                self.state, self.detail = "ok", f"{len(sessions)} sessions in your Tesla charging history"
                mine = sessions_of_car(sessions, vin)
                logger.info(f"Tesla charging history: {len(mine)} of {len(sessions)} sessions belong to this car")
                for entry in mine:
                    session_id = entry.get("sessionId")
                    if session_id is not None:
                        if session_id in claimed:
                            continue
                        claimed.add(session_id)
                    charge = session_to_charge(entry, currency)
                    if not charge:
                        continue
                    existing = self.database.charge_near(car["id"], charge["start_ts"])
                    if existing is None:
                        if self.database.import_charge(car["id"], charge):
                            imported += 1
                    elif charge["billed_amount"] is not None:
                        self.database.set_billed(
                            existing,
                            charge["billed_amount"],
                            charge["billed_currency"],
                            as_cost=charge["cost"] is not None,
                        )
            return imported

    def _due(self, charge: dict, now: float) -> bool:
        """Whether it is time to look for this session's bill (again)."""
        last = self._last_try.get(charge["id"])
        if last is None:
            return True
        return now - last >= (RETRY_FRESH if now - charge["end_ts"] < FRESH_FOR else RETRY_OLDER)

    def _apply(self, charge: dict, entry: dict | None) -> None:
        if entry is None:
            logger.debug(f"Charge {charge['id']}: not in Tesla's charging history (yet)")
            return
        bill = billed(entry)
        if bill is None:
            logger.info(f"Charge {charge['id']}: found in Tesla's charging history, but without a usable amount")
            return
        amount, currency = bill
        # An amount in another currency (charging abroad) is kept, but can't be the cost in the totals
        same_currency = currency == self.config.currency.upper()
        self.database.set_billed(charge["id"], amount, currency, as_cost=same_currency)
        note = "" if same_currency else f" (kept beside the estimated cost, which is in {self.config.currency})"
        logger.info(f"Charge {charge['id']}: Tesla billed {currency} {amount:.2f}{note}")
