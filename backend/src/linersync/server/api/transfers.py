"""Transfer routes: one playlist, many playlists, and the bulk estimate."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from linersync.application.runner import SyncRunner
from linersync.application.transfer import BulkItem, TransferService
from linersync.domain.jobs import BulkTransferJob, TransferJob
from linersync.domain.tracks import ServiceId
from linersync.ports.errors import ConnectorError
from linersync.server.api.schemas import (
    BulkItemIn,
    BulkTransferIn,
    EstimateIn,
    EstimateOut,
    JobOut,
    PlaylistOut,
    TransferIn,
)
from linersync.server.api.syncs import check_destinations, check_source
from linersync.server.auth import require_session
from linersync.server.state import AppServices, get_services

router = APIRouter(prefix="/api/transfers", dependencies=[Depends(require_session)])

Services = Annotated[AppServices, Depends(get_services)]


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
    job = services.jobs.submit(request, f"Transfer to {destination}")
    return JobOut.of(job)


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
    n = len(body.items)
    label = f"Transfer {n} playlist{'' if n == 1 else 's'}"
    job = services.jobs.submit(BulkTransferJob(transfers), label)
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
        if e.auth_required:
            services.connections.on_auth_required(e.service)
        detail = e.message or "The service could not be reached"
        raise HTTPException(status_code=502, detail=detail) from None
    return EstimateOut(
        playlists=found.playlists,
        songs=found.songs,
        api_calls={str(s): n for s, n in found.api_calls.items()},
        add_only_destinations=list(found.add_only_destinations),
        unreadable=[PlaylistOut.of(m) for m in found.unreadable],
    )
