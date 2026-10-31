"""Backup routes: the summary, back up now, backup runs, stored covers, the
archive and its versions, and restore."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from linersync.application.backups import BACKUP_ALL_LABEL
from linersync.application.export_csv import csv_filename, known_ids, playlist_csv
from linersync.domain.backups import (
    BACKUP_SERVICE,
    BackedUpPlaylist,
    PlaylistVersion,
)
from linersync.domain.jobs import BackupJob, TransferJob
from linersync.domain.runs import Trigger
from linersync.domain.tracks import ServiceId
from linersync.server.api.export import csv_response
from linersync.server.api.runs import known_track
from linersync.server.api.schemas import (
    BackupPlaylistDetailOut,
    BackupPlaylistOut,
    BackupRunIn,
    BackupRunOut,
    BackupServiceOut,
    BackupSummaryOut,
    BackupTrackOut,
    BackupVersionDetailOut,
    BackupVersionOut,
    JobOut,
    RestoreIn,
)
from linersync.server.api.syncs import (
    UNKNOWN_BACKUP,
    backup_unlocked,
    check_destinations,
    check_services,
)
from linersync.server.auth import require_session
from linersync.server.connections import ConnectionStatus
from linersync.server.state import AppServices, get_services

router = APIRouter(prefix="/api/backups", dependencies=[Depends(require_session)])

Services = Annotated[AppServices, Depends(get_services)]

CONNECT_FIRST = "Connect a service first"
COVER_CACHE = "private, max-age=31536000, immutable"
COVER_CSP = "default-src 'none'; sandbox"
ONLY_ARCHIVED = "Only archived playlists can be deleted"


def _set_up(services: AppServices) -> list[ServiceId]:
    return [
        i.service
        for i in services.connections.list()
        if i.status is not ConnectionStatus.NOT_SET_UP
    ]


def _display_name(services: AppServices, service: ServiceId) -> str:
    plugin = services.registry.plugins.get(service)
    return plugin.display_name if plugin is not None else service


def _service_out(
    services: AppServices, service: ServiceId, connected: bool
) -> BackupServiceOut:
    store = services.backups
    live = store.list_entries(service, archived=False)
    runs = store.list_runs(service, limit=1)
    return BackupServiceOut(
        service=service,
        display_name=_display_name(services, service),
        connected=connected,
        playlists=len(live),
        archived=len(store.list_entries(service, archived=True)),
        missing=sum(1 for e in live if e.missing_since is not None),
        last_run=BackupRunOut.of(runs[0]) if runs else None,
    )


@router.get("")
async def backup_summary(services: Services) -> BackupSummaryOut:
    store = services.backups
    set_up = set(_set_up(services))
    known = (
        set_up
        | {e.service for e in store.list_entries()}
        | {r.service for r in store.list_runs()}
    )
    outs = [_service_out(services, s, s in set_up) for s in known]
    outs.sort(key=lambda o: (o.display_name.casefold(), o.service))
    active = services.jobs.active_backup()
    return BackupSummaryOut(
        services=outs,
        schedule=services.settings_store.get().backup_schedule,
        next_backup_at=services.scheduler.next_backup_at(),
        active_job=JobOut.of(active) if active is not None else None,
    )


@router.post("/run", status_code=202)
async def run_backup(services: Services, body: BackupRunIn | None = None) -> JobOut:
    names = body.services if body is not None else []
    if names:
        check_services(services, names)
    elif not _set_up(services):
        raise HTTPException(status_code=422, detail=CONNECT_FIRST)
    chosen = tuple(dict.fromkeys(ServiceId(n) for n in names))
    label = (
        f"Back up {_display_name(services, chosen[0])}"
        if len(chosen) == 1
        else BACKUP_ALL_LABEL
    )
    job = services.jobs.submit(BackupJob(chosen, Trigger.MANUAL), label)
    return JobOut.of(job)


@router.get("/runs")
async def list_backup_runs(
    services: Services,
    service: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[BackupRunOut]:
    runs = services.backups.list_runs(
        ServiceId(service) if service is not None else None, limit
    )
    return [BackupRunOut.of(r) for r in runs]


@router.get("/runs/{backup_id}")
async def get_backup_run(backup_id: str, services: Services) -> BackupRunOut:
    run = services.backups.get_run(backup_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown backup run")
    return BackupRunOut.of(run)


@router.get(
    "/covers/{sha256}",
    responses={200: {"content": {"image/*": {}}, "description": "The cover image"}},
)
async def get_cover(sha256: str, services: Services) -> Response:
    cover = services.backups.get_cover(sha256)
    if cover is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=cover.data,
        media_type=cover.media_type,
        headers={
            "Cache-Control": COVER_CACHE,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": COVER_CSP,
        },
    )


# -- the archive --------------------------------------------------------------
def _entry(services: AppServices, entry_id: str) -> BackedUpPlaylist:
    entry = services.backups.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=UNKNOWN_BACKUP)
    return entry


def _version(services: AppServices, version_id: str) -> PlaylistVersion:
    version = services.backups.get_version(version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=UNKNOWN_BACKUP)
    return version


@router.get("/playlists")
async def list_backup_playlists(
    services: Services, service: str | None = None, archived: bool | None = None
) -> list[BackupPlaylistOut]:
    entries = services.backups.list_entries(
        ServiceId(service) if service is not None else None, archived=archived
    )
    return [BackupPlaylistOut.of(e) for e in entries]


@router.get("/playlists/{entry_id}")
async def get_backup_playlist(
    entry_id: str, services: Services
) -> BackupPlaylistDetailOut:
    entry = _entry(services, entry_id)
    history = [BackupVersionOut.of(v) for v in services.backups.list_versions(entry_id)]
    return BackupPlaylistDetailOut(
        **BackupPlaylistOut.of(entry).model_dump(), history=history
    )


@router.delete("/playlists/{entry_id}", status_code=204)
async def delete_backup_playlist(entry_id: str, services: Services) -> Response:
    entry = _entry(services, entry_id)
    if not entry.archived:
        raise HTTPException(status_code=409, detail=ONLY_ARCHIVED)
    backup_unlocked(services)
    services.backups.delete_entry(entry_id)
    services.backups.prune_covers()
    return Response(status_code=204)


@router.get("/versions/{version_id}")
async def get_backup_version(
    version_id: str, services: Services
) -> BackupVersionDetailOut:
    version = _version(services, version_id)
    tracks = [
        BackupTrackOut(position=i, added_at=e.added_at, track=known_track(e.track))
        for i, e in enumerate(version.snapshot.entries, start=1)
    ]
    return BackupVersionDetailOut(
        **BackupVersionOut.of(version).model_dump(), tracks=tracks
    )


@router.get(
    "/versions/{version_id}/csv",
    responses={200: {"content": {"text/csv": {}}, "description": "The version as CSV"}},
)
async def version_csv(version_id: str, services: Services) -> Response:
    version = _version(services, version_id)
    entry = _entry(services, version.entry_id)
    ids = known_ids(services.repos.syncs.list(), services.repos.matches.list_all())
    text = playlist_csv(version.snapshot, entry.service, ids)
    return csv_response(text, csv_filename(version.snapshot.meta.name, entry.service))


@router.post("/versions/{version_id}/restore", status_code=202)
async def restore_version(
    version_id: str, services: Services, body: RestoreIn | None = None
) -> JobOut:
    """Always a new playlist, by default on the playlist's own service (D6)."""
    version = _version(services, version_id)
    entry = _entry(services, version.entry_id)
    request = body if body is not None else RestoreIn()
    destination = ServiceId(request.destination or entry.service)
    check_destinations(services, [destination])
    name = version.snapshot.meta.name
    label = f"Restore “{name}” to {_display_name(services, destination)}"
    transfer = TransferJob(BACKUP_SERVICE, version_id, destination, request.dry_run)
    return JobOut.of(services.jobs.submit(transfer, label))
