"""YouTube Music sign-in: a signed-in browser's cookie, or Google OAuth (device flow).

Cookie auth signs each request with SAPISIDHASH, as music.youtube.com itself
does (ported from ytmusicapi, MIT).
"""

import hashlib
from collections.abc import Mapping
from datetime import datetime

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.oauth import OAuthError, PkceAuth
from linersync.adapters.connectors.kit.plugin import DeviceCodeStart, DevicePoll
from linersync.adapters.connectors.youtube_music.parse import ORIGIN, SERVICE
from linersync.ports.credentials import CredentialStore
from linersync.ports.runtime import Clock

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SLOW_DOWN_STEP_S = 5  # RFC 8628: each slow_down adds 5 seconds to the interval
_SAPISID_NAMES = ("SAPISID", "__Secure-3PAPISID")


def clean_cookie(cookie: str) -> str:
    """One header line: a paste may carry the "Cookie:" name and line breaks."""
    text = cookie.strip().removeprefix("Cookie:").strip()
    parts = (" ".join(p.split()) for p in text.split(";"))
    return "; ".join(p for p in parts if p)


def sapisid_from_cookie(cookie: str) -> str | None:
    pairs: dict[str, str] = {}
    for part in clean_cookie(cookie).split(";"):
        name, sep, value = part.strip().partition("=")
        if sep:
            pairs[name.strip()] = value.strip()
    return next((pairs[n] for n in _SAPISID_NAMES if pairs.get(n)), None)


def sapisid_hash(sapisid: str, now: datetime) -> str:
    ts = int(now.timestamp())
    digest = hashlib.sha1(f"{ts} {sapisid} {ORIGIN}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def _signed_out() -> HttpFailure:
    return HttpFailure(
        SERVICE,
        "YouTube Music sign-in expired; sign in again",
        maybe_applied=False,
        auth_required=True,
    )


class CookieAuth:
    """AuthProvider for a pasted cookie. A cookie can't be refreshed: when
    YouTube stops accepting it, the user pastes a new one."""

    def __init__(self, credentials: CredentialStore, clock: Clock) -> None:
        self._credentials = credentials
        self._clock = clock

    async def headers(self) -> Mapping[str, str]:
        cookie = clean_cookie(self._credentials.get(SERVICE).get("cookie", ""))
        sapisid = sapisid_from_cookie(cookie)
        if sapisid is None:
            raise _signed_out()
        # Which of the browser's signed-in Google accounts, as /u/<n>/ counts.
        auth_user = self._credentials.get(SERVICE).get("auth_user", "").strip()
        return {
            "Cookie": cookie,
            "Authorization": sapisid_hash(sapisid, self._clock.now()),
            "X-Origin": ORIGIN,
            "X-Goog-AuthUser": auth_user or "0",
        }

    async def refresh(self) -> bool:
        return False


class GoogleAuth(PkceAuth):
    """Google OAuth tokens for a self-hoster's own "TVs and Limited Input
    devices" client; refreshes send its client secret."""

    def __init__(
        self, credentials: CredentialStore, token_client: HttpClient, clock: Clock
    ) -> None:
        super().__init__(
            SERVICE, "YouTube Music", TOKEN_URL, credentials, token_client, clock
        )


async def start_device(client: HttpClient, client_id: str) -> DeviceCodeStart:
    """Raises HttpFailure when Google refuses the client."""
    response = await client.request(
        "POST",
        DEVICE_URL,
        idempotent=False,
        data={"client_id": client_id, "scope": SCOPE},
    )
    body = response.json()
    interval = int(body.get("interval", 5))
    return DeviceCodeStart(
        user_code=str(body["user_code"]),
        verification_url=str(body["verification_url"]),
        interval_s=interval,
        expires_in_s=int(body.get("expires_in", 1800)),
        keep={"device_code": str(body["device_code"]), "interval_s": str(interval)},
    )


async def poll_device(
    auth: GoogleAuth, credentials: CredentialStore, keep: Mapping[str, str]
) -> DevicePoll:
    creds = credentials.get(SERVICE)
    try:
        await auth.exchange(
            {
                "grant_type": DEVICE_GRANT,
                "device_code": keep.get("device_code", ""),
                "client_id": creds.get("client_id", ""),
                "client_secret": creds.get("client_secret", ""),
            }
        )
    except OAuthError as e:
        match e.error:
            case "authorization_pending":
                return DevicePoll("pending")
            case "slow_down":
                try:
                    interval = int(keep.get("interval_s", "5"))
                except ValueError:
                    interval = 5
                return DevicePoll("pending", interval_s=interval + SLOW_DOWN_STEP_S)
            case "invalid_grant":
                return DevicePoll(
                    "expired",
                    "This code was already used or is no longer valid. Get a new code.",
                )
            case "access_denied":
                return DevicePoll(
                    "denied", "You declined the sign-in. Get a new code to try again."
                )
            case "expired_token":
                return DevicePoll("expired", "This code expired. Get a new one.")
            case "invalid_client":
                return DevicePoll(
                    "denied", "Google refused the client ID or secret. Check them."
                )
        # Anything else may pass; the code's expiry ends the flow regardless.
        return DevicePoll("pending")
    except HttpFailure:
        return DevicePoll("pending")
    return DevicePoll("done")
