"""Job routes: list, get and cancel."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from linersync.domain.jobs import Job, JobStatus
from linersync.server.api.common import Services, found
from linersync.server.api.schemas import JobOut
from linersync.server.auth import require_session

router = APIRouter(prefix="/api/jobs", dependencies=[Depends(require_session)])

RECENT_LIMIT = 50
UNKNOWN_JOB = "Unknown job"
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
    return JobOut.of(found(services.job_store.get(job_id), UNKNOWN_JOB))


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, services: Services) -> JobOut:
    try:
        job = services.jobs.cancel(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=UNKNOWN_JOB) from None
    except ValueError:
        raise HTTPException(status_code=409, detail=_FINISHED) from None
    return JobOut.of(job)
