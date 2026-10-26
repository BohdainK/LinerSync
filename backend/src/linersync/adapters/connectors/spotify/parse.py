"""Parse Spotify Web API (2026 shapes) responses into domain objects."""

import html
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId, Track

SPOTIFY = ServiceId("spotify")

_UNREADABLE = "Spotify only lets apps read playlists you own or collaborate on"
_MOSAIC_HOST = "mosaic.scdn.co"


def _images(obj: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if obj.get("type") == "episode":
        imgs = obj.get("images")
    else:
        imgs = (obj.get("album") or {}).get("images")
    return [i for i in (imgs or []) if i.get("url")]


def track_artwork_url(obj: Mapping[str, Any]) -> str | None:
    imgs = _images(obj)
    if not imgs:
        return None
    smallest = min(imgs, key=lambda i: i.get("width") or 10**9)
    return str(smallest["url"])


def parse_track(
    obj: Mapping[str, Any] | None, artwork_hash: str | None = None
) -> Track | None:
    if obj is None or not obj.get("uri"):
        return None
    if obj.get("type") == "episode":
        artists = ((obj.get("show") or {}).get("name") or "",)
        album = None
    else:
        artists = tuple(a["name"] for a in obj.get("artists") or [])
        album = (obj.get("album") or {}).get("name")
    return Track(
        service=SPOTIFY,
        track_id=str(obj["uri"]),
        title=str(obj.get("name") or ""),
        artists=artists,
        album=album,
        duration_ms=obj.get("duration_ms"),
        isrc=(obj.get("external_ids") or {}).get("isrc"),
        artwork_hash=artwork_hash,
        url=(obj.get("external_urls") or {}).get("spotify"),
    )


def _cover_url(obj: Mapping[str, Any]) -> str | None:
    images = obj.get("images") or []
    if not images or not images[0].get("url"):
        return None
    url = str(images[0]["url"])
    if urlparse(url).hostname == _MOSAIC_HOST:
        return None
    return url


def parse_playlist_meta(obj: Mapping[str, Any], me_id: str) -> PlaylistMeta:
    owned = (obj.get("owner") or {}).get("id") == me_id
    readable = owned or bool(obj.get("collaborative"))
    items = obj.get("items")
    total = items.get("total") if isinstance(items, Mapping) else None
    return PlaylistMeta(
        service=SPOTIFY,
        playlist_id=str(obj["id"]),
        name=html.unescape(str(obj.get("name") or "")),
        description=html.unescape(obj.get("description") or ""),
        cover_url=_cover_url(obj),
        version_token=obj.get("snapshot_id"),
        owned=owned,
        readable=readable,
        unreadable_reason=None if readable else _UNREADABLE,
        track_count=total,
    )


@dataclass(frozen=True, slots=True)
class RawItem:
    item: Mapping[str, Any] | None
    added_at: datetime | None


@dataclass(frozen=True, slots=True)
class ItemsPage:
    items: list[RawItem]
    total: int
    next_url: str | None


def _added_at(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def parse_items_page(obj: Mapping[str, Any]) -> ItemsPage:
    items = [
        RawItem(entry.get("item"), _added_at(entry.get("added_at")))
        for entry in obj.get("items") or []
    ]
    return ItemsPage(items, int(obj["total"]), obj.get("next"))
