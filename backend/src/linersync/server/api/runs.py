"""Run routes: run detail (songs named from the run's context, D8) and undo."""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException

from linersync.application.attention import open_items
from linersync.application.suggestions import find_similar, search_text
from linersync.application.undo import Capabilities, can_undo, undo_steps
from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.capabilities import Capability
from linersync.domain.jobs import RunContext, UndoJob
from linersync.domain.operations import (
    AddTracks,
    CreatePlaylist,
    MemberPlan,
    Operation,
    RemoveTracks,
    Reorder,
    UpdateMetadata,
)
from linersync.domain.runs import MemberResult, SyncRun, VerificationMismatch
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.errors import ConnectorError
from linersync.server.api.attention import attention_out
from linersync.server.api.common import (
    Services,
    connector_failed,
    display_name,
    found,
)
from linersync.server.api.schemas import (
    AlternativesIn,
    AttentionOut,
    CandidateOut,
    JobOut,
    MatchPairOut,
    MemberOut,
    MismatchOut,
    OperationOut,
    OperationType,
    RunOut,
    RunSummaryOut,
    TrackOut,
    UndoStepOut,
    UnresolvedOut,
)
from linersync.server.api.views import TrackNames, change_out
from linersync.server.auth import require_session
from linersync.server.state import AppServices

router = APIRouter(prefix="/api/runs", dependencies=[Depends(require_session)])

CANT_UNDO = "This run can't be undone"
UNKNOWN_RUN = "Unknown run"
UNKNOWN_SONG = "Unknown song"


class Place(NamedTuple):
    """Where a member's playlist lives."""

    service: str
    playlist_id: str | None


def _capabilities(services: AppServices) -> Capabilities:
    def of(service: ServiceId) -> frozenset[Capability]:
        plugin = services.registry.plugins.get(service)
        return frozenset() if plugin is None else plugin.capabilities

    return of


def _member_services(
    places: Mapping[str, "Place"],
) -> dict[str, ServiceId]:
    return {mid: ServiceId(p.service) for mid, p in places.items() if p.service}


def undoable(services: AppServices, run: SyncRun) -> bool:
    places = _member_places(services, run, services.contexts.get(run.run_id))
    return can_undo(
        run,
        services.repos.snapshots,
        _capabilities(services),
        _member_services(places),
    )


def _undo_steps(
    services: AppServices,
    run: SyncRun,
    places: Mapping[str, Place],
    names: TrackNames,
) -> list[UndoStepOut]:
    steps = undo_steps(
        run,
        services.repos.snapshots,
        _capabilities(services),
        _member_services(places),
    )
    return [
        UndoStepOut(
            member_id=s.member_id,
            action=s.action,
            by_hand=s.by_hand,
            playlist_name=s.playlist_name,
            service=s.service,
            tracks=names.tracks(s.service or "", s.track_ids),
        )
        for s in steps
    ]


# -- view ---------------------------------------------------------------------
def _member_places(
    services: AppServices, run: SyncRun, ctx: RunContext | None
) -> dict[str, Place]:
    """member id → place: the run context, else the saved sync."""
    places: dict[str, Place] = {}
    group = services.repos.syncs.get(run.sync_id)
    if group is not None:
        places = {m.member_id: Place(m.service, m.playlist_id) for m in group.members}
    if ctx is not None:
        places.update(
            {m.member_id: Place(m.service, m.playlist_id) for m in ctx.members}
        )
    return places


def _operation(op: Operation, service: str, names: TrackNames) -> OperationOut:
    match op:
        case CreatePlaylist() | UpdateMetadata():
            kind: OperationType = (
                "create_playlist"
                if isinstance(op, CreatePlaylist)
                else "update_metadata"
            )
            return OperationOut(
                type=kind,
                op_id=op.op_id,
                name=op.draft.name,
                description=op.draft.description,
                cover_url=op.draft.cover_url,
            )
        case AddTracks():
            return OperationOut(
                type="add_tracks",
                op_id=op.op_id,
                tracks=names.tracks(service, op.track_ids),
            )
        case Reorder():
            return OperationOut(
                type="reorder", op_id=op.op_id, order_length=len(op.order)
            )
        case RemoveTracks():
            return OperationOut(
                type="remove_tracks",
                op_id=op.op_id,
                tracks=names.tracks(service, (r.track_id for r in op.removals)),
                positions=[r.position for r in op.removals],
            )


def _surplus(ids: Sequence[str], other: Sequence[str]) -> list[str]:
    """`ids` minus `other` as multisets, in `ids` order."""
    left = Counter(other)
    out: list[str] = []
    for tid in ids:
        if left[tid] > 0:
            left[tid] -= 1
        else:
            out.append(tid)
    return out


def _mismatch(m: VerificationMismatch, service: str, names: TrackNames) -> MismatchOut:
    return MismatchOut(
        missing=names.tracks(service, _surplus(m.expected, m.actual)),
        unexpected=names.tracks(service, _surplus(m.actual, m.expected)),
    )


def _matches(plan: MemberPlan, service: str, names: TrackNames) -> list[MatchPairOut]:
    """Each source song beside the song it became on `service`, in source order."""
    added = {
        tid
        for op in plan.operations
        if isinstance(op, AddTracks)
        for tid in op.track_ids
    }
    return [
        MatchPairOut(
            source=names.track(e.source_key.service, e.source_key.track_id),
            target=names.track(service, e.target_track_id),
            added=e.target_track_id in added,
        )
        for e in plan.synced
    ]


def _member(
    member_id: str,
    place: Place | None,
    plan: MemberPlan | None,
    result: MemberResult | None,
    names: TrackNames,
) -> MemberOut:
    service = place.service if place is not None else None
    playlist_id = place.playlist_id if place is not None else None
    tracks_on = service or ""  # the service the member's song ids belong to
    operations = (
        []
        if plan is None
        else [_operation(op, tracks_on, names) for op in plan.operations]
    )
    matches = [] if plan is None else _matches(plan, tracks_on, names)
    if result is None:  # planned but not applied (a dry run, or never reached)
        not_made = plan.not_made if plan is not None else ()
        return MemberOut(
            member_id=member_id,
            service=service,
            playlist_id=playlist_id,
            status=None,
            error=None,
            rebuilt=False,
            created_playlist_id=None,
            operations=operations,
            not_made=[change_out(c, tracks_on, names) for c in not_made],
            mismatch=None,
            matches=matches,
        )
    return MemberOut(
        member_id=member_id,
        service=service,
        playlist_id=result.created_playlist_id
        if result.created_playlist_id is not None
        else playlist_id,
        status=result.status,
        error=result.error,
        rebuilt=result.rebuilt,
        created_playlist_id=result.created_playlist_id,
        operations=operations,
        not_made=[change_out(c, tracks_on, names) for c in result.not_made],
        mismatch=None
        if result.mismatch is None
        else _mismatch(result.mismatch, tracks_on, names),
        matches=matches,
    )


def _members(
    run: SyncRun, places: Mapping[str, Place], names: TrackNames
) -> list[MemberOut]:
    plans = {p.member_id: p for p in run.plan.member_plans} if run.plan else {}
    results = {r.member_id: r for r in run.members}
    ids = list(plans) + [mid for mid in results if mid not in plans]
    return [
        _member(mid, places.get(mid), plans.get(mid), results.get(mid), names)
        for mid in ids
    ]


def run_detail(services: AppServices, run: SyncRun) -> RunOut:
    ctx = services.contexts.get(run.run_id)
    names = TrackNames(ctx.tracks if ctx is not None else ())
    places = _member_places(services, run, ctx)
    unresolved = [
        UnresolvedOut(
            member_id=u.member_id,
            position=u.position,
            source=TrackOut.of(u.source),
            reasons=list(u.decision.reasons),
            candidates=[CandidateOut.of(c) for c in u.decision.candidates],
        )
        for u in (run.plan.unresolved if run.plan is not None else ())
    ]
    return RunOut(
        **RunSummaryOut.of(run).model_dump(),
        members=_members(run, places, names),
        unresolved=unresolved,
        undoes_run_id=run.undoes_run_id,
        undoable=undoable(services, run),
        undo_steps=_undo_steps(services, run, places, names),
    )


# -- routes -------------------------------------------------------------------
@router.get("/{run_id}")
async def get_run(run_id: str, services: Services) -> RunOut:
    return run_detail(services, found(services.repos.runs.get(run_id), UNKNOWN_RUN))


@router.post("/{run_id}/undo", status_code=202)
async def undo_run(run_id: str, services: Services) -> JobOut:
    run = found(services.repos.runs.get(run_id), UNKNOWN_RUN)
    if not undoable(services, run):
        raise HTTPException(status_code=409, detail=CANT_UNDO)
    return JobOut.of(services.jobs.submit(UndoJob(run_id), "Undo run"))


@router.post("/{run_id}/unresolved/{index}/attention")
async def unresolved_attention(
    run_id: str, index: int, services: Services
) -> AttentionOut:
    """The open "song didn't match" item for the run's `index`th unresolved
    song, made now when the run left none (dry runs and transfers don't)."""
    run = found(services.repos.runs.get(run_id), UNKNOWN_RUN)
    entries = run.plan.unresolved if run.plan is not None else ()
    if not 0 <= index < len(entries):
        raise HTTPException(status_code=404, detail=UNKNOWN_SONG)
    u = entries[index]
    place = _member_places(services, run, services.contexts.get(run_id)).get(
        u.member_id
    )
    if place is None:
        raise HTTPException(status_code=404, detail=UNKNOWN_SONG)
    repo = services.repos.attention
    target = ServiceId(place.service)
    for item in open_items(
        repo, AttentionKind.UNMATCHED, sync_id=run.sync_id, target_service=target
    ):
        if item.source is not None and item.source.key == u.source.key:
            return attention_out(services, item)
    item = repo.upsert(
        AttentionItem(
            item_id=services.new_id(),
            kind=AttentionKind.UNMATCHED,
            created_at=services.clock.now(),
            detail="; ".join(u.decision.reasons),
            sync_id=run.sync_id,
            member_id=u.member_id,
            service=target,
            source=u.source,
            target_service=target,
            candidates=u.decision.candidates,
        )
    )
    return attention_out(services, item)


@router.post("/{run_id}/alternatives")
async def alternatives(
    run_id: str, body: AlternativesIn, services: Services
) -> list[CandidateOut]:
    """Other songs on a member's service that a source song could become:
    looser matches, or what a text search finds when `query` is given."""
    found(services.repos.runs.get(run_id), UNKNOWN_RUN)
    ctx = found(services.contexts.get(run_id), UNKNOWN_RUN)
    member = found(
        next((m for m in ctx.members if m.member_id == body.member_id), None),
        UNKNOWN_SONG,
    )
    key = TrackKey(ServiceId(body.source_service), body.source_track_id)
    source = found(next((t for t in ctx.tracks if t.key == key), None), UNKNOWN_SONG)
    connector = services.connections.connectors().get(member.service)
    if connector is None:
        name = display_name(services, member.service)
        raise HTTPException(status_code=409, detail=f"{name} isn't connected")
    cfg = services.settings_store.get().engine.match
    try:
        if body.query is None:
            found_ = await find_similar(source, connector, cfg)
        else:
            found_ = await search_text(source, connector, cfg, body.query)
    except ConnectorError as e:
        raise connector_failed(services.connections, e, member.service) from None
    return [CandidateOut.of(c) for c in found_]
