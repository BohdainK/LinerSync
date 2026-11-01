"""Needs-attention routes: list, pick, not on service, dismiss, approve, resume."""

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Response

from linersync.application.attention import AttentionService
from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.jobs import SyncJob
from linersync.domain.runs import Trigger
from linersync.server.api.common import Services, found, reconcile_schedule
from linersync.server.api.schemas import (
    ActionOut,
    AttentionOut,
    CandidateOut,
    JobOut,
    PickIn,
    RunNowIn,
    TrackOut,
)
from linersync.server.api.views import TrackNames, change_out
from linersync.server.auth import require_session
from linersync.server.state import AppServices

router = APIRouter(prefix="/api/attention", dependencies=[Depends(require_session)])

NOT_A_CANDIDATE = "Pick one of the suggested songs"
UNKNOWN_ITEM = "Unknown item"


def _found(services: AppServices, item_id: str) -> AttentionItem:
    return found(services.repos.attention.get(item_id), UNKNOWN_ITEM)


def _resolve(action: Callable[[], None]) -> None:
    """Run an AttentionService action; a resolved or wrong-kind item is 409."""
    try:
        action()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


def _attention(services: AppServices) -> AttentionService:
    return AttentionService(services.repos, services.clock)


def _attention_out(services: AppServices, item: AttentionItem) -> AttentionOut:
    group = services.repos.syncs.get(item.sync_id) if item.sync_id is not None else None
    tracks = [c.track for c in item.candidates]
    if item.source is not None:
        tracks.append(item.source)
    names = TrackNames(tracks)
    return AttentionOut(
        item_id=item.item_id,
        kind=item.kind,
        created_at=item.created_at,
        detail=item.detail,
        sync_id=item.sync_id,
        sync_name=None if group is None else group.name,
        member_id=item.member_id,
        service=item.service,
        source=None if item.source is None else TrackOut.of(item.source),
        target_service=item.target_service,
        candidates=[CandidateOut.of(c) for c in item.candidates],
        not_made=[change_out(c, item.service or "", names) for c in item.not_made],
    )


def _run_now(services: AppServices, item: AttentionItem, body: RunNowIn) -> ActionOut:
    if not body.run_now or item.sync_id is None:
        return ActionOut(job=None)
    group = services.repos.syncs.get(item.sync_id)
    if group is None:
        return ActionOut(job=None)
    job = services.jobs.submit(
        SyncJob(group.sync_id, Trigger.MANUAL), f"Sync “{group.name}”"
    )
    return ActionOut(job=JobOut.of(job))


# -- routes -------------------------------------------------------------------
@router.get("")
async def list_attention(services: Services) -> list[AttentionOut]:
    items = sorted(
        services.repos.attention.list_open(),
        key=lambda i: i.created_at,
        reverse=True,
    )
    return [_attention_out(services, i) for i in items]


@router.post("/{item_id}/pick", status_code=204)
async def pick(item_id: str, body: PickIn, services: Services) -> Response:
    item = _found(services, item_id)
    is_open = item.kind is AttentionKind.UNMATCHED and item.resolved_at is None
    candidates = {c.track.track_id for c in item.candidates}
    if is_open and body.target_track_id not in candidates:
        raise HTTPException(status_code=422, detail=NOT_A_CANDIDATE)
    _resolve(lambda: _attention(services).pick_candidate(item_id, body.target_track_id))
    return Response(status_code=204)


@router.post("/{item_id}/not-on-service", status_code=204)
async def not_on_service(item_id: str, services: Services) -> Response:
    _found(services, item_id)
    _resolve(lambda: _attention(services).mark_not_on_service(item_id))
    return Response(status_code=204)


@router.post("/{item_id}/dismiss", status_code=204)
async def dismiss(item_id: str, services: Services) -> Response:
    _found(services, item_id)
    _resolve(lambda: _attention(services).dismiss(item_id))
    return Response(status_code=204)


@router.post("/{item_id}/approve")
async def approve(
    item_id: str, services: Services, body: RunNowIn | None = None
) -> ActionOut:
    item = _found(services, item_id)
    _resolve(lambda: _attention(services).approve(item_id))
    return _run_now(services, item, body or RunNowIn())


@router.post("/{item_id}/resume")
async def resume(
    item_id: str, services: Services, body: RunNowIn | None = None
) -> ActionOut:
    item = _found(services, item_id)
    _resolve(lambda: _attention(services).resume_sync(item_id))
    reconcile_schedule(services)
    return _run_now(services, item, body or RunNowIn())
