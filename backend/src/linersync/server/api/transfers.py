"""Transfer routes: one playlist, many playlists, and the bulk estimate."""

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException

from linersync.application.picks import remember_picks
from linersync.application.runner import SyncRunner
from linersync.application.transfer import BulkItem, TransferService
from linersync.domain.jobs import BulkTransferJob, JobStatus, TransferJob
from linersync.domain.tracks import ServiceId
from linersync.ports.errors import ConnectorError
from linersync.server.api.common import (
    Services,
    check_destinations,
    check_source,
    connector_failed,
    display_name,
    invalid,
    pick_tuples,
)
from linersync.server.api.schemas import (
    BulkItemIn,
    BulkTransferIn,
    EstimateIn,
    EstimateOut,
    FromPreviewIn,
    JobOut,
    PlaylistOut,
    TransferIn,
)
from linersync.server.auth import require_session
from linersync.server.state import AppServices

NOT_A_PREVIEW = "That is not a finished preview of a transfer"
PREVIEW_INCOMPLETE = "The preview did not finish for every playlist; preview again"

router = APIRouter(prefix="/api/transfers", dependencies=[Depends(require_session)])


def _check_items(services: AppServices, items: list[BulkItemIn]) -> None:
    for item in items:
        check_source(services, item.source_service, item.playlist_id)
        check_destinations(services, item.destination_services)


@router.post("", status_code=202)
async def transfer(body: TransferIn, services: Services) -> JobOut:
    check_source(services, body.source_service, body.playlist_id)
    check_destinations(services, [body.destination])
    destination = services.registry.plugins[ServiceId(body.destination)].display_name
    request = TransferJob(
        ServiceId(body.source_service),
        body.playlist_id,
        ServiceId(body.destination),
        body.dry_run,
    )
    job = services.jobs.submit(request, _transfer_label(destination))
    return JobOut.of(job)


def _transfer_label(destination: str) -> str:
    return f"Transfer to {destination}"


def _bulk_label(playlists: int) -> str:
    return f"Transfer {playlists} playlist{'' if playlists == 1 else 's'}"


@router.post("/bulk", status_code=202)
async def bulk_transfer(body: BulkTransferIn, services: Services) -> JobOut:
    _check_items(services, body.items)
    transfers = tuple(
        TransferJob(
            ServiceId(item.source_service),
            item.playlist_id,
            ServiceId(destination),
            body.dry_run,
        )
        for item in body.items
        for destination in item.destination_services
    )
    job = services.jobs.submit(BulkTransferJob(transfers), _bulk_label(len(body.items)))
    return JobOut.of(job)


@router.post("/from-preview", status_code=202)
async def from_preview(body: FromPreviewIn, services: Services) -> JobOut:
    """Start the real transfer for a finished preview, reusing its matches and
    applying the person's swaps."""
    preview = services.job_store.get(body.preview_job_id)
    request = None if preview is None else preview.request
    if preview is None or not isinstance(request, TransferJob | BulkTransferJob):
        raise invalid(NOT_A_PREVIEW)
    transfers = (
        request.transfers if isinstance(request, BulkTransferJob) else (request,)
    )
    if not all(t.dry_run for t in transfers):
        raise invalid(NOT_A_PREVIEW)
    if preview.status is not JobStatus.SUCCEEDED or len(preview.run_ids) != len(
        transfers
    ):
        raise HTTPException(status_code=409, detail=PREVIEW_INCOMPLETE)
    real = tuple(
        replace(t, dry_run=False, preview_run_id=run_id)
        for t, run_id in zip(transfers, preview.run_ids, strict=True)
    )
    remember_picks(services.repos.matches, pick_tuples(body.picks))
    if isinstance(request, BulkTransferJob):
        playlists = len({(t.source_service, t.playlist_id) for t in real})
        job = services.jobs.submit(BulkTransferJob(real), _bulk_label(playlists))
    else:
        destination = display_name(services, real[0].destination)
        job = services.jobs.submit(real[0], _transfer_label(destination))
    return JobOut.of(job)


@router.post("/estimate")
async def estimate(body: EstimateIn, services: Services) -> EstimateOut:
    _check_items(services, body.items)
    items = [
        BulkItem(
            ServiceId(item.source_service),
            item.playlist_id,
            tuple(ServiceId(d) for d in item.destination_services),
        )
        for item in body.items
    ]
    deps = services.jobs.build_deps()
    try:
        found = await TransferService(deps, SyncRunner(deps)).estimate_bulk(items)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except ConnectorError as e:
        raise connector_failed(services.connections, e, e.service) from None
    return EstimateOut(
        playlists=found.playlists,
        songs=found.songs,
        api_calls={str(s): n for s, n in found.api_calls.items()},
        add_only_destinations=list(found.add_only_destinations),
        unreadable=[PlaylistOut.of(m) for m in found.unreadable],
    )
