import pytest

from linersync.domain.matching.scoring import decide_match, score_candidate
from linersync.domain.matching.types import MatchConfig, Matched, NeedsAttention
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
