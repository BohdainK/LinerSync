from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from linersync.application.stats import (
    Activity,
    Day,
    MatchingStats,
    ServiceLibrary,
    activity,
    changes_made,
    daily,
    library,
    matching,
    window_start,
)
from linersync.domain.backups import BackedUpPlaylist, BackupRun, BackupStatus
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.operations import (
    AddTracks,
    MemberPlan,
    Removal,
    RemoveTracks,
    Reorder,
    SyncPlan,
)
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.runs import (
    MemberResult,
    MemberStatus,
    RunKind,
    RunStatus,
    SyncRun,
    Trigger,
)
from linersync.domain.tracks import ServiceId, TrackKey
from tests.factories import NOW

SPOTIFY = ServiceId("spotify")
TIDAL = ServiceId("tidal")


def entry(
    entry_id: str,
    service: ServiceId = SPOTIFY,
    *,
    tracks: int = 10,
    versions: int = 1,
    archived: bool = False,
) -> BackedUpPlaylist:
    return BackedUpPlaylist(
        entry_id=entry_id,
        service=service,
        playlist_id=entry_id,
        name=entry_id,
        latest_version_id=f"{entry_id}-v",
        track_count=tracks,
        versions=versions,
        first_backed_up_at=NOW,
        last_seen_at=NOW,
        archived_at=NOW if archived else None,
    )


def member_plan(member_id: str, *ops: AddTracks | RemoveTracks | Reorder) -> MemberPlan:
    return MemberPlan(
        member_id=member_id,
        operations=ops,
        not_made=(),
        expected_after=(),
        synced=(),
        draft=PlaylistDraft(name="Mix"),
    )


def run(
    run_id: str = "r1",
    *,
    kind: RunKind = RunKind.SYNC,
    status: RunStatus = RunStatus.SUCCEEDED,
    dry_run: bool = False,
    plans: tuple[MemberPlan, ...] = (),
    applied: Mapping[str, tuple[str, ...]] | None = None,
) -> SyncRun:
    return SyncRun(
        run_id=run_id,
        sync_id="s1",
        kind=kind,
        trigger=Trigger.MANUAL,
        dry_run=dry_run,
        status=status,
        started_at=NOW,
        plan=SyncPlan("s1", plans, ()) if plans else None,
        members=tuple(
            MemberResult(mid, MemberStatus.APPLIED, applied_op_ids=ids)
            for mid, ids in (applied or {}).items()
        ),
    )


def backup_run(backup_id: str) -> BackupRun:
    return BackupRun(
        backup_id=backup_id,
        service=SPOTIFY,
        started_at=NOW,
        finished_at=NOW + timedelta(minutes=1),
        status=BackupStatus.SUCCEEDED,
    )


ADD_3 = AddTracks("dst0:0", ("a", "b", "c"))
REMOVE_2 = RemoveTracks("dst0:1", (Removal("x", 0), Removal("y", 1)))


def test_library_groups_by_service_and_keeps_archived_apart() -> None:
    rows = library(
        [
            entry("p1", TIDAL, tracks=5, versions=2),
            entry("p2", SPOTIFY, tracks=10, versions=3),
            entry("p3", SPOTIFY, tracks=7),
            entry("p4", SPOTIFY, tracks=99, versions=4, archived=True),
        ]
    )
    assert rows == [
        ServiceLibrary(SPOTIFY, playlists=2, songs=17, archived=1, versions=8),
        ServiceLibrary(TIDAL, playlists=1, songs=5, archived=0, versions=2),
    ]
    assert library([]) == []


def test_changes_made_counts_applied_adds_and_removals() -> None:
    r = run(
        plans=(member_plan("dst0", ADD_3, REMOVE_2, Reorder("dst0:2", ("a",))),),
        applied={"dst0": ("r1:dst0:0", "r1:dst0:1", "r1:dst0:2")},
    )
    assert changes_made(r) == (3, 2)


def test_changes_made_skips_ops_that_were_not_applied() -> None:
    plans = (member_plan("dst0", ADD_3, REMOVE_2),)
    assert changes_made(run(plans=plans, applied={"dst0": ("r1:dst0:0",)})) == (3, 0)
    assert changes_made(run(plans=plans)) == (0, 0)


def test_changes_made_ignores_replanned_ops_dry_runs_and_runs_without_plan() -> None:
    plans = (member_plan("dst0", ADD_3),)
    replanned = run(plans=plans, applied={"dst0": ("r1:r1:dst0:0",)})
    dry = run(plans=plans, dry_run=True, applied={"dst0": ("r1:dst0:0",)})
    undo = run(kind=RunKind.UNDO, applied={"dst0": ("u1:0",)})
    assert [changes_made(r) for r in (replanned, dry, undo)] == [(0, 0)] * 3


def test_activity_counts_kinds_outcomes_and_changes() -> None:
    plans = (member_plan("dst0", ADD_3, REMOVE_2),)
    applied = {"dst0": ("r1:dst0:0", "r1:dst0:1")}
    runs = [
        run("r1", plans=plans, applied=applied),
        replace(run("r2", kind=RunKind.TRANSFER), status=RunStatus.PARTIAL),
        run("r3", kind=RunKind.UNDO, status=RunStatus.FAILED),
        run("r4", status=RunStatus.ABORTED),
        run("r5", status=RunStatus.AWAITING_APPROVAL),
        run("r6", status=RunStatus.PAUSED_SOURCE_MISSING),
        run("r7", status=RunStatus.SKIPPED),
        run("r8", status=RunStatus.RUNNING),
        run("r9", dry_run=True),
    ]
    assert activity(runs, [backup_run("b1"), backup_run("b2")]) == Activity(
        sync_runs=5,
        transfer_runs=1,
        undo_runs=1,
        succeeded=1,
        partial=1,
        failed=2,
        waiting=2,
        skipped=1,
        songs_added=3,
        songs_removed=2,
        backups=2,
    )
    assert activity([], []) == Activity()


def test_matching_counts_not_on_service() -> None:
    key = TrackKey(SPOTIFY, "t1")
    matches = [
        RememberedMatch(key, TIDAL, "T1"),
        RememberedMatch(TrackKey(SPOTIFY, "t2"), TIDAL, None),
        RememberedMatch(key, ServiceId("apple_music"), "A1"),
    ]
    assert matching(matches) == MatchingStats(remembered=3, not_on_service=1)
    assert matching([]) == MatchingStats()


AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def test_window_start_is_local_midnight_days_back() -> None:
    now = datetime(2026, 3, 10, 23, 30, tzinfo=UTC)  # 11 March 00:30 in Amsterdam
    assert window_start(now, 1, AMSTERDAM) == datetime(2026, 3, 10, 23, tzinfo=UTC)
    assert window_start(now, 7, AMSTERDAM) == datetime(2026, 3, 4, 23, tzinfo=UTC)
    assert window_start(now, 7, ZoneInfo("UTC")) == datetime(2026, 3, 4, tzinfo=UTC)


def test_daily_buckets_runs_by_local_day_and_outcome() -> None:
    first = date(2026, 3, 9)

    def at(day: int, hour: int, status: RunStatus, dry_run: bool = False) -> SyncRun:
        started = datetime(2026, 3, day, hour, tzinfo=UTC)
        return replace(run(status=status, dry_run=dry_run), started_at=started)

    runs = [
        at(9, 10, RunStatus.SUCCEEDED),
        at(9, 23, RunStatus.FAILED),  # 10 March in Amsterdam
        at(10, 8, RunStatus.PARTIAL),
        at(10, 9, RunStatus.AWAITING_APPROVAL),
        at(10, 10, RunStatus.ABORTED),
        at(10, 11, RunStatus.SKIPPED),
        at(10, 12, RunStatus.RUNNING),
        at(10, 13, RunStatus.SUCCEEDED, dry_run=True),
        at(8, 22, RunStatus.SUCCEEDED),  # 8 March 23:00 local, before the window
    ]
    assert daily(runs, first, 3, AMSTERDAM) == [
        Day(date(2026, 3, 9), succeeded=1),
        Day(date(2026, 3, 10), partial=2, failed=2),
        Day(date(2026, 3, 11)),
    ]
