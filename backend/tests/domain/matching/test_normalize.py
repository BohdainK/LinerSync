import pytest

from linersync.domain.matching.normalize import (
    IGNORED_VERSIONS,
    VERSION_TAGS,
    normalize_artists,
    normalize_text,
    parse_title,
)


@pytest.mark.parametrize(
    ("raw", "base", "versions", "featured"),
    [
        ("Song Name - 2011 Remaster", "song name", {"remaster"}, ()),
        ("Song (Live at Wembley)", "song", {"live"}, ()),
        ("Song (feat. X & Y)", "song", set(), ("x", "y")),
        ("Song [Radio Edit]", "song", {"radio edit"}, ()),
        ("Beyoncé's Song!", "beyonces song", set(), ()),
        ("Song (Love Theme)", "song love theme", set(), ()),
        ("Song (Remastered)", "song", {"remaster"}, ()),
        ("Song - Radio Edit", "song", {"radio edit"}, ()),
        ("Song (Live) [Remastered 2011]", "song", {"live", "remaster"}, ()),
        ("Song - feat. X", "song", set(), ("x",)),
        ("Intro - Part 1 - Remastered 2009", "intro part 1", {"remaster"}, ()),
        ("Wild - Live and Let Die", "wild live and let die", set(), ()),
        ("Song - Live at Wembley", "song", {"live"}, ()),
        ("Song (Alive Again)", "song alive again", set(), ()),
    ],
)
def test_parse_title(
    raw: str, base: str, versions: set[str], featured: tuple[str, ...]
) -> None:
    parsed = parse_title(raw)
    assert parsed.base == base
    assert parsed.versions == frozenset(versions)
    assert parsed.featured == featured


def test_normalize_text() -> None:
    assert normalize_text("  Rock & Roll,  Baby! ") == "rock and roll baby"


def test_constants() -> None:
    assert IGNORED_VERSIONS <= VERSION_TAGS
    assert "radio edit" in VERSION_TAGS


def test_normalize_artists_splits_and_drops_various() -> None:
    assert normalize_artists(["A & B", "Various Artists"], ["C"]) == {"a", "b", "c"}


def test_normalize_artists_feat_split() -> None:
    assert normalize_artists(["A feat. B ft. C", "D featuring E, F"]) == {
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
    }
