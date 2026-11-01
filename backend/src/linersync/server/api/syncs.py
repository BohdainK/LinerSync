"""Sync routes: CRUD, preview, run now and run history."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from linersync.domain.capabilities import limitations
from linersync.domain.jobs import PreviewJob, SyncJob
from linersync.domain.runs import SyncRun, Trigger
from linersync.domain.sync import (
    SOURCE_MEMBER_ID,
    Direction,
    MemberRole,
    SyncGroup,
    SyncMember,
    SyncStatus,
    destination_member_id,
    effective_mode,
)
from linersync.domain.tracks import ServiceId
from linersync.server.api.common import (
    Services,
    check_destinations,
    check_schedule,
    check_source,
    found,
    invalid,
    reconcile_schedule,
)
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
)
from linersync.server.auth import require_session
from linersync.server.state import AppServices

router = APIRouter(prefix="/api/syncs", dependencies=[Depends(require_session)])

PAUSED_BY_YOU = "Paused by you"
_BUSY = "Wait for the current run to finish"
UNKNOWN_SYNC = "Unknown sync"


# -- validation ---------------------------------------------------------------
def _destination(member_id: str, d: DestinationIn) -> SyncMember:
    return SyncMember(
        member_id, ServiceId(d.service), d.playlist_id, MemberRole.DESTINATION, d.mode
    )


def _valid(build: Callable[[], SyncGroup]) -> SyncGroup:
    """Build a group, reporting its shape errors as 422."""
    try:
        return build()
    except ValueError as e:
        raise invalid(str(e)) from None


def _new_group(services: AppServices, body: SyncCreateIn, sync_id: str) -> SyncGroup:
    check_source(services, body.source.service, body.source.playlist_id)
    check_destinations(services, (d.service for d in body.destinations))
    check_schedule(services, body.schedule)
    source = SyncMember(
        SOURCE_MEMBER_ID,
        ServiceId(body.source.service),
        body.source.playlist_id,
        MemberRole.SOURCE,
    )
    destinations = [
        _destination(destination_member_id(i), d)
        for i, d in enumerate(body.destinations)
    ]
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
    return found(services.repos.syncs.get(sync_id), UNKNOWN_SYNC)


def _not_busy(services: AppServices, sync_id: str) -> None:
    if services.jobs.active_for_sync(sync_id) is not None:
        raise HTTPException(status_code=409, detail=_BUSY)


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
    missing = _missing_destinations(last)
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
            _destination_out(services, d, d.member_id in missing)
            for d in group.destinations
        ],
        active_job=None if active is None else JobOut.of(active),
        last_run=None if last is None else RunSummaryOut.of(last),
    )


def _missing_destinations(last_run: SyncRun | None) -> set[str]:
    """Members the last run, a dry run, would rebuild: their playlist is gone."""
    if last_run is None or not last_run.dry_run:
        return set()
    return {m.member_id for m in last_run.members if m.rebuilt}


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
        check_schedule(services, body.schedule)
        changes["schedule"] = body.schedule
    if body.paused is not None:
        changes["status"] = SyncStatus.PAUSED if body.paused else SyncStatus.ACTIVE
        changes["pause_reason"] = PAUSED_BY_YOU if body.paused else None
    changes["members"] = _patched_members(services, group, body)
    updated = _valid(lambda: replace(group, **changes))
    services.repos.syncs.save(updated)
    reconcile_schedule(services)
    return _one_out(services, updated)


def _patched_members(
    services: AppServices, group: SyncGroup, body: SyncPatchIn
) -> tuple[SyncMember, ...]:
    """The members with the patch's mode changes, removals and additions."""
    destination_ids = {m.member_id for m in group.destinations}
    modes = body.modes or {}
    for member_id in modes:
        if member_id not in destination_ids:
            raise invalid(f"Unknown destination {member_id}")
    removed = set(body.remove_member_ids or ())
    if SOURCE_MEMBER_ID in removed:
        raise invalid("The source can't be removed")
    unknown = sorted(removed - destination_ids)
    if unknown:
        raise invalid(f"Unknown destination {unknown[0]}")
    members = [
        replace(m, mode=modes[m.member_id]) if m.member_id in modes else m
        for m in group.members
        if m.member_id not in removed
    ]
    added = body.add_destinations or []
    check_destinations(services, (d.service for d in added))
    for d in added:
        members.append(_destination(_free_member_id(members), d))
    return tuple(members)


def _free_member_id(members: list[SyncMember]) -> str:
    taken = {m.member_id for m in members}
    n = 0
    while destination_member_id(n) in taken:
        n += 1
    return destination_member_id(n)


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
    return [RunSummaryOut.of(r) for r in runs]
