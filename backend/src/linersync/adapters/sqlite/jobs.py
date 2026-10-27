"""SQLite implementations of the job and run-context ports."""

import sqlalchemy as sa
from sqlalchemy import Engine

from linersync.adapters.sqlite import schema as t
from linersync.adapters.sqlite.codec import dumps, loads
from linersync.adapters.sqlite.db import utc_text
from linersync.domain.jobs import ACTIVE_STATUSES, Job, JobStatus, RunContext


class SqliteJobs:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, job: Job) -> None:
        values = {
            "status": str(job.status),
            "sync_id": job.sync_id,
            "created_at": utc_text(job.created_at),
            "finished_at": None
            if job.finished_at is None
            else utc_text(job.finished_at),
            "body": dumps(Job, job),
        }
        with self._engine.begin() as conn:
            updated = conn.execute(
                sa.update(t.jobs).where(t.jobs.c.job_id == job.job_id).values(**values)
            ).rowcount
            if updated == 0:
                conn.execute(sa.insert(t.jobs).values(job_id=job.job_id, **values))

    def get(self, job_id: str) -> Job | None:
        with self._engine.begin() as conn:
            body = conn.execute(
                sa.select(t.jobs.c.body).where(t.jobs.c.job_id == job_id)
            ).scalar_one_or_none()
        return None if body is None else loads(Job, body)

    def list_by_status(self, *statuses: JobStatus) -> list[Job]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                sa.select(t.jobs.c.body)
                .where(t.jobs.c.status.in_([str(s) for s in statuses]))
                .order_by(t.jobs.c.created_at, sa.text("jobs.rowid"))
            ).scalars()
            return [loads(Job, b) for b in rows]

    def list_recent(self, limit: int) -> list[Job]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                sa.select(t.jobs.c.body)
                .where(t.jobs.c.status.not_in([str(s) for s in ACTIVE_STATUSES]))
                .order_by(sa.nulls_last(t.jobs.c.finished_at.desc()), t.jobs.c.job_id)
                .limit(limit)
            ).scalars()
            return [loads(Job, b) for b in rows]


class SqliteRunContexts:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, ctx: RunContext) -> None:
        body = dumps(RunContext, ctx)
        with self._engine.begin() as conn:
            updated = conn.execute(
                sa.update(t.run_contexts)
                .where(t.run_contexts.c.run_id == ctx.run_id)
                .values(body=body)
            ).rowcount
            if updated == 0:
                conn.execute(
                    sa.insert(t.run_contexts).values(run_id=ctx.run_id, body=body)
                )

    def get(self, run_id: str) -> RunContext | None:
        with self._engine.begin() as conn:
            body = conn.execute(
                sa.select(t.run_contexts.c.body).where(
                    t.run_contexts.c.run_id == run_id
                )
            ).scalar_one_or_none()
        return None if body is None else loads(RunContext, body)
