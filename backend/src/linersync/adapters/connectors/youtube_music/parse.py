"""Parse YouTube Music's internal youtubei/v1 responses into domain objects.

Response shapes ported from ytmusicapi (MIT), https://github.com/sigma67/ytmusicapi.
Every path into a response lives here, so a change on YouTube's side is a
fixture update and a fix in this file.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId, Track

SERVICE = ServiceId("youtube_music")
ORIGIN = "https://music.youtube.com"
API_BASE = f"{ORIGIN}/youtubei/v1"
CLIENT_VERSION = "1.20251020.03.00"
SONGS_FILTER = "EgWKAQIIAWoMEA4QChADEAQQCRAF"
ATV = "MUSIC_VIDEO_TYPE_ATV"  # an Art Track: the album recording, not a video
LIBRARY = "FEmusic_liked_playlists"
# Collections that show up as playlists (Liked Music, Episodes for Later).
_NOT_PLAYLISTS = {"LM", "SE"}

_ARTIST = "MUSIC_PAGE_TYPE_ARTIST"
_ALBUM = "MUSIC_PAGE_TYPE_ALBUM"
_DURATION = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})")
_JOINERS = {"", ",", "&", "and", "x", "×", "/", "feat.", "ft."}
_COUNT = re.compile(r"([\d,]+)\s+(?:songs?|tracks?)")


def context() -> dict[str, Any]:
    return {
        "client": {
            "clientName": "WEB_REMIX",
            "clientVersion": CLIENT_VERSION,
            "hl": "en",
        },
        "user": {},
    }


def nav(obj: Any, *path: str | int) -> Any:
    """Follow keys and indexes; None as soon as one is missing."""
    for key in path:
        try:
            obj = obj[key]
        except (KeyError, IndexError, TypeError):
            return None
    return obj


def _text(runs_holder: Any) -> str:
    runs = nav(runs_holder, "runs") or []
    return "".join(str(r.get("text", "")) for r in runs if isinstance(r, Mapping))


def parse_duration(text: str) -> int | None:
    match = _DURATION.fullmatch(text.strip())
    if match is None:
        return None
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def parse_track_count(text: str) -> int | None:
    match = _COUNT.search(text)
    return int(match.group(1).replace(",", "")) if match else None


@dataclass(frozen=True, slots=True)
class Song:
    track: Track
    set_video_id: str | None  # this occurrence in a playlist; None in search
    video_type: str | None
    artwork_url: str | None = None


def _page_type(run: Mapping[str, Any]) -> str | None:
    found = nav(
        run,
        "navigationEndpoint",
        "browseEndpoint",
        "browseEndpointContextSupportedConfigs",
        "browseEndpointContextMusicConfig",
        "pageType",
    )
    return str(found) if found else None


def _column_runs(renderer: Mapping[str, Any], n: int) -> list[Mapping[str, Any]]:
    runs = nav(
        renderer,
        "flexColumns",
        n,
        "musicResponsiveListItemFlexColumnRenderer",
        "text",
        "runs",
    )
    return [r for r in runs or [] if isinstance(r, Mapping)]


def thumbnail_url(renderer: Mapping[str, Any]) -> str | None:
    thumbs = nav(
        renderer, "thumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"
    ) or nav(
        renderer,
        "thumbnailRenderer",
        "musicThumbnailRenderer",
        "thumbnail",
        "thumbnails",
    )
    url = nav(thumbs, 0, "url")
    return str(url) if url else None


def _artists(runs: list[Mapping[str, Any]]) -> tuple[str, ...]:
    """The names before the first " • ": linked or not, since an artist
    without a channel page has no link. Joiners (", ", " & ") are skipped."""
    names: list[str] = []
    for run in runs:
        text = str(run.get("text", ""))
        if text.strip() == "•":
            break
        if _page_type(run) not in (None, _ARTIST):
            continue
        if text.strip().lower() in _JOINERS or parse_duration(text) is not None:
            continue
        names.append(text.strip())
    return tuple(names)


def parse_item(renderer: Mapping[str, Any]) -> Song | None:
    """A song row from a playlist or search; None for a greyed-out row that
    no longer has a playable video."""
    title_runs = _column_runs(renderer, 0)
    watch = nav(title_runs, 0, "navigationEndpoint", "watchEndpoint") or {}
    video_id = nav(renderer, "playlistItemData", "videoId") or watch.get("videoId")
    if not video_id:
        return None
    columns = len(renderer.get("flexColumns") or [])
    rest = [r for n in range(1, columns) for r in _column_runs(renderer, n)]
    artists = _artists(_column_runs(renderer, 1)) or tuple(
        str(r["text"]) for r in rest if _page_type(r) == _ARTIST
    )
    album = next((str(r["text"]) for r in rest if _page_type(r) == _ALBUM), None)
    fixed = nav(
        renderer,
        "fixedColumns",
        0,
        "musicResponsiveListItemFixedColumnRenderer",
        "text",
    )
    duration = parse_duration(_text(fixed)) if fixed else None
    if duration is None:  # search rows put the length in the second column
        duration = next(
            (d for r in rest if (d := parse_duration(str(r.get("text", ""))))),
            None,
        )
    track = Track(
        service=SERVICE,
        track_id=str(video_id),
        title="".join(str(r.get("text", "")) for r in title_runs),
        artists=artists,
        album=album,
        duration_ms=duration,
        url=f"{ORIGIN}/watch?v={video_id}",
    )
    video_type = nav(
        watch,
        "watchEndpointMusicSupportedConfigs",
        "watchEndpointMusicConfig",
        "musicVideoType",
    )
    set_id = nav(renderer, "playlistItemData", "playlistSetVideoId")
    return Song(
        track,
        str(set_id) if set_id else None,
        str(video_type) if video_type else None,
        thumbnail_url(renderer),
    )


def _items(contents: Sequence[Any]) -> tuple[list[Song | None], str | None]:
    songs: list[Song | None] = []
    token: str | None = None
    for entry in contents:
        renderer = nav(entry, "musicResponsiveListItemRenderer")
        if renderer is not None:
            songs.append(parse_item(renderer))
            continue
        found = nav(
            entry,
            "continuationItemRenderer",
            "continuationEndpoint",
            "continuationCommand",
            "token",
        )
        if found:
            token = str(found)
    return songs, token


@dataclass(frozen=True, slots=True)
class PlaylistPage:
    name: str
    description: str
    track_count: int | None
    items: list[Song | None]  # None: a greyed-out row
    continuation: str | None
    editable: bool  # the signed-in user can edit it


def parse_playlist(doc: Mapping[str, Any]) -> PlaylistPage:
    two = nav(doc, "contents", "twoColumnBrowseResultsRenderer")
    first = nav(
        two, "tabs", 0, "tabRenderer", "content", "sectionListRenderer", "contents", 0
    )
    editable = nav(first, "musicEditablePlaylistDetailHeaderRenderer")
    header = (
        nav(editable, "header", "musicResponsiveHeaderRenderer")
        if editable is not None
        else nav(first, "musicResponsiveHeaderRenderer")
    )
    if header is None:
        raise ValueError("no playlist header")
    section = nav(two, "secondaryContents", "sectionListRenderer")
    shelf = nav(section, "contents", 0, "musicPlaylistShelfRenderer")
    items, token = _items(nav(shelf, "contents") or [])
    if token is None:
        found = nav(section, "continuations", 0, "nextContinuationData", "continuation")
        token = str(found) if found else None
    description = nav(
        header, "description", "musicDescriptionShelfRenderer", "description"
    )
    return PlaylistPage(
        name=_text(header.get("title")),
        description=_text(description),
        track_count=parse_track_count(_text(header.get("secondSubtitle"))),
        items=items,
        continuation=token,
        editable=editable is not None,
    )


def parse_continuation(
    doc: Mapping[str, Any],
) -> tuple[list[Song | None], str | None]:
    contents = nav(
        doc,
        "onResponseReceivedActions",
        0,
        "appendContinuationItemsAction",
        "continuationItems",
    )
    if contents is None:
        raise ValueError("no continuation items")
    return _items(contents)


def _tile(item: Mapping[str, Any]) -> PlaylistMeta | None:
    renderer = nav(item, "musicTwoRowItemRenderer")
    browse_id = nav(
        renderer, "title", "runs", 0, "navigationEndpoint", "browseEndpoint", "browseId"
    )
    if not browse_id or not str(browse_id).startswith("VL"):
        return None  # the "New playlist" tile
    playlist_id = str(browse_id)[2:]
    if playlist_id in _NOT_PLAYLISTS:
        return None
    return PlaylistMeta(
        service=SERVICE,
        playlist_id=playlist_id,
        name=_text(renderer.get("title")),
        cover_url=thumbnail_url(renderer),
        owned=True,
        readable=True,
        track_count=parse_track_count(_text(renderer.get("subtitle"))),
    )


def parse_library(doc: Mapping[str, Any]) -> tuple[list[PlaylistMeta], str | None]:
    grid = nav(
        doc,
        "contents",
        "singleColumnBrowseResultsRenderer",
        "tabs",
        0,
        "tabRenderer",
        "content",
        "sectionListRenderer",
        "contents",
        0,
        "gridRenderer",
    )
    if grid is None:
        grid = nav(doc, "continuationContents", "gridContinuation")
    if grid is None:
        raise ValueError("no playlist grid")
    metas = [m for item in grid.get("items") or [] if (m := _tile(item)) is not None]
    token = nav(grid, "continuations", 0, "nextContinuationData", "continuation")
    return metas, str(token) if token else None


def parse_search(doc: Mapping[str, Any]) -> list[Song]:
    sections = nav(
        doc,
        "contents",
        "tabbedSearchResultsRenderer",
        "tabs",
        0,
        "tabRenderer",
        "content",
        "sectionListRenderer",
        "contents",
    )
    songs: list[Song] = []
    for section in sections or []:
        for entry in nav(section, "musicShelfRenderer", "contents") or []:
            renderer = nav(entry, "musicResponsiveListItemRenderer")
            song = parse_item(renderer) if renderer is not None else None
            if song is not None:
                songs.append(song)
    return songs


def parse_account_name(doc: Mapping[str, Any]) -> str | None:
    name = _text(
        nav(
            doc,
            "actions",
            0,
            "openPopupAction",
            "popup",
            "multiPageMenuRenderer",
            "header",
            "activeAccountHeaderRenderer",
            "accountName",
        )
    )
    return name or None


def parse_edit_status(doc: Mapping[str, Any]) -> bool:
    return doc.get("status") == "STATUS_SUCCEEDED"
