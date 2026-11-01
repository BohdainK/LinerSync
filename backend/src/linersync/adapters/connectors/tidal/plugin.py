"""The Tidal plugin: setup guide, sign-in and connection test."""

from collections.abc import Mapping
from datetime import datetime

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.oauth import pkce_authorize
from linersync.adapters.connectors.kit.plugin import (
    AuthorizeRequest,
    Category,
    ConnectionTest,
    ConnectorContext,
    artwork_hasher,
    connection_failed,
    http_client,
)
from linersync.adapters.connectors.tidal.auth import (
    API_BASE,
    SERVICE,
    TidalAuth,
    authorize_url,
    check_redirect_uri,
)
from linersync.adapters.connectors.tidal.connector import (
    CAPABILITIES,
    JSON_API,
    LIMITS,
    TidalConnector,
)
from linersync.ports.connector import Connector
from linersync.ports.credentials import CredentialStore
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


def _clients(ctx: ConnectorContext) -> tuple[HttpClient, TidalAuth, HttpClient]:
    """The token endpoint's client (plain: only the API may see the Tidal
    token), the sign-in that uses it, and the API client."""
    token_client = http_client(ctx, SERVICE)
    auth = TidalAuth(ctx.credentials, token_client, ctx.clock)
    return token_client, auth, http_client(ctx, SERVICE, API_BASE, auth=auth)


async def _get_me(api: HttpClient) -> Mapping[str, object]:
    response = await api.request(
        "GET", f"{API_BASE}/users/me", idempotent=True, headers={"Accept": JSON_API}
    )
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    attrs = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(attrs, dict):
        raise HttpFailure(
            SERVICE, "GET /users/me returned an unexpected body", maybe_applied=False
        )
    return {"id": data.get("id"), **attrs}


def _account(me: Mapping[str, object]) -> dict[str, str]:
    return {
        "user_id": str(me.get("id") or ""),
        "username": str(me.get("username") or ""),
        "country_code": str(me.get("country") or ""),
    }


async def resolve_country(api: HttpClient, credentials: CredentialStore) -> str:
    """The user's country, which most reads need; cached in the credentials."""
    cached = credentials.get(SERVICE).get("country_code")
    if cached:
        return cached
    account = _account(await _get_me(api))
    if not account["country_code"]:
        raise HttpFailure(
            SERVICE, "Tidal did not say your country", maybe_applied=False
        )
    credentials.save(SERVICE, account)
    return account["country_code"]


class TidalPlugin:
    service = SERVICE
    display_name = "Tidal"
    category: Category = "streaming"
    capabilities = CAPABILITIES
    limits = LIMITS
    quota_policy = QuotaPolicy()

    def setup_guide(self) -> SetupGuide:
        return SetupGuide(
            self.service,
            self.display_name,
            self.category,
            ("A Tidal subscription", "A free Tidal developer account"),
            (
                SetupStep(
                    "before",
                    "Before you start",
                    StepKind.INFO,
                    body=(
                        "LinerSync signs in to Tidal with an app that you create "
                        "yourself. It's free and takes a few minutes."
                    ),
                ),
                SetupStep(
                    "create_app",
                    "Create a Tidal app",
                    StepKind.INFO,
                    body=(
                        "Open the Tidal developer dashboard and create an app. Add "
                        "the Redirect URI below to the app, and give it the scopes "
                        "playlists.read, playlists.write, search.read, user.read, "
                        "collection.read and collection.write."
                    ),
                    links=(
                        Link(
                            "Tidal developer dashboard",
                            "https://developer.tidal.com/dashboard",
                        ),
                    ),
                    copy_values=(CopyValue("Redirect URI", "{redirect_uri}"),),
                ),
                SetupStep(
                    "client_id",
                    "Enter your Client ID",
                    StepKind.FORM,
                    body="Copy the Client ID from your app's page.",
                    fields=(
                        SetupField(
                            "client_id",
                            "Client ID",
                            pattern="[A-Za-z0-9]{8,64}",
                            pattern_hint=(
                                "Copy the Client ID exactly as the dashboard shows it"
                            ),
                        ),
                    ),
                ),
                SetupStep(
                    "sign_in",
                    "Sign in to Tidal",
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
        token_client, auth, api = _clients(ctx)
        async with token_client, api:
            await auth.exchange_code(code, redirect_uri, keep["code_verifier"])
            ctx.credentials.save(SERVICE, _account(await _get_me(api)))

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        token_client, _, api = _clients(ctx)
        async with token_client, api:
            try:
                me = await _get_me(api)
            except HttpFailure as e:
                return connection_failed(e, "Sign in to Tidal again")
        account = _account(me)
        ctx.credentials.save(SERVICE, account)
        return ConnectionTest(True, "Connected", account["username"] or None)

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return None  # Tidal documents no lifetime for a sign-in

    def create(self, ctx: ConnectorContext) -> Connector:
        _, _, api = _clients(ctx)
        artwork = artwork_hasher(ctx, SERVICE)

        async def country() -> str:
            return await resolve_country(api, ctx.credentials)

        return TidalConnector(api, artwork, country, clock=ctx.clock)


TIDAL_PLUGIN = TidalPlugin()
PLUGIN = TIDAL_PLUGIN
