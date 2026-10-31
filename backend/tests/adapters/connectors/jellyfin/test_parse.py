from typing import Any

from linersync.adapters.connectors.jellyfin.parse import (
    SERVICE,
    artwork_url,
    parse_playlist_meta,
    parse_track,
)
from tests.adapters.connectors.shapes import load_fixture

BASE = "https://jf.example/jellyfin"


def _song() -> dict[str, Any]:
    item: dict[str, Any] = load_fixture("jellyfin", "playlist_items")["Items"][0]
    return item


def test_parse_track_maps_fields() -> None:
    t = parse_track(_song(), BASE)
    assert t is not None
    assert t.service == SERVICE
    assert t.track_id == "00000000000000000000000000000001"
    assert t.title == "Alpha"
    assert t.artists == ("Alpha Band",)
    assert t.album == "First"
    assert t.duration_ms == 200_000
    assert t.isrc is None
    assert t.url == f"{BASE}/web/#/details?id=00000000000000000000000000000001"


def test_parse_track_skips_non_audio() -> None:
    assert parse_track({**_song(), "Type": "Video"}, BASE) is None


def test_parse_track_falls_back_to_album_artist() -> None:
    t = parse_track({**_song(), "Artists": [], "AlbumArtist": "Solo"}, BASE)
    assert t is not None
    assert t.artists == ("Solo",)


def test_parse_track_without_runtime_has_no_duration() -> None:
    item = _song()
    del item["RunTimeTicks"]
    t = parse_track(item, BASE)
    assert t is not None
    assert t.duration_ms is None


def test_artwork_prefers_item_tag_over_album() -> None:
    assert artwork_url(_song(), BASE) == (
        f"{BASE}/Items/00000000000000000000000000000001/Images/Primary"
        "?tag=55555555555555555555555555555555&maxWidth=300"
    )
    album_only = {**_song(), "ImageTags": {}}
    assert artwork_url(album_only, BASE) == (
        f"{BASE}/Items/33333333333333333333333333333333/Images/Primary"
        "?tag=44444444444444444444444444444444&maxWidth=300"
    )
    assert artwork_url({**album_only, "AlbumPrimaryImageTag": None}, BASE) is None


def test_parse_playlist_meta() -> None:
    item = load_fixture("jellyfin", "items_playlists")["Items"][0]
    meta = parse_playlist_meta(item, BASE)
    assert meta.service == SERVICE
    assert meta.playlist_id == "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert meta.name == "Road Trip"
    assert meta.description == "Long drives"
    assert meta.track_count == 3
    assert meta.version_token is None
    assert meta.cover_url == (
        f"{BASE}/Items/a1b2c3d4e5f60718293a4b5c6d7e8f90/Images/Primary"
        "?tag=9f8e7d6c5b4a39281706f5e4d3c2b1a0&maxWidth=300"
    )
