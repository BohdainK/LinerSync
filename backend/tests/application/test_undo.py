from dataclasses import replace

import pytest

from linersync.application.config import EngineConfig
from linersync.application.runner import SyncRunner
from linersync.application.transfer import TransferService
from linersync.application.undo import UndoNotAllowed, UndoService
from linersync.domain.operations import ChangeNotMade, Reorder
from linersync.domain.planning.brake import BrakeConfig
from linersync.domain.runs import MemberStatus, RunKind, RunStatus, Trigger
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
    world.spotify_to_spotify(("x", "a", "a"))
    run = await _brakeless(world).run("s2", trigger=MANUAL)
    assert world.spotify.playlists["P2"].track_ids == tuple("abc")
    before = world.repos.syncs.get("s2")

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
    assert world.repos.syncs.get("s2") == before  # last_synced unchanged
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
