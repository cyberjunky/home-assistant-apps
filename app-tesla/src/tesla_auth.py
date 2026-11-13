"""Tesla account sign-in and token upkeep.

Signing in is a one-time step from the dashboard: Tesla's own login page is
opened in the app's sign-in browser (signin_browser.py), where the user
signs in. Tesla ends that by sending the browser to an app link carrying a
one-time code, which is exchanged here for an access token and a refresh token
(OAuth 2 authorization code flow with PKCE). From then on the tokens renew
themselves. A refresh token made elsewhere (the Tesla Auth app) works too.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import ssl
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .config import ADDON_VERSION, DATA_DIR

logger = logging.getLogger(__name__)

TOKEN_FILE = "tokens.json"

# Tesla's single sign-on service. The client id and redirect address are the
# ones of Tesla's own app; third-party tools cannot register their own for the
# owner API, so every such tool signs in this way.
AUTH_HOST = os.environ.get("TESLA_AUTH_HOST", "https://auth.tesla.com")
AUTH_HOST_CN = "https://auth.tesla.cn"
AUTH_PATH = "/oauth2/v3"
CLIENT_ID = "ownerapi"
# Since April 2026 Tesla ends the sign-in with a redirect to this app link (it used to be a
# page on auth.tesla.com). A browser has nothing to open it with; the code is in its address.
REDIRECT_URI = "tesla://auth/callback"
SCOPE = "openid email offline_access"

# Renew the access token when it has less than this left
REFRESH_MARGIN = 15 * 60
# A started sign-in can be completed for this long
LOGIN_TIMEOUT = 30 * 60

# Kept from the app's old name: "Tesla/..." would look like Tesla's own software to its sign-on service.
USER_AGENT = f"TeslaMeter/{ADDON_VERSION}"


# The whole output of the Tesla Auth app: each token under its banner
_ACCESS_BANNER_RE = re.compile(r"ACCESS TOKEN[\s-]*([A-Za-z0-9._~+/=-]{20,})")
_REFRESH_BANNER_RE = re.compile(r"REFRESH TOKEN[\s-]*([A-Za-z0-9._~+/=-]{20,})")


def new_client() -> httpx.AsyncClient:
    """The app's own HTTP client for Tesla's owner API (and for the token endpoint in development).

    HTTP/2 over TLS 1.3, with browser-like Accept headers.
    Note: Tesla's sign-on service judges a client by its TLS/HTTP handshake and
    gives a plain client tokens its owner API refuses, which is why tokens are
    requested through the app's real browser (see signin_browser); this client
    makes the owner-API calls, which accept any valid token.
    """
    tls = httpx.create_ssl_context()
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    return httpx.AsyncClient(http2=True, verify=tls, headers=headers, timeout=30)


def audience(access_token: str) -> list[str]:
    """The services an access token says it is for (host names), e.g. ["owner-api.teslamotors.com"]."""
    claim = _jwt_claims(access_token).get("aud")
    values = [claim] if isinstance(claim, str) else [a for a in claim or [] if isinstance(a, str)]
    return [urlsplit(value).netloc or value for value in values]


def for_owner_api(access_token: str) -> bool | None:
    """Whether an access token was issued for the owner API; None when it doesn't say."""
    hosts = audience(access_token)
    if any("owner-api" in host for host in hosts):
        return True
    return False if any("fleet-api" in host for host in hosts) else None


class AuthError(Exception):
    """Not signed in, or Tesla rejected the tokens: the user has to sign in again."""


class SignOnUnavailable(Exception):
    """Tesla's sign-on service couldn't be reached, or the browser needed to reach it isn't available."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt_claims(token: str) -> dict:
    """The payload of a JWT, unverified: only used to show the account and find the region."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


class TeslaAuth:
    """Holds the Tesla tokens, keeps them fresh and runs the sign-in flow."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else DATA_DIR / TOKEN_FILE
        self._tokens: dict = {}
        self._pending: dict | None = None
        self._lock = asyncio.Lock()
        self._session: httpx.AsyncClient | None = None
        self.last_error: str = ""
        # Set whenever the user signs in or out, so the vehicle monitors react at once
        self.changed = asyncio.Event()
        self._load()

    # ------------------------------------------------------------------
    # Token storage
    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            self._tokens = json.loads(self._path.read_text())
        except FileNotFoundError:
            self._tokens = {}
        except (OSError, ValueError) as e:
            logger.error(f"Cannot read {self._path}: {e}")
            self._tokens = {}

    def _save(self) -> None:
        # Written next to the database but never part of it, so a database
        # download from the dashboard doesn't contain the account's tokens
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tokens))
        tmp.chmod(0o600)
        tmp.replace(self._path)

    def _store(self, body: dict, issuer: str, source: str = "browser") -> None:
        claims = _jwt_claims(body.get("id_token", ""))
        self._tokens = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token") or self._tokens.get("refresh_token", ""),
            "expires_at": int(time.time()) + int(body.get("expires_in", 28800)),
            "issuer": issuer,
            "email": claims.get("email") or self._tokens.get("email", ""),
            # Who requested the token: "browser" (the app's browser), "app" (taken over
            # from the Tesla Auth app) or "client" (the app's own HTTP client)
            "source": source,
        }
        self._save()
        self.last_error = ""

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    @property
    def signed_in(self) -> bool:
        return bool(self._tokens.get("refresh_token"))

    @property
    def token_source(self) -> str:
        """Who requested the access token in use: "browser", "app" or "client"."""
        return self._tokens.get("source", "browser")

    @property
    def region(self) -> str:
        return "cn" if ".cn" in urlsplit(self._tokens.get("issuer", "")).netloc else "global"

    def status(self) -> dict:
        return {
            "signed_in": self.signed_in,
            "email": self._tokens.get("email", ""),
            "expires_at": self._tokens.get("expires_at"),
            "region": self.region,
            "owner_api": for_owner_api(self._tokens.get("access_token", "")),
            "audience": audience(self._tokens.get("access_token", "")),
            "source": self._tokens.get("source", "client"),
            "error": self.last_error,
        }

    async def _http(self) -> httpx.AsyncClient:
        if self._session is None or self._session.is_closed:
            self._session = new_client()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.is_closed:
            await self._session.aclose()

    # ------------------------------------------------------------------
    # Sign-in
    # ------------------------------------------------------------------
    def begin_login(self) -> str:
        """Start a sign-in and return the address of Tesla's login page for it."""
        verifier = _b64url(secrets.token_bytes(64))
        state = _b64url(secrets.token_bytes(16))
        self._pending = {"verifier": verifier, "state": state, "started": time.time()}
        query = urlencode(
            {
                "client_id": CLIENT_ID,
                "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
                "code_challenge_method": "S256",
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPE,
                "state": state,
            }
        )
        return f"{AUTH_HOST}{AUTH_PATH}/authorize?{query}"

    async def complete_login(self, callback: str, browser: "object | None" = None) -> None:
        """Finish the sign-in with the app link Tesla sent the sign-in browser to.

        The sign-in code is exchanged for tokens by that same browser when it is
        given (it is still on Tesla's sign-on site), so the request is a real
        browser's.
        """
        pending = self._pending
        if not pending or time.time() - pending["started"] > LOGIN_TIMEOUT:
            raise AuthError("The sign-in has expired - start again")

        params = parse_qs(urlsplit(callback).query)
        if (params.get("error") or [""])[0]:
            raise AuthError("The sign-in was cancelled")
        code = (params.get("code") or [""])[0]
        if not code:
            raise AuthError("Tesla did not return a sign-in code - start again")
        if (params.get("state") or [""])[0] != pending["state"]:
            raise AuthError("That answer belongs to another sign-in attempt - start again")
        # Chinese accounts are sent on to their own sign-on service
        issuer = f"{AUTH_HOST}{AUTH_PATH}"
        if urlsplit((params.get("issuer") or [""])[0]).netloc == urlsplit(AUTH_HOST_CN).netloc:
            issuer = f"{AUTH_HOST_CN}{AUTH_PATH}"

        body, source = await self._token_request(
            issuer,
            {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": pending["verifier"],
                "redirect_uri": REDIRECT_URI,
            },
            browser=browser,
        )
        if not body.get("refresh_token"):
            raise AuthError("Tesla did not return a refresh token")
        async with self._lock:
            self._store(body, issuer, source)
        self._pending = None
        logger.info(f"Signed in to Tesla{' as ' + self._tokens['email'] if self._tokens['email'] else ''}")
        self.changed.set()

    async def use_refresh_token(self, pasted: str) -> None:
        """Sign in with tokens made elsewhere, e.g. by the Tesla Auth app.

        Accepts a refresh token on its own, or everything the Tesla Auth app
        shows. With only a refresh token an access token is requested right away.
        With both, the app's access token is used as it is until it expires:
        that tells whether Tesla treats a token requested by this app
        differently from one requested by the app.
        """
        banner = _REFRESH_BANNER_RE.search(pasted)
        refresh_token = banner.group(1) if banner else pasted.strip()
        if not refresh_token:
            raise AuthError("Empty refresh token")
        if len(refresh_token.split()) > 1:
            raise AuthError("Paste only the refresh token (the second of the two tokens)")
        # The token says which sign-on service issued it
        issuer = _jwt_claims(refresh_token).get("iss") or f"{AUTH_HOST}{AUTH_PATH}"
        if urlsplit(issuer).netloc not in (urlsplit(AUTH_HOST).netloc, urlsplit(AUTH_HOST_CN).netloc):
            issuer = f"{AUTH_HOST}{AUTH_PATH}"
        if "TESLA_AUTH_HOST" in os.environ:
            issuer = f"{AUTH_HOST}{AUTH_PATH}"

        access = _ACCESS_BANNER_RE.search(pasted) if banner else None
        if access:
            claims = _jwt_claims(access.group(1))
            try:
                expires_at = int(claims["exp"])
            except (KeyError, TypeError, ValueError):
                expires_at = int(time.time()) + 8 * 3600
            if expires_at - time.time() < REFRESH_MARGIN:
                raise AuthError("These tokens are too old - generate new ones in the Tesla Auth app")
            async with self._lock:
                self._tokens = {
                    "access_token": access.group(1),
                    "refresh_token": refresh_token,
                    "expires_at": expires_at,
                    "issuer": issuer,
                    "email": claims.get("email", ""),
                    "source": "app",
                }
                self._save()
                self.last_error = ""
            logger.info(
                "Signed in with the Tesla Auth app's tokens; its access token is used until"
                f" {time.strftime('%H:%M', time.localtime(expires_at))}, valid for:"
                f" {', '.join(audience(access.group(1))) or 'not stated'}"
            )
        else:
            body, source = await self._refresh_request(issuer, refresh_token)
            async with self._lock:
                self._tokens = {}
                self._store({**body, "refresh_token": body.get("refresh_token") or refresh_token}, issuer, source)
            logger.info("Signed in to Tesla with a refresh token")
        self.changed.set()

    def sign_out(self) -> None:
        self._tokens = {}
        self._pending = None
        with contextlib.suppress(OSError):
            self._path.unlink()
        logger.info("Signed out of Tesla")
        self.changed.set()

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------
    async def access_token(self, force_refresh: bool = False) -> str:
        """A valid access token, renewed first when it is about to expire."""
        async with self._lock:
            if not self.signed_in:
                raise AuthError("Not signed in to Tesla")
            fresh = self._tokens.get("expires_at", 0) - time.time() > REFRESH_MARGIN
            if fresh and not force_refresh:
                return self._tokens["access_token"]
            try:
                body, source = await self._refresh_request(self._tokens["issuer"], self._tokens["refresh_token"])
            except AuthError as e:
                # Tesla no longer accepts the refresh token: only a new sign-in helps
                self.last_error = f"Tesla sign-in expired: {e}"
                logger.error(self.last_error)
                self._tokens.pop("refresh_token", None)
                self._save()
                self.changed.set()
                raise
            except (SignOnUnavailable, httpx.HTTPError, TimeoutError) as e:
                # Tesla (or the browser needed to reach it) unavailable: the current token may still do
                logger.warning(f"Could not renew the Tesla token: {type(e).__name__}: {e}")
                if self._tokens.get("expires_at", 0) > time.time():
                    return self._tokens["access_token"]
                raise AuthError(f"Cannot renew the Tesla token: {type(e).__name__}") from e
            self._store(body, self._tokens["issuer"], source)
            logger.debug("Tesla access token renewed")
            return self._tokens["access_token"]

    async def _refresh_request(self, issuer: str, refresh_token: str) -> tuple[dict, str]:
        return await self._token_request(
            issuer,
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
                "scope": SCOPE,
            },
        )

    async def _token_request(self, issuer: str, payload: dict, browser: "object | None" = None) -> tuple[dict, str]:
        """Ask the sign-on service for tokens. Returns (answer, who asked: "browser" or "client").

        Asked by the real browser so the request carries a browser's TLS/HTTP
        handshake, which Tesla's sign-on service answers with tokens its owner
        API accepts (a plain Python client gets tokens the owner API refuses).
        By an open sign-in browser when one is given, by a short-lived browser
        otherwise, and only by the app's own HTTP client when no browser is
        available (development). AuthError when Tesla refuses the code or token,
        SignOnUnavailable when the service can't be reached.
        """
        from . import signin_browser  # late import: signin_browser imports this module

        url = f"{issuer}/token"
        status, body, source, via = 0, {}, "browser", ""
        try:
            if browser is not None:
                await browser.wait_for_page("/".join(url.split("/", 3)[:3]), timeout=10)
                status, body = await browser.post_json(url, payload)
                via = "the sign-in browser"
            elif signin_browser.available():
                status, body = await signin_browser.post_from_browser(f"{issuer}/authorize", url, payload)
                via = "the app's browser"
            else:
                source, status, body, via = await self._token_request_direct(url, payload)
        except (RuntimeError, OSError, TimeoutError) as e:
            raise SignOnUnavailable(f"the app's browser could not request tokens: {e}") from e

        if status == 200 and isinstance(body, dict) and body.get("access_token"):
            # Which services Tesla made the token for is worth a log line: a token for the
            # Fleet API only is refused by the owner API
            logger.info(
                f"Tesla issued an access token (asked through {via}),"
                f" valid for: {', '.join(audience(body['access_token'])) or 'not stated'}"
            )
            return body, source
        if status in (400, 401, 403):
            detail = body.get("error_description") or body.get("error") or "" if isinstance(body, dict) else ""
            raise AuthError(detail or f"Tesla refused the sign-in (HTTP {status})")
        raise SignOnUnavailable(f"Tesla's sign-on service answered HTTP {status}")

    async def _token_request_direct(self, url: str, payload: dict) -> tuple[str, int, dict, str]:
        """Ask the sign-on service with the app's own HTTP client (no browser available)."""
        client = await self._http()
        resp = await client.post(url, json=payload)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        stream = resp.extensions.get("network_stream")
        tls = stream.get_extra_info("ssl_object") if stream else None
        return "client", resp.status_code, body, f"{resp.http_version}{' / ' + tls.version() if tls else ''}"
