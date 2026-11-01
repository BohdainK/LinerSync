"""Stats: counts over what LinerSync already stores. Pure functions, no I/O."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from linersync.domain.backups import BackedUpPlaylist, BackupRun
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.operations import AddTracks, RemoveTracks
from linersync.domain.runs import RunKind, RunStatus, SyncRun
from linersync.domain.tracks import ServiceId
from linersync.ports.quota import local_midnight

FAILED_STATUSES = frozenset({RunStatus.FAILED, RunStatus.ABORTED})
WAITING_STATUSES = frozenset(
    {RunStatus.AWAITING_APPROVAL, RunStatus.PAUSED_SOURCE_MISSING}
)


@dataclass(frozen=True, slots=True)
class ServiceLibrary:
    service: ServiceId
    playlists: int = 0  # not archived
    songs: int = 0  # summed over the playlists that aren't archived
    archived: int = 0
    versions: int = 0


@dataclass(frozen=True, slots=True)
class Activity:
    sync_runs: int = 0
    transfer_runs: int = 0
    undo_runs: int = 0
    succeeded: int = 0
    partial: int = 0
    failed: int = 0
    waiting: int = 0
    skipped: int = 0
    songs_added: int = 0
    songs_removed: int = 0
    backups: int = 0


@dataclass(frozen=True, slots=True)
class Day:
    day: date  # local to the server's timezone
    succeeded: int = 0
    partial: int = 0  # partly done, or waiting for approval or the source
    failed: int = 0


@dataclass(frozen=True, slots=True)
class MatchingStats:
    remembered: int = 0
    not_on_service: int = 0


def library(entries: Iterable[BackedUpPlaylist]) -> list[ServiceLibrary]:
    """One row per service with backed-up playlists, sorted by service id."""
    rows: dict[ServiceId, ServiceLibrary] = {}
    for e in entries:
        row = rows.get(e.service, ServiceLibrary(e.service))
        row = replace(row, versions=row.versions + e.versions)
        if e.archived:
            row = replace(row, archived=row.archived + 1)
        else:
            row = replace(
                row, playlists=row.playlists + 1, songs=row.songs + e.track_count
            )
        rows[e.service] = row
    return [rows[s] for s in sorted(rows)]


def changes_made(run: SyncRun) -> tuple[int, int]:
    """(songs added, songs removed) by the operations the run applied. Only
    operations in the stored plan count: undo runs have no plan, and the ops
    of a re-plan after a failed write aren't stored, so neither is counted."""
    if run.plan is None or run.dry_run:
        return 0, 0
    applied = {op_id for m in run.members for op_id in m.applied_op_ids}
    added = removed = 0
    for plan in run.plan.member_plans:
        for op in plan.operations:
            if f"{run.run_id}:{op.op_id}" not in applied:
                continue
            if isinstance(op, AddTracks):
                added += len(op.track_ids)
            elif isinstance(op, RemoveTracks):
                removed += len(op.removals)
    return added, removed


def activity(runs: Iterable[SyncRun], backups: Iterable[BackupRun]) -> Activity:
    """Counts over finished runs that weren't dry runs, and the backup runs.
    The caller picks the window."""
    kinds = dict.fromkeys(RunKind, 0)
    statuses = dict.fromkeys(RunStatus, 0)
    added = removed = 0
    for run in runs:
        if run.dry_run or run.status is RunStatus.RUNNING:
            continue
        kinds[run.kind] += 1
        statuses[run.status] += 1
        a, r = changes_made(run)
        added += a
        removed += r
    return Activity(
        sync_runs=kinds[RunKind.SYNC],
        transfer_runs=kinds[RunKind.TRANSFER],
        undo_runs=kinds[RunKind.UNDO],
        succeeded=statuses[RunStatus.SUCCEEDED],
        partial=statuses[RunStatus.PARTIAL],
        failed=sum(statuses[s] for s in FAILED_STATUSES),
        waiting=sum(statuses[s] for s in WAITING_STATUSES),
        skipped=statuses[RunStatus.SKIPPED],
        songs_added=added,
        songs_removed=removed,
        backups=sum(1 for _ in backups),
    )


def window_start(now: datetime, days: int, zone: ZoneInfo) -> datetime:
    """Local midnight starting the last `days` days, today included, in UTC."""
    first = now.astimezone(zone).date() - timedelta(days=days - 1)
    return local_midnight(first, zone)


def daily(runs: Iterable[SyncRun], first: date, days: int, zone: ZoneInfo) -> list[Day]:
    """One Day per local date from `first`, over finished runs that weren't
    dry runs. Skipped runs and runs outside the days are left out."""
    counts = {first + timedelta(days=i): [0, 0, 0] for i in range(days)}
    for run in runs:
        if run.dry_run:
            continue
        bucket = counts.get(run.started_at.astimezone(zone).date())
        if bucket is None:
            continue
        if run.status is RunStatus.SUCCEEDED:
            bucket[0] += 1
        elif run.status is RunStatus.PARTIAL or run.status in WAITING_STATUSES:
            bucket[1] += 1
        elif run.status in FAILED_STATUSES:
            bucket[2] += 1
    return [Day(d, *c) for d, c in counts.items()]


def matching(matches: Iterable[RememberedMatch]) -> MatchingStats:
    remembered = not_on_service = 0
    for m in matches:
        remembered += 1
        if m.target_track_id is None:
            not_on_service += 1
    return MatchingStats(remembered=remembered, not_on_service=not_on_service)
