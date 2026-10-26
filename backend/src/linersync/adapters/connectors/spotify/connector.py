"""The Spotify connector: reads, searches and writes through the Web API.

An error never reads as an empty playlist: any failure, short read or change
during a read raises ReadFailed. Writes report `maybe_applied` whenever a
request may have landed, so the engine re-reads instead of retrying blindly.
"""

import base64
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from linersync.adapters.connectors.kit.http import (
    HttpClient,
    HttpFailure,
    apply_in_batches,
    read_failed,
    write_failed,
)
from linersync.adapters.connectors.kit.images import ArtworkHasher, jpeg_for_upload
from linersync.adapters.connectors.spotify.auth import API_BASE
from linersync.adapters.connectors.spotify.moves import plan_moves, plan_removal
from linersync.adapters.connectors.spotify.parse import (
    SPOTIFY,
    RawItem,
    parse_items_page,
    parse_playlist_meta,
    parse_track,
    track_artwork_url,
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

CAPABILITIES = frozenset(Capability) - {Capability.DELETE_PLAYLIST}
LIMITS = ConnectorLimits(50, 100, 2)

_LOCAL = "spotify:local:"
_META_FIELDS = (
    "id,name,description,images,owner(id),collaborative,snapshot_id,items(total)"
)
_PAGE = 50
_BATCH = 100
_SEARCH_LIMIT = 10

_Op = UpdateMetadata | AddTracks | Reorder | RemoveTracks


class SpotifyConnector:
    service = SPOTIFY
    display_name = "Spotify"
    capabilities = CAPABILITIES
    limits = LIMITS

    def __init__(
        self,
        api: HttpClient,
        artwork: ArtworkHasher | None,
        fetch_image: Callable[[str], Awaitable[bytes]],
        *,
        clock: Clock,
    ) -> None:
        self._api = api
        self._artwork = artwork
        self._fetch_image = fetch_image
        self._clock = clock
        self._me_id: str | None = None

    # -- reads -------------------------------------------------------------

    async def list_playlists(self) -> list[PlaylistMeta]:
        try:
            me_id = await self._me()
            metas: list[PlaylistMeta] = []
            url: str | None = f"{API_BASE}/me/playlists"
            params: dict[str, str | int] | None = {"limit": _PAGE, "offset": 0}
            while url is not None:
                body = await self._get(url, params)
                metas.extend(
                    parse_playlist_meta(obj, me_id)
                    for obj in body.get("items") or []
                    if isinstance(obj, Mapping)
                )
                url, params = _next(body), None
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("your playlists") from None
        return metas

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        try:
            return await self._read(playlist_id)
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood(f"playlist {playlist_id}") from None

    async def _read(self, playlist_id: str) -> PlaylistSnapshot:
        me_id = await self._me()
        url = f"{API_BASE}/playlists/{playlist_id}"
        try:
            obj = await self._get(url, {"fields": _META_FIELDS})
        except HttpFailure as e:
            if e.status == 404:
                raise PlaylistNotFound(
                    SPOTIFY, f"Spotify has no playlist {playlist_id}"
                ) from None
            raise
        meta = parse_playlist_meta(obj, me_id)
        if not meta.readable:
            raise PlaylistNotFound(
                SPOTIFY,
                f'Spotify playlist "{meta.name}" can\'t be read: '
                "you no longer own or collaborate on it",
            )
        if not meta.version_token:
            raise _no_version(meta.name)
        total = meta.track_count
        if total is None:
            raise ReadFailed(SPOTIFY, f'Spotify did not say how long "{meta.name}" is')

        raw = await self._read_items(playlist_id, meta.name, total)
        for n, entry in enumerate(raw, start=1):
            if entry.item is None or not entry.item.get("uri"):
                raise ReadFailed(
                    SPOTIFY,
                    f"Spotify returned a song it can't identify at position {n}",
                )

        check = await self._get(url, {"fields": "snapshot_id"})
        if not check.get("snapshot_id"):
            raise _no_version(meta.name)
        if check.get("snapshot_id") != meta.version_token:
            raise ReadFailed(
                SPOTIFY,
                f'Spotify playlist "{meta.name}" changed while it was being read; '
                "it will be read again next run",
            )

        entries = await self._entries(raw)
        return PlaylistSnapshot(
            meta, entries, expected_total=total, taken_at=self._clock.now()
        )

    async def _read_items(
        self, playlist_id: str, name: str, total: int
    ) -> list[RawItem]:
        items: list[RawItem] = []
        url: str | None = f"{API_BASE}/playlists/{playlist_id}/items"
        params: dict[str, str | int] | None = {
            "limit": _PAGE,
            "offset": 0,
            "additional_types": "track,episode",
        }
        while url is not None:
            page = parse_items_page(await self._get(url, params))
            if page.total != total:
                raise ReadFailed(
                    SPOTIFY,
                    f'Spotify reported {page.total} songs in "{name}" after '
                    f"reporting {total}; it will be read again next run",
                )
            if page.next_url is not None and not page.items:
                raise ReadFailed(SPOTIFY, f'Spotify returned an empty page of "{name}"')
            items.extend(page.items)
            if len(items) > total:
                break
            url, params = page.next_url, None
        if len(items) != total:
            raise ReadFailed(
                SPOTIFY,
                f'Spotify returned {len(items)} of {total} songs in "{name}"',
            )
        return items

    async def _entries(self, raw: Sequence[RawItem]) -> tuple[PlaylistEntry, ...]:
        hashes: dict[str, str | None] = {}
        if self._artwork is not None:
            urls = [track_artwork_url(r.item) for r in raw if r.item is not None]
            hashes = await self._artwork.hash_many(urls)
        entries: list[PlaylistEntry] = []
        for r in raw:
            art = track_artwork_url(r.item) if r.item is not None else None
            parsed = parse_track(r.item, hashes.get(art) if art else None)
            if parsed is None:  # checked by the caller; kept for the type checker
                raise ReadFailed(SPOTIFY, "Spotify returned a song it can't identify")
            entries.append(PlaylistEntry(parsed, r.added_at))
        return tuple(entries)

    async def search(self, query: TrackQuery) -> list[Track]:
        try:
            if query.isrc:
                hits = await self._search(f"isrc:{query.isrc}")
                if hits:
                    return hits
            q = f'track:"{_unquote(query.title)}"'
            if query.artists:
                q += f' artist:"{_unquote(query.artists[0])}"'
            return await self._search(q)
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("a search") from None

    async def _search(self, q: str) -> list[Track]:
        body = await self._get(
            f"{API_BASE}/search",
            {"q": q, "type": "track", "limit": _SEARCH_LIMIT, "market": "from_token"},
        )
        items = (body.get("tracks") or {}).get("items") or []
        found = (parse_track(obj) for obj in items if isinstance(obj, Mapping))
        return [t for t in found if t is not None]

    # -- writes ------------------------------------------------------------

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        try:
            me_id = await self._me()
            response = await self._api.request(
                "POST",
                f"{API_BASE}/me/playlists",
                idempotent=False,
                json={
                    "name": draft.name,
                    "description": draft.description,
                    "public": False,
                },
            )
            obj = response.json()
            meta = parse_playlist_meta(obj, me_id)
        except HttpFailure as e:
            raise write_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise WriteFailed(
                SPOTIFY,
                "Spotify's reply to creating a playlist was not understood",
                True,
            ) from None
        if draft.cover_url:
            # The playlist exists, so its meta must be returned. A cover that
            # fails here is not retried until the source cover changes.
            with contextlib.suppress(Exception):
                await self._upload_cover(meta.playlist_id, draft.cover_url)
        return meta

    async def apply(self, playlist_id: str, op: _Op) -> None:
        match op:
            case AddTracks():
                await self._add(playlist_id, op)
            case RemoveTracks():
                await self._remove(playlist_id, op)
            case Reorder():
                await self._reorder(playlist_id, op)
            case UpdateMetadata():
                await self._update_metadata(playlist_id, op)

    async def _add(self, playlist_id: str, op: AddTracks) -> None:
        # Spotify rejects local files; verify reports them as not added.
        uris = [u for u in op.track_ids if not u.startswith(_LOCAL)]
        url = f"{API_BASE}/playlists/{playlist_id}/items"

        async def send(chunk: Sequence[str]) -> None:
            await self._api.request(
                "POST", url, idempotent=False, json={"uris": list(chunk)}
            )

        try:
            await apply_in_batches(uris, _BATCH, send)
        except HttpFailure as e:
            raise write_failed(e) from None

    async def _current_ids(self, playlist_id: str) -> tuple[str, ...]:
        try:
            snap = await self.read_playlist(playlist_id)
        except ReadFailed as e:
            raise WriteFailed(
                SPOTIFY, e.message, False, auth_required=e.auth_required
            ) from None
        return snap.track_ids

    async def _remove(self, playlist_id: str, op: RemoveTracks) -> None:
        current = await self._current_ids(playlist_id)
        try:
            plan = plan_removal(current, op.removals)
        except ValueError:
            raise _changed() from None
        for _, ids in plan.reinserts:
            if any(u.startswith(_LOCAL) for u in ids):
                raise WriteFailed(
                    SPOTIFY,
                    "Spotify can't remove only some copies of a local file",
                    False,
                )
        url = f"{API_BASE}/playlists/{playlist_id}/items"
        sent = False
        try:
            for start in range(0, len(plan.delete_uris), _BATCH):
                chunk = plan.delete_uris[start : start + _BATCH]
                await self._api.request(
                    "DELETE",
                    url,
                    idempotent=True,
                    json={"items": [{"uri": u} for u in chunk]},
                )
                sent = True
            for position, ids in plan.reinserts:
                for start in range(0, len(ids), _BATCH):
                    await self._api.request(
                        "POST",
                        url,
                        idempotent=False,
                        json={
                            "uris": list(ids[start : start + _BATCH]),
                            "position": position + start,
                        },
                    )
                    sent = True
        except HttpFailure as e:
            raise write_failed(e, maybe_applied=True if sent else None) from None

    async def _reorder(self, playlist_id: str, op: Reorder) -> None:
        current = await self._current_ids(playlist_id)
        try:
            moves = plan_moves(current, op.order)
        except ValueError:
            raise _changed() from None
        url = f"{API_BASE}/playlists/{playlist_id}/items"
        sent = False
        try:
            for move in moves:
                await self._api.request(
                    "PUT",
                    url,
                    idempotent=False,
                    json={
                        "range_start": move.range_start,
                        "insert_before": move.insert_before,
                        "range_length": 1,
                    },
                )
                sent = True
        except HttpFailure as e:
            raise write_failed(e, maybe_applied=True if sent else None) from None

    async def _update_metadata(self, playlist_id: str, op: UpdateMetadata) -> None:
        draft = op.draft
        # The cover is prepared first: a dead or unusable source cover is
        # skipped, so it never blocks the details or the songs.
        jpeg = await self._cover_jpeg(draft.cover_url) if draft.cover_url else None
        try:
            await self._api.request(
                "PUT",
                f"{API_BASE}/playlists/{playlist_id}",
                idempotent=True,
                json={"name": draft.name, "description": draft.description},
            )
        except HttpFailure as e:
            raise write_failed(e) from None
        if jpeg is None:
            return
        try:
            await self._put_cover(playlist_id, jpeg)
        except HttpFailure as e:
            raise write_failed(e, maybe_applied=True) from None

    async def _cover_jpeg(self, cover_url: str) -> bytes | None:
        """The cover as an upload-ready JPEG, or None if it can't be fetched or
        prepared."""
        try:
            return jpeg_for_upload(await self._fetch_image(cover_url))
        except Exception:
            return None

    async def _upload_cover(self, playlist_id: str, cover_url: str) -> None:
        jpeg = await self._cover_jpeg(cover_url)
        if jpeg is not None:
            await self._put_cover(playlist_id, jpeg)

    async def _put_cover(self, playlist_id: str, jpeg: bytes) -> None:
        await self._api.request(
            "PUT",
            f"{API_BASE}/playlists/{playlist_id}/images",
            idempotent=True,
            content=base64.b64encode(jpeg),
            headers={"Content-Type": "image/jpeg"},
        )

    # -- helpers -----------------------------------------------------------

    async def _me(self) -> str:
        if self._me_id is None:
            body = await self._get(f"{API_BASE}/me", None)
            self._me_id = str(body["id"])
        return self._me_id

    async def _get(
        self, url: str, params: Mapping[str, str | int] | None
    ) -> Mapping[str, Any]:
        response = await self._api.request("GET", url, idempotent=True, params=params)
        body = response.json()
        if not isinstance(body, Mapping):
            raise ValueError("not a JSON object")
        return body


def _next(body: Mapping[str, Any]) -> str | None:
    value = body.get("next")
    return str(value) if value else None


def _unquote(text: str) -> str:
    return text.replace('"', "")


def _changed() -> WriteFailed:
    return WriteFailed(
        SPOTIFY,
        "The Spotify playlist changed since it was planned; it will be replanned",
        False,
    )


def _no_version(name: str) -> ReadFailed:
    # Without a snapshot_id a change during the read could go unnoticed.
    return ReadFailed(SPOTIFY, f'Spotify did not give a version of "{name}"')


def _not_understood(what: str) -> ReadFailed:
    return ReadFailed(SPOTIFY, f"Spotify's reply for {what} was not understood")
