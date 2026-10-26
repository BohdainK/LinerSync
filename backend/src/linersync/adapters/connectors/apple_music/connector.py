"""The Apple Music connector: an add-only target (read, add, create).

An error never reads as an empty playlist: an empty library playlist's song
list answers 404, which can't be told apart from an error, so it is ReadFailed
(D3). Apple's library lags behind writes, so adds and creates wait for the
change to show before returning; otherwise verify would add the songs again.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import httpx

from linersync.adapters.connectors.apple_music.parse import (
    API_BASE,
    APPLE,
    add_tracks_body,
    artwork_url,
    parse_catalog_song,
    parse_library_playlist,
    parse_library_song,
)
from linersync.adapters.connectors.kit.http import (
    HttpClient,
    HttpFailure,
    apply_in_batches,
    write_failed,
)
from linersync.adapters.connectors.kit.images import ArtworkHasher
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

CAPABILITIES = frozenset(
    {Capability.READ_ORDER, Capability.ADD, Capability.CREATE_PLAYLIST}
)
LIMITS = ConnectorLimits(100, 100, 2)

_LIBRARY = f"{API_BASE}/v1/me/library/playlists"
_PAGE = 100
_BATCH = 100
_CATALOG_CHUNK = 100
_SEARCH_LIMIT = 10
_ART_SIZE = 64

EMPTY_OR_ERROR = (
    "Apple Music returned no song list for this playlist. An empty Apple Music "
    "playlist can't yet be told apart from an error, so it was not treated as "
    "empty. Add one song to it."
)
CHANGED_DURING_READ = (
    "Apple Music playlist changed while it was being read; "
    "it will be read again next run"
)
REFUSED = (
    "Apple Music refused the change (the playlist may not be editable, "
    "or the sign-in expired)"
)

_Op = UpdateMetadata | AddTracks | Reorder | RemoveTracks
_Page = tuple[str, Mapping[str, str | int]]


class AppleMusicConnector:
    service = APPLE
    display_name = "Apple Music"
    capabilities = CAPABILITIES
    limits = LIMITS

    def __init__(
        self,
        api: HttpClient,
        storefront: Callable[[], Awaitable[str]],
        artwork: ArtworkHasher | None,
        sleep: Callable[[float], Awaitable[None]],
        *,
        clock: Clock,
        settle_timeout: float = 10.0,
        settle_interval: float = 1.0,
    ) -> None:
        self._api = api
        self._storefront = storefront
        self._artwork = artwork
        self._sleep = sleep
        self._clock = clock
        self._settle_timeout = settle_timeout
        self._settle_interval = settle_interval

    # -- reads -------------------------------------------------------------

    async def list_playlists(self) -> list[PlaylistMeta]:
        try:
            metas: list[PlaylistMeta] = []
            page: _Page | None = (_LIBRARY, {"limit": _PAGE})
            seen: set[str] = set()
            while page is not None:
                body = await self._get(*page)
                metas.extend(parse_library_playlist(obj) for obj in _data(body))
                page = _next(body)
                if page is not None:
                    key = str(httpx.URL(page[0], params=page[1]))
                    if key in seen:
                        raise ReadFailed(
                            APPLE, "Apple Music repeated a page of your playlists"
                        )
                    seen.add(key)
        except HttpFailure as e:
            raise _read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("your playlists") from None
        return metas

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        try:
            return await self._read(playlist_id)
        except HttpFailure as e:
            raise _read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood(f"playlist {playlist_id}") from None

    async def _read(self, playlist_id: str) -> PlaylistSnapshot:
        obj = await self._playlist_obj(playlist_id)
        meta = parse_library_playlist(obj)
        songs, total = await self._read_songs(playlist_id, meta.name)
        # A change that keeps the length (e.g. a reorder) only shows in
        # lastModifiedDate. Apple omits it on never-changed playlists; then
        # only the total and count checks apply.
        before = _last_modified(obj)
        after = _last_modified(await self._playlist_obj(playlist_id))
        if before is not None and after is not None and before != after:
            raise ReadFailed(APPLE, CHANGED_DURING_READ)
        entries = await self._entries(songs)
        return PlaylistSnapshot(
            meta, entries, expected_total=total, taken_at=self._clock.now()
        )

    async def _playlist_obj(self, playlist_id: str) -> Mapping[str, Any]:
        try:
            body = await self._get(f"{_LIBRARY}/{playlist_id}", None)
        except HttpFailure as e:
            if e.status == 404:
                raise PlaylistNotFound(
                    APPLE, f"Apple Music has no library playlist {playlist_id}"
                ) from None
            raise
        return _first(body)

    async def _read_songs(
        self, playlist_id: str, name: str
    ) -> tuple[list[Mapping[str, Any]], int]:
        songs: list[Mapping[str, Any]] = []
        total: int | None = None
        request: _Page | None = (f"{_LIBRARY}/{playlist_id}/tracks", {"limit": _PAGE})
        while request is not None:
            try:
                page = await self._get(*request)
            except HttpFailure as e:
                if e.status == 404 and total is None:
                    raise ReadFailed(APPLE, EMPTY_OR_ERROR) from None
                raise
            page_total = _total(page)
            if page_total is None:
                raise ReadFailed(APPLE, f'Apple Music did not say how long "{name}" is')
            if total is None:
                total = page_total
            elif page_total != total:
                raise ReadFailed(
                    APPLE,
                    f'Apple Music reported {page_total} songs in "{name}" after '
                    f"reporting {total}; it will be read again next run",
                )
            data = _data(page)
            request = _next(page)
            if request is not None and not data:
                raise ReadFailed(
                    APPLE, f'Apple Music returned an empty page of "{name}"'
                )
            songs.extend(data)
            if len(songs) > total:
                break
        if total is None or len(songs) != total:
            raise ReadFailed(
                APPLE,
                f'Apple Music returned {len(songs)} of {total} songs in "{name}"',
            )
        return songs, total

    async def _entries(
        self, songs: Sequence[Mapping[str, Any]]
    ) -> tuple[PlaylistEntry, ...]:
        catalog = await self._catalog(
            [cid for s in songs if (cid := _catalog_id(s)) is not None]
        )
        art: list[str | None] = []
        for s in songs:
            cid = _catalog_id(s)
            found = catalog.get(cid) if cid is not None else None
            url = artwork_url(_attrs(s).get("artwork"), _ART_SIZE)
            if url is None and found is not None:
                url = artwork_url(_attrs(found).get("artwork"), _ART_SIZE)
            art.append(url)
        hashes: dict[str, str | None] = {}
        if self._artwork is not None:
            hashes = await self._artwork.hash_many(art)
        entries: list[PlaylistEntry] = []
        for s, url in zip(songs, art, strict=True):
            cid = _catalog_id(s)
            found = catalog.get(cid) if cid is not None else None
            found_attrs = _attrs(found) if found is not None else {}
            entries.append(
                PlaylistEntry(
                    parse_library_song(
                        s,
                        isrc=found_attrs.get("isrc") or None,
                        url=found_attrs.get("url") or None,
                        artwork_hash=hashes.get(url) if url else None,
                    )
                )
            )
        return tuple(entries)

    async def _catalog(self, ids: Sequence[str]) -> dict[str, Mapping[str, Any]]:
        """Catalog songs by id; songs Apple doesn't return are simply absent."""
        unique = list(dict.fromkeys(ids))
        if not unique:
            return {}
        sf = await self._storefront()
        found: dict[str, Mapping[str, Any]] = {}
        for start in range(0, len(unique), _CATALOG_CHUNK):
            chunk = unique[start : start + _CATALOG_CHUNK]
            body = await self._get(
                f"{API_BASE}/v1/catalog/{sf}/songs", {"ids": ",".join(chunk)}
            )
            for obj in _data(body):
                found[str(obj["id"])] = obj
        return found

    async def search(self, query: TrackQuery) -> list[Track]:
        try:
            sf = await self._storefront()
            if query.isrc:
                body = await self._get(
                    f"{API_BASE}/v1/catalog/{sf}/songs", {"filter[isrc]": query.isrc}
                )
                hits = [parse_catalog_song(obj) for obj in _data(body)]
                if hits:
                    return hits
            term = " ".join([query.title, *query.artists[:1]]).strip()
            body = await self._get(
                f"{API_BASE}/v1/catalog/{sf}/search",
                {"types": "songs", "term": term, "limit": _SEARCH_LIMIT},
            )
            songs = (body.get("results") or {}).get("songs") or {}
            return [parse_catalog_song(obj) for obj in _data(songs)]
        except HttpFailure as e:
            raise _read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("a search") from None

    # -- writes ------------------------------------------------------------

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        # Apple's API can't set a playlist cover, so draft.cover_url is ignored.
        try:
            response = await self._api.request(
                "POST",
                _LIBRARY,
                idempotent=False,
                json={
                    "attributes": {"name": draft.name, "description": draft.description}
                },
            )
            meta = parse_library_playlist(_first(_json(response.json())))
        except HttpFailure as e:
            raise write_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise WriteFailed(
                APPLE,
                "Apple Music's reply to creating a playlist was not understood",
                True,
            ) from None

        async def visible() -> PlaylistMeta | None:
            try:
                body = await self._get(f"{_LIBRARY}/{meta.playlist_id}", None)
                return parse_library_playlist(_first(body))
            except HttpFailure as e:
                if e.status == 404:
                    return None
                raise

        appeared = await self._settle(visible)
        return appeared or meta

    async def apply(self, playlist_id: str, op: _Op) -> None:
        match op:
            case AddTracks():
                await self._add(playlist_id, op)
            case RemoveTracks():
                raise WriteFailed(
                    APPLE, "Apple Music can't remove songs from a playlist", False
                )
            case Reorder():
                raise WriteFailed(
                    APPLE, "Apple Music can't reorder the songs in a playlist", False
                )
            case UpdateMetadata():
                raise WriteFailed(
                    APPLE, "Apple Music can't change a playlist's details", False
                )

    async def _add(self, playlist_id: str, op: AddTracks) -> None:
        ids = list(op.track_ids)
        if not ids:
            return
        url = f"{_LIBRARY}/{playlist_id}/tracks"
        try:
            baseline = await self._song_total(url)
        except HttpFailure as e:  # nothing has been sent yet
            read = _read_failed(e)
            raise WriteFailed(
                APPLE, read.message, False, auth_required=read.auth_required
            ) from None
        except (KeyError, TypeError, ValueError):
            baseline = None
        if baseline is None:
            raise WriteFailed(
                APPLE,
                "Apple Music did not say how many songs the playlist has; "
                "nothing was added",
                False,
            )

        landed = 0  # ids in batches that succeeded or may have landed

        async def send(chunk: Sequence[str]) -> None:
            nonlocal landed
            try:
                await self._api.request(
                    "POST", url, idempotent=False, json=add_tracks_body(chunk)
                )
            except HttpFailure as e:
                if e.maybe_applied:  # this batch itself may have landed
                    landed += len(chunk)
                raise
            landed += len(chunk)

        try:
            await apply_in_batches(ids, _BATCH, send)
        except HttpFailure as e:
            if e.status == 403:
                error = WriteFailed(APPLE, REFUSED, e.maybe_applied)
            else:
                error = write_failed(e)
            if error.maybe_applied:
                # Wait for what may have landed to show, so the engine's
                # re-read sees it and does not add those songs again.
                await self._wait_for_total(url, baseline + landed)
            raise error from None

        await self._wait_for_total(url, baseline + landed)

    async def _wait_for_total(self, url: str, target: int) -> None:
        async def settled() -> bool | None:
            total = await self._song_total(url)
            return True if total is not None and total >= target else None

        await self._settle(settled)

    async def _song_total(self, url: str) -> int | None:
        """The playlist's song count; 404 means none are visible yet."""
        try:
            page = await self._get(url, {"limit": 1})
        except HttpFailure as e:
            if e.status == 404:
                return 0
            raise
        return _total(page)

    async def _settle[T](self, check: Callable[[], Awaitable[T | None]]) -> T | None:
        """Poll `check` every settle_interval until it returns a value or
        settle_timeout of waiting has passed. Waiting is counted in sleeps, so
        an injected sleep makes it deterministic. Never raises: the write has
        already been made. A failed poll counts as "not visible yet" (R11),
        because stopping early would let verify re-add lagging songs."""
        waited = 0.0
        while True:
            try:
                result = await check()
            except (HttpFailure, KeyError, TypeError, ValueError):
                result = None
            if result is not None or waited >= self._settle_timeout:
                return result
            await self._sleep(self._settle_interval)
            waited += self._settle_interval

    # -- helpers -----------------------------------------------------------

    async def _get(
        self, url: str, params: Mapping[str, str | int] | None
    ) -> Mapping[str, Any]:
        response = await self._api.request("GET", url, idempotent=True, params=params)
        return _json(response.json())


def _json(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("not a JSON object")
    return body


def _data(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = body.get("data")
    if data is None:
        return []
    if not isinstance(data, list) or not all(isinstance(o, Mapping) for o in data):
        raise ValueError("data is not a list of objects")
    return data


def _first(body: Mapping[str, Any]) -> Mapping[str, Any]:
    data = _data(body)
    if not data:
        raise ValueError("no data")
    return data[0]


def _attrs(obj: Mapping[str, Any]) -> Mapping[str, Any]:
    attrs = obj.get("attributes")
    return attrs if isinstance(attrs, Mapping) else {}


def _catalog_id(song: Mapping[str, Any]) -> str | None:
    play = _attrs(song).get("playParams")
    value = play.get("catalogId") if isinstance(play, Mapping) else None
    return str(value) if value else None


def _last_modified(obj: Mapping[str, Any]) -> str | None:
    value = _attrs(obj).get("lastModifiedDate")
    return value if isinstance(value, str) and value else None


def _total(body: Mapping[str, Any]) -> int | None:
    meta = body.get("meta")
    total = meta.get("total") if isinstance(meta, Mapping) else None
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def _next(body: Mapping[str, Any]) -> "_Page | None":
    """Apple's `next` is a path on the API host (e.g. /v1/...?offset=100) that
    may omit `limit`. Its query is kept and `limit` added; httpx would replace
    the query if both were passed."""
    value = body.get("next")
    if not value:
        return None
    url = httpx.URL(str(value))
    params: dict[str, str | int] = {"limit": _PAGE, **dict(url.params)}
    return f"{API_BASE}{url.path}", params


def _read_failed(e: HttpFailure) -> ReadFailed:
    # HttpClient only treats 401 as an auth failure; for Apple a 403 means the
    # Music-User-Token is missing or revoked.
    return ReadFailed(
        APPLE, e.message, auth_required=e.auth_required or e.status == 403
    )


def _not_understood(what: str) -> ReadFailed:
    return ReadFailed(APPLE, f"Apple Music's reply for {what} was not understood")
