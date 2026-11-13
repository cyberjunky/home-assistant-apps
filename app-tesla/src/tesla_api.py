"""Client for Tesla's owner API: the vehicles of the account and their data."""

import logging
import os
from datetime import UTC, datetime

import httpx

from .tesla_auth import AuthError, TeslaAuth, audience, new_client

logger = logging.getLogger(__name__)

API_HOST = "https://owner-api.teslamotors.com"
API_HOST_CN = "https://owner-api.vn.cloud.tesla.cn"
# The charging history (Supercharger sessions and what they cost) comes from Tesla's
# ownership service, the one the Tesla app reads, not from the owner API.
OWNERSHIP_HOST = "https://ownership.tesla.com"
CHARGING_HISTORY_PATH = "/mobile-app/charging/history"

# Everything vehicle_data can return; the location is only included when asked for
DATA_ENDPOINTS = (
    "charge_state;climate_state;closures_state;drive_state;gui_settings;"
    "location_data;vehicle_config;vehicle_state;vehicle_data_combo"
)


class TeslaApiError(Exception):
    """A request to the Tesla API failed."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class VehicleUnavailable(TeslaApiError):
    """The car is asleep or out of reach (HTTP 408): its data can't be read right now."""


class RateLimited(TeslaApiError):
    """Too many requests (HTTP 429): wait for retry_after seconds."""

    def __init__(self, retry_after: int):
        super().__init__(f"Rate limited by Tesla for {retry_after}s", 429)
        self.retry_after = retry_after


class OwnerApiClosed(TeslaApiError):
    """Tesla answers "forbidden, see developer.tesla.com/docs/fleet-api"."""

    def __init__(self, source: str, hosts: list[str]):
        valid_for = ", ".join(hosts) or "not stated"
        if source in ("browser", "app"):
            # The token came from a real browser (or the Tesla Auth app) and is still refused:
            # that points to the account, not to how this app connects
            how = "the app's browser" if source == "browser" else "the Tesla Auth app"
            message = (
                f"Tesla's owner API refuses this account (403, pointing to its Fleet API), even with a token from"
                f" {how}. That is the account, not this app: Tesla has closed the owner API for business and"
                f" fleet accounts, which must use its Fleet API. Token valid for: {valid_for}"
            )
        else:
            message = (
                "Tesla's owner API refuses the token this app's own client was given (403, pointing to its"
                f" Fleet API). Token valid for: {valid_for}. Sign out and sign in again with the dashboard's"
                " browser, which requests a token the owner API accepts"
            )
        super().__init__(message, 403)


class TeslaApi:
    """Reads vehicles from the owner API, signing requests with the account's access token."""

    def __init__(self, auth: TeslaAuth):
        self.auth = auth
        self._session: httpx.AsyncClient | None = None

    @property
    def host(self) -> str:
        override = os.environ.get("TESLA_API_HOST")
        if override:
            return override
        return API_HOST_CN if self.auth.region == "cn" else API_HOST

    async def _http(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = new_client()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.is_closed:
            await self._session.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        whole: bool = False,
        base: str | None = None,
        headers: dict | None = None,
    ) -> dict | list:
        """Call the API and return the "response" part of the answer (the whole answer with whole=True)."""
        client = await self._http()
        token = await self.auth.access_token()
        snippet = ""
        for attempt in (1, 2):
            request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
            try:
                resp = await client.request(
                    method, f"{base or self.host}{path}", params=params, headers=request_headers
                )
            except (httpx.HTTPError, TimeoutError) as e:
                raise TeslaApiError(f"Tesla API unreachable: {type(e).__name__}: {e}") from e
            status = resp.status_code
            retry_after = resp.headers.get("Retry-After", "")
            try:
                body = resp.json()
            except ValueError:
                body = None
            # A short bit of the raw answer, so an unexpected one can be diagnosed from the log
            snippet = (resp.text or "")[:200].replace("\n", " ").strip()

            if status == 401 and attempt == 1:
                # The token was revoked or expired early: renew it once and retry
                token = await self.auth.access_token(force_refresh=True)
                continue
            break

        error = body.get("error") if isinstance(body, dict) else None
        if status == 401:
            raise AuthError("Tesla rejected the access token")
        if status == 408:
            raise VehicleUnavailable(str(error or "vehicle unavailable"), status)
        if status == 429:
            try:
                wait = int(float(retry_after))
            except ValueError:
                wait = 60
            raise RateLimited(max(wait, 5))
        if status == 200 and whole and isinstance(body, dict | list):
            return body
        if status == 403 and "fleet-api" in str(error):
            raise OwnerApiClosed(self.auth.token_source, audience(token))
        if status != 200 or not isinstance(body, dict) or "response" not in body:
            detail = error or snippet or "unexpected answer"
            raise TeslaApiError(f"Tesla API error {status} for {path}: {detail}", status)
        return body["response"]

    async def vehicles(self) -> list[dict]:
        """The vehicles of the account (the product list also holds Powerwalls and the like)."""
        products = await self._request("GET", "/api/1/products")
        return [p for p in products if isinstance(p, dict) and p.get("vin") and p.get("id")]

    async def vehicle(self, tesla_id: int) -> dict:
        """A vehicle's summary, including its state (online, asleep, offline). Doesn't keep the car awake."""
        return await self._request("GET", f"/api/1/vehicles/{tesla_id}")

    async def vehicle_data(self, tesla_id: int) -> dict:
        """All live data of a vehicle. Raises VehicleUnavailable when it is asleep or offline."""
        return await self._request("GET", f"/api/1/vehicles/{tesla_id}/vehicle_data", {"endpoints": DATA_ENDPOINTS})

    async def wake_up(self, tesla_id: int) -> dict:
        """Ask a sleeping vehicle to wake up; it takes up to a minute before it is online."""
        return await self._request("POST", f"/api/1/vehicles/{tesla_id}/wake_up")

    async def charging_history(self, vin: str, max_pages: int = 60, page_size: int = 50) -> dict:
        """The account's sessions at Tesla's own chargers with what was billed, newest first.

        An account endpoint on the ownership service (what the Tesla app reads); it
        doesn't touch the car. Asks for the widest date range first (the endpoint
        seems to default to a recent window otherwise); if Tesla rejects the date
        parameters, it asks again without them. Pages through all sessions and
        returns them combined under "data"; billing.py picks the sessions out.
        """
        # A wide window, so it isn't limited to Tesla's default recent span
        start = datetime(2015, 1, 1, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for date_range in ({"startTime": start, "endTime": end}, {}):
            try:
                return await self._charging_history(vin, max_pages, page_size, date_range)
            except TeslaApiError as e:
                # The date parameters may be unsupported: fall back to asking without them
                if date_range and 400 <= e.status < 500:
                    logger.debug(f"Charging history rejected the date range ({e.status}); retrying without it")
                    continue
                raise
        return {"data": []}

    async def _charging_history(self, vin: str, max_pages: int, page_size: int, extra: dict) -> dict:
        from .billing import find_sessions  # local import to avoid a cycle at module load

        base = os.environ.get("TESLA_OWNERSHIP_HOST", OWNERSHIP_HOST)
        all_sessions: list = []
        seen: set = set()
        for page in range(1, max_pages + 1):
            params = {
                "deviceLanguage": "en",
                "deviceCountry": "US",
                "operationName": "getChargingHistoryV2",
                "vin": vin,
                "pageNo": page,
                "pageSize": page_size,
                "sortBy": "startDateTime",
                "sortOrder": "DESC",
                **extra,
            }
            answer = await self._request(
                "GET", CHARGING_HISTORY_PATH, params, whole=True, base=base, headers={"Accept": "application/json"}
            )
            sessions = find_sessions(answer)
            if sessions is None:
                # Unexpected layout: hand the raw answer back so billing can log and report it
                return answer if page == 1 else {"data": all_sessions}
            # Stop when a page is empty or only repeats earlier ones (Tesla ignoring the page number)
            new = [s for s in sessions if s.get("sessionId") not in seen]
            if not new:
                break
            seen.update(s.get("sessionId") for s in new)
            all_sessions.extend(new)
            if len(sessions) < page_size:
                break
        return {"data": all_sessions}
