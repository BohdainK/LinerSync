from datetime import UTC, datetime

import pytest

from linersync.adapters.connectors.qobuz.parse import (
    SERVICE,
    artwork_url,
    parse_account,
    parse_playlist_meta,
    parse_track,
    parse_tracks_page,
)
from tests.adapters.connectors.shapes import load_fixture

ME = "1111111"


def test_track_from_playlist_item() -> None:
    item = load_fixture("qobuz", "playlist_get")["tracks"]["items"][0]
    t = parse_track(item, artwork_hash="h")
    assert (t.service, t.track_id, t.title) == (SERVICE, "64868955", "Alpha")
    assert t.artists == ("Alpha Band",)
    assert (t.album, t.duration_ms, t.isrc) == ("First", 214_000, "GBUM71029604")
    assert t.artwork_hash == "h"
    assert t.url == "https://open.qobuz.com/track/64868955"


def test_track_version_and_featured_artist() -> None:
    item = load_fixture("qobuz", "playlist_get")["tracks"]["items"][1]
    t = parse_track(item)
    assert t.title == "Bravo (2011 Remaster)"
    assert t.artists == ("Bravo Band", "Guest Singer")


def test_track_without_performers_string() -> None:
    t = parse_track({"id": 5, "title": "Solo", "performer": {"name": "Solo Act"}})
    assert (t.artists, t.album, t.duration_ms, t.isrc) == (
        ("Solo Act",),
        None,
        None,
        None,
    )


def test_performers_skip_non_artist_roles_and_repeats() -> None:
    t = parse_track(
        {
            "id": 5,
            "title": "Song",
            "performer": {"name": "Main"},
            "performers": "Main, MainArtist - Main, FeaturedArtist - Writer, Composer",
        }
    )
    assert t.artists == ("Main",)


def test_performer_names_with_commas_stay_whole() -> None:
    t = parse_track(
        {
            "id": 6,
            "title": "September",
            "performer": {"name": "Earth, Wind & Fire"},
            "performers": (
                "Earth, Wind & Fire, MainArtist - Maurice White, Composer, Producer"
                " - Guest, McKay, FeaturedArtist - Al McKay, ComposerLyricist, Guitar"
            ),
        }
    )
    assert t.artists == ("Earth, Wind & Fire", "Guest, McKay")


def test_artwork_prefers_small_cover() -> None:
    item = load_fixture("qobuz", "playlist_get")["tracks"]["items"][0]
    assert artwork_url(item) == (
        "https://static.qobuz.com/images/covers/ab/c1/abc123def_230.jpg"
    )
    assert artwork_url({"id": 1, "title": "x"}) is None


def test_playlist_meta_owned() -> None:
    doc = load_fixture("qobuz", "user_playlists")
    mine, theirs = (parse_playlist_meta(p, ME) for p in doc["playlists"]["items"])
    assert (mine.playlist_id, mine.name, mine.description) == (
        "8501234",
        "Road Trip",
        "Songs for the road",
    )
    assert (mine.owned, mine.track_count, mine.version_token) == (True, 3, None)
    assert mine.cover_url == (
        "https://static.qobuz.com/images/covers/ab/c1/abc123def_300.jpg"
    )
    assert theirs.owned is False
    assert theirs.readable is True


def test_playlist_meta_unknown_user_counts_as_owned() -> None:
    doc = load_fixture("qobuz", "user_playlists")
    assert parse_playlist_meta(doc["playlists"]["items"][1], None).owned is True


def test_tracks_page() -> None:
    raw, total = parse_tracks_page(load_fixture("qobuz", "playlist_get"))
    assert total == 3
    assert [(r.track_id, r.playlist_track_id) for r in raw] == [
        ("64868955", "3775432101"),
        ("64868956", "3775432102"),
        ("64868955", "3775432103"),
    ]
    assert raw[0].added_at == datetime.fromtimestamp(1720000000, UTC)


def test_tracks_page_without_tracks_block() -> None:
    assert parse_tracks_page({"id": 1}) == ([], None)


def test_tracks_page_item_without_occurrence_id_is_not_understood() -> None:
    with pytest.raises(KeyError):
        parse_tracks_page({"tracks": {"total": 1, "items": [{"id": 1}]}})


def test_account() -> None:
    assert parse_account(load_fixture("qobuz", "user_playlists")) == (ME, "sam")
    assert parse_account({"playlists": {}}) == (None, None)
