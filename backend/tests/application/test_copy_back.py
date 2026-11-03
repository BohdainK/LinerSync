from dataclasses import replace

from linersync.application.copy_back import CopyBackService
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.operations import AddTracks
from linersync.domain.runs import MemberStatus, RunKind, RunStatus, SyncRun, Trigger
from linersync.domain.sync import MemberRole, SyncedEntry, SyncGroup, SyncMember
from linersync.domain.tracks import TrackKey
from tests.application.conftest import APPLE, SPOTIFY, World
from tests.factories import snapshot, track

MANUAL = Trigger.MANUAL
X = track("apple_music", "X", title="x")


async def _sync(w: World) -> SyncGroup:
    run = await w.runner().run(w.group.sync_id, trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED, run
    saved = w.repos.syncs.get(w.group.sync_id)
    assert saved is not None
    return saved


async def _user_added_x(w: World) -> SyncGroup:
    """Sync P1 → A1, then the user adds X ("x") on A1; the next sync lists it
    as theirs. Spotify's catalog holds "x", and Apple's X, so a run that
    didn't pair them would match x to X again."""
    w.spotify.catalog = (*w.spotify.catalog, track("spotify", "x", title="x"))
    w.apple.catalog = (*w.apple.catalog, X)
    await _sync(w)
    w.apple.add_playlist(
        snapshot("apple_music", "A1", ["A", "B", "C", "X"], tracks={"X": X})
    )
    saved = await _sync(w)
    state = saved.destinations[0].last_synced
    assert state is not None and [t.track_id for t in state.added_by_you] == ["X"]
    return saved


def _copier(w: World) -> CopyBackService:
    return CopyBackService(w.deps, w.runner())


async def test_copy_back_dry_run_plans_adds_and_writes_nothing(world: World) -> None:
    saved = await _user_added_x(world)
    world.spotify.calls.clear()
    run = await _copier(world).copy(saved, "dst0", (), dry_run=True, seed=None)
    assert run.kind is RunKind.COPY_TO_SOURCE
    assert run.dry_run and run.status is RunStatus.SUCCEEDED
    assert run.plan is not None
    plan = run.plan.for_member("src")
    assert [op.track_ids for op in plan.operations if isinstance(op, AddTracks)] == [
        ("x",)
    ]
    assert plan.synced == (SyncedEntry(X.key, "x"),)
    assert plan.expected_after == ("a", "b", "c", "x")
    (member,) = run.members
    assert (member.member_id, member.status) == ("src", MemberStatus.DRY_RUN)
    assert not any(c.startswith("apply:") for c in world.spotify.calls)
    assert world.spotify.playlists["P1"].track_ids == ("a", "b", "c")
    assert world.repos.syncs.get("s1") == saved
    assert world.repos.runs.get(run.run_id) == run


async def _two_destinations(w: World) -> SyncGroup:
    """World's sync with a second destination, Spotify P2, then X added by the
    user on A1 (dst0)."""
    w.spotify.add_playlist(snapshot("spotify", "P2", []))
    second = SyncMember("dst1", SPOTIFY, "P2", MemberRole.DESTINATION)
    w.group = replace(w.group, members=(*w.group.members, second))
    w.repos.syncs.save(w.group)
    return await _user_added_x(w)


async def test_copy_back_adds_to_source_pairs_and_syncs_others(world: World) -> None:
    saved = await _two_destinations(world)
    runs: list[SyncRun] = []
    run = await _copier(world).copy(
        saved, "dst0", ("X",), dry_run=False, seed=None, on_run=runs.append
    )
    assert run.status is RunStatus.SUCCEEDED, run
    assert [r.run_id for r in runs][0] == run.run_id
    assert [r.kind for r in runs] == [RunKind.COPY_TO_SOURCE, RunKind.SYNC]
    assert runs[1].status is RunStatus.SUCCEEDED, runs[1]
    assert world.spotify.playlists["P1"].track_ids == ("a", "b", "c", "x")
    assert world.apple.playlists["A1"].track_ids.count("X") == 1
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C", "X")
    assert world.spotify.playlists["P2"].track_ids == ("a", "b", "c", "x")
    after = world.repos.syncs.get("s1")
    assert after is not None
    state = after.destinations[0].last_synced
    assert state is not None
    assert SyncedEntry(TrackKey(SPOTIFY, "x"), "X") in state.entries
    assert state.added_by_you == ()
    remembered = world.repos.matches.get(TrackKey(SPOTIFY, "x"), APPLE)
    assert remembered == RememberedMatch(TrackKey(SPOTIFY, "x"), APPLE, "X")


async def test_copy_back_pairs_song_already_in_source(world: World) -> None:
    saved = await _user_added_x(world)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c", "x"]))
    world.spotify.calls.clear()
    run = await _copier(world).copy(saved, "dst0", (), dry_run=False, seed=None)
    assert run.status is RunStatus.SUCCEEDED, run
    assert run.plan is not None
    plan = run.plan.for_member("src")
    assert plan.operations == ()
    assert plan.synced == (SyncedEntry(X.key, "x"),)
    assert not any(c.startswith("apply:") for c in world.spotify.calls)
    assert world.spotify.playlists["P1"].track_ids == ("a", "b", "c", "x")
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C", "X")
    after = world.repos.syncs.get("s1")
    assert after is not None
    state = after.destinations[0].last_synced
    assert state is not None
    assert SyncedEntry(TrackKey(SPOTIFY, "x"), "X") in state.entries
    assert state.added_by_you == ()


async def test_copy_back_not_found_is_reported(world: World) -> None:
    saved = await _user_added_x(world)
    world.repos.matches.remember(RememberedMatch(X.key, SPOTIFY, None))
    run = await _copier(world).copy(saved, "dst0", (), dry_run=True, seed=None)
    assert run.plan is not None
    plan = run.plan.for_member("src")
    assert plan.operations == () and plan.synced == ()
    assert [(c.kind, c.track_id, c.detail) for c in plan.not_made] == [
        ("add", "X", "not found on Spotify")
    ]
    (member,) = run.members
    assert member.not_made == plan.not_made


async def test_copy_back_unmatched_song_is_unresolved(world: World) -> None:
    saved = await _user_added_x(world)
    world.spotify.catalog = (
        track("spotify", "x1", title="x"),
        track("spotify", "x2", title="x"),
    )
    run = await _copier(world).copy(saved, "dst0", (), dry_run=True, seed=None)
    assert run.plan is not None
    (u,) = run.plan.unresolved
    assert (u.member_id, u.source.key) == ("src", X.key)
    assert {c.track.track_id for c in u.decision.candidates} == {"x1", "x2"}


async def test_copy_back_uses_the_preview_pairs(world: World) -> None:
    saved = await _user_added_x(world)
    world.spotify.catalog = (*world.spotify.catalog, track("spotify", "x9", title="z"))
    seed = {SPOTIFY: {X.key: "x9"}}
    run = await _copier(world).copy(saved, "dst0", (), dry_run=False, seed=seed)
    assert run.status is RunStatus.SUCCEEDED, run
    assert world.spotify.playlists["P1"].track_ids == ("a", "b", "c", "x9")


async def test_copy_back_ignores_songs_the_sync_owns(world: World) -> None:
    saved = await _user_added_x(world)
    run = await _copier(world).copy(
        saved, "dst0", ("A", "nope"), dry_run=True, seed=None
    )
    assert run.plan is not None
    plan = run.plan.for_member("src")
    assert plan.operations == () and plan.synced == ()
    (member,) = run.members
    assert member.status is MemberStatus.NOTHING_TO_DO


async def test_copy_back_search_without_results_is_not_found(world: World) -> None:
    saved = await _user_added_x(world)
    world.spotify.catalog = ()
    run = await _copier(world).copy(saved, "dst0", (), dry_run=True, seed=None)
    assert run.plan is not None
    assert run.plan.unresolved == ()
    plan = run.plan.for_member("src")
    assert [(c.kind, c.track_id, c.detail) for c in plan.not_made] == [
        ("add", "X", "not found on Spotify")
    ]


async def test_copy_back_pairs_what_landed_when_the_write_fails(world: World) -> None:
    saved = await _user_added_x(world)
    world.spotify.fail_writes(AddTracks, applied=True)
    runs: list[SyncRun] = []
    run = await _copier(world).copy(
        saved, "dst0", (), dry_run=False, seed=None, on_run=runs.append
    )
    assert run.status is RunStatus.FAILED
    assert [r.run_id for r in runs] == [run.run_id]  # no sync after a failed copy
    assert world.spotify.playlists["P1"].track_ids == ("a", "b", "c", "x")
    after = world.repos.syncs.get("s1")
    assert after is not None
    state = after.destinations[0].last_synced
    assert state is not None
    assert SyncedEntry(TrackKey(SPOTIFY, "x"), "X") in state.entries
    assert state.added_by_you == ()
    await _sync(world)
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C", "X")


async def test_copy_back_leaves_unpaired_what_did_not_land(world: World) -> None:
    saved = await _user_added_x(world)
    world.spotify.drop_adds({"x"})
    run = await _copier(world).copy(saved, "dst0", (), dry_run=False, seed=None)
    assert run.status is RunStatus.PARTIAL
    (member,) = run.members
    assert member.mismatch is not None
    assert world.repos.syncs.get("s1") == saved
