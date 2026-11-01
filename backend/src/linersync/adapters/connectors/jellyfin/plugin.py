"""The Jellyfin plugin: an admin API key plus the user whose playlists to read."""

from collections.abc import Mapping
from datetime import datetime

from linersync.adapters.connectors.jellyfin.auth import ApiKeyAuth
from linersync.adapters.connectors.jellyfin.connector import (
    CAPABILITIES,
    LIMITS,
    JellyfinConnector,
)
from linersync.adapters.connectors.jellyfin.parse import SERVICE
from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.plugin import (
    Category,
    ConnectionTest,
    ConnectorContext,
    artwork_hasher,
    http_client,
)
from linersync.ports.connector import Connector
from linersync.ports.credentials import CredentialStore
from linersync.ports.errors import ReadFailed
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

_DOCS = "https://jellyfin.org/docs/general/server/settings/api-keys/"


def base_url(credentials: Mapping[str, str]) -> str:
    """The server URL as entered, without a trailing slash; a path prefix
    (https://host/jellyfin) is kept."""
    return credentials["server_url"].strip().rstrip("/")


async def resolve_user_id(
    api: HttpClient, base: str, credentials: CredentialStore, *, fresh: bool = False
) -> str:
    """The id of the configured username on the configured server, cached in
    the credentials until either changes (`fresh` skips the cache). Names match
    case-insensitively, as Jellyfin's do."""
    values = credentials.get(SERVICE)
    username = values.get("username", "").strip()
    key = f"{base}|{username}"
    cached = values.get("user_id")
    if cached and values.get("user_id_for") == key and not fresh:
        return cached
    response = await api.request("GET", f"{base}/Users", idempotent=True)
    users = response.json()
    wanted = username.casefold()
    for user in users if isinstance(users, list) else []:
        if isinstance(user, dict) and str(user.get("Name", "")).casefold() == wanted:
            user_id = str(user["Id"])
            credentials.save(SERVICE, {"user_id": user_id, "user_id_for": key})
            return user_id
    raise ReadFailed(SERVICE, f'No Jellyfin user named "{username}"')


def _api_client(ctx: ConnectorContext) -> HttpClient:
    auth = ApiKeyAuth(ctx.credentials.get(SERVICE).get("api_key", ""))
    return http_client(ctx, SERVICE, auth=auth)


class JellyfinPlugin:
    service = SERVICE
    display_name = "Jellyfin"
    category: Category = "self_hosted"
    capabilities = CAPABILITIES
    limits = LIMITS
    quota_policy = QuotaPolicy()

    def setup_guide(self) -> SetupGuide:
        return SetupGuide(
            self.service,
            self.display_name,
            self.category,
            ("A Jellyfin server and an administrator account on it",),
            (
                SetupStep(
                    "before",
                    "Before you start",
                    StepKind.INFO,
                    body=(
                        "LinerSync reads your Jellyfin playlists with an API key. "
                        "It never changes anything on your server."
                    ),
                    links=(Link("Jellyfin API keys", _DOCS),),
                ),
                SetupStep(
                    "api_key",
                    "Create an API key",
                    StepKind.INFO,
                    body=(
                        "In Jellyfin, open Dashboard → API Keys, add a key named "
                        "LinerSync, and copy it."
                    ),
                ),
                SetupStep(
                    "details",
                    "Enter your server details",
                    StepKind.FORM,
                    fields=(
                        SetupField(
                            "server_url",
                            "Server URL",
                            pattern="https?://.+",
                            pattern_hint="Start with http:// or https://",
                            placeholder="http://jellyfin.local:8096",
                        ),
                        SetupField(
                            "api_key",
                            "API key",
                            kind=FieldKind.SECRET,
                            pattern="[0-9a-f]{32}",
                            pattern_hint="An API key is 32 characters, 0–9 and a–f",
                        ),
                        SetupField(
                            "username",
                            "Jellyfin username",
                            help="Whose playlists to read",
                        ),
                    ),
                ),
                SetupStep("test", "Test the connection", StepKind.TEST),
            ),
        )

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        return validate_values(self.setup_guide(), values)

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        values = ctx.credentials.get(SERVICE)
        async with _api_client(ctx) as api:
            try:
                await resolve_user_id(
                    api, base_url(values), ctx.credentials, fresh=True
                )
            except HttpFailure as e:
                if e.auth_required or e.status in (401, 403):
                    return ConnectionTest(False, "Check your Jellyfin API key")
                if e.status is None:
                    return ConnectionTest(
                        False, f"Can't reach Jellyfin at {base_url(values)}"
                    )
                return ConnectionTest(False, e.message)
            except ReadFailed as e:
                return ConnectionTest(False, e.message)
            except (KeyError, TypeError, ValueError):
                return ConnectionTest(False, "Jellyfin's reply was not understood")
        return ConnectionTest(True, "Connected", values.get("username", "").strip())

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return None  # API keys don't expire

    def create(self, ctx: ConnectorContext) -> Connector:
        base = base_url(ctx.credentials.get(SERVICE))
        api = _api_client(ctx)

        async def user_id() -> str:
            return await resolve_user_id(api, base, ctx.credentials)

        # Jellyfin serves images without auth; keep the key off them.
        artwork = artwork_hasher(ctx, SERVICE)
        return JellyfinConnector(api, base, user_id, artwork, clock=ctx.clock)


JELLYFIN_PLUGIN = JellyfinPlugin()
PLUGIN = JELLYFIN_PLUGIN
