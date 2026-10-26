from collections.abc import Mapping, Sequence
from dataclasses import replace
from unittest.mock import ANY

import pytest
from hypothesis import given
from hypothesis.strategies import lists, sampled_from, sets

from linersync.domain.capabilities import Capability
from linersync.domain.matching.types import (
    MatchDecision,
    Matched,
    MatchVia,
    NeedsAttention,
    ScoredCandidate,
)
from linersync.domain.operations import (
    AddTracks,
    ChangeNotMade,
    CreatePlaylist,
    Removal,
    RemoveTracks,
    Reorder,
    UpdateMetadata,
)
from linersync.domain.planning.planner import (
    PlanRequest,
    UnsupportedDirection,
    plan_member,
    plan_sync,
)
from linersync.domain.playlists import PlaylistDraft, PlaylistSnapshot
from linersync.domain.sync import (
    Direction,
    MemberRole,
    MemberState,
    SyncedEntry,
    SyncGroup,
    SyncMember,
    SyncMode,
)
from linersync.domain.tracks import ServiceId, TrackKey
from tests.factories import NOW, group, snapshot, track

SRC = ServiceId("spotify")
DST = ServiceId("apple_music")
ALL_CAPS = frozenset(Capability)


def key(tid: str) -> TrackKey:
    return TrackKey(SRC, tid)


def matched(tid: str) -> Matched:
    return Matched(tid, 1.0, (), MatchVia.SCORED)


DEFAULT_MATCHES: dict[TrackKey, MatchDecision] = {
    key("a"): matched("A"),
    key("b"): matched("B"),
    key("c"): matched("C"),
}


def last(*pairs: tuple[str, str]) -> MemberState:
    return MemberState(
        entries=tuple(SyncedEntry(key(s), t) for s, t in pairs),
        draft=PlaylistDraft("Mix"),
        version_token=None,
        synced_at=NOW,
    )


def request(
    source: Sequence[str],
    target: Sequence[str] | None,
    *,
    modes: Sequence[SyncMode] = (SyncMode.FULL,),
    caps: frozenset[Capability] = ALL_CAPS,
    decisions: Mapping[TrackKey, MatchDecision] | None = None,
    last_synced: MemberState | None = None,
    target_name: str = "Mix",
) -> PlanRequest:
    g = group(destinations=[(DST, f"A{i}", m) for i, m in enumerate(modes)])
    if last_synced is not None:
        for d in g.destinations:
            g = g.with_member(
                SyncMember(
                    d.member_id, d.service, d.playlist_id, d.role, d.mode, last_synced
                )
            )
    snaps: dict[str, PlaylistSnapshot | None] = {"src": snapshot(SRC, "P1", source)}
    for d in g.destinations:
        snaps[d.member_id] = (
            None
            if target is None
            else snapshot(DST, str(d.playlist_id), target, name=target_name)
        )
    return PlanRequest(
        group=g,
        snapshots=snaps,
        capabilities={m.member_id: caps for m in g.members},
        matches={DST: {**DEFAULT_MATCHES, **(decisions or {})}},
    )


def test_full_mirror_adds_then_reorders_then_removes() -> None:
    p = plan_sync(request(["a", "b", "c"], ["A", "X"])).for_member("dst0")
    assert [type(o) for o in p.operations] == [AddTracks, Reorder, RemoveTracks]
    ops = p.operations
    assert isinstance(ops[0], AddTracks) and ops[0].track_ids == ("B", "C")
    assert isinstance(ops[1], Reorder) and ops[1].order == ("A", "B", "C", "X")
    assert isinstance(ops[2], RemoveTracks)
    assert ops[2].removals == (Removal("X", 3),)
    assert p.expected_after == ("A", "B", "C")
    assert [o.op_id for o in ops] == ["dst0:0", "dst0:1", "dst0:2"]
    assert p.not_made == ()
    assert p.synced == tuple(
        SyncedEntry(key(s), t) for s, t in [("a", "A"), ("b", "B"), ("c", "C")]
    )


def test_add_only_flags_removed_on_source() -> None:
    req = request(
        ["a"],
        ["A", "B"],
        modes=[SyncMode.ADD_ONLY],
        last_synced=last(("a", "A"), ("b", "B")),
    )
    p = plan_sync(req).for_member("dst0")
    assert not any(isinstance(o, RemoveTracks) for o in p.operations)
    assert p.not_made == (ChangeNotMade("remove", "B", ANY),)
    assert p.expected_after == ("A", "B")


def test_add_only_does_not_readd_user_deleted() -> None:
    req = request(
        ["a", "b"],
        ["A"],
        modes=[SyncMode.ADD_ONLY],
        last_synced=last(("a", "A"), ("b", "B")),
    )
    p = plan_sync(req).for_member("dst0")
    assert not any(isinstance(o, AddTracks) for o in p.operations)
    assert p.operations == ()
    assert p.not_made == ()
    assert p.expected_after == ("A",)
    assert p.synced == (SyncedEntry(key("a"), "A"), SyncedEntry(key("b"), "B"))


def test_add_only_user_deleted_stays_deleted_across_runs() -> None:
    first = request(
        ["a", "b"],
        ["A"],
        modes=[SyncMode.ADD_ONLY],
        last_synced=last(("a", "A"), ("b", "B")),
    )
    p1 = plan_sync(first).for_member("dst0")
    assert p1.operations == ()
    state = MemberState(p1.synced, p1.draft, None, NOW)
    # b now re-matches to a different id; detection must still hold
    second = request(
        ["a", "b"],
        ["A"],
        modes=[SyncMode.ADD_ONLY],
        decisions={key("b"): matched("B2")},
        last_synced=state,
    )
    p2 = plan_sync(second).for_member("dst0")
    assert not any(isinstance(o, AddTracks) for o in p2.operations)
    assert p2.synced == p1.synced


def test_add_only_adds_new_source_songs() -> None:
    req = request(
        ["a", "b", "c"],
        ["A"],
        modes=[SyncMode.ADD_ONLY],
        last_synced=last(("a", "A"), ("b", "B")),
    )
    p = plan_sync(req).for_member("dst0")
    assert [type(o) for o in p.operations] == [AddTracks]
    assert isinstance(p.operations[0], AddTracks)
    assert p.operations[0].track_ids == ("C",)
    assert p.expected_after == ("A", "C")


def test_add_only_deleted_copy_of_repeat_not_readded() -> None:
    req = request(
        ["a", "a", "b"],
        ["A", "B"],
        modes=[SyncMode.ADD_ONLY],
        last_synced=last(("a", "A"), ("a", "A"), ("b", "B")),
    )
    p = plan_sync(req).for_member("dst0")
    assert not any(isinstance(o, AddTracks) for o in p.operations)
    assert p.operations == ()
    assert p.expected_after == ("A", "B")
    assert p.synced == tuple(
        SyncedEntry(key(s), t) for s, t in [("a", "A"), ("a", "A"), ("b", "B")]
    )
    # stable on the next run
    state = MemberState(p.synced, p.draft, None, NOW)
    again = request(
        ["a", "a", "b"], ["A", "B"], modes=[SyncMode.ADD_ONLY], last_synced=state
    )
    assert plan_sync(again).for_member("dst0").operations == ()


def test_add_only_new_source_repeat_still_added() -> None:
    req = request(
        ["a", "a"],
        ["A"],
        modes=[SyncMode.ADD_ONLY],
        last_synced=last(("a", "A")),
    )
    p = plan_sync(req).for_member("dst0")
    assert [type(o) for o in p.operations] == [AddTracks]
    assert p.expected_after == ("A", "A")


def test_capability_downgrade_full_to_add_only() -> None:
    req = request(
        ["a"],
        ["A", "B", "X"],
        caps=ALL_CAPS - {Capability.REMOVE},
        last_synced=last(("a", "A"), ("b", "B")),
    )
    p = plan_sync(req).for_member("dst0")
    assert not any(isinstance(o, RemoveTracks) for o in p.operations)
    assert p.not_made == (ChangeNotMade("remove", "B", ANY),)
    assert p.expected_after == ("A", "B", "X")


def test_unresolved_counterpart_protected() -> None:
    attention = NeedsAttention(("low confidence",), ())
    req = request(
        ["a", "b"],
        ["A", "B"],
        decisions={key("a"): attention},
        last_synced=last(("a", "A"), ("b", "B")),
    )
    plan = plan_sync(req)
    p = plan.for_member("dst0")
    assert not any(isinstance(o, RemoveTracks) for o in p.operations)
    assert "A" in p.expected_after
    assert [(u.member_id, u.source.key, u.position) for u in plan.unresolved] == [
        ("dst0", key("a"), 0)
    ]
    assert plan.unresolved[0].decision == attention
    assert p.synced == (SyncedEntry(key("b"), "B"),)


def test_candidate_protected_on_first_run() -> None:
    attention = NeedsAttention(
        ("ambiguous",), (ScoredCandidate(track(DST, "A"), 0.7, ()),)
    )
    req = request(["a"], ["A"], decisions={key("a"): attention})
    p = plan_sync(req).for_member("dst0")
    assert not any(isinstance(o, RemoveTracks) for o in p.operations)
    assert p.expected_after == ("A",)


def test_missing_target_rebuilds() -> None:
    p = plan_sync(request(["a", "b", "c"], None)).for_member("dst0")
    assert [type(o) for o in p.operations] == [CreatePlaylist, AddTracks]
    ops = p.operations
    assert isinstance(ops[0], CreatePlaylist) and ops[0].draft.name == "Mix"
    assert isinstance(ops[1], AddTracks) and ops[1].track_ids == ("A", "B", "C")
    assert p.expected_after == ("A", "B", "C")


def test_missing_target_with_empty_source_is_not_created() -> None:
    # An empty playlist is created on the first run that has songs: an empty
    # Apple playlist can't be read back (D3).
    for caps in (ALL_CAPS, ALL_CAPS - {Capability.CREATE_PLAYLIST}):
        for mode in (SyncMode.FULL, SyncMode.ADD_ONLY):
            req = request([], None, modes=[mode], caps=caps)
            p = plan_sync(req).for_member("dst0")
            assert p.operations == ()
            assert p.not_made == ()
            assert p.expected_after == ()


def test_missing_target_without_create_capability() -> None:
    req = request(["a"], None, caps=ALL_CAPS - {Capability.CREATE_PLAYLIST})
    p = plan_sync(req).for_member("dst0")
    assert p.operations == ()
    assert p.not_made == (ChangeNotMade("create", None, ANY),)


def test_metadata_update_full_only() -> None:
    req = request(
        ["a"],
        ["A"],
        modes=[SyncMode.FULL, SyncMode.ADD_ONLY],
        target_name="Old",
    )
    plan = plan_sync(req)
    full = plan.for_member("dst0")
    assert [type(o) for o in full.operations] == [UpdateMetadata]
    assert isinstance(full.operations[0], UpdateMetadata)
    assert full.operations[0].draft == PlaylistDraft("Mix")
    assert plan.for_member("dst1").operations == ()

    no_edit = request(
        ["a"], ["A"], caps=ALL_CAPS - {Capability.EDIT_METADATA}, target_name="Old"
    )
    p = plan_sync(no_edit).for_member("dst0")
    assert p.operations == ()
    assert p.not_made == (ChangeNotMade("metadata", None, ANY),)


def _target_with(
    name: str, description: str, cover_url: str | None
) -> PlaylistSnapshot:
    snap = snapshot(DST, "A0", ["A"], name=name)
    return replace(
        snap, meta=replace(snap.meta, description=description, cover_url=cover_url)
    )


_PREV = PlaylistDraft("Mix", "d", "https://src/c.jpg")
_OTHER_COVER = "https://dst/other.jpg"


def test_metadata_compared_with_last_synced_draft() -> None:
    target = _target_with("Mix", "d", _OTHER_COVER)
    p = plan_member(
        "dst0", target, ["A"], _PREV, ALL_CAPS, SyncMode.FULL, previous_draft=_PREV
    )
    assert p.operations == ()


def test_source_rename_updates_target() -> None:
    target = _target_with("Mix", "d", _OTHER_COVER)
    draft = PlaylistDraft("Mix 2", "d", "https://src/c.jpg")
    p = plan_member(
        "dst0", target, ["A"], draft, ALL_CAPS, SyncMode.FULL, previous_draft=_PREV
    )
    assert [type(o) for o in p.operations] == [UpdateMetadata]


def test_source_cover_change_updates_target() -> None:
    target = _target_with("Mix", "d", _OTHER_COVER)
    draft = PlaylistDraft("Mix", "d", "https://src/new.jpg")
    p = plan_member(
        "dst0", target, ["A"], draft, ALL_CAPS, SyncMode.FULL, previous_draft=_PREV
    )
    assert [type(o) for o in p.operations] == [UpdateMetadata]


def test_target_renamed_by_user_left_alone_until_source_changes() -> None:
    target = _target_with("Mine", "d", _OTHER_COVER)
    p = plan_member(
        "dst0", target, ["A"], _PREV, ALL_CAPS, SyncMode.FULL, previous_draft=_PREV
    )
    assert p.operations == ()


def test_plan_sync_uses_last_synced_draft() -> None:
    state = MemberState(
        entries=(SyncedEntry(key("a"), "A"),),
        draft=PlaylistDraft("Mix", "", "https://src/c.jpg"),
        version_token=None,
        synced_at=NOW,
    )
    req = request(["a"], ["A"], last_synced=state)
    dest_id = req.group.destinations[0].member_id
    target, source = req.snapshots[dest_id], req.snapshots["src"]
    assert target is not None and source is not None
    snaps = {
        "src": replace(
            source, meta=replace(source.meta, cover_url="https://src/c.jpg")
        ),
        dest_id: replace(target, meta=replace(target.meta, cover_url=_OTHER_COVER)),
    }
    p = plan_sync(replace(req, snapshots=snaps)).for_member(dest_id)
    assert not any(isinstance(o, UpdateMetadata) for o in p.operations)


def test_no_reorder_capability_records_not_made() -> None:
    req = request(["a", "b"], ["B", "A", "X"], caps=ALL_CAPS - {Capability.REORDER})
    p = plan_sync(req).for_member("dst0")
    assert [type(o) for o in p.operations] == [RemoveTracks]
    assert isinstance(p.operations[0], RemoveTracks)
    assert p.operations[0].removals == (Removal("X", 2),)
    assert p.not_made == (ChangeNotMade("reorder", None, ANY),)
    assert p.expected_after == ("B", "A")


def test_no_reorder_capability_same_order_is_quiet() -> None:
    req = request(
        ["a", "b", "c"], ["A", "X", "B"], caps=ALL_CAPS - {Capability.REORDER}
    )
    p = plan_sync(req).for_member("dst0")
    assert [type(o) for o in p.operations] == [AddTracks, RemoveTracks]
    assert isinstance(p.operations[1], RemoveTracks)
    assert p.operations[1].removals == (Removal("X", 1),)
    assert p.not_made == ()
    assert p.expected_after == ("A", "B", "C")


def test_one_to_many_independent_modes() -> None:
    req = request(["a", "b", "c"], ["A", "X"], modes=[SyncMode.FULL, SyncMode.ADD_ONLY])
    plan = plan_sync(req)
    assert [p.member_id for p in plan.member_plans] == ["dst0", "dst1"]
    assert plan.sync_id == "s1"
    assert plan.for_member("dst0").expected_after == ("A", "B", "C")
    add_only = plan.for_member("dst1")
    assert [type(o) for o in add_only.operations] == [AddTracks]
    assert [o.op_id for o in add_only.operations] == ["dst1:0"]
    assert add_only.expected_after == ("A", "X", "B", "C")


def test_two_way_unsupported() -> None:
    g = SyncGroup(
        sync_id="s1",
        name="Mix",
        direction=Direction.TWO_WAY,
        members=(
            SyncMember("p0", SRC, "P1", MemberRole.PEER),
            SyncMember("p1", DST, "A1", MemberRole.PEER),
        ),
    )
    req = PlanRequest(group=g, snapshots={}, capabilities={}, matches={})
    with pytest.raises(UnsupportedDirection):
        plan_sync(req)


def test_repeats_survive_planning() -> None:
    p = plan_sync(request(["a", "b", "a"], [])).for_member("dst0")
    assert p.expected_after == ("A", "B", "A")
    assert isinstance(p.operations[0], AddTracks)
    assert p.operations[0].track_ids == ("A", "B", "A")


def _apply(current: Sequence[str], ops: Sequence[object]) -> tuple[str, ...]:
    items = list(current)
    for op in ops:
        if isinstance(op, AddTracks):
            items.extend(op.track_ids)
        elif isinstance(op, Reorder):
            assert sorted(op.order) == sorted(items)
            items = list(op.order)
        elif isinstance(op, RemoveTracks):
            for r in op.removals:
                assert items[r.position] == r.track_id
            drop = {r.position for r in op.removals}
            items = [t for i, t in enumerate(items) if i not in drop]
    return tuple(items)


@given(
    lists(sampled_from("ABCD"), max_size=8),
    lists(sampled_from("ABCD"), max_size=8),
    sets(sampled_from("ABCD")),
    sets(sampled_from(list(Capability))),
    sampled_from(list(SyncMode)),
)
def test_expected_after_matches_applied_ops(
    current: list[str],
    desired: list[str],
    protected: set[str],
    caps: set[Capability],
    mode: SyncMode,
) -> None:
    snap = snapshot(DST, "A0", current)
    p = plan_member(
        "dst0",
        snap,
        desired,
        PlaylistDraft("Mix"),
        frozenset(caps),
        mode,
        protected=frozenset(protected),
    )
    assert _apply(current, p.operations) == p.expected_after
    removed = [
        r.track_id
        for o in p.operations
        if isinstance(o, RemoveTracks)
        for r in o.removals
    ]
    assert not set(removed) & protected
    if mode is SyncMode.ADD_ONLY or Capability.REMOVE not in caps:
        assert not removed
    emission = [UpdateMetadata, AddTracks, Reorder, RemoveTracks]
    kinds = [emission.index(type(o)) for o in p.operations]
    assert kinds == sorted(kinds)
