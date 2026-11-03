"""The Spotify plugin: setup guide, sign-in and connection test."""

from collections.abc import Mapping
from datetime import datetime

import httpx

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.adapters.connectors.kit.oauth import pkce_authorize
from linersync.adapters.connectors.kit.parsing import expires_after
from linersync.adapters.connectors.kit.plugin import (
    AuthorizeRequest,
    Category,
    ConnectionTest,
    ConnectorContext,
    connection_failed,
    http_client,
)
from linersync.adapters.connectors.spotify.auth import (
    LOGIN_LIFETIME,
    SpotifyAuth,
    authorize_url,
    check_redirect_uri,
)
from linersync.adapters.connectors.spotify.connector import (
    CAPABILITIES,
    LIMITS,
    SpotifyConnector,
)
from linersync.adapters.connectors.spotify.parse import API_BASE, SERVICE
from linersync.ports.connector import Connector
from linersync.ports.quota import QuotaPolicy
from linersync.ports.setup import (
    CopyValue,
    Link,
    SetupField,
    SetupGuide,
    SetupStep,
    StepKind,
    validate_values,
)

_TOKEN_HOST = "https://accounts.spotify.com"


def is_quota_exceeded(response: httpx.Response) -> bool:
    if response.status_code != 429:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("reason") == "QUOTA_EXCEEDED"


def _token_client(ctx: ConnectorContext) -> HttpClient:
    return http_client(ctx, SERVICE, _TOKEN_HOST)


def _api_client(ctx: ConnectorContext, auth: SpotifyAuth) -> HttpClient:
    return http_client(
        ctx, SERVICE, API_BASE, auth=auth, is_quota_exceeded=is_quota_exceeded
    )


async def _get_me(api: HttpClient) -> Mapping[str, object]:
    response = await api.request("GET", f"{API_BASE}/me", idempotent=True)
    body = response.json()
    if not isinstance(body, dict):
        raise HttpFailure(
            SERVICE, "GET /me returned an unexpected body", maybe_applied=False
        )
    return body


class SpotifyPlugin:
    service = SERVICE
    display_name = "Spotify"
    category: Category = "streaming"
    capabilities = CAPABILITIES
    limits = LIMITS
    quota_policy = QuotaPolicy()

    def setup_guide(self) -> SetupGuide:
        return SetupGuide(
            self.service,
            self.display_name,
            self.category,
            (
                "A Spotify Premium account (Spotify requires it for "
                "development-mode apps)",
                "A development-mode app can have at most 5 users",
            ),
            (
                SetupStep(
                    "before",
                    "Before you start",
                    StepKind.INFO,
                    body=(
                        "LinerSync signs in to Spotify with an app that you create "
                        "yourself. It takes a few minutes and needs no secret key."
                    ),
                ),
                SetupStep(
                    "create_app",
                    "Create a Spotify app",
                    StepKind.INFO,
                    body=(
                        "Open the Spotify developer dashboard and create an app. "
                        "Paste the Redirect URI below into the app's settings, and "
                        'tick "Web API" when asked which APIs you plan to use. '
                        "Reaching LinerSync through your own tunnel or reverse "
                        "proxy? The Redirect URI is built from "
                        "LINERSYNC_PUBLIC_URL, so it must be the HTTPS address you "
                        "open LinerSync at. If you move LinerSync to a new "
                        "address, add its Redirect URI to the app as well."
                    ),
                    links=(
                        Link(
                            "Spotify developer dashboard",
                            "https://developer.spotify.com/dashboard",
                        ),
                    ),
                    copy_values=(CopyValue("Redirect URI", "{redirect_uri}"),),
                ),
                SetupStep(
                    "client_id",
                    "Enter your Client ID",
                    StepKind.FORM,
                    body="Copy the Client ID from your app's settings page.",
                    fields=(
                        SetupField(
                            "client_id",
                            "Client ID",
                            pattern="[0-9a-f]{32}",
                            pattern_hint="A Client ID is 32 characters, 0–9 and a–f",
                        ),
                    ),
                ),
                SetupStep(
                    "sign_in",
                    "Sign in to Spotify",
                    StepKind.OAUTH,
                    body="Sign in and allow LinerSync to manage your playlists.",
                ),
                SetupStep("test", "Test the connection", StepKind.TEST),
            ),
        )

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        return validate_values(self.setup_guide(), values)

    def authorize(
        self, credentials: Mapping[str, str], redirect_uri: str, state: str
    ) -> AuthorizeRequest:
        return pkce_authorize(
            credentials,
            redirect_uri,
            state,
            check_redirect_uri=check_redirect_uri,
            authorize_url=authorize_url,
        )

    async def complete(
        self,
        ctx: ConnectorContext,
        redirect_uri: str,
        code: str,
        keep: Mapping[str, str],
    ) -> None:
        token_client = _token_client(ctx)
        auth = SpotifyAuth(ctx.credentials, token_client, ctx.clock)
        async with token_client, _api_client(ctx, auth) as api:
            await auth.exchange_code(code, redirect_uri, keep["code_verifier"])
            me = await _get_me(api)
            ctx.credentials.save(SERVICE, {"user_id": str(me["id"])})

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        token_client = _token_client(ctx)
        auth = SpotifyAuth(ctx.credentials, token_client, ctx.clock)
        async with token_client, _api_client(ctx, auth) as api:
            try:
                me = await _get_me(api)
            except HttpFailure as e:
                return connection_failed(e, "Sign in to Spotify again")
        name = me.get("display_name") or me.get("id")
        return ConnectionTest(True, "Connected", str(name) if name else None)

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return expires_after(credentials.get("authorized_at"), LOGIN_LIFETIME)

    def create(self, ctx: ConnectorContext) -> Connector:
        auth = SpotifyAuth(ctx.credentials, _token_client(ctx), ctx.clock)
        # Unauthenticated: artwork and covers live on hosts that must never
        # see the Spotify token.
        fetch_image = http_fetcher(http_client(ctx, SERVICE))
        artwork = ArtworkHasher(fetch_image) if ctx.settings.hash_artwork else None
        return SpotifyConnector(
            _api_client(ctx, auth), artwork, fetch_image, clock=ctx.clock
        )


SPOTIFY_PLUGIN = SpotifyPlugin()
PLUGIN = SPOTIFY_PLUGIN
