from typing import Any

from linersync.adapters.connectors.apple_music.parse import (
    APPLE,
    add_tracks_body,
    artwork_url,
    parse_catalog_song,
    parse_library_playlist,
    parse_library_song,
)
from tests.adapters.connectors.shapes import load_fixture


def _fx(name: str) -> Any:
    return load_fixture("apple_music", name)


def test_parse_library_song_uses_catalog_id() -> None:
    obj = _fx("library_playlist_tracks")["data"][0]
    t = parse_library_song(obj, isrc="DEUM72005143", url="https://x", artwork_hash="h")
    assert t.service == APPLE
    assert t.track_id == "1542568135"
    assert t.title == "Diaries (From Home)"
    assert t.artists == ("Dirk Maassen",)
    assert t.album == "Echoes"
    assert t.duration_ms == 169813
    assert (t.isrc, t.url, t.artwork_hash) == ("DEUM72005143", "https://x", "h")


def test_parse_library_song_without_catalog_keeps_library_id() -> None:
    t = parse_library_song(_fx("library_playlist_tracks")["data"][1])
    assert t.track_id == "i.abc123"
    assert t.isrc is None and t.url is None and t.artwork_hash is None


def test_parse_catalog_song_isrc_and_url() -> None:
    t = parse_catalog_song(_fx("catalog_songs")["data"][0], artwork_hash="h")
    assert t.track_id == "1542568135"
    assert t.isrc == "DEUM72005143"
    assert t.url is not None and t.url.endswith("?i=1542568135")
    assert t.artwork_hash == "h"
    search = _fx("catalog_search")["results"]["songs"]["data"][0]
    assert parse_catalog_song(search).isrc == "DEUM72005143"


def test_parse_library_playlist_fields() -> None:
    mine, followed, plain = (
        parse_library_playlist(o) for o in _fx("library_playlists")["data"]
    )
    assert mine.service == APPLE
    assert mine.playlist_id == "p.RB1AAkGsv74Zkl"
    assert mine.name == "Mine"
    assert mine.description == "My library playlist"
    assert mine.owned and mine.readable
    assert mine.version_token == "2022-01-05T10:00:00Z"
    assert mine.cover_url is not None and "600x600bb.jpg" in mine.cover_url
    assert mine.track_count is None
    assert followed.owned is False
    assert followed.description == "" and followed.cover_url is None
    assert followed.version_token is None
    assert plain.owned and plain.description == ""


def test_artwork_url_fills_size() -> None:
    art = {"url": "https://x/{w}x{h}bb.jpg", "width": 10, "height": 10}
    assert artwork_url(art, 64) == "https://x/64x64bb.jpg"
    assert artwork_url(None, 64) is None
    assert artwork_url({}, 64) is None


def test_add_tracks_body_types() -> None:
    assert add_tracks_body(["1542568135", "i.abc123"]) == {
        "data": [
            {"id": "1542568135", "type": "songs"},
            {"id": "i.abc123", "type": "library-songs"},
        ]
    }
