"""Run routes: run detail (songs named from the run's context, D8) and undo."""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from linersync.domain.jobs import RunContext, UndoJob
from linersync.domain.operations import (
    AddTracks,
    ChangeNotMade,
    CreatePlaylist,
    MemberPlan,
    Operation,
    RemoveTracks,
    Reorder,
    UpdateMetadata,
)
from linersync.domain.runs import MemberResult, RunKind, SyncRun, VerificationMismatch
from linersync.domain.tracks import ServiceId, Track, TrackKey
from linersync.server.api.schemas import (
    CandidateOut,
    ChangeOut,
    JobOut,
    MemberOut,
    MismatchOut,
    OperationOut,
    OperationType,
    RunOut,
    TrackOut,
    UnresolvedOut,
    run_summary,
)
from linersync.server.auth import require_session
from linersync.server.state import AppServices, get_services

router = APIRouter(prefix="/api/runs", dependencies=[Depends(require_session)])

Services = Annotated[AppServices, Depends(get_services)]

_UNDOABLE_KINDS = frozenset({RunKind.SYNC, RunKind.TRANSFER})
CANT_UNDO = "This run can't be undone"


def _found(services: AppServices, run_id: str) -> SyncRun:
    run = services.repos.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run")
    return run


def undoable(services: AppServices, run: SyncRun) -> bool:
    """The rule `UndoService.undo` applies."""
    snapshots = services.repos.snapshots
    return (
        run.kind in _UNDOABLE_KINDS
        and not run.dry_run
        and any(snapshots.has_pre_write(run.run_id, m.member_id) for m in run.members)
    )


# -- view ---------------------------------------------------------------------
class TrackNames:
    """Looks songs up in the run's context; an unknown id keeps its id as title."""

    def __init__(self, tracks: Iterable[Track]) -> None:
        self._tracks: dict[TrackKey, Track] = {t.key: t for t in tracks}

    def track(self, service: str, track_id: str) -> TrackOut:
        found = self._tracks.get(TrackKey(ServiceId(service), track_id))
        if found is None:
            return TrackOut(
                service=service,
                track_id=track_id,
                title=track_id,
                artists=[],
                album=None,
                duration_ms=None,
                isrc=None,
                url=None,
                known=False,
            )
        return known_track(found)

    def tracks(self, service: str, track_ids: Iterable[str]) -> list[TrackOut]:
        return [self.track(service, tid) for tid in track_ids]


def known_track(t: Track) -> TrackOut:
    return TrackOut(
        service=t.service,
        track_id=t.track_id,
        title=t.title,
        artists=list(t.artists),
        album=t.album,
        duration_ms=t.duration_ms,
        isrc=t.isrc,
        url=t.url,
        known=True,
    )


def _member_places(
    services: AppServices, run: SyncRun, ctx: RunContext | None
) -> dict[str, tuple[str, str | None]]:
    """member id → (service, playlist id): the run context, else the saved sync."""
    places: dict[str, tuple[str, str | None]] = {}
    group = services.repos.syncs.get(run.sync_id)
    if group is not None:
        places = {m.member_id: (m.service, m.playlist_id) for m in group.members}
    if ctx is not None:
        places.update({m.member_id: (m.service, m.playlist_id) for m in ctx.members})
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


def change_out(change: ChangeNotMade, service: str, names: TrackNames) -> ChangeOut:
    return ChangeOut(
        kind=change.kind,
        detail=change.detail,
        track=None
        if change.track_id is None
        else names.track(service, change.track_id),
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


def _member(
    member_id: str,
    place: tuple[str, str | None] | None,
    plan: MemberPlan | None,
    result: MemberResult | None,
    names: TrackNames,
) -> MemberOut:
    service = place[0] if place is not None else None
    on = service or ""
    playlist_id = place[1] if place is not None else None
    if result is not None and result.created_playlist_id is not None:
        playlist_id = result.created_playlist_id
    not_made: Sequence[ChangeNotMade] = ()
    if result is not None:
        not_made = result.not_made
    elif plan is not None:
        not_made = plan.not_made
    return MemberOut(
        member_id=member_id,
        service=service,
        playlist_id=playlist_id,
        status=None if result is None else result.status,
        error=None if result is None else result.error,
        rebuilt=result is not None and result.rebuilt,
        created_playlist_id=None if result is None else result.created_playlist_id,
        operations=[]
        if plan is None
        else [_operation(op, on, names) for op in plan.operations],
        not_made=[change_out(c, on, names) for c in not_made],
        mismatch=None
        if result is None or result.mismatch is None
        else _mismatch(result.mismatch, on, names),
    )


def _members(
    run: SyncRun, places: Mapping[str, tuple[str, str | None]], names: TrackNames
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
            source=known_track(u.source),
            reasons=list(u.decision.reasons),
            candidates=[
                CandidateOut(
                    track=known_track(c.track),
                    confidence=c.confidence,
                    reasons=list(c.reasons),
                )
                for c in u.decision.candidates
            ],
        )
        for u in (run.plan.unresolved if run.plan is not None else ())
    ]
    return RunOut(
        **run_summary(run).model_dump(),
        members=_members(run, places, names),
        unresolved=unresolved,
        undoes_run_id=run.undoes_run_id,
        undoable=undoable(services, run),
    )


# -- routes -------------------------------------------------------------------
@router.get("/{run_id}")
async def get_run(run_id: str, services: Services) -> RunOut:
    return run_detail(services, _found(services, run_id))


@router.post("/{run_id}/undo", status_code=202)
async def undo_run(run_id: str, services: Services) -> JobOut:
    run = _found(services, run_id)
    if not undoable(services, run):
        raise HTTPException(status_code=409, detail=CANT_UNDO)
    return JobOut.of(services.jobs.submit(UndoJob(run_id), "Undo run"))
