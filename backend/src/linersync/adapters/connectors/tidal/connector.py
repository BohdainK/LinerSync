"""The Tidal connector: reads, searches and writes through the TIDAL API v2.

An error never reads as an empty playlist: any failure, over-long read or
change during a read raises ReadFailed. Songs Tidal lists but can't serve in
the user's country are left out, which makes the snapshot incomplete rather
than silently shorter.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from linersync.adapters.connectors.kit.http import (
    HttpClient,
    HttpFailure,
    apply_in_batches,
    read_failed,
    write_failed,
)
from linersync.adapters.connectors.kit.images import ArtworkHasher
from linersync.adapters.connectors.tidal.moves import plan_moves
from linersync.adapters.connectors.tidal.parse import (
    API_BASE,
    SERVICE,
    VIDEO_PREFIX,
    Included,
    RawItem,
    artwork_url,
    next_link,
    parse_items_page,
    parse_playlist_meta,
    parse_track,
    track_ref,
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
LIMITS = ConnectorLimits(page_size=50, add_batch_size=50, search_cost=2)

JSON_API = "application/vnd.api+json"
_ITEM_INCLUDE = "items.artists,items.albums.coverArt"
_SEARCH_LIMIT = 10
_BATCH = 50  # occurrences per add or remove
_NAME_MAX = 250
_DESCRIPTION_MAX = 500

_Op = UpdateMetadata | AddTracks | Reorder | RemoveTracks


class TidalConnector:
    service = SERVICE
    display_name = "Tidal"
    capabilities = CAPABILITIES
    limits = LIMITS

    def __init__(
        self,
        api: HttpClient,
        artwork: ArtworkHasher | None,
        country: Callable[[], Awaitable[str]],
        *,
        clock: Clock,
    ) -> None:
        self._api = api
        self._artwork = artwork
        self._country = country
        self._clock = clock

    # -- reads -------------------------------------------------------------

    async def list_playlists(self) -> list[PlaylistMeta]:
        try:
            metas: list[PlaylistMeta] = []
            url: str | None = f"{API_BASE}/playlists"
            first: dict[str, str | int] = {
                "filter[owners.id]": "me",
                "countryCode": await self._country(),
                "include": "coverArt",
            }
            params: dict[str, str | int] | None = first
            while url is not None:
                doc = await self._get(url, params)
                included = Included(doc)
                metas.extend(
                    parse_playlist_meta(res, included)
                    for res in doc.get("data") or []
                    if isinstance(res, Mapping)
                )
                url, params = _follow(next_link(doc), first), None
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("your playlists") from None
        return metas

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        try:
            meta, raw, included = await self._read(playlist_id)
            entries = await self._entries(raw, included)
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood(f"playlist {playlist_id}") from None
        return PlaylistSnapshot(
            meta, entries, expected_total=meta.track_count, taken_at=self._clock.now()
        )

    async def _read(
        self, playlist_id: str
    ) -> tuple[PlaylistMeta, list[RawItem], Included]:
        """The playlist's meta and every item, including ones Tidal can't serve
        (their ``resource`` is None)."""
        country = await self._country()
        url = f"{API_BASE}/playlists/{quote(playlist_id, safe='')}"
        try:
            doc = await self._get(url, {"countryCode": country, "include": "coverArt"})
        except HttpFailure as e:
            if e.status == 404:
                raise PlaylistNotFound(
                    SERVICE, f"Tidal has no playlist {playlist_id}"
                ) from None
            raise
        meta = parse_playlist_meta(doc["data"], Included(doc))
        if not meta.version_token:
            raise _no_version(meta.name)
        total = meta.track_count
        if total is None:
            raise ReadFailed(SERVICE, f'Tidal did not say how long "{meta.name}" is')

        included = Included({})
        raw: list[RawItem] = []
        next_url: str | None = f"{url}/relationships/items"
        first: dict[str, str | int] = {"countryCode": country, "include": _ITEM_INCLUDE}
        params: dict[str, str | int] | None = first
        while next_url is not None:
            page = await self._get(next_url, params)
            items, link = parse_items_page(page)
            next_url, params = _follow(link, first), None
            if next_url is not None and not items:
                raise ReadFailed(
                    SERVICE, f'Tidal returned an empty page of "{meta.name}"'
                )
            included.add(page)
            raw.extend(items)
            if len(raw) > total:
                raise ReadFailed(
                    SERVICE,
                    f'Tidal returned more songs than it reported in "{meta.name}"; '
                    "it will be read again next run",
                )

        check = await self._get(url, {"countryCode": country})
        if check["data"]["attributes"].get("lastModifiedAt") != meta.version_token:
            raise ReadFailed(
                SERVICE,
                f'Tidal playlist "{meta.name}" changed while it was being read; '
                "it will be read again next run",
            )
        return meta, raw, included

    async def _entries(
        self, raw: list[RawItem], included: Included
    ) -> tuple[PlaylistEntry, ...]:
        available = [r for r in raw if r.resource is not None]
        hashes: dict[str, str | None] = {}
        if self._artwork is not None:
            urls = [artwork_url(r.resource, included) for r in available if r.resource]
            hashes = await self._artwork.hash_many(urls)
        entries: list[PlaylistEntry] = []
        for r in available:
            assert r.resource is not None  # filtered above
            art = artwork_url(r.resource, included)
            track = parse_track(r.resource, included, hashes.get(art) if art else None)
            entries.append(PlaylistEntry(track, r.added_at))
        return tuple(entries)

    async def search(self, query: TrackQuery) -> list[Track]:
        try:
            country = await self._country()
            if query.isrc:
                doc = await self._get(
                    f"{API_BASE}/tracks",
                    {
                        "filter[isrc]": query.isrc,
                        "countryCode": country,
                        "include": "artists,albums",
                    },
                )
                included = Included(doc)
                hits = [
                    parse_track(res, included)
                    for res in doc.get("data") or []
                    if isinstance(res, Mapping)
                ]
                if hits:
                    return hits[:_SEARCH_LIMIT]
            text = query.title
            if query.artists:
                text += f" {query.artists[0]}"
            doc = await self._get(
                f"{API_BASE}/searchResults/{quote(text, safe='')}/relationships/tracks",
                {"countryCode": country, "include": "tracks.artists,tracks.albums"},
            )
            included = Included(doc)
            found: list[Track] = []
            for ref in doc.get("data") or []:
                res = included.get(str(ref.get("type")), str(ref.get("id")))
                if res is not None:
                    found.append(parse_track(res, included))
            return found[:_SEARCH_LIMIT]
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("a search") from None

    # -- writes ------------------------------------------------------------

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        # The API has no cover upload, so draft.cover_url is not carried over.
        try:
            response = await self._send(
                "POST",
                f"{API_BASE}/playlists",
                {
                    "data": {
                        "type": "playlists",
                        "attributes": {**_details(draft), "accessType": "UNLISTED"},
                    }
                },
            )
            doc = response.json()
            return parse_playlist_meta(doc["data"], Included(doc))
        except HttpFailure as e:
            raise write_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise WriteFailed(
                SERVICE, "Tidal's reply to creating a playlist was not understood", True
            ) from None

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

    def _items_url(self, playlist_id: str) -> str:
        return f"{API_BASE}/playlists/{quote(playlist_id, safe='')}/relationships/items"

    async def _add(self, playlist_id: str, op: AddTracks) -> None:
        url = self._items_url(playlist_id)
        skipped: list[str] = []

        async def send(chunk: Sequence[str]) -> None:
            data = [_ref(t) for t in chunk]
            response = await self._send(
                "POST", url, {"data": data, "meta": {"onDuplicates": "ADD"}}
            )
            skipped.extend(_skipped(response))

        try:
            await apply_in_batches(op.track_ids, _BATCH, send)
        except HttpFailure as e:
            raise write_failed(e) from None
        if skipped:
            # The other songs landed; these never will until Tidal offers them.
            raise WriteFailed(
                SERVICE,
                f"{len(skipped)} song(s) isn't available on Tidal in your country "
                f"and wasn't added: {', '.join(skipped)}",
                True,
            )

    async def _current_items(self, playlist_id: str) -> list[RawItem]:
        """Every occurrence with its itemId, read fresh. Refuses a playlist
        holding songs Tidal can't serve: positions would not line up."""
        try:
            meta, raw, _ = await self._read(playlist_id)
        except HttpFailure as e:
            raise WriteFailed(
                SERVICE, e.message, False, auth_required=e.auth_required
            ) from None
        except ReadFailed as e:
            raise WriteFailed(
                SERVICE, e.message, False, auth_required=e.auth_required
            ) from None
        except (KeyError, TypeError, ValueError):
            raise WriteFailed(
                SERVICE,
                f"Tidal's reply for playlist {playlist_id} was not understood",
                False,
            ) from None
        if len(raw) != meta.track_count or any(r.resource is None for r in raw):
            raise WriteFailed(
                SERVICE,
                "Tidal can't change this playlist while it holds songs that aren't "
                "available",
                False,
            )
        return raw

    async def _remove(self, playlist_id: str, op: RemoveTracks) -> None:
        raw = await self._current_items(playlist_id)
        refs: list[dict[str, Any]] = []
        for r in op.removals:
            if not 0 <= r.position < len(raw) or raw[r.position].track_id != r.track_id:
                raise _changed()
            refs.append(_occurrence(raw[r.position]))
        url = self._items_url(playlist_id)

        async def send(chunk: Sequence[dict[str, Any]]) -> None:
            await self._send("DELETE", url, {"data": list(chunk)})

        try:
            await apply_in_batches(refs, _BATCH, send)
        except HttpFailure as e:
            raise write_failed(e) from None

    async def _reorder(self, playlist_id: str, op: Reorder) -> None:
        raw = await self._current_items(playlist_id)
        try:
            moves = plan_moves([(r.track_id, r.item_id) for r in raw], op.order)
        except ValueError:
            raise _changed() from None
        by_item = {r.item_id: r for r in raw}
        url = self._items_url(playlist_id)
        sent = False
        try:
            for move in moves:
                body: dict[str, Any] = {
                    "data": [_occurrence(by_item[i]) for i in move.item_ids]
                }
                if move.before is not None:
                    body["meta"] = {"positionBefore": move.before}
                await self._send("PATCH", url, body)
                sent = True
        except HttpFailure as e:
            raise write_failed(e, maybe_applied=True if sent else None) from None

    async def _update_metadata(self, playlist_id: str, op: UpdateMetadata) -> None:
        try:
            await self._send(
                "PATCH",
                f"{API_BASE}/playlists/{quote(playlist_id, safe='')}",
                {
                    "data": {
                        "type": "playlists",
                        "id": playlist_id,
                        "attributes": _details(op.draft),
                    }
                },
            )
        except HttpFailure as e:
            raise write_failed(e) from None

    # -- helpers -----------------------------------------------------------

    async def _send(self, method: str, url: str, body: Any) -> httpx.Response:
        """A mutation. Its Idempotency-Key makes a retry replay, not repeat, it,
        so the HTTP layer may retry it like an idempotent request."""
        return await self._api.request(
            method,
            url,
            idempotent=True,
            json=body,
            headers={
                "Accept": JSON_API,
                "Content-Type": JSON_API,
                "Idempotency-Key": uuid.uuid4().hex,
            },
        )

    async def _get(
        self, url: str, params: Mapping[str, str | int] | None
    ) -> Mapping[str, Any]:
        response = await self._api.request(
            "GET", url, idempotent=True, params=params, headers={"Accept": JSON_API}
        )
        body = response.json()
        if not isinstance(body, Mapping):
            raise ValueError("not a JSON object")
        return body


def _details(draft: PlaylistDraft) -> dict[str, str]:
    return {
        "name": draft.name[:_NAME_MAX],
        "description": draft.description[:_DESCRIPTION_MAX],
    }


def _follow(link: str | None, first: Mapping[str, str | int]) -> str | None:
    """The next page's URL. A next link may carry only its cursor, so the first
    request's params it lacks are added to it (passing them as ``params`` would
    replace the link's query, cursor included)."""
    if link is None:
        return None
    url = httpx.URL(link)
    missing = {k: v for k, v in first.items() if k not in url.params}
    return str(url.copy_merge_params(missing))


def _skipped(response: httpx.Response) -> list[str]:
    """Ids the add response lists under meta.skipped, as domain track ids."""
    try:
        body = response.json()
        entries = (body.get("meta") or {}).get("skipped") or []
    except (ValueError, AttributeError):
        return []
    out: list[str] = []
    for e in entries:
        if isinstance(e, Mapping) and e.get("id"):
            prefix = VIDEO_PREFIX if e.get("type") == "videos" else ""
            out.append(f"{prefix}{e['id']}")
    return out


def _ref(track_id: str) -> dict[str, str]:
    type_, id_ = track_ref(track_id)
    return {"id": id_, "type": type_}


def _occurrence(item: RawItem) -> dict[str, Any]:
    return {"id": item.ref_id, "type": item.ref_type, "meta": {"itemId": item.item_id}}


def _changed() -> WriteFailed:
    return WriteFailed(
        SERVICE,
        "The Tidal playlist changed since it was planned; it will be replanned",
        False,
    )


def _no_version(name: str) -> ReadFailed:
    # Without lastModifiedAt a change during the read could go unnoticed.
    return ReadFailed(SERVICE, f'Tidal did not give a version of "{name}"')


def _not_understood(what: str) -> ReadFailed:
    return ReadFailed(SERVICE, f"Tidal's reply for {what} was not understood")
