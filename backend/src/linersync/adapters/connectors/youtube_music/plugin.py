"""The YouTube Music plugin: setup guide, sign-in and connection test.

Two ways to authenticate, chosen with LINERSYNC_YTMUSIC_AUTH:

- ``headers`` (default): the Cookie header of a signed-in music.youtube.com
  tab. Unofficial, needs no Google Cloud project, and lasts until YouTube
  rotates the session.
- ``oauth``: the user's own Google Cloud OAuth client ("TVs and Limited Input
  devices"), signed in with a device code.
"""

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.parsing import expires_after
from linersync.adapters.connectors.kit.plugin import (
    Category,
    ConnectionTest,
    ConnectorContext,
    DeviceCodeStart,
    DevicePoll,
    artwork_hasher,
    connection_failed,
    fingerprint,
    http_client,
)
from linersync.adapters.connectors.youtube_music.auth import (
    CookieAuth,
    GoogleAuth,
    poll_device,
    sapisid_from_cookie,
    start_device,
)
from linersync.adapters.connectors.youtube_music.connector import (
    CAPABILITIES,
    LIMITS,
    YouTubeMusicConnector,
)
from linersync.adapters.connectors.youtube_music.parse import (
    API_BASE,
    SERVICE,
    context,
    parse_account_name,
)
from linersync.ports.connector import Connector
from linersync.ports.quota import QuotaPolicy
from linersync.ports.setup import (
    FieldKind,
    Link,
    SetupField,
    SetupGuide,
    SetupStep,
    StepKind,
    validate_values,
)

AUTH_MODE_ENV = "LINERSYNC_YTMUSIC_AUTH"
COOKIE_LIFETIME = timedelta(days=30)
_NO_SAPISID = (
    "The cookie has no SAPISID; copy it from a signed-in music.youtube.com tab"
)
_SIGN_IN_AGAIN = "Sign in to YouTube Music again"
_UNOFFICIAL = (
    "LinerSync talks to YouTube Music the way its website does: there is no "
    "official API. It can stop working when YouTube changes its site, until "
    "LinerSync is updated."
)


class YTAuthMode(StrEnum):
    HEADERS = "headers"
    OAUTH = "oauth"


def auth_mode_from_env() -> YTAuthMode:
    raw = os.environ.get(AUTH_MODE_ENV, YTAuthMode.HEADERS).strip().lower()
    try:
        return YTAuthMode(raw)
    except ValueError:
        return YTAuthMode.HEADERS


class YouTubeMusicPlugin:
    service = SERVICE
    display_name = "YouTube Music"
    category: Category = "streaming"
    capabilities = CAPABILITIES
    limits = LIMITS
    quota_policy = QuotaPolicy()
    # A cookie's end isn't announced: the 30 days are a reminder, not a deadline.
    login_expiry_estimated = True

    def __init__(self, mode: YTAuthMode = YTAuthMode.HEADERS) -> None:
        self.mode = mode

    # -- guide -------------------------------------------------------------

    def setup_guide(self) -> SetupGuide:
        if self.mode is YTAuthMode.OAUTH:
            prerequisites: tuple[str, ...] = (
                "A Google account with YouTube Music",
                "A Google Cloud project (free)",
            )
            steps = self._oauth_steps()
        else:
            prerequisites = ("A Google account with YouTube Music", "A desktop browser")
            steps = self._cookie_steps()
        return SetupGuide(
            self.service, self.display_name, self.category, prerequisites, steps
        )

    def _cookie_steps(self) -> tuple[SetupStep, ...]:
        return (
            SetupStep(
                "before",
                "Before you start",
                StepKind.INFO,
                body=(
                    f"{_UNOFFICIAL}\n\nLinerSync uses the session of a browser "
                    "where you're signed in to YouTube Music. Anyone with this "
                    "cookie can use your Google account on YouTube, so it is "
                    "stored encrypted and never shown again."
                ),
                links=(Link("YouTube Music", "https://music.youtube.com"),),
            ),
            SetupStep(
                "cookie",
                "Paste your Cookie header",
                StepKind.FORM,
                body=(
                    "Open music.youtube.com signed in, open the developer tools "
                    "(F12), go to Network, click any request to music.youtube.com, "
                    "and copy the value of the Cookie request header."
                ),
                fields=(
                    SetupField(
                        "cookie",
                        "Cookie header",
                        FieldKind.MULTILINE_SECRET,
                        help="It's long and contains SAPISID=…",
                    ),
                    SetupField(
                        "auth_user",
                        "Account number",
                        help=(
                            "Only if this browser is signed in to several Google "
                            "accounts: the value of the X-Goog-AuthUser request "
                            "header. Leave it empty for the first account."
                        ),
                        pattern="[0-9]{1,2}",
                        pattern_hint="A number, such as 0 or 1",
                        placeholder="0",
                        required=False,
                    ),
                ),
            ),
            SetupStep("test", "Test the connection", StepKind.TEST),
        )

    def _oauth_steps(self) -> tuple[SetupStep, ...]:
        return (
            SetupStep(
                "before",
                "Before you start",
                StepKind.INFO,
                body=(
                    f"{_UNOFFICIAL}\n\nLinerSync signs in with a Google OAuth "
                    "client that you create yourself. It's free."
                ),
            ),
            SetupStep(
                "create_client",
                "Create a Google OAuth client",
                StepKind.INFO,
                body=(
                    "In the Google Cloud console, create a project, enable the "
                    "YouTube Data API v3, and create an OAuth client ID of type "
                    '"TVs and Limited Input devices". Then set the OAuth consent '
                    'screen\'s publishing status to "In production": while it is in '
                    '"Testing", Google ends the sign-in after 7 days. Google warns '
                    "that the app is unverified when you sign in; that's expected "
                    "for a client only you use."
                ),
                links=(
                    Link(
                        "Google Cloud credentials",
                        "https://console.cloud.google.com/apis/credentials",
                    ),
                ),
            ),
            SetupStep(
                "client",
                "Enter the client ID and secret",
                StepKind.FORM,
                fields=(
                    SetupField(
                        "client_id",
                        "Client ID",
                        pattern=r"[0-9a-z-]+\.apps\.googleusercontent\.com",
                        pattern_hint="It ends in .apps.googleusercontent.com",
                    ),
                    SetupField("client_secret", "Client secret", FieldKind.SECRET),
                ),
            ),
            SetupStep(
                "sign_in",
                "Sign in to YouTube Music",
                StepKind.DEVICE_CODE,
                body="Get a code, open Google's page and enter it there.",
            ),
            SetupStep("test", "Test the connection", StepKind.TEST),
        )

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        problems = validate_values(self.setup_guide(), values)
        if (
            self.mode is YTAuthMode.HEADERS
            and "cookie" not in problems
            and sapisid_from_cookie(values.get("cookie", "")) is None
        ):
            problems["cookie"] = _NO_SAPISID
        return problems

    # -- device code (OAuth mode) -----------------------------------------

    @property
    def uses_device_code(self) -> bool:
        return self.mode is YTAuthMode.OAUTH

    def _require_device_code(self) -> None:
        if not self.uses_device_code:
            raise ValueError(
                "YouTube Music signs in with a cookie here, not a device code"
            )

    async def start_device_code(self, ctx: ConnectorContext) -> DeviceCodeStart:
        self._require_device_code()
        async with http_client(ctx, SERVICE) as client:
            try:
                client_id = ctx.credentials.get(SERVICE).get("client_id", "")
                return await start_device(client, client_id)
            except HttpFailure:
                raise ValueError(
                    "Google refused the client ID. Check it, and that the client "
                    'type is "TVs and Limited Input devices".'
                ) from None
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "Google's reply was not understood. Try again."
                ) from None

    async def poll_device_code(
        self, ctx: ConnectorContext, keep: Mapping[str, str]
    ) -> DevicePoll:
        self._require_device_code()
        async with http_client(ctx, SERVICE) as client:
            auth = GoogleAuth(ctx.credentials, client, ctx.clock)
            return await poll_device(auth, ctx.credentials, keep)

    # -- connection --------------------------------------------------------

    def _auth(
        self, ctx: ConnectorContext
    ) -> tuple[CookieAuth | GoogleAuth, HttpClient | None]:
        if self.mode is YTAuthMode.OAUTH:
            token_client = http_client(ctx, SERVICE)
            return GoogleAuth(ctx.credentials, token_client, ctx.clock), token_client
        return CookieAuth(ctx.credentials, ctx.clock), None

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        auth, token_client = self._auth(ctx)
        api = http_client(ctx, SERVICE, API_BASE, auth=auth)
        try:
            response = await api.request(
                "POST",
                f"{API_BASE}/account/account_menu",
                idempotent=True,
                params={"prettyPrint": "false"},
                json={"context": context()},
            )
            name = parse_account_name(response.json())
        except HttpFailure as e:
            return connection_failed(e, _SIGN_IN_AGAIN)
        except (KeyError, TypeError, ValueError):
            return ConnectionTest(False, "YouTube Music's reply was not understood")
        finally:
            await api.aclose()
            if token_client is not None:
                await token_client.aclose()
        if name is None:  # a signed-out session still answers, without an account
            return ConnectionTest(False, _SIGN_IN_AGAIN)
        if self.mode is YTAuthMode.HEADERS:
            self._remember_cookie(ctx)
        return ConnectionTest(True, "Connected", name)

    def _remember_cookie(self, ctx: ConnectorContext) -> None:
        creds = ctx.credentials.get(SERVICE)
        cookie_fingerprint = fingerprint(creds.get("cookie", ""))
        if creds.get("cookie_fingerprint") != cookie_fingerprint:
            ctx.credentials.save(
                SERVICE,
                {
                    "cookie_fingerprint": cookie_fingerprint,
                    "cookie_seen_at": ctx.clock.now().astimezone(UTC).isoformat(),
                },
            )

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        if self.mode is YTAuthMode.OAUTH:
            return None  # a refresh token has no documented lifetime
        if credentials.get("cookie_fingerprint") != fingerprint(
            credentials.get("cookie", "")
        ):
            return None  # this cookie hasn't passed a test yet
        # Sessions don't announce their end; ask for a fresh cookie well before.
        return expires_after(credentials.get("cookie_seen_at"), COOKIE_LIFETIME)

    def create(self, ctx: ConnectorContext) -> Connector:
        auth, _ = self._auth(ctx)
        return YouTubeMusicConnector(
            http_client(ctx, SERVICE, API_BASE, auth=auth),
            artwork_hasher(ctx, SERVICE),
            clock=ctx.clock,
        )


YOUTUBE_MUSIC_PLUGIN = YouTubeMusicPlugin(auth_mode_from_env())
PLUGIN = YOUTUBE_MUSIC_PLUGIN
