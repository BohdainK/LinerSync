import pytest

from linersync.adapters.connectors.youtube_music.parse import (
    ATV,
    SERVICE,
    context,
    parse_account_name,
    parse_continuation,
    parse_duration,
    parse_edit_status,
    parse_library,
    parse_playlist,
    parse_search,
    parse_track_count,
)
from tests.adapters.connectors.shapes import load_fixture


@pytest.mark.parametrize(
    ("text", "ms"),
    [
        ("4:03", 243_000),
        ("1:02:03", 3_723_000),
        ("0:07", 7_000),
        ("4 min", None),
        ("", None),
    ],
)
def test_parse_duration(text: str, ms: int | None) -> None:
    assert parse_duration(text) == ms


@pytest.mark.parametrize(
    ("text", "count"),
    [("218 songs", 218), ("1 song", 1), ("1,024 songs", 1024), ("No songs", None)],
)
def test_parse_track_count(text: str, count: int | None) -> None:
    assert parse_track_count(text) == count


def test_context_is_web_remix() -> None:
    client = context()["client"]
    assert client["clientName"] == "WEB_REMIX"
    assert client["clientVersion"]


def test_search_recorded() -> None:
    songs = parse_search(load_fixture("youtube_music", "search_songs"))
    assert len(songs) == 3
    first = songs[0]
    t = first.track
    assert t.service == SERVICE
    assert t.track_id == "RhoprlMxteE"
    assert t.title == "Yellow Submarine (Remastered 2009)"
    assert t.artists == ("The Beatles",)
    assert t.album == "Revolver (Remastered 2009)"
    assert t.duration_ms == 159_000
    assert t.isrc is None
    assert t.url == "https://music.youtube.com/watch?v=RhoprlMxteE"
    assert first.video_type == ATV
    assert first.set_video_id is None
    assert first.artwork_url and first.artwork_url.startswith("https://")


def test_playlist_recorded() -> None:
    page = parse_playlist(load_fixture("youtube_music", "playlist"))
    assert page.name == "The Beatles Jukebox"
    assert page.description == "Every Beatles track ever. Just press play."
    assert page.track_count == 218
    assert page.continuation
    assert len(page.items) == 3
    first = page.items[0]
    assert first is not None
    assert first.track.track_id == "CGj85pVzRJs"
    assert first.set_video_id == "56B44F6D10557CC6"
    assert first.track.duration_ms == 243_000
    second = page.items[1]
    assert second is not None
    assert second.track.artists == ("George Martin",)
    assert second.track.album == "Yellow Submarine (Remastered 2009)"


def test_continuation_recorded() -> None:
    items, token = parse_continuation(
        load_fixture("youtube_music", "playlist_continuation")
    )
    assert len(items) == 2
    assert all(i is not None and i.set_video_id for i in items)
    assert token is None


def test_owned_playlist_with_greyed_item() -> None:
    page = parse_playlist(load_fixture("youtube_music", "owned_playlist"))
    assert (page.name, page.description, page.track_count) == (
        "Road trip",
        "Songs for the car",
        3,
    )
    assert page.continuation is None
    assert page.items[0] is not None and page.items[1] is not None
    assert page.items[2] is None


def test_library_skips_new_playlist_tile_and_liked_music() -> None:
    metas, token = parse_library(load_fixture("youtube_music", "library_playlists"))
    assert [(m.playlist_id, m.name, m.track_count) for m in metas] == [
        ("PLsimowned0000000000000000000000", "Road trip", 3),
        ("PLsimother000000000000000000000", "Gym", 1024),
    ]
    assert token == "lib-cont-1"
    assert metas[0].cover_url
    more, end = parse_library(load_fixture("youtube_music", "library_continuation"))
    assert [m.name for m in more] == ["Last"]
    assert end is None


def test_account_and_edit_status() -> None:
    assert (
        parse_account_name(load_fixture("youtube_music", "account_menu"))
        == "Sam Example"
    )
    assert parse_account_name({"actions": []}) is None
    assert parse_edit_status(load_fixture("youtube_music", "edit_playlist")) is True
    assert parse_edit_status({"status": "STATUS_FAILED"}) is False


def _row(*artist_runs: tuple[str, str | None]) -> dict[str, object]:
    def run(text: str, page: str | None) -> dict[str, object]:
        if page is None:
            return {"text": text}
        config = {"browseEndpointContextMusicConfig": {"pageType": page}}
        return {
            "text": text,
            "navigationEndpoint": {
                "browseEndpoint": {"browseEndpointContextSupportedConfigs": config}
            },
        }

    def column(runs: list[dict[str, object]]) -> dict[str, object]:
        return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": runs}}}

    title = run("Song", None)
    title["navigationEndpoint"] = {"watchEndpoint": {"videoId": "vid00000001"}}
    return {
        "flexColumns": [
            column([title]),
            column([run(t, p) for t, p in artist_runs]),
        ]
    }


@pytest.mark.parametrize(
    ("runs", "artists"),
    [
        (
            [("Linked", "MUSIC_PAGE_TYPE_ARTIST"), (" & ", None), ("Plain", None)],
            ("Linked", "Plain"),
        ),
        (
            [("Plain", None), (", ", None), ("Linked", "MUSIC_PAGE_TYPE_ARTIST")],
            ("Plain", "Linked"),
        ),
        (
            [
                ("Linked", "MUSIC_PAGE_TYPE_ARTIST"),
                (" • ", None),
                ("Album", "MUSIC_PAGE_TYPE_ALBUM"),
                (" • ", None),
                ("3:01", None),
            ],
            ("Linked",),
        ),
        ([("Solo", None), (" • ", None), ("2:39", None)], ("Solo",)),
    ],
)
def test_unlinked_co_artists_are_kept(
    runs: list[tuple[str, str | None]], artists: tuple[str, ...]
) -> None:
    from linersync.adapters.connectors.youtube_music.parse import parse_item

    song = parse_item(_row(*runs))
    assert song is not None
    assert song.track.artists == artists
