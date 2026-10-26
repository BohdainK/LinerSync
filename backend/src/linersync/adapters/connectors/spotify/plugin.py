"""The Spotify plugin: setup guide, sign-in and connection test."""

from collections.abc import Mapping
from datetime import datetime

import httpx

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.adapters.connectors.kit.plugin import (
    AuthorizeRequest,
    Category,
    ConnectionTest,
    ConnectorContext,
)
from linersync.adapters.connectors.spotify.auth import (
    API_BASE,
    LOGIN_LIFETIME,
    SERVICE,
    SpotifyAuth,
    authorize_url,
    check_redirect_uri,
    pkce_pair,
)
from linersync.adapters.connectors.spotify.connector import (
    CAPABILITIES,
    LIMITS,
    SpotifyConnector,
)
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
_POLICY = QuotaPolicy()


def is_quota_exceeded(response: httpx.Response) -> bool:
    if response.status_code != 429:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("reason") == "QUOTA_EXCEEDED"


def _token_client(ctx: ConnectorContext) -> HttpClient:
    return HttpClient(
        SERVICE,
        _TOKEN_HOST,
        settings=ctx.settings.http,
        quota=ctx.quota,
        quota_policy=_POLICY,
        clock=ctx.clock,
        transport=ctx.transport,
        sleep=ctx.sleep,
    )


def _artwork_client(ctx: ConnectorContext) -> HttpClient:
    """Unauthenticated: artwork and covers live on hosts that must never see
    the Spotify token."""
    return HttpClient(
        SERVICE,
        "",
        settings=ctx.settings.http,
        quota=ctx.quota,
        quota_policy=QuotaPolicy(),
        clock=ctx.clock,
        transport=ctx.transport,
        sleep=ctx.sleep,
    )


def _api_client(ctx: ConnectorContext, auth: SpotifyAuth) -> HttpClient:
    return HttpClient(
        SERVICE,
        API_BASE,
        settings=ctx.settings.http,
        quota=ctx.quota,
        quota_policy=_POLICY,
        clock=ctx.clock,
        auth=auth,
        transport=ctx.transport,
        sleep=ctx.sleep,
        is_quota_exceeded=is_quota_exceeded,
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
    quota_policy = _POLICY

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
                        'tick "Web API" when asked which APIs you plan to use.'
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
        check_redirect_uri(redirect_uri)
        verifier, challenge = pkce_pair()
        url = authorize_url(credentials["client_id"], redirect_uri, state, challenge)
        return AuthorizeRequest(url, {"code_verifier": verifier})

    async def complete(
        self,
        ctx: ConnectorContext,
        redirect_uri: str,
        code: str,
        keep: Mapping[str, str],
    ) -> None:
        token_client = _token_client(ctx)
        auth = SpotifyAuth(ctx.credentials, token_client, ctx.clock)
        api = _api_client(ctx, auth)
        try:
            await auth.exchange_code(code, redirect_uri, keep["code_verifier"])
            me = await _get_me(api)
            ctx.credentials.save(SERVICE, {"user_id": str(me["id"])})
        finally:
            await token_client.aclose()
            await api.aclose()

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        token_client = _token_client(ctx)
        api = _api_client(ctx, SpotifyAuth(ctx.credentials, token_client, ctx.clock))
        try:
            me = await _get_me(api)
        except HttpFailure as e:
            if e.auth_required:
                return ConnectionTest(False, "Sign in to Spotify again")
            return ConnectionTest(False, e.message)
        finally:
            await token_client.aclose()
            await api.aclose()
        name = me.get("display_name") or me.get("id")
        return ConnectionTest(True, "Connected", str(name) if name else None)

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        raw = credentials.get("authorized_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw) + LOGIN_LIFETIME
        except ValueError:
            return None

    def create(self, ctx: ConnectorContext) -> Connector:
        auth = SpotifyAuth(ctx.credentials, _token_client(ctx), ctx.clock)
        fetch_image = http_fetcher(_artwork_client(ctx))
        artwork = ArtworkHasher(fetch_image) if ctx.settings.hash_artwork else None
        return SpotifyConnector(
            _api_client(ctx, auth), artwork, fetch_image, clock=ctx.clock
        )


SPOTIFY_PLUGIN = SpotifyPlugin()
PLUGIN = SPOTIFY_PLUGIN
