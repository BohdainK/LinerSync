from linersync.domain.matching.types import MatchConfig
from linersync.domain.operations import AddTracks, MemberPlan
from linersync.domain.planning.substitutes import accept_substitutes
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.sync import SyncedEntry
from linersync.domain.tracks import Track
from tests.factories import track

CFG = MatchConfig()
SOURCES = {t.key: t for t in (track("spotify", s) for s in "abc")}


def _plan(added: tuple[str, ...] = ("B", "C")) -> MemberPlan:
    return MemberPlan(
        member_id="dst0",
        operations=(AddTracks("dst0:0", added),),
        not_made=(),
        expected_after=("A", "B", "C"),
        synced=tuple(SyncedEntry(track("spotify", s).key, s.upper()) for s in "abc"),
        draft=PlaylistDraft("Mix", ""),
    )


def _apple(*ids_titles: tuple[str, str]) -> list[Track]:
    return [track("apple_music", i, title=t) for i, t in ids_titles]


def test_added_song_under_another_id_is_accepted() -> None:
    actual = _apple(("A", "a"), ("B2", "b"), ("C", "c"))
    fixed = accept_substitutes(_plan(), actual, SOURCES, CFG)
    assert fixed is not None
    assert fixed.expected_after == ("A", "B2", "C")
    assert [e.target_track_id for e in fixed.synced] == ["A", "B2", "C"]


def test_a_song_that_was_already_there_is_not_accepted() -> None:
    actual = _apple(("A2", "a"), ("B", "b"), ("C", "c"))
    assert accept_substitutes(_plan(), actual, SOURCES, CFG) is None


def test_a_different_song_is_not_accepted() -> None:
    actual = _apple(("A", "a"), ("Z", "zebra"), ("C", "c"))
    assert accept_substitutes(_plan(), actual, SOURCES, CFG) is None


def test_a_reorder_is_not_a_substitute() -> None:
    actual = _apple(("A", "a"), ("C", "c"), ("B", "b"))
    assert accept_substitutes(_plan(), actual, SOURCES, CFG) is None


def test_a_missing_song_is_not_a_substitute() -> None:
    actual = _apple(("A", "a"), ("C", "c"))
    assert accept_substitutes(_plan(), actual, SOURCES, CFG) is None


def test_unknown_source_song_is_not_accepted() -> None:
    actual = _apple(("A", "a"), ("B2", "b"), ("C", "c"))
    assert accept_substitutes(_plan(), actual, {}, CFG) is None
