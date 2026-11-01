"""Parse TIDAL API v2 (JSON:API) documents into domain objects."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from linersync.adapters.connectors.kit.parsing import attributes, iso_datetime
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId, Track

SERVICE = ServiceId("tidal")
API_BASE = "https://openapi.tidal.com/v2"
VIDEO_PREFIX = "video:"

_DURATION = re.compile(r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?")


def track_ref(track_id: str) -> tuple[str, str]:
    """The JSON:API (type, id) a domain track id stands for."""
    if track_id.startswith(VIDEO_PREFIX):
        return "videos", track_id.removeprefix(VIDEO_PREFIX)
    return "tracks", track_id


def iso_duration_ms(value: str | None) -> int | None:
    if not value or value == "PT":
        return None
    match = _DURATION.fullmatch(value)
    if match is None:
        return None
    hours = int(match["h"] or 0)
    minutes = int(match["m"] or 0)
    seconds = float(match["s"] or 0)
    return round(((hours * 60 + minutes) * 60 + seconds) * 1000)


class Included:
    """The resources of a document's ``included`` section, by (type, id)."""

    def __init__(self, doc: Mapping[str, Any]) -> None:
        self._by_ref: dict[tuple[str, str], Mapping[str, Any]] = {}
        self.add(doc)

    def add(self, doc: Mapping[str, Any]) -> None:
        """Also index another document's included resources."""
        for res in doc.get("included") or []:
            if isinstance(res, Mapping):
                self._by_ref[(str(res.get("type")), str(res.get("id")))] = res

    def get(self, type_: str, id_: str) -> Mapping[str, Any] | None:
        return self._by_ref.get((type_, id_))

    def related(
        self, resource: Mapping[str, Any], name: str
    ) -> list[Mapping[str, Any]]:
        """The included resources a relationship links to, in linkage order."""
        rel = (resource.get("relationships") or {}).get(name) or {}
        out: list[Mapping[str, Any]] = []
        for ref in rel.get("data") or []:
            found = self.get(str(ref.get("type")), str(ref.get("id")))
            if found is not None:
                out.append(found)
        return out


def _files(artwork: Mapping[str, Any]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for f in attributes(artwork).get("files") or []:
        href = f.get("href") if isinstance(f, Mapping) else None
        if href:
            width = (f.get("meta") or {}).get("width") or 0
            out.append((int(width), str(href)))
    return out


def _cover_files(
    resource: Mapping[str, Any], included: Included
) -> list[tuple[int, str]]:
    arts = included.related(resource, "coverArt")
    return _files(arts[0]) if arts else []


def artwork_url(resource: Mapping[str, Any], included: Included) -> str | None:
    """The smallest cover file of the track's first album."""
    albums = included.related(resource, "albums")
    files = _cover_files(albums[0], included) if albums else []
    return min(files)[1] if files else None


def parse_track(
    resource: Mapping[str, Any],
    included: Included,
    artwork_hash: str | None = None,
) -> Track:
    attrs = attributes(resource)
    video = resource.get("type") == "videos"
    rid = str(resource["id"])
    title = str(attrs.get("title") or "")
    if attrs.get("version"):
        title = f"{title} ({attrs['version']})"
    albums = included.related(resource, "albums")
    album = attributes(albums[0]).get("title") if albums else None
    return Track(
        service=SERVICE,
        track_id=f"{VIDEO_PREFIX}{rid}" if video else rid,
        title=title,
        artists=tuple(
            str(attributes(a).get("name") or "")
            for a in included.related(resource, "artists")
        ),
        album=None if video or album is None else str(album),
        duration_ms=iso_duration_ms(attrs.get("duration")),
        isrc=None if video else attrs.get("isrc"),
        artwork_hash=artwork_hash,
        url=f"https://tidal.com/browse/{'video' if video else 'track'}/{rid}",
    )


def parse_playlist_meta(
    resource: Mapping[str, Any], included: Included
) -> PlaylistMeta:
    attrs = attributes(resource)
    files = _cover_files(resource, included)
    count = attrs.get("numberOfItems")
    return PlaylistMeta(
        service=SERVICE,
        playlist_id=str(resource["id"]),
        name=str(attrs.get("name") or ""),
        description=str(attrs.get("description") or ""),
        cover_url=max(files)[1] if files else None,
        version_token=attrs.get("lastModifiedAt"),
        owned=True,
        readable=True,
        track_count=count if isinstance(count, int) else None,
    )


@dataclass(frozen=True, slots=True)
class RawItem:
    ref_type: str
    ref_id: str
    item_id: str
    added_at: datetime | None
    resource: Mapping[str, Any] | None  # None: not available to this user

    @property
    def track_id(self) -> str:
        prefix = VIDEO_PREFIX if self.ref_type == "videos" else ""
        return f"{prefix}{self.ref_id}"


def next_link(doc: Mapping[str, Any]) -> str | None:
    value = (doc.get("links") or {}).get("next")
    return str(value) if value else None


def parse_items_page(doc: Mapping[str, Any]) -> tuple[list[RawItem], str | None]:
    included = Included(doc)
    items: list[RawItem] = []
    for ref in doc.get("data") or []:
        ref_type, ref_id = str(ref["type"]), str(ref["id"])
        meta = ref.get("meta") or {}
        items.append(
            RawItem(
                ref_type,
                ref_id,
                str(meta["itemId"]),
                iso_datetime(meta.get("addedAt")),
                included.get(ref_type, ref_id),
            )
        )
    return items, next_link(doc)
