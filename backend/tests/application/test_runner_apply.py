from typing import Any

from linersync.application.attention import AttentionService
from linersync.domain.attention import AttentionKind
from linersync.domain.matching.types import MatchDecision, NeedsAttention
from linersync.domain.operations import AddTracks, Operation, RemoveTracks
from linersync.domain.playlists import PlaylistDraft, PlaylistMeta, PlaylistSnapshot
from linersync.domain.runs import MemberStatus, RunStatus, Trigger
from linersync.domain.sync import SyncedEntry, SyncMode
from linersync.domain.tracks import TrackKey
from linersync.ports.errors import WriteFailed
from tests.application.conftest import SPOTIFY, World
from tests.factories import group, snapshot, track

MANUAL = Trigger.MANUAL


def _open(w: World, kind: AttentionKind) -> list[str | None]:
    return [i.member_id for i in w.repos.attention.list_open() if i.kind is kind]


async def test_happy_path_spotify_to_apple(world: World) -> None:
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    assert not run.dry_run
    (member,) = run.members
    assert member.status is MemberStatus.APPLIED
    assert member.mismatch is None
    assert member.applied_op_ids == (f"{run.run_id}:dst0:0",)
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C")
    pre = world.repos.snapshots.get_pre_write(run.run_id, "dst0")
    assert pre is not None and pre.track_ids == ()
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    dst = saved.members[1].last_synced
    assert dst is not None
    assert [e.target_track_id for e in dst.entries] == ["A", "B", "C"]
    assert dst.version_token == world.apple.playlists["A1"].meta.version_token
    src = saved.source.last_synced
    assert src is not None
    assert [e.target_track_id for e in src.entries] == ["a", "b", "c"]
    assert world.repos.runs.get(run.run_id) == run


async def test_removals_never_follow_failed_add(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", list("abcdef")))
    world.spotify_to_spotify(tuple("abcdex"))
    world.spotify.fail_writes(AddTracks, times=2)
    run = await world.runner().run("s2", trigger=MANUAL)
    (member,) = run.members
    assert member.status is MemberStatus.FAILED
    assert member.error
    assert run.status is RunStatus.FAILED
    assert not any(c.startswith("apply:RemoveTracks") for c in world.spotify.calls)
    assert not any(c.startswith("apply:Reorder") for c in world.spotify.calls)
    assert world.spotify.playlists["P2"].track_ids == tuple("abcdex")
    saved = world.repos.syncs.get("s2")
    assert saved is not None and saved.members[1].last_synced is None
    assert saved.source.last_synced is None


async def test_timeout_that_applied_does_not_duplicate(world: World) -> None:
    world.apple.fail_writes(AddTracks, applied=True)
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C")
    assert world.apple.calls.count("apply:AddTracks:A1") == 1
    # initial read, re-read for the re-plan, verify read
    assert world.apple.calls.count("read:A1") == 3


async def test_verify_mismatch_flagged(world: World) -> None:
    world.apple.drop_adds({"B"})
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.PARTIAL
    (member,) = run.members
    assert member.status is MemberStatus.APPLIED
    assert member.mismatch is not None
    assert member.mismatch.expected == ("A", "B", "C")
    assert member.mismatch.actual == ("A", "C")
    assert _open(world, AttentionKind.VERIFY_MISMATCH) == ["dst0"]
    saved = world.repos.syncs.get("s1")
    assert saved is not None and saved.members[1].last_synced is None


async def test_service_filing_an_added_song_under_another_id_is_verified(
    world: World,
) -> None:
    # Seen live: Apple took catalog id B but lists it as B2 (the same song).
    world.apple.substitute_adds({"B": track("apple_music", "B2", title="b")})
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    (member,) = run.members
    assert member.mismatch is None
    assert member.verified_after == ("A", "B2", "C")
    assert _open(world, AttentionKind.VERIFY_MISMATCH) == []
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    dst = saved.members[1].last_synced
    assert dst is not None
    assert [e.target_track_id for e in dst.entries] == ["A", "B2", "C"]

    # The next run sees b as already there: nothing is added again.
    world.apple.calls.clear()
    again = await world.runner().run("s1", trigger=MANUAL)
    assert again.status is RunStatus.SUCCEEDED
    assert "apply:AddTracks:A1" not in world.apple.calls
    assert world.apple.playlists["A1"].track_ids == ("A", "B2", "C")


async def test_added_song_replaced_by_another_song_is_a_mismatch(
    world: World,
) -> None:
    world.apple.substitute_adds({"B": track("apple_music", "Z", title="zebra")})
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.PARTIAL
    (member,) = run.members
    assert member.mismatch is not None
    assert member.mismatch.actual == ("A", "Z", "C")
    assert _open(world, AttentionKind.VERIFY_MISMATCH) == ["dst0"]
    (item,) = world.repos.attention.list_open()
    assert item.detail == (
        "After syncing, Apple Music has other songs than the plan expected"
    )


async def test_verify_read_failure_is_not_empty(world: World) -> None:
    runner = world.runner()
    original = world.apple.read_playlist
    reads = 0

    async def flaky(playlist_id: str) -> PlaylistSnapshot:
        nonlocal reads
        reads += 1
        if reads == 2:
            world.apple.fail_reads(playlist_id)
        return await original(playlist_id)

    world.apple.read_playlist = flaky  # type: ignore[method-assign]
    run = await runner.run("s1", trigger=MANUAL)
    (member,) = run.members
    assert run.status is RunStatus.PARTIAL
    assert member.status is MemberStatus.APPLIED
    assert member.mismatch is not None
    assert member.mismatch.actual == ()
    assert member.error
    saved = world.repos.syncs.get("s1")
    assert saved is not None and saved.members[1].last_synced is None


async def test_add_only_target_records_changes_not_made(world: World) -> None:
    await world.runner().run("s1", trigger=MANUAL)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b"]))
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    (member,) = run.members
    assert member.status is MemberStatus.NOTHING_TO_DO
    assert [(c.kind, c.track_id) for c in member.not_made] == [("remove", "C")]
    items = [
        i
        for i in world.repos.attention.list_open()
        if i.kind is AttentionKind.CHANGES_NOT_MADE
    ]
    assert len(items) == 1
    assert items[0].member_id == "dst0"
    assert items[0].not_made == member.not_made
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C")


async def test_unmatched_item_not_duplicated_across_runs(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c", "zzz"]))
    await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    items = [
        i
        for i in world.repos.attention.list_open()
        if i.kind is AttentionKind.UNMATCHED
    ]
    assert len(items) == 1
    (item,) = items
    assert item.source is not None and item.source.track_id == "zzz"
    assert item.target_service == "apple_music"
    assert item.sync_id == "s1" and item.member_id == "dst0"
    assert item.detail


async def test_emptied_source_trips_brake_then_approval_applies(world: World) -> None:
    world.spotify_to_spotify(("a", "b", "c"), mirror=True)
    world.spotify.add_playlist(snapshot("spotify", "P1", []))
    run = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert [m.status for m in run.members] == [MemberStatus.AWAITING_APPROVAL]
    assert not any(c.startswith("apply:") for c in world.spotify.calls)
    assert world.spotify.playlists["P2"].track_ids == ("a", "b", "c")
    (item,) = [
        i for i in world.repos.attention.list_open() if i.kind is AttentionKind.BRAKE
    ]
    assert item.sync_id == "s2" and item.member_id == "dst0"
    assert item.fingerprint is not None
    assert item.detail == "This run would remove 3 of 3 songs"

    # A manual run is held too: approval is the only bypass.
    again = await world.runner().run("s2", trigger=MANUAL)
    assert again.status is RunStatus.AWAITING_APPROVAL

    world.repos.approvals.approve("s2", "dst0", item.fingerprint)
    approved = await world.runner().run("s2", trigger=MANUAL)
    assert approved.status is RunStatus.SUCCEEDED
    assert len(world.spotify.playlists["P2"].entries) == 0
    assert any(c.startswith("apply:RemoveTracks") for c in world.spotify.calls)


async def test_rebuild_missing_target(world: World) -> None:
    world.apple.delete_playlist("A1")
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    (member,) = run.members
    assert member.rebuilt
    assert member.created_playlist_id == "apple_music-new-1"
    assert len(member.applied_op_ids) == 2
    assert "create:Mix" in world.apple.calls
    assert "apply:AddTracks:apple_music-new-1" in world.apple.calls
    assert world.apple.playlists["apple_music-new-1"].track_ids == ("A", "B", "C")
    assert world.repos.snapshots.has_pre_write(run.run_id, "dst0")
    assert world.repos.snapshots.get_pre_write(run.run_id, "dst0") is None
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    assert saved.members[1].playlist_id == "apple_music-new-1"
    assert saved.members[1].last_synced is not None


async def test_failed_create_is_not_replanned(world: World) -> None:
    world.apple.delete_playlist("A1")

    async def broken(draft: PlaylistDraft) -> PlaylistMeta:
        world.apple.calls.append("create:broken")
        raise WriteFailed(world.apple.service, "timeout", maybe_applied=True)

    world.apple.create_playlist = broken  # type: ignore[method-assign]
    run = await world.runner().run("s1", trigger=MANUAL)
    (member,) = run.members
    assert member.status is MemberStatus.FAILED
    assert run.status is RunStatus.FAILED
    assert world.apple.calls.count("create:broken") == 1
    assert not any(c.startswith("apply:") for c in world.apple.calls)
    saved = world.repos.syncs.get("s1")
    assert saved is not None and saved.members[1] == world.group.members[1]


async def test_one_destination_failure_other_continues(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P2", []))
    two = group(
        destinations=[
            ("apple_music", "A1", SyncMode.FULL),
            ("spotify", "P2", SyncMode.FULL),
        ]
    )
    world.repos.syncs.save(two)
    world.spotify.fail_writes(AddTracks, times=2)
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.PARTIAL
    by_id = {m.member_id: m for m in run.members}
    assert by_id["dst0"].status is MemberStatus.APPLIED
    assert by_id["dst1"].status is MemberStatus.FAILED
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C")
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    assert saved.members[1].last_synced is not None
    assert saved.members[2].last_synced is None


async def test_unreadable_destination_makes_run_partial(world: World) -> None:
    world.apple.fail_reads("A1")
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.PARTIAL
    assert [m.status for m in run.members] == [MemberStatus.SKIPPED_UNREADABLE]
    assert not any(c.startswith(("apply:", "create:")) for c in world.apple.calls)


async def test_nothing_to_do_updates_last_synced(world: World) -> None:
    world.spotify_to_spotify(("a", "b", "c"))
    run = await world.runner().run("s2", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    assert [m.status for m in run.members] == [MemberStatus.NOTHING_TO_DO]
    assert not world.repos.snapshots.has_pre_write(run.run_id, "dst0")
    saved = world.repos.syncs.get("s2")
    assert saved is not None
    state = saved.members[1].last_synced
    assert state is not None
    assert [e.target_track_id for e in state.entries] == ["a", "b", "c"]


async def test_replan_respects_brake(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", list("abcdef")))
    world.spotify_to_spotify(tuple("abcdex"), mirror=True)
    world.spotify.fail_writes(AddTracks)
    # the failed add is followed by the user emptying the target's other songs
    original = world.spotify.read_playlist

    async def reread(playlist_id: str) -> PlaylistSnapshot:
        if playlist_id == "P2" and world.spotify.calls.count("read:P2") == 1:
            world.spotify.add_playlist(snapshot("spotify", "P2", ["x", "y", "z"]))
        return await original(playlist_id)

    world.spotify.read_playlist = reread  # type: ignore[method-assign]
    run = await world.runner().run("s2", trigger=MANUAL)
    assert [m.status for m in run.members] == [MemberStatus.AWAITING_APPROVAL]
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert not any(c.startswith("apply:RemoveTracks") for c in world.spotify.calls)
    assert _open(world, AttentionKind.BRAKE) == ["dst0"]


async def test_progress_events_published(world: World) -> None:
    await world.runner().run("s1", trigger=MANUAL)
    phases = [e.phase for e in world.events.events]
    assert "apply" in phases
    assert "verify" in phases
    assert phases[-1] == "done"
    assert phases.index("apply") < phases.index("verify")


async def test_removals_applied_last(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", list("abcdef")))
    world.spotify_to_spotify(tuple("abcdex"), mirror=True)
    run = await world.runner().run("s2", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    writes = [c for c in world.spotify.calls if c.startswith("apply:")]
    assert writes[-1] == f"apply:{RemoveTracks.__name__}:P2"
    assert world.spotify.playlists["P2"].track_ids == tuple("abcdef")


async def test_rebuilt_add_only_target_replan_restores_songs(world: World) -> None:
    first = await world.runner().run("s1", trigger=MANUAL)
    assert first.status is RunStatus.SUCCEEDED
    world.apple.delete_playlist("A1")
    world.apple.fail_writes(AddTracks)
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    (member,) = run.members
    assert member.created_playlist_id == "apple_music-new-1"
    assert world.apple.playlists["apple_music-new-1"].track_ids == ("A", "B", "C")
    ids = member.applied_op_ids
    assert len(set(ids)) == len(ids)
    assert ids == (f"{run.run_id}:dst0:0", f"{run.run_id}:r1:dst0:0")
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    state = saved.members[1].last_synced
    assert state is not None
    assert [e.target_track_id for e in state.entries] == ["A", "B", "C"]


async def test_rebuilt_target_that_failed_is_filled_next_run(world: World) -> None:
    await world.runner().run("s1", trigger=MANUAL)
    world.apple.delete_playlist("A1")
    world.apple.fail_writes(AddTracks, times=2)
    failed = await world.runner().run("s1", trigger=MANUAL)
    assert failed.status is RunStatus.FAILED
    (member,) = failed.members
    assert member.created_playlist_id == "apple_music-new-1"
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    assert saved.members[1].playlist_id == "apple_music-new-1"
    assert saved.members[1].last_synced is None

    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    assert world.apple.playlists["apple_music-new-1"].track_ids == ("A", "B", "C")
    assert world.apple.calls.count("create:Mix") == 1


async def test_full_replan_then_applies_removals(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", list("abcdef")))
    world.spotify_to_spotify(tuple("abcdex"), mirror=True)
    world.spotify.fail_writes(AddTracks)
    run = await world.runner().run("s2", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    (member,) = run.members
    assert member.status is MemberStatus.APPLIED
    assert world.spotify.playlists["P2"].track_ids == tuple("abcdef")
    writes = [c for c in world.spotify.calls if c.startswith("apply:")]
    assert writes == [
        "apply:AddTracks:P2",
        "apply:AddTracks:P2",
        "apply:Reorder:P2",
        "apply:RemoveTracks:P2",
    ]
    ids = member.applied_op_ids
    assert len(ids) == 3 and len(set(ids)) == 3
    assert all(i.startswith(f"{run.run_id}:r1:") for i in ids)


def _p2(w: World) -> tuple[str, ...]:
    return w.spotify.playlists["P2"].track_ids


async def test_approval_covers_one_pass_only(world: World) -> None:
    world.spotify_to_spotify(("a", "b", "c"), mirror=True)
    world.spotify.add_playlist(snapshot("spotify", "P1", []))
    held = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert held.status is RunStatus.AWAITING_APPROVAL
    (item,) = [
        i for i in world.repos.attention.list_open() if i.kind is AttentionKind.BRAKE
    ]
    AttentionService(world.repos, world.clock).approve(item.item_id)
    approved = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert approved.status is RunStatus.SUCCEEDED
    assert _p2(world) == ()

    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c"]))
    refill = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert refill.status is RunStatus.SUCCEEDED
    assert _p2(world) == ("a", "b", "c")

    # The same removal plan again: the earlier approval no longer covers it.
    world.spotify.add_playlist(snapshot("spotify", "P1", []))
    again = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert again.status is RunStatus.AWAITING_APPROVAL
    assert _p2(world) == ("a", "b", "c")
    assert _open(world, AttentionKind.BRAKE) == ["dst0"]


async def test_brake_item_superseded_when_plan_no_longer_trips(world: World) -> None:
    world.spotify_to_spotify(("a", "b", "c"), mirror=True)
    world.spotify.add_playlist(snapshot("spotify", "P1", []))
    await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    (item,) = [
        i for i in world.repos.attention.list_open() if i.kind is AttentionKind.BRAKE
    ]
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c"]))
    run = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.SUCCEEDED
    assert _open(world, AttentionKind.BRAKE) == []
    resolved = world.repos.attention.get(item.item_id)
    assert resolved is not None and resolved.resolution == "superseded"


async def test_changes_not_made_item_accumulates(world: World) -> None:
    await world.runner().run("s1", trigger=MANUAL)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["b", "c"]))
    await world.runner().run("s1", trigger=MANUAL)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["c"]))
    run = await world.runner().run("s1", trigger=MANUAL)
    (member,) = run.members
    assert [(c.kind, c.track_id) for c in member.not_made] == [("remove", "B")]
    await world.runner().run("s1", trigger=MANUAL)  # nothing new: no duplicates
    (item,) = [
        i
        for i in world.repos.attention.list_open()
        if i.kind is AttentionKind.CHANGES_NOT_MADE
    ]
    assert [(c.kind, c.track_id) for c in item.not_made] == [
        ("remove", "A"),
        ("remove", "B"),
    ]
    assert item.detail.startswith("2 change(s)")


async def test_member_result_records_verified_after(world: World) -> None:
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.members[0].verified_after == ("A", "B", "C")


async def test_verified_after_is_actual_list_on_mismatch(world: World) -> None:
    world.apple.drop_adds({"B"})
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.members[0].verified_after == ("A", "C")


async def test_invalid_artwork_hash_does_not_crash_run(world: World) -> None:
    src = {"a": track("spotify", "a", artwork_hash="not-hex")}
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a"], tracks=src))
    world.apple.catalog = (track("apple_music", "A", title="a", artwork_hash="0f"),)
    run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.SUCCEEDED
    assert world.apple.playlists["A1"].track_ids == ("A",)


async def test_unexpected_exception_saves_aborted_run(world: World) -> None:
    seen: list[RunStatus] = []

    async def boom(playlist_id: str, op: Operation) -> None:
        (running,) = world.repos.runs.list_for_sync("s1")
        seen.append(running.status)
        raise RuntimeError("boom")

    world.apple.apply = boom  # type: ignore[method-assign]
    run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    assert seen == [RunStatus.RUNNING]  # saved before the first write
    assert run.status is RunStatus.ABORTED
    assert run.error == "RuntimeError: boom"
    assert world.repos.runs.get(run.run_id) == run
    assert world.events.events[-1].phase == "done"
    assert world.events.events[-1].run_id == run.run_id
    assert world.repos.snapshots.has_pre_write(run.run_id, "dst0")
    assert [(m.member_id, m.status) for m in run.members] == [
        ("dst0", MemberStatus.FAILED)
    ]
    saved = world.repos.syncs.get("s1")
    assert saved == world.group


async def test_unresolved_pair_survives_applied_run(world: World) -> None:
    await world.runner().run("s1", trigger=MANUAL)
    world.apple.catalog += (track("apple_music", "D", title="d"),)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c", "d"]))
    runner = world.runner()
    resolve = runner._matcher.resolve
    b = TrackKey(SPOTIFY, "b")

    async def b_needs_attention(
        *args: Any, **kwargs: Any
    ) -> dict[TrackKey, MatchDecision]:
        out = await resolve(*args, **kwargs)
        out[b] = NeedsAttention(("low confidence",), ())
        return out

    runner._matcher.resolve = b_needs_attention  # type: ignore[method-assign]
    run = await runner.run("s1", trigger=MANUAL)
    (member,) = run.members
    assert member.status is MemberStatus.APPLIED
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C", "D")
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    state = saved.members[1].last_synced
    assert state is not None
    assert SyncedEntry(b, "B") in state.entries
    assert [e.target_track_id for e in state.entries if e.source_key != b] == [
        "A",
        "C",
        "D",
    ]


async def test_brake_trips_for_owned_removals_without_mirror(world: World) -> None:
    world.spotify_to_spotify(())
    first = await world.runner().run("s2", trigger=MANUAL)
    assert first.status is RunStatus.SUCCEEDED
    assert _p2(world) == ("a", "b", "c")
    world.spotify.add_playlist(snapshot("spotify", "P1", []))
    run = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert [m.status for m in run.members] == [MemberStatus.AWAITING_APPROVAL]
    assert _p2(world) == ("a", "b", "c")
    (item,) = [
        i for i in world.repos.attention.list_open() if i.kind is AttentionKind.BRAKE
    ]
    assert item.detail == "This run would remove 3 of 3 songs"


async def test_applied_run_records_user_lists_and_order(world: World) -> None:
    world.spotify_to_spotify(())
    await world.runner().run("s2", trigger=MANUAL)
    world.spotify.add_playlist(snapshot("spotify", "P2", ["a", "c", "X"]))
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c", "d"]))
    run = await world.runner().run("s2", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    saved = world.repos.syncs.get("s2")
    assert saved is not None
    state = saved.destinations[0].last_synced
    assert state is not None
    assert [t.track_id for t in state.removed_by_you] == ["b"]
    assert [t.track_id for t in state.added_by_you] == ["X"]
    assert state.target_order == world.spotify.playlists["P2"].track_ids
    assert SyncedEntry(TrackKey(SPOTIFY, "b"), "b") in state.entries


async def test_mirror_once_cleared_after_settled_run(world: World) -> None:
    many = tuple("abcdefghij")
    g = world.spotify_to_spotify((*many, "x"), mirror=True)
    world.spotify.add_playlist(snapshot("spotify", "P1", many))
    assert g.destinations[0].mirror_once
    run = await world.runner().run("s2", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    assert world.spotify.playlists["P2"].track_ids == many
    saved = world.repos.syncs.get("s2")
    assert saved is not None
    assert not saved.destinations[0].mirror_once


async def test_dry_run_leaves_state_untouched(world: World) -> None:
    g = world.spotify_to_spotify(("a", "b", "c", "x"), mirror=True)
    run = await world.runner().run("s2", trigger=MANUAL, dry_run=True)
    assert run.dry_run
    assert world.repos.syncs.get("s2") == g
    assert world.spotify.playlists["P2"].track_ids == ("a", "b", "c", "x")
