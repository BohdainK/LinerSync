"""Connection setup: form values, OAuth, browser sign-in, status and tests."""

import builtins
import json
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from linersync.adapters.connectors.kit.discovery import PluginRegistry
from linersync.adapters.connectors.kit.plugin import (
    BrowserSignInSetup,
    ConnectionTest,
    ConnectorContext,
    ConnectorPlugin,
    ConnectorSettings,
    DeviceCodeSetup,
    DeviceCodeStart,
    DevicePoll,
    OAuthSetup,
    is_experimental,
    is_login_expiry_estimated,
)
from linersync.adapters.sqlite.connections import (
    ConnectionRecord,
    SqliteConnectionRecords,
)
from linersync.adapters.sqlite.settings import SqliteSettings
from linersync.application.config import EngineConfig
from linersync.domain.capabilities import Capability
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId
from linersync.ports.connector import Connector
from linersync.ports.credentials import CredentialStore
from linersync.ports.errors import ConnectorError
from linersync.ports.quota import QuotaRecorder
from linersync.ports.runtime import Clock
from linersync.ports.setup import FieldKind, SetupGuide, render_guide

logger = logging.getLogger(__name__)

OAUTH_STATE_LIFETIME = timedelta(minutes=10)
_EXPIRED = "This sign-in link expired. Start again."
_CODE_EXPIRED = "This code expired. Get a new one."
# The pending device-code sign-in, kept with the credentials so a restart
# doesn't lose a code the user is about to enter.
_DEVICE_KEY = "pending_device_code"
_SECRET_KINDS = frozenset({FieldKind.SECRET, FieldKind.MULTILINE_SECRET})


class ConnectionStatus(StrEnum):
    NOT_SET_UP = "not_set_up"
    UNTESTED = "untested"
    CONNECTED = "connected"
    NEEDS_SIGN_IN = "needs_sign_in"
    FAILING = "failing"


@dataclass(frozen=True, slots=True)
class ConnectionInfo:
    service: ServiceId
    display_name: str
    category: str
    capabilities: tuple[Capability, ...]
    experimental: bool
    status: ConnectionStatus
    message: str
    account_name: str | None
    checked_at: datetime | None
    login_expires_at: datetime | None
    values: dict[str, str]  # stored values of TEXT fields only
    secrets_set: tuple[str, ...]  # keys of stored SECRET / MULTILINE_SECRET fields
    login_expiry_estimated: bool = False  # login_expires_at is a guess


class OAuthFailed(Exception):
    def __init__(self, message: str, service: ServiceId | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.service = service


class NotSetUp(ValueError):
    """The service has no stored credentials yet."""


class WrongSetup(ValueError):
    """The service signs in some other way than the step asked for."""


@dataclass(frozen=True, slots=True)
class _PendingOAuth:
    service: ServiceId
    keep: dict[str, str]
    expires: datetime


@dataclass(frozen=True, slots=True)
class _PendingDevice:
    start: DeviceCodeStart  # the current interval; keep has it as "interval_s"
    expires: datetime

    def dump(self) -> str:
        s = self.start
        return json.dumps(
            {
                "user_code": s.user_code,
                "verification_url": s.verification_url,
                "interval_s": s.interval_s,
                "keep": s.keep,
                "expires": self.expires.isoformat(),
            }
        )

    @staticmethod
    def load(raw: str) -> "_PendingDevice | None":
        try:
            data = json.loads(raw)
            start = DeviceCodeStart(
                str(data["user_code"]),
                str(data["verification_url"]),
                int(data["interval_s"]),
                0,
                {str(k): str(v) for k, v in dict(data["keep"]).items()},
            )
            return _PendingDevice(start, datetime.fromisoformat(data["expires"]))
        except (KeyError, TypeError, ValueError):
            return None


class ConnectionManager:
    """Also serves as the JobService's JobEnvironment."""

    def __init__(
        self,
        registry: PluginRegistry,
        credentials: CredentialStore,
        quota: QuotaRecorder,
        records: SqliteConnectionRecords,
        clock: Clock,
        settings: ConnectorSettings,
        app_settings: SqliteSettings | None = None,
    ) -> None:
        self._registry = registry
        self._app_settings = app_settings
        self._credentials = credentials
        self._quota = quota
        self._records = records
        self._clock = clock
        self._settings = settings
        self._pending: dict[str, _PendingOAuth] = {}
        self._devices: dict[ServiceId, _PendingDevice] = {}

    # -- lookups ----------------------------------------------------------
    def plugin(self, service: ServiceId) -> ConnectorPlugin:
        return self._registry.get(service)

    def _ctx(self) -> ConnectorContext:
        return ConnectorContext(
            self._credentials, self._quota, self._clock, self._settings
        )

    def _require_set_up(self, service: ServiceId) -> dict[str, str]:
        stored = self._credentials.get(service)
        if not stored:
            name = self.plugin(service).display_name
            raise NotSetUp(f"Set up {name} first")
        return stored

    def _plugin_as[P](self, service: ServiceId, kind: type[P], how: str) -> P:
        """The plugin as a `kind` setup, else WrongSetup ("doesn't use `how`").
        `kind` is a runtime-checkable Protocol, which mypy only accepts as
        `type[P]` with an ignore at the call (python/mypy#4717)."""
        plugin = self.plugin(service)
        if not isinstance(plugin, kind):
            raise WrongSetup(f"{plugin.display_name} doesn't use {how}")
        return plugin

    def list(self) -> list[ConnectionInfo]:
        infos = [self.info(service) for service in self._registry.plugins]
        return sorted(infos, key=lambda i: (i.display_name.casefold(), i.service))

    def set_up(self) -> builtins.list[ConnectionInfo]:
        """The services with stored credentials, in `list` order."""
        return [i for i in self.list() if i.status is not ConnectionStatus.NOT_SET_UP]

    def info(self, service: ServiceId) -> ConnectionInfo:
        plugin = self.plugin(service)
        stored = self._credentials.get(service)
        fields = plugin.setup_guide().fields()
        status = ConnectionStatus.NOT_SET_UP
        message = ""
        account_name: str | None = None
        checked_at: datetime | None = None
        expires: datetime | None = None
        if stored:
            record = self._records.get(service)
            if record is None:
                status = ConnectionStatus.UNTESTED
            else:
                status = ConnectionStatus(record.status)
                message = record.message
                account_name = record.account_name
                checked_at = record.checked_at
            expires = plugin.login_expires_at(stored)
        return ConnectionInfo(
            service=service,
            display_name=plugin.display_name,
            category=plugin.category,
            capabilities=tuple(sorted(plugin.capabilities)),
            experimental=is_experimental(plugin),
            status=status,
            message=message,
            account_name=account_name,
            checked_at=checked_at,
            login_expires_at=expires,
            login_expiry_estimated=is_login_expiry_estimated(plugin),
            values={
                f.key: stored[f.key]
                for f in fields
                if f.kind is FieldKind.TEXT and f.key in stored
            },
            secrets_set=tuple(
                f.key for f in fields if f.kind in _SECRET_KINDS and f.key in stored
            ),
        )

    def guide(self, service: ServiceId) -> SetupGuide:
        return render_guide(
            self.plugin(service).setup_guide(),
            {
                "redirect_uri": self._settings.oauth_redirect_uri,
                "public_url": self._settings.public_base_url.rstrip("/"),
            },
        )

    # -- form values and tests -------------------------------------------
    def save_values(
        self, service: ServiceId, values: Mapping[str, str]
    ) -> dict[str, str]:
        plugin = self.plugin(service)
        fields = {f.key: f for f in plugin.setup_guide().fields()}
        stored = self._credentials.get(service)
        problems = {key: "Unknown field" for key in values if key not in fields}
        merged = {key: stored[key] for key in fields if key in stored}
        for key, raw in values.items():
            field = fields.get(key)
            if field is None:
                continue
            value = raw.strip()
            if not value and field.kind in _SECRET_KINDS and key in stored:
                continue
            merged[key] = value
        problems.update(plugin.validate(merged))
        if problems:
            return problems
        self._credentials.save(service, merged)
        self._records.save(service, ConnectionRecord(ConnectionStatus.UNTESTED))
        return {}

    async def test(self, service: ServiceId) -> ConnectionTest:
        plugin = self.plugin(service)
        if not self._credentials.get(service):
            return ConnectionTest(False, f"Set up {plugin.display_name} first")
        try:
            result = await plugin.test_connection(self._ctx())
        except ConnectorError as e:
            result = ConnectionTest(False, e.message or "The connection test failed")
        except Exception as e:  # noqa: BLE001 - plugin code; never echo its text
            logger.warning("%s connection test raised %s", service, type(e).__name__)
            result = ConnectionTest(False, "The connection test failed")
        record = ConnectionRecord(
            ConnectionStatus.CONNECTED if result.ok else ConnectionStatus.FAILING,
            result.message,
            result.account_name if result.ok else None,
            self._clock.now(),
        )
        self._records.save(service, record)
        return result

    # -- OAuth ------------------------------------------------------------
    def start_oauth(self, service: ServiceId) -> str:
        plugin = self._plugin_as(service, OAuthSetup, "a sign-in link")  # type: ignore[type-abstract]
        stored = self._require_set_up(service)
        now = self._clock.now()
        self._pending = {s: p for s, p in self._pending.items() if p.expires > now}
        state = secrets.token_urlsafe(32)
        request = plugin.authorize(stored, self._settings.oauth_redirect_uri, state)
        self._pending[state] = _PendingOAuth(
            service, dict(request.keep), now + OAUTH_STATE_LIFETIME
        )
        return request.url

    async def finish_oauth(
        self, state: str | None, code: str | None, error: str | None
    ) -> ServiceId:
        pending = self._pending.pop(state, None) if state else None
        if pending is None or pending.expires <= self._clock.now():
            raise OAuthFailed(_EXPIRED)
        service = pending.service
        plugin = self._registry.plugins.get(service)
        if plugin is None or not isinstance(plugin, OAuthSetup):
            raise OAuthFailed(_EXPIRED)
        name = plugin.display_name
        if error:
            raise OAuthFailed(f"{name} sign-in failed: {error}", service)
        if not code:
            raise OAuthFailed(f"{name} sign-in failed: no code was returned", service)
        try:
            await plugin.complete(
                self._ctx(), self._settings.oauth_redirect_uri, code, pending.keep
            )
        except ConnectorError as e:
            detail = e.message or "try again"
            raise OAuthFailed(f"{name} sign-in failed: {detail}", service) from None
        except Exception as e:  # noqa: BLE001 - plugin code; never echo its text
            logger.warning("%s sign-in failed with %s", service, type(e).__name__)
            raise OAuthFailed(f"{name} sign-in failed. Try again.", service) from None
        self._records.save(service, ConnectionRecord(ConnectionStatus.UNTESTED))
        return service

    # -- device code ------------------------------------------------------
    def _device(self, service: ServiceId) -> DeviceCodeSetup:
        return self._plugin_as(service, DeviceCodeSetup, "a device code")  # type: ignore[type-abstract]

    def _pending_device(self, service: ServiceId) -> _PendingDevice | None:
        """The unexpired pending sign-in, from memory or from before a restart."""
        pending = self._devices.get(service)
        if pending is None:
            raw = self._credentials.get(service).get(_DEVICE_KEY, "")
            pending = _PendingDevice.load(raw) if raw else None
        if pending is not None and pending.expires <= self._clock.now():
            pending = None
        if pending is None:
            self._set_pending_device(service, None)
        else:
            self._devices[service] = pending
        return pending

    def _set_pending_device(
        self, service: ServiceId, pending: _PendingDevice | None
    ) -> None:
        if pending is None:
            self._devices.pop(service, None)
            if self._credentials.get(service).get(_DEVICE_KEY):
                self._credentials.save(service, {_DEVICE_KEY: ""})
            return
        self._devices[service] = pending
        self._credentials.save(service, {_DEVICE_KEY: pending.dump()})

    async def start_device_code(self, service: ServiceId) -> DeviceCodeStart:
        plugin = self._device(service)
        self._require_set_up(service)
        pending = self._pending_device(service)
        if pending is not None:
            # Another tab already has a code: show the same one, so neither
            # tab's code stops working.
            left = pending.expires - self._clock.now()
            return replace(pending.start, expires_in_s=int(left.total_seconds()))
        start = await plugin.start_device_code(self._ctx())
        expires = self._clock.now() + timedelta(seconds=start.expires_in_s)
        self._set_pending_device(
            service, _PendingDevice(replace(start, keep=dict(start.keep)), expires)
        )
        return start

    async def poll_device_code(self, service: ServiceId) -> DevicePoll:
        plugin = self._device(service)
        pending = self._pending_device(service)
        if pending is None:
            return DevicePoll("expired", _CODE_EXPIRED)
        result = await plugin.poll_device_code(self._ctx(), pending.start.keep)
        if result.status == "expired" and not result.message:
            result = DevicePoll("expired", _CODE_EXPIRED)
        if result.status != "pending":
            self._set_pending_device(service, None)
        elif result.interval_s is not None:
            interval = result.interval_s
            keep = {**pending.start.keep, "interval_s": str(interval)}
            start = replace(pending.start, interval_s=interval, keep=keep)
            self._set_pending_device(service, replace(pending, start=start))
        if result.status == "done":
            self._records.save(service, ConnectionRecord(ConnectionStatus.UNTESTED))
        return result

    # -- browser sign-in --------------------------------------------------
    def _browser(self, service: ServiceId) -> BrowserSignInSetup:
        return self._plugin_as(service, BrowserSignInSetup, "a browser sign-in")  # type: ignore[type-abstract]

    def sign_in_config(self, service: ServiceId) -> dict[str, str]:
        plugin = self._browser(service)
        return plugin.sign_in_config(self._require_set_up(service))

    def accept_sign_in(self, service: ServiceId, result: Mapping[str, str]) -> None:
        plugin = self._browser(service)
        self._require_set_up(service)
        plugin.accept_sign_in(self._ctx(), result)
        self._records.save(service, ConnectionRecord(ConnectionStatus.UNTESTED))

    # -- lifecycle --------------------------------------------------------
    def disconnect(self, service: ServiceId) -> None:
        self.plugin(service)
        self._devices.pop(service, None)
        self._credentials.delete(service)
        self._records.delete(service)

    def connectors(self) -> dict[ServiceId, Connector]:
        """Connectors for set-up services. A plugin that can't build its
        connector is marked failing and left out, so only jobs that need it
        fail."""
        ctx = self._ctx()
        built: dict[ServiceId, Connector] = {}
        for service, plugin in self._registry.plugins.items():
            if not self._credentials.get(service):
                continue
            try:
                built[service] = plugin.create(ctx)
            except Exception as e:  # noqa: BLE001 - plugin code; never echo its text
                logger.warning(
                    "%s connector failed to start: %s", service, type(e).__name__
                )
                self._mark(
                    service,
                    ConnectionStatus.FAILING,
                    f"Couldn't start the {plugin.display_name} connector",
                )
        return built

    def _mark(self, service: ServiceId, status: ConnectionStatus, message: str) -> None:
        """Record a problem, keeping the account name from the last check."""
        previous = self._records.get(service)
        self._records.save(
            service,
            ConnectionRecord(
                status,
                message,
                previous.account_name if previous else None,
                self._clock.now(),
            ),
        )

    def engine_config(self) -> EngineConfig:
        if self._app_settings is None:
            return EngineConfig()
        return self._app_settings.get().engine

    def on_auth_required(self, service: ServiceId) -> None:
        plugin = self._registry.plugins.get(service)
        if plugin is not None:
            self._mark(
                service,
                ConnectionStatus.NEEDS_SIGN_IN,
                f"Sign in to {plugin.display_name} again",
            )

    async def playlists(self, service: ServiceId) -> builtins.list[PlaylistMeta]:
        plugin = self.plugin(service)
        self._require_set_up(service)
        return await plugin.create(self._ctx()).list_playlists()
