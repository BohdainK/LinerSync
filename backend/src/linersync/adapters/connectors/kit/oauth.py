"""OAuth Authorization Code with PKCE (no client secret) and refresh, shared by
the services that sign in this way."""

import asyncio
import base64
import hashlib
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.domain.tracks import ServiceId
from linersync.ports.credentials import CredentialStore
from linersync.ports.runtime import Clock

_REFRESH_MARGIN = timedelta(seconds=60)
# Standard OAuth error codes (RFC 6749, RFC 8628). Only these leave a failed
# token response; the rest of its body may hold secrets.
_OAUTH_ERRORS = (
    "authorization_pending",
    "slow_down",
    "access_denied",
    "expired_token",
    "invalid_grant",
    "invalid_client",
)


class OAuthError(HttpFailure):
    """A failed token request; ``error`` is its OAuth error code, if standard."""

    def __init__(self, *args: Any, error: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.error = error


_VERIFIER = re.compile(r"[A-Za-z0-9\-._~]{43,128}")


def pkce_pair(verifier: str | None = None) -> tuple[str, str]:
    """(verifier, S256 challenge). Generates a verifier when none is given."""
    if verifier is None:
        verifier = secrets.token_urlsafe(64)
    if _VERIFIER.fullmatch(verifier) is None:
        raise ValueError("A PKCE verifier is 43 to 128 unreserved characters")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class PkceAuth:
    """AuthProvider that keeps the access token fresh and saves rotated tokens."""

    def __init__(
        self,
        service: ServiceId,
        display_name: str,
        token_url: str,
        credentials: CredentialStore,
        token_client: HttpClient,
        clock: Clock,
    ) -> None:
        self._service = service
        self._name = display_name
        self._token_url = token_url
        self._credentials = credentials
        self._token_client = token_client
        self._clock = clock
        self._lock = asyncio.Lock()

    async def headers(self) -> Mapping[str, str]:
        creds = self._credentials.get(self._service)
        if self._needs_refresh(creds):
            async with self._lock:
                creds = self._credentials.get(self._service)
                if self._needs_refresh(creds):
                    await self._refresh(creds)
                    creds = self._credentials.get(self._service)
        token = creds.get("access_token")
        if not token:
            raise self._sign_in_again()
        return {"Authorization": f"Bearer {token}"}

    async def refresh(self) -> bool:
        seen = self._credentials.get(self._service).get("access_token")
        async with self._lock:
            creds = self._credentials.get(self._service)
            if creds.get("access_token") != seen:
                return True  # another caller already refreshed
            await self._refresh(creds)
        return True

    async def exchange_code(self, code: str, redirect_uri: str, verifier: str) -> None:
        creds = self._credentials.get(self._service)
        await self.exchange(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": creds.get("client_id", ""),
                "code_verifier": verifier,
            }
        )

    async def exchange(self, form: Mapping[str, str]) -> None:
        """Trade a grant (a code, a device code) for tokens and save them.
        Raises OAuthError when the token endpoint refuses."""
        body = await self._post(form)
        values = self._token_values(body)
        values["authorized_at"] = self._clock.now().astimezone(UTC).isoformat()
        if "refresh_token" not in values:
            raise HttpFailure(
                self._service,
                f"{self._name} sign-in failed: no refresh token",
                maybe_applied=False,
            )
        self._credentials.save(self._service, values)

    def _needs_refresh(self, creds: Mapping[str, str]) -> bool:
        expires = _parse(creds.get("expires_at"))
        if expires is None or not creds.get("access_token"):
            return "refresh_token" in creds
        return expires - _REFRESH_MARGIN <= self._clock.now()

    async def _refresh(self, creds: Mapping[str, str]) -> None:
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            raise self._sign_in_again()
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": creds.get("client_id", ""),
        }
        if creds.get("client_secret"):  # confidential clients (Google) need it
            form["client_secret"] = creds["client_secret"]
        body = await self._post(form)
        self._credentials.save(self._service, self._token_values(body))

    async def _post(self, form: Mapping[str, str]) -> dict[str, Any]:
        # Failure messages are rebuilt here: the token endpoint's body and our
        # own form fields can hold secrets and must never reach an error.
        failure: HttpFailure | None = None
        try:
            response = await self._token_client.request(
                "POST", self._token_url, idempotent=False, data=form
            )
        except HttpFailure as e:
            # Build the replacement outside this block so that neither
            # __context__ nor __cause__ keeps the original (it holds the body).
            code = next((c for c in _OAUTH_ERRORS if f'"{c}"' in e.message), None)
            dead = e.status == 400 and code == "invalid_grant"
            status, auth_required, quota = e.status, e.auth_required, e.quota_exhausted
            failure = self._token_failure(dead, status, auth_required, quota, code)
            if dead and form.get("grant_type") == "refresh_token":
                # A dead refresh token is discarded and never tried again.
                self._credentials.save(
                    self._service, {"refresh_token": "", "access_token": ""}
                )
        if failure is not None:
            raise failure
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise HttpFailure(
                self._service,
                f"{self._name} token response was not understood",
                maybe_applied=False,
            )
        return body

    def _token_failure(
        self,
        dead: bool,
        status: int | None,
        auth_required: bool,
        quota: bool,
        code: str | None,
    ) -> HttpFailure:
        if dead:
            # Still an OAuthError, so a device-code poll can tell a used code.
            return OAuthError(
                self._service,
                f"{self._name} sign-in expired; sign in again",
                status=status,
                maybe_applied=False,
                auth_required=True,
                error=code,
            )
        where = f" ({status})" if status is not None else ""
        return OAuthError(
            self._service,
            f"{self._name} token request failed{where}",
            status=status,
            maybe_applied=False,
            auth_required=auth_required,
            quota_exhausted=quota,
            error=code,
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

    def _sign_in_again(self) -> HttpFailure:
        return HttpFailure(
            self._service,
            f"{self._name} sign-in expired; sign in again",
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
