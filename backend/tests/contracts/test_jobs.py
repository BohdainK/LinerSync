from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from linersync.adapters.memory.jobs import InMemoryJobs, InMemoryRunContexts
from linersync.adapters.sqlite.db import connect, migrate
from linersync.adapters.sqlite.jobs import SqliteJobs, SqliteRunContexts
from linersync.domain.jobs import (
    BackupJob,
    BulkTransferJob,
    Job,
    JobProgress,
    JobRequest,
    JobStatus,
    PreviewJob,
    RunContext,
    RunMemberInfo,
    SyncJob,
    TransferJob,
    UndoJob,
)
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.runs import Trigger
from linersync.domain.sync import MemberRole, MemberState, SyncGroup
from linersync.domain.tracks import ServiceId
from linersync.ports.jobs import JobRepository, RunContextRepository
from tests.factories import NOW, group, track

Pair = tuple[JobRepository, RunContextRepository]


def _memory() -> Pair:
    return InMemoryJobs(), InMemoryRunContexts()


def _sqlite() -> Pair:
    e = connect(":memory:")
    migrate(e)
    return SqliteJobs(e), SqliteRunContexts(e)


pytestmark = pytest.mark.parametrize("make", [_memory, _sqlite])
Factory = Callable[[], Pair]


def _job(
    job_id: str,
    request: JobRequest | None = None,
    status: JobStatus = JobStatus.QUEUED,
    created: datetime = NOW,
    finished: datetime | None = None,
) -> Job:
    return Job(
        job_id=job_id,
        request=request or SyncJob("s1", Trigger.MANUAL),
        status=status,
        created_at=created,
        label="Mix",
        finished_at=finished,
    )


def test_job_roundtrip_each_request_kind(make: Factory) -> None:
    jobs, _ = make()
    g = group()
    synced = replace(
        g.members[1],
        last_synced=MemberState(
            entries=(),
            draft=PlaylistDraft(name="Mix"),
            version_token="v1",
            synced_at=NOW,
        ),
    )
    g = SyncGroup(g.sync_id, g.name, g.direction, (g.members[0], synced))
    transfer = TransferJob(ServiceId("spotify"), "P1", ServiceId("tidal"), True)
    requests: list[JobRequest] = [
        SyncJob("s1", Trigger.SCHEDULED, True),
        PreviewJob(g),
        transfer,
        BulkTransferJob((transfer, replace(transfer, dry_run=False))),
        UndoJob("r1"),
    ]
    expected_kinds = ["sync", "preview", "transfer", "bulk_transfer", "undo"]
    for i, request in enumerate(requests):
        job = replace(
            _job(f"j{i}", request),
            started_at=NOW,
            run_ids=("r1", "r2"),
            error="boom",
            progress=JobProgress("apply", 1, 2, "hi"),
            cancel_requested=True,
        )
        assert job.kind == expected_kinds[i]
        jobs.save(job)
        assert jobs.get(f"j{i}") == job
    assert jobs.get("nope") is None
    assert _job("a").sync_id == "s1"
    assert _job("b", PreviewJob(g)).sync_id is None


def test_backup_job_roundtrip(make: Factory) -> None:
    jobs, _ = make()
    for i, request in enumerate(
        [BackupJob(), BackupJob((ServiceId("spotify"),), Trigger.SCHEDULED)]
    ):
        job = replace(
            _job(f"b{i}", request),
            backup_ids=("bk1", "bk2"),
            progress=JobProgress("backup", 0, 2, "Spotify: Mix"),
        )
        assert job.kind == "backup"
        assert job.sync_id is None
        jobs.save(job)
        assert jobs.get(f"b{i}") == job
    assert _job("x").backup_ids == ()


def test_list_by_status_oldest_first(make: Factory) -> None:
    jobs, _ = make()
    jobs.save(_job("c", created=NOW + timedelta(seconds=2)))
    jobs.save(_job("a", created=NOW))
    jobs.save(_job("b", status=JobStatus.RUNNING, created=NOW + timedelta(seconds=1)))
    jobs.save(_job("d", status=JobStatus.FAILED, created=NOW))
    assert [j.job_id for j in jobs.list_by_status(JobStatus.QUEUED)] == ["a", "c"]
    assert [
        j.job_id for j in jobs.list_by_status(JobStatus.QUEUED, JobStatus.RUNNING)
    ] == ["a", "b", "c"]
    assert jobs.list_by_status() == []


def test_list_by_status_ties_keep_insertion_order(make: Factory) -> None:
    jobs, _ = make()
    for job_id in ("c", "a", "b"):
        jobs.save(_job(job_id))
    first = jobs.get("c")
    assert first is not None
    jobs.save(replace(first, status=JobStatus.RUNNING, started_at=NOW))
    jobs.save(first)
    got = jobs.list_by_status(JobStatus.QUEUED, JobStatus.RUNNING)
    assert [j.job_id for j in got] == ["c", "a", "b"]


def test_list_recent_finished_only_newest_first_limited(make: Factory) -> None:
    jobs, _ = make()
    jobs.save(_job("q"))
    jobs.save(_job("r", status=JobStatus.RUNNING))
    for i, status in enumerate(
        [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]
    ):
        jobs.save(_job(f"f{i}", status=status, finished=NOW + timedelta(seconds=i)))
    jobs.save(_job("old", status=JobStatus.INTERRUPTED))
    assert [j.job_id for j in jobs.list_recent(2)] == ["f2", "f1"]
    assert [j.job_id for j in jobs.list_recent(10)] == ["f2", "f1", "f0", "old"]


def test_save_replaces(make: Factory) -> None:
    jobs, _ = make()
    job = _job("j")
    jobs.save(job)
    jobs.save(replace(job, status=JobStatus.RUNNING, started_at=NOW))
    assert jobs.list_by_status(JobStatus.QUEUED) == []
    running = jobs.list_by_status(JobStatus.RUNNING)
    assert [j.job_id for j in running] == ["j"]
    assert running[0].started_at == NOW


def test_naive_datetime_rejected(make: Factory) -> None:
    jobs, _ = make()
    with pytest.raises(ValueError):
        jobs.save(_job("j", created=datetime(2026, 1, 1)))


def test_run_context_roundtrip_and_replace(make: Factory) -> None:
    _, contexts = make()
    ctx = RunContext(
        "r1",
        (
            RunMemberInfo("src", ServiceId("spotify"), "P1", MemberRole.SOURCE),
            RunMemberInfo("dst0", ServiceId("tidal"), None, MemberRole.DESTINATION),
        ),
        (track("spotify", "T1"),),
    )
    assert contexts.get("r1") is None
    contexts.save(ctx)
    assert contexts.get("r1") == ctx
    ctx2 = replace(ctx, tracks=(track("spotify", "T2"),))
    contexts.save(ctx2)
    assert contexts.get("r1") == ctx2
