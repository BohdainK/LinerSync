"""Parse the Qobuz web player's JSON API (api.json/0.2) into domain objects.

The shapes follow what the community clients (qobuz-dl, streamrip) document;
the API itself is private and undocumented.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from linersync.adapters.connectors.kit.parsing import as_mapping
from linersync.domain.playlists import PlaylistMeta
from linersync.domain.tracks import ServiceId, Track

SERVICE = ServiceId("qobuz")
API_BASE = "https://www.qobuz.com/api.json/0.2"
_ARTIST_ROLES = {"MainArtist", "FeaturedArtist"}
# Credit roles seen in ``performers``. A part that is one of these, or a
# CamelCase compound ending in one ("ComposerLyricist", "MainArtist"), ends
# the name before it, so a name with commas ("Earth, Wind & Fire") stays whole.
_ROLES = {
    r.casefold()
    for r in (
        "Arranger",
        "Artist",
        "Author",
        "Bass",
        "Choir",
        "Composer",
        "Conductor",
        "Drums",
        "Engineer",
        "Ensemble",
        "Guitar",
        "Keyboards",
        "Lyricist",
        "Masterer",
        "Mixer",
        "Orchestra",
        "Performer",
        "Piano",
        "Producer",
        "Personnel",
        "Programmer",
        "Publisher",
        "Remixer",
        "Soloist",
        "Vocals",
        "Writer",
    )
}
_COMPOUND_ROLE = re.compile(r"(?:[A-Z][a-z]+)+")
_WORD = re.compile(r"[A-Z][a-z]+")
_COVER_SIZES = ("small", "thumbnail", "large")
_PLAYLIST_IMAGES = ("images300", "images150", "images")


def _artists(item: Mapping[str, Any]) -> tuple[str, ...]:
    """The performer, then the other main and featured artists that the
    ``performers`` credit line names ("Name, Role, Role - Name, Role")."""
    names: list[str] = []
    main = as_mapping(item.get("performer")).get("name")
    if main:
        names.append(str(main))
    for credit in str(item.get("performers") or "").split(" - "):
        parts = [part.strip() for part in credit.split(",")]
        cut = next((i for i in range(1, len(parts)) if _is_role(parts[i])), None)
        if cut is None:
            continue
        name = ", ".join(parts[:cut])
        if name and name not in names and _ARTIST_ROLES & set(parts[cut:]):
            names.append(name)
    return tuple(names)


def _is_role(part: str) -> bool:
    if part.casefold() in _ROLES:
        return True
    if _COMPOUND_ROLE.fullmatch(part) is None:
        return False
    return _WORD.findall(part)[-1].casefold() in _ROLES


def artwork_url(item: Mapping[str, Any]) -> str | None:
    image = as_mapping(as_mapping(item.get("album")).get("image"))
    return next((str(image[s]) for s in _COVER_SIZES if image.get(s)), None)


def parse_track(item: Mapping[str, Any], artwork_hash: str | None = None) -> Track:
    tid = str(item["id"])
    title = str(item.get("title") or "")
    if item.get("version"):
        title = f"{title} ({item['version']})"
    album = as_mapping(item.get("album")).get("title")
    duration = item.get("duration")
    return Track(
        service=SERVICE,
        track_id=tid,
        title=title,
        artists=_artists(item),
        album=str(album) if album else None,
        duration_ms=duration * 1000 if isinstance(duration, int) else None,
        isrc=str(item["isrc"]) if item.get("isrc") else None,
        artwork_hash=artwork_hash,
        url=f"https://open.qobuz.com/track/{tid}",
    )


def parse_playlist_meta(obj: Mapping[str, Any], me: str | None) -> PlaylistMeta:
    """``me`` is the signed-in user's id; when unknown, the playlist counts as
    owned and a write to someone else's playlist fails when it's made."""
    owner = as_mapping(obj.get("owner")).get("id")
    cover = next(
        (
            str(images[0])
            for key in _PLAYLIST_IMAGES
            if isinstance(images := obj.get(key), list) and images
        ),
        None,
    )
    count = obj.get("tracks_count")
    return PlaylistMeta(
        service=SERVICE,
        playlist_id=str(obj["id"]),
        name=str(obj.get("name") or ""),
        description=str(obj.get("description") or ""),
        cover_url=cover,
        # updated_at isn't known to change when songs do; without a trusted
        # token, backups re-read the playlist every time.
        version_token=None,
        owned=me is None or str(owner) == me,
        readable=True,
        track_count=count if isinstance(count, int) else None,
    )


@dataclass(frozen=True, slots=True)
class RawItem:
    playlist_track_id: str  # this occurrence; what deleteTracks takes
    track_id: str
    added_at: datetime | None
    item: Mapping[str, Any]


def parse_tracks_page(obj: Mapping[str, Any]) -> tuple[list[RawItem], int | None]:
    """A page of ``playlist/get?extra=tracks``: its items and the reported total."""
    block = as_mapping(obj.get("tracks"))
    raw: list[RawItem] = []
    for item in block.get("items") or []:
        added = item.get("created_at")
        raw.append(
            RawItem(
                str(item["playlist_track_id"]),
                str(item["id"]),
                datetime.fromtimestamp(added, UTC) if isinstance(added, int) else None,
                item,
            )
        )
    total = block.get("total")
    return raw, total if isinstance(total, int) else None


def parse_account(body: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The (user id, login) a ``playlist/getUserPlaylists`` reply names."""
    user = as_mapping(body.get("user"))
    uid, login = user.get("id"), user.get("login")
    return (str(uid) if uid is not None else None, str(login) if login else None)
