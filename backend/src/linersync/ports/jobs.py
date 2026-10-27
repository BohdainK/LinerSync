from typing import Protocol

from linersync.domain.jobs import Job, JobStatus, RunContext


class JobRepository(Protocol):
    def save(self, job: Job) -> None: ...
    def get(self, job_id: str) -> Job | None: ...
    def list_by_status(self, *statuses: JobStatus) -> list[Job]:
        """Oldest created_at first."""
        ...

    def list_recent(self, limit: int) -> list[Job]:
        """Finished jobs only, newest finished_at first."""
        ...


class RunContextRepository(Protocol):
    def save(self, ctx: RunContext) -> None:
        """Replaces any context for the same run."""
        ...

    def get(self, run_id: str) -> RunContext | None: ...
