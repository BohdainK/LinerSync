"""Jellyfin REST JSON (BaseItemDto) to domain objects. Pure functions."""

from collections.abc import Mapping
from typing import Any

from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId, Track

SERVICE = ServiceId("jellyfin")
_TICKS_PER_MS = 10_000  # RunTimeTicks are 100 ns units


def parse_track(item: Mapping[str, Any], base_url: str) -> Track | None:
    """None for anything that isn't a song (videos, books, ...)."""
    if item.get("Type") != "Audio":
        return None
    item_id = str(item["Id"])
    artists = tuple(str(a) for a in item.get("Artists") or ())
    if not artists and item.get("AlbumArtist"):
        artists = (str(item["AlbumArtist"]),)
    ticks = item.get("RunTimeTicks")
    return Track(
        SERVICE,
        item_id,
        str(item.get("Name") or ""),
        artists,
        album=item.get("Album") or None,
        duration_ms=int(ticks) // _TICKS_PER_MS if ticks is not None else None,
        isrc=None,
        url=f"{base_url}/web/#/details?id={item_id}",
        artwork_url=artwork_url(item, base_url),
    )


def artwork_url(item: Mapping[str, Any], base_url: str) -> str | None:
    """The item's own primary image, else its album's."""
    tag = (item.get("ImageTags") or {}).get("Primary")
    if tag:
        return _image(base_url, str(item["Id"]), str(tag))
    album_id, album_tag = item.get("AlbumId"), item.get("AlbumPrimaryImageTag")
    if album_id and album_tag:
        return _image(base_url, str(album_id), str(album_tag))
    return None


def parse_playlist_meta(item: Mapping[str, Any], base_url: str) -> PlaylistMeta:
    count = item.get("ChildCount")
    return PlaylistMeta(
        SERVICE,
        str(item["Id"]),
        str(item.get("Name") or ""),
        description=str(item.get("Overview") or ""),
        cover_url=artwork_url(item, base_url),
        version_token=None,
        track_count=int(count) if count is not None else None,
    )


def _image(base_url: str, item_id: str, tag: str) -> str:
    return f"{base_url}/Items/{item_id}/Images/Primary?tag={tag}&maxWidth=300"
