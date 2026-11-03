from dataclasses import replace

import pytest

from linersync.application.attention import AttentionService
from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.runs import RunStatus, Trigger
from linersync.domain.sync import SyncMode, SyncStatus
from linersync.domain.tracks import ServiceId
from tests.application.conftest import World
from tests.factories import group, snapshot, track

MANUAL = Trigger.MANUAL


def _service(w: World) -> AttentionService:
    return AttentionService(w.repos, w.clock)


def _open(w: World, kind: AttentionKind) -> list[AttentionItem]:
    return [i for i in w.repos.attention.list_open() if i.kind is kind]


async def _with_unmatched(w: World) -> AttentionItem:
    w.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c", "zzz"]))
    await w.runner().run("s1", trigger=MANUAL)
    (item,) = _open(w, AttentionKind.UNMATCHED)
    return item


async def test_pick_candidate_remembered_and_used_next_run(world: World) -> None:
    item = await _with_unmatched(world)
    world.apple.catalog += (track("apple_music", "Z", title="other"),)
    _service(world).pick_candidate(item.item_id, "Z")
    assert _open(world, AttentionKind.UNMATCHED) == []
    resolved = world.repos.attention.get(item.item_id)
    assert resolved is not None and resolved.resolution == "picked Z"
    assert resolved.resolved_at == world.clock.now()
    assert item.source is not None
    remembered = world.repos.matches.get(item.source.key, ServiceId("apple_music"))
    assert remembered is not None and remembered.target_track_id == "Z"

    world.apple.calls.clear()
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    assert "search:zzz" not in world.apple.calls
    assert "Z" in world.apple.playlists["A1"].track_ids
    assert _open(world, AttentionKind.UNMATCHED) == []


async def test_pick_resolves_same_song_in_other_syncs(world: World) -> None:
    item = await _with_unmatched(world)
    # A second sync of the same source song into the same service.
    world.apple.add_playlist(snapshot("apple_music", "A2", []))
    world.repos.syncs.save(
        group(destinations=[("apple_music", "A2", SyncMode.FULL)], sync_id="s3")
    )
    await world.runner().run("s3", trigger=MANUAL)
    assert len(_open(world, AttentionKind.UNMATCHED)) == 2
    world.apple.catalog += (track("apple_music", "Z", title="other"),)
    _service(world).pick_candidate(item.item_id, "Z")
    assert _open(world, AttentionKind.UNMATCHED) == []


async def test_not_on_service_stops_asking(world: World) -> None:
    item = await _with_unmatched(world)
    _service(world).mark_not_on_service(item.item_id)
    resolved = world.repos.attention.get(item.item_id)
    assert resolved is not None and resolved.resolution == "not on service"

    world.apple.calls.clear()
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED
    assert _open(world, AttentionKind.UNMATCHED) == []
    assert "search:zzz" not in world.apple.calls
    assert world.apple.playlists["A1"].track_ids == ("A", "B", "C")


async def test_approve_then_rerun_applies(world: World) -> None:
    world.spotify_to_spotify(("a", "b", "c"), mirror=True)
    world.spotify.add_playlist(snapshot("spotify", "P1", []))
    run = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.AWAITING_APPROVAL
    (item,) = _open(world, AttentionKind.BRAKE)
    _service(world).approve(item.item_id)
    assert _open(world, AttentionKind.BRAKE) == []
    again = await world.runner().run("s2", trigger=Trigger.SCHEDULED)
    assert again.status is RunStatus.SUCCEEDED
    assert len(world.spotify.playlists["P2"].entries) == 0


async def test_resume_sync_unpauses(world: World) -> None:
    world.spotify.drop_playlist("P1")
    await world.runner().run("s1", trigger=MANUAL)
    (item,) = _open(world, AttentionKind.SOURCE_MISSING)
    world.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c"]))
    _service(world).resume_sync(item.item_id)
    saved = world.repos.syncs.get("s1")
    assert saved is not None
    assert saved.status is SyncStatus.ACTIVE and saved.pause_reason is None
    got = world.repos.attention.get(item.item_id)
    assert got is not None and got.resolution == "resumed"
    run = await world.runner().run("s1", trigger=MANUAL)
    assert run.status is RunStatus.SUCCEEDED


async def test_resume_sync_missing_group_raises(world: World) -> None:
    world.spotify.drop_playlist("P1")
    await world.runner().run("s1", trigger=MANUAL)
    (item,) = _open(world, AttentionKind.SOURCE_MISSING)
    repos = replace(world.repos, syncs=_NoSyncs())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AttentionService(repos, world.clock).resume_sync(item.item_id)


class _NoSyncs:
    def get(self, sync_id: str) -> None:
        return None


async def test_dismiss_resolves_any_kind(world: World) -> None:
    item = await _with_unmatched(world)
    _service(world).dismiss(item.item_id)
    got = world.repos.attention.get(item.item_id)
    assert got is not None and got.resolution == "dismissed"


async def test_wrong_kind_rejected(world: World) -> None:
    item = await _with_unmatched(world)
    svc = _service(world)
    with pytest.raises(ValueError):
        svc.approve(item.item_id)
    with pytest.raises(ValueError):
        svc.resume_sync(item.item_id)
    svc.dismiss(item.item_id)
    with pytest.raises(ValueError):  # already resolved
        svc.pick_candidate(item.item_id, "Z")
    with pytest.raises(ValueError):
        svc.dismiss(item.item_id)
    with pytest.raises(ValueError):  # unknown
        svc.dismiss("nope")
