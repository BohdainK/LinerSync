from dataclasses import replace

from linersync.application.transfer import BulkItem, TransferService
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.runs import MemberStatus, RunKind, RunStatus
from linersync.ports.connector import ConnectorLimits
from tests.application.conftest import APPLE, SPOTIFY, World
from tests.factories import snapshot


def _svc(w: World) -> TransferService:
    return TransferService(w.deps, w.runner())


async def test_transfer_creates_named_playlist(unsaved_world: World) -> None:
    w = unsaved_world
    run = await _svc(w).transfer(SPOTIFY, "P1", APPLE)
    assert run.kind is RunKind.TRANSFER
    assert run.status is RunStatus.SUCCEEDED
    (member,) = run.members
    assert member.status is MemberStatus.APPLIED
    created = [s for pid, s in w.apple.playlists.items() if pid != "A1"]
    assert len(created) == 1
    assert created[0].meta.name == "Mix"
    assert created[0].track_ids == ("A", "B", "C")
    assert w.repos.runs.get(run.run_id) == run


async def test_transfer_dry_run_writes_nothing(unsaved_world: World) -> None:
    w = unsaved_world
    run = await _svc(w).transfer(SPOTIFY, "P1", APPLE, dry_run=True)
    assert run.dry_run
    assert set(w.apple.playlists) == {"A1"}
    assert not any(c.startswith(("create", "apply")) for c in w.apple.calls)


async def test_transfer_not_listed_as_sync(unsaved_world: World) -> None:
    await _svc(unsaved_world).transfer(SPOTIFY, "P1", APPLE)
    assert unsaved_world.repos.syncs.list() == []


def _bulk_world(w: World) -> None:
    w.spotify.limits = ConnectorLimits(page_size=100, add_batch_size=100)
    w.apple.limits = ConnectorLimits(page_size=100, add_batch_size=100, search_cost=1)
    for pid, n in (("B1", 150), ("B2", 50)):
        snap = snapshot("spotify", pid, [f"t{i}" for i in range(n)])
        w.spotify.add_playlist(replace(snap, meta=replace(snap.meta, track_count=n)))


async def test_estimate_bulk(unsaved_world: World) -> None:
    w = unsaved_world
    _bulk_world(w)
    est = await _svc(w).estimate_bulk(
        [BulkItem(SPOTIFY, pid, (APPLE,)) for pid in ("B1", "B2")]
    )
    assert est.playlists == 2 and est.songs == 200
    assert est.api_calls == {"spotify": 3, "apple_music": 200 + 3 + 2}
    assert est.add_only_destinations == ("apple_music",)
    assert est.unreadable == ()
    assert w.spotify.calls == ["list"]


async def test_estimate_lists_unreadable(unsaved_world: World) -> None:
    w = unsaved_world
    _bulk_world(w)
    snap = snapshot("spotify", "X1", ["q"])
    meta: PlaylistMeta = replace(
        snap.meta, readable=False, unreadable_reason="private", track_count=1
    )
    w.spotify.add_playlist(replace(snap, meta=meta))
    est = await _svc(w).estimate_bulk(
        [BulkItem(SPOTIFY, pid, (APPLE,)) for pid in ("B2", "X1")]
    )
    assert est.playlists == 1 and est.songs == 50
    assert est.unreadable == (meta,)
    assert est.api_calls == {"spotify": 1, "apple_music": 50 + 1 + 1}
