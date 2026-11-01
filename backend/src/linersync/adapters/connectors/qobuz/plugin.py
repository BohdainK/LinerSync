"""The Qobuz plugin: setup guide, sign-in and connection test.

Experimental: Qobuz offers no API access to apps like LinerSync, so this
talks to the private API its web player uses, with the app ID and user auth
token the user copies from their own signed-in web player.
"""

from collections.abc import Mapping
from datetime import datetime

from linersync.adapters.connectors.kit.http import HttpFailure
from linersync.adapters.connectors.kit.plugin import (
    Category,
    ConnectionTest,
    ConnectorContext,
    artwork_hasher,
    connection_failed,
    fingerprint,
    http_client,
)
from linersync.adapters.connectors.qobuz.auth import QobuzAuth
from linersync.adapters.connectors.qobuz.connector import (
    CAPABILITIES,
    LIMITS,
    QobuzConnector,
)
from linersync.adapters.connectors.qobuz.parse import API_BASE, SERVICE, parse_account
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

_SIGN_IN_AGAIN = "Sign in to Qobuz again"


class QobuzPlugin:
    service = SERVICE
    display_name = "Qobuz"
    category: Category = "streaming"
    capabilities = CAPABILITIES
    limits = LIMITS
    quota_policy = QuotaPolicy()
    experimental = True

    def setup_guide(self) -> SetupGuide:
        return SetupGuide(
            self.service,
            self.display_name,
            self.category,
            ("A Qobuz subscription", "A desktop browser"),
            (
                SetupStep(
                    "before",
                    "Before you start",
                    StepKind.INFO,
                    body=(
                        "Experimental: Qobuz has no API for apps like LinerSync, so "
                        "LinerSync uses the unofficial one behind Qobuz's web player. "
                        "It may stop working whenever Qobuz updates its web player, "
                        "and using it is against Qobuz's terms.\n\n"
                        "LinerSync signs in with the token of a web player where "
                        "you're signed in. Anyone with this token can use your Qobuz "
                        "account, so it is stored encrypted and never shown again."
                    ),
                    links=(Link("Qobuz web player", "https://play.qobuz.com"),),
                ),
                SetupStep(
                    "token",
                    "Paste your app ID and token",
                    StepKind.FORM,
                    body=(
                        "Open play.qobuz.com signed in, open the developer tools "
                        "(F12), go to Network, and click any request to "
                        "www.qobuz.com/api.json. Copy the values of its X-App-Id and "
                        "X-User-Auth-Token request headers."
                    ),
                    fields=(
                        SetupField(
                            "app_id",
                            "App ID",
                            pattern="[0-9]{6,12}",
                            pattern_hint="Copy only the number after X-App-Id",
                        ),
                        SetupField(
                            "user_auth_token",
                            "User auth token",
                            FieldKind.SECRET,
                            help="The value of the X-User-Auth-Token header",
                            pattern=r"[A-Za-z0-9._~+/=-]{8,}",
                            pattern_hint=(
                                "Copy only the value after X-User-Auth-Token:"
                            ),
                        ),
                    ),
                ),
                SetupStep("test", "Test the connection", StepKind.TEST),
            ),
        )

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        return validate_values(self.setup_guide(), values)

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        auth = QobuzAuth(ctx.credentials)
        async with http_client(ctx, SERVICE, API_BASE, auth=auth) as api:
            try:
                response = await api.request(
                    "GET",
                    f"{API_BASE}/playlist/getUserPlaylists",
                    idempotent=True,
                    params={"limit": 1},
                )
                body = response.json()
                user_id, login = parse_account(body if isinstance(body, dict) else {})
            except HttpFailure as e:
                if e.status == 400 and not e.auth_required:
                    return ConnectionTest(
                        False,
                        "Qobuz refused the app ID; copy it again from the web player",
                    )
                return connection_failed(e, _SIGN_IN_AGAIN)
            except ValueError:
                return ConnectionTest(False, "Qobuz's reply was not understood")
        # The user id belongs to this token: a token for another account must
        # not inherit the last one's, even when Qobuz's reply names no user.
        token = ctx.credentials.get(SERVICE).get("user_auth_token", "")
        ctx.credentials.save(
            SERVICE,
            {"user_id": user_id, "user_id_token": fingerprint(token)}
            if user_id is not None
            else {"user_id": "", "user_id_token": ""},
        )
        return ConnectionTest(True, "Connected", login)

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return None  # Qobuz documents no lifetime for a token

    def create(self, ctx: ConnectorContext) -> Connector:
        api = http_client(ctx, SERVICE, API_BASE, auth=QobuzAuth(ctx.credentials))
        # Artwork goes over a plain client: its host must never see the token.
        artwork = artwork_hasher(ctx, SERVICE)
        creds = ctx.credentials.get(SERVICE)
        user_id = creds.get("user_id") or None
        owner_token = creds.get("user_id_token")
        if owner_token and owner_token != fingerprint(creds.get("user_auth_token", "")):
            user_id = None  # learnt for an earlier token
        return QobuzConnector(api, artwork, user_id=user_id, clock=ctx.clock)


QOBUZ_PLUGIN = QobuzPlugin()
PLUGIN = QOBUZ_PLUGIN
