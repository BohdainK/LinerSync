from dataclasses import replace
from datetime import UTC, datetime

import pytest

from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.capabilities import Capability, limitations
from linersync.domain.sync import (
    Direction,
    MemberRole,
    SyncGroup,
    SyncMember,
    SyncMode,
    effective_mode,
)
from linersync.domain.tracks import ServiceId
from tests.factories import group, snapshot, track

SP = ServiceId("spotify")
AM = ServiceId("apple_music")


def test_track_key_str() -> None:
    assert str(track("spotify", "abc").key) == "spotify:abc"


def test_snapshot_complete_only_when_total_matches() -> None:
    assert snapshot("spotify", "P", ["a", "b"]).is_complete
    assert not snapshot("spotify", "P", ["a"], expected_total=2).is_complete
    assert not snapshot("spotify", "P", ["a"], expected_total=None).is_complete


def test_snapshot_shortfall_carries_the_reason() -> None:
    short = snapshot("spotify", "P", ["a"], expected_total=2)
    assert short.shortfall() == "(1 of 2 songs)"
    reason = "1 song is no longer available; remove it"
    assert replace(short, missing_reason=reason).shortfall() == (
        "(1 of 2 songs; 1 song is no longer available; remove it)"
    )


def _m(
    mid: str, role: MemberRole, pid: str | None = "X", svc: ServiceId = SP
) -> SyncMember:
    return SyncMember(mid, svc, pid, role)


@pytest.mark.parametrize(
    "members",
    [
        (
            _m("a", MemberRole.DESTINATION, svc=AM),
            _m("b", MemberRole.DESTINATION, svc=AM),
        ),
        (
            _m("a", MemberRole.SOURCE),
            _m("b", MemberRole.SOURCE),
            _m("c", MemberRole.DESTINATION, svc=AM),
        ),
        (_m("a", MemberRole.SOURCE),),
        (_m("a", MemberRole.SOURCE), _m("a", MemberRole.DESTINATION, svc=AM)),
        (_m("a", MemberRole.SOURCE), _m("b", MemberRole.DESTINATION)),
        (
            _m("a", MemberRole.SOURCE),
            _m("b", MemberRole.DESTINATION, svc=AM),
            _m("c", MemberRole.PEER, svc=AM),
        ),
    ],
)
def test_one_way_group_validation(members: tuple[SyncMember, ...]) -> None:
    with pytest.raises(ValueError):
        SyncGroup("s", "n", Direction.ONE_WAY, members)


def test_group_helpers() -> None:
    g = group(
        destinations=(
            ("apple_music", "A1", SyncMode.FULL),
            ("tidal", None, SyncMode.ADD_ONLY),
        )
    )
    assert g.source.member_id == "src"
    assert [d.member_id for d in g.destinations] == ["dst0", "dst1"]
    changed = g.with_member(replace(g.destinations[1], playlist_id="T1"))
    assert changed.members[2].playlist_id == "T1"
    assert changed.members[0] == g.members[0]


def test_effective_mode() -> None:
    assert (
        effective_mode(SyncMode.FULL, frozenset({Capability.ADD})) is SyncMode.ADD_ONLY
    )
    assert (
        effective_mode(SyncMode.FULL, frozenset({Capability.ADD, Capability.REMOVE}))
        is SyncMode.FULL
    )


def test_apple_limitation_copy() -> None:
    caps = frozenset(
        {Capability.READ_ORDER, Capability.ADD, Capability.CREATE_PLAYLIST}
    )
    assert (
        limitations("Apple Music", caps, SyncMode.FULL)[0]
        == "Apple Music can't remove or reorder songs, "
        "so this sync runs as add only into Apple Music"
    )


def test_other_limitations_copy() -> None:
    caps = frozenset({Capability.ADD, Capability.REMOVE})
    assert limitations("X", caps, SyncMode.FULL) == (
        "X can't reorder songs, so song order won't carry over to X",
        "X can't edit playlist details, "
        "so name and description changes won't carry over",
        "X can't create playlists, so the destination playlist must already exist",
    )
    only_remove_missing = frozenset(
        {
            Capability.ADD,
            Capability.REORDER,
            Capability.EDIT_METADATA,
            Capability.CREATE_PLAYLIST,
        }
    )
    assert limitations("X", only_remove_missing, SyncMode.FULL) == (
        "X can't remove songs, so this sync runs as add only into X",
    )


def test_attention_dedupe_key_ignores_item_id_and_time() -> None:
    src = track("spotify", "abc")
    a = AttentionItem(
        "i1",
        AttentionKind.UNMATCHED,
        datetime(2026, 1, 1, tzinfo=UTC),
        "d",
        sync_id="s1",
        member_id="dst0",
        source=src,
    )
    b = replace(a, item_id="i2", created_at=datetime(2026, 2, 2, tzinfo=UTC))
    assert a.dedupe_key == b.dedupe_key == ("unmatched", "s1", "dst0", "spotify:abc")


def test_read_only_limitation() -> None:
    caps = frozenset({Capability.READ_ORDER})
    assert limitations("Jellyfin", caps, SyncMode.FULL) == (
        "Jellyfin is read-only, so it can't be a destination",
    )
