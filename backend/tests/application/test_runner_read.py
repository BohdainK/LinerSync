from dataclasses import replace

import pytest

from linersync.domain.attention import AttentionKind
from linersync.domain.operations import AddTracks, CreatePlaylist
from linersync.domain.runs import MemberStatus, RunKind, RunStatus, Trigger
from linersync.domain.sync import SyncMode, SyncStatus
from tests.application.conftest import APPLE, World
from tests.factories import group, snapshot, track

MANUAL = Trigger.MANUAL


def _writes(w: World) -> list[str]:
    return [
        c
        for c in w.spotify.calls + w.apple.calls
        if c.startswith(("apply:", "create:"))
    ]


async def test_source_read_error_aborts_without_writes(world: World) -> None:
    world.spotify.fail_reads("P1")
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.ABORTED
    assert run.error is not None and "P1" in run.error
    assert run.plan is None
    assert world.apple.calls == []
    assert _writes(world) == []
    assert world.repos.attention.list_open() == []
    assert world.repos.syncs.get("s1") == world.group
    assert world.repos.runs.get(run.run_id) == run


async def test_truncated_source_aborts(world: World) -> None:
    world.spotify.truncate_reads("P1")
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.ABORTED
    assert run.error
    assert world.apple.calls == []
    assert _writes(world) == []
    assert world.repos.syncs.get("s1") == world.group


async def test_truncated_source_says_why(world: World) -> None:
    world.spotify.truncate_reads("P1", reason="1 song is unavailable; remove it")
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.ABORTED
    assert run.error and "1 song is unavailable; remove it" in run.error


async def test_source_missing_pauses_sync(world: World) -> None:
    world.spotify.delete_playlist("P1")
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.PAUSED_SOURCE_MISSING
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    assert saved.status is SyncStatus.PAUSED
    assert saved.pause_reason
    items = world.repos.attention.list_open()
    assert [i.kind for i in items] == [AttentionKind.SOURCE_MISSING]
    assert items[0].sync_id == "s1"
    assert world.apple.calls == []
    assert _writes(world) == []


async def test_source_missing_in_dry_run_writes_nothing(world: World) -> None:
    world.spotify.delete_playlist("P1")
    run = await world.runner().run("s1", trigger=MANUAL, dry_run=True)
    assert run.status is RunStatus.PAUSED_SOURCE_MISSING
    assert world.repos.syncs.get("s1") == world.group
    assert world.repos.attention.list_open() == []


async def test_source_missing_unpersisted_leaves_no_item(unsaved_world: World) -> None:
    w = unsaved_world
    w.spotify.delete_playlist("P1")
    run, g = await w.runner().run_group(
        w.group, kind=RunKind.TRANSFER, trigger=MANUAL, dry_run=False, persist=False
    )
    assert run.status is RunStatus.PAUSED_SOURCE_MISSING
    assert g.status is SyncStatus.PAUSED
    assert w.repos.attention.list_open() == []
    assert w.repos.syncs.list() == []


async def test_paused_sync_skipped(world: World) -> None:
    paused = replace(world.group, status=SyncStatus.PAUSED, pause_reason="x")
    world.repos.syncs.save(paused)
    run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.SKIPPED
    assert world.spotify.calls == []
    assert world.apple.calls == []
    assert world.repos.runs.list_for_sync("s1") == [run]


async def test_destination_read_error_excludes_only_that_destination(
    world: World,
) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P2", ["a"]))
    two = group(
        destinations=[
            ("apple_music", "A1", SyncMode.FULL),
            ("spotify", "P2", SyncMode.FULL),
        ]
    )
    world.repos.syncs.save(two)
    world.spotify.fail_reads("P2")
    run = await world.runner().run("s1", trigger=MANUAL, dry_run=True)
    assert run.status is RunStatus.PARTIAL
    by_id = {m.member_id: m for m in run.members}
    assert by_id["dst0"].status is MemberStatus.DRY_RUN
    assert by_id["dst1"].status is MemberStatus.SKIPPED_UNREADABLE
    assert by_id["dst1"].error
    assert run.plan is not None
    assert [p.member_id for p in run.plan.member_plans] == ["dst0"]
    assert run.plan.for_member("dst0").expected_after == ("A", "B", "C")
    assert _writes(world) == []


async def test_truncated_destination_is_skipped(world: World) -> None:
    world.apple.truncate_reads("A1")
    run = await world.runner().run("s1", trigger=MANUAL, dry_run=True)
    assert run.status is RunStatus.PARTIAL
    assert [m.status for m in run.members] == [MemberStatus.SKIPPED_UNREADABLE]
    assert run.plan is None
    assert world.apple.calls == ["read:A1"]


async def test_matching_failure_skips_destination(world: World) -> None:
    world.apple.fail_search()
    run = await world.runner().run("s1", trigger=MANUAL, dry_run=True)
    assert run.status is RunStatus.PARTIAL
    (member,) = run.members
    assert member.status is MemberStatus.SKIPPED_UNREADABLE
    assert member.error
    assert run.plan is None


async def test_missing_destination_is_rebuilt(world: World) -> None:
    world.apple.delete_playlist("A1")
    run = await world.runner().run("s1", trigger=MANUAL, dry_run=True)
    assert run.status is RunStatus.SUCCEEDED
    (member,) = run.members
    assert member.status is MemberStatus.DRY_RUN
    assert member.rebuilt
    assert run.plan is not None
    ops = run.plan.for_member("dst0").operations
    assert [type(op) for op in ops] == [CreatePlaylist, AddTracks]
    assert _writes(world) == []


async def test_dry_run_writes_nothing(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c", "zzz"]))
    run = await world.runner().run("s1", trigger=MANUAL, dry_run=True)
    assert run.status is RunStatus.SUCCEEDED
    assert run.dry_run
    assert run.kind is RunKind.SYNC
    assert run.plan is not None
    assert len(run.plan.unresolved) == 1
    assert [m.status for m in run.members] == [MemberStatus.DRY_RUN]
    assert _writes(world) == []
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    assert all(m.last_synced is None for m in saved.members)
    assert world.repos.attention.list_open() == []
    phases = [e.phase for e in world.events.events]
    assert {"read", "match", "plan"} <= set(phases)


async def test_dry_run_nothing_to_do(world: World) -> None:
    world.spotify_to_spotify(("a", "b", "c"))
    run = await world.runner().run("s2", trigger=MANUAL, dry_run=True)
    assert run.status is RunStatus.SUCCEEDED
    assert [m.status for m in run.members] == [MemberStatus.NOTHING_TO_DO]


async def test_run_unknown_sync_raises(world: World) -> None:
    with pytest.raises(KeyError):
        await world.runner().run("nope", trigger=MANUAL)


async def test_preview_unsaved_group(unsaved_world: World) -> None:
    w = unsaved_world
    run = await w.runner().preview(w.group)
    assert run.status is RunStatus.SUCCEEDED
    assert run.dry_run
    assert run.trigger is Trigger.MANUAL
    assert [m.status for m in run.members] == [MemberStatus.DRY_RUN]
    assert w.repos.syncs.list() == []
    assert _writes(w) == []


async def test_seeded_run_matches_new_source_songs_normally(world: World) -> None:
    keys = [e.track.key for e in snapshot("spotify", "P1", ["a", "b", "c"]).entries]
    world.apple.catalog = (
        *world.apple.catalog,
        track("apple_music", "A2", title="a"),
    )
    run, _ = await world.runner().run_group(
        world.group,
        kind=RunKind.SYNC,
        trigger=MANUAL,
        dry_run=True,
        persist=False,
        seed={APPLE: {keys[0]: "A2"}},
    )
    assert run.plan is not None
    synced = {e.source_key: e.target_track_id for e in run.plan.member_plans[0].synced}
    assert synced[keys[0]] == "A2"
    assert synced[keys[1]] == "B" and synced[keys[2]] == "C"
