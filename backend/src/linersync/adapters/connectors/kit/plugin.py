"""What a connector package exposes to the application."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

import httpx

from linersync.adapters.connectors.kit.http import HttpSettings
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.connector import Connector, ConnectorLimits
from linersync.ports.credentials import CredentialStore
from linersync.ports.quota import QuotaPolicy, QuotaRecorder
from linersync.ports.runtime import Clock
from linersync.ports.setup import SetupGuide

Category = Literal["streaming", "self_hosted"]
OAUTH_CALLBACK_PATH = "/api/connections/oauth/callback"  # Plan 3 implements this route


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


@dataclass(frozen=True, slots=True)
class ConnectionTest:
    ok: bool
    message: str
    account_name: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizeRequest:
    url: str
    keep: dict[str, str]  # stored by the server until the callback


@runtime_checkable
class ConnectorPlugin(Protocol):
    service: ServiceId
    display_name: str
    category: Category
    capabilities: frozenset[Capability]
    limits: ConnectorLimits
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
