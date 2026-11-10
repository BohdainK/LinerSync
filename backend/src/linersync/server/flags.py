"""Feature flags: switches plugins offer, set by the user on the Settings page.

The stored value applies to the plugin at startup and on every change, so it
takes effect without a restart: plugins read their flags each time they report
capabilities or build a connector, and connectors are built per job.

An environment variable a plugin names in ``locked_by`` fixes the flag
instead: the stored value is then ignored and the page shows it as set by the
server.
"""

import logging
from dataclasses import dataclass, replace

from linersync.adapters.connectors.kit.discovery import PluginRegistry
from linersync.adapters.connectors.kit.plugin import (
    ConnectorPlugin,
    FeatureFlag,
    FlagSetup,
    FlagUnavailable,
)
from linersync.adapters.sqlite.settings import SqliteSettings
from linersync.domain.tracks import ServiceId

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FlagInfo:
    flag_id: str  # "<service>.<key>"
    service: ServiceId
    service_name: str
    flag: FeatureFlag


class UnknownFlag(KeyError):
    pass


def flag_id(service: ServiceId, key: str) -> str:
    return f"{service}.{key}"


class FeatureFlags:
    def __init__(self, registry: PluginRegistry, settings: SqliteSettings) -> None:
        self._registry = registry
        self._settings = settings

    def _flagged(self) -> list[tuple[ServiceId, ConnectorPlugin]]:
        return [
            (service, plugin)
            for service, plugin in self._registry.plugins.items()
            if isinstance(plugin, FlagSetup)
        ]

    def list(self) -> list[FlagInfo]:
        out: list[FlagInfo] = []
        for service, plugin in self._flagged():
            assert isinstance(plugin, FlagSetup)
            for flag in plugin.feature_flags():
                out.append(
                    FlagInfo(
                        flag_id(service, flag.key), service, plugin.display_name, flag
                    )
                )
        return sorted(out, key=lambda i: (i.service_name.casefold(), i.flag_id))

    def get(self, id_: str) -> FlagInfo:
        for info in self.list():
            if info.flag_id == id_:
                return info
        raise UnknownFlag(id_)

    def apply_stored(self) -> None:
        """Set every flag the user decides to its stored value (absent = off).
        A stored value that can't apply here (say, a sign-in method that
        doesn't support it) leaves the flag off rather than failing startup."""
        stored = self._settings.get().flags
        for service, plugin in self._flagged():
            assert isinstance(plugin, FlagSetup)
            for flag in plugin.feature_flags():
                if flag.locked_by:
                    continue
                on = stored.get(flag_id(service, flag.key), False) and not (
                    flag.unavailable
                )
                try:
                    plugin.set_flag(flag.key, on)
                except (FlagUnavailable, KeyError):
                    logger.warning("%s flag %s could not be set", service, flag.key)

    def set(self, id_: str, on: bool) -> FlagInfo:
        """Raises UnknownFlag, or FlagUnavailable when it is locked or can't
        be turned on here."""
        info = self.get(id_)
        plugin = self._registry.get(info.service)
        assert isinstance(plugin, FlagSetup)
        if info.flag.locked_by:
            raise FlagUnavailable(f"{info.flag.locked_by} sets this on the server")
        if on and info.flag.unavailable:
            raise FlagUnavailable(info.flag.unavailable)
        plugin.set_flag(info.flag.key, on)
        current = self._settings.get()
        self._settings.save(replace(current, flags={**current.flags, id_: on}))
        return self.get(id_)
