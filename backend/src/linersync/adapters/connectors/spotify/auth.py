"""Spotify sign-in: Authorization Code with PKCE (no client secret) and refresh."""

import asyncio
import base64
import hashlib
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.domain.tracks import ServiceId
from linersync.ports.credentials import CredentialStore
from linersync.ports.runtime import Clock

SERVICE = ServiceId("spotify")
SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
    "ugc-image-upload",
)
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
LOGIN_LIFETIME = timedelta(days=182)
_REFRESH_MARGIN = timedelta(seconds=60)
_VERIFIER = re.compile(r"[A-Za-z0-9\-._~]{43,128}")


def pkce_pair(verifier: str | None = None) -> tuple[str, str]:
    """(verifier, S256 challenge). Generates a verifier when none is given."""
    if verifier is None:
        verifier = secrets.token_urlsafe(64)
    if _VERIFIER.fullmatch(verifier) is None:
        raise ValueError("A PKCE verifier is 43 to 128 unreserved characters")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def check_redirect_uri(uri: str) -> None:
    parts = urlsplit(uri)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname == "127.0.0.1":
        return
    if parts.scheme == "http" and parts.hostname == "localhost":
        raise ValueError(
            "Spotify doesn't accept http://localhost; use http://127.0.0.1 instead"
        )
    raise ValueError(
        "Spotify needs an https:// address, or http://127.0.0.1 for local use"
    )


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class SpotifyAuth:
    """AuthProvider that keeps the access token fresh and saves rotated tokens."""

    def __init__(
        self, credentials: CredentialStore, token_client: HttpClient, clock: Clock
    ) -> None:
        self._credentials = credentials
        self._token_client = token_client
        self._clock = clock
        self._lock = asyncio.Lock()

    async def headers(self) -> Mapping[str, str]:
        creds = self._credentials.get(SERVICE)
        if self._needs_refresh(creds):
            async with self._lock:
                creds = self._credentials.get(SERVICE)
                if self._needs_refresh(creds):
                    await self._refresh(creds)
                    creds = self._credentials.get(SERVICE)
        token = creds.get("access_token")
        if not token:
            raise _sign_in_again()
        return {"Authorization": f"Bearer {token}"}

    async def refresh(self) -> bool:
        seen = self._credentials.get(SERVICE).get("access_token")
        async with self._lock:
            creds = self._credentials.get(SERVICE)
            if creds.get("access_token") != seen:
                return True  # another caller already refreshed
            await self._refresh(creds)
        return True

    async def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> None:
        creds = self._credentials.get(SERVICE)
        body = await self._post(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": creds.get("client_id", ""),
                "code_verifier": verifier,
            }
        )
        values = self._token_values(body)
        values["authorized_at"] = self._clock.now().astimezone(UTC).isoformat()
        if "refresh_token" not in values:
            raise HttpFailure(
                SERVICE, "Spotify sign-in failed: no refresh token", maybe_applied=False
            )
        self._credentials.save(SERVICE, values)

    def _needs_refresh(self, creds: Mapping[str, str]) -> bool:
        expires = _parse(creds.get("expires_at"))
        if expires is None or not creds.get("access_token"):
            return "refresh_token" in creds
        return expires - _REFRESH_MARGIN <= self._clock.now()

    async def _refresh(self, creds: Mapping[str, str]) -> None:
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            raise _sign_in_again()
        body = await self._post(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": creds.get("client_id", ""),
            }
        )
        self._credentials.save(SERVICE, self._token_values(body))

    async def _post(self, form: Mapping[str, str]) -> dict[str, Any]:
        # Failure messages are rebuilt here: the token endpoint's body and our
        # own form fields can hold secrets and must never reach an error.
        failure: HttpFailure | None = None
        try:
            response = await self._token_client.request(
                "POST", TOKEN_URL, idempotent=False, data=form
            )
        except HttpFailure as e:
            # Build the replacement outside this block so that neither
            # __context__ nor __cause__ keeps the original (it holds the body).
            dead = e.status == 400 and "invalid_grant" in e.message
            status, auth_required, quota = e.status, e.auth_required, e.quota_exhausted
            failure = self._token_failure(dead, status, auth_required, quota)
            if dead and form.get("grant_type") == "refresh_token":
                # A dead refresh token is discarded and never tried again.
                self._credentials.save(
                    SERVICE, {"refresh_token": "", "access_token": ""}
                )
        if failure is not None:
            raise failure
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise HttpFailure(
                SERVICE,
                "Spotify token response was not understood",
                maybe_applied=False,
            )
        return body

    @staticmethod
    def _token_failure(
        dead: bool, status: int | None, auth_required: bool, quota: bool
    ) -> HttpFailure:
        if dead:
            return _sign_in_again()
        where = f" ({status})" if status is not None else ""
        return HttpFailure(
            SERVICE,
            f"Spotify token request failed{where}",
            status=status,
            maybe_applied=False,
            auth_required=auth_required,
            quota_exhausted=quota,
        )

    def _token_values(self, body: Mapping[str, Any]) -> dict[str, str]:
        expires_in = body.get("expires_in")
        seconds = expires_in if isinstance(expires_in, int | float) else 3600
        expires_at = self._clock.now().astimezone(UTC) + timedelta(seconds=seconds)
        values = {
            "access_token": str(body["access_token"]),
            "expires_at": expires_at.isoformat(),
        }
        rotated = body.get("refresh_token")
        if isinstance(rotated, str) and rotated:
            values["refresh_token"] = rotated
        return values


def _sign_in_again() -> HttpFailure:
    return HttpFailure(
        SERVICE,
        "Spotify sign-in expired; sign in again",
        maybe_applied=False,
        auth_required=True,
    )


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
