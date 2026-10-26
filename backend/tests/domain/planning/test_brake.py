from dataclasses import replace

from linersync.domain.operations import (
    AddTracks,
    CreatePlaylist,
    MemberPlan,
    Operation,
    Removal,
    RemoveTracks,
    Reorder,
    UpdateMetadata,
)
from linersync.domain.planning.brake import (
    BrakeConfig,
    BrakeTrip,
    check_brake,
    fingerprint,
)
from linersync.domain.playlists import PlaylistDraft


def _plan(*ops: Operation) -> MemberPlan:
    return MemberPlan(
        member_id="m1",
        operations=ops,
        not_made=(),
        expected_after=(),
        synced=(),
        draft=PlaylistDraft(name="x"),
    )


def _removals(n: int, op_id: str = "o1") -> RemoveTracks:
    return RemoveTracks(op_id, tuple(Removal(f"t{i}", i) for i in range(n)))


def test_twenty_percent_is_allowed() -> None:
    assert check_brake(_plan(_removals(2)), 10, BrakeConfig()) is None


def test_over_twenty_percent_trips() -> None:
    trip = check_brake(_plan(_removals(3)), 10, BrakeConfig())
    assert trip == BrakeTrip(
        member_id="m1",
        removals=3,
        size=10,
        reason="This run would remove 3 of 10 songs",
    )


def test_count_limit_trips() -> None:
    trip = check_brake(_plan(_removals(51)), 1000, BrakeConfig())
    assert trip is not None
    assert trip.removals == 51


def test_empty_target_never_trips() -> None:
    assert check_brake(_plan(), 0, BrakeConfig()) is None


def test_config_respected() -> None:
    plan = _plan(_removals(3))
    assert check_brake(plan, 10, BrakeConfig(max_fraction=0.5)) is None
    assert check_brake(plan, 1000, BrakeConfig(max_count=2)) is not None


def test_fingerprint_ignores_op_ids_but_not_content() -> None:
    base = _plan(AddTracks("a", ("t1", "t2")), _removals(2, "r"))
    same = _plan(AddTracks("zzz", ("t1", "t2")), _removals(2, "yyy"))
    assert fingerprint(base) == fingerprint(same)
    assert len(fingerprint(base)) == 64

    variants = [
        _plan(AddTracks("a", ("t1", "t3")), _removals(2)),
        _plan(AddTracks("a", ("t2", "t1")), _removals(2)),
        _plan(
            AddTracks("a", ("t1", "t2")),
            RemoveTracks("r", (Removal("t0", 0), Removal("t1", 5))),
        ),
        _plan(_removals(2), AddTracks("a", ("t1", "t2"))),
        _plan(CreatePlaylist("a", PlaylistDraft(name="n"))),
        _plan(UpdateMetadata("a", PlaylistDraft(name="n"))),
        _plan(UpdateMetadata("a", PlaylistDraft(name="n", description="d"))),
        _plan(UpdateMetadata("a", PlaylistDraft(name="n", cover_url="u"))),
    ]
    prints = {fingerprint(p) for p in [base, *variants]}
    assert len(prints) == len(variants) + 1
    assert fingerprint(replace(base, member_id="other")) == fingerprint(base)


def test_fingerprint_golden_hash() -> None:
    # Pinned: stored approvals and BRAKE items carry this value, so a change
    # to the hashing must be deliberate.
    plan = _plan(
        UpdateMetadata("o0", PlaylistDraft(name="Mix", description="d")),
        AddTracks("o1", ("A", "B")),
        Reorder("o2", ("B", "A", "X")),
        RemoveTracks("o3", (Removal("X", 2),)),
    )
    assert fingerprint(plan) == (
        "13558dd18424456c7aad7387e46a9a0997b6cbaaaf75e4f64c5c2b9d7d0b0e9e"
    )
