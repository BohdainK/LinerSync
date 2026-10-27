from datetime import datetime

from linersync.domain.jobs import Job, JobStatus, RunContext


def _aware(dt: datetime | None) -> None:
    if dt is not None and (dt.tzinfo is None or dt.utcoffset() is None):
        raise ValueError("naive datetime")


class InMemoryJobs:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def save(self, job: Job) -> None:
        _aware(job.created_at)
        _aware(job.finished_at)
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_by_status(self, *statuses: JobStatus) -> list[Job]:
        found = [j for j in self._jobs.values() if j.status in statuses]
        return sorted(found, key=lambda j: j.created_at)

    def list_recent(self, limit: int) -> list[Job]:
        done = [j for j in self._jobs.values() if j.finished]
        with_time = sorted(
            (j for j in done if j.finished_at is not None),
            key=lambda j: j.finished_at or j.created_at,
            reverse=True,
        )
        without = [j for j in done if j.finished_at is None]
        return (with_time + without)[:limit]


class InMemoryRunContexts:
    def __init__(self) -> None:
        self._items: dict[str, RunContext] = {}

    def save(self, ctx: RunContext) -> None:
        self._items[ctx.run_id] = ctx

    def get(self, run_id: str) -> RunContext | None:
        return self._items.get(run_id)
