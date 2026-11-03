from dataclasses import replace

import pytest

from linersync.domain.matching.scoring import decide_match, score_candidate
from linersync.domain.matching.types import MatchConfig, Matched, NeedsAttention
from linersync.domain.tracks import Track
from tests.factories import track

CFG = MatchConfig()


def test_isrc_match_is_confident() -> None:
    src = track("spotify", "s", title="Don't Stop", isrc="USABC1234567")
    cand = track("apple_music", "c", title="Dont Stop!", isrc="usabc1234567")
    c = score_candidate(src, cand, CFG)
    assert c.confidence >= 0.97 and "ISRC matches" in c.reasons


def test_spec_example_reasons() -> None:
    src = track("spotify", "s", title="Song", duration_ms=200_000, artwork_hash="00")
    cand = track(
        "apple_music", "c", title="Song", duration_ms=214_000, artwork_hash="ff"
    )
    c = score_candidate(src, cand, CFG)
    assert c.reasons == ("no ISRC", "duration differs by 14s", "different artwork")
    assert c.confidence < 0.85


def test_same_artwork_and_isrc_differs() -> None:
    src = track("spotify", "s", title="Song", isrc="A", artwork_hash="0f")
    cand = track("apple_music", "c", title="Song", isrc="B", artwork_hash="0e")
    c = score_candidate(src, cand, CFG)
    assert c.reasons == ("ISRC differs", "same artwork")
    assert c.confidence == pytest.approx(1.0)


def test_live_version_penalised() -> None:
    src = track("spotify", "s", title="Song")
    cand = track("apple_music", "c", title="Song (Live)")
    c = score_candidate(src, cand, CFG)
    assert c.confidence < 0.85 and "version differs (live)" in c.reasons


def test_remaster_not_penalised() -> None:
    src = track("spotify", "s", title="Song")
    cand = track("apple_music", "c", title="Song - 2011 Remaster")
    assert score_candidate(src, cand, CFG).confidence >= 0.85


def test_featured_artist_in_title_equivalent() -> None:
    src = track("spotify", "s", title="Song (feat. B)", artists=("A",))
    cand = track("apple_music", "c", title="Song", artists=("A", "B"))
    assert score_candidate(src, cand, CFG).confidence >= 0.85


def test_decide_accepts_clear_winner() -> None:
    src = track("spotify", "s", title="Song")
    good = track("apple_music", "good", title="Song")
    poor = track("apple_music", "poor", title="Other Thing", artists=("Nobody",))
    result = decide_match(src, [poor, good], CFG)
    assert isinstance(result, Matched)
    assert result.target_track_id == "good"


def test_decide_ambiguous_goes_to_attention() -> None:
    src = track("spotify", "s", title="Song")
    a = track("apple_music", "a", title="Song")
    b = track("apple_music", "b", title="Song")
    result = decide_match(src, [a, b], CFG)
    assert isinstance(result, NeedsAttention)
    assert [c.track.track_id for c in result.candidates] == ["a", "b"]


def test_decide_isrc_top_accepted_despite_margin() -> None:
    src = track("spotify", "s", title="Song", isrc="X1")
    a = track("apple_music", "a", title="Song", isrc="X1")
    b = track("apple_music", "b", title="Song", isrc="X1")
    assert isinstance(decide_match(src, [a, b], CFG), Matched)


def test_decide_caps_candidates() -> None:
    src = track("spotify", "s", title="Song")
    cands = [track("apple_music", f"c{i}", title="Song") for i in range(4)]
    result = decide_match(src, cands, MatchConfig(max_candidates=2))
    assert isinstance(result, NeedsAttention) and len(result.candidates) == 2


def test_decide_no_candidates() -> None:
    src = track("spotify", "s")
    assert decide_match(src, [], CFG) == NeedsAttention(
        ("no results on this service",), ()
    )


def test_threshold_configurable() -> None:
    src = track("spotify", "s", title="Song", duration_ms=200_000)
    cand = track("apple_music", "c", title="Song", duration_ms=208_000)
    assert isinstance(decide_match(src, [cand], CFG), Matched)
    strict = MatchConfig(accept_threshold=0.99)
    assert isinstance(decide_match(src, [cand], strict), NeedsAttention)


def test_isrc_floor_lifts_penalised_match() -> None:
    src = track("spotify", "s", title="Song", isrc="X1", artwork_hash="00")
    cand = track(
        "apple_music",
        "c",
        title="Song (Live)",
        isrc="x1",
        duration_ms=214_000,
        artwork_hash="ff",
    )
    c = score_candidate(src, cand, CFG)
    assert c.confidence == pytest.approx(0.97)
    assert c.reasons == (
        "ISRC matches",
        "version differs (live)",
        "duration differs by 14s",
        "different artwork",
    )


def test_confidence_clamped_to_one() -> None:
    src = track("spotify", "s", title="Song", artwork_hash="0f")
    cand = track("apple_music", "c", title="Song", artwork_hash="0f")
    assert score_candidate(src, cand, CFG).confidence == 1.0


def test_unparsable_artwork_hash_is_treated_as_missing() -> None:
    src = track("spotify", "s", title="Song", artwork_hash="not-hex")
    cand = track("apple_music", "c", title="Song", artwork_hash="0f")
    c = score_candidate(src, cand, CFG)
    assert c.reasons == ("no ISRC",)
    plain = score_candidate(
        track("spotify", "s", title="Song"),
        track("apple_music", "c", title="Song"),
        CFG,
    )
    assert c.confidence == plain.confidence


def test_isrc_tie_prefers_the_source_album_over_a_compilation() -> None:
    # Seen live: Apple lists the same ISRC on a compilation first; every copy
    # scores 1.0, so the album must break the tie.
    def copy(track_id: str, album: str) -> Track:
        return track(
            "apple_music",
            track_id,
            title="The Boat I Row",
            artists=("Tame Impala",),
            album=album,
            isrc="AUUM72100333",
        )

    src = track(
        "spotify",
        "s",
        title="The Boat I Row",
        artists=("Tame Impala",),
        album="The Slow Rush B-Sides & Remixes",
        isrc="AUUM72100333",
    )
    compilation = copy("comp", "Frühstücksmusik Herbst / Winter 2022 / 2023")
    original = copy("orig", "The Slow Rush (B-Sides & Remixes)")
    for candidates in ([compilation, original], [original, compilation]):
        d = decide_match(src, candidates, CFG)
        assert isinstance(d, Matched) and d.target_track_id == "orig"


def test_album_tiebreak_orders_attention_candidates() -> None:
    src = track("spotify", "s", title="Song", album="Debut")
    other = track("apple_music", "a", title="Song", album="Hits 2023")
    same = track("apple_music", "b", title="Song", album="Debut")
    d = decide_match(src, [other, same], CFG)
    assert isinstance(d, NeedsAttention)
    assert [c.track.track_id for c in d.candidates] == ["b", "a"]


# -- ISRC search hits (services that index ISRCs but never return them) -------


def _hit(**overrides: object) -> Track:
    fields: dict[str, object] = {
        "title": "Song",
        "artists": ("A",),
        "duration_ms": 201_000,
    }
    fields.update(overrides)
    return replace(track("youtube_music", "v", **fields), found_by_isrc=True)


def test_agreeing_isrc_hit_is_floored() -> None:
    # Two credited artists on one side, one on the other: a weak base score.
    src = track("spotify", "s", title="Song", artists=("A", "B"), isrc="USABC1234567")
    c = score_candidate(src, _hit(), CFG)
    assert c.confidence == pytest.approx(0.95)
    assert "found by ISRC search" in c.reasons


def test_isrc_hit_that_disagrees_gets_no_floor() -> None:
    src = track("spotify", "s", title="Song", artists=("A", "B"), isrc="USABC1234567")
    c = score_candidate(src, _hit(title="Another Tune"), CFG)
    assert c.confidence < 0.85
    assert "found by ISRC search" not in c.reasons


def test_isrc_hit_needs_source_isrc() -> None:
    src = track("spotify", "s", title="Song", artists=("A", "B"))
    c = score_candidate(src, _hit(), CFG)
    assert "found by ISRC search" not in c.reasons
    assert c.confidence < 0.95


def test_isrc_hit_far_duration_gets_no_floor() -> None:
    src = track("spotify", "s", title="Song", artists=("A", "B"), isrc="USABC1234567")
    c = score_candidate(src, _hit(duration_ms=205_000), CFG)
    assert "found by ISRC search" not in c.reasons


def test_isrc_hit_other_version_gets_no_floor() -> None:
    src = track("spotify", "s", title="Song", artists=("A",), isrc="USABC1234567")
    c = score_candidate(src, _hit(title="Song (Live)"), CFG)
    assert "found by ISRC search" not in c.reasons


def test_found_by_isrc_ignored_in_equality() -> None:
    t = track("youtube_music", "v")
    assert replace(t, found_by_isrc=True) == t


def test_two_agreeing_isrc_hits_pick_the_source_album() -> None:
    # One recording on two releases: both are found by the ISRC search.
    src = track(
        "spotify", "s", title="Song", artists=("A", "B"), album="Revolver", isrc="X1"
    )
    revolver = replace(_hit(album="Revolver"), track_id="v1")
    soundtrack = replace(_hit(album="Soundtrack"), track_id="v2")
    result = decide_match(src, [soundtrack, revolver], CFG)
    assert isinstance(result, Matched)
    assert result.target_track_id == "v1"


def test_isrc_hit_beside_a_close_non_isrc_candidate_still_needs_margin() -> None:
    # The exemption only covers two hits of the same recording.
    src = track("spotify", "s", title="Song", artists=("A", "B"), isrc="X1")
    close = track(
        "youtube_music", "o", title="Song", artists=("A", "B"), duration_ms=205_000
    )
    result = decide_match(src, [_hit(), close], CFG)
    assert isinstance(result, NeedsAttention)


def test_artwork_url_ignored_in_equality() -> None:
    t = track("youtube_music", "v")
    assert replace(t, artwork_url="https://i/a") == t
