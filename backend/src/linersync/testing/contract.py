"""Behaviour laws every connector must obey.

Plain async functions over a ``ConnectorHarness``; no pytest dependency, so
third-party connectors can run them from any test runner.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import StrEnum
from typing import Protocol

from linersync.domain.capabilities import Capability
from linersync.domain.operations import (
    AddTracks,
    Removal,
    RemoveTracks,
    Reorder,
    UpdateMetadata,
)
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import Track, TrackQuery
from linersync.ports.connector import Connector
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed


class Fault(StrEnum):
    READ_ERROR = "read_error"
    TRUNCATED_READ = "truncated_read"
    WRITE_REJECTED = "write_rejected"
    WRITE_MAYBE_APPLIED = "write_maybe_applied"


class ContractSkip(Exception):
    """The connector or harness lacks what this law needs."""


class ConnectorHarness(Protocol):
    connector: Connector
    # At least 3 tracks on this service, distinct titles, each with an ISRC;
    # ids are valid for AddTracks.
    catalog: tuple[Track, ...]

    async def make_playlist(self, name: str, track_ids: Sequence[str]) -> str:
        """Arrange a playlist out of band; returns its playlist_id."""
        ...

    def supports(self, fault: Fault) -> bool: ...

    def inject(self, fault: Fault, playlist_id: str) -> None:
        """The next read (READ_ERROR/TRUNCATED_READ) or AddTracks (WRITE_*)
        of that playlist fails."""
        ...


Law = Callable[[ConnectorHarness], Awaitable[None]]

_DRAFT = PlaylistDraft("Mix & Match <3>", 'Rock & roll, "quoted"')


def _need(h: ConnectorHarness, *caps: Capability) -> None:
    for cap in caps:
        if cap not in h.connector.capabilities:
            raise ContractSkip(f"connector lacks {cap}")


def _need_fault(h: ConnectorHarness, fault: Fault) -> None:
    if not h.supports(fault):
        raise ContractSkip(f"harness cannot inject {fault}")


def _ids(h: ConnectorHarness) -> tuple[str, str, str]:
    x, y, z = (t.track_id for t in h.catalog[:3])
    return x, y, z


async def _read_ids(h: ConnectorHarness, playlist_id: str) -> tuple[str, ...]:
    snap = await h.connector.read_playlist(playlist_id)
    assert snap.is_complete, "snapshot is not complete"
    return snap.track_ids


async def list_includes_arranged_playlist(h: ConnectorHarness) -> None:
    x, _, _ = _ids(h)
    pid = await h.make_playlist("Contract list", [x])
    metas = await h.connector.list_playlists()
    assert pid in {m.playlist_id for m in metas}
    for m in metas:
        assert m.service == h.connector.service, f"{m.playlist_id} has {m.service}"


async def read_is_complete_ordered_with_repeats(h: ConnectorHarness) -> None:
    x, y, _ = _ids(h)
    pid = await h.make_playlist("Contract repeats", [x, y, x])
    assert await _read_ids(h, pid) == (x, y, x)


async def read_paginates_beyond_page_size(h: ConnectorHarness) -> None:
    ids = [t.track_id for t in h.catalog]
    n = 2 * h.connector.limits.page_size + 1
    wanted = [ids[i % len(ids)] for i in range(n)]
    pid = await h.make_playlist("Contract pages", wanted)
    assert list(await _read_ids(h, pid)) == wanted


async def read_unknown_playlist_is_not_found(h: ConnectorHarness) -> None:
    try:
        await h.connector.read_playlist("contract-no-such-playlist")
    except PlaylistNotFound:
        return
    raise AssertionError("expected PlaylistNotFound")


async def snapshot_tracks_have_identity(h: ConnectorHarness) -> None:
    x, y, _ = _ids(h)
    pid = await h.make_playlist("Contract identity", [x, y])
    snap = await h.connector.read_playlist(pid)
    assert snap.entries
    for e in snap.entries:
        assert e.track.service == h.connector.service
        assert e.track.track_id
        assert e.track.title


async def search_by_isrc_finds_track(h: ConnectorHarness) -> None:
    target = h.catalog[0]
    found = await h.connector.search(TrackQuery.from_track(target))
    assert target.track_id in [t.track_id for t in found]


async def create_then_add_then_read(h: ConnectorHarness) -> None:
    _need(h, Capability.CREATE_PLAYLIST, Capability.ADD)
    x, y, _ = _ids(h)
    meta = await h.connector.create_playlist(PlaylistDraft("Contract created"))
    assert meta.name == "Contract created"
    await h.connector.apply(meta.playlist_id, AddTracks("contract-1", (x, y)))
    snap = await h.connector.read_playlist(meta.playlist_id)
    assert snap.meta.name == "Contract created"
    assert snap.is_complete
    assert snap.track_ids == (x, y)


async def add_appends_in_order_with_repeats(h: ConnectorHarness) -> None:
    _need(h, Capability.ADD)
    x, y, _ = _ids(h)
    pid = await h.make_playlist("Contract add", [x])
    await h.connector.apply(pid, AddTracks("contract-1", (y, x)))
    assert await _read_ids(h, pid) == (x, y, x)


async def add_beyond_batch_size_applies_all(h: ConnectorHarness) -> None:
    _need(h, Capability.ADD)
    ids = [t.track_id for t in h.catalog]
    n = h.connector.limits.add_batch_size + 1
    wanted = [ids[i % len(ids)] for i in range(n)]
    pid = await h.make_playlist("Contract batch", [])
    await h.connector.apply(pid, AddTracks("contract-1", tuple(wanted)))
    assert list(await _read_ids(h, pid)) == wanted


async def remove_one_copy_of_repeat_keeps_other(h: ConnectorHarness) -> None:
    _need(h, Capability.REMOVE)
    x, y, z = _ids(h)
    pid = await h.make_playlist("Contract remove", [x, y, x, z])
    await h.connector.apply(pid, RemoveTracks("contract-1", (Removal(x, 2),)))
    assert await _read_ids(h, pid) == (x, y, z)


async def reorder_produces_exact_sequence(h: ConnectorHarness) -> None:
    _need(h, Capability.REORDER)
    x, y, z = _ids(h)
    pid = await h.make_playlist("Contract reorder", [x, y, z, x])
    await h.connector.apply(pid, Reorder("contract-1", (x, z, x, y)))
    assert await _read_ids(h, pid) == (x, z, x, y)


async def metadata_round_trips(h: ConnectorHarness) -> None:
    _need(h, Capability.EDIT_METADATA)
    x, _, _ = _ids(h)
    pid = await h.make_playlist("Contract metadata", [x])
    await h.connector.apply(pid, UpdateMetadata("contract-1", _DRAFT))
    snap = await h.connector.read_playlist(pid)
    assert snap.meta.name == _DRAFT.name
    assert snap.meta.description == _DRAFT.description
    metas = {m.playlist_id: m for m in await h.connector.list_playlists()}
    assert metas[pid].name == _DRAFT.name


async def read_error_is_never_empty(h: ConnectorHarness) -> None:
    _need_fault(h, Fault.READ_ERROR)
    x, y, _ = _ids(h)
    pid = await h.make_playlist("Contract read error", [x, y])
    h.inject(Fault.READ_ERROR, pid)
    try:
        snap = await h.connector.read_playlist(pid)
    except ReadFailed:
        return
    raise AssertionError(f"expected ReadFailed, got {len(snap.entries)} entries")


async def truncated_read_is_not_complete(h: ConnectorHarness) -> None:
    _need_fault(h, Fault.TRUNCATED_READ)
    x, y, _ = _ids(h)
    pid = await h.make_playlist("Contract truncated", [x, y])
    h.inject(Fault.TRUNCATED_READ, pid)
    try:
        snap = await h.connector.read_playlist(pid)
    except ReadFailed:
        return
    assert snap.is_complete is False, "truncated read claims to be complete"


async def rejected_write_reports_not_applied(h: ConnectorHarness) -> None:
    _need_fault(h, Fault.WRITE_REJECTED)
    _need(h, Capability.ADD)
    x, y, z = _ids(h)
    pid = await h.make_playlist("Contract rejected", [x, y])
    h.inject(Fault.WRITE_REJECTED, pid)
    try:
        await h.connector.apply(pid, AddTracks("contract-1", (z,)))
    except WriteFailed as e:
        assert e.maybe_applied is False
    else:
        raise AssertionError("expected WriteFailed")
    assert await _read_ids(h, pid) == (x, y)


async def maybe_applied_write_reports_maybe(h: ConnectorHarness) -> None:
    _need_fault(h, Fault.WRITE_MAYBE_APPLIED)
    _need(h, Capability.ADD)
    x, y, z = _ids(h)
    pid = await h.make_playlist("Contract maybe", [x, y])
    h.inject(Fault.WRITE_MAYBE_APPLIED, pid)
    try:
        await h.connector.apply(pid, AddTracks("contract-1", (z,)))
    except WriteFailed as e:
        assert e.maybe_applied is True
    else:
        raise AssertionError("expected WriteFailed")


LAWS: Mapping[str, Law] = {
    fn.__name__: fn
    for fn in (
        list_includes_arranged_playlist,
        read_is_complete_ordered_with_repeats,
        read_paginates_beyond_page_size,
        read_unknown_playlist_is_not_found,
        snapshot_tracks_have_identity,
        search_by_isrc_finds_track,
        create_then_add_then_read,
        add_appends_in_order_with_repeats,
        add_beyond_batch_size_applies_all,
        remove_one_copy_of_repeat_keeps_other,
        reorder_produces_exact_sequence,
        metadata_round_trips,
        read_error_is_never_empty,
        truncated_read_is_not_complete,
        rejected_write_reports_not_applied,
        maybe_applied_write_reports_maybe,
    )
}
