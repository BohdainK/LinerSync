"""The Apple Music plugin: setup guide, MusicKit browser sign-in, connection test."""

import importlib.metadata
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta

from linersync.adapters.connectors.apple_music.connector import (
    CAPABILITIES,
    LIMITS,
    AppleMusicConnector,
)
from linersync.adapters.connectors.apple_music.parse import API_BASE, APPLE
from linersync.adapters.connectors.apple_music.token import (
    DeveloperTokens,
    check_private_key,
    make_developer_token,
)
from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.adapters.connectors.kit.plugin import (
    Category,
    ConnectionTest,
    ConnectorContext,
)
from linersync.ports.connector import Connector
from linersync.ports.credentials import CredentialStore
from linersync.ports.quota import QuotaPolicy
from linersync.ports.runtime import Clock
from linersync.ports.setup import (
    FieldKind,
    Link,
    SetupField,
    SetupGuide,
    SetupStep,
    StepKind,
    validate_values,
)

# An estimate: Apple documents no fixed lifetime for a Music-User-Token.
USER_TOKEN_LIFETIME = timedelta(days=180)
_POLICY = QuotaPolicy()
_ID_PATTERN = "[A-Z0-9]{10}"
_BAD_KEY = "Check Team ID, Key ID and key file"
_SIGN_IN_AGAIN = "Sign in to Apple Music again"


class AppleMusicAuth:
    """AuthProvider: the developer token (renewed near expiry) plus the
    Music-User-Token, both read from the credential store on each request."""

    def __init__(self, credentials: CredentialStore, clock: Clock) -> None:
        self._credentials = credentials
        self._clock = clock
        self._tokens: DeveloperTokens | None = None
        self._key: tuple[str, str, str] | None = None

    def _developer_tokens(self) -> DeveloperTokens:
        values = self._credentials.get(APPLE)
        key = (
            values.get("team_id", ""),
            values.get("key_id", ""),
            values.get("private_key", ""),
        )
        if self._tokens is None or key != self._key:
            self._tokens = DeveloperTokens(*key, self._clock)
            self._key = key
        return self._tokens

    def _sign(self, renew: bool) -> str:
        try:
            tokens = self._developer_tokens()
            return tokens.force_renew() if renew else tokens.current()
        except Exception:  # never echo the key or the library's message
            raise HttpFailure(
                APPLE,
                "The Apple Music key could not sign a developer token",
                maybe_applied=False,
                auth_required=True,
            ) from None

    async def headers(self) -> Mapping[str, str]:
        headers = {"Authorization": f"Bearer {self._sign(renew=False)}"}
        user_token = self._credentials.get(APPLE).get("music_user_token")
        if user_token:
            headers["Music-User-Token"] = user_token
        return headers

    async def refresh(self) -> bool:
        self._sign(renew=True)
        return True


def _api_client(ctx: ConnectorContext, auth: AppleMusicAuth) -> HttpClient:
    return HttpClient(
        APPLE,
        API_BASE,
        settings=ctx.settings.http,
        quota=ctx.quota,
        quota_policy=_POLICY,
        clock=ctx.clock,
        auth=auth,
        transport=ctx.transport,
        sleep=ctx.sleep,
    )


def _artwork_client(ctx: ConnectorContext) -> HttpClient:
    """Unauthenticated: artwork lives on CDN hosts that must never see the
    developer token or the Music-User-Token."""
    return HttpClient(
        APPLE,
        "",
        settings=ctx.settings.http,
        quota=ctx.quota,
        quota_policy=QuotaPolicy(),
        clock=ctx.clock,
        transport=ctx.transport,
        sleep=ctx.sleep,
    )


async def _fetch_storefront(api: HttpClient) -> str:
    response = await api.request("GET", f"{API_BASE}/v1/me/storefront", idempotent=True)
    try:
        body = response.json()
        storefront = body["data"][0]["id"]
    except (ValueError, KeyError, IndexError, TypeError):
        storefront = None
    if not isinstance(storefront, str) or not storefront:
        raise HttpFailure(
            APPLE,
            "GET /v1/me/storefront returned an unexpected body",
            maybe_applied=False,
        )
    return storefront


def _storefront_source(
    ctx: ConnectorContext, api: HttpClient
) -> Callable[[], Awaitable[str]]:
    """The saved storefront, else fetched once from Apple, cached and saved."""
    cached: list[str] = []

    async def storefront() -> str:
        if cached:
            return cached[0]
        saved = ctx.credentials.get(APPLE).get("storefront")
        if not saved:
            saved = await _fetch_storefront(api)
            ctx.credentials.save(APPLE, {"storefront": saved})
        cached.append(saved)
        return saved

    return storefront


class AppleMusicPlugin:
    service = APPLE
    display_name = "Apple Music"
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
                "A paid Apple Developer Program membership "
                "(needed to create a MusicKit key)",
                "An Apple Music subscription on the account you sign in with",
            ),
            (
                SetupStep(
                    "team_id",
                    "Find your Team ID",
                    StepKind.INFO,
                    body=(
                        "Sign in to your Apple Developer account. Your Team ID is "
                        "shown under Membership details: 10 letters and digits."
                    ),
                    links=(
                        Link(
                            "Apple Developer account",
                            "https://developer.apple.com/account",
                        ),
                    ),
                ),
                SetupStep(
                    "media_id",
                    "Create a Media ID",
                    StepKind.INFO,
                    body=(
                        "Under Certificates, Identifiers & Profiles, add an "
                        "identifier of type Media IDs, and enable MusicKit for it."
                    ),
                ),
                SetupStep(
                    "key",
                    "Create a MusicKit key",
                    StepKind.INFO,
                    body=(
                        "Under Keys, create a key with Media Services (MusicKit) "
                        "enabled and link it to your Media ID. Note its Key ID, "
                        "then download the .p8 file. Keep it safe: Apple lets you "
                        "download it only once."
                    ),
                ),
                SetupStep(
                    "details",
                    "Enter your key details",
                    StepKind.FORM,
                    body="Paste the whole contents of the .p8 file, including "
                    "the BEGIN and END lines.",
                    fields=(
                        SetupField(
                            "team_id",
                            "Team ID",
                            pattern=_ID_PATTERN,
                            pattern_hint="A Team ID is 10 characters, A–Z and 0–9",
                        ),
                        SetupField(
                            "key_id",
                            "Key ID",
                            pattern=_ID_PATTERN,
                            pattern_hint="A Key ID is 10 characters, A–Z and 0–9",
                        ),
                        SetupField(
                            "private_key",
                            "Private key (.p8 file)",
                            kind=FieldKind.MULTILINE_SECRET,
                            placeholder="-----BEGIN PRIVATE KEY-----",
                        ),
                    ),
                ),
                SetupStep(
                    "sign_in",
                    "Sign in to Apple Music",
                    StepKind.BROWSER_SIGN_IN,
                    body=(
                        "Sign in with your Apple ID and allow LinerSync to access "
                        "your music library. Apple's sign-in only works when "
                        "LinerSync is opened over HTTPS or at http://127.0.0.1."
                    ),
                ),
                SetupStep("test", "Test the connection", StepKind.TEST),
            ),
        )

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        problems = validate_values(self.setup_guide(), values)
        if "private_key" not in problems:
            message = check_private_key(values["private_key"].strip())
            if message is not None:
                problems["private_key"] = message
        return problems

    def sign_in_config(self, credentials: Mapping[str, str]) -> dict[str, str]:
        token = make_developer_token(
            credentials["team_id"],
            credentials["key_id"],
            credentials["private_key"],
            datetime.now(UTC),
        )
        return {
            "developer_token": token,
            "app_name": "LinerSync",
            "app_build": importlib.metadata.version("linersync"),
        }

    def accept_sign_in(self, ctx: ConnectorContext, result: Mapping[str, str]) -> None:
        token = (result.get("music_user_token") or "").strip()
        if not token:
            raise ValueError("Apple Music sign-in returned no Music-User-Token")
        ctx.credentials.save(
            APPLE,
            {
                "music_user_token": token,
                "music_user_token_saved_at": ctx.clock.now().isoformat(),
                # The sign-in may be another Apple ID: fetch its storefront again.
                "storefront": "",
            },
        )

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        api = _api_client(ctx, AppleMusicAuth(ctx.credentials, ctx.clock))
        try:
            try:
                await api.request(
                    "GET", f"{API_BASE}/v1/storefronts/us", idempotent=True
                )
            except HttpFailure as e:
                if e.auth_required:
                    return ConnectionTest(False, _BAD_KEY)
                return ConnectionTest(False, e.message)
            if not ctx.credentials.get(APPLE).get("music_user_token"):
                return ConnectionTest(
                    False, "Your key works. Sign in to Apple Music to finish."
                )
            try:
                storefront = await _fetch_storefront(api)
            except HttpFailure as e:
                if e.auth_required or e.status == 403:
                    return ConnectionTest(False, _SIGN_IN_AGAIN)
                return ConnectionTest(False, e.message)
        finally:
            await api.aclose()
        ctx.credentials.save(APPLE, {"storefront": storefront})
        return ConnectionTest(True, "Connected")

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        raw = credentials.get("music_user_token_saved_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw) + USER_TOKEN_LIFETIME
        except ValueError:
            return None

    def create(self, ctx: ConnectorContext) -> Connector:
        api = _api_client(ctx, AppleMusicAuth(ctx.credentials, ctx.clock))
        artwork = (
            ArtworkHasher(http_fetcher(_artwork_client(ctx)))
            if ctx.settings.hash_artwork
            else None
        )
        return AppleMusicConnector(
            api,
            _storefront_source(ctx, api),
            artwork,
            ctx.sleep,
            clock=ctx.clock,
        )


APPLE_MUSIC_PLUGIN = AppleMusicPlugin()
PLUGIN = APPLE_MUSIC_PLUGIN
