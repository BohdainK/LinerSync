from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from linersync.adapters.sqlite.codec import decode, dumps, encode, loads
from linersync.application.config import BreakerConfig, EngineConfig
from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.matching.types import NeedsAttention, ScoredCandidate
from linersync.domain.operations import (
    AddTracks,
    ChangeNotMade,
    CreatePlaylist,
    MemberPlan,
    Operation,
    Removal,
    RemoveTracks,
    Reorder,
    SyncPlan,
    UnresolvedEntry,
    UpdateMetadata,
)
from linersync.domain.playlists import (
    PlaylistDraft,
    PlaylistEntry,
    PlaylistMeta,
    PlaylistSnapshot,
)
from linersync.domain.runs import (
    MemberResult,
    MemberStatus,
    RunKind,
    RunStatus,
    SyncRun,
    Trigger,
    VerificationMismatch,
)
from linersync.domain.sync import (
    Direction,
    MemberRole,
    MemberState,
    SyncedEntry,
    SyncGroup,
    SyncMember,
    SyncMode,
    SyncStatus,
)
from linersync.domain.tracks import ServiceId, Track, TrackKey
from tests.factories import NOW, track

GOLDEN = Path(__file__).parent.parent.parent / "fixtures/storage/sync_run_v1.json"


def _sample_run() -> SyncRun:
    draft = PlaylistDraft("Mix", "desc", None)
    src = track("spotify", "t1", album="A", isrc="X1")
    cand = ScoredCandidate(track("apple_music", "c1"), 0.5, ("title", "artist"))
    plan = SyncPlan(
        sync_id="s1",
        member_plans=(
            MemberPlan(
                member_id="dst0",
                operations=(
                    CreatePlaylist("o1", draft),
                    UpdateMetadata("o2", draft),
                    AddTracks("o3", ("a", "b")),
                    Reorder("o4", ("b", "a")),
                    RemoveTracks("o5", (Removal("z", 3),)),
                ),
                not_made=(ChangeNotMade("add", None, "nope"),),
                expected_after=("b", "a"),
                synced=(SyncedEntry(TrackKey(ServiceId("spotify"), "t1"), "a"),),
                draft=draft,
            ),
        ),
        unresolved=(
            UnresolvedEntry("dst0", src, 2, NeedsAttention(("close",), (cand,))),
        ),
    )
    return SyncRun(
        run_id="r1",
        sync_id="s1",
        kind=RunKind.SYNC,
        trigger=Trigger.MANUAL,
        dry_run=False,
        status=RunStatus.PARTIAL,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=5),
        plan=plan,
        members=(
            MemberResult(
                "dst0",
                MemberStatus.FAILED,
                applied_op_ids=("o1",),
                mismatch=VerificationMismatch("dst0", ("a",), ("b",)),
                verified_after=("b",),
            ),
        ),
    )


def test_round_trip_sync_run_with_plan() -> None:
    run = _sample_run()
    assert loads(SyncRun, dumps(SyncRun, run)) == run


def test_union_members_with_same_fields_stay_distinct() -> None:
    op = UpdateMetadata("o", PlaylistDraft("n"))
    out = decode(Operation, encode(Operation, op))
    assert isinstance(out, UpdateMetadata)
    assert out == op


_text = st.text(max_size=8)
_dt = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
)
_tracks = st.builds(
    Track,
    service=st.sampled_from([ServiceId("spotify"), ServiceId("apple_music")]),
    track_id=_text,
    title=_text,
    artists=st.lists(_text, max_size=3).map(tuple),
    album=st.none() | _text,
    duration_ms=st.none() | st.integers(0, 10**7),
    isrc=st.none() | _text,
    artwork_hash=st.none() | _text,
    url=st.none() | _text,
)
_cands = st.lists(
    st.builds(
        ScoredCandidate,
        track=_tracks,
        confidence=st.floats(0, 1),
        reasons=st.lists(_text, max_size=2).map(tuple),
    ),
    max_size=3,
).map(tuple)
_not_made = st.lists(
    st.builds(
        ChangeNotMade,
        kind=st.sampled_from(
            ["add", "remove", "reorder", "metadata", "create", "delete"]
        ),
        track_id=st.none() | _text,
        detail=_text,
    ),
    max_size=3,
).map(tuple)
_states = st.builds(
    MemberState,
    entries=st.lists(
        st.builds(
            SyncedEntry,
            source_key=st.builds(
                TrackKey, service=st.just(ServiceId("spotify")), track_id=_text
            ),
            target_track_id=_text,
        ),
        max_size=3,
    ).map(tuple),
    draft=st.builds(
        PlaylistDraft, name=_text, description=_text, cover_url=st.none() | _text
    ),
    version_token=st.none() | _text,
    synced_at=_dt,
)
_groups = st.builds(
    lambda state, schedule, status: SyncGroup(
        "s1",
        "Mix",
        Direction.ONE_WAY,
        (
            SyncMember("src", ServiceId("spotify"), "P1", MemberRole.SOURCE),
            SyncMember(
                "dst0",
                ServiceId("apple_music"),
                "A1",
                MemberRole.DESTINATION,
                SyncMode.ADD_ONLY,
                state,
            ),
        ),
        schedule,
        status,
    ),
    state=_states,
    schedule=st.none() | _text,
    status=st.sampled_from(SyncStatus),
)
_attention = st.builds(
    AttentionItem,
    item_id=_text,
    kind=st.sampled_from(AttentionKind),
    created_at=_dt,
    detail=_text,
    source=st.none() | _tracks,
    service=st.none() | st.just(ServiceId("spotify")),
    candidates=_cands,
    not_made=_not_made,
    resolved_at=st.none() | _dt,
)
_snapshots = st.builds(
    PlaylistSnapshot,
    meta=st.builds(
        PlaylistMeta,
        service=st.just(ServiceId("spotify")),
        playlist_id=_text,
        name=_text,
        track_count=st.none() | st.integers(0, 1000),
        owned=st.booleans(),
    ),
    entries=st.lists(
        st.builds(PlaylistEntry, track=_tracks, added_at=st.none() | _dt), max_size=3
    ).map(tuple),
    expected_total=st.none() | st.integers(0, 10),
    taken_at=_dt,
)
_configs = st.builds(
    EngineConfig,
    breaker=st.builds(
        BreakerConfig,
        failure_threshold=st.integers(1, 9),
        cooldown=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=3)),
    ),
    max_replans=st.integers(0, 5),
)


@given(_groups)
def test_round_trip_group(g: SyncGroup) -> None:
    assert loads(SyncGroup, dumps(SyncGroup, g)) == g


@given(_attention)
def test_round_trip_attention(a: AttentionItem) -> None:
    assert loads(AttentionItem, dumps(AttentionItem, a)) == a


@given(_snapshots)
def test_round_trip_snapshot(s: PlaylistSnapshot) -> None:
    assert loads(PlaylistSnapshot, dumps(PlaylistSnapshot, s)) == s


@given(_configs)
def test_round_trip_engine_config(c: EngineConfig) -> None:
    assert loads(EngineConfig, dumps(EngineConfig, c)) == c


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        encode(datetime, datetime(2026, 1, 1))


def test_unknown_keys_ignored_missing_defaults_filled() -> None:
    out = decode(
        PlaylistMeta, {"service": "s", "playlist_id": "p", "name": "n", "extra": 1}
    )
    assert out == PlaylistMeta(ServiceId("s"), "p", "n")


def test_missing_required_key_raises() -> None:
    with pytest.raises(ValueError):
        decode(PlaylistMeta, {"service": "s"})


def test_golden_sync_run_v1() -> None:
    assert loads(SyncRun, GOLDEN.read_text()) == _sample_run()


# -- strict decode (imports only) ----------------------------------------------
@dataclass(frozen=True)
class _Strict:
    name: str
    count: int = 0
    ratio: float = 0.0
    flag: bool = False
    note: str | None = None
    tags: tuple[str, ...] = ()
    extra: dict[str, int] = field(default_factory=dict)
    kind: Literal["a", "b"] = "a"
    when: datetime | None = None
    took: timedelta = timedelta()


@dataclass(frozen=True)
class _Checked:
    value: str

    def __post_init__(self) -> None:
        if self.value == "boom":
            raise RuntimeError("boom")


def test_strict_decode_accepts_well_typed_data() -> None:
    data = {
        "name": "n",
        "count": 2,
        "ratio": 1,
        "flag": True,
        "note": None,
        "tags": ["x"],
        "extra": {"k": 1},
        "kind": "b",
        "when": "2026-01-01T00:00:00+00:00",
        "took": 1.5,
    }
    out = decode(_Strict, data, strict=True)
    assert out == _Strict(
        "n", 2, 1.0, True, None, ("x",), {"k": 1}, "b", NOW, timedelta(seconds=1.5)
    )


@pytest.mark.parametrize(
    "data",
    [
        {"name": None},
        {"name": 1},
        {"name": ["x"]},
        {"name": "n", "count": "1"},
        {"name": "n", "count": True},
        {"name": "n", "count": 1.5},
        {"name": "n", "ratio": True},
        {"name": "n", "ratio": "1"},
        {"name": "n", "flag": 1},
        {"name": "n", "note": 3},
        {"name": "n", "tags": {"a": 1}},
        {"name": "n", "tags": "ab"},
        {"name": "n", "tags": [None]},
        {"name": "n", "extra": [1]},
        {"name": "n", "extra": {"k": "1"}},
        {"name": "n", "kind": "c"},
        {"name": "n", "when": 5},
        {"name": "n", "took": True},
        {"name": "n", "took": None},
        ["n"],
    ],
)
def test_strict_decode_rejects_type_damage(data: object) -> None:
    with pytest.raises(ValueError):
        decode(_Strict, data, strict=True)


def test_strict_decode_turns_any_exception_into_value_error() -> None:
    with pytest.raises(AttributeError):
        decode(dict[str, int], [1])  # lenient decode keeps its old behaviour
    with pytest.raises(ValueError):
        decode(dict[str, int], [1], strict=True)
    with pytest.raises(ValueError, match="boom"):
        decode(_Checked, {"value": "boom"}, strict=True)


def test_lenient_decode_is_unchanged() -> None:
    assert decode(_Strict, {"name": None, "count": "1"}) == _Strict(None, "1")  # type: ignore[arg-type]


def test_track_artwork_url_round_trips_and_old_bodies_default_to_none() -> None:
    t = Track(ServiceId("spotify"), "t1", "Song", ("A",), artwork_url="https://i/a")
    assert loads(Track, dumps(Track, t)).artwork_url == "https://i/a"
    old = '{"service":"spotify","track_id":"t1","title":"Song","artists":["A"]}'
    assert loads(Track, old).artwork_url is None
