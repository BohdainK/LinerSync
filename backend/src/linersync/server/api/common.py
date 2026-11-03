"""Helpers the routers share: lookups, downloads, validation, locks and the
schedule."""

from collections.abc import Iterable
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, HTTPException, Response

from linersync.adapters.scheduler import cron_problem
from linersync.domain.backups import BACKUP_SERVICE
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.errors import ConnectorError
from linersync.server.api.schemas import PickChoiceIn
from linersync.server.connections import ConnectionManager, ConnectionStatus
from linersync.server.state import AppServices, get_services

Services = Annotated[AppServices, Depends(get_services)]

BACKUP_BUSY = "Wait for the current backup to finish"
UNKNOWN_BACKUP = "Unknown backup"
UNREACHABLE = "The service could not be reached"
CSV_TYPE = "text/csv; charset=utf-8"


def invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def found[T](value: T | None, detail: str) -> T:
    """`value`, or a 404 with `detail` when the lookup found nothing."""
    if value is None:
        raise HTTPException(status_code=404, detail=detail)
    return value


def display_name(services: AppServices, service: ServiceId) -> str:
    """The plugin's name, or the bare id for a service no longer installed."""
    plugin = services.registry.plugins.get(service)
    return plugin.display_name if plugin is not None else service


def connector_failed(
    connections: ConnectionManager, e: ConnectorError, service: ServiceId
) -> HTTPException:
    """A 502 for a failed service call; a lost sign-in is recorded first."""
    if e.auth_required:
        connections.on_auth_required(service)
    return HTTPException(status_code=502, detail=e.message or UNREACHABLE)


def attachment(filename: str) -> str:
    """A Content-Disposition value with an ASCII fallback and the UTF-8 name."""
    fallback = "".join(c if c.isascii() and c.isprintable() else "_" for c in filename)
    fallback = fallback.replace("\\", "_").replace('"', "_")
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def csv_response(body: str, filename: str) -> Response:
    return Response(
        content=body.encode("utf-8-sig"),
        media_type=CSV_TYPE,
        headers={"Content-Disposition": attachment(filename)},
    )


# -- validation ---------------------------------------------------------------
def check_services(services: AppServices, names: Iterable[str]) -> None:
    for name in names:
        if name not in services.registry.plugins:
            raise invalid(f"Unknown service {name}")
        info = services.connections.info(ServiceId(name))
        if info.status is ConnectionStatus.NOT_SET_UP:
            raise invalid(f"Connect {info.display_name} first")


def check_destinations(services: AppServices, names: Iterable[str]) -> None:
    """Destinations must exist, be set up, and be able to add songs."""
    names = list(names)
    check_services(services, names)
    for name in names:
        plugin = services.registry.plugins[ServiceId(name)]
        if Capability.ADD not in plugin.capabilities:
            raise invalid(
                f"{plugin.display_name} is read-only, so it can't be a destination"
            )


def check_source(services: AppServices, service: str, playlist_id: str) -> None:
    """A source may also be a stored backup: an entry id or a version id (D6)."""
    if service != BACKUP_SERVICE:
        check_services(services, [service])
        return
    store = services.backups
    if store.get_version(playlist_id) is None and store.get_entry(playlist_id) is None:
        raise invalid(UNKNOWN_BACKUP)


def check_schedule(services: AppServices, schedule: str | None) -> None:
    """A cron expression (None means "the default") must parse."""
    if schedule is None:
        return
    problem = cron_problem(schedule, services.config.timezone)
    if problem is not None:
        raise invalid(problem)


def backup_unlocked(services: AppServices) -> None:
    """Backup data can't change while a backup job is queued or running (D10)."""
    if services.jobs.active_backup() is not None:
        raise HTTPException(status_code=409, detail=BACKUP_BUSY)


# -- schedule -----------------------------------------------------------------
def reconcile_schedule(services: AppServices) -> None:
    services.scheduler.reconcile_all(
        services.repos.syncs.list(), services.settings_store.get()
    )


def pick_tuples(
    picks: Iterable[PickChoiceIn],
) -> Iterable[tuple[TrackKey, ServiceId, str]]:
    """The picks as (source song, target service, chosen target song)."""
    return (
        (
            TrackKey(ServiceId(p.source_service), p.source_track_id),
            ServiceId(p.target_service),
            p.target_track_id,
        )
        for p in picks
    )
