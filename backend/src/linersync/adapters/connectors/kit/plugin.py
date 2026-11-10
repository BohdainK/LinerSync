"""What a connector package exposes to the application."""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

import httpx

from linersync.adapters.connectors.kit.http import (
    AuthProvider,
    HttpClient,
    HttpFailure,
    HttpSettings,
)
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.connector import Connector, ConnectorLimits
from linersync.ports.credentials import CredentialStore
from linersync.ports.quota import QuotaPolicy, QuotaRecorder
from linersync.ports.runtime import Clock
from linersync.ports.setup import SetupGuide

Category = Literal["streaming", "self_hosted"]
OAUTH_CALLBACK_PATH = "/api/connections/oauth/callback"  # Plan 3 implements this route
_NO_QUOTA = QuotaPolicy()


@dataclass(frozen=True, slots=True)
class ConnectorSettings:
    public_base_url: str = "http://127.0.0.1:8000"
    http: HttpSettings = field(default_factory=HttpSettings)
    hash_artwork: bool = True

    @property
    def oauth_redirect_uri(self) -> str:
        return self.public_base_url.rstrip("/") + OAUTH_CALLBACK_PATH


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    credentials: CredentialStore
    quota: QuotaRecorder
    clock: Clock
    settings: ConnectorSettings = field(default_factory=ConnectorSettings)
    transport: httpx.AsyncBaseTransport | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


def http_client(
    ctx: ConnectorContext,
    service: ServiceId,
    base_url: str = "",
    *,
    auth: AuthProvider | None = None,
    quota_policy: QuotaPolicy = _NO_QUOTA,
    **options: Any,
) -> HttpClient:
    """An HttpClient wired to the context. Without ``auth`` it is the plain
    client that token endpoints and artwork hosts get: those must never see a
    service's credentials. ``options`` go to HttpClient as they are."""
    return HttpClient(
        service,
        base_url,
        settings=ctx.settings.http,
        quota=ctx.quota,
        quota_policy=quota_policy,
        clock=ctx.clock,
        auth=auth,
        transport=ctx.transport,
        sleep=ctx.sleep,
        **options,
    )


def artwork_hasher(ctx: ConnectorContext, service: ServiceId) -> ArtworkHasher | None:
    """A hasher fetching over a plain client, or None when hashing is off."""
    if not ctx.settings.hash_artwork:
        return None
    return ArtworkHasher(http_fetcher(http_client(ctx, service)))


def fingerprint(secret: str) -> str:
    """A short digest that tells whether a stored secret was replaced."""
    return hashlib.sha256(secret.strip().encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ConnectionTest:
    ok: bool
    message: str
    account_name: str | None = None


def connection_failed(e: HttpFailure, sign_in_again: str) -> ConnectionTest:
    """A failed connection test: ``sign_in_again`` when the sign-in is no
    longer accepted, else the failure's own message."""
    return ConnectionTest(False, sign_in_again if e.auth_required else e.message)


@dataclass(frozen=True, slots=True)
class AuthorizeRequest:
    url: str
    keep: dict[str, str]  # stored by the server until the callback


@runtime_checkable
class ConnectorPlugin(Protocol):
    service: ServiceId
    display_name: str
    category: Category
    limits: ConnectorLimits

    @property
    def capabilities(self) -> frozenset[Capability]:
        """Read on every use: a feature flag may change it while the server
        runs (FlagSetup)."""
        ...

    quota_policy: QuotaPolicy

    def setup_guide(self) -> SetupGuide: ...

    def validate(self, values: Mapping[str, str]) -> dict[str, str]:
        """validate_values plus plugin-specific checks."""
        ...

    async def test_connection(self, ctx: ConnectorContext) -> ConnectionTest:
        """Never raises for service errors."""
        ...

    def login_expires_at(self, credentials: Mapping[str, str]) -> datetime | None: ...

    def create(self, ctx: ConnectorContext) -> Connector: ...


def is_experimental(plugin: ConnectorPlugin) -> bool:
    """A plugin may set ``experimental = True`` (optional; absent means False)
    when it relies on unofficial access that can break at any time. The app
    labels such services so their failures don't read as LinerSync's."""
    return bool(getattr(plugin, "experimental", False))


def is_login_expiry_estimated(plugin: ConnectorPlugin) -> bool:
    """A plugin may set ``login_expiry_estimated = True`` (optional; absent
    means False) when ``login_expires_at`` is a guess rather than a deadline
    the service announced, so the app words it as a reminder."""
    return bool(getattr(plugin, "login_expiry_estimated", False))


@dataclass(frozen=True, slots=True)
class FeatureFlag:
    """A switch the user can flip on the Settings page, as it is right now."""

    key: str  # unique within the plugin; the app's id is "<service>.<key>"
    title: str
    description: str
    on: bool
    # Asked before turning it on; "" turns it on without asking.
    confirm: str = ""
    # Why it can't be turned on with this server's setup; "" = it can.
    unavailable: str = ""
    # The environment variable that fixes its value; "" = the user decides.
    locked_by: str = ""
    # Turning it on changes what syncs into this service do in full mode.
    affects_full_syncs: bool = False


class FlagUnavailable(ValueError):
    """The flag is locked by the server or can't be turned on here."""


@runtime_checkable
class FlagSetup(Protocol):
    """Optional: a plugin with feature flags. The server sets each one from
    the stored settings at startup and again whenever the user changes it,
    so ``capabilities`` and ``create`` must read the current value."""

    def feature_flags(self) -> tuple[FeatureFlag, ...]: ...

    def set_flag(self, key: str, on: bool) -> None:
        """KeyError for an unknown key; FlagUnavailable when it can't be set."""
        ...


@runtime_checkable
class OAuthSetup(Protocol):
    def authorize(
        self, credentials: Mapping[str, str], redirect_uri: str, state: str
    ) -> AuthorizeRequest: ...

    async def complete(
        self,
        ctx: ConnectorContext,
        redirect_uri: str,
        code: str,
        keep: Mapping[str, str],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DeviceCodeStart:
    user_code: str  # shown to the user, who types it at verification_url
    verification_url: str
    interval_s: int  # how often to poll
    expires_in_s: int
    keep: dict[str, str]  # stored by the server until the flow ends


DevicePollStatus = Literal["pending", "done", "denied", "expired"]


@dataclass(frozen=True, slots=True)
class DevicePoll:
    status: DevicePollStatus
    message: str = ""
    # A new polling interval, when asked to slow down. The server keeps it and
    # passes it back in ``keep`` as "interval_s".
    interval_s: int | None = None


@runtime_checkable
class DeviceCodeSetup(Protocol):
    """Sign-in by a code the user enters on another page (OAuth device flow)."""

    async def start_device_code(self, ctx: ConnectorContext) -> DeviceCodeStart: ...

    async def poll_device_code(
        self, ctx: ConnectorContext, keep: Mapping[str, str]
    ) -> DevicePoll:
        """Saves the credentials itself when the status is "done"."""
        ...


@runtime_checkable
class BrowserSignInSetup(Protocol):
    def sign_in_config(self, credentials: Mapping[str, str]) -> dict[str, str]: ...

    def accept_sign_in(self, ctx: ConnectorContext, result: Mapping[str, str]) -> None:
        """Raises ValueError on bad input."""
        ...
