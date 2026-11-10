"""The Apple Music plugin: setup guide, sign-in, connection test.

Two ways to authenticate, chosen with LINERSYNC_APPLE_MUSIC_AUTH:

- ``web`` (default): the token Apple's web player uses, read from
  music.apple.com, plus the ``media-user-token`` cookie the user pastes.
  Unofficial, but needs no Apple Developer Program membership.
- ``developer``: a developer token signed with the user's own MusicKit key,
  plus a Music-User-Token from MusicKit JS sign-in in the browser.

Playlist editing (experimental, ``web`` only) also lets LinerSync remove and
reorder songs and delete playlists. That only works by borrowing the web
player's token: Apple's real API (``developer``) has no such writes, so with
``developer`` Apple Music stays add-only. It is a feature flag the user turns
on in Settings; LINERSYNC_APPLE_MUSIC_EDITING=on|off fixes it instead.
"""

import importlib.metadata
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from linersync.adapters.connectors.apple_music.connector import (
    CAPABILITIES,
    EDITING_CAPABILITIES,
    LIMITS,
    AppleMusicConnector,
)
from linersync.adapters.connectors.apple_music.parse import API_BASE, APPLE
from linersync.adapters.connectors.apple_music.token import (
    DeveloperTokens,
    check_private_key,
    make_developer_token,
)
from linersync.adapters.connectors.apple_music.web_token import (
    WEB_HEADERS,
    WEB_PLAYER,
    WebTokens,
)
from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure
from linersync.adapters.connectors.kit.parsing import expires_after
from linersync.adapters.connectors.kit.plugin import (
    Category,
    ConnectionTest,
    ConnectorContext,
    FeatureFlag,
    FlagUnavailable,
    artwork_hasher,
    http_client,
)
from linersync.domain.capabilities import Capability
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
_ID_PATTERN = "[A-Z0-9]{10}"
_BAD_KEY = "Check Team ID, Key ID and key file"
_SIGN_IN_AGAIN = "Sign in to Apple Music again"
_PASTE_AGAIN = "Copy the media-user-token cookie from music.apple.com again"
_WEB_TOKEN_REJECTED = (
    "Apple rejected the web player's token. Apple may have changed its web player."
)
AUTH_MODE_ENV = "LINERSYNC_APPLE_MUSIC_AUTH"
EDITING_ENV = "LINERSYNC_APPLE_MUSIC_EDITING"
EDITING_NOTE = (
    "Experimental: playlist editing is on. LinerSync can remove and reorder "
    "songs and delete playlists on Apple Music. This only works by borrowing "
    "the web player's token; Apple's official API can't do it. It may stop "
    "working, or behave unexpectedly, whenever Apple changes its web player."
)


class AuthMode(StrEnum):
    WEB = "web"
    DEVELOPER = "developer"


def auth_mode_from_env() -> AuthMode:
    raw = os.environ.get(AUTH_MODE_ENV, AuthMode.WEB).strip().lower()
    try:
        return AuthMode(raw)
    except ValueError:
        raise ValueError(
            f"{AUTH_MODE_ENV} must be 'web' or 'developer', got {raw!r}"
        ) from None


EDITING_FLAG = "editing"
_EDITING_TITLE = "Apple Music playlist editing"
_EDITING_DESCRIPTION = (
    "Lets LinerSync remove and reorder songs and delete playlists on Apple "
    "Music, so syncs into Apple Music can run in full and undo can clean up "
    "there. Experimental: it may stop working, or behave unexpectedly, "
    "whenever Apple changes its web player. It only works by borrowing the "
    "web player's token. Apple's official API can't do it, so it doesn't "
    "work with the developer (MusicKit) sign-in."
)
_EDITING_CONFIRM = (
    "Syncs into Apple Music in full mode will start removing and reordering "
    "songs the sync added, from their next run on. Undo will also remove "
    "songs and delete playlists on Apple Music."
)
_EDITING_UNAVAILABLE = (
    f"Apple Music signs in with your own MusicKit key ({AUTH_MODE_ENV}="
    "developer). Apple's official API can't remove, reorder or delete, so "
    f"editing only works with the web player sign-in ({AUTH_MODE_ENV}=web), "
    "which means setting up Apple Music again."
)


def editing_env(mode: AuthMode) -> bool | None:
    """EDITING_ENV's value, or None when it isn't set (the user decides in
    Settings). On is only possible with the web player's token."""
    raw = os.environ.get(EDITING_ENV, "").strip().lower()
    if raw == "":
        return None
    if raw in ("off", "0", "false", "no"):
        return False
    if raw not in ("on", "1", "true", "yes"):
        raise ValueError(f"{EDITING_ENV} must be 'on' or 'off', got {raw!r}")
    if mode is not AuthMode.WEB:
        raise ValueError(
            f"{EDITING_ENV}=on only works with {AUTH_MODE_ENV}=web: removing, "
            "reordering and deleting go through the web player's token, and "
            "Apple's official API (developer) can't do them"
        )
    return True


def editing_from_env(mode: AuthMode) -> bool:
    """Experimental playlist editing as EDITING_ENV sets it, off when unset."""
    return editing_env(mode) is True


class AppleMusicAuth:
    """AuthProvider: the developer token (renewed near expiry) plus the
    Music-User-Token, both read from the credential store on each request.

    With ``web``, the developer token is the web player's, sent with the
    web player's Origin; otherwise it is signed with the user's MusicKit key."""

    def __init__(
        self, credentials: CredentialStore, clock: Clock, web: WebTokens | None = None
    ) -> None:
        self._credentials = credentials
        self._clock = clock
        self._web = web
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
        if self._web is not None:
            token = await self._web.current()
            headers = {"Authorization": f"Bearer {token}", **WEB_HEADERS}
        else:
            headers = {"Authorization": f"Bearer {self._sign(renew=False)}"}
        user_token = self._credentials.get(APPLE).get("music_user_token")
        if user_token:
            headers["Music-User-Token"] = user_token
        return headers

    async def refresh(self) -> bool:
        if self._web is not None:
            await self._web.force_renew()
        else:
            self._sign(renew=True)
        return True


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
    limits = LIMITS
    quota_policy = QuotaPolicy()

    def __init__(
        self,
        mode: AuthMode = AuthMode.WEB,
        *,
        editing: bool = False,
        editing_locked: bool = False,
    ) -> None:
        """``editing_locked``: EDITING_ENV set it, so Settings can't change it."""
        if editing and mode is not AuthMode.WEB:
            raise ValueError("Apple Music editing needs the web player's token")
        self.mode = mode
        self.editing = editing
        self.editing_locked = editing_locked

    @property
    def capabilities(self) -> frozenset[Capability]:
        return EDITING_CAPABILITIES if self.editing else CAPABILITIES

    def feature_flags(self) -> tuple[FeatureFlag, ...]:
        web = self.mode is AuthMode.WEB
        return (
            FeatureFlag(
                EDITING_FLAG,
                _EDITING_TITLE,
                _EDITING_DESCRIPTION,
                on=self.editing,
                confirm=_EDITING_CONFIRM,
                unavailable="" if web else _EDITING_UNAVAILABLE,
                locked_by=EDITING_ENV if self.editing_locked else "",
                affects_full_syncs=True,
            ),
        )

    def set_flag(self, key: str, on: bool) -> None:
        if key != EDITING_FLAG:
            raise KeyError(key)
        if on == self.editing:
            return
        if self.editing_locked:
            raise FlagUnavailable(f"{EDITING_ENV} sets this on the server")
        if on and self.mode is not AuthMode.WEB:
            raise FlagUnavailable(_EDITING_UNAVAILABLE)
        self.editing = on

    def _api(self, ctx: ConnectorContext) -> HttpClient:
        web = None
        if self.mode is AuthMode.WEB:
            # Unauthenticated: the web player page must never see the
            # developer token or the Music-User-Token.
            web = WebTokens(ctx.credentials, ctx.clock, http_client(ctx, APPLE))
        auth = AppleMusicAuth(ctx.credentials, ctx.clock, web)
        return http_client(ctx, APPLE, API_BASE, auth=auth)

    def setup_guide(self) -> SetupGuide:
        if self.mode is AuthMode.WEB:
            return self._web_guide()
        return self._developer_guide()

    def _web_guide(self) -> SetupGuide:
        return SetupGuide(
            self.service,
            self.display_name,
            self.category,
            ("An Apple Music subscription on the account you sign in with",),
            (
                SetupStep(
                    "web_sign_in",
                    "Sign in on music.apple.com",
                    StepKind.INFO,
                    body=(
                        "Open Apple Music on the web and sign in with your Apple "
                        "ID. LinerSync talks to Apple the way that web player "
                        "does. This is unofficial: it can stop working when "
                        "Apple changes the web player."
                        + (f" {EDITING_NOTE}" if self.editing else "")
                    ),
                    links=(Link("Apple Music on the web", WEB_PLAYER),),
                ),
                SetupStep(
                    "user_token",
                    "Copy your media-user-token",
                    StepKind.FORM,
                    body=(
                        "In the same browser, open the developer tools. Under "
                        "Application (Chrome, Edge) or Storage (Firefox, Safari), "
                        "open Cookies for https://music.apple.com and copy the "
                        "value of the media-user-token cookie."
                    ),
                    fields=(
                        SetupField(
                            "music_user_token",
                            "media-user-token",
                            kind=FieldKind.SECRET,
                        ),
                    ),
                ),
                SetupStep("test", "Test the connection", StepKind.TEST),
            ),
        )

    def _developer_guide(self) -> SetupGuide:
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
        if self.mode is AuthMode.DEVELOPER and "private_key" not in problems:
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
        web = self.mode is AuthMode.WEB
        async with self._api(ctx) as api:
            try:
                await api.request(
                    "GET", f"{API_BASE}/v1/storefronts/us", idempotent=True
                )
            except HttpFailure as e:
                if web and e.status == 401:
                    return ConnectionTest(False, _WEB_TOKEN_REJECTED)
                if e.auth_required and not web:
                    return ConnectionTest(False, _BAD_KEY)
                return ConnectionTest(False, e.message)
            if not ctx.credentials.get(APPLE).get("music_user_token"):
                if web:
                    return ConnectionTest(False, "Paste your media-user-token")
                return ConnectionTest(
                    False, "Your key works. Sign in to Apple Music to finish."
                )
            try:
                storefront = await _fetch_storefront(api)
            except HttpFailure as e:
                if e.auth_required or e.status == 403:
                    return ConnectionTest(
                        False, _PASTE_AGAIN if web else _SIGN_IN_AGAIN
                    )
                return ConnectionTest(False, e.message)
        ctx.credentials.save(APPLE, {"storefront": storefront})
        return ConnectionTest(True, "Connected")

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None:
        return expires_after(
            credentials.get("music_user_token_saved_at"), USER_TOKEN_LIFETIME
        )

    def create(self, ctx: ConnectorContext) -> Connector:
        api = self._api(ctx)
        return AppleMusicConnector(
            api,
            _storefront_source(ctx, api),
            artwork_hasher(ctx, APPLE),
            ctx.sleep,
            clock=ctx.clock,
            editing=self.editing,
        )


_MODE = auth_mode_from_env()
_EDITING = editing_env(_MODE)
APPLE_MUSIC_PLUGIN = AppleMusicPlugin(
    _MODE, editing=_EDITING is True, editing_locked=_EDITING is not None
)
PLUGIN = APPLE_MUSIC_PLUGIN
