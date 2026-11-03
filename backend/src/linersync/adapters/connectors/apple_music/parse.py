from collections.abc import Mapping, Sequence
from typing import Any

from linersync.adapters.connectors.kit.parsing import DISPLAY_ART_SIZE, attributes
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId, Track

APPLE = ServiceId("apple_music")
API_BASE = "https://api.music.apple.com"


def artwork_url(artwork: Mapping[str, Any] | None, size: int) -> str | None:
    if not artwork:
        return None
    url = artwork.get("url")
    if not isinstance(url, str) or not url:
        return None
    return url.replace("{w}", str(size)).replace("{h}", str(size))


def _track(
    track_id: str,
    attrs: Mapping[str, Any],
    isrc: str | None,
    url: str | None,
    artwork_hash: str | None,
    fallback_artwork: Mapping[str, Any] | None = None,
) -> Track:
    return Track(
        service=APPLE,
        track_id=track_id,
        title=attrs.get("name", ""),
        artists=(attrs["artistName"],) if attrs.get("artistName") else (),
        album=attrs.get("albumName"),
        duration_ms=attrs.get("durationInMillis"),
        isrc=isrc,
        artwork_hash=artwork_hash,
        url=url,
        artwork_url=artwork_url(attrs.get("artwork"), DISPLAY_ART_SIZE)
        or artwork_url(fallback_artwork, DISPLAY_ART_SIZE),
    )


def parse_library_song(
    obj: Mapping[str, Any],
    *,
    isrc: str | None = None,
    url: str | None = None,
    artwork_hash: str | None = None,
    fallback_artwork: Mapping[str, Any] | None = None,
) -> Track:
    """``fallback_artwork``: the catalog song's, for a library song without."""
    attrs = attributes(obj)
    catalog_id = (attrs.get("playParams") or {}).get("catalogId")
    return _track(
        str(catalog_id or obj["id"]),
        attrs,
        isrc,
        url,
        artwork_hash,
        fallback_artwork,
    )


def parse_catalog_song(
    obj: Mapping[str, Any], artwork_hash: str | None = None
) -> Track:
    attrs = attributes(obj)
    return _track(
        str(obj["id"]), attrs, attrs.get("isrc"), attrs.get("url"), artwork_hash
    )


def parse_library_playlist(obj: Mapping[str, Any]) -> PlaylistMeta:
    attrs = attributes(obj)
    return PlaylistMeta(
        service=APPLE,
        playlist_id=str(obj["id"]),
        name=attrs.get("name", ""),
        description=(attrs.get("description") or {}).get("standard") or "",
        cover_url=artwork_url(attrs.get("artwork"), 600),
        version_token=attrs.get("lastModifiedDate") or None,
        owned=bool(attrs.get("canEdit", False)),
        readable=True,
        track_count=None,
    )


def add_tracks_body(track_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "data": [
            {"id": i, "type": "songs" if i.isdigit() else "library-songs"}
            for i in track_ids
        ]
    }
