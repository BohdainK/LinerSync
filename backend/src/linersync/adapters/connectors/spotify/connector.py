"""The Spotify connector: reads, searches and writes through the Web API.

An error never reads as an empty playlist: any failure, short read or change
during a read raises ReadFailed. Writes report `maybe_applied` whenever a
request may have landed, so the engine re-reads instead of retrying blindly.
"""

import base64
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from linersync.adapters.connectors.kit import SEARCH_LIMIT, PlaylistOp
from linersync.adapters.connectors.kit.errors import Errors
from linersync.adapters.connectors.kit.http import (
    HttpClient,
    HttpFailure,
    apply_in_batches,
    json_object,
    nothing_sent,
    read_failed,
    write_failed,
)
from linersync.adapters.connectors.kit.images import (
    ArtworkHasher,
    hashes_for,
    jpeg_for_upload,
)
from linersync.adapters.connectors.spotify.moves import Move, plan_moves, plan_removal
from linersync.adapters.connectors.spotify.parse import (
    API_BASE,
    SERVICE,
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
LIMITS = ConnectorLimits(page_size=50, add_batch_size=100, search_cost=2)

_LOCAL = "spotify:local:"
_META_FIELDS = (
    "id,name,description,images,owner(id),collaborative,snapshot_id,items(total)"
)
_BATCH = 100  # items per add, delete or insert request

E = Errors(SERVICE, "Spotify")


class SpotifyConnector:
    service = SERVICE
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
            params: dict[str, str | int] | None = {
                "limit": LIMITS.page_size,
                "offset": 0,
            }
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
            raise E.not_understood("your playlists") from None
        return metas

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        try:
            return await self._read(playlist_id)
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise E.not_understood(f"playlist {playlist_id}") from None

    async def _read(self, playlist_id: str) -> PlaylistSnapshot:
        me_id = await self._me()
        url = f"{API_BASE}/playlists/{playlist_id}"
        try:
            obj = await self._get(url, {"fields": _META_FIELDS})
        except HttpFailure as e:
            if e.status == 404:
                raise E.not_found(playlist_id) from None
            raise
        meta = parse_playlist_meta(obj, me_id)
        if not meta.readable:
            raise PlaylistNotFound(
                SERVICE,
                f'Spotify playlist "{meta.name}" can\'t be read: '
                "you no longer own or collaborate on it",
            )
        if not meta.version_token:
            raise E.no_version(meta.name)
        total = meta.track_count
        if total is None:
            raise E.no_length(meta.name)

        raw = await self._read_items(playlist_id, meta.name, total)
        for n, entry in enumerate(raw, start=1):
            if entry.item is None or not entry.item.get("uri"):
                raise ReadFailed(
                    SERVICE,
                    f"Spotify returned a song it can't identify at position {n}",
                )

        check = await self._get(url, {"fields": "snapshot_id"})
        if not check.get("snapshot_id"):
            raise E.no_version(meta.name)
        if check.get("snapshot_id") != meta.version_token:
            raise E.changed_during_read(meta.name)

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
            "limit": LIMITS.page_size,
            "offset": 0,
            "additional_types": "track,episode",
        }
        while url is not None:
            page = parse_items_page(await self._get(url, params))
            if page.total != total:
                raise E.total_moved(page.total, total, name)
            if page.next_url is not None and not page.items:
                raise E.empty_page(name)
            items.extend(page.items)
            if len(items) > total:
                break
            url, params = page.next_url, None
        if len(items) != total:
            raise E.short(len(items), total, name)
        return items

    async def _entries(self, raw: Sequence[RawItem]) -> tuple[PlaylistEntry, ...]:
        urls = [track_artwork_url(r.item) if r.item is not None else None for r in raw]
        hashes = await hashes_for(self._artwork, urls)
        entries: list[PlaylistEntry] = []
        for r, artwork_hash in zip(raw, hashes, strict=True):
            parsed = parse_track(r.item, artwork_hash)
            if parsed is None:  # checked by the caller; kept for the type checker
                raise ReadFailed(SERVICE, "Spotify returned a song it can't identify")
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
            raise E.not_understood("a search") from None

    async def _search(self, q: str) -> list[Track]:
        body = await self._get(
            f"{API_BASE}/search",
            {"q": q, "type": "track", "limit": SEARCH_LIMIT, "market": "from_token"},
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
            raise E.create_not_understood() from None
        if draft.cover_url:
            # The playlist exists, so its meta must be returned. A cover that
            # fails here is not retried until the source cover changes.
            with contextlib.suppress(Exception):
                await self._upload_cover(meta.playlist_id, draft.cover_url)
        return meta

    async def apply(self, playlist_id: str, op: PlaylistOp) -> None:
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
            raise nothing_sent(e) from None
        return snap.track_ids

    async def _remove(self, playlist_id: str, op: RemoveTracks) -> None:
        current = await self._current_ids(playlist_id)
        try:
            plan = plan_removal(current, op.removals)
        except ValueError:
            raise E.changed() from None
        for _, ids in plan.reinserts:
            if any(u.startswith(_LOCAL) for u in ids):
                raise WriteFailed(
                    SERVICE,
                    "Spotify can't remove only some copies of a local file",
                    False,
                )
        # Delete every copy, then put back the copies that stay, one request
        # at a time: a failure after the first is maybe applied.
        deletes = plan.delete_uris
        requests: list[tuple[str, dict[str, Any]]] = [
            ("DELETE", {"items": [{"uri": u} for u in deletes[i : i + _BATCH]]})
            for i in range(0, len(deletes), _BATCH)
        ]
        for position, ids in plan.reinserts:
            requests.extend(
                ("POST", {"uris": list(ids[i : i + _BATCH]), "position": position + i})
                for i in range(0, len(ids), _BATCH)
            )
        url = f"{API_BASE}/playlists/{playlist_id}/items"

        async def send(chunk: Sequence[tuple[str, dict[str, Any]]]) -> None:
            ((method, body),) = chunk
            # Deleting every copy again changes nothing; inserting again would.
            await self._api.request(
                method, url, idempotent=method == "DELETE", json=body
            )

        try:
            await apply_in_batches(requests, 1, send)
        except HttpFailure as e:
            raise write_failed(e) from None

    async def _reorder(self, playlist_id: str, op: Reorder) -> None:
        current = await self._current_ids(playlist_id)
        try:
            moves = plan_moves(current, op.order)
        except ValueError:
            raise E.changed() from None
        url = f"{API_BASE}/playlists/{playlist_id}/items"

        async def send(chunk: Sequence[Move]) -> None:
            (move,) = chunk
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

        try:  # one move per request: a failure after the first is maybe applied
            await apply_in_batches(moves, 1, send)
        except HttpFailure as e:
            raise write_failed(e) from None

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
        return json_object(response)


def _next(body: Mapping[str, Any]) -> str | None:
    value = body.get("next")
    return str(value) if value else None


def _unquote(text: str) -> str:
    return text.replace('"', "")
