"""The Jellyfin connector: reads and searches; Jellyfin is a source only.

An error never reads as an empty playlist: any failure, short read or change
during a read raises ReadFailed. Entries that aren't songs are left out but
still counted, so such a snapshot is never complete.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from linersync.adapters.connectors.jellyfin.parse import (
    SERVICE,
    artwork_url,
    parse_playlist_meta,
    parse_track,
)
from linersync.adapters.connectors.kit import SEARCH_LIMIT, PlaylistOp
from linersync.adapters.connectors.kit.errors import Errors
from linersync.adapters.connectors.kit.http import (
    HttpClient,
    HttpFailure,
    json_object,
    read_failed,
)
from linersync.adapters.connectors.kit.images import ArtworkHasher, hashes_for
from linersync.domain.capabilities import Capability
from linersync.domain.playlists import (
    PlaylistDraft,
    PlaylistEntry,
    PlaylistMeta,
    PlaylistSnapshot,
)
from linersync.domain.tracks import Track, TrackQuery
from linersync.ports.connector import ConnectorLimits
from linersync.ports.errors import WriteFailed
from linersync.ports.runtime import Clock

CAPABILITIES = frozenset({Capability.READ_ORDER})
LIMITS = ConnectorLimits(page_size=100, add_batch_size=1, search_cost=1)

_READ_ONLY = "Jellyfin is read-only"
_SONG_FIELDS = "ProviderIds,MediaSources"

E = Errors(SERVICE, "Jellyfin")


class JellyfinConnector:
    service = SERVICE
    display_name = "Jellyfin"
    capabilities = CAPABILITIES
    limits = LIMITS

    def __init__(
        self,
        api: HttpClient,
        base_url: str,
        user_id: Callable[[], Awaitable[str]],
        artwork: ArtworkHasher | None,
        *,
        clock: Clock,
    ) -> None:
        self._api = api
        self._base = base_url
        self._user_id = user_id
        self._artwork = artwork
        self._clock = clock

    # -- reads -------------------------------------------------------------

    async def list_playlists(self) -> list[PlaylistMeta]:
        try:
            body = await self._get("/Items", _playlists(await self._user_id()))
            return [
                parse_playlist_meta(obj, self._base)
                for obj in body.get("Items") or []
                if isinstance(obj, Mapping)
            ]
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise E.not_understood("your playlists") from None

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        try:
            return await self._read(playlist_id)
        except HttpFailure as e:
            if e.status == 404:
                raise E.not_found(playlist_id) from None
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise E.not_understood(f"playlist {playlist_id}") from None

    async def _read(self, playlist_id: str) -> PlaylistSnapshot:
        user_id = await self._user_id()
        found = await self._get("/Items", _playlists(user_id, Ids=playlist_id))
        metas = [o for o in found.get("Items") or [] if o.get("Id") == playlist_id]
        if not metas:
            raise E.not_found(playlist_id)
        meta = parse_playlist_meta(metas[0], self._base)

        raw = await self._read_items(playlist_id, user_id, meta.name)
        # Jellyfin has no playlist version to compare, and a change that keeps
        # the length would splice pages unnoticed: a second pass must agree.
        again = await self._read_items(playlist_id, user_id, meta.name)
        if _entry_keys(again) != _entry_keys(raw):
            raise E.changed_during_read(meta.name)

        entries = await self._entries(raw)
        return PlaylistSnapshot(
            meta, entries, expected_total=len(raw), taken_at=self._clock.now()
        )

    async def _read_items(
        self, playlist_id: str, user_id: str, name: str
    ) -> list[Mapping[str, Any]]:
        raw: list[Mapping[str, Any]] = []
        total: int | None = None
        while total is None or len(raw) < total:
            page = await self._get(
                f"/Playlists/{playlist_id}/Items",
                {
                    "userId": user_id,
                    "StartIndex": len(raw),
                    "Limit": LIMITS.page_size,
                    "Fields": _SONG_FIELDS,
                },
            )
            page_total = int(page["TotalRecordCount"])
            if total is not None and page_total != total:
                raise E.changed_during_read(name)
            total = page_total
            items = [o for o in page.get("Items") or [] if isinstance(o, Mapping)]
            if not items:
                break
            raw.extend(items)
        if len(raw) != total:
            raise E.short(len(raw), total, name)
        return raw

    async def _entries(self, raw: list[Mapping[str, Any]]) -> tuple[PlaylistEntry, ...]:
        urls = [artwork_url(r, self._base) for r in raw]
        hashes = await hashes_for(self._artwork, urls)
        entries: list[PlaylistEntry] = []
        for r, artwork_hash in zip(raw, hashes, strict=True):
            parsed = parse_track(r, self._base)
            if parsed is None:
                continue  # not a song: counted in the total, so not complete
            entries.append(PlaylistEntry(replace(parsed, artwork_hash=artwork_hash)))
        return tuple(entries)

    async def search(self, query: TrackQuery) -> list[Track]:
        try:
            body = await self._get(
                "/Items",
                {
                    "userId": await self._user_id(),
                    "IncludeItemTypes": "Audio",
                    "Recursive": "true",
                    "SearchTerm": query.title,
                    "Limit": SEARCH_LIMIT,
                    "Fields": _SONG_FIELDS,
                },
            )
            found = [
                parse_track(obj, self._base)
                for obj in body.get("Items") or []
                if isinstance(obj, Mapping)
            ]
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise E.not_understood("a search") from None
        return [t for t in found if t is not None]

    # -- writes ------------------------------------------------------------

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        raise WriteFailed(SERVICE, _READ_ONLY)

    async def apply(self, playlist_id: str, op: PlaylistOp) -> None:
        raise WriteFailed(SERVICE, _READ_ONLY)

    # -- helpers -----------------------------------------------------------

    async def _get(
        self, path: str, params: Mapping[str, str | int]
    ) -> Mapping[str, Any]:
        response = await self._api.request(
            "GET", self._base + path, idempotent=True, params=params
        )
        return json_object(response)


def _playlists(user_id: str, **extra: str) -> dict[str, str | int]:
    """Query params for /Items listing the user's playlists."""
    return {
        "userId": user_id,
        "IncludeItemTypes": "Playlist",
        "Recursive": "true",
        **extra,
        "Fields": "ChildCount,Overview",
    }


def _entry_keys(raw: list[Mapping[str, Any]]) -> list[str]:
    return [str(o.get("PlaylistItemId") or o.get("Id")) for o in raw]
