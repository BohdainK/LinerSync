"""Sync routes: CRUD, preview, run now and run history."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from linersync.adapters.scheduler import cron_problem
from linersync.domain.backups import BACKUP_SERVICE
from linersync.domain.capabilities import limitations
from linersync.domain.jobs import PreviewJob, SyncJob
from linersync.domain.runs import Trigger
from linersync.domain.sync import (
    Direction,
    MemberRole,
    SyncGroup,
    SyncMember,
    SyncStatus,
    effective_mode,
)
from linersync.domain.tracks import ServiceId
from linersync.server.api.schemas import (
    DestinationIn,
    DestinationOut,
    JobOut,
    RunIn,
    RunSummaryOut,
    SourceOut,
    SyncBulkIn,
    SyncCreateIn,
    SyncOut,
    SyncPatchIn,
    run_summary,
)
from linersync.server.auth import require_session
from linersync.server.connections import ConnectionStatus
from linersync.server.state import AppServices, get_services

router = APIRouter(prefix="/api/syncs", dependencies=[Depends(require_session)])

Services = Annotated[AppServices, Depends(get_services)]

PAUSED_BY_YOU = "Paused by you"
_BUSY = "Wait for the current run to finish"
BACKUP_BUSY = "Wait for the current backup to finish"
UNKNOWN_BACKUP = "Unknown backup"


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


# -- validation ---------------------------------------------------------------
def check_services(services: AppServices, names: Iterable[str]) -> None:
    for name in names:
        if name not in services.registry.plugins:
            raise _invalid(f"Unknown service {name}")
        info = services.connections.info(ServiceId(name))
        if info.status is ConnectionStatus.NOT_SET_UP:
            raise _invalid(f"Connect {info.display_name} first")


def check_source(services: AppServices, service: str, playlist_id: str) -> None:
    """A source may also be a stored backup: an entry id or a version id (D6)."""
    if service != BACKUP_SERVICE:
        check_services(services, [service])
        return
    store = services.backups
    if store.get_version(playlist_id) is None and store.get_entry(playlist_id) is None:
        raise _invalid(UNKNOWN_BACKUP)


def backup_unlocked(services: AppServices) -> None:
    """Backup data can't change while a backup job is queued or running (D10)."""
    if services.jobs.active_backup() is not None:
        raise HTTPException(status_code=409, detail=BACKUP_BUSY)


def _check_schedule(services: AppServices, schedule: str | None) -> None:
    if schedule is None:
        return
    problem = cron_problem(schedule, services.config.timezone)
    if problem is not None:
        raise _invalid(problem)


def _destination(member_id: str, d: DestinationIn) -> SyncMember:
    return SyncMember(
        member_id, ServiceId(d.service), d.playlist_id, MemberRole.DESTINATION, d.mode
    )


def _valid(build: Callable[[], SyncGroup]) -> SyncGroup:
    """Build a group, reporting its shape errors as 422."""
    try:
        return build()
    except ValueError as e:
        raise _invalid(str(e)) from None


def _new_group(services: AppServices, body: SyncCreateIn, sync_id: str) -> SyncGroup:
    check_source(services, body.source.service, body.source.playlist_id)
    check_services(services, (d.service for d in body.destinations))
    _check_schedule(services, body.schedule)
    source = SyncMember(
        "src",
        ServiceId(body.source.service),
        body.source.playlist_id,
        MemberRole.SOURCE,
    )
    destinations = [_destination(f"dst{i}", d) for i, d in enumerate(body.destinations)]
    return _valid(
        lambda: SyncGroup(
            sync_id=sync_id,
            name=body.name,
            direction=Direction.ONE_WAY,
            members=(source, *destinations),
            schedule=body.schedule,
        )
    )


def _found(services: AppServices, sync_id: str) -> SyncGroup:
    group = services.repos.syncs.get(sync_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Unknown sync")
    return group


def _not_busy(services: AppServices, sync_id: str) -> None:
    if services.jobs.active_for_sync(sync_id) is not None:
        raise HTTPException(status_code=409, detail=_BUSY)


def reconcile_schedule(services: AppServices) -> None:
    services.scheduler.reconcile(
        services.repos.syncs.list(), services.settings_store.get().default_schedule
    )
    services.scheduler.schedule_backup(services.settings_store.get().backup_schedule)


# -- views --------------------------------------------------------------------
def _sync_outs(services: AppServices, groups: Iterable[SyncGroup]) -> list[SyncOut]:
    default_schedule = services.settings_store.get().default_schedule
    next_runs = services.scheduler.next_runs()
    return [_sync_out(services, g, default_schedule, next_runs) for g in groups]


def _one_out(services: AppServices, group: SyncGroup) -> SyncOut:
    return _sync_outs(services, [group])[0]


def _sync_out(
    services: AppServices,
    group: SyncGroup,
    default_schedule: str,
    next_runs: Mapping[str, datetime],
) -> SyncOut:
    runs = services.repos.runs.list_for_sync(group.sync_id, limit=1)
    last = runs[0] if runs else None
    rebuilt = (
        {m.member_id for m in last.members if m.rebuilt}
        if last is not None and last.dry_run
        else set()
    )
    active = services.jobs.active_for_sync(group.sync_id)
    src = group.source
    return SyncOut(
        sync_id=group.sync_id,
        name=group.name,
        status=group.status.value,
        pause_reason=group.pause_reason,
        schedule=group.schedule,
        effective_schedule=group.schedule or default_schedule,
        next_run_at=next_runs.get(group.sync_id),
        source=SourceOut(
            member_id=src.member_id, service=src.service, playlist_id=src.playlist_id
        ),
        destinations=[
            _destination_out(services, d, d.member_id in rebuilt)
            for d in group.destinations
        ],
        active_job=None if active is None else JobOut.of(active),
        last_run=None if last is None else run_summary(last),
    )


def _destination_out(
    services: AppServices, member: SyncMember, missing: bool
) -> DestinationOut:
    plugin = services.registry.plugins.get(member.service)
    notes: tuple[str, ...] = ()
    if plugin is None:
        effective = member.mode
    else:
        effective = effective_mode(member.mode, plugin.capabilities)
        notes = limitations(plugin.display_name, plugin.capabilities, member.mode)
    return DestinationOut(
        member_id=member.member_id,
        service=member.service,
        playlist_id=member.playlist_id,
        mode=member.mode,
        effective_mode=effective,
        limitations=list(notes),
        last_synced_at=None
        if member.last_synced is None
        else member.last_synced.synced_at,
        missing=missing,
    )


# -- routes -------------------------------------------------------------------
@router.get("")
async def list_syncs(services: Services) -> list[SyncOut]:
    groups = sorted(
        services.repos.syncs.list(), key=lambda g: (g.name.casefold(), g.sync_id)
    )
    return _sync_outs(services, groups)


@router.post("", status_code=201)
async def create_sync(body: SyncCreateIn, services: Services) -> SyncOut:
    group = _new_group(services, body, services.new_id())
    services.repos.syncs.save(group)
    reconcile_schedule(services)
    return _one_out(services, group)


@router.post("/bulk", status_code=201)
async def create_syncs(body: SyncBulkIn, services: Services) -> list[SyncOut]:
    groups = [_new_group(services, item, services.new_id()) for item in body.syncs]
    for group in groups:
        services.repos.syncs.save(group)
    reconcile_schedule(services)
    return _sync_outs(services, groups)


@router.post("/preview", status_code=202)
async def preview_sync(body: SyncCreateIn, services: Services) -> JobOut:
    group = _new_group(services, body, f"preview-{services.new_id()}")
    job = services.jobs.submit(PreviewJob(group), f"Preview “{group.name}”")
    return JobOut.of(job)


@router.get("/{sync_id}")
async def get_sync(sync_id: str, services: Services) -> SyncOut:
    return _one_out(services, _found(services, sync_id))


@router.patch("/{sync_id}")
async def patch_sync(sync_id: str, body: SyncPatchIn, services: Services) -> SyncOut:
    group = _found(services, sync_id)
    _not_busy(services, sync_id)
    changes: dict[str, Any] = {}
    if body.name is not None:
        changes["name"] = body.name
    if "schedule" in body.model_fields_set:
        _check_schedule(services, body.schedule)
        changes["schedule"] = body.schedule
    if body.paused is not None:
        changes["status"] = SyncStatus.PAUSED if body.paused else SyncStatus.ACTIVE
        changes["pause_reason"] = PAUSED_BY_YOU if body.paused else None
    members = list(group.members)
    destination_ids = {m.member_id for m in group.destinations}
    for member_id in body.modes or {}:
        if member_id not in destination_ids:
            raise _invalid(f"Unknown destination {member_id}")
    if body.modes:
        modes = body.modes
        members = [
            replace(m, mode=modes[m.member_id]) if m.member_id in modes else m
            for m in members
        ]
    removed = set(body.remove_member_ids or ())
    if "src" in removed:
        raise _invalid("The source can't be removed")
    unknown = sorted(removed - destination_ids)
    if unknown:
        raise _invalid(f"Unknown destination {unknown[0]}")
    members = [m for m in members if m.member_id not in removed]
    added = body.add_destinations or []
    check_services(services, (d.service for d in added))
    for d in added:
        members.append(_destination(_free_member_id(members), d))
    changes["members"] = tuple(members)
    updated = _valid(lambda: replace(group, **changes))
    services.repos.syncs.save(updated)
    reconcile_schedule(services)
    return _one_out(services, updated)


def _free_member_id(members: list[SyncMember]) -> str:
    taken = {m.member_id for m in members}
    n = 0
    while f"dst{n}" in taken:
        n += 1
    return f"dst{n}"


@router.delete("/{sync_id}", status_code=204)
async def delete_sync(sync_id: str, services: Services) -> Response:
    _found(services, sync_id)
    _not_busy(services, sync_id)
    services.repos.syncs.delete(sync_id)
    reconcile_schedule(services)
    return Response(status_code=204)


@router.post("/{sync_id}/run", status_code=202)
async def run_sync(
    sync_id: str, services: Services, body: RunIn | None = None
) -> JobOut:
    group = _found(services, sync_id)
    dry_run = body is not None and body.dry_run
    label = f"Dry run “{group.name}”" if dry_run else f"Sync “{group.name}”"
    job = services.jobs.submit(SyncJob(sync_id, Trigger.MANUAL, dry_run), label)
    return JobOut.of(job)


@router.get("/{sync_id}/runs")
async def list_runs(
    sync_id: str,
    services: Services,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunSummaryOut]:
    _found(services, sync_id)
    runs = services.repos.runs.list_for_sync(sync_id, limit=limit)
    return [run_summary(r) for r in runs]
