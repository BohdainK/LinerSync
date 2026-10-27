"""Job routes: list, get and cancel."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from linersync.domain.jobs import Job, JobStatus
from linersync.server.api.schemas import JobOut
from linersync.server.auth import require_session
from linersync.server.state import AppServices, get_services

router = APIRouter(prefix="/api/jobs", dependencies=[Depends(require_session)])

Services = Annotated[AppServices, Depends(get_services)]

RECENT_LIMIT = 50
_FINISHED = "This job has already finished"


@router.get("")
async def list_jobs(
    services: Services,
    state: Annotated[Literal["active", "recent"], Query()] = "active",
) -> list[JobOut]:
    store = services.job_store
    jobs: list[Job]
    if state == "active":
        jobs = [
            *store.list_by_status(JobStatus.RUNNING),
            *store.list_by_status(JobStatus.QUEUED),
        ]
    else:
        jobs = store.list_recent(RECENT_LIMIT)
    return [JobOut.of(j) for j in jobs]


@router.get("/{job_id}")
async def get_job(job_id: str, services: Services) -> JobOut:
    job = services.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return JobOut.of(job)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, services: Services) -> JobOut:
    try:
        job = services.jobs.cancel(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown job") from None
    except ValueError:
        raise HTTPException(status_code=409, detail=_FINISHED) from None
    return JobOut.of(job)
