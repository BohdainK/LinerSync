"""Cron scheduling. It only calls `on_due(sync_id)`; the server submits the job."""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import partial

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from linersync.domain.sync import SyncGroup, SyncStatus

log = logging.getLogger(__name__)

COALESCE = True
MAX_INSTANCES = 1
MISFIRE_GRACE_SECONDS = 300
_PREFIX = "sync:"
_BACKUP_ID = "backup"


def cron_problem(expr: str, timezone: str) -> str | None:
    try:
        CronTrigger.from_crontab(expr, timezone=timezone)
    except ValueError as exc:
        return str(exc) or "invalid cron expression"
    return None


class SyncScheduler:
    def __init__(
        self,
        on_due: Callable[[str], None],
        timezone: str,
        on_backup_due: Callable[[], None] = lambda: None,
    ) -> None:
        self._on_due = on_due
        self._on_backup_due = on_backup_due
        self._timezone = timezone
        self.aps = AsyncIOScheduler(
            timezone=timezone,
            job_defaults={
                "coalesce": COALESCE,
                "max_instances": MAX_INSTANCES,
                "misfire_grace_time": MISFIRE_GRACE_SECONDS,
            },
        )

    async def _fire(self, sync_id: str) -> None:
        # Coroutine jobs run on the event loop; sync ones run in a worker thread.
        self._on_due(sync_id)

    async def _fire_backup(self) -> None:
        self._on_backup_due()

    def start(self) -> None:
        self.aps.start()

    def shutdown(self) -> None:
        if self.aps.running:
            self.aps.shutdown(wait=False)

    def reconcile(self, groups: Sequence[SyncGroup], default_schedule: str) -> None:
        wanted: set[str] = set()
        for g in groups:
            if g.status is not SyncStatus.ACTIVE:
                continue
            expr = g.schedule or default_schedule
            try:
                trigger = CronTrigger.from_crontab(expr, timezone=self._timezone)
            except ValueError:
                log.warning("skipping sync %s: invalid schedule %r", g.sync_id, expr)
                continue
            job_id = _PREFIX + g.sync_id
            wanted.add(job_id)
            self.aps.add_job(
                partial(self._fire, g.sync_id),
                trigger,
                id=job_id,
                replace_existing=True,
                coalesce=COALESCE,
                max_instances=MAX_INSTANCES,
                misfire_grace_time=MISFIRE_GRACE_SECONDS,
            )
        for job in self.aps.get_jobs():
            if job.id.startswith(_PREFIX) and job.id not in wanted:
                self.aps.remove_job(job.id)

    def schedule_backup(self, expr: str | None) -> None:
        """Set, replace or (None or an invalid expression) remove the backup job."""
        trigger: CronTrigger | None = None
        if expr is not None:
            try:
                trigger = CronTrigger.from_crontab(expr, timezone=self._timezone)
            except ValueError:
                log.warning("skipping backup schedule: invalid expression %r", expr)
        if trigger is None:
            if self.aps.get_job(_BACKUP_ID) is not None:
                self.aps.remove_job(_BACKUP_ID)
            return
        self.aps.add_job(
            self._fire_backup,
            trigger,
            id=_BACKUP_ID,
            replace_existing=True,
            coalesce=COALESCE,
            max_instances=MAX_INSTANCES,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )

    def next_backup_at(self) -> datetime | None:
        job = self.aps.get_job(_BACKUP_ID)
        nxt: datetime | None = getattr(job, "next_run_time", None)
        return None if nxt is None else nxt.astimezone(UTC)

    def next_runs(self) -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        for job in self.aps.get_jobs():
            nxt: datetime | None = getattr(job, "next_run_time", None)
            if job.id.startswith(_PREFIX) and nxt is not None:
                out[job.id.removeprefix(_PREFIX)] = nxt.astimezone(UTC)
        return out
