"""SQLite implementations of the job and run-context ports."""

import sqlalchemy as sa
from sqlalchemy import Engine

from linersync.adapters.sqlite import schema as t
from linersync.adapters.sqlite.codec import dumps
from linersync.adapters.sqlite.db import (
    load_all,
    load_one,
    upsert,
    utc_text,
    utc_text_or_none,
)
from linersync.domain.jobs import ACTIVE_STATUSES, Job, JobStatus, RunContext


class SqliteJobs:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, job: Job) -> None:
        values = {
            "status": str(job.status),
            "sync_id": job.sync_id,
            "created_at": utc_text(job.created_at),
            "finished_at": utc_text_or_none(job.finished_at),
            "body": dumps(Job, job),
        }
        with self._engine.begin() as conn:
            upsert(conn, t.jobs, {"job_id": job.job_id}, values)

    def get(self, job_id: str) -> Job | None:
        query = sa.select(t.jobs.c.body).where(t.jobs.c.job_id == job_id)
        return load_one(self._engine, Job, query)

    def list_by_status(self, *statuses: JobStatus) -> list[Job]:
        query = (
            sa.select(t.jobs.c.body)
            .where(t.jobs.c.status.in_([str(s) for s in statuses]))
            .order_by(t.jobs.c.created_at, sa.text("jobs.rowid"))
        )
        return load_all(self._engine, Job, query)

    def list_recent(self, limit: int) -> list[Job]:
        query = (
            sa.select(t.jobs.c.body)
            .where(t.jobs.c.status.not_in([str(s) for s in ACTIVE_STATUSES]))
            .order_by(sa.nulls_last(t.jobs.c.finished_at.desc()), t.jobs.c.job_id)
            .limit(limit)
        )
        return load_all(self._engine, Job, query)


class SqliteRunContexts:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, ctx: RunContext) -> None:
        body = dumps(RunContext, ctx)
        with self._engine.begin() as conn:
            upsert(conn, t.run_contexts, {"run_id": ctx.run_id}, {"body": body})

    def get(self, run_id: str) -> RunContext | None:
        query = sa.select(t.run_contexts.c.body).where(
            t.run_contexts.c.run_id == run_id
        )
        return load_one(self._engine, RunContext, query)
