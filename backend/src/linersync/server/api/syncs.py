"""Sync routes: CRUD, preview, run now and run history."""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from linersync.application.copy_back import previewed_track_ids
from linersync.application.picks import remember_picks
from linersync.domain.capabilities import Capability, limitations
from linersync.domain.jobs import (
    BulkPreviewJob,
    CopyToSourceJob,
    JobStatus,
    PreviewJob,
    SyncJob,
)
from linersync.domain.runs import RunKind, SyncRun, Trigger
from linersync.domain.sync import (
    SOURCE_MEMBER_ID,
    Direction,
    MemberRole,
    SyncedEntry,
    SyncGroup,
    SyncMember,
    SyncStatus,
    destination_member_id,
    effective_mode,
)
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.server.api.common import (
    Services,
    check_destinations,
    check_schedule,
    check_source,
    display_name,
    found,
    invalid,
    pick_tuples,
    reconcile_schedule,
)
from linersync.server.api.schemas import (
    CopyToSourceIn,
    DestinationIn,
    DestinationOut,
    JobOut,
    ReAddIn,
    RunIn,
    RunSummaryOut,
    SourceOut,
    SyncBulkIn,
    SyncCreateIn,
    SyncOut,
    SyncPatchIn,
    SyncStartIn,
    TrackOut,
)
from linersync.server.auth import require_session
from linersync.server.state import AppServices

router = APIRouter(prefix="/api/syncs", dependencies=[Depends(require_session)])

PAUSED_BY_YOU = "Paused by you"
NOT_A_PREVIEW = "That is not a finished preview of this sync"
_BUSY = "Wait for the current run to finish"
UNKNOWN_SYNC = "Unknown sync"
# How many recent runs are looked at to find the sync's last run.
LAST_RUN_WINDOW = 10
UNKNOWN_DESTINATION = "Unknown destination"
NOT_A_COPY_PREVIEW = "That is not a preview of copying these songs to the source"
COPY_PREVIEW_INCOMPLETE = "The preview did not finish; preview again"
CANT_COPY = "Songs can't be added to the source playlist"
NOT_ADDED = "Those songs were not added by you"


# -- validation ---------------------------------------------------------------
def _destination(member_id: str, d: DestinationIn) -> SyncMember:
    return SyncMember(
        member_id,
        ServiceId(d.service),
        d.playlist_id,
        MemberRole.DESTINATION,
        d.mode,
        mirror_once=d.existing == "overwrite" and d.playlist_id is not None,
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


def _check_start(services: AppServices, body: SyncCreateIn) -> None:
    """The preview to start from must be a dry run of this sync's source."""
    start = body.start
    if start is None:
        return
    run = services.repos.runs.get(start.preview_run_id)
    context = services.contexts.get(start.preview_run_id)
    if run is None or context is None or not run.dry_run:
        raise invalid(NOT_A_PREVIEW)
    source = next((m for m in context.members if m.member_id == SOURCE_MEMBER_ID), None)
    if (
        source is None
        or source.service != body.source.service
        or source.playlist_id != body.source.playlist_id
    ):
        raise invalid(NOT_A_PREVIEW)


def _start(services: AppServices, group: SyncGroup, start: SyncStartIn) -> None:
    """Remember the swaps and queue the real first run from the preview."""
    remember_picks(services.repos.matches, pick_tuples(start.picks))
    services.jobs.submit(
        SyncJob(group.sync_id, Trigger.MANUAL, preview_run_id=start.preview_run_id),
        f"Sync “{group.name}”",
    )


def _create_all(services: AppServices, bodies: list[SyncCreateIn]) -> list[SyncOut]:
    """Validate every body, then save the syncs and start those with a preview."""
    groups = [_new_group(services, b, services.new_id()) for b in bodies]
    for body in bodies:
        _check_start(services, body)
    for group in groups:
        services.repos.syncs.save(group)
    for group, body in zip(groups, bodies, strict=True):
        if body.start is not None:
            _start(services, group, body.start)
    reconcile_schedule(services)
    return _sync_outs(services, groups)


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
    # A copy-to-source run is the source's, not the destinations': the sync's
    # last run is its last other run (a real copy is followed by one anyway).
    runs = services.repos.runs.list_for_sync(group.sync_id, limit=LAST_RUN_WINDOW)
    last = next((r for r in runs if r.kind is not RunKind.COPY_TO_SOURCE), None)
    missing = _missing_destinations(last)
    active = services.jobs.active_for_sync(group.sync_id)
    src = group.source
    can_copy = _can_copy_to_source(services, src)
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
            _destination_out(services, d, d.member_id in missing, can_copy)
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


def _can_copy_to_source(services: AppServices, source: SyncMember) -> bool:
    """Songs can be added to a source that supports adding and is the user's."""
    plugin = services.registry.plugins.get(source.service)
    if plugin is None or Capability.ADD not in plugin.capabilities:
        return False
    return source.last_synced is None or source.last_synced.owned


def _destination_out(
    services: AppServices, member: SyncMember, missing: bool, can_copy: bool
) -> DestinationOut:
    plugin = services.registry.plugins.get(member.service)
    notes: tuple[str, ...] = ()
    if plugin is None:
        effective = member.mode
    else:
        effective = effective_mode(member.mode, plugin.capabilities)
        notes = limitations(plugin.display_name, plugin.capabilities, member.mode)
    state = member.last_synced
    removed = [] if state is None else [TrackOut.of(t) for t in state.removed_by_you]
    added = [] if state is None else [TrackOut.of(t) for t in state.added_by_you]
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
        removed_by_you=removed,
        added_by_you=added,
        can_copy_to_source=can_copy,
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
    return _create_all(services, [body])[0]


@router.post("/bulk", status_code=201)
async def create_syncs(body: SyncBulkIn, services: Services) -> list[SyncOut]:
    return _create_all(services, body.syncs)


@router.post("/preview", status_code=202)
async def preview_sync(body: SyncCreateIn, services: Services) -> JobOut:
    group = _new_group(services, body, f"preview-{services.new_id()}")
    job = services.jobs.submit(PreviewJob(group), f"Preview “{group.name}”")
    return JobOut.of(job)


@router.post("/preview/bulk", status_code=202)
async def preview_syncs(body: SyncBulkIn, services: Services) -> JobOut:
    groups = tuple(
        _new_group(services, item, f"preview-{services.new_id()}")
        for item in body.syncs
    )
    label = f"Preview {len(groups)} syncs"
    return JobOut.of(services.jobs.submit(BulkPreviewJob(groups), label))


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


@router.post("/{sync_id}/destinations/{member_id}/re-add", status_code=202)
async def re_add(
    sync_id: str, member_id: str, body: ReAddIn, services: Services
) -> JobOut:
    """Put a song the user removed from the destination back on the next run."""
    group = _found(services, sync_id)
    _not_busy(services, sync_id)
    member = found(
        next((m for m in group.destinations if m.member_id == member_id), None),
        UNKNOWN_DESTINATION,
    )
    key = TrackKey(ServiceId(body.source_service), body.source_track_id)
    state = member.last_synced
    if state is None or all(t.key != key for t in state.removed_by_you):
        raise invalid("That song was not removed by you")
    # Pairs whose copy is still on the destination stay, counted per copy (a
    # song may be in the source more than once); with no recorded order
    # (legacy state) every pair for the song goes.
    present = Counter(state.target_order)
    entries: list[SyncedEntry] = []
    for e in state.entries:
        if e.source_key == key:
            if present[e.target_track_id] <= 0:
                continue
            present[e.target_track_id] -= 1
        entries.append(e)
    removed = tuple(t for t in state.removed_by_you if t.key != key)
    updated = replace(
        member,
        last_synced=replace(state, entries=tuple(entries), removed_by_you=removed),
    )
    members = tuple(updated if m.member_id == member_id else m for m in group.members)
    services.repos.syncs.save(replace(group, members=members))
    job = services.jobs.submit(SyncJob(sync_id, Trigger.MANUAL), f"Sync “{group.name}”")
    return JobOut.of(job)


@router.post("/{sync_id}/destinations/{member_id}/copy-to-source", status_code=202)
async def copy_to_source(
    sync_id: str, member_id: str, body: CopyToSourceIn, services: Services
) -> JobOut:
    """Preview copying songs the user added on the destination into the source,
    or (with that preview) copy them; the sync then runs."""
    group = _found(services, sync_id)
    member = found(
        next((m for m in group.destinations if m.member_id == member_id), None),
        UNKNOWN_DESTINATION,
    )
    _not_busy(services, sync_id)
    if not _can_copy_to_source(services, group.source):
        raise invalid(CANT_COPY)
    source = display_name(services, group.source.service)
    if body.dry_run:
        _check_added(member, body.track_ids)
        request = CopyToSourceJob(sync_id, member_id, tuple(body.track_ids))
        label = f"Preview copy to {source} “{group.name}”"
        return JobOut.of(services.jobs.submit(request, label))
    request = _copy_from_preview(services, sync_id, member_id, body.preview_job_id)
    _check_added(member, request.track_ids)
    remember_picks(services.repos.matches, pick_tuples(body.picks))
    label = f"Copy to {source} “{group.name}”"
    return JobOut.of(services.jobs.submit(request, label))


def _check_added(member: SyncMember, track_ids: Iterable[str]) -> None:
    """At least one chosen song (any, when none are chosen) is still listed as
    added by the user on `member`."""
    state = member.last_synced
    added = set() if state is None else {t.track_id for t in state.added_by_you}
    wanted = set(track_ids)
    if not (added & wanted if wanted else added):
        raise invalid(NOT_ADDED)


def _copy_from_preview(
    services: AppServices, sync_id: str, member_id: str, preview_job_id: str | None
) -> CopyToSourceJob:
    """The real copy of a finished dry copy of the same sync and destination."""
    preview = None if preview_job_id is None else services.job_store.get(preview_job_id)
    request = None if preview is None else preview.request
    if (
        preview is None
        or not isinstance(request, CopyToSourceJob)
        or not request.dry_run
        or request.sync_id != sync_id
        or request.member_id != member_id
    ):
        raise invalid(NOT_A_COPY_PREVIEW)
    run = services.repos.runs.get(preview.run_ids[0]) if preview.run_ids else None
    if preview.status is not JobStatus.SUCCEEDED or run is None:
        raise HTTPException(status_code=409, detail=COPY_PREVIEW_INCOMPLETE)
    # Exactly the songs previewed, even when the preview asked for "all".
    track_ids = previewed_track_ids(run)
    if not track_ids:
        raise invalid(NOT_ADDED)
    return replace(
        request, track_ids=track_ids, dry_run=False, preview_run_id=run.run_id
    )


@router.get("/{sync_id}/runs")
async def list_runs(
    sync_id: str,
    services: Services,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunSummaryOut]:
    _found(services, sync_id)
    runs = services.repos.runs.list_for_sync(sync_id, limit=limit)
    return [RunSummaryOut.of(r) for r in runs]
