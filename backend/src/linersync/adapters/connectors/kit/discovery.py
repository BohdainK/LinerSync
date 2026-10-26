"""Entry-point discovery. A broken third-party plugin is reported, never raised."""

import inspect
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points

from linersync.adapters.connectors.kit.plugin import ConnectorPlugin
from linersync.domain.tracks import ServiceId
from linersync.ports.setup import check_guide

ENTRY_POINT_GROUP = "linersync.connectors"


@dataclass(frozen=True, slots=True)
class PluginLoadError:
    name: str
    message: str


@dataclass(frozen=True, slots=True)
class PluginRegistry:
    plugins: dict[ServiceId, ConnectorPlugin]
    errors: tuple[PluginLoadError, ...]

    def get(self, service: ServiceId) -> ConnectorPlugin:
        return self.plugins[service]


def _load(ep: EntryPoint) -> ConnectorPlugin:
    obj = ep.load()
    if inspect.isclass(obj):
        obj = obj()
    if not isinstance(obj, ConnectorPlugin):
        raise TypeError("not a ConnectorPlugin")
    guide = obj.setup_guide()
    problems = check_guide(guide)
    if problems:
        raise ValueError("invalid setup guide: " + "; ".join(problems))
    if guide.service != obj.service:
        raise ValueError(
            f"guide service {guide.service!r} does not match {obj.service!r}"
        )
    return obj


def discover_plugins(
    entry_points_: Iterable[EntryPoint] | None = None,
) -> PluginRegistry:
    if entry_points_ is None:
        entry_points_ = entry_points(group=ENTRY_POINT_GROUP)
    plugins: dict[ServiceId, ConnectorPlugin] = {}
    owners: dict[ServiceId, str] = {}
    errors: list[PluginLoadError] = []
    for ep in sorted(entry_points_, key=lambda e: e.name):
        try:
            plugin = _load(ep)
        except Exception as exc:  # noqa: BLE001 - third-party code
            errors.append(PluginLoadError(ep.name, f"{type(exc).__name__}: {exc}"))
            continue
        if plugin.service in plugins:
            errors.append(
                PluginLoadError(
                    ep.name,
                    f"service {plugin.service!r} is already provided by "
                    f"{owners[plugin.service]}",
                )
            )
            continue
        plugins[plugin.service] = plugin
        owners[plugin.service] = ep.name
    return PluginRegistry(plugins, tuple(errors))
