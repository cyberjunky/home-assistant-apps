"""The Garmin account: sign-in (with MFA), the saved tokens and every request to Garmin Connect.

python-garminconnect does the work. It is synchronous, so each request runs in a worker thread,
one at a time. The password is only used for the sign-in itself and never stored: what stays is
the token file python-garminconnect writes (owner-only) in its own folder under /data, apart from
the database, so a database download never holds the account's tokens. python-garminconnect also
refreshes the tokens itself and writes the new ones to that file.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from .config import DATA_DIR

logger = logging.getLogger(__name__)

TOKEN_DIR = DATA_DIR / "garmin"
TOKEN_FILE = "garmin_tokens.json"
# Garmin asks to slow down: leave it alone this long before the next request
RATE_LIMIT_PAUSE = 15 * 60
# An MFA code is only good for a few minutes; a sign-in waiting longer than this is dropped
MFA_TIMEOUT = 10 * 60


class NotSignedIn(Exception):
    """No Garmin account signed in (or Garmin no longer accepts the sign-in)."""


class RateLimited(Exception):
    """Garmin asked to slow down; requests wait until `until`."""

    def __init__(self, until: float):
        super().__init__("Garmin asked to slow down")
        self.until = until


class SignInError(Exception):
    """The sign-in failed; the message is meant for the dashboard."""


def _account_created(base: Any) -> str:
    """The day the Garmin account was made, from the base profile ('2019-12-30T13:28:55.000+0000')."""
    created = str((base or {}).get("createDate") or "")[:10] if isinstance(base, dict) else ""
    try:
        return date.fromisoformat(created).isoformat()
    except ValueError:
        return ""


class GarminAccount:
    """Holds the signed-in Garmin client and runs every request to Garmin Connect."""

    def __init__(self, token_dir: Path = TOKEN_DIR, factory: Callable[..., Garmin] = Garmin):
        self.token_dir = Path(token_dir)
        self._factory = factory
        self.api: Garmin | None = None
        self.profile: dict = {}
        self.last_error = ""
        self.blocked_until = 0.0
        self.requests = 0
        self.last_request_at = 0.0
        self._lock = asyncio.Lock()
        self._pending: tuple[Garmin, Any, float] | None = None
        # Set when the account signs in or out, so the collector reacts at once
        self.changed = asyncio.Event()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    @property
    def token_path(self) -> Path:
        return self.token_dir / TOKEN_FILE

    @property
    def signed_in(self) -> bool:
        return self.api is not None

    @property
    def has_tokens(self) -> bool:
        return self.token_path.is_file()

    @property
    def waiting_for_mfa(self) -> bool:
        return self._pending is not None and time.time() - self._pending[2] < MFA_TIMEOUT

    def status(self) -> dict:
        return {
            "signed_in": self.signed_in,
            "has_tokens": self.has_tokens,
            "mfa": self.waiting_for_mfa,
            "name": self.profile.get("full_name", ""),
            "units": self.profile.get("units", "metric"),
            "timezone": self.profile.get("timezone", ""),
            "created": self.profile.get("created", ""),
            "birth_date": self.profile.get("birth_date", ""),
            "error": self.last_error,
            "blocked_until": self.blocked_until if self.blocked_until > time.time() else None,
            "requests": self.requests,
            "last_request_at": self.last_request_at or None,
        }

    # ------------------------------------------------------------------
    # Signing in and out
    # ------------------------------------------------------------------
    async def resume(self) -> bool:
        """Sign in with the saved tokens. Returns whether that worked."""
        if not self.has_tokens:
            return False
        garmin = self._factory()
        try:
            await asyncio.to_thread(garmin.login, str(self.token_dir))
        except GarminConnectAuthenticationError as e:
            logger.warning(f"Garmin no longer accepts the saved sign-in: {e}")
            self.last_error = "Garmin no longer accepts the saved sign-in. Sign in again in Settings."
            return False
        except GarminConnectTooManyRequestsError:
            self.blocked_until = time.time() + RATE_LIMIT_PAUSE
            self.last_error = "Garmin asked to slow down; signing in again later"
            return False
        except (GarminConnectConnectionError, OSError) as e:
            logger.warning(f"Signing in to Garmin failed, trying again later: {e}")
            self.last_error = f"Garmin Connect can't be reached: {e}"
            return False
        await self._signed_in(garmin)
        return True

    async def login(self, email: str, password: str) -> str:
        """Start a sign-in. Returns "ok", or "mfa" when Garmin wants a code (see submit_mfa)."""
        email, password = email.strip(), password
        if not email or not password:
            raise SignInError("Enter your Garmin email address and password")
        garmin = self._factory(email=email, password=password, return_on_mfa=True)
        try:
            status, state = await asyncio.to_thread(garmin.login)
        except GarminConnectTooManyRequestsError as e:
            raise SignInError("Garmin blocks sign-ins for now (too many attempts). Wait a few minutes.") from e
        except GarminConnectAuthenticationError as e:
            raise SignInError("Garmin refused the email address or password") from e
        except (GarminConnectConnectionError, OSError) as e:
            raise SignInError(f"Garmin Connect can't be reached: {e}") from e
        if status == "needs_mfa":
            self._pending = (garmin, state, time.time())
            return "mfa"
        await self._keep_tokens(garmin)
        return "ok"

    async def submit_mfa(self, code: str) -> None:
        """Finish a sign-in that waits for the code Garmin sent by email or the authenticator app."""
        code = "".join(code.split())
        if not self.waiting_for_mfa or self._pending is None:
            self._pending = None
            raise SignInError("The sign-in expired; start again")
        if not code.isdigit():
            raise SignInError("The code is the number Garmin sent you")
        garmin, state, _ = self._pending
        try:
            await asyncio.to_thread(garmin.resume_login, state, code)
        except GarminConnectAuthenticationError as e:
            raise SignInError("Garmin did not accept the code. Check it and try again.") from e
        except GarminConnectTooManyRequestsError as e:
            self._pending = None
            raise SignInError("Garmin blocks sign-ins for now (too many attempts). Wait a few minutes.") from e
        except (GarminConnectConnectionError, OSError) as e:
            raise SignInError(f"Garmin Connect can't be reached: {e}") from e
        self._pending = None
        await self._keep_tokens(garmin)

    def cancel_login(self) -> None:
        self._pending = None

    async def _keep_tokens(self, garmin: Garmin) -> None:
        """Write the new tokens, then sign in from that file, so token refreshes are saved there too."""
        await asyncio.to_thread(garmin.client.dump, str(self.token_dir))
        if not await self.resume():
            raise SignInError(self.last_error or "Signing in with the new tokens failed")

    async def _signed_in(self, garmin: Garmin) -> None:
        self.api = garmin
        self.last_error = ""
        base: Any = {}
        with contextlib.suppress(Exception):
            base = await asyncio.to_thread(garmin.get_user_profile_base)
        timezone = ""
        with contextlib.suppress(Exception):
            timezone = await asyncio.to_thread(garmin.get_user_timezone) or ""
        units = garmin.get_unit_system() or "metric"
        self.profile = {
            "full_name": garmin.get_full_name() or "",
            "units": "imperial" if str(units).lower().startswith("statute") else "metric",
            "timezone": timezone,
            "created": _account_created(base),
            "birth_date": str((base or {}).get("birthDate") or "") if isinstance(base, dict) else "",
            "profile_id": getattr(garmin, "profile_id", None),
        }
        logger.info(
            f"Signed in to Garmin Connect{' as ' + self.profile['full_name'] if self.profile['full_name'] else ''}"
        )
        self.changed.set()

    def sign_out(self) -> None:
        """Forget the account: the token file goes, the recorded data stays."""
        self.api = None
        self._pending = None
        self.profile = {}
        self.last_error = ""
        with contextlib.suppress(FileNotFoundError):
            self.token_path.unlink()
        logger.info("Signed out of Garmin Connect")
        self.changed.set()

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------
    async def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """One request to Garmin Connect, by python-garminconnect method name.

        Returns None for data Garmin doesn't have (404). Raises NotSignedIn when Garmin
        rejects the sign-in, RateLimited when it asks to slow down.
        """
        async with self._lock:
            if self.api is None:
                raise NotSignedIn("Not signed in to Garmin Connect")
            if self.blocked_until > time.time():
                raise RateLimited(self.blocked_until)
            self.requests += 1
            self.last_request_at = time.time()
            try:
                return await asyncio.to_thread(getattr(self.api, method), *args, **kwargs)
            except GarminConnectNotFoundError:
                return None
            except GarminConnectTooManyRequestsError as e:
                self.blocked_until = time.time() + RATE_LIMIT_PAUSE
                logger.warning(f"Garmin asked to slow down; pausing for {RATE_LIMIT_PAUSE // 60} minutes")
                raise RateLimited(self.blocked_until) from e
            except GarminConnectAuthenticationError as e:
                logger.warning(f"Garmin rejected the sign-in: {e}")
                self.api = None
                self.last_error = "Garmin no longer accepts the sign-in. Sign in again in Settings."
                self.changed.set()
                raise NotSignedIn(self.last_error) from e
