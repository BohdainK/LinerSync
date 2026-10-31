from datetime import UTC, datetime

import pytest

from linersync.adapters.connectors.tidal.parse import (
    SERVICE,
    Included,
    artwork_url,
    iso_duration_ms,
    parse_items_page,
    parse_playlist_meta,
    parse_track,
    track_ref,
)
from tests.adapters.connectors.shapes import load_fixture


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT3M5S", 185_000),
        ("PT1H2M3.5S", 3_723_500),
        ("PT4M", 240_000),
        ("PT0S", 0),
        ("3:05", None),
        ("P1D", None),
        ("", None),
        (None, None),
    ],
)
def test_iso_duration_ms(value: str | None, expected: int | None) -> None:
    assert iso_duration_ms(value) == expected


def test_track_ref() -> None:
    assert track_ref("video:9") == ("videos", "9")
    assert track_ref("9") == ("tracks", "9")


def test_items_page_parses_track_video_and_unavailable() -> None:
    doc = load_fixture("tidal", "playlist_items")
    items, next_link = parse_items_page(doc)
    assert next_link is None
    assert [(i.ref_type, i.ref_id, i.item_id) for i in items] == [
        ("tracks", "75413016", "item-a"),
        ("videos", "1234567", "item-b"),
        ("tracks", "99999999", "item-c"),
    ]
    assert items[0].added_at == datetime(2026, 10, 2, 9, tzinfo=UTC)
    assert items[0].resource is not None
    assert items[2].resource is None


def test_parse_track_maps_fields() -> None:
    doc = load_fixture("tidal", "playlist_items")
    items, _ = parse_items_page(doc)
    assert items[0].resource is not None
    t = parse_track(items[0].resource, Included(doc), "hash")
    assert t.service == SERVICE
    assert t.track_id == "75413016"
    assert t.title == "Song (Remastered)"
    assert t.artists == ("Second Artist", "First Artist")
    assert t.album == "The Album"
    assert t.duration_ms == 185_000
    assert t.isrc == "GBAYE0601498"
    assert t.artwork_hash == "hash"
    assert t.url == "https://tidal.com/browse/track/75413016"


def test_parse_video_gets_prefixed_id() -> None:
    doc = load_fixture("tidal", "playlist_items")
    items, _ = parse_items_page(doc)
    assert items[1].resource is not None
    t = parse_track(items[1].resource, Included(doc))
    assert t.track_id == "video:1234567"
    assert t.isrc is None
    assert t.album is None
    assert t.artists == ("First Artist",)
    assert t.url == "https://tidal.com/browse/video/1234567"


def test_artwork_url_is_smallest_album_file() -> None:
    doc = load_fixture("tidal", "playlist_items")
    items, _ = parse_items_page(doc)
    assert items[0].resource is not None
    url = artwork_url(items[0].resource, Included(doc))
    assert url == "https://resources.tidal.com/images/al1/80x80.jpg"
    assert items[1].resource is not None
    assert artwork_url(items[1].resource, Included(doc)) is None


def test_playlist_meta() -> None:
    doc = load_fixture("tidal", "playlist")
    meta = parse_playlist_meta(doc["data"], Included(doc))
    assert meta.service == SERVICE
    assert meta.playlist_id == "550e8400-e29b-41d4-a716-446655440000"
    assert meta.name == "Road trip"
    assert meta.description == "Songs for the car"
    assert meta.version_token == "2026-10-29T17:45:12.123Z"
    assert meta.track_count == 3
    assert meta.owned and meta.readable
    assert meta.cover_url == "https://resources.tidal.com/images/pl1/1080x1080.jpg"


def test_playlist_meta_without_description_or_cover() -> None:
    doc = load_fixture("tidal", "playlists")
    meta = parse_playlist_meta(doc["data"][1], Included(doc))
    assert meta.description == ""
    assert meta.cover_url is None
    assert meta.track_count == 0


def test_search_document_parses_through_included() -> None:
    doc = load_fixture("tidal", "search_tracks")
    included = Included(doc)
    resource = included.get("tracks", doc["data"][0]["id"])
    assert resource is not None
    assert parse_track(resource, included).artists == ("First Artist",)


def test_included_merges_pages() -> None:
    included = Included(load_fixture("tidal", "playlist"))
    included.add(load_fixture("tidal", "playlist_items"))
    assert included.get("artworks", "art-pl-1") is not None
    assert included.get("artists", "a1") is not None
