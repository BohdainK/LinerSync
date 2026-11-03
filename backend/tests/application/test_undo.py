from dataclasses import replace

import pytest

from linersync.application.config import EngineConfig
from linersync.application.runner import SyncRunner
from linersync.application.transfer import TransferService
from linersync.application.undo import UndoNotAllowed, UndoService
from linersync.domain.operations import ChangeNotMade, Reorder
from linersync.domain.planning.brake import BrakeConfig
from linersync.domain.runs import MemberStatus, RunKind, RunStatus, Trigger
from linersync.domain.sync import MemberState, SyncMember
from tests.application.conftest import APPLE, SPOTIFY, World
from tests.factories import snapshot

MANUAL = Trigger.MANUAL


def _undo(w: World) -> UndoService:
    return UndoService(w.deps, w.runner())


def _brakeless(w: World) -> SyncRunner:
    """A runner whose brake never trips, so a sync may remove freely."""
    config = EngineConfig(brake=BrakeConfig(max_fraction=1.0, max_count=1000))
    return SyncRunner(replace(w.deps, config=config))


async def test_undo_restores_full_target(world: World) -> None:
    # The sync removes x and a repeat of a, adds b and c, and reorders; undo
    # must re-add x and the repeat, remove b and c, and restore the order.
    before = world.spotify_to_spotify(("x", "a", "a"), mirror=True).destinations
    run = await _brakeless(world).run("s2", trigger=MANUAL)
    assert world.spotify.playlists["P2"].track_ids == tuple("abc")

    undo = await _undo(world).undo(run.run_id)

    assert undo.kind is RunKind.UNDO
    assert undo.undoes_run_id == run.run_id
    assert undo.trigger is MANUAL and not undo.dry_run
    assert undo.status is RunStatus.SUCCEEDED
    (member,) = undo.members
    assert member.member_id == "dst0"
    assert member.status is MemberStatus.APPLIED
    assert member.mismatch is None
    assert world.spotify.playlists["P2"].track_ids == ("x", "a", "a")
    assert world.repos.runs.get(undo.run_id) == undo
    assert _dests(world) == before  # the sync's memory too
    assert world.events.events[-1].phase == "done"
    assert world.events.events[-1].run_id == undo.run_id


async def test_undo_on_add_only_target_lists_manual_fixes(world: World) -> None:
    run = await world.runner().run("s1", trigger=MANUAL)
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C")

    undo = await _undo(world).undo(run.run_id)

    assert undo.status is RunStatus.PARTIAL
    (member,) = undo.members
    assert [(c.kind, c.track_id) for c in member.not_made] == [
        ("remove", "A"),
        ("remove", "B"),
        ("remove", "C"),
    ]
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C")


async def test_undo_add_only_reports_surplus_repeat(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "a"]))
    world.apple.add_playlist(snapshot("apple_music", "A1", ["A"]))
    run = await world.runner().run("s1", trigger=MANUAL)
    assert world.apple.playlists["A1"].track_ids == ("A", "A")

    undo = await _undo(world).undo(run.run_id)

    assert undo.status is RunStatus.PARTIAL
    (member,) = undo.members
    assert [(c.kind, c.track_id) for c in member.not_made] == [("remove", "A")]
    assert world.apple.playlists["A1"].track_ids == ("A", "A")


async def test_undo_of_created_playlist_does_not_delete(unsaved_world: World) -> None:
    w = unsaved_world
    run = await TransferService(w.deps, w.runner()).transfer(SPOTIFY, "P1", APPLE)
    created = set(w.apple.playlists) - {"A1"}
    calls = len(w.apple.calls)

    undo = await _undo(w).undo(run.run_id)

    assert undo.status is RunStatus.PARTIAL
    (member,) = undo.members
    assert member.not_made == (
        ChangeNotMade(
            "delete", None, "Delete the created playlist by hand if you don't want it"
        ),
    )
    assert set(w.apple.playlists) - {"A1"} == created
    assert len(w.apple.calls) == calls  # nothing read, nothing written


async def test_undo_rejects_dry_run(world: World) -> None:
    run = await world.runner().run("s1", trigger=MANUAL, dry_run=True)
    with pytest.raises(UndoNotAllowed):
        await _undo(world).undo(run.run_id)


async def test_undo_saves_pre_write_for_future_undo(world: World) -> None:
    world.spotify_to_spotify(("b", "a"))
    run = await world.runner().run("s2", trigger=MANUAL)
    undo = await _undo(world).undo(run.run_id)

    pre = world.repos.snapshots.get_pre_write(undo.run_id, "dst0")
    assert pre is not None
    assert pre.track_ids == ("a", "b", "c")
    # Undoing an undo is not enabled yet (only sync and transfer runs).
    with pytest.raises(UndoNotAllowed):
        await _undo(world).undo(undo.run_id)


async def test_undo_skips_unreadable_target(world: World) -> None:
    world.spotify_to_spotify(("b", "a"))
    run = await world.runner().run("s2", trigger=MANUAL)
    world.spotify.truncate_reads("P2")

    undo = await _undo(world).undo(run.run_id)

    (member,) = undo.members
    assert member.status is MemberStatus.SKIPPED_UNREADABLE
    assert member.error
    assert undo.status is RunStatus.PARTIAL
    assert world.spotify.playlists["P2"].track_ids == ("a", "b", "c")
    assert not world.repos.snapshots.has_pre_write(undo.run_id, "dst0")


async def test_undo_unknown_run_raises(world: World) -> None:
    with pytest.raises(KeyError):
        await _undo(world).undo("nope")


_CHANGED = "playlist changed since this run — undo newer runs first"


def _p2(w: World) -> tuple[str, ...]:
    return w.spotify.playlists["P2"].track_ids


async def test_undo_rejects_run_that_wrote_nothing(world: World) -> None:
    world.spotify_to_spotify(("a", "b", "c"))
    run = await world.runner().run("s2", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    with pytest.raises(UndoNotAllowed):
        await _undo(world).undo(run.run_id)


async def test_undo_failed_run_after_partial_write(world: World) -> None:
    world.spotify_to_spotify(("b", "a"))
    world.spotify.fail_writes(Reorder, times=2)
    run = await world.runner().run("s2", trigger=MANUAL)
    assert run.status is RunStatus.FAILED
    assert _p2(world) == ("b", "a", "c")

    undo = await _undo(world).undo(run.run_id)

    assert undo.status is RunStatus.SUCCEEDED
    assert _p2(world) == ("b", "a")


async def _two_adding_runs(w: World) -> tuple[str, str, tuple[str, ...]]:
    w.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b"]))
    w.spotify_to_spotify(())
    run1 = await w.runner().run("s2", trigger=MANUAL)
    assert _p2(w) == ("a", "b")
    more = ("a", "b", *"cdefghij")
    w.spotify.add_playlist(snapshot("spotify", "P1", more))
    run2 = await w.runner().run("s2", trigger=MANUAL)
    assert _p2(w) == more
    return run1.run_id, run2.run_id, more


async def test_undo_older_run_blocked_by_later_changes(world: World) -> None:
    run1, _, more = await _two_adding_runs(world)
    writes = len([c for c in world.spotify.calls if c.startswith("apply:")])

    undo = await _undo(world).undo(run1)

    assert undo.status is RunStatus.FAILED
    (member,) = undo.members
    assert member.status is MemberStatus.FAILED
    assert member.error == _CHANGED
    assert _p2(world) == more
    assert len([c for c in world.spotify.calls if c.startswith("apply:")]) == writes


async def test_undo_newest_then_older_in_sequence(world: World) -> None:
    run1, run2, _ = await _two_adding_runs(world)

    u2 = await _undo(world).undo(run2)
    assert u2.status is RunStatus.SUCCEEDED
    assert _p2(world) == ("a", "b")

    u1 = await _undo(world).undo(run1)
    assert u1.status is RunStatus.SUCCEEDED
    assert _p2(world) == ()


async def test_undo_unverified_run_blocked_by_later_write(world: World) -> None:
    world.spotify_to_spotify(("b", "a"))
    world.spotify.fail_writes(Reorder, times=2)
    run1 = await world.runner().run("s2", trigger=MANUAL)
    assert run1.members[0].verified_after is None
    await world.runner().run("s2", trigger=MANUAL)
    assert _p2(world) == ("a", "b", "c")

    undo = await _undo(world).undo(run1.run_id)

    assert undo.status is RunStatus.FAILED
    (member,) = undo.members
    assert member.status is MemberStatus.FAILED and member.error == _CHANGED
    assert _p2(world) == ("a", "b", "c")


def _dests(w: World) -> tuple[SyncMember, ...]:
    saved = w.repos.syncs.get("s2")
    assert saved is not None
    return saved.destinations


def _state(w: World) -> MemberState:
    saved = w.repos.syncs.get("s2")
    assert saved is not None
    state = saved.destinations[0].last_synced
    assert state is not None
    return state


async def test_undo_then_sync_reapplies_source_and_keeps_user_songs(
    world: World,
) -> None:
    # Source a,b,c; destination a,b,c plus the user's own x.
    world.spotify_to_spotify(("a", "b", "c", "x"))
    await world.runner().run("s2", trigger=MANUAL)
    assert _p2(world) == ("a", "b", "c", "x")
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "c", "d"]))
    run = await _brakeless(world).run("s2", trigger=MANUAL)
    assert _p2(world) == ("a", "c", "x", "d")

    undo = await _undo(world).undo(run.run_id)
    assert undo.status is RunStatus.SUCCEEDED
    assert _p2(world) == ("a", "b", "c", "x")

    again = await _brakeless(world).run("s2", trigger=MANUAL)
    assert again.status is RunStatus.SUCCEEDED
    assert _p2(world) == ("a", "c", "x", "d")
    state = _state(world)
    assert state.removed_by_you == ()
    assert [t.track_id for t in state.added_by_you] == ["x"]


async def test_undo_restores_previous_sync_memory(world: World) -> None:
    before = world.spotify_to_spotify(("x", "a", "a"), mirror=True).destinations
    run = await _brakeless(world).run("s2", trigger=MANUAL)
    assert _dests(world) != before

    await _undo(world).undo(run.run_id)

    assert _dests(world) == before  # last_synced and mirror_once


async def test_undo_newest_then_older_restores_each_memory(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b"]))
    before = world.spotify_to_spotify(()).destinations
    run1 = await world.runner().run("s2", trigger=MANUAL)
    after1 = _dests(world)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c"]))
    run2 = await world.runner().run("s2", trigger=MANUAL)

    await _undo(world).undo(run2.run_id)
    assert _dests(world) == after1
    await _undo(world).undo(run1.run_id)
    assert _dests(world) == before


async def test_undo_restores_memory_past_later_runs_that_wrote_nothing(
    world: World,
) -> None:
    world.spotify_to_spotify(("a", "b", "c", "x"))
    await world.runner().run("s2", trigger=MANUAL)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "c", "d"]))
    run = await _brakeless(world).run("s2", trigger=MANUAL)
    prior = world.repos.snapshots.get_prior_state(run.run_id, "dst0")
    assert prior is not None
    idle = await _brakeless(world).run("s2", trigger=MANUAL)
    assert not world.repos.snapshots.has_pre_write(idle.run_id, "dst0")

    await _undo(world).undo(run.run_id)

    assert _state(world) == prior.last_synced


async def test_failed_undo_leaves_memory(world: World) -> None:
    run1, _, _ = await _two_adding_runs(world)
    before = world.repos.syncs.get("s2")

    undo = await _undo(world).undo(run1)

    assert undo.status is RunStatus.FAILED
    assert world.repos.syncs.get("s2") == before


async def test_undo_of_legacy_snapshot_leaves_memory(world: World) -> None:
    world.spotify_to_spotify(("b", "a"))
    run = await world.runner().run("s2", trigger=MANUAL)
    pre = world.repos.snapshots.get_pre_write(run.run_id, "dst0")
    world.repos.snapshots.save_pre_write(run.run_id, "dst0", pre)  # no prior
    before = world.repos.syncs.get("s2")

    undo = await _undo(world).undo(run.run_id)

    assert undo.status is RunStatus.SUCCEEDED
    assert world.repos.syncs.get("s2") == before


async def test_undo_keeps_memory_of_a_later_run_that_wrote(world: World) -> None:
    # Run 2 wrote after run 1 and was never undone. The user reverted run 2's
    # change by hand, so run 1 can still be undone, but the sync's memory must
    # not roll back past run 2.
    run1, _, _ = await _two_adding_runs(world)
    world.spotify.add_playlist(snapshot("spotify", "P2", ["a", "b"]))
    before = _dests(world)

    undo = await _undo(world).undo(run1)

    assert undo.status is RunStatus.SUCCEEDED
    assert _p2(world) == ()
    assert _dests(world) == before
