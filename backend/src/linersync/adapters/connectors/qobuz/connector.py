"""The Qobuz connector: reads, searches and writes through the web player's API.

An error never reads as an empty playlist: any failure, short or over-long
read, or change during a read raises ReadFailed. Qobuz gives no trusted
version for a playlist, so a read that took several pages fetches the
playlist again and fails if its length or update time moved.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from linersync.adapters.connectors.kit.http import (
    HttpClient,
    HttpFailure,
    apply_in_batches,
    read_failed,
    write_failed,
)
from linersync.adapters.connectors.kit.images import ArtworkHasher
from linersync.adapters.connectors.qobuz.parse import (
    API_BASE,
    SERVICE,
    RawItem,
    artwork_url,
    parse_account,
    parse_playlist_meta,
    parse_track,
    parse_tracks_page,
)
from linersync.domain.capabilities import Capability
from linersync.domain.operations import AddTracks, RemoveTracks, Reorder, UpdateMetadata
from linersync.domain.playlists import (
    PlaylistDraft,
    PlaylistEntry,
    PlaylistMeta,
    PlaylistSnapshot,
)
from linersync.domain.tracks import Track, TrackQuery
from linersync.ports.connector import ConnectorLimits
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed
from linersync.ports.runtime import Clock

# No REORDER: the web player's move endpoint couldn't be confirmed.
CAPABILITIES = frozenset(
    {
        Capability.READ_ORDER,
        Capability.ADD,
        Capability.REMOVE,
        Capability.CREATE_PLAYLIST,
        Capability.EDIT_METADATA,
    }
)
LIMITS = ConnectorLimits(page_size=500, add_batch_size=50, search_cost=2)

_PAGE = 500
_BATCH = 50  # songs per add or remove
_SEARCH_LIMIT = 10

_Op = UpdateMetadata | AddTracks | Reorder | RemoveTracks


class QobuzConnector:
    service = SERVICE
    display_name = "Qobuz"
    capabilities = CAPABILITIES
    limits = LIMITS

    def __init__(
        self,
        api: HttpClient,
        artwork: ArtworkHasher | None,
        *,
        user_id: str | None,
        clock: Clock,
    ) -> None:
        self._api = api
        self._artwork = artwork
        self._user_id = user_id  # learnt from the playlist list when not stored
        self._clock = clock

    # -- reads -------------------------------------------------------------

    async def list_playlists(self) -> list[PlaylistMeta]:
        try:
            objs: list[Mapping[str, Any]] = []
            offset = 0
            while True:
                body = await self._get(
                    "playlist/getUserPlaylists", {"offset": offset, "limit": _PAGE}
                )
                self._user_id = parse_account(body)[0] or self._user_id
                block = body["playlists"]
                items = [p for p in block.get("items") or [] if isinstance(p, Mapping)]
                objs.extend(items)
                total = block.get("total")
                if not items or (isinstance(total, int) and len(objs) >= total):
                    break  # without a total, read on until an empty page
                offset += len(items)
            return [parse_playlist_meta(p, self._user_id) for p in objs]
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("your playlists") from None

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        try:
            meta, raw = await self._read(playlist_id)
            entries = await self._entries(raw)
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood(f"playlist {playlist_id}") from None
        return PlaylistSnapshot(
            meta, entries, expected_total=len(raw), taken_at=self._clock.now()
        )

    async def _read(self, playlist_id: str) -> tuple[PlaylistMeta, list[RawItem]]:
        """The playlist's meta and every occurrence, in order.

        Each later page starts one back, at the last song already read: if
        that song moved, the playlist changed in between, even when its
        length didn't (a removal plus an add)."""
        first: Mapping[str, Any] | None = None
        total: int | None = None
        raw: list[RawItem] = []
        seen: set[str] = set()
        while True:
            page = await self._playlist_page(playlist_id, max(len(raw) - 1, 0))
            if first is None:
                first = page
            items, page_total = parse_tracks_page(page)
            meta = parse_playlist_meta(first, self._user_id)
            if page_total is None:
                raise ReadFailed(
                    SERVICE, f'Qobuz did not say how long "{meta.name}" is'
                )
            if total is None:
                total = page_total
            if raw:
                if not items or items[0].playlist_track_id != raw[-1].playlist_track_id:
                    raise _changed_during_read(meta.name)
                items = items[1:]
            if page_total != total or any(r.playlist_track_id in seen for r in items):
                raise _changed_during_read(meta.name)
            seen.update(r.playlist_track_id for r in items)
            raw.extend(items)
            if len(raw) > total:
                raise ReadFailed(
                    SERVICE,
                    f'Qobuz returned more songs than it reported in "{meta.name}"; '
                    "it will be read again next run",
                )
            if len(raw) == total:
                break
            if not items:
                raise ReadFailed(
                    SERVICE,
                    f'Qobuz returned fewer songs than it reported in "{meta.name}"',
                )
        if raw and len(raw) > len(parse_tracks_page(first)[0]):
            check = await self._get("playlist/get", {"playlist_id": playlist_id})
            if _version(check) != _version(first):
                raise _changed_during_read(meta.name)
        return meta, raw

    async def _playlist_page(self, playlist_id: str, offset: int) -> Mapping[str, Any]:
        params: dict[str, str | int] = {
            "playlist_id": playlist_id,
            "extra": "tracks",
            "offset": offset,
            "limit": _PAGE,
        }
        try:
            return await self._get("playlist/get", params)
        except HttpFailure as e:
            if e.status == 404:
                raise PlaylistNotFound(
                    SERVICE, f"Qobuz has no playlist {playlist_id}"
                ) from None
            raise

    async def _entries(self, raw: list[RawItem]) -> tuple[PlaylistEntry, ...]:
        hashes: dict[str, str | None] = {}
        if self._artwork is not None:
            hashes = await self._artwork.hash_many([artwork_url(r.item) for r in raw])
        entries: list[PlaylistEntry] = []
        for r in raw:
            art = artwork_url(r.item)
            track = parse_track(r.item, hashes.get(art) if art else None)
            entries.append(PlaylistEntry(track, r.added_at))
        return tuple(entries)

    async def search(self, query: TrackQuery) -> list[Track]:
        try:
            if query.isrc:
                wanted = query.isrc.upper()
                hits = [
                    t
                    for t in await self._search(query.isrc)
                    if t.isrc is not None and t.isrc.upper() == wanted
                ]
                if hits:
                    return hits[:_SEARCH_LIMIT]
            text = query.title
            if query.artists:
                text = f"{query.artists[0]} {text}"
            return (await self._search(text))[:_SEARCH_LIMIT]
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("a search") from None

    async def _search(self, text: str) -> list[Track]:
        body = await self._get("track/search", {"query": text, "limit": _SEARCH_LIMIT})
        items = (body.get("tracks") or {}).get("items") or []
        return [parse_track(i) for i in items if isinstance(i, Mapping)]

    # -- writes ------------------------------------------------------------

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        # The API takes no cover, so draft.cover_url is not carried over.
        try:
            response = await self._post(
                "playlist/create",
                {
                    "name": draft.name,
                    "description": draft.description,
                    "is_public": "false",
                    "is_collaborative": "false",
                },
                idempotent=False,  # a replay would make a second playlist
            )
            return parse_playlist_meta(response, self._user_id)
        except HttpFailure as e:
            raise write_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise WriteFailed(
                SERVICE, "Qobuz's reply to creating a playlist was not understood", True
            ) from None

    async def apply(self, playlist_id: str, op: _Op) -> None:
        match op:
            case AddTracks():
                await self._add(playlist_id, op)
            case RemoveTracks():
                await self._remove(playlist_id, op)
            case UpdateMetadata():
                await self._update_metadata(playlist_id, op)
            case Reorder():
                raise WriteFailed(SERVICE, "Qobuz can't reorder songs", False)

    async def _add(self, playlist_id: str, op: AddTracks) -> None:
        async def send(chunk: Sequence[str]) -> None:
            await self._post(
                "playlist/addTracks",
                {
                    "playlist_id": playlist_id,
                    "track_ids": ",".join(chunk),
                    "no_duplicate": "false",  # the playlist may repeat a song
                },
                idempotent=False,  # a replay would add the songs again
            )

        try:
            await apply_in_batches(op.track_ids, _BATCH, send)
        except HttpFailure as e:
            raise write_failed(e) from None

    async def _current_items(self, playlist_id: str) -> list[RawItem]:
        """Every occurrence with its playlist_track_id, read fresh."""
        try:
            _, raw = await self._read(playlist_id)
        except (HttpFailure, ReadFailed) as e:
            raise WriteFailed(
                SERVICE, e.message, False, auth_required=e.auth_required
            ) from None
        except (KeyError, TypeError, ValueError):
            raise WriteFailed(
                SERVICE,
                f"Qobuz's reply for playlist {playlist_id} was not understood",
                False,
            ) from None
        return raw

    async def _remove(self, playlist_id: str, op: RemoveTracks) -> None:
        raw = await self._current_items(playlist_id)
        occurrences: list[str] = []
        for r in op.removals:
            if not 0 <= r.position < len(raw) or raw[r.position].track_id != r.track_id:
                raise WriteFailed(
                    SERVICE,
                    "The Qobuz playlist changed since it was planned; "
                    "it will be replanned",
                    False,
                )
            occurrences.append(raw[r.position].playlist_track_id)

        async def send(chunk: Sequence[str]) -> None:
            # Ids name occurrences, so a replay removes nothing more.
            await self._post(
                "playlist/deleteTracks",
                {"playlist_id": playlist_id, "playlist_track_ids": ",".join(chunk)},
                idempotent=True,
            )

        try:
            await apply_in_batches(occurrences, _BATCH, send)
        except HttpFailure as e:
            raise write_failed(e) from None

    async def _update_metadata(self, playlist_id: str, op: UpdateMetadata) -> None:
        try:
            await self._post(
                "playlist/update",
                {
                    "playlist_id": playlist_id,
                    "name": op.draft.name,
                    "description": op.draft.description,
                },
                idempotent=True,
            )
        except HttpFailure as e:
            raise write_failed(e) from None

    # -- helpers -----------------------------------------------------------

    async def _get(
        self, endpoint: str, params: Mapping[str, str | int]
    ) -> Mapping[str, Any]:
        try:
            response = await self._api.request(
                "GET", f"{API_BASE}/{endpoint}", idempotent=True, params=params
            )
        except HttpFailure as e:
            raise _app_id_rejected(e) from None
        return _object(response.json())

    async def _post(
        self, endpoint: str, form: Mapping[str, str], *, idempotent: bool
    ) -> Mapping[str, Any]:
        try:
            response = await self._api.request(
                "POST", f"{API_BASE}/{endpoint}", idempotent=idempotent, data=form
            )
        except HttpFailure as e:
            raise _app_id_rejected(e) from None
        return _object(response.json())


def _app_id_rejected(e: HttpFailure) -> HttpFailure:
    """Qobuz answers 400 to an app ID it no longer accepts (the web player
    moved to a new one): that needs a fresh paste, like an expired token."""
    if e.status == 400 and "app_id" in e.message:
        e.auth_required = True
        e.message = "Qobuz no longer accepts the app ID; sign in to Qobuz again"
    return e


def _object(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("not a JSON object")
    return body


def _version(obj: Mapping[str, Any]) -> tuple[Any, Any]:
    return obj.get("tracks_count"), obj.get("updated_at")


def _changed_during_read(name: str) -> ReadFailed:
    return ReadFailed(
        SERVICE,
        f'Qobuz playlist "{name}" changed while it was being read; '
        "it will be read again next run",
    )


def _not_understood(what: str) -> ReadFailed:
    return ReadFailed(SERVICE, f"Qobuz's reply for {what} was not understood")
