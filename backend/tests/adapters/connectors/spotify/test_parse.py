from datetime import UTC, datetime

from linersync.adapters.connectors.spotify.parse import (
    SERVICE,
    parse_items_page,
    parse_playlist_meta,
    parse_track,
    track_artwork_url,
)
from tests.adapters.connectors.shapes import load_fixture


def _items() -> list[dict[str, object]]:
    return load_fixture("spotify", "playlist_items")["items"]  # type: ignore[no-any-return]


def test_parse_track_fields() -> None:
    raw = _items()[0]["item"]
    t = parse_track(raw, artwork_hash="h")  # type: ignore[arg-type]
    assert t is not None
    assert t.service == SERVICE
    assert t.track_id == "spotify:track:trk1"
    assert t.title == "Rock Song"
    assert t.artists == ("Band One", "Guest Two")
    assert t.album == "Debut"
    assert t.isrc == "USRC17607839"
    assert t.duration_ms == 215000
    assert t.url == "https://open.spotify.com/track/trk1"
    assert t.artwork_hash == "h"
    assert track_artwork_url(raw) == "https://i.scdn.co/image/a64"  # type: ignore[arg-type]


def test_parse_local_file_and_episode() -> None:
    local = parse_track(_items()[2]["item"])  # type: ignore[arg-type]
    assert local is not None
    assert local.track_id == "spotify:local:Garage+Band:Demos:Rough+Mix:215"
    assert local.isrc is None
    assert local.album == "Demos"
    assert track_artwork_url(_items()[2]["item"]) is None  # type: ignore[arg-type]
    ep_raw = _items()[3]["item"]
    ep = parse_track(ep_raw)  # type: ignore[arg-type]
    assert ep is not None
    assert ep.track_id == "spotify:episode:ep1"
    assert ep.artists == ("The Podcast",)
    assert ep.album is None
    assert ep.isrc is None
    assert track_artwork_url(ep_raw) == "https://i.scdn.co/image/e64"  # type: ignore[arg-type]


def test_parse_track_none_without_uri() -> None:
    assert parse_track(None) is None
    assert parse_track({"name": "x", "id": None}) is None


def test_description_unescaped_and_mosaic_cover_ignored() -> None:
    meta = parse_playlist_meta(load_fixture("spotify", "playlist"), "me")
    assert meta.description == "Rock & roll"
    assert meta.cover_url == "https://i.scdn.co/image/owned-640"
    assert meta.version_token == "snap-owned-2"
    assert meta.track_count == 4
    lists = {p["id"]: p for p in load_fixture("spotify", "my_playlists")["items"]}
    assert parse_playlist_meta(lists["pl_mosaic"], "me").cover_url is None
    assert parse_playlist_meta(lists["pl_collab"], "me").cover_url is None


def test_followed_playlist_unreadable_with_reason() -> None:
    lists = {p["id"]: p for p in load_fixture("spotify", "my_playlists")["items"]}
    followed = parse_playlist_meta(lists["pl_followed"], "me")
    assert not followed.owned
    assert not followed.readable
    assert followed.unreadable_reason == (
        "Spotify only lets apps read playlists you own or collaborate on"
    )
    assert followed.track_count is None
    collab = parse_playlist_meta(lists["pl_collab"], "me")
    assert not collab.owned
    assert collab.readable
    assert collab.unreadable_reason is None
    assert collab.track_count == 40
    owned = parse_playlist_meta(lists["pl_owned"], "me")
    assert owned.owned and owned.readable


def test_items_page_total_and_next() -> None:
    page = parse_items_page(load_fixture("spotify", "playlist_items"))
    assert page.total == 9
    assert page.next_url == (
        "https://api.spotify.com/v1/playlists/pl_owned/items?offset=4&limit=4"
    )
    assert len(page.items) == 4
    assert page.items[0].added_at == datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    assert page.items[3].item is not None
    assert page.items[3].item["type"] == "episode"
