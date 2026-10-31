"""The YouTube Music connector: reads, searches and writes through youtubei/v1.

YouTube Music has no public API and gives playlists no version, so a read is
checked by fetching the first page again: a playlist that changed meanwhile
raises ReadFailed. Greyed-out songs are left out, which makes the snapshot
incomplete rather than silently shorter. YouTube never returns ISRCs, but its
search indexes them: candidates from an ISRC query are marked found_by_isrc.
"""

import dataclasses
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
from linersync.adapters.connectors.youtube_music.moves import plan_moves
from linersync.adapters.connectors.youtube_music.parse import (
    API_BASE,
    ATV,
    LIBRARY,
    SERVICE,
    SONGS_FILTER,
    PlaylistPage,
    Song,
    context,
    parse_continuation,
    parse_edit_status,
    parse_library,
    parse_playlist,
    parse_search,
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

CAPABILITIES = frozenset(
    {
        Capability.READ_ORDER,
        Capability.ADD,
        Capability.REMOVE,
        Capability.REORDER,
        Capability.EDIT_METADATA,
        Capability.CREATE_PLAYLIST,
    }
)
LIMITS = ConnectorLimits(page_size=100, add_batch_size=50, search_cost=1)

_SEARCH_LIMIT = 10
_BATCH = 50  # actions per edit request
_NAME_MAX = 150
_DESCRIPTION_MAX = 5000

_Op = UpdateMetadata | AddTracks | Reorder | RemoveTracks


@dataclasses.dataclass(frozen=True, slots=True)
class _Read:
    page: PlaylistPage  # the first page, with its header
    songs: list[Song | None]  # every row, in order; None for a greyed-out row
    total: int


class YouTubeMusicConnector:
    service = SERVICE
    display_name = "YouTube Music"
    capabilities = CAPABILITIES
    limits = LIMITS

    def __init__(
        self, api: HttpClient, artwork: ArtworkHasher | None, *, clock: Clock
    ) -> None:
        self._api = api
        self._artwork = artwork
        self._clock = clock

    # -- reads -------------------------------------------------------------

    async def list_playlists(self) -> list[PlaylistMeta]:
        try:
            metas, token = parse_library(await self._post("browse", browseId=LIBRARY))
            while token is not None:
                more, token = parse_library(
                    await self._post("browse", continuation=token)
                )
                metas.extend(more)
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("your playlists") from None
        return metas

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        try:
            read = await self._read(playlist_id)
            entries = await self._entries(read.songs)
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood(f"playlist {playlist_id}") from None
        page = read.page
        meta = PlaylistMeta(
            service=SERVICE,
            playlist_id=playlist_id,
            name=page.name,
            description=page.description,
            owned=page.editable,
            readable=True,
            track_count=read.total,
        )
        return PlaylistSnapshot(
            meta,
            entries,
            expected_total=read.total,
            taken_at=self._clock.now(),
            missing_reason=_unavailable(read.songs),
        )

    async def _first_page(self, playlist_id: str) -> PlaylistPage:
        try:
            doc = await self._post("browse", browseId=f"VL{playlist_id}")
        except HttpFailure as e:
            if e.status == 404:
                raise PlaylistNotFound(
                    SERVICE, f"YouTube Music has no playlist {playlist_id}"
                ) from None
            raise
        return parse_playlist(doc)

    async def _read(self, playlist_id: str) -> _Read:
        page = await self._first_page(playlist_id)
        total = page.track_count
        if total is None and not page.items and page.continuation is None:
            total = 0  # an empty playlist may show no count at all
        if total is None:
            raise ReadFailed(
                SERVICE, f'YouTube Music did not say how long "{page.name}" is'
            )
        songs = list(page.items)
        token = page.continuation
        while token is not None:
            more, token = parse_continuation(
                await self._post("browse", continuation=token)
            )
            if token is not None and not more:
                raise ReadFailed(
                    SERVICE, f'YouTube Music returned an empty page of "{page.name}"'
                )
            songs.extend(more)
            if len(songs) > total:
                raise ReadFailed(
                    SERVICE,
                    f"YouTube Music returned more songs than it reported in "
                    f'"{page.name}"; it will be read again next run',
                )
        # No version to compare: the first page must look the same afterwards,
        # and no entry may turn up twice (a move between later pages).
        check = await self._first_page(playlist_id)
        set_ids = [i for i in _set_ids(songs) if i is not None]
        if (
            (check.track_count or 0) != total
            or _set_ids(check.items) != _set_ids(page.items)
            or len(set(set_ids)) != len(set_ids)
        ):
            raise ReadFailed(
                SERVICE,
                f'YouTube Music playlist "{page.name}" changed while it was being '
                "read; it will be read again next run",
            )
        return _Read(page, songs, total)

    async def _entries(self, songs: list[Song | None]) -> tuple[PlaylistEntry, ...]:
        available = [s for s in songs if s is not None]
        hashes: dict[str, str | None] = {}
        if self._artwork is not None:
            hashes = await self._artwork.hash_many(s.artwork_url for s in available)
        return tuple(
            PlaylistEntry(
                dataclasses.replace(
                    s.track,
                    artwork_hash=hashes.get(s.artwork_url) if s.artwork_url else None,
                ),
                None,
            )
            for s in available
        )

    async def search(self, query: TrackQuery) -> list[Track]:
        """ISRC hits first (marked found_by_isrc), then metadata hits. YouTube
        answers any ISRC with something, so the metadata search always runs."""
        try:
            found: list[Track] = []
            if query.isrc:
                songs = await self._search(query.isrc)
                # The top row is the indexed recording when there is one, even
                # if it's a video; the rest are fuzzy matches on the string.
                kept = songs[:1] + _prefer_art_tracks(songs[1:])
                # Only a candidate: the ISRC itself is never copied over.
                found = [dataclasses.replace(s.track, found_by_isrc=True) for s in kept]
            text = query.title
            if query.artists:
                text = f"{query.artists[0]} {query.title}"
            seen = {t.track_id for t in found}
            for s in _prefer_art_tracks(await self._search(text)):
                if s.track.track_id not in seen:
                    found.append(s.track)
                    seen.add(s.track.track_id)
            return found[:_SEARCH_LIMIT]
        except HttpFailure as e:
            raise read_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise _not_understood("a search") from None

    async def _search(self, text: str) -> list[Song]:
        return parse_search(await self._post("search", query=text, params=SONGS_FILTER))

    # -- writes ------------------------------------------------------------

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        # No cover upload through this API: draft.cover_url is not carried over.
        name = draft.name[:_NAME_MAX]
        description = draft.description[:_DESCRIPTION_MAX]
        try:
            doc = await self._post(
                "playlist/create",
                idempotent=False,
                title=name,
                description=description,
                privacyStatus="PRIVATE",
            )
            playlist_id = str(doc["playlistId"])
        except HttpFailure as e:
            raise write_failed(e) from None
        except (KeyError, TypeError, ValueError):
            raise WriteFailed(
                SERVICE,
                "YouTube Music's reply to creating a playlist was not understood",
                True,
            ) from None
        return PlaylistMeta(
            service=SERVICE,
            playlist_id=playlist_id,
            name=name,
            description=description,
            owned=True,
            readable=True,
            track_count=0,
        )

    async def apply(self, playlist_id: str, op: _Op) -> None:
        match op:
            case AddTracks():
                # DEDUPE_OPTION_SKIP skips YouTube's duplicate check, so a
                # song can be added again; without it the edit is refused.
                actions = [
                    {
                        "action": "ACTION_ADD_VIDEO",
                        "addedVideoId": t,
                        "dedupeOption": "DEDUPE_OPTION_SKIP",
                    }
                    for t in op.track_ids
                ]
                await self._edit_in_batches(playlist_id, actions)
            case RemoveTracks():
                await self._remove(playlist_id, op)
            case Reorder():
                await self._reorder(playlist_id, op)
            case UpdateMetadata():
                draft = op.draft
                await self._edit_in_batches(
                    playlist_id,
                    [
                        {
                            "action": "ACTION_SET_PLAYLIST_NAME",
                            "playlistName": draft.name[:_NAME_MAX],
                        },
                        {
                            "action": "ACTION_SET_PLAYLIST_DESCRIPTION",
                            "playlistDescription": draft.description[:_DESCRIPTION_MAX],
                        },
                    ],
                )

    async def _edit_in_batches(
        self, playlist_id: str, actions: Sequence[dict[str, Any]]
    ) -> None:
        """Edits are not idempotent: a request that may have landed is never
        sent again, and later batches after a landed one are maybe applied."""

        async def send(chunk: Sequence[dict[str, Any]]) -> None:
            doc = await self._post(
                "browse/edit_playlist",
                idempotent=False,
                playlistId=playlist_id,
                actions=list(chunk),
            )
            if not parse_edit_status(doc):
                raise HttpFailure(
                    SERVICE,
                    "YouTube Music didn't make the change",
                    maybe_applied=False,
                )

        try:
            await apply_in_batches(actions, _BATCH, send)
        except HttpFailure as e:
            raise write_failed(e) from None

    async def _current(self, playlist_id: str) -> list[Song]:
        """Every occurrence with its setVideoId, read fresh. Refuses a playlist
        that didn't read completely: positions would not line up."""
        try:
            read = await self._read(playlist_id)
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
                f"YouTube Music's reply for playlist {playlist_id} was not understood",
                False,
            ) from None
        songs = [s for s in read.songs if s is not None and s.set_video_id]
        if len(songs) != read.total or len(songs) != len(read.songs):
            raise WriteFailed(
                SERVICE,
                "YouTube Music can't change this playlist while it holds songs "
                "that aren't available",
                False,
            )
        return songs

    async def _remove(self, playlist_id: str, op: RemoveTracks) -> None:
        songs = await self._current(playlist_id)
        actions: list[dict[str, Any]] = []
        for r in op.removals:
            if not 0 <= r.position < len(songs):
                raise _changed()
            song = songs[r.position]
            if song.track.track_id != r.track_id:
                raise _changed()
            actions.append(
                {
                    "action": "ACTION_REMOVE_VIDEO",
                    "removedVideoId": r.track_id,
                    "setVideoId": song.set_video_id,
                }
            )
        await self._edit_in_batches(playlist_id, actions)

    async def _reorder(self, playlist_id: str, op: Reorder) -> None:
        songs = await self._current(playlist_id)
        current = [(s.track.track_id, str(s.set_video_id)) for s in songs]
        try:
            moves = plan_moves(current, op.order)
        except ValueError:
            raise _changed() from None
        actions: list[dict[str, Any]] = []
        for set_id, before in moves:
            action: dict[str, Any] = {
                "action": "ACTION_MOVE_VIDEO_BEFORE",
                "setVideoId": set_id,
            }
            if before is not None:
                action["movedSetVideoIdSuccessor"] = before
            actions.append(action)
        await self._edit_in_batches(playlist_id, actions)

    # -- helpers -----------------------------------------------------------

    async def _post(
        self, endpoint: str, *, idempotent: bool = True, **body: Any
    ) -> Mapping[str, Any]:
        response = await self._api.request(
            "POST",
            f"{API_BASE}/{endpoint}",
            idempotent=idempotent,
            params={"prettyPrint": "false"},
            json={"context": context(), **body},
        )
        doc = response.json()
        if not isinstance(doc, Mapping):
            raise ValueError("not a JSON object")
        return doc


def _prefer_art_tracks(songs: list[Song]) -> list[Song]:
    """Art Tracks are the album recordings; music videos add intros and outros
    that skew the length. Keep videos only when nothing else came."""
    if any(s.video_type == ATV for s in songs):
        return [s for s in songs if s.video_type == ATV]
    return songs


def _set_ids(items: list[Song | None]) -> list[str | None]:
    return [s.set_video_id if s else None for s in items]


def _changed() -> WriteFailed:
    return WriteFailed(
        SERVICE,
        "The YouTube Music playlist changed since it was planned; it will be replanned",
        False,
    )


def _not_understood(what: str) -> ReadFailed:
    return ReadFailed(SERVICE, f"YouTube Music's reply for {what} was not understood")


def _unavailable(songs: list[Song | None]) -> str | None:
    greyed = sum(1 for s in songs if s is None)
    if greyed == 0:
        return None
    if greyed == 1:
        return (
            "1 song in it is no longer available on YouTube Music; "
            "remove it from the playlist there"
        )
    return (
        f"{greyed} songs in it are no longer available on YouTube Music; "
        "remove them from the playlist there"
    )
